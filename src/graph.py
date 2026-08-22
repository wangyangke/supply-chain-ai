"""Graph topology metrics for the supply-chain relationship network.

All functions are *pure* and deterministic — no LLM, no external service.
This is the implementation substrate for the two risk metrics exposed on
``GET /api/v1/graph``:

- ``degree_centrality`` — per-node in-degree / out-degree, the standard
  topology concentration measure.
- ``supplier_dependency_concentration`` — the Hirschman-Herfindahl index
  (HHI) over the target's inbound *supplier* edges, quantifying how
  concentrated supply is in one counterparty
  (0 = perfectly diversified, 10000 = single supplier).

Both metrics are computed in-place from the in-memory relationship list.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

from .models import Relationship


def slice_graph(
    relationships: Iterable[Relationship],
    as_of: date,
) -> list[Relationship]:
    """Return the subgraph of relationships valid at ``as_of``.

    A relationship is *valid at* ``as_of`` when its ``[valid_from,
    valid_until]`` interval contains ``as_of``. The interval is
    half-open on the left and inclusive on the right:

    - ``valid_from`` is None -> treat as -inf (always started)
    - ``valid_until`` is None -> treat as +inf (still active)
    - a relationship with both bounds None is always included
      (consistent with the recency engine's ``still_valid`` logic)

    This is the substrate for ``GET /api/v1/graph?as_of=YYYY-MM-DD``:
    the caller asks "what did the network look like on this date?"
    and gets a deterministically-reconstructed subgraph.
    """
    result: list[Relationship] = []
    for r in relationships:
        started = r.valid_from is None or r.valid_from <= as_of
        not_ended = r.valid_until is None or r.valid_until >= as_of
        if started and not_ended:
            result.append(r)
    return result


def degree_centrality(
    relationships: Iterable[Relationship],
) -> dict[str, dict[str, int]]:
    """Return per-node ``{in_degree, out_degree, total_degree}`` counts.

    An edge ``source -> target`` contributes +1 out-degree to ``source``
    and +1 in-degree to ``target``.
    """
    in_deg: dict[str, int] = {}
    out_deg: dict[str, int] = {}
    for r in relationships:
        out_deg[r.source_company_id] = out_deg.get(r.source_company_id, 0) + 1
        in_deg[r.target_company_id] = in_deg.get(r.target_company_id, 0) + 1

    nodes: dict[str, dict[str, int]] = {}
    for cid in set(in_deg) | set(out_deg):
        i = in_deg.get(cid, 0)
        o = out_deg.get(cid, 0)
        nodes[cid] = {
            "in_degree": i,
            "out_degree": o,
            "total_degree": i + o,
        }
    return nodes


def supplier_dependency_concentration(
    relationships: Iterable[Relationship],
    target_id: str,
) -> float:
    """HHI over the target's inbound supplier edges.

    Filters the relationship list to edges of type ``supplier`` whose
    *target* is ``target_id`` (i.e. the companies that supply the target),
    then computes the HHI over the set of distinct supplier source nodes.

    HHI = sum(market_share_i^2) * 10000, where market share is computed
    by counting each distinct supplier as one unit (equal weights). With
    ``n`` equal suppliers, HHI = 10000 / n.

    - 0 suppliers -> 0.0 (no supplier dependency to measure)
    - 1 supplier  -> 10000.0 (single-source, maximum concentration)
    - 3 suppliers -> ~3333.3
    """
    suppliers: set[str] = set()
    for r in relationships:
        if r.type.value == "supplier" and r.target_company_id == target_id:
            suppliers.add(r.source_company_id)
    n = len(suppliers)
    if n == 0:
        return 0.0
    share = 1.0 / n
    return round(share * share * n * 10000.0, 2)
