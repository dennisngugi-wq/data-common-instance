#!/usr/bin/env python3
import argparse
import collections
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import resolve
import settings
from registry import slug
from storage import LocalStore

COLUMNS = {
    "country": "Country", "dcid": "DCID", "domain": "Domain", "indicator": "Indicator",
    "description": "Dataset Description", "creator": "Creator", "creator_url": "Creator URL",
    "url": "URL/Source Metadata URL", "content_url": "distribution.contentURL",
    "format": "distribution.encodingformat", "geo_level": "Geo Level", "published": "datePublished",
    "coverage": "Temporal Coverage", "frequency": "Update Frequency", "license": "License",
    "license_url": "License URL", "comments": "Comments", "region": "Region",
}
REQUIRED = ["country", "dcid", "domain", "indicator", "description", "creator", "creator_url", "url",
            "content_url", "format", "geo_level", "license", "license_url"]
URL_KEYS = ("url", "content_url", "license_url", "creator_url")
RECOMMENDED = ["coverage", "frequency"]
UNKNOWN = {"", "not found", "n/a", "na", "none", "unknown", "-", "--", "tbd", "not available", "nan"}
URL = re.compile(r"^https?://[^\s]+$", re.I)
PLACE_DCID = re.compile(r"^(country/[A-Z]{3}|[a-z]+/[A-Za-z0-9_.\-]+)$")
FILE_FORMATS = ("csv", "xls", "xlsx", "json", "api", "xml", "zip", "tsv")
FREQUENCIES = {
    "annual": "Annual", "annually": "Annual", "annualy": "Annual", "yearly": "Annual",
    "monthly": "Monthly", "quarterly": "Quarterly", "weekly": "Weekly", "daily": "Daily",
    "decennium": "Decennial", "decennial": "Decennial", "quinquennial": "Quinquennial",
    "biennial": "Biennial", "semi-annual": "Semi-annual", "semiannual": "Semi-annual",
}


def _text(value, keep_lines=False):
    if value is None:
        return ""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()[:10]
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip() if keep_lines else " ".join(str(value).split())


def known(value):
    return _text(value).lower() not in UNKNOWN


def clean_url(value):
    text = _text(value)
    if "%3A%2F" in text.upper():
        text = unquote(text)
    return text


def read_csv(path):
    import csv
    import io
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return [list(r) for r in csv.reader(io.StringIO(text, newline=""))]


def read_workbook(path):
    if Path(path).suffix.lower() == ".csv":
        return {Path(path).stem: read_csv(path)}
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = {}
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        out[ws.title] = rows
    return out


def is_catalogue(path):
    try:
        return any(find_header(rows)[0] is not None for rows in read_workbook(path).values())
    except Exception:
        return False


def find_header(rows):
    wanted = {COLUMNS[k].lower() for k in ("indicator", "content_url", "license_url")}
    for i, row in enumerate(rows[:10]):
        cells = {_text(c).lower(): j for j, c in enumerate(row) if _text(c)}
        if wanted <= set(cells):
            return i, cells
    return None, None


def column_map(cells):
    alias = {"creator url": "creator_url"}
    by_name = {COLUMNS[k].lower(): k for k in COLUMNS}
    by_name.update(alias)
    return {by_name[name]: j for name, j in cells.items() if name in by_name}


def dataset_id(country_dcid, indicator, content_url):
    base = re.sub(r"[^a-z0-9]+", "_", f"{country_dcid.split('/')[-1]} {indicator}".lower()).strip("_")
    return base[:60]


def issue(code, key, problem, fix, value=""):
    return {"code": code, "column": COLUMNS[key], "problem": problem, "fix": fix, "value": _text(value)[:300]}


