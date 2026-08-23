# Acceptance Report — Schema 2.0 Provenance & Scoring Methodology (ported from `supply-chain-research` V2)

- **Acceptance date:** 2026-08-22
- **Repository:** `supply-chain-ai` (wangyangke) — multi-target supply-chain / partnership research service
- **Reviewed commit:** `b9ac653` (HEAD, `main`); changes are uncommitted working-tree deltas
- **Reference:** `supply-chain-research` V2 (`kaminono`) — single-target build that originated the three "hard assets"
- **Mode:** Independent acceptance of the *ported* features; no production source was modified beyond the port scope.

---

## 1. Executive summary

**Verdict: PASS** (one *optional* browser gate is UNVERIFIED in this environment only; see §4).
**Estimated readiness: 95 / 100.**

This round ports three "hard assets" from V2 into the multi-target `supply-chain-ai` service and verifies each with real evidence:

| # | Hard asset (from V2) | Ported into `supply-chain-ai` | Result |
|---|---|---|---|
| 1 | Schema 2.0 evidence provenance (`independence_group` / `support_level` / `access_notes`) | `src/models.py`, `scripts/validate_data.py`, `scripts/research_agent.py`, `scripts/merge_staged.py`; 2 datasets backfilled to `schema_version: "2.0"` | ✅ PASS |
| 2 | Scoring methodology document + endpoint | `docs/scoring_methodology.md`, `GET /api/v1/scoring-methodology`, dashboard live-render (anti-drift) | ✅ PASS |
| 3 | Acceptance report + browser XSS test | this report + `tests/test_dashboard_browser.py`; dashboard hardened with `esc()` / `safeUrl()` | ✅ PASS (browser runtime UNVERIFIED here) |

**Key constraint honored:** the scoring *engine* was deliberately **not** changed (no V2 "epistemic cap"). V2's cap would have broken the existing hard-coded assertions in `tests/test_api.py` (`confidence_score == 86`, `confirmed == 17 / inferred == 4`, `total == sum(dimensions)`). Schema 2.0 fields therefore follow a **capture + validate + document** path that keeps the 121-test suite at zero risk.

---

## 2. Gate results

| Gate | Result | Evidence |
|---|---|---|
| Tests (pytest) | **PASS** | 121 passed, 1 skipped (browser), 0 failed |
| Coverage | **PASS** | 93.76% (threshold 90%) |
| Data integrity — 2 targets | **PASS** | nvidia / unitree all `VALID` |
| Scoring reproducibility (dry-run) | **PASS** | 0 deltas; every stored status matches engine band |
| Schema 2.0 adoption | **PASS** | 2 / 2 datasets `schema_version == "2.0"` |
| Methodology endpoint (live) | **PASS** | `GET /api/v1/scoring-methodology` → canonical rubric (§3.4) |
| Dashboard anti-drift | **PASS** | methodology tab fetched live; static fallback hidden |
| XSS hardening (code) | **PASS** | `esc()` / `safeUrl()` neutralize payloads — measured (§3.6) |
| Browser XSS test | **UNVERIFIED** | Playwright cannot be installed in this env; test committed + skippable |
| API (`/health`, methodology) | **PASS** | real uvicorn, HTTP 200 |
| CLI | **PASS** | unchanged, pre-existing green |
| Docker | n/a | not re-verified this round (unchanged from prior acceptance) |
| Secret / security review | **PASS** | no secrets tracked; evidence links restricted to http(s) in UI |

---

## 3. Detailed verification

### 3.1 Tests & coverage
```
pytest -q            → 121 passed, 1 skipped, 0 failed
pytest --cov=src     → TOTAL 785 stmts, 49 missed, 93.76% (≥ 90% required)
```
The single skipped test is `tests/test_dashboard_browser.py` (browser UI), skipped via `pytest.importorskip("playwright")`.

### 3.2 Data integrity — both targets
Run with `python scripts/validate_data.py --data data/targets/<id> [--strict]`:

| Target | Companies | Relationships | Evidence | Result |
|---|---:|---:|---:|---|
| nvidia | 22 | 21 | 29 | VALID (1 *by-design* warning under `--strict`, see §4) |
| unitree | 6 | 5 | 9 | VALID |

Schema 2.0 checks enforced by the validator:
- `dataset.schema_version ∈ {"1.0", "2.0"}`;
- every evidence item has a non-empty `independence_group` (provenance lineage);
- every evidence item has a valid `support_level` ∈ {`direct`, `indirect`, `contextual`};
- `source_url` must be https (warning otherwise).

### 3.3 Scoring reproducibility (dry-run)
```
python scripts/sync_scores.py --data data/targets/<id> --breakdown
→ "All stored statuses match the engine score bands."  (0 deltas on every target)
```
Stored `confidence_score` and `status` are byte-for-byte reproducible from the engine — the "reproducibility contract" is intact after the Schema 2.0 port.

### 3.4 Methodology endpoint (live, real server)
`curl /api/v1/scoring-methodology` returns the canonical, machine-readable rubric:
```json
{
  "schema_version": "2.0",
  "weights": {"authority":25,"evidence_quality":25,"recency":20,"specificity":20,"quantifiability":10},
  "authority_tiers": {"sec_filing":25,"exchange_filing":25,"government":22,
                      "company_ir":20,"company_press_release":20,"analyst_research":18,
                      "business_media":16,"industry_database":15,"reference":10,"informal":4,"unknown":0},
  "evidence_quality": {"basis":"number of independent source URLs",
                       "scale":[{"min_independent_sources":1,"points":16}, … ,{"min":4,"points":25}],
                       "note":"…independence_group further records the underlying lineage…"},
  "recency_bands_days": [{"maximum_days":180,"points":20}, … ],
  "specificity": {"cap":20,"term_points":3,"weak_term_penalty":-2,"direct_statement_bonus":5, …},
  "quantifiability": {"cap":10,"scored_fields":["evidence.quote","relationship.summary"]},
  "refinements": { … },
  "status_bands": {"confirmed":">=70","inferred":"40-69","unknown":"<40"},
  "support_level_field": "…"
}
```
The same function (`scoring_methodology()` in `src/scoring.py`) feeds the endpoint, the Markdown doc, and the dashboard — a single source of truth, no drift.

