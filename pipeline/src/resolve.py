import base64
import hashlib
import io
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

import settings

DATA_EXT = re.compile(r"\.(csv|xlsx?|json|tsv|zip|xml)(\?|#|$)", re.I)
FORMATS = {"csv": "CSV", "xls": "XLS", "xlsx": "XLSX", "json": "JSON", "tsv": "TSV", "zip": "ZIP", "xml": "XML"}
MAX_BYTES = 3_000_000
API_HINT = re.compile(r"(/api/|/download|/export|/rest/|format=(csv|json|xls))", re.I)
HREF = re.compile(r"""(?:href|src|data-url|data-href|content)\s*=\s*["']([^"'#][^"']*)["']""", re.I)
ABSOLUTE = re.compile(r"""https?://[^\s"'<>\\)]+""")
DATABROWSER = re.compile(r"^(?P<root>https?://[^#]*?)databrowser/?#/.*?(?P<flow>[A-Za-z0-9_.\-]+,[A-Za-z0-9_.\-]+,[0-9.]+)/?$")
OWID = re.compile(r"^(https?://ourworldindata\.org/grapher/[A-Za-z0-9\-_]+)(\?.*)?$")
CKAN = re.compile(r"^(?P<origin>https?://[^/]+)(?P<prefix>/[a-z]{2})?/dataset/(?P<name>[A-Za-z0-9\-_]+)(?:/resource/(?P<rid>[0-9a-f\-]{36}))?")


def http(url, data=None, headers=None, timeout=45):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": settings.USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read(MAX_BYTES)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", b""
    except Exception as exc:
        logging.info("resolve: %s: %s", url[:100], exc)
        return 0, "", b""


def sniff(body, content_type=""):
    if not body:
        return None
    head = body[:600].lstrip()
    if body[:2] == b"PK":
        return "XLSX" if b"xl/" in body[:4000] or b"[Content_Types]" in body[:4000] else "ZIP"
    if head[:1] in (b"<",) or b"<html" in head.lower():
        return None
    if head[:1] in (b"{", b"["):
        try:
            doc = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            return None
        if isinstance(doc, dict) and ("value" in doc and "id" in doc and "size" in doc):
            return "JSON-stat"
        if isinstance(doc, dict) and doc.get("success") is False:
            return None
        return "JSON" if doc else None
    text = body[:4000].decode("utf-8", "replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 2 and all(("," in ln or ";" in ln or "\t" in ln) for ln in lines[:3]):
        return "CSV"
    return None


def from_data_uri(url, name, files_dir, site_url):
    start = url.find("data:")
    if start < 0 or not files_dir:
        return None
    head, _, payload = url[start + 5:].partition(",")
    if not payload:
        return None
    raw = base64.b64decode(payload) if head.endswith(";base64") else urllib.parse.unquote_to_bytes(payload)
    fmt = sniff(raw)
    if fmt not in ("CSV", "JSON"):
        return None
    ext = "csv" if fmt == "CSV" else "json"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / f"{name}.{ext}").write_bytes(raw)
    return {"download_url": f"{site_url.rstrip('/')}/custom_dc/custom/catalogue_data/{name}.{ext}", "format": fmt,
            "method": "inline data: link saved as a file", "request": None}


def from_databrowser(url, *_):
    m = DATABROWSER.match(url)
    if not m:
        return None
    flow = m.group("flow")
    hub = m.group("root") + "databrowserhub/api/Core/nodes/{}/datasets/" + urllib.parse.quote(flow, safe=",.") + "/data"
    for node in (1, 2, 3):
        endpoint = hub.format(node)
        status, ctype, body = http(endpoint, data=b"[]", headers={"Content-Type": "application/json"})
        if status == 200 and sniff(body, ctype) == "JSON-stat":
            return {"download_url": endpoint, "format": "JSON-stat", "method": "Data Browser hub API (POST [])",
                    "request": {"method": "POST", "body": "[]", "content_type": "application/json"}}
    return None


