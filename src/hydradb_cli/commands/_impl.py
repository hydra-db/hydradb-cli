"""Shared command implementations.

Both the canonical commands (``hydradb query|ingest|list|inspect|delete|
relations|database|doctor``) and the deprecated aliases (``recall``, ``tenant``,
``memories``, ``knowledge``, ``fetch``, ``whoami``) call into these functions, so
an alias always resolves to exactly the same wrapper call as its canonical
command. Everything here talks to the hand-owned :class:`HydraDB` wrapper — never
to the SDK directly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hydradb_cli.hydra import HydraDBClientError
from hydradb_cli.hydra.client import LAYOUT_SPLIT, LAYOUT_UNIFIED
from hydradb_cli.output import (
    console,
    err_console,
    get_output_format,
    make_kv_table,
    make_table,
    print_error,
    print_json,
    print_result,
    spinner,
)
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
VALID_RATINGS = {"positive", "negative", "neutral"}
# Who is reporting. Validated locally for the same reason --rating is: the
# server rejects anything else, but only after a round trip, and a typo like
# "agnet" is worth catching before it costs one.
VALID_SOURCES = {"user", "agent"}
VALID_FETCH_MODES = {"content", "url", "both"}
# Unified ingest (PRO-1618): the context_category labels and conversation roles
# the server accepts. Validated locally for the same reason --rating is.
VALID_CATEGORIES = {"auto", "user_preference", "business_knowledge", "decision_trace"}
VALID_ROLES = {"user", "assistant", "system"}
# happened_at is a calendar date, YYYY-MM-DD only: no time, no zone.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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


# ── storage layout (PRO-1618) ────────────────────────────────────────────────


def _is_unified(wrapper: Any, database: str) -> bool:
    """Whether ``database`` is a unified database.

    One memoised ``GET /databases`` probe per wrapper. A successful probe that
    does not list ``database`` reads as split, which is what every pre-PRO-1618
    database is; a FAILED probe is the error it is, not a guess: guessing
    split would send the split request shape to a database that may be
    unified. Compared by value so a mocked wrapper (whose ``layout`` returns a
    MagicMock) reads as split too.
    Every command branches on THIS, never on a request flag: a unified database
    never receives ``type``, and a split one keeps every existing call as is.
    """
    try:
        return wrapper.databases.layout(database) == LAYOUT_UNIFIED
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)


def database_layout(tenant_id: str | None) -> tuple[str, str]:
    """The database a command is about to touch and its layout, ``unified`` or ``split``."""
    tid = require_tenant_id(tenant_id)
    return tid, (LAYOUT_UNIFIED if _is_unified(get_wrapper(), tid) else LAYOUT_SPLIT)


def _refuse_kind_on_unified(kind: str | None, database: str) -> None:
    """A unified database has one corpus, so there is no kind to select.

    The server refuses ``knowledge``/``memory`` there and the contract says
    never to send ``type`` at all. Refused locally, naming the rule, rather
    than silently swapped for something the user did not ask for.
    """
    if kind:
        print_error(
            f"Database '{database}' is unified: it has one corpus, so a kind ('{kind}') cannot be selected. "
            "Re-run without --kind."
        )


def _refuse_split_write_on_unified(wrapper: Any, database: str, layout: str | None, what: str) -> None:
    """The split ingest shapes (``memories``/``app_knowledge``/``documents``
    with ``type``) are refused by a unified database. Probed here unless the
    caller already resolved the layout, so the deprecated aliases are covered."""
    unified = layout == LAYOUT_UNIFIED if layout is not None else _is_unified(wrapper, database)
    if unified:
        print_error(
            f"Database '{database}' is unified: {what}. "
            "Use 'hydradb ingest --text ...' or 'hydradb ingest --conversation-file ...' (no --kind)."
        )


# ── query ────────────────────────────────────────────────────────────────────


def _feedback_hint(r: dict) -> str:
    """The line that makes ``hydradb feedback`` reachable.

    Printed verbatim so it can be copied, and printed on an EMPTY result too:
    a query that found nothing is exactly the case most worth reporting, and
    it is the one where there are no chunk ids to fall back on.
    """
    request_id = r.get("request_id")
    if not request_id:
        return ""
    return f'\n[dim]request_id: {request_id}  ·  rate it: hydradb feedback {request_id} --feedback "..."[/dim]'


def _is_unified_query_body(r: dict) -> bool:
    """Shape detection (contract rule 4).

    A unified body carries ``llm_prompt``, a ``graph`` ARRAY and a
    ``forceful_relations`` ARRAY; a split body carries ``chunk_content``/
    ``graph_context``. A split body can carry ``graph`` and
    ``forceful_relations`` too, but as objects (``{paths}``,
    ``{declared, inferred}``), so both are told apart by type, never by the key
    being there. Stored logs and split databases keep producing the old shape,
    so the layout probe alone cannot decide this.
    """
    return "llm_prompt" in r or isinstance(r.get("graph"), list) or isinstance(r.get("forceful_relations"), list)


def _preview(text: str, limit: int) -> str:
    return text[:limit] + "..." if len(text) > limit else text


def _pct(score: Any) -> str:
    return f"{score:.0%}" if isinstance(score, (int, float)) and not isinstance(score, bool) else ""


def _unified_chunk_panel(chunk: dict, label: str) -> Panel:
    """One ``chunks[]`` item: context_id, score, content, enrichment and
    enrichment_kind, temporal facts. ``enrichment`` is a plain string and
    ``enrichment_kind`` its sibling (the declared context_category); either
    can be absent, and a kind with no enrichment is still shown. Content and
    enrichment are shown whole, never trimmed (the unified answer is not
    compacted anywhere). Content is API data, so it is rendered as plain Text
    and never parsed as markup."""
    score = _pct(chunk.get("score"))
    score_str = f" • {score}" if score else ""
    context_id = chunk.get("context_id") or ""
    id_str = f" • {escape(str(context_id))}" if context_id else ""
    body: list[Any] = [Text(chunk.get("content") or "")]
    enrichment = chunk.get("enrichment")
    enrichment = enrichment if isinstance(enrichment, str) else ""
    kind = chunk.get("enrichment_kind")
    kind = kind if isinstance(kind, str) else ""
    if enrichment or kind:
        head = f"enrichment ({kind})" if kind else "enrichment"
        body.append(Text.assemble((f"{head}: " if enrichment else head, "dim"), enrichment))
    for fact in chunk.get("temporal") or []:
        if isinstance(fact, dict):
            span = " to ".join(str(x) for x in (fact.get("start_date"), fact.get("end_date")) if x)
            body.append(
                Text.assemble(("temporal: ", "dim"), fact.get("content") or "", (f" [{span}]" if span else "", "dim"))
            )
    return Panel(
        Group(*body),
        title=f"[bold]{label}[/bold]{score_str}{id_str}",
        title_align="left",
        border_style="cyan",
        padding=(0, 1),
    )


#: ``graph[].origin`` (PRO-1618): which retrieval lane found a path.
#: ``query_path`` was grown from the entities in the query; ``chunk_relation``
#: is the neighbourhood of a chunk that ranked.
_ORIGIN_QUERY_PATH = "query_path"
_ORIGIN_CHUNK_RELATION = "chunk_relation"


def _list_field(r: dict, key: str) -> list:
    """``r[key]`` when it is a list, else ``[]``. The unified keys are read by
    type, never by presence: a split body has a ``graph`` and a
    ``forceful_relations`` too, but as objects."""
    value = r.get(key)
    return value if isinstance(value, list) else []


def _chunk_labels(chunks: list, forceful: list) -> dict[str, tuple[str, str]]:
    """``chunk_id -> (label, context_id)`` for every chunk the body returned:
    ``1``, ``2``... for ``chunks[]`` and ``R1``, ``R2``... for
    ``forceful_relations[].chunk``, the labels ``llm_prompt`` cites them by.
    First write wins, as on the server: a chunk that is both a result and a
    forceful relation is cited as the result. This is how a graph hop is tied
    to its chunk and context: by ``relation.chunk_id``, never by parsing it."""
    labels: dict[str, tuple[str, str]] = {}
    entries = [(str(i), chunk) for i, chunk in enumerate(chunks, 1)]
    entries += [(f"R{i}", rel.get("chunk") or {}) for i, rel in enumerate(forceful, 1)]
    for label, chunk in entries:
        chunk_id = chunk.get("chunk_id")
        if chunk_id and chunk_id not in labels:
            labels[chunk_id] = (label, chunk.get("context_id") or "")
    return labels


def _hop_chunk(triplet: dict, labels: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    return labels.get((triplet.get("relation") or {}).get("chunk_id") or "")


def _hop_line(triplet: dict, labels: dict[str, tuple[str, str]], *, cite: bool) -> str:
    src = (triplet.get("source") or {}).get("name") or "?"
    predicate = (triplet.get("relation") or {}).get("predicate") or "related to"
    tgt = (triplet.get("target") or {}).get("name") or "?"
    line = f"{src} -> {predicate} -> {tgt}"
    hit = _hop_chunk(triplet, labels) if cite else None
    return f"{line} [{hit[0]}]" if hit else line


def _graph_panels(graph: list, labels: dict[str, tuple[str, str]]) -> list[Panel]:
    """``graph[]`` grouped by ``origin``, the way the split body kept
    ``query_paths`` and ``chunk_relations`` apart.

    Query paths are listed with each hop citing the returned chunk it was
    extracted from, when there is one. Chunk relations are listed under the
    chunk they hang under: every hop's ``relation.chunk_id`` is that chunk.
    A path with no known origin is still shown, in a group of its own, rather
    than guessed into one. ``P`` labels are positions in ``graph[]``, which is
    what ``llm_prompt`` numbers paths by.
    """
    query_rows: list[list[str]] = []
    chunk_rows: list[list[str]] = []
    other_rows: list[list[str]] = []
    for i, path in enumerate(graph, 1):
        if not isinstance(path, dict):
            continue
        triplets = [t for t in path.get("triplets") or [] if isinstance(t, dict)]
        summary = path.get("path_summary") or ""
        origin = path.get("origin")
        if origin == _ORIGIN_CHUNK_RELATION:
            under: list[str] = []
            for triplet in triplets:
                hit = _hop_chunk(triplet, labels)
                cell = f"[{hit[0]}] {hit[1]}".rstrip() if hit else ""
                if cell and cell not in under:
                    under.append(cell)
            hops = "\n".join(_hop_line(t, labels, cite=False) for t in triplets)
            chunk_rows.append([f"P{i}", "\n".join(under), summary, hops])
            continue
        hops = "\n".join(_hop_line(t, labels, cite=True) for t in triplets)
        (query_rows if origin == _ORIGIN_QUERY_PATH else other_rows).append([f"P{i}", summary, hops])

    groups = (
        (query_rows, "query path(s)", ("#", "Path", "Triplets")),
        (chunk_rows, "chunk relation path(s)", ("#", "Chunk", "Path", "Triplets")),
        (other_rows, "path(s) with no known origin", ("#", "Path", "Triplets")),
    )
    return [
        Panel(
            make_table(*columns, rows=rows),
            title=f"[bold cyan]/// Graph: {len(rows)} {what}[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        )
        for rows, what, columns in groups
        if rows
    ]


def _forceful_relations_panel(forceful: list) -> Panel:
    """``forceful_relations[]``: chunks in the result because the caller
    declared a relation at ingest, with the declared edge (``via``) that
    pulled each one in. ``R`` labels match ``llm_prompt``'s."""
    rows = []
    for i, rel in enumerate(forceful, 1):
        via = rel.get("via") or {}
        chunk = rel.get("chunk") or {}
        rows.append(
            [
                f"R{i}",
                via.get("from") or "",
                via.get("to") or chunk.get("context_id") or "",
                _pct(chunk.get("score")),
                chunk.get("content") or "",
            ]
        )
    return Panel(
        make_table("#", "From", "To", "Score", "Content", rows=rows),
        title=f"[bold cyan]/// Forceful relations: {len(forceful)} chunk(s)[/bold cyan]",
        border_style="cyan",
        padding=(0, 1),
    )


