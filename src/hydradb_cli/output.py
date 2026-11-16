"""Output formatting for HydraDB CLI.

Supports two modes:
- human: Rich-formatted tables, panels, and styled text (default)
- json: Raw JSON for agent/script consumption
"""

import json
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

import typer
from rich.console import Console, RenderableType
from rich.markup import escape
from rich.table import Table
from rich.theme import Theme

_THEME = Theme(
    {
        "hydra.brand": "bold cyan",
        "hydra.success": "green",
        "hydra.error": "bold red",
        "hydra.warning": "yellow",
        "hydra.dim": "dim",
        "hydra.key": "cyan",
        "hydra.value": "white",
        "hydra.accent": "bold cyan",
    }
)

console = Console(theme=_THEME, highlight=False)
err_console = Console(stderr=True, theme=_THEME, highlight=False)

_output_format: str = "human"
_scope_notice: dict[str, Any] | None = None
_scope_emitted = False

BRAND = "[bold cyan]///[/bold cyan]"


_warned_deprecations: set[str] = set()


def set_output_format(fmt: str) -> None:
    global _output_format, _scope_notice, _scope_emitted
    _output_format = fmt
    _scope_notice = None
    _scope_emitted = False


def set_scope_notice(database: str, collection: str | None, *, database_source: str, collection_source: str) -> None:
    """Record resolved context scope without inventing an omitted collection.

    Nothing is recorded when the caller passed both scopes: the notice exists to
    say a default was used, and there is none to report."""
    global _scope_notice
    if database_source == "explicit" and collection_source == "explicit":
        _scope_notice = None
        return
    _scope_notice = {
        "database": database,
        "collection": collection,
        "database_source": database_source,
        "collection_source": collection_source,
    }


def emit_scope_notice() -> None:
    """Keep human output and piped LLM prompts aware of their effective scope."""
    global _scope_emitted
    if _scope_notice is not None and not _scope_emitted:
        err_console.print("scope: " + json.dumps(_scope_notice, ensure_ascii=False), markup=False)
        _scope_emitted = True


def warn_deprecated(old: str, new: str) -> None:
    """Emit exactly one stderr warning naming the canonical replacement.

    Always goes to stderr (never stdout) so ``--output json`` stays clean, and
    is emitted at most once per process for a given (old, new) pair.
    """
    key = f"{old}=>{new}"
    if key in _warned_deprecations:
        return
    _warned_deprecations.add(key)
    err_console.print(f"[hydra.warning]warning:[/hydra.warning] '{old}' is deprecated; use '{new}' instead.")


def get_output_format() -> str:
    return _output_format


@contextmanager
def spinner(message: str):
    """Show a dots spinner during an operation. No-op in JSON mode or non-TTY."""
    if _output_format == "json" or not console.is_terminal:
        yield
    else:
        with console.status(f"[dim]{message}[/dim]", spinner="dots"):
            yield


def print_json(data: Any) -> None:
    """Print data as formatted JSON to stdout."""
    if _scope_notice is not None and isinstance(data, dict):
        if "scope" not in data:
            data = {**data, "scope": dict(_scope_notice)}
        elif "cli_scope" not in data:
            data = {**data, "cli_scope": dict(_scope_notice)}
        else:
            data = {"data": data, "cli_scope": dict(_scope_notice)}
    typer.echo(json.dumps(data, indent=2, default=str))


def print_success(message: str) -> None:
    """Print a success message with a checkmark in human mode."""
    if _output_format != "json":
        console.print(f"  [hydra.success]\u2713[/hydra.success] {message}")


def print_warning(message: str) -> None:
    """Print a warning message in human mode."""
    if _output_format != "json":
        console.print(f"  [hydra.warning]![/hydra.warning] {message}")


def print_error(message: str, exit_code: int = 1) -> None:
    """Print an error message and exit."""
    if _output_format == "json":
        typer.echo(json.dumps({"success": False, "error": message}))
    else:
        err_console.print(f"  [hydra.error]\u2717 Error:[/hydra.error] {message}")
    raise typer.Exit(code=exit_code)


def print_result(
    data: Any,
    human_formatter: Callable[[Any], str | RenderableType] | None = None,
) -> None:
    """Print API result — JSON in json mode, formatted in human mode.

    The human_formatter may return a plain string or any Rich renderable
    (Panel, Table, Text, Group, etc.).
    """
    if _output_format == "json":
        print_json(data)
    elif human_formatter:
        emit_scope_notice()
        console.print(human_formatter(data))
    else:
        print_json(data)


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Print a Rich table in human mode, or JSON array in json mode."""
    if _output_format == "json":
        items = [dict(zip(headers, row, strict=True)) for row in rows]
        print_json(items)
        return

    if not rows:
        console.print("  [dim]No results.[/dim]")
        return

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        pad_edge=True,
        expand=False,
    )
    for h in headers:
        table.add_column(h)
    for row in rows:
        table.add_row(*[str(c) for c in row])
    console.print(table)


def make_table(
    *columns: str,
    rows: list[list[str]],
    title: str | None = None,
    border_style: str = "dim",
) -> Table:
    """Build a Rich Table without printing it — callers compose into panels etc.

    Cells are escaped, never parsed as markup. Every row this builds is API
    data — ids, titles, provider names, Cypher results — and a title like
    "[draft] Q3 plan" would otherwise be read as a style tag and vanish from
    the output. Callers that want styled text use a Panel or a plain string;
    `make_kv_table` deliberately keeps markup, and its callers rely on it.
    """
    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style=border_style,
        pad_edge=True,
        expand=False,
        title=title,
        title_style="bold",
    )
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[escape(str(c)) for c in row])
    return table


def make_kv_table(pairs: list[tuple[str, str]], title: str | None = None) -> Table:
    """Build a two-column key-value table."""
    table = Table(
        show_header=False,
        border_style="dim",
        pad_edge=True,
        expand=False,
        title=title,
        title_style="bold",
    )
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value")
    for k, v in pairs:
        table.add_row(k, v)
    return table
