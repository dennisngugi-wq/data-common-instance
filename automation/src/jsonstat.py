"""JSON-stat 2.0 (SDMX dataflows, e.g. ZIMSTAT on Open Data for Africa) -> observations + discovered variables.

One dataset = one cube: a geo dimension (role "geo", e.g. REF_AREA), a time dimension (role "time",
TIME_PERIOD) and breakdown dimensions (SEX, AGE, UNIT_MEASURE, ...). Mapping:

  statistical variable   one per combination of the breakdown members that vary in the dataset
                         dcid <NS>_<registry>_<FLOW>[_<member code>...], name "<dataset>: <member labels>"
                         (dimensions with a single member are described, not split on)
  unit                   UNIT_MEASURE (or the dimension named in mapping.unit_dimension) -> custom unit node
  place                  geo member code -> dcid: codes table, else the label resolved through the gazetteer /
                         Data Commons API with a type chosen by code pattern; codes outside place_codes are
                         not places of this publisher (e.g. destination countries of migrants) and are skipped
  date                   YYYY (P1Y), YYYY-MM (P1M), YYYY-Qn -> first month + observationPeriod P3M,
                         YYYY-Sn -> P6M; anything else is reported and skipped
No geo dimension -> mapping.default_place (the publisher's country).
"""

import json
import re

from normalize import Result, _Collector
from registry import Variable, slug

PERIODS = [
    (re.compile(r"^(\d{4})$"), lambda m: (m.group(1), "P1Y")),
    (re.compile(r"^(\d{4})-(\d{2})$"), lambda m: (f"{m.group(1)}-{m.group(2)}", "P1M")),
    (re.compile(r"^(\d{4})-?Q([1-4])$"), lambda m: (f"{m.group(1)}-{3 * int(m.group(2)) - 2:02d}", "P3M")),
    (re.compile(r"^(\d{4})-?S([12])$"), lambda m: (f"{m.group(1)}-{6 * int(m.group(2)) - 5:02d}", "P6M")),
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), lambda m: (m.group(0), "")),
]
SKIP_DIMS = {"FREQ"}   # frequency is implied by the date and observationPeriod


def parse_period(code):
    for rx, fn in PERIODS:
        m = rx.match(str(code).strip())
        if m:
            return fn(m)
    return None


