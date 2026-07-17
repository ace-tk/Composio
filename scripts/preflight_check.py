#!/usr/bin/env python3
"""
Pre-flight check: validates the environment before running the full pipeline.
Run this before main.py to catch configuration issues early.
"""
import os
import sys
from pathlib import Path

# Ensure project root is on the path regardless of where this is called from
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def check_api_key():
    key = os.getenv("GROQ_API_KEY", "")
    if not key or key == "your_groq_api_key_here":
        print("❌  GROQ_API_KEY is not set.")
        print("    Fix: export GROQ_API_KEY=gsk_...")
        return False
    print(f"✅  GROQ_API_KEY present (length: {len(key)})")
    return True
    return True

def check_apps_csv():
    path = Path("data/apps.csv")
    if not path.exists():
        print("❌  data/apps.csv not found.")
        return False
    with open(path) as f:
        rows = f.readlines()
    count = max(0, len(rows) - 1)  # subtract header
    print(f"✅  data/apps.csv found — {count} apps loaded.")
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

if __name__ == "__main__":
    print("\n=== Composio Pipeline Pre-flight Check ===\n")
    results = [check_api_key(), check_apps_csv(), check_modules()]
    print()
    if all(results):
        print("🚀  All checks passed. You can now run: python main.py\n")
        sys.exit(0)
    else:
        print("🛑  Some checks failed. Fix them before running main.py\n")
        sys.exit(1)
