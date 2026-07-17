"""
agents/verifier.py — Robust, high-fidelity verification stage (v3).

What's new over v2:
  1. Retry logic distinguishes retryable (429, timeout, network) from
     non-retryable (validation errors, malformed requests) LLM failures.
  2. Four distinct failure categories instead of a single DOC_AMBIGUITY bucket:
       - Evidence Fetch Failure  → URLs unreachable
       - LLM Failure             → infrastructure / rate-limit issues
       - Unsupported Claim       → evidence is silent but not contradictory
       - Contradictory Evidence  → evidence actively conflicts with a claim
  3. Independent per-field confidence adjustment:
       - Critical fields (auth, api_surface) carry higher weight.
       - Only truly contradicted or missing critical fields lower the score.
       - Ambiguous non-critical fields apply a small, proportionate penalty.
  4. Nuanced Manual Review decision:
       - Contradicted critical field         → always Manual Review
       - ≥2 critical fields unsupported      → Manual Review
       - All critical fields fine, minor gaps → VERIFIED (with note)
       - Low post-adjustment confidence      → Manual Review
  5. Structured per-field reasoning appended to processing_notes.
  6. Public interface (verify method signature and SaaSApplicationData schema)
     is unchanged — drop-in replacement.
"""

import asyncio
import logging
import time
from datetime import datetime
from enum import Enum
import os
from typing import Dict, List, Optional, Tuple

import httpx
import instructor
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from utils.models import SaaSApplicationData, ResearchStatus
from utils.scraper import fetch_page_text_with_retry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Failure categories
# ---------------------------------------------------------------------------

class FailureCategory(str, Enum):
    RATE_LIMIT           = "Rate Limit"
    LLM_FAILURE          = "LLM Failure"
    EVIDENCE_FETCH       = "Evidence Fetch Failure"
    UNSUPPORTED_CLAIM    = "Unsupported Claim"
    CONTRADICTORY        = "Contradictory Evidence"
    VALIDATION_ERROR     = "Validation Error"
    UNEXPECTED_EXCEPTION = "Unexpected Exception"


# ---------------------------------------------------------------------------
# Field metadata — drives weighted confidence adjustments
# ---------------------------------------------------------------------------

# (weight, is_critical)
# Critical fields failing triggers a stricter Manual Review threshold.
_FIELD_META: Dict[str, Tuple[float, bool]] = {
    "auth_methods":         (2.5, True),   # most important for integration
    "api_surface":          (2.0, True),   # REST vs GraphQL matters
    "self_serve_status":    (1.5, True),   # blocks integration path
    "buildability_verdict": (1.0, False),  # useful but less binary
    "mcp_available":        (0.5, False),  # nice-to-have
}
_TOTAL_WEIGHT = sum(w for w, _ in _FIELD_META.values())


# ---------------------------------------------------------------------------
# LLM response schema
# ---------------------------------------------------------------------------

class FieldVerdict(BaseModel):
    field: str = Field(
        ...,
        description=(
            "Exact field name — one of: auth_methods, api_surface, "
            "self_serve_status, buildability_verdict, mcp_available."
        ),
    )
    supported: bool = Field(
        ...,
        description="True only if evidence explicitly confirms the claimed value.",
    )
    ambiguous: bool = Field(
        ...,
        description=(
            "True when evidence is silent or unclear. "
            "Do NOT set this to True when evidence actively contradicts the claim."
        ),
    )
    contradicted: bool = Field(
        default=False,
        description="True when evidence directly conflicts with the claimed value.",
    )
    reasoning: str = Field(
        ...,
        description=(
            "One or two sentences: state what the evidence says (or doesn't say) "
            "and why you reached this verdict."
        ),
    )


class VerificationResult(BaseModel):
    """Structured per-field audit result from the LLM."""

    field_verdicts: List[FieldVerdict] = Field(
        ...,
        description=(
            "Verdicts for every critical field. Must cover all 5: "
            "auth_methods, api_surface, self_serve_status, "
            "buildability_verdict, mcp_available."
        ),
    )
    overall_reasoning: str = Field(
        ...,
        description=(
            "2–3 sentence summary: which claims were verified, which remain "
            "uncertain, and whether manual review is warranted."
        ),
    )


# ---------------------------------------------------------------------------
# VerifierAgent
# ---------------------------------------------------------------------------

