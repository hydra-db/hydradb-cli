"""Deprecated ``tenant`` command group — aliases for ``hydradb database``.

Vocabulary alignment (CONTRACT §1): "tenant" → "database", "sub-tenant" →
"collection". Each command warns once and delegates to the canonical ``database``
implementation.
"""

import typer

from hydradb_cli.commands import _impl
from hydradb_cli.output import warn_deprecated

app = typer.Typer(help="[dim](deprecated)[/dim] Tenant management — use 'hydradb database'.")


@app.command()
def create(
    tenant_id: str = typer.Argument(help="Unique tenant identifier."),
) -> None:
    """[dim](deprecated)[/dim] Create a tenant — use 'hydradb database create'."""
    warn_deprecated("tenant create", "database create")
    _impl.do_database_create(tenant_id)


@app.command()
def monitor(
    tenant_id_arg: str | None = typer.Argument(None, metavar="TENANT_ID", help="Tenant ID. Uses default if omitted."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
) -> None:
    """[dim](deprecated)[/dim] Tenant stats + status — use 'hydradb database monitor'."""
    warn_deprecated("tenant monitor", "database monitor")
    _impl.do_database_monitor(tenant_id or tenant_id_arg)


@app.command("list-sub-tenants")
def list_sub_tenants(
    tenant_id_arg: str | None = typer.Argument(None, metavar="TENANT_ID", help="Tenant ID. Uses default if omitted."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", hidden=True),
) -> None:
    """[dim](deprecated)[/dim] List collections — use 'hydradb database collections'."""
    warn_deprecated("tenant list-sub-tenants", "database collections")
    _impl.do_database_collections(tenant_id or tenant_id_arg)


@app.command()
def delete(
    tenant_id: str = typer.Argument(help="Tenant ID to delete."),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """[dim](deprecated)[/dim] Delete a tenant — use 'hydradb database delete'."""
    warn_deprecated("tenant delete", "database delete")
    if not confirm:
        typer.confirm(f"Are you sure you want to delete tenant '{tenant_id}' and ALL its data?", abort=True)
    _impl.do_database_delete(tenant_id)
