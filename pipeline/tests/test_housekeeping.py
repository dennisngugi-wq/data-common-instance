import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["DC_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import run_pipeline
from storage import LocalStore


class PruneTest(unittest.TestCase):
    def test_only_the_newest_backups_and_reports_are_kept(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        store = LocalStore(tmp)
        for day in range(1, 8):
            store.write_text(f"backups/202611{day:02d}T000000Z/feed/feed.csv", "x")
            store.write_text(f"reports/202611{day:02d}T000000Z.json", "{}")
        store.write_text("state/pipeline_state.json", "{}")
        self.assertEqual(run_pipeline.prune(store, keep_backups=3, keep_reports=5), 6)
        self.assertEqual(sorted(p.name for p in (tmp / "backups").iterdir()),
                         ["20261105T000000Z", "20261106T000000Z", "20261107T000000Z"])
        self.assertEqual(len(list((tmp / "reports").iterdir())), 5)
        self.assertTrue((tmp / "state" / "pipeline_state.json").exists())
        self.assertEqual(run_pipeline.prune(store, keep_backups=3, keep_reports=5), 0)


if __name__ == "__main__":
    unittest.main()
