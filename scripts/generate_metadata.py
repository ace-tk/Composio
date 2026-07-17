import json
import logging
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from utils.dataset_sync import load_apps_csv, sync_json_to_apps_csv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Base paths
DATA_DIR = BASE_DIR / "data"

APPS_CSV = DATA_DIR / "apps.csv"
VERIFIED_JSON = DATA_DIR / "verified.json"
WORKFLOW_JSON = DATA_DIR / "workflow.json"
CASE_STUDIES_JSON = DATA_DIR / "case_studies.json"

def generate_workflow_metadata():
    """
    Generates workflow.json detailing how the pipeline executed.
    """
    workflow_data = {
        "version": "1.0.0",
        "pipeline_stages": [
            "1. Discovery & Scraping (Research Agent)",
            "2. Data Extraction & Strict Formatting (Research Agent)",
            "3. Evidence Validation & Auditing (Verifier Agent)",
            "4. Human-in-the-Loop Fallback (Manual Review Queue)",
            "5. Strategic Pattern Analysis (Analyst Agent)",
            "6. Case Study Rendering"
        ],
        "execution_order": "Batch processing -> Verification -> Insights Generation -> Report Rendering",
        "research_count": 0,
        "verification_count": 0,
        "manual_review_count": 0,
        "failed_count": 0,
        "generated_artifacts": [
            "data/research.json",
            "data/verified.json",
            "data/verification_report.json",
            "data/insights.json",
            "data/workflow.json",
            "data/case_studies.json"
        ],
        "timing_information": {
            "report_generated_at": datetime.now().isoformat(),
            "total_agent_processing_seconds": 0.0
        }
    }
    
    total_time = 0.0

    _, ordered_app_names = load_apps_csv(APPS_CSV)
    allowed_app_names = set(ordered_app_names)
    _, data, _ = sync_json_to_apps_csv(
        VERIFIED_JSON, allowed_app_names, ordered_app_names
    )

    workflow_data["research_count"] = len(ordered_app_names)
    for item in data:
        status = item.get("status")
        if status == "Verified":
            workflow_data["verification_count"] += 1
        elif status == "Manual Review":
            workflow_data["manual_review_count"] += 1
        elif status == "Failed":
            workflow_data["failed_count"] += 1

        total_time += item.get("processing_time_seconds", 0.0)
                
    workflow_data["timing_information"]["total_agent_processing_seconds"] = round(total_time, 2)
    
    with open(WORKFLOW_JSON, "w") as f:
        json.dump(workflow_data, f, indent=2)
    logger.info("Generated workflow.json")
    
def generate_case_studies():
    """
    Generates case_studies.json picking up concrete examples of the AI's behavior.
    """
    case_studies = {
        "success_examples": [],
        "manual_review_examples": [],
        "confidence_drop_examples": []
    }
    
    _, ordered_app_names = load_apps_csv(APPS_CSV)
    allowed_app_names = set(ordered_app_names)
    _, data, _ = sync_json_to_apps_csv(
        VERIFIED_JSON, allowed_app_names, ordered_app_names
    )

    for item in data:
        app_name = item.get("app_name")
        status = item.get("status")
        conf = item.get("confidence_score", 0.0)
        notes = item.get("processing_notes", [])

        summary_obj = {
            "app_name": app_name,
            "status": status,
            "confidence_score": conf,
            "ai_reasoning_and_decisions": notes[-1] if notes else "Processed completely autonomously."
        }

        # Group into case studies
        if status == "Verified" and conf >= 90.0:
            case_studies["success_examples"].append(summary_obj)
        elif status == "Manual Review":
            case_studies["manual_review_examples"].append(summary_obj)
        elif conf < 90.0 and status == "Verified":
            case_studies["confidence_drop_examples"].append(summary_obj)
                
    # Limit to top 5 examples for the UI rendering later
    for key in case_studies:
        case_studies[key] = case_studies[key][:5]
        
    with open(CASE_STUDIES_JSON, "w") as f:
        json.dump(case_studies, f, indent=2)
    logger.info("Generated case_studies.json")

if __name__ == "__main__":
    logger.info("Generating final workflow metadata and case studies...")
    generate_workflow_metadata()
    generate_case_studies()
    logger.info("Done.")
