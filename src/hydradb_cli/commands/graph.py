"""Graph (BYOG) commands — full Cypher over graph collections you own.

``hydradb graph query|collections|load``, plus ``graph database`` and
``graph collection`` for lifecycle management.

This is HydraDB's **graph database** offering, distinct from the memory and
knowledge corpora the other commands address. Nothing crosses between them:
``hydradb query`` cannot see graph data, and ``hydradb graph query`` cannot see
memories. See https://docs.hydradb.com/essentials/v2/graph-collections-byog
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from hydradb_cli.cypher import (
    COLLECTION_PATTERN,
    MAX_BODY_BYTES,
    body_size,
    is_write_query,
    rows_to_table,
    unsupported_construct,
    write_clauses_in,
)
from hydradb_cli.hydra import HydraDBClientError
from hydradb_cli.output import (
    console,
    get_output_format,
    make_table,
    print_error,
    print_json,
    print_success,
    print_warning,
    spinner,
)
from hydradb_cli.utils.common import get_wrapper, handle_api_error

app = typer.Typer(
    help=(
        "[bold]Graph[/bold] (BYOG) — full Cypher over graph collections you own.\n\n"
        "A separate store from memories and knowledge: 'hydradb query' cannot see graph "
        "data, and 'hydradb graph query' cannot see memories."
    ),
    no_args_is_help=True,
)

database_app = typer.Typer(help="Manage [bold]graph databases[/bold].", no_args_is_help=True)
collection_app = typer.Typer(help="Manage [bold]graph collections[/bold].", no_args_is_help=True)
app.add_typer(database_app, name="database")
app.add_typer(collection_app, name="collection")


def _execute(message: str, call):
    """Run a wrapper call under a spinner, translating errors to CLI exits."""
    try:
        with spinner(message):
            return call()
    except HydraDBClientError as e:
        handle_api_error(e)


def _validate_collection(collection: str | None) -> None:
    """Reject an invalid collection name locally, naming the rule.

    The server answers with a bare 400; stating the charset here is the
    difference between a fixable message and a guess.
    """
    if collection is None:
        return
    if not COLLECTION_PATTERN.match(collection):
        print_error(
            f"Invalid collection name '{collection}'. Collection names must match "
            "[A-Za-z0-9][A-Za-z0-9_-]{0,63} — start with a letter or digit, then letters, "
            "digits, underscores or hyphens, up to 64 characters."
        )


def _parse_params(param: list[str] | None, params_json: str | None) -> dict[str, Any]:
    """Build the Cypher parameter map from ``--param k=v`` and/or ``--params-json``.

    Both forms are supported because they serve different callers: ``--param``
    is what a human types, ``--params-json`` is what a script pipes. Values from
    ``--param`` are parsed as JSON when they parse, so ``--param n=3`` is the
    number 3 and ``--param n=Alice`` is the string — without that, every value
    would be a string and numeric comparisons in Cypher would silently match
    nothing.
    """
    params: dict[str, Any] = {}

    if params_json:
        try:
            loaded = json.loads(params_json)
        except json.JSONDecodeError as e:
            print_error(f"--params-json is not valid JSON: {e}")
        if not isinstance(loaded, dict):
            print_error('--params-json must be a JSON object, e.g. \'{"name": "Alice"}\'.')
        params.update(loaded)

    for item in param or []:
        if "=" not in item:
            print_error(f"--param must be key=value, got '{item}'.")
        key, _, raw = item.partition("=")
        key = key.strip()
        if not key:
            print_error(f"--param needs a key before '=', got '{item}'.")
        try:
            params[key] = json.loads(raw)
        except json.JSONDecodeError:
            params[key] = raw

    return params


def _guard_query(query: str, *, read_only: bool, require_write: bool = False) -> None:
    """Refuse, before the network, anything the server or the caller ruled out."""
    clauses = write_clauses_in(query)

    if read_only and clauses:
        print_error(
            f"--read-only was given and this query contains {', '.join(clauses)}. "
            "Nothing was executed. Drop --read-only to run it."
        )
    if require_write and not clauses:
        print_error("This query has no write clause. Nothing was executed.")

    unsupported = unsupported_construct(query)
    if unsupported:
        print_error(f"{unsupported} Nothing was executed.")


def _print_rows(rows: list[dict], *, database: str, collection: str, is_write: bool) -> None:
    if get_output_format() == "json":
        # The rows verbatim — a jq contract from day one. Deliberately not
        # wrapped in a status envelope: `hydradb graph query ... | jq '.[0].name'`
        # is the obvious thing to type, and it should work.
        print_json(rows)
        return

    if not rows:
        if is_write:
            # A write with no RETURN legitimately yields zero rows. Calling that
            # "no results" invites the user to re-run a write that committed.
            print_success(
                f"Write completed against {database}/{collection}. "
                "The query returned no rows, which is expected when it has no RETURN clause."
            )
        else:
            console.print(f"  [dim]No rows matched in {database}/{collection}.[/dim]")
        return

    headers, cells = rows_to_table(rows)
    console.print(make_table(*headers, rows=cells))
    console.print(f"  [dim]{len(rows)} row(s) from {database}/{collection}[/dim]")


@app.command(name="query")
def query(
    cypher: str = typer.Argument(metavar="CYPHER", help="The Cypher query to run."),
    param: list[str] | None = typer.Option(
        None, "--param", "-p", help="Query parameter as key=value. Repeatable. Values parse as JSON when they can."
    ),
    params_json: str | None = typer.Option(None, "--params-json", help="Query parameters as a JSON object."),
    read_only: bool = typer.Option(
        False, "--read-only", help="Refuse the query if it contains a write clause, before sending it."
    ),
    database: str | None = typer.Option(None, "--database", "-d", help="Graph database. Uses the default if unset."),
    collection: str | None = typer.Option(None, "--collection", "-c", help="Graph collection."),
) -> None:
    """Run a Cypher query against a graph collection.

    Pass user data through --param rather than building it into the query
    string: parameters are bound safely and keep query plans cacheable.

    HydraDB runs your Cypher verbatim. Two constructs are rejected before
    execution: procedure calls (CALL db.*, CALL apoc.*) and LOAD CSV. Note that
    EXPLAIN and PROFILE EXECUTE the query rather than planning it.
    """
    _validate_collection(collection)
    _guard_query(cypher, read_only=read_only)

    params = _parse_params(param, params_json)
    wrapper = get_wrapper()

    payload = {"query": cypher, "params": params}
    if body_size(payload) > MAX_BODY_BYTES:
        print_error(
            f"This request is {body_size(payload) // 1024} KiB, over HydraDB's "
            f"{MAX_BODY_BYTES // 1024} KiB limit. Split it into batches — "
            "'hydradb graph load' chunks a JSON file automatically."
        )

    rows = _execute(
        "Running query...",
        lambda: wrapper.graph.query(query=cypher, params=params, database=database, collection=collection),
    )

    _print_rows(
        rows,
        database=database or wrapper.default_database or "",
        collection=collection or wrapper.default_graph_collection,
        is_write=is_write_query(cypher),
    )


@app.command(name="collections")
def collections(
    database: str | None = typer.Option(None, "--database", "-d", help="Graph database. Uses the default if unset."),
) -> None:
    """List the graph collections in a graph database.

    Each collection is an independent graph. Collections auto-create on their
    first write, so a name missing here has simply never been written to.
    """
    wrapper = get_wrapper()
    names = _execute("Listing collections...", lambda: wrapper.graph.collections(database=database))

    if get_output_format() == "json":
        print_json({"database": database or wrapper.default_database, "collections": names})
        return

    if not names:
        console.print(
            f"  [dim]No graph collections in {database or wrapper.default_database} yet. "
            "Collections are created by their first write.[/dim]"
        )
        return

    console.print(make_table("Collection", rows=[[name] for name in names]))


@app.command(name="load")
def load(
    file: Path = typer.Argument(help="JSON file: an array of row objects."),
    label: str = typer.Option(..., "--label", "-l", help="Node label to MERGE, e.g. Person."),
    key: str = typer.Option(..., "--key", "-k", help="Property to MERGE on. Must be unique per row."),
    chunk: int = typer.Option(500, "--chunk", help="Rows per request."),
    database: str | None = typer.Option(None, "--database", "-d", help="Graph database. Uses the default if unset."),
    collection: str | None = typer.Option(None, "--collection", "-c", help="Graph collection."),
) -> None:
    """Bulk-load rows from a JSON file into a graph collection.

    Chunks the file into batches that fit inside the 256 KiB request cap and
    the 30s write budget, and MERGEs on a key you own so a load that fails
    part-way is safe to re-run — a bare CREATE would duplicate every row in an
    already-applied chunk.
    """
    _validate_collection(collection)

    if not file.exists():
        print_error(f"File not found: {file}")
    try:
        rows = json.loads(file.read_text())
    except json.JSONDecodeError as e:
        print_error(f"{file} is not valid JSON: {e}")
    if not isinstance(rows, list):
        print_error(f"{file} must contain a JSON array of row objects.")
    if not rows:
        print_error(f"{file} contains no rows.")

    missing = [i for i, row in enumerate(rows) if not isinstance(row, dict) or key not in row]
    if missing:
        shown = ", ".join(str(i) for i in missing[:5])
        print_error(
            f"Every row must be an object containing the merge key '{key}'. "
            f"Missing at row index: {shown}{' ...' if len(missing) > 5 else ''}. Nothing was loaded."
        )

    if not COLLECTION_PATTERN.match(label):
        # The label is interpolated into the query text (Cypher cannot bind a
        # label as a parameter), so it is constrained to a safe charset rather
        # than trusted. Same rule as a collection name, which is strict enough.
        print_error(f"Invalid label '{label}'. Use letters, digits, underscores and hyphens only.")

    wrapper = get_wrapper()
    cypher = f"UNWIND $rows AS row MERGE (n:{label} {{{key}: row.{key}}}) SET n += row"

    batches = [rows[i : i + chunk] for i in range(0, len(rows), chunk)]
    oversized = [
        i for i, batch in enumerate(batches) if body_size({"query": cypher, "params": {"rows": batch}}) > MAX_BODY_BYTES
    ]
    if oversized:
        print_error(
            f"{len(oversized)} batch(es) exceed the {MAX_BODY_BYTES // 1024} KiB request cap at "
            f"--chunk {chunk}. Nothing was loaded — re-run with a smaller --chunk."
        )

    loaded = 0
    for index, batch in enumerate(batches, 1):
        _execute(
            f"Loading batch {index}/{len(batches)}...",
            lambda b=batch: wrapper.graph.query(
                query=cypher, params={"rows": b}, database=database, collection=collection
            ),
        )
        loaded += len(batch)

    if get_output_format() == "json":
        print_json({"loaded": loaded, "batches": len(batches), "label": label, "key": key})
        return
    print_success(f"Loaded {loaded} row(s) as (:{label}) in {len(batches)} batch(es), merging on '{key}'.")


@database_app.command(name="create")
def database_create(
    database: str = typer.Argument(help="Name of the graph database to create."),
) -> None:
    """Create a graph database. Ready immediately — there is no provisioning wait."""
    wrapper = get_wrapper()
    result = _execute("Creating graph database...", lambda: wrapper.graph.create_database(database=database))

    if get_output_format() == "json":
        print_json(result)
        return
    print_success(
        f"Created graph database '{database}' (status: {result.get('status', 'ready')}). "
        "Collections are created by their first write."
    )


@database_app.command(name="delete")
def database_delete(
    database: str = typer.Argument(help="Name of the graph database to drop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Drop a graph database and every collection in it. Irreversible."""
    if not yes:
        typer.confirm(
            f"Drop graph database '{database}' and ALL its collections? This cannot be undone.",
            abort=True,
        )

    wrapper = get_wrapper()
    result = _execute("Dropping graph database...", lambda: wrapper.graph.drop_database(database=database))

    if get_output_format() == "json":
        print_json(result)
        return

    dropped = result.get("deleted_collections") or []
    listed = f" Collections removed: {', '.join(dropped)}." if dropped else ""
    if result.get("deleted") is False:
        # A real, different outcome: the database predates BYOG, so only its
        # graph collections went. Reporting a full drop would tell the user
        # something is gone that is still there.
        print_warning(
            f"Dropped the graph collections in '{database}', but NOT the database itself — "
            f"it was created through the standard database API, so remove it with "
            f"'hydradb database delete'.{listed}"
        )
        return
    print_success(f"Dropped graph database '{database}' and everything in it.{listed}")


@collection_app.command(name="delete")
def collection_delete(
    collection: str = typer.Argument(help="Name of the graph collection to drop."),
    database: str | None = typer.Option(None, "--database", "-d", help="Graph database. Uses the default if unset."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Drop one graph collection and all its data. Irreversible, and idempotent."""
    _validate_collection(collection)
    if not yes:
        typer.confirm(f"Drop graph collection '{collection}' and all its data? This cannot be undone.", abort=True)

    wrapper = get_wrapper()
    _execute(
        "Dropping graph collection...",
        lambda: wrapper.graph.drop_collection(collection=collection, database=database),
    )

    if get_output_format() == "json":
        print_json({"database": database or wrapper.default_database, "collection": collection, "dropped": True})
        return
    print_success(
        f"Dropped graph collection '{collection}'. This call is idempotent, so it also "
        "succeeds when the collection did not exist."
    )
