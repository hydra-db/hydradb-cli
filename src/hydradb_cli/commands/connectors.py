"""Connector commands — managed integrations that sync external sources.

``hydradb connectors providers|list|get|create|discover|configure|resources|
sync|status|rotate-credentials|delete``.

The documented lifecycle is **create → discover → configure → sync → poll →
query → delete**: create the connector with credentials, ask the provider what
is available, activate the subset you want, then sync. Once synced, the data is
reachable through the ordinary ``hydradb query``.

See https://docs.hydradb.com/essentials/v2/connectors

Credentials are the sensitive part of this surface and are handled accordingly:
they are never accepted as a bare argv value, and never echoed back in any
output mode. See :func:`_read_credentials`.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import typer

from hydradb_cli.hydra import HydraDBClientError
from hydradb_cli.output import (
    console,
    get_output_format,
    make_kv_table,
    make_table,
    print_error,
    print_json,
    print_success,
    spinner,
)
from hydradb_cli.utils.common import get_wrapper, handle_api_error

app = typer.Typer(
    help=(
        "[bold]Connectors[/bold] — sync external sources (Slack, GitHub, Notion, Jira, …) "
        "into a database.\n\nLifecycle: create → discover → configure → sync."
    ),
    no_args_is_help=True,
)

resource_app = typer.Typer(help="Manage individual connector [bold]resources[/bold].", no_args_is_help=True)
app.add_typer(resource_app, name="resource")

# The env var a script sets instead of piping credentials. Never a CLI flag —
# see _read_credentials.
ENV_CREDENTIALS = "HYDRADB_CONNECTOR_CREDENTIALS"

# Keys whose VALUES must never be echoed, whatever the provider calls them.
#
# Deliberately narrower than "anything containing 'key'": that also matches
# `filter_key`, which is a metadata field name a user needs in order to write a
# query filter. Redacting it would corrupt correct output in the name of
# security without protecting anything.
_SECRET_KEY_EXACT = frozenset(
    {
        "credentials",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "secret_key",
        "client_secret",
    }
)
_SECRET_KEY_SUBSTRINGS = ("token", "secret", "password", "passphrase")

# Not "[redacted]": Rich parses square brackets as markup and silently swallows
# the whole thing, so the redaction would render as an empty cell — which reads
# as "this field is absent" rather than "this field was hidden".
REDACTED = "<redacted>"


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _SECRET_KEY_EXACT or any(hint in lowered for hint in _SECRET_KEY_SUBSTRINGS)


def _execute(message: str, call):
    """Run a wrapper call under a spinner, translating errors to CLI exits."""
    try:
        with spinner(message):
            return call()
    except HydraDBClientError as e:
        handle_api_error(e)


def _redact(value: Any) -> Any:
    """Recursively replace credential VALUES with a placeholder.

    Applied to anything that could contain what the user just sent, including
    ``--output json``. The API does not echo credentials back today, but this
    surface exists to move secrets around and the cost of being wrong is a
    token in a terminal scrollback or a CI log.

    Only *scalar* values under a secret-looking key are replaced. A dict or list
    under such a key is descended into instead, because it is structure rather
    than a secret — the provider credential schema nests a field called
    ``access_token`` whose value is its own JSON Schema (``description``,
    ``format``, ``type``). Blanking that removed the one thing
    ``connectors providers <name>`` exists to show, while protecting nothing:
    the actual secret is always a leaf.
    """
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _is_secret_key(key) and not isinstance(item, (dict, list)):
                redacted[key] = REDACTED
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _emit(data: Any, human) -> None:
    """Print a result, redacted in both output modes."""
    safe = _redact(data)
    if get_output_format() == "json":
        print_json(safe)
        return
    human(safe)


def _read_credentials(from_stdin: bool, provider_schema: dict | None = None) -> dict:
    """Obtain connector credentials without ever putting them in argv.

    Three sources, in order: ``--credentials-stdin``, the
    ``HYDRADB_CONNECTOR_CREDENTIALS`` env var, then an interactive no-echo
    prompt driven by the provider's own credential schema.

    There is deliberately no ``--credentials`` flag. A secret passed as a
    command-line argument lands in shell history and is visible to any user on
    the box via ``ps`` for the lifetime of the process — neither of which the
    user can undo after the fact.
    """
    raw: str | None = None

    if from_stdin:
        raw = sys.stdin.read().strip()
        if not raw:
            print_error("--credentials-stdin was given but nothing arrived on stdin.")
    elif os.environ.get(ENV_CREDENTIALS):
        raw = os.environ[ENV_CREDENTIALS].strip()

    if raw is not None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            print_error(f"Credentials must be a JSON object: {e}")
        if not isinstance(parsed, dict):
            print_error('Credentials must be a JSON object, e.g. {"access_token": "xoxb-..."}.')
        return parsed

    # Interactive: ask for exactly the fields this provider declares, so the
    # user is not left guessing what it wants.
    fields = _required_credential_fields(provider_schema)
    if not fields:
        # No schema means there is nothing to prompt FOR — say that, rather
        # than pointing at a way to enable prompting. (This previously told the
        # user to "run without --no-input", a flag that has never existed.)
        print_error(
            "No credentials supplied, and this provider did not declare a credential "
            "schema, so there are no fields to prompt for. Pipe them with "
            f"--credentials-stdin or set {ENV_CREDENTIALS}."
        )
    if not sys.stdin.isatty():
        print_error(
            "No credentials supplied and stdin is not a terminal. Pipe them with "
            f"--credentials-stdin or set {ENV_CREDENTIALS}."
        )

    collected: dict[str, str] = {}
    for name, description in fields:
        if description:
            console.print(f"  [dim]{name}: {description}[/dim]")
        collected[name] = typer.prompt(f"  {name}", hide_input=True)
    return collected


def _required_credential_fields(schema: dict | None) -> list[tuple[str, str]]:
    """(name, description) for each field a provider's credential schema requires."""
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required = schema.get("required") or list(properties)
    return [(name, (properties.get(name) or {}).get("description", "")) for name in required if name in properties]


