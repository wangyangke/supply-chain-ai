#!/usr/bin/env python3
"""One-shot acceptance gate for supply-chain-ai.

Runs, in order:
  1. Data validation for every committed target (default strictness;
     known by-design warnings are allowed and still report VALID).
  2. Scoring reproducibility dry-run for every target (the engine must
     agree with the stored scores/statuses).
  3. Methodology-endpoint consistency: the live GET /api/v1/scoring-methodology
     response must exactly equal scoring_methodology() produced by the engine.

Exits non-zero if any check fails, so it can gate CI or a local pre-push check.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TARGETS = ["nvidia", "unitree"]
PORT = 8123
METHODOLOGY_OK = "All stored statuses match the engine score bands."


def run(cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


results = []


def record(name, ok, detail=""):
    results.append(ok)
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)


print("=== Supply-Chain-AI Acceptance Gate ===")

# 1. Data validation
for t in TARGETS:
    r = run([PY, "scripts/validate_data.py", "--data", f"data/targets/{t}"])
    ok = r.returncode == 0 and "VALID" in r.stdout
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip().splitlines()[-1]
    record(f"validate {t}", ok, "" if ok else tail)

# 2. Scoring reproducibility (dry-run)
for t in TARGETS:
    r = run([PY, "scripts/sync_scores.py", "--data", f"data/targets/{t}", "--breakdown"])
    ok = r.returncode == 0 and METHODOLOGY_OK in r.stdout
    record(f"scoring reproducible {t}", ok, "" if ok else "engine/stored mismatch")

# 3. Methodology endpoint consistency
server = subprocess.Popen(
    [PY, "-m", "uvicorn", "src.api:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
try:
    health = f"http://127.0.0.1:{PORT}/health"
    for _ in range(50):
        try:
            urllib.request.urlopen(health, timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    expected = json.loads(run([
        PY, "-c",
        "from src.scoring import scoring_methodology; import json; print(json.dumps(scoring_methodology()))",
    ]).stdout)
    actual = json.loads(
        urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/api/v1/scoring-methodology", timeout=5
        ).read()
    )
    ok = expected == actual
    record("methodology endpoint consistent", ok, "" if ok else "live response != engine output")
finally:
    server.terminate()
    try:
        server.wait(timeout=5)
    except Exception:
        server.kill()

passed = sum(1 for x in results if x)
total = len(results)
print(f"\n=== SUMMARY: {passed}/{total} PASS ===")
sys.exit(0 if passed == total else 1)
