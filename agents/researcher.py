"""
================================================================================
COMPOSIO AI RESEARCH AGENT - ARCHITECTURE & SPECIFICATION
================================================================================

1. MISSION
--------------------------------------------------------------------------------
The mission of the Research Agent is to autonomously discover, read, and extract 
highly accurate integration and API metadata for any given SaaS application. It 
must act as an expert Product Ops engineer, prioritizing undeniable evidence over 
assumptions, to determine exactly how difficult it will be for Composio to 
integrate with the target platform.

2. RESPONSIBILITIES
--------------------------------------------------------------------------------
- Locate the official developer documentation and API reference for a given SaaS.
- Navigate through documentation pages to extract auth methods, API surfaces, 
  and access models (Self-serve vs. Gated).
- Determine if a Model Context Protocol (MCP) server already exists.
- Synthesize findings into a strict JSON schema.
- Attach specific URL evidence to every major claim made.
- Identify primary blockers for integration and assign a buildability verdict.

3. ALLOWED TOOLS
--------------------------------------------------------------------------------
- **Search Engine API**: To find official documentation URLs via queries like 
  "{App Name} API documentation" or "{App Name} developer portal".
- **Web Scraper (HTTPX/Playwright)**: To fetch the raw HTML/text from URLs.
- **LLM Extraction Engine**: To parse the scraped text and map it to the schema.
- **Pydantic Validation Tool**: To ensure all outputs perfectly match the data contract.

4. RESEARCH WORKFLOW
--------------------------------------------------------------------------------
1. **Intake**: Receive `app_name` and `website`.
2. **Discovery**: Use the search tool to find the primary Developer Docs URL and 
   Pricing Page URL.
3. **Scraping**: Fetch the contents of the identified URLs.
4. **Extraction**: Pass the scraped context to the LLM with strict instructions 
   to populate the schema.
5. **Evidence Linking**: For each extracted field (Auth, API Surface, etc.), 
   bind it to the specific URL that proved it.
6. **Validation**: Run the output through the Pydantic validator.
7. **Scoring**: Calculate internal confidence.
8. **Output**: Return the structured `SaaSApplicationData` object.

5. DECISION MAKING PROCESS
--------------------------------------------------------------------------------
- If multiple authentication methods exist, list them all, but highlight the most 
  modern/standard one (e.g., OAuth 2.0).
- If documentation contradicts the pricing page (e.g., Docs say "Self-serve API", 
  Pricing says "Enterprise only"), assume the most restrictive case (Gated) and 
  add a processing note.
- If information cannot be found after searching the top 3 relevant URLs, do NOT 
  guess. Mark the field as UNKNOWN and lower the confidence score.

6. TRUSTWORTHY SOURCES
--------------------------------------------------------------------------------
- Official Developer Portals (e.g., developers.stripe.com)
- Official API References (e.g., api.twilio.com)
- Official Help Centers on the main domain (e.g., help.notion.so)
- Official GitHub repositories owned by the organization
- Official Pricing pages (for determining Gated vs. Self-serve)

7. UNTRUSTWORTHY SOURCES (MUST NEVER BE TRUSTED)
--------------------------------------------------------------------------------
- Third-party blogs (e.g., Medium, Dev.to)
- Forum comments (e.g., Reddit, StackOverflow) - can be used for hints, but 
  cannot be cited as evidence.
- AI hallucinations (general knowledge). The agent must only extract data present 
  in the scraped context.
- Unofficial API wrappers on GitHub built by individual users.

8. EVIDENCE COLLECTION
--------------------------------------------------------------------------------
Every claim must be backed by an `Evidence` object. 
- Example: If the agent claims "REST API", it must attach the specific URL 
  where it read that, select `SourceType.DEVELOPER_DOCS`, and write a `claim_supported` 
  summary: "The introduction page states RESTful endpoints are available."

9. CONFIDENCE CALCULATION
--------------------------------------------------------------------------------
Confidence starts at 100.0 and degrades based on specific penalties:
- -20.0 if a major field (Auth, API Surface) is marked UNKNOWN.
- -15.0 if the Developer Docs domain does not closely match the main `website` domain.
- -10.0 for every Evidence object that comes from a secondary source (e.g., 
  Help Center instead of Developer Docs).
- -30.0 if web scraping was blocked (403/Captcha) and the LLM had to rely purely 
  on search snippets.

10. HUMAN REVIEW TRIGGERS
--------------------------------------------------------------------------------
The agent will automatically flag `manual_review_required = True` if:
- The `confidence_score` drops below 75.0.
- `self_serve_status` is determined to be HYBRID or UNKNOWN.
- `buildability_verdict` is determined to be IMPOSSIBLE.
- The Pydantic validator fails 3 consecutive times during the Extraction phase.

11. FAILURE CONDITIONS
--------------------------------------------------------------------------------
The agent will mark `status = ResearchStatus.FAILED` if:
- The search engine returns 0 relevant results for the app name.
- All target URLs return 404 or persistent 403 Forbidden errors, AND search 
  snippets contain insufficient data.
- LLM context limit is repeatedly exceeded even after text chunking/summarization.

12. RETRY RULES
--------------------------------------------------------------------------------
- **Network Errors**: Retry fetching a URL up to 3 times with exponential backoff.
- **Validation Errors**: If the LLM returns invalid JSON or violates the Pydantic 
  schema, feed the error back to the LLM. Retry extraction up to 3 times.
- **Missing Docs**: If the first search yields no docs, retry search using 
  alternative queries (e.g., "{App Name} integrations"). Max 2 search attempts.

13. EXACT JSON SCHEMA IT MUST ALWAYS RETURN
--------------------------------------------------------------------------------
The agent MUST return a valid JSON object adhering precisely to the 
`SaaSApplicationData` Pydantic model defined in `utils/models.py`. 
No arbitrary fields can be added. 

Schema summary:
{
  "app_name": "str",
  "website": "url",
  "category": "str",
  "one_line_description": "str",
  "agent_summary": "str",
  "authentication_methods": ["OAuth 2.0", "API Key", ...],
  "self_serve_status": "Self-Serve" | "Gated" | "Hybrid" | "Unknown",
  "api_surface": ["REST", "GraphQL", ...],
  "api_documentation_url": "url",
  "mcp_available": bool,
  "buildability_verdict": "Easy" | "Moderate" | "Hard" | "Impossible" | "Unknown",
  "primary_blocker": "str" | null,
  "evidence": [
    {
      "url": "url",
      "source_type": "Official Docs" | "Pricing Page" | ...,
      "claim_supported": "str"
    }
  ],
  "confidence_score": float,
  "status": "Researched" | "Failed",
  "manual_review_required": bool,
  "research_timestamp": "datetime",
  "verification_timestamp": null,
  "processing_time_seconds": float,
  "processing_notes": ["str"],
  "notes": "str" | null
}
================================================================================
"""

