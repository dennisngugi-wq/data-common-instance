import hashlib
import io
import json
import logging
import re
from pathlib import Path

import dcmatch
import fetch as fetch_mod
import settings
from places import norm
from registry import FEED_ID, Feed, Variable, slug

SHEETS_FILE = "sheets.json"
INBOX_DIR = "imports/data/inbox"
KINDS = {"dataset", "registry", "custom"}
GOOGLE = re.compile(r"docs\.google\.com/spreadsheets/d/(e/)?([A-Za-z0-9_\-]{20,})")

ABOUT_FIELDS = {
    "name": ["dataset name", "name", "title", "dataset title"],
    "description": ["description", "dataset description", "about"],
    "source": ["source organisation", "source organization", "source", "publisher", "organisation", "organization"],
    "source_url": ["source website", "publisher website", "source url", "organisation website",
                   "organization website"],
    "url": ["dataset page", "landing page", "dataset url", "dataset link", "url"],
    "license": ["licence url", "license url", "licence", "license"],
    "license_evidence": ["licence evidence", "license evidence", "where the licence is stated",
                         "where the license is stated"],
    "citation": ["citation", "cite as"],
    "place_type": ["place type", "places are", "geography level"],
    "within": ["country", "places are in", "within"],
    "topic": ["topic", "group", "category"],
    "projections": ["includes projections", "projections", "future years"],
}
DATA_COLUMNS = {
    "place": ["place", "country", "area", "region", "location", "geography", "province", "county", "district",
              "state", "place name", "country name"],
    "date": ["date", "year", "period", "time", "time period"],
    "variable": ["variable", "indicator", "statistic", "measure", "series", "indicator name"],
    "value": ["value", "observation", "obs value", "amount", "number"],
    "unit": ["unit", "units", "unit of measure", "unit of measurement"],
}
VARIABLE_COLUMNS = {"variable": DATA_COLUMNS["variable"], "description": ["description", "definition"],
                    "unit": DATA_COLUMNS["unit"], "min": ["min", "minimum"], "max": ["max", "maximum"],
                    "dcid": ["data commons variable", "data commons dcid", "dc variable", "dcid",
                             "existing variable"]}
PLACE_TYPES = {
    "country": "Country", "countries": "Country",
    "province": "AdministrativeArea1", "provinces": "AdministrativeArea1", "state": "AdministrativeArea1",
    "states": "AdministrativeArea1", "region": "AdministrativeArea1", "regions": "AdministrativeArea1",
    "county": "AdministrativeArea1", "counties": "AdministrativeArea1", "admin1": "AdministrativeArea1",
    "administrativearea1": "AdministrativeArea1",
    "district": "AdministrativeArea2", "districts": "AdministrativeArea2", "admin2": "AdministrativeArea2",
    "administrativearea2": "AdministrativeArea2",
    "city": "City", "cities": "City",
}
TABS = {"about": ["about", "metadata", "dataset", "info"], "data": ["data", "values", "observations"],
        "variables": ["variables", "indicators", "variable definitions"]}


def _k(text):
    return norm(text).replace(" ", "")


def _pick(names, wanted):
    want = {_k(w) for w in wanted}
    return next((n for n in names if _k(n) in want), None)


def parse_link(link):
    link = str(link or "").strip()
    m = GOOGLE.search(link)
    if m:
        if m.group(1):
            return {"published_id": m.group(2)}
        gid = re.search(r"[#?&]gid=(\d+)", link)
        return {"sheet_id": m.group(2), **({"gid": gid.group(1)} if gid else {})}
    if re.match(r"^https?://\S+$", link):
        return {"url": link}
    if link:
        return {"path": link}
    return {}


def _json_entries(path, problems):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        problems.setdefault("config", []).append(f"{path.name}: not valid JSON ({exc}); no sheet was read from it")
        return []
    items = doc.get("sheets") if isinstance(doc, dict) else doc
    return [(f"{path.name} entry {i + 1}", e) for i, e in enumerate(items or [])]


def _is_link(text):
    return bool(GOOGLE.search(text) or re.match(r"^https?://", text) or text.lower().endswith((".xlsx", ".xls")))


