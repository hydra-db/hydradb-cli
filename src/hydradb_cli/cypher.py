"""Cypher analysis and rendering for the graph (BYOG) commands.

Three things that need no network, so they can be tested directly:

  * deciding whether a query WRITES, so ``--read-only`` can refuse one;
  * turning the server's row objects back into something readable.

Kept deliberately in step with the same logic in the MCP client
(``src/cypher.ts`` there): the two clients wrap identical endpoints and must not
disagree about what counts as a write.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Clauses that mutate the graph.
#
# ``ADD`` is deliberately absent even though Neo4j's own ``_is_write_query``
# lists it: it is not a Cypher write clause, only part of ``SET n:Label`` /
# ``ADD CONSTRAINT``, both already caught by a real member of this list.
# Including it would reject ``MATCH (n) WHERE n.tag = 'add' ...`` for no reason.
#
# ``CREATE INDEX`` / ``DROP INDEX`` count as writes: they are schema changes,
# they are billed against the 30s write budget, and a read-only caller has no
# business issuing them.
WRITE_CLAUSES = (
    "CREATE",
    "MERGE",
    "SET",
    "DELETE",
    "DETACH",
    "REMOVE",
    "DROP",
    "FOREACH",
    "LOAD",
)

# The documented request-body ceiling for POST /byog/query.
MAX_BODY_BYTES = 256 * 1024

# Collection names the server accepts.
#
# Anchored with ``\Z``, not ``$``: in Python ``$`` also matches immediately
# BEFORE a trailing newline, so ``"contacts\n"`` — which a scripted caller
# gets from `--collection "$(cat name.txt)"` or a here-doc — would pass
# validation and be sent verbatim. ``\Z`` is the true end of string.
COLLECTION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")

# A bare Cypher identifier — a label or a property name that can be written
# unquoted in a query.
#
# Deliberately NOT ``COLLECTION_PATTERN``: that one allows hyphens, which are
# legal in a collection name and illegal in an identifier. Reusing it let
# ``--label my-label`` through, and the server then rejected the generated
# query with "Invalid input '-': expected a label" — a confusing failure
# blamed on the file rather than on the flag.
#
# Cypher can quote an identifier with backticks, but a caller who needs that is
# better served writing their own MERGE than having one built for them.
CYPHER_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")


def strip_non_code(query: str) -> str:
    """Blank out everything that is not executable Cypher.

    This is the whole reason the detector is not a substring scan. Neo4j's MCP
    checks ``any(keyword in query.upper() for keyword in [...])``, so::

        MATCH (p:Person) WHERE p.name = "CREATE something" RETURN p.name

    is classified as a write and refused — a query HydraDB accepts and that
    mutates nothing. The same happens to a comment mentioning a write, and to a
    property literally named ``\\`delete\\```.

    Replacing with spaces rather than deleting keeps every offset stable, so a
    keyword sitting against a stripped region cannot be fused with its
    neighbour into a different token.

    Handles single- and double-quoted strings (with backslash and doubled
    escapes), backtick-quoted identifiers, ``//`` line comments and ``/* */``
    block comments.
    """
    out: list[str] = []
    i = 0
    length = len(query)

    while i < length:
        ch = query[i]
        nxt = query[i + 1] if i + 1 < length else ""

        # Line comment — runs to the newline, which is preserved.
        if ch == "/" and nxt == "/":
            while i < length and query[i] != "\n":
                out.append(" ")
                i += 1
            continue

        # Block comment. An unterminated one blanks the rest, which is correct:
        # the server rejects the query anyway, and everything after an unclosed
        # opener reads as a comment.
        if ch == "/" and nxt == "*":
            out.append("  ")
            i += 2
            while i < length and not (query[i] == "*" and i + 1 < length and query[i + 1] == "/"):
                out.append("\n" if query[i] == "\n" else " ")
                i += 1
            if i < length:
                out.append("  ")
                i += 2
            continue

        # Quoted region: string literal or backticked identifier.
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(" ")
            i += 1
            while i < length:
                # Backslash escape. Backticked identifiers escape by doubling
                # rather than with backslashes, but consuming the pair is still
                # safe there.
                if query[i] == "\\" and quote != "`":
                    out.append("  " if i + 1 < length else " ")
                    i += 2
                    continue
                if query[i] == quote:
                    # A doubled quote is an escaped quote, not a terminator.
                    if i + 1 < length and query[i + 1] == quote:
                        out.append("  ")
                        i += 2
                        continue
                    out.append(" ")
                    i += 1
                    break
                out.append("\n" if query[i] == "\n" else " ")
                i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def write_clauses_in(query: str) -> list[str]:
    """The write clauses a query actually uses, in the order they appear."""
    code = strip_non_code(query).upper()
    # ``\b`` on both sides so SET does not match OFFSET and CREATE does not
    # match a variable called createdAt.
    return [clause for clause in WRITE_CLAUSES if re.search(rf"\b{clause}\b", code)]


def is_write_query(query: str) -> bool:
    """Whether this query mutates the graph.

    Conservative in one direction only: it may call a read a write, and the
    cost of that is being told to drop ``--read-only``. The reverse — calling a
    write a read — must never happen, because that is what the guarantee rests
    on.
    """
    return bool(write_clauses_in(query))


def unsupported_construct(query: str) -> str | None:
    """Name a construct HydraDB rejects *before* running anything.

    Both of these fail with a 400 at validation time, so nothing executes
    either way. Catching them locally gives the reason and the alternative in
    one step, immediately, instead of a remote error to interpret — and
    retrying either unchanged fails identically.
    """
    code = strip_non_code(query)

    # ``CALL { ... }`` subqueries ARE supported; ``CALL some.procedure(...)`` is
    # not. The distinction is the token after CALL, not the bare keyword.
    call = re.search(r"\bCALL\s*(\{)?", code, re.IGNORECASE)
    if call and call.group(1) is None:
        return (
            "HydraDB rejects procedure calls (CALL db.*, CALL apoc.*) before running them. "
            "CALL { ... } subqueries are supported."
        )

    if re.search(r"\bLOAD\s+CSV\b", code, re.IGNORECASE):
        return (
            "HydraDB rejects LOAD CSV — it does not load files or URLs server-side. "
            "Pass rows through parameters instead, e.g. "
            "'UNWIND $rows AS row MERGE (n:Thing {id: row.id}) SET n += row', "
            "or use 'hydradb graph load'."
        )

    return None


def body_size(payload: dict[str, Any]) -> int:
    """Encoded size of a request body, in bytes.

    Bytes rather than characters: the cap is on the encoded body, and non-ASCII
    property values are exactly where a batch that looks small stops being it.
    """
    return len(json.dumps(payload, default=str).encode("utf-8"))


# Keys HydraDB's renderer adds to a returned node or relationship, as opposed to
# properties the user stored.
_NODE_KEYS = ("id", "labels")
_REL_KEYS = ("id", "relation", "source_node_id", "target_node_id")


def _is_node(value: Any) -> bool:
    return isinstance(value, dict) and "labels" in value and "id" in value


def _is_relationship(value: Any) -> bool:
    return isinstance(value, dict) and "relation" in value and "source_node_id" in value


def _is_path(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("nodes"), list) and isinstance(value.get("edges"), list)


def _inline(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _props(value: dict, reserved: tuple[str, ...]) -> str:
    items = [(k, v) for k, v in value.items() if k not in reserved]
    if not items:
        return ""
    return " {" + ", ".join(f"{k}: {_inline(v)}" for k, v in items) + "}"


def render_value(value: Any) -> str:
    """Render one returned value the way a graph user reads it.

    A node arrives as a flat object mixing its properties with the
    renderer-added ``id`` and ``labels``; dumping that as raw JSON leaves the
    reader to do the separating. This renders ``(:Person {name: Alice})``
    instead — shorter, and the notation the query was written in.
    """
    if _is_path(value):
        parts: list[str] = []
        nodes = value["nodes"]
        edges = value["edges"]
        for index, node in enumerate(nodes):
            parts.append(render_value(node))
            if index < len(edges):
                edge = edges[index]
                rel = edge.get("relation", "?") if isinstance(edge, dict) else "?"
                parts.append(f"-[:{rel}]->")
        return "".join(parts)

    if _is_node(value):
        labels = value.get("labels") or []
        label_str = "".join(f":{label}" for label in labels) if isinstance(labels, list) else ""
        return f"({label_str}{_props(value, _NODE_KEYS)})"

    if _is_relationship(value):
        return (
            f"[{value.get('source_node_id')}]-[:{value.get('relation')}"
            f"{_props(value, _REL_KEYS)}]->[{value.get('target_node_id')}]"
        )

    return _inline(value)


def rows_to_table(rows: list[dict[str, Any]]) -> tuple[list[str], list[list[str]]]:
    """Turn result rows into (headers, cells) for a Rich table.

    Columns are the union of every row's keys, in first-seen order, so a query
    whose rows differ in shape still shows every column rather than silently
    dropping one. A cell absent from a row renders as an em dash rather than
    being omitted, keeping columns aligned.
    """
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)

    cells = [[render_value(row[h]) if h in row else "—" for h in headers] for row in rows]
    return headers, cells