def from_owid(url, *_):
    m = OWID.match(url)
    if not m:
        return None
    candidate = m.group(1) + ".csv"
    status, ctype, body = http(candidate)
    if status == 200 and sniff(body, ctype) == "CSV":
        return {"download_url": candidate, "format": "CSV", "method": "Our World in Data CSV", "request": None}
    return None


def from_fred(url, *_):
    m = re.match(r"^https?://fred\.stlouisfed\.org/series/([A-Za-z0-9_]+)", url)
    if not m:
        return None
    candidate = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={m.group(1)}"
    status, ctype, body = http(candidate)
    if status == 200 and sniff(body, ctype) == "CSV":
        return {"download_url": candidate, "format": "CSV", "method": "FRED series CSV", "request": None}
    return None


def from_ckan(url, *_):
    m = CKAN.match(url)
    if not m:
        return None
    api = f"{m.group('origin')}/api/3/action/package_show?id={m.group('name')}"
    status, ctype, body = http(api)
    if status != 200:
        return None
    try:
        resources = json.loads(body.decode("utf-8", "replace"))["result"]["resources"]
    except (ValueError, KeyError, TypeError):
        return None
    ranked = sorted(resources, key=lambda r: (r.get("id") != m.group("rid"),
                                              (r.get("format") or "").lower() not in ("csv", "xlsx", "xls", "json")))
    for res in ranked[:5]:
        link = res.get("url") or ""
        if not link.startswith("http"):
            continue
        fmt = FORMATS.get((res.get("format") or "").lower()) or sniff_format_from_url(link)
        if fmt or API_HINT.search(link):
            status, ctype, body = http(link)
            if status == 200 and sniff(body, ctype):
                return {"download_url": link, "format": sniff(body, ctype), "method": "CKAN package resource",
                        "request": None}
    return None


def sniff_format_from_url(url):
    m = DATA_EXT.search(url)
    return FORMATS.get(m.group(1).lower()) if m else None


def from_page(url, *_):
    status, ctype, body = http(url)
    if status != 200 or "html" not in ctype.lower() and not body[:1] == b"<":
        return None
    page = body.decode("utf-8", "replace")
    found = []
    for raw in HREF.findall(page) + ABSOLUTE.findall(page):
        link = urllib.parse.urljoin(url, raw.replace("&amp;", "&"))
        if link not in found and link.startswith("http") and (DATA_EXT.search(link) or API_HINT.search(link)):
            found.append(link)
    for link in found[:15]:
        status, ctype, body = http(link)
        fmt = sniff(body, ctype) if status == 200 else None
        if fmt:
            return {"download_url": link, "format": fmt, "method": "download link found on the page", "request": None}
    return None


STRATEGIES = (from_data_uri, from_databrowser, from_owid, from_fred, from_ckan, from_page)


def overrides(config_dir):
    import yaml
    path = config_dir / "data_links.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def cache_key(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def resolve(rec, files_dir=None, site_url="", manual=None):
    """Find a real data download for a row whose data link is a web page or an inline data: link."""
    page = (rec.get("content_url") or "").split()
    page = " ".join(page)
    name = cache_key(page + rec.get("indicator", "") + rec.get("country", ""))
    manual = manual or {}
    hit = manual.get(page) or manual.get(f"{rec.get('country')}|{rec.get('indicator')}")
    if hit:
        return {"format": "JSON", "method": "recorded in config/data_links.yaml", "request": None, **hit}
    candidates = [page] + [u for u in rec.get("url", "").split() if u != page]
    for candidate in candidates:
        for strategy in STRATEGIES:
            try:
                found = strategy(candidate, name, files_dir, site_url)
            except Exception as exc:
                logging.info("resolve: %s failed on %s: %s", strategy.__name__, candidate[:80], exc)
                found = None
            if found:
                return {**found, "found_on": candidate}
    return None