def diagnose(rec):
    errors, warnings = [], []
    for key in REQUIRED:
        if not known(rec.get(key)):
            errors.append(issue("CAT2", key, f"'{COLUMNS[key]}' is empty",
                                f"Fill '{COLUMNS[key]}' for this row from the publisher's own page; do not guess it.",
                                rec.get(key)))
    desc = rec.get("description", "")
    if known(desc) and not 50 <= len(desc) <= 5000:
        n = len(desc)
        fix = (f"Add at least {50 - n} more characters of factual, source-supported description." if n < 50
               else f"Shorten the description by at least {n - 5000} characters.")
        errors.append(issue("C2", "description", f"description must be 50-5000 characters (now {n})", fix, desc))
    for key in RECOMMENDED:
        if not known(rec.get(key)):
            warnings.append(f"'{COLUMNS[key]}' is not given")
    for key, rule in (("url", "C3"), ("creator_url", "C5"), ("content_url", "C6"), ("license_url", "C4")):
        parts = rec.get(key, "").split()
        if known(rec.get(key)) and not (parts and all(URL.match(x) for x in parts)):
            errors.append(issue(rule, key, f"'{COLUMNS[key]}' must be a full http(s) URL (now {rec[key][:60]!r})",
                                f"Replace it with one full link starting with https:// (no text, no browser data: link, "
                                f"no relative path); several links need a space between them.", rec[key]))
    if known(rec.get("dcid")) and not PLACE_DCID.match(rec["dcid"]):
        errors.append(issue("CAT3", "dcid", f"DCID {rec['dcid']!r} is not a Data Commons place id such as country/KEN",
                            "Use the Data Commons place id, e.g. country/KEN for Kenya, not the country name.", rec["dcid"]))
    fmt = rec.get("format", "").lower()
    if known(fmt) and not any(f in fmt for f in FILE_FORMATS):
        errors.append(issue("C6", "content_url", "no data download found: the link is a web page and no file or API was found on it",
                            "Open the page, find the real file or data API (CSV, XLS, XLSX, JSON), paste that direct link into "
                            "'distribution.contentURL' and set 'distribution.encodingformat' to its format.", rec.get("content_url")))
    if known(rec.get("frequency")) and rec["frequency"].lower() not in FREQUENCIES:
        warnings.append(f"update frequency {rec['frequency']!r} is not a standard value")
    if not known(rec.get("published")):
        warnings.append("datePublished is not given: the refresh date is used (needs Data Commons approval)")
    return errors, warnings


def check_row(rec, line):
    issues, warnings = diagnose(rec)
    return [f"{i['code']} {i['problem']}" for i in issues], warnings


def parse(path):
    tabs = read_workbook(path)
    records, problems, ignored = [], [], []
    for tab, rows in tabs.items():
        hi, cells = find_header(rows)
        if hi is None:
            ignored.append(tab)
            continue
        cols = column_map(cells)
        missing = [COLUMNS[k] for k in REQUIRED if k not in cols]
        if missing:
            problems.append(f"tab {tab!r}: columns missing: {', '.join(missing)}")
            continue
        for n, row in enumerate(rows[hi + 1:], start=hi + 2):
            rec = {k: _text(row[j], keep_lines=k in URL_KEYS) if j < len(row) else "" for k, j in cols.items()}
            if not any(rec.get(k) for k in ("indicator", "description", "content_url")):
                continue
            for k in URL_KEYS:
                rec[k] = " ".join(clean_url(rec[k]).split()) if rec.get(k) else ""
            rec["_tab"], rec["_line"] = tab, n
            records.append(rec)
    return records, problems, ignored


def validate(records):
    accepted, rejected = [], []
    seen = {}
    for rec in records:
        issues, warnings = diagnose(rec)
        errors = [f"{i['code']} {i['problem']}" for i in issues]
        did = dataset_id(rec.get("dcid", ""), rec.get("indicator", ""), rec.get("content_url", ""))
        key = (rec.get("dcid", ""), rec.get("indicator", "").lower(), rec.get("content_url", "").lower())
        if not errors:
            if key in seen:
                errors.append(f"N2 same country, indicator and data link as line {seen[key]}")
                issues.append(issue("N2", "content_url", f"same country, indicator and data link as line {seen[key]}",
                                    f"Delete this row, or give it a different indicator or data link (line {seen[key]} is kept).",
                                    rec.get("content_url")))
            else:
                seen[key] = rec["_line"]
        item = {"tab": rec["_tab"], "line": rec["_line"], "country": rec.get("country"),
                "indicator": rec.get("indicator"), "link": rec.get("original_content_url") or rec.get("content_url"),
                "errors": errors, "issues": issues, "warnings": warnings}
        if errors:
            rejected.append(item)
            continue
        accepted.append({**{k: v for k, v in rec.items() if not k.startswith("_")}, "id": did,
                         "name": f"{rec['indicator']} ({rec['country']})", "warnings": warnings,
                         "tab": rec["_tab"], "line": rec["_line"]})
    ids = collections.Counter(a["id"] for a in accepted)
    for a in accepted:
        if ids[a["id"]] > 1:
            a["id"] = f"{a['id']}_{a['line']}"
    return accepted, rejected


