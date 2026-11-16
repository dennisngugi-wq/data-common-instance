import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import runlog

OFFLINE = ("E1 fetch failed: GET https://api.example.org/data.csv failed: "
           "<urlopen error [Errno -3] Temporary failure in name resolution>")
LICENCE = "C4 PENDING: no licence URL for this dataset (never guessed)."


def accepted(**extra):
    return {"status": "ACCEPTED", "errors": [], "warnings": [LICENCE], "notes": [], "dataset": "Crops",
            "stats": {"observations": 1200, "places": 3, "first_date": "2001", "last_date": "2020"}, **extra}


class RunLogTest(unittest.TestCase):
    def test_offline_download_in_plain_words(self):
        info = runlog.explain(OFFLINE)
        self.assertIn("Could not download from api.example.org", info["headline"])
        self.assertIn("no internet connection", info["headline"])

    def test_server_error_and_missing_file(self):
        self.assertIn("server had an error", runlog.explain("E1 fetch failed: HTTP Error 500: Internal")["headline"])
        self.assertIn("not found", runlog.explain("E1 fetch failed: HTTP Error 404: Not Found")["headline"])

    def test_read_error_has_no_repeated_prefix(self):
        info = runlog.explain("E1 could not read the data: ValueError('bad')")
        self.assertEqual(info["headline"].lower().count("could not read the data"), 1)

    def test_feed_block_hides_licence_warning_and_shows_counts(self):
        block = runlog.feed_block("crops", accepted(published_now=True), "  1/10", 3)
        self.assertIn("UPDATED", block)
        self.assertIn("1,200 values", block)
        self.assertNotIn("licence", block)

    def test_summary_groups_repeated_problems(self):
        report = {"mode": "publish", "live_imports": ["a", "b", "c"], "config_problems": [], "schema_errors": [],
                  "disabled_feeds": [], "feeds": {
                      "a": {"status": "REJECTED", "errors": [OFFLINE], "warnings": [LICENCE]},
                      "b": {"status": "REJECTED", "errors": [OFFLINE.replace("api.", "www.")], "warnings": []},
                      "c": {"status": "REJECTED", "errors": ["E1 could not read the data: KeyError('x')"]},
                      "d": accepted(published_now=True)}}
        text = runlog.summary(report)
        self.assertIn("2 dataset(s) could not be downloaded: no internet connection", text)
        self.assertIn("2 dataset(s) have no licence URL", text)
        self.assertIn("NEW DATA PUBLISHED", text)
        self.assertLess(text.index("could not be downloaded"), text.index("no licence URL"))

    def test_summary_when_all_clean(self):
        report = {"mode": "publish", "live_imports": ["d"], "config_problems": [], "schema_errors": [],
                  "disabled_feeds": [], "feeds": {"d": {**accepted(), "warnings": []}}}
        self.assertIn("NEEDS YOUR ATTENTION: nothing", runlog.summary(report))

    def test_sheet_fallback_line(self):
        lines = runlog.source_lines({"zimstat": {"status": "USING LAST COPY", "error": OFFLINE,
                                                 "datasets_loaded": 99, "last_copy_from": "2026-11-13 14:10 UTC"}})
        text = "\n".join(lines)
        self.assertIn("SAVED COPY", text)
        self.assertIn("no internet connection", text)
        self.assertIn("2026-11-13 14:10 UTC", text)


if __name__ == "__main__":
    unittest.main()
