"""FastAPI HTTP API for the supply chain & partnership research service.

Endpoints
---------
- GET /health                     dataset / service health
- GET /api/v1/stats               dataset statistics
- GET /api/v1/companies           list companies (filter + pagination)
- GET /api/v1/companies/{id}      single company
- GET /api/v1/relationships       list relationships (filters + pagination)
- GET /api/v1/relationships/{id}  relationship detail incl. evidence + score breakdown
- GET /api/v1/relationships/{id}/evidence   evidence for a relationship
- GET /api/v1/evidence/{id}       single evidence
- GET /api/v1/graph               relationship graph (nodes + edges)

Every relationship carries its 0-100 confidence score; the detail endpoint
additionally returns the per-dimension `score_breakdown` that explains how
the score was computed (authority / evidence_quality / recency /
specificity / quantifiability).
"""

from __future__ import annotations

import importlib.util
import os
import re
import threading
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path as _Path
from pydantic import BaseModel

from .models import HealthResponse
from .scoring import score_relationship
from .store import DatasetError, Store, TargetRegistry

app = FastAPI(
    title="Supply Chain & Partnership Research Service",
    version="1.1.0",
    description=(
        "Reproducible supply chain & partnership research over committed "
        "JSON datasets. Multi-target: data/targets.json registers research "
        "targets (default: nvidia); switch via the ?target= query param or "
        "GET /api/v1/targets. New targets are onboarded with "
        "scripts/onboard_target.py + the agent research protocol."
    ),
)

_stores: dict[str, Store] = {}
_registry: Optional[TargetRegistry] = None


def get_registry() -> TargetRegistry:
    """Lazily load the target registry (once per process).

    Root resolution: SCR_DATA_ROOT > SCR_DATA_DIR > ./data. SCR_DATA_DIR
    may point either at a multi-target root (with targets.json) or, for
    backward compatibility, directly at a single dataset directory.
    """
    global _registry
    if _registry is None:
        root = (
            os.environ.get("SCR_DATA_ROOT")
            or os.environ.get("SCR_DATA_DIR")
            or "./data"
        )
        _registry = TargetRegistry.load(root)
    return _registry


def get_store(target: Optional[str] = None) -> Store:
    """Load (and cache) the store for one research target.

    Resolution order: explicit ?target= param -> SCR_TARGET env ->
    registry default_target. Legacy mode: if SCR_DATA_DIR points directly
    at a dataset directory, the registry falls back to that single target.
    """
    registry = get_registry()
    tid = target or os.environ.get("SCR_TARGET") or registry.default_target
    if tid not in _stores:
        _stores[tid] = Store.load(str(registry.target_dir(tid)))
    return _stores[tid]


def _resolve_store(target: Optional[str]) -> Store:
    try:
        return get_store(target)
    except DatasetError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "target_not_found", "message": str(exc)},
        ) from exc


def _company_names(store: Store) -> dict[str, str]:
    return {cid: c.name for cid, c in store.companies.items()}


def _relationship_detail(store: Store, rel) -> dict:
    """Relationship payload with its explainable score breakdown."""
    data = rel.model_dump(mode="json")
    evidence = store.list_evidence_for_relationship(rel.id)
    data["evidence"] = [e.model_dump(mode="json") for e in evidence]
    breakdown = score_relationship(
        rel,
        evidence,
        store.dataset.as_of,
        company_names=_company_names(store),
        research_target_id=store.dataset.research_target,
    )
    data["score_breakdown"] = breakdown.to_dict()
    return data


@app.exception_handler(DatasetError)
async def dataset_error_handler(request, exc: DatasetError):
    return JSONResponse(
        status_code=500,
        content={"error": "dataset_error", "message": str(exc)},
    )


@app.get("/", tags=["ui"], include_in_schema=False)
def dashboard() -> HTMLResponse:
    """Serve the interactive HTML dashboard (self-contained, zero-dependency)."""
    candidates = [
        _Path(__file__).resolve().parent.parent / "dashboard.html",
        _Path("/app/dashboard.html"),
        _Path("/app/static/dashboard.html"),
    ]
    for p in candidates:
        if p.is_file():
            return HTMLResponse(p.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>Dashboard not found</h1><p>dashboard.html is missing. "
        "Use the <a href='/docs'>API docs</a> instead.</p>",
        status_code=404,
    )


@app.get("/dashboard", tags=["ui"], include_in_schema=False)
def dashboard_redirect() -> HTMLResponse:
    """Alias for the dashboard."""
    return dashboard()


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health(target: Optional[str] = Query(None, description="Research target id")) -> HealthResponse:
    store = _resolve_store(target)
    stats = store.stats()
    return HealthResponse(
        status="ok",
        dataset=store.dataset.research_target,
        as_of=store.dataset.as_of,
        companies=stats["companies"],
        relationships=stats["relationships"],
        evidence=stats["evidence"],
        server_time=datetime.now(timezone.utc),
    )


@app.get("/api/v1/targets", tags=["meta"])
def list_targets():
    """List all registered research targets (data/targets.json)."""
    registry = get_registry()
    return {
        "default_target": registry.default_target,
        "targets": registry.summary(),
    }


