#!/usr/bin/env python3
import argparse
import dataclasses
import datetime as dt
import json
import logging
import shutil
import sys
import time
import traceback
from pathlib import Path

import build_dcp
import croissant
import fetch as fetch_mod
import jsonstat
import knowledge
import normalize as normalize_mod
import registries
import registry as registry_mod
import runlog
import settings
import sheets
from places import PlaceResolver
from storage import LocalStore


def compare_with_production(feed, new_obs, published_text, large_share):
    errors, warnings = [], []
    old = build_dcp.read_published_csv(published_text)
    if not old:
        return errors, warnings, {"previous_rows": 0}
    new = {(o["statvar"], o["place"], o["date"]): float(build_dcp._fmt(o["value"])) for o in new_obs}
    drop = 1 - len(new) / len(old) if len(new) < len(old) else 0.0
    vanished = [k for k in old if k not in new]
    if drop > feed.max_row_drop_share:
        errors.append(f"E9 {len(old)} rows published, the source now gives {len(new)} ({drop:.0%} fewer, limit "
                      f"{feed.max_row_drop_share:.0%}); keeping the published version. If the source really "
                      "removed data, raise max_row_drop_share for this feed for one run.")
    elif vanished:
        warnings.append(f"{len(vanished)} previously published value(s) no longer in the source, e.g. "
                        f"{vanished[:3]}")
    revised = [(k, old[k], new[k]) for k in new if k in old and old[k] != 0
               and abs(new[k] - old[k]) / abs(old[k]) > large_share]
    if revised:
        warnings.append(f"{len(revised)} value(s) revised by more than {large_share:.0%}, e.g. "
                        + "; ".join(f"{k[0]} {k[1]} {k[2]}: {a:g} -> {b:g}" for k, a, b in revised[:3]))
    return errors, warnings, {"previous_rows": len(old), "new_rows": len(new),
                              "added": len([k for k in new if k not in old]), "removed": len(vanished),
                              "changed": len([k for k in new if k in old and new[k] != old[k]])}


def published_files(store, import_name):
    prefix = f"{settings.DCP_INPUT_ROOT}/{import_name}/"
    return {key[len(prefix):]: store.read_text(key) for key in store.list(prefix)}


def publish(store, import_name, folder: Path, stamp):
    new = {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.iterdir()) if p.is_file()}
    old = published_files(store, import_name)
    if new == old:
        return False
    prefix = f"{settings.DCP_INPUT_ROOT}/{import_name}"
    for name, text in old.items():
        store.write_text(f"{settings.BACKUP_PREFIX}/{stamp}/{import_name}/{name}", text)
    for name, text in new.items():
        store.write_text(f"{prefix}/{name}", text)
    for name in old:
        if name not in new:
            (Path(store.root) / prefix / name).unlink(missing_ok=True)
    return True


def prune(store, keep_backups=30, keep_reports=90):
    removed = 0
    for prefix, keep, is_dir in ((settings.BACKUP_PREFIX, keep_backups, True),
                                 (settings.REPORT_PREFIX, keep_reports, False)):
        base = Path(store.root) / prefix
        if not base.is_dir():
            continue
        items = sorted(p for p in base.iterdir() if p.is_dir() == is_dir)
        for old in items[:-keep] if len(items) > keep else []:
            shutil.rmtree(old) if is_dir else old.unlink()
            removed += 1
    return removed


def _var_to_dict(v):
    d = dataclasses.asdict(v)
    d.pop("raw", None)
    return d


def _attach_dynamic(reg, fid, variables, units):
    feed = reg.feeds[fid]
    for key in feed.variables:
        if key in reg.variables and key not in variables:
            reg.variables.pop(key)
    feed.variables = sorted(variables)
    for key, v in variables.items():
        reg.variables[key] = v
        reg.groups.update({d: (n, p) for d, n, p in registry_mod.group_chain(reg.ns, v.group)})
    reg.units.update(units)


