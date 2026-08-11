"""Common utilities shared across CLI commands."""

import sys

import httpx

from hydradb_cli.config import get_api_key, get_base_url, get_collection, get_database
from hydradb_cli.hydra import HydraDB, HydraDBClientError
from hydradb_cli.output import print_error, warn_deprecated


def mask_api_key(key: str) -> str:
    """Mask an API key for display, showing only prefix and suffix."""
    if len(key) > 12:
        return f"{key[:8]}...{key[-4:]}"
    return "***"


def require_api_key() -> str:
    """Get the API key or exit with a helpful error."""
    key = get_api_key()
    if not key:
        print_error("No API key configured. Run 'hydradb login' or set HYDRADB_API_KEY environment variable.")
    return key  # type: ignore[return-value]


def require_tenant_id(tenant_id: str | None = None) -> str:
    """Get the database (tenant) scope from argument, config, or exit with error."""
    tid = tenant_id or get_database()
    if not tid or not tid.strip():
        print_error("No database specified. Use --database or run 'hydradb config set database <id>'.")
    return tid  # type: ignore[return-value]


def resolve_sub_tenant_id(sub_tenant_id: str | None = None) -> str | None:
    """Get the collection (sub-tenant) scope from argument or config (may be None)."""
    return sub_tenant_id or get_collection()


def resolve_scope_flags(
    database: str | None = None,
    collection: str | None = None,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
) -> tuple[str | None, str | None]:
    """Collapse the canonical and deprecated scope flags into one pair (CONTRACT §1).

    ``--database``/``--collection`` are canonical; ``--tenant-id``/``--sub-tenant-id``
    remain as hidden aliases that warn once each. The canonical spelling wins when both
    are given. Every command routes its scope flags through here so the warning is
    emitted in exactly one place.
    """
    if tenant_id and not database:
        warn_deprecated("--tenant-id", "--database")
    if sub_tenant_id and not collection:
        warn_deprecated("--sub-tenant-id", "--collection")
    return database or tenant_id, collection or sub_tenant_id


def build_wrapper(
    api_key: str,
    base_url: str | None = None,
    database: str | None = None,
    collection: str | None = None,
) -> HydraDB:
    """Construct the SDK wrapper from explicit values (used by ``login``)."""
    return HydraDB(
        token=api_key,
        base_url=base_url or get_base_url(),
        database=database,
        collection=collection,
    )


def get_wrapper() -> HydraDB:
    """Create an authenticated HydraDB wrapper or exit with error.

    Default database/collection scope is pulled from config so commands can omit
    ``--tenant-id``/``--sub-tenant-id`` when a default is configured.
    """
    api_key = require_api_key()
    return HydraDB(
        token=api_key,
        base_url=get_base_url(),
        database=get_database(),
        collection=get_collection(),
    )


def _extract_error_message(detail: str) -> str:
    """Pull a human-readable message out of structured or raw error details."""
    import ast

    try:
        parsed = ast.literal_eval(detail)
        if isinstance(parsed, dict):
            return parsed.get("message") or parsed.get("detail") or str(parsed)
        return str(parsed)
    except Exception:
        return detail


def handle_api_error(e: HydraDBClientError) -> None:
    """Format and print an API error, then exit."""
    if e.status_code == 0:
        print_error(f"Connection error: {e.detail}")
    elif e.status_code == 401:
        print_error("Authentication failed. Check your API key or run 'hydradb login'.")
    elif e.status_code == 403:
        print_error("Access denied. Your API key may not have permission for this operation.")
    elif e.status_code == 404:
        msg = _extract_error_message(e.detail)
        print_error(f"Not found: {msg}")
    elif e.status_code == 422:
        msg = _extract_error_message(e.detail)
        print_error(f"Invalid request: {msg}")
    elif e.status_code == 429:
        print_error("Rate limited. Please wait and try again.")
    elif e.status_code == 500:
        msg = _extract_error_message(e.detail)
        if "tenant collection statistics" in msg.lower():
            print_error(
                "Could not retrieve database stats. The database may not exist or the backend is temporarily "
                "unavailable."
            )
        elif "memory service" in msg.lower():
            print_error("Memory service is temporarily unavailable. Please try again.")
        else:
            print_error(f"Server error: {msg}")
    else:
        msg = _extract_error_message(e.detail)
        print_error(f"API error (HTTP {e.status_code}): {msg}")


def handle_network_error(e: httpx.RequestError) -> None:
    """Format and print a network-level error, then exit."""
    print_error(f"Network error: Unable to reach the HydraDB API. Check your connection and base URL. ({e})")


def validate_range(value: float, name: str, low: float, high: float) -> None:
    """Validate a numeric value is within [low, high], or exit with error."""
    if value < low or value > high:
        print_error(f"--{name} must be between {low} and {high}, got {value}")


def require_non_empty(value: str | None, name: str) -> str:
    """Validate a string is non-empty/non-whitespace, or exit with error."""
    if not value or not value.strip():
        print_error(f"{name} cannot be empty.")
    return value.strip()  # type: ignore[union-attr]


def read_stdin_safe() -> str | None:
    """Read piped stdin to completion, or None when stdin is a terminal.

    Returns the stripped content, or None when stdin is an interactive terminal
    or carries nothing.

    A non-tty stdin is always a redirect — a pipe, a file, or a closed handle —
    so it is guaranteed to reach EOF and is read in full. Polling for readiness
    first would silently discard the user's input instead: ``select`` rejects
    non-socket handles on Windows, and any short timeout races a producer that
    takes longer than that to write (``curl … | hydradb ingest``).
    """
    stream = sys.stdin
    if stream is None:
        return None
    try:
        if stream.isatty():
            return None
        data = stream.read()
    except (OSError, ValueError):
        return None
    return data.strip() or None