def auto_id(link):
    spec = parse_link(link)
    key = spec.get("sheet_id") or spec.get("published_id")
    tail = hashlib.sha1(str(key or link).encode()).hexdigest()[:4]
    if key:
        return f"gs_{re.sub(r'[^a-z0-9]', '', key.lower())[:10]}_{tail}"
    stem = Path(link.split("?")[0].rstrip("/")).stem
    name = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")[:34] or "sheet"
    return f"{name}_{tail}" if spec.get("url") else name


def _text_entries(path):
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip().startswith("#"):
            continue
        where = f"{INBOX_DIR}/{path.name} line {n}"
        for part in line.split(","):
            parts = part.split()
            if not parts:
                continue
            if not _is_link(parts[0]):
                entry = {"id": parts[0], "link": parts[1] if len(parts) > 1 else "", "enabled": True}
                if len(parts) > 2:
                    entry["kind"] = parts[2]
                out.append((where, entry))
                continue
            kind = next((x.lower() for x in parts if x.lower() in KINDS), None)
            for link in parts:
                if _is_link(link):
                    out.append((where, {"id": auto_id(link), "link": link, "enabled": True,
                                        **({"kind": kind} if kind else {})}))
                elif link.lower() not in KINDS:
                    out.append((where, {"id": "", "link": link, "enabled": True}))
    return out


def _root_and_inbox(config_dir):
    c = Path(config_dir).resolve()
    for candidate in [c.parent.parent, c.parent, c]:
        if (candidate / "imports" / "data" / "inbox").is_dir():
            return candidate, candidate / "imports" / "data" / "inbox"
        if (candidate / "data" / "inbox").is_dir():
            return candidate, candidate / "data" / "inbox"
    return c.parent, c.parent / "data" / "inbox"


def _file_entries(config_dir, inbox, listed, problems):
    root = _root_and_inbox(config_dir)[0]
    named = "\n".join(p.read_text(encoding="utf-8") for d in ("feeds", "registries")
                      for p in sorted((Path(config_dir) / d).glob("*.yaml")))
    out = []
    for p in sorted(inbox.rglob("*.xls*")):
        rel = p.relative_to(root).as_posix()
        if p.name.startswith(("~$", ".")) or rel in listed or rel in named:
            continue
        if p.suffix.lower() != ".xlsx":
            problems.setdefault("config", []).append(f"{rel}: save this file as .xlsx (Excel workbook) to load it")
            continue
        import catalog
        if catalog.is_catalogue(p):
            continue
        out.append((rel, {"id": auto_id(rel), "link": rel, "enabled": True}))
    return out


def inbox_dir(config_dir):
    return _root_and_inbox(config_dir)[1]


def raw_entries(config_dir, problems):
    entries = []
    path = Path(config_dir) / SHEETS_FILE
    if path.exists():
        entries += _json_entries(path, problems)
    inbox = inbox_dir(config_dir)
    if inbox.is_dir():
        for p in sorted(inbox.glob("*.txt")):
            entries += _text_entries(p)
        for p in sorted(inbox.glob("*.json")):
            for where, e in _json_entries(p, problems):
                entries.append((f"{INBOX_DIR}/{where}", dict(e, enabled=e.get("enabled", True))
                                if isinstance(e, dict) else e))
        listed = {str(e.get("link", "")).strip() for _, e in entries if isinstance(e, dict)}
        entries += _file_entries(config_dir, inbox, listed, problems)
    return entries


