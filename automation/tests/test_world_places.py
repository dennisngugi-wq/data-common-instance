import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("DC_API_KEY", "")

import build_place_index as b


def node(dcid, name, *types):
    return {"dcid": dcid, "name": name, "types": list(types)}


CHILDREN = {
    "AdministrativeArea1": {"country/KEN": [node("q/nbo-county", "Nairobi County", "AdministrativeArea1")],
                            "country/ZWE": [node("q/byo", "Bulawayo", "AdministrativeArea1", "City")]},
    "AdministrativeArea2": {},
    "AdministrativeArea3": {},
    "City": {"country/KEN": [node("q/nbo", "Nairobi", "City"), node("q/x1", "Kibera", "City"),
                             node("q/x2", "Kibera", "City")],
             "country/ZWE": [node("q/byo", "Bulawayo", "City")]},
}
PARENTS = {"q/x1": [node("q/nbo-county", "Nairobi County", "AdministrativeArea1")],
           "q/x2": [node("q/kis", "Kisumu County", "AdministrativeArea1")]}


def fake_call(body, attempts=3):
    if body["property"] == "->containedInPlace":
        return {"data": {d: {"arcs": {"containedInPlace": {"nodes": PARENTS.get(d, [])}}} for d in body["nodes"]}}
    typ = body["property"].split("typeOf:")[1].rstrip("}")
    return {"data": {c: {"arcs": {"containedInPlace+": {"nodes": CHILDREN[typ].get(c, [])}}}
                     for c in body["nodes"]}}


class WorldPlacesTest(unittest.TestCase):
    def build(self):
        out = Path(tempfile.mkdtemp()) / "world_places.tsv"
        with mock.patch.object(b, "_call", fake_call), \
                mock.patch("places.PlaceResolver.__init__", lambda self, **k: setattr(self, "allow_api", True)):
            status, n = b.build_world(out, force=True)
        rows = {line.split("\t")[0]: line.split("\t") for line in out.read_text(encoding="utf-8").splitlines()}
        return status, n, rows

    def test_every_level_with_country(self):
        status, n, rows = self.build()
        self.assertEqual(status, "updated")
        self.assertEqual(rows["q/nbo"], ["q/nbo", "Nairobi", "City", "Kenya"])
        self.assertEqual(rows["q/nbo-county"][2], "AdministrativeArea1")

    def test_place_in_two_levels_keeps_the_higher_one(self):
        _, _, rows = self.build()
        self.assertEqual(rows["q/byo"][2], "AdministrativeArea1")

    def test_same_name_in_one_country_gets_its_province(self):
        _, _, rows = self.build()
        self.assertEqual(rows["q/x1"][3], "Kenya › Nairobi County")
        self.assertEqual(rows["q/x2"][3], "Kenya › Kisumu County")

    def test_api_down_keeps_last_copy(self):
        out = Path(tempfile.mkdtemp()) / "world_places.tsv"
        out.write_text("old\tOld\tCity\tKenya\n", encoding="utf-8")

        def down(body, attempts=3):
            raise RuntimeError("timed out")

        with mock.patch.object(b, "_call", down), \
                mock.patch("places.PlaceResolver.__init__", lambda self, **k: setattr(self, "allow_api", True)):
            status, _ = b.build_world(out, force=True)
        self.assertEqual(status, "skipped")
        self.assertEqual(out.read_text(encoding="utf-8"), "old\tOld\tCity\tKenya\n")


if __name__ == "__main__":
    unittest.main()