def _format_unified_query_result(r: dict, request_id: str | None = None):
    """The four-key unified body (PRO-1618) for a person: ``chunks[]`` as
    panels, ``graph[]`` as path tables grouped by ``origin``,
    ``forceful_relations[]`` as a table of what declared relations pulled in
    (no section when there are none), and the feedback hint. ``llm_prompt`` is
    not shown here: ``--llm`` prints it verbatim."""
    chunks = _list_field(r, "chunks")
    graph = _list_field(r, "graph")
    forceful = [f for f in _list_field(r, "forceful_relations") if isinstance(f, dict)]
    hint = _feedback_hint({"request_id": request_id}) if request_id else ""
    if not chunks and not graph and not forceful:
        return "[dim]No relevant results found.[/dim]" + hint

    parts: list[Any] = [Text(f"  Found {len(chunks)} result(s)", style="bold")]
    for i, chunk in enumerate(chunks, 1):
        parts.append(_unified_chunk_panel(chunk, str(i)))

    parts.extend(_graph_panels(graph, _chunk_labels(chunks, forceful)))

    if forceful:
        parts.append(_forceful_relations_panel(forceful))

    if hint:
        parts.append(Text.from_markup(hint.lstrip("\n")))
    return Group(*parts)


def _print_unified_query(body: dict, request_id: str | None, *, llm: bool) -> None:
    """Print a unified query result. ``--output json`` is the body verbatim, the
    four keys and nothing added; ``--llm`` is the server-built prompt on plain
    stdout (not Rich, which would re-wrap it at the terminal width and read
    bracketed fragments as markup), with the feedback hint on stderr so the
    prompt can be piped; otherwise the structured rendering."""
    if get_output_format() == "json":
        print_json(body)
        return
    if llm:
        typer.echo(body.get("llm_prompt") or "")
        if request_id:
            err_console.print(_feedback_hint({"request_id": request_id}).lstrip("\n"))
        return
    console.print(_format_unified_query_result(body, request_id))


