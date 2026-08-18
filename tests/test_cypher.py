"""Unit tests for the Cypher analysis used by the graph (BYOG) commands.

These need no network and no wrapper: they cover the write detector that
``--read-only`` rests on, the constructs HydraDB rejects before execution, the
derived-schema queries, and result rendering.
"""

import pytest

from hydradb_cli.cypher import (
    COLLECTION_PATTERN,
    CYPHER_IDENTIFIER,
    MAX_BODY_BYTES,
    body_size,
    is_write_query,
    render_value,
    rows_to_table,
    strip_non_code,
    unsupported_construct,
    write_clauses_in,
)

# ── write detection ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) RETURN n",
        "MATCH (p:Person)-[:KNOWS*1..3]->(f) RETURN DISTINCT f.name AS name",
        "MATCH (a),(b) RETURN shortestPath((a)-[*..6]->(b)) AS p",
        "MATCH (p:Person) WHERE (p)-[:KNOWS]->() RETURN p.name AS name",
        "CALL { MATCH (p:Person) RETURN p.name AS n } RETURN n",
        "MATCH (p:Person) RETURN p.name AS name ORDER BY name SKIP $offset LIMIT $limit",
    ],
)
def test_reads_are_not_writes(query):
    assert is_write_query(query) is False


@pytest.mark.parametrize(
    "query",
    [
        "CREATE (n:Person {name: 'Alice'})",
        "MERGE (n:Person {ext_id: 1})",
        "MATCH (n) SET n.seen = true",
        "MATCH (n) DELETE n",
        "MATCH (n) DETACH DELETE n",
        "MATCH (n) REMOVE n.tag",
        "CREATE INDEX FOR (n:Person) ON (n.name)",
        "DROP INDEX ON :Person(name)",
        "MATCH (n) FOREACH (x IN [1] | SET n.k = x)",
    ],
)
def test_writes_are_detected(query):
    assert is_write_query(query) is True


def test_write_keyword_inside_a_string_literal_is_not_a_write():
    """The false positive that motivated a literal-aware detector.

    Neo4j's own ``_is_write_query`` substring-scans the raw query and refuses
    this one — a query HydraDB accepts and that mutates nothing (verified
    against the live API).
    """
    query = 'MATCH (p:Person) WHERE p.name = "CREATE something" RETURN p.name AS name'
    assert is_write_query(query) is False
    assert write_clauses_in(query) == []


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) RETURN n // TODO: also CREATE an index",
        "/* we used to MERGE here */ MATCH (n) RETURN n",
        "MATCH (n) RETURN n.`delete` AS d",
        "MATCH (n) WHERE n.note = 'please DELETE me' RETURN n",
    ],
)
def test_write_keywords_in_comments_and_identifiers_are_not_writes(query):
    assert is_write_query(query) is False


def test_a_decoy_literal_does_not_hide_a_real_write():
    """The literal must not blind the scan to the clause beside it."""
    query = 'MATCH (p:Person) WHERE p.note = "do not DELETE" SET p.seen = true'
    assert is_write_query(query) is True
    assert write_clauses_in(query) == ["SET"]


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) RETURN n.createdAt AS c",  # contains CREATE
        "MATCH (n) RETURN n.offset AS o",  # contains SET
        "MATCH (n) RETURN n.dropped AS d",  # contains DROP
    ],
)
def test_keywords_embedded_in_longer_words_are_not_matched(query):
    assert is_write_query(query) is False


def test_escaped_quote_does_not_end_a_literal_early():
    # If the escaped quote terminated the string, the DELETE after it would be
    # read as live code.
    assert is_write_query(r"MATCH (n) WHERE n.s = 'it\'s DELETE time' RETURN n") is False


def test_doubled_quote_does_not_end_a_literal_early():
    assert is_write_query("MATCH (n) WHERE n.s = 'a '' DELETE b' RETURN n") is False


def test_strip_preserves_length_so_offsets_stay_stable():
    query = 'MATCH (n) WHERE n.s = "CREATE" RETURN n'
    assert len(strip_non_code(query)) == len(query)


# ── rejected constructs ──────────────────────────────────────────────────────


def test_procedure_calls_are_named_but_subqueries_are_not():
    assert unsupported_construct("CALL db.labels()")
    assert unsupported_construct("CALL apoc.meta.schema()")
    assert unsupported_construct("CALL { MATCH (n) RETURN n AS x } RETURN x") is None
    assert unsupported_construct("CALL  { MATCH (n) RETURN n AS x } RETURN x") is None


def test_load_csv_is_named_with_the_alternative():
    message = unsupported_construct("LOAD CSV FROM 'file:///x.csv' AS row RETURN row")
    assert message is not None
    assert "parameters" in message or "params" in message


def test_procedure_name_inside_a_literal_is_not_a_procedure_call():
    assert unsupported_construct('MATCH (n) WHERE n.doc = "CALL db.labels()" RETURN n') is None


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
