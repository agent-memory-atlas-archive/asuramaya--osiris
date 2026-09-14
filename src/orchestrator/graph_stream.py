"""NAVIGABLE SPACE, THE SERVER, piece B (rulings f832c3a4 + 0a3d6719, thread
b6cb1d7c0b36): one endpoint streaming the WHOLE graph as typed arrays, shape agreed by
DM with Seshat (mail 10439/10449/10451) before it was frozen -- she is the sole
consumer, building the three.js renderer against it.

WIRE FORMAT: a 4-byte little-endian uint32 header length, that many bytes of UTF-8 JSON
header, then the raw arrays concatenated back to back in the order the header's own
`arrays` map names them (see `_ARRAY_ORDER`): x/y (Float32), type_code/project_code
(Uint16), weight (Float32), status_flag (Uint8, a bitmask -- bit0 reserved for a
retired/superseded state, bit1 = CONTESTED per capture.py's own shared CONTESTED_SQL,
the rest reserved), edge_src/edge_dst (Uint32), edge_type_code (Uint8). Array POSITION
is the object index -- no redundant index column, per Seshat's own read of the proposal
(mail 10449).

THREE ADDITIONS BEYOND THE FROZEN BINARY SHAPE, all header-only (never touching the
arrays Seshat signed off on), flagged here and in the build report rather than done
quietly:
  1. `object_ids` -- an index-aligned array of UUID strings in the JSON header. Needed
     so a consumer (or this module's own future delta poller) can resolve an array
     index back to a real object id; the frozen binary arrays deliberately carry none.
  2. Deltas key on object id (a string), not on array index. Building a persisted,
     cross-request index-assignment registry is out of scope for a spike-stage
     renderer that does not exist yet; a client already holds the snapshot's own
     `object_ids` array and can resolve a delta's id to ITS OWN local index in O(1)
     either way. This is a documented departure from the dispatch's literal "keyed
     by object index" wording, not a silent one.
  3. `edge_types` -- a code table for `edge_type_code`, same shape as `types`/
     `projects`. A gap found by Seshat wiring against the live endpoint (mail 10555):
     the frozen shape resolved node type/project codes but left `edge_type_code`
     nameless, which she needs for real relationship color-coding rather than a raw
     hash-on-int placeholder.

NOTE ON THE RETIRED BIT (worth naming, not a bug): today's snapshot query only ever
includes objects the layout heartbeat has actually placed, which itself only ever
places `status NOT IN ('archived','merged','retired')` objects (graph_layout.py) --
so bit0 reads 0 for every object this endpoint can currently return. The bit is
reserved, not wired to anything live yet, exactly the same population boundary every
other /graph endpoint already draws.
"""
from __future__ import annotations

import json
import struct
import uuid
from typing import Any

import asyncpg

from src.orchestrator.capture import CONTESTED_SQL

SCHEMA_VERSION = 1
STATUS_RETIRED = 1 << 0
STATUS_CONTESTED = 1 << 1

# name -> struct format code; order here IS the wire order.
_ARRAY_ORDER: tuple[tuple[str, str], ...] = (
    ("x", "f"), ("y", "f"),
    ("type_code", "H"), ("project_code", "H"),
    ("weight", "f"), ("status_flag", "B"),
    ("edge_src", "I"), ("edge_dst", "I"), ("edge_type_code", "B"),
)


