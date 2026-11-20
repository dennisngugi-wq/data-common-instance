import datetime as dt
import re
from dataclasses import dataclass, field

DEFAULT_MISSING = {"", "..", "...", "-", "--", "–", "—", "na", "n/a", "n.a.", "nan", "null", "none",
                   ":", "x", "*", "#n/a", "nd", "n.d."}
YEAR = re.compile(r"^\d{4}$")
MAX_EXAMPLES = 8


@dataclass
class Result:
    observations: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


class _Collector:

    def __init__(self):
        self.items = {}

    def add(self, rule, example):
        entry = self.items.setdefault(rule, [0, []])
        entry[0] += 1
        if len(entry[1]) < MAX_EXAMPLES and example not in entry[1]:
            entry[1].append(example)

    def messages(self):
        return [f"{rule}: {n} row(s), e.g. {'; '.join(ex)}" for rule, (n, ex) in self.items.items()]


def parse_value(cell, missing, scale=1.0):
    if cell is None:
        return None
    if isinstance(cell, bool):
        raise ValueError(str(cell))
    if isinstance(cell, (int, float)):
        if cell != cell:
            return None
        return float(cell) * scale
    text = str(cell).strip()
    if text.lower() in missing:
        return None
    text = text.replace(" ", "").replace(" ", "").replace(",", "")
    if text.endswith("%"):
        text = text[:-1]
    return float(text) * scale


def parse_date(cell, fmt=None):
    if cell is None:
        raise ValueError("empty")
    if isinstance(cell, (dt.date, dt.datetime)):
        return cell.isoformat()[:10]
    if isinstance(cell, float):
        if not cell.is_integer():
            raise ValueError(str(cell))
        cell = int(cell)
    text = str(cell).strip()
    if fmt:
        d = dt.datetime.strptime(text, fmt)
        return d.date().isoformat() if any(x in fmt for x in ("%d", "%j")) else (
            f"{d.year:04d}-{d.month:02d}" if "%m" in fmt or "%b" in fmt or "%B" in fmt else f"{d.year:04d}")
    if YEAR.match(text):
        return text
    m = re.match(r"^(\d{4})-(\d{1,2})$", text)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?$", text)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    raise ValueError(text)


def _year_of(date):
    return int(date[:4])


