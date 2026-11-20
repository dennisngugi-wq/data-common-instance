import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["DC_API_KEY"] = ""
os.environ["KNOWLEDGE_DIR"] = tempfile.mkdtemp()

import croissant
import normalize
import registries
import registry
import sheets
from places import PlaceResolver
from storage import LocalStore

SOURCES = """
KNBS:
  name: "Kenya National Bureau of Statistics"
  url: "https://www.knbs.or.ke/"
  short: KNBS
  aliases: ["Kenya Bureau of Statistics"]
"""
DESC = "Maize and bean production by country, from the national crop surveys, published every year."


def workbook(about, data, variables=None):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = "How to fill"
    wb.active.append(["instructions"])
    ws = wb.create_sheet("About")
    ws.append(["Field", "Value"])
    for k, v in about.items():
        ws.append([k, v])
    ws = wb.create_sheet("Data")
    for r in data:
        ws.append(r)
    if variables:
        ws = wb.create_sheet("Variables")
        for r in variables:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ABOUT = {"Dataset name": "Crop production", "Description": DESC, "Source organisation": "Kenya Bureau of Statistics",
         "Licence URL": "https://creativecommons.org/licenses/by/4.0/", "Licence evidence": "https://knbs.example/terms",
         "Dataset page": "https://knbs.example/crops"}
LONG = [["Place", "Year", "Variable", "Value", "Unit"],
        ["Kenya", 2022, "Maize production", 3400000, "Tonnes"],
        ["Uganda", 2022, "Maize production", "2,900,000", "Tonnes"],
        ["Kenya", 2022, "Beans production", 800000, "Tonnes"],
        ["Kenya", 2023, "Beans production", "", "Tonnes"]]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "config"
        (self.cfg / "feeds").mkdir(parents=True)
        (self.cfg / "registries").mkdir()
        (self.cfg / "instance.yaml").write_text('name: T\nnamespace: TDC\nsite_url: "http://x"\n')
        (self.cfg / "sources.yaml").write_text(SOURCES)
        (self.cfg / "places.yaml").write_text("aliases: {}\n")
        (self.tmp / "data" / "inbox").mkdir(parents=True)
        self.store = LocalStore(self.tmp / "store")
        self.patch = mock.patch.multiple("settings", PROJECT_ROOT=self.tmp, DATA_DIR=self.tmp / "data",
                                         CONFIG_DIR=self.cfg)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sheets_json(self, *entries):
        (self.cfg / "sheets.json").write_text(json.dumps({"sheets": list(entries)}))

    def load(self, xlsx, entry=None):
        (self.tmp / "data" / "inbox" / "crops.xlsx").write_bytes(xlsx)
        self.sheets_json(entry or {"id": "crops", "link": "data/inbox/crops.xlsx", "enabled": True})
        reg = registry.load(self.cfg)
        rep = {}
        sheets.add_feeds(reg, self.store, self.cfg, "s1", rep)
        return reg, rep

    def run_feed(self, reg, fid="crops"):
        feed = reg.feeds[fid]
        resolver = PlaceResolver(allow_api=False)
        self.assertIsNone(sheets.resolve_within(feed, resolver))
        return normalize.normalize(sheets.table(feed, self.store), feed, reg, resolver)


