"""Knowledge ingestion commands: upload, upload-text, verify, delete."""

import httpx
import typer
from rich.panel import Panel
from rich.table import Table

from hydradb_cli.client import HydraDBClientError
from hydradb_cli.output import print_error, print_result, spinner
from hydradb_cli.utils.common import (
    get_client,
    handle_api_error,
    handle_network_error,
    require_tenant_id,
    resolve_sub_tenant_id,
)

app = typer.Typer(help="Knowledge base ingestion and management.")

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
    "not found \u2014 source ID does not exist": "red",
}


def _human_status(raw: str, error_code: str | None = None) -> str:
    """Map raw API status + error_code to a clear label."""
    label = _STATUS_LABELS.get(raw.lower(), raw)
    if label == "errored" and error_code:
        if error_code == "FILE_NOT_FOUND":
            return "not found \u2014 source ID does not exist"
        return f"errored ({error_code})"
    return label


def _status_style(label: str) -> str:
    """Return a Rich style string for a given status label."""
    for key, style in _STATUS_STYLES.items():
        if key in label:
            return style
    return "white"


@app.command()
def upload(
    files: list[str] = typer.Argument(help="One or more file paths to upload (PDF, DOCX, TXT, etc.)."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
    upsert: bool = typer.Option(
        False,
        "--upsert",
        help="Update existing sources with the same ID.",
    ),
) -> None:
    """Upload files to the knowledge base.

    Supports PDF, DOCX, TXT, and other document formats. Files are processed
    asynchronously \u2014 use 'hydradb knowledge verify' to check processing status.

    Examples:

        hydradb knowledge upload ./report.pdf --tenant-id my-tenant

        hydradb knowledge upload doc1.pdf doc2.pdf --tenant-id my-tenant --upsert
    """
    if not files:
        print_error("At least one file path is required.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner(f"Uploading {len(files)} file(s)..."):
            result = client.upload_knowledge(
                tenant_id=tid,
                file_paths=files,
                sub_tenant_id=stid,
                upsert=upsert,
            )

        def fmt(r: dict):
            table = Table(
                show_header=True,
                header_style="bold cyan",
                border_style="dim",
                pad_edge=True,
                expand=False,
            )
            table.add_column("Source ID")
            table.add_column("Status")

            results = r.get("results", [])
            for item in results:
                sid = item.get("source_id", item.get("id", "unknown"))
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
                subtitle="[dim]Run 'hydradb knowledge verify' to check processing status[/dim]",
                border_style="green",
                padding=(0, 1),
            )

        print_result(result, fmt)
    except FileNotFoundError as e:
        print_error(str(e))
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)


@app.command("upload-text")
def upload_text(
    text: str | None = typer.Option(
        None,
        "--text",
        "-t",
        help="Text content to upload as a knowledge source.",
    ),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
    title: str | None = typer.Option(None, "--title", help="Title for the knowledge source."),
    source_id: str | None = typer.Option(None, "--source-id", help="Custom source ID."),
    upsert: bool = typer.Option(
        False,
        "--upsert",
        help="Overwrite an existing source with the same ID.",
    ),
) -> None:
    """Upload text content to the knowledge base.

    Use this for inline text, meeting notes, or any text content that should
    be searchable via recall.

    Examples:

        hydradb knowledge upload-text --text "Q4 pricing: Starter $29, Pro $79" --tenant-id my-tenant

        hydradb knowledge upload-text --text "..." --title "Meeting Notes" --tenant-id my-tenant
    """
    if not text or not text.strip():
        print_error("--text is required and cannot be empty.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner("Uploading text..."):
            result = client.upload_text(
                tenant_id=tid,
                text=text,
                sub_tenant_id=stid,
                title=title,
                source_id=source_id,
                upsert=upsert,
            )

        def fmt(r: dict):
            preview = text[:80] + "..." if len(text) > 80 else text
            lines = [
                f"[green]\u2713[/green] Knowledge source uploaded to tenant [bold]{tid}[/bold]",
                f'[dim]"{preview}"[/dim]',
            ]
            results = r.get("results", [])
            for item in results:
                sid = item.get("source_id", item.get("id", "unknown"))
                lines.append(f"[cyan]Source ID:[/cyan] {sid}")
            return Panel("\n".join(lines), border_style="green", padding=(0, 1))

        print_result(result, fmt)
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)


@app.command()
def verify(
    file_ids: list[str] = typer.Argument(help="One or more file/source IDs to check processing status."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
) -> None:
    """Check processing status of uploaded knowledge.

    After uploading files, use this command to verify they have been fully
    indexed and are ready for recall.

    Examples:

        hydradb knowledge verify source_abc123 --tenant-id my-tenant
    """
    if not file_ids:
        print_error("At least one file/source ID is required.")

    clean_ids = [fid.strip() for fid in file_ids if fid.strip()]
    if not clean_ids:
        print_error("File IDs cannot be empty.")

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner("Verifying processing status..."):
            result = client.verify_processing(
                tenant_id=tid,
                file_ids=clean_ids,
                sub_tenant_id=stid,
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
                    fid = item.get("file_id", item.get("id", "unknown"))
                    raw_status = item.get("indexing_status", item.get("status", "unknown"))
                    error_code = item.get("error_code")
                    label = _human_status(raw_status, error_code)
                    style = _status_style(label)
                    table.add_row(fid, f"[{style}]{label}[/{style}]")
            elif isinstance(r, dict):
                for key, val in r.items():
                    table.add_row(key, str(val))
            return table

        print_result(result, fmt)
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)


@app.command()
def delete(
    ids: list[str] = typer.Argument(help="One or more source IDs to delete from the knowledge base."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help="Tenant ID. Uses default if not specified."),
    sub_tenant_id: str | None = typer.Option(None, "--sub-tenant-id", help="Sub-tenant ID."),
    confirm: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt.",
    ),
) -> None:
    """Delete knowledge sources by their IDs.

    This removes the sources and their indexed content from the knowledge base.
    This action is irreversible.

    Examples:

        hydradb knowledge delete HydraDoc1234 HydraDoc5678 --tenant-id my-tenant -y
    """
    if not ids:
        print_error("At least one source ID is required.")

    clean_ids = [i.strip() for i in ids if i.strip()]
    if not clean_ids:
        print_error("Source IDs cannot be empty.")

    if not confirm:
        typer.confirm(
            f"Delete {len(clean_ids)} knowledge source(s)? This action is irreversible.",
            abort=True,
        )

    tid = require_tenant_id(tenant_id)
    stid = resolve_sub_tenant_id(sub_tenant_id)
    client = get_client()

    try:
        with spinner("Deleting knowledge sources..."):
            result = client.delete_knowledge(
                tenant_id=tid,
                ids=clean_ids,
                sub_tenant_id=stid,
            )
        print_result(
            result,
            lambda r: (
                f"[green]\u2713[/green] Deleted {len(clean_ids)} knowledge source(s) from tenant [bold]{tid}[/bold]."
            ),
        )
    except HydraDBClientError as e:
        handle_api_error(e)
    except httpx.RequestError as e:
        handle_network_error(e)
