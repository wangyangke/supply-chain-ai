#!/usr/bin/env python3
"""Onboard a new research target (any company) into the multi-target dataset.

Mechanical scaffolding only — creates data/targets/<target_id>/ with the
target company registered, empty relationships/evidence, and a staging/
directory, then registers the target in data/targets.json.

After running this, the AGENT (not this script) performs the research per
docs/research_agent_protocol.md:

  1. python scripts/onboard_target.py --id unitree --name "宇树科技 (Unitree Robotics)" ...
  2. agent searches the web, stages candidates via
     scripts/research_harvest.py --target <id> --out data/targets/<id>/staging/<rel>.json
  3. agent verifies each candidate (disambiguation / co-occurrence guard /
     quote extraction / conflict arbitration) and sets agent_approved
  4. python scripts/merge_staged.py --staging <file> --data data/targets/<id>
  5. python scripts/sync_scores.py --data data/targets/<id> --write
  6. python scripts/validate_data.py --data data/targets/<id>

Usage:
  python scripts/onboard_target.py --id unitree --name "Unitree Robotics" \
      --stock-code "" --exchange "" --country CN \
      --sector "Humanoid & quadruped robots" --description "..."
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not slug:
        raise SystemExit("target id must contain at least one [a-z0-9] character")
    return slug


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True, help="Target slug, e.g. unitree")
    ap.add_argument("--name", required=True, help="Full company name")
    ap.add_argument("--stock-code", default="", help="Ticker, e.g. NVDA (empty if unlisted)")
    ap.add_argument("--exchange", default="", help="Exchange, e.g. NASDAQ (empty if unlisted)")
    ap.add_argument("--isin", default="", help="ISIN if known")
    ap.add_argument("--country", required=True, help="ISO country code, e.g. US / CN")
    ap.add_argument("--sector", default="", help="Sector description")
    ap.add_argument("--description", required=True, help="One-paragraph company description")
    ap.add_argument("--as-of", default=date.today().isoformat(), help="Research as-of date (default: today)")
    ap.add_argument("--data-root", default=str(PROJECT_ROOT / "data"), help="Multi-target data root")
    args = ap.parse_args()

    tid = _slugify(args.id)
    root = Path(args.data_root)
    registry_path = root / "targets.json"
    if not registry_path.exists():
        raise SystemExit(f"targets.json not found in {root} — run from the project root")

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if tid in {t["id"] for t in registry.get("targets", [])}:
        raise SystemExit(f"target '{tid}' already registered in {registry_path}")

    target_dir = root / "targets" / tid
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "staging").mkdir(exist_ok=True)

    company = {
        "id": tid,
        "name": args.name,
        "stock_code": args.stock_code,
        "exchange": args.exchange,
        "isin": args.isin,
        "country": args.country,
        "entity_type": "target",
        "sector": args.sector,
        "description": args.description,
    }
    (target_dir / "companies.json").write_text(
        json.dumps([company], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target_dir / "relationships.json").write_text("[]\n", encoding="utf-8")
    (target_dir / "evidence.json").write_text("[]\n", encoding="utf-8")
    (target_dir / "dataset.json").write_text(
        json.dumps({
            "schema_version": "1.0",
            "as_of": args.as_of,
            "research_target": tid,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    registry.setdefault("targets", []).append({
        "id": tid,
        "name": args.name,
        "stock_code": args.stock_code,
        "exchange": args.exchange,
        "path": f"targets/{tid}",
        "description": args.description[:160],
    })
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Onboarded target '{tid}' at {target_dir}")
    print()
    print("Next steps (agent research flow, docs/research_agent_protocol.md):")
    print(f"  1. Agent searches the web for {args.name} relationships")
    print(f"  2. python scripts/research_harvest.py --backend manual --input hits.jsonl \\")
    print(f"       --target {tid} --out {target_dir}/staging/<relationship>.json")
    print("  3. Agent verifies candidates (disambiguation / co-occurrence guard / quotes)")
    print(f"  4. python scripts/merge_staged.py --staging <file> --data {target_dir}")
    print(f"  5. python scripts/sync_scores.py --data {target_dir} --write")
    print(f"  6. python scripts/validate_data.py --data {target_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