@app.get("/api/v1/targets/{target_id}/dataset", tags=["meta"])
def target_dataset(target_id: str):
    """Full dataset snapshot for one target, in the dashboard's DATA shape
    (used by the dashboard to lazy-load dynamically researched targets)."""
    store = _resolve_store(target_id)
    return {
        "dataset": {
            "schema_version": store.dataset.schema_version,
            "as_of": store.dataset.as_of.isoformat(),
            "research_target": store.dataset.research_target,
        },
        "companies": {cid: c.model_dump(mode="json") for cid, c in store.companies.items()},
        "relationships": [
            {
                **r.model_dump(mode="json"),
                "evidence_items": [
                    e.model_dump(mode="json")
                    for e in store.list_evidence_for_relationship(r.id)
                ],
            }
            for r in store.relationships
        ],
        "evidence": {eid: e.model_dump(mode="json") for eid, e in store.evidence.items()},
    }


@app.get("/api/v1/stats", tags=["meta"])
def stats(target: Optional[str] = Query(None, description="Research target id")):
    store = _resolve_store(target)
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for rel in store.relationships:
        by_type[rel.type.value] = by_type.get(rel.type.value, 0) + 1
        by_status[rel.status.value] = by_status.get(rel.status.value, 0) + 1
    return {
        **store.stats(),
        "research_target": store.dataset.research_target,
        "relationships_by_type": dict(sorted(by_type.items())),
        "relationships_by_status": dict(sorted(by_status.items())),
    }


@app.get("/api/v1/companies", tags=["companies"])
def list_companies(
    name: Optional[str] = Query(None, description="Substring match on name or id"),
    entity_type: Optional[str] = Query(
        None, pattern="^(target|related)$", description="'target' or 'related'"
    ),
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    target: Optional[str] = Query(None, description="Research target id"),
):
    store = _resolve_store(target)
    return store.list_companies(
        name=name, entity_type=entity_type, page=page, page_size=page_size
    ).to_dict()


@app.get("/api/v1/companies/{company_id}", tags=["companies"])
def get_company(
    company_id: str,
    target: Optional[str] = Query(None, description="Research target id"),
):
    store = _resolve_store(target)
    company = store.get_company(company_id)
    if company is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "company_not_found", "message": f"Unknown company id '{company_id}'"},
        )
    return company.model_dump(mode="json")


@app.get("/api/v1/relationships", tags=["relationships"])
def list_relationships(
    company_id: Optional[str] = Query(None, description="Relationships involving this company"),
    relationship_type: Optional[str] = Query(
        None,
        pattern="^(supplier|customer|partner|investor_or_investee|peer)$",
        description="Relationship category",
    ),
    status: Optional[str] = Query(
        None, pattern="^(confirmed|inferred|unknown)$", description="Epistemic status"
    ),
    min_confidence: Optional[int] = Query(None, ge=0, le=100, description="Inclusive lower bound"),
    max_confidence: Optional[int] = Query(None, ge=0, le=100, description="Inclusive upper bound"),
    valid_as_of: Optional[date] = Query(
        None, description="Only relationships valid on this date (ISO yyyy-mm-dd)"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    target: Optional[str] = Query(None, description="Research target id"),
):
    if (
        min_confidence is not None
        and max_confidence is not None
        and min_confidence > max_confidence
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_range",
                "message": "min_confidence must be <= max_confidence",
            },
        )
    store = _resolve_store(target)
    return store.list_relationships(
        company_id=company_id,
        relationship_type=relationship_type,
        min_confidence=min_confidence,
        max_confidence=max_confidence,
        status=status,
        valid_as_of=valid_as_of,
        page=page,
        page_size=page_size,
    ).to_dict()


@app.get("/api/v1/relationships/{relationship_id}", tags=["relationships"])
def get_relationship(
    relationship_id: str,
    target: Optional[str] = Query(None, description="Research target id"),
):
    store = _resolve_store(target)
    rel = store.get_relationship(relationship_id)
    if rel is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "relationship_not_found",
                "message": f"Unknown relationship id '{relationship_id}'",
            },
        )
    return _relationship_detail(store, rel)


@app.get("/api/v1/relationships/{relationship_id}/evidence", tags=["relationships"])
def relationship_evidence(
    relationship_id: str,
    target: Optional[str] = Query(None, description="Research target id"),
):
    store = _resolve_store(target)
    rel = store.get_relationship(relationship_id)
    if rel is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "relationship_not_found",
                "message": f"Unknown relationship id '{relationship_id}'",
            },
        )
    return {"relationship_id": relationship_id, "evidence": [
        e.model_dump(mode="json") for e in store.list_evidence_for_relationship(relationship_id)
    ]}


@app.get("/api/v1/evidence/{evidence_id}", tags=["evidence"])
def get_evidence(
    evidence_id: str,
    target: Optional[str] = Query(None, description="Research target id"),
):
    store = _resolve_store(target)
    evidence = store.get_evidence(evidence_id)
    if evidence is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "evidence_not_found", "message": f"Unknown evidence id '{evidence_id}'"},
        )
    return evidence.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Online research agent (search -> agent verify -> merge -> cache -> serve)
