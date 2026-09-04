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
  - [list / inspect / relations / subgraph / verify](#list--inspect--relations--subgraph--verify)
  - [delete](#delete)
  - [database](#database)
  - [graph (Cypher / BYOG)](#graph-cypher--byog)
  - [connectors](#connectors)
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
HYDRADB_CLI_VERSION=0.2.0 curl -fsSL https://cli.hydradb.com/install | bash
```

Force reinstall:

```bash
HYDRADB_CLI_FORCE=1 curl -fsSL https://cli.hydradb.com/install | bash
```

### From a GitHub release

```bash
pip install https://github.com/usecortex/hydradb-cli/releases/download/v0.2.0/hydradb_cli-0.2.0-py3-none-any.whl
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
# hydradb-cli 0.2.0
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
| `--kind` | Corpus to search: `memory` or `knowledge` on a split database (omit to search both); on a unified database omit it or pass `unified` |
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
| `--kind` | `memory` (default) or `knowledge`; on a unified database everything is `unified` and this is chosen for you |
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

### list / inspect / relations / subgraph / verify

Browse and read back what you have stored.

| Command | What it does | Key options |
|---------|--------------|-------------|
| `list` | Lists ingested sources and memories | `--kind`, `--page`, `--page-size` |
| `inspect <id>` | Fetches a source's content or a presigned download URL | `--mode` (`content`, `url`, `both`) |
| `relations <id>` | Knowledge graph triplets linked to a source | `--kind`, `--limit` |
| `subgraph <id>` | Everything connected to one item — its thread, replies, parents, children, links — traversed breadth-first | `--kind`, `--depth`, `--max-sources` |
| `verify <ids...>` | Checks indexing progress of uploaded sources | — |

```bash
hydradb list
hydradb list --kind knowledge --page-size 10
hydradb inspect source_abc123
hydradb inspect source_abc123 --mode url
hydradb relations source_abc123
hydradb subgraph source_abc123
hydradb subgraph source_abc123 --depth 2 --max-sources 50
hydradb --output json subgraph source_abc123 | jq '.sources[].source_id'
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
| `database create <database> [--type split\|unified]` | Provisions a new database; `--type unified` gives it ONE corpus (no `--kind` needed on later commands) | — |
| `database list` | Lists all databases for the authenticated user | — |
| `database collections [database]` | Lists collections within a database | — |
| `database stats [database]` | Row-count statistics | — |
| `database readiness [database]` | Whether the database is ready for ingestion | — |
| `database monitor [database]` | Merged stats + readiness | — |
| `database delete <database>` | Permanently deletes a database. Asks for confirmation | `--yes` / `-y` |

```bash
hydradb database create my-new-database
hydradb database create my-unified-database --type unified
hydradb database readiness
hydradb database collections
hydradb database delete old-database --yes
```

---

### graph (Cypher / BYOG)

Full **Cypher** over graph collections you own end to end — HydraDB's graph database offering ([docs](https://docs.hydradb.com/essentials/v2/graph-collections-byog)).

This is a **separate store** from memories and knowledge, and nothing crosses between them: `hydradb query` cannot see graph data, and `hydradb graph query` cannot see memories. Each collection is an independent graph; a query sees exactly one and never another's data.

| Command | What it does | Key options |
|---------|--------------|-------------|
| `graph query <cypher>` | Runs Cypher against one collection | `--param k=v`, `--params-json`, `--database`, `--collection` |
| `graph collections` | Lists the graphs in a graph database | `--database` |
| `graph load <file.json>` | Chunked, re-runnable bulk import | `--label`, `--key`, `--chunk` |
| `graph database create <name>` | Creates a graph database (ready immediately) | — |
| `graph database delete <name>` | Drops a database and every collection in it | `--yes` / `-y` |
| `graph collection delete <name>` | Drops one collection and its data | `--database`, `--yes` / `-y` |

```bash
# Read
hydradb graph query "MATCH (p:Person) RETURN p.name AS name ORDER BY name"

# Parameters — always prefer these over string-building
hydradb graph query "MATCH (p:Person {name: \$n})-[:KNOWS*1..3]->(f) RETURN DISTINCT f.name AS name" \
  --param n=Alice

# What is actually in here? There is no schema command — ask the graph itself.
hydradb graph query "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c ORDER BY l"

# Bulk import: chunked to fit the request cap, MERGEd so a re-run is safe
hydradb graph load people.json --label Person --key ext_id

# JSON out — the rows verbatim, so jq works directly
hydradb graph query "MATCH (p:Person) RETURN p.name AS name" --output json | jq '.[].name'
```

Collections **auto-create on first write**, so there is no create-collection command.

**The CLI does not inspect your Cypher.** There is no `--read-only` flag and no local
pre-rejection of unsupported constructs: both would mean classifying Cypher text
client-side, a heuristic that can only agree with the server or be wrong — and being
wrong means refusing a query HydraDB would have run. Your query is sent verbatim and
the server rules on it.

The request cap and the `graph load` label/merge-key rules *are* checked locally,
because those are transport and string-building facts the client owns rather than
rules about what Cypher means.

**Differences from Neo4j.** Each of these is rejected *before* execution, so a rejected
query changes nothing and fails identically on retry:

- Procedure calls (`CALL db.*`, `CALL apoc.*`) are rejected **by the server**, before it executes anything — `CALL { ... }` subqueries are fine. There is no schema command and no `apoc.meta.schema()`; discover a collection's structure by querying it, as above.
- `LOAD CSV` is rejected — pass rows through parameters, or use `graph load`.
- Existence checks are bare pattern predicates (`WHERE (p)-[:KNOWS]->()`); `EXISTS { ... }` and `exists()` are not accepted.
- `shortestPath` belongs in `RETURN`/`WITH`, not `MATCH p = ...`, and must be directed.
- `EXPLAIN` / `PROFILE` **execute** the query rather than planning it.

Requests are capped at 256 KiB (enforced locally, before upload) and large result sets
are truncated server-side — paginate with `ORDER BY ... SKIP $offset LIMIT $limit`.
### connectors

Managed integrations that sync external sources — Slack, GitHub, Notion, Jira, Google Drive, Gmail, HubSpot and [many more](https://docs.hydradb.com/essentials/v2/connectors) — into a HydraDB database. Once synced, the data is reachable through the ordinary `hydradb query`.

The lifecycle is **create → discover → configure → sync**.

| Command | What it does | Key options |
|---------|--------------|-------------|
| `connectors providers [provider]` | Lists the provider catalogue, or one provider's credential schema and filterable fields | `--category`, `--supported/--all` |
| `connectors list` | Lists connectors with their sync state | `--provider` |
| `connectors get <id>` | Shows one connector | — |
| `connectors status <id>` | Sync health: last run, cycles, documents dispatched | — |
| `connectors create` | Creates a connector | `--provider`, `--name`, `--scope`, `--credentials-stdin`, `--sync-interval` |
| `connectors discover <id>` | Lists resources the provider offers | `--limit`, `--cursor` |
| `connectors configure <id>` | Activates resources and sets sync options | `--resource` / `-r`, `--resources-json`, `--lookback-days` |
| `connectors resources <id>` | Lists configured resources and their fetch health | — |
| `connectors resource add/remove` | Manages one resource row | `--type`, `--collection`, `--yes` |
| `connectors sync <id>` | Triggers an on-demand sync | — |
| `connectors rotate-credentials <id>` | Replaces stored credentials | `--credentials-stdin` |
| `connectors delete <id>` | Deletes a connector | `--yes` / `-y` |

```bash
# What can I connect, and what does it need?
hydradb connectors providers --category messaging
hydradb connectors providers slack

# Create — credentials are piped, never passed as an argument
echo '{"access_token":"xoxb-..."}' | \
  hydradb connectors create --provider slack --scope T01234ABC --credentials-stdin

# See what is available, then activate a subset
hydradb connectors discover <id>
hydradb connectors configure <id> -r C123:channel -r C456:channel --lookback-days 60

# Run a cycle now, then watch it
hydradb connectors sync <id>
hydradb connectors status <id>
```

**Credentials are never accepted as a command-line argument.** There is deliberately no
`--credentials` flag: a secret in `argv` lands in shell history and is visible to any user
on the machine via `ps`, and neither can be undone afterwards. Supply them by piping a JSON
object with `--credentials-stdin`, by setting `HYDRADB_CONNECTOR_CREDENTIALS`, or
interactively when prompted (input is hidden, and the prompt asks for exactly the fields
that provider declares).

Credential *values* are never echoed back either, in any output mode including
`--output json`. The credential *schema* — which fields a provider needs — is public
metadata and is shown in full.

`--scope` is a stable external account identifier (a Slack workspace id, a GitHub org).
Set it when you run more than one connector for the same provider, so documents from
different accounts cannot collide.

Syncing is **asynchronous**: `connectors sync` queues a cycle and returns. A query issued
straight afterwards can legitimately return nothing — poll `connectors status` instead.

The provider catalogue is served by the API and is never hardcoded in the CLI, so newly
supported providers appear without upgrading.

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
| `HYDRADB_GRAPH_COLLECTION` | Default graph collection for `hydradb graph` (default `default`) | — |
| `HYDRADB_CONNECTOR_CREDENTIALS` | Connector credentials as a JSON object, for non-interactive `connectors create` | — |

`HYDRADB_GRAPH_COLLECTION` deliberately does **not** fall back to
`HYDRADB_COLLECTION`: a context collection names a memory/knowledge partition
and means nothing to a graph, so inheriting it would silently point Cypher at a
collection you never chose — which reads an empty graph rather than failing.

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