def needs_resolution(rec):
    link = rec.get("content_url", "")
    fmt = rec.get("format", "").lower()
    return known(link) and ("data:" in link or not any(f in fmt for f in FILE_FORMATS))


def apply_resolution(records, store, files_dir=None, site_url="", manual=None, today=None):
    cache = json.loads(store.read_text("catalogue/resolved.json") or "{}")
    today = today or refresh_date()
    stats = {"tried": 0, "found": 0}
    for rec in records:
        if not needs_resolution(rec):
            continue
        key = resolve.cache_key(rec["content_url"] + rec.get("indicator", "") + rec.get("country", ""))
        old = cache.get(key) or {}
        if old.get("download_url"):
            found = old
        elif old.get("failed") and old["failed"] >= (dt.date.fromisoformat(today) - dt.timedelta(days=7)).isoformat():
            found = None
        else:
            stats["tried"] += 1
            found = resolve.resolve(rec, files_dir, site_url, manual)
            cache[key] = found or {"failed": today}
        if found and found.get("download_url"):
            marker = "/custom_dc/custom/catalogue_data/"
            if site_url and marker in found["download_url"]:
                found["download_url"] = site_url.rstrip("/") + marker + found["download_url"].split(marker)[1]
                cache[key] = found
            stats["found"] += 1
            rec["original_content_url"] = rec["content_url"]
            rec["content_url"] = found["download_url"]
            rec["format"] = found["format"] if not any(f in rec["format"].lower() for f in FILE_FORMATS) else rec["format"]
            rec["resolved"] = {k: found.get(k) for k in ("method", "request", "found_on", "format")}
    store.write_text("catalogue/resolved.json", json.dumps(cache, indent=1, ensure_ascii=False) + "\n")
    return stats


def run(files, store, manual=None, files_dir=None, site_url=""):
    summary = {"files": {}, "accepted": 0, "rejected": 0, "resolved": {"tried": 0, "found": 0}}
    for path in files:
        records, problems, ignored = parse(path)
        stats = apply_resolution(records, store, files_dir, site_url, manual)
        summary["resolved"]["tried"] += stats["tried"]
        summary["resolved"]["found"] += stats["found"]
        accepted, rejected = validate(records)
        fid = re.sub(r"[^a-z0-9]+", "_", Path(path).stem.lower()).strip("_")[:50]
        base = f"catalogue/{fid}"
        store.write_text(f"{base}/datasets.json", json.dumps(accepted, indent=1, ensure_ascii=False) + "\n")
        store.write_text(f"{base}/rejected.json", json.dumps(rejected, indent=1, ensure_ascii=False) + "\n")
        reasons = collections.Counter(re.sub(r"\(now .*", "", e).strip()[:70] for r in rejected for e in r["errors"])
        methods = collections.Counter(a["resolved"]["method"] for a in accepted if a.get("resolved"))
        summary["files"][Path(path).name] = {"rows": len(records), "accepted": len(accepted),
                                            "resolved_methods": dict(methods.most_common()),
                                            "detail": {"accepted": accepted, "rejected": rejected}, "records": records,
                                            "rejected": len(rejected), "tabs_without_datasets": ignored,
                                            "file_problems": problems, "rejection_reasons": dict(reasons.most_common())}
        summary["accepted"] += len(accepted)
        summary["rejected"] += len(rejected)
    return summary


