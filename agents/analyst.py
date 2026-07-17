import logging
import os
import instructor
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from typing import List, Dict, Any

from utils.models import SaaSApplicationData

logger = logging.getLogger(__name__)

class Insight(BaseModel):
    finding: str = Field(..., description="The concrete, data-driven finding from the dataset.")
    recommendation: str = Field(..., description="Actionable product recommendation explaining exactly WHY this finding matters to Composio.")

class AnalysisReport(BaseModel):
    """Structured report schema for Product Ops insights."""
    summary: str = Field(..., description="High-level executive summary of the dataset and core takeaways.")
    key_metrics: Dict[str, Any] = Field(..., description="Hard statistics calculated from the verified dataset.")
    patterns: List[str] = Field(..., description="Key macro trends identified across the dataset.")
    recommendations: List[Insight] = Field(..., description="Strategic recommendations mapping findings to business value.")
    risk_analysis: str = Field(..., description="Analysis of major blockers, gated APIs, and integration risks.")
    opportunity_analysis: str = Field(..., description="Identification of quick wins, easy categories, and highly standardized APIs.")

class AnalystAgent:
    """
    The AnalystAgent transforms raw verified research into strategic business intelligence.
    It pre-calculates statistics to ground the LLM, then asks the LLM to provide the "So what?".
    """
    
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY", "")
        self.client = instructor.from_openai(
            AsyncOpenAI(
                api_key=api_key,
                base_url="https://api.groq.com/openai/v1"
            )
        )

    async def analyze(self, verified_apps: List[SaaSApplicationData]) -> AnalysisReport:
        """
        Calculates raw statistics from the dataset and tasks an LLM with generating 
        actionable strategic insights for the Product Ops team.
        """
        logger.info(f"Analyzing {len(verified_apps)} verified applications for insights...")
        
        if not verified_apps:
            raise ValueError("No verified applications provided to analyze.")
            
        # 1. Pre-calculate hard statistics to prevent the LLM from hallucinating math
        stats = {
            "total_verified_apps": len(verified_apps),
            "auth_distribution": {},
            "api_surface_distribution": {},
            "access_model_distribution": {},
            "buildability_distribution": {},
            "mcp_adoption_count": 0,
            "category_distribution": {}
        }
        
        for app in verified_apps:
            # Auth
            for auth in app.authentication_methods:
                stats["auth_distribution"][auth.value] = stats["auth_distribution"].get(auth.value, 0) + 1
            # API Surface
            for api in app.api_surface:
                stats["api_surface_distribution"][api.value] = stats["api_surface_distribution"].get(api.value, 0) + 1
            # Access Model
            am = app.self_serve_status.value
            stats["access_model_distribution"][am] = stats["access_model_distribution"].get(am, 0) + 1
            # Buildability
            bv = app.buildability_verdict.value
            stats["buildability_distribution"][bv] = stats["buildability_distribution"].get(bv, 0) + 1
            # MCP
            if app.mcp_available:
                stats["mcp_adoption_count"] += 1
            # Category Rollups
            cat = app.category
            if cat not in stats["category_distribution"]:
                stats["category_distribution"][cat] = {"total_apps": 0, "easy_builds": 0, "gated_access": 0}
            stats["category_distribution"][cat]["total_apps"] += 1
            if bv == "Easy":
                stats["category_distribution"][cat]["easy_builds"] += 1
            if am == "Gated":
                stats["category_distribution"][cat]["gated_access"] += 1

        # 2. Prompt the LLM as a Strategic Executive
        prompt = f"""
        You are the VP of Product Operations for Composio, an API integration platform.
        You have just completed a research sprint on {stats['total_verified_apps']} verified SaaS applications.
        
        Here are the exact, pre-calculated hard statistics from the database:
        {stats}
        
        Your job is to generate a highly strategic AnalysisReport.
        Do NOT just repeat the statistics in text form. Your goal is to answer: "So what does this mean for Composio?"
        
        Focus areas:
        - Authentication and API tech trends (e.g., if REST + OAuth dominates, what does that mean for our core engine?)
        - Self-serve vs Gated trends.
        - Categories that represent "Quick Wins" (high easy_builds, low gated_access).
        - Categories that require Business Development (BD) to partner with before Engineering can build (high gated_access).
        - General risk and opportunity analysis based on the data.
        
        Every recommendation MUST explain WHY the finding matters. 
        """
        
        # We use a larger Groq model here since this runs only once at the very end
        report: AnalysisReport = await self.client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # Groq's best reasoning model for strategic analysis
            response_model=AnalysisReport,
            messages=[
                {"role": "system", "content": "You are a visionary Product Operations executive."},
                {"role": "user", "content": prompt}
            ],
            max_retries=3
        )
        
        # Enforce grounding by explicitly overriding the LLM's metrics with our calculated truth
        report.key_metrics = stats
        
        logger.info("Pattern Analysis complete.")
        return report
