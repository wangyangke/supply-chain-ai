#!/usr/bin/env python3
"""Validate the committed dataset snapshot (data/).

Checks that the fixtures satisfy the schema, referential integrity, and
the scoring engine's band mapping — the same guarantees the test suite
enforces, packaged as a standalone script reviewers can run on any dataset
(including a newly-built one for a different research target).

Usage:
  python scripts/validate_data.py [--data data] [--strict]

Exit code 0 = valid, 1 = errors found, 2 = warnings found (with --strict).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models import (
    AccessRestriction,
    EntityType,
    RelationshipStatus,
    RelationshipType,
    SourceType,
)
from src.scoring import expected_band, score_relationship
from src.store import Store

VALID_TYPES = {t.value for t in RelationshipType}
VALID_STATUSES = {s.value for s in RelationshipStatus}
VALID_ENTITY_TYPES = {e.value for e in EntityType}
VALID_SOURCE_TYPES = {s.value for s in SourceType}
VALID_ACCESS = {a.value for a in AccessRestriction}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(store: Store, report: Report) -> None:
    # ---- dataset.json -----------------------------------------------------
    ds = store.dataset
    if ds.schema_version != "1.0":
        report.err(f"dataset.json: unsupported schema_version '{ds.schema_version}'")
    if not ds.research_target:
        report.err("dataset.json: missing research_target")
    target = store.get_company(ds.research_target)
    if target is None:
        report.err(f"research_target '{ds.research_target}' is not a company in companies.json")

    # ---- companies.json ---------------------------------------------------
    target_count = sum(1 for c in store.companies.values() if c.entity_type.value == "target")
    if target_count != 1:
        report.err(f"companies.json: expected exactly 1 'target' entity, found {target_count}")
    for cid, c in store.companies.items():
        if c.entity_type.value not in VALID_ENTITY_TYPES:
            report.err(f"company {cid}: invalid entity_type '{c.entity_type.value}'")
        if not c.name.strip():
            report.err(f"company {cid}: empty name")
        if not c.country.strip():
            report.err(f"company {cid}: empty country")

    # ---- evidence.json ----------------------------------------------------
    for eid, ev in store.evidence.items():
        if ev.source_type.value not in VALID_SOURCE_TYPES:
            report.err(f"evidence {eid}: invalid source_type '{ev.source_type.value}'")
        if ev.access_restriction.value not in VALID_ACCESS:
            report.err(f"evidence {eid}: invalid access_restriction '{ev.access_restriction.value}'")
        if not ev.source_url.startswith("https://"):
            report.warn(f"evidence {eid}: source_url is not https: {ev.source_url[:60]}")
        if ev.published_at and ev.accessed_at and ev.published_at > ev.accessed_at:
            report.err(
                f"evidence {eid}: published_at {ev.published_at} after accessed_at {ev.accessed_at}"
            )
        if not ev.evidence_locator.strip():
            report.err(f"evidence {eid}: empty evidence_locator")
        if not ev.quote.strip():
            report.err(f"evidence {eid}: empty quote")

    # ---- relationships.json ------------------------------------------------
    seen_rel_ids: set[str] = set()
    for rel in store.relationships:
        if rel.id in seen_rel_ids:
            report.err(f"relationship {rel.id}: duplicate id")
        seen_rel_ids.add(rel.id)

        if rel.type.value not in VALID_TYPES:
            report.err(f"relationship {rel.id}: invalid type '{rel.type.value}'")
        if rel.status.value not in VALID_STATUSES:
            report.err(f"relationship {rel.id}: invalid status '{rel.status.value}'")
        if not (0 <= rel.confidence_score <= 100):
            report.err(f"relationship {rel.id}: confidence_score {rel.confidence_score} out of [0,100]")
        if rel.valid_from and rel.valid_until and rel.valid_from > rel.valid_until:
            report.err(
                f"relationship {rel.id}: valid_from {rel.valid_from} > valid_until {rel.valid_until}"
            )
        if rel.source_company_id == rel.target_company_id:
            report.err(f"relationship {rel.id}: self-referential (source == target)")
        if not rel.evidence_ids:
            report.err(f"relationship {rel.id}: no evidence attached")
        if not rel.summary.strip():
            report.err(f"relationship {rel.id}: empty summary")

        # Every evidence id must exist AND belong to this relationship.
        for eid in rel.evidence_ids:
            ev = store.get_evidence(eid)
            if ev is None:
                report.err(f"relationship {rel.id}: unknown evidence '{eid}'")
            elif ev.relationship_id != rel.id:
                report.err(
                    f"relationship {rel.id}: evidence '{eid}' is attached to '{ev.relationship_id}'"
                )

        # Duplicate URLs inside one relationship: two quotes from the same
        # page are legitimate (distinct claims), but the *independence*
        # scoring counts the URL once — this warning is informational.
        urls = [store.get_evidence(eid).source_url for eid in rel.evidence_ids if store.get_evidence(eid)]
        if len(urls) != len(set(urls)):
            report.warn(
                f"relationship {rel.id}: {len(urls) - len(set(urls))} evidence item(s) share a "
                f"source_url (scored as ONE independent source by design; verify they are "
                f"distinct quotes, not accidental duplicates)"
            )

        # --- engine consistency (the reproducibility contract) ---
        evidence = store.list_evidence_for_relationship(rel.id)
        names = {cid: c.name for cid, c in store.companies.items()}
        breakdown = score_relationship(
            rel, evidence, ds.as_of,
            company_names=names,
            research_target_id=ds.research_target,
        )
        if round(breakdown.total) != rel.confidence_score:
            report.err(
                f"relationship {rel.id}: stored score {rel.confidence_score} != "
                f"engine {breakdown.total:.1f} — run `python scripts/sync_scores.py --write`"
            )
        if breakdown.band != rel.status.value:
            report.err(
                f"relationship {rel.id}: stored status '{rel.status.value}' != "
                f"band '{breakdown.band}' (score {rel.confidence_score})"
            )

    # ---- cross-checks ------------------------------------------------------
    # Every evidence must reference a real relationship.
    for eid, ev in store.evidence.items():
        if store.get_relationship(ev.relationship_id) is None:
            report.err(f"evidence {eid}: unknown relationship '{ev.relationship_id}'")

    # The research target must appear in every relationship (by construction
    # for this service: relationships are always about the target company).
    for rel in store.relationships:
        if ds.research_target not in (rel.source_company_id, rel.target_company_id):
            report.err(
                f"relationship {rel.id}: does not involve research_target '{ds.research_target}'"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(PROJECT_ROOT / "data"), help="Dataset directory")
    ap.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    args = ap.parse_args()

    report = Report()
    try:
        store = Store.load(args.data)
    except Exception as exc:  # DatasetError / parse failures
        report.err(f"dataset failed to load: {exc}")
        print(f"Validation FAILED — {len(report.errors)} error(s)")
        for e in report.errors:
            print(f"  [error] {e}")
        return 1

    validate(store, report)

    print(f"Dataset OK: {store.dataset.research_target} as-of {store.dataset.as_of} "
          f"({len(store.companies)} companies, {len(store.relationships)} relationships, "
          f"{len(store.evidence)} evidence)")
    if report.errors:
        print(f"\nValidation FAILED — {len(report.errors)} error(s):")
        for e in report.errors:
            print(f"  [error] {e}")
    if report.warnings:
        tag = "warning" if not args.strict else "error"
        print(f"\n{len(report.warnings)} {tag}(s):")
        for w in report.warnings:
            print(f"  [{tag}] {w}")

    failed = bool(report.errors) or (args.strict and bool(report.warnings))
    print(f"\nResult: {'VALID' if not failed else 'INVALID'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
