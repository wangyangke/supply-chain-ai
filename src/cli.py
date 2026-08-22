"""Typer CLI for the supply chain & partnership research service.

Usage (installed as `scrs`, or `python -m src.cli`):

  scrs targets                          # list registered research targets
  scrs [--target unitree] health
  scrs [--target nvidia] stats
  scrs companies [--name nvidia] [--entity-type related] [--json]
  scrs company <company-id>
  scrs relationships [--company nvidia] [--type supplier] [--status confirmed]
                     [--min-score 70] [--valid-as-of 2026-08-21] [--json]
  scrs relationship <relationship-id> [--json]
  scrs evidence <evidence-id>
  scrs score <relationship-id>        # recompute + explain a confidence score
  scrs graph [--json]

Multi-target: the global --target option (or SCR_TARGET env) selects the
research target from data/targets.json (default: nvidia). Data root
resolution: SCR_DATA_ROOT > SCR_DATA_DIR > ./data; SCR_DATA_DIR may also
point directly at a single legacy dataset directory.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date as date_type
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .scoring import score_relationship
from .store import Store, TargetRegistry

app = typer.Typer(
    name="scrs",
    help="Reproducible supply chain & partnership research service.",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


@app.callback()
def _global_options(
    target: Optional[str] = typer.Option(
        None, "--target", "-t",
        help="Research target id from data/targets.json (default: registry default, e.g. nvidia)",
    ),
) -> None:
    """Select the research target for all commands."""
    if target:
        os.environ["SCR_TARGET"] = target


def _load_store() -> Store:
    root = (
        os.environ.get("SCR_DATA_ROOT")
        or os.environ.get("SCR_DATA_DIR")
        or "./data"
    )
    try:
        registry = TargetRegistry.load(root)
        tid = os.environ.get("SCR_TARGET") or registry.default_target
        return Store.load(str(registry.target_dir(tid)))
    except Exception as exc:  # DatasetError / FileNotFoundError
        err_console.print(f"[bold red]Failed to load dataset from {root}:[/] {exc}")
        raise typer.Exit(code=1)


def _company_names(store: Store) -> dict[str, str]:
    return {cid: c.name for cid, c in store.companies.items()}


def _print_json(payload) -> None:
    # soft_wrap keeps JSON machine-parseable even in narrow/redirected
    # terminals (rich would otherwise wrap long lines mid-string).
    console.print(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        soft_wrap=True,
    )


@app.command()
def health(
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON"),
) -> None:
    """Show dataset / service health."""
    store = _load_store()
    stats = store.stats()
    payload = {
        "status": "ok",
        "dataset": store.dataset.research_target,
        "as_of": store.dataset.as_of.isoformat(),
        "schema_version": store.dataset.schema_version,
        **stats,
    }
    if as_json:
        _print_json(payload)
        return
    table = Table(title=f"Health — {store.dataset.research_target}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    for k, v in payload.items():
        table.add_row(k, str(v))
    console.print(table)


@app.command()
def stats(
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON"),
) -> None:
    """Show dataset statistics, including breakdowns by type and status."""
    store = _load_store()
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for rel in store.relationships:
        by_type[rel.type.value] = by_type.get(rel.type.value, 0) + 1
        by_status[rel.status.value] = by_status.get(rel.status.value, 0) + 1
    payload = {
        **store.stats(),
        "research_target": store.dataset.research_target,
        "relationships_by_type": by_type,
        "relationships_by_status": by_status,
    }
    if as_json:
        _print_json(payload)
        return
    table = Table(title=f"Dataset stats — {store.dataset.research_target}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    for k, v in payload.items():
        table.add_row(k, json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else str(v))
    console.print(table)


@app.command()
def targets(as_json: bool = typer.Option(False, "--json", help="Emit raw JSON")) -> None:
    """List registered research targets (data/targets.json)."""
    root = (
        os.environ.get("SCR_DATA_ROOT")
        or os.environ.get("SCR_DATA_DIR")
        or "./data"
    )
    try:
        registry = TargetRegistry.load(root)
    except Exception as exc:
        err_console.print(f"[bold red]Failed to load target registry from {root}:[/] {exc}")
        raise typer.Exit(code=1)
    entries = registry.summary()
    if as_json:
        _print_json({"default_target": registry.default_target, "targets": entries})
        return
    table = Table(title=f"Research targets (default: {registry.default_target})")
    for col in ("id", "name", "ticker", "exchange", "default"):
        table.add_column(col, style="cyan" if col == "id" else None)
    for e in entries:
        table.add_row(
            e["id"], e["name"], e.get("stock_code") or "-",
            e.get("exchange") or "-", "*" if e["is_default"] else "",
        )
    console.print(table)


@app.command()
def companies(
    name: Optional[str] = typer.Option(None, help="Substring match on name or id"),
    entity_type: Optional[str] = typer.Option(
        None, help="Filter by entity type: target or related"
    ),
    page: int = typer.Option(1, help="1-based page number"),
    page_size: int = typer.Option(50, help="Items per page"),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON"),
) -> None:
    """List companies."""
    store = _load_store()
    result = store.list_companies(name=name, entity_type=entity_type, page=page, page_size=page_size)
    if as_json:
        _print_json(result.to_dict())
        return
    table = Table(title=f"Companies (page {result.page}/{result.total_pages})")
    for col in ("id", "name", "ticker", "exchange", "country", "entity_type", "sector"):
        table.add_column(col, style="cyan" if col == "id" else None)
    for c in result.items:
        table.add_row(
            c.id, c.name, c.stock_code or "-", c.exchange or "-",
            c.country, c.entity_type.value, c.sector or "-",
        )
    console.print(table)


@app.command()
def company(
    company_id: str,
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON"),
) -> None:
    """Show a single company."""
    store = _load_store()
    c = store.get_company(company_id)
    if c is None:
        err_console.print(f"[bold red]Unknown company id '{company_id}'[/]")
        raise typer.Exit(code=1)
    payload = c.model_dump(mode="json")
    if as_json:
        _print_json(payload)
        return
    table = Table(title=f"Company — {c.name}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    for k, v in payload.items():
        table.add_row(k, str(v))
    console.print(table)


@app.command()
def relationships(
    company: Optional[str] = typer.Option(None, "--company", help="Company id involved"),
    relationship_type: Optional[str] = typer.Option(None, "--type", help="Relationship category"),
    status: Optional[str] = typer.Option(None, help="confirmed / inferred / unknown"),
    min_score: Optional[int] = typer.Option(None, "--min-score", help="Inclusive lower bound"),
    max_score: Optional[int] = typer.Option(None, "--max-score", help="Inclusive upper bound"),
    valid_as_of: Optional[str] = typer.Option(None, "--valid-as-of", help="ISO date (yyyy-mm-dd)"),
    page: int = typer.Option(1, help="1-based page number"),
    page_size: int = typer.Option(50, help="Items per page"),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON"),
) -> None:
    """List relationships with filters."""
    store = _load_store()
    parsed_date: Optional[date_type] = None
    if valid_as_of:
        try:
            parsed_date = date_type.fromisoformat(valid_as_of)
        except ValueError:
            err_console.print(f"[bold red]Invalid --valid-as-of date: {valid_as_of!r} "
                              "(expected yyyy-mm-dd)[/]")
            raise typer.Exit(code=1)
    result = store.list_relationships(
        company_id=company,
        relationship_type=relationship_type,
        min_confidence=min_score,
        max_confidence=max_score,
        status=status,
        valid_as_of=parsed_date,
        page=page,
        page_size=page_size,
    )
    if as_json:
        _print_json(result.to_dict())
        return
    table = Table(title=f"Relationships (page {result.page}/{result.total_pages})")
    for col in ("id", "type", "source", "target", "status", "score", "valid_from", "valid_until"):
        table.add_column(col, style="cyan" if col == "id" else None)
    for r in result.items:
        table.add_row(
            r.id, r.type.value, r.source_company_id, r.target_company_id,
            r.status.value, str(r.confidence_score),
            r.valid_from.isoformat() if r.valid_from else "-",
            r.valid_until.isoformat() if r.valid_until else "active",
        )
    console.print(table)


@app.command()
def relationship(
    relationship_id: str,
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON"),
) -> None:
    """Show one relationship with evidence and score breakdown."""
    store = _load_store()
    rel = store.get_relationship(relationship_id)
    if rel is None:
        err_console.print(f"[bold red]Unknown relationship id '{relationship_id}'[/]")
        raise typer.Exit(code=1)
    evidence = store.list_evidence_for_relationship(rel.id)
    breakdown = score_relationship(
        rel,
        evidence,
        store.dataset.as_of,
        company_names=_company_names(store),
        research_target_id=store.dataset.research_target,
    )
    if as_json:
        payload = rel.model_dump(mode="json")
        payload["evidence"] = [e.model_dump(mode="json") for e in evidence]
        payload["score_breakdown"] = breakdown.to_dict()
        _print_json(payload)
        return
    console.print(f"[bold]{rel.id}[/]  {rel.type.value}  {rel.direction}")
    console.print(f"  status: {rel.status.value}   confidence: {rel.confidence_score}/100 "
                  f"(band: {breakdown.band})")
    console.print(f"  valid: {rel.valid_from or '?'} -> {rel.valid_until or 'active'}")
    console.print(f"  summary: {rel.summary}")
    dims = breakdown.dimensions
    console.print(
        "  breakdown: "
        + "  ".join(f"{k}={v:.0f}" for k, v in dims.items())
        + f"  total={breakdown.total:.0f}"
    )
    for e in evidence:
        console.print(f"  [cyan]evidence {e.id}[/] ({e.source_type.value}, {e.published_at})")
        console.print(f"    {e.source_url}")
        console.print(f"    locator: {e.evidence_locator}")
        console.print(f"    access: {e.access_restriction.value} | {e.license_note}")


@app.command()
def evidence(
    evidence_id: str,
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON"),
) -> None:
    """Show a single evidence item."""
    store = _load_store()
    e = store.get_evidence(evidence_id)
    if e is None:
        err_console.print(f"[bold red]Unknown evidence id '{evidence_id}'[/]")
        raise typer.Exit(code=1)
    payload = e.model_dump(mode="json")
    if as_json:
        _print_json(payload)
        return
    table = Table(title=f"Evidence — {e.id}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    for k, v in payload.items():
        table.add_row(k, str(v))
    console.print(table)


@app.command()
def score(
    relationship_id: str,
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON"),
) -> None:
    """Recompute and explain the confidence score of one relationship."""
    store = _load_store()
    rel = store.get_relationship(relationship_id)
    if rel is None:
        err_console.print(f"[bold red]Unknown relationship id '{relationship_id}'[/]")
        raise typer.Exit(code=1)
    evidence = store.list_evidence_for_relationship(rel.id)
    breakdown = score_relationship(
        rel,
        evidence,
        store.dataset.as_of,
        company_names=_company_names(store),
        research_target_id=store.dataset.research_target,
    )
    payload = {
        "relationship_id": rel.id,
        "stored_score": rel.confidence_score,
        "stored_status": rel.status.value,
        **breakdown.to_dict(),
        "evidence_ids": [e.id for e in evidence],
    }
    if as_json:
        _print_json(payload)
        return
    table = Table(title=f"Score — {rel.id}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    for k, v in payload.items():
        table.add_row(k, json.dumps(v, ensure_ascii=False) if isinstance(v, list) else str(v))
    console.print(table)


@app.command()
def graph(
    as_of: Optional[str] = typer.Option(
        None, "--as-of",
        help="ISO date (yyyy-mm-dd); slice edges to those valid at this date",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON"),
) -> None:
    """Print the relationship graph (nodes + edges).

    Optional ``--as-of`` reconstructs the network at a historical date
    (Track B temporal replay): edges are filtered to relationships whose
    ``[valid_from, valid_until]`` interval contains the date, and
    ``risk_metrics`` are recomputed on the sliced edge set.
    """
    from .graph import (
        degree_centrality as _deg,
        slice_graph as _slice,
        supplier_dependency_concentration as _hhi,
    )

    store = _load_store()

    # Resolve slice date. When --as-of is omitted, return the full
    # graph (backward compatibility); only an explicit --as-of
    # triggers temporal slicing.
    if as_of:
        try:
            slice_date = date_type.fromisoformat(as_of)
        except ValueError:
            err_console.print(
                f"[bold red]Invalid --as-of date: {as_of!r} "
                "(expected yyyy-mm-dd)[/]"
            )
            raise typer.Exit(code=1)
        sliced_rels = _slice(store.relationships, slice_date)
        response_as_of = slice_date.isoformat()
    else:
        sliced_rels = store.relationships
        response_as_of = store.dataset.as_of.isoformat()

    nodes = [
        {
            "id": c.id,
            "name": c.name,
            "entity_type": c.entity_type.value,
            "stock_code": c.stock_code,
            "exchange": c.exchange,
            "country": c.country,
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
        }
        for r in sliced_rels
    ]
    payload = {
        "research_target": store.dataset.research_target,
        "as_of": response_as_of,
        "nodes": nodes,
        "edges": edges,
        "risk_metrics": {
            "degree_centrality": _deg(sliced_rels),
            "supplier_dependency_concentration": _hhi(
                sliced_rels, store.dataset.research_target,
            ),
        },
    }
    if as_json:
        _print_json(payload)
        return
    table = Table(
        title=f"Relationship graph — {store.dataset.research_target} "
        f"(as_of {response_as_of})"
    )
    table.add_column("edge", style="cyan")
    table.add_column("source", style="green")
    table.add_column("->", style="dim")
    table.add_column("target", style="green")
    table.add_column("type")
    table.add_column("status")
    table.add_column("score")
    for e in sorted(edges, key=lambda x: (-x["confidence_score"], x["id"])):
        table.add_row(
            e["id"], e["source"], "->", e["target"],
            e["type"], e["status"], str(e["confidence_score"]),
        )
    console.print(table)
    hhi = payload["risk_metrics"]["supplier_dependency_concentration"]
    console.print(
        f"  [dim]risk_metrics:[/] supplier_dependency_concentration={hhi} "
        f"(0=diversified, 10000=single-source)"
    )


if __name__ == "__main__":
    sys.exit(app())