def _validate_credentials(credentials: dict, schema: dict | None) -> None:
    """Name a missing required field locally rather than failing at create time."""
    required = {name for name, _ in _required_credential_fields(schema)}
    missing = sorted(required - set(credentials))
    if missing:
        print_error(
            f"Missing required credential field(s): {', '.join(missing)}. "
            "Run 'hydradb connectors providers <provider>' to see what this provider needs."
        )


def _relative(timestamp: str | None) -> str:
    """Render an ISO timestamp as an age, which is what a sync question asks.

    "2026-08-17T18:30:05Z" answers "when"; "12m ago" answers "is it working",
    which is the question someone runs this command to settle.
    """
    if not timestamp:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return str(timestamp)

    # A timestamp with no offset is assumed UTC. Subtracting a naive datetime
    # from an aware one raises TypeError, which surfaced as an unhandled stack
    # trace out of `connectors list` and `connectors status` — a rendering
    # helper taking down the whole command. The API returns RFC3339 with `Z`
    # today (checked across every connector), so this is defence against a
    # shape we do not control rather than a bug anyone has hit.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    delta = (datetime.now(timezone.utc) - parsed).total_seconds()
    future = delta < 0
    seconds = abs(delta)

    if seconds < 60:
        rendered = f"{int(seconds)}s"
    elif seconds < 3600:
        rendered = f"{int(seconds // 60)}m"
    elif seconds < 86400:
        rendered = f"{int(seconds // 3600)}h"
    else:
        rendered = f"{int(seconds // 86400)}d"

    return f"in {rendered}" if future else f"{rendered} ago"