def _format_query_result(r: dict):
    if _is_unified_query_body(r):
        # A unified body reached the split path (a server that does not list a
        # database's layout still answers a type-less query on a unified one
        # in the unified shape). Render it as what it is.
        return _format_unified_query_result(r, r.get("request_id"))
    chunks = r.get("chunks") or []
    if not chunks:
        return "[dim]No relevant results found.[/dim]" + _feedback_hint(r)

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

    hint = _feedback_hint(r)
    if hint:
        panels.append(Text.from_markup(hint.lstrip("\n")))

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
    titles: list[str] | None = None,
    acl: list[str] | None = None,
    follow_forceful_relations: bool | None = None,
    llm: bool = False,
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

    clean_titles: list[str] | None = None
    if titles:
        clean_titles = []
        seen_titles: set[str] = set()
        for value in titles:
            title = value.strip()
            if not title:
                print_error("--title cannot be empty or whitespace-only.")
            key = title.lower()
            if key not in seen_titles:
                seen_titles.add(key)
                clean_titles.append(title)
    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()

    if _is_unified(wrapper, tid):
        _refuse_kind_on_unified(kind, tid)
        outcome = _execute(
            spinner_msg,
            lambda: wrapper.context.query_unified(
                query=query,
                operator=operator,
                query_by="text" if operator else None,
                max_results=max_results,
                mode=mode,
                alpha=alpha,
                recency_bias=recency_bias,
                graph_context=graph_context,
                additional_context=additional_context,
                titles=clean_titles,
                acl=acl,
                follow_forceful_relations=follow_forceful_relations,
                database=tid,
                collection=stid,
            ),
        )
        body, request_id = outcome if isinstance(outcome, tuple) else (outcome, None)
        _print_unified_query(body if isinstance(body, dict) else {}, request_id, llm=llm)
        return

    if follow_forceful_relations is not None:
        print_error(
            "--follow-forceful-relations/--no-follow-forceful-relations applies to unified databases only; "
            f"'{tid}' is a split database."
        )
    if llm:
        print_error(f"--llm applies to unified databases only; '{tid}' is a split database and has no llm_prompt.")

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
            titles=clean_titles,
            acl=acl,
            database=tid,
            collection=stid,
        ),
    )
    print_result(result, _format_query_result)