def log_text(summary, stamp):
    out = ["=" * 78, f" CATALOGUE LOAD  ·  {stamp}", "=" * 78]
    for name, f in summary["files"].items():
        accepted, rejected = f["detail"]["accepted"], f["detail"]["rejected"]
        out += ["", f"File: {name}", "-" * 78,
                f"  Rows read ............................ {f['rows']}",
                f"  Accepted (loaded) .................... {f['accepted']}",
                f"  Rejected (not loaded) ................ {f['rejected']}",
                f"  Data link resolved from a web page ... {sum(f['resolved_methods'].values())}"]
        for method, n in f["resolved_methods"].items():
            out.append(f"      {n:>4}  {method}")
        if f["tabs_without_datasets"]:
            out.append(f"  Tabs without dataset rows (ignored) .. {', '.join(f['tabs_without_datasets'])}")
        for prob in f["file_problems"]:
            out.append(f"  PROBLEM: {prob}")
        out += ["", "  WHY ROWS WERE REJECTED (rule, number of rows)"]
        for reason, n in f["rejection_reasons"].items():
            out.append(f"      {n:>4}  {reason}")
        out += ["", "  RESOLVED FROM WEB PAGES (Excel line · dataset · how · download link)"]
        for a in accepted:
            if a.get("resolved"):
                out.append(f"      line {a['line']:>4} · {a['name']} · {a['resolved']['method']}")
                out.append(f"                  {a['content_url']}")
        out += ["", "  REJECTED ROWS (Excel line · country · indicator · reason · link)"]
        for r in sorted(rejected, key=lambda x: x["line"]):
            out.append(f"      line {r['line']:>4} · {r['country']} · {r['indicator']}")
            for e in r["errors"]:
                out.append(f"                  - {e}")
            if r.get("link"):
                out.append(f"                  {str(r['link'])[:140]}")
        warn = collections.Counter(w.split(":")[0][:60] for a in accepted for w in a.get("warnings", []))
        if warn:
            out += ["", "  WARNINGS ON LOADED ROWS (number of rows)"]
            out += [f"      {n:>4}  {w}" for w, n in warn.most_common()]
    return "\n".join(out) + "\n"


