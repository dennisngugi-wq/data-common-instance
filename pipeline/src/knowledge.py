import datetime as dt
import json
import logging
import os
from pathlib import Path

import settings

NOTE = "datacommon-project-catalog"
SNAPSHOT_KEY = "state/knowledge_snapshot.json"


def _dir():
    d = Path(os.environ.get("KNOWLEDGE_DIR") or settings.PROJECT_ROOT.parents[1] / "mcp agent" / "knowledge")
    return d if (d / "INDEX.md").exists() else None


def _row(reg, fid, state, report):
    f = reg.feeds[fid]
    src = reg.sources[f.source_key]
    st = state["feeds"].get(fid, {})
    summary = report.get("feeds", {}).get(fid, {}).get("stats") or {}
    lic = f.dataset.get("license") or "PENDING (not found; Croissant withheld)"
    return {"id": fid, "dataset": f.dataset.get("name", ""), "dcid": f.dataset_dcid, "source": src["name"],
            "license": lic, "url": f.dataset.get("url", ""), "data": st.get("content_url") or
            f.fetch.get("url") or f.fetch.get("path") or (f"Google Sheet {f.fetch.get('sheet_id')}"
                                                          if f.fetch.get("sheet_id") else ""),
            "variables": len(f.variables), "rows": st.get("rows"), "refreshed": st.get("refreshed"),
            "places": summary.get("places"), "dates": f"{summary.get('first_date')}..{summary.get('last_date')}"
            if summary.get("first_date") else "", "registry": f.registry, "last_error": st.get("last_error")}


def render(reg, rows, report):
    inst = reg.instance
    pending = [r for r in rows if r["license"].startswith("PENDING")]
    out = [f"# {inst['name']}: datasets on the instance (auto-generated)", "",
           "- **Type:** generated catalogue (do not edit: rewritten by the pipeline after every run that changes data)",
           "- **Origin:** `Projects/datacommon-project` (config/feeds, config/registries, local_store/state)",
           f"- **Instance:** {inst['site_url']} (namespace `{reg.ns}`), MCP endpoint {inst['site_url']}/mcp",
           "- **Read when:** asking what data this instance holds, where a number comes from, its licence/Croissant "
           "status, or which datasets still need a licence or a fix.", "",
           "## Summary",
           f"{len(rows)} datasets live from {len({r['source'] for r in rows})} sources; "
           f"{sum(r['rows'] or 0 for r in rows):,} observations; {len(pending)} datasets without a confirmed "
           "licence (data loaded, Croissant JSON-LD withheld). Each dataset is a Data Commons Dataset node "
           "(Croissant sc:Dataset) with one Provenance (cr:FileObject); the source organisation is the creator.", ""]
    regs = report.get("registries") or {}
    if regs:
        out += ["## Registries (sheets mirrored every run, all tabs)"]
        for rid, r in sorted(regs.items()):
            tabs = ", ".join(f"{t} ({n} rows)" for t, n in (r.get("tabs") or {}).items())
            out.append(f"- `{rid}`: {r.get('status')}; {r.get('datasets_loaded', '?')} datasets; tabs: {tabs or 'n/a'}")
        out.append("")
    out += ["## Datasets", "",
            "| Feed | Dataset (dcid) | Source | Licence | Variables | Rows | Places | Dates | Refreshed |",
            "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| `{r['id']}` | {r['dataset']} (`{r['dcid']}`) | {r['source']} | {r['license']} | "
                   f"{r['variables']} | {r['rows'] or ''} | {r['places'] or ''} | {r['dates']} | {r['refreshed'] or ''} |")
    failing = [(fid, e) for fid, e in sorted((report.get("feeds") or {}).items()) if e.get("status") == "REJECTED"]
    if failing:
        out += ["", "## Not loaded in the last run (previous version, if any, stays live)"]
        out += [f"- `{fid}`: {(e.get('errors') or ['?'])[0][:220]}" for fid, e in failing[:60]]
    out += ["", "## Open questions",
            "- Licences marked PENDING must come from the publisher (never inferred); add the licence URL to the "
            "feed file or registry sheet and the Croissant JSON-LD is published on the next run."]
    return "\n".join(out) + "\n"


def update(reg, state, store, report, live):
    kdir = _dir()
    if not kdir:
        logging.info("knowledge base folder not found (KNOWLEDGE_DIR); skipped")
        return
    try:
        rows = [_row(reg, fid, state, report) for fid in live if fid in reg.feeds]
        note = render(reg, rows, report)
        path = kdir / "sources" / f"{NOTE}.md"
        if not path.exists() or path.read_text(encoding="utf-8") != note:
            path.write_text(note, encoding="utf-8")
        index = kdir / "INDEX.md"
        text = index.read_text(encoding="utf-8")
        if f"[{NOTE}]" not in text:
            line = (f"- [{NOTE}] - auto-generated list of every dataset live on the DataCommon Project instance "
                    "(source, licence/Croissant status, variables, rows, refresh) - for what the instance holds or "
                    "where a number comes from")
            index.write_text(text.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")
        snap = json.loads(store.read_text(SNAPSHOT_KEY) or "{}")
        now = {r["id"]: {"dataset": r["dataset"], "refreshed": r["refreshed"], "license": r["license"]} for r in rows}
        added = sorted(set(now) - set(snap))
        removed = sorted(set(snap) - set(now))
        refreshed = sorted(f for f in set(now) & set(snap) if now[f]["refreshed"] != snap[f]["refreshed"])
        licensed = sorted(f for f in set(now) & set(snap) if now[f]["license"] != snap[f]["license"])
        if added or removed or refreshed or licensed:
            today = dt.date.today().isoformat()
            parts = []
            if added:
                parts.append(f"{len(added)} dataset(s) added ({', '.join(added[:12])}{' ...' if len(added) > 12 else ''})")
            if removed:
                parts.append(f"{len(removed)} removed ({', '.join(removed[:12])})")
            if refreshed:
                parts.append(f"new data for {len(refreshed)} ({', '.join(refreshed[:12])})")
            if licensed:
                parts.append(f"licence changed for {', '.join(licensed[:12])}")
            log = kdir / "CHANGELOG.md"
            text = log.read_text(encoding="utf-8").rstrip("\n")
            entry = f"- datacommon-project (auto): {'; '.join(parts)}. See sources/{NOTE}.md."
            if not text.endswith(f"## {today}") and f"\n## {today}\n" not in text + "\n":
                text += f"\n\n## {today}"
            log.write_text(text + "\n" + entry + "\n", encoding="utf-8")
            store.write_text(SNAPSHOT_KEY, json.dumps(now, indent=1, sort_keys=True) + "\n")
    except Exception:
        logging.exception("knowledge base update failed")
