"""
Publish pipeline JSON artifacts into templates/data/ for the static dashboard.

Vercel deploys the templates/ directory as the site root, so dashboard JSON
must live under templates/data/ to be reachable at /data/*.json.
"""
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Files the dashboard (and related UI) may fetch after deployment.
DASHBOARD_JSON_FILES = (
    "verification_report.json",
    "insights.json",
    "case_studies.json",
    "workflow.json",
    "research.json",
    "verified.json",
)


def publish_dashboard_data(
    source_dir: Path,
    dest_dir: Path,
) -> list[str]:
    """
    Copy dashboard JSON artifacts from the pipeline data/ folder into
    templates/data/ so they are available on the static site.

    Returns the list of filenames that were copied.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []

    for filename in DASHBOARD_JSON_FILES:
        src = source_dir / filename
        if not src.exists():
            logger.warning("Skipping missing dashboard artifact: %s", src)
            continue
        shutil.copy2(src, dest_dir / filename)
        copied.append(filename)

    if copied:
        logger.info(
            "Published %d dashboard artifact(s) to %s",
            len(copied),
            dest_dir,
        )
    return copied
