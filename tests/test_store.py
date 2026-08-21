"""Store layer tests: loading, referential integrity, filtering, pagination."""

import json
import shutil
from datetime import date

import pytest

from src.store import DatasetError, Store


# ---------------------------------------------------------------------------
# Loading & integrity
# ---------------------------------------------------------------------------

class TestLoading:
    def test_load_real_dataset(self, store):
        assert len(store.companies) == 21
        assert len(store.relationships) == 20
        assert len(store.evidence) == 26

    def test_dataset_metadata(self, dataset):
        assert dataset.schema_version == "1.0"
        assert dataset.research_target == "nvidia"
        assert dataset.as_of.isoformat() == "2026-08-21"

    def test_research_target_present(self, store):
        target = store.get_company(store.dataset.research_target)
        assert target is not None
        assert target.entity_type.value == "target"

    def test_missing_files_raise(self, tmp_path):
        try:
            Store.load(str(tmp_path))
        except DatasetError as exc:
            assert "Dataset files missing" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("expected DatasetError")

    def test_bad_referential_integrity_raises(self, tmp_path, data_dir):
        # Copy the dataset and break one relationship's evidence reference.
        for name in ("dataset.json", "companies.json", "relationships.json", "evidence.json"):
            shutil.copy(data_dir / name, tmp_path / name)
        rels = json.loads((tmp_path / "relationships.json").read_text())
        rels[0]["evidence_ids"] = ["ev_does_not_exist"]
        (tmp_path / "relationships.json").write_text(json.dumps(rels, ensure_ascii=False))

        with pytest.raises(DatasetError):
            Store.load(str(tmp_path))


# ---------------------------------------------------------------------------
# Company queries
# ---------------------------------------------------------------------------

class TestCompanies:
    def test_get_company(self, store):
        nv = store.get_company("nvidia")
        assert nv.name == "NVIDIA Corporation"
        assert nv.stock_code == "NVDA"
        assert nv.entity_type.value == "target"
        assert store.get_company("nope") is None

    def test_list_all(self, store):
        result = store.list_companies(page_size=100)
        assert result.total == 21
        assert result.total_pages == 1
        assert not result.has_next

    def test_filter_by_name(self, store):
        result = store.list_companies(name="micro")
        ids = {c.id for c in result.items}
        assert "micron" in ids
        assert "microsoft" in ids  # 'micro' matches both names

    def test_filter_by_entity_type(self, store):
        result = store.list_companies(entity_type="target")
        assert [c.id for c in result.items] == ["nvidia"]

    def test_pagination(self, store):
        page1 = store.list_companies(page=1, page_size=10)
        assert len(page1.items) == 10
        assert page1.total == 21
        assert page1.total_pages == 3
        assert page1.has_next and not page1.has_previous

        page3 = store.list_companies(page=3, page_size=10)
        assert len(page3.items) == 1
        assert not page3.has_next and page3.has_previous


# ---------------------------------------------------------------------------
# Relationship queries
# ---------------------------------------------------------------------------

class TestRelationships:
    def test_list_all(self, store):
        result = store.list_relationships(page_size=100)
        assert result.total == 20

    def test_default_sorted_by_score_desc(self, store):
        result = store.list_relationships(page_size=100)
        scores = [r.confidence_score for r in result.items]
        assert scores == sorted(scores, reverse=True)

    def test_filter_by_type(self, store):
        result = store.list_relationships(relationship_type="supplier", page_size=100)
        assert result.total == 4
        assert all(r.type.value == "supplier" for r in result.items)
        assert result.items[0].id == "rel_sup_001"  # highest-scoring supplier

    def test_filter_by_company(self, store):
        result = store.list_relationships(company_id="coreweave")
        assert result.total == 1
        assert result.items[0].id == "rel_inv_001"

    def test_filter_by_status(self, store):
        result = store.list_relationships(status="inferred", page_size=100)
        assert result.total == 4
        assert all(r.status.value == "inferred" for r in result.items)
        assert {r.id for r in result.items} == {
            "rel_sup_004", "rel_cus_002", "rel_cus_003", "rel_cus_004",
        }

    def test_filter_by_confidence_range(self, store):
        result = store.list_relationships(min_confidence=70, max_confidence=79, page_size=100)
        expected = [
            r for r in store.relationships if 70 <= r.confidence_score <= 79
        ]
        assert result.total == len(expected)
        assert all(70 <= r.confidence_score <= 79 for r in result.items)
        # The 80+ relationships must be excluded.
        ids = {r.id for r in result.items}
        assert "rel_sup_001" not in ids
        assert "rel_inv_001" not in ids

    def test_filter_by_valid_as_of_excludes_future_relationship(self, store):
        # Cisco partnership began 2025-02-01 → not valid on 2024-06-30.
        result = store.list_relationships(valid_as_of=date(2024, 6, 30), page_size=100)
        assert result.total == 19
        assert "rel_par_004" not in {r.id for r in result.items}
        # SoundHound investment (2017-01-01 -> 2025-02-14) WAS valid then.
        assert "rel_inv_003" in {r.id for r in result.items}

    def test_valid_as_of_includes_active(self, store):
        result = store.list_relationships(valid_as_of=date(2026, 1, 1), page_size=100)
        assert "rel_inv_001" in {r.id for r in result.items}
        assert "rel_par_004" in {r.id for r in result.items}  # began 2025-02-01

    def test_pagination(self, store):
        page1 = store.list_relationships(page=1, page_size=5)
        assert len(page1.items) == 5
        assert page1.total == 20
        assert page1.total_pages == 4
        page4 = store.list_relationships(page=4, page_size=5)
        assert len(page4.items) == 5
        assert not page4.has_next


# ---------------------------------------------------------------------------
# Evidence queries
# ---------------------------------------------------------------------------

class TestEvidence:
    def test_get_evidence(self, store):
        ev = store.get_evidence("ev_sup_001")
        assert ev is not None
        assert ev.relationship_id == "rel_sup_001"
        assert ev.access_restriction.value == "public"
        assert store.get_evidence("nope") is None

    def test_list_evidence_for_relationship(self, store):
        evs = store.list_evidence_for_relationship("rel_inv_001")
        assert len(evs) == 3
        assert all(e.relationship_id == "rel_inv_001" for e in evs)

    def test_list_evidence_unknown_relationship(self, store):
        assert store.list_evidence_for_relationship("nope") == []


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats(self, store):
        stats = store.stats()
        assert stats["companies"] == 21
        assert stats["relationships"] == 20
        assert stats["evidence"] == 26
        assert stats["as_of"] == "2026-08-21"
