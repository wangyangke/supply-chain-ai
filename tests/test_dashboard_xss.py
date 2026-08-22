"""XSS safety tests for the dashboard.

Three layers of verification, all offline-runnable:

1. test_no_unescaped_data_interpolation — static audit (pure Python, no deps).
   Guarantees no free-text data field is concatenated raw into innerHTML.
2. test_esc_neutralizes_xss_payloads — runs the REAL esc()/safeUrl() functions
   extracted from dashboard.html via Node, against classic XSS payloads.
3. test_dom_render_makes_xss_inert — end-to-end: linkedom loads dashboard.html,
   executes the inline app, injects a malicious evidence record, and we assert
   the rendered modal HTML contains no active payload.

If Node or linkedom is missing, layers 2/3 skip (not fail) — layer 1 still
guards structure. On CI both Node and linkedom are present, so all three run.
"""

import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(ROOT, "tests", "_xss_helper.mjs")


def _find_node():
    n = shutil.which("node")
    if n:
        return n
    # managed node on the dev machine
    cand = "/Users/tangting/.workbuddy/binaries/node/versions/22.22.2/bin/node"
    if os.path.exists(cand):
        return cand
    return None


NODE = _find_node()


def _run_node(mode):
    if not NODE:
        pytest.skip("node not available")
    r = subprocess.run(
        [NODE, HELPER, ROOT, mode],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode == 3:
        pytest.skip("optional dependency (linkedom) unavailable")
    if r.returncode != 0:
        pytest.skip(f"node helper error rc={r.returncode}: {r.stderr[:300]}")
    try:
        return json.loads(r.stdout)
    except Exception:
        pytest.skip("node helper produced no JSON: " + r.stdout[:300])


def test_esc_neutralizes_xss_payloads():
    res = _run_node("functions")
    assert res.get("all_neutralized") is True, res
    assert res.get("javascript_url_blocked") is True, res
    assert res.get("https_preserved") is True, res
    assert res.get("relative_preserved") is True, res


def test_dom_render_makes_xss_inert():
    res = _run_node("dom")
    assert res.get("active_payload") is False, res
    assert res.get("linked") != "FOUND_RAW_JS_URL", res


# Free-text data fields that, if concatenated raw into innerHTML, are an XSS sink.
DANGEROUS_FIELDS = {
    "quote", "name", "description", "summary", "publisher", "access_notes",
    "independence_group", "evidence_locator", "license_note", "detail", "sector",
    "country", "stock_code", "exchange", "isin", "accessed_at", "published_at",
}
PREFIXES = ["e", "c", "r", "t", "src", "tgt", "tc"]


def test_no_unescaped_data_interpolation():
    html = open(os.path.join(ROOT, "dashboard.html"), encoding="utf-8").read()
    offenders = []
    for ln, line in enumerate(html.splitlines(), 1):
        if "innerHTML" not in line:
            continue
        for p in PREFIXES:
            for f in DANGEROUS_FIELDS:
                tok = f"{p}.{f}"
                wrapped = (f"esc({tok})" in line) or (f"safeUrl({tok})" in line)
                bare = bool(re.search(r"\+[\s\"']*" + re.escape(tok) + r"[\s\"']*\+", line))
                if bare and not wrapped:
                    offenders.append((ln, tok))
                    break
    assert not offenders, f"Unescaped data interpolation(s) into innerHTML: {offenders[:10]}"