@app.command(name="providers")
def providers(
    provider_id: str | None = typer.Argument(None, metavar="[PROVIDER]", help="Show one provider in detail."),
    category: str | None = typer.Option(None, "--category", help="Filter by category, e.g. crm, messaging."),
    supported_only: bool = typer.Option(True, "--supported/--all", help="Only providers marked supported."),
) -> None:
    """List available connector providers, or show one in detail.

    With a PROVIDER argument, shows the credential schema that provider needs
    plus the fields it makes searchable and filterable.

    The catalogue is served by the API and never hardcoded here, so newly
    supported providers appear without a CLI upgrade.
    """
    wrapper = get_wrapper()

    if provider_id:
        detail = _execute("Fetching provider...", lambda: wrapper.connectors.provider(provider_id))
        if not detail:
            print_error(f"Unknown provider '{provider_id}'. Run 'hydradb connectors providers' to list them.")
        _emit(detail, lambda d: _print_provider_detail(provider_id, d))
        return

    catalogue = _execute("Fetching providers...", lambda: wrapper.connectors.providers())

    if category:
        catalogue = [p for p in catalogue if p.get("category") == category]
    if supported_only:
        catalogue = [p for p in catalogue if p.get("supported")]

    if get_output_format() == "json":
        print_json(catalogue)
        return

    if not catalogue:
        console.print("  [dim]No providers matched.[/dim]")
        return

    def _stage(p: dict) -> str:
        if p.get("is_alpha"):
            return "alpha"
        if p.get("is_beta"):
            return "beta"
        return "GA"

    rows = [
        [p.get("provider", "—"), p.get("category", "—"), _stage(p)]
        for p in sorted(catalogue, key=lambda p: (p.get("category") or "", p.get("provider") or ""))
    ]
    console.print(make_table("Provider", "Category", "Stage", rows=rows))
    console.print(f"  [dim]{len(rows)} provider(s). 'hydradb connectors providers <name>' for details.[/dim]")


def _print_provider_detail(provider_id: str, detail: dict) -> None:
    fields = _required_credential_fields(detail.get("credential_schema"))
    console.print(
        make_kv_table(
            [
                ("provider", detail.get("provider", provider_id)),
                ("indexed objects", ", ".join(detail.get("indexed_object_types") or []) or "—"),
                ("credentials", ", ".join(name for name, _ in fields) or "—"),
            ],
            title=f"Provider: {provider_id}",
        )
    )

    if fields:
        console.print(
            make_table(
                "Credential field",
                "Description",
                rows=[[name, description or "—"] for name, description in fields],
                title="Required credentials",
            )
        )

    filterable = detail.get("filterable_fields") or []
    if filterable:
        console.print(
            make_table(
                "Filter key",
                "Type",
                "Description",
                rows=[
                    [f.get("filter_key", f.get("name", "—")), f.get("data_type", "—"), f.get("description", "—")]
                    for f in filterable
                ],
                title="Filterable fields (use with query metadata filters)",
            )
        )

    searchable = detail.get("searchable_fields") or []
    if searchable:
        # Searchable fields cannot be targeted individually — they are folded
        # into one combined text index — so they are listed, not tabulated as
        # though each were a queryable handle.
        console.print(
            "  [dim]Searchable (combined text index, not individually targetable): "
            + ", ".join(f.get("name", "?") for f in searchable)
            + "[/dim]"
        )


@app.command(name="list")
def list_connectors(
    provider: str | None = typer.Option(None, "--provider", help="Only connectors for this provider."),
) -> None:
    """List connectors and their sync state."""
    wrapper = get_wrapper()
    connectors = _execute("Listing connectors...", lambda: wrapper.connectors.list(provider=provider))

    if get_output_format() == "json":
        print_json(_redact(connectors))
        return

    if not connectors:
        console.print("  [dim]No connectors yet. Create one with 'hydradb connectors create'.[/dim]")
        return

    rows = [
        [
            c.get("connector_id", "—"),
            c.get("name") or "—",
            c.get("provider", "—"),
            c.get("database", "—"),
            c.get("sync_status", "—"),
            _relative(c.get("last_successful_sync_at")),
        ]
        for c in connectors
    ]
    console.print(make_table("ID", "Name", "Provider", "Database", "Sync", "Last success", rows=rows))
    console.print(f"  [dim]{len(rows)} connector(s).[/dim]")


@app.command(name="get")
def get_connector(
    connector_id: str = typer.Argument(help="Connector ID."),
) -> None:
    """Show one connector."""
    wrapper = get_wrapper()
    connector = _execute("Fetching connector...", lambda: wrapper.connectors.get(connector_id))
    _emit(connector, lambda c: console.print(make_kv_table(_connector_rows(c), title="Connector")))


def _connector_rows(c: dict) -> list[tuple[str, str]]:
    return [
        ("connector_id", str(c.get("connector_id", "—"))),
        ("name", str(c.get("name") or "—")),
        ("provider", str(c.get("provider", "—"))),
        ("database", str(c.get("database", "—"))),
        ("collection", str(c.get("collection") or "—")),
        ("scope", str(c.get("provider_account_scope") or "—")),
        ("status", str(c.get("status", "—"))),
        ("lifecycle", str(c.get("lifecycle", "—"))),
    ]


