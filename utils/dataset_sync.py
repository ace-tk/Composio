"""
Utilities to keep JSON pipeline artifacts synchronized with the current apps.csv.
"""
import csv
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
APPS_CSV = DATA_DIR / "apps.csv"
RESEARCH_JSON = DATA_DIR / "research.json"
VERIFIED_JSON = DATA_DIR / "verified.json"
REPORT_JSON = DATA_DIR / "verification_report.json"


def load_apps_csv(csv_path: Path) -> tuple[list[dict], list[str]]:
    """Load apps.csv rows and return (rows, ordered_app_names)."""
    if not csv_path.exists():
        return [], []

    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows, [row["app_name"] for row in rows]


def load_records_by_app_name(json_path: Path) -> dict[str, dict]:
    """Load a JSON list file into a dict keyed by app_name."""
    if not json_path.exists():
        return {}

    try:
        with open(json_path) as f:
            data_list = json.load(f)
    except json.JSONDecodeError:
        logger.warning("%s is corrupted or empty.", json_path)
        return {}

    if not isinstance(data_list, list):
        logger.warning("%s does not contain a JSON list.", json_path)
        return {}

    records: dict[str, dict] = {}
    for item in data_list:
        if isinstance(item, dict):
            app_name = item.get("app_name")
            if app_name:
                records[app_name] = item
    return records


def sync_records_to_apps(
    records: dict[str, dict],
    allowed_app_names: set[str],
) -> dict[str, dict]:
    """Return only records whose app_name is present in the current apps.csv."""
    return {
        app_name: data
        for app_name, data in records.items()
        if app_name in allowed_app_names
    }


def records_to_ordered_list(
    records: dict[str, dict],
    ordered_app_names: list[str],
) -> list[dict]:
    """Build a list in apps.csv order, including only apps that have records."""
    return [records[app_name] for app_name in ordered_app_names if app_name in records]


def _list_app_names(data_list: list[dict]) -> list[str]:
    return [item.get("app_name") for item in data_list if isinstance(item, dict)]


def sync_json_to_apps_csv(
    json_path: Path,
    allowed_app_names: set[str],
    ordered_app_names: list[str],
) -> tuple[dict[str, dict], list[dict], bool]:
    """
    Load a JSON artifact, drop apps not in apps.csv, and rewrite the file when
    its on-disk contents are out of sync.

    Returns (records_by_name, ordered_records, file_was_updated).
    """
    records = load_records_by_app_name(json_path)
    synced_records = sync_records_to_apps(records, allowed_app_names)
    ordered_records = records_to_ordered_list(synced_records, ordered_app_names)
    expected_names = _list_app_names(ordered_records)

    on_disk_names: list[str] | None = None
    if json_path.exists():
        try:
            with open(json_path) as f:
                on_disk = json.load(f)
            if isinstance(on_disk, list):
                on_disk_names = _list_app_names(on_disk)
        except json.JSONDecodeError:
            on_disk_names = None

    stale_count = len(records) - len(synced_records)
    file_is_out_of_sync = on_disk_names != expected_names
    file_was_updated = False

    if file_is_out_of_sync:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w") as f:
            json.dump(ordered_records, f, indent=2)
        file_was_updated = True
        if stale_count:
            logger.info(
                "Purged %d stale record(s) from %s; kept %d app(s) from apps.csv.",
                stale_count,
                json_path.name,
                len(ordered_records),
            )
        else:
            logger.info(
                "Rewrote %s to match apps.csv order (%d app(s)).",
                json_path.name,
                len(ordered_records),
            )

    return synced_records, ordered_records, file_was_updated


def sync_all_artifacts(
    csv_path: Path = APPS_CSV,
    research_path: Path = RESEARCH_JSON,
    verified_path: Path = VERIFIED_JSON,
) -> dict[str, int]:
    """
    Synchronize research.json and verified.json with the current apps.csv.

    Returns a summary dict with counts for logging and preflight checks.
    """
    _, ordered_app_names = load_apps_csv(csv_path)
    allowed_app_names = set(ordered_app_names)

    summary = {
        "csv_app_count": len(ordered_app_names),
        "research_count": 0,
        "verified_count": 0,
        "research_purged": 0,
        "verified_purged": 0,
    }

    if not ordered_app_names:
        return summary

    if research_path.exists():
        before = load_records_by_app_name(research_path)
        _, ordered_research, _ = sync_json_to_apps_csv(
            research_path, allowed_app_names, ordered_app_names
        )
        summary["research_count"] = len(ordered_research)
        summary["research_purged"] = len(before) - len(
            sync_records_to_apps(before, allowed_app_names)
        )

    if verified_path.exists():
        before = load_records_by_app_name(verified_path)
        _, ordered_verified, _ = sync_json_to_apps_csv(
            verified_path, allowed_app_names, ordered_app_names
        )
        summary["verified_count"] = len(ordered_verified)
        summary["verified_purged"] = len(before) - len(
            sync_records_to_apps(before, allowed_app_names)
        )

    return summary
