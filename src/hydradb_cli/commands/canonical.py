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
    kind: str | None = typer.Option(
        None, "--kind", help="Corpus to query on a split database: 'memory' or 'knowledge'. Not used on a unified one."
    ),
    operator: str | None = typer.Option(None, "--operator", help="Keyword operator: 'or', 'and', or 'phrase'."),
    max_results: int = typer.Option(10, "--max-results", "-n", help="Maximum number of results (1-50)."),
    mode: str | None = typer.Option(None, "--mode", "-m", help="Retrieval mode: 'fast' or 'thinking'."),
    alpha: float | None = typer.Option(None, "--alpha", help="Hybrid search alpha (0.0=keyword, 1.0=semantic)."),
    recency_bias: float | None = typer.Option(None, "--recency-bias", help="Preference for newer content (0.0-1.0)."),
    graph_context: bool | None = typer.Option(
        None, "--graph-context/--no-graph-context", help="Include knowledge graph relations."
    ),
    additional_context: str | None = typer.Option(None, "--context", help="Additional context to guide retrieval."),
    titles: list[str] | None = typer.Option(
        None,
        "--title",
        help="Exact document title to search inside; repeat for multiple titles.",
    ),
    acl: list[str] | None = typer.Option(
        None,
        "--acl",
        help="Principals to answer as, repeatable (--acl alice@corp.com --acl 'group:google:eng@corp.com'). Restricts results to documents whose access list admits one of them. Omit to search everything the API key can reach.",
    ),
    follow_forceful_relations: bool | None = typer.Option(
        None,
        "--follow-forceful-relations/--no-follow-forceful-relations",
        help="Unified databases only: also return chunks pulled in by relations declared at ingest (server default on).",
    ),
    llm: bool = typer.Option(
        False,
        "--llm",
        help="Unified databases only: print the server-built llm_prompt verbatim, ready to inject into a model call.",
    ),
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
        titles=list(titles) if titles else None,
        acl=list(acl) if acl else None,
        follow_forceful_relations=follow_forceful_relations,
        llm=llm,
        tenant_id=tid,
        sub_tenant_id=stid,
    )


