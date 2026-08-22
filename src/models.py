"""Domain models for the supply chain & partnership research service.

The schema is intentionally company-agnostic: any target company (NVIDIA,
Unitree, etc.) can be represented by loading a compatible dataset. All
models are Pydantic v2 classes so they can be used both for data
validation and API serialization.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def compute_content_hash(
    source_url: str, evidence_locator: str, quote: str
) -> str:
    """SHA-256 fingerprint of an evidence item's immutable content.

    Hashes the canonical triple ``(source_url, evidence_locator, quote)``
    so that any tampering with the cited text, its locator, or the source
    URL is detectable by the validation script. The hash is stored on the
    Evidence object as ``content_hash`` and re-checked on load.
    """
    payload = f"{source_url}\u241f{evidence_locator}\u241f{quote}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


class EvidenceSupportLevel(str, Enum):
    """How directly an evidence item supports the represented edge.

    Deliberately separate from ``source_type``: an authoritative source can
    still be merely contextual for a particular relationship (e.g. a co-
    mention in an unrelated press release). This is the field that lets the
    research judge and the reviewer distinguish a named claim from a
    coincidence — the core of deliverable #3 (entity ambiguity / source
    conflict / co-occurrence misjudgment).
    """

    DIRECT = "direct"
    INDIRECT = "indirect"
    CONTEXTUAL = "contextual"


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
    independence_group: str = Field(
        min_length=1,
        description=(
            "Underlying evidence lineage (provenance group). Derivative or "
            "syndicated documents share one group even when their URLs differ, "
            "so the same press release's reprints/aggregations are not double-"
            "counted as independent sources."
        ),
    )
    support_level: EvidenceSupportLevel = Field(
        description=(
            "Whether this item directly, indirectly, or contextually supports "
            "the edge. Independent of source_type: an authoritative source can "
            "still be only contextual for a given relationship."
        ),
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
    access_notes: Optional[str] = Field(
        default=None,
        description=(
            "Observed availability, generation, or access caveats for reviewer "
            "use (e.g. AI-generated snippet, paywall, comparison evidence)."
        ),
    )
    license_note: str = Field(
        default="", description="License or terms note for the source"
    )
    quote: str = Field(
        description="Direct quote or precise paraphrase from the source"
    )
    content_hash: str = Field(
        default="",
        description=(
            "SHA-256 fingerprint of (source_url, evidence_locator, quote). "
            "If empty on construction, it is auto-computed; if non-empty, "
            "it is verified against the recomputed hash. The validation "
            "script treats a mismatch as a tampering error."
        ),
    )

    @model_validator(mode="after")
    def _check_content_hash(self) -> "Evidence":
        expected = compute_content_hash(
            self.source_url, self.evidence_locator, self.quote
        )
        if not self.content_hash:
            object.__setattr__(self, "content_hash", expected)
        elif self.content_hash != expected:
            raise ValueError(
                "content_hash does not match (source_url, evidence_locator, "
                "quote) — run `python scripts/sync_scores.py --write` to recompute"
            )
        return self


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
