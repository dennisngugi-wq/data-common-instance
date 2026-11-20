import csv
import datetime as dt
import hashlib
import io
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import settings


class FetchError(Exception):
    pass


@dataclass
class Table:
    header: list
    rows: list
    content_url: str
    sha256: str
    raw: bytes
    media_type: str


def _env(text):
    def sub(m):
        if m.group(1) not in os.environ:
            raise FetchError(f"environment variable {m.group(1)} is not set (add it to env.list)")
        return os.environ[m.group(1)]
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", sub, str(text))


def http_get(url, headers=None, method="GET", body=None):
    hdrs = {"User-Agent": settings.USER_AGENT, "Accept": "*/*"}
    hdrs.update({k: _env(v) for k, v in (headers or {}).items()})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    last = None
    for attempt in range(1, settings.HTTP_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs, data=data, method=method)
            with urllib.request.urlopen(req, timeout=settings.HTTP_TIMEOUT) as r:
                return r.read(), r.headers.get_content_type(), r.geturl()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (400, 401, 403, 404, 410) and not (method == "POST" and exc.code == 403):
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        if attempt < settings.HTTP_RETRIES:
            wait = 10 * attempt
            logging.warning("GET %s failed (%s); retry %d in %ds", url[:100], last, attempt, wait)
            time.sleep(wait)
    raise FetchError(f"GET {url[:150]} failed: {last}")


def _read_source(spec):
    if spec.get("url"):
        data, media, final = http_get(spec["url"], spec.get("headers"))
        return data, media, spec["url"]
    if spec.get("path"):
        path = (settings.PROJECT_ROOT / spec["path"]).resolve()
        if settings.DATA_DIR.resolve() not in path.parents:
            raise FetchError(f"path {spec['path']} must be inside the project's data/ folder")
        if not path.exists():
            raise FetchError(f"file {spec['path']} not found")
        return path.read_bytes(), "", ""
    raise FetchError("fetch needs a url or a path")


def fetch(spec):
    kind = spec.get("type")
    if kind == "google_sheet":
        sheet_id = str(spec.get("sheet_id", "")).strip()
        if not re.match(r"^[A-Za-z0-9_\-]{20,}$", sheet_id):
            raise FetchError("google_sheet needs sheet_id (the long id in the sheet's URL)")
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
        if spec.get("gid") is not None and str(spec.get("gid")).strip() != "":
            url += f"&gid={int(spec['gid'])}"
        try:
            data, media, _ = http_get(url, spec.get("headers"))
        except FetchError as exc:
            if "400" in str(exc) or "404" in str(exc):
                raise FetchError(f"Google could not export the sheet: check sheet_id and gid (the number after "
                                 f"#gid= in the tab's URL; leave gid out for the first tab). {exc}") from exc
            raise
        if media == "text/html" or data.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
            raise FetchError("Google returned a web page instead of CSV: the sheet is not shared publicly "
                             "(Share > Anyone with the link: Viewer) or the gid does not exist")
        return _csv_table(data, spec, url, "text/csv")
    data, media, url = _read_source(spec)
    return parse_bytes(spec, data, url)


def parse_bytes(spec, data, url=""):
    kind = spec.get("type")
    if kind in ("csv", "google_sheet"):
        return _csv_table(data, spec, url, "text/csv")
    if kind == "excel":
        return _excel_table(data, spec, url)
    if kind == "json":
        return _json_table(data, spec, url)
    if kind == "html_table":
        return _html_table(data, spec, url)
    raise FetchError(f"unknown fetch type {kind!r}")


def _decode(data, encoding=None):
    if encoding:
        return data.decode(encoding)
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise FetchError("cannot decode the file as text")


def _from_grid(grid, spec, url, raw, media):
    header_row = int(spec.get("header_row", 1))
    if len(grid) < header_row:
        raise FetchError(f"the file has fewer than {header_row} rows: no header found")
    header = []
    for i, h in enumerate(grid[header_row - 1]):
        h = "" if h is None else str(h).strip()
        if isinstance(grid[header_row - 1][i], float) and grid[header_row - 1][i].is_integer():
            h = str(int(grid[header_row - 1][i]))
        header.append(h or f"column_{i + 1}")
    seen = {}
    for i, h in enumerate(header):
        if h in seen:
            seen[h] += 1
            header[i] = f"{h}_{seen[h]}"
        else:
            seen[h] = 1
    rows = []
    stop_blank = spec.get("stop_at_blank_row", spec.get("type") == "excel")
    for cells in grid[header_row:]:
        if all(c is None or str(c).strip() == "" for c in cells):
            if stop_blank and rows:
                break
            continue
        rows.append({h: (cells[i] if i < len(cells) else None) for i, h in enumerate(header)})
    return Table(header=header, rows=rows, content_url=url, sha256=hashlib.sha256(raw).hexdigest(), raw=raw,
                 media_type=media)


