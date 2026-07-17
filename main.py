import asyncio
import csv
import json
import logging
import os
import time
from pathlib import Path

from agents.researcher import ResearchAgent
from agents.verifier import VerifierAgent
from agents.analyst import AnalystAgent
from scripts.generate_metadata import generate_workflow_metadata, generate_case_studies
from utils.models import SaaSApplicationData, ResearchStatus

# Configure robust logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
APPS_CSV = DATA_DIR / "apps.csv"
RESEARCH_JSON = DATA_DIR / "research.json"
VERIFIED_JSON = DATA_DIR / "verified.json"
REPORT_JSON = DATA_DIR / "verification_report.json"
INSIGHTS_JSON = DATA_DIR / "insights.json"
WORKFLOW_JSON = DATA_DIR / "workflow.json"
CASE_STUDIES_JSON = DATA_DIR / "case_studies.json"

async def process_apps():
    """
    Reads apps from CSV and runs the ResearchAgent on each.
    Saves progress iteratively to avoid data loss.
    Handles network retries gracefully.
    """
    agent = ResearchAgent()
    
    existing_data = {}
    if RESEARCH_JSON.exists():
        try:
            with open(RESEARCH_JSON, "r") as f:
                data_list = json.load(f)
                for item in data_list:
                    existing_data[item["app_name"]] = item
            logger.info(f"Loaded {len(existing_data)} existing records. Resuming research...")
        except json.JSONDecodeError:
            logger.warning("research.json is corrupted or empty. Starting fresh.")

    apps_to_process = []
    if APPS_CSV.exists():
        with open(APPS_CSV, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                apps_to_process.append(row)
    else:
        logger.error(f"Cannot find {APPS_CSV}. Please ensure it exists.")
        return
            
    total_apps = len(apps_to_process)
    
    for idx, row in enumerate(apps_to_process, 1):
        app_name = row["app_name"]
        website = row["url"]
        
        logger.info(f"Progress: [{idx}/{total_apps}] - Processing {app_name}")
        
        # Resume capability
        if app_name in existing_data:
            status = existing_data[app_name].get("status")
            if status in [ResearchStatus.RESEARCHED.value, ResearchStatus.VERIFIED.value, ResearchStatus.MANUAL_REVIEW.value, ResearchStatus.FAILED.value]:
                logger.info(f"Skipping {app_name}, already researched (Status: {status}).")
                continue
            
        # Resilient Execution Loop (Max 3 retries for transient errors)
        max_retries = 3
        result = None
        for attempt in range(max_retries):
            try:
                result = await agent.run(app_name, website)
                if result.status == ResearchStatus.FAILED:
                    logger.warning(f"Agent returned FAILED for {app_name}. Retrying ({attempt+1}/{max_retries})...")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                break # Success or max retries reached
            except Exception as e:
                logger.error(f"Unexpected crash while processing {app_name}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    # Failsafe object to keep pipeline moving
                    result = SaaSApplicationData(
                        app_name=app_name,
                        website=website,
                        category="Unknown",
                        one_line_description="Failed to process due to repeated errors.",
                        agent_summary=f"Critical Exception encountered: {str(e)}",
                        authentication_methods=[],
                        self_serve_status="Unknown",
                        api_surface=[],
                        mcp_available=False,
                        buildability_verdict="Unknown",
                        status=ResearchStatus.FAILED,
                        processing_time_seconds=0,
                        processing_notes=[f"Failed after {max_retries} attempts."],
                        evidence=[]
                    )
                    break
        
        # Save atomically immediately after every single app
        existing_data[app_name] = result.model_dump(mode='json')
        with open(RESEARCH_JSON, "w") as f:
            json.dump(list(existing_data.values()), f, indent=2)
            
        if result.status == ResearchStatus.FAILED:
            logger.error(f"Permanently failed to process {app_name}.")
        else:
            logger.info(f"Saved Successfully: {app_name}")

        # Rate-limit guard: Groq free-tier allows ~30 req/min; sleep between apps
        await asyncio.sleep(15)
            
    logger.info("Research pipeline complete.")

def classify_failure(app_data: SaaSApplicationData) -> Optional[str]:
    """
    Classifies a failed or manual review application into a standardized failure category
    by inspecting its processing notes, status, and other fields.
    """
    if app_data.status not in [ResearchStatus.MANUAL_REVIEW, ResearchStatus.FAILED]:
        return None

    # Gather all text describing the failure/review reason
    text_blocks = []
    if app_data.processing_notes:
        text_blocks.extend(app_data.processing_notes)
    if hasattr(app_data, 'notes') and app_data.notes:
        text_blocks.append(app_data.notes)
    if app_data.one_line_description:
        text_blocks.append(app_data.one_line_description)
    if app_data.agent_summary:
        text_blocks.append(app_data.agent_summary)

    combined_text = " ".join(text_blocks).lower()

    # Match patterns to standardized categories in order of specificity
    # 1. Rate Limit
    if any(p in combined_text for p in ["429", "rate limit"]):
        return "Rate Limit"

    # 2. Validation Error
    if any(p in combined_text for p in ["failed to call a function", "tool call validation failed", "schema validation", "json_validate_failed", "validation error"]):
        return "Validation Error"

    # 3. Network Error
    if any(p in combined_text for p in ["timeout", "network error", "connection error"]):
        return "Network Error"

    # 4. Evidence Fetch Failure
    if any(p in combined_text for p in ["could not fetch text", "no evidence urls", "fetch failed", "evidence fetch failure", "no scraped content", "content loaded is also empty"]):
        return "Evidence Fetch Failure"

    # 5. LLM Failure
    if any(p in combined_text for p in ["exhausted all retries", "llm failure"]):
        return "LLM Failure"

    # 6. Contradictory Evidence
    if any(p in combined_text for p in ["contradict", "conflict", "does not match"]):
        return "Contradictory Evidence"

    # 7. Unsupported Claim
    if any(p in combined_text for p in ["unsupported", "does not support", "not supported", "unspecified"]):
        return "Unsupported Claim"

    # 8. Documentation Ambiguity
    if any(p in combined_text for p in ["insufficient evidence", "silent", "ambiguous", "does not explicitly", "insufficient", "not explicitly", "limited info", "limited", "incomplete"]):
        return "Documentation Ambiguity"

    # 9. Unexpected Exception
    if any(p in combined_text for p in ["exception", "crash"]):
        return "Unexpected Exception"

    return "Other"

async def process_verification():
    logger.info("Starting Verification Pipeline...")
    
    if not RESEARCH_JSON.exists():
        logger.error("No research.json found. Please run the research pipeline first.")
        return
        
    with open(RESEARCH_JSON, "r") as f:
        try:
            research_data = json.load(f)
        except json.JSONDecodeError:
            logger.error("research.json is corrupted.")
            return

    verifier = VerifierAgent()
    verified_data = {}
    if VERIFIED_JSON.exists():
        try:
            with open(VERIFIED_JSON, "r") as f:
                data_list = json.load(f)
                for item in data_list:
                    verified_data[item["app_name"]] = item
            logger.info(f"Loaded {len(verified_data)} existing verified records.")
        except json.JSONDecodeError:
            pass

    report = {
        "total_apps": len(research_data),
        "automatically_verified": 0,
        "manual_review_required": 0,
        "failed": 0,
        "average_confidence": 0.0,
        "verification_time": 0.0,
        "common_failure_reasons": [],
        "failure_categories": {
            "Rate Limit": 0,
            "Evidence Fetch Failure": 0,
            "LLM Failure": 0,
            "Validation Error": 0,
            "Documentation Ambiguity": 0,
            "Contradictory Evidence": 0,
            "Unsupported Claim": 0,
            "Network Error": 0,
            "Unexpected Exception": 0,
            "Other": 0
        }
    }
    
    total_confidence = 0.0
    start_time = time.time()
    
    total_apps = len(research_data)
    for idx, item in enumerate(research_data, 1):
        app_name = item["app_name"]
        logger.info(f"Verification Progress: [{idx}/{total_apps}] - {app_name}")
        
        app_data = SaaSApplicationData.model_validate(item)
        
        if app_name in verified_data and verified_data[app_name].get("status") in [ResearchStatus.VERIFIED.value, ResearchStatus.MANUAL_REVIEW.value]:
             app_data = SaaSApplicationData.model_validate(verified_data[app_name])
        else:
             if app_data.status not in [ResearchStatus.FAILED, ResearchStatus.NEW]:
                 # Resilient verification execution
                 max_retries = 3
                 for attempt in range(max_retries):
                     try:
                         app_data = await verifier.verify(app_data)
                         break
                     except Exception as e:
                         if attempt < max_retries - 1:
                             await asyncio.sleep(2 ** attempt)
                         else:
                             logger.error(f"Verification crashed for {app_name}: {e}")
                             app_data.status = ResearchStatus.FAILED
                 
                 verified_data[app_name] = app_data.model_dump(mode='json')
                 with open(VERIFIED_JSON, "w") as f:
                     json.dump(list(verified_data.values()), f, indent=2)

        # Update Stats
        if app_data.status == ResearchStatus.VERIFIED:
            report["automatically_verified"] += 1
        elif app_data.status == ResearchStatus.MANUAL_REVIEW:
            report["manual_review_required"] += 1
        elif app_data.status == ResearchStatus.FAILED:
            report["failed"] += 1
            
        total_confidence += app_data.confidence_score
        
        if app_data.status != ResearchStatus.VERIFIED:
            if app_data.processing_notes:
                last_note = app_data.processing_notes[-1]
                report["common_failure_reasons"].append(f"{app_name}: {last_note[:120]}")
            
            category = classify_failure(app_data)
            if category and category in report["failure_categories"]:
                report["failure_categories"][category] += 1
            
    if report["total_apps"] > 0:
        report["average_confidence"] = round(total_confidence / report["total_apps"], 2)
        
    report["verification_time"] = round(time.time() - start_time, 2)

    # Validation check
    total_failures_and_review = report["failed"] + report["manual_review_required"]
    sum_categorized = sum(report["failure_categories"].values())
    if sum_categorized > total_failures_and_review:
        logger.error(f"Validation failed: sum of categorized failures ({sum_categorized}) > total failed + manual review ({total_failures_and_review})")
    else:
        logger.info(f"Validation passed: sum of categorized failures ({sum_categorized}) <= total failed + manual review ({total_failures_and_review})")
    
    with open(REPORT_JSON, "w") as f:
        json.dump(report, f, indent=2)
        
    logger.info("Verification pipeline complete.")

async def process_analysis():
    logger.info("Starting Pattern Analysis Engine...")
    
    if not VERIFIED_JSON.exists():
        logger.error("No verified.json found.")
        return
        
    with open(VERIFIED_JSON, "r") as f:
         verified_data = json.load(f)
            
    apps_to_analyze = [SaaSApplicationData.model_validate(item) for item in verified_data if item.get("status") == ResearchStatus.VERIFIED.value]
            
    if not apps_to_analyze:
        logger.warning("No fully VERIFIED apps available for analysis.")
        return
        
    analyst = AnalystAgent()
    try:
        report = await analyst.analyze(apps_to_analyze)
        with open(INSIGHTS_JSON, "w") as f:
            json.dump(report.model_dump(mode='json'), f, indent=2)
        logger.info("Insights generated.")
    except Exception as e:
        logger.error(f"Failed to generate analysis: {e}")

def validate_outputs():
    """Verifies all expected JSON files were created and are valid."""
    logger.info("Validating output artifacts...")
    required_files = [RESEARCH_JSON, VERIFIED_JSON, REPORT_JSON, INSIGHTS_JSON, WORKFLOW_JSON, CASE_STUDIES_JSON]
    
    all_valid = True
    for fpath in required_files:
        if not fpath.exists():
            logger.error(f"CRITICAL ERROR: Expected output file missing: {fpath}")
            all_valid = False
            continue
        try:
            with open(fpath, "r") as f:
                json.load(f)
        except json.JSONDecodeError:
            logger.error(f"CRITICAL ERROR: File is corrupted or invalid JSON: {fpath}")
            all_valid = False
            
    if all_valid:
        logger.info("All output artifacts successfully validated.")
    else:
        logger.error("Pipeline failed output validation.")

def print_execution_summary(start_time):
    """Prints a clear terminal summary of the entire run."""
    print("\n" + "="*50)
    print("COMPOSIO AI PIPELINE EXECUTION SUMMARY")
    print("="*50)
    
    try:
        with open(REPORT_JSON, "r") as f:
            report = json.load(f)
            
        print(f"Total Apps Processed:     {report.get('total_apps', 0)}")
        print(f"Automatically Verified:   {report.get('automatically_verified', 0)}")
        print(f"Manual Review Required:   {report.get('manual_review_required', 0)}")
        print(f"Failed Extractions:       {report.get('failed', 0)}")
        print(f"Average AI Confidence:    {report.get('average_confidence', 0)}%")
        
    except Exception:
        print("Error reading verification report.")
        
    elapsed = round(time.time() - start_time, 2)
    print(f"Total Execution Time:     {elapsed} seconds")
    print("\nGenerated Output Files:")
    print("  ✓ data/research.json")
    print("  ✓ data/verified.json")
    print("  ✓ data/verification_report.json")
    print("  ✓ data/insights.json")
    print("  ✓ data/workflow.json")
    print("  ✓ data/case_studies.json")
    print("="*50 + "\n")

def main():
    start_time = time.time()
    
    async def run_all():
        await process_apps()
        await process_verification()
        await process_analysis()
        
    # Execute the asynchronous pipeline
    asyncio.run(run_all())
    
    # Generate static metadata
    logger.info("Generating workflow metadata and case studies...")
    generate_workflow_metadata()
    generate_case_studies()
    
    # Validate final outputs
    validate_outputs()
    
    # Print summary
    print_execution_summary(start_time)

if __name__ == "__main__":
    main()
