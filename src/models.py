"""Domain models for the supply chain & partnership research service.

The schema is intentionally company-agnostic: any target company (NVIDIA,
Unitree, etc.) can be represented by loading a compatible dataset. All
models are Pydantic v2 classes so they can be used both for data
validation and API serialization.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EntityType(str, Enum):
    """Whether the entity is the research target or a related company."""
    TARGET = "target"
    RELATED = "related"


class RelationshipType(str, Enum):
    """The five relationship categories required by the challenge."""
    SUPPLIER = "supplier"
    CUSTOMER = "customer"
    PARTNER = "partner"
    INVESTOR_OR_INVESTEE = "investor_or_investee"
    PEER = "peer"


class RelationshipStatus(str, Enum):
    """Classification of the epistemic status of a relationship.

    - confirmed: directly evidenced by authoritative sources
    - inferred:   reasonable inference from indirect evidence
    - unknown:    reported but not verifiable from available sources
    """
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class AccessRestriction(str, Enum):
    """Access / licensing restrictions on an evidence source."""
    PUBLIC = "public"
    PAYWALL = "paywall"
    LOGIN = "login"
    REGISTRATION = "registration"
    UNKNOWN = "unknown"


class SourceType(str, Enum):
    """Taxonomy of evidence source types, used by the scoring engine to
    weigh source authority."""
    SEC_FILING = "sec_filing"                      # SEC EDGAR 10-K/8-K etc.
    EXCHANGE_FILING = "exchange_filing"            # HKEX / other exchange filings
    GOVERNMENT = "government"                      # government / regulatory body
    COMPANY_IR = "company_ir"                      # investor relations pages
    COMPANY_PRESS_RELEASE = "company_press_release"  # official press releases
    BUSINESS_MEDIA = "business_media"              # established financial media
    ANALYST_RESEARCH = "analyst_research"          # analyst reports / databases
    INDUSTRY_DATABASE = "industry_database"        # industry data providers
    REFERENCE = "reference"                        # encyclopedic / secondary refs
    INFORMAL = "informal"                          # blogs / forums / social media
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    """A single piece of evidence supporting a relationship."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique evidence id, e.g. 'ev_001'")
    relationship_id: str = Field(description="Relationship this evidence supports")
    source_url: str = Field(description="Canonical URL of the source")
    publisher: str = Field(description="Publisher / issuing body of the source")
    source_type: SourceType = Field(
        default=SourceType.UNKNOWN,
        description="Source taxonomy used by the scoring engine for authority weighting",
    )
    published_at: Optional[date] = Field(
        default=None, description="When the source was published (if known)"
    )
    accessed_at: date = Field(description="When this evidence was collected")
    evidence_locator: str = Field(
        description="Precise locator: filing name, item, page/paragraph, or section"
    )
    access_restriction: AccessRestriction = Field(
        default=AccessRestriction.PUBLIC,
        description="Access / licensing restriction of the source",
    )
    license_note: str = Field(
        default="", description="License or terms note for the source"
    )
    quote: str = Field(
        description="Direct quote or precise paraphrase from the source"
    )


# ---------------------------------------------------------------------------
# Relationship
# ---------------------------------------------------------------------------

class Relationship(BaseModel):
    """A directed relationship between two companies."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique relationship id, e.g. 'rel_001'")
    source_company_id: str = Field(
        description="Company id at the tail of the relationship arrow"
    )
    target_company_id: str = Field(
        description="Company id at the head of the relationship arrow"
    )
    type: RelationshipType = Field(description="Relationship category")
    direction: str = Field(
        description="Human-readable direction, e.g. 'tsmc -> nvidia'"
    )
    status: RelationshipStatus = Field(
        description="Epistemic status: confirmed / inferred / unknown"
    )
    confidence_score: int = Field(
        ge=0, le=100, description="0-100 relevance / confidence score"
    )
    valid_from: Optional[date] = Field(
        default=None, description="Start of the relationship's known validity window"
    )
    valid_until: Optional[date] = Field(
        default=None,
        description="End of the relationship's known validity window; null = still active",
    )
    evidence_ids: list[str] = Field(
        default_factory=list, description="Evidence ids supporting this relationship"
    )
    summary: str = Field(description="One-paragraph human summary of the relationship")

    @field_validator("confidence_score")
    @classmethod
    def clamp_score(cls, v: int) -> int:
        if v < 0 or v > 100:
            raise ValueError("confidence_score must be in [0, 100]")
        return v


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

class Company(BaseModel):
    """A company entity — either the research target or a related company."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable slug id, e.g. 'nvidia'")
    name: str = Field(description="Legal / common company name")
    stock_code: Optional[str] = Field(
        default=None, description="Ticker symbol if publicly listed, e.g. 'NVDA'"
    )
    exchange: Optional[str] = Field(
        default=None, description="Listing exchange, e.g. 'NASDAQ'"
    )
    isin: Optional[str] = Field(
        default=None, description="ISIN identifier if available"
    )
    country: str = Field(description="Headquarters country")
    entity_type: EntityType = Field(
        description="'target' for the research object, 'related' otherwise"
    )
    sector: Optional[str] = Field(default=None, description="Industry sector")
    description: str = Field(description="Short description of the company")


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

class Dataset(BaseModel):
    """The full dataset snapshot — a single loadable document."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(description="Dataset schema version")
    as_of: date = Field(description="Research data cutoff date (snapshot date)")
    research_target: str = Field(
        description="Company id that is the research target"
    )
    companies: list[Company]
    relationships: list[Relationship]
    evidence: list[Evidence]


# ---------------------------------------------------------------------------
# API response models
# ---------------------------------------------------------------------------

class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool
    has_previous: bool


class ErrorResponse(BaseModel):
    error: str = Field(description="Machine-readable error code")
    message: str = Field(description="Human-readable error message")
    detail: Optional[Any] = Field(default=None, description="Optional extra detail")


class HealthResponse(BaseModel):
    status: str
    dataset: str
    as_of: date
    companies: int
    relationships: int
    evidence: int
    server_time: datetime
