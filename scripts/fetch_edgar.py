#!/usr/bin/env python3
"""Fetch NVIDIA supply-chain evidence from SEC EDGAR (public API only).

Compliance:
  - Uses the public EDGAR full-text search API and browse-edgar feeds.
  - Sends a descriptive User-Agent as required by EDGAR's fair access policy
    (https://www.sec.gov/os/accessing-edgar-data).
  - Never bypasses robots.txt, logins, paywalls, CAPTCHAs or rate limits.
  - Output is written to data/raw_edgar/ as JSON; reviewers do not need to
    re-fetch anything — the fetched excerpts are committed to the repo.

Usage:
  python scripts/fetch_edgar.py [--company NVDA] [--cik 0001045810] [--out data/raw_edgar]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import date
from pathlib import Path

import httpx

EDGAR_BROWSE = "https://www.sec.gov/cgi-bin/browse-edgar"
EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index?q={q}"
# Official full-text search endpoint (returns HTML; we extract the JSON payload)
EDGAR_FTS_HTML = "https://efts.sec.gov/LATEST/search-index?q={q}&forms=10-K"
EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik_no_dashes}/{accession_no_dashes}/"

DEFAULT_UA = os.environ.get("SCR_EDGAR_USER_AGENT", "SupplyChainResearch/1.0 (research@example.com)")
RATE_LIMIT_SLEEP = 0.4  # well below EDGAR's 10 req/s limit


def build_client(user_agent: str) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=30.0,
        follow_redirects=True,
    )


def fetch_submissions(client: httpx.Client, cik: str) -> dict:
    """Get recent submissions for a CIK via the submissions JSON API."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    resp = client.get(url)
    resp.raise_for_status()
    return resp.json()


def latest_10k_primary_doc(client: httpx.Client, submissions: dict) -> dict | None:
    """Return the primary document info of the most recent 10-K/10-K/A."""
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accession = recent.get("accessionNumber", [])
    primary_doc = recent.get("primaryDocument", [])
    filing_date = recent.get("filingDate", [])
    for i, form in enumerate(forms):
        if form in ("10-K", "10-K/A"):
            return {
                "form": form,
                "accessionNumber": accession[i].replace("-", ""),
                "primaryDocument": primary_doc[i],
                "filingDate": filing_date[i],
                "cik_no_dashes": None,  # filled by caller
            }
    return None


def download_10k_text(client: httpx.Client, cik_no_dashes: str, acc_no: str, doc: str, out_dir: Path) -> Path | None:
    """Download the full 10-K HTML and save it for offline parsing."""
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_no_dashes}/{acc_no}/{doc}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"10k_{acc_no}_{doc}"
    if out_path.exists():
        return out_path
    resp = client.get(url)
    resp.raise_for_status()
    out_path.write_bytes(resp.content)
    return out_path


def strip_html(html: str) -> str:
    """Crude HTML -> text conversion, good enough for keyword context."""
    # Remove scripts/styles
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    # Replace tags with spaces
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text


def find_keyword_contexts(text: str, keywords: list[str], window: int = 350, max_hits: int = 4) -> list[str]:
    """Find snippets around keywords (case-insensitive)."""
    lower = text.lower()
    hits: list[str] = []
    for kw in keywords:
        start = 0
        count = 0
        while count < max_hits:
            idx = lower.find(kw.lower(), start)
            if idx == -1:
                break
            snippet = text[max(0, idx - 120): idx + window]
            hits.append(snippet.strip())
            start = idx + len(kw)
            count += 1
        if len(hits) >= max_hits:
            break
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch NVIDIA supply-chain evidence from SEC EDGAR")
    ap.add_argument("--cik", default="0001045810", help="NVIDIA CIK")
    ap.add_argument("--out", default="data/raw_edgar", help="Output directory")
    ap.add_argument("--user-agent", default=DEFAULT_UA)
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = build_client(args.user_agent)

    print(f"[1/3] Fetching submissions for CIK {args.cik} ...")
    submissions = fetch_submissions(client, args.cik)
    time.sleep(RATE_LIMIT_SLEEP)

    print("[2/3] Locating latest 10-K ...")
    filing = latest_10k_primary_doc(client, submissions)
    if not filing:
        print("ERROR: no 10-K found", file=sys.stderr)
        return 1
    cik_no_dashes = args.cik.lstrip("0")
    print(f"      -> {filing['form']} filed {filing['filingDate']}, doc={filing['primaryDocument']}")

    print("[3/3] Downloading 10-K and extracting keyword contexts ...")
    path = download_10k_text(client, cik_no_dashes, filing["accessionNumber"], filing["primaryDocument"], out_dir)
    time.sleep(RATE_LIMIT_SLEEP)

    raw_html = path.read_text(encoding="utf-8", errors="replace")
    text = strip_html(raw_html)
    text_path = out_dir / "10k_text.txt"
    text_path.write_text(text, encoding="utf-8")

    # Keyword groups relevant to the five relationship categories
    keyword_groups = {
        "supplier": ["supplier", "suppliers", "foundry", "wafer", "subcontract"],
        "customer": ["customers", "customer", "direct customer", "largest customers"],
        "partner": ["partner", "partnership", "collaborat", "alliance"],
        "investor": ["investment", "investee", "equity method", "strategic investment", "ownership interest"],
        "peer": ["competitor", "competition", "competitive"],
    }

    contexts: dict[str, list[str]] = {}
    for group, kws in keyword_groups.items():
        contexts[group] = find_keyword_contexts(text, kws)

    result = {
        "source": "SEC EDGAR",
        "cik": args.cik,
        "filing": filing,
        "10k_url": f"https://www.sec.gov/Archives/edgar/data/{cik_no_dashes}/{filing['accessionNumber']}/{filing['primaryDocument']}",
        "fetched_at": date.today().isoformat(),
        "keyword_contexts": contexts,
    }
    json_path = out_dir / "edgar_supply_chain.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DONE -> {json_path}")
    print(f"      contexts: " + ", ".join(f"{k}={len(v)}" for k, v in contexts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
