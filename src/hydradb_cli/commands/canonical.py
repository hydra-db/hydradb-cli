"""Canonical top-level commands (CONTRACT §3).

These are the names users should adopt: ``hydradb query | ingest | list |
inspect | delete | relations | verify | database … | doctor``. Every legacy
command remains available as a deprecated alias (see the other modules in this
package), and each alias resolves to the same ``_impl`` function these commands
call.
"""

from __future__ import annotations

import sys

import typer
from rich.panel import Panel

from hydradb_cli.commands import _impl
from hydradb_cli.config import get_full_config
from hydradb_cli.output import console, make_kv_table, print_error, print_json, spinner
from hydradb_cli.utils.common import mask_api_key, read_stdin_safe, resolve_scope_flags

database_app = typer.Typer(help="Manage [bold]databases[/bold] (create, delete, list, collections, stats, readiness).")


def _resolve_text_input(text: str | None) -> str:
    """Resolve memory/knowledge text from ``--text``, ``-`` (stdin), or a pipe."""
    if text == "-":
        if sys.stdin.isatty():
            typer.echo("Reading from stdin (Ctrl+D to finish)...", err=True)
            text = sys.stdin.read().strip()
        else:
            text = read_stdin_safe()
        if not text:
            print_error("No input received from stdin.")
    if text is None:
        stdin_data = read_stdin_safe()
        if stdin_data:
            text = stdin_data
        else:
            print_error(
                "No text provided. Use --text 'your text', pipe via stdin, or use --text - for interactive input."
            )
    if not text or not text.strip():
        print_error("Text cannot be empty or whitespace-only.")
    return text.strip()


def query(
    query_text: str = typer.Argument(metavar="QUERY", help="Search query."),
    kind: str | None = typer.Option(None, "--kind", help="Corpus to query: 'memory' or 'knowledge'."),
    operator: str | None = typer.Option(None, "--operator", help="Keyword operator: 'or', 'and', or 'phrase'."),
    max_results: int = typer.Option(10, "--max-results", "-n", help="Maximum number of results (1-50)."),
    mode: str | None = typer.Option(None, "--mode", "-m", help="Retrieval mode: 'fast' or 'thinking'."),
    alpha: float | None = typer.Option(None, "--alpha", help="Hybrid search alpha (0.0=keyword, 1.0=semantic)."),
    recency_bias: float | None = typer.Option(None, "--recency-bias", help="Preference for newer content (0.0-1.0)."),
    graph_context: bool | None = typer.Option(
        None, "--graph-context/--no-graph-context", help="Include knowledge graph relations."
    ),
    additional_context: str | None = typer.Option(None, "--context", help="Additional context to guide retrieval."),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
) -> None:
    """Query knowledge or memories — the single retrieval entry point."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    _impl.do_query(
        query_text,
        kind=kind,
        operator=operator,
        max_results=max_results,
        mode=mode,
        alpha=alpha,
        recency_bias=recency_bias,
        graph_context=graph_context,
        additional_context=additional_context,
        tenant_id=tid,
        sub_tenant_id=stid,
    )


def ingest(
    files: list[str] | None = typer.Argument(None, help="Knowledge file path(s) to ingest."),
    kind: str | None = typer.Option(None, "--kind", help="Kind to ingest: 'memory' (default) or 'knowledge'."),
    text: str | None = typer.Option(None, "--text", "-t", help="Text to ingest. Use '-' to read from stdin."),
    title: str | None = typer.Option(None, "--title", help="Optional title."),
    source_id: str | None = typer.Option(None, "--source-id", help="Source identifier."),
    user_name: str | None = typer.Option(None, "--user-name", help="User name (memory only)."),
    infer: bool = typer.Option(True, "--infer/--no-infer", help="Extract insights and build knowledge graph."),
    markdown: bool = typer.Option(False, "--markdown", help="Treat text as markdown (memory only)."),
    upsert: bool = typer.Option(True, "--upsert/--no-upsert", help="Update existing items with the same source_id."),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
) -> None:
    """Ingest a memory, knowledge text, or knowledge file(s)."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    if files:
        # Files are always knowledge sources. Reject every option that would be
        # silently ignored rather than storing the file the wrong way. Only
        # --upsert applies to file ingest.
        if kind == "memory":
            print_error("File arguments are knowledge sources; --kind memory cannot be combined with files.")
        if text or title or source_id or user_name:
            print_error("--text/--title/--source-id/--user-name do not apply to file ingest; pass files only.")
        if markdown:
            print_error("--markdown does not apply to file ingest; pass files only.")
        if not infer:
            print_error("--infer/--no-infer does not apply to file ingest; pass files only.")
        _impl.do_ingest_knowledge_files(files, upsert=upsert, tenant_id=tid, sub_tenant_id=stid)
        return
    if kind == "knowledge":
        _impl.do_ingest_knowledge_text(
            _resolve_text_input(text),
            title=title,
            source_id=source_id,
            tenant_id=tid,
            sub_tenant_id=stid,
        )
        return
    _impl.do_ingest_memory(
        _resolve_text_input(text),
        title=title,
        source_id=source_id,
        user_name=user_name,
        infer=infer,
        markdown=markdown,
        upsert=upsert,
        tenant_id=tid,
        sub_tenant_id=stid,
    )


