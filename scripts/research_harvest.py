#!/usr/bin/env python3
"""Agent-driven evidence harvesting (staging only, never merges directly).

Mechanical half of the agent research protocol (docs/research_agent_protocol.md):
executes searches via a pluggable backend, dedupes hits, normalizes fields,
guesses source_type / access_restriction, and emits *staged* evidence
candidates for agent review. The agent (per protocol §5-§8) must verify every
candidate (entity disambiguation, co-occurrence guard, quote extraction,
conflict arbitration) before anything is merged into data/evidence.json.

Backends
--------
manual   read raw hits from a JSONL file produced by the agent itself
         (each line: {"title","url","snippet","published_at"}) — works with
         zero API keys; the agent's own web-search results are pasted in.
tavily   call the Tavily search API      (env: SCR_TAVILY_API_KEY)
brave    call the Brave search API       (env: SCR_BRAVE_API_KEY)

Usage
-----
  python scripts/research_harvest.py --backend manual --input hits.jsonl \
      --target nvidia --out data/staging/candidates.json

  python scripts/research_harvest.py --backend tavily \
      --query 'Oracle deploys NVIDIA GPUs press release' --target nvidia

  python scripts/research_harvest.py --check data/staging/candidates.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

# Ensure the project root is importable when run as `python scripts/research_harvest.py`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models import AccessRestriction, SourceType  # noqa: E402

# ---------------------------------------------------------------------------
# Source classification heuristics
# ---------------------------------------------------------------------------

_SEC_DOMAINS = ("sec.gov", "www.sec.gov")
_GOV_DOMAINS = (".gov",)
_EXCHANGE_DOMAINS = ("hkexnews.hk", "twse.com.tw", "krx.co.kr", "euronext.com")
_TIER1_MEDIA = (
    "reuters.com", "bloomberg.com", "ft.com", "cnbc.com", "wsj.com",
    "apnews.com", "bbc.com", "nytimes.com",
)
_PAYWALL_DOMAINS = ("wsj.com", "ft.com", "bloomberg.com", "nytimes.com")
_REFERENCE_DOMAINS = ("wikipedia.org", "britannica.com", "investopedia.com")
_ANALYST_DOMAINS = ("omdia.com", "gartner.com", "idc.com", "trendforce.com")

# Relationship verbs used for a *heuristic* co-occurrence warning only; the
# final verb-attribution judgment is the agent's job (protocol §7).
_RELATIONSHIP_VERBS = (
    "supply", "supplier", "purchase", "buy", "customer", "deploy",
    "partner", "partnership", "collaborate", "invest", "stake",
    "compet", "rival", "acqui",
)


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def guess_source_type(url: str, publisher: str = "") -> SourceType:
    """Heuristic source classification; agent confirms during review."""
    host = _host(url)
    if any(host.endswith(d.removeprefix("www.")) for d in _SEC_DOMAINS):
        return SourceType.SEC_FILING
    if any(host.endswith(d) for d in _EXCHANGE_DOMAINS):
        return SourceType.EXCHANGE_FILING
    if any(host.endswith(d) for d in _GOV_DOMAINS):
        return SourceType.GOVERNMENT
    if any(host.endswith(d) for d in _REFERENCE_DOMAINS):
        return SourceType.REFERENCE
    if any(host.endswith(d) for d in _ANALYST_DOMAINS):
        return SourceType.ANALYST_RESEARCH
    if any(host.endswith(d) for d in _TIER1_MEDIA):
        return SourceType.BUSINESS_MEDIA
    # company domains: IR/newsroom subdomains or press-ish paths
    if any(k in host for k in ("ir.", "investor.", "newsroom.", "press.")) or \
       any(k in urlparse(url).path.lower() for k in ("/press", "/newsroom", "/ir/", "/investor")):
        return SourceType.COMPANY_PRESS_RELEASE
    if publisher and any(k in publisher.lower() for k in ("inc.", "corp", "ltd", "plc", "n.v.")):
        return SourceType.COMPANY_IR
    return SourceType.UNKNOWN


def guess_access_restriction(url: str) -> AccessRestriction:
    host = _host(url)
    if any(host.endswith(d) for d in _PAYWALL_DOMAINS):
        return AccessRestriction.PAYWALL
    return AccessRestriction.PUBLIC


def canonicalize_url(url: str) -> str:
    """Strip common tracking params for dedupe + canonical citation."""
    from urllib.parse import parse_qsl, urlencode, urlunparse

    parts = urlparse(url.strip())
    kept = [
        (k, v) for k, v in parse_qsl(parts.query)
        if not k.lower().startswith(("utm_", "fbclid", "gclid", "ref", "spm"))
    ]
    return urlunparse(parts._replace(query=urlencode(kept), fragment=""))


def snippet_has_relationship_verb(snippet: str) -> bool:
    low = snippet.lower()
    return any(v in low for v in _RELATIONSHIP_VERBS)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def backend_manual(path: Path) -> list[dict[str, Any]]:
    """Read agent-produced hits from JSONL."""
    hits = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                hits.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"warn: skipping invalid JSONL line {lineno}: {exc}", file=sys.stderr)
    return hits


def backend_tavily(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    api_key = os.environ.get("SCR_TAVILY_API_KEY")
    if not api_key:
        raise SystemExit("error: SCR_TAVILY_API_KEY not set for tavily backend")
    import httpx

    resp = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": api_key, "query": query, "max_results": max_results},
        timeout=30,
    )
    resp.raise_for_status()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "snippet": r.get("content", ""), "published_at": r.get("published_date")}
        for r in resp.json().get("results", [])
    ]


def backend_brave(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    api_key = os.environ.get("SCR_BRAVE_API_KEY")
    if not api_key:
        raise SystemExit("error: SCR_BRAVE_API_KEY not set for brave backend")
    import httpx

    resp = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        params={"q": query, "count": max_results},
        timeout=30,
    )
    resp.raise_for_status()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "snippet": r.get("description", ""), "published_at": r.get("age")}
        for r in resp.json().get("web", {}).get("results", [])
    ]


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------

def stage_candidates(
    hits: list[dict[str, Any]],
    target: str,
    relationship_id: Optional[str],
) -> list[dict[str, Any]]:
    """Normalize raw hits into staged evidence candidates (review required)."""
    seen: set[str] = set()
    staged: list[dict[str, Any]] = []
    today = date.today().isoformat()

    for hit in hits:
        url = canonicalize_url(hit.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)

        snippet = hit.get("snippet", "")
        needs_review = ["entity_disambiguation", "quote_extraction", "conflict_check"]
        if not snippet_has_relationship_verb(snippet):
            needs_review.append("cooccurrence_guard")  # protocol §7
        published = hit.get("published_at") or None
        if not published:
            needs_review.append("published_at_missing")

        staged.append({
            "staging_note": (
                "STAGED CANDIDATE — do NOT merge before agent verification "
                "(docs/research_agent_protocol.md §5-§8)"
            ),
            "relationship_id": relationship_id,
            "research_target": target,
            "title": hit.get("title", ""),
            "source_url": url,
            "publisher": hit.get("publisher", _host(url)),
            "source_type": guess_source_type(url, hit.get("publisher", "")).value,
            "published_at": published,
            "accessed_at": today,
            "access_restriction": guess_access_restriction(url).value,
            "evidence_locator": "",
            "quote": "",
            "raw_snippet": snippet,
            "needs_review": needs_review,
        })
    return staged


def check_staging(path: Path) -> int:
    """Validate a staging file: schema-plausible + review flags resolved."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    candidates = data.get("candidates", [])
    problems: list[str] = []
    for i, c in enumerate(candidates):
        pending = c.get("needs_review", [])
        merged = c.get("agent_approved", False)
        if merged and pending:
            problems.append(f"[{i}] approved but still has needs_review: {pending}")
        if merged and not c.get("quote"):
            problems.append(f"[{i}] approved but quote is empty (protocol §5)")
        if merged and not c.get("evidence_locator"):
            problems.append(f"[{i}] approved but evidence_locator is empty")
        st = c.get("source_type", "unknown")
        if merged and st == "unknown":
            problems.append(f"[{i}] approved but source_type still unknown")
        if c.get("published_at"):
            try:
                date.fromisoformat(c["published_at"])
            except ValueError:
                problems.append(f"[{i}] invalid published_at: {c['published_at']}")
    for p in problems:
        print(f"  ✗ {p}")
    approved = sum(1 for c in candidates if c.get("agent_approved"))
    print(f"\n{len(candidates)} candidates, {approved} agent-approved, {len(problems)} problem(s)")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["manual", "tavily", "brave"])
    ap.add_argument("--input", type=Path, help="JSONL hits file (manual backend)")
    ap.add_argument("--query", help="Search query (tavily/brave backends)")
    ap.add_argument("--target", default="", help="Research target company id, e.g. nvidia")
    ap.add_argument("--relationship-id", help="Relationship these hits support (optional)")
    ap.add_argument("--max-results", type=int, default=8)
    ap.add_argument("--out", type=Path, help="Staging output JSON path")
    ap.add_argument("--check", type=Path, help="Validate an existing staging file and exit")
    args = ap.parse_args()

    if args.check:
        return check_staging(args.check)

    if args.backend == "manual":
        if not args.input or not args.input.is_file():
            ap.error("--backend manual requires --input <hits.jsonl>")
        hits = backend_manual(args.input)
    else:
        if not args.query:
            ap.error(f"--backend {args.backend} requires --query")
        hits = (backend_tavily if args.backend == "tavily" else backend_brave)(
            args.query, args.max_results
        )

    staged = stage_candidates(hits, args.target, args.relationship_id)
    payload = {
        "staging_version": "1.0",
        "research_target": args.target,
        "relationship_id": args.relationship_id,
        "generated_at": date.today().isoformat(),
        "protocol": "docs/research_agent_protocol.md",
        "candidates": staged,
    }
    out = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out, encoding="utf-8")
        print(f"wrote {len(staged)} staged candidate(s) -> {args.out}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