@app.command(name="status")
def status(
    connector_id: str = typer.Argument(help="Connector ID."),
) -> None:
    """Show a connector's sync health: when it last ran, what it has dispatched."""
    wrapper = get_wrapper()
    connector = _execute("Fetching connector...", lambda: wrapper.connectors.get(connector_id))

    if get_output_format() == "json":
        print_json(_redact(connector))
        return

    interval = connector.get("sync_interval_seconds")
    console.print(
        make_kv_table(
            [
                ("sync_status", str(connector.get("sync_status", "—"))),
                ("last successful", _relative(connector.get("last_successful_sync_at"))),
                ("last attempted", _relative(connector.get("last_attempted_sync_at"))),
                ("next sync", _relative(connector.get("next_sync_at"))),
                ("interval", f"{interval}s" if interval else "—"),
                ("cycles completed", str(connector.get("sync_cycles_completed", "—"))),
                ("documents dispatched", str(connector.get("documents_dispatched", "—"))),
                ("active resources", str(connector.get("active_resource_count", "—"))),
            ],
            title=f"Sync status: {connector.get('name') or connector_id}",
        )
    )

    # A connector that has never dispatched anything is the common "why is
    # nothing showing up" case, and it usually means no resources are configured.
    if not connector.get("active_resource_count"):
        console.print(
            "  [dim]No active resources — this connector will not sync anything until you run "
            "'hydradb connectors discover' and then 'configure'.[/dim]"
        )


@app.command(name="create")
def create(
    provider: str = typer.Option(..., "--provider", help="Provider id, e.g. slack. See 'connectors providers'."),
    name: str | None = typer.Option(None, "--name", help="Human-readable label for this connector."),
    scope: str | None = typer.Option(
        None, "--scope", help="Stable external account identifier (workspace id, org, …)."
    ),
    credentials_stdin: bool = typer.Option(
        False, "--credentials-stdin", help="Read the credentials JSON object from stdin."
    ),
    sync_interval: int | None = typer.Option(None, "--sync-interval", help="Seconds between automatic syncs."),
    database: str | None = typer.Option(None, "--database", "-d", help="Target database. Uses the default if unset."),
    collection: str | None = typer.Option(None, "--collection", help="Target collection."),
) -> None:
    """Create a connector.

    Credentials are never taken as a command-line argument — they would land in
    shell history and be visible via 'ps'. Supply them by piping a JSON object
    with --credentials-stdin, by setting HYDRADB_CONNECTOR_CREDENTIALS, or
    interactively when prompted (input is hidden).

        echo '{"access_token":"xoxb-..."}' | hydradb connectors create --provider slack --credentials-stdin

    --scope distinguishes two connectors for the same provider on different
    accounts. Without it, documents from separate accounts can collide.
    """
    wrapper = get_wrapper()

    # Fetch the schema first so the prompt asks for the right fields and a
    # missing one is named locally rather than at create time.
    schema = _execute("Fetching provider...", lambda: wrapper.connectors.provider(provider))
    if not schema:
        print_error(f"Unknown provider '{provider}'. Run 'hydradb connectors providers' to list them.")

    credentials = _read_credentials(credentials_stdin, schema.get("credential_schema"))
    _validate_credentials(credentials, schema.get("credential_schema"))

    created = _execute(
        "Creating connector...",
        lambda: wrapper.connectors.create(
            provider=provider,
            name=name,
            database=database,
            collection=collection,
            provider_account_scope=scope,
            credentials=credentials,
            sync_interval_seconds=sync_interval,
        ),
    )

    connector_id = created.get("connector_id")
    if get_output_format() == "json":
        print_json(_redact(created))
        return
    print_success(f"Created connector {connector_id} ({provider}).")
    console.print(
        f"  [dim]Next: 'hydradb connectors discover {connector_id}' to see what is available, "
        "then 'configure' to activate it.[/dim]"
    )


