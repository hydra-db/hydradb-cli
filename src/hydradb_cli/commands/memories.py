"""User memory commands: add, list, delete."""

import sys

import httpx
import typer
from rich.markup import escape
from rich.panel import Panel

from hydradb_cli.client import HydraDBClientError
from hydradb_cli.output import make_table, print_error, print_result, spinner
from hydradb_cli.utils.common import (
    get_client,
    handle_api_error,
    handle_network_error,
    read_stdin_safe,
    require_tenant_id,
    resolve_sub_tenant_id,
)

app = typer.Typer(help="Manage user memories.")


@app.command()
def add(
    text: str | None = typer.Option(
        None,
        "--text",
        "-t",
        help="Text content to store as a memory. Use '-' to read from stdin.",
    ),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
    infer: bool = typer.Option(
        True,
        "--infer/--no-infer",
        help="Whether HydraDB should extract insights and build knowledge graph.",
    ),
    markdown: bool = typer.Option(
        False,
        "--markdown",
        help="Treat the text as markdown content.",
    ),
    title: str | None = typer.Option(None, "--title", help="Optional title for the memory."),
    source_id: str | None = typer.Option(
        None,
        "--source-id",
        help="Source identifier to group related memories.",
    ),
    user_name: str | None = typer.Option(
        None,
        "--user-name",
        help="User name for personalization.",
    ),
    upsert: bool = typer.Option(
        True,
        "--upsert/--no-upsert",
        help="Update existing memories with the same source_id.",
    ),
) -> None:
    """Add a user memory to HydraDB.

    Memories are used by agents for learning about users. HydraDB automatically
    extracts insights, preferences, and builds a knowledge graph from the content.

    Examples:

        hydradb memories add --text "User prefers dark mode" --tenant-id my-tenant

        echo "Meeting notes..." | hydradb memories add --tenant-id my-tenant
    """
    if text == "-":
        if sys.stdin.isatty():
            typer.echo("Reading from stdin (Ctrl+D to finish)...", err=True)
            text = sys.stdin.read().strip()
        else:
            stdin_data = read_stdin_safe()
            text = stdin_data
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
        print_error("Memory text cannot be empty or whitespace-only.")

    text = text.strip()

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner("Adding memory..."):
            result = client.add_memory(
                tenant_id=tid,
                text=text,
                sub_tenant_id=stid,
                infer=infer,
                is_markdown=markdown,
                title=title,
                source_id=source_id,
                user_name=user_name,
                upsert=upsert,
            )

        def fmt(r: dict):
            success_count = r.get("success_count", 0)
            failed_count = r.get("failed_count", 0)
            preview = text[:80] + "..." if len(text) > 80 else text

            status = "green" if failed_count == 0 else "yellow"
            mark = "\u2713" if failed_count == 0 else "!"
            header = f"[{status}]{mark}[/{status}] Memory added ({success_count} success, {failed_count} failed)"

            lines = [header, f'[dim]"{escape(preview)}"[/dim]']
            results = r.get("results", [])
            for item in results:
                sid = item.get("source_id", "unknown")
                item_status = item.get("status", "unknown")
                error = item.get("error")
                lines.append(f"[cyan]Source ID:[/cyan] {sid} [dim]({item_status})[/dim]")
                if error:
                    lines.append(f"[red]Error:[/red] {error}")
            return Panel("\n".join(lines), border_style=status, padding=(0, 1))

        print_result(result, fmt)
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)


@app.command("list")
def list_memories(
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
) -> None:
    """List all user memories for a tenant.

    Examples:

        hydradb memories list --tenant-id my-tenant
    """
    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner("Fetching memories..."):
            result = client.list_memories(tenant_id=tid, sub_tenant_id=stid)

        def fmt(r: dict):
            memories = r.get("user_memories", [])
            if not memories:
                return "[dim]No memories found.[/dim]"
            rows = []
            for i, mem in enumerate(memories, 1):
                mid = mem.get("memory_id", "unknown")
                content = mem.get("memory_content", "")
                preview = content[:100] + "..." if len(content) > 100 else content
                rows.append([str(i), mid, escape(preview)])
            return make_table(
                "#",
                "Memory ID",
                "Content",
                rows=rows,
                title=f"Found {len(memories)} memories",
            )

        print_result(result, fmt)
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)


@app.command()
def delete(
    memory_id: str = typer.Argument(help="ID of the memory to delete."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
    confirm: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt.",
    ),
) -> None:
    """Delete a specific user memory by its ID.

    Use 'hydradb memories list' to find memory IDs.

    Examples:

        hydradb memories delete mem_abc123 --tenant-id my-tenant
    """
    if not memory_id.strip():
        print_error("Memory ID cannot be empty.")

    if not confirm:
        typer.confirm(
            f"Delete memory '{memory_id}'? This action is irreversible.",
            abort=True,
        )

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner("Deleting memory..."):
            result = client.delete_memory(
                tenant_id=tid,
                memory_id=memory_id,
                sub_tenant_id=stid,
            )

        def fmt(r: dict) -> str:
            deleted = r.get("user_memory_deleted")
            success = r.get("success")
            if deleted and success:
                return f"[green]\u2713[/green] Memory [bold]{memory_id}[/bold] deleted."
            elif success and not deleted:
                return f"[yellow]![/yellow] Memory [bold]{memory_id}[/bold] was not found or already deleted."
            else:
                return f"[red]\u2717[/red] Could not confirm deletion of memory [bold]{memory_id}[/bold]."

        print_result(result, fmt)
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)