# ── feedback ─────────────────────────────────────────────────────────────────


def _format_feedback_result(r: dict):
    # `recorded: false` means accepted but not durably stored. Reporting that
    # as success would tell someone their evaluation run was captured when it
    # was not.
    if r.get("recorded") is False:
        return Panel(
            "[yellow]![/yellow] Feedback accepted but NOT durably stored.\n"
            "[dim]The server answered without recording it; treat this run as uncaptured.[/dim]",
            border_style="yellow",
            padding=(0, 1),
        )

    lines = [f"[green]✓[/green] Feedback recorded for request {r.get('request_id', '(unknown)')}"]
    if r.get("feedback_id"):
        lines.append(f"[cyan]Feedback ID:[/cyan] {r['feedback_id']}")
    if r.get("created_at"):
        lines.append(f"[dim]{r['created_at']}[/dim]")
    return Panel("\n".join(lines), border_style="green", padding=(0, 1))


def do_feedback(
    request_id: str,
    *,
    feedback: str | None = None,
    rating: str | None = None,
    ground_truth_answer: str | None = None,
    ground_truth_source_ids: list[str] | None = None,
    source: str | None = None,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
) -> None:
    if rating and rating not in VALID_RATINGS:
        print_error(f"--rating must be one of: {', '.join(sorted(VALID_RATINGS))}. Got '{rating}'.")
    if source and source not in VALID_SOURCES:
        print_error(f"--source must be one of: {', '.join(sorted(VALID_SOURCES))}. Got '{source}'.")
    if not request_id.strip():
        print_error("REQUEST_ID cannot be empty. Run 'hydradb query' and use the request id it prints.")
    # The wrapper guards this too, for anyone importing it as a library. But it
    # reports a refusal as HydraDBClientError(0, ...), and status 0 is this
    # codebase's marker for a TRANSPORT failure (errors.py uses it only for
    # connect/timeout), which `handle_api_error` renders as "Connection error:".
    # Caught here instead, the way --rating and --kind are, so a local refusal
    # reads as one rather than blaming the network.
    if not any((value or "").strip() for value in [feedback, ground_truth_answer, *(ground_truth_source_ids or [])]):
        print_error(
            "feedback needs something to record: pass --feedback, --ground-truth-answer, or --ground-truth-source-id."
        )

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()

    result = _execute(
        "Recording feedback...",
        lambda: wrapper.feedback.submit(
            request_id=request_id,
            feedback=feedback,
            rating=rating,
            ground_truth_answer=ground_truth_answer,
            ground_truth_source_ids=ground_truth_source_ids,
            source=source,
            database=tid,
            collection=stid,
        ),
    )
    print_result(result, _format_feedback_result)


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
    layout: str | None = None,
) -> None:
    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()
    _refuse_split_write_on_unified(wrapper, tid, layout, "a memory (kind) cannot be written to it")

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
    layout: str | None = None,
) -> None:
    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()
    _refuse_split_write_on_unified(wrapper, tid, layout, "knowledge text (kind) cannot be written to it")

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
    layout: str | None = None,
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
        _refuse_split_write_on_unified(
            wrapper, tid, layout, "files are not accepted (text or a conversation only). Extract the text first"
        )

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


