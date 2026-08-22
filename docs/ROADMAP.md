# Product Roadmap

> Status: **planned** — these are the two highest-leverage evolution tracks
> identified in the post-V2 review. Each track is scoped so it can land
> independently without destabilizing the current scoring/data pipeline.

This roadmap captures the two structural expansions called out by the
acceptance review:

1. **Asia-Pacific exchange disclosure ingestion** — broadening the
   evidence base beyond SEC EDGAR to the primary exchanges of the
   research targets (SSE STAR Market, HKEX, TWSE, TSE).
2. **Temporal graph replay** — extending the static snapshot model
   into a time-aware graph that can reconstruct the partnership network
   at an arbitrary historical date.

Both tracks preserve the existing system's core invariants:
code-determined scoring, paragraph-level evidence provenance, and the
multi-target registry architecture.

---

## Track A — Asia-Pacific Exchange Disclosure Ingestion

### Motivation

The current pipeline sources ~95% of its evidence from SEC EDGAR
(English-language filings). For research targets listed on Asia-Pacific
exchanges (e.g. Unitree on SSE STAR Market, UBTech on HKEX), the
authoritative primary source is the exchange's own disclosure system —
not EDGAR. Without these sources the pipeline:

- misses the highest-authority evidence available for APAC targets,
- cannot validate relationships that are only disclosed in the home-
  exchange filings (quarterly reports, equity change announcements,
   related-party transaction disclosures).

### Goals

- Ingest filings from at least **two** APAC exchange disclosure portals
  in the initial cut: **SSE STAR Market** (688xxx tickers, via the
  SSE e-interaction / disclosure platform) and **HKEX** (via the
  HKEXnews search API).
- Emit evidence records with `source_type = exchange_filing` (already
  in the `SourceType` enum and scored at authority tier 25), so no
  scoring-engine change is required.
- Preserve the compliance posture: rate-limited (≤10 req/s), descriptive
  User-Agent, robots.txt gate (already in `scripts/robots_check.py`).

### Non-goals (this cut)

- Real-time streaming ingestion. The system remains snapshot-based.
- Full-text search across all APAC exchanges. Initial cut targets the
  two exchanges above; others (TWSE, TSE, KRX) are deferred.

### Proposed architecture

```
scripts/fetch_exchange.py        ← new: per-exchange fetch adapters
   ├─ SSEAdapter   (SSE STAR Market disclosure platform)
   └─ HKEXAdapter  (HKEXnews search API)
scripts/robots_check.py          ← existing: compliance gate
data/targets/<id>/raw_exchange/  ← new: raw fetched filings
```

Each adapter implements a minimal interface:

```python
class ExchangeAdapter(Protocol):
    exchange: str                       # "SSE" / "HKEX" / ...
    def fetch(self, ticker: str, since: date) -> list[Filing]: ...
    def to_evidence(self, filing: Filing, rel_id: str) -> dict: ...
```

Evidence records emitted by adapters follow the **same staging +
agent-review + merge** pipeline already used for EDGAR evidence
(`docs/research_agent_protocol.md`). This means the adapter only needs to
produce raw filings + a staging skeleton; the existing review/merge
tooling handles the rest.

### Deliverables & acceptance criteria

| # | Deliverable | Acceptance criterion |
|---|---|---|
| A1 | `scripts/fetch_exchange.py` with SSE + HKEX adapters | `python scripts/fetch_exchange.py --adapter sse --ticker 688836` fetches ≥1 filing and writes it under `data/targets/<id>/raw_exchange/` |
| A2 | Adapter output conforms to staging schema | `python scripts/research_harvest.py --check <staging.json>` exits 0 |
| A3 | At least 3 APAC-sourced evidence records merged into the Unitree dataset | `python scripts/validate_data.py --data data/targets/unitree` reports 0 errors and the new evidence passes the content_hash anti-tamper check |
| A4 | README §3 updated with the new exchange adapters and their rate limits | doc review |

### Risks & mitigations

| Risk | Mitigation |
|---|---|
| APAC disclosure portals may lack a clean API; HTML scraping is fragile | Adapter isolates parsing; fallback to manual `--input` JSONL backend already exists |
| Non-English filings complicate the LLM extraction step | The protocol already isolates LLM extraction in a staging layer; we restrict LLM extraction to filings where the adapter has produced a clean text layer |
| Rate-limit / robots differences per exchange | `scripts/robots_check.py` already caches per host; per-adapter rate limits documented in adapter docstring |

### Sequencing

