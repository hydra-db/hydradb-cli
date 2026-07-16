"""Recall commands: full, preferences, keyword."""

import httpx
import typer
from rich.console import Group
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from hydradb_cli.client import HydraDBClientError
from hydradb_cli.output import print_error, print_result, spinner
from hydradb_cli.utils.common import (
    get_client,
    handle_api_error,
    handle_network_error,
    require_tenant_id,
    resolve_sub_tenant_id,
    validate_range,
)

app = typer.Typer(help="Recall context from HydraDB.")

VALID_MODES = {"fast", "thinking"}
VALID_OPERATORS = {"or", "and", "phrase"}
VALID_SEARCH_MODES = {"sources", "memories"}


def _format_recall_result(r: dict):
    """Human-readable formatter for recall responses."""
    chunks = r.get("chunks", [])
    if not chunks:
        return "[dim]No relevant results found.[/dim]"

    panels = []
    for i, chunk in enumerate(chunks, 1):
        score = chunk.get("relevancy_score")
        score_str = f" \u2022 {score:.0%}" if score is not None else ""
        title_text = chunk.get("source_title", "")
        title_str = f" \u2014 {escape(str(title_text))}" if title_text else ""

        content = chunk.get("chunk_content", "")
        preview = content[:300] + "..." if len(content) > 300 else content

        panel_title = f"[bold]{i}[/bold]{score_str}{title_str}"
        panels.append(
            Panel(
                escape(preview),
                title=panel_title,
                title_align="left",
                border_style="cyan",
                padding=(0, 1),
            )
        )

    graph = r.get("graph_context", {})
    query_paths = graph.get("query_paths", [])
    if query_paths:
        footer = Text(f"  Graph: {len(query_paths)} entity path(s) found.", style="dim")
        panels.append(footer)

    header = Text(f"  Found {len(chunks)} result(s)", style="bold")
    return Group(header, *panels)


def _validate_recall_params(
    query: str,
    mode: str | None,
    alpha: float | None,
    recency_bias: float | None,
    max_results: int,
) -> None:
    """Validate shared recall parameters before hitting the API."""
    if not query.strip():
        print_error("Query cannot be empty.")

    if mode and mode not in VALID_MODES:
        print_error(f"--mode must be one of: {', '.join(sorted(VALID_MODES))}. Got '{mode}'.")

    if alpha is not None:
        validate_range(alpha, "alpha", 0.0, 1.0)

    if recency_bias is not None:
        validate_range(recency_bias, "recency-bias", 0.0, 1.0)

    if max_results < 1 or max_results > 50:
        print_error(f"--max-results must be between 1 and 50, got {max_results}.")


@app.command("full")
def full_recall(
    query: str = typer.Argument(help="Search query to find relevant knowledge."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
    max_results: int = typer.Option(10, "--max-results", "-n", help="Maximum number of results (1-50)."),
    mode: str | None = typer.Option(
        None,
        "--mode",
        "-m",
        help="Retrieval mode: 'fast' or 'thinking' (deeper graph traversal).",
    ),
    alpha: float | None = typer.Option(
        None,
        "--alpha",
        help="Hybrid search alpha (0.0=keyword, 1.0=semantic).",
    ),
    recency_bias: float | None = typer.Option(
        None,
        "--recency-bias",
        help="Preference for newer content (0.0=none, 1.0=strong).",
    ),
    graph_context: bool | None = typer.Option(
        None,
        "--graph-context/--no-graph-context",
        help="Include knowledge graph relations in results.",
    ),
    additional_context: str | None = typer.Option(
        None,
        "--context",
        help="Additional context to guide retrieval.",
    ),
) -> None:
    """Search over indexed knowledge (documents, files, knowledge base).

    Use full recall when you need context from uploaded documents and knowledge
    sources. For user memories and preferences, use 'hydradb recall preferences'.

    Examples:

        hydradb recall full "What did the team say about pricing?" --tenant-id my-tenant

        hydradb recall full "contract terms" --mode thinking --max-results 20 --tenant-id t1
    """
    _validate_recall_params(query, mode, alpha, recency_bias, max_results)

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner("Searching..."):
            result = client.full_recall(
                tenant_id=tid,
                query=query,
                sub_tenant_id=stid,
                max_results=max_results,
                mode=mode,
                alpha=alpha,
                recency_bias=recency_bias,
                graph_context=graph_context,
                additional_context=additional_context,
            )
        print_result(result, _format_recall_result)
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)


