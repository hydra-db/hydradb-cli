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
  - [login / logout / whoami](#login--logout--whoami)
  - [tenant](#tenant)
  - [memories](#memories)
  - [knowledge](#knowledge)
  - [recall](#recall)
  - [fetch](#fetch)
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
| **Tenant ID** | Required for most API operations |
| **Network access** | Must be able to reach `https://api.hydradb.com` (or your custom base URL) |

The CLI installs three runtime dependencies automatically: `typer`, `httpx`, and `rich`.

---

## Installation

### Using curl

```bash
curl -fsSL https://cli.hydradb.com/install | bash
```

This installs the published `hydradb-cli` package from PyPI. The installer uses `pipx` when available, because it keeps CLI tools isolated. If `pipx` is not installed, it falls back to `pip install --user`.

Install a specific version:

```bash
HYDRADB_CLI_VERSION=0.1.0 curl -fsSL https://cli.hydradb.com/install | bash
```

Force reinstall:

```bash
HYDRADB_CLI_FORCE=1 curl -fsSL https://cli.hydradb.com/install | bash
```

### From PyPI

```bash
pip install hydradb-cli
```

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
# hydradb-cli 0.1.0
```

If `hydradb` is not found, make sure your virtual environment is activated or that your Python scripts directory is on your `PATH`.

---

## Authentication

Before using any data commands you need to provide your API key and tenant ID. There are two ways to do this:

### Option A — `hydradb login` (persistent)

Credentials are saved to `~/.hydradb/config.json` (file permissions set to `0600`).

```bash
# Interactive — prompts for the API key
hydradb login --tenant-id YOUR_TENANT_ID

# Non-interactive
hydradb login --api-key YOUR_API_KEY --tenant-id YOUR_TENANT_ID

# Optionally set sub-tenant and a custom API base URL
hydradb login --api-key YOUR_API_KEY --tenant-id YOUR_TENANT_ID \
              --sub-tenant-id SUB_TENANT \
              --base-url https://custom.api.endpoint
```

### Option B — Environment variables (session-only)

```bash
export HYDRA_DB_API_KEY=your_api_key
export HYDRA_DB_TENANT_ID=your_tenant_id
```

Environment variables take precedence over the config file when both are set.

### Verify your session

```bash
hydradb whoami
```

Displays your resolved API key (masked), tenant ID, base URL, config file path, and whether each value came from the config file or an environment variable.

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

Valid keys: `api_key`, `tenant_id`, `sub_tenant_id`, `base_url`.

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
hydradb -o json memories list
hydradb -o json recall full "pricing" | jq '.chunks[0].chunk_content'
```

---

### login / logout / whoami

Manage your CLI session credentials.

| Command | What it does |
|---------|--------------|
| `hydradb login` | Saves API key and tenant info to `~/.hydradb/config.json`. Prompts for the key interactively if `--api-key` is omitted in a TTY session. Validates the key against the API when a tenant ID is provided. |
| `hydradb logout` | Removes the stored config file. |
| `hydradb whoami` | Prints the active API key (masked), tenant ID, sub-tenant ID, base URL, config path, and where each value was resolved from. |

---

### tenant

Create, monitor, and manage HydraDB tenants.

| Command | What it does | Key options |
|---------|--------------|-------------|
| `tenant create <tenant_id>` | Provisions a new tenant via `POST /tenants/create`. | `--embeddings`, `--embeddings-dimension` |
| `tenant monitor [tenant_id]` | Returns status and usage statistics for a tenant. Uses the default tenant when the argument is omitted. | — |
| `tenant list-sub-tenants [tenant_id]` | Lists all sub-tenant IDs under a tenant. | — |
| `tenant delete <tenant_id>` | Permanently deletes a tenant. Asks for confirmation. | `--yes` / `-y` to skip prompt |

```bash
hydradb tenant create my-new-tenant
hydradb tenant monitor
hydradb tenant list-sub-tenants
hydradb tenant delete old-tenant --yes
```

---

### memories

Store and manage user-level memories (preferences, notes, context).

| Command | What it does | Key options |
|---------|--------------|-------------|
| `memories add` | Adds a new user memory. Text can come from `--text`, stdin, or `-` for piped input. By default the API infers structured data from the text. | `--text`, `--title`, `--source-id`, `--user-name`, `--markdown`, `--infer` / `--no-infer`, `--upsert` / `--no-upsert` |
| `memories list` | Lists all stored user memories for the current tenant. | — |
| `memories delete <memory_id>` | Deletes a single memory by ID. | `--yes` / `-y` |

```bash
hydradb memories add --text "User prefers dark mode and weekly email summaries"
hydradb memories add --text "Raw meeting notes..." --no-infer --title "Meeting Notes"
hydradb memories add --text "# Preferences\n- Dark mode" --markdown
hydradb memories list
hydradb memories delete mem_abc123 --yes
```

---

### knowledge

Upload, verify, and delete knowledge sources (files or raw text).

| Command | What it does | Key options |
|---------|--------------|-------------|
| `knowledge upload <files...>` | Uploads one or more files (PDF, DOCX, TXT, etc.) for ingestion. | `--upsert` |
| `knowledge upload-text` | Uploads plain text content directly without a file. | `--text` / `-t`, `--title`, `--source-id`, `--upsert` |
| `knowledge verify <ids...>` | Checks the processing status of previously uploaded sources. | — |
| `knowledge delete <ids...>` | Deletes one or more knowledge sources. | `--yes` / `-y` |

```bash
hydradb knowledge upload ./contract.pdf ./notes.docx
hydradb knowledge upload ./updated-report.pdf --upsert
hydradb knowledge upload-text --text "Q4 pricing: Starter $29, Pro $79" --title "Pricing"
hydradb knowledge verify source_abc123
hydradb knowledge delete source_abc123 source_def456 --yes
```

---

### recall

Search and retrieve information from your knowledge base or user memories.

| Command | What it does | Key options |
|---------|--------------|-------------|
| `recall full <query>` | Semantic search over knowledge/documents. Returns ranked chunks. | `--max-results` / `-n`, `--mode` / `-m` (`fast` or `thinking`), `--alpha`, `--recency-bias`, `--graph-context` / `--no-graph-context`, `--context` |
| `recall preferences <query>` | Semantic search scoped to user memories. | Same as `full` |
| `recall keyword <query>` | Keyword / boolean search across sources or memories. | `--operator` (`or`, `and`, `phrase`), `--search-mode` (`sources` or `memories`), `--max-results` |

```bash
hydradb recall full "What did the team say about pricing?"
hydradb recall full "contract terms" --mode thinking --max-results 20
hydradb recall preferences "What does the user prefer?"
hydradb recall keyword "pricing AND enterprise" --operator and
```

---

### fetch

Inspect ingested sources, retrieve content, and explore knowledge graph relationships.

| Command | What it does | Key options |
|---------|--------------|-------------|
| `fetch sources` | Lists all ingested data sources. | `--kind` (`knowledge` or `memories`), `--page`, `--page-size` |
| `fetch content <source_id>` | Fetches the full content of a source, a presigned download URL, or both. | `--mode` (`content`, `url`, `both`) |
| `fetch relations <source_id>` | Returns knowledge graph triplets (subject → predicate → object) linked to a source. | `--is-memory` / `--is-knowledge`, `--limit` |

```bash
hydradb fetch sources
hydradb fetch sources --kind knowledge --page-size 10
hydradb fetch content source_abc123
hydradb fetch content source_abc123 --mode url
hydradb fetch relations source_abc123
```

---

### config

View and update CLI configuration values without editing the config file manually.

| Command | What it does |
|---------|--------------|
| `config show` | Displays all current settings (API key is masked). |
| `config set <key> <value>` | Sets a single configuration value. Valid keys: `api_key`, `tenant_id`, `sub_tenant_id`, `base_url`. |

```bash
hydradb config show
hydradb config set tenant_id my-tenant
hydradb config set base_url https://api.hydradb.com
```

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `HYDRA_DB_API_KEY` | API key (overrides config file) |
| `HYDRA_DB_TENANT_ID` | Default tenant ID (overrides config file) |
| `HYDRA_DB_SUB_TENANT_ID` | Default sub-tenant ID (overrides config file) |
| `HYDRA_DB_BASE_URL` | API base URL (overrides config file, default `https://api.hydradb.com`) |
| `HYDRADB_OUTPUT` | Default output format — `human` or `json` |

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
