"""Configuration management for HydraDB CLI.

Handles API key storage, database/collection defaults, and base URL.
Config is stored in ``~/.hydradb/config.json``. Environment variables override
file-based config.

Environment variables use the canonical ``HYDRADB_`` prefix (CONTRACT §1). The
older ``HYDRA_DB_*`` / ``HYDRA_OPENCLAW_*`` names are still honoured as
deprecated aliases, each emitting exactly one stderr warning per process naming
its canonical replacement. The canonical name wins when both are set.
"""

import json
import os
import sys
from pathlib import Path

# Canonical environment variable names (CONTRACT §1).
ENV_API_KEY = "HYDRADB_API_KEY"
ENV_DATABASE = "HYDRADB_DATABASE"
ENV_COLLECTION = "HYDRADB_COLLECTION"
ENV_BASE_URL = "HYDRADB_BASE_URL"
# Graph (BYOG) scope. A graph collection is a different namespace from a
# context collection, so it is configured separately and never falls back to
# HYDRADB_COLLECTION — Cypher aimed at the wrong one reads an empty graph
# rather than failing, which is the hardest kind of mistake to notice.
ENV_GRAPH_COLLECTION = "HYDRADB_GRAPH_COLLECTION"
DEFAULT_GRAPH_COLLECTION = "default"

# Back-compat aliases for the historical constant names used elsewhere/in tests.
ENV_TENANT_ID = ENV_DATABASE
ENV_SUB_TENANT_ID = ENV_COLLECTION

# Deprecated env aliases still read (each warns once, naming the canonical name).
# A client aliases ONLY the legacy prefix it historically used — for the CLI that
# is `HYDRA_DB_*`. Other clients' prefixes (e.g. `HYDRA_OPENCLAW_*`, the plugin's
# bare `HYDRADB_TENANT_ID`) are intentionally NOT read here (CONTRACT §1).
_DEPRECATED_ENV_ALIASES: dict[str, list[str]] = {
    ENV_API_KEY: ["HYDRA_DB_API_KEY"],
    ENV_DATABASE: ["HYDRA_DB_TENANT_ID"],
    ENV_COLLECTION: ["HYDRA_DB_SUB_TENANT_ID"],
    # `HYDRADB_API_URL` is the spelling the CLI's own docs page shipped, so it is one of
    # this client's historical names under the §1 per-client scoping rule.
    ENV_BASE_URL: ["HYDRA_DB_BASE_URL", "HYDRADB_API_URL"],
}

DEFAULT_BASE_URL = "https://api.hydradb.com"
CONFIG_DIR = Path.home() / ".hydradb"
CONFIG_FILE = CONFIG_DIR / "config.json"

_warned_env_aliases: set[str] = set()


def _warn_env_alias(alias: str, canonical: str) -> None:
    if alias in _warned_env_aliases:
        return
    _warned_env_aliases.add(alias)
    print(  # noqa: T201 - deliberate one-line stderr deprecation notice
        f"warning: environment variable {alias} is deprecated; use {canonical} instead.",
        file=sys.stderr,
    )


def _env(canonical: str) -> str | None:
    """Resolve an env var by its canonical name, falling back to deprecated
    aliases (with a one-time warning). Canonical wins if both are set."""
    value = os.environ.get(canonical)
    if value:
        return value
    for alias in _DEPRECATED_ENV_ALIASES.get(canonical, []):
        alias_value = os.environ.get(alias)
        if alias_value:
            _warn_env_alias(alias, canonical)
            return alias_value
    return None


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _read_config_file() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_config_file(data: dict) -> None:
    _ensure_config_dir()
    CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n")
    # Restrict permissions on config file (contains API key)
    CONFIG_FILE.chmod(0o600)


def get_api_key() -> str | None:
    """Get API key from env var or config file."""
    return _env(ENV_API_KEY) or _read_config_file().get("api_key")


def get_database() -> str | None:
    """Get default database (canonical name for the tenant scope).

    Reads the canonical config-file key ``database`` first, then the deprecated
    ``tenant_id`` key for back-compat with existing config files.
    """
    file_cfg = _read_config_file()
    return _env(ENV_DATABASE) or file_cfg.get("database") or file_cfg.get("tenant_id")


def get_collection() -> str | None:
    """Get default collection (canonical name for the sub-tenant scope)."""
    file_cfg = _read_config_file()
    return _env(ENV_COLLECTION) or file_cfg.get("collection") or file_cfg.get("sub_tenant_id")


def get_graph_collection() -> str:
    """Default graph (BYOG) collection.

    Falls back to the literal "default" rather than to ``get_collection()``:
    the context collection names a memory/knowledge partition and means
    nothing to a graph, so inheriting it would silently point Cypher at a
    collection the user never chose.
    """
    file_cfg = _read_config_file()
    return _env(ENV_GRAPH_COLLECTION) or file_cfg.get("graph_collection") or DEFAULT_GRAPH_COLLECTION


# Historical names kept as thin aliases so existing call sites keep working.
def get_tenant_id() -> str | None:
    """Deprecated internal alias for :func:`get_database`."""
    return get_database()


def get_sub_tenant_id() -> str | None:
    """Deprecated internal alias for :func:`get_collection`."""
    return get_collection()


def get_base_url() -> str:
    """Get API base URL from env var or config file."""
    env_val = _env(ENV_BASE_URL)
    if env_val:
        return env_val.rstrip("/")
    return _read_config_file().get("base_url", DEFAULT_BASE_URL)


def save_config(
    api_key: str | None = None,
    tenant_id: str | None = None,
    sub_tenant_id: str | None = None,
    base_url: str | None = None,
    database: str | None = None,
    collection: str | None = None,
) -> None:
    """Save configuration values to config file.

    ``database``/``collection`` are the canonical keys; ``tenant_id``/
    ``sub_tenant_id`` are their deprecated spellings. Each scope is one slot
    written under both spellings, so setting either name takes effect and a
    file written here stays readable by older CLI versions. The canonical
    argument wins when both are passed.
    """
    data = _read_config_file()
    if api_key is not None:
        data["api_key"] = api_key
    # Write both spellings of each scope. Storing only the name the caller
    # happened to use lets the other one shadow it: `get_database` reads
    # `database` before `tenant_id`, so saving `tenant_id` alone onto a config
    # that already has `database` would report success and change nothing.
    database_value = database if database is not None else tenant_id
    if database_value is not None:
        data["database"] = database_value
        data["tenant_id"] = database_value
    collection_value = collection if collection is not None else sub_tenant_id
    if collection_value is not None:
        data["collection"] = collection_value
        data["sub_tenant_id"] = collection_value
    if base_url is not None:
        data["base_url"] = base_url.rstrip("/")
    _write_config_file(data)


def clear_config() -> None:
    """Remove the config file."""
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()


def get_full_config() -> dict:
    """Return the resolved config (env vars override file values)."""
    file_cfg = _read_config_file()
    return {
        "api_key": get_api_key(),
        "tenant_id": get_database(),
        "sub_tenant_id": get_collection(),
        "base_url": get_base_url(),
        "config_file": str(CONFIG_FILE),
        "api_key_source": "env" if _env(ENV_API_KEY) else ("file" if file_cfg.get("api_key") else "none"),
        "tenant_id_source": "env"
        if _env(ENV_DATABASE)
        else ("file" if (file_cfg.get("database") or file_cfg.get("tenant_id")) else "none"),
    }
