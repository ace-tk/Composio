"""
Web scraping utilities.

Provides:
  - fetch_page_text(url)            — single attempt (used by ResearchAgent)
  - fetch_page_text_with_retry(url) — retries up to 3x with backoff (used by VerifierAgent)
"""

import asyncio
import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Groq free-tier: ~6,000 TPM.  With ≤4 URLs per app, budget ~3,000 chars each (~750 tokens).
_TEXT_LIMIT = 3000
_MAX_RETRIES = 3
_BASE_BACKOFF = 2  # seconds; doubles each retry


async def _do_fetch(url: str, timeout: int) -> str:
    """Single fetch attempt. Returns visible text or empty string on error."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.get(url, headers=_HEADERS)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            for element in soup(["script", "style", "nav", "footer", "noscript"]):
                element.decompose()

            text = soup.get_text(separator=" ", strip=True)
            return text[:_TEXT_LIMIT]

    except Exception as e:
        logger.warning(f"Fetch error for {url}: {e}")
        return ""


async def fetch_page_text(url: str, timeout: int = 15) -> str:
    """
    Single-attempt fetch.  Kept for backward compatibility with ResearchAgent.
    Handles timeouts and HTTP errors gracefully; returns '' on any failure.
    """
    return await _do_fetch(url, timeout)


async def fetch_page_text_with_retry(url: str, timeout: int = 15) -> str:
    """
    Fetch with up to _MAX_RETRIES attempts and exponential backoff.

    Retry triggers:
      - HTTP 429 (rate limit)
      - Timeout / network errors
      - Any other transient exception

    Returns '' only when all attempts fail.
    """
    for attempt in range(_MAX_RETRIES):
        text = await _do_fetch(url, timeout)
        if text:
            return text

        if attempt < _MAX_RETRIES - 1:
            wait = _BASE_BACKOFF ** (attempt + 1)
            logger.warning(
                f"Retrying fetch for {url} in {wait}s "
                f"(attempt {attempt + 1}/{_MAX_RETRIES})."
            )
            await asyncio.sleep(wait)

    logger.warning(f"All {_MAX_RETRIES} fetch attempts failed for {url}.")
    return ""
