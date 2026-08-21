"""Data store layer: loads the JSON dataset snapshot and provides queries.

The store is company-agnostic — it reads a `Dataset` document from disk
and exposes filtering / pagination helpers. Data ships as committed JSON
fixtures so reviewers never need to re-fetch restricted sources.

Dataset layout (all under SCR_DATA_DIR, default ./data):
  dataset.json        -> {"schema_version": "1.0", "as_of": "2026-08-21", "research_target": "nvidia"}
  companies.json      -> JSON array of Company objects
  relationships.json  -> JSON array of Relationship objects
  evidence.json       -> JSON array of Evidence objects
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Optional

from .models import (
    Company,
    Dataset,
    Evidence,
    Relationship,
)


class DatasetError(Exception):
    """Raised when the dataset cannot be loaded or is inconsistent."""


class PaginatedResult:
    """Simple pagination wrapper."""

    def __init__(self, items: list, page: int, page_size: int, total: int):
        self.items = items
        self.page = page
        self.page_size = page_size
        self.total = total

    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    def to_dict(self) -> dict:
        return {
            "items": [i.model_dump(mode="json") for i in self.items],
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "total_pages": self.total_pages,
            "has_next": self.has_next,
            "has_previous": self.has_previous,
        }


class Store:
    """In-memory store over the committed dataset snapshot."""

    def __init__(self, dataset: Dataset):
        self.dataset = dataset
        self.companies: dict[str, Company] = {c.id: c for c in dataset.companies}
        self.relationships: list[Relationship] = dataset.relationships
        self.evidence: dict[str, Evidence] = {e.id: e for e in dataset.evidence}
        self._validate_referential_integrity()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, data_dir: Optional[str] = None) -> "Store":
        """Load the dataset from a directory containing dataset.json,
        companies.json, relationships.json and evidence.json."""
        data_dir = data_dir or os.environ.get("SCR_DATA_DIR", "./data")
        data_path = Path(data_dir)

        meta_path = data_path / "dataset.json"
        companies_path = data_path / "companies.json"
        relationships_path = data_path / "relationships.json"
        evidence_path = data_path / "evidence.json"

        missing = [
            p.name
            for p in (meta_path, companies_path, relationships_path, evidence_path)
            if not p.exists()
        ]
        if missing:
            raise DatasetError(
                f"Dataset files missing in {data_path}: {', '.join(missing)}. "
                "See the update flow in README.md (scripts/fetch_edgar.py -> "
                "scripts/extract_company_mentions.py -> scripts/sync_scores.py) "
                "or point SCR_DATA_DIR at a valid dataset."
            )

        try:
            meta = _read_object(meta_path)
            companies = [Company.model_validate(c) for c in _read_json(companies_path)]
            relationships = [Relationship.model_validate(r) for r in _read_json(relationships_path)]
            evidence = [Evidence.model_validate(e) for e in _read_json(evidence_path)]
        except Exception as exc:  # pragma: no cover - defensive
            raise DatasetError(f"Dataset parse/validation failed: {exc}") from exc

        dataset = Dataset(
            schema_version=meta.get("schema_version", "1.0"),
            as_of=_parse_date(meta.get("as_of")),
            research_target=meta.get("research_target", ""),
            companies=companies,
            relationships=relationships,
            evidence=evidence,
        )
        if not dataset.research_target:
            raise DatasetError("dataset.json must declare 'research_target'")
        return cls(dataset)

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------

    def _validate_referential_integrity(self) -> None:
        errors: list[str] = []
        for rel in self.relationships:
            if rel.source_company_id not in self.companies:
                errors.append(f"relationship {rel.id}: unknown source company '{rel.source_company_id}'")
            if rel.target_company_id not in self.companies:
                errors.append(f"relationship {rel.id}: unknown target company '{rel.target_company_id}'")
            for eid in rel.evidence_ids:
                if eid not in self.evidence:
                    errors.append(f"relationship {rel.id}: unknown evidence '{eid}'")
        known_rel_ids = {r.id for r in self.relationships}
        for ev in self.evidence.values():
            if ev.relationship_id not in known_rel_ids:
                errors.append(f"evidence {ev.id}: unknown relationship '{ev.relationship_id}'")
        if errors:
            raise DatasetError("Referential integrity check failed:\n  - " + "\n  - ".join(errors[:20]))

    # ------------------------------------------------------------------
    # Company queries
    # ------------------------------------------------------------------

    def get_company(self, company_id: str) -> Optional[Company]:
        return self.companies.get(company_id)

    def list_companies(
        self,
        name: Optional[str] = None,
        entity_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResult:
        items = list(self.companies.values())
        if name:
            needle = name.lower()
            items = [c for c in items if needle in c.name.lower() or needle in c.id]
        if entity_type:
            items = [c for c in items if c.entity_type.value == entity_type]
        items.sort(key=lambda c: c.id)
        return _paginate(items, page, page_size)

    # ------------------------------------------------------------------
    # Relationship queries
    # ------------------------------------------------------------------

    def get_relationship(self, relationship_id: str) -> Optional[Relationship]:
        for rel in self.relationships:
            if rel.id == relationship_id:
                return rel
        return None

    def list_relationships(
        self,
        company_id: Optional[str] = None,
        relationship_type: Optional[str] = None,
        min_confidence: Optional[int] = None,
        max_confidence: Optional[int] = None,
        status: Optional[str] = None,
        valid_as_of: Optional[date] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResult:
        items = self.relationships
        if company_id:
            items = [
                r for r in items
                if r.source_company_id == company_id or r.target_company_id == company_id
            ]
        if relationship_type:
            items = [r for r in items if r.type.value == relationship_type]
        if min_confidence is not None:
            items = [r for r in items if r.confidence_score >= min_confidence]
        if max_confidence is not None:
            items = [r for r in items if r.confidence_score <= max_confidence]
        if status:
            items = [r for r in items if r.status.value == status]
        if valid_as_of is not None:
            items = [
                r for r in items
                if (r.valid_from is None or r.valid_from <= valid_as_of)
                and (r.valid_until is None or r.valid_until >= valid_as_of)
            ]
        items.sort(key=lambda r: (-r.confidence_score, r.id))
        return _paginate(items, page, page_size)

    # ------------------------------------------------------------------
    # Evidence queries
    # ------------------------------------------------------------------

    def get_evidence(self, evidence_id: str) -> Optional[Evidence]:
        return self.evidence.get(evidence_id)

    def list_evidence_for_relationship(self, relationship_id: str) -> list[Evidence]:
        rel = self.get_relationship(relationship_id)
        if rel is None:
            return []
        return [self.evidence[eid] for eid in rel.evidence_ids if eid in self.evidence]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "companies": len(self.companies),
            "relationships": len(self.relationships),
            "evidence": len(self.evidence),
            "as_of": self.dataset.as_of.isoformat(),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> list:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return data
    raise DatasetError(f"{path} must contain a JSON array")


def _read_object(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        return data
    raise DatasetError(f"{path} must contain a JSON object")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DatasetError(f"invalid date in dataset.json: {value!r}") from exc


def _paginate(items: list, page: int, page_size: int) -> PaginatedResult:
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return PaginatedResult(items[start:end], page, page_size, total)