def feedback(
    request_id: str = typer.Argument(metavar="REQUEST_ID", help="The request id printed by 'hydradb query'."),
    feedback_text: str | None = typer.Option(
        None, "--feedback", "-f", help="What was right or wrong about the results."
    ),
    rating: str | None = typer.Option(None, "--rating", help="Overall verdict: 'positive', 'negative', or 'neutral'."),
    ground_truth_answer: str | None = typer.Option(
        None, "--ground-truth-answer", help="The answer the query SHOULD have produced."
    ),
    ground_truth_source_id: list[str] | None = typer.Option(
        None,
        "--ground-truth-source-id",
        help="A source id that should have been retrieved, repeatable. Scored as a retrieval judgement.",
    ),
    source: str | None = typer.Option(
        None, "--source", help="Who is reporting: 'user' (default) or 'agent' for scripted runs."
    ),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
) -> None:
    """Report whether a query's results were actually useful."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    _impl.do_feedback(
        request_id,
        feedback=feedback_text,
        rating=rating,
        ground_truth_answer=ground_truth_answer,
        ground_truth_source_ids=list(ground_truth_source_id) if ground_truth_source_id else None,
        source=source,
        tenant_id=tid,
        sub_tenant_id=stid,
    )


def ingest(
    files: list[str] | None = typer.Argument(None, help="Knowledge file path(s) to ingest (split databases only)."),
    kind: str | None = typer.Option(
        None,
        "--kind",
        help="Kind to ingest on a split database: 'memory' (default) or 'knowledge'. Not used on a unified one.",
    ),
    text: str | None = typer.Option(None, "--text", "-t", help="Text to ingest. Use '-' to read from stdin."),
    title: str | None = typer.Option(None, "--title", help="Optional title."),
    source_id: str | None = typer.Option(
        None, "--source-id", help="Source identifier (--context-id on a unified database)."
    ),
    user_name: str | None = typer.Option(
        None, "--user-name", help="Who is speaking: the user's name (split: memory only; unified: any item)."
    ),
    infer: bool = typer.Option(True, "--infer/--no-infer", help="Extract insights and build knowledge graph."),
    markdown: bool = typer.Option(False, "--markdown", help="Treat text as markdown (split memory only)."),
    upsert: bool = typer.Option(True, "--upsert/--no-upsert", help="Update existing items with the same id."),
    conversation_file: str | None = typer.Option(
        None,
        "--conversation-file",
        help="Unified databases: path to a JSON list of {role, content} turns to ingest as one conversation (roles: user, assistant, system). Name the user with --user-name.",
    ),
    context_id: str | None = typer.Option(
        None,
        "--context-id",
        help="Unified databases: caller-assigned id for the item (server-generated when omitted). --source-id means the same there.",
    ),
    enrich: bool = typer.Option(
        True,
        "--enrich/--no-enrich",
        help="Unified databases: extract facts and graph relations for the item. --no-infer means the same there.",
    ),
    instructions: str | None = typer.Option(
        None, "--instructions", help="Unified databases: steer enrichment for this item."
    ),
    happened_at: str | None = typer.Option(
        None, "--happened-at", help="Unified databases: the event date the item is about, YYYY-MM-DD."
    ),
    attributes: str | None = typer.Option(
        None, "--attributes", help="Unified databases: declared, filterable attributes as a JSON object."
    ),
    custom_attributes: str | None = typer.Option(
        None, "--custom-attributes", help="Unified databases: free-form attributes as a JSON object."
    ),
    category: str | None = typer.Option(
        None,
        "--category",
        help="Unified databases: context_category label: 'auto', 'user_preference', 'business_knowledge' or 'decision_trace'.",
    ),
    forceful_relation: list[str] | None = typer.Option(
        None,
        "--forceful-relation",
        help="Unified databases: a context id this item is declared related to; repeatable.",
    ),
    acl: list[str] | None = typer.Option(
        None,
        "--acl",
        help="Unified databases: a principal allowed to retrieve the item, repeatable (user_email:a@x.com, domain:acme.com).",
    ),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
) -> None:
    """Ingest a memory, knowledge text, or knowledge file(s); on a unified database, one context item."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    # The layout decides the request shape (PRO-1618), never a flag: a unified
    # database gets one JSON context item and never a kind; a split one keeps
    # every existing call exactly as it was.
    db, layout = _impl.database_layout(tid)

    def run_unified(resolved_text: str | None = None) -> None:
        """The unified write. ``resolved_text`` is the text already read on the
        split path (a redo); otherwise it is read here, after the checks."""
        if files:
            print_error(
                f"Database '{db}' is unified: files are not accepted (text or a conversation only). "
                "Extract the text and pass it with --text."
            )
        if kind:
            print_error(f"Database '{db}' is unified: it has one corpus, so --kind does not apply. Omit it.")
        if markdown:
            print_error("--markdown does not apply on a unified database.")
        if conversation_file and text:
            print_error("Pass exactly one of --text or --conversation-file.")
        if context_id and source_id and context_id != source_id:
            print_error("--context-id and --source-id name the same thing on a unified database; pass one of them.")
        if not conversation_file and resolved_text is None:
            resolved_text = _resolve_text_input(text)
        _impl.do_ingest_unified(
            text=None if conversation_file else resolved_text,
            conversation_file=conversation_file,
            context_id=context_id or source_id,
            title=title,
            user_name=user_name,
            enrich=enrich and infer,
            instructions=instructions,
            happened_at=happened_at,
            attributes=attributes,
            custom_attributes=custom_attributes,
            category=category,
            forceful_relations=list(forceful_relation) if forceful_relation else None,
            acl=list(acl) if acl else None,
            upsert=upsert,
            tenant_id=tid,
            sub_tenant_id=stid,
        )

    if layout == "unified":
        run_unified()
        return

    unified_only = {
        "--conversation-file": conversation_file,
        "--context-id": context_id,
        "--no-enrich": not enrich,
        "--instructions": instructions,
        "--happened-at": happened_at,
        "--attributes": attributes,
        "--custom-attributes": custom_attributes,
        "--category": category,
        "--forceful-relation": forceful_relation,
        "--acl": acl,
    }
    used = [flag for flag, value in unified_only.items() if value]
    if used and layout == _impl.LAYOUT_UNKNOWN:
        print_error(
            f"{', '.join(used)} appl{'ies' if len(used) == 1 else 'y'} to unified databases only, and whether "
            f"'{db}' is unified could not be checked just now. Try again."
        )
    if used:
        print_error(f"{', '.join(used)} appl{'ies' if len(used) == 1 else 'y'} to unified databases only.")
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
        _impl.do_ingest_knowledge_files(files, upsert=upsert, tenant_id=tid, sub_tenant_id=stid, layout=layout)
        return
    resolved = _resolve_text_input(text)
    if kind == "knowledge":
        _impl.do_ingest_knowledge_text(
            resolved,
            title=title,
            source_id=source_id,
            tenant_id=tid,
            sub_tenant_id=stid,
            layout=layout,
            on_unified=lambda: run_unified(resolved),
        )
        return
    _impl.do_ingest_memory(
        resolved,
        title=title,
        source_id=source_id,
        user_name=user_name,
        infer=infer,
        markdown=markdown,
        upsert=upsert,
        tenant_id=tid,
        sub_tenant_id=stid,
        layout=layout,
        on_unified=lambda: run_unified(resolved),
    )