1. Build SSEAdapter against the SSE STAR disclosure platform (A1).
2. Build HKEXAdapter against HKEXnews (A1).
3. Run both against the Unitree target, stage, agent-review, merge (A2–A3).
4. Update README §3 and `docs/research_agent_protocol.md` with the
   exchange-adapter section (A4).

---

## Track B — Temporal Graph Replay

### Motivation

The current model records `valid_from` / `valid_until` on each
relationship, but the graph is queried only as a *present snapshot*.
Reviewers cannot ask "what did NVIDIA's supplier network look like on
2022-01-01?" and get a deterministically-reconstructed subgraph. This
matters because:

- supply-chain relationships are inherently time-bounded (contracts
  expire, second-sourcing changes the topology);
- the recency score dimension already reasons about `valid_until`, but
  the graph API (`GET /api/v1/graph`) does not expose time slicing;
- a temporal view is the natural substrate for *supplier dependency
  concentration* risk metrics over time (deliverable #6's
  `supplier_dependency_concentration`).

### Goals

- `GET /api/v1/graph?as_of=YYYY-MM-DD` returns the subgraph of
  relationships whose `[valid_from, valid_until]` interval contains
  `as_of` (with `valid_until = null` treated as "still active").
- Two new graph-level risk metrics on the `/api/v1/graph` response:
  - **`degree_centrality`** — per-node in-degree / out-degree, the
    standard topology concentration measure;
  - **`supplier_dependency_concentration`** — the Hirschman-Herfindahl
    index (HHI) over the target's inbound supplier edges, quantifying
    how concentrated supply is in one counterparty (0 = perfectly
    diversified, 10000 = single supplier).
- The metrics are computed in pure Python from the graph edges (no LLM,
  no external service), consistent with the anti-hallucination posture.

### Non-goals (this cut)

- No persistent storage of historical snapshots. The replay is computed
  on demand from `valid_from` / `valid_until`.
- No visualization of the time-sliced graph in the dashboard; the API
  exposes the data, the dashboard can consume it later.

### Proposed architecture

```
src/graph.py                    ← new: temporal slice + metrics
   ├─ slice_graph(rels, as_of) -> list[Relationship]
   ├─ degree_centrality(rels, companies) -> dict[node_id, dict]
   └─ supplier_dependency_concentration(rels, target_id) -> float
src/api.py  /api/v1/graph       ← extended: as_of param + metrics
src/cli.py  graph               ← extended: --as-of + risk metrics
```

The metrics functions are pure and unit-tested in
`tests/test_graph.py` (new), mirroring the pattern used for
`tests/test_scoring.py`.

### Deliverables & acceptance criteria

| # | Deliverable | Acceptance criterion |
|---|---|---|
| B1 | `src/graph.py` with `slice_graph`, `degree_centrality`, `supplier_dependency_concentration` | All three functions have unit tests in `tests/test_graph.py` and pass |
| B2 | `GET /api/v1/graph?as_of=2024-01-01` returns the time-sliced subgraph | API test asserts the response excludes relationships whose validity window does not contain `as_of` |
| B3 | `GET /api/v1/graph` response includes `risk_metrics` with `degree_centrality` and `supplier_dependency_concentration` | API test asserts presence and numeric validity (HHI in `[0, 10000]`) |
| B4 | CLI `graph --as-of 2024-01-01 --json` mirrors the API response | CLI test asserts JSON shape parity with the API |

### Risks & mitigations

| Risk | Mitigation |
|---|---|
| `valid_from` / `valid_until` are sparse in the current dataset (many nulls) | The slice semantics treat `valid_until = null` as "active from `valid_from` onward"; relationships with both null are always included (consistent with the recency engine's `still_valid` logic) |
| HHI over a small supplier set is noisy | The metric is documented as most meaningful when the target has ≥3 supplier edges; the API response includes the supplier count so reviewers can judge |

### Sequencing

1. Implement `slice_graph` + tests (B1, slice part).
2. Implement `degree_centrality` + `supplier_dependency_concentration` + tests (B1, metrics part).
3. Extend `/api/v1/graph` with `as_of` and `risk_metrics` + API tests (B2, B3).
4. Extend CLI `graph` with `--as-of` and `risk_metrics` + CLI tests (B4).

---

## Cross-cutting work

Both tracks share a dependency on the **multi-target registry**
(`data/targets.json`), which is already in place. No registry changes
are required for either track.

Track A's new evidence records will automatically flow through the
**content_hash anti-tamper** check added in this iteration, so no
additional integrity plumbing is needed.

Track B's `degree_centrality` and `supplier_dependency_concentration`
are added to `/api/v1/graph` in this iteration (deliverable #6); the
`as_of` time-slicing itself is the Track B follow-on.
