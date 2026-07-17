"""
VerifierAgent — Robust, fault-tolerant verification stage.

Improvements over v1:
  1. Exponential backoff for 429s, timeouts, and transient errors.
  2. Evidence fallback: uses claim text when URLs cannot be fetched.
  3. Per-field claim auditing: ambiguous fields → Manual Review, not Failed.
  4. Structured failure categories logged and returned in app_data.
  5. Failure category counts emitted for verification_report.json.
"""

import asyncio
import logging
import time
from datetime import datetime
from enum import Enum
import os
import httpx
import instructor
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from typing import List, Optional

from utils.models import SaaSApplicationData, ResearchStatus
from utils.scraper import fetch_page_text_with_retry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured Failure Categories
# ---------------------------------------------------------------------------

class FailureCategory(str, Enum):
    RATE_LIMIT           = "Rate Limit"
    EVIDENCE_FETCH       = "Evidence Fetch Failure"
    DOC_AMBIGUITY        = "Documentation Ambiguity"
    VALIDATION_ERROR     = "Validation Error"
    UNEXPECTED_EXCEPTION = "Unexpected Exception"


# ---------------------------------------------------------------------------
# LLM output schema — per-field auditing
# ---------------------------------------------------------------------------

class FieldVerdict(BaseModel):
    field: str = Field(..., description="The field being audited (e.g. 'auth_methods', 'api_surface').")
    supported: bool = Field(..., description="True if the evidence explicitly supports the claimed value.")
    ambiguous: bool = Field(..., description="True if evidence is incomplete or unclear about this field.")
    reasoning: str = Field(..., description="One sentence explanation for this field's verdict.")


class VerificationResult(BaseModel):
    """Per-field audit result returned by the LLM."""
    field_verdicts: List[FieldVerdict] = Field(
        ...,
        description="Verdicts for each critical field: auth_methods, self_serve_status, api_surface, mcp_available, buildability_verdict."
    )
    confidence_adjustment: float = Field(
        ...,
        description="Net amount to add/subtract from original confidence score. Negative = penalty."
    )
    overall_reasoning: str = Field(
        ...,
        description="High-level summary of the verification decision."
    )


# ---------------------------------------------------------------------------
# VerifierAgent
# ---------------------------------------------------------------------------

