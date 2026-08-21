#!/usr/bin/env python3
"""Merge agent-verified staging candidates into a target dataset.

Implements the staging -> review -> merge step of
docs/research_agent_protocol.md. RED LINE: only candidates with
`agent_approved: true` (and not `agent_rejected`) are merged; the script
refuses to merge anything else.

Staging file format (see data/targets/nvidia/staging/oracle_candidates.json
for a worked example, plus the two top-level blocks below):

  {
    "staging_version": "1.0",
    "research_target": "nvidia",
    "counterparty": {                 // company to upsert into companies.json
      "id": "oracle", "name": "Oracle Corporation", "stock_code": "ORCL",
      "exchange": "NYSE", "isin": "...", "country": "US",
      "entity_type": "related", "sector": "...", "description": "..."
    },
    "relationship": {                 // relationship skeleton (id is assigned)
      "type": "partner",              // supplier|customer|partner|investor_or_investee|peer
      "direction": "oracle <-> nvidia (bidirectional)",
      "valid_from": "2023-03-21", "valid_until": null,
      "summary": "..."
    },
    "candidates": [ ... ]             // research_harvest.py output, agent-verified
  }

After merging, run:
  python scripts/sync_scores.py --data <target_dir> --write
  python scripts/validate_data.py --data <target_dir>

Usage:
  python scripts/merge_staged.py --staging data/targets/nvidia/staging/x.json \
      --data data/targets/nvidia [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

TYPE_PREFIX = {
    "supplier": "sup",
    "customer": "cus",
    "partner": "par",
    "investor_or_investee": "inv",
    "peer": "peer",
}
EV_SUFFIXES = ["", "b", "c", "d", "e", "f", "g", "h"]


def _load(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


def _next_number(rel_ids: list[str], prefix: str) -> int:
    best = 0
    for rid in rel_ids:
        m = re.fullmatch(rf"rel_{re.escape(prefix)}_(\d+)", rid)
        if m:
            best = max(best, int(m.group(1)))
    return best + 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--staging", required=True, help="Staging JSON file (agent-verified)")
    ap.add_argument("--data", required=True, help="Target dataset directory (data/targets/<id>)")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be merged, write nothing")
    args = ap.parse_args()

    staging_path = Path(args.staging)
    data_dir = Path(args.data)
    staging = json.loads(staging_path.read_text(encoding="utf-8"))

    if staging.get("merged"):
        raise SystemExit(f"staging file already merged into {staging.get('merged_into')} — refusing to merge twice")

    counterparty = staging.get("counterparty")
    rel_spec = staging.get("relationship")
    if not counterparty or not counterparty.get("id"):
        raise SystemExit("staging file missing 'counterparty' block with company id")
    if not rel_spec or rel_spec.get("type") not in TYPE_PREFIX:
        raise SystemExit(f"staging file missing 'relationship' block with a valid type ({sorted(TYPE_PREFIX)})")

    candidates = staging.get("candidates", [])
    approved = [c for c in candidates if c.get("agent_approved") and not c.get("agent_rejected")]
    if not approved:
        raise SystemExit("no agent-approved candidates — nothing to merge (red line: unverified evidence stays out)")
    for c in approved:
        missing = [f for f in ("source_url", "quote", "evidence_locator", "publisher") if not str(c.get(f, "")).strip()]
        if missing:
            raise SystemExit(f"approved candidate '{c.get('title', '?')[:50]}' lacks {missing} — complete verification first")
    if len(approved) > len(EV_SUFFIXES):
        raise SystemExit(f"too many approved candidates ({len(approved)} > {len(EV_SUFFIXES)})")

    companies = _load(data_dir / "companies.json")
    relationships = _load(data_dir / "relationships.json")
    evidence = _load(data_dir / "evidence.json")
    target_id = json.loads((data_dir / "dataset.json").read_text(encoding="utf-8"))["research_target"]

    prefix = TYPE_PREFIX[rel_spec["type"]]
    n = _next_number([r["id"] for r in relationships], prefix)
    rel_id = f"rel_{prefix}_{n:03d}"
    ev_ids = [f"ev_{prefix}_{n:03d}{s}" for s in EV_SUFFIXES[: len(approved)]]

    if counterparty["id"] in {c["id"] for c in companies}:
        company_action = f"company '{counterparty['id']}' already exists — keeping existing record"
    else:
        company_action = f"company '{counterparty['id']}' will be added"

    direction = rel_spec.get("direction") or f"{counterparty['id']} -> {target_id}"
    new_rel = {
        "id": rel_id,
        "source_company_id": counterparty["id"],
        "target_company_id": target_id,
        "type": rel_spec["type"],
        "direction": direction,
        "status": "inferred",  # placeholder; sync_scores.py --write recomputes band
        "confidence_score": 0,  # placeholder; sync_scores.py --write recomputes score
        "valid_from": rel_spec.get("valid_from"),
        "valid_until": rel_spec.get("valid_until"),
        "evidence_ids": ev_ids,
        "summary": rel_spec.get("summary", ""),
    }

    print(f"merge plan: {company_action}")
    print(f"  relationship {rel_id} ({rel_spec['type']}, {len(approved)} evidence)")
    for eid, c in zip(ev_ids, approved):
        print(f"  {eid}: {c.get('published_at') or 'no-date'} | {c['publisher'][:50]}")
    rejected = [c for c in candidates if c.get("agent_rejected")]
    if rejected:
        print(f"  ({len(rejected)} rejected candidate(s) stay unmerged by design)")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    if counterparty["id"] not in {c["id"] for c in companies}:
        companies.append(counterparty)
    relationships.append(new_rel)
    for eid, c in zip(ev_ids, approved):
        evidence.append({
            "id": eid,
            "relationship_id": rel_id,
            "source_url": c["source_url"],
            "publisher": c["publisher"],
            "source_type": c.get("source_type", "unknown"),
            "published_at": c.get("published_at"),
            "accessed_at": c.get("accessed_at") or date.today().isoformat(),
            "evidence_locator": c["evidence_locator"],
            "access_restriction": c.get("access_restriction", "unknown"),
            "license_note": c.get("license_note", ""),
            "quote": c["quote"],
        })

    (data_dir / "companies.json").write_text(json.dumps(companies, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (data_dir / "relationships.json").write_text(json.dumps(relationships, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (data_dir / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    staging["merged"] = True
    staging["merged_into"] = rel_id
    staging["merged_evidence_ids"] = ev_ids
    staging["merged_at"] = date.today().isoformat()
    staging_path.write_text(json.dumps(staging, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\nmerged into {rel_id}; staging marked merged (audit trail kept)")
    print(f"next: python scripts/sync_scores.py --data {data_dir} --write")
    print(f"      python scripts/validate_data.py --data {data_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
