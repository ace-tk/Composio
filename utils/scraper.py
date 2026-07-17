"""
utils/scraper.py — Robust web scraping with retries, structured errors, and diagnostics.

Public API (unchanged — drop-in replacement):
    fetch_page_text(url, timeout)            → str   (ResearchAgent)
    fetch_page_text_with_retry(url, timeout) → str   (VerifierAgent)

Improvements in this version:
    1. Exponential-backoff retries for 429 / 5xx / timeout / network errors.
    2. Realistic multi-header browser fingerprint to reduce bot-blocking.
    3. Automatic redirect following with final-URL logging.
    4. Configurable per-request timeout; never hangs indefinitely.
    5. Structured ScrapeFailureCategory enum — every error is classified.
    6. Empty / near-empty page detection before handing text downstream.
    7. Per-scrape structured log: URL, status, retries, category, latency.
    8. Permanent client errors (404, 410, 403) are NOT retried.
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Optional, Tuple

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Groq free-tier: ~6,000 TPM.  With ≤4 URLs per app, budget ~3,000 chars (~750 tokens).
_TEXT_LIMIT   = 3000
_MIN_TEXT_LEN = 80        # Fewer chars than this → treat page as empty/useless
_MAX_RETRIES  = 3

# Exponential-backoff delays per attempt index (0-based): 1s, 2s, 4s
_BACKOFF_DELAYS = [1, 2, 4]

# HTTP status codes that indicate a transient server-side failure worth retrying
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# HTTP status codes that are permanent client errors — never retry
_PERMANENT_ERROR_STATUS = {400, 401, 403, 404, 405, 410, 451}

# Realistic browser headers — reduces simple bot-detection rejections
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
}


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

class ScrapeFailureCategory(str, Enum):
    RATE_LIMIT         = "Rate Limit"
    TIMEOUT            = "Timeout"
    NETWORK_ERROR      = "Network Error"
    REDIRECT_FAILURE   = "Redirect Failure"
    EMPTY_RESPONSE     = "Empty Response"
    HTTP_ERROR         = "HTTP Error"
    UNEXPECTED         = "Unexpected Exception"


def _classify_http_error(status_code: int) -> ScrapeFailureCategory:
    if status_code == 429:
        return ScrapeFailureCategory.RATE_LIMIT
    if status_code in _PERMANENT_ERROR_STATUS:
        return ScrapeFailureCategory.HTTP_ERROR
    if 500 <= status_code < 600:
        return ScrapeFailureCategory.HTTP_ERROR
    return ScrapeFailureCategory.HTTP_ERROR


def _classify_exception(exc: Exception) -> ScrapeFailureCategory:
    msg = str(exc).lower()
    if isinstance(exc, httpx.TimeoutException):
        return ScrapeFailureCategory.TIMEOUT
    if isinstance(exc, httpx.TooManyRedirects):
        return ScrapeFailureCategory.REDIRECT_FAILURE
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError)):
        return ScrapeFailureCategory.NETWORK_ERROR
    if "timeout" in msg:
        return ScrapeFailureCategory.TIMEOUT
    if "network" in msg or "connect" in msg or "connection" in msg:
        return ScrapeFailureCategory.NETWORK_ERROR
    return ScrapeFailureCategory.UNEXPECTED


# ---------------------------------------------------------------------------
# HTML → clean text
# ---------------------------------------------------------------------------

def _extract_text(html: str) -> str:
    """Strips boilerplate tags and returns space-normalised visible text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript", "header", "aside"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def _is_empty_page(text: str) -> bool:
    """Returns True when the visible text is too short to be useful."""
    return len(text.strip()) < _MIN_TEXT_LEN


# ---------------------------------------------------------------------------
# Core single-attempt fetch (returns text + metadata)
# ---------------------------------------------------------------------------