@app.command(name="discover")
def discover(
    connector_id: str = typer.Argument(help="Connector ID."),
    limit: int | None = typer.Option(None, "--limit", help="Maximum resources to return."),
    cursor: str | None = typer.Option(None, "--cursor", help="Pagination cursor from a previous call."),
) -> None:
    """List the resources this connector's provider offers.

    Nothing is synced until these are activated with 'connectors configure'.
    """
    wrapper = get_wrapper()
    result = _execute(
        "Discovering resources...",
        lambda: wrapper.connectors.discover(connector_id, cursor=cursor, limit=limit),
    )

    if get_output_format() == "json":
        print_json(_redact(result))
        return

    items = result.get("resources") if isinstance(result, dict) else None
    if not items:
        console.print("  [dim]No resources discovered.[/dim]")
        return

    console.print(
        make_table(
            "Resource ID",
            "Type",
            "Name",
            rows=[
                [
                    # Discovery returns the identifier as `id`, while `configure`
                    # and `resource add` take it as `resource_id`. Read both, so
                    # the column always shows the value the next command wants.
                    r.get("id") or r.get("resource_id") or "—",
                    r.get("resource_type", "—"),
                    r.get("name") or r.get("display_name") or "—",
                ]
                for r in items
            ],
        )
    )
    console.print("  [dim]Activate these with 'hydradb connectors configure <id> -r <resource-id>:<type>'.[/dim]")
    next_cursor = result.get("next_cursor") or result.get("cursor")
    if next_cursor:
        console.print(f"  [dim]More available — pass --cursor {next_cursor}.[/dim]")


@app.command(name="configure")
def configure(
    connector_id: str = typer.Argument(help="Connector ID."),
    resource: list[str] | None = typer.Option(
        None, "--resource", "-r", help="Resource to activate as id[:type]. Repeatable."
    ),
    resources_json: str | None = typer.Option(
        None, "--resources-json", help="Full resources array as JSON, for the fields --resource cannot express."
    ),
    lookback_days: int | None = typer.Option(None, "--lookback-days", help="How far back to sync initially."),
) -> None:
    """Activate resources on a connector and set sync options.

    Take the ids from 'hydradb connectors discover'.

        hydradb connectors configure <id> -r C123:channel -r C456:channel --lookback-days 60
    """
    if not resource and not resources_json:
        print_error("Pass at least one --resource, or a full --resources-json array. Nothing was configured.")

    payload: list[dict[str, Any]] = []

    if resources_json:
        try:
            loaded = json.loads(resources_json)
        except json.JSONDecodeError as e:
            print_error(f"--resources-json is not valid JSON: {e}")
        if not isinstance(loaded, list):
            print_error("--resources-json must be a JSON array of resource objects.")
        payload.extend(loaded)

    for item in resource or []:
        resource_id, _, resource_type = item.partition(":")
        if not resource_id:
            print_error(f"--resource needs a resource id, got '{item}'.")
        entry: dict[str, Any] = {"resource_id": resource_id}
        if resource_type:
            entry["resource_type"] = resource_type
        payload.append(entry)

    wrapper = get_wrapper()
    result = _execute(
        "Configuring resources...",
        lambda: wrapper.connectors.configure(connector_id, resources=payload, lookback_days=lookback_days),
    )

    if get_output_format() == "json":
        print_json(_redact(result))
        return
    print_success(f"Configured {len(payload)} resource(s) on {connector_id}.")
    console.print(f"  [dim]Next: 'hydradb connectors sync {connector_id}' to run a cycle now.[/dim]")


@app.command(name="resources")
def resources(
    connector_id: str = typer.Argument(help="Connector ID."),
) -> None:
    """List the resources configured on a connector, with their sync state."""
    wrapper = get_wrapper()
    items = _execute("Listing resources...", lambda: wrapper.connectors.resources(connector_id))

    if get_output_format() == "json":
        print_json(_redact(items))
        return

    if not items:
        console.print(
            "  [dim]No resources configured — this connector will not sync anything. "
            "Run 'discover' then 'configure'.[/dim]"
        )
        return

    def _health(r: dict) -> str:
        health = (r.get("provider_metadata") or {}).get("health") or {}
        last_status = health.get("last_status")
        rows = health.get("last_row_count")
        if last_status is None:
            return "—"
        return f"{last_status} ({rows} rows)" if rows is not None else str(last_status)

    console.print(
        make_table(
            "Resource ID",
            "Type",
            "Status",
            "Last fetch",
            rows=[
                [r.get("resource_id", "—"), r.get("resource_type", "—"), r.get("status", "—"), _health(r)]
                for r in items
            ],
        )
    )


