import catalog

GOOD = {
    "country": "Kenya", "dcid": "country/KEN", "domain": "Energy", "indicator": "Electricity access",
    "description": "Share of the Kenyan population with access to electricity, published every year by the agency.",
    "creator": "Kenya National Bureau of Statistics", "creator_url": "https://www.knbs.or.ke/",
    "url": "https://www.knbs.or.ke/energy", "content_url": "https://www.knbs.or.ke/energy.xlsx",
    "format": "XLS", "geo_level": "National", "license": "Open", "license_url": "https://example.org/terms",
    "published": "N/A", "coverage": "", "frequency": "Annual", "_tab": "Africa", "_line": 2,
}


def test_good_row_is_accepted_with_warnings():
    accepted, rejected = catalog.validate([dict(GOOD)])
    assert len(accepted) == 1 and not rejected
    assert accepted[0]["name"] == "Electricity access (Kenya)"
    assert any("datePublished" in w for w in accepted[0]["warnings"])


def test_data_uri_and_missing_licence_are_rejected():
    bad = dict(GOOD, content_url="data:text/csv;charset=utf-8,a,b", license_url="")
    accepted, rejected = catalog.validate([bad])
    assert not accepted
    text = " ".join(rejected[0]["errors"])
    assert "C6" in text and "License URL" in text


def test_short_description_and_bad_dcid_are_rejected():
    accepted, rejected = catalog.validate([dict(GOOD, description="Too short", dcid="Kenya")])
    assert not accepted
    text = " ".join(rejected[0]["errors"])
    assert "C2" in text and "CAT3" in text


def test_duplicate_row_is_rejected():
    accepted, rejected = catalog.validate([dict(GOOD), dict(GOOD, _line=3)])
    assert len(accepted) == 1 and "N2" in rejected[0]["errors"][0]


def test_several_links_in_one_cell_are_checked():
    ok = dict(GOOD, content_url="https://a.org/x.csv https://b.org/y.csv")
    assert catalog.validate([ok])[0]
    bad = dict(GOOD, content_url="https://a.org/x.csv see page")
    assert not catalog.validate([bad])[0]


def test_inbox_catalogue_workbook_is_not_read_as_a_data_sheet(tmp_path):
    import openpyxl
    import sheets
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Region", "Country", "Indicator", "distribution.contentURL", "License URL"])
    path = tmp_path / "inbox" / "files" / "africa.xlsx"
    path.parent.mkdir(parents=True)
    wb.save(path)
    (tmp_path / "config").mkdir()
    assert sheets._file_entries(tmp_path / "config", tmp_path / "inbox", set(), {}) == []


def test_web_page_only_format_is_rejected():
    accepted, rejected = catalog.validate([dict(GOOD, format="Web based")])
    assert not accepted and "C6" in rejected[0]["errors"][0]


def test_provenance_node_follows_the_design_doc():
    import registry
    reg = registry.load()
    rec = {**dict(GOOD), "id": "ken_x", "name": "Electricity access (Kenya)", "coverage": "2011-2021",
           "published": "2024-03-01"}
    sources = {}
    pending = catalog.add_provenances([rec], reg, sources)
    block = catalog.provenance_blocks(pending, reg, {pending[0][0]: "c/p/1"})[0]
    assert "provenanceCategory: dcid:StatisticsProvenance" in block
    assert 'lastDataRefreshDate: "2024-03-01"' in block
    assert 'earliestObservationDate: "2011"' in block and 'latestObservationDate: "2021"' in block
    assert 'downloadUrl: "https://www.knbs.or.ke/energy.xlsx"' in block
    assert "isPartOf: dcid:DCProjectDataset_cat_KenX" in block
    assert list(sources.values())[0]["provenances"]
