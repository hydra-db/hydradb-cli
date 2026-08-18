"""Rendering and local limits for the graph (BYOG) commands.

Deliberately contains NO Cypher analysis. An earlier version lexed the query to
classify reads vs writes and to pre-reject constructs the server refuses, which
put a second, worse implementation of the server's rules inside a client: it
could only ever agree with the server or be wrong, and being wrong meant
refusing a query HydraDB would have run.

The server is the authority on what Cypher is valid and permitted. It rejects
unsupported constructs before executing anything — verified: a query mixing
CREATE with a procedure call leaves the node count unchanged — and its messages
are more specific than the ones this module used to produce.

What is left is the work a client genuinely owns: turning the server's row
objects into something readable, and the limits and name rules that are cheaper
to check here than to discover from a remote error.

Kept in step with the MCP's ``src/cypher.ts``, which was stripped the same way:
the two clients wrap identical endpoints and must not diverge (CONTRACT §0,
group 2).
"""

from __future__ import annotations

import json
import re
from typing import Any

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