def encode_snapshot(
    *,
    object_ids: list[str], x: list[float], y: list[float],
    type_code: list[int], project_code: list[int], weight: list[float],
    status_flag: list[int], edge_src: list[int], edge_dst: list[int],
    edge_type_code: list[int], types: list[str], projects: list[str],
    edge_types: list[str],
) -> bytes:
    """Pure function, no DB: builds the exact wire bytes from already-resolved
    columns -- the DB-facing half (`fetch_snapshot`) is the only caller that ever
    needs a live pool, so this half is fully unit-testable on its own."""
    count = len(object_ids)
    edge_count = len(edge_src)
    arrays: dict[str, list[Any]] = {
        "x": x, "y": y, "type_code": type_code, "project_code": project_code,
        "weight": weight, "status_flag": status_flag,
        "edge_src": edge_src, "edge_dst": edge_dst, "edge_type_code": edge_type_code,
    }
    for name in ("x", "y", "type_code", "project_code", "weight", "status_flag"):
        if len(arrays[name]) != count:
            raise ValueError(
                f"{name} has {len(arrays[name])} entries, expected {count} (count)")
    for name in ("edge_src", "edge_dst", "edge_type_code"):
        if len(arrays[name]) != edge_count:
            raise ValueError(
                f"{name} has {len(arrays[name])} entries, expected {edge_count} "
                f"(edge_count)")
    body = bytearray()
    offsets: dict[str, dict[str, int | str]] = {}
    for name, code in _ARRAY_ORDER:
        col = arrays[name]
        packed = struct.pack(f"<{len(col)}{code}", *col)
        offsets[name] = {"offset": len(body), "length": len(col), "dtype": code}
        body.extend(packed)

    header = {
        "schema_version": SCHEMA_VERSION, "count": count, "edge_count": edge_count,
        "types": types, "projects": projects, "edge_types": edge_types,
        "object_ids": object_ids, "arrays": offsets,
    }
    header_bytes = json.dumps(header).encode()
    return struct.pack("<I", len(header_bytes)) + header_bytes + bytes(body)


def decode_snapshot(data: bytes) -> dict[str, Any]:
    """The reference decoder -- a Python-side twin of what a JS client does, used by
    this module's own round-trip test and by the CLI mirror's summary line. A real
    renderer works straight off the typed-array offsets in the header instead."""
    (header_len,) = struct.unpack_from("<I", data, 0)
    header = json.loads(bytes(data[4:4 + header_len]).decode())
    body = data[4 + header_len:]
    out: dict[str, Any] = {
        k: header[k] for k in
        ("schema_version", "count", "edge_count", "types", "projects", "edge_types",
         "object_ids")
    }
    for name, meta in header["arrays"].items():
        code = str(meta["dtype"])
        n = int(meta["length"])
        off = int(meta["offset"])
        out[name] = list(struct.unpack_from(f"<{n}{code}", body, off))
    return out


