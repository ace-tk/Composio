from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, HttpUrl

class SelfServeStatus(str, Enum):
    SELF_SERVE = "Self-Serve"
    GATED = "Gated"
    HYBRID = "Hybrid"
    UNKNOWN = "Unknown"

class BuildabilityVerdict(str, Enum):
    EASY = "Easy"
    MODERATE = "Moderate"
    HARD = "Hard"
    IMPOSSIBLE = "Impossible"
    UNKNOWN = "Unknown"

class ResearchStatus(str, Enum):
    NEW = "New"
    RESEARCHED = "Researched"
    VERIFIED = "Verified"
    MANUAL_REVIEW = "Manual Review"
    FAILED = "Failed"

class AuthMethod(str, Enum):
    OAUTH2 = "OAuth 2.0"
    API_KEY = "API Key"
    BASIC = "Basic Auth"
    JWT = "JWT"
    UNKNOWN = "Unknown"
    OTHER = "Other"

class APISurface(str, Enum):
    REST = "REST"
    GRAPHQL = "GraphQL"
    SOAP = "SOAP"
    WEBHOOKS = "Webhooks"
    GRPC = "gRPC"
    UNKNOWN = "Unknown"
    OTHER = "Other"

class SourceType(str, Enum):
    OFFICIAL_DOCS = "Official Docs"
    DEVELOPER_DOCS = "Developer Docs"
    PRICING_PAGE = "Pricing Page"
    HELP_CENTER = "Help Center"
    GITHUB = "GitHub"
    BLOG = "Blog"
    OTHER = "Other"

class Evidence(BaseModel):
    """Represents a piece of evidence supporting a claim made by the AI."""
    url: HttpUrl = Field(..., description="The direct URL to the source material.")
    source_type: SourceType = Field(..., description="The type of source material.")
    claim_supported: str = Field(..., description="A short description of the specific claim this evidence supports (e.g., 'Confirms OAuth 2.0 support').")

class SaaSApplicationData(BaseModel):
    """
    The core data contract representing a single researched SaaS application.
    This schema is used across all agents (Researcher, Verifier, Analyst) to ensure consistency.
    """
    
    # ---------------------------------------------------------
    # Core Identity
    # ---------------------------------------------------------
    app_name: str = Field(
        ..., 
        description="The official name of the SaaS application."
    )
    website: HttpUrl = Field(
        ..., 
        description="The main homepage URL of the SaaS."
    )
    category: str = Field(
        ..., 
        description="The primary industry category (e.g., CRM, Developer Tools, Marketing)."
    )
    one_line_description: str = Field(
        ..., 
        description="A concise, one-sentence description of what the product does."
    )
    agent_summary: str = Field(
        ...,
        description="A short AI-generated summary of the application's overall integration ecosystem."
    )

    # ---------------------------------------------------------
    # Technical & API Details
    # ---------------------------------------------------------
    authentication_methods: List[AuthMethod] = Field(
        ..., 
        description="List of supported authentication mechanisms for the API."
    )
    self_serve_status: SelfServeStatus = Field(
        ..., 
        description="Indicates whether a developer can sign up and access the API immediately."
    )
    api_surface: List[APISurface] = Field(
        ..., 
        description="The types of APIs offered by the platform."
    )
    api_documentation_url: Optional[HttpUrl] = Field(
        None, 
        description="The direct URL to the official developer or API documentation."
    )
    mcp_available: bool = Field(
        ..., 
        description="Indicates if a Model Context Protocol (MCP) server already exists for this app."
    )

    # ---------------------------------------------------------
    # Integration Assessment
    # ---------------------------------------------------------
    buildability_verdict: BuildabilityVerdict = Field(
        ..., 
        description="Assessment of how difficult it is to build an integration."
    )
    primary_blocker: Optional[str] = Field(
        None, 
        description="The main obstacle preventing a quick integration (e.g., 'Requires enterprise plan', 'Undocumented API')."
    )

    # ---------------------------------------------------------
    # Evidence & Verification
    # ---------------------------------------------------------
    evidence: List[Evidence] = Field(
        default_factory=list, 
        description="Structured list of evidence linking claims to source URLs."
    )
    confidence_score: float = Field(
        0.0, 
        ge=0.0, 
        le=100.0, 
        description="Confidence score from 0.0 to 100.0 regarding the accuracy of the extracted data."
    )
    status: ResearchStatus = Field(
        default=ResearchStatus.NEW, 
        description="Current pipeline state of this record."
    )
    manual_review_required: bool = Field(
        default=False, 
        description="Flag indicating if a human needs to manually verify or fix this record."
    )
    
    # ---------------------------------------------------------
    # Processing Metadata
    # ---------------------------------------------------------
    research_timestamp: Optional[datetime] = Field(
        None,
        description="When the initial research was completed."
    )
    verification_timestamp: Optional[datetime] = Field(
        None,
        description="When the verification process was completed."
    )
    processing_time_seconds: Optional[float] = Field(
        None,
        description="Total time in seconds spent researching and verifying this app."
    )
    processing_notes: List[str] = Field(
        default_factory=list,
        description="A list of warnings, notes, or contextual information generated during processing."
    )
    notes: Optional[str] = Field(
        None, 
        description="Any additional context, edge cases, or reasoning provided by the AI agents or human reviewers."
    )
