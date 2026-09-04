"""Deprecated ``memories`` command group — aliases for ``hydradb ingest/list/delete``.

Each command warns once and delegates to the canonical implementation with
``kind=memory``.
"""

import typer

from hydradb_cli.commands import _impl
from hydradb_cli.commands.canonical import _resolve_text_input
from hydradb_cli.output import warn_deprecated

app = typer.Typer(help="[dim](deprecated)[/dim] Memory operations — use 'hydradb ingest/list/delete'.")


@app.command()
def add(
    text: str | None = typer.Option(None, "--text", "-t", help="Text to store. Use '-' to read from stdin."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Database. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Collection."),
    infer: bool = typer.Option(True, "--infer/--no-infer", help="Extract insights and build knowledge graph."),
    markdown: bool = typer.Option(False, "--markdown", help="Treat the text as markdown content."),
    title: str | None = typer.Option(None, "--title", help="Optional title for the memory."),
    source_id: str | None = typer.Option(None, "--source-id", help="Source identifier to group related memories."),
    user_name: str | None = typer.Option(None, "--user-name", help="User name for personalization."),
    upsert: bool = typer.Option(True, "--upsert/--no-upsert", help="Update existing memories with the same source_id."),
) -> None:
    """[dim](deprecated)[/dim] Add a memory — use 'hydradb ingest --kind memory'."""
    warn_deprecated("memories add", "ingest --kind memory")
    _impl.do_ingest_memory(
        _resolve_text_input(text),
        title=title,
        source_id=source_id,
        user_name=user_name,
        infer=infer,
        markdown=markdown,
        upsert=upsert,
        tenant_id=tenant_id,
        sub_tenant_id=sub_tenant_id,
    )


@app.command("list")
def list_memories(
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Database. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Collection."),
    acl: list[str] | None = typer.Option(
        None, "--acl", help="Principals to answer as (permission-aware search). Repeatable."
    ),
) -> None:
    """[dim](deprecated)[/dim] List memories — use 'hydradb list --kind memory'."""
    warn_deprecated("memories list", "list --kind memory")
    _impl.do_list(
        kind="memory",
        acl=list(acl) if acl else None,
        tenant_id=tenant_id,
        sub_tenant_id=sub_tenant_id,
        spinner_msg="Fetching memories...",
    )


@app.command()
def delete(
    memory_id: str = typer.Argument(help="ID of the memory to delete."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Database. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Collection."),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """[dim](deprecated)[/dim] Delete a memory — use 'hydradb delete --kind memory'."""
    warn_deprecated("memories delete", "delete --kind memory")
    if not confirm:
        typer.confirm(f"Delete memory '{memory_id}'? This action is irreversible.", abort=True)
    _impl.do_delete([memory_id], kind="memory", tenant_id=tenant_id, sub_tenant_id=sub_tenant_id)
