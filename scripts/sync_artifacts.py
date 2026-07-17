#!/usr/bin/env python3
"""
Synchronize pipeline JSON artifacts with the current apps.csv.

Run this after changing apps.csv to remove stale records without re-running
the full research pipeline:

    python scripts/sync_artifacts.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.dataset_sync import (
    APPS_CSV,
    REPORT_JSON,
    load_apps_csv,
    sync_all_artifacts,
)


def main() -> int:
    _, ordered_app_names = load_apps_csv(APPS_CSV)
    if not ordered_app_names:
        print("❌  apps.csv is missing or empty.")
        return 1

    summary = sync_all_artifacts()

    print("\n=== Artifact Sync Complete ===\n")
    print(f"apps.csv:        {summary['csv_app_count']} apps")
    print(f"research.json:   {summary['research_count']} records", end="")
    if summary["research_purged"]:
        print(f"  (purged {summary['research_purged']} stale)")
    else:
        print()

    print(f"verified.json:   {summary['verified_count']} records", end="")
    if summary["verified_purged"]:
        print(f"  (purged {summary['verified_purged']} stale)")
    else:
        print()

    if REPORT_JSON.exists():
        with open(REPORT_JSON) as f:
            report = json.load(f)
        if report.get("total_apps") != summary["csv_app_count"]:
            report["total_apps"] = summary["csv_app_count"]
            with open(REPORT_JSON, "w") as f:
                json.dump(report, f, indent=2)
            print(f"verification_report.json: updated total_apps -> {summary['csv_app_count']}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
