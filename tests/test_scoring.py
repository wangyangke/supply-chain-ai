"""Scoring engine tests: dimensions, bands, and the two research refinements.

The engine must be *explicable* (a reviewer can trace a score to its five
dimensions) and *reproducible* (the committed `confidence_score` values are
exactly what the engine produces from the committed evidence).
"""

from datetime import date

from src.models import Evidence, Relationship
from src.scoring import (
    _name_in_text,
    _score_authority,
    _score_evidence_quality,
    _score_recency,
    _score_specificity,
    expected_band,
    score_relationship,
)

AS_OF = date(2026, 8, 21)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_rel(**kw) -> Relationship:
    defaults = dict(
        id="rel_x",
        source_company_id="nvidia",
        target_company_id="acme",
        type="partner",
        direction="nvidia -> acme",
        status="inferred",
        confidence_score=50,
        valid_from=None,
        valid_until=None,
        evidence_ids=[],
        summary="",
    )
    defaults.update(kw)
    return Relationship(**defaults)


def make_ev(**kw) -> Evidence:
    defaults = dict(
        id="ev_x",
        relationship_id="rel_x",
        source_url="https://example.com/x",
        publisher="Example Corp",
        source_type="reference",
        independence_group="example.com",
        support_level="indirect",
        published_at=date(2026, 1, 1),
        accessed_at=AS_OF,
        evidence_locator="Section 1",
        access_restriction="public",
        license_note="",
        quote="",
    )
    defaults.update(kw)
    return Evidence(**defaults)


# ---------------------------------------------------------------------------
# Dimension: authority
# ---------------------------------------------------------------------------

class TestAuthority:
    def test_sec_filing_is_top_tier(self):
        ev = make_ev(source_type="sec_filing")
        assert _score_authority([ev]) == 25.0

    def test_press_release_tier(self):
        ev = make_ev(source_type="company_press_release")
        assert _score_authority([ev]) == 20.0

    def test_reference_tier(self):
        ev = make_ev(source_type="reference")
        assert _score_authority([ev]) == 10.0

    def test_takes_max_of_multiple(self):
        evs = [make_ev(id="e1", source_type="informal"), make_ev(id="e2", source_type="sec_filing")]
        assert _score_authority(evs) == 25.0

    def test_empty_evidence_scores_zero(self):
        assert _score_authority([]) == 0.0


# ---------------------------------------------------------------------------
# Dimension: evidence quality (independent sources)
# ---------------------------------------------------------------------------

class TestEvidenceQuality:
    def test_single_source(self):
        ev = make_ev()
        assert _score_evidence_quality([ev]) == 16.0

    def test_two_independent_sources(self):
        evs = [
            make_ev(id="e1", source_url="https://a.example/1"),
            make_ev(id="e2", source_url="https://b.example/2"),
        ]
        assert _score_evidence_quality(evs) == 21.0

    def test_duplicate_url_counts_once(self):
        evs = [
            make_ev(id="e1", source_url="https://a.example/1"),
            make_ev(id="e2", source_url="https://a.example/1"),
        ]
        assert _score_evidence_quality(evs) == 16.0

    def test_three_sources(self):
        evs = [
            make_ev(id=f"e{i}", source_url=f"https://s{i}.example/") for i in range(3)
        ]
        assert _score_evidence_quality(evs) == 24.0

    def test_empty_scores_zero(self):
        assert _score_evidence_quality([]) == 0.0


# ---------------------------------------------------------------------------
# Dimension: recency (incl. the official-ongoing refinement)
# ---------------------------------------------------------------------------