from typing import Any, List
import logging
import time
from datetime import datetime
import os
import instructor
from openai import AsyncOpenAI

from utils.models import SaaSApplicationData, ResearchStatus
from utils.scraper import fetch_page_text

logger = logging.getLogger(__name__)

class ResearchAgent:
    """
    The ResearchAgent orchestrates the discovery, scraping, and LLM extraction 
    phases based on the architecture rules defined above.
    """
    
    def __init__(self):
        # Uses Groq's OpenAI-compatible endpoint. Reads key from GROQ_API_KEY.
        api_key = os.getenv("GROQ_API_KEY", "")
        self.client = instructor.from_openai(
            AsyncOpenAI(
                api_key=api_key,
                base_url="https://api.groq.com/openai/v1"
            ),
            mode=instructor.Mode.JSON
        )

    async def search_docs_url(self, app_name: str, website: str) -> List[str]:
        """
        In a full implementation, this uses a Search API (e.g., Google Custom Search).
        For this pipeline milestone, we heuristically guess the most common developer URLs.
        """
        base_url = website.rstrip('/')
        return [
            f"{base_url}/docs",
            f"{base_url}/developers",
            f"{base_url}/pricing",
            f"{base_url}/api"
        ]

    async def run(self, app_name: str, website: str) -> SaaSApplicationData:
        """
        Main entry point for researching a single SaaS application.
        """
        logger.info(f"Processing {app_name}...")
        start_time = time.time()
        
        try:
            logger.info("Finding Developer Docs...")
            target_urls = await self.search_docs_url(app_name, website)
            
            scraped_content = ""
            for url in target_urls:
                logger.info(f"Scraping {url}...")
                text = await fetch_page_text(url)
                if text:
                    scraped_content += f"\n\n--- Content from {url} ---\n{text}"
            
            if not scraped_content:
                logger.warning(f"Could not fetch any concrete content for {app_name}. Fallback to general knowledge.")

            logger.info("Extracting structured information...")
            
            prompt = f"""
            You are an expert AI Product Ops researcher.
            Extract the integration metadata for the SaaS application: {app_name} ({website}).
            
            Here is the scraped content from their documentation/website:
            {scraped_content}
            
            Instructions:
            - If the scraped content is insufficient, use your general knowledge to infer.
            - If you use general knowledge, your confidence_score MUST be lower (e.g., < 60.0).
            - If you find concrete evidence in the text, provide the URLs in the evidence field and assign a high confidence_score (e.g., > 85.0).
            - Strictly adhere to the requested schema.
            """
            
            # instructor wrapper guarantees the output matches SaaSApplicationData
            response: SaaSApplicationData = await self.client.chat.completions.create(
                model="llama-3.1-8b-instant",  # Groq model: fast and free-tier friendly
                response_model=SaaSApplicationData,
                messages=[
                    {"role": "system", "content": "You are a strict data extraction assistant."},
                    {"role": "user", "content": prompt}
                ],
                max_retries=3 # Automated retry for LLM validation failures
            )
            
            # Populate processing metadata
            response.research_timestamp = datetime.now().isoformat()
            response.processing_time_seconds = round(time.time() - start_time, 2)
            response.status = ResearchStatus.RESEARCHED
            
            # Additional confidence logic
            if not scraped_content:
                response.confidence_score = min(response.confidence_score, 40.0)
                response.processing_notes.append("No scraped content available. Relied on LLM general knowledge.")
                
            if response.confidence_score < 75.0:
                response.manual_review_required = True
                
            return response

        except Exception as e:
            logger.error(f"Error researching {app_name}: {e}")
            # Ensure the pipeline doesn't crash on a single app failure
            return SaaSApplicationData(
                app_name=app_name,
                website=website,
                category="Unknown",
                one_line_description="Failed to process due to error.",
                agent_summary=f"Error encountered: {str(e)}",
                authentication_methods=[],
                self_serve_status="Unknown",
                api_surface=[],
                mcp_available=False,
                buildability_verdict="Unknown",
                status=ResearchStatus.FAILED,
                processing_time_seconds=round(time.time() - start_time, 2),
                processing_notes=[f"Exception: {e}"],
                evidence=[]
            )
