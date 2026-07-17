"""Tests for apps.csv synchronization utilities."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from utils.dataset_sync import (
    load_apps_csv,
    load_records_by_app_name,
    records_to_ordered_list,
    sync_json_to_apps_csv,
    sync_records_to_apps,
)


class DatasetSyncTests(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parent.parent
        self.csv_path = self.project_root / "data" / "apps.csv"
        self.apps, self.ordered_names = load_apps_csv(self.csv_path)
        self.allowed_names = set(self.ordered_names)

    def test_purges_stale_records_from_research_json(self):
        current_records = load_records_by_app_name(self.project_root / "data" / "research.json")
        current_list = records_to_ordered_list(current_records, self.ordered_names)
        stale_apps = [
            {"app_name": f"StaleApp_{i}", "website": f"https://stale{i}.com", "status": "Researched"}
            for i in range(66)
        ]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            csv_path = tmp_path / "apps.csv"
            research_path = tmp_path / "research.json"

            shutil.copy(self.csv_path, csv_path)
            with open(research_path, "w") as f:
                json.dump(current_list + stale_apps, f, indent=2)

            _, ordered_names = load_apps_csv(csv_path)
            allowed = set(ordered_names)
            _, ordered_records, updated = sync_json_to_apps_csv(
                research_path, allowed, ordered_names
            )

            self.assertTrue(updated)
            self.assertEqual(len(ordered_records), len(ordered_names))
            self.assertTrue(all(item["app_name"] in allowed for item in ordered_records))

            with open(research_path) as f:
                on_disk = json.load(f)
            self.assertEqual(len(on_disk), len(ordered_names))

    def test_skip_all_resume_still_rewrites_stale_file(self):
        current_records = load_records_by_app_name(self.project_root / "data" / "research.json")
        current_list = records_to_ordered_list(current_records, self.ordered_names)
        stale_apps = [
            {"app_name": f"StaleApp_{i}", "website": f"https://stale{i}.com", "status": "Researched"}
            for i in range(66)
        ]

        with tempfile.TemporaryDirectory() as tmp:
            research_path = Path(tmp) / "research.json"
            with open(research_path, "w") as f:
                json.dump(current_list + stale_apps, f, indent=2)

            existing = load_records_by_app_name(research_path)
            synced = sync_records_to_apps(existing, self.allowed_names)
            ordered_save = records_to_ordered_list(synced, self.ordered_names)

            with open(research_path, "w") as f:
                json.dump(ordered_save, f, indent=2)

            with open(research_path) as f:
                on_disk = json.load(f)

            self.assertEqual(len(on_disk), len(self.ordered_names))


if __name__ == "__main__":
    unittest.main()
