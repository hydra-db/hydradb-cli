"""Fetch commands: content, sources, relations."""

import httpx
import typer
from rich.console import Group
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from hydradb_cli.client import HydraDBClientError
from hydradb_cli.output import make_table, print_error, print_result, spinner
from hydradb_cli.utils.common import (
    get_client,
    handle_api_error,
    handle_network_error,
    require_tenant_id,
    resolve_sub_tenant_id,
)

app = typer.Typer(help="Fetch and inspect stored data.")

VALID_FETCH_MODES = {"content", "url", "both"}
VALID_SOURCE_KINDS = {"knowledge", "memories"}


@app.command()
def content(
    source_id: str = typer.Argument(help="Source ID to fetch content for."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
    mode: str = typer.Option(
        "content",
        "--mode",
        help="Fetch mode: 'content' (text), 'url' (presigned URL), or 'both'.",
    ),
) -> None:
    """Fetch the full content of a source by its ID.

    Returns the original text content that was ingested. Use 'hydradb fetch sources'
    to find source IDs.

    Examples:

        hydradb fetch content source_abc123 --tenant-id my-tenant

        hydradb fetch content source_abc123 --mode url --tenant-id my-tenant
    """
    if not source_id.strip():
        print_error("Source ID cannot be empty.")

    if mode not in VALID_FETCH_MODES:
        print_error(f"--mode must be one of: {', '.join(sorted(VALID_FETCH_MODES))}. Got '{mode}'.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner("Fetching content..."):
            result = client.fetch_content(
                tenant_id=tid,
                source_id=source_id,
                sub_tenant_id=stid,
                mode=mode,
            )

        def fmt(r: dict):
            content_text = r.get("content", "")
            content_b64 = r.get("content_base64", "")
            url = r.get("presigned_url", "")
            content_type = r.get("content_type", "")
            size = r.get("size_bytes")

            meta_parts = [f"[cyan]Source:[/cyan] {source_id}"]
            if content_type:
                meta_parts.append(f"[cyan]Type:[/cyan] {content_type}")
            if size is not None:
                meta_parts.append(f"[cyan]Size:[/cyan] {size} bytes")
            if url:
                meta_parts.append(f"[cyan]URL:[/cyan] {url}")

            meta = "\n".join(meta_parts)

            if content_text:
                body = f"{meta}\n\n{escape(content_text)}"
            elif content_b64:
                body = f"{meta}\n\n[dim](Binary content, {len(content_b64)} chars base64-encoded)[/dim]"
            else:
                body = meta

            return Panel(
                body,
                title="[bold cyan]/// Source Content[/bold cyan]",
                border_style="cyan",
                padding=(0, 1),
            )

        print_result(result, fmt)
    except HydraDBClientError as e:
        if e.status_code == 404:
            print_error(
                f"Source '{source_id}' not found. This can happen if the file was uploaded "
                f"under a different sub-tenant. Try specifying --sub-tenant-id explicitly."
            )
        else:
            handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)


@app.command()
def sources(
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
    kind: str | None = typer.Option(
        None,
        "--kind",
        help="Filter by kind: 'knowledge' or 'memories'.",
    ),
    page: int | None = typer.Option(None, "--page", help="Page number (1-indexed)."),
    page_size: int | None = typer.Option(None, "--page-size", help="Items per page (1-100)."),
) -> None:
    """List all ingested sources (knowledge and/or memories).

    Shows source IDs, titles, types, and metadata. Use source IDs with
    'hydradb fetch content' to retrieve full content.

    Examples:

        hydradb fetch sources --tenant-id my-tenant

        hydradb fetch sources --kind knowledge --page-size 20 --tenant-id my-tenant
    """
    if kind and kind not in VALID_SOURCE_KINDS:
        print_error(f"--kind must be one of: {', '.join(sorted(VALID_SOURCE_KINDS))}. Got '{kind}'.")

    if page is not None and page < 1:
        print_error(f"--page must be at least 1, got {page}.")

    if page_size is not None and (page_size < 1 or page_size > 100):
        print_error(f"--page-size must be between 1 and 100, got {page_size}.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner("Fetching sources..."):
            result = client.list_data(
                tenant_id=tid,
                sub_tenant_id=stid,
                kind=kind,
                page=page,
                page_size=page_size,
            )

        def fmt(r: dict):
            sources_list = r.get("sources", [])
            memories_list = r.get("user_memories", [])

            if sources_list:
                rows = []
                for i, src in enumerate(sources_list, 1):
                    sid = src.get("id", "unknown")
                    src_title = src.get("title", "")
                    stype = src.get("type", "")
                    rows.append([str(i), sid, src_title, stype])

                table = make_table(
                    "#",
                    "Source ID",
                    "Title",
                    "Type",
                    rows=rows,
                    title=f"Found {len(sources_list)} source(s)",
                )

                parts = [table]
                total = r.get("total")
                pagination = r.get("pagination", {})
                footer_parts = []
                if total is not None:
                    footer_parts.append(f"Total: {total}")
                if pagination.get("has_next"):
                    current = pagination.get("page", 1)
                    footer_parts.append(f"Next page: --page {current + 1}")
                if footer_parts:
                    parts.append(Text("  " + "  |  ".join(footer_parts), style="dim"))
                return Group(*parts)

            if memories_list:
                rows = []
                for i, mem in enumerate(memories_list, 1):
                    mid = mem.get("memory_id", "unknown")
                    mem_content = mem.get("memory_content", "")
                    preview = mem_content[:100] + "..." if len(mem_content) > 100 else mem_content
                    rows.append([str(i), mid, escape(preview)])
                return make_table(
                    "#",
                    "Memory ID",
                    "Content",
                    rows=rows,
                    title=f"Found {len(memories_list)} memory/memories",
                )

            return "[dim]No sources found.[/dim]"

        print_result(result, fmt)
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)


@app.command()
def relations(
    source_id: str = typer.Argument(help="Source ID to fetch graph relations for."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
    is_memory: bool | None = typer.Option(
        None,
        "--is-memory/--is-knowledge",
        help="Whether the source is a memory (vs knowledge).",
    ),
    limit: int | None = typer.Option(None, "--limit", help="Maximum number of relations to return."),
) -> None:
    """Fetch knowledge graph relations for a source.

    Shows entity relationships extracted from the source content.

    Examples:

        hydradb fetch relations source_abc123 --tenant-id my-tenant

        hydradb fetch relations mem_123 --is-memory --tenant-id my-tenant
    """
    if not source_id.strip():
        print_error("Source ID cannot be empty.")

    if limit is not None and limit < 1:
        print_error(f"--limit must be at least 1, got {limit}.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner("Fetching graph relations..."):
            result = client.graph_relations(
                tenant_id=tid,
                source_id=source_id,
                sub_tenant_id=stid,
                is_memory=is_memory,
                limit=limit,
            )

        def fmt(r: dict):
            relations_list = r.get("relations", [])
            if not relations_list:
                return f"[dim]No graph relations found for source '{source_id}'.[/dim]"

            rows = []
            for rel in relations_list:
                triplets = rel.get("triplets", [])
                for t in triplets:
                    src = t.get("source", {}).get("name", "?")
                    pred = t.get("relation", {}).get("canonical_predicate", "related to")
                    tgt = t.get("target", {}).get("name", "?")
                    rows.append([src, pred, tgt])

            return make_table(
                "Subject",
                "Predicate",
                "Object",
                rows=rows,
                title=f"Graph relations for '{source_id}'",
            )

        print_result(result, fmt)
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)
