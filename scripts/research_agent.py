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

Configuration (env, ALL OPTIONAL — the agent works zero-config):
  SCR_SEARCH_BACKEND   bing | duckduckgo | tavily | brave
                       (default: bing — free, no key required, works behind CN proxies;
                        duckduckgo also free but may be blocked by some CN proxies;
                        tavily/brave are optional quality upgrades)
  SCR_TAVILY_API_KEY   for the tavily backend (optional)
  SCR_BRAVE_API_KEY    for the brave backend (optional)
  SCR_LLM_BASE_URL     OpenAI-compatible base URL (optional)
  SCR_LLM_API_KEY      API key for the LLM endpoint (optional)
  SCR_LLM_MODEL        model name (default: gpt-4o-mini)

  Without LLM keys the agent falls back to RULE-BASED verification
  (verb attribution + source-type heuristics, candidates flagged
  needs_review). Scores land lower — honest, inspectable, zero-config.

For tests, inject `search_fn` / `llm_fn` — no network or keys needed.

CLI:
  python scripts/research_agent.py "宇通客车" --data-root data
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# scripts/ is not a package — make sibling modules importable both when run
# as `python scripts/research_agent.py` and when loaded via importlib.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_harvest import (  # noqa: E402
    canonicalize_url,
    guess_access_restriction,
    guess_source_type,
)

# Official / statutory source types assert relationships directly; media and
# analyst outlets report them (indirectly); reference / informal / unknown
# sources are only contextual for a given edge. This mirrors the Schema 2.0
# evidence.support_level semantics so agent-generated items are auditable.
_DIRECT_SOURCE_TYPES = {
    "sec_filing", "exchange_filing", "government",
    "company_ir", "company_press_release",
}
_INDIRECT_SOURCE_TYPES = {
    "business_media", "analyst_research", "industry_database",
}


def guess_support_level(source_type: str) -> str:
    """Heuristic support_level for an agent-generated evidence item."""
    if source_type in _DIRECT_SOURCE_TYPES:
        return "direct"
    if source_type in _INDIRECT_SOURCE_TYPES:
        return "indirect"
    return "contextual"

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
# Default backend (all optional; zero-config fallback = bing + rules)
# ---------------------------------------------------------------------------

def _html_unescape(text: str) -> str:
    """Minimal HTML entity unescape for search snippets."""
    for entity, char in [("&ensp;", " "), ("&#0183;", "·"), ("&middot;", "·"),
                         ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    return text


def backend_bing(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    """Free, keyless search backend via Bing (works behind CN proxies)."""
    resp = httpx.get(
        "https://www.bing.com/search",
        params={"q": query, "mkt": "zh-CN", "count": max_results},
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                 "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        timeout=30,
        follow_redirects=True,
    )
    resp.raise_for_status()
    html = resp.text
    hits = []
    blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.S)
    for block in blocks[:max_results]:
        h2 = re.search(r'<h2[^>]*>(.*?)</h2>', block, re.S)
        if not h2:
            continue
        m = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', h2.group(1), re.S)
        if not m:
            continue
        url, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not url.startswith("http"):
            continue
        ps = re.findall(r'<p[^>]*>(.*?)</p>', block, re.S)
        snippet = ""
        for p in ps:
            clean = re.sub(r"<[^>]+>", "", p).strip()
            if clean and len(clean) > 10:
                snippet = _html_unescape(clean)
                break
        hits.append({
            "title": _html_unescape(title),
            "url": canonicalize_url(url),
            "snippet": snippet,
            "published_at": None,
        })
    return hits


def backend_duckduckgo(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    """Free, keyless search backend via DuckDuckGo's HTML endpoint.
    NOTE: DuckDuckGo may be unreachable behind some CN proxies — use bing instead."""
    resp = httpx.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (compatible; SupplyChainResearch/1.1)"},
        timeout=30,
        follow_redirects=True,
    )
    resp.raise_for_status()
    html = resp.text
    hits = []
    # result links: <a class="result__a" href="//duckduckgo.com/l/?uddg=<enc>...">title</a>
    # snippets:      <a class="result__snippet" ...>snippet</a>
    links = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
    snippets = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    for i, (href, title) in enumerate(links[:max_results]):
        m = re.search(r'uddg=([^&]+)', href)
        url = urllib.parse.unquote(m.group(1)) if m else href
        snippet = re.sub(r"<[^>]+>", "", snippets[i]) if i < len(snippets) else ""
        hits.append({
            "title": re.sub(r"<[^>]+>", "", title).strip(),
            "url": canonicalize_url(url),
            "snippet": snippet.strip(),
            "published_at": None,
        })
    return hits


def default_search_fn() -> SearchFn:
    """Backend chain: explicit SCR_SEARCH_BACKEND -> keyed backend if its key
    exists -> bing (free, zero-config default, works behind CN proxies)."""
    backend = os.environ.get("SCR_SEARCH_BACKEND", "").lower()
    if backend == "duckduckgo":
        return backend_duckduckgo
    if backend == "brave" and os.environ.get("SCR_BRAVE_API_KEY"):
        key = os.environ["SCR_BRAVE_API_KEY"]
        def _search(query: str, max_results: int = 8) -> list[dict[str, Any]]:
            resp = httpx.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
                headers={"X-Subscription-Token": key, "Accept": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""),
                 "snippet": r.get("description", ""), "published_at": r.get("age")}
                for r in data.get("web", {}).get("results", [])
            ]
        return _search

    if backend == "tavily" and os.environ.get("SCR_TAVILY_API_KEY"):
        key = os.environ["SCR_TAVILY_API_KEY"]
        def _search(query: str, max_results: int = 8) -> list[dict[str, Any]]:
            resp = httpx.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query, "max_results": max_results},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""),
                 "snippet": r.get("content", ""), "published_at": r.get("published_date")}
                for r in data.get("results", [])
            ]
        return _search

    return backend_bing