def list_items(
    kind: str | None = typer.Option(None, "--kind", help="Filter by kind: 'memory' or 'knowledge'."),
    page: int | None = typer.Option(None, "--page", help="Page number (1-indexed)."),
    page_size: int | None = typer.Option(None, "--page-size", help="Items per page (1-100)."),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
) -> None:
    """List ingested sources and memories."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    _impl.do_list(kind=kind, page=page, page_size=page_size, tenant_id=tid, sub_tenant_id=stid)


def inspect(
    source_id: str = typer.Argument(help="Source ID to inspect."),
    mode: str = typer.Option("content", "--mode", help="Fetch mode: 'content', 'url', or 'both'."),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
) -> None:
    """Inspect a source's content by its ID."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    _impl.do_inspect(source_id, mode=mode, tenant_id=tid, sub_tenant_id=stid)


def delete(
    ids: list[str] = typer.Argument(help="One or more IDs to delete."),
    kind: str = typer.Option("knowledge", "--kind", help="Kind to delete: 'memory' or 'knowledge'."),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Delete memories or knowledge sources by ID."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    clean_ids = [i.strip() for i in ids if i.strip()]
    if not clean_ids:
        print_error("At least one ID is required.")
    if not confirm:
        typer.confirm(f"Delete {len(clean_ids)} item(s)? This action is irreversible.", abort=True)
    _impl.do_delete(clean_ids, kind=kind, tenant_id=tid, sub_tenant_id=stid)


def relations(
    source_id: str = typer.Argument(help="Source ID to fetch graph relations for."),
    kind: str | None = typer.Option(None, "--kind", help="Corpus: 'memory' or 'knowledge'."),
    limit: int | None = typer.Option(None, "--limit", help="Maximum number of relations to return."),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
) -> None:
    """Fetch knowledge-graph relations for a source."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    _impl.do_relations(source_id, kind=kind, limit=limit, tenant_id=tid, sub_tenant_id=stid)


def verify(
    ids: list[str] = typer.Argument(help="One or more source IDs to check."),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
) -> None:
    """Check per-source ingestion status (indexing progress)."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    _impl.do_ingestion_status(ids, tenant_id=tid, sub_tenant_id=stid)


def doctor() -> None:
    """Check local config and API reachability."""
    cfg = get_full_config()
    api_key = cfg.get("api_key")

    reachable: bool | None = None
    detail = ""
    if api_key and cfg.get("tenant_id"):
        try:
            with spinner("Checking API reachability..."):
                _impl.get_wrapper().databases.readiness()
            reachable = True
        except Exception as e:  # noqa: BLE001 - doctor reports, never raises
            reachable = False
            detail = str(e)

    from hydradb_cli.output import get_output_format

    if get_output_format() == "json":
        safe = dict(cfg)
        if safe.get("api_key"):
            safe["api_key"] = mask_api_key(api_key)
        safe["reachable"] = reachable
        print_json(safe)
        return

    pairs: list[tuple[str, str]] = []
    pairs.append(
        (
            "API Key",
            f"{mask_api_key(api_key)} [dim]({cfg['api_key_source']})[/dim]" if api_key else "[dim]Not configured[/dim]",
        )
    )
    pairs.append(("Database", cfg.get("tenant_id") or "[dim]Not configured[/dim]"))
    if cfg.get("sub_tenant_id"):
        pairs.append(("Collection", cfg["sub_tenant_id"]))
    pairs.append(("Base URL", cfg["base_url"]))
    if reachable is True:
        pairs.append(("Reachable", "[green]yes[/green]"))
    elif reachable is False:
        pairs.append(("Reachable", f"[red]no[/red] [dim]({detail})[/dim]"))
    else:
        pairs.append(("Reachable", "[dim]not checked (configure API key + database)[/dim]"))

    console.print(
        Panel(make_kv_table(pairs), title="[bold cyan]/// Doctor[/bold cyan]", border_style="cyan", padding=(0, 1))
    )


# ── database sub-commands ────────────────────────────────────────────────────


@database_app.command("create")
def database_create(
    database: str = typer.Argument(help="Unique database identifier."),
) -> None:
    """Create a new database."""
    _impl.do_database_create(database)


@database_app.command("delete")
def database_delete(
    database: str = typer.Argument(help="Database ID to delete."),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Delete a database and all its data. This action is irreversible."""
    if not confirm:
        typer.confirm(f"Delete database '{database}' and ALL its data?", abort=True)
    _impl.do_database_delete(database)


@database_app.command("list")
def database_list() -> None:
    """List all databases for the authenticated user."""
    _impl.do_database_list()


@database_app.command("collections")
def database_collections(
    tenant_id_arg: str | None = typer.Argument(None, metavar="DATABASE", help="Database. Uses default if omitted."),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if omitted."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
) -> None:
    """List collections for a database."""
    db, _ = resolve_scope_flags(database, None, tenant_id, None)
    _impl.do_database_collections(db or tenant_id_arg)


@database_app.command("stats")
def database_stats(
    tenant_id_arg: str | None = typer.Argument(None, metavar="DATABASE", help="Database. Uses default if omitted."),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if omitted."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
) -> None:
    """Show row-count statistics for a database."""
    db, _ = resolve_scope_flags(database, None, tenant_id, None)
    _impl.do_database_stats(db or tenant_id_arg)


@database_app.command("readiness")
def database_readiness(
    tenant_id_arg: str | None = typer.Argument(None, metavar="DATABASE", help="Database. Uses default if omitted."),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if omitted."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
) -> None:
    """Check whether a database is provisioned and ready for ingestion."""
    db, _ = resolve_scope_flags(database, None, tenant_id, None)
    _impl.do_database_readiness(db or tenant_id_arg)


@database_app.command("monitor")
def database_monitor(
    tenant_id_arg: str | None = typer.Argument(None, metavar="DATABASE", help="Database. Uses default if omitted."),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if omitted."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
) -> None:
    """Merged database stats + readiness."""
    db, _ = resolve_scope_flags(database, None, tenant_id, None)
    _impl.do_database_monitor(db or tenant_id_arg)