# ---------------------------------------------------------------------------

class ResearchRequest(BaseModel):
    query: str


_research_jobs: dict[str, dict] = {}
_research_lock = threading.Lock()


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _agent_mode_info() -> str:
    """Describe the active research agent configuration (informational only).

    The agent is always usable — DuckDuckGo is the zero-config search default
    and rule-based verification kicks in when no LLM key is present.  Tavily /
    Brave / LLM keys are optional quality upgrades.
    """
    backend = os.environ.get("SCR_SEARCH_BACKEND", "").lower()
    if backend == "brave" and os.environ.get("SCR_BRAVE_API_KEY"):
        search = "brave"
    elif backend == "tavily" and os.environ.get("SCR_TAVILY_API_KEY"):
        search = "tavily"
    else:
        search = "duckduckgo"
    llm = "llm" if (os.environ.get("SCR_LLM_BASE_URL") and os.environ.get("SCR_LLM_API_KEY")) else "rule-based"
    return f"{search}+{llm}"


def _load_research_agent_class():
    """Import scripts/research_agent.py (scripts/ is not a package)."""
    script = _Path(__file__).resolve().parent.parent / "scripts" / "research_agent.py"
    spec = importlib.util.spec_from_file_location("research_agent", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ResearchAgent


def _run_research_job(job_id: str, query: str) -> None:
    global _registry
    job = _research_jobs[job_id]
    try:
        agent_cls = _load_research_agent_class()
        root = (
            os.environ.get("SCR_DATA_ROOT")
            or os.environ.get("SCR_DATA_DIR")
            or "./data"
        )
        agent = agent_cls(
            root,
            on_step=lambda name, detail: job["steps"].append(
                {"step": name, "detail": detail, "at": datetime.now(timezone.utc).isoformat()}
            ),
        )
        result = agent.run(query)
        # Invalidate registry + store caches so the new target is servable.
        with _research_lock:
            _registry = None
            _stores.pop(result["target_id"], None)
        job["status"] = "done"
        job["result"] = result
    except Exception as exc:  # agent / script / validation failure
        job["status"] = "failed"
        job["error"] = str(exc)
    job["finished_at"] = datetime.now(timezone.utc).isoformat()


@app.post("/api/v1/research", tags=["research"], status_code=202)
def start_research(req: ResearchRequest):
    """Start an async research job for a new company.

    The job runs the full agent pipeline (resolve identity -> onboard ->
    search -> LLM verification -> merge -> rescore -> validate). On success
    the new target is registered in targets.json, cached on disk under
    data/targets/<id>/, and immediately servable via ?target=<id> and the
    dashboard switcher.
    """
    query = req.query.strip()
    if not (2 <= len(query) <= 120):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_query", "message": "query must be 2-120 characters"},
        )
    slug = _slugify(query)
    registry = get_registry()
    if slug in registry.targets:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "target_exists",
                "message": f"target '{slug}' already registered — use ?target={slug} instead",
            },
        )
    with _research_lock:
        for j in _research_jobs.values():
            if j["status"] == "running" and _slugify(j["query"]) == slug:
                return {"job_id": j["job_id"], "status": "running", "deduplicated": True}
        job_id = uuid.uuid4().hex[:12]
        _research_jobs[job_id] = {
            "job_id": job_id,
            "query": query,
            "status": "running",
            "steps": [],
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }
        threading.Thread(target=_run_research_job, args=(job_id, query), daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@app.get("/api/v1/research/{job_id}", tags=["research"])
def research_status(job_id: str):
    """Poll a research job: running (with step trace) -> done | failed."""
    job = _research_jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "job_not_found", "message": f"Unknown research job '{job_id}'"},
        )
    return job


@app.get("/api/v1/graph", tags=["graph"])
def graph(target: Optional[str] = Query(None, description="Research target id")):
    """Relationship graph: nodes are companies, edges are relationships."""
    store = _resolve_store(target)
    nodes = [
        {
            "id": c.id,
            "name": c.name,
            "entity_type": c.entity_type.value,
            "stock_code": c.stock_code,
            "exchange": c.exchange,
            "country": c.country,
            "sector": c.sector,
        }
        for c in store.companies.values()
    ]
    edges = [
        {
            "id": r.id,
            "source": r.source_company_id,
            "target": r.target_company_id,
            "type": r.type.value,
            "direction": r.direction,
            "status": r.status.value,
            "confidence_score": r.confidence_score,
            "valid_from": r.valid_from.isoformat() if r.valid_from else None,
            "valid_until": r.valid_until.isoformat() if r.valid_until else None,
        }
        for r in store.relationships
    ]
    return {
        "research_target": store.dataset.research_target,
        "as_of": store.dataset.as_of.isoformat(),
        "nodes": nodes,
        "edges": edges,
    }


def run() -> None:
    """Programmatic entry point for `python -m src.api`."""
    import uvicorn

    uvicorn.run(
        "src.api:app",
        host=os.environ.get("SCR_HOST", "127.0.0.1"),
        port=int(os.environ.get("SCR_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    run()