async def _attempt_fetch(
    client: httpx.AsyncClient,
    url: str,
    attempt: int,
) -> Tuple[str, Optional[str], Optional[int], Optional[ScrapeFailureCategory], bool]:
    """
    Single fetch attempt.

    Returns:
        (text, final_url, http_status, failure_category, is_retryable)
        - text            : extracted visible text, or ''
        - final_url       : URL after all redirects
        - http_status     : integer HTTP status code, or None
        - failure_category: ScrapeFailureCategory or None on success
        - is_retryable    : whether the caller should retry
    """
    try:
        response = await client.get(url, headers=_HEADERS)
        final_url   = str(response.url)
        status_code = response.status_code

        # --- permanent errors: do not retry ---
        if status_code in _PERMANENT_ERROR_STATUS:
            cat = _classify_http_error(status_code)
            return "", final_url, status_code, cat, False

        # --- retryable server errors ---
        if status_code in _RETRYABLE_STATUS:
            cat = _classify_http_error(status_code)
            return "", final_url, status_code, cat, True

        response.raise_for_status()  # catch any other non-2xx

        text = _extract_text(response.text)
        if _is_empty_page(text):
            return "", final_url, status_code, ScrapeFailureCategory.EMPTY_RESPONSE, False

        return text[:_TEXT_LIMIT], final_url, status_code, None, False

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        final_url   = str(e.response.url)
        cat         = _classify_http_error(status_code)
        retryable   = status_code in _RETRYABLE_STATUS
        return "", final_url, status_code, cat, retryable

    except httpx.TooManyRedirects as e:
        return "", url, None, ScrapeFailureCategory.REDIRECT_FAILURE, False

    except httpx.TimeoutException as e:
        return "", url, None, ScrapeFailureCategory.TIMEOUT, True

    except (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError) as e:
        return "", url, None, ScrapeFailureCategory.NETWORK_ERROR, True

    except Exception as e:
        cat = _classify_exception(e)
        retryable = cat not in (
            ScrapeFailureCategory.REDIRECT_FAILURE,
            ScrapeFailureCategory.HTTP_ERROR,
        )
        return "", url, None, cat, retryable


# ---------------------------------------------------------------------------
# Internal retry loop
# ---------------------------------------------------------------------------

async def _fetch_with_retry(url: str, timeout: int, max_retries: int) -> str:
    """
    Runs up to `max_retries` fetch attempts with exponential backoff.
    Stops early on permanent errors or successful text extraction.
    Emits a structured log line for every attempt.
    """
    t_start   = time.monotonic()
    last_cat  = ScrapeFailureCategory.UNEXPECTED
    attempts  = 0

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout),
    ) as client:
        for attempt in range(max_retries):
            attempts += 1
            t_attempt = time.monotonic()

            text, final_url, status, category, retryable = await _attempt_fetch(
                client, url, attempt
            )

            elapsed_attempt = round(time.monotonic() - t_attempt, 2)

            if text:
                # ── SUCCESS ──
                elapsed_total = round(time.monotonic() - t_start, 2)
                logger.info(
                    f"[Scraper] ✓ SUCCESS | url={url} | final={final_url} "
                    f"| status={status} | attempts={attempts} "
                    f"| chars={len(text)} | time={elapsed_total}s"
                )
                return text

            # ── FAILURE on this attempt ──
            last_cat = category or ScrapeFailureCategory.UNEXPECTED
            logger.warning(
                f"[Scraper] ✗ attempt={attempt + 1}/{max_retries} "
                f"| category={last_cat.value} | url={url} "
                f"| final={final_url} | status={status} "
                f"| latency={elapsed_attempt}s"
                + ("" if retryable else " | permanent — no retry")
            )

            if not retryable:
                break

            if attempt < max_retries - 1:
                delay = _BACKOFF_DELAYS[min(attempt, len(_BACKOFF_DELAYS) - 1)]
                logger.info(
                    f"[Scraper] ↺ retrying in {delay}s "
                    f"(attempt {attempt + 2}/{max_retries}) | url={url}"
                )
                await asyncio.sleep(delay)

    # ── ALL ATTEMPTS EXHAUSTED ──
    elapsed_total = round(time.monotonic() - t_start, 2)
    logger.warning(
        f"[Scraper] ✗ FAILED | url={url} | category={last_cat.value} "
        f"| attempts={attempts} | time={elapsed_total}s"
    )
    return ""


# ---------------------------------------------------------------------------
# Public API  (signatures unchanged — drop-in replacement)
# ---------------------------------------------------------------------------

async def fetch_page_text(url: str, timeout: int = 15) -> str:
    """
    Single-attempt fetch for the ResearchAgent.

    Preserved for backward compatibility. Internally uses the same robust
    fetch machinery (redirect following, empty-page detection, structured
    error logging) but performs only ONE attempt — no retry delays.

    Returns:
        Visible page text (up to _TEXT_LIMIT chars), or '' on any failure.
    """
    return await _fetch_with_retry(url, timeout=timeout, max_retries=1)


async def fetch_page_text_with_retry(url: str, timeout: int = 15) -> str:
    """
    Retry-enabled fetch for the VerifierAgent.

    Retries up to 3 times with exponential backoff (1s → 2s → 4s).

    Retries on:
        - HTTP 429, 500–504
        - Timeouts
        - Network / connection errors

    Does NOT retry on:
        - HTTP 403, 404, 410, or other permanent client errors

    Returns:
        Visible page text (up to _TEXT_LIMIT chars), or '' after all retries.
    """
    return await _fetch_with_retry(url, timeout=timeout, max_retries=_MAX_RETRIES)
