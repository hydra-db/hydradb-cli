"""Configuration commands: show, set."""

import typer
from rich.panel import Panel

from hydradb_cli.config import get_full_config, save_config
from hydradb_cli.output import (
    console,
    get_output_format,
    make_kv_table,
    print_error,
    print_json,
    print_result,
    warn_deprecated,
)
from hydradb_cli.utils.common import mask_api_key

app = typer.Typer(help="View and manage CLI configuration.")

# Canonical keys plus deprecated aliases (old vocabulary) still accepted.
CANONICAL_KEYS = {"api_key", "database", "collection", "base_url"}
_DEPRECATED_KEY_ALIASES = {"tenant_id": "database", "sub_tenant_id": "collection"}
VALID_KEYS = CANONICAL_KEYS | set(_DEPRECATED_KEY_ALIASES)


@app.command()
def show() -> None:
    """Show current CLI configuration.

    Displays all configured values and their sources (environment variable
    or config file). API keys are masked for security.

    Examples:

        hydradb config show

        hydradb config show --output json
    """
    cfg = get_full_config()

    if get_output_format() == "json":
        safe_cfg = dict(cfg)
        if safe_cfg.get("api_key"):
            key = safe_cfg["api_key"]
            safe_cfg["api_key"] = mask_api_key(key)
        print_json(safe_cfg)
        return

    pairs: list[tuple[str, str]] = []

    api_key = cfg.get("api_key")
    if api_key:
        masked = mask_api_key(api_key)
        pairs.append(("api_key", f"{masked} [dim]({cfg['api_key_source']})[/dim]"))
    else:
        pairs.append(("api_key", "[dim](not set)[/dim]"))

    # Labels are the canonical vocabulary (CONTRACT §1). `get_full_config` still
    # keys the resolved scope as `tenant_id`/`sub_tenant_id` because that is the
    # documented `--output json` shape; only the human-readable labels change.
    database = cfg.get("tenant_id")
    if database:
        pairs.append(("database", f"{database} [dim]({cfg['tenant_id_source']})[/dim]"))
    else:
        pairs.append(("database", "[dim](not set)[/dim]"))

    collection = cfg.get("sub_tenant_id")
    pairs.append(("collection", collection or "[dim](not set)[/dim]"))
    acl = cfg.get("acl")
    if acl:
        pairs.append(("acl", f"{', '.join(acl)} [dim]({cfg['acl_source']})[/dim]"))
    else:
        pairs.append(("acl", "[dim](not set)[/dim]"))
    pairs.append(("base_url", cfg["base_url"]))
    pairs.append(("config_file", cfg["config_file"]))

    table = make_kv_table(pairs)
    panel = Panel(
        table,
        title="[bold cyan]/// Configuration[/bold cyan]",
        border_style="cyan",
        padding=(0, 1),
    )
    console.print(panel)


@app.command("set")
def set_value(
    key: str = typer.Argument(help=f"Config key to set. Valid keys: {', '.join(sorted(VALID_KEYS))}"),
    value: str = typer.Argument(help="Value to set."),
) -> None:
    """Set a configuration value.

    Values are saved to ~/.hydradb/config.json. Environment variables
    always take precedence over file-based config.

    Examples:

        hydradb config set database my-database

        hydradb config set base_url https://api.hydradb.com
    """
    if key not in VALID_KEYS:
        print_error(f"Unknown config key '{key}'. Valid keys: {', '.join(sorted(VALID_KEYS))}")

    if key in _DEPRECATED_KEY_ALIASES:
        warn_deprecated(f"config key '{key}'", _DEPRECATED_KEY_ALIASES[key])

    if key == "api_key" and not value.strip():
        print_error("API key cannot be empty. Use 'hydradb logout' to remove credentials.")

    if key == "base_url" and not value.strip():
        print_error("Base URL cannot be empty.")

    save_config(**{key: value})

    display_value = value
    if key == "api_key" and len(value) > 12:
        display_value = f"{value[:8]}...{value[-4:]}"

    result = {"success": True, "key": key, "message": f"Set {key} in config."}
    print_result(
        result,
        lambda r: (
            f"[green]\u2713[/green] Set [cyan]{key}[/cyan] = [bold]{display_value}[/bold] in ~/.hydradb/config.json"
        ),
    )