async def fetch_snapshot(pool: asyncpg.Pool) -> bytes:
    """The live query: every already-placed object's position/type/project/weight/
    status, plus every live edge between two objects both in that set -- encoded via
    `encode_snapshot`. Row order is `o.created_at ASC, o.id ASC` (stable within one
    call) but is NOT persisted as a registry anywhere -- see the module docstring for
    why deltas key on object id rather than this order."""
    rows = await pool.fetch(
        "SELECT o.id, o.type, "
        "  (SELECT (a.value #>> '{}')::float8 FROM current_assertions a "
        "   WHERE a.object_id=o.id AND a.name='graph_x') AS x, "
        "  (SELECT (a.value #>> '{}')::float8 FROM current_assertions a "
        "   WHERE a.object_id=o.id AND a.name='graph_y') AS y, "
        "  p.canonical AS project_canonical, "
        f"  {CONTESTED_SQL} AS contested "
        "FROM objects o "
        "LEFT JOIN links l ON l.from_id=o.id AND l.type='in_repo' "
        "  AND (l.valid_until IS NULL OR l.valid_until > now()) "
        "LEFT JOIN objects p ON p.id=l.to_id AND p.type='SoftwareProject' "
        "WHERE o.status NOT IN ('archived','merged','retired') "
        "  AND EXISTS (SELECT 1 FROM current_assertions a "
        "    WHERE a.object_id=o.id AND a.name='graph_x') "
        "ORDER BY o.created_at ASC, o.id ASC")
    weight_rows = await pool.fetch(
        "SELECT node, count(*) AS n FROM ("
        "  SELECT from_id AS node FROM links "
        "    WHERE valid_until IS NULL OR valid_until > now() "
        "  UNION ALL "
        "  SELECT to_id AS node FROM links "
        "    WHERE valid_until IS NULL OR valid_until > now()"
        ") x GROUP BY node")
    weight_by_id: dict[uuid.UUID, int] = {r["node"]: int(r["n"]) for r in weight_rows}

    type_index: dict[str, int] = {}
    project_index: dict[str, int] = {}
    id_index: dict[uuid.UUID, int] = {}
    object_ids: list[str] = []
    xs: list[float] = []
    ys: list[float] = []
    type_codes: list[int] = []
    project_codes: list[int] = []
    weights: list[float] = []
    statuses: list[int] = []

    for i, r in enumerate(rows):
        oid = r["id"]
        id_index[oid] = i
        object_ids.append(str(oid))
        xs.append(float(r["x"] or 0.0))
        ys.append(float(r["y"] or 0.0))
        type_codes.append(type_index.setdefault(r["type"], len(type_index)))
        project_key = r["project_canonical"] or "unfiled"
        project_codes.append(project_index.setdefault(project_key, len(project_index)))
        weights.append(float(weight_by_id.get(oid, 0)))
        statuses.append(STATUS_CONTESTED if r["contested"] else 0)

    edge_src: list[int] = []
    edge_dst: list[int] = []
    edge_type_codes: list[int] = []
    edge_type_index: dict[str, int] = {}
    if id_index:
        edge_rows = await pool.fetch(
            "SELECT from_id, to_id, type FROM links "
            "WHERE (valid_until IS NULL OR valid_until > now()) "
            "  AND from_id = ANY($1::uuid[]) AND to_id = ANY($1::uuid[])",
            list(id_index.keys()))
        for r in edge_rows:
            f, t = r["from_id"], r["to_id"]
            if f not in id_index or t not in id_index:
                continue
            edge_src.append(id_index[f])
            edge_dst.append(id_index[t])
            edge_type_codes.append(
                edge_type_index.setdefault(r["type"], len(edge_type_index)))

    types = [name for name, _ in sorted(type_index.items(), key=lambda kv: kv[1])]
    projects = [name for name, _ in sorted(project_index.items(), key=lambda kv: kv[1])]
    edge_types = [name for name, _ in sorted(edge_type_index.items(), key=lambda kv: kv[1])]
    return encode_snapshot(
        object_ids=object_ids, x=xs, y=ys, type_code=type_codes,
        project_code=project_codes, weight=weights, status_flag=statuses,
        edge_src=edge_src, edge_dst=edge_dst, edge_type_code=edge_type_codes,
        types=types, projects=projects, edge_types=edge_types,
    )


async def deltas_since(pool: asyncpg.Pool, cursor: int) -> tuple[list[dict[str, Any]], int]:
    """Poll the outbox table for graph-relevant events past `cursor` (an outbox id,
    0 on a fresh connection) -- the SAME poll-and-diff idiom /cases/{id}/stream,
    /console/stream and /pane/{id}/stream already use (app.py), never a new push
    mechanism. Returns `(deltas, new_cursor)`; an empty list with an unchanged cursor
    means nothing graph-relevant happened since the last poll. Each delta is keyed by
    object id (a string) -- see the module docstring for why, not array index."""
    rows = await pool.fetch(
        "SELECT o.id AS outbox_id, o.object_id, o.event_type, "
        "  (SELECT (a.value #>> '{}')::float8 FROM current_assertions a "
        "   WHERE a.object_id=o.object_id AND a.name='graph_x') AS x, "
        "  (SELECT (a.value #>> '{}')::float8 FROM current_assertions a "
        "   WHERE a.object_id=o.object_id AND a.name='graph_y') AS y "
        "FROM outbox o "
        "WHERE o.id > $1 AND o.event_type IN "
        "  ('object_created','property_added','object_merged') "
        "  AND o.object_id IS NOT NULL "
        "ORDER BY o.id ASC",
        cursor)
    if not rows:
        return [], cursor
    deltas: list[dict[str, Any]] = []
    new_cursor = cursor
    for r in rows:
        new_cursor = max(new_cursor, r["outbox_id"])
        op = "retired" if r["event_type"] == "object_merged" else "moved"
        delta: dict[str, Any] = {"op": op, "id": str(r["object_id"])}
        if r["x"] is not None and r["y"] is not None:
            delta["x"] = float(r["x"])
            delta["y"] = float(r["y"])
        deltas.append(delta)
    return deltas, new_cursor