class TestRecency:
    def test_official_ongoing_relationship_is_fresh(self):
        # sec_filing from 2015, relationship still valid in 2026 → top band,
        # because an official filing asserts the relationship continues.
        ev = make_ev(source_type="sec_filing", published_at=date(2015, 1, 1))
        rel = make_rel(valid_until=None)
        assert _score_recency(rel, [ev], AS_OF) == 20.0

    def test_press_release_ongoing_is_fresh(self):
        ev = make_ev(source_type="company_press_release", published_at=date(2020, 1, 1))
        rel = make_rel(valid_until=None)
        assert _score_recency(rel, [ev], AS_OF) == 20.0

    def test_terminated_relationship_decays(self):
        # Exited relationship (valid_until in the past) keeps full decay.
        ev = make_ev(source_type="sec_filing", published_at=date(2015, 1, 1))
        rel = make_rel(valid_until=date(2025, 2, 14))
        assert _score_recency(rel, [ev], AS_OF) < 20.0

    def test_recent_non_official_news(self):
        ev = make_ev(source_type="business_media", published_at=date(2026, 8, 1))
        rel = make_rel(valid_until=None)
        assert _score_recency(rel, [ev], AS_OF) == 20.0

    def test_very_old_evidence_keeps_floor(self):
        ev = make_ev(source_type="informal", published_at=date(2000, 1, 1))
        rel = make_rel(valid_until=date(2005, 1, 1))  # long terminated
        assert _score_recency(rel, [ev], AS_OF) == 1.0

    def test_no_dates_uses_accessed_at(self):
        ev = make_ev(published_at=None)
        rel = make_rel(valid_until=None)
        assert _score_recency(rel, [ev], AS_OF) == 20.0


# ---------------------------------------------------------------------------
# Dimension: specificity (incl. the direct-statement refinement)
# ---------------------------------------------------------------------------

class TestSpecificity:
    def test_direct_statement_bonus_from_official_source(self):
        # sec_filing names the counterparty in a competitor context.
        rel = make_rel(
            type="peer", source_company_id="nvidia", target_company_id="amd",
        )
        ev = make_ev(
            source_type="sec_filing",
            quote="Our current competitors include Advanced Micro Devices, Inc.",
        )
        names = {"nvidia": "NVIDIA Corporation", "amd": "Advanced Micro Devices, Inc. (AMD)"}
        score = _score_specificity(rel, [ev], company_names=names, research_target_id="nvidia")
        assert score >= 5.0  # direct-statement bonus included

    def test_no_bonus_for_third_party_estimate(self):
        # Same quote, but from media → third-party estimate, no bonus.
        rel = make_rel(
            type="peer", source_company_id="nvidia", target_company_id="amd",
        )
        ev = make_ev(
            source_type="business_media",
            quote="Analysts say NVIDIA's current competitors include Advanced Micro Devices.",
        )
        names = {"nvidia": "NVIDIA Corporation", "amd": "Advanced Micro Devices, Inc. (AMD)"}
        score_media = _score_specificity(rel, [ev], company_names=names, research_target_id="nvidia")
        ev_official = make_ev(
            id="ev_official", source_type="sec_filing",
            quote="Our current competitors include Advanced Micro Devices, Inc.",
        )
        score_official = _score_specificity(
            rel, [ev_official], company_names=names, research_target_id="nvidia"
        )
        assert score_official > score_media

    def test_weak_language_penalizes(self):
        rel = make_rel()
        ev_strong = make_ev(
            quote="NVIDIA and ACME announced a multi-year partnership agreement.",
        )
        ev_weak = make_ev(
            id="ev_weak",
            quote="NVIDIA and ACME reportedly may partner, sources say.",
        )
        names = {"nvidia": "NVIDIA Corporation", "acme": "Acme Corp"}
        s_strong = _score_specificity(rel, [ev_strong], company_names=names, research_target_id="nvidia")
        s_weak = _score_specificity(rel, [ev_weak], company_names=names, research_target_id="nvidia")
        assert s_strong > s_weak

    def test_specificity_capped(self):
        rel = make_rel()
        ev = make_ev(
            quote=("foundry supplier purchase agreement partner collaboration "
                   "manufacturing contract exclusive largest sole supplier "
                   "multi-year partnership joint venture strategic alliance "),
        )
        rel = make_rel(summary="NVIDIA and ACME are partners with a joint agreement to co-develop.")
        score = _score_specificity(rel, [ev])
        assert score <= 20.0


# ---------------------------------------------------------------------------
# Name matching (tolerant matching used by the direct-statement bonus)
# ---------------------------------------------------------------------------

class TestNameMatching:
    def test_full_name(self):
        assert _name_in_text("NVIDIA Corporation announced today", "NVIDIA Corporation")

    def test_token_match_legal_suffix_dropped(self):
        assert _name_in_text("Cisco Systems, Inc. announced today", "Cisco")
        assert _name_in_text("SK Hynix Inc. announced today", "SK Hynix")
        assert _name_in_text("Micron Technology, Inc. announced today", "Micron")

    def test_no_match(self):
        assert not _name_in_text("We partner with OpenAI", "Cisco")
        assert not _name_in_text("no company mentioned here", "NVIDIA")