def load_everything(args, store, stamp, report):
    reg = registry_mod.load(args.config_dir, include_disabled=(args.feed or []) if args.check else ())
    report["registries"] = {}
    for rid, cfg in registries.load_configs(args.config_dir, reg.problems).items():
        if args.no_sync or args.replay:
            records, rep = registries.stored_records(cfg, store), {"registry": rid, "status": "LAST COPY (no sync)"}
        else:
            records, rep = registries.sync(cfg, store, stamp)
        feeds, problems = registries.feeds(cfg, records, reg)
        rep["datasets_loaded"] = len(feeds)
        report["registries"][rid] = rep
        for fid, msgs in problems.items():
            reg.problems.setdefault(fid, []).extend(msgs)
        for fid, feed in feeds.items():
            if fid in reg.feeds:
                reg.problems.setdefault(fid, []).append(f"id clash with config/feeds/{fid}.yaml")
                continue
            reg.feeds[fid] = feed
    sheet_reports = {}
    sheets.add_feeds(reg, store, args.config_dir, None if (args.no_sync or args.replay) else stamp, sheet_reports,
                     include=(args.feed or []) if args.check else ())
    for sid, rep in sheet_reports.items():
        rep["datasets_loaded"] = 1 if sid in reg.feeds else 0
        report["registries"][f"sheet:{sid}"] = rep
    state = json.loads(store.read_text(settings.STATE_KEY) or "{}")
    state.setdefault("feeds", {})
    for fid, saved in (state.get("dynamic") or {}).items():
        if fid in reg.feeds and reg.feeds[fid].dynamic:
            _attach_dynamic(reg, fid, {k: registry_mod.Variable(**d) for k, d in saved["variables"].items()},
                            saved.get("units", {}))
    return reg, state


def process_feed(feed, reg, args, work, store, resolver):
    entry = {"status": "REJECTED", "errors": [], "warnings": [], "notes": [], "dataset": feed.dataset.get("name"),
             "source": reg.sources[feed.source_key]["name"], "registry": feed.registry or None}
    c_err, c_warn = croissant.check_feed(feed, reg)
    entry["errors"] += c_err
    entry["warnings"] += c_warn
    result, content_url = None, ""
    try:
        if feed.fetch.get("type") == "sheet":
            problem = sheets.resolve_within(feed, resolver)
            if problem:
                entry["errors"].append(problem)
                return entry, None, ""
            table = sheets.table(feed, store)
            content_url = table.content_url
            entry["fetched"] = {"rows": len(table.rows), "columns": table.header[:30], "content_url": content_url,
                                "sheet": feed.meta.get("sheet_link")}
            if feed.meta.get("unused_variable_rows"):
                entry["warnings"].append("Variables tab rows that match no variable in the Data tab: "
                                         f"{feed.meta['unused_variable_rows'][:8]}")
            if feed.meta.get("data_commons_variables"):
                entry["notes"].append("Uses existing Data Commons variables (no duplicate is created): " + "; ".join(
                    f"{lab} = {dcid}" for lab, dcid in sorted(feed.meta["data_commons_variables"].items())[:12]))
            result = normalize_mod.normalize(table, feed, reg, resolver)
        elif feed.fetch.get("type") == "jsonstat":
            raw_path = work / "raw" / f"{feed.id}.raw"
            if args.replay:
                raw, content_url = raw_path.read_bytes(), feed.fetch.get("url", "")
            else:
                raw, content_url = fetch_mod.fetch_raw(feed.fetch)
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(raw)
            entry["fetched"] = {"bytes": len(raw), "content_url": content_url}
            result = jsonstat.normalize(raw, feed, reg, resolver)
        else:
            if args.replay:
                meta = json.loads((work / "raw" / f"{feed.id}.meta.json").read_text())
                table = fetch_mod.parse_bytes(feed.fetch, (work / "raw" / f"{feed.id}.raw").read_bytes(),
                                              meta.get("content_url", ""))
            else:
                table = fetch_mod.fetch(feed.fetch)
                fetch_mod.save_raw(table, work / "raw", feed.id)
            content_url = table.content_url
            entry["fetched"] = {"rows": len(table.rows), "columns": table.header[:30], "content_url": content_url}
            result = normalize_mod.normalize(table, feed, reg, resolver)
    except fetch_mod.FetchError as exc:
        entry["errors"].append(f"E1 fetch failed: {exc}")
        logging.error("%s: fetch failed: %s", feed.id, exc)
        return entry, None, ""
    except Exception as exc:
        entry["errors"].append(f"E1 could not read the data: {exc!r}")
        logging.error("%s: %s", feed.id, traceback.format_exc())
        return entry, None, ""
    entry["errors"] += result.errors
    entry["warnings"] += result.warnings
    entry["notes"] += result.notes
    entry["stats"] = result.stats
    published_csv = store.read_text(f"{settings.DCP_INPUT_ROOT}/{feed.id}/{feed.id}.csv")
    p_err, p_warn, diff = compare_with_production(feed, result.observations, published_csv,
                                                  float(reg.instance["validation"]["large_revision_share"]))
    entry["errors"] += p_err
    entry["warnings"] += p_warn
    entry["changes_vs_published"] = diff
    if entry["errors"]:
        logging.error("%s: REJECTED (%d error(s)): %s", feed.id, len(entry["errors"]), entry["errors"][0])
        return entry, None, content_url
    entry["status"] = "ACCEPTED"
    logging.debug("%s: accepted, %d observations, %d places", feed.id, len(result.observations),
                 result.stats.get("places", 0))
    return entry, result, content_url


