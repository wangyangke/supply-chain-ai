#!/usr/bin/env python3
"""Extract per-company evidence from the downloaded NVIDIA 10-K text.

Uses only the already-committed 10-K text file (data/raw_edgar/10k_text.txt)
downloaded from SEC EDGAR — no re-fetching required by reviewers.

Usage:
  python scripts/extract_company_mentions.py [--text data/raw_edgar/10k_text.txt]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Candidate companies and the terms to search for (alias list).
# entity_name -> search aliases (case-insensitive)
COMPANY_TERMS: dict[str, list[str]] = {
    "tsmc": ["TSMC", "Taiwan Semiconductor Manufacturing"],
    "sk_hynix": ["SK Hynix", "Hynix"],
    "micron": ["Micron"],
    "amkor": ["Amkor"],
    "ase_technology": ["ASE Technology", "Advanced Semiconductor Engineering"],
    "asml": ["ASML"],
    "microsoft": ["Microsoft"],
    "meta": ["Meta"],
    "amazon": ["Amazon"],
    "google": ["Google", "Alphabet"],
    "dell": ["Dell Technologies", "Dell"],
    "hpe": ["Hewlett Packard Enterprise", "HPE"],
    "accenture": ["Accenture"],
    "servicenow": ["ServiceNow"],
    "snowflake": ["Snowflake"],
    "cisco": ["Cisco"],
    "coreweave": ["CoreWeave"],
    "soundhound": ["SoundHound"],
    "recursion": ["Recursion Pharmaceuticals", "Recursion"],
    "amd": ["AMD", "Advanced Micro Devices"],
    "intel": ["Intel"],
    "broadcom": ["Broadcom"],
    "qualcomm": ["Qualcomm"],
}


def find_contexts(text: str, terms: list[str], window: int = 260, max_hits: int = 3) -> list[dict]:
    lower = text.lower()
    out: list[dict] = []
    for term in terms:
        idx = lower.find(term.lower())
        count = 0
        start = 0
        while count < max_hits:
            idx = lower.find(term.lower(), start)
            if idx == -1:
                break
            snippet = text[max(0, idx - 100): idx + window].strip()
            out.append({"term": term, "snippet": snippet})
            start = idx + len(term)
            count += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="data/raw_edgar/10k_text.txt")
    args = ap.parse_args()

    text_path = Path(args.text)
    if not text_path.exists():
        print(f"ERROR: {text_path} not found — run scripts/fetch_edgar.py first", file=__import__("sys").stderr)
        return 1
    text = text_path.read_text(encoding="utf-8", errors="replace")

    results: dict[str, list[dict]] = {}
    for company, terms in COMPANY_TERMS.items():
        results[company] = find_contexts(text, terms)

    out_path = Path("data/raw_edgar/company_mentions.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("Company mention counts in 10-K:")
    for company, hits in results.items():
        status = "FOUND" if hits else "NOT MENTIONED"
        print(f"  {company:16s} {status:14s} {len(hits)} snippet(s)")
    print(f"\nSaved -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
