"""Dataset registries: a Google Sheet (or .xlsx) where each row describes one dataset with Croissant-style
columns (name, description, source organisation, landing page, data link, licence, ...), e.g. the team's
ZIMSTAT sheet. config/registries/<id>.yaml says where the sheet is, which tab lists the datasets and which
column holds which field.

Every run (`sync`):
  1. download the WHOLE workbook (all tabs: the .xlsx export of the sheet), keep it as it is:
       <store>/registries/<id>/workbook.xlsx, tabs/<tab>.csv (every tab, every column), rows.json
  2. compare with the previous copy and log datasets added / removed / changed: registries/<id>/changes/<stamp>.json
  3. if the sheet cannot be downloaded, keep using the last copy (the run report says so)
Then `feeds()` turns every dataset row into a Feed (id <registry id>_<dataset id slug>) with its own Dataset and
Provenance, so a new row in the sheet becomes a new dataset on the site the next night, with no code change.
"""

import csv
import datetime as dt
import hashlib
import io
import json
import logging
import re
from pathlib import Path

import yaml

import fetch as fetch_mod
import settings
from registry import Feed, KEY, slug

URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>)]+")
DEFAULT_COLUMNS = {
    "id": "Dataset ID", "name": "Dataset Name", "description": "Description", "organisation": "Source Organization",
    "url": "Source URL", "provenance_url": "Provenance URL", "content_url": "Source file link",
    "license": "License URL", "license_name": "License Type", "domain": "Domain", "subdomain": "Sub-domain",
    "update_frequency": "update_frequency", "last_update": "source_last_update_date",
    "min_date": "Min Date", "max_date": "Max Date",
}


def load_configs(config_dir, problems):
    out = {}
    for path in sorted((Path(config_dir) / "registries").glob("*.yaml")):
        rid = path.stem
        try:
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            problems.setdefault(f"registry:{rid}", []).append(f"cannot read {path.name}: {exc}")
            continue
        if not cfg.get("enabled", False):
            continue
        if not re.match(r"^[a-z0-9][a-z0-9_]{0,30}$", rid):
            problems.setdefault(f"registry:{rid}", []).append("file name must be lowercase letters, digits, _")
            continue
        cfg["id"], cfg["path"] = rid, path
        cfg["columns"] = {**DEFAULT_COLUMNS, **(cfg.get("columns") or {})}
        out[rid] = cfg
    return out


def _workbook_bytes(cfg):
    sheet = cfg.get("sheet") or {}
    if sheet.get("sheet_id"):
        url = f"https://docs.google.com/spreadsheets/d/{sheet['sheet_id']}/export?format=xlsx"
        data, media, _ = fetch_mod.http_get(url)
        if data[:4] != b"PK\x03\x04":
            raise fetch_mod.FetchError("Google returned no workbook: is the sheet shared 'Anyone with the link'?")
        return data, url
    if sheet.get("url"):
        return fetch_mod.http_get(sheet["url"])[0], sheet["url"]
    if sheet.get("path"):
        return fetch_mod._read_source({"path": sheet["path"]})[0], ""
    raise fetch_mod.FetchError("registry needs sheet: {sheet_id | url | path}")


def _tabs(data):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    tabs = {}
    for ws in wb.worksheets:
        rows = [["" if c is None else (c.isoformat()[:10] if isinstance(c, (dt.date, dt.datetime)) else
                                     (str(int(c)) if isinstance(c, float) and c.is_integer() else str(c)))
                 for c in r] for r in ws.iter_rows(values_only=True)]
        while rows and not any(x.strip() for x in rows[-1]):
            rows.pop()
        tabs[ws.title] = rows
    return tabs


def _csv(rows):
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def _records(tabs, cfg):
    tab = cfg.get("datasets_tab")
    if tab not in tabs:
        raise fetch_mod.FetchError(f"datasets_tab {tab!r} not in the workbook; tabs: {list(tabs)}")
    rows = tabs[tab]
    header_row = int(cfg.get("header_row", 1))
    header = [h.strip() for h in rows[header_row - 1]]
    idcol = cfg["columns"]["id"]
    if idcol not in header:
        raise fetch_mod.FetchError(f"id column {idcol!r} not found in tab {tab!r}: {header}")
    out = {}
    for r in rows[header_row:]:
        rec = {h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(header) if h}
        if rec.get(idcol):
            out[rec[idcol]] = rec
    return out


def sync(cfg, store, stamp):
    """Mirror every tab; returns (records by dataset id, report dict)."""
    rid, base = cfg["id"], f"registries/{cfg['id']}"
    report = {"registry": rid, "status": "OK"}
    previous = json.loads(store.read_text(f"{base}/rows.json") or "{}")
    try:
        data, url = _workbook_bytes(cfg)
        tabs = _tabs(data)
        records = _records(tabs, cfg)
    except Exception as exc:  # unreachable sheet or changed layout: use the last good copy
        report.update(status="USING LAST COPY" if previous else "FAILED", error=str(exc)[:400])
        logging.error("registry %s: %s", rid, exc)
        return previous, report
    sha = hashlib.sha256(json.dumps(tabs, sort_keys=True).encode()).hexdigest()
    report.update(tabs={t: max(len(r) - 1, 0) for t, r in tabs.items()}, datasets=len(records), source=url)
    old_ids, new_ids = set(previous), set(records)
    changes = {"added": sorted(new_ids - old_ids), "removed": sorted(old_ids - new_ids),
               "changed": {i: sorted(k for k in set(records[i]) | set(previous[i])
                                     if records[i].get(k) != previous[i].get(k))
                           for i in sorted(new_ids & old_ids) if records[i] != previous[i]}}
    report["changes"] = {k: (len(v)) for k, v in changes.items()}
    if (store.read_text(f"{base}/sha256.txt") or "").strip() != sha:
        root = Path(store.root) / base
        root.mkdir(parents=True, exist_ok=True)
        (root / "workbook.xlsx").write_bytes(data)
        for t, rows in tabs.items():
            store.write_text(f"{base}/tabs/{re.sub(r'[^A-Za-z0-9_.-]+', '_', t)}.csv", _csv(rows))
        store.write_text(f"{base}/rows.json", json.dumps(records, indent=1, ensure_ascii=False) + "\n")
        store.write_text(f"{base}/sha256.txt", sha + "\n")
        if previous or records:
            store.write_text(f"{base}/changes/{stamp}.json", json.dumps(changes, indent=1) + "\n")
    report["changed_since_last_run"] = any(changes[k] for k in changes)
    return records, report