def normalize(table, feed, registry, resolver):
    m = feed.mapping
    res = Result()
    errs, warns = _Collector(), _Collector()
    missing = {str(x).strip().lower() for x in m.get("missing", [])} or DEFAULT_MISSING
    missing |= {""}
    feed_scale = float(m.get("scale", 1))
    header = set(table.header)

    needed = []
    place_spec, date_spec = m.get("place") or {}, m.get("date") or {}
    if "column" in place_spec:
        needed.append(place_spec["column"])
    if date_spec.get("column"):
        needed.append(date_spec["column"])
    needed += list((m.get("filters") or {}).keys())
    if m.get("values"):
        needed += list(m["values"].keys())
    if m.get("variable_column"):
        needed.append(m["variable_column"])
    if m.get("value_column"):
        needed.append(m["value_column"])
    absent = [c for c in needed if c not in header]
    if absent:
        res.errors.append(f"E3 columns not found: {absent}; the file has: {table.header[:40]}")
        return res

    rows = table.rows
    for col, want in (m.get("filters") or {}).items():
        allowed = {str(w) for w in (want if isinstance(want, list) else [want])}
        rows = [r for r in rows if str(r.get(col) if r.get(col) is not None else "").strip() in allowed]
    res.stats["rows_in_file"] = len(table.rows)
    res.stats["rows_after_filters"] = len(rows)

    if "value" in place_spec:
        place_of = {None: str(place_spec["value"])}

        def get_place(r):
            return None
    else:
        place_col = place_spec["column"]

        def get_place(r):
            v = r.get(place_col)
            return str(v).strip() if v is not None else ""
        place_of = resolver.resolve_all([get_place(r) for r in rows], place_spec)
    unresolved = sorted(k for k, v in place_of.items() if not v and k)
    skip_unresolved = m.get("unresolved_places", "error") == "skip"
    if unresolved:
        msg = f"{len(unresolved)} place value(s) not recognised: {unresolved[:15]}"
        if skip_unresolved:
            res.notes.append(f"skipped (unresolved_places: skip): {msg}")
        else:
            res.errors.append(f"E4 {msg}. Fix the spelling, add an alias in config/places.yaml, or set "
                              "unresolved_places: skip")
    for name, cands in sorted(resolver.ambiguous.items()):
        if name in place_of:
            warns.add("place name matched several places; first one used", f"{name} -> {cands}")

    var = registry.variables
    cells = []
    if m.get("values"):
        for r in rows:
            for value_col, target in m["values"].items():
                key = target["variable"] if isinstance(target, dict) else target
                scale = float(target.get("scale", 1)) if isinstance(target, dict) else 1.0
                cells.append((r, key, r.get(value_col), r.get(date_spec.get("column")) if date_spec.get("column")
                              else date_spec.get("value"), scale))
    elif m.get("year_columns"):
        year_cols = [h for h in table.header if YEAR.match(h)]
        if not year_cols:
            res.errors.append("E3 year_columns: no column named like a year (e.g. 2020) in the header")
            return res
        res.stats["year_columns"] = f"{year_cols[0]}..{year_cols[-1]}"
        for r in rows:
            key = _label_variable(r, m, warns)
            if key is None:
                continue
            for yc in year_cols:
                cells.append((r, key, r.get(yc), yc, 1.0))
    else:
        if not (m.get("variable_column") and m.get("value_column")):
            res.errors.append("E3 mapping needs values:, or variable_column + value_column, or year_columns")
            return res
        for r in rows:
            key = _label_variable(r, m, warns)
            if key is None:
                continue
            cells.append((r, key, r.get(m["value_column"]), r.get(date_spec.get("column"))
                          if date_spec.get("column") else date_spec.get("value"), 1.0))

    seen = {}
    this_year = dt.date.today().year
    earliest = int(m.get("earliest_year", registry.instance.get("earliest_year", 1800)))
    exclusions = _exclusions(m, place_of, res)
    excluded = 0
    dupes = 0
    for r, key, raw_value, raw_date, scale in cells:
        v = var[key]
        place_raw = get_place(r)
        place = place_of.get(place_raw)
        if not place:
            continue
        try:
            value = parse_value(raw_value, missing, scale * feed_scale)
        except ValueError:
            errs.add("E5 value is not a number", f"{place_raw}/{key}: {str(raw_value)[:30]!r}")
            continue
        if value is None:
            continue
        try:
            date = parse_date(raw_date, date_spec.get("format"))
        except ValueError:
            errs.add("E5 date not understood (use YYYY, YYYY-MM or YYYY-MM-DD, or set date.format)",
                     f"{place_raw}: {str(raw_date)[:30]!r}")
            continue
        if _excluded(exclusions, place, date, key):
            excluded += 1
            continue
        year = _year_of(date)
        if year < earliest or (year > this_year and not feed.allow_future):
            errs.add(f"E8 date outside {earliest}..{this_year} (set allow_future: true for projections)",
                     f"{place_raw}/{key}: {date}")
            continue
        if (v.min is not None and value < v.min) or (v.max is not None and value > v.max):
            errs.add(f"E6 value outside the allowed range of {key} [{v.min}, {v.max}]",
                     f"{place_raw} {date}: {value:g}")
            continue
        k = (v.dcid, place, date)
        if k in seen:
            if abs(seen[k] - value) > 1e-9 * max(1.0, abs(value)):
                errs.add("E7 two different values for the same variable, place and date",
                         f"{key} {place} {date}: {seen[k]:g} vs {value:g}")
            else:
                dupes += 1
            continue
        seen[k] = value
    _add_totals(m, var, seen, res)
    res.observations = [{"statvar": s, "place": p, "date": d, "value": val, "feed": feed.id}
                        for (s, p, d), val in sorted(seen.items())]
    if dupes:
        res.notes.append(f"{dupes} exact duplicate row(s) dropped")
    if excluded:
        res.notes.append(f"{excluded} value(s) left out by the reviewed exclusions in the feed file: "
                         + "; ".join(f"{e['label']} ({e['reason']})" for e in exclusions))
    res.errors += errs.messages()
    res.warnings += warns.messages()
    res.stats["observations"] = len(res.observations)
    res.stats["places"] = len({o["place"] for o in res.observations})
    res.stats["variables"] = len({o["statvar"] for o in res.observations})
    dates = sorted({o["date"] for o in res.observations})
    res.stats["first_date"], res.stats["last_date"] = (dates[0], dates[-1]) if dates else (None, None)
    if not res.observations and not res.errors:
        res.errors.append("E2 no observations: the file has no rows with a recognised place and a number")
    return res


def _add_totals(m, var, seen, res):
    t = m.get("totals")
    if not t:
        return
    parent, keys = t.get("place"), t.get("variables") or []
    if not parent or not keys or not t.get("reason"):
        res.errors.append("E3 totals needs place, variables and reason")
        return
    unknown = [k for k in keys if k not in var]
    if unknown:
        res.errors.append(f"E3 totals: unknown variables {unknown}")
        return
    dcids = {var[k].dcid for k in keys}
    sums = {}
    for (s, p, d), val in seen.items():
        if s in dcids and p != parent:
            sums[(s, parent, d)] = sums.get((s, parent, d), 0.0) + val
    added = 0
    for k, val in sums.items():
        if k not in seen:
            seen[k] = val
            added += 1
    if added:
        res.notes.append(f"{added} value(s) for {parent} are the sum of the places in the file ({t['reason']})")


def _exclusions(m, place_of, res):
    out = []
    for e in m.get("exclude") or []:
        if not isinstance(e, dict) or not e.get("place") or not e.get("reason"):
            res.errors.append(f"E3 exclude entries need place and reason: {e!r}")
            continue
        raw = str(e["place"])
        out.append({"place": place_of.get(raw) or raw, "date": str(e["date"]) if e.get("date") else None,
                    "variable": e.get("variable"), "reason": " ".join(str(e["reason"]).split()),
                    "label": "/".join(x for x in (raw, str(e.get("date") or "all dates"), e.get("variable")) if x)})
    return out


def _excluded(exclusions, place, date, key):
    return any(e["place"] == place and (e["date"] is None or e["date"] == date)
               and (e["variable"] is None or e["variable"] == key) for e in exclusions)


def _label_variable(row, m, warns):
    if m.get("variable"):
        return m["variable"]
    label = str(row.get(m["variable_column"]) or "").strip()
    key = (m.get("variables_map") or {}).get(label)
    if key is None:
        warns.add("rows ignored: their variable label is not in variables_map", label[:60])
    return key
