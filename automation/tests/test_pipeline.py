"""Offline tests: every fetch type and layout, validation rules, publishing safety, local-loader merge.

  docker compose run --rm pipeline python -m unittest discover -s automation/tests -v
  (or, with PyYAML + openpyxl installed: python3 -m unittest discover -s automation/tests -v)
"""

import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
os.environ["DC_API_KEY"] = ""          # never call the Data Commons API from tests
os.environ["KNOWLEDGE_DIR"] = tempfile.mkdtemp()   # never write to the real knowledge base

import build_dcp            # noqa: E402
import build_local_dc       # noqa: E402
import croissant            # noqa: E402
import fetch                # noqa: E402
import normalize            # noqa: E402
import registry             # noqa: E402
import run_pipeline         # noqa: E402
from places import PlaceResolver  # noqa: E402

INSTANCE = """
name: "Test Commons"
namespace: "TestDC"
site_url: "http://localhost:9999"
earliest_year: 1900
validation: {max_row_drop_share: 0.2, large_revision_share: 0.5}
"""
SOURCES = """
Org:
  name: "Test Statistics Office"
  url: "https://stats.example.org/"
"""
DATASET = """
source: Org
dataset:
  name: "Test dataset"
  description: "A test dataset with enough words in its description to pass the fifty character Croissant rule."
  url: "https://stats.example.org/dataset"
  license: "https://creativecommons.org/licenses/by/4.0/"
  license_evidence: "stated on the dataset page"
provenance:
  name: "{name}"
"""


class ConfigCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "config" / "feeds").mkdir(parents=True)
        (self.tmp / "config" / "instance.yaml").write_text(INSTANCE)
        (self.tmp / "config" / "sources.yaml").write_text(SOURCES)
        (self.tmp / "config" / "places.yaml").write_text("aliases:\n  Kenya Rep: country/KEN\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def feed(self, fid, body, prov=None):
        text = "enabled: true\n" + DATASET.format(name=prov or fid) + textwrap.dedent(body).strip("\n") + "\n"
        if "variables:" not in body:
            text += VAR
        (self.tmp / "config" / "feeds" / f"{fid}.yaml").write_text(text)

    def load(self):
        return registry.load(self.tmp / "config")

    def run_feed(self, fid, table):
        reg = self.load()
        self.assertNotIn(fid, reg.problems, reg.problems.get(fid))
        return normalize.normalize(table, reg.feeds[fid], reg, PlaceResolver(aliases=reg.aliases))


VAR = """
variables:
  pop:
    name: "Population"
    description: "Number of residents."
    group: "Demographics"
    population_type: Person
    measured_property: count
    min: 0
    max: 1000000000
"""


class TestLayouts(ConfigCase):
    def test_long_layout_google_sheet_csv(self):
        self.feed("sheet", """
            fetch: {type: google_sheet, sheet_id: "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"}
            mapping:
              place: {column: Country, match: name}
              date: {column: Year}
              variable_column: Indicator
              value_column: Value
              variables_map: {"Total population": pop}
            """)
        csv_bytes = ("Country,Year,Indicator,Value\nKenya,2023,Total population,\"55,100,000\"\n"
                     "Kenya Rep,2022,Total population,54000000\nNigeria,2023,Total population,..\n"
                     "Nigeria,2023,Something else,5\n").encode()
        table = fetch.parse_bytes({"type": "google_sheet"}, csv_bytes)
        res = self.run_feed("sheet", table)
        self.assertEqual(res.errors, [])
        self.assertEqual([(o["place"], o["date"], o["value"]) for o in res.observations],
                         [("country/KEN", "2022", 54000000.0), ("country/KEN", "2023", 55100000.0)])
        self.assertTrue(any("variables_map" in w for w in res.warnings))   # "Something else" reported

    def test_google_sheet_not_public_is_rejected(self):
        page = b"<!DOCTYPE html><html><body>Sign in</body></html>"
        with mock.patch.object(fetch, "http_get", return_value=(page, "text/html", "u")):
            with self.assertRaisesRegex(fetch.FetchError, "not shared publicly"):
                fetch.fetch({"type": "google_sheet", "sheet_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"})

    def test_excel_years_as_columns_with_title_rows_and_scale(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Table 1"
        ws.append(["Table 1: Population (millions)"])
        ws.append([])
        ws.append(["Country", 2020, 2021])
        ws.append(["Kenya", 53.0, 54.1])
        ws.append(["Ethiopia", 117.2, None])
        ws.append([])
        ws.append(["Source: test office"])           # note below the table must be ignored
        buf = io.BytesIO()
        wb.save(buf)
        (self.tmp / "data").mkdir()
        self.feed("xl", """
            fetch: {type: excel, path: "data/t.xlsx", sheet: "Table 1", header_row: 3}
            mapping:
              place: {column: Country, match: name}
              year_columns: true
              variable: pop
              scale: 1000000
            """)
        table = fetch.parse_bytes({"type": "excel", "sheet": "Table 1", "header_row": 3}, buf.getvalue())
        self.assertEqual(table.header, ["Country", "2020", "2021"])
        res = self.run_feed("xl", table)
        self.assertEqual(res.errors, [])
        self.assertEqual(len(res.observations), 3)
        self.assertIn({"statvar": "TestDC_pop", "place": "country/KEN", "date": "2021", "value": 54100000.0,
                       "feed": "xl"}, res.observations)

    def test_json_api_wide_layout(self):
        doc = [{"page": 1}, [{"countryiso3code": "KEN", "date": "2024", "value": 56000000},
                             {"countryiso3code": "AFE", "date": "2024", "value": 7},
                             {"countryiso3code": "NGA", "date": "2024", "value": None}]]
        self.feed("api", """
            fetch: {type: json, url: "https://api.example.org/x", records: "1"}
            mapping:
              place: {column: countryiso3code, match: iso3}
              date: {column: date}
              values: {value: pop}
              unresolved_places: skip
            """)
        table = fetch.parse_bytes({"type": "json", "records": "1"}, json.dumps(doc).encode())
        res = self.run_feed("api", table)
        self.assertEqual(res.errors, [])
        self.assertEqual(len(res.observations), 1)
        self.assertTrue(any("AFE" in n for n in res.notes))

    def test_html_table(self):
        html = b"""<html><table><tr><td>menu</td></tr></table>
            <table><tr><th>Country</th><th>Year</th><th>Pop</th></tr>
            <tr><td>Kenya</td><td>2020</td><td>53 000 000</td></tr></table></html>"""
        self.feed("web", """
            fetch: {type: html_table, url: "https://example.org/p", match: "Country"}
            mapping:
              place: {column: Country, match: name}
              date: {column: Year}
              values: {Pop: pop}
            """)
        table = fetch.parse_bytes({"type": "html_table", "match": "Country"}, html)
        res = self.run_feed("web", table)
        self.assertEqual([o["value"] for o in res.observations], [53000000.0])


class TestValidation(ConfigCase):
    WIDE = """
        fetch: {type: csv, url: "https://example.org/x.csv"}
        mapping:
          place: {column: iso3, match: iso3}
          date: {column: year}
          values: {pop: pop}
        """

    def table(self, text):
        return fetch.parse_bytes({"type": "csv"}, text.encode())

    def test_errors_are_reported_not_raised(self):
        self.feed("f", self.WIDE)
        res = self.run_feed("f", self.table(
            "iso3,year,pop\nKEN,2020,abc\nKEN,1850,5\nKEN,2021,-3\nKEN,2022,1\nKEN,2022,2\nXXX,2022,1\n"))
        joined = " | ".join(res.errors)
        for rule in ("E4", "E5", "E6", "E7", "E8"):
            self.assertIn(rule, joined)

    def test_missing_column(self):
        self.feed("f", self.WIDE)
        res = self.run_feed("f", self.table("iso3,yr,pop\nKEN,2020,1\n"))
        self.assertTrue(res.errors[0].startswith("E3"))

    def test_reviewed_exclusion(self):
        self.feed("f", self.WIDE.replace("values: {pop: pop}", """values: {pop: pop}
          exclude:
            - {place: KEN, date: "2021", reason: "checked with the office: typo"}"""))
        res = self.run_feed("f", self.table("iso3,year,pop\nKEN,2020,1\nKEN,2021,-5\n"))
        self.assertEqual(res.errors, [])
        self.assertEqual(len(res.observations), 1)

    def test_config_problems(self):
        self.feed("bad", """
            fetch: {type: ftp}
            mapping: {place: {column: c}, date: {column: y}, values: {v: undefined_var}}
            """)
        self.feed("Bad-Name", self.WIDE)
        reg = self.load()
        self.assertIn("bad", reg.problems)
        self.assertIn("Bad-Name", reg.problems)
        self.assertNotIn("bad", reg.feeds)

    def test_croissant_rules(self):
        self.feed("f", self.WIDE)
        reg = self.load()
        feed = reg.feeds["f"]
        self.assertEqual(croissant.check_feed(feed, reg)[0], [])
        feed.dataset["description"] = "too short"
        feed.dataset["license"] = "CC BY"
        errors = croissant.check_feed(feed, reg)[0]
        self.assertTrue(any(e.startswith("C2") for e in errors))
        self.assertTrue(any(e.startswith("C4") for e in errors))

    def test_row_drop_against_production(self):
        self.feed("f", self.WIDE)
        feed = self.load().feeds["f"]
        published = "variable,entity,date,value,unit\n" + "".join(
            f"dcid:TestDC_pop,dcid:country/KEN,{y},1,\n" for y in range(2000, 2010))
        new = [{"statvar": "TestDC_pop", "place": "country/KEN", "date": str(y), "value": 1.0} for y in (2000, 2001)]
        errors, _, _ = run_pipeline.compare_with_production(feed, new, published, 0.5)
        self.assertTrue(errors and errors[0].startswith("E9"))


class TestPublishAndMerge(ConfigCase):
    def test_end_to_end_publish_reject_keeps_previous_and_merge(self):
        (self.tmp / "data").mkdir()
        self.feed("a", """
            fetch: {type: csv, path: "data/a.csv"}
            mapping: {place: {column: iso3, match: iso3}, date: {column: year}, values: {pop: pop}}
            """, prov="Prov A")
        store, work = self.tmp / "store", self.tmp / "work"
        args = ["--config-dir", str(self.tmp / "config"), "--store-dir", str(store), "--work-dir", str(work)]
        with mock.patch("settings.PROJECT_ROOT", self.tmp), mock.patch("settings.DATA_DIR", self.tmp / "data"):
            (self.tmp / "data" / "a.csv").write_text("iso3,year,pop\n" + "".join(
                f"KEN,{y},{y}\n" for y in range(2000, 2010)))
            self.assertEqual(run_pipeline.main(args), 0)
            csv_path = store / "ingestion/input/a/a.csv"
            first = csv_path.read_text()
            self.assertIn("dcid:TestDC_pop,dcid:country/KEN,2009,2009,", first)
            # same data again: nothing republished
            self.assertEqual(run_pipeline.main(args), 0)
            report = json.loads((work / "validation_summary.json").read_text())
            self.assertEqual(report["published_now"], [])
            # the sheet is emptied by mistake: rejected (E9), the published version stays
            (self.tmp / "data" / "a.csv").write_text("iso3,year,pop\nKEN,2000,2000\n")
            self.assertEqual(run_pipeline.main(args), 1)
            self.assertEqual(csv_path.read_text(), first)
        reg = self.load()
        out = self.tmp / "local_dc"
        changed, rows, live = build_local_dc.build(store, out, reg)
        self.assertEqual((rows, live), (10, ["a"]))
        prov = (out / "provenance.mcf").read_text()
        self.assertTrue(prov.startswith("Node: dcid:c/p/1\n"))
        self.assertIn("isPartOf: dcid:TestDCDataset_a", prov)
        self.assertIn("provenanceCategory: dcid:StatisticsProvenance", prov)
        self.assertNotIn("\nname:", prov)
        schema = (out / "schema.mcf").read_text()
        self.assertIn("Node: dcid:TestDCDataset_a", schema)
        self.assertNotIn("ddd:", schema)
        config = json.loads((out / "config.json").read_text())
        self.assertEqual(config["inputFiles"]["a.csv"]["provenance"], "Prov A")
        self.assertEqual(build_local_dc.build(store, out, reg)[0], [])   # UNCHANGED the second time

    def test_provenance_ids_follow_loader_order(self):
        (self.tmp / "config" / "sources.yaml").write_text(SOURCES + """
Other:
  name: "Another Org"
  url: "https://other.example.org/"
""")
        for fid, src in (("z_first_source", "Org"), ("b_other", "Other"), ("c_first_again", "Org")):
            self.feed(fid, f"""
                fetch: {{type: csv, url: "https://e.org/x.csv"}}
                mapping: {{place: {{column: p}}, date: {{column: d}}, values: {{v: pop}}}}
                """, prov=f"P {fid}")
            path = self.tmp / "config" / "feeds" / f"{fid}.yaml"
            path.write_text(path.read_text().replace("source: Org", f"source: {src}"))
        reg = self.load()
        sources, ids = build_local_dc.provenance_ids(reg, list(reg.feeds))
        # sources in order of first appearance (feeds sorted by id), then their provenances
        self.assertEqual(list(sources), ["Another Org", "Test Statistics Office"])
        self.assertEqual(ids, {"P b_other": "c/p/1", "P c_first_again": "c/p/2", "P z_first_source": "c/p/3"})


if __name__ == "__main__":
    unittest.main()
