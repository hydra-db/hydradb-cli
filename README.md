# HydraDB CLI

Command-line interface for [HydraDB](https://hydradb.com) — manage memories, recall knowledge, and run ingestion directly from the terminal.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Authentication](#authentication)
- [Configuration](#configuration)
- [Commands](#commands)
  - [Global Options](#global-options)
  - [Scope options](#scope-options)
  - [query](#query)
  - [ingest](#ingest)
  - [list / inspect / relations / verify](#list--inspect--relations--verify)
  - [delete](#delete)
  - [database](#database)
  - [doctor](#doctor)
  - [Deprecated aliases](#deprecated-aliases)
  - [login / logout](#login--logout)
  - [config](#config)
- [Environment Variables](#environment-variables)
- [Running Tests](#running-tests)
- [License](#license)

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| **Python**  | 3.10 or higher |
| **pip**     | Latest recommended (`python -m pip install --upgrade pip`) |
| **HydraDB API Key** | Obtain from your HydraDB dashboard |
| **Database** | Required for most API operations |
| **Network access** | Must be able to reach `https://api.hydradb.com` (or your custom base URL) |

The CLI installs four runtime dependencies automatically: `typer`, `httpx`, `rich`, and
`hydradb-sdk`.

---

## Installation

### Using curl

```bash
curl -fsSL https://cli.hydradb.com/install | bash
```

This downloads the wheel for the latest [GitHub release](https://github.com/usecortex/hydradb-cli/releases) and installs it. The installer uses `pipx` when available, because it keeps CLI tools isolated. If `pipx` is not installed, it falls back to `pip install --user`. Runtime dependencies still resolve from PyPI as usual.

Install a specific version:

```bash
HYDRADB_CLI_VERSION=0.1.1 curl -fsSL https://cli.hydradb.com/install | bash
```

Force reinstall:

```bash
HYDRADB_CLI_FORCE=1 curl -fsSL https://cli.hydradb.com/install | bash
```

### From a GitHub release

```bash
pip install https://github.com/usecortex/hydradb-cli/releases/download/v0.1.1/hydradb_cli-0.1.1-py3-none-any.whl
```

> **Note:** PyPI releases are paused. `pip install hydradb-cli` still resolves the older `0.1.0`, so use
> the curl installer or the release wheel above to get the current version.

### From source

```bash
git clone https://github.com/usecortex/hydradb-cli.git
cd hydradb-cli
pip install .
```

For development (editable install so local changes take effect immediately):

```bash
pip install -e ".[dev]"
```

### Verify the installation

```bash
hydradb --version
# hydradb-cli 0.1.1
```

If `hydradb` is not found, make sure your virtual environment is activated or that your Python scripts directory is on your `PATH`.

---

## Authentication

Before using any data commands you need to provide your API key and database. There are two ways to do this:

### Option A — `hydradb login` (persistent)

Credentials are saved to `~/.hydradb/config.json` (file permissions set to `0600`).

```bash
# Interactive — prompts for the API key
hydradb login --database YOUR_DATABASE

# Non-interactive
hydradb login --api-key YOUR_API_KEY --database YOUR_DATABASE

# Optionally set a collection and a custom API base URL
hydradb login --api-key YOUR_API_KEY --database YOUR_DATABASE \
              --collection YOUR_COLLECTION \
              --base-url https://custom.api.endpoint
```

### Option B — Environment variables (session-only)

```bash
export HYDRADB_API_KEY=your_api_key
export HYDRADB_DATABASE=your_database
```

> The CLI's older `HYDRA_DB_*` names still work as deprecated aliases and print a
> one-line warning naming the canonical `HYDRADB_*` replacement.

Environment variables take precedence over the config file when both are set.

### Verify your session

```bash
hydradb doctor
```

Displays your resolved API key (masked), database, collection and base URL, whether each value came from the config file or an environment variable, and whether the API is reachable.

### Log out

```bash
hydradb logout
```

Deletes `~/.hydradb/config.json`.

---

## Configuration

The CLI reads settings from two sources (env vars override the file):

| Source | Location |
|--------|----------|
| Config file | `~/.hydradb/config.json` |
| Environment | See [Environment Variables](#environment-variables) |

You can view or update the config file through the CLI:

```bash
# Show current configuration
hydradb config show

# Set a single value
hydradb config set <key> <value>
```

Valid keys: `api_key`, `database`, `collection`, `base_url` (the old `tenant_id` / `sub_tenant_id` keys still work as deprecated aliases).

---

## Commands

### Global Options

These flags go **before** the subcommand:

| Flag | Description |
|------|-------------|
| `--version` / `-v` | Print the CLI version and exit |
| `--output` / `-o` | Output format: `human` (default) or `json` |

Every command supports JSON output, which is useful for scripting and piping:

```bash
hydradb -o json list --kind memory
hydradb -o json query "pricing" --kind knowledge | jq '.chunks[0].chunk_content'
```

The `--output json` shape is a stable, documented contract — the wrapper unwraps
the SDK response envelope back to the same plain-dict shape, so `jq` pipelines
keep working across SDK updates.

---

### Scope options

Every command that reads or writes data accepts these. Both fall back to your saved
config, so you can omit them once `hydradb login` has run.

| Flag | Description |
|------|-------------|
| `--database` / `-d` | Database to operate on |
| `--collection` | Collection (partition) within the database |

---

### query

Retrieve knowledge or memories — the single entry point for search.

| Option | Description |
|--------|-------------|
| `--kind` | Corpus to search: `memory` or `knowledge`. Omit to search both |
| `--operator` | Keyword operator: `or`, `and`, `phrase` |
| `--max-results` / `-n` | Maximum results, 1–50 (default `10`) |
| `--mode` / `-m` | Retrieval mode: `fast` or `thinking` |
| `--alpha` | Hybrid search weight (`0.0` keyword → `1.0` semantic) |
| `--recency-bias` | Preference for newer content (`0.0`–`1.0`) |
| `--graph-context` / `--no-graph-context` | Include knowledge graph relations |
| `--context` | Additional context to guide retrieval |

```bash
hydradb query "What did the team say about pricing?"
hydradb query "contract terms" --kind knowledge --mode thinking --max-results 20
hydradb query "What does the user prefer?" --kind memory
hydradb query "pricing AND enterprise" --operator and
```

---

### ingest

Store a memory, knowledge text, or knowledge file(s). Defaults to `--kind memory`;
file arguments are always knowledge sources.

| Option | Description |
|--------|-------------|
| `--kind` | `memory` (default) or `knowledge` |
| `--text` / `-t` | Text to ingest. Use `-` to read from stdin |
| `--title` | Optional title |
| `--source-id` | Client-assigned source identifier |
| `--user-name` | User name (memory only) |
| `--infer` / `--no-infer` | Extract insights and build the knowledge graph (default on) |
| `--markdown` | Treat text as markdown (memory only) |
| `--upsert` / `--no-upsert` | Update existing items with the same `source_id` (default on) |

```bash
hydradb ingest --text "User prefers dark mode and weekly email summaries"
hydradb ingest --text "Raw meeting notes..." --no-infer --title "Meeting Notes"
hydradb ingest --kind knowledge --text "Q4 pricing: Starter $29, Pro $79" --title "Pricing"
hydradb ingest ./contract.pdf ./notes.docx
echo "piped note" | hydradb ingest
```

`--text`, `--title`, `--source-id`, `--user-name`, `--markdown` and `--no-infer` do not
apply to file ingest and are rejected rather than silently ignored.

---

### list / inspect / relations / verify

Browse and read back what you have stored.

| Command | What it does | Key options |
|---------|--------------|-------------|
| `list` | Lists ingested sources and memories | `--kind`, `--page`, `--page-size` |
| `inspect <id>` | Fetches a source's content or a presigned download URL | `--mode` (`content`, `url`, `both`) |
| `relations <id>` | Knowledge graph triplets linked to a source | `--kind`, `--limit` |
| `verify <ids...>` | Checks indexing progress of uploaded sources | — |

```bash
hydradb list
hydradb list --kind knowledge --page-size 10
hydradb inspect source_abc123
hydradb inspect source_abc123 --mode url
hydradb relations source_abc123
hydradb verify source_abc123
```

---

### delete

Removes memories or knowledge sources by ID. Defaults to `--kind knowledge`, and prompts
for confirmation unless `--yes` is passed.

```bash
hydradb delete source_abc123 --yes
hydradb delete mem_abc123 --kind memory --yes
hydradb delete source_abc123 source_def456 --yes
```

Deleting an ID that does not exist exits non-zero rather than reporting success.

---

### database

Create and manage databases.

| Command | What it does | Key options |
|---------|--------------|-------------|
| `database create <database>` | Provisions a new database | — |
| `database list` | Lists all databases for the authenticated user | — |
| `database collections [database]` | Lists collections within a database | — |
| `database stats [database]` | Row-count statistics | — |
| `database readiness [database]` | Whether the database is ready for ingestion | — |
| `database monitor [database]` | Merged stats + readiness | — |
| `database delete <database>` | Permanently deletes a database. Asks for confirmation | `--yes` / `-y` |

```bash
hydradb database create my-new-database
hydradb database readiness
hydradb database collections
hydradb database delete old-database --yes
```

---

### doctor

Reports your resolved configuration and whether the API is reachable.

```bash
hydradb doctor
hydradb -o json doctor
```

---

### Deprecated aliases

Each of these still works and prints a one-line stderr warning naming its replacement.
They will be removed in a future major version.

| Deprecated | Use instead |
|------------|-------------|
| `memories add`, `knowledge upload`, `knowledge upload-text` | `ingest` |
| `memories list`, `fetch sources` | `list` |
| `memories delete`, `knowledge delete` | `delete` |
| `knowledge verify` | `verify` |
| `recall full`, `recall preferences`, `recall keyword` | `query` |
| `fetch content` | `inspect` |
| `fetch relations` | `relations` |
| `tenant …` | `database …` |
| `whoami` | `doctor` |
| `--tenant-id`, `--sub-tenant-id` | `--database`, `--collection` |

---

### login / logout

Manage your CLI session credentials.

| Command | What it does |
|---------|--------------|
| `hydradb login` | Saves your API key and default scope to `~/.hydradb/config.json`. Prompts for the key interactively if `--api-key` is omitted in a TTY session. Validates the key against the API when a database is provided. |
| `hydradb logout` | Removes the stored config file. |

See [`doctor`](#doctor) to inspect the resolved session.

---


### config

View and update CLI configuration values without editing the config file manually.

| Command | What it does |
|---------|--------------|
| `config show` | Displays all current settings (API key is masked). |
| `config set <key> <value>` | Sets a single configuration value. Valid keys: `api_key`, `database`, `collection`, `base_url` (the old `tenant_id` / `sub_tenant_id` keys still work as deprecated aliases). |

```bash
hydradb config show
hydradb config set database my-database
hydradb config set base_url https://api.hydradb.com
```

---

## Environment Variables

| Variable | Purpose | Deprecated alias (still read, warns once) |
|----------|---------|-------------------------------------------|
| `HYDRADB_API_KEY` | API key (overrides config file) | `HYDRA_DB_API_KEY` |
| `HYDRADB_DATABASE` | Default database (overrides config file) | `HYDRA_DB_TENANT_ID` |
| `HYDRADB_COLLECTION` | Default collection (overrides config file) | `HYDRA_DB_SUB_TENANT_ID` |
| `HYDRADB_BASE_URL` | API base URL (default `https://api.hydradb.com`) | `HYDRA_DB_BASE_URL`, `HYDRADB_API_URL` |
| `HYDRADB_OUTPUT` | Default output format — `human` or `json` | — |

The canonical `HYDRADB_*` name wins when both it and its deprecated alias are
set. The CLI aliases only its own historical `HYDRA_DB_*` prefix — it does not
read other clients' env prefixes.

---

## Running Tests

Tests use `pytest` and live in the `tests/` directory.

```bash
pip install -e .          # editable install so tests can import the package
pytest                    # runs all tests
```

---

## Documentation

Full documentation is available at [docs.hydradb.com/plugins/cli](https://docs.hydradb.com/plugins/cli).

---

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.
