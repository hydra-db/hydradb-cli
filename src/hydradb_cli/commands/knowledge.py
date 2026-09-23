"""Deprecated ``knowledge`` command group — aliases for ``hydradb ingest/verify/delete``.

Each command warns once and delegates to the canonical implementation with
``kind=knowledge``.
"""

import typer

from hydradb_cli.commands import _impl
from hydradb_cli.output import print_error, warn_deprecated

app = typer.Typer(help="[dim](deprecated)[/dim] Knowledge ingestion — use 'hydradb ingest/verify/delete'.")


@app.command()
def upload(
    files: list[str] = typer.Argument(help="One or more file paths to upload."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Database. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Collection."),
    upsert: bool = typer.Option(False, "--upsert", help="Update existing sources with the same ID."),
) -> None:
    """[dim](deprecated)[/dim] Upload files — use 'hydradb ingest <files>'."""
    warn_deprecated("knowledge upload", "ingest <files>")
    _impl.do_ingest_knowledge_files(files, upsert=upsert, tenant_id=tenant_id, sub_tenant_id=sub_tenant_id)


@app.command("upload-text")
def upload_text(
    text: str | None = typer.Option(None, "--text", "-t", help="Text content to upload as a knowledge source."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Database. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Collection."),
    title: str | None = typer.Option(None, "--title", help="Title for the knowledge source."),
    source_id: str | None = typer.Option(None, "--source-id", help="Custom source ID."),
) -> None:
    """[dim](deprecated)[/dim] Upload text — use 'hydradb ingest --kind knowledge --text'."""
    warn_deprecated("knowledge upload-text", "ingest --kind knowledge --text")
    if not text or not text.strip():
        print_error("--text is required and cannot be empty.")
    _impl.do_ingest_knowledge_text(
        text, title=title, source_id=source_id, tenant_id=tenant_id, sub_tenant_id=sub_tenant_id
    )


@app.command()
def verify(
    file_ids: list[str] = typer.Argument(help="One or more file/source IDs to check processing status."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Database. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Collection."),
) -> None:
    """[dim](deprecated)[/dim] Check processing status — use 'hydradb verify'."""
    warn_deprecated("knowledge verify", "verify")
    _impl.do_ingestion_status(file_ids, tenant_id=tenant_id, sub_tenant_id=sub_tenant_id)


@app.command()
def delete(
    ids: list[str] = typer.Argument(help="One or more source IDs to delete from the knowledge base."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Database. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Collection."),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """[dim](deprecated)[/dim] Delete knowledge sources — use 'hydradb delete --kind knowledge'."""
    warn_deprecated("knowledge delete", "delete --kind knowledge")
    clean_ids = [i.strip() for i in ids if i.strip()]
    if not clean_ids:
        print_error("At least one source ID is required.")
    if not confirm:
        typer.confirm(f"Delete {len(clean_ids)} knowledge source(s)? This action is irreversible.", abort=True)
    _impl.do_delete(clean_ids, kind="knowledge", tenant_id=tenant_id, sub_tenant_id=sub_tenant_id, kind_implied=True)
