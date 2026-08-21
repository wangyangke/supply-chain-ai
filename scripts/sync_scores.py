#!/usr/bin/env python3
"""Synchronize relationship confidence scores with the scoring engine.

The dataset ships with human research *status* (confirmed/inferred/unknown)
and evidence. This script recomputes confidence_score from the scoring
engine and writes it back into relationships.json, guaranteeing that the
committed data is exactly reproducible from the engine + evidence.

Usage:
  python scripts/sync_scores.py [--data data] [--write]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the project root is importable when run as `python scripts/sync_scores.py`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models import Relationship
from src.scoring import score_relationship
from src.store import Store


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(PROJECT_ROOT / "data"))
    ap.add_argument("--write", action="store_true", help="Write scores back to relationships.json")
    ap.add_argument("--breakdown", action="store_true", help="Print per-dimension scores")
    args = ap.parse_args()

    data_dir = Path(args.data)
    store = Store.load(data_dir)
    company_names = {cid: c.name for cid, c in store.companies.items()}
    research_target = store.dataset.research_target
    updates: dict[str, int] = {}
    mismatches: list[str] = []
    print(f"{'rel':14s} {'status':>10s} {'engine_score':>12s} {'band':>10s} {'delta':>6s}")
    for rel in store.relationships:
        evs = store.list_evidence_for_relationship(rel.id)
        b = score_relationship(
            rel, evs, store.dataset.as_of,
            company_names=company_names, research_target_id=research_target,
        )
        new_score = round(b.total)
        updates[rel.id] = new_score
        delta = new_score - rel.confidence_score
        print(
            f"{rel.id:14s} {rel.status.value:>10s} {new_score:>12d} {b.band:>10s} {delta:>6d}"
        )
        if b.band != rel.status.value:
            mismatches.append(f"{rel.id}: stored status={rel.status.value}, band={b.band} ({new_score})")
        if args.breakdown:
            dims = b.dimensions
            print(
                "    " + "  ".join(
                    f"{k}={v:.0f}" for k, v in dims.items()
                ) + f"  [n_ev={len(evs)}]"
            )

    print()
    if mismatches:
        print("Band/status mismatches (stored status vs engine band):")
        for m in mismatches:
            print(f"  ! {m}")
    else:
        print("All stored statuses match the engine score bands.")

    if args.write:
        path = data_dir / "relationships.json"
        rels = json.loads(path.read_text(encoding="utf-8"))
        for r in rels:
            if r["id"] in updates:
                r["confidence_score"] = updates[r["id"]]
                # Derive the epistemic status from the score band so the
                # committed data is internally consistent by construction:
                # score >= 70 -> confirmed, 40-69 -> inferred, < 40 -> unknown.
                score = r["confidence_score"]
                r["status"] = (
                    "confirmed" if score >= 70
                    else "inferred" if score >= 40
                    else "unknown"
                )
        path.write_text(json.dumps(rels, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote updated scores and band-derived statuses to {path}")
    else:
        print("\nDry run (no write). Use --write to persist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
