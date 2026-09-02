"""Deprecated ``recall`` command group — aliases for ``hydradb query``.

Kept working (each emits one stderr deprecation warning) so existing scripts and
the documented ``-o json`` contract do not break. New code should use
``hydradb query``.
"""

import typer

from hydradb_cli.commands import _impl
from hydradb_cli.output import warn_deprecated

app = typer.Typer(help="[dim](deprecated)[/dim] Recall context — use 'hydradb query'.")


@app.command("full")
def full_recall(
    query: str = typer.Argument(help="Search query to find relevant knowledge."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Database. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Collection."),
    max_results: int = typer.Option(10, "--max-results", "-n", help="Maximum number of results (1-50)."),
    mode: str | None = typer.Option(None, "--mode", "-m", help="Retrieval mode: 'fast' or 'thinking'."),
    alpha: float | None = typer.Option(None, "--alpha", help="Hybrid search alpha (0.0=keyword, 1.0=semantic)."),
    recency_bias: float | None = typer.Option(None, "--recency-bias", help="Preference for newer content (0.0-1.0)."),
    graph_context: bool | None = typer.Option(
        None, "--graph-context/--no-graph-context", help="Include knowledge graph relations."
    ),
    additional_context: str | None = typer.Option(None, "--context", help="Additional context to guide retrieval."),
    acl: list[str] | None = typer.Option(
        None, "--acl", help="Principals to answer as (permission-aware search). Repeatable."
    ),
) -> None:
    """[dim](deprecated)[/dim] Search indexed knowledge — use 'hydradb query --kind knowledge'."""
    warn_deprecated("recall full", "query --kind knowledge")
    _impl.do_query(
        query,
        kind="knowledge",
        max_results=max_results,
        mode=mode,
        alpha=alpha,
        recency_bias=recency_bias,
        graph_context=graph_context,
        additional_context=additional_context,
        acl=list(acl) if acl else None,
        tenant_id=tenant_id,
        sub_tenant_id=sub_tenant_id,
    )


@app.command("preferences")
def recall_preferences(
    query: str = typer.Argument(help="Search query to find relevant user memories."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Database. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Collection."),
    max_results: int = typer.Option(10, "--max-results", "-n", help="Maximum number of results (1-50)."),
    mode: str | None = typer.Option(None, "--mode", "-m", help="Retrieval mode: 'fast' or 'thinking'."),
    alpha: float | None = typer.Option(None, "--alpha", help="Hybrid search alpha (0.0=keyword, 1.0=semantic)."),
    recency_bias: float | None = typer.Option(None, "--recency-bias", help="Preference for newer content (0.0-1.0)."),
    graph_context: bool | None = typer.Option(
        None, "--graph-context/--no-graph-context", help="Include knowledge graph relations."
    ),
    additional_context: str | None = typer.Option(None, "--context", help="Additional context to guide retrieval."),
    acl: list[str] | None = typer.Option(
        None, "--acl", help="Principals to answer as (permission-aware search). Repeatable."
    ),
) -> None:
    """[dim](deprecated)[/dim] Search user memories — use 'hydradb query --kind memory'."""
    warn_deprecated("recall preferences", "query --kind memory")
    _impl.do_query(
        query,
        kind="memory",
        max_results=max_results,
        mode=mode,
        alpha=alpha,
        recency_bias=recency_bias,
        graph_context=graph_context,
        additional_context=additional_context,
        acl=list(acl) if acl else None,
        tenant_id=tenant_id,
        sub_tenant_id=sub_tenant_id,
        spinner_msg="Searching memories...",
    )


@app.command("keyword")
def keyword_recall(
    query: str = typer.Argument(help="Keyword search terms."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Database. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Collection."),
    operator: str | None = typer.Option(None, "--operator", help="How to combine terms: 'or', 'and', or 'phrase'."),
    max_results: int = typer.Option(10, "--max-results", "-n", help="Maximum number of results."),
    search_mode: str | None = typer.Option(
        None, "--search-mode", help="What to search: 'sources' (knowledge) or 'memories'."
    ),
    acl: list[str] | None = typer.Option(
        None, "--acl", help="Principals to answer as (permission-aware search). Repeatable."
    ),
) -> None:
    """[dim](deprecated)[/dim] Keyword/boolean search — use 'hydradb query --operator'."""
    warn_deprecated("recall keyword", "query --operator")
    kind = "memory" if search_mode == "memories" else "knowledge" if search_mode == "sources" else None
    _impl.do_query(
        query,
        kind=kind,
        operator=operator,
        max_results=max_results,
        acl=list(acl) if acl else None,
        tenant_id=tenant_id,
        sub_tenant_id=sub_tenant_id,
    )
