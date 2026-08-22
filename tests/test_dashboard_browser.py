"""Real-browser regression coverage for the interactive Dashboard.

Guards the ported "hard assets" that touch the UI:

  * Schema 2.0 evidence provenance (``independence_group`` / ``support_level``
    / ``access_notes``) is rendered as inert TEXT, never executable HTML
    (XSS safety — the dashboard escapes every data-derived string before
    assigning ``innerHTML``);
  * the dashboard renders the WHOLE committed snapshot instead of silently
    truncating at 100 rows (unlike the V2 single-target build, which only
    fetched ``page_size=100``);
  * the Scoring Methodology tab renders LIVE from
    ``GET /api/v1/scoring-methodology`` so it can never drift from the engine.

Requires Playwright + a Chromium browser. The whole module is skipped
automatically when either is unavailable, so a plain ``pytest`` run on a
machine without a browser stays green. To enable:

    pip install playwright
    playwright install chromium
    pytest tests/test_dashboard_browser.py
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

pytest.importorskip("playwright", reason="playwright not installed; browser UI tests are optional")

from playwright.sync_api import Page, expect

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def dashboard_url():
    """Start a real uvicorn process and wait until /health responds."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Dashboard test server exited during startup")
            try:
                with urlopen(f"{url}/health", timeout=0.2) as resp:
                    if resp.status == 200:
                        break
            except (URLError, TimeoutError):
                time.sleep(0.05)
        else:
            raise RuntimeError("Dashboard test server did not become healthy")
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


XSS = '<img src="x" onerror="window.__dashboardXss = true">'


def _company(cid: str, name: str) -> dict:
    return {
        "id": cid, "name": name, "stock_code": cid.upper()[:4],
        "exchange": "NYSE", "isin": "US0000000000", "country": "US",
        "entity_type": "target" if cid == "mock" else "related",
        "sector": "Test", "description": "fixture",
    }


def _dataset(companies: dict, relationships: list) -> dict:
    return {
        "dataset": {"schema_version": "2.0", "as_of": "2026-08-21", "research_target": "mock"},
        "companies": companies,
        "relationships": relationships,
        "evidence": {},
    }


def test_dashboard_renders_schema2_evidence_safely(page: Page, dashboard_url: str):
    """Evidence provenance fields are shown as TEXT, never as executable HTML."""
    evidence_item = {
        "id": "ev_xss", "relationship_id": "rel_xss",
        "source_url": "javascript:alert(1)",  # must be neutralised to '#'
        "publisher": XSS,
        "source_type": "business_media",
        "published_at": "2026-08-01", "accessed_at": "2026-08-21",
        "evidence_locator": "xss locator", "access_restriction": "public",
        "license_note": "xss license",
        "quote": XSS,
        "independence_group": XSS,
        "access_notes": XSS,
        "support_level": "indirect",
    }
    rel = {
        "id": "rel_xss", "source_company_id": "mock", "target_company_id": "mock2",
        "type": "partner", "direction": "mock -> mock2", "status": "confirmed",
        "confidence_score": 80, "valid_from": "2024-01-01", "valid_until": None,
        "evidence_ids": ["ev_xss"], "summary": "xss summary " + XSS,
        "evidence_items": [evidence_item],
    }
    companies = {
        "mock": _company("mock", "Mock Target Inc."),
        "mock2": _company("mock2", XSS),
    }
    dataset = _dataset(companies, [rel])

    def route_api(route):
        if route.request.url.endswith("/api/v1/targets/mock/dataset"):
            route.fulfill(json=dataset)
        else:
            route.continue_()

    page.route("**/api/v1/**", route_api)
    page.goto(dashboard_url)
    # Inject the malicious dataset the way the research flow would.
    page.evaluate("() => loadTargetIntoDashboard('mock')")

    # 1) The malicious COMPANY name renders as inert text, not an element.
    assert page.locator("img[src='x']").count() == 0
    assert page.evaluate("Boolean(window.__dashboardXss)") is False

    # 2) The malicious EVIDENCE fields render as inert text inside the modal.
    page.locator("#rel-body tr").first.click()
    modal = page.locator("#modal-content")
    expect(modal).to_be_visible()
    assert modal.locator("img[src='x']").count() == 0
    # Payloads are present only as escaped text (e.g. "&lt;img src=x ...").
    assert XSS in (modal.inner_text() or "")
    # The javascript: URL was neutralised to a no-op anchor.
    assert modal.locator("a[href^='javascript:']").count() == 0


def test_dashboard_renders_all_rows_without_100_truncation(page: Page, dashboard_url: str):
    """Large snapshots are rendered in full; no silent 100-row cut-off."""
    companies = {"mock": _company("mock", "Mock Target Inc.")}
    relationships = []
    for i in range(150):
        cid = f"c{i:03d}"
        companies[cid] = _company(cid, f"Company {i:03d}")
        relationships.append({
            "id": f"rel_{i:03d}", "source_company_id": "mock", "target_company_id": cid,
            "type": "customer", "direction": f"mock -> {cid}", "status": "inferred",
            "confidence_score": 50, "valid_from": "2024-01-01", "valid_until": None,
            "evidence_ids": [], "summary": "scale fixture",
        })
    # One malicious company name proves XSS-safety at scale too.
    companies["xss"] = _company("xss", XSS)
    relationships.append({
        "id": "rel_xss", "source_company_id": "mock", "target_company_id": "xss",
        "type": "customer", "direction": "mock -> xss", "status": "inferred",
        "confidence_score": 50, "valid_from": "2024-01-01", "valid_until": None,
        "evidence_ids": [], "summary": "scale xss",
    })
    dataset = _dataset(companies, relationships)  # 152 companies, 151 relationships

    def route_api(route):
        if route.request.url.endswith("/api/v1/targets/mock/dataset"):
            route.fulfill(json=dataset)
        else:
            route.continue_()

    page.route("**/api/v1/**", route_api)
    page.goto(dashboard_url)
    page.evaluate("() => loadTargetIntoDashboard('mock')")

    # All companies render (no 100-row truncation).
    assert page.locator("#comp-body tr").count() == 152
    # XSS payload at scale is still inert.
    assert page.locator("img[src='x']").count() == 0
    assert page.evaluate("Boolean(window.__dashboardXss)") is False


def test_scoring_methodology_renders_live(page: Page, dashboard_url: str):
    """The Scoring tab is populated LIVE from the engine, not a stale snapshot."""
    page.goto(dashboard_url)
    page.locator("#tab-scoring").click()
    live = page.locator("#methodology-live")
    expect(live).to_be_visible()
    text = live.inner_text() or ""
    # Live table carries the canonical engine dimensions + weights.
    assert re.search(r"authority", text, re.I), "live methodology missing authority dimension"
    assert re.search(r"evidence[ _]quality", text, re.I), "live methodology missing evidence_quality"
    # The static fallback must be hidden once the live table is in place.
    assert page.locator("#methodology-static").is_hidden()
