import os
import sys
import unittest
from pathlib import Path

os.environ["DC_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import build_site_catalog
import topics


class TopicsTest(unittest.TestCase):
    def test_most_specific_label_wins(self):
        self.assertEqual(topics.classify(["Labour Statistics", "Macroeconomics"]), ["employment"])
        self.assertEqual(topics.classify(["Prison Statistics", "Demographic & Social"]), ["crime"])
        self.assertEqual(topics.classify(["Uncategorized", "Macroeconomics"]), ["economy"])

    def test_text_is_used_only_without_a_matching_label(self):
        self.assertEqual(topics.classify(["Demographics"], "economy economy prices"), ["demographics"])
        self.assertEqual(topics.classify([], "Annual CO2 emissions per person"), ["environment"])
        self.assertEqual(topics.classify([], "Something unrelated"), ["other"])

    def test_data_sources_layout_matches_the_stock_page(self):
        catalog = {"sources": [{"name": "World Bank", "url": "https://www.worldbank.org/", "datasets": [{
            "dcid": "dc/d/WDI", "name": "World Development Indicators", "description": "x" * 400 + ". More.",
            "url": "https://example.org/wdi", "license": "https://creativecommons.org/licenses/by/4.0/",
            "refreshed": "2026-11-13", "places": 216, "variables": 1, "first_date": "1960", "last_date": "2025",
            "topics": ["demographics", "economy"]}]}]}
        out = build_site_catalog.data_sources(catalog)
        self.assertEqual([t["slug"] for t in out], ["demographics", "economy"])
        group = out[0]["dataSourceGroups"][0]
        self.assertEqual((group["label"], group["url"]), ("World Bank", "https://www.worldbank.org/"))
        ds = group["dataSources"][0]
        self.assertEqual(ds["url"], "/dataset/dc/d/WDI")
        self.assertIn("[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)", ds["description"])
        self.assertLess(len(ds["description"].split("\n\n")[0]), 340)

    def test_licence_labels(self):
        self.assertEqual(build_site_catalog.licence_label("https://creativecommons.org/licenses/by-sa/3.0/"),
                         "CC BY-SA 3.0")
        self.assertEqual(build_site_catalog.licence_label("https://creativecommons.org/publicdomain/zero/1.0/"),
                         "CC0 1.0")
        self.assertEqual(build_site_catalog.licence_label("https://example.org/terms"), "Licence")


if __name__ == "__main__":
    unittest.main()


def test_tool_examples_come_only_from_loaded_variables():
    import build_homepage
    assert build_homepage.tool_examples({})["timeline_vis_tool_examples.json"] == []
    out = build_homepage.tool_examples({"Count_X": {"name": "Total X", "places": {"country/KEN": None}}})
    assert out["map_vis_tool_examples.json"][0]["title"] == "Total X"
    assert "country%2FKEN" in out["timeline_vis_tool_examples.json"][0]["url"]
