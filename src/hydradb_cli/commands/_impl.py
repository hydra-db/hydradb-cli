"""Shared command implementations.

Both the canonical commands (``hydradb query|ingest|list|inspect|delete|
relations|database|doctor``) and the deprecated aliases (``recall``, ``tenant``,
``memories``, ``knowledge``, ``fetch``, ``whoami``) call into these functions, so
an alias always resolves to exactly the same wrapper call as its canonical
command. Everything here talks to the hand-owned :class:`HydraDB` wrapper — never
to the SDK directly.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hydradb_cli.hydra import HydraDBClientError
from hydradb_cli.output import make_kv_table, make_table, print_error, print_result, spinner
from hydradb_cli.utils.common import (
    get_wrapper,
    handle_api_error,
    handle_network_error,
    require_tenant_id,
    resolve_sub_tenant_id,
    validate_range,
)

VALID_MODES = {"fast", "thinking"}
VALID_OPERATORS = {"or", "and", "phrase"}
VALID_KINDS = {"knowledge", "memory"}
VALID_FETCH_MODES = {"content", "url", "both"}

_STATUS_LABELS = {
    "queued": "queued",
    "processing": "processing",
    "indexed": "indexed",
    "completed": "indexed",
    "errored": "errored",
    "failed": "errored",
}

_STATUS_STYLES = {
    "queued": "yellow",
    "processing": "yellow",
    "indexed": "green",
    "errored": "red",
    "not found — source ID does not exist": "red",
}


def _execute(spinner_msg: str, call: Callable[[], Any]) -> Any:
    """Run a wrapper call under a spinner, translating errors to CLI exits."""
    try:
        with spinner(spinner_msg):
            return call()
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)


# ── query ────────────────────────────────────────────────────────────────────


def _format_query_result(r: dict):
    chunks = r.get("chunks") or []
    if not chunks:
        return "[dim]No relevant results found.[/dim]"

    panels: list[Any] = []
    for i, chunk in enumerate(chunks, 1):
        score = chunk.get("relevancy_score")
        score_str = f" • {score:.0%}" if score is not None else ""
        title_text = chunk.get("source_title", "")
        title_str = f" — {title_text}" if title_text else ""

        content = chunk.get("chunk_content", "") or ""
        preview = content[:300] + "..." if len(content) > 300 else content

        panels.append(
            Panel(
                preview,
                title=f"[bold]{i}[/bold]{score_str}{title_str}",
                title_align="left",
                border_style="cyan",
                padding=(0, 1),
            )
        )

    graph = r.get("graph_context") or {}
    query_paths = graph.get("query_paths", []) if isinstance(graph, dict) else []
    if query_paths:
        panels.append(Text(f"  Graph: {len(query_paths)} entity path(s) found.", style="dim"))

    header = Text(f"  Found {len(chunks)} result(s)", style="bold")
    return Group(header, *panels)


def do_query(
    query: str,
    *,
    kind: str | None,
    operator: str | None = None,
    max_results: int = 10,
    mode: str | None = None,
    alpha: float | None = None,
    recency_bias: float | None = None,
    graph_context: bool | None = None,
    additional_context: str | None = None,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
    spinner_msg: str = "Searching...",
) -> None:
    if not query.strip():
        print_error("Query cannot be empty.")
    if kind and kind not in VALID_KINDS:
        print_error(f"--kind must be one of: {', '.join(sorted(VALID_KINDS))}. Got '{kind}'.")
    if mode and mode not in VALID_MODES:
        print_error(f"--mode must be one of: {', '.join(sorted(VALID_MODES))}. Got '{mode}'.")
    if operator and operator not in VALID_OPERATORS:
        print_error(f"--operator must be one of: {', '.join(sorted(VALID_OPERATORS))}. Got '{operator}'.")
    if alpha is not None:
        validate_range(alpha, "alpha", 0.0, 1.0)
    if recency_bias is not None:
        validate_range(recency_bias, "recency-bias", 0.0, 1.0)
    if max_results < 1 or max_results > 50:
        print_error(f"--max-results must be between 1 and 50, got {max_results}.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()

    result = _execute(
        spinner_msg,
        lambda: wrapper.context.query(
            query=query,
            kind=kind,
            operator=operator,
            query_by="text" if operator else None,
            max_results=max_results,
            mode=mode,
            alpha=alpha,
            recency_bias=recency_bias,
            graph_context=graph_context,
            additional_context=additional_context,
            database=tid,
            collection=stid,
        ),
    )
    print_result(result, _format_query_result)


# ── ingest ───────────────────────────────────────────────────────────────────


def _format_ingest_memory(r: dict, text: str):
    success_count = r.get("success_count", 0)
    failed_count = r.get("failed_count", 0)
    preview = text[:80] + "..." if len(text) > 80 else text

    status = "green" if failed_count == 0 else "yellow"
    mark = "✓" if failed_count == 0 else "!"
    lines = [
        f"[{status}]{mark}[/{status}] Memory added ({success_count} success, {failed_count} failed)",
        f'[dim]"{preview}"[/dim]',
    ]
    for item in r.get("results", []):
        # v2 returns `id`; keep `source_id` as a fallback so neither renders "unknown".
        sid = item.get("source_id") or item.get("id", "unknown")
        item_status = item.get("status", "unknown")
        error = item.get("error")
        lines.append(f"[cyan]Source ID:[/cyan] {sid} [dim]({item_status})[/dim]")
        if error:
            lines.append(f"[red]Error:[/red] {error}")
    return Panel("\n".join(lines), border_style=status, padding=(0, 1))


def do_ingest_memory(
    text: str,
    *,
    title: str | None = None,
    source_id: str | None = None,
    user_name: str | None = None,
    infer: bool = True,
    markdown: bool = False,
    upsert: bool = True,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
) -> None:
    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()

    result = _execute(
        "Adding memory...",
        lambda: wrapper.context.ingest(
            kind="memory",
            text=text,
            title=title,
            source_id=source_id,
            user_name=user_name,
            infer=infer,
            is_markdown=markdown,
            upsert=upsert,
            database=tid,
            collection=stid,
        ),
    )
    print_result(result, lambda r: _format_ingest_memory(r, text))


def do_ingest_knowledge_text(
    text: str,
    *,
    title: str | None = None,
    source_id: str | None = None,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
) -> None:
    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()

    result = _execute(
        "Uploading text...",
        lambda: wrapper.context.ingest(
            kind="knowledge",
            text=text,
            title=title,
            source_id=source_id,
            database=tid,
            collection=stid,
        ),
    )

    def fmt(r: dict):
        preview = text[:80] + "..." if len(text) > 80 else text
        lines = [
            f"[green]✓[/green] Knowledge source uploaded to database [bold]{tid}[/bold]",
            f'[dim]"{preview}"[/dim]',
        ]
        for item in r.get("results", []):
            sid = item.get("source_id") or item.get("id", "unknown")
            lines.append(f"[cyan]Source ID:[/cyan] {sid}")
        return Panel("\n".join(lines), border_style="green", padding=(0, 1))

    print_result(result, fmt)


def _human_status(raw: str, error_code: str | None = None) -> str:
    label = _STATUS_LABELS.get(raw.lower(), raw)
    if label == "errored" and error_code:
        if error_code == "FILE_NOT_FOUND":
            return "not found — source ID does not exist"
        return f"errored ({error_code})"
    return label


def _status_style(label: str) -> str:
    for key, style in _STATUS_STYLES.items():
        if key in label:
            return style
    return "white"


def do_ingest_knowledge_files(
    files: list[str],
    *,
    upsert: bool = False,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
) -> None:
    if not files:
        print_error("At least one file path is required.")

    documents = []
    opened = []
    try:
        for fp in files:
            p = Path(fp)
            if not p.exists() or not p.is_file():
                print_error(f"File not found: {fp}")
            if p.stat().st_size == 0:
                print_error(f"File is empty: {fp}")
            fh = p.open("rb")
            opened.append(fh)
            documents.append((p.name, fh, None))

        tid = require_tenant_id(tenant_id)
        stid = resolve_sub_tenant_id(sub_tenant_id)
        wrapper = get_wrapper()

        result = _execute(
            f"Uploading {len(files)} file(s)...",
            lambda: wrapper.context.ingest_many(
                kind="knowledge",
                documents=documents,
                upsert=upsert,
                database=tid,
                collection=stid,
            ),
        )
    finally:
        for fh in opened:
            fh.close()

    def fmt(r: dict):
        table = Table(show_header=True, header_style="bold cyan", border_style="dim", pad_edge=True, expand=False)
        table.add_column("Source ID")
        table.add_column("Status")
        for item in r.get("results", []):
            sid = item.get("source_id") or item.get("id", "unknown")
            status = _human_status(item.get("status", "processing"))
            style = _status_style(status)
            error = item.get("error")
            status_display = f"[{style}]{status}[/{style}]"
            if error:
                status_display += f" [red]({error})[/red]"
            table.add_row(sid, status_display)
        return Panel(
            table,
            title=f"[bold cyan]/// Uploaded {len(files)} file(s) to '{tid}'[/bold cyan]",
            subtitle="[dim]Run 'hydradb verify' to check processing status[/dim]",
            border_style="green",
            padding=(0, 1),
        )

    print_result(result, fmt)


# ── list ─────────────────────────────────────────────────────────────────────


def do_list(
    *,
    kind: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
    spinner_msg: str = "Fetching sources...",
) -> None:
    if kind and kind not in VALID_KINDS:
        print_error(f"--kind must be one of: {', '.join(sorted(VALID_KINDS))}. Got '{kind}'.")
    if page is not None and page < 1:
        print_error(f"--page must be at least 1, got {page}.")
    if page_size is not None and (page_size < 1 or page_size > 100):
        print_error(f"--page-size must be between 1 and 100, got {page_size}.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()

    result = _execute(
        spinner_msg,
        lambda: wrapper.context.list(
            kind=kind,
            page=page,
            page_size=page_size,
            database=tid,
            collection=stid,
        ),
    )

    def fmt(r: dict):
        items = r.get("sources") or r.get("user_memories") or []
        if not items:
            return "[dim]No sources found.[/dim]"
        rows = []
        for i, item in enumerate(items, 1):
            sid = item.get("id") or item.get("memory_id") or item.get("source_id") or "unknown"
            title = item.get("title") or item.get("memory_content") or item.get("content") or item.get("text") or ""
            title = title[:100] + "..." if len(title) > 100 else title
            rows.append([str(i), sid, title, item.get("type", "")])
        table = make_table("#", "ID", "Title", "Type", rows=rows, title=f"Found {len(items)} item(s)")

        parts: list[Any] = [table]
        footer_parts = []
        total = r.get("total")
        pagination = r.get("pagination") or {}
        if total is not None:
            footer_parts.append(f"Total: {total}")
        if isinstance(pagination, dict) and pagination.get("has_next"):
            current = pagination.get("page", 1)
            footer_parts.append(f"Next page: --page {current + 1}")
        if footer_parts:
            parts.append(Text("  " + "  |  ".join(footer_parts), style="dim"))
        return Group(*parts)

    print_result(result, fmt)


# ── inspect ──────────────────────────────────────────────────────────────────


def do_inspect(
    source_id: str,
    *,
    mode: str = "content",
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
) -> None:
    if not source_id.strip():
        print_error("Source ID cannot be empty.")
    if mode not in VALID_FETCH_MODES:
        print_error(f"--mode must be one of: {', '.join(sorted(VALID_FETCH_MODES))}. Got '{mode}'.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()

    try:
        with spinner("Fetching content..."):
            result = wrapper.context.inspect(id=source_id, mode=mode, database=tid, collection=stid)
    except HydraDBClientError as e:
        if e.status_code == 404:
            print_error(
                f"Source '{source_id}' not found. This can happen if the file was uploaded "
                f"under a different collection. Try specifying --sub-tenant-id explicitly."
            )
        else:
            handle_api_error(e)
        return
    except httpx.RequestError as e:
        handle_network_error(e)
        return

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
            body = f"{meta}\n\n{content_text}"
        elif content_b64:
            body = f"{meta}\n\n[dim](Binary content, {len(content_b64)} chars base64-encoded)[/dim]"
        else:
            body = meta
        return Panel(body, title="[bold cyan]/// Source Content[/bold cyan]", border_style="cyan", padding=(0, 1))

    print_result(result, fmt)


# ── delete ───────────────────────────────────────────────────────────────────


def do_delete(
    ids: list[str],
    *,
    kind: str,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
) -> None:
    clean_ids = [i.strip() for i in ids if i.strip()]
    if not clean_ids:
        print_error("IDs cannot be empty.")
    if kind not in VALID_KINDS:
        print_error(f"--kind must be one of: {', '.join(sorted(VALID_KINDS))}. Got '{kind}'.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()

    result = _execute(
        "Deleting...",
        lambda: wrapper.context.delete(ids=clean_ids, kind=kind, database=tid, collection=stid),
    )

    noun = "memory" if kind == "memory" else "knowledge source(s)"
    # v2 returns HTTP 200 with {success:false, deleted_count:0} when nothing
    # matched — that is a no-op, not a success. Surface it as an error (non-zero
    # exit, and `{"success":false,"error":…}` in json mode) rather than claiming
    # a deletion that never happened.
    if result.get("success") is False:
        print_error(f"Nothing deleted: no matching {noun} for {', '.join(clean_ids)}.")

    print_result(
        result, lambda r: f"[green]✓[/green] Deleted {len(clean_ids)} {noun} from database [bold]{tid}[/bold]."
    )


# ── relations ────────────────────────────────────────────────────────────────


def do_relations(
    source_id: str,
    *,
    kind: str | None = None,
    limit: int | None = None,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
) -> None:
    if not source_id.strip():
        print_error("Source ID cannot be empty.")
    if kind and kind not in VALID_KINDS:
        print_error(f"--kind must be one of: {', '.join(sorted(VALID_KINDS))}. Got '{kind}'.")
    if limit is not None and limit < 1:
        print_error(f"--limit must be at least 1, got {limit}.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()

    result = _execute(
        "Fetching graph relations...",
        lambda: wrapper.context.relations(id=source_id, kind=kind, limit=limit, database=tid, collection=stid),
    )

    def fmt(r: dict):
        relations_list = r.get("relations") or []
        if not relations_list:
            return f"[dim]No graph relations found for source '{source_id}'.[/dim]"
        rows = []
        for rel in relations_list:
            src = (rel.get("source") or {}).get("name", "?")
            tgt = (rel.get("target") or {}).get("name", "?")
            for evidence in rel.get("relations", []) or []:
                pred = evidence.get("canonical_predicate", "related to")
                rows.append([src, pred, tgt])
            if not (rel.get("relations")):
                rows.append([src, "related to", tgt])
        # Title on a Panel, not on the Table: a Rich table title wraps to the
        # table's own width, and these three columns are narrow enough that any
        # ordinary source ID breaks mid-token. Same shape as the database
        # subcommands and `inspect`.
        return Panel(
            make_table("Subject", "Predicate", "Object", rows=rows),
            title=f"[bold cyan]/// Relations: {source_id}[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        )

    print_result(result, fmt)


# ── connected subgraph ───────────────────────────────────────────────────────


def do_subgraph(
    source_id: str,
    *,
    kind: str | None = None,
    depth: int | None = None,
    max_sources: int | None = None,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
) -> None:
    if not source_id.strip():
        print_error("Item ID cannot be empty.")
    if kind and kind not in VALID_KINDS:
        print_error(f"--kind must be one of: {', '.join(sorted(VALID_KINDS))}. Got '{kind}'.")
    if depth is not None and not 1 <= depth <= 10:
        print_error(f"--depth must be between 1 and 10, got {depth}.")
    if max_sources is not None and max_sources < 1:
        print_error(f"--max-sources must be at least 1, got {max_sources}.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()

    result = _execute(
        "Traversing the connected subgraph...",
        lambda: wrapper.context.subgraph(
            id=source_id, kind=kind, depth=depth, max_sources=max_sources, database=tid, collection=stid
        ),
    )

    def fmt(r: dict):
        members = r.get("sources") or []
        if not members:
            # An unknown id is an answer, not an error: the server says so with
            # an empty member list rather than a 404, and this says the same.
            return f"[dim]No item '{source_id}' in this collection, so there is no subgraph to show.[/dim]"
        hops = r.get("max_depth_reached") or 0

        # discovered_relation is the MECHANISM (same_thread, parent, child, or
        # a relates_to type); discovered_via is the member this one was
        # reached FROM — another row's id, so the table is also a tree. The
        # parent id is shortened here because it has its own row in full.
        def short(i: str) -> str:
            return i[:12] + "…" if len(i) > 14 else i

        rows = []
        for m in sorted(members, key=lambda m: (m.get("depth", 0), m.get("source_id", ""))):
            d = m.get("depth", 0)
            if d == 0:
                reached = "start"
            else:
                reached = m.get("discovered_relation") or "linked"
                if m.get("discovered_via"):
                    reached += f" ← {short(m['discovered_via'])}"
            what = " ".join(x for x in (m.get("app_provider"), m.get("app_kind")) if x)
            rows.append(
                [str(d), m.get("source_id", "?"), m.get("title") or m.get("app_external_id") or "", what, reached]
            )
        n = len(members)
        if n == 1:
            headline = f"{source_id} stands alone: nothing in the graph links to it yet."
        else:
            headline = f"{n} items connected through {hops} hop{'' if hops == 1 else 's'}"
            if r.get("is_truncated"):
                headline += "  [yellow](clipped at --max-sources; the subgraph continues)[/yellow]"
        footer = (
            f"{len(r.get('relations') or [])} relation(s) among them · "
            f"{len(r.get('auxiliary_relations') or [])} structural link(s) around them"
            + ("  [yellow](structural links clipped)[/yellow]" if r.get("auxiliary_truncated") else "")
        )
        table = make_table("Depth", "Item", "Title", "Kind", "Reached by", rows=rows)
        # Title on a Panel, not on the Table, for the same reason as
        # `relations`: a table title wraps to the table's width and breaks an
        # ordinary id mid-token.
        return Panel(
            Group(headline, table, footer),
            title=f"[bold cyan]/// Subgraph: {source_id}[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        )

    print_result(result, fmt)


# ── ingestion status (verify) ────────────────────────────────────────────────


def do_ingestion_status(
    ids: list[str],
    *,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
) -> None:
    clean_ids = [i.strip() for i in ids if i.strip()]
    if not clean_ids:
        print_error("At least one source ID is required.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()

    result = _execute(
        "Verifying processing status...",
        lambda: wrapper.context.ingestion_status(ids=clean_ids, database=tid, collection=stid),
    )

    def fmt(r: dict):
        table = Table(
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
            pad_edge=True,
            expand=False,
            title=f"Processing status for {len(clean_ids)} source(s)",
            title_style="bold",
        )
        table.add_column("Source ID")
        table.add_column("Status")
        statuses = r.get("statuses", r.get("results", []))
        if isinstance(statuses, list):
            for item in statuses:
                fid = item.get("file_id") or item.get("id", "unknown")
                raw_status = item.get("indexing_status") or item.get("status", "unknown")
                label = _human_status(raw_status, item.get("error_code"))
                table.add_row(fid, f"[{_status_style(label)}]{label}[/{_status_style(label)}]")
        return table

    print_result(result, fmt)


# ── database group ───────────────────────────────────────────────────────────


def do_database_create(database: str) -> None:
    if not database.strip():
        print_error("Database ID cannot be empty.")

    # is_embeddings_tenant is deliberately not passed. The API treats it as an
    # internal flag: it provisions a raw-embeddings collection *instead of* the
    # knowledge and memory collections, so the resulting database cannot be used
    # by any other command in this CLI (see CHANGELOG 'Removed').
    wrapper = get_wrapper()
    result = _execute(
        "Creating database...",
        lambda: wrapper.databases.create(database=database),
    )
    print_result(result, lambda r: f"[green]✓[/green] Database [bold]{database}[/bold] created successfully.")


def do_database_delete(database: str) -> None:
    if not database.strip():
        print_error("Database ID cannot be empty.")
    wrapper = get_wrapper()
    result = _execute("Deleting database...", lambda: wrapper.databases.delete(database=database))
    print_result(result, lambda r: f"[green]✓[/green] Database [bold]{database}[/bold] deleted.")


def do_database_list() -> None:
    wrapper = get_wrapper()
    result = _execute("Listing databases...", lambda: wrapper.databases.list())

    def fmt(r: dict):
        ids = r.get("databases") or r.get("tenant_ids") or []
        if not ids:
            return "[dim]No databases found.[/dim]"
        return make_table("Database ID", rows=[[i] for i in ids], title=f"Found {len(ids)} database(s)")

    print_result(result, fmt)


def do_database_collections(tenant_id: str | None = None) -> None:
    tid = require_tenant_id(tenant_id)
    wrapper = get_wrapper()
    result = _execute("Listing collections...", lambda: wrapper.databases.collections(database=tid))

    def fmt(r: dict):
        ids = r.get("collections") or r.get("sub_tenant_ids") or []
        if not ids:
            return f"[dim]No collections found for database '{tid}'.[/dim]"
        # Title goes on the panel, not the table: a Table title is wrapped to the
        # table's own width, which mangles longer database names. The sibling
        # `database` subcommands all use this panel shape.
        return Panel(
            make_table("Collection ID", rows=[[i] for i in ids]),
            title=f"[bold cyan]/// Collections: {tid}[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        )

    print_result(result, fmt)


def do_database_stats(tenant_id: str | None = None) -> None:
    tid = require_tenant_id(tenant_id)
    wrapper = get_wrapper()
    result = _execute("Fetching database stats...", lambda: wrapper.databases.stats(database=tid))

    def fmt(r: dict):
        pairs = [(k, str(v)) for k, v in r.items() if k not in ("tenant_id", "database")]
        return Panel(
            make_kv_table(pairs),
            title=f"[bold cyan]/// Database: {tid}[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        )

    print_result(result, fmt)


def do_database_readiness(tenant_id: str | None = None) -> None:
    tid = require_tenant_id(tenant_id)
    wrapper = get_wrapper()
    result = _execute("Checking readiness...", lambda: wrapper.databases.readiness(database=tid))

    def fmt(r: dict):
        infra = r.get("infra") or {}
        ready = infra.get("ready_for_ingestion") if isinstance(infra, dict) else None
        pairs = [(k, str(v)) for k, v in (infra.items() if isinstance(infra, dict) else [])]
        header = "[green]ready[/green]" if ready else "[yellow]not ready[/yellow]"
        return Panel(
            make_kv_table(pairs) if pairs else header,
            title=f"[bold cyan]/// Readiness: {tid} — {header}[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        )

    print_result(result, fmt)


def do_database_monitor(tenant_id: str | None = None) -> None:
    """Merged façade over stats + readiness, preserved behind ``monitor``."""
    tid = require_tenant_id(tenant_id)
    wrapper = get_wrapper()

    def _call() -> dict:
        return {
            "database": tid,
            "stats": wrapper.databases.stats(database=tid),
            "readiness": wrapper.databases.readiness(database=tid),
        }

    result = _execute("Fetching database stats...", _call)

    def fmt(r: dict):
        stats = r.get("stats") or {}
        readiness = r.get("readiness") or {}
        infra = readiness.get("infra") or {}
        ready = infra.get("ready_for_ingestion") if isinstance(infra, dict) else None
        pairs = [(k, str(v)) for k, v in stats.items() if k not in ("tenant_id", "database")]
        pairs.append(("ready_for_ingestion", str(ready)))
        return Panel(
            make_kv_table(pairs),
            title=f"[bold cyan]/// Database: {tid}[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        )

    print_result(result, fmt)
