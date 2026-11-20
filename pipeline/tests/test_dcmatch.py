import os
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["DC_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import dcmatch
import sheets
from storage import LocalStore

CANDIDATES = {
    "Total population": [
        {"dcid": "dc/topic/Demographics", "typeOf": ["Topic"], "name": "Total population"},
        {"dcid": "Count_Person", "typeOf": ["StatisticalVariable"], "name": "Total Population"},
        {"dcid": "Count_Person_Female", "typeOf": ["StatisticalVariable"], "name": "Female population"}],
    "Maize yield in my village": [
        {"dcid": "Yield_Maize", "typeOf": ["StatisticalVariable"], "name": "Maize yield"}],
}


class FakeApi:
    def __init__(self):
        self.calls = []

    def __call__(self, path, body):
        self.calls.append((path, body))
        if path == "/v2/resolve":
            return {"entities": [{"node": n, "candidates": CANDIDATES.get(n, [])} for n in body["nodes"]]}
        return {"data": {d: {"arcs": {"typeOf": {"nodes": [{"dcid": "StatisticalVariable"}]},
                                      "name": {"nodes": [{"value": "Total population"}]}}}
                         if d == "Count_Person" else {} for d in body["nodes"]}}


class DecideTest(unittest.TestCase):
    def test_only_an_identical_name_is_mapped(self):
        api = FakeApi()
        out, problems = dcmatch.decide(["Total population", "Maize yield in my village"], {}, {}, post=api)
        self.assertEqual(problems, [])
        self.assertEqual(out["Total population"]["dcid"], "Count_Person")
        self.assertNotIn("dcid", out["Maize yield in my village"])
        self.assertTrue(out["Maize yield in my village"]["checked"])

    def test_a_decision_is_kept_and_not_asked_again(self):
        api = FakeApi()
        known, _ = dcmatch.decide(["Total population", "Maize yield in my village"], {}, {}, post=api)
        again = FakeApi()
        out, problems = dcmatch.decide(["Total population", "Maize yield in my village"], {}, known, post=again)
        self.assertEqual((again.calls, problems), ([], []))
        self.assertEqual(out["Total population"]["dcid"], "Count_Person")

    def test_sheet_can_name_the_variable_or_keep_its_own(self):
        out, problems = dcmatch.decide(["People", "Total population", "Cows"],
                                       {"People": "Count_Person", "Total population": "new", "Cows": "Not_There"},
                                       {}, post=FakeApi())
        self.assertEqual(out["People"]["dcid"], "Count_Person")
        self.assertNotIn("dcid", out["Total population"])
        self.assertTrue(any("Not_There" in p for p in problems))

    def test_two_labels_cannot_be_the_same_variable(self):
        _, problems = dcmatch.decide(["People", "Total population"], {"People": "Count_Person"}, {}, post=FakeApi())
        self.assertTrue(any("both the Data Commons variable Count_Person" in p for p in problems))

    def test_api_failure_is_a_problem_not_a_new_variable(self):
        def down(path, body):
            raise TimeoutError("timed out")
        out, problems = dcmatch.decide(["Total population"], {}, {}, post=down)
        self.assertEqual(out, {})
        self.assertTrue(any("retried at the next refresh" in p for p in problems))


class SheetMatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_match_is_stored_next_to_the_sheet(self):
        store = LocalStore(self.tmp)
        parsed = {"data": {"header": ["Place", "Year", "Total population", "Maize yield in my village"], "rows": []},
                  "variables": {}}
        doc = sheets.match_variables({"id": "kenya"}, parsed, store, post=FakeApi())
        self.assertEqual(doc["variables"]["Total population"]["dcid"], "Count_Person")
        saved = json.loads(store.read_text("sheets/kenya/dc_variables.json"))
        self.assertEqual(saved["variables"]["Total population"]["dcid"], "Count_Person")


class LinksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "config").mkdir()
        (self.tmp / "data" / "inbox" / "files").mkdir(parents=True)

    def entries(self):
        problems = {}
        return sheets.load_list(self.tmp / "config", problems), problems

    def test_links_separated_by_commas_or_lines_need_no_id(self):
        a = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit#gid=0"
        b = "https://docs.google.com/spreadsheets/d/1ZzZzEfGhIjKlMnOpQrStUvWxYz098765/edit"
        (self.tmp / "data" / "inbox" / "links.txt").write_text(
            f"# my sheets\n{a}, {b}\nhttps://example.org/files/Kenya%20Crops.xlsx\ncrops data/inbox/crops.xlsx\n")
        out, problems = self.entries()
        self.assertEqual(problems, {})
        self.assertEqual(len(out), 4)
        self.assertEqual(out[sheets.auto_id(a)]["spec"]["sheet_id"], "1AbCdEfGhIjKlMnOpQrStUvWxYz012345")
        self.assertTrue(all(e["enabled"] for e in out.values()))
        self.assertIn("crops", out)
        self.assertEqual(sheets.auto_id(a), sheets.auto_id(a.replace("/edit#gid=0", "/view")))

    def test_same_link_twice_is_loaded_once(self):
        a = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit"
        (self.tmp / "data" / "inbox" / "links.txt").write_text(f"{a}\nagain {a}\n")
        out, problems = self.entries()
        self.assertEqual(len(out), 1)
        self.assertTrue(any("already loaded" in p for p in problems["config"]))

    def test_excel_files_in_the_inbox_are_found_by_themselves(self):
        (self.tmp / "data" / "inbox" / "files" / "Kenya Crops 2024.xlsx").write_bytes(b"x")
        (self.tmp / "data" / "inbox" / "~$Kenya Crops 2024.xlsx").write_bytes(b"x")
        (self.tmp / "data" / "inbox" / "old.xls").write_bytes(b"x")
        (self.tmp / "data" / "inbox" / "listed.xlsx").write_bytes(b"x")
        (self.tmp / "config" / "sheets.json").write_text(json.dumps(
            {"sheets": [{"id": "listed", "link": "data/inbox/listed.xlsx", "enabled": False}]}))
        out, problems = self.entries()
        self.assertEqual(out["kenya_crops_2024"]["link"], "data/inbox/files/Kenya Crops 2024.xlsx")
        self.assertTrue(out["kenya_crops_2024"]["enabled"])
        self.assertFalse(out["listed"]["enabled"])
        self.assertEqual(len(out), 2)
        self.assertTrue(any("old.xls" in p and ".xlsx" in p for p in problems["config"]))


if __name__ == "__main__":
    unittest.main()