@app.command("preferences")
def recall_preferences(
    query: str = typer.Argument(help="Search query to find relevant user memories."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
    max_results: int = typer.Option(10, "--max-results", "-n", help="Maximum number of results (1-50)."),
    mode: str | None = typer.Option(
        None,
        "--mode",
        "-m",
        help="Retrieval mode: 'fast' or 'thinking'.",
    ),
    alpha: float | None = typer.Option(
        None,
        "--alpha",
        help="Hybrid search alpha (0.0=keyword, 1.0=semantic).",
    ),
    recency_bias: float | None = typer.Option(
        None,
        "--recency-bias",
        help="Preference for newer content (0.0=none, 1.0=strong).",
    ),
    graph_context: bool | None = typer.Option(
        None,
        "--graph-context/--no-graph-context",
        help="Include knowledge graph relations.",
    ),
    additional_context: str | None = typer.Option(
        None,
        "--context",
        help="Additional context to guide retrieval.",
    ),
) -> None:
    """Search over user memories (preferences, conversations, behavioral data).

    Use recall preferences when you need context from stored user interactions,
    preferences, and inferred traits. For document/knowledge search, use
    'hydradb recall full'.

    Examples:

        hydradb recall preferences "What does the user prefer?" --tenant-id my-tenant

        hydradb recall preferences "communication style" --mode thinking --tenant-id t1
    """
    _validate_recall_params(query, mode, alpha, recency_bias, max_results)

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner("Searching memories..."):
            result = client.recall_preferences(
                tenant_id=tid,
                query=query,
                sub_tenant_id=stid,
                max_results=max_results,
                mode=mode,
                alpha=alpha,
                recency_bias=recency_bias,
                graph_context=graph_context,
                additional_context=additional_context,
            )
        print_result(result, _format_recall_result)
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)


@app.command("keyword")
def keyword_recall(
    query: str = typer.Argument(help="Keyword search terms."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
    operator: str | None = typer.Option(
        None,
        "--operator",
        help="How to combine terms: 'or', 'and', or 'phrase'.",
    ),
    max_results: int = typer.Option(10, "--max-results", "-n", help="Maximum number of results."),
    search_mode: str | None = typer.Option(
        None,
        "--search-mode",
        help="What to search: 'sources' (documents) or 'memories' (user memories).",
    ),
) -> None:
    """Deterministic keyword/boolean search over indexed content.

    Unlike semantic recall, this performs exact text matching. Useful when you
    need precise term matches rather than semantic similarity.

    Examples:

        hydradb recall keyword "pricing AND enterprise" --operator and --tenant-id my-tenant

        hydradb recall keyword "John Smith" --operator phrase --search-mode memories --tenant-id t1
    """
    if not query.strip():
        print_error("Query cannot be empty.")

    if operator and operator not in VALID_OPERATORS:
        print_error(f"--operator must be one of: {', '.join(sorted(VALID_OPERATORS))}. Got '{operator}'.")

    if search_mode and search_mode not in VALID_SEARCH_MODES:
        print_error(f"--search-mode must be one of: {', '.join(sorted(VALID_SEARCH_MODES))}. Got '{search_mode}'.")

    if max_results < 1:
        print_error(f"--max-results must be at least 1, got {max_results}.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner("Searching..."):
            result = client.boolean_recall(
                tenant_id=tid,
                query=query,
                sub_tenant_id=stid,
                operator=operator,
                max_results=max_results,
                search_mode=search_mode,
            )
        print_result(result, _format_recall_result)
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)
