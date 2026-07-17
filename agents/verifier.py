import logging
import time
from datetime import datetime
import os
import instructor
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from utils.models import SaaSApplicationData, ResearchStatus
from utils.scraper import fetch_page_text

logger = logging.getLogger(__name__)

class VerificationResult(BaseModel):
    """Internal model for the LLM output during verification."""
    is_valid: bool = Field(..., description="true if the original claims are fully supported by evidence.")
    confidence_adjustment: float = Field(..., description="Amount to add/subtract from the original confidence score. Negative for penalties.")
    reasoning: str = Field(..., description="Detailed explanation of why claims are valid or invalid.")
    manual_review_needed: bool = Field(..., description="true if evidence is insufficient, contradictory, missing, or required fields are absent.")

class VerifierAgent:
    """
    The VerifierAgent is strictly responsible for auditing the ResearcherAgent's work.
    It does not scrape new URLs or invent data; it only validates existing claims against attached evidence.
    """
    
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY", "")
        self.client = instructor.from_openai(
            AsyncOpenAI(
                api_key=api_key,
                base_url="https://api.groq.com/openai/v1"
            ),
            mode=instructor.Mode.JSON
        )

    async def verify(self, app_data: SaaSApplicationData) -> SaaSApplicationData:
        """
        Cross-checks the extracted data against the evidence URLs.
        Updates confidence scores and determines if a human needs to step in.
        """
        logger.info(f"Verifying {app_data.app_name}...")
        start_time = time.time()
        
        try:
            # 1. Quick Failure: Is there any evidence at all?
            if not app_data.evidence:
                logger.warning(f"No evidence found for {app_data.app_name}.")
                app_data.confidence_score = min(app_data.confidence_score, 40.0)
                app_data.manual_review_required = True
                app_data.status = ResearchStatus.MANUAL_REVIEW
                app_data.processing_notes.append("Verifier: No evidence URLs provided by Researcher.")
                app_data.verification_timestamp = datetime.now().isoformat()
                app_data.processing_time_seconds = (app_data.processing_time_seconds or 0) + round(time.time() - start_time, 2)
                return app_data

            # 2. Fetch the text from the provided evidence URLs
            evidence_content = ""
            for ev in app_data.evidence:
                logger.info(f"Checking evidence at {ev.url}...")
                text = await fetch_page_text(str(ev.url))
                if text:
                    # Append limited snippet to keep prompt focused
                    evidence_content += f"\n\n--- Source: {ev.url} ---\nClaim to verify: {ev.claim_supported}\nContent:\n{text[:5000]}"
            
            # If the sites blocked the Verifier
            if not evidence_content:
                logger.warning(f"Failed to fetch content from evidence URLs for {app_data.app_name}.")
                app_data.confidence_score -= 20.0
                app_data.manual_review_required = True
                app_data.status = ResearchStatus.MANUAL_REVIEW
                app_data.processing_notes.append("Verifier: Could not fetch text from evidence URLs.")
                app_data.verification_timestamp = datetime.now().isoformat()
                app_data.processing_time_seconds = (app_data.processing_time_seconds or 0) + round(time.time() - start_time, 2)
                return app_data
                
            # 3. Prompt the LLM strictly as an Auditor
            prompt = f"""
            You are a strict Verification Engine for an AI Product Ops system.
            Your job is to audit the integration metadata for {app_data.app_name}.
            
            Original Claims by Researcher:
            - Auth Methods: {[m.value for m in app_data.authentication_methods]}
            - Self-serve vs Gated: {app_data.self_serve_status.value}
            - API Surface: {[s.value for s in app_data.api_surface]}
            - MCP Available: {app_data.mcp_available}
            - Buildability Verdict: {app_data.buildability_verdict.value}
            
            Evidence Text Scraped from Provided URLs:
            {evidence_content}
            
            Rules:
            1. DO NOT invent new information. You are an auditor, not a researcher.
            2. If the text does not explicitly support the claims, mark is_valid=false and manual_review_needed=true.
            3. If the evidence is contradictory (e.g. text says OAuth 2.0 but claim says Basic), require manual review.
            4. If required fields appear "Unknown", require manual review.
            5. Be harsh. We prioritize accuracy over automation.
            """
            
            result: VerificationResult = await self.client.chat.completions.create(
                model="llama-3.1-8b-instant",  # Groq model: matches researcher for consistency
                response_model=VerificationResult,
                messages=[
                    {"role": "system", "content": "You are a highly skeptical auditor."},
                    {"role": "user", "content": prompt}
                ],
                max_retries=3
            )
            
            # 4. Apply LLM Judgment
            app_data.confidence_score = max(0.0, min(100.0, app_data.confidence_score + result.confidence_adjustment))
            app_data.processing_notes.append(f"Verifier Reasoning: {result.reasoning}")
            
            # 5. Routing Rules
            if result.manual_review_needed or not result.is_valid or app_data.confidence_score < 75.0:
                app_data.manual_review_required = True
                app_data.status = ResearchStatus.MANUAL_REVIEW
                logger.info(f"Manual Review Required for {app_data.app_name}. Score: {app_data.confidence_score}")
            else:
                app_data.manual_review_required = False
                app_data.status = ResearchStatus.VERIFIED
                logger.info(f"Evidence Found ✓ Confidence {app_data.confidence_score}% Verified")

            app_data.verification_timestamp = datetime.now().isoformat()
            app_data.processing_time_seconds = (app_data.processing_time_seconds or 0) + round(time.time() - start_time, 2)
            
            return app_data
            
        except Exception as e:
            logger.error(f"Error verifying {app_data.app_name}: {e}")
            app_data.status = ResearchStatus.FAILED
            app_data.processing_notes.append(f"Verification Failed with exception: {e}")
            app_data.verification_timestamp = datetime.now().isoformat()
            return app_data
