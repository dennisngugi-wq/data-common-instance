import json

import catalog
import resolve


def test_inline_data_link_is_saved_as_a_downloadable_file(tmp_path):
    url = "data:text/csv;charset=utf-8,Year,Value%0A2019,4.5%0A2020,5.1"
    found = resolve.from_data_uri(url, "abc", tmp_path, "https://dc.example.org")
    assert found["format"] == "CSV"
    assert found["download_url"] == "https://dc.example.org/custom_dc/custom/catalogue_data/abc.csv"
    assert (tmp_path / "abc.csv").read_text().startswith("Year,Value")


def test_web_page_is_not_data():
    assert resolve.sniff(b"<!doctype html><html><body>hi</body></html>") is None
    assert resolve.sniff(b"a,b\n1,2\n3,4\n") == "CSV"
    assert resolve.sniff(json.dumps({"id": ["A"], "size": [1], "value": {"0": 1}}).encode()) == "JSON-stat"


def test_databrowser_page_resolves_to_its_data_api(monkeypatch):
    calls = []

    def fake(url, data=None, headers=None, timeout=45):
        calls.append((url, data))
        body = json.dumps({"id": ["A"], "size": [1], "value": {"0": 1}}).encode()
        return 200, "application/json", body

    monkeypatch.setattr(resolve, "http", fake)
    page = "https://opendata.knbs.or.ke/beta/databrowser/#/en/knbs/categories/KE1,CAT_DOMAIN_2,1.0/205/20501/KE1,DF_CPI_YR,1.0"
    found = resolve.from_databrowser(page)
    assert found["download_url"] == "https://opendata.knbs.or.ke/beta/databrowserhub/api/Core/nodes/1/datasets/KE1,DF_CPI_YR,1.0/data"
    assert found["request"]["body"] == "[]" and calls[0][1] == b"[]"


def test_page_with_a_download_link_is_resolved(monkeypatch):
    def fake(url, data=None, headers=None, timeout=45):
        if "files/data.csv" in url:
            return 200, "text/csv", b"a,b\n1,2\n"
        return 200, "text/html", b'<html><a href="/files/data.csv">Download</a> <a href="/about">About</a></html>'

    monkeypatch.setattr(resolve, "http", fake)
    assert resolve.from_page("https://example.org/page")["download_url"] == "https://example.org/files/data.csv"


def test_unresolved_row_is_rejected_and_resolved_row_accepted(monkeypatch, tmp_path):
    from storage import LocalStore
    base = dict(catalog_row := {
        "country": "Kenya", "dcid": "country/KEN", "domain": "Economy", "indicator": "CPI",
        "description": "Year on year inflation by region published by the national statistics office each month.",
        "creator": "KNBS", "creator_url": "https://www.knbs.or.ke/", "url": "https://opendata.knbs.or.ke/page",
        "content_url": "https://opendata.knbs.or.ke/page", "format": "Web based", "geo_level": "National",
        "license": "Open", "license_url": "https://example.org/terms", "published": "N/A", "coverage": "",
        "frequency": "", "_tab": "Africa", "_line": 2})
    store = LocalStore(tmp_path)
    monkeypatch.setattr(resolve, "resolve", lambda rec, *a, **k: None)
    rec = dict(base)
    catalog.apply_resolution([rec], store, today="2026-11-18")
    assert not catalog.validate([rec])[0]
    monkeypatch.setattr(resolve, "resolve", lambda rec, *a, **k: {
        "download_url": "https://opendata.knbs.or.ke/api/data", "format": "JSON-stat", "method": "m", "request": None})
    rec2 = dict(base)
    catalog.apply_resolution([rec2], LocalStore(tmp_path / "b"), today="2026-11-18")
    accepted = catalog.validate([rec2])[0]
    assert accepted and accepted[0]["content_url"] == "https://opendata.knbs.or.ke/api/data"
    assert accepted[0]["format"] == "JSON-stat"