# ── ingest (unified databases, PRO-1618) ─────────────────────────────────────


def _load_conversation(path: str) -> list[dict[str, Any]]:
    """Read ``--conversation-file``: a JSON list of ``{role, content, name?}``
    turns. Every turn is checked here so a bad one is named by index locally
    rather than as ``context[0]`` after a round trip."""
    p = Path(path)
    if not p.is_file():
        print_error(f"Conversation file not found: {path}")
    try:
        turns = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print_error(f"--conversation-file must be a JSON list of {{role, content, name?}} turns: {exc}")
    if not isinstance(turns, list) or not turns:
        print_error("--conversation-file must be a non-empty JSON list of {role, content, name?} turns.")
    clean: list[dict[str, Any]] = []
    for i, turn in enumerate(turns):
        if not isinstance(turn, dict):
            print_error(f"conversation[{i}] must be an object with role and content.")
        role = turn.get("role")
        if role not in VALID_ROLES:
            print_error(f"conversation[{i}].role must be one of: {', '.join(sorted(VALID_ROLES))}. Got {role!r}.")
        content = turn.get("content")
        if not isinstance(content, str) or not content.strip():
            print_error(f"conversation[{i}].content must be a non-empty string.")
        unknown = sorted(set(turn) - {"role", "content", "name"})
        if unknown:
            print_error(
                f"conversation[{i}] has unknown field(s): {', '.join(unknown)}. Only role, content and name are accepted."
            )
        item: dict[str, Any] = {"role": role, "content": content}
        name = turn.get("name")
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                print_error(f"conversation[{i}].name must be a non-empty string when present.")
            item["name"] = name
        clean.append(item)
    return clean