class VerifierAgent:
    """
    Audits ResearcherAgent claims against live documentation evidence.

    Design principles:
    - One field failing never fails the whole record.
    - Infrastructure failures (rate limits, timeouts) are retried; logic
      errors (schema mismatches, validation) are not.
    - Confidence is adjusted per-field using weighted scoring.
    - Manual Review is reserved for genuinely ambiguous or contradicted
      critical integration fields.
    """

    # LLM retry config
    MAX_LLM_RETRIES  = 3
    # Backoff delays per attempt index 0,1,2 → 2s, 4s, 8s
    _LLM_BACKOFFS    = [2, 4, 8]

    # Routing thresholds
    _VERIFIED_CONFIDENCE_MIN   = 72.0   # below this → Manual Review
    _MAX_CRITICAL_UNSUPPORTED  = 1      # >1 unsupported critical → Manual Review

    def __init__(self) -> None:
        api_key = os.getenv("GROQ_API_KEY", "")
        self.client = instructor.from_openai(
            AsyncOpenAI(
                api_key=api_key,
                base_url="https://api.groq.com/openai/v1",
            ),
            mode=instructor.Mode.JSON,
        )

    # ------------------------------------------------------------------
    # Public entry point (signature unchanged)
    # ------------------------------------------------------------------

    async def verify(self, app_data: SaaSApplicationData) -> SaaSApplicationData:
        """
        Runs the full verification workflow.
        Never raises — all errors are caught and routed to appropriate statuses.
        """
        logger.info(f"[Verifier] ── Starting → {app_data.app_name}")
        start_time = time.time()

        try:
            # ── Step 1: Evidence guard ──────────────────────────────────
            if not app_data.evidence:
                return self._flag_manual_review(
                    app_data,
                    FailureCategory.EVIDENCE_FETCH,
                    "No evidence URLs provided by the Researcher.",
                    confidence_penalty=20.0,
                    start_time=start_time,
                )

            # ── Step 2: Fetch evidence (retry-enabled) + fallback ───────
            evidence_content = await self._build_evidence_content(app_data)

            if not evidence_content:
                fallback = self._build_fallback_from_claims(app_data)
                if fallback:
                    logger.warning(
                        f"[Verifier] {app_data.app_name}: all URLs unreachable — "
                        "using researcher claim text as fallback evidence."
                    )
                    evidence_content = fallback
                    app_data.processing_notes.append(
                        f"[{FailureCategory.EVIDENCE_FETCH.value}] "
                        "All evidence URLs failed; fell back to claim descriptions."
                    )
                else:
                    return self._flag_manual_review(
                        app_data,
                        FailureCategory.EVIDENCE_FETCH,
                        "Could not fetch any evidence URL and no claim text fallback available.",
                        confidence_penalty=20.0,
                        start_time=start_time,
                    )

            # ── Step 3: LLM audit with smart retry ──────────────────────
            result, llm_failure_note = await self._call_llm_with_backoff(
                app_data, evidence_content
            )

            if result is None:
                # Attach the failure note before flagging
                if llm_failure_note:
                    app_data.processing_notes.append(llm_failure_note)
                return self._flag_manual_review(
                    app_data,
                    FailureCategory.LLM_FAILURE,
                    "LLM verification exhausted all retries. Flagged for human review.",
                    confidence_penalty=10.0,
                    start_time=start_time,
                )

            # ── Step 4: Apply verdicts ───────────────────────────────────
            app_data = self._apply_verdict(app_data, result)

        except Exception as exc:
            category = self._classify_exception(exc)
            logger.error(
                f"[Verifier] Unexpected error for {app_data.app_name}: {exc}",
                exc_info=True,
            )
            app_data.status = ResearchStatus.FAILED
            app_data.processing_notes.append(
                f"[{category.value}] Verification aborted with exception: {exc}"
            )

        app_data.verification_timestamp = datetime.now().isoformat()
        app_data.processing_time_seconds = (
            (app_data.processing_time_seconds or 0.0)
            + round(time.time() - start_time, 2)
        )
        return app_data

    # ------------------------------------------------------------------
    # Evidence fetching
    # ------------------------------------------------------------------

    async def _build_evidence_content(self, app_data: SaaSApplicationData) -> str:
        """Fetches each evidence URL. Returns concatenated text."""
        chunks: List[str] = []
        for ev in app_data.evidence:
            url = str(ev.url)
            logger.info(f"[Verifier] Fetching evidence: {url}")
            text = await fetch_page_text_with_retry(url)
            if text:
                chunks.append(
                    f"\n\n--- Source: {url} ---\n"
                    f"Claim to verify: {ev.claim_supported}\n"
                    f"Content:\n{text[:4000]}"
                )
            else:
                logger.warning(f"[Verifier] Fetch failed: {url}")
                app_data.processing_notes.append(
                    f"[{FailureCategory.EVIDENCE_FETCH.value}] "
                    f"Failed to retrieve: {url}"
                )
        return "".join(chunks)

    @staticmethod
    def _build_fallback_from_claims(app_data: SaaSApplicationData) -> str:
        """Assembles minimal evidence text from the researcher's claim descriptions."""
        lines = [
            f"Claim: {ev.claim_supported} (source: {ev.url})"
            for ev in app_data.evidence
            if ev.claim_supported
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # LLM call with smart retry (distinguishes retryable vs permanent)
    # ------------------------------------------------------------------

    async def _call_llm_with_backoff(
        self,
        app_data: SaaSApplicationData,
        evidence_content: str,
    ) -> Tuple[Optional[VerificationResult], Optional[str]]:
        """
        Calls the LLM up to MAX_LLM_RETRIES times.

        Returns:
            (VerificationResult, None)          on success
            (None, failure_note_string)         when all retries exhausted
        """
        prompt = self._build_prompt(app_data, evidence_content)
        last_failure_note: Optional[str] = None

        for attempt in range(self.MAX_LLM_RETRIES):
            try:
                result: VerificationResult = await self.client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    response_model=VerificationResult,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a precise integration-metadata auditor. "
                                "Evaluate every field independently. "
                                "Silence in documentation is NOT contradiction."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_retries=1,  # instructor schema retries; outer loop handles LLM retries
                )
                logger.info(
                    f"[Verifier] {app_data.app_name}: LLM responded on attempt "
                    f"{attempt + 1}/{self.MAX_LLM_RETRIES}."
                )
                return result, None

            # ── Retryable: rate limit ───────────────────────────────────
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    note = (
                        f"[{FailureCategory.RATE_LIMIT.value}] "
                        f"429 on LLM call attempt {attempt + 1}."
                    )
                    app_data.processing_notes.append(note)
                    last_failure_note = note
                    wait = self._LLM_BACKOFFS[min(attempt, len(self._LLM_BACKOFFS) - 1)]
                    logger.warning(
                        f"[Verifier] {app_data.app_name}: 429 — "
                        f"retrying in {wait}s (attempt {attempt + 1}/{self.MAX_LLM_RETRIES})."
                    )
                    await asyncio.sleep(wait)
                else:
                    # Non-429 HTTP error — permanent, do not retry
                    note = (
                        f"[{FailureCategory.LLM_FAILURE.value}] "
                        f"Non-retryable HTTP {exc.response.status_code} from LLM API."
                    )
                    logger.error(f"[Verifier] {app_data.app_name}: {note}")
                    return None, note

            # ── Retryable: network / timeout ────────────────────────────
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as exc:
                wait = self._LLM_BACKOFFS[min(attempt, len(self._LLM_BACKOFFS) - 1)]
                note = (
                    f"[{FailureCategory.LLM_FAILURE.value}] "
                    f"Network error on LLM attempt {attempt + 1}: {type(exc).__name__}."
                )
                app_data.processing_notes.append(note)
                last_failure_note = note
                logger.warning(
                    f"[Verifier] {app_data.app_name}: {type(exc).__name__} — "
                    f"retrying in {wait}s."
                )
                await asyncio.sleep(wait)

            # ── Non-retryable: validation / schema errors ────────────────
            except Exception as exc:
                msg = str(exc).lower()
                is_validation = any(
                    kw in msg for kw in ("validation", "schema", "pydantic", "json", "parse")
                )
                if is_validation:
                    note = (
                        f"[{FailureCategory.VALIDATION_ERROR.value}] "
                        f"LLM output failed schema validation: {str(exc)[:120]}"
                    )
                    logger.error(f"[Verifier] {app_data.app_name}: {note}")
                    return None, note

                # Anything else — retry with generic note
                wait = self._LLM_BACKOFFS[min(attempt, len(self._LLM_BACKOFFS) - 1)]
                note = (
                    f"[{FailureCategory.LLM_FAILURE.value}] "
                    f"Transient LLM error attempt {attempt + 1}: {str(exc)[:120]}"
                )
                app_data.processing_notes.append(note)
                last_failure_note = note
                logger.warning(
                    f"[Verifier] {app_data.app_name}: transient error — "
                    f"retrying in {wait}s."
                )
                await asyncio.sleep(wait)

        return None, last_failure_note

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(app_data: SaaSApplicationData, evidence_content: str) -> str:
        return f"""
You are auditing integration metadata for: {app_data.app_name}

=== RESEARCHER CLAIMS ===
- auth_methods:         {[m.value for m in app_data.authentication_methods]}
- api_surface:          {[s.value for s in app_data.api_surface]}
- self_serve_status:    {app_data.self_serve_status.value}
- buildability_verdict: {app_data.buildability_verdict.value}
- mcp_available:        {app_data.mcp_available}

=== EVIDENCE FROM DOCUMENTATION ===
{evidence_content}

=== AUDIT INSTRUCTIONS ===
For EACH of the 5 fields above, produce one FieldVerdict:

1. supported=true   → Evidence explicitly confirms the claim.
2. ambiguous=true   → Evidence is silent or unclear (documentation gap, not contradiction).
3. contradicted=true → Evidence directly conflicts with the claim (both supported and ambiguous must be false).

Rules:
- Evaluate each field independently. One field's problem must not affect another.
- "Does not mention X" means ambiguous, NOT contradicted.
- Only mark contradicted=true when the text says something incompatible with the claim.
- In overall_reasoning, list which fields were verified, which were uncertain, and which were contradicted.
- Be specific — reference actual evidence text where possible.
"""

    # ------------------------------------------------------------------
    # Verdict application — weighted confidence + nuanced routing
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_verdict(
        app_data: SaaSApplicationData,
        result: VerificationResult,
    ) -> SaaSApplicationData:
        """
        Applies per-field weighted confidence adjustment and routes to the
        correct status using nuanced thresholds.
        """
        verdicts = result.field_verdicts

        # ── 1. Build indexed lookup ──────────────────────────────────────
        verdict_map: Dict[str, FieldVerdict] = {v.field: v for v in verdicts}

        # ── 2. Weighted confidence adjustment ───────────────────────────
        # Start from the researcher's score. Each field contributes or
        # deducts based on its weight and verdict.
        raw_adjustment = 0.0
        field_summary_lines: List[str] = []

        for field, (weight, is_critical) in _FIELD_META.items():
            v = verdict_map.get(field)
            if v is None:
                # Field missing from LLM response — treat as ambiguous
                penalty = -(weight * 2.0)
                raw_adjustment += penalty
                field_summary_lines.append(
                    f"  • {field}: MISSING verdict (penalty {penalty:+.1f})"
                )
                continue

            if v.supported:
                bonus = weight * 3.0
                raw_adjustment += bonus
                field_summary_lines.append(
                    f"  • {field}: ✓ SUPPORTED (+{bonus:.1f}) — {v.reasoning}"
                )
            elif v.contradicted:
                penalty = -(weight * 5.0)   # contradiction is a strong signal
                raw_adjustment += penalty
                field_summary_lines.append(
                    f"  • {field}: ✗ CONTRADICTED ({penalty:+.1f}) — {v.reasoning}"
                )
            elif v.ambiguous:
                # Scale penalty: critical fields penalised more
                penalty = -(weight * 1.5) if is_critical else -(weight * 0.5)
                raw_adjustment += penalty
                field_summary_lines.append(
                    f"  • {field}: ? AMBIGUOUS ({penalty:+.1f}) — {v.reasoning}"
                )
            else:
                # unsupported but not contradicted — treat as ambiguous
                penalty = -(weight * 2.0)
                raw_adjustment += penalty
                field_summary_lines.append(
                    f"  • {field}: – UNSUPPORTED ({penalty:+.1f}) — {v.reasoning}"
                )

        # Normalise raw adjustment to a ±20 range (so a perfect run adds ~20)
        normalised = raw_adjustment / _TOTAL_WEIGHT * 2.6
        new_score = max(0.0, min(100.0, app_data.confidence_score + normalised))
        app_data.confidence_score = round(new_score, 2)

        # ── 3. Structured reasoning into processing_notes ───────────────
        app_data.processing_notes.append(
            f"Verifier Reasoning: {result.overall_reasoning}"
        )
        app_data.processing_notes.append(
            "Verifier Field Breakdown:\n" + "\n".join(field_summary_lines)
        )

        # ── 4. Classify contradictions and critical unsupported fields ───
        contradicted_critical = [
            field for field, (_, is_critical) in _FIELD_META.items()
            if is_critical
            and verdict_map.get(field) is not None
            and verdict_map[field].contradicted
        ]
        unsupported_critical = [
            field for field, (_, is_critical) in _FIELD_META.items()
            if is_critical
            and (
                verdict_map.get(field) is None
                or (
                    not verdict_map[field].supported
                    and not verdict_map[field].ambiguous
                )
            )
        ]
        ambiguous_critical = [
            field for field, (_, is_critical) in _FIELD_META.items()
            if is_critical
            and verdict_map.get(field) is not None
            and verdict_map[field].ambiguous
        ]

        # ── 5. Routing decision ──────────────────────────────────────────
        if contradicted_critical:
            # Active contradiction in a critical field
            app_data.manual_review_required = True
            app_data.status = ResearchStatus.MANUAL_REVIEW
            app_data.processing_notes.append(
                f"[{FailureCategory.CONTRADICTORY.value}] "
                f"Critical fields with contradicting evidence: {contradicted_critical}. "
                "Human review required to resolve discrepancy."
            )
            logger.info(
                f"[Verifier] {app_data.app_name} → Manual Review "
                f"(contradictions: {contradicted_critical}, "
                f"score={app_data.confidence_score:.1f}%)"
            )

        elif len(unsupported_critical) > VerifierAgent._MAX_CRITICAL_UNSUPPORTED:
            # More than one critical field has no evidence at all
            app_data.manual_review_required = True
            app_data.status = ResearchStatus.MANUAL_REVIEW
            app_data.processing_notes.append(
                f"[{FailureCategory.UNSUPPORTED_CLAIM.value}] "
                f"Multiple critical fields lack evidence: {unsupported_critical}. "
                "Flagged for human review."
            )
            logger.info(
                f"[Verifier] {app_data.app_name} → Manual Review "
                f"(unsupported critical: {unsupported_critical}, "
                f"score={app_data.confidence_score:.1f}%)"
            )

        elif app_data.confidence_score < VerifierAgent._VERIFIED_CONFIDENCE_MIN:
            # Low confidence after weighted adjustment
            app_data.manual_review_required = True
            app_data.status = ResearchStatus.MANUAL_REVIEW
            app_data.processing_notes.append(
                f"[{FailureCategory.UNSUPPORTED_CLAIM.value}] "
                f"Post-verification confidence too low ({app_data.confidence_score:.1f}%). "
                "Flagged for human review."
            )
            logger.info(
                f"[Verifier] {app_data.app_name} → Manual Review "
                f"(low confidence: {app_data.confidence_score:.1f}%)"
            )

        else:
            # All critical fields either supported or only mildly ambiguous
            app_data.manual_review_required = False
            app_data.status = ResearchStatus.VERIFIED

            supported_fields = [
                f for f, v in verdict_map.items() if v.supported
            ]
            if ambiguous_critical:
                app_data.processing_notes.append(
                    f"Verified with minor evidence gaps in: {ambiguous_critical}. "
                    "Non-critical ambiguity does not block verification."
                )

            logger.info(
                f"[Verifier] {app_data.app_name} → Verified ✓ "
                f"(supported={supported_fields}, "
                f"score={app_data.confidence_score:.1f}%)"
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
        """Applies a confidence penalty and routes to Manual Review without LLM."""
        app_data.confidence_score = max(
            0.0, app_data.confidence_score - confidence_penalty
        )
        app_data.manual_review_required = True
        app_data.status = ResearchStatus.MANUAL_REVIEW
        app_data.processing_notes.append(f"[{category.value}] {reason}")
        app_data.verification_timestamp = datetime.now().isoformat()
        app_data.processing_time_seconds = (
            (app_data.processing_time_seconds or 0.0)
            + round(time.time() - start_time, 2)
        )
        logger.warning(
            f"[Verifier] {app_data.app_name} → Manual Review [{category.value}]: {reason}"
        )
        return app_data

    @staticmethod
    def _classify_exception(exc: Exception) -> FailureCategory:
        """Maps an exception to the nearest failure category."""
        msg = str(exc).lower()
        if "429" in msg or "rate limit" in msg or "quota" in msg:
            return FailureCategory.RATE_LIMIT
        if "timeout" in msg or "network" in msg or "connect" in msg:
            return FailureCategory.LLM_FAILURE
        if "validation" in msg or "schema" in msg or "pydantic" in msg or "parse" in msg:
            return FailureCategory.VALIDATION_ERROR
        return FailureCategory.UNEXPECTED_EXCEPTION
