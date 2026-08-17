"""HydraDB CLI — Agent-friendly command line interface for HydraDB.

The context layer for AI. Query context, ingest knowledge and memories, and
inspect stored data from the terminal.

Usage:
    hydradb <command> [options]

Canonical commands:
    query      Retrieve knowledge or memories
    ingest     Store memories or knowledge (text or files)
    list       List ingested sources and memories
    inspect    Fetch a source's content
    delete     Delete memories or knowledge sources
    relations  Explore knowledge-graph relations
    verify     Check per-source ingestion status
    database   Manage databases
    graph      Run Cypher against graph collections you own (BYOG)
    connectors Sync external sources (Slack, GitHub, Notion, …)
    doctor     Check config and API reachability
    login / logout / config   Authentication and configuration

Every legacy command (tenant, memories, knowledge, recall, fetch, whoami)
remains available as a deprecated alias.
"""

import typer

from hydradb_cli import __version__
from hydradb_cli.commands import (
    auth,
    canonical,
    config_cmd,
    connectors,
    fetch,
    graph,
    knowledge,
    memories,
    recall,
    tenant,
)
from hydradb_cli.output import console, set_output_format

app = typer.Typer(
    name="hydradb",
    help=(
        "[bold cyan]///[/bold cyan] HydraDB CLI\n\n"
        "The context layer for AI — query context, ingest knowledge and memories, "
        "and inspect stored data from the terminal."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold cyan]///[/bold cyan] [bold]hydradb-cli[/bold] {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    output: str = typer.Option(
        "human",
        "--output",
        "-o",
        help="Output format: 'human' (default) or 'json'.",
        envvar="HYDRADB_OUTPUT",
    ),
    version: bool | None = typer.Option(
        None,
        "--version",
        "-v",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """[bold cyan]///[/bold cyan] HydraDB CLI — The context layer for AI, from the terminal."""
    if output not in ("human", "json"):
        typer.echo(f"Error: --output must be 'human' or 'json', got '{output}'", err=True)
        raise typer.Exit(code=1)
    set_output_format(output)


# ── Canonical commands ───────────────────────────────────────────────────────
app.command(name="query", help="Retrieve knowledge or memories.")(canonical.query)
app.command(name="ingest", help="Store memories or knowledge (text or files).")(canonical.ingest)
app.command(name="list", help="List ingested sources and memories.")(canonical.list_items)
app.command(name="inspect", help="Fetch a source's content by ID.")(canonical.inspect)
app.command(name="delete", help="Delete memories or knowledge sources.")(canonical.delete)
app.command(name="relations", help="Explore knowledge-graph relations.")(canonical.relations)
app.command(name="verify", help="Check per-source ingestion status.")(canonical.verify)
app.command(name="doctor", help="Check config and API reachability.")(canonical.doctor)
app.add_typer(canonical.database_app, name="database", help="[bold]Database[/bold] management.")

# ── Graph (BYOG) ─────────────────────────────────────────────────────────────
# HydraDB's graph database offering: full Cypher over graph collections you own
# end to end. A separate store from the memory/knowledge corpora above — nothing
# crosses between them.
app.add_typer(graph.app, name="graph", help="[bold]Graph[/bold] (Cypher) queries and collections.")

# ── Connectors ───────────────────────────────────────────────────────────────
# Managed integrations that sync external sources (Slack, GitHub, Notion, Jira,
# Google Drive, …) into a database. Lifecycle: create -> discover -> configure
# -> sync; once synced, the data is reachable through the ordinary `query`.
app.add_typer(connectors.app, name="connectors", help="[bold]Connectors[/bold] — sync external sources.")

# ── Auth + config ────────────────────────────────────────────────────────────
app.command(name="login", help="Authenticate with HydraDB and save credentials.")(auth.login)
app.command(name="logout", help="Remove stored credentials.")(auth.logout)
app.command(name="whoami", help="[dim](deprecated)[/dim] Show auth status — use 'hydradb doctor'.")(auth.whoami)
app.add_typer(config_cmd.app, name="config", help="CLI [bold]configuration[/bold].")

# ── Deprecated command aliases (kept working; each emits a stderr warning) ────
app.add_typer(tenant.app, name="tenant", help="[dim](deprecated)[/dim] use 'hydradb database'.")
app.add_typer(memories.app, name="memories", help="[dim](deprecated)[/dim] use 'hydradb ingest/list/delete'.")
app.add_typer(knowledge.app, name="knowledge", help="[dim](deprecated)[/dim] use 'hydradb ingest/verify/delete'.")
app.add_typer(recall.app, name="recall", help="[dim](deprecated)[/dim] use 'hydradb query'.")
app.add_typer(fetch.app, name="fetch", help="[dim](deprecated)[/dim] use 'hydradb inspect/list/relations'.")


if __name__ == "__main__":
    app()