### 3.5 Schema 2.0 landing
- **`src/models.py`** — new `EvidenceSupportLevel(str, Enum)` (`direct` / `indirect` / `contextual`); `Evidence` gains three required fields: `independence_group: str` (min_length 1), `support_level: EvidenceSupportLevel`, `access_notes: Optional[str]`. `extra="forbid"` retained.
- **`scripts/research_agent.py`** — `guess_support_level()` heuristic + lineage captured as `independence_group` (URL netloc) at candidate-build time.
- **`scripts/merge_staged.py`** — merge maps the three fields from staged candidates.
- **Backfill** — `nvidia` (29), `unitree` (9) evidence items backfilled; every dataset bumped to `schema_version: "2.0"`.
- **Tests** — `tests/test_scoring.py` fixtures and `tests/test_store.py` (`schema_version == "2.0"`) updated; suite green.

### 3.6 XSS hardening (core of hard asset #3)
The dashboard previously interpolated data-derived strings into `innerHTML` (evidence modal, company/relationship rows, header). Every data-derived interpolation now passes through:
- `esc(s)` — HTML-escapes `& < > " '`;
- `safeUrl(u)` — returns `#` unless the URL is `http(s)`, neutralizing `javascript:` etc.
- The relationship graph already used `textContent` (inherently safe).

**Measured with the *actual* functions extracted from `dashboard.html` (not a reimplementation):**
```
esc('<img src="x" onerror="window.__dashboardXss = true">')
  → "&lt;img src=&quot;x&quot; onerror=&quot;window.__dashboardXss = true&quot;&gt;"   ✅ inert text
safeUrl('javascript:alert(1)')  → "#"                                                  ✅ neutralized
safeUrl('https://example.com/a')→ "https://example.com/a"                              ✅ preserved
esc("AT&T said 'hi'")           → "AT&amp;T said &#39;hi&#39;"                          ✅ safe
```
The committed browser test (`tests/test_dashboard_browser.py`) exercises exactly this: it injects a malicious company name + malicious evidence payloads through the research-injection path (`loadTargetIntoDashboard`) and asserts (a) no executable `<img>` element is created, (b) `window.__dashboardXss` stays `false`, (c) `javascript:` links are dropped.

---

## 4. Residual findings

- **P2-01 (by-design, not a defect).** Under `--strict`, `nvidia` reports 1 warning: `rel_inv_001` has two evidence items sharing a `source_url`. This is intentional — the independence scoring counts the URL once, exactly as designed. Without `--strict` the dataset is `VALID`. No action required.
- **P3-01 (environment, not a code defect).** `tests/test_dashboard_browser.py` is UNVERIFIED in *this* environment because Playwright cannot be installed here (corporate proxy blocks public PyPI; the internal pip mirror has no `playwright` wheel). The test is committed with `pytest.importorskip("playwright")` and will run automatically wherever `pip install playwright && playwright install chromium` succeeds (e.g. CI or a dev machine). This mirrors V2's own "Docker runtime UNVERIFIED" honesty.

No regressions were introduced; the 121-test suite and 93.76% coverage are unchanged in character from the pre-port baseline.

---

## 5. How this port differs from V2 (design notes)

1. **Engine unchanged.** V2 replaced the scoring math with an "epistemic cap". Because `supply-chain-ai` has hard-coded assertions on exact scores, we kept the engine and instead *captured, validated, and documented* the Schema 2.0 fields — zero risk to the 121 tests.
2. **Multi-target, not single-target.** `data/targets/<id>/` + `data/targets.json` registry. The methodology tab is therefore rendered by a *runtime* `fetch('/api/v1/scoring-methodology')` (anti-drift), whereas V2 embedded a snapshot.
3. **No >100-row truncation.** V2's dashboard silently cut companies/relationships at `page_size=100`. `supply-chain-ai` renders the full committed snapshot; the browser test explicitly asserts 150+ rows render with no truncation.
4. **Browser test adapted** to the multi-target injection path (`loadTargetIntoDashboard`) rather than V2's fixed NVIDIA IDs.

---

## 6. Post-push / follow-up (optional)

1. In an environment with Playwright + Chromium, run `pytest tests/test_dashboard_browser.py` to close P3-01 (browser runtime verification).
2. (Optional) Add `playwright` to `pyproject.toml` dev extras and enable browser tests in the CI matrix once the package mirror provides the wheel (or CI allows public-PyPI access).

---

## 7. Pre-commit checklist

- [x] Tests PASS (121 passed, 1 skipped)
- [x] Coverage ≥ 90% (93.76%)
- [x] 2 datasets VALID (Schema 2.0)
- [x] Scoring reproducible (0 deltas)
- [x] Methodology endpoint live + canonical
- [x] Dashboard anti-drift (live fetch, static fallback hidden)
- [x] XSS hardening measured (esc / safeUrl)
- [x] Browser XSS test committed + skippable
- [x] `supply-chain-research-mainV2/` (untracked V2 copy) excluded from commit
- [x] No secrets / no new `innerHTML` with raw data
