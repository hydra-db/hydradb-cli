"""Integration tests for the ``hydradb graph`` (BYOG) commands.

Commands reach the graph endpoints through ``wrapper.graph``; these patch the
``get_wrapper`` seam the graph module imports, so no HTTP happens. The guards
that matter most are the ones that stop a request being made at all, so most of
these assert on what was NOT called.
"""

import json
import re
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import hydradb_cli.config
import hydradb_cli.output
from hydradb_cli.config import save_config
from hydradb_cli.main import app

runner = CliRunner()

_HYDRA_ENV_VARS = (
    "HYDRADB_API_KEY",
    "HYDRADB_DATABASE",
    "HYDRADB_COLLECTION",
    "HYDRADB_GRAPH_COLLECTION",
    "HYDRADB_BASE_URL",
    "HYDRADB_OUTPUT",
    "HYDRADB_TENANT_ID",
    "HYDRADB_SUB_TENANT_ID",
    "HYDRA_DB_API_KEY",
    "HYDRA_DB_TENANT_ID",
    "HYDRA_DB_SUB_TENANT_ID",
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_WIDE = {"COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"}


@pytest.fixture(autouse=True)
def clean_config(tmp_path, monkeypatch):
    config_dir = tmp_path / ".hydradb"
    monkeypatch.setattr("hydradb_cli.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("hydradb_cli.config.CONFIG_FILE", config_dir / "config.json")
    for var in _HYDRA_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    hydradb_cli.output._warned_deprecations.clear()
    hydradb_cli.config._warned_env_aliases.clear()
    yield


def _auth():
    save_config(api_key="test-key-abcdef1234567890", tenant_id="t1")


def _wrapper(rows=None, collections=None, drop_database=None):
    w = MagicMock()
    w.default_database = "t1"
    w.default_graph_collection = "default"
    w.graph.query.return_value = rows if rows is not None else []
    w.graph.collections.return_value = collections if collections is not None else []
    w.graph.create_database.return_value = {"database": "g1", "status": "ready"}
    w.graph.drop_collection.return_value = {}
    w.graph.drop_database.return_value = (
        drop_database if drop_database is not None else {"deleted": True, "deleted_collections": ["c1"]}
    )
    return w


def _patch(w):
    return patch("hydradb_cli.commands.graph.get_wrapper", return_value=w)


def _text(result) -> str:
    return re.sub(r"\s+", " ", _ANSI_RE.sub("", result.output))


# ── the client does not judge the query ─────────────────────────────────────


def test_a_write_is_sent_like_any_other_query():
    _auth()
    w = _wrapper(rows=[])
    with _patch(w):
        result = runner.invoke(app, ["graph", "query", "CREATE (n:Person)"], env=_WIDE)

    assert result.exit_code == 0
    w.graph.query.assert_called_once()


def test_a_rejected_construct_is_sent_to_the_server_verbatim():
    """The server rejects these before executing anything, and says why.

    Verified live: a query mixing CREATE with a procedure call left the node
    count unchanged. Pre-judging it here would only duplicate that ruling, and
    could refuse a query HydraDB would have run.
    """
    _auth()
    w = _wrapper(rows=[])
    with _patch(w):
        result = runner.invoke(app, ["graph", "query", "CALL db.labels()"], env=_WIDE)

    assert result.exit_code == 0
    assert w.graph.query.call_args.kwargs["query"] == "CALL db.labels()"


def test_there_is_no_read_only_flag():
    """Read-only would require classifying Cypher client-side — a heuristic.

    Offering it invites trust in a guarantee it cannot make.
    """
    result = runner.invoke(app, ["graph", "query", "--help"], env=_WIDE)
    assert "--read-only" not in _text(result)


def test_invalid_collection_name_is_rejected_before_the_network():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["graph", "query", "MATCH (n) RETURN n", "--collection", "bad name"], env=_WIDE)

    assert result.exit_code == 1
    # The rule is named, not just the rejection.
    assert "A-Za-z0-9" in _text(result)
    w.graph.query.assert_not_called()


# ── parameters ───────────────────────────────────────────────────────────────


def test_params_parse_as_json_when_they_can():
    """`--param n=3` must be the number 3, not the string "3".

    Without this, every value would be a string and a numeric comparison in
    Cypher would silently match nothing.
    """
    _auth()
    w = _wrapper(rows=[])
    with _patch(w):
        runner.invoke(
            app,
            [
                "graph",
                "query",
                "MATCH (n) WHERE n.age = $age AND n.name = $name RETURN n",
                "--param",
                "age=3",
                "--param",
                "name=Alice",
            ],
            env=_WIDE,
        )

    params = w.graph.query.call_args.kwargs["params"]
    assert params == {"age": 3, "name": "Alice"}


def test_params_json_is_merged():
    _auth()
    w = _wrapper(rows=[])
    with _patch(w):
        runner.invoke(
            app,
            ["graph", "query", "MATCH (n) RETURN n", "--params-json", '{"a": 1, "b": [1,2]}'],
            env=_WIDE,
        )

    assert w.graph.query.call_args.kwargs["params"] == {"a": 1, "b": [1, 2]}


def test_malformed_params_json_is_rejected():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["graph", "query", "MATCH (n) RETURN n", "--params-json", "{oops"], env=_WIDE)

    assert result.exit_code == 1
    w.graph.query.assert_not_called()


