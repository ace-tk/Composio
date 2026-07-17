#!/usr/bin/env python3
"""
Pre-flight check: validates the environment before running the full pipeline.
Run this before main.py to catch configuration issues early.
"""
import json
import os
import sys
from pathlib import Path

# Ensure project root is on the path regardless of where this is called from
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.dataset_sync import (
    APPS_CSV,
    REPORT_JSON,
    RESEARCH_JSON,
    VERIFIED_JSON,
    load_apps_csv,
    load_records_by_app_name,
)


def check_api_key():
    key = os.getenv("GROQ_API_KEY", "")
    if not key or key == "your_groq_api_key_here":
        print("❌  GROQ_API_KEY is not set.")
        print("    Fix: export GROQ_API_KEY=gsk_...")
        return False
    print(f"✅  GROQ_API_KEY present (length: {len(key)})")
    return True


def check_apps_csv():
    _, ordered_app_names = load_apps_csv(APPS_CSV)
    if not ordered_app_names:
        print("❌  data/apps.csv not found or empty.")
        return False
    print(f"✅  data/apps.csv found — {len(ordered_app_names)} apps loaded.")
    return True


def check_modules():
    try:
        from agents.researcher import ResearchAgent
        from agents.verifier import VerifierAgent
        from agents.analyst import AnalystAgent
        from utils.models import SaaSApplicationData
        print("✅  All modules import successfully.")
        return True
    except ImportError as e:
        print(f"❌  Module import failed: {e}")
        return False


def check_artifact_sync():
    _, ordered_app_names = load_apps_csv(APPS_CSV)
    allowed_app_names = set(ordered_app_names)
    expected_count = len(ordered_app_names)

    stale_found = False
    for json_path in (RESEARCH_JSON, VERIFIED_JSON):
        if not json_path.exists():
            continue
        records = load_records_by_app_name(json_path)
        stale_count = len(records) - len(
            {name for name in records if name in allowed_app_names}
        )
        if stale_count:
            stale_found = True
            print(
                f"⚠️  {json_path.name} contains {stale_count} stale app(s) "
                f"not in apps.csv. Run: python scripts/sync_artifacts.py"
            )

    if REPORT_JSON.exists():
        with open(REPORT_JSON) as f:
            report = json.load(f)
        if report.get("total_apps") != expected_count:
            stale_found = True
            print(
                f"⚠️  verification_report.json total_apps={report.get('total_apps')} "
                f"but apps.csv has {expected_count}. Run: python scripts/sync_artifacts.py"
            )

    if stale_found:
        return False

    print("✅  Pipeline artifacts are synchronized with apps.csv.")
    return True


if __name__ == "__main__":
    print("\n=== Composio Pipeline Pre-flight Check ===\n")
    results = [
        check_api_key(),
        check_apps_csv(),
        check_modules(),
        check_artifact_sync(),
    ]
    print()
    if all(results):
        print("🚀  All checks passed. You can now run: python main.py\n")
        sys.exit(0)
    else:
        print("🛑  Some checks failed. Fix them before running main.py\n")
        sys.exit(1)
