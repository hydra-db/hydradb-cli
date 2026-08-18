# Changelog

## Unreleased

### Added

- **`hydradb graph` — full Cypher over graph collections you own (BYOG).** HydraDB's graph database offering had no CLI surface at all: `query`, `ingest` and the rest address the memory and knowledge corpora, and the property graphs users model and own end to end were reachable only through the raw API. This adds `graph query`, `graph collections`, `graph load`, `graph database create/delete` and `graph collection delete`. Everything existing is untouched — the two stores are separate, and nothing crosses between them.

  `graph query` takes parameters through `--param k=v` (values parse as JSON when they can, so `--param n=3` is the number 3) or `--params-json`. `--output json` prints the rows verbatim, so `hydradb graph query ... | jq '.[].name'` works without unwrapping an envelope.

  **The CLI does not inspect your Cypher.** There is no `--read-only` flag and no local pre-rejection of unsupported constructs: both would put a second, worse implementation of the server's rules inside a client, able only to agree with the server or to be wrong — and being wrong means refusing a query HydraDB would have run. The server rejects unsupported constructs before executing anything (verified: a query mixing `CREATE` with a procedure call leaves the node count unchanged) and its messages are more specific than the ones the CLI used to produce. Queries are sent verbatim.

  There is deliberately **no** `graph schema` command. HydraDB rejects `CALL db.*` and `CALL apoc.*` outright, and a derived schema is not part of the product — callers discover a collection's structure by querying it (`MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c ORDER BY l`), which the help text and README both show.

  `graph load` chunks a JSON file into batches that fit inside the 256 KiB request cap and the 30s write budget, and `MERGE`s on a key you supply so a load that fails part-way is safe to re-run — a bare `CREATE` would duplicate every row of an already-applied chunk. Rows missing the merge key, an unsafe `--label`, and batches that would exceed the cap are all rejected before anything is sent, so a load never half-applies.

  The 256 KiB request cap and the label/merge-key name rules ARE checked locally, because those are transport and string-building facts the client owns rather than rules about what Cypher means.

  Both destructive commands (`graph database delete`, `graph collection delete`) confirm unless `--yes` is given. `graph database delete` distinguishes a full drop from the case where only the graph collections went because the database was created through the standard database API — reporting that as a full drop would say something is gone that is still there.

- **`HYDRADB_GRAPH_COLLECTION`** sets the default graph collection (default `default`). It deliberately does not fall back to `HYDRADB_COLLECTION`: a context collection names a memory/knowledge partition and means nothing to a graph, so inheriting it would silently point Cypher at a collection you never chose.

### Internal

- `HydraDB.graph` is a hand-rolled `httpx` path rather than an SDK call: the pinned `hydradb-sdk==2.1.2` exposes `context`, `databases`, `connectors` and `webhooks` and has no `byog` resource, so those endpoints are unreachable through it. It reuses the wrapper's existing envelope unwrapping and error translation and raises the same `HydraDBClientError`, so `handle_api_error` treats a BYOG failure exactly like an SDK one. When the SDK grows a `byog` resource, that one class is reimplemented over it and no caller changes. The exact SDK pin (CONTRACT S2 rule 1) is unaffected — there is no generated name to be insulated from yet.

## 0.2.0 — 2026-07-31

### Removed

- **`database create --embeddings` and `--embeddings-dimension`.** These forwarded
  `is_embeddings_tenant` to the API, which the spec documents as an internal flag. It
  provisions a raw-embeddings collection *instead of* the knowledge and memory
  collections, so the resulting database could not be used by any other command in this
  CLI: `ingest` reported success and then failed asynchronously with `E6004`, `stats`
  showed `row_count: 0`, `query` returned nothing, and `ready_for_ingestion` never
  became true. The raw-embeddings API these databases exist for has no CLI surface.
  `hydradb tenant create` loses the same two flags.

### Fixed

- **`config show` now labels the scope rows `database` and `collection`** instead of
  `tenant_id` / `sub_tenant_id`, matching the vocabulary you set them with. Config file
  keys are unchanged and files holding the old keys keep working, as does the
  `--output json` shape.
- **`database collections` no longer mangles its title** when the database name is long.
  The title moved onto a panel — the shape `database stats`/`readiness`/`monitor` already
  use — instead of a Rich table title wrapped to the table's narrow width.
- **`relations` no longer mangles its title** either. Same cause and same fix: the
  subject/predicate/object columns are narrow, so any ordinary source ID broke mid-token.

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