def run(args):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    started = dt.datetime.now(dt.timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    today = started.date().isoformat()
    work = Path(args.work_dir)
    store = LocalStore(args.store_dir)
    report = {"started": started.isoformat(timespec="seconds"), "mode": "check" if args.check else "publish",
              "feeds": {}}
    reg, state = load_everything(args, store, stamp, report)
    report.update(instance=reg.instance["name"], config_problems=reg.problems.get("config", []),
                  disabled_feeds=reg.disabled)
    if args.list_feeds:
        print("\n".join(sorted(reg.feeds)))
        return 0
    if args.sync_only:
        work.mkdir(parents=True, exist_ok=True)
        (work / "registries_report.json").write_text(json.dumps(report["registries"], indent=2) + "\n")
        print("\n".join(runlog.source_lines(report["registries"])))
        return 0 if all(r.get("status") != "FAILED" for r in report["registries"].values()) else 1
    if args.finalize:
        return finalize(reg, state, work, store, stamp)

    resolver = PlaceResolver(aliases=reg.aliases, cache=json.loads(store.read_text(settings.PLACE_CACHE_KEY) or "{}"))
    for fid, msgs in reg.problems.items():
        if fid != "config" and (not args.feed or fid in args.feed):
            report["feeds"][fid] = {"status": "REJECTED", "errors": [f"config: {m}" for m in msgs]}
    selected = [f for f in sorted(reg.feeds) if not args.feed or f in args.feed]
    for f in args.feed or []:
        if f not in reg.feeds and f not in reg.problems:
            report["feeds"][f] = {"status": "REJECTED", "errors": [
                "no enabled feed with this id (config/sheets.json enabled: true, config/feeds/<id>.yaml, "
                "or a registry dataset)"]}

    accepted, seconds = {}, {}
    for fid in selected:
        t0 = time.monotonic()
        entry, result, content_url = process_feed(reg.feeds[fid], reg, args, work, store, resolver)
        seconds[fid] = time.monotonic() - t0
        report["feeds"][fid] = entry
        if result is not None:
            if reg.feeds[fid].dynamic:
                _attach_dynamic(reg, fid, result.variables, result.units)
            accepted[fid] = (reg.feeds[fid], result, content_url)

    live = sorted(fid for fid in set(accepted) | {fid for fid in reg.feeds
                                                  if store.read_text(f"{settings.DCP_INPUT_ROOT}/{fid}/{fid}.csv")}
                  if croissant.publishable(reg.feeds[fid], reg))
    writer = build_dcp.Writer(reg.restrict(live))
    out = work / "output"
    schema_files = writer.schema_files()
    schema_errors = []
    undefined = build_dcp.undefined_references(schema_files, reg.ns)
    if undefined:
        schema_errors.append(f"V10 referenced but never defined: {undefined[:20]}")
    foreign = build_dcp.foreign_nodes(schema_files, reg.ns)
    if foreign:
        schema_errors.append(f"V12 nodes outside the {reg.ns} namespace would redefine base Data Commons: {foreign}")
    report["schema_errors"] = schema_errors
    folders = {}
    if not schema_errors and live and not args.no_schema:
        folders[reg.schema_import] = out / reg.schema_import
        build_dcp.write_folder(folders[reg.schema_import], schema_files)
    refresh_by_feed = {}
    for fid in sorted(accepted):
        feed, result, content_url = accepted[fid]
        csv_text = writer.observations_csv(result.observations)
        sha = build_dcp.csv_sha(csv_text)
        prev = state["feeds"].get(fid, {})
        refreshed = prev.get("refreshed") if prev.get("csv_sha256") == sha and prev.get("refreshed") else today
        refresh = {"csv_sha256": sha, "refreshed": refreshed, "first_imported": prev.get("first_imported") or today,
                   "content_url": content_url}
        refresh_by_feed[fid] = refresh
        folders[fid] = out / fid
        build_dcp.write_folder(folders[fid], writer.feed_files(feed, result.observations, refresh, result.stats))
        report["feeds"][fid]["data_changed"] = prev.get("csv_sha256") != sha
    for fid in live:
        if fid not in refresh_by_feed and state["feeds"].get(fid, {}).get("refreshed"):
            refresh_by_feed[fid] = state["feeds"][fid]
    previews = croissant.preview(reg, [reg.feeds[f] for f in live], refresh_by_feed)
    (work / "croissant").mkdir(parents=True, exist_ok=True)
    for dcid, doc in previews.items():
        (work / "croissant" / f"{dcid.replace('/', '_')}.json").write_text(json.dumps(doc, indent=2) + "\n")

    published = []
    if not args.check and not schema_errors:
        for name in [reg.schema_import] + sorted(accepted):
            if name in folders and publish(store, name, folders[name], stamp):
                published.append(name)
        state.setdefault("dynamic", {})
        for fid, (feed, result, content_url) in accepted.items():
            state["feeds"][fid] = {**refresh_by_feed[fid], "last_success": started.isoformat(timespec="seconds"),
                                   "rows": len(result.observations), "last_error": None}
            if feed.dynamic:
                state["dynamic"][fid] = {"variables": {k: _var_to_dict(v) for k, v in result.variables.items()},
                                         "units": result.units}
        for fid, entry in report["feeds"].items():
            if entry["status"] == "REJECTED":
                state["feeds"].setdefault(fid, {})["last_error"] = (entry["errors"] or ["?"])[0][:500]
        state["places_used"] = {**state.get("places_used", {}), **resolver.used}
        store.write_text(settings.STATE_KEY, json.dumps(state, indent=2, sort_keys=True) + "\n")
        store.write_text(settings.PLACE_CACHE_KEY, json.dumps(resolver.cache, indent=2, sort_keys=True) + "\n")

    for fid in published:
        if fid in report["feeds"]:
            report["feeds"][fid]["published_now"] = True
    rejected = sorted(f for f, e in report["feeds"].items() if e["status"] == "REJECTED")
    report.update({"finished": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                   "accepted": sorted(accepted), "rejected": rejected, "live_imports": live,
                   "published_now": published, "place_api_calls": resolver.api_calls})
    (work / "feed_reports").mkdir(parents=True, exist_ok=True)
    for fid, entry in report["feeds"].items():
        (work / "feed_reports" / f"{fid}.json").write_text(json.dumps(
            {**entry, "run": report["started"], "published_now": fid in published}, indent=2, default=str) + "\n")
    if not args.feed:
        text = json.dumps(report, indent=2, default=str) + "\n"
        (work / "validation_summary.json").write_text(text, encoding="utf-8")
        if not args.check:
            store.write_text(f"{settings.REPORT_PREFIX}/{stamp}.json", text)
            knowledge.update(reg, state, store, report, live)
    per_dataset_run = bool(args.feed) and not args.check
    if not per_dataset_run:
        print(runlog.section(f"Datasets ({len(report['feeds'])})"))
    for fid, entry in sorted(report["feeds"].items()):
        print(runlog.feed_block(fid, entry, args.position or "", seconds.get(fid)))
    if not per_dataset_run:
        if args.check:
            print(runlog.item("Croissant preview", "ok", "work/croissant/"))
        print(runlog.summary(report, "CHECK RESULT" if args.check else "SUMMARY"))
    ok = not rejected and not report["config_problems"] and not schema_errors
    return 0 if ok else 1


def finalize(reg, state, work, store, stamp):
    feeds = {}
    for path in sorted((work / "feed_reports").glob("*.json")):
        if path.stem in reg.feeds or path.stem in reg.problems:
            feeds[path.stem] = json.loads(path.read_text())
    live = sorted(fid for fid in reg.feeds if store.read_text(f"{settings.DCP_INPUT_ROOT}/{fid}/{fid}.csv")
                  and croissant.publishable(reg.feeds[fid], reg))
    report = {"started": stamp, "mode": "publish (per-dataset containers)", "instance": reg.instance["name"],
              "config_problems": reg.problems.get("config", []), "disabled_feeds": reg.disabled, "feeds": feeds,
              "accepted": sorted(f for f, e in feeds.items() if e.get("status") == "ACCEPTED"),
              "rejected": sorted(f for f, e in feeds.items() if e.get("status") == "REJECTED"),
              "live_imports": live, "published_now": sorted(f for f, e in feeds.items() if e.get("published_now")),
              "registries": json.loads((work / "registries_report.json").read_text())
              if (work / "registries_report.json").exists() else {}}
    writer = build_dcp.Writer(reg.restrict(live))
    files = writer.schema_files()
    errors = []
    undefined = build_dcp.undefined_references(files, reg.ns)
    if undefined:
        errors.append(f"V10 referenced but never defined: {undefined[:20]}")
    foreign = build_dcp.foreign_nodes(files, reg.ns)
    if foreign:
        errors.append(f"V12 nodes outside the {reg.ns} namespace would redefine base Data Commons: {foreign}")
    report["schema_errors"] = errors
    if not errors and live:
        folder = work / "output" / reg.schema_import
        build_dcp.write_folder(folder, files)
        if publish(store, reg.schema_import, folder, stamp):
            report["published_now"].insert(0, reg.schema_import)
    text = json.dumps(report, indent=2, default=str) + "\n"
    (work / "validation_summary.json").write_text(text, encoding="utf-8")
    store.write_text(f"{settings.REPORT_PREFIX}/{stamp}.json", text)
    prune(store)
    knowledge.update(reg, state, store, report, live)
    print(runlog.item("Shared schema (variables, units)", "error" if errors else
                      ("updated" if reg.schema_import in report["published_now"] else "unchanged"),
                      "; ".join(errors)))
    print(runlog.item("Run report and knowledge base", "updated", "work/validation_summary.json"))
    (work / "run_summary.txt").write_text(runlog.summary(report) + "\n", encoding="utf-8")
    return 0 if not report["rejected"] and not report["config_problems"] and not errors else 1


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--config-dir", default=str(settings.CONFIG_DIR))
    p.add_argument("--work-dir", default=str(settings.PROJECT_ROOT / "work"))
    p.add_argument("--store-dir", default=str(settings.PROJECT_ROOT / "local_store"))
    p.add_argument("--feed", action="append", help="only this feed id (repeatable)")
    p.add_argument("--check", action="store_true", help="fetch and validate only; publish nothing")
    p.add_argument("--replay", action="store_true", help="re-parse the last downloads in work/raw")
    p.add_argument("--no-schema", action="store_true", help="do not publish the shared schema (per-dataset runs)")
    p.add_argument("--no-sync", action="store_true", help="use the last stored copy of the registry sheets")
    p.add_argument("--sync-only", action="store_true", help="only mirror the registry sheets (all tabs)")
    p.add_argument("--list-feeds", action="store_true", help="print every feed id (config feeds + registry rows)")
    p.add_argument("--position", help="shown in the log, e.g. 3/102 (per-dataset runs)")
    p.add_argument("--finalize", action="store_true",
                   help="after per-dataset runs: aggregate reports and update the knowledge base")
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
