"""Conformance runner (CONTRACT §4).

Feeds ``vectors.json`` through the wrapper against a mocked SDK transport and
asserts, for every vector:

  (a) the wrapper emits the canonical operation (correct endpoint + HTTP method);
  (b) the SDK call carries the expected fields (``args_include`` / ``args_scope``)
      and honours the content-type / forbidden-field guards (a unified-database
      call is a raw v2 request rather than an SDK call, recorded the same way);
  (c) every deprecated **CLI** alias listed resolves to the same canonical
      operation (same endpoint + method).

This is the drift guard: a rename, a changed default, or a divergence in ingest
encoding fails here.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from conftest import Recorder, load_vectors
from typer.testing import CliRunner

from hydradb_cli.main import app

runner = CliRunner()
VECTORS = load_vectors()["vectors"]

# op → (HTTP method, path suffix) the canonical SDK call must hit.
OP_ENDPOINT: dict[str, tuple[str, str]] = {
    "query": ("POST", "query"),
    "ingest": ("POST", "context/ingest"),
    "list": ("POST", "context/list"),
    "inspect": ("GET", "context/inspect"),
    "delete": ("DELETE", "context"),
    "relations": ("GET", "context/relations"),
    "context.ingestionStatus": ("GET", "context/status"),
    "database.create": ("POST", "databases"),
    "database.delete": ("DELETE", "databases"),
    "database.collections": ("GET", "databases/collections"),
    "database.readiness": ("GET", "databases/status"),
}


def _dispatch(wrapper, op: str, args: dict):
    """Invoke the canonical wrapper method for a vector op."""
    ctx = wrapper.context
    dbs = wrapper.databases
    if op == "query":
        return ctx.query(query=args["query"], kind=args.get("kind"), operator=args.get("operator"))
    if op == "ingest":
        if args.get("layout") == "unified":
            # PRO-1618: the JSON body with the `context` list, in the contract's
            # field names; `id` is the client-assigned context_id.
            item = {
                key: args[arg]
                for arg, key in (("id", "context_id"), ("title", "title"), ("text", "text"))
                if args.get(arg) is not None
            }
            return ctx.ingest_context([item])
        return ctx.ingest(kind=args["kind"], text=args.get("text"), title=args.get("title"))
    if op == "list":
        return ctx.list(kind=args.get("kind"))
    if op == "inspect":
        return ctx.inspect(id=args["id"], mode=args.get("mode"))
    if op == "delete":
        return ctx.delete(ids=args["ids"], kind=args.get("kind"))
    if op == "relations":
        return ctx.relations(id=args.get("id"), kind=args.get("kind"))
    if op == "context.ingestionStatus":
        return ctx.ingestion_status(ids=args["ids"])
    if op == "database.create":
        return dbs.create(database=args["database"])
    if op == "database.delete":
        return dbs.delete(database=args["database"])
    if op == "database.collections":
        return dbs.collections(database=args["database"])
    if op == "database.readiness":
        return dbs.readiness(database=args["database"])
    raise AssertionError(f"unhandled op: {op}")


def _endpoint_matches(recorder: Recorder, op: str) -> bool:
    method, suffix = OP_ENDPOINT[op]
    return recorder.method == method and recorder.path.rstrip("/").endswith(suffix)


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
def test_wrapper_emits_canonical_call(vector, wrapper, recorder):
    op = vector["call"]["op"]
    args = vector["call"].get("args", {})
    expect = vector["expect"]
    sdk = expect["sdk"]

    _dispatch(wrapper, op, args)

    # (a) canonical endpoint + method
    assert _endpoint_matches(recorder, op), f"{vector['id']}: hit {recorder.method} {recorder.path}"

    fields = recorder.fields()

    # content-type guards
    if sdk.get("content_type"):
        assert recorder.content_type.startswith(sdk["content_type"]), (
            f"{vector['id']}: content-type {recorder.content_type!r}"
        )
    if sdk.get("forbid_content_type"):
        assert not recorder.content_type.startswith(sdk["forbid_content_type"])
    if sdk.get("forbid_field"):
        assert sdk["forbid_field"] not in fields, f"{vector['id']}: forbidden field {sdk['forbid_field']} present"
    if sdk.get("source_field_in"):
        assert any(f in fields for f in sdk["source_field_in"]), (
            f"{vector['id']}: none of {sdk['source_field_in']} present in {sorted(fields)}"
        )

    # (b) required fields + scope
    for key, value in sdk.get("args_include", {}).items():
        assert key in fields, f"{vector['id']}: missing field {key}"
        assert _field_eq(fields[key], value), f"{vector['id']}: {key}={fields[key]!r} != {value!r}"
    for key, value in sdk.get("args_scope", {}).items():
        assert key in fields, f"{vector['id']}: missing scope {key}"
        assert _field_eq(fields[key], value), f"{vector['id']}: scope {key}={fields[key]!r} != {value!r}"


def _field_eq(actual, expected) -> bool:
    """Compare a request field to an expected value across wire encodings.

    Multipart values arrive as strings; JSON/query values keep their native type.
    Lists (e.g. ``ids``) may arrive as a JSON array or a repeated/scalar param.
    """
    if isinstance(expected, list):
        if isinstance(actual, list):
            return actual == expected
        # scalar single-element list on the wire (e.g. ?ids=src_123)
        return len(expected) == 1 and str(actual) == str(expected[0])
    return str(actual) == str(expected)


# ── CLI alias resolution ──────────────────────────────────────────────────────


def _cli_argv(alias: str, op: str, args: dict, tmp_path) -> list[str] | None:
    """Reconstruct the argv for a deprecated CLI alias from a vector's args."""
    parts = alias.split()
    if op == "query":
        argv = parts + [args["query"]]
        if args.get("operator"):
            argv += ["--operator", args["operator"]]
        return argv
    if op == "ingest":
        if alias == "knowledge upload":
            f = tmp_path / "doc.txt"
            f.write_text(args.get("text") or "content")
            return parts + [str(f)]
        return parts + ["--text", args.get("text") or "content"]
    if op == "list":
        return parts
    if op == "inspect":
        return parts + [args["id"]]
    if op == "delete":
        return parts + list(args["ids"]) + ["--yes"]
    if op == "relations":
        return parts + [args["id"]]
    if op == "context.ingestionStatus":
        return parts + list(args["ids"])
    if op.startswith("database."):
        if op == "database.create":
            return parts + [args["database"]]
        if op == "database.delete":
            return parts + [args["database"], "--yes"]
        return parts + [args["database"]]
    return None