class TestLinks(Base):
    def test_parse_link(self):
        sid = "1ZJqKs5QYiFDFussdAwz7dSXO4N0dKsdC"
        self.assertEqual(sheets.parse_link(f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid=12"),
                         {"sheet_id": sid, "gid": "12"})
        self.assertEqual(sheets.parse_link(f"https://docs.google.com/spreadsheets/d/{sid}/edit?usp=sharing"),
                         {"sheet_id": sid})
        self.assertEqual(sheets.parse_link(f"https://docs.google.com/spreadsheets/d/e/2PACX-{sid}/pubhtml"),
                         {"published_id": f"2PACX-{sid}"})
        self.assertEqual(sheets.parse_link("https://x.org/t.xlsx"), {"url": "https://x.org/t.xlsx"})
        self.assertEqual(sheets.parse_link("data/inbox/t.xlsx"), {"path": "data/inbox/t.xlsx"})

    def test_bad_entries_are_reported_not_loaded(self):
        self.sheets_json({"id": "Bad Id", "link": "x", "enabled": True}, {"id": "ok", "link": "", "enabled": True},
                         {"id": "off", "link": "data/inbox/later.xlsx", "enabled": False})
        problems = {}
        out = sheets.load_list(self.cfg, problems)
        self.assertEqual(list(out), ["off"])
        self.assertEqual(len(problems["config"]), 2)

    def test_links_dropped_in_the_inbox_are_loaded(self):
        sid = "1ZJqKs5QYiFDFussdAwz7dSXO4N0dKsdC"
        (self.tmp / "data" / "inbox" / "links.txt").write_text(
            f"# my sheets\n\nkenya_pop https://docs.google.com/spreadsheets/d/{sid}/edit\n"
            "crops data/inbox/crops.xlsx\nzim https://e.org/list.xlsx registry\n")
        (self.tmp / "data" / "inbox" / "more.json").write_text(json.dumps(
            [{"id": "later", "link": "data/inbox/later.xlsx", "enabled": False}]))
        problems = {}
        out = sheets.load_list(self.cfg, problems)
        self.assertEqual(problems, {})
        self.assertEqual(sorted(out), ["crops", "kenya_pop", "later", "zim"])
        self.assertTrue(out["kenya_pop"]["enabled"])
        self.assertEqual(out["kenya_pop"]["spec"], {"sheet_id": sid})
        self.assertEqual(out["zim"]["kind"], "registry")
        self.assertFalse(out["later"]["enabled"])

    def test_inbox_link_cannot_reuse_an_id(self):
        self.sheets_json({"id": "crops", "link": "data/inbox/crops.xlsx", "enabled": True})
        (self.tmp / "data" / "inbox" / "links.txt").write_text("crops data/inbox/other.xlsx\n")
        problems = {}
        out = sheets.load_list(self.cfg, problems)
        self.assertEqual(out["crops"]["link"], "data/inbox/crops.xlsx")
        self.assertTrue(any("used twice" in p and "links.txt line 1" in p for p in problems["config"]))

    def test_invalid_json_is_a_config_problem(self):
        (self.cfg / "sheets.json").write_text("{ nope")
        reg = registry.load(self.cfg)
        self.assertTrue(any("sheets.json" in p for p in reg.problems["config"]))


class TestDatasetSheet(Base):
    def test_long_layout_loads_with_units_and_source_alias(self):
        reg, rep = self.load(workbook(ABOUT, LONG))
        self.assertEqual(rep["crops"]["status"], "OK")
        feed = reg.feeds["crops"]
        self.assertEqual(feed.source_key, "KNBS")
        self.assertTrue(croissant.publishable(feed, reg))
        self.assertEqual(len(feed.variables), 2)
        v = reg.variables["crops_MaizeProduction"]
        self.assertEqual((v.name, v.dcid, v.unit), ("Maize production", "TDC_crops_MaizeProduction", "TDCUnit_Tonnes"))
        self.assertEqual(reg.units["TDCUnit_Tonnes"]["name"], "Tonnes")
        res = self.run_feed(reg)
        self.assertEqual(res.errors, [])
        self.assertEqual(len(res.observations), 3)
        self.assertIn(("TDC_crops_MaizeProduction", "country/UGA", "2022", 2900000.0),
                      [(o["statvar"], o["place"], o["date"], o["value"]) for o in res.observations])
        self.assertTrue((self.tmp / "store/sheets/crops/tabs/How_to_fill.csv").exists())

    def test_wide_layout(self):
        reg, _ = self.load(workbook(ABOUT, [["Country", "Year", "Maize", "Beans"], ["Kenya", 2022, 3.4, 0.8]]))
        res = self.run_feed(reg)
        self.assertEqual(res.errors, [])
        self.assertEqual(sorted(o["statvar"] for o in res.observations), ["TDC_crops_Beans", "TDC_crops_Maize"])

    def test_missing_licence_is_not_loaded_never_guessed(self):
        about = {k: v for k, v in ABOUT.items() if not k.startswith("Licence")}
        reg, _ = self.load(workbook(about, LONG))
        self.assertEqual(reg.feeds["crops"].dataset["license"], "")
        self.assertFalse(croissant.publishable(reg.feeds["crops"], reg))

    def test_new_organisation_needs_its_website(self):
        about = dict(ABOUT, **{"Source organisation": "Uganda Bureau of Statistics"})
        reg, _ = self.load(workbook(about, LONG))
        self.assertNotIn("crops", reg.feeds)
        self.assertTrue(any("Source website" in m for m in reg.problems["crops"]))
        about["Source website"] = "https://www.ubos.org/"
        reg, _ = self.load(workbook(about, LONG))
        src = reg.sources[reg.feeds["crops"].source_key]
        self.assertEqual((src["name"], src["url"]), ("Uganda Bureau of Statistics", "https://www.ubos.org/"))

    def test_variables_tab_min_max_rejects_typos(self):
        variables = [["Variable", "Description", "Unit", "Min", "Max"],
                     ["Maize production", "Maize harvested in the year.", "Tonnes", "0", "3,000,000"]]
        reg, _ = self.load(workbook(ABOUT, LONG, variables))
        self.assertEqual(reg.variables["crops_MaizeProduction"].description, "Maize harvested in the year.")
        res = self.run_feed(reg)
        self.assertTrue(any(e.startswith("E6") for e in res.errors))

    def test_unknown_place_is_an_error(self):
        reg, _ = self.load(workbook(ABOUT, LONG + [["Atlantis", 2022, "Maize production", 1, "Tonnes"]]))
        res = self.run_feed(reg)
        self.assertTrue(any(e.startswith("E4") and "Atlantis" in e for e in res.errors))

    def test_sheet_without_about_tab_is_reported(self):
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active.append(["Place", "Year", "Value"])
        buf = io.BytesIO()
        wb.save(buf)
        reg, rep = self.load(buf.getvalue())
        self.assertEqual(rep["crops"]["status"], "FAILED")
        self.assertIn("About", rep["crops"]["error"])
        self.assertNotIn("crops", reg.feeds)

    def test_check_can_try_a_disabled_sheet(self):
        reg, rep = self.load(workbook(ABOUT, LONG), {"id": "crops", "link": "data/inbox/crops.xlsx", "enabled": False})
        self.assertNotIn("crops", reg.feeds)
        reg = registry.load(self.cfg)
        sheets.add_feeds(reg, self.store, self.cfg, "s1", {}, include=["crops"])
        self.assertIn("crops", reg.feeds)

    def test_disabled_sheet_is_not_loaded_and_last_copy_is_reused(self):
        reg, _ = self.load(workbook(ABOUT, LONG))
        (self.tmp / "data/inbox/crops.xlsx").unlink()
        reg2 = registry.load(self.cfg)
        rep = {}
        sheets.add_feeds(reg2, self.store, self.cfg, "s2", rep)
        self.assertEqual(rep["crops"]["status"], "USING LAST COPY")
        self.assertIn("crops", reg2.feeds)
        self.sheets_json({"id": "crops", "link": "data/inbox/crops.xlsx", "enabled": False})
        reg3 = registries.add_stored_feeds(registry.load(self.cfg), self.store, self.cfg)
        self.assertNotIn("crops", reg3.feeds)


class TestSwitches(Base):
    FEED = """enabled: false
source: KNBS
dataset: {name: N, description: D}
fetch: {type: google_sheet, sheet_id: OLDOLDOLDOLDOLDOLDOLDOLD}
mapping: {place: {column: Country}, date: {column: Year}, values: {V: v}}
variables: {v: {name: V, description: V}}
"""

    def test_custom_feed_takes_link_and_switch_from_sheets_json(self):
        (self.cfg / "feeds" / "my_table.yaml").write_text(self.FEED)
        sid = "1ZJqKs5QYiFDFussdAwz7dSXO4N0dKsdC"
        self.sheets_json({"id": "my_table", "kind": "custom", "enabled": True,
                          "link": f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid=7"})
        reg = registry.load(self.cfg)
        self.assertEqual(reg.feeds["my_table"].fetch, {"type": "google_sheet", "sheet_id": sid, "gid": "7"})
        self.sheets_json({"id": "my_table", "kind": "custom", "enabled": False, "link": "data/inbox/x.xlsx"})
        self.assertNotIn("my_table", registry.load(self.cfg).feeds)

    def test_custom_entry_without_mapping_file_is_reported(self):
        self.sheets_json({"id": "nomap", "kind": "custom", "enabled": True, "link": "https://x.org/a.xlsx"})
        self.assertIn("nomap", registry.load(self.cfg).problems)

    def test_registry_takes_link_and_switch_from_sheets_json(self):
        (self.cfg / "registries" / "zim.yaml").write_text("enabled: false\nsheet: {path: data/old.xlsx}\n"
                                                          "datasets_tab: Extracted\n")
        self.sheets_json({"id": "zim", "kind": "registry", "enabled": True, "link": "data/inbox/new.xlsx"})
        cfgs = registries.load_configs(self.cfg, {})
        self.assertEqual(cfgs["zim"]["sheet"], {"path": "data/inbox/new.xlsx"})
        self.sheets_json({"id": "zim", "kind": "registry", "enabled": False, "link": "data/inbox/new.xlsx"})
        self.assertEqual(registries.load_configs(self.cfg, {}), {})


if __name__ == "__main__":
    unittest.main()
