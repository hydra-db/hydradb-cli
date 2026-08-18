"""Unit tests for the graph (BYOG) rendering, limits and name rules.

There is deliberately no Cypher analysis to test: the client does not classify
or pre-judge queries, it sends them and lets the server rule on them.
"""

import pytest

from hydradb_cli.cypher import (
    COLLECTION_PATTERN,
    CYPHER_IDENTIFIER,
    MAX_BODY_BYTES,
    body_size,
    render_value,
    rows_to_table,
)

# ── names and sizes ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["contacts", "a", "A1", "my-graph_2", "0start", "a" * 64])
def test_valid_collection_names(name):
    assert COLLECTION_PATTERN.match(name)


@pytest.mark.parametrize("name", ["", "-leading", "_leading", "has space", "has/slash", "a" * 65, "contacts\n"])
def test_invalid_collection_names(name):
    assert COLLECTION_PATTERN.match(name) is None


def test_body_cap_matches_the_documented_limit():
    assert MAX_BODY_BYTES == 262_144


def test_body_size_counts_bytes_not_characters():
    """Non-ASCII is exactly where a batch that looks small stops being small."""
    assert body_size({"q": "é"}) > body_size({"q": "e"})


# ── rendering ────────────────────────────────────────────────────────────────


def test_node_renders_as_cypher_notation():
    node = {"id": 0, "labels": ["Person"], "name": "Alice", "role": "admin"}
    assert render_value(node) == "(:Person {name: Alice, role: admin})"


def test_relationship_renders_with_endpoints():
    rel = {"id": 0, "relation": "KNOWS", "since": 2020, "source_node_id": 0, "target_node_id": 1}
    assert render_value(rel) == "[0]-[:KNOWS {since: 2020}]->[1]"


def test_path_renders_in_traversal_order():
    path = {
        "nodes": [
            {"id": 0, "labels": ["Person"], "name": "Alice"},
            {"id": 1, "labels": ["Person"], "name": "Bob"},
        ],
        "edges": [{"id": 0, "relation": "KNOWS", "source_node_id": 0, "target_node_id": 1}],
    }
    assert render_value(path) == "(:Person {name: Alice})-[:KNOWS]->(:Person {name: Bob})"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Alice", "Alice"), (42, "42"), (None, "null"), (True, "true"), (False, "false")],
)
def test_scalars_render_as_themselves(value, expected):
    assert render_value(value) == expected


def test_rows_to_table_covers_every_column():
    """Rows of differing shape must not silently lose a column."""
    headers, cells = rows_to_table([{"a": 1}, {"b": 2}])
    assert headers == ["a", "b"]
    # The absent cell is marked rather than omitted, keeping columns aligned.
    assert cells == [["1", "—"], ["—", "2"]]


# ── Cypher identifiers ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["Person", "_x", "ext_id", "a1", "A_1b2"])
def test_valid_cypher_identifiers(name):
    assert CYPHER_IDENTIFIER.match(name)


@pytest.mark.parametrize("name", ["", "my-label", "ext-id", "1st", "has space", "a.b", "n)", "Person\n"])
def test_invalid_cypher_identifiers(name):
    assert CYPHER_IDENTIFIER.match(name) is None


@pytest.mark.parametrize("pattern", [COLLECTION_PATTERN, CYPHER_IDENTIFIER])
def test_patterns_reject_a_trailing_newline(pattern):
    """Python's ``$`` also matches immediately before a trailing newline.

    A scripted caller writing `--label "$(cat name.txt)"` gets exactly that, and
    with ``$`` the value passed validation and was interpolated verbatim into
    the query. Both patterns are anchored with ``\\Z``.
    """
    assert pattern.match("Person") is not None
    assert pattern.match("Person\n") is None


def test_identifiers_are_stricter_than_collection_names():
    """A hyphen is legal in a collection name and illegal in an identifier.

    Conflating the two is what let `--label my-label` reach the server and fail
    there. These patterns must not be interchangeable.
    """
    assert COLLECTION_PATTERN.match("my-label")
    assert CYPHER_IDENTIFIER.match("my-label") is None