def _alias_cases():
    cases = []
    for vector in VECTORS:
        for alias in vector.get("aliases", {}).get("cli", []):
            cases.append((vector, alias))
    return cases


@pytest.mark.parametrize(
    "vector,alias",
    _alias_cases(),
    ids=[f"{v['id']}:{a}" for v, a in _alias_cases()],
)
def test_cli_alias_resolves_to_canonical(vector, alias, wrapper, recorder, tmp_path, monkeypatch):
    """Every listed CLI alias must route to the same canonical SDK operation."""
    op = vector["call"]["op"]
    args = vector["call"].get("args", {})
    argv = _cli_argv(alias, op, args, tmp_path)
    assert argv is not None, f"no argv builder for {op}"

    # The alias command reads default scope from config; supply it via env.
    monkeypatch.setenv("HYDRADB_API_KEY", "test-token")
    monkeypatch.setenv("HYDRADB_DATABASE", "db_test")
    monkeypatch.setenv("HYDRADB_COLLECTION", "col_test")

    with patch("hydradb_cli.commands._impl.get_wrapper", return_value=wrapper):
        result = runner.invoke(app, argv)

    assert result.exit_code == 0, f"alias '{alias}' failed: {result.output}\n{result.exception}"
    assert _endpoint_matches(recorder, op), (
        f"alias '{alias}' hit {recorder.method} {recorder.path}, expected {OP_ENDPOINT[op]}"
    )