def test_param_without_equals_is_rejected():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["graph", "query", "MATCH (n) RETURN n", "--param", "oops"], env=_WIDE)

    assert result.exit_code == 1
    w.graph.query.assert_not_called()


# ── output ───────────────────────────────────────────────────────────────────


def test_json_output_is_the_rows_verbatim():
    """`hydradb graph query ... | jq '.[0].name'` should just work."""
    _auth()
    w = _wrapper(rows=[{"name": "Alice"}, {"name": "Bob"}])
    with _patch(w):
        result = runner.invoke(app, ["--output", "json", "graph", "query", "MATCH (n) RETURN n"], env=_WIDE)

    assert result.exit_code == 0
    assert json.loads(result.output) == [{"name": "Alice"}, {"name": "Bob"}]


def test_nodes_render_readably_in_human_output():
    _auth()
    w = _wrapper(rows=[{"p": {"id": 0, "labels": ["Person"], "name": "Alice"}}])
    with _patch(w):
        result = runner.invoke(app, ["graph", "query", "MATCH (p) RETURN p"], env=_WIDE)

    assert "(:Person {name: Alice})" in _text(result)


def test_an_empty_result_names_both_readings_rather_than_guessing():
    """Zero rows is a read that matched nothing OR a write with no RETURN.

    Telling them apart would mean lexing the Cypher, which this client no
    longer does. Naming both is enough to stop a user re-running a committed
    write because the result looked empty.
    """
    _auth()
    w = _wrapper(rows=[])
    with _patch(w):
        result = runner.invoke(app, ["graph", "query", "MATCH (n:Person) SET n.seen = true"], env=_WIDE)

    assert result.exit_code == 0
    text = _text(result)
    assert "0 rows" in text
    assert "nothing matched" in text
    assert "has been applied" in text


# ── collections and lifecycle ────────────────────────────────────────────────


def test_collections_lists_names():
    _auth()
    w = _wrapper(collections=["contacts", "orgs"])
    with _patch(w):
        result = runner.invoke(app, ["graph", "collections"], env=_WIDE)

    assert "contacts" in _text(result)
    assert "orgs" in _text(result)


def test_no_collections_explains_that_writes_create_them():
    _auth()
    w = _wrapper(collections=[])
    with _patch(w):
        result = runner.invoke(app, ["graph", "collections"], env=_WIDE)

    assert "first write" in _text(result)


def test_database_create_reports_readiness():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["graph", "database", "create", "g1"], env=_WIDE)

    assert result.exit_code == 0
    w.graph.create_database.assert_called_once_with(database="g1")


def test_database_delete_requires_confirmation():
    _auth()
    w = _wrapper()
    with _patch(w):
        # Empty stdin means the confirm prompt is declined.
        result = runner.invoke(app, ["graph", "database", "delete", "g1"], input="\n", env=_WIDE)

    assert result.exit_code != 0
    w.graph.drop_database.assert_not_called()


def test_database_delete_proceeds_with_yes():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["graph", "database", "delete", "g1", "--yes"], env=_WIDE)

    assert result.exit_code == 0
    w.graph.drop_database.assert_called_once_with(database="g1")


def test_partial_drop_is_not_reported_as_a_full_one():
    """`deleted: False` means only the collections went — the database remains.

    Reporting a full drop would tell the user something is gone that is not.
    """
    _auth()
    w = _wrapper(drop_database={"deleted": False, "deleted_collections": ["contacts"]})
    with _patch(w):
        result = runner.invoke(app, ["graph", "database", "delete", "g1", "--yes"], env=_WIDE)

    text = _text(result)
    assert "NOT the database itself" in text
    assert "contacts" in text


def test_collection_delete_requires_confirmation():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["graph", "collection", "delete", "c1"], input="\n", env=_WIDE)

    assert result.exit_code != 0
    w.graph.drop_collection.assert_not_called()


# ── bulk load ────────────────────────────────────────────────────────────────


def test_load_chunks_and_merges(tmp_path):
    _auth()
    rows = [{"ext_id": str(i), "name": f"U{i}"} for i in range(250)]
    path = tmp_path / "rows.json"
    path.write_text(json.dumps(rows))

    w = _wrapper(rows=[])
    with _patch(w):
        result = runner.invoke(
            app,
            ["graph", "load", str(path), "--label", "Person", "--key", "ext_id", "--chunk", "100"],
            env=_WIDE,
        )

    assert result.exit_code == 0
    assert w.graph.query.call_count == 3  # 100 + 100 + 50
    # MERGE on a caller-owned key, so a load that fails part-way is re-runnable.
    assert "MERGE (n:Person {ext_id: row.ext_id})" in w.graph.query.call_args.kwargs["query"]


