# Changelog

## Unreleased

### Removed

- **`database create --embeddings` and `--embeddings-dimension`.** These forwarded
  `is_embeddings_tenant` to the API, which the spec documents as an internal flag. It
  provisions a raw-embeddings collection *instead of* the knowledge and memory
  collections, so the resulting database could not be used by any other command in this
  CLI: `ingest` reported success and then failed asynchronously with `E6004`, `stats`
  showed `row_count: 0`, `query` returned nothing, and `ready_for_ingestion` never
  became true. The raw-embeddings API these databases exist for has no CLI surface.
  `hydradb tenant create` loses the same two flags.

### Changed

- **The curl installer now installs from GitHub Releases instead of PyPI.** PyPI
  publishing is paused, so `pip install hydradb-cli` still resolves 0.1.0;
  `curl -fsSL https://cli.hydradb.com/install | bash` picks up the latest release wheel.
  Runtime dependencies continue to resolve from PyPI. `HYDRADB_CLI_VERSION` now selects
  a release tag, and the new `HYDRADB_CLI_REPO` overrides the source repository.
  No change to the shipped package itself.

## 0.1.1 — 2026-07-27

The CLI now talks to the HydraDB v2 API and adopts a consistent vocabulary across every
command. Everything that worked in 0.1.0 still works: each renamed command and flag is
kept as an alias that prints a one-line deprecation warning naming its replacement, so
existing scripts keep running while you migrate.

Warnings always go to stderr, so `--output json` pipelines are unaffected.

### Added

- **New command names**, one verb per action:
  `query`, `ingest`, `list`, `inspect`, `delete`, `relations`, `verify`, `doctor`, and
  `database {create,delete,list,collections,stats,readiness,monitor}`.
- **`--database` / `-d` and `--collection`** to scope any command, replacing
  `--tenant-id` / `--sub-tenant-id`.
- **Environment variables** `HYDRADB_API_KEY`, `HYDRADB_DATABASE`, `HYDRADB_COLLECTION`
  and `HYDRADB_BASE_URL`.
- **Config keys** `database` and `collection`, e.g. `hydradb config set database my-db`.
- **`hydradb database stats`** and **`hydradb database readiness`** as separate commands.
  Previously this data was only available merged together via `monitor`, which is still
  there as the combined view.
- **`hydradb doctor`**, which reports your resolved configuration *and* whether the API is
  reachable. It replaces `whoami`, which only ever showed local config.
- A dependency on the official `hydradb-sdk` package, which now handles all API calls.

### Changed

- `login` saves the `database` and `collection` config keys, and its JSON output gains a
  `database` field. The existing `tenant_id` field is unchanged.
- `login --output json` and every other JSON shape keep their documented `jq` paths, with
  the two exceptions listed under Breaking.
- The "no database specified" error now points at `--database` and
  `hydradb config set database`.

### Deprecated

Each of these still works and warns once, naming its replacement:

| Deprecated | Use instead |
|---|---|
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
| `HYDRA_DB_*` and `HYDRADB_API_URL` env vars | `HYDRADB_*` (see Added) |

### Breaking (`--output json`)

Two commands change their JSON shape as a direct result of the v2 API migration. The
documented `query … | jq '.chunks[0].chunk_content'` path is **unchanged**.

- **`list`** (and its `memories list` / `fetch sources` aliases): items are now under
  `sources` rather than `user_memories`. Memories and knowledge appear in the same
  `sources` array.
- **`database monitor`** (and the `tenant monitor` alias): returns a merged
  `{ "database", "stats", "readiness" }` object. The individual pieces are now available
  from `database stats` and `database readiness`.

### Fixed

- Ingesting no longer displays `Source ID: unknown` when the server returns the identifier
  as `id`.
- `ingest --kind knowledge --text … --source-id foo` now preserves the ID you supply, so a
  later `delete foo` matches. Previously the server minted its own ID and the delete
  silently failed.
- `delete` of an ID that does not exist no longer reports success — it exits non-zero and
  reports `{"success": false, …}` in JSON mode.
- Ingesting multiple files at once no longer drops files; each is uploaded and the results
  are merged.
- `hydradb --version` reported `0.1.0` regardless of the installed version.