class VerifierAgent:
    """
    Audits ResearcherAgent claims against live documentation evidence.

    Resilience features:
    - Retries LLM calls up to MAX_LLM_RETRIES times with exponential backoff.
    - Falls back to claim text when evidence URLs are unreachable.
    - Routes ambiguous-but-mostly-valid records to Manual Review, not Failed.
    - Emits structured failure categories for reporting.
    """

    MAX_LLM_RETRIES   = 3
    BASE_BACKOFF_SECS = 2   # seconds; doubles each retry

    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY", "")
        self.client = instructor.from_openai(
            AsyncOpenAI(
                api_key=api_key,
                base_url="https://api.groq.com/openai/v1"
            ),
            mode=instructor.Mode.JSON
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def verify(self, app_data: SaaSApplicationData) -> SaaSApplicationData:
        """
        Runs the full verification workflow. Returns the updated SaaSApplicationData.
        Never raises — all errors are captured and routed to appropriate statuses.
        """
        logger.info(f"[Verifier] Starting → {app_data.app_name}")
        start_time = time.time()

        try:
            # ── Step 1: Guard — ensure there is at least some evidence ──
            if not app_data.evidence:
                return self._flag_manual_review(
                    app_data,
                    FailureCategory.EVIDENCE_FETCH,
                    "No evidence URLs provided by Researcher.",
                    confidence_penalty=20.0,
                    start_time=start_time,
                )

            # ── Step 2: Fetch evidence content with retry + fallback ──
            evidence_content = await self._build_evidence_content(app_data)

            if not evidence_content:
                # Complete fetch failure — still try fallback claim text
                fallback = self._build_fallback_from_claims(app_data)
                if fallback:
                    logger.warning(
                        f"[Verifier] {app_data.app_name}: all URLs blocked. "
                        "Using claim text as fallback evidence."
                    )
                    evidence_content = fallback
                    app_data.processing_notes.append(
                        "Verifier: Could not fetch evidence URLs. Using research claim text as fallback."
                    )
                else:
                    return self._flag_manual_review(
                        app_data,
                        FailureCategory.EVIDENCE_FETCH,
                        "Could not fetch text from any evidence URL and no fallback claim text available.",
                        confidence_penalty=20.0,
                        start_time=start_time,
                    )

            # ── Step 3: LLM audit with exponential backoff ──
            result = await self._call_llm_with_backoff(app_data, evidence_content)
            if result is None:
                # All LLM retries exhausted
                return self._flag_manual_review(
                    app_data,
                    FailureCategory.RATE_LIMIT,
                    "LLM verification failed after maximum retries (likely rate limit).",
                    confidence_penalty=10.0,
                    start_time=start_time,
                )

            # ── Step 4: Per-field routing ──
            app_data = self._apply_verdict(app_data, result)

        except Exception as e:
            category = self._classify_exception(e)
            logger.error(f"[Verifier] Unexpected error for {app_data.app_name}: {e}")
            app_data.status = ResearchStatus.FAILED
            app_data.processing_notes.append(
                f"[{category.value}] Verification failed with exception: {e}"
            )

        app_data.verification_timestamp = datetime.now().isoformat()
        app_data.processing_time_seconds = (
            (app_data.processing_time_seconds or 0.0) + round(time.time() - start_time, 2)
        )
        return app_data

    # ------------------------------------------------------------------
    # Evidence fetching
    # ------------------------------------------------------------------

    async def _build_evidence_content(self, app_data: SaaSApplicationData) -> str:
        """Fetches each evidence URL with retry logic. Returns concatenated text."""
        evidence_content = ""
        for ev in app_data.evidence:
            url = str(ev.url)
            logger.info(f"[Verifier] Fetching evidence: {url}")
            text = await fetch_page_text_with_retry(url)
            if text:
                evidence_content += (
                    f"\n\n--- Source: {url} ---\n"
                    f"Claim to verify: {ev.claim_supported}\n"
                    f"Content:\n{text[:4000]}"
                )
            else:
                logger.warning(f"[Verifier] Could not fetch: {url}")
                app_data.processing_notes.append(
                    f"[{FailureCategory.EVIDENCE_FETCH.value}] Failed to fetch evidence URL: {url}"
                )
        return evidence_content

    @staticmethod
    def _build_fallback_from_claims(app_data: SaaSApplicationData) -> str:
        """Builds a minimal evidence block from the researcher's claim descriptions."""
        lines = []
        for ev in app_data.evidence:
            if ev.claim_supported:
                lines.append(f"Claim: {ev.claim_supported} (source: {ev.url})")
        return "\n".join(lines) if lines else ""

    # ------------------------------------------------------------------
    # LLM call with exponential backoff
    # ------------------------------------------------------------------

    async def _call_llm_with_backoff(
        self,
        app_data: SaaSApplicationData,
        evidence_content: str,
    ) -> Optional[VerificationResult]:
        """Calls the LLM up to MAX_LLM_RETRIES times. Returns None on exhaustion."""
        prompt = self._build_prompt(app_data, evidence_content)

        for attempt in range(self.MAX_LLM_RETRIES):
            try:
                result: VerificationResult = await self.client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    response_model=VerificationResult,
                    messages=[
                        {"role": "system", "content": "You are a highly skeptical auditor who evaluates claims field-by-field."},
                        {"role": "user",   "content": prompt},
                    ],
                    max_retries=1,  # instructor-level retries; we handle outer retries
                )
                return result

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait = self.BASE_BACKOFF_SECS ** (attempt + 1)
                    logger.warning(
                        f"[Verifier] {app_data.app_name}: 429 Rate Limit. "
                        f"Retrying in {wait}s (attempt {attempt + 1}/{self.MAX_LLM_RETRIES})."
                    )
                    app_data.processing_notes.append(
                        f"[{FailureCategory.RATE_LIMIT.value}] 429 on LLM call, attempt {attempt + 1}."
                    )
                    await asyncio.sleep(wait)
                else:
                    raise

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                wait = self.BASE_BACKOFF_SECS ** (attempt + 1)
                logger.warning(
                    f"[Verifier] {app_data.app_name}: network error '{e}'. "
                    f"Retrying in {wait}s (attempt {attempt + 1}/{self.MAX_LLM_RETRIES})."
                )
                app_data.processing_notes.append(
                    f"[{FailureCategory.EVIDENCE_FETCH.value}] Network error on LLM call, attempt {attempt + 1}."
                )
                await asyncio.sleep(wait)

            except Exception as e:
                category = self._classify_exception(e)
                wait = self.BASE_BACKOFF_SECS ** (attempt + 1)
                logger.warning(
                    f"[Verifier] {app_data.app_name}: [{category.value}] {e}. "
                    f"Retrying in {wait}s (attempt {attempt + 1}/{self.MAX_LLM_RETRIES})."
                )
                app_data.processing_notes.append(
                    f"[{category.value}] LLM call error on attempt {attempt + 1}: {str(e)[:120]}"
                )
                await asyncio.sleep(wait)

        return None  # All retries exhausted

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(app_data: SaaSApplicationData, evidence_content: str) -> str:
        return f"""
You are a strict Verification Engine for an AI Product Ops system.
Your job is to audit the integration metadata for {app_data.app_name} FIELD BY FIELD.

Original Claims by Researcher:
- auth_methods:          {[m.value for m in app_data.authentication_methods]}
- self_serve_status:     {app_data.self_serve_status.value}
- api_surface:           {[s.value for s in app_data.api_surface]}
- mcp_available:         {app_data.mcp_available}
- buildability_verdict:  {app_data.buildability_verdict.value}

Evidence Text (from documentation URLs):
{evidence_content}

Instructions:
1. Audit each field individually. If evidence explicitly supports the claim, mark supported=true.
2. If evidence is silent or unclear for a field, mark ambiguous=true (do NOT mark as invalid just for being silent).
3. If evidence CONTRADICTS a claim, mark supported=false, ambiguous=false.
4. Return a field_verdicts list covering all 5 fields above.
5. Set confidence_adjustment: positive if evidence strongly supports claims, negative for contradictions or gaps.
6. Be precise. Silence in documentation ≠ contradiction.
"""

    # ------------------------------------------------------------------
    # Verdict application — per-field routing
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_verdict(
        app_data: SaaSApplicationData,
        result: VerificationResult,
    ) -> SaaSApplicationData:
        """
        Routes the app based on per-field verdicts:
        - All supported → Verified
        - Some ambiguous but none contradicted → Manual Review
        - Any field contradicted → Manual Review (not Failed, since it may be a doc issue)
        """
        # Clamp confidence
        app_data.confidence_score = max(
            0.0, min(100.0, app_data.confidence_score + result.confidence_adjustment)
        )
        app_data.processing_notes.append(
            f"Verifier Reasoning: {result.overall_reasoning}"
        )

        verdicts = result.field_verdicts
        contradicted_fields = [v.field for v in verdicts if not v.supported and not v.ambiguous]
        ambiguous_fields    = [v.field for v in verdicts if v.ambiguous]
        supported_fields    = [v.field for v in verdicts if v.supported]

        n_total       = max(len(verdicts), 1)
        n_supported   = len(supported_fields)
        support_ratio = n_supported / n_total

        # Routing decision
        if contradicted_fields:
            # At least one field contradicted → Manual Review for human to arbitrate
            app_data.manual_review_required = True
            app_data.status = ResearchStatus.MANUAL_REVIEW
            app_data.processing_notes.append(
                f"[{FailureCategory.DOC_AMBIGUITY.value}] "
                f"Contradicted fields: {contradicted_fields}. Flagged for manual review."
            )
            logger.info(
                f"[Verifier] {app_data.app_name}: Manual Review — "
                f"contradictions in {contradicted_fields}."
            )

        elif ambiguous_fields and support_ratio < 0.6:
            # Majority of fields unverifiable → Manual Review
            app_data.manual_review_required = True
            app_data.status = ResearchStatus.MANUAL_REVIEW
            app_data.processing_notes.append(
                f"[{FailureCategory.DOC_AMBIGUITY.value}] "
                f"Too many ambiguous fields ({ambiguous_fields}). Manual review required."
            )
            logger.info(
                f"[Verifier] {app_data.app_name}: Manual Review — "
                f"insufficient evidence for {ambiguous_fields}."
            )

        elif app_data.confidence_score < 75.0:
            # Low confidence even if no hard contradictions
            app_data.manual_review_required = True
            app_data.status = ResearchStatus.MANUAL_REVIEW
            app_data.processing_notes.append(
                f"[{FailureCategory.DOC_AMBIGUITY.value}] "
                f"Low confidence score ({app_data.confidence_score:.1f}%). Manual review required."
            )
            logger.info(
                f"[Verifier] {app_data.app_name}: Manual Review — "
                f"low confidence {app_data.confidence_score:.1f}%."
            )

        else:
            # Sufficient support, no contradictions
            app_data.manual_review_required = False
            app_data.status = ResearchStatus.VERIFIED
            logger.info(
                f"[Verifier] {app_data.app_name}: Verified ✓ "
                f"({n_supported}/{n_total} fields supported, "
                f"confidence={app_data.confidence_score:.1f}%)"
            )

        return app_data

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _flag_manual_review(
        app_data: SaaSApplicationData,
        category: FailureCategory,
        reason: str,
        confidence_penalty: float,
        start_time: float,
    ) -> SaaSApplicationData:
        """Applies a confidence penalty and routes to Manual Review."""
        app_data.confidence_score = max(0.0, app_data.confidence_score - confidence_penalty)
        app_data.manual_review_required = True
        app_data.status = ResearchStatus.MANUAL_REVIEW
        app_data.processing_notes.append(f"[{category.value}] {reason}")
        app_data.verification_timestamp = datetime.now().isoformat()
        app_data.processing_time_seconds = (
            (app_data.processing_time_seconds or 0.0) + round(time.time() - start_time, 2)
        )
        logger.warning(f"[Verifier] {app_data.app_name} → Manual Review: {reason}")
        return app_data

    @staticmethod
    def _classify_exception(e: Exception) -> FailureCategory:
        """Maps an exception to the nearest structured failure category."""
        msg = str(e).lower()
        if "429" in msg or "rate limit" in msg or "quota" in msg:
            return FailureCategory.RATE_LIMIT
        if "timeout" in msg or "network" in msg or "connect" in msg:
            return FailureCategory.EVIDENCE_FETCH
        if "validation" in msg or "schema" in msg or "pydantic" in msg:
            return FailureCategory.VALIDATION_ERROR
        if "ambiguous" in msg or "explicit" in msg:
            return FailureCategory.DOC_AMBIGUITY
        return FailureCategory.UNEXPECTED_EXCEPTION