# ---------------------------------------------------------------------------
# Quantifiability
# ---------------------------------------------------------------------------

class TestQuantifiability:
    def test_money_amount(self):
        # $ + digits → 3 + 2 = 5 (no percentage term).
        rel = make_rel()
        ev = make_ev(quote="NVIDIA invested $2 billion at $87.20 per share.")
        breakdown = score_relationship(rel, [ev], AS_OF)
        assert breakdown.dimensions["quantifiability"] == 5.0

    def test_percentage(self):
        # % + digits → 5 + 2 = 7.
        rel = make_rel()
        ev = make_ev(quote="NVIDIA holds a 19% stake in the company.")
        breakdown = score_relationship(rel, [ev], AS_OF)
        assert breakdown.dimensions["quantifiability"] == 7.0

    def test_money_and_percentage_cap(self):
        # % + $ + digits → 5 + 3 + 2 = 10 (capped).
        rel = make_rel()
        ev = make_ev(
            quote="NVIDIA invested $2 billion for a 19% stake in the company."
        )
        breakdown = score_relationship(rel, [ev], AS_OF)
        assert breakdown.dimensions["quantifiability"] == 10.0

    def test_no_numbers(self):
        rel = make_rel()
        ev = make_ev(quote="The two companies have a long-standing partnership.")
        breakdown = score_relationship(rel, [ev], AS_OF)
        assert breakdown.dimensions["quantifiability"] == 0.0


# ---------------------------------------------------------------------------
# Bands & total
# ---------------------------------------------------------------------------

class TestBands:
    def test_expected_band_rubric(self):
        assert expected_band(70) == "confirmed"
        assert expected_band(100) == "confirmed"
        assert expected_band(69) == "inferred"
        assert expected_band(40) == "inferred"
        assert expected_band(39) == "unknown"
        assert expected_band(0) == "unknown"

    def test_score_breakdown_total_is_sum_of_dimensions(self):
        rel = make_rel()
        evs = [
            make_ev(id="e1", source_type="sec_filing",
                    quote="NVIDIA and ACME signed a multi-year partnership agreement for $2 billion."),
        ]
        breakdown = score_relationship(rel, evs, AS_OF)
        dims = breakdown.dimensions
        assert abs(breakdown.total - sum(dims.values())) < 1e-6
        assert set(dims.keys()) == {
            "authority", "evidence_quality", "recency", "specificity", "quantifiability",
        }

    def test_high_quality_relationship_confirmed(self):
        rel = make_rel(
            id="rel_strong",
            source_company_id="nvidia",
            target_company_id="acme",
            type="partner",
            status="inferred",
            summary="NVIDIA and ACME announced a multi-year strategic partnership.",
        )
        evs = [
            make_ev(id="e1", source_type="company_press_release",
                    quote="NVIDIA and ACME today announced a multi-year partnership agreement."),
            make_ev(id="e2", source_url="https://example.com/2", source_type="business_media",
                    quote="The partnership will generate $5 billion in revenue."),
        ]
        breakdown = score_relationship(rel, evs, AS_OF)
        assert breakdown.band == "confirmed"
        assert breakdown.total >= 70.0


# ---------------------------------------------------------------------------
# Reproducibility: committed data must match the engine exactly
# ---------------------------------------------------------------------------

class TestReproducibility:
    def test_all_stored_scores_match_engine(self, store):
        names = {cid: c.name for cid, c in store.companies.items()}
        for rel in store.relationships:
            evidence = store.list_evidence_for_relationship(rel.id)
            breakdown = score_relationship(
                rel, evidence, store.dataset.as_of,
                company_names=names,
                research_target_id=store.dataset.research_target,
            )
            assert round(breakdown.total) == rel.confidence_score, (
                f"{rel.id}: stored {rel.confidence_score} != engine {breakdown.total:.1f}"
            )
            assert breakdown.band == rel.status.value, (
                f"{rel.id}: stored status '{rel.status.value}' != band '{breakdown.band}'"
            )

    def test_every_relationship_has_evidence(self, store):
        for rel in store.relationships:
            assert len(store.list_evidence_for_relationship(rel.id)) >= 1, rel.id

    def test_status_distribution(self, store):
        statuses = {r.status.value for r in store.relationships}
        assert statuses == {"confirmed", "inferred"}