def _parse_json_object(raw: str | None, flag: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        print_error(f"{flag} must be a JSON object: {exc}")
    if not isinstance(parsed, dict):
        print_error(f'{flag} must be a JSON object, for example \'{{"team": "support"}}\'.')
    return parsed


def build_context_item(
    *,
    text: str | None = None,
    conversation: list[dict[str, Any]] | None = None,
    context_id: str | None = None,
    title: str | None = None,
    enrich: bool = True,
    instructions: str | None = None,
    happened_at: str | None = None,
    attributes: dict[str, Any] | None = None,
    custom_attributes: dict[str, Any] | None = None,
    category: str | None = None,
    forceful_relations: list[str] | None = None,
    acl: list[str] | None = None,
    upsert: bool = True,
) -> dict[str, Any]:
    """One ``context[]`` item in the contract's exact field names (PRO-1618).

    Exactly one of ``text`` or ``conversation``. Optional fields are omitted
    when unset, never sent as null; ``enrich`` and ``upsert`` are always sent
    because the CLI always has a value for them.
    """
    if (text is None) == (conversation is None):
        print_error("Pass exactly one of --text or --conversation-file.")
    item: dict[str, Any] = {}
    if context_id:
        item["context_id"] = context_id
    if title:
        item["title"] = title
    if text is not None:
        item["text"] = text
    else:
        item["conversation"] = conversation
    item["enrich"] = bool(enrich)
    item["upsert"] = bool(upsert)
    if instructions:
        item["instructions"] = instructions
    if happened_at:
        try:
            valid = bool(_DATE_RE.match(happened_at)) and date.fromisoformat(happened_at) is not None
        except ValueError:
            valid = False
        if not valid:
            print_error(f"--happened-at must be a calendar date in YYYY-MM-DD form, got '{happened_at}'.")
        item["happened_at"] = happened_at
    if attributes is not None:
        item["attributes"] = attributes
    if custom_attributes is not None:
        item["custom_attributes"] = custom_attributes
    if category:
        if category not in VALID_CATEGORIES:
            print_error(f"--category must be one of: {', '.join(sorted(VALID_CATEGORIES))}. Got '{category}'.")
        item["context_category"] = category
    if forceful_relations:
        ids: list[str] = []
        for value in forceful_relations:
            candidate = (value or "").strip()
            if not candidate:
                print_error("--forceful-relation cannot be empty or whitespace-only.")
            if candidate not in ids:
                ids.append(candidate)
        item["forceful_relations"] = {"ids": ids}
    if acl is not None:
        item["acl"] = list(acl)
    return item


def _format_ingest_unified(r: dict, item: dict[str, Any]):
    success_count = r.get("success_count", 0)
    failed_count = r.get("failed_count", 0)
    ok = failed_count == 0
    status = "green" if ok else "yellow"
    mark = "✓" if ok else "!"
    if "conversation" in item:
        preview = f"conversation, {len(item['conversation'])} turn(s)"
    else:
        preview = f'"{_preview(item.get("text") or "", 80)}"'
    lines = [
        f"[{status}]{mark}[/{status}] Context queued ({success_count} success, {failed_count} failed)",
        f"[dim]{escape(preview)}[/dim]",
    ]
    for res in r.get("results", []) or []:
        # The 202 still spells the item's context_id `source_id`.
        cid = res.get("source_id") or res.get("context_id") or res.get("id") or "unknown"
        lines.append(f"[cyan]Context ID:[/cyan] {escape(str(cid))} [dim]({res.get('status', 'unknown')})[/dim]")
        if res.get("error"):
            code = f" ({res['error_code']})" if res.get("error_code") else ""
            lines.append(f"[red]Error:[/red] {escape(str(res['error']))}{escape(code)}")
    lines.append("[dim]Poll with 'hydradb verify <context id>'.[/dim]")
    return Panel("\n".join(lines), border_style=status, padding=(0, 1))


def do_ingest_unified(
    *,
    text: str | None = None,
    conversation_file: str | None = None,
    context_id: str | None = None,
    title: str | None = None,
    enrich: bool = True,
    instructions: str | None = None,
    happened_at: str | None = None,
    attributes: str | None = None,
    custom_attributes: str | None = None,
    category: str | None = None,
    forceful_relations: list[str] | None = None,
    acl: list[str] | None = None,
    upsert: bool = True,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
) -> None:
    """Ingest one context item into a UNIFIED database: a JSON ``POST
    /context/ingest`` with the ``context`` list, no ``type``, no multipart."""
    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    if conversation_file and text is not None:
        print_error("Pass exactly one of --text or --conversation-file.")
    conversation = _load_conversation(conversation_file) if conversation_file else None
    item = build_context_item(
        text=text,
        conversation=conversation,
        context_id=context_id,
        title=title,
        enrich=enrich,
        instructions=instructions,
        happened_at=happened_at,
        attributes=_parse_json_object(attributes, "--attributes"),
        custom_attributes=_parse_json_object(custom_attributes, "--custom-attributes"),
        category=category,
        forceful_relations=forceful_relations,
        acl=acl,
        upsert=upsert,
    )
    wrapper = get_wrapper()

    result = _execute(
        "Ingesting context...",
        lambda: wrapper.context.ingest_context([item], database=tid, collection=stid),
    )
    print_result(result, lambda r: _format_ingest_unified(r, item))
    # A 202 only means accepted: the per-item verdicts are in the body, and a
    # failure among them is a failed ingest. The panel above already printed
    # the server's own error for the row; the exit code is what says the
    # command did not succeed.
    failed_rows = [
        r
        for r in (result.get("results") or [])
        if isinstance(r, dict) and (r.get("error") or r.get("status") in ("failed", "errored"))
    ]
    if (result.get("failed_count") or 0) > 0 or failed_rows:
        raise typer.Exit(code=1)


# ── list ─────────────────────────────────────────────────────────────────────


def do_list(
    *,
    kind: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    acl: list[str] | None = None,
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
    if _is_unified(wrapper, tid):
        # One corpus: no kind is selected and none is sent.
        _refuse_kind_on_unified(kind, tid)

    result = _execute(
        spinner_msg,
        lambda: wrapper.context.list(
            kind=kind,
            page=page,
            page_size=page_size,
            acl=acl,
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
    acl: list[str] | None = None,
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
            result = wrapper.context.inspect(id=source_id, mode=mode, acl=acl, database=tid, collection=stid)
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
    kind: str | None,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
) -> None:
    clean_ids = [i.strip() for i in ids if i.strip()]
    if not clean_ids:
        print_error("IDs cannot be empty.")
    if kind is not None and kind not in VALID_KINDS:
        print_error(f"--kind must be one of: {', '.join(sorted(VALID_KINDS))}. Got '{kind}'.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    wrapper = get_wrapper()
    if _is_unified(wrapper, tid):
        # One corpus: no kind is selected and none is sent.
        _refuse_kind_on_unified(kind, tid)
        noun = "item(s)"
    else:
        # The split default, unchanged: a delete without --kind is a knowledge delete.
        kind = kind or "knowledge"
        noun = "memory" if kind == "memory" else "knowledge source(s)"

    result = _execute(
        "Deleting...",
        lambda: wrapper.context.delete(ids=clean_ids, kind=kind, database=tid, collection=stid),
    )

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
    acl: list[str] | None = None,
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
    if _is_unified(wrapper, tid):
        # One corpus: no kind is selected and none is sent.
        _refuse_kind_on_unified(kind, tid)

    result = _execute(
        "Fetching graph relations...",
        lambda: wrapper.context.relations(id=source_id, kind=kind, limit=limit, acl=acl, database=tid, collection=stid),
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
    acl: list[str] | None = None,
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
    if _is_unified(wrapper, tid):
        # One corpus: no kind is selected and none is sent.
        _refuse_kind_on_unified(kind, tid)

    result = _execute(
        "Traversing the connected subgraph...",
        lambda: wrapper.context.subgraph(
            id=source_id, kind=kind, depth=depth, max_sources=max_sources, acl=acl, database=tid, collection=stid
        ),
    )

    def fmt(r: dict):
        members = r.get("sources") or []
        if not members:
            # An unknown id is an answer, not an error: the server says so with
            # an empty member list rather than a 404, and this says the same.
            return f"[dim]No item '{escape(source_id)}' in this collection, so there is no subgraph to show.[/dim]"
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
        # One member means "nothing links to this" only when the traversal ran
        # to completion. Clipped at --max-sources, one row is just where we
        # stopped looking, and calling a connected item isolated is a wrong
        # answer rather than a terse one.
        if n == 1 and not r.get("is_truncated"):
            headline = f"{escape(source_id)} stands alone: nothing in the graph links to it yet."
        else:
            headline = f"{n} item{'' if n == 1 else 's'} connected through {hops} hop{'' if hops == 1 else 's'}"
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
            title=f"[bold cyan]/// Subgraph: {escape(source_id)}[/bold cyan]",
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


def do_database_create(database: str, layout: str | None = None) -> None:
    if not database.strip():
        print_error("Database ID cannot be empty.")
    if layout and layout not in (LAYOUT_SPLIT, LAYOUT_UNIFIED):
        print_error(f"--type must be '{LAYOUT_SPLIT}' or '{LAYOUT_UNIFIED}'. Got '{layout}'.")

    # is_embeddings_tenant is deliberately not passed. The API treats it as an
    # internal flag: it provisions a raw-embeddings collection *instead of* the
    # knowledge and memory collections, so the resulting database cannot be used
    # by any other command in this CLI (see CHANGELOG 'Removed').
    wrapper = get_wrapper()
    result = _execute(
        "Creating database...",
        lambda: wrapper.databases.create(database=database, layout=layout),
    )
    suffix = " (unified: one corpus, no --kind on later commands)" if layout == LAYOUT_UNIFIED else ""
    print_result(result, lambda r: f"[green]✓[/green] Database [bold]{database}[/bold] created successfully.{suffix}")


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
        # `details[]` (PRO-1618) carries each database's storage layout; a
        # server that omits it has only split databases.
        layouts = {row.get("database"): row.get("type") for row in (r.get("details") or []) if isinstance(row, dict)}
        rows = [[i, LAYOUT_UNIFIED if layouts.get(i) == LAYOUT_UNIFIED else LAYOUT_SPLIT] for i in ids]
        return make_table("Database ID", "Type", rows=rows, title=f"Found {len(ids)} database(s)")

    print_result(result, fmt)


def do_database_delete_collection(database: str | None, collection: str) -> None:
    """Delete one collection, leaving the parent database and its siblings intact."""
    tid = require_tenant_id(database)
    if not (collection or "").strip():
        print_error("Collection ID cannot be empty.")
    wrapper = get_wrapper()
    result = _execute(
        "Deleting collection...",
        lambda: wrapper.databases.delete_collection(database=tid, collection=collection),
    )

    def fmt(_r: dict):
        # Deletion is asynchronous: the API accepts and purges in the background.
        # Say that rather than reporting a completion we cannot observe — the
        # collection stays fenced (404) until every store is purged, and there
        # is no completion endpoint to poll.
        return (
            f"[green]✓[/green] Collection [bold]{collection}[/bold] in database "
            f"[bold]{tid}[/bold] scheduled for deletion.\n"
            "[dim]Cleanup runs in the background. The collection rejects reads and writes "
            "until it finishes; re-run this command to retry a cleanup that failed.[/dim]"
        )

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
