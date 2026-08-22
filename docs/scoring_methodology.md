# Scoring Methodology (Schema 2.0)

This document is the canonical description of how the confidence score for
each relationship is computed. It is kept in lock-step with the engine: the
API endpoint `GET /api/v1/scoring-methodology` returns the **same** values
produced by `src.scoring.scoring_methodology()`, and the Dashboard renders
that endpoint verbatim — so this prose, the UI, and the code cannot drift
apart.

The score is **explicable**: it is the weighted sum of five documented
dimensions, each traceable to the underlying evidence via the per-relationship
`score_breakdown` returned by the API and shown in the Dashboard.

## The five dimensions (total 100)

| Dimension | Weight | What it measures | Source of truth |
|---|---:|---|---|
| `authority` | 25 | Authoritativeness of the **most** authoritative source | `evidence.source_type` |
| `evidence_quality` | 25 | Number of **independent** source URLs | `evidence.source_url` |
| `recency` | 20 | Age of the newest evidence (with research-judgment refinements) | `evidence.published_at` |
| `specificity` | 20 | How specifically the relationship is described | `evidence.quote` (+ `relationship.summary`) |
| `quantifiability` | 10 | Whether concrete numbers are attached | `evidence.quote` (+ `relationship.summary`) |

Final score = `min(weighted_sum, 100)`. The status band is derived from the
final score, **never** stored independently:

| Status | Score band |
|---|---|
| `confirmed` | `>= 70` |
| `inferred` | `40 – 69` |
| `unknown` | `< 40` |

## Authority tiers (`source_type` → points)

Only the highest-authority source type among the evidence contributes
(no other field can raise authority):

| source_type | points |
|---|---:|
| `sec_filing` / `exchange_filing` | 25 |
| `government` | 22 |
| `company_ir` / `company_press_release` | 20 |
| `analyst_research` | 18 |
| `business_media` | 16 |
| `industry_database` | 15 |
| `reference` | 10 |
| `informal` | 4 |
| `unknown` | 0 |

## Evidence quality

Points for the number of **independent** source URLs (distinct URLs count
once; duplicates are de-duplicated). Only evidence fields are scored.

| independent URLs | points |
|---:|---:|
| 1 | 16 |
| 2 | 21 |
| 3 | 24 |
| 4+ | 25 |

`evidence.independence_group` records the underlying **provenance lineage**
so that syndicated / derivative copies of the same press release are not
mistaken for independent sources (deliverable #3: source conflict &
co-occurrence misjudgment). The group is captured and validated for every
item; it is the audit trail reviewers use to judge independent support.

## Recency bands (days since newest evidence → points)

| age ≤ | points |
|---:|---:|
| 180 | 20 |
| 365 | 16 |
| 730 | 12 |
| 1095 | 8 |
| 1825 | 4 |
| older | 1 (floor) |

**Refinement — official-ongoing freshness:** an official confirmation
(`sec`/`exchange` filing, `government`, company `ir`/`press_release`) of an
*ongoing* relationship (`valid_until` is null or in the future) scores the
top recency band — the source asserts the relationship still holds as of the
snapshot date. Terminated relationships (`valid_until` in the past) decay
normally. Evidence with no publication date uses `accessed_at` as a proxy
(capped lower, since collection metadata is not publication proof).

## Specificity

Scans `evidence.quote` and `relationship.summary` for strong relationship
vocabulary (positive, capped at 20) and hedged language (negative penalty).
A **direct-statement bonus** is awarded when an *official* source names the
counterparty in an explicit relationship context (e.g. "Our current
competitors include … AMD"); third-party estimates do not qualify — that is
the difference between a named claim and an inference.

## Quantifiability

Rewards relationship-relevant measures found in the evidence quotes /
summary: percentages, monetary amounts, and concrete units (shares, GPUs,
chips, wafers, servers, orders, …). Capped at 10.

## `evidence.support_level` (Schema 2.0)

Every evidence item declares `support_level` ∈
`direct | indirect | contextual`, **independent of `source_type`**. An
authoritative source can still be merely *contextual* for a given
relationship (e.g. a co-mention in an unrelated release). This field lets a
reviewer distinguish a genuine, direct claim from a coincidence — the core
of deliverable #3 (entity ambiguity / source conflict / co-occurrence
misjudgment). It is captured and validated for every item; it is also the
field a future scoring refinement could weight directly (the engine
currently records and audits it rather than re-scoring on it, keeping the
reproducibility contract exact).

## Reproducibility contract

The committed `confidence_score` and `status` of every relationship are
**derived** values: `scripts/sync_scores.py` writes them from the engine,
and `scripts/validate_data.py` (and the test suite) assert the stored
values equal a fresh engine recomputation. Changing evidence changes the
score; the score is never edited by hand.