def load(raw):
    doc = json.loads(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
    if doc.get("class") == "bundle" or "dimension" not in doc:
        doc = next(iter(v for v in doc.values() if isinstance(v, dict) and "dimension" in v), doc)
    if "dimension" not in doc:
        raise ValueError("not a JSON-stat dataset (no 'dimension')")
    return doc


def _categories(dim):
    cat = dim.get("category", {})
    index = cat.get("index")
    labels = cat.get("label", {}) or {}
    if isinstance(index, list):
        codes = index
    elif isinstance(index, dict):
        codes = [c for c, _ in sorted(index.items(), key=lambda kv: kv[1])]
    else:
        codes = list(labels)
    return codes, {c: str(labels.get(c, c)) for c in codes}


def _cells(doc):
    """Yield ({dim id: member code}, value) for every non-empty cell (row-major, JSON-stat order)."""
    ids, sizes = doc.get("id") or list(doc["dimension"]), doc.get("size")
    codes = [_categories(doc["dimension"][d])[0] for d in ids]
    sizes = sizes or [len(c) for c in codes]
    values = doc.get("value")
    items = values.items() if isinstance(values, dict) else enumerate(values or [])
    for idx, val in items:
        if val is None or (isinstance(val, str) and val.strip() in ("", "..", "-", "NaN")):   # JSON-stat "no value"
            continue
        idx, pos = int(idx), {}
        for d, size, cs in zip(reversed(ids), reversed(sizes), reversed(codes)):
            pos[d] = cs[idx % size]
            idx //= size
        yield pos, val


def normalize(raw, feed, registry, resolver):
    """-> Result with observations (statvar, place, date, value, period, unit) and Result.variables."""
    m, res = feed.mapping, Result()
    errs, warns = _Collector(), _Collector()
    try:
        doc = load(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        res.errors.append(f"E1 response is not JSON-stat: {exc}")
        return res
    dims = doc["dimension"]
    role = doc.get("role") or {}
    # roles may name dimensions the cube does not contain (declared REF_AREA, national-only data): use dims only
    geo = ([d for d in role.get("geo") or [] if d in dims] or [d for d in dims if d == "REF_AREA"] or [None])[0]
    time = ([d for d in role.get("time") or [] if d in dims] or [d for d in dims if d == "TIME_PERIOD"] or [None])[0]
    if not time:
        res.errors.append("E3 no time dimension in the dataset")
        return res
    unit_dim = m.get("unit_dimension", "UNIT_MEASURE")
    unit_dim = unit_dim if unit_dim in dims else None
    cats = {d: _categories(dims[d]) for d in dims}
    breakdown = [d for d in (doc.get("id") or list(dims)) if d not in (geo, time) and d not in SKIP_DIMS]
    varying = [d for d in breakdown if len(cats[d][0]) > 1]
    constant = [d for d in breakdown if len(cats[d][0]) == 1]
    dlabel = lambda d: str(dims[d].get("label") or d)  # noqa: E731

    # ---- places
    place_of = {}
    allowed = re.compile(m.get("place_codes", ".*"))
    if geo:
        codes, labels = cats[geo]
        fixed = m.get("place_map", {}) or {}
        skipped = []
        for code in codes:
            if code in fixed:
                place_of[code] = fixed[code]
            elif not allowed.match(code):
                skipped.append(f"{code} {labels[code]}")
        by_type = {}
        for code in codes:
            if code in place_of or not allowed.match(code):
                continue
            ptype = next((t for rx, t in (m.get("place_types") or {}).items() if re.match(rx, code)), None)
            by_type.setdefault(ptype, []).append(code)
        for ptype, cs in by_type.items():
            spec = {"match": "name", "type": ptype, "within": m.get("place_within"), "strict_type": True}
            hits = resolver.resolve_all([labels[c] for c in cs], spec)
            for c in cs:
                place_of[c] = hits.get(labels[c].strip())
        # two members resolving to one place (e.g. a district and a town both labelled "Kariba") cannot be told
        # apart: leave both out rather than guess
        by_dcid = {}
        for c, d in place_of.items():
            if d and c not in fixed:
                by_dcid.setdefault(d, []).append(c)
        clashes = {d: cs for d, cs in by_dcid.items() if len(cs) > 1}
        for d, cs in clashes.items():
            for c in cs:
                place_of[c] = None
            res.notes.append(f"{dlabel(geo)} members {[f'{c} {labels[c]}' for c in cs]} all match {d}: "
                             "ambiguous, skipped (add the right codes to place_map to load them)")
        unresolved = [f"{c} {labels[c]}" for c in codes if not any(c in cs for cs in clashes.values()) and c if allowed.match(c) and c not in fixed and not place_of.get(c)]
        if skipped:
            res.notes.append(f"{len(skipped)} {dlabel(geo)} code(s) are not areas of this publisher, skipped: "
                             f"{skipped[:8]}")
        if unresolved:
            res.notes.append(f"{len(unresolved)} {dlabel(geo)} member(s) are not places in Data Commons "
                             f"(e.g. hospitals), skipped: {unresolved[:8]}")
    default_place = m.get("default_place")

    # ---- variables
    flow = slug(feed.meta.get("_flow") or feed.id)
    ns, prefix = registry.ns, m.get("variable_prefix", "")
    ds_name = feed.dataset.get("name") or doc.get("label") or feed.id
    const_text = "; ".join(f"{dlabel(d)}: {cats[d][1][cats[d][0][0]]}" for d in constant)
    measured = f"{ns}_{prefix}{flow}_Value"
    variables, units, by_key = {}, {}, {}

    # a cube mixing frequencies (monthly + quarterly, ...) gets one variable per frequency: 2023-Q1 and
    # 2023-01 are different numbers that would otherwise share a date
    kinds = {(parse_period(c) or (None, None))[1] for c in cats[time][0]} - {None}
    by_freq = len(kinds) > 1
    freq_name = {"P1Y": "annual", "P6M": "half-yearly", "P3M": "quarterly", "P1M": "monthly", "": "daily"}

    def variable_for(pos, obs_period=None):
        members = [(d, pos[d]) for d in varying if d != unit_dim] + ([(unit_dim, pos[unit_dim])] if unit_dim in varying
                                                                      else [])
        key = "_".join([f"{prefix}{flow}"] + [re.sub(r"[^A-Za-z0-9]+", "", c) or "X" for _, c in members]
                       + ([obs_period or "D"] if by_freq else []))
        if key in by_key:
            return by_key[key]
        names = [cats[d][1][c] for d, c in members if d != unit_dim]
        unit = None
        if unit_dim:
            ucode = pos[unit_dim]
            unit = f"{ns}Unit_{prefix}{re.sub(r'[^A-Za-z0-9]+', '', ucode) or 'X'}"
            units[unit] = {"name": cats[unit_dim][1][ucode], "description": f"{dlabel(unit_dim)}: "
                                                                             f"{cats[unit_dim][1][ucode]}"}
        breakdown_text = "; ".join(f"{dlabel(d)}: {cats[d][1][c]}" for d, c in members if d != unit_dim)
        desc = " ".join(x for x in [
            f"{ds_name}.",
            f"Breakdown: {breakdown_text}." if breakdown_text else "",
            f"Fixed for the whole dataset: {const_text}." if const_text else "",
            f"Source series: {feed.meta.get('_flow_full', flow)}."] if x)
        if by_freq:
            names.append(freq_name.get(obs_period, obs_period))
        v = Variable(key=f"{ns}_{key}", dcid=f"{ns}_{key}", name=f"{ds_name}: {', '.join(names)}" if names else ds_name,
                     description=desc, group=m.get("group", "Other"), population_type="Thing",
                     measured_property=measured, custom_property=True, unit=unit, stat_type="measuredValue",
                     constraints={}, min=None, max=None, existing=False)
        variables[v.key] = v
        by_key[key] = v
        return v

    # ---- observations
    import datetime as _dt
    this_year = _dt.date.today().year
    earliest = int(m.get("earliest_year", registry.instance.get("earliest_year", 1800)))
    seen, bad_period = {}, set()
    for pos, val in _cells(doc):
        place = place_of.get(pos[geo]) if geo else default_place
        if not place:
            continue
        period = parse_period(pos[time])
        if not period:
            bad_period.add(pos[time])
            continue
        date, obs_period = period
        if int(date[:4]) < earliest or (int(date[:4]) > this_year and not feed.allow_future):
            errs.add(f"E8 date outside {earliest}..{this_year} (allow_future: true for projections)", f"{date}")
            continue
        try:
            value = float(val)
        except (TypeError, ValueError):
            errs.add("E5 value is not a number", f"{pos}: {val!r}")
            continue
        if value != value or value in (float("inf"), float("-inf")):
            continue
        v = variable_for(pos, obs_period)
        k = (v.dcid, place, date)
        if k in seen and seen[k][0] != value:
            errs.add("E7 two different values for the same variable, place and date", f"{v.dcid} {place} {date}")
            continue
        seen[k] = (value, obs_period)
    if bad_period:
        warns.add("periods in a format Data Commons cannot store, skipped", ", ".join(sorted(bad_period)[:8]))
    units_of = {v.dcid: v.unit for v in variables.values()}
    res.observations = [{"statvar": s, "place": p, "date": d, "value": val, "period": per, "feed": feed.id}
                        for (s, p, d), (val, per) in sorted(seen.items())]
    res.variables, res.units = variables, units
    res.errors += errs.messages()
    res.warnings += warns.messages()
    dates = sorted({o["date"] for o in res.observations})
    res.stats.update({"observations": len(res.observations), "places": len({o["place"] for o in res.observations}),
                      "variables": len(variables), "dimensions": {d: len(cats[d][0]) for d in dims},
                      "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
                      "units": sorted(set(filter(None, units_of.values())))})
    if not res.observations and not res.errors:
        res.errors.append("E2 no observations with a recognised place and period")
    return res
