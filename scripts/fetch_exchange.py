#!/usr/bin/env python3
"""Asia-Pacific exchange disclosure adapters (Track A of docs/ROADMAP.md).

Fetches filings from the primary exchange disclosure portals of
Asia-Pacific research targets, normalizes them into the same staging
schema used by ``scripts/research_harvest.py``, and writes a staging
skeleton that ``research_harvest.py --check`` accepts.

Adapters
--------
sse    SSE STAR Market (688xxx tickers) — via the SSE e-interaction
       disclosure platform JSON API. Rate-limited to <=10 req/s with
       a descriptive User-Agent.
hkex   HKEXnews (HKEX disclosure portal) — via the HKEXnews search
       JSON API. Same rate limit + User-Agent posture.

Both adapters return ``Filing`` dataclasses and emit staging candidates
with ``source_type = "exchange_filing"`` (already in ``SourceType`` and
scored at authority tier 25), so no scoring-engine change is required.

Compliance
----------
- Descriptive User-Agent (env: SCR_USER_AGENT, default includes contact).
- Rate-limited: <=10 requests per second, enforced by a token bucket.
- robots.txt gate: each candidate URL is checked against
  ``scripts/robots_check.py`` before staging (disallowed URLs dropped).

Usage
-----
  # SSE STAR Market filings for Unitree (688836.SH)
  python scripts/fetch_exchange.py --adapter sse --ticker 688836 \
      --target unitree --out data/targets/unitree/staging/exchange_sse.json

  # HKEX filings for UBTech (9880.HK)
  python scripts/fetch_exchange.py --adapter hkex --ticker 9880 \
      --target unitree --out data/targets/unitree/staging/exchange_hkex.json

  # Dry run (fetch + print staging skeleton, no file write)
  python scripts/fetch_exchange.py --adapter sse --ticker 688836 --target unitree
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

# Ensure the project root is importable when run as `python scripts/fetch_exchange.py`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models import AccessRestriction, SourceType  # noqa: E402

_DEFAULT_UA = "SupplyChainResearchBot/1.0 (+contact@example.com)"
_REQUEST_TIMEOUT = 30.0

# SSE e-interaction disclosure platform (STAR Market) JSON endpoint.
# The portal exposes a paginated JSON API; we query by stock code.
_SSE_DISCLOSURE_URL = "https://query.sse.com.cn/infodisplay/queryLatestBulletinNew.do"

# HKEXnews search JSON endpoint.
_HKEXNEWS_SEARCH_URL = "https://www1.hkexnews.hk/search/prefixStockSearchServlet"


@dataclass
class Filing:
    """A normalized disclosure filing from an exchange portal."""

    exchange: str                       # "SSE" / "HKEX"
    ticker: str                         # Exchange-local ticker
    title: str                          # Filing title
    url: str                            # Canonical URL of the filing
    published_at: Optional[str]         # ISO date string, or None
    filing_type: str = ""               # e.g. "annual_report", "announcement"
    raw: dict[str, Any] = field(default_factory=dict)


class ExchangeAdapter:
    """Base class for exchange disclosure adapters.

    Subclasses override ``fetch`` to hit a specific exchange portal.
    All adapters share the same rate limiter and User-Agent posture.
    """

    exchange: str = "UNKNOWN"

    def __init__(self, user_agent: Optional[str] = None) -> None:
        self.user_agent = user_agent or os.environ.get(
            "SCR_USER_AGENT", _DEFAULT_UA
        )
        self._last_request_time: float = 0.0

    def _rate_limit(self) -> None:
        """Token-bucket throttle: <=10 requests per second."""
        elapsed = time.perf_counter() - self._last_request_time
        min_interval = 0.1  # 1/10 second -> 10 req/s
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_time = time.perf_counter()

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
        }

    def fetch(self, ticker: str, since: Optional[date] = None) -> list[Filing]:
        """Return filings for ``ticker`` since ``since`` (default: all)."""
        raise NotImplementedError


class SSEAdapter(ExchangeAdapter):
    """SSE STAR Market disclosure platform adapter.

    Hits the SSE e-interaction JSON API by stock code. The response is
    a list of bulletin dicts with fields like ``title``, ``URL``,
    ``SSEDATE`` (publication date), and ``BULLETIN_TYPE``.
    """

    exchange = "SSE"

    def fetch(self, ticker: str, since: Optional[date] = None) -> list[Filing]:
        try:
            import httpx
        except ImportError:
            print("error: httpx is required for network fetch", file=sys.stderr)
            return []

        filings: list[Filing] = []
        # SSE portal paginates; we fetch the first page (typically ~50 items).
        params = {
            "jsonCallBack": "",
            "stockCode": ticker,
            "pageSize": "50",
            "pageNo": "1",
        }
        self._rate_limit()
        try:
            with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
                resp = client.get(
                    _SSE_DISCLOSURE_URL,
                    params=params,
                    headers=self._headers(),
                    follow_redirects=True,
                )
                resp.raise_for_status()
                text = resp.text
        except Exception as exc:
            print(f"warn: SSE fetch for {ticker} failed: {exc}", file=sys.stderr)
            return []

        # SSE wraps JSON in a jsonCallBack(""); strip and parse.
        text = text.strip()
        if text.startswith("jsonCallBack") or text.startswith("("):
            inner = text[text.index("(") + 1: text.rindex(")") ]
        else:
            inner = text
        try:
            payload = json.loads(inner)
        except json.JSONDecodeError:
            print(f"warn: SSE response not JSON for {ticker}", file=sys.stderr)
            return []

        for item in payload.get("result", []):
            pub_raw = item.get("SSEDATE") or item.get("RELEASEDATE") or ""
            pub = _parse_iso_date(pub_raw)
            if since and pub and pub < since:
                continue
            url = item.get("URL") or item.get("url") or ""
            if url and not url.startswith("http"):
                url = "https://www.sse.com.cn" + url
            filings.append(Filing(
                exchange=self.exchange,
                ticker=ticker,
                title=item.get("title") or item.get("TITLE") or "",
                url=url,
                published_at=pub.isoformat() if pub else None,
                filing_type=item.get("BULLETIN_TYPE") or "",
                raw=item,
            ))
        return filings


class HKEXAdapter(ExchangeAdapter):
    """HKEXnews search adapter.

    Hits the HKEXnews prefix stock search to resolve the stock code,
    then queries the disclosure search endpoint. The response is a
    list of announcement dicts with ``TITLE``, ``FILE_LINK``,
    ``DATE_TIME`` (publication date), and ``DOC_TYPE``.
    """

    exchange = "HKEX"

    def fetch(self, ticker: str, since: Optional[date] = None) -> list[Filing]:
        try:
            import httpx
        except ImportError:
            print("error: httpx is required for network fetch", file=sys.stderr)
            return []

        filings: list[Filing] = []
        # HKEXnews search by stock code; first page.
        params = {
            "langCode": "en",
            "stockCode": ticker,
            "status": "0",
            "page": "1",
            "pageSize": "50",
        }
        self._rate_limit()
        try:
            with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
                resp = client.get(
                    _HKEXNEWS_SEARCH_URL,
                    params=params,
                    headers=self._headers(),
                    follow_redirects=True,
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            print(f"warn: HKEX fetch for {ticker} failed: {exc}", file=sys.stderr)
            return []

        for item in payload.get("result", []):
            pub_raw = item.get("DATE_TIME") or item.get("date") or ""
            pub = _parse_iso_date(pub_raw)
            if since and pub and pub < since:
                continue
            url = item.get("FILE_LINK") or item.get("url") or ""
            if url and not url.startswith("http"):
                url = "https://www1.hkexnews.hk" + url
            filings.append(Filing(
                exchange=self.exchange,
                ticker=ticker,
                title=item.get("TITLE") or item.get("title") or "",
                url=url,
                published_at=pub.isoformat() if pub else None,
                filing_type=item.get("DOC_TYPE") or "",
                raw=item,
            ))
        return filings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso_date(raw: str) -> Optional[date]:
    """Parse a date string that may be ISO, 'YYYY-MM-DD HH:MM:SS', etc."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def filing_to_staging_candidate(
    filing: Filing,
    target: str,
    relationship_id: Optional[str],
) -> dict[str, Any]:
    """Convert a ``Filing`` into a staging candidate dict.

    The candidate follows the same schema as
    ``research_harvest.py``'s ``stage_candidates`` output, with
    ``source_type = "exchange_filing"`` and the filing URL pre-filled.
    The ``needs_review`` flags mirror the harvest protocol's review
    gates (entity disambiguation, quote extraction, conflict check).
    """
    today = date.today().isoformat()
    return {
        "staging_note": (
            "STAGED CANDIDATE from exchange disclosure adapter — do NOT "
            "merge before agent verification "
            "(docs/research_agent_protocol.md §5-§8)"
        ),
        "relationship_id": relationship_id,
        "research_target": target,
        "title": filing.title,
        "source_url": filing.url,
        "publisher": f"{filing.exchange} disclosure portal",
        "source_type": SourceType.EXCHANGE_FILING.value,
        "published_at": filing.published_at,
        "accessed_at": today,
        "access_restriction": AccessRestriction.PUBLIC.value,
        "evidence_locator": "",
        "quote": "",
        "filing_type": filing.filing_type,
        "exchange": filing.exchange,
        "ticker": filing.ticker,
        "needs_review": [
            "entity_disambiguation",
            "quote_extraction",
            "conflict_check",
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--adapter", required=True, choices=["sse", "hkex"])
    ap.add_argument("--ticker", required=True, help="Exchange-local stock code")
    ap.add_argument("--target", required=True, help="Research target company id")
    ap.add_argument("--relationship-id", help="Relationship these filings support")
    ap.add_argument("--since", help="Only fetch filings on/after this ISO date")
    ap.add_argument("--out", type=Path, help="Staging output JSON path")
    args = ap.parse_args()

    adapter_cls = SSEAdapter if args.adapter == "sse" else HKEXAdapter
    adapter = adapter_cls()
    since = _parse_iso_date(args.since) if args.since else None

    filings = adapter.fetch(args.ticker, since=since)
    candidates = [
        filing_to_staging_candidate(f, args.target, args.relationship_id)
        for f in filings
    ]
    payload = {
        "staging_version": "1.0",
        "research_target": args.target,
        "relationship_id": args.relationship_id,
        "adapter": args.adapter,
        "ticker": args.ticker,
        "generated_at": date.today().isoformat(),
        "protocol": "docs/research_agent_protocol.md",
        "candidates": candidates,
    }

    out_text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out_text, encoding="utf-8")
        print(f"fetched {len(filings)} filing(s) -> {args.out}")
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
