#!/usr/bin/env python3
import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import json
import re
import ssl
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import settings

HEADERS = {
    "id": "Provenance_id", "dataset": "Dataset DCID/Provenance_name", "name": "Dataset Name/Provenance_url",
    "source": "Source DCID", "source_url": "Source URL", "alt_url": "Alternative Source URL", "url": "URL",
    "data_url": "SourceDataUrl/Distribution.contentUrl", "desc_url": "Description URL",
    "format": "Source Format", "license": "License Type", "license_url": "License URL",
}
UNKNOWN = {"", "none", "not found", "n/a", "na", "nan", "-", "tbd"}
DATA_EXT = (".csv", ".tsv", ".xls", ".xlsx", ".json", ".geojson", ".xml", ".zip", ".gz", ".txt", ".parquet", ".ods", ".shp", ".kml")
DATA_MIME = ("csv", "excel", "spreadsheet", "json", "xml", "zip", "octet-stream", "gzip", "x-gzip", "plain", "parquet", "geo+json")
API_HINT = re.compile(r"(/api/|/api\?|[?&]format=(csv|json|xml)|/rest/|/sdmx|/odata|/v\d+(\.\d+)?/|\.json\b|\.csv\b|/export|/download|/feed/)", re.I)
BAD_LICENSE_PAGE = re.compile(r"privacy|accessibility|cookie|sitemap|contact|disclaimer-only|careers|login|signin", re.I)
LICENSE_WORDS = re.compile(r"licen[cs]e|terms of (use|service)|terms and conditions|creative commons|public domain|"
                           r"open data|reuse|re-use|copyright|attribution|permitted use|data policy", re.I)
STANDARD_LICENSE_HOSTS = ("creativecommons.org", "opendatacommons.org", "apache.org", "opensource.org", "spdx.org",
                          "nationalarchives.gov.uk", "data.gov.in", "publicdomain")
URL_TOKEN = re.compile(r"https?://[^\s,;]+", re.I)
LICENSE_TYPE = re.compile(r"^dcid:[A-Za-z0-9.\-]+$")
ctx = ssl.create_default_context()
ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE


def text(v):
    return "" if v is None else " ".join(str(v).split())


def known(v):
    return text(v).lower() not in UNKNOWN


def urls_in(value):
    return [u.rstrip(".)") for u in URL_TOKEN.findall(str(value or ""))]


def is_homepage(url):
    p = urlsplit(url)
    return p.path in ("", "/") and not p.query and not p.fragment


class Prober:
    def __init__(self, workers=24, per_host=3, timeout=20):
        self.cache, self.timeout = {}, timeout
        self.hosts, self.lock = collections.defaultdict(lambda: threading.Semaphore(per_host)), threading.Lock()
        self.workers = workers

    def one(self, url, want_body):
        host = urlsplit(url).netloc.lower()
        with self.hosts[host]:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; datacommon-project-validator/1.0)",
                                                       "Accept": "*/*"})
            out = {"url": url}
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as r:
                    head = r.read(300_000 if want_body else 2048)
                    out.update(status=r.status, final=r.geturl(), type=(r.headers.get("Content-Type") or "").lower(),
                               disposition=r.headers.get("Content-Disposition") or "", head=head)
            except urllib.error.HTTPError as e:
                out.update(status=e.code, final=url, type=(e.headers.get("Content-Type") or "").lower() if e.headers else "", head=b"")
            except Exception as e:
                out.update(status=0, final=url, type="", head=b"", error=f"{type(e).__name__}: {str(e)[:120]}")
            return out

    def run(self, jobs):
        todo = {u: w for u, w in jobs.items() if u not in self.cache}
        with cf.ThreadPoolExecutor(self.workers) as ex:
            futs = {ex.submit(self.one, u, w): u for u, w in todo.items()}
            for i, f in enumerate(cf.as_completed(futs), 1):
                self.cache[futs[f]] = f.result()
                if i % 100 == 0:
                    print(f"  probed {i}/{len(todo)}", flush=True)


def reach(res):
    s = res["status"]
    if s == 0:
        return "unreachable", res.get("error", "no response")
    if s in (401, 403, 429, 999):
        return "blocked", f"HTTP {s}: the server refuses automated requests; open it in a browser to confirm"
    if s >= 400:
        return "broken", f"HTTP {s}"
    return "ok", f"HTTP {s}"


def looks_like_data(url, res):
    path = urlsplit(res["final"] or url).path.lower()
    ctype = res.get("type", "")
    head = res.get("head", b"")[:600].lstrip().lower()
    if "html" in ctype or head.startswith((b"<!doctype html", b"<html")):
        return False, "returns a web page (HTML), not a data file"
    if path.endswith(DATA_EXT) or any(m in ctype for m in DATA_MIME) or "attachment" in res.get("disposition", "").lower():
        return True, ""
    if API_HINT.search(url):
        return True, ""
    return False, f"content type {ctype or 'unknown'} does not look like a data file or API"