def stored_records(cfg, store):
    return json.loads(store.read_text(f"registries/{cfg['id']}/rows.json") or "{}")


def _first_url(text):
    m = URL_IN_TEXT.search(text or "")
    return m.group(0).rstrip(".,;") if m else ""


def feeds(cfg, records, reg):
    """Dataset rows -> Feeds (and problems per row). Source = the row's organisation, matched to
    config/sources.yaml by name or alias; unknown organisations are reported, not invented."""
    c, out, problems = cfg["columns"], {}, {}
    for rec_id, rec in sorted(records.items()):
        flow = rec_id.split(",")[1] if rec_id.count(",") == 2 else rec_id
        fid = f"{cfg['id']}_{re.sub(r'[^a-z0-9]+', '_', flow.lower()).strip('_')}"[:63]
        if cfg.get("include") and not any(re.search(p, rec_id) for p in cfg["include"]):
            continue
        org = rec.get(c["organisation"], "") or cfg.get("default_source", "")
        src_key = reg.source_key_for(org) or cfg.get("default_source")
        msgs = []
        if src_key not in reg.sources:
            msgs.append(f"source organisation {org!r} is not in config/sources.yaml (name or aliases)")
        data_url = _first_url(rec.get(c["content_url"]))
        if not data_url:
            msgs.append(f"no data link in column {c['content_url']!r}")
        lic = rec.get(c["license"], "").strip()
        lic_url = lic if re.match(r"^https?://\S+$", lic) else ""
        name = " ".join(rec.get(c["name"], "").split())
        landing = _first_url(rec.get(c["url"])) or _first_url(rec.get(c["provenance_url"]))
        sub = rec.get(c["subdomain"]) or rec.get(c["domain"]) or "Other"
        desc = " ".join(rec.get(c["description"], "").split())
        facts = [f"{desc.rstrip('.')}." if desc else f"{name}.",
                 f"Published by {reg.sources[src_key]['name'] if src_key in reg.sources else org}"
                 + (f" ({rec.get(c['domain'])} / {sub})" if rec.get(c["domain"]) else "")
                 + f" as dataset {rec_id}.",
                 (f"Coverage {rec.get(c['min_date'])} to {rec.get(c['max_date'])}." if rec.get(c["min_date"]) else ""),
                 (f"Updated {rec.get(c['update_frequency']).lower()}." if rec.get(c["update_frequency"]) else "")]
        if msgs:
            problems[fid] = msgs
            continue
        mapping = dict(cfg.get("mapping") or {})
        mapping.update(type="jsonstat", group=f"{reg.sources[src_key]['name']}/{sub}/{name}")
        feed = Feed(id=fid, path=cfg["path"], enabled=True, source_key=src_key,
                    dataset={"name": name, "description": " ".join(x for x in facts if x), "url": landing,
                             "license": lic_url, "license_evidence": rec.get(c["license_name"], "") if lic_url else "",
                             "citation": ""},
                    provenance={"name": f"{reg.sources[src_key].get('short') or src_key} {flow}"},
                    fetch=dict(cfg.get("data") or {}, url=data_url), mapping=mapping, variables=[],
                    max_row_drop_share=float(cfg.get("max_row_drop_share",
                                                     reg.instance["validation"]["max_row_drop_share"])),
                    allow_future=bool(cfg.get("allow_future", False)), dynamic=True,
                    license_pending=not lic_url and cfg.get("unlicensed") == "load_without_croissant",
                    registry=cfg["id"], meta={**rec, "_flow": flow, "_flow_full": rec_id})
        feed.dataset_dcid = f"{reg.ns}Dataset_{slug(cfg['id'])}_{slug(flow)}"
        feed.provenance_dcid = f"{reg.ns}Prov_{fid}"
        out[fid] = feed
    return out, problems


def add_stored_feeds(reg, store, config_dir=None):
    """Add every registry dataset (from the last mirrored copy of its sheet) to reg.feeds, with the variables the
    last successful run discovered. Used by every step after the per-dataset runs (merge, catalogue, homepage)."""
    for rid, cfg in load_configs(config_dir or settings.CONFIG_DIR, reg.problems).items():
        for fid, feed in feeds(cfg, stored_records(cfg, store), reg)[0].items():
            reg.feeds.setdefault(fid, feed)
    state = json.loads(store.read_text(settings.STATE_KEY) or "{}")
    from registry import Variable, group_chain
    for fid, saved in (state.get("dynamic") or {}).items():
        if fid in reg.feeds:
            reg.feeds[fid].variables = sorted(saved["variables"])
            for k, d in saved["variables"].items():
                v = Variable(**d)
                reg.variables[k] = v
                reg.groups.update({g: (n, p) for g, n, p in group_chain(reg.ns, v.group)})
            reg.units.update(saved.get("units") or {})
    return reg
