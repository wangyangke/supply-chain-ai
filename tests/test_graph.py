"""Tests for graph topology metrics and temporal slicing.

All functions under test are pure and deterministic.
"""

from datetime import date

from src.graph import (
    degree_centrality,
    slice_graph,
    supplier_dependency_concentration,
)
from src.models import Relationship, RelationshipStatus, RelationshipType


AS_OF = date(2026, 8, 23)


def make_rel(**kw) -> Relationship:
    defaults = dict(
        id="rel_x",
        source_company_id="nvidia",
        target_company_id="acme",
        type=RelationshipType.SUPPLIER,
        direction="nvidia -> acme",
        status=RelationshipStatus.CONFIRMED,
        confidence_score=80,
        evidence_ids=[],
        summary="",
    )
    defaults.update(kw)
    return Relationship(**defaults)


# ---------------------------------------------------------------------------
# slice_graph
# ---------------------------------------------------------------------------

class TestSliceGraph:
    def test_both_bounds_none_always_included(self):
        rels = [make_rel(valid_from=None, valid_until=None)]
        assert slice_graph(rels, date(2020, 1, 1)) == rels
        assert slice_graph(rels, date(2030, 1, 1)) == rels

    def test_active_ongoing_included(self):
        rels = [make_rel(valid_from=date(2020, 1, 1), valid_until=None)]
        assert slice_graph(rels, date(2024, 6, 30)) == rels

    def test_terminated_before_as_of_excluded(self):
        rels = [make_rel(valid_from=date(2020, 1, 1), valid_until=date(2023, 1, 1))]
        assert slice_graph(rels, date(2024, 6, 30)) == []

    def test_not_yet_started_excluded(self):
        rels = [make_rel(valid_from=date(2027, 1, 1), valid_until=None)]
        assert slice_graph(rels, AS_OF) == []

    def test_within_bounded_window_included(self):
        rels = [make_rel(valid_from=date(2020, 1, 1), valid_until=date(2025, 12, 31))]
        assert slice_graph(rels, date(2024, 6, 30)) == rels

    def test_mixed_relationships(self):
        rels = [
            make_rel(id="r1", valid_from=date(2020, 1, 1), valid_until=None),
            make_rel(id="r2", valid_from=date(2020, 1, 1), valid_until=date(2023, 1, 1)),
            make_rel(id="r3", valid_from=date(2027, 1, 1), valid_until=None),
        ]
        sliced = slice_graph(rels, date(2024, 6, 30))
        sliced_ids = {r.id for r in sliced}
        assert sliced_ids == {"r1"}

    def test_valid_until_exactly_as_of_included(self):
        rels = [make_rel(valid_from=date(2020, 1, 1), valid_until=date(2024, 6, 30))]
        assert slice_graph(rels, date(2024, 6, 30)) == rels


# ---------------------------------------------------------------------------
# degree_centrality
# ---------------------------------------------------------------------------

class TestDegreeCentrality:
    def test_empty_relationships(self):
        assert degree_centrality([]) == {}

    def test_single_edge(self):
        rels = [make_rel(source_company_id="a", target_company_id="b")]
        deg = degree_centrality(rels)
        assert deg == {
            "a": {"in_degree": 0, "out_degree": 1, "total_degree": 1},
            "b": {"in_degree": 1, "out_degree": 0, "total_degree": 1},
        }

    def test_multiple_edges_hub_node(self):
        rels = [
            make_rel(id=f"r{i}", source_company_id=f"s{i}", target_company_id="nvidia")
            for i in range(3)
        ]
        deg = degree_centrality(rels)
        assert deg["nvidia"]["in_degree"] == 3
        assert deg["nvidia"]["out_degree"] == 0
        assert deg["nvidia"]["total_degree"] == 3


# ---------------------------------------------------------------------------
# supplier_dependency_concentration
# ---------------------------------------------------------------------------

class TestSupplierDependencyConcentration:
    def test_no_suppliers(self):
        assert supplier_dependency_concentration([], "nvidia") == 0.0

    def test_single_supplier_max_concentration(self):
        rels = [make_rel(source_company_id="s1", target_company_id="nvidia")]
        assert supplier_dependency_concentration(rels, "nvidia") == 10000.0

    def test_three_equal_suppliers(self):
        rels = [
            make_rel(id=f"r{i}", source_company_id=f"s{i}", target_company_id="nvidia")
            for i in range(3)
        ]
        assert supplier_dependency_concentration(rels, "nvidia") == 3333.33

    def test_only_supplier_edges_counted(self):
        rels = [
            make_rel(id="r0", source_company_id="s1", target_company_id="nvidia",
                     type=RelationshipType.CUSTOMER),
            make_rel(id="r1", source_company_id="s2", target_company_id="nvidia",
                     type=RelationshipType.SUPPLIER),
        ]
        assert supplier_dependency_concentration(rels, "nvidia") == 10000.0

    def test_target_id_filter(self):
        rels = [
            make_rel(id="r0", source_company_id="s1", target_company_id="nvidia",
                     type=RelationshipType.SUPPLIER),
            make_rel(id="r1", source_company_id="s2", target_company_id="amd",
                     type=RelationshipType.SUPPLIER),
        ]
        assert supplier_dependency_concentration(rels, "nvidia") == 10000.0
        assert supplier_dependency_concentration(rels, "amd") == 10000.0
