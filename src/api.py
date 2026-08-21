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

import os
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path as _Path

from .models import HealthResponse
from .scoring import score_relationship
from .store import DatasetError, Store

app = FastAPI(
    title="Supply Chain & Partnership Research Service",
    version="1.0.0",
    description=(
        "Reproducible supply chain & partnership research over a committed "
        "JSON dataset. Company-agnostic: point SCR_DATA_DIR at any dataset "
        "that follows the schema (dataset.json / companies.json / "
        "relationships.json / evidence.json)."
    ),
)

_store: Optional[Store] = None


def get_store() -> Store:
    """Lazily load the dataset store (once per process)."""
    global _store
    if _store is None:
        _store = Store.load(os.environ.get("SCR_DATA_DIR", "./data"))
    return _store


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
def health() -> HealthResponse:
    store = get_store()
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


@app.get("/api/v1/stats", tags=["meta"])
def stats():
    store = get_store()
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
):
    store = get_store()
    return store.list_companies(
        name=name, entity_type=entity_type, page=page, page_size=page_size
    ).to_dict()


@app.get("/api/v1/companies/{company_id}", tags=["companies"])
def get_company(company_id: str):
    store = get_store()
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
    store = get_store()
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
def get_relationship(relationship_id: str):
    store = get_store()
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
def relationship_evidence(relationship_id: str):
    store = get_store()
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
def get_evidence(evidence_id: str):
    store = get_store()
    evidence = store.get_evidence(evidence_id)
    if evidence is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "evidence_not_found", "message": f"Unknown evidence id '{evidence_id}'"},
        )
    return evidence.model_dump(mode="json")


@app.get("/api/v1/graph", tags=["graph"])
def graph():
    """Relationship graph: nodes are companies, edges are relationships."""
    store = get_store()
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