def test_load_rejects_rows_missing_the_merge_key(tmp_path):
    _auth()
    path = tmp_path / "rows.json"
    path.write_text(json.dumps([{"name": "no key here"}]))

    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["graph", "load", str(path), "--label", "Person", "--key", "ext_id"], env=_WIDE)

    assert result.exit_code == 1
    assert "Nothing was loaded" in _text(result)
    w.graph.query.assert_not_called()


def test_load_rejects_a_hyphenated_label(tmp_path):
    """A hyphen is legal in a collection name and illegal in a Cypher label.

    Validating the label with the collection pattern let `--label my-label`
    through, and the server then rejected the generated query with
    "Invalid input '-': expected a label" — confirmed against the live API.
    """
    _auth()
    path = tmp_path / "rows.json"
    path.write_text(json.dumps([{"ext_id": "1"}]))

    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["graph", "load", str(path), "--label", "my-label", "--key", "ext_id"], env=_WIDE)

    assert result.exit_code == 1
    assert "Invalid label" in _text(result)
    w.graph.query.assert_not_called()


def test_load_rejects_a_merge_key_that_is_not_an_identifier(tmp_path):
    """`--key` is interpolated twice — as a map key and as `row.<key>`.

    Cypher can bind neither, so a key like `ext-id` produces a query the server
    rejects (verified live) rather than a load.
    """
    _auth()
    path = tmp_path / "rows.json"
    path.write_text(json.dumps([{"ext-id": "1"}]))

    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["graph", "load", str(path), "--label", "Person", "--key", "ext-id"], env=_WIDE)

    assert result.exit_code == 1
    assert "Invalid merge key" in _text(result)
    w.graph.query.assert_not_called()


@pytest.mark.parametrize("chunk", ["0", "-1"])
def test_load_rejects_a_nonpositive_chunk(tmp_path, chunk):
    """`--chunk 0` raised ValueError; `--chunk -1` built no batches at all.

    The negative case was the worse one: it reported success having loaded
    nothing.
    """
    _auth()
    path = tmp_path / "rows.json"
    path.write_text(json.dumps([{"ext_id": "1"}]))

    w = _wrapper()
    with _patch(w):
        result = runner.invoke(
            app,
            ["graph", "load", str(path), "--label", "Person", "--key", "ext_id", "--chunk", chunk],
            env=_WIDE,
        )

    assert result.exit_code != 0
    w.graph.query.assert_not_called()


def test_load_rejects_an_unsafe_label(tmp_path):
    """The label is interpolated into the query — Cypher cannot bind it."""
    _auth()
    path = tmp_path / "rows.json"
    path.write_text(json.dumps([{"ext_id": "1"}]))

    w = _wrapper()
    with _patch(w):
        result = runner.invoke(
            app,
            ["graph", "load", str(path), "--label", "P) DETACH DELETE (n", "--key", "ext_id"],
            env=_WIDE,
        )

    assert result.exit_code == 1
    w.graph.query.assert_not_called()


def test_load_refuses_oversized_batches_before_sending_any(tmp_path):
    """An all-or-nothing check: a load must not half-apply then fail."""
    _auth()
    rows = [{"ext_id": str(i), "pad": "x" * 500} for i in range(2000)]
    path = tmp_path / "rows.json"
    path.write_text(json.dumps(rows))

    w = _wrapper()
    with _patch(w):
        result = runner.invoke(
            app,
            ["graph", "load", str(path), "--label", "P", "--key", "ext_id", "--chunk", "2000"],
            env=_WIDE,
        )

    assert result.exit_code == 1
    assert "Nothing was loaded" in _text(result)
    w.graph.query.assert_not_called()


def test_load_rejects_a_missing_file(tmp_path):
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(
            app,
            ["graph", "load", str(tmp_path / "nope.json"), "--label", "P", "--key", "id"],
            env=_WIDE,
        )

    assert result.exit_code == 1
    w.graph.query.assert_not_called()


# ── scope ────────────────────────────────────────────────────────────────────


def test_graph_collection_does_not_inherit_the_context_collection(monkeypatch):
    """A context collection names a memory partition and means nothing to a graph.

    Inheriting it would silently point Cypher at a collection the user never
    chose, which reads an empty graph rather than failing.
    """
    from hydradb_cli.config import get_graph_collection

    save_config(api_key="k-abcdef1234567890", tenant_id="t1", sub_tenant_id="my-memories")
    assert get_graph_collection() == "default"

    monkeypatch.setenv("HYDRADB_GRAPH_COLLECTION", "my-graph")
    assert get_graph_collection() == "my-graph"
