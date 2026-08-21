#!/usr/bin/env python3
"""Online research agent: turn a company name into a fully-researched target.

This is the automated version of the agent research channel
(docs/research_agent_protocol.md). Given a company query it runs the full
pipeline WITHOUT human intervention:

  1. resolve      LLM resolves the query to a company identity (slug, name,
                  ticker, exchange, country, sector)
  2. onboard      scripts/onboard_target.py scaffolds data/targets/<id>/
  3. search       search backend (Tavily / Brave) collects candidate sources
                  per relationship category (partner+supplier / investor /
                  peer+customer)
  4. verify       LLM verifies hits per protocol (entity disambiguation,
                  co-occurrence guard, quote extraction) and emits staging
                  files (same format as the human/agent-verified ones)
  5. merge        scripts/merge_staged.py merges each staging file
                  (red line enforced: only agent_approved candidates)
  6. score+check  scripts/sync_scores.py --write + scripts/validate_data.py

Configuration (env):
  SCR_SEARCH_BACKEND   tavily | brave        (default: tavily)
  SCR_TAVILY_API_KEY   for the tavily backend
  SCR_BRAVE_API_KEY    for the brave backend
  SCR_LLM_BASE_URL     OpenAI-compatible base URL (e.g. http://127.0.0.1:15721/v1)
  SCR_LLM_API_KEY      API key for the LLM endpoint
  SCR_LLM_MODEL        model name (default: gpt-4o-mini)

For tests, inject `search_fn` / `llm_fn` — no network or keys needed.

CLI:
  python scripts/research_agent.py "宇通客车" --data-root data
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SearchFn = Callable[[str, int], list[dict[str, Any]]]
LlmFn = Callable[[str, str], str]
StepFn = Callable[[str, str], None]

SEARCH_QUERIES = [
    ("partner_supplier", "{name} partnership supplier collaboration 合作伙伴 供应商"),
    ("investor", "{name} investors shareholders funding round 投资方 股东 融资"),
    ("peer_customer", "{name} competitors peers customers 竞争对手 客户"),
]

REL_TYPES = "supplier|customer|partner|investor_or_investee|peer"


# ---------------------------------------------------------------------------
# Default backends (env-keyed)
# ---------------------------------------------------------------------------

def default_search_fn() -> SearchFn:
    backend = os.environ.get("SCR_SEARCH_BACKEND", "tavily")
    if backend == "brave":
        key = os.environ.get("SCR_BRAVE_API_KEY")
        if not key:
            raise RuntimeError("SCR_BRAVE_API_KEY not set")
        def _search(query: str, max_results: int = 8) -> list[dict[str, Any]]:
            req = urllib.request.Request(
                "https://api.search.brave.com/res/v1/web/search?q="
                + urllib.parse.quote(query) + f"&count={max_results}",
                headers={"X-Subscription-Token": key, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""),
                 "snippet": r.get("description", ""), "published_at": r.get("age")}
                for r in data.get("web", {}).get("results", [])
            ]
        return _search

    key = os.environ.get("SCR_TAVILY_API_KEY")
    if not key:
        raise RuntimeError("SCR_TAVILY_API_KEY not set")
    def _search(query: str, max_results: int = 8) -> list[dict[str, Any]]:
        body = json.dumps({"api_key": key, "query": query, "max_results": max_results}).encode()
        req = urllib.request.Request(
            "https://api.tavily.com/search", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""),
             "snippet": r.get("content", ""), "published_at": r.get("published_date")}
            for r in data.get("results", [])
        ]
    return _search


def default_llm_fn() -> LlmFn:
    base = os.environ.get("SCR_LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("SCR_LLM_API_KEY", "")
    model = os.environ.get("SCR_LLM_MODEL", "gpt-4o-mini")
    if not base or not key:
        raise RuntimeError("SCR_LLM_BASE_URL / SCR_LLM_API_KEY not set")
    def _llm(system: str, user: str) -> str:
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
        }).encode()
        req = urllib.request.Request(
            base + "/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    return _llm


def _extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of an LLM response."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for i, ch in enumerate(text):
        if ch in "[{":
            depth, instr, esc = 0, False, False
            for j in range(i, len(text)):
                c = text[j]
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif instr:
                    if c == '"':
                        instr = False
                elif c == '"':
                    instr = True
                elif c in "[{":
                    depth += 1
                elif c in "]}":
                    depth -= 1
                    if depth == 0:
                        return json.loads(text[i:j + 1])
    raise ValueError("no JSON found in LLM response")


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class ResearchAgent:
    """Company name -> researched, validated, registered target dataset."""

    def __init__(
        self,
        data_root: str | Path,
        search_fn: Optional[SearchFn] = None,
        llm_fn: Optional[LlmFn] = None,
        on_step: Optional[StepFn] = None,
    ):
        self.data_root = Path(data_root)
        self.search_fn = search_fn or default_search_fn()
        self.llm_fn = llm_fn or default_llm_fn()
        self.on_step = on_step or (lambda name, detail: None)
        self.steps: list[dict[str, str]] = []

    def _step(self, name: str, detail: str) -> None:
        self.steps.append({"step": name, "detail": detail})
        self.on_step(name, detail)

    # -- pipeline ---------------------------------------------------------

    def run(self, query: str) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("empty company query")

        # 1) resolve identity
        self._step("resolve", f"resolving company identity for '{query}'")
        identity = self._resolve_identity(query)
        tid = identity["id"]
        self._step("resolve", f"{query} -> {identity['name']} ({identity.get('stock_code') or 'unlisted'})")

        # 2) onboard (skip if target already registered)
        registry = json.loads((self.data_root / "targets.json").read_text(encoding="utf-8"))
        if tid in {t["id"] for t in registry.get("targets", [])}:
            raise ValueError(f"target '{tid}' already exists")
        self._step("onboard", f"scaffolding data/targets/{tid}")
        self._run_script("onboard_target.py", [
            "--id", tid, "--name", identity["name"],
            "--stock-code", identity.get("stock_code", ""),
            "--exchange", identity.get("exchange", ""),
            "--country", identity.get("country", ""),
            "--sector", identity.get("sector", ""),
            "--description", identity["description"],
            "--as-of", date.today().isoformat(),
            "--data-root", str(self.data_root),
        ])

        # 3+4) search & verify per category
        target_dir = self.data_root / "targets" / tid
        staging_files: list[Path] = []
        for category, q_template in SEARCH_QUERIES:
            q = q_template.format(name=identity["name"])
            self._step("search", f"[{category}] {q}")
            hits = self.search_fn(q, 8)
            self._step("search", f"[{category}] {len(hits)} hits")
            if not hits:
                continue
            self._step("verify", f"[{category}] LLM verifying {len(hits)} hits per protocol")
            stagings = self._verify_batch(identity, category, hits)
            for i, doc in enumerate(stagings):
                path = target_dir / "staging" / f"{category}_{i}.json"
                path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                staging_files.append(path)
                rel = doc.get("relationship", {})
                n_ok = len([c for c in doc.get("candidates", []) if c.get("agent_approved")])
                self._step("verify", f"staged {doc['counterparty']['id']} ({rel.get('type')}): {n_ok} approved")

        # 5) merge
        for path in staging_files:
            self._step("merge", f"merging {path.name}")
            self._run_script("merge_staged.py", ["--staging", str(path), "--data", str(target_dir)])

        # 6) score + validate
        self._step("score", "engine recompute (sync_scores --write)")
        self._run_script("sync_scores.py", ["--data", str(target_dir), "--write"])
        self._step("check", "independent validation (validate_data.py)")
        self._run_script("validate_data.py", ["--data", str(target_dir)])

        rels = json.loads((target_dir / "relationships.json").read_text(encoding="utf-8"))
        evs = json.loads((target_dir / "evidence.json").read_text(encoding="utf-8"))
        comps = json.loads((target_dir / "companies.json").read_text(encoding="utf-8"))
        result = {
            "target_id": tid,
            "name": identity["name"],
            "companies": len(comps),
            "relationships": len(rels),
            "evidence": len(evs),
        }
        self._step("done", json.dumps(result, ensure_ascii=False))
        return result

    # -- LLM steps ----------------------------------------------------------

    def _resolve_identity(self, query: str) -> dict[str, Any]:
        system = (
            "You are a financial research assistant. Resolve a company query to a "
            "canonical identity. Reply with ONLY a JSON object: "
            '{"id": "<lowercase_slug_a-z0-9_>", "name": "<full official name>", '
            '"stock_code": "<ticker or empty>", "exchange": "<exchange or empty>", '
            '"country": "<ISO 2-letter>", "sector": "<sector>", '
            '"description": "<2-3 sentence description incl. what the company does and key facts>"}. '
            "If the company is not publicly listed, stock_code and exchange are empty strings."
        )
        out = _extract_json(self.llm_fn(system, f"Company query: {query}"))
        if not re.fullmatch(r"[a-z0-9_]+", out.get("id", "")):
            raise ValueError(f"LLM returned invalid target id: {out.get('id')!r}")
        if not out.get("name") or not out.get("description"):
            raise ValueError("LLM identity resolution incomplete (need name + description)")
        return out

    def _verify_batch(self, identity: dict, category: str, hits: list[dict]) -> list[dict]:
        system = (
            "You are a supply-chain research agent following a strict evidence protocol. "
            "Given a research target and search hits, identify REAL business relationships "
            f"of types: {REL_TYPES}. Rules: "
            "(1) entity disambiguation — confirm the counterparty is the right legal entity (ticker/full name); "
            "(2) co-occurrence guard — mere co-mention (e.g. stock prices moving together) is NOT a relationship; require a relational verb (supplies, invests, partners, competes); "
            "(3) every candidate needs source_url, publisher, source_type (sec_filing|exchange_filing|government|company_ir|company_press_release|business_media|analyst_research|industry_database|reference|informal|unknown), "
            "published_at (yyyy-mm-dd or null), evidence_locator, and an exact verbatim quote from the snippet supporting the relationship; "
            "(4) only relationships involving the research target; counterparties should preferably be listed companies; "
            "(5) reject weak/ambiguous evidence by NOT including it. "
            "Reply with ONLY a JSON array of staging objects: "
            '[{"counterparty": {"id","name","stock_code","exchange","isin","country","entity_type":"related","sector","description"}, '
            '"relationship": {"type","direction","valid_from","valid_until":null,"summary"}, '
            '"candidates": [{"title","source_url","publisher","source_type","published_at","accessed_at","access_restriction":"public","evidence_locator","quote","license_note","agent_approved":true,"needs_review":[],"agent_review_notes"}]}]. '
            "Return an empty array [] if no hit supports a verifiable relationship."
        )
        hits_text = "\n\n".join(
            f"[{i}] {h.get('title','')}\nURL: {h.get('url','')}\nDate: {h.get('published_at') or 'unknown'}\nSnippet: {h.get('snippet','')}"
            for i, h in enumerate(hits)
        )
        user = (
            f"Research target: {identity['name']} (id: {identity['id']}, {identity.get('stock_code') or 'unlisted'})\n"
            f"Category: {category}\nAccessed date: {date.today().isoformat()}\n\nSearch hits:\n{hits_text}"
        )
        docs = _extract_json(self.llm_fn(system, user))
        if not isinstance(docs, list):
            raise ValueError("LLM verification did not return a JSON array")
        # sanitize: keep only well-formed docs with approved, quoted candidates
        clean = []
        for doc in docs:
            cp, rel, cands = doc.get("counterparty"), doc.get("relationship"), doc.get("candidates", [])
            if not cp or not cp.get("id") or not rel or rel.get("type") not in REL_TYPES.split("|"):
                continue
            ok = [c for c in cands if c.get("agent_approved") and str(c.get("quote", "")).strip()
                  and str(c.get("source_url", "")).startswith("http") and str(c.get("evidence_locator", "")).strip()]
            if not ok:
                continue
            for c in ok:
                c.setdefault("accessed_at", date.today().isoformat())
                c.setdefault("access_restriction", "public")
                c.setdefault("license_note", "Public web publication; quoted for research with attribution.")
                c.setdefault("needs_review", [])
                c["agent_approved"] = True
            doc["candidates"] = ok
            doc.setdefault("staging_version", "1.0")
            doc["research_target"] = identity["id"]
            doc["generated_at"] = date.today().isoformat()
            doc["protocol"] = "docs/research_agent_protocol.md"
            clean.append(doc)
        return clean

    # -- helpers ------------------------------------------------------------

    def _run_script(self, name: str, args: list[str]) -> None:
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / name), *args],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )
        if proc.returncode != 0:
            raise RuntimeError(f"{name} failed: {proc.stdout[-500:]} {proc.stderr[-500:]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="Company name / ticker to research")
    ap.add_argument("--data-root", default=str(PROJECT_ROOT / "data"))
    args = ap.parse_args()

    agent = ResearchAgent(
        args.data_root,
        on_step=lambda name, detail: print(f"[{name:8s}] {detail}"),
    )
    try:
        result = agent.run(args.query)
    except Exception as exc:
        print(f"research failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