def write_checked_workbook(source, accepted, rejected, out_path):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = openpyxl.load_workbook(source)
    for tab in wb.worksheets:
        hi, cells = find_header(read_rows(tab))
        if hi is None:
            continue
        cols = column_map(cells)
        header_row = hi + 1
        last = tab.max_column
        extra = ["Pipeline status", "Pipeline reason", "Original data link", "How the download link was found",
                 "Download request"]
        for i, title in enumerate(extra, start=last + 1):
            c = tab.cell(header_row, i, title)
            src = tab.cell(header_row, cols["content_url"] + 1)
            c.font, c.fill, c.alignment = Font(bold=True, color=src.font.color), PatternFill(src.fill.fill_type, src.fill.fgColor), Alignment(wrap_text=True)
            tab.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 38
        green, red = PatternFill("solid", fgColor="E2F0D9"), PatternFill("solid", fgColor="FBE4E4")
        by_line = {a["line"]: ("Loaded", a) for a in accepted if a["tab"] == tab.title}
        by_line.update({r["line"]: ("Not loaded", r) for r in rejected if r["tab"] == tab.title})
        for line, (status, item) in by_line.items():
            tab.cell(line, last + 1, status)
            if status == "Loaded":
                res = item.get("resolved")
                if res:
                    tab.cell(line, last + 3, str(item.get("original_content_url"))[:32000])
                    tab.cell(line, last + 4, res.get("method"))
                    if res.get("request"):
                        tab.cell(line, last + 5, json.dumps(res["request"]))
                    cell = tab.cell(line, cols["content_url"] + 1, item["content_url"])
                    cell.fill = green
                tab.cell(line, last + 1).fill = green
            else:
                tab.cell(line, last + 2, "; ".join(item["errors"]))
                tab.cell(line, last + 1).fill = red
                tab.cell(line, last + 2).fill = red
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def write_failure_workbook(source_name, records, accepted, rejected, out_path):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    by_line = {(r["_tab"], r["_line"]): r for r in records}
    wb = openpyxl.Workbook()
    head_fill, head_font = PatternFill("solid", fgColor="1F3864"), Font(bold=True, color="FFFFFF")

    def sheet(ws, headers, rows, widths):
        ws.append(headers)
        for c in ws[1]:
            c.fill, c.font, c.alignment = head_fill, head_font, Alignment(wrap_text=True, vertical="center")
        for row in rows:
            ws.append(row)
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    fixes = []
    for r in sorted(rejected, key=lambda x: (x["tab"], x["line"])):
        rec = by_line.get((r["tab"], r["line"]), {})
        for i in r["issues"]:
            fixes.append([r["tab"], r["line"], r["country"], r["indicator"], i["column"], i["code"], i["problem"],
                          i["value"], i["fix"], str(r.get("link") or "")[:500]])
    ws = wb.active
    ws.title = "To fix"
    sheet(ws, ["Tab", "Line in file", "Country", "Indicator", "Column", "Rule broken", "What is wrong",
               "Current value", "Fix needed", "Data link"], fixes, [14, 8, 16, 34, 26, 9, 50, 40, 60, 40])
    counts = collections.Counter((i["code"], i["column"]) for r in rejected for i in r["issues"])
    ws = wb.create_sheet("Summary by rule")
    sheet(ws, ["Rule", "Column", "Rows affected"], [[c, col, n] for (c, col), n in counts.most_common()], [10, 30, 14])
    warn = collections.Counter(w.split(":")[0][:80] for a in accepted for w in a.get("warnings", []))
    ws = wb.create_sheet("Warnings (loaded rows)")
    sheet(ws, ["Warning", "Rows"], [[w, n] for w, n in warn.most_common()], [90, 10])
    ws = wb.create_sheet("Run")
    sheet(ws, ["Item", "Value"], [["Source file", source_name], ["Rows read", len(records)],
                                  ["Loaded", len(accepted)], ["Not loaded", len(rejected)],
                                  ["Generated (UTC)", dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")]], [22, 60])
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def read_rows(ws):
    return [[c.value for c in row] for row in ws.iter_rows(min_row=1, max_row=10)]


def stored(store):
    out = []
    for key in sorted(store.list("catalogue")):
        if key.endswith("/datasets.json"):
            out += json.loads(store.read_text(key) or "[]")
    return out


def source_of(rec, reg):
    key = reg.source_key_for(rec["creator"])
    if key and key in reg.sources:
        s = reg.sources[key]
        return {"name": s["name"], "url": s["url"], "dcid": s["dcid"]}
    url = rec["creator_url"].split()[0]
    return {"name": rec["creator"], "url": url, "dcid": f"{reg.ns}Source_cat_{slug(rec['creator'])}"}


def dataset_dcid(rec, reg):
    return f"{reg.ns}Dataset_cat_{slug(rec['id'])}"


def full_description(rec):
    parts = [rec["description"].rstrip(".") + "."]
    parts.append(f"Country: {rec['country']}; domain: {rec['domain']}; geographic level: {rec['geo_level']}.")
    if known(rec.get("coverage")):
        parts.append(f"Temporal coverage: {rec['coverage']}.")
    if known(rec.get("frequency")):
        parts.append(f"Updated {FREQUENCIES.get(rec['frequency'].lower(), rec['frequency']).lower()}.")
    return " ".join(parts)


def mcf(records, reg):
    def q(text):
        return '"' + " ".join(str(text).split()).replace('"', "'") + '"'
    blocks, seen = [], set()
    for rec in records:
        src = source_of(rec, reg)
        if src["dcid"] not in seen:
            seen.add(src["dcid"])
            blocks.append("\n".join([f"Node: dcid:{src['dcid']}", "typeOf: dcid:Source", f"name: {q(src['name'])}",
                                     f"url: {q(src['url'])}"]))
        blocks.append("\n".join([f"Node: dcid:{dataset_dcid(rec, reg)}", "typeOf: dcid:Dataset",
                                 f"name: {q(rec['name'])}", f"description: {q(full_description(rec))}",
                                 f"url: {q(rec['url'].split()[0])}", f"license: {q(rec['license_url'].split()[0])}",
                                 f"isPartOf: dcid:{src['dcid']}"]))
    return "\n\n".join(blocks) + "\n" if blocks else ""


def refresh_date():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def published_date(rec):
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", rec.get("published", ""))
    return m.group(1) if m else None


def observation_years(coverage):
    years = [int(y) for y in re.findall(r"(?<!\d)(1[6-9]\d\d|20\d\d)(?!\d)", coverage or "")]
    return (str(min(years)), str(max(years))) if years else None


def add_provenances(records, reg, sources):
    taken = {p for s in sources.values() for p in s["provenances"]}
    today = refresh_date()
    pending = []
    for rec in records:
        src = source_of(rec, reg)
        entry = sources.setdefault(src["name"], {"url": src["url"], "provenances": {}})
        pname, n = rec["name"], 1
        while pname in taken:
            n += 1
            pname = f"{rec['name']} ({n})"
        taken.add(pname)
        entry["provenances"][pname] = rec["url"].split()[0]
        pending.append((pname, rec))
    return pending


def provenance_blocks(pending, reg, ids):
    today = refresh_date()
    blocks = []
    for pname, rec in pending:
        lines = [f"Node: dcid:{ids[pname]}", f"isPartOf: dcid:{dataset_dcid(rec, reg)}",
                 "provenanceCategory: dcid:StatisticsProvenance",
                 f'description: "{full_description(rec).replace(chr(34), chr(39))}"',
                 f'license: "{rec["license_url"].split()[0]}"', f'downloadUrl: "{rec["content_url"].split()[0]}"',
                 f'lastDataRefreshDate: "{published_date(rec) or today}"', f'earliestDataImportDate: "{today}"']
        span = observation_years(rec.get("coverage"))
        if span:
            lines += [f'earliestObservationDate: "{span[0]}"', f'latestObservationDate: "{span[1]}"']
        blocks.append("\n".join(lines) + "\n")
    return blocks


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--inbox", default=str(settings.DATA_DIR / "inbox" / "files"))
    p.add_argument("--store-dir", default=str(settings.STORE_DIR))
    a = p.parse_args(argv)
    files = [f for f in sorted(Path(a.inbox).iterdir())
             if f.suffix.lower() in (".xlsx", ".csv") and not f.name.startswith(("~$", "."))]
    import registry as registry_mod
    site_url = registry_mod.load().instance.get("site_url", "")
    summary = run(files, LocalStore(a.store_dir), resolve.overrides(settings.CONFIG_DIR),
                  settings.SITE_DIR / "static" / "catalogue_data", site_url)
    work = settings.WORK_DIR
    work.mkdir(parents=True, exist_ok=True)
    slim = {k: ({n: {kk: vv for kk, vv in f.items() if kk not in ("detail", "records")} for n, f in v.items()} if k == "files" else v)
            for k, v in summary.items()}
    (work / "catalogue_report.json").write_text(json.dumps(slim, indent=1, ensure_ascii=False) + "\n")
    for path in files:
        name = Path(path).name
        detail = summary["files"][name]["detail"]
        outbox = settings.DATA_DIR / "outbox"
        if Path(path).suffix.lower() == ".xlsx":
            write_checked_workbook(path, detail["accepted"], detail["rejected"], outbox / f"{Path(path).stem} - checked.xlsx")
        write_failure_workbook(name, summary["files"][name]["records"], detail["accepted"], detail["rejected"],
                               outbox / f"{Path(path).stem} - failures to fix.xlsx")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logs = settings.LOGS_DIR
    (logs / "history").mkdir(parents=True, exist_ok=True)
    text = log_text(summary, stamp)
    (logs / "catalogue.log").write_text(text, encoding="utf-8")
    (logs / "history" / f"catalogue_{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d_%H%M')}.log").write_text(
        text, encoding="utf-8")
    import runlog
    for name, f in summary["files"].items():
        top = ""
        found = summary["resolved"]
        print(runlog.item(f"Catalogue {name}", "ok",
                          f"{f['accepted']} datasets accepted, {f['rejected']} rejected" + (f" ({top})" if top else "")
                          + (f"; data links found on web pages: {found['found']}" if found["found"] else "")))
        for method, n in f["resolved_methods"].items():
            print(f"      resolved from web pages: {n:>3} x {method}")
        for reason, n in list(f["rejection_reasons"].items())[:6]:
            print(f"      not loaded:              {n:>3} x {reason}")
        print(f"      full row-by-row detail: logs/catalogue.log (resolved {found['found']}, rejected {f['rejected']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
