"""Registries (all tabs mirrored, rows -> datasets), source aliases, JSON-stat mapping, no-conflict rule (V12)."""

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
os.environ["KNOWLEDGE_DIR"] = tempfile.mkdtemp()   # never write to the real knowledge base

import build_dcp     # noqa: E402
import jsonstat      # noqa: E402
import registries    # noqa: E402
import registry      # noqa: E402
from places import PlaceResolver  # noqa: E402
from storage import LocalStore    # noqa: E402

SOURCES = """
ZIMSTAT:
  name: "Zimbabwe National Statistics Agency"
  url: "https://www.zimstat.co.zw/"
  short: ZIMSTAT
  aliases: ["Zimbabwe National Statistics Agency (ZIMSTAT)"]
KNBS:
  name: "Kenya National Bureau of Statistics"
  url: "https://www.knbs.or.ke/"
  short: KNBS
  aliases: ["Kenya Bureau of Statistics"]
Base:
  name: "World Bank"
  url: "https://www.worldbank.org/"
  dcid: "dc/s/WorldBank"
"""


def workbook(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = "Links"
    wb.active.append(["just", "another", "tab"])
    ws = wb.create_sheet("Extracted")
    ws.append(["Dataset ID", "Dataset Name", "Description", "Source Organization", "Source URL",
               "Source file link", "License URL", "License Type", "Sub-domain"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def cube():
    """Monthly + quarterly arrivals, provinces + a foreign country + national."""
    return {"class": "dataset", "label": "Arrivals", "id": ["REF_AREA", "UNIT_MEASURE", "TIME_PERIOD"],
            "size": [3, 1, 2], "role": {"geo": ["REF_AREA"], "time": ["TIME_PERIOD"]},
            "dimension": {
                "REF_AREA": {"label": "Reference Area", "category": {"index": {"ZW": 0, "ZW_0": 1, "454": 2},
                             "label": {"ZW": "Zimbabwe", "ZW_0": "Bulawayo", "454": "Malawi"}}},
                "UNIT_MEASURE": {"label": "Unit", "category": {"index": ["PS"], "label": {"PS": "Persons"}}},
                "TIME_PERIOD": {"label": "Time", "category": {"index": ["2023-01", "2023-Q1"]}}},
            "value": [10, 30, 1, 3, 99, 99]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cfg = self.tmp / "config"
        (cfg / "feeds").mkdir(parents=True)
        (cfg / "registries").mkdir()
        (cfg / "instance.yaml").write_text('name: T\nnamespace: TDC\nsite_url: "http://x"\n')
        (cfg / "sources.yaml").write_text(SOURCES)
        (cfg / "places.yaml").write_text("aliases: {}\n")
        (cfg / "registries" / "zim.yaml").write_text(
            "enabled: true\nsheet: {path: data/wb.xlsx}\ndatasets_tab: Extracted\nunlicensed: load_without_croissant\n"
            "allow_future: true\ndata: {type: jsonstat, method: POST, body: []}\n"
            "mapping: {place_codes: '^(ZW|ZW_\\d)$', place_map: {ZW: country/ZWE}, place_within: country/ZWE,"
            " place_types: {'^ZW_\\d$': AdministrativeArea1}}\n")
        (self.tmp / "data").mkdir()
        self.reg = registry.load(cfg)
        self.store = LocalStore(self.tmp / "store")
        self.patch = mock.patch.multiple("settings", PROJECT_ROOT=self.tmp, DATA_DIR=self.tmp / "data")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestRegistry(Base):
    ROW = ["ZW1,ARR,1.0", "Arrivals", "Monthly arrivals", "Zimbabwe National Statistics Agency (ZIMSTAT)",
           "https://zimstat.example/#/ARR", "https://api.example/datasets/ZW1,ARR,1.0/data (POST body [])",
           "NOT FOUND", "NOT FOUND", "Migration"]

    def test_alias_matching_ignores_spacing_and_case(self):
        for name in ("KNBS", "Kenya Bureau of Statistics", "KenyaBureauOfStatistics", "kenya national bureau of "
                     "statistics", "Kenya National Bureau of Statistics"):
            self.assertEqual(self.reg.source_key_for(name), "KNBS", name)
        self.assertIsNone(self.reg.source_key_for("Some Other Office"))

    def test_sync_mirrors_all_tabs_and_logs_changes(self):
        (self.tmp / "data" / "wb.xlsx").write_bytes(workbook([self.ROW]))
        cfg = registries.load_configs(self.tmp / "config", {})["zim"]
        records, rep = registries.sync(cfg, self.store, "s1")
        self.assertEqual(rep["tabs"], {"Links": 0, "Extracted": 1})
        self.assertTrue((self.tmp / "store/registries/zim/tabs/Links.csv").exists())
        self.assertEqual(rep["changes"]["added"], 1)
        (self.tmp / "data" / "wb.xlsx").write_bytes(workbook([self.ROW, ["ZW1,NEW,1.0"] + self.ROW[1:]]))
        records, rep = registries.sync(cfg, self.store, "s2")
        self.assertEqual(rep["changes"], {"added": 1, "removed": 0, "changed": 0})
        feeds, problems = registries.feeds(cfg, records, self.reg)
        self.assertEqual(sorted(feeds), ["zim_arr", "zim_new"])
        f = feeds["zim_arr"]
        self.assertEqual(f.source_key, "ZIMSTAT")                     # matched through the alias
        self.assertEqual(f.fetch["url"], "https://api.example/datasets/ZW1,ARR,1.0/data")
        self.assertTrue(f.license_pending)
        self.assertEqual(f.dataset["license"], "")                     # never guessed
        self.assertGreaterEqual(len(f.dataset["description"]), 50)

    def test_unreachable_sheet_uses_last_copy(self):
        (self.tmp / "data" / "wb.xlsx").write_bytes(workbook([self.ROW]))
        cfg = registries.load_configs(self.tmp / "config", {})["zim"]
        registries.sync(cfg, self.store, "s1")
        (self.tmp / "data" / "wb.xlsx").unlink()
        records, rep = registries.sync(cfg, self.store, "s2")
        self.assertEqual(rep["status"], "USING LAST COPY")
        self.assertEqual(list(records), ["ZW1,ARR,1.0"])


class TestJsonStat(Base):
    def test_cube_mapping(self):
        (self.tmp / "data" / "wb.xlsx").write_bytes(workbook([TestRegistry.ROW]))
        cfg = registries.load_configs(self.tmp / "config", {})["zim"]
        feeds, _ = registries.feeds(cfg, registries.sync(cfg, self.store, "s")[0], self.reg)
        resolver = PlaceResolver(cache={"children|AdministrativeArea1|country/ZWE": {"bulawayo": "wikidataId/Q24045859"}})
        res = jsonstat.normalize(json.dumps(cube()).encode(), feeds["zim_arr"], self.reg, resolver)
        self.assertEqual(res.errors, [])
        got = {(o["statvar"].rsplit("_", 1)[-1], o["place"], o["date"], o["period"], o["value"]) for o in res.observations}
        self.assertEqual(got, {("P1M", "country/ZWE", "2023-01", "P1M", 10.0), ("P3M", "country/ZWE", "2023-01", "P3M", 30.0),
                               ("P1M", "wikidataId/Q24045859", "2023-01", "P1M", 1.0),
                               ("P3M", "wikidataId/Q24045859", "2023-01", "P3M", 3.0)})
        self.assertTrue(any("454 Malawi" in n for n in res.notes))      # not an area of the publisher
        units = {v.unit for v in res.variables.values()}
        self.assertEqual(units, {"TDCUnit_PS"})
        self.assertTrue(all("quarterly" in v.name or "monthly" in v.name for v in res.variables.values()))

    def test_period_parsing(self):
        self.assertEqual(jsonstat.parse_period("2024-Q3"), ("2024-07", "P3M"))
        self.assertEqual(jsonstat.parse_period("2024"), ("2024", "P1Y"))
        self.assertIsNone(jsonstat.parse_period("2024-W05"))


class TestNoConflict(Base):
    def test_existing_nodes_are_referenced_not_written(self):
        (self.tmp / "config/feeds/f.yaml").write_text("""enabled: true
source: Base
dataset: {dcid: dc/d/WorldBank_WDI, name: WDI, url: "https://e.org", license: "https://creativecommons.org/licenses/by/4.0/",
  license_evidence: x, description: "a description that is long enough to pass the croissant fifty char rule"}
fetch: {type: csv, url: "https://e.org/x.csv"}
mapping: {place: {column: p}, date: {column: d}, values: {v: pop}}
variables:
  pop: {dcid: Count_Person, name: Population}
""")
        reg = registry.load(self.tmp / "config")
        files = build_dcp.Writer(reg).schema_files()
        self.assertEqual(build_dcp.foreign_nodes(files, reg.ns), [])
        text = "".join(files.values())
        self.assertNotIn("Node: dcid:dc/d/WorldBank_WDI", text)
        self.assertNotIn("Node: dcid:Count_Person", text)


if __name__ == "__main__":
    unittest.main()


class TestMergeIncludesRegistryDatasets(Base):
    def test_published_registry_dataset_reaches_loader_input(self):
        import build_local_dc
        (self.tmp / "data" / "wb.xlsx").write_bytes(workbook([TestRegistry.ROW]))
        cfg = registries.load_configs(self.tmp / "config", {})["zim"]
        registries.sync(cfg, self.store, "s")
        self.store.write_text("ingestion/input/tdc_schema/schema.mcf", "Node: ddd:TDCGroup_Root\ntypeOf: dcid:StatVarGroup\n")
        self.store.write_text("ingestion/input/zim_arr/zim_arr.csv",
                              "variable,entity,date,value,unit,period\ndcid:TDC_x,dcid:country/ZWE,2023-01,10,,P1M\n")
        with mock.patch("settings.CONFIG_DIR", self.tmp / "config"):
            changed, rows, live = build_local_dc.build(self.tmp / "store", self.tmp / "local_dc")
        self.assertEqual(live, ["zim_arr"])
        self.assertIn("country/ZWE,TDC_x,2023-01,10,,P1M", (self.tmp / "local_dc" / "zim_arr.csv").read_text())