def list_items(
    kind: str | None = typer.Option(
        None, "--kind", help="Filter by kind on a split database: 'memory' or 'knowledge'. Not used on a unified one."
    ),
    page: int | None = typer.Option(None, "--page", help="Page number (1-indexed)."),
    page_size: int | None = typer.Option(None, "--page-size", help="Items per page (1-100)."),
    acl: list[str] | None = typer.Option(
        None,
        "--acl",
        help="Principals to answer as, repeatable (--acl alice@corp.com --acl 'group:google:eng@corp.com'). Restricts results to documents whose access list admits one of them. Omit to search everything the API key can reach.",
    ),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
) -> None:
    """List ingested sources and memories."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    _impl.do_list(
        kind=kind, page=page, page_size=page_size, acl=list(acl) if acl else None, tenant_id=tid, sub_tenant_id=stid
    )


def inspect(
    source_id: str = typer.Argument(help="Source ID to inspect."),
    mode: str = typer.Option("content", "--mode", help="Fetch mode: 'content', 'url', or 'both'."),
    acl: list[str] | None = typer.Option(
        None,
        "--acl",
        help="Principals to answer as, repeatable (--acl alice@corp.com --acl 'group:google:eng@corp.com'). Restricts results to documents whose access list admits one of them. Omit to search everything the API key can reach.",
    ),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
) -> None:
    """Inspect a source's content by its ID."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    _impl.do_inspect(source_id, mode=mode, acl=list(acl) if acl else None, tenant_id=tid, sub_tenant_id=stid)


def delete(
    ids: list[str] = typer.Argument(help="One or more IDs to delete."),
    kind: str | None = typer.Option(
        None,
        "--kind",
        help="Kind to delete on a split database: 'knowledge' (default) or 'memory'. Not used on a unified one.",
    ),
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
    kind: str | None = typer.Option(
        None, "--kind", help="Corpus on a split database: 'memory' or 'knowledge'. Not used on a unified one."
    ),
    limit: int | None = typer.Option(None, "--limit", help="Maximum number of relations to return."),
    acl: list[str] | None = typer.Option(
        None,
        "--acl",
        help="Principals to answer as, repeatable (--acl alice@corp.com --acl 'group:google:eng@corp.com'). Restricts results to documents whose access list admits one of them. Omit to search everything the API key can reach.",
    ),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
) -> None:
    """Fetch knowledge-graph relations for a source."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    _impl.do_relations(
        source_id, kind=kind, limit=limit, acl=list(acl) if acl else None, tenant_id=tid, sub_tenant_id=stid
    )


def subgraph(
    source_id: str = typer.Argument(help="Item ID to start from (from 'hydradb query' or 'hydradb list')."),
    kind: str | None = typer.Option(
        None, "--kind", help="Corpus on a split database: 'knowledge' (default) or 'memory'. Not used on a unified one."
    ),
    depth: int | None = typer.Option(None, "--depth", help="Hops to traverse (1–10; server default 5)."),
    max_sources: int | None = typer.Option(None, "--max-sources", help="Cap on members returned (server default 200)."),
    acl: list[str] | None = typer.Option(
        None,
        "--acl",
        help="Principals to answer as, repeatable (--acl alice@corp.com --acl 'group:google:eng@corp.com'). The subgraph contains only items those principals may see. Omit to search everything the API key can reach.",
    ),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if not specified."),
    collection: str | None = typer.Option(None, "--collection", help="Collection."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
) -> None:
    """Everything connected to one item: its thread, replies, parents, children, links."""
    tid, stid = resolve_scope_flags(database, collection, tenant_id, sub_tenant_id)
    _impl.do_subgraph(
        source_id,
        kind=kind,
        depth=depth,
        max_sources=max_sources,
        acl=list(acl) if acl else None,
        tenant_id=tid,
        sub_tenant_id=stid,
    )


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
    layout: str | None = typer.Option(
        None,
        "--type",
        help="Storage layout: 'split' (separate knowledge and memory corpora selected by --kind) or 'unified' (one corpus; no --kind on later commands). Omitted: the server's default, which is unified on current servers.",
    ),
) -> None:
    """Create a new database."""
    _impl.do_database_create(database, layout)


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


@database_app.command("delete-collection")
def database_delete_collection(
    collection_arg: str | None = typer.Argument(None, metavar="COLLECTION", help="Collection to delete."),
    database: str | None = typer.Option(None, "--database", "-d", help="Database. Uses default if omitted."),
    collection: str | None = typer.Option(None, "--collection", "-c", help="Collection to delete."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", hidden=True),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Delete one collection and all its data. The database is left intact.

    This action is irreversible: knowledge, memories, embeddings, graph nodes
    and stored objects for the collection are permanently removed. Sibling
    collections in the same database are untouched.
    """
    db, coll = resolve_scope_flags(database, collection or collection_arg, tenant_id, sub_tenant_id)
    target = coll or collection_arg
    if not (target or "").strip():
        print_error("Collection is required. Pass it as an argument or with --collection.")
    if not confirm:
        typer.confirm(
            f"Delete collection '{target}' and ALL its data? The database is kept.",
            abort=True,
        )
    _impl.do_database_delete_collection(db, target)


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
