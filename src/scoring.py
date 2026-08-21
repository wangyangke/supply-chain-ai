"""Confidence scoring engine (0-100).

The score is designed to be *explicable*: it is the weighted sum of five
dimensions, each with a documented rubric. Reviewers can trace any score
back to the underlying evidence via the dimension breakdown returned by
`score_relationship`.

Dimensions and weights:

  authority           25  Authoritativeness of the most authoritative source
  evidence_quality    25  Number of independent sources & evidence depth
  recency             20  Age of the most recent evidence
  specificity         20  How specifically the relationship is described
  quantifiability     10  Whether concrete numbers are attached

Two refinements encode research judgment:

- Recency: official confirmation (filing, IR, press release) of an
  *ongoing* relationship is treated as fresh — the source asserts the
  relationship continues to exist as of the snapshot date. Terminated
  relationships (valid_until in the past) decay normally.
- Specificity: a *direct statement* bonus applies when an official source
  names the counterparty in an explicit relationship context (e.g.
  "Our current competitors include ... AMD"). Third-party estimates do not
  qualify — that is the difference between a named claim and an inference.

The mapping status -> expected band (documented in docs/scoring_methodology.md):
  confirmed  -> score >= 70
  inferred   -> 40 <= score < 70
  unknown    -> score < 40
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from .models import AccessRestriction, Evidence, Relationship


# ---------------------------------------------------------------------------
# Rubric constants
# ---------------------------------------------------------------------------

# Authority weight per source type (not access restriction — access is
# tracked separately for compliance, authority reflects source trust).
AUTHORITY_TIERS: dict[str, float] = {
    # Regulatory / statutory filings (highest trust)
    "sec_filing": 25.0,
    "exchange_filing": 25.0,
    # Government / regulatory bodies
    "government": 22.0,
    # Official company channels
    "company_ir": 20.0,
    "company_press_release": 20.0,
    # Analyst research / industry databases
    "analyst_research": 18.0,
    # Established financial / business media
    "business_media": 16.0,
    "industry_database": 15.0,
    # Encyclopedic / secondary reference
    "reference": 10.0,
    # Personal blogs / forums / social media
    "informal": 4.0,
    # Unknown publisher
    "unknown": 0.0,
}

# Evidence count thresholds for the evidence_quality dimension.
# A single high-quality source is already worth substantial points;
# independent additional sources add up to the cap.
EVIDENCE_COUNT_SCALE = [
    (0, 0.0),
    (1, 16.0),
    (2, 21.0),
    (3, 24.0),
    (4, 25.0),
]

# Recency bands (days) -> points. Filings are refreshed annually and are
# slow-moving information, so authority is factored in: high-authority
# sources decay slower than news.
RECENCY_BANDS = [
    (180, 20.0),
    (365, 16.0),
    (730, 12.0),
    (1095, 8.0),
    (1825, 4.0),
]

# Strong relationship words -> +SPECIFICITY_TERM_POINTS each (evidence
# quote and relationship summary are scanned together).
SPECIFICITY_TERMS = [
    "foundry", "supplier", "purchase", "buy", "buys", "customer",
    "competitor", "compete", "competition", "partner", "partnership",
    "collaborat", "invest", "stake", "acquired", "co-develop",
    "manufactur", "produce", "producing", "fab", "memory", "primary",
    "largest", "sole", "exclusive", "contract", "agreement",
    "multi-year", "alliance", "official", "joint", "expansion",
    "expanded", "strategic", "milestone", "sold",
]
SPECIFICITY_TERM_POINTS = 3.0
SPECIFICITY_CAP = 20.0

# Weak / hedged language -> penalty (signals inference, not confirmation).
WEAK_TERMS = [
    "reportedly", "may", "might", "likely", "possibly", "believed",
    "rumor", "rumour", "according to reports", "sources say",
    "not a direct", "indirect", "could",
]
WEAK_TERM_PENALTY = -2.0

# Source types that carry official / statutory weight. Used by the recency
# logic (official confirmation of an ongoing relationship is fresh) and the
# direct-statement bonus (the parties themselves or a regulator state the
# relationship, as opposed to a third party estimating it).
OFFICIAL_SOURCE_TYPES = (
    "sec_filing",
    "exchange_filing",
    "government",
    "company_ir",
    "company_press_release",
)

# Relationship-type vocabulary used by the direct-statement bonus.
RELATIONSHIP_TYPE_TERMS: dict[str, tuple[str, ...]] = {
    "supplier": (
        "supplier", "supply", "supplies", "purchase", "purchases",
        "purchased", "buy", "buys", "buying", "foundry", "memory",
        "fab", "manufactur", "produce", "producing",
    ),
    "customer": (
        "customer", "customers", "purchase", "purchases", "purchased",
        "buy", "buys", "buying", "orders", "sold",
    ),
    "partner": (
        "partner", "partnership", "collaborat", "co-develop",
        "alliance", "joint", "team",
    ),
    "investor_or_investee": (
        "invest", "invested", "investment", "stake", "funding",
        "equity", "round",
    ),
    "peer": (
        "competitor", "competitors", "compete", "competes",
        "competition", "competitive",
    ),
}

# Bonus for a *direct statement*: an official source names the counterparty
# in an explicit relationship context (e.g. "Our current competitors
# include ... AMD"). This distinguishes named claims from co-occurrence.
DIRECT_STATEMENT_BONUS = 5.0

# Words that carry no identity signal when matching a company name against
# evidence text (legal suffixes and function words).
NAME_STOPWORDS = {
    "inc", "corp", "corporation", "ltd", "limited", "company", "companies",
    "com", "plc", "holdings", "holding", "group", "the", "and", "for",
    "with", "of", "co",
}

MAX_SCORE = 100.0


def _significant_name_tokens(name: str) -> set[str]:
    """Extract identity-bearing tokens from a company name.

    'Advanced Micro Devices, Inc. (AMD)' -> {'advanced', 'micro', 'devices', 'amd'}
    'Cisco Systems, Inc.'                -> {'cisco', 'systems'}
    'SK Hynix Inc.'                      -> {'sk', 'hynix'}
    """
    tokens = set(re.findall(r"[a-z0-9]{2,}", name.lower()))
    return {t for t in tokens if t not in NAME_STOPWORDS}


def _name_in_text(text: str, name: str) -> bool:
    """Match a company name against evidence text tolerantly.

    Tries the full name first, then falls back to any significant name
    token (so 'Cisco Systems, Inc.' matches 'Cisco (NASDAQ: CSCO)').
    """
    name_l = name.lower()
    text_l = text.lower()
    if name_l in text_l:
        return True
    return any(tok in text_l for tok in _significant_name_tokens(name_l))


class ScoreBreakdown:
    """Per-dimension breakdown for explicability."""

    def __init__(self, dimensions: dict[str, float], total: float, band: str):
        self.dimensions = dimensions
        self.total = total
        self.band = band

    def to_dict(self) -> dict:
        return {
            "total": round(self.total, 1),
            "band": self.band,
            "dimensions": {k: round(v, 1) for k, v in self.dimensions.items()},
        }


def _clamp(value: float, lo: float = 0.0, hi: float = MAX_SCORE) -> float:
    return max(lo, min(hi, value))


def _score_authority(evidence: list[Evidence]) -> float:
    """Highest-authority source type among the evidence sources."""
    if not evidence:
        return 0.0
    return max(AUTHORITY_TIERS.get(e.source_type.value, 0.0) for e in evidence)


def _score_evidence_quality(evidence: list[Evidence]) -> float:
    """Points for number of *independent* evidence items.

    Independence is judged by distinct source URLs. Duplicate URLs count
    once; distinct documents from the same publisher (e.g. two different
    annual 10-K filings) are treated as independent.
    """
    if not evidence:
        return 0.0
    unique_urls = {e.source_url for e in evidence}
    n = len(unique_urls)
    base = 0.0
    for threshold, points in EVIDENCE_COUNT_SCALE:
        if n >= threshold:
            base = points
        else:
            break
    return base


def _score_recency(rel: Relationship, evidence: list[Evidence], as_of: date) -> float:
    """Banded decay based on the newest evidence's age.

    Two adjustments reflect real-world research judgment:
    - High-authority sources (filings, company IR/press releases) carry
      slow-moving facts and decay more slowly than news.
    - Relationships still marked valid (valid_until is None) get a
      freshness bonus: the evidence *proves* an ongoing relationship, so
      age is halved before band lookup. Terminated relationships
      (valid_until in the past) keep full decay — this is how a stale
      or exited relationship loses recency points.
    """
    dates = [e.published_at for e in evidence if e.published_at is not None]
    if not dates:
        # No publication date: use accessed_at as a proxy
        dates = [e.accessed_at for e in evidence]
    if not dates:
        return 0.0
    newest = max(dates)
    age_days = max((as_of - newest).days, 0)

    official = any(e.source_type.value in OFFICIAL_SOURCE_TYPES for e in evidence)
    still_valid = rel.valid_until is None or rel.valid_until >= as_of

    # Official confirmation of an *ongoing* relationship is fresh by
    # definition: the source asserts the relationship continues to exist as
    # of as_of. Filings are refreshed annually and official announcements do
    # not expire while the partnership continues, so cap effective age at
    # the top band. Terminated relationships keep full decay.
    if still_valid and official:
        return RECENCY_BANDS[0][1]

    # Filings refresh ~annually; treat them as fresh for longer.
    if official:
        age_days = max(age_days - 180, 0)
    # Ongoing relationships decay slower than terminated ones.
    if still_valid:
        age_days = age_days // 2

    for threshold_days, points in RECENCY_BANDS:
        if age_days <= threshold_days:
            return points
    return 1.0  # very old evidence keeps a small floor


def _is_direct_statement(
    rel: Relationship,
    evidence: list[Evidence],
    texts: list[str],
    company_names: Optional[dict[str, str]],
    research_target_id: Optional[str],
) -> bool:
    """True if an *official* source names the counterparty in an explicit
    relationship context (e.g. 'Our current competitors include ... AMD').

    The counterparty is the endpoint that is not the research target. A
    direct statement must come from an official / statutory source: claims
    by the parties themselves or by a regulator are treated as direct;
    third-party estimates (analyst reports, media) are not, even when they
    are specific — that is the epistemic difference between a named claim
    and an external estimate.
    """
    if not any(e.source_type.value in OFFICIAL_SOURCE_TYPES for e in evidence):
        return False
    if not company_names:
        return False

    counterparty_ids = {rel.source_company_id, rel.target_company_id}
    if research_target_id:
        counterparty_ids.discard(research_target_id)
    names = [
        company_names[cid]
        for cid in counterparty_ids
        if company_names.get(cid)
    ]
    if not names:
        return False

    combined = " ".join(t.lower() for t in texts)
    if not any(_name_in_text(combined, name) for name in names):
        return False

    type_terms = RELATIONSHIP_TYPE_TERMS.get(rel.type.value, ())
    return any(term in combined for term in type_terms)


def _score_specificity(
    rel: Relationship,
    evidence: list[Evidence],
    company_names: Optional[dict[str, str]] = None,
    research_target_id: Optional[str] = None,
) -> float:
    """How specifically the relationship is described.

    Scans both the evidence quotes and the relationship summary for strong
    relationship vocabulary (positive) and hedged language (negative), and
    adds a direct-statement bonus when an official source names the
    counterparty in an explicit relationship context.
    """
    texts = [e.quote or "" for e in evidence] + [rel.summary or ""]
    combined = " ".join(texts).lower()

    specificity = 0.0
    for term in SPECIFICITY_TERMS:
        if term in combined:
            specificity += SPECIFICITY_TERM_POINTS
    for term in WEAK_TERMS:
        if term in combined:
            specificity += WEAK_TERM_PENALTY

    if _is_direct_statement(rel, evidence, texts, company_names, research_target_id):
        specificity += DIRECT_STATEMENT_BONUS

    return _clamp(specificity, 0.0, SPECIFICITY_CAP)


def _score_quantifiability(rel: Relationship, evidence: list[Evidence]) -> float:
    """Points if the relationship carries concrete numbers in quotes/summary."""
    texts = [e.quote or "" for e in evidence] + [rel.summary or ""]
    combined = " ".join(texts).lower()
    score = 0.0
    if "%" in combined or "percent" in combined:
        score += 5.0
    if "$" in combined or "usd" in combined or "billion" in combined or "million" in combined:
        score += 3.0
    # A quantified figure (e.g. 19%, $2 billion, 30,000 professionals)
    if re.search(r"\d", combined):
        score += 2.0
    return _clamp(score, 0.0, 10.0)


def score_relationship(
    rel: Relationship,
    evidence: list[Evidence],
    as_of: date,
    company_names: Optional[dict[str, str]] = None,
    research_target_id: Optional[str] = None,
) -> ScoreBreakdown:
    """Compute the explicable confidence score for a relationship.

    Args:
        rel: the relationship to score
        evidence: all evidence items attached to the relationship
        as_of: the research snapshot date used for recency decay
        company_names: optional map of company id -> display name, used by
            the direct-statement bonus (an official source naming the
            counterparty in a relationship context)
        research_target_id: the research target company id, used to identify
            the counterparty of each relationship
    """
    dimensions = {
        "authority": _score_authority(evidence),
        "evidence_quality": _score_evidence_quality(evidence),
        "recency": _score_recency(rel, evidence, as_of),
        "specificity": _score_specificity(
            rel, evidence, company_names, research_target_id
        ),
        "quantifiability": _score_quantifiability(rel, evidence),
    }
    total = _clamp(sum(dimensions.values()))

    if total >= 70:
        band = "confirmed"
    elif total >= 40:
        band = "inferred"
    else:
        band = "unknown"

    return ScoreBreakdown(dimensions, total, band)


def expected_band(score: float) -> str:
    """Map a score to its expected status band (documented rubric)."""
    if score >= 70:
        return "confirmed"
    if score >= 40:
        return "inferred"
    return "unknown"