def clean_type(value):
    t = re.sub(r"\s+", "", text(value).replace("dcid:", "", 1) if text(value).lower().startswith("dcid:") else text(value))
    t = re.sub(r"^dcid:", "", t)
    return f"dcid:{t}" if t else ""


def check(rows, header, prober, out):
    idx = {k: header.index(v) for k, v in HEADERS.items() if v in header}
    def cell(r, k):
        return r[idx[k]] if k in idx and idx[k] < len(r) else None
    def add(line, pid, col, check_name, finding, value, fix, severity="Error"):
        out.append([line, text(pid), col, check_name, severity, finding, text(value)[:300], fix])

    jobs = {}
    for r in rows:
        for k in ("source_url", "alt_url", "url", "data_url", "desc_url", "license_url"):
            for u in urls_in(cell(r[1], k)):
                jobs[u] = jobs.get(u, False) or k == "license_url"
    print(f"Probing {len(jobs)} distinct URLs ...", flush=True)
    prober.run(jobs)

    for line, r in rows:
        pid = cell(r, "id")
        for k, label in (("source_url", HEADERS["source_url"]), ("alt_url", HEADERS["alt_url"]), ("url", HEADERS["url"]),
                         ("desc_url", HEADERS["desc_url"])):
            v = cell(r, k)
            if not known(v):
                if k == "source_url":
                    add(line, pid, label, "Source URL", "Source URL is empty", v,
                        "Add the link to the publisher's page for this dataset (the data landing page, not the homepage).")
                continue
            us = urls_in(v)
            if not us:
                add(line, pid, label, "URL accessibility", "Not a URL", v, "Replace with a full https:// link.")
                continue
            for u in us:
                state, why = reach(prober.cache[u])
                if state != "ok":
                    add(line, pid, label, "URL accessibility", f"{state}: {why}", u,
                        "Open the link in a browser; if the page moved, replace it with the current address."
                        if state != "blocked" else "Open the link in a browser; if it loads, keep it and note it as bot-protected.",
                        "Warning" if state == "blocked" else "Error")
                elif k == "source_url" and is_homepage(u):
                    add(line, pid, label, "Source URL", "Link is a generic homepage, not the dataset's landing page", u,
                        "Replace with the page that describes this dataset (its own dataset/indicator/catalogue page).")
                elif k == "source_url" and is_homepage(prober.cache[u]["final"]) and not is_homepage(u):
                    add(line, pid, label, "Source URL", "Link redirects to the site's homepage", u,
                        f"The address redirects to {prober.cache[u]['final']}; find the dataset's actual landing page.", "Warning")

        v = cell(r, "data_url")
        label = HEADERS["data_url"]
        if not known(v):
            add(line, pid, label, "Data link", "Distribution.contentUrl is empty", v,
                "Add the direct link to the data file or API endpoint, or mark the dataset as not downloadable.")
        else:
            us = urls_in(v)
            if not us:
                add(line, pid, label, "Data link", "Not a URL (text instead of a link)", v,
                    "Replace with the direct https:// link to the file or API; move explanatory text to Description.")
            for u in us:
                res = prober.cache[u]
                state, why = reach(res)
                if state != "ok":
                    add(line, pid, label, "URL accessibility", f"{state}: {why}", u,
                        "Replace with a working direct download or API link." if state != "blocked"
                        else "Open in a browser to confirm it downloads; keep if it does.",
                        "Warning" if state == "blocked" else "Error")
                    continue
                ok, why = looks_like_data(u, res)
                if not ok:
                    add(line, pid, label, "Data link", why, u,
                        "Open the page, find the real file or data API (CSV, XLS(X), JSON, ZIP) and paste that direct link.")

        v = cell(r, "license")
        label = HEADERS["license"]
        raw = text(v)
        if not known(v):
            add(line, pid, label, "License type", "License type is empty or 'Not Found'", v,
                "Record the licence the publisher states for this dataset (never guessed). If none is stated, leave the row unloaded.")
        elif not LICENSE_TYPE.match(raw):
            norm = clean_type(raw)
            if raw.lower().startswith(("all rights", "custom", "standard government")) or not norm.startswith("dcid:"):
                add(line, pid, label, "License type", "Not a standard Data Commons licence value", raw,
                    "Use a dcid: licence value (e.g. dcid:PublicDomain, dcid:CreativeCommonsAttribution) that matches the "
                    "publisher's stated terms; if none fits, ask the Data Commons team to add one.")
            else:
                add(line, pid, label, "License type", "Licence value is not in the 'dcid:Name' form (spaces or doubled prefix)", raw,
                    f"Rewrite as {norm} (no spaces after 'dcid:').", "Warning")
        elif "/" in raw:
            add(line, pid, label, "License type", "Combines two licences in one value", raw,
                "Use one licence value for the dataset.", "Warning")

        v = cell(r, "license_url")
        label = HEADERS["license_url"]
        if not known(v):
            add(line, pid, label, "License URL", "License URL is empty or 'Not Found'", v,
                "Add the link to the page where the publisher states the licence/terms for this dataset.")
        else:
            us = urls_in(v)
            if not us:
                add(line, pid, label, "License URL", "Not a URL (text instead of a link)", v,
                    "Replace with the full https:// link to the licence or terms page; move the text to Description.")
            for u in us:
                res = prober.cache[u]
                state, why = reach(res)
                if state != "ok":
                    add(line, pid, label, "URL accessibility", f"{state}: {why}", u, "Replace with the publisher's current licence/terms page.",
                        "Warning" if state == "blocked" else "Error")
                    continue
                path = urlsplit(u).path + " " + urlsplit(u).query
                if BAD_LICENSE_PAGE.search(path):
                    add(line, pid, label, "License URL", "Link looks like a privacy/accessibility/contact page, not licensing terms", u,
                        "Replace with the page that states the dataset's licence or terms of use.")
                    continue
                known_host = any(h in urlsplit(u).netloc.lower() + urlsplit(u).path.lower() for h in STANDARD_LICENSE_HOSTS)
                if not known_host:
                    body = res.get("head", b"").decode("utf-8", "ignore")
                    title = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
                    if is_homepage(u):
                        add(line, pid, label, "License URL", "Link is a generic homepage, not a licensing page", u,
                            "Replace with the page that states the licence or terms.")
                    elif body and not LICENSE_WORDS.search(body) and "html" in res["type"]:
                        add(line, pid, label, "License URL", "Page text contains no licence or terms wording", u,
                            "Confirm the page states the licence; otherwise replace with the page that does."
                            + (f" (page title: {text(title.group(1))[:80]})" if title else ""), "Warning")
                lt = raw.lower()
                if "creativecommons.org" in u.lower():
                    for token, frag in (("sharealike", "/by-sa/"), ("zero", "/zero/")):
                        if token in lt and frag not in u.lower():
                            add(line, pid, label, "License type vs URL", f"Licence type says {token} but the URL is a different licence", f"{raw} | {u}",
                                "Make the licence type and the licence URL name the same licence.")

    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("file")
    p.add_argument("--tab", default="Provenance Output")
    a = p.parse_args(argv)
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = openpyxl.load_workbook(a.file, read_only=True, data_only=True)
    allrows = [list(r) for r in wb[a.tab].iter_rows(values_only=True)]
    header = [text(c) for c in allrows[0]]
    rows = [(n, r) for n, r in enumerate(allrows[1:], start=2) if any(c not in (None, "") for c in r[:16])]
    out = []
    prober = Prober()
    check(rows, header, prober, out)
    sev = {"Error": 0, "Warning": 1}
    out.sort(key=lambda x: (sev[x[4]], x[3], x[0]))
    res = openpyxl.Workbook()
    head_fill, head_font = PatternFill("solid", fgColor="1F3864"), Font(bold=True, color="FFFFFF")

    def sheet(ws, headers, data, widths):
        ws.append(headers)
        for c in ws[1]:
            c.fill, c.font, c.alignment = head_fill, head_font, Alignment(wrap_text=True)
        for row in data:
            ws.append(row)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    ws = res.active
    ws.title = "To fix"
    sheet(ws, ["Row", "Provenance_id", "Column", "Check", "Severity", "Finding", "Current value", "Recommended correction"],
          out, [6, 34, 30, 20, 9, 52, 52, 70])
    by = collections.Counter((o[3], o[4], o[5].split(":")[0][:60]) for o in out)
    sheet(res.create_sheet("Summary"), ["Check", "Severity", "Finding", "Count"],
          [[c, s, f, n] for (c, s, f), n in by.most_common()], [22, 10, 70, 8])
    bad_rows = len({o[0] for o in out})
    sheet(res.create_sheet("Run"), ["Item", "Value"],
          [["File", Path(a.file).name], ["Tab", a.tab], ["Rows checked", len(rows)], ["Rows with a finding", bad_rows],
           ["Rows clean", len(rows) - bad_rows], ["Distinct URLs probed", len(prober.cache)],
           ["Generated (UTC)", dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")]], [26, 60])
    target = settings.DATA_DIR / "outbox" / f"{Path(a.file).stem} - validation.xlsx"
    target.parent.mkdir(parents=True, exist_ok=True)
    res.save(target)
    print(f"{len(rows)} rows, {bad_rows} with findings, {len(out)} findings -> {target}")
    for (c, s, f), n in by.most_common(40):
        print(f"  {n:>5}  {s:<7} {c}: {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