def load_list(config_dir, problems):
    out, links = {}, {}
    for where, e in raw_entries(config_dir, problems):
        if not isinstance(e, dict):
            problems.setdefault("config", []).append(f"{where}: must be an object {{id, link, enabled}}")
            continue
        sid = str(e.get("id", "")).strip()
        msgs = []
        if not FEED_ID.match(sid) or len(sid) > 40:
            msgs.append(f"{where}: id {sid!r} must be lowercase letters, digits and _ (max 40), e.g. kenya_population")
        elif sid in out:
            msgs.append(f"{where}: id {sid!r} is used twice")
        kind = str(e.get("kind") or "dataset").strip().lower()
        if kind not in KINDS:
            msgs.append(f"{where}: kind must be one of {sorted(KINDS)}")
        spec = parse_link(e.get("link"))
        if not spec:
            msgs.append(f"{where} ({sid}): link is empty")
        link_key = json.dumps({k: v for k, v in spec.items() if k != "gid"}, sort_keys=True)
        if spec and not msgs and link_key in links and kind == "dataset":
            msgs.append(f"{where}: this link is already loaded as {links[link_key]!r}; a sheet is loaded once")
        if msgs:
            problems.setdefault("config", []).extend(msgs)
            continue
        links.setdefault(link_key, sid)
        out[sid] = {"id": sid, "kind": kind, "enabled": bool(e.get("enabled", False)), "link": str(e.get("link", "")),
                    "spec": spec, "note": e.get("note", ""), "allow_future": bool(e.get("allow_future", False))}
    return out


def overrides(config_dir, kind, problems=None):
    return {k: e for k, e in load_list(config_dir, problems if problems is not None else {}).items()
            if e["kind"] == kind}


def apply_link(fetch_spec, entry):
    spec, f = entry["spec"], dict(fetch_spec or {})
    for k in ("sheet_id", "gid", "url", "path", "published_id"):
        f.pop(k, None)
    if spec.get("sheet_id"):
        f.update(type="google_sheet", sheet_id=spec["sheet_id"], **({"gid": spec["gid"]} if spec.get("gid") else {}))
    elif spec.get("published_id"):
        f.update(url=f"https://docs.google.com/spreadsheets/d/e/{spec['published_id']}/pub?output=csv")
        f.setdefault("type", "csv")
    else:
        f.update({k: v for k, v in spec.items()})
    return f


def workbook_bytes(spec):
    if spec.get("sheet_id"):
        url = f"https://docs.google.com/spreadsheets/d/{spec['sheet_id']}/export?format=xlsx"
    elif spec.get("published_id"):
        url = f"https://docs.google.com/spreadsheets/d/e/{spec['published_id']}/pub?output=xlsx"
    elif spec.get("url"):
        url = spec["url"]
    elif spec.get("path"):
        return fetch_mod._read_source({"path": spec["path"]})[0], ""
    else:
        raise fetch_mod.FetchError("no link")
    data = fetch_mod.http_get(url)[0]
    if data[:4] != b"PK\x03\x04":
        if "google" in url:
            raise fetch_mod.FetchError("Google returned no workbook: share the sheet as 'Anyone with the link: "
                                       "Viewer' (or File > Share > Publish to the web)")
        raise fetch_mod.FetchError("the link is not an .xlsx workbook (save .xls/.csv files as .xlsx)")
    return data, url


def read_workbook(data):
    import registries
    return registries._tabs(data)


def parse(tabs):
    names = list(tabs)
    about_tab = _pick(names, TABS["about"])
    var_tab = _pick(names, TABS["variables"])
    data_tab = _pick(names, TABS["data"])
    if not data_tab:
        rest = [n for n in names if n not in (about_tab, var_tab) and not _k(n).startswith("howto")]
        data_tab = rest[0] if len(rest) == 1 else None
    if not about_tab:
        raise fetch_mod.FetchError(f"no 'About' tab (dataset name, description, source, licence); tabs: {names}")
    if not data_tab:
        raise fetch_mod.FetchError(f"no 'Data' tab; tabs: {names}")
    about = {}
    for row in tabs[about_tab]:
        if len(row) < 2 or not row[0].strip():
            continue
        field = next((f for f, opts in ABOUT_FIELDS.items() if _k(row[0]) in {_k(o) for o in opts}), None)
        if field and not about.get(field):
            about[field] = " ".join(row[1].split())
    rows = [r for r in tabs[data_tab] if any(c.strip() for c in r)]
    if not rows:
        raise fetch_mod.FetchError(f"tab {data_tab!r} is empty")
    header = [h.strip() or f"column_{i + 1}" for i, h in enumerate(rows[0])]
    data_rows = [{h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(header)} for r in rows[1:]]
    variables = {}
    if var_tab and tabs[var_tab]:
        vh = [h.strip() for h in tabs[var_tab][0]]
        col = {f: _pick(vh, opts) for f, opts in VARIABLE_COLUMNS.items()}
        if col["variable"]:
            for r in tabs[var_tab][1:]:
                rec = {h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(vh)}
                label = " ".join(rec.get(col["variable"], "").split())
                if label:
                    variables[label] = {f: rec.get(c, "") if c else "" for f, c in col.items() if f != "variable"}
    return {"about": about, "data": {"header": header, "rows": data_rows}, "variables": variables,
            "tabs": {"about": about_tab, "data": data_tab, "variables": var_tab}}