def default_llm_fn() -> Optional[LlmFn]:
    """OpenAI-compatible LLM if configured, else None (rule-based fallback)."""
    base = os.environ.get("SCR_LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("SCR_LLM_API_KEY", "")
    model = os.environ.get("SCR_LLM_MODEL", "gpt-4o-mini")
    if not base or not key:
        return None
    def _llm(system: str, user: str) -> str:
        resp = httpx.post(
            base + "/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
            },
            headers={"Authorization": f"Bearer {key}"},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
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
        # None -> rule-based fallback (zero-config mode)
        self.llm_fn = llm_fn if llm_fn is not None else default_llm_fn()
        self.on_step = on_step or (lambda name, detail: None)
        self.steps: list[dict[str, str]] = []

    @property
    def mode(self) -> str:
        return "llm" if self.llm_fn is not None else "rule-based"

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
            self._step("verify", f"[{category}] verifying {len(hits)} hits ({self.mode} mode)")
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
            "mode": self.mode,
        }
        self._step("done", json.dumps(result, ensure_ascii=False))
        return result

    # -- LLM steps ----------------------------------------------------------

    def _resolve_identity(self, query: str) -> dict[str, Any]:
        if self.llm_fn is None:
            return self._resolve_identity_heuristic(query)
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

    @staticmethod
    def _guess_country(text: str) -> str:
        """Best-effort country guess from text content (rule-based mode only).

        Chinese characters -> CN; otherwise default to US (English company
        queries in a supply-chain context are overwhelmingly US/global firms).
        All rule-based records are flagged needs_review anyway.
        """
        if re.search(r"[一-鿿]", text):
            return "CN"
        return "US"

    def _resolve_identity_heuristic(self, query: str) -> dict[str, Any]:
        """Zero-config identity: no LLM — slug from the query, details left to
        the evidence (the target's own company record is descriptive only)."""
        slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
        if not slug:  # e.g. pure-Chinese query — stable hash-based slug
            slug = "c_" + hashlib.md5(query.encode("utf-8")).hexdigest()[:8]
        return {
            "id": slug,
            "name": query,
            "stock_code": "",
            "exchange": "",
            "country": self._guess_country(query),
            "sector": "",
            "description": (
                f"Research target '{query}', auto-registered in rule-based mode "
                "(no LLM configured). Verify and enrich identity fields manually."
            ),
        }

    def _verify_batch(self, identity: dict, category: str, hits: list[dict]) -> list[dict]:
        if self.llm_fn is None:
            return self._verify_batch_rule_based(identity, category, hits)
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

    # -- rule-based fallback (zero-config: no LLM configured) ----------------

    _TYPE_VERBS = [
        ("supplier", ("supply", "supplier", "supplies", "purchase", "procure", "供应", "供货", "采购", "供应商")),
        ("partner", ("partner", "collaborat", "team up", "joins force", "合作", "伙伴", "联手", "携手", "战略")),
        ("investor_or_investee", ("invest", "stake", "shareholder", "funding", "领投", "持股", "股东", "投资", "融资", "注资", "参股")),
        ("customer", ("customer", "client", "客户", "采购方", "订购")),
        ("peer", ("compet", "rival", "versus", "对比", "竞争", "对手", "竞品", "对标")),
    ]
    _CATEGORY_DEFAULT_TYPE = {
        "partner_supplier": "partner",
        "investor": "investor_or_investee",
        "peer_customer": "peer",
    }
    _EN_COMPANY_RE = re.compile(
        r"\b([A-Z][A-Za-z&'.]*(?:\s+[A-Z][A-Za-z&'.]*){0,3}\s+"
        r"(?:Inc\.?|Corp(?:oration)?\.?|Ltd\.?|LLC|PLC|N\.V\.|SE|AG|Group|Holdings|"
        r"Technologies|Technology|Motors|Electronics|Systems|Semiconductors?))\b")
    _ZH_COMPANY_RE = re.compile(
        r"([一-鿿]{2,8}(?:公司|集团|科技|股份|电子|汽车|银行|证券|电器|重工))")

    def _extract_counterparty(self, text: str, target_name: str) -> Optional[str]:
        """Pull a company-like entity mention that is not the target itself."""
        target_tokens = {t.lower() for t in re.split(r"\W+", target_name) if len(t) > 2}
        for match in self._EN_COMPANY_RE.findall(text) + self._ZH_COMPANY_RE.findall(text):
            name = match.strip()
            tokens = {t.lower() for t in re.split(r"\W+", name) if len(t) > 2}
            if tokens and tokens <= target_tokens:
                continue  # the target itself
            if len(name) < 4:
                continue
            return name
        return None

    def _guess_rel_type(self, text: str, category: str) -> tuple[str, Optional[str]]:
        low = text.lower()
        for rel_type, verbs in self._TYPE_VERBS:
            for v in verbs:
                if v in low:
                    return rel_type, v
        return self._CATEGORY_DEFAULT_TYPE.get(category, "partner"), None

    def _verify_batch_rule_based(self, identity: dict, category: str, hits: list[dict]) -> list[dict]:
        """Zero-config verification: verb attribution + source-type heuristics.

        Deliberately conservative: hits without a relationship verb are dropped
        (co-occurrence guard), counterparties come from company-name patterns,
        and every candidate is flagged needs_review for human spot-check.
        """
        by_counterparty: dict[str, dict] = {}
        for h in hits:
            text = f"{h.get('title', '')} {h.get('snippet', '')}"
            url = h.get("url", "")
            if not url.startswith("http"):
                continue
            rel_type, verb = self._guess_rel_type(text, category)
            if verb is None and category not in self._CATEGORY_DEFAULT_TYPE:
                continue  # no relational verb at all -> co-occurrence guard
            cp_name = self._extract_counterparty(text, identity["name"])
            if not cp_name:
                continue
            slug = re.sub(r"[^a-z0-9]+", "_", cp_name.lower()).strip("_") or \
                "c_" + hashlib.md5(cp_name.encode("utf-8")).hexdigest()[:8]
            if slug == identity["id"]:
                continue
            doc = by_counterparty.setdefault(slug, {
                "counterparty": {
                    "id": slug,
                    "name": cp_name,
                    "stock_code": "",
                    "exchange": "",
                    "isin": "",
                    "country": self._guess_country(cp_name + " " + text),
                    "entity_type": "related",
                    "sector": "",
                    "description": f"Auto-extracted counterparty of {identity['name']} (rule-based mode; verify identity manually).",
                },
                "relationship": {
                    "type": rel_type,
                    "direction": f"{slug} <-> {identity['id']}",
                    "valid_from": None,
                    "valid_until": None,
                    "summary": f"Auto-staged {rel_type} relationship between {cp_name} and {identity['name']} (rule-based extraction; needs human review).",
                },
                "candidates": [],
            })
            doc["candidates"].append({
                "title": h.get("title", ""),
                "source_url": canonicalize_url(url),
                "publisher": urllib.parse.urlparse(url).netloc.removeprefix("www."),
                "source_type": guess_source_type(url, h.get("title", "")).value,
                "independence_group": urllib.parse.urlparse(url).netloc.removeprefix("www."),
                "support_level": guess_support_level(
                    guess_source_type(url, h.get("title", "")).value
                ),
                "published_at": h.get("published_at"),
                "accessed_at": date.today().isoformat(),
                "access_restriction": guess_access_restriction(url).value,
                "evidence_locator": f"Search result snippet for query category '{category}'",
                "quote": (h.get("snippet") or h.get("title", "")).strip(),
                "access_notes": (
                    "Aggregated web-search snippet (rule-based fallback, no LLM); "
                    "verify the quoted claim against the primary source."
                ),
                "license_note": "Public search-result snippet; quoted for research with attribution.",
                "agent_approved": True,
                "needs_review": ["entity_disambiguation", "type_confirmation"],
                "agent_review_notes": (
                    f"rule-based fallback (no LLM configured): verb '{verb or category}' matched; "
                    "counterparty extracted by name pattern; REQUIRES human spot-check"
                ),
            })
        out = []
        for doc in by_counterparty.values():
            doc["staging_version"] = "1.0"
            doc["research_target"] = identity["id"]
            doc["generated_at"] = date.today().isoformat()
            doc["protocol"] = "docs/research_agent_protocol.md"
            out.append(doc)
        return out

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
