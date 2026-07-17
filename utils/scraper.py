import httpx
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)

async def fetch_page_text(url: str, timeout: int = 15) -> str:
    """
    Fetches a URL and returns the visible text.
    Handles timeouts and HTTP errors gracefully.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            # Mask as a standard browser to avoid basic blocks
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Remove irrelevant or noisy tags
            for element in soup(["script", "style", "nav", "footer", "noscript"]):
                element.decompose()
                
            # Extract text
            text = soup.get_text(separator=" ", strip=True)
            
            # Truncate aggressively per URL — Groq free tier has a 6,000 TPM cap.
            # With 4 URLs scraped per app, budget ~3,000 chars each (~750 tokens).
            return text[:3000]
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return ""