def variable_labels(parsed):
    header, rows = parsed["data"]["header"], parsed["data"]["rows"]
    cols = {f: _pick(header, opts) for f, opts in DATA_COLUMNS.items()}
    if cols["variable"] and cols["value"]:
        return list(dict.fromkeys(r.get(cols["variable"], "").strip() for r in rows
                                  if r.get(cols["variable"], "").strip()))
    return [h for h in header if h not in (cols["place"], cols["date"], cols["unit"])]


def match_variables(entry, parsed, store, post=None):
    key = f"sheets/{entry['id']}/dc_variables.json"
    known = json.loads(store.read_text(key) or "{}").get("variables", {})
    labels = variable_labels(parsed)
    asked = {lab: (parsed.get("variables") or {}).get(" ".join(lab.split()), {}).get("dcid", "") for lab in labels}
    found, problems = dcmatch.decide(labels, asked, known, **({"post": post} if post else {}))
    doc = {"variables": {**known, **found}, "problems": problems}
    store.write_text(key, json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    return doc


def sync(entry, store, stamp):
    base = f"sheets/{entry['id']}"
    report = {"sheet": entry["id"], "status": "OK", "link": entry["link"]}
    previous = store.read_text(f"{base}/parsed.json")
    try:
        data, url = workbook_bytes(entry["spec"])
        tabs = read_workbook(data)
        parsed = parse(tabs)
    except Exception as exc:
        import registries
        report.update(status="USING LAST COPY" if previous else "FAILED", error=str(exc)[:400],
                      last_copy_from=registries.saved_at(store, f"{base}/sha256.txt"))
        logging.error("sheet %s: %s", entry["id"], exc)
        return (stored(entry, store) if previous else None), report
    parsed["content_url"] = url
    parsed["synced"] = stamp
    sha = hashlib.sha256(json.dumps(tabs, sort_keys=True).encode()).hexdigest()
    report.update(tabs={t: max(len(r) - 1, 0) for t, r in tabs.items()}, data_rows=len(parsed["data"]["rows"]))
    report["changed_since_last_run"] = (store.read_text(f"{base}/sha256.txt") or "").strip() != sha
    if report["changed_since_last_run"]:
        import registries
        root = Path(store.root) / base
        root.mkdir(parents=True, exist_ok=True)
        (root / "workbook.xlsx").write_bytes(data)
        for t, rows in tabs.items():
            store.write_text(f"{base}/tabs/{re.sub(r'[^A-Za-z0-9_.-]+', '_', t)}.csv", registries._csv(rows))
        store.write_text(f"{base}/parsed.json", json.dumps(parsed, indent=1, ensure_ascii=False) + "\n")
        store.write_text(f"{base}/sha256.txt", sha + "\n")
    matched = match_variables(entry, parsed, store)
    parsed["dc_variables"] = matched
    report["data_commons_variables"] = sum(1 for v in matched["variables"].values() if v.get("dcid"))
    return parsed, report


def stored(entry, store):
    text = store.read_text(f"sheets/{entry['id']}/parsed.json")
    if not text:
        return None
    parsed = json.loads(text)
    parsed["dc_variables"] = json.loads(store.read_text(f"sheets/{entry['id']}/dc_variables.json") or "{}")
    return parsed


def _source_key(reg, about, msgs):
    org = about.get("source", "")
    if not org:
        msgs.append("About: 'Source organisation' is empty (who publishes the data)")
        return None
    key = reg.source_key_for(org)
    if key:
        return key
    site = about.get("source_url", "")
    if not re.match(r"^https?://\S+$", site):
        msgs.append(f"About: organisation {org!r} is new: fill in 'Source website' (its home page, https://...)")
        return None
    key = slug(org)[:60]
    reg.sources.setdefault(key, {"key": key, "dcid": f"{reg.ns}Source_{key}", "name": org, "url": site,
                                 "existing": False, "aliases": [], "short": ""})
    return key


def build_feed(entry, parsed, reg):
    msgs = []
    about = parsed["about"]
    src_key = _source_key(reg, about, msgs)
    name = about.get("name", "")
    if not name:
        msgs.append("About: 'Dataset name' is empty")
    header, rows = parsed["data"]["header"], parsed["data"]["rows"]
    cols = {f: _pick(header, opts) for f, opts in DATA_COLUMNS.items()}
    if not cols["place"]:
        msgs.append(f"Data: no place column (call it Place, Country, Province, County, ...); columns: {header}")
    if not cols["date"]:
        msgs.append(f"Data: no date column (call it Date or Year); columns: {header}")
    long = bool(cols["variable"] and cols["value"])
    if bool(cols["variable"]) != bool(cols["value"]):
        msgs.append("Data: use both a 'Variable' and a 'Value' column, or neither (one column per variable)")
    if msgs:
        return None, {}, {}, msgs

    ns, sid = reg.ns, entry["id"]
    src = reg.sources[src_key]
    topic = about.get("topic") or name
    group = f"{src['name']}/{topic}"
    labels = variable_labels(parsed)
    if not labels:
        return None, {}, {}, ["Data: no variable found (a 'Variable' column, or columns next to Place and Date)"]

    defs, variables, units, keys, vmap = parsed.get("variables") or {}, {}, {}, set(), {}
    matched = parsed.get("dc_variables") or {}
    msgs += matched.get("problems") or []
    dc, mapped = matched.get("variables") or {}, {}
    unit_in_data = {}
    if long and cols["unit"]:
        for r in rows:
            lab = r.get(cols["variable"], "").strip()
            u = r.get(cols["unit"], "").strip()
            if lab and u:
                unit_in_data.setdefault(lab, set()).add(u)
    for lab in labels:
        d = defs.get(" ".join(lab.split())) or {}
        base = key = f"{sid}_{slug(lab)}"[:56]
        n = 1
        while key in keys:
            n += 1
            key = f"{base}_{n}"
        keys.add(key)
        vmap[lab] = key
        unit_names = {d["unit"]} if d.get("unit") else unit_in_data.get(lab, set())
        if len(unit_names) > 1:
            msgs.append(f"Data: variable {lab!r} has several units {sorted(unit_names)}: one unit per variable")
            continue
        unit = None
        if unit_names:
            uname = next(iter(unit_names))
            unit = f"{ns}Unit_{slug(uname)[:60]}"
            units[unit] = {"name": uname, "description": uname}
        try:
            vmin = float(d["min"].replace(",", "")) if d.get("min") else None
            vmax = float(d["max"].replace(",", "")) if d.get("max") else None
        except ValueError:
            msgs.append(f"Variables: min/max of {lab!r} must be numbers")
            continue
        desc = d.get("description") or f"{lab}, from the dataset \"{name}\" published by {src['name']}."
        known = (dc.get(lab) or {}).get("dcid")
        if known:
            mapped[" ".join(lab.split())] = known
        variables[key] = Variable(
            key=key, dcid=known or f"{ns}_{key}", name=" ".join(lab.split()), description=" ".join(desc.split()),
            group=group, population_type="Thing", measured_property=f"{ns}_{key}_Value", custom_property=not known,
            unit=unit, stat_type="measuredValue", constraints={}, min=vmin, max=vmax, existing=bool(known))
    unknown = sorted(set(defs) - {" ".join(lab.split()) for lab in labels})
    if msgs:
        return None, {}, {}, msgs

    place = {"column": cols["place"], "match": "auto"}
    ptype = PLACE_TYPES.get(_k(about.get("place_type", "")), about.get("place_type") or None)
    if ptype:
        place["type"] = ptype
    if about.get("within"):
        place["within_name"] = about["within"]
    mapping = {"place": place, "date": {"column": cols["date"]}}
    if long:
        mapping.update(variable_column=cols["variable"], value_column=cols["value"], variables_map=vmap)
    else:
        mapping["values"] = {lab: vmap[lab] for lab in labels}
    lic = about.get("license", "")
    lic_url = lic if re.match(r"^https?://\S+$", lic) else ""
    landing = about.get("url", "")
    if not re.match(r"^https?://\S+$", landing):
        landing = entry["link"] if re.match(r"^https?://\S+$", entry["link"]) else ""
    projections = _k(about.get("projections", "")) in {"yes", "true", "y", "1"}
    feed = Feed(id=sid, path=Path(settings.CONFIG_DIR) / SHEETS_FILE, enabled=True, source_key=src_key,
                dataset={"name": name, "description": about.get("description", ""), "url": landing,
                         "license": lic_url, "license_evidence": about.get("license_evidence", "") if lic_url else "",
                         "citation": about.get("citation", "")},
                provenance={"name": f"{src.get('short') or src['name']}: {name}"[:120]},
                fetch={"type": "sheet", "sheet": sid, "url": parsed.get("content_url", "")},
                mapping=mapping, variables=sorted(variables),
                max_row_drop_share=float(reg.instance["validation"]["max_row_drop_share"]),
                allow_future=entry["allow_future"] or projections,
                meta={"sheet_link": entry["link"], "unused_variable_rows": unknown,
                      "data_commons_variables": mapped})
    feed.dataset_dcid = f"{ns}Dataset_{sid}"
    feed.provenance_dcid = f"{ns}Prov_{sid}"
    return feed, variables, units, []


def add_feeds(reg, store, config_dir=None, sync_stamp=None, report=None, include=()):
    from registry import group_chain
    entries = load_list(config_dir or settings.CONFIG_DIR, reg.problems)
    for sid, entry in entries.items():
        if entry["kind"] != "dataset" or not (entry["enabled"] or sid in include):
            continue
        if sync_stamp:
            parsed, rep = sync(entry, store, sync_stamp)
            if report is not None:
                report[sid] = rep
        else:
            parsed = stored(entry, store)
        if parsed is None:
            reg.problems.setdefault(sid, []).append(
                "sheet not downloaded yet (or never readable): " + ((report or {}).get(sid, {}).get("error") or
                                                                    "run a refresh"))
            continue
        if sid in reg.feeds:
            reg.problems.setdefault(sid, []).append(f"id {sid!r} is also a feed in config/feeds/; rename one")
            continue
        feed, variables, units, msgs = build_feed(entry, parsed, reg)
        if msgs:
            reg.problems.setdefault(sid, []).extend(msgs)
            continue
        twin = next((o for o, f in reg.feeds.items() if f.source_key == feed.source_key
                     and dcmatch.same_name(f.dataset.get("name"), feed.dataset["name"])), None)
        if twin:
            reg.problems.setdefault(sid, []).append(
                f"About: this is the same dataset (name and organisation) as {twin!r}; a dataset is loaded once")
            continue
        reg.feeds[sid] = feed
        for k, v in variables.items():
            reg.variables[k] = v
            if not v.existing:
                reg.groups.update({g: (n, p) for g, n, p in group_chain(reg.ns, v.group)})
        reg.units.update(units)
    return reg


def table(feed, store):
    parsed = stored({"id": feed.fetch["sheet"]}, store)
    if parsed is None:
        raise fetch_mod.FetchError("sheet not downloaded yet")
    header, rows = parsed["data"]["header"], parsed["data"]["rows"]
    raw = json.dumps(parsed["data"], sort_keys=True).encode()
    return fetch_mod.Table(header=header, rows=rows, content_url=parsed.get("content_url", ""),
                           sha256=hashlib.sha256(raw).hexdigest(), raw=raw, media_type="text/csv")


def resolve_within(feed, resolver):
    place = feed.mapping.get("place") or {}
    name = place.get("within_name")
    if not name or place.get("within"):
        return None
    hit = resolver.resolve_all([name], {"match": "auto"}).get(name.strip())
    if not hit:
        return f"About: 'Country' {name!r} is not a place Data Commons knows"
    place["within"] = hit
    return None