def _csv_table(data, spec, url, media):
    text = _decode(data, spec.get("encoding"))
    delimiter = spec.get("delimiter")
    if not delimiter:
        try:
            delimiter = csv.Sniffer().sniff(text[:5000], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    grid = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return _from_grid(grid, spec, url, data, media)


def _excel_table(data, spec, url):
    import openpyxl

    if data[:4] != b"PK\x03\x04":
        raise FetchError("not an .xlsx/.xlsm workbook (old .xls files: open and save as .xlsx)")
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheet = spec.get("sheet")
    if sheet is None:
        ws = wb.worksheets[0]
    elif isinstance(sheet, int):
        ws = wb.worksheets[sheet]
    elif sheet in wb.sheetnames:
        ws = wb[sheet]
    else:
        raise FetchError(f"sheet {sheet!r} not found; sheets: {wb.sheetnames}")
    grid = []
    for row in ws.iter_rows(values_only=True):
        grid.append([c.date().isoformat() if isinstance(c, dt.datetime) else
                     c.isoformat() if isinstance(c, dt.date) else c for c in row])
    return _from_grid(grid, spec, url,
                      data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}{k}."))
    else:
        out[prefix[:-1]] = obj if not isinstance(obj, list) else json.dumps(obj)
    return out


def _json_table(data, spec, url):
    try:
        doc = json.loads(_decode(data))
    except json.JSONDecodeError as exc:
        raise FetchError(f"response is not JSON: {exc}") from exc
    node = doc
    for part in [p for p in str(spec.get("records", "")).split(".") if p]:
        try:
            node = node[int(part)] if isinstance(node, list) else node[part]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise FetchError(f"records path {spec.get('records')!r} not found in the JSON ({part!r})") from exc
    if not isinstance(node, list):
        raise FetchError("records must point to a list of records")
    rows = [_flatten(r) if isinstance(r, dict) else {"value": r} for r in node]
    header = []
    for r in rows:
        for k in r:
            if k not in header:
                header.append(k)
    return Table(header=header, rows=rows, content_url=url, sha256=hashlib.sha256(data).hexdigest(), raw=data,
                 media_type="application/json")


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._stack = [], []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append({"rows": [], "row": None, "cell": None})
        elif self._stack:
            t = self._stack[-1]
            if tag == "tr":
                t["row"] = []
            elif tag in ("td", "th") and t["row"] is not None:
                t["cell"] = []

    def handle_endtag(self, tag):
        if not self._stack:
            return
        t = self._stack[-1]
        if tag in ("td", "th") and t["cell"] is not None:
            t["row"].append(" ".join("".join(t["cell"]).split()))
            t["cell"] = None
        elif tag == "tr" and t["row"] is not None:
            if t["row"]:
                t["rows"].append(t["row"])
            t["row"] = None
        elif tag == "table":
            self.tables.append(self._stack.pop()["rows"])

    def handle_data(self, data):
        if self._stack and self._stack[-1]["cell"] is not None:
            self._stack[-1]["cell"].append(data)


def _html_table(data, spec, url):
    parser = _Tables()
    parser.feed(_decode(data))
    tables = parser.tables
    if not tables:
        raise FetchError("no <table> found on the page (tables drawn by JavaScript are not visible to the pipeline)")
    if spec.get("match"):
        hits = [t for t in tables if any(spec["match"] in c for r in t for c in r)]
        if not hits:
            raise FetchError(f"no table contains {spec['match']!r}")
        grid = hits[0]
    else:
        idx = int(spec.get("table", 0))
        if idx >= len(tables):
            raise FetchError(f"table {idx} requested, the page has {len(tables)}")
        grid = tables[idx]
    return _from_grid(grid, spec, url, data, "text/html")


def save_raw(table, folder: Path, feed_id):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{feed_id}.raw").write_bytes(table.raw)
    meta = {"content_url": table.content_url, "sha256": table.sha256, "media_type": table.media_type,
            "rows": len(table.rows), "header": table.header}
    (folder / f"{feed_id}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def fetch_raw(spec):
    if spec.get("url"):
        method = str(spec.get("method", "GET")).upper()
        data, media, _ = http_get(spec["url"], spec.get("headers"), method=method,
                                  body=spec.get("body") if method == "POST" else None)
        return data, spec["url"]
    data, _, url = _read_source(spec)
    return data, url