@resource_app.command(name="add")
def resource_add(
    connector_id: str = typer.Argument(help="Connector ID."),
    resource_id: str = typer.Argument(help="Resource ID, from 'connectors discover'."),
    resource_type: str | None = typer.Option(None, "--type", help="Resource type."),
    display_name: str | None = typer.Option(None, "--name", help="Display name."),
    collection: str | None = typer.Option(None, "--collection", help="Route this resource to a specific collection."),
) -> None:
    """Add one resource to a connector."""
    wrapper = get_wrapper()
    result = _execute(
        "Adding resource...",
        lambda: wrapper.connectors.add_resource(
            connector_id,
            resource_id=resource_id,
            resource_type=resource_type,
            display_name=display_name,
            collection_override=collection,
        ),
    )

    if get_output_format() == "json":
        print_json(_redact(result))
        return
    print_success(f"Added resource {resource_id} to {connector_id}.")


@resource_app.command(name="remove")
def resource_remove(
    connector_id: str = typer.Argument(help="Connector ID."),
    resource_id: str = typer.Argument(help="Resource ID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Remove one resource from a connector. It stops syncing."""
    if not yes:
        typer.confirm(f"Remove resource '{resource_id}' from connector {connector_id}?", abort=True)

    wrapper = get_wrapper()
    _execute("Removing resource...", lambda: wrapper.connectors.remove_resource(connector_id, resource_id))

    if get_output_format() == "json":
        print_json({"connector_id": connector_id, "resource_id": resource_id, "removed": True})
        return
    print_success(f"Removed resource {resource_id} from {connector_id}.")


@app.command(name="sync")
def sync(
    connector_id: str = typer.Argument(help="Connector ID."),
) -> None:
    """Trigger an on-demand sync cycle.

    Syncing is asynchronous — this queues a cycle and returns. Poll with
    'hydradb connectors status' to see it complete.
    """
    wrapper = get_wrapper()
    result = _execute("Triggering sync...", lambda: wrapper.connectors.sync(connector_id))

    if get_output_format() == "json":
        print_json(_redact(result))
        return
    print_success(f"Sync triggered for {connector_id}.")
    console.print(
        "  [dim]Syncing is asynchronous — the data is not queryable until the cycle finishes. "
        f"Check with 'hydradb connectors status {connector_id}'.[/dim]"
    )


@app.command(name="rotate-credentials")
def rotate_credentials(
    connector_id: str = typer.Argument(help="Connector ID."),
    credentials_stdin: bool = typer.Option(
        False, "--credentials-stdin", help="Read the credentials JSON object from stdin."
    ),
) -> None:
    """Replace a connector's stored credentials.

    Supplied exactly as for 'create': piped, from the environment, or via a
    hidden prompt — never as a command-line argument.
    """
    wrapper = get_wrapper()
    connector = _execute("Fetching connector...", lambda: wrapper.connectors.get(connector_id))
    provider = connector.get("provider")

    schema = {}
    if provider:
        schema = _execute("Fetching provider...", lambda: wrapper.connectors.provider(provider)) or {}

    credentials = _read_credentials(credentials_stdin, schema.get("credential_schema"))
    _validate_credentials(credentials, schema.get("credential_schema"))

    result = _execute(
        "Rotating credentials...",
        lambda: wrapper.connectors.rotate_credentials(connector_id, credentials=credentials),
    )

    if get_output_format() == "json":
        print_json(_redact(result))
        return
    print_success(f"Rotated credentials for {connector_id}.")


@app.command(name="delete")
def delete(
    connector_id: str = typer.Argument(help="Connector ID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete a connector. It stops syncing; already-ingested data remains."""
    if not yes:
        typer.confirm(f"Delete connector {connector_id}? It will stop syncing.", abort=True)

    wrapper = get_wrapper()
    _execute("Deleting connector...", lambda: wrapper.connectors.delete(connector_id))

    if get_output_format() == "json":
        print_json({"connector_id": connector_id, "deleted": True})
        return
    print_success(f"Deleted connector {connector_id}.")
    console.print(
        "  [dim]Documents it already ingested remain in the database — remove them with "
        "'hydradb delete' if you want them gone.[/dim]"
    )
