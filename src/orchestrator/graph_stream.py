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

FOUR ADDITIONS BEYOND THE FROZEN BINARY SHAPE, all header-only (never touching the
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
  4. `link_type_class` -- index-aligned to `edge_types`, "structural"|"semantic" (as
     of THE PHYSICS LAYOUT, ruling d7d55257: "container"|"structural"|"semantic" --
     container is a flag nested inside the structural class, see
     src.ontology.link_classes.link_class's own docstring) per Thoth's THE READING
     LAYER dispatch (ruling c5953bb1) and src.ontology.link_classes (agreed with
     Seshat by DM, mail 10604, before this was committed). She reads the
     classification off the wire rather than hardcoding a second copy client-side --
     graph_layout.py's own relax pass reads the SAME table to decide which edges are
     allowed to pull two objects together (semantic only, never structural/membership).

NOTE ON THE RETIRED BIT (worth naming, not a bug): today's snapshot query only ever
includes objects the layout heartbeat has actually placed, which itself only ever
places `status NOT IN ('archived','merged','retired')` objects (graph_layout.py) --
so bit0 reads 0 for every object this endpoint can currently return. The bit is
reserved, not wired to anything live yet, exactly the same population boundary every
other /graph endpoint already draws.

THE LEGIBILITY PASS, Khnum tip 2 (ruling e1cb9e3b, operator 2026-09-14 evening):
  5. `labels` -- an index-aligned array of short display strings (tip 2g), CHOSEN as a
     header table over a batched GET /graph/labels?ids= endpoint: every object in the
     snapshot needs a label on first paint regardless, so a table costs one field on a
     payload the client already fetches rather than a second N-object round trip (or a
     second endpoint's own pagination/caching story) for data with the exact same
     lifetime as the snapshot itself. Built by `_short_label`, matching Seshat's own
     tip 1c formatting rule exactly (Agent by handle, SoftwareProject by repo name,
     Person by name, else type + a short title, 40 chars hard-truncated with an
     ellipsis) so the two renderings never disagree.
  6. `project_aggregates` / `type_aggregates` (tip 2i) -- level-of-detail summaries,
     computed in Python from the SAME positions already resident in memory (no extra
     query): per project, and per (project, type), a centroid, member count, and a
     radius (the MAX member distance from that centroid -- a tight, exact bound, a
     documented choice over a percentile estimate). `type` here is genuinely the
     object's own TYPE, unlike placement (THE LEGIBILITY PASS's tip 2h killed type
     rings there) -- Seshat's own tip 3k LOD plan draws "type glyphs" at mid zoom, a
     display grouping independent of how objects are actually laid out.
  7. `cluster_edges` (tip 2j) -- one record per (project pair, link class) with a live
     edge count, for the far-zoom aggregated-edges view -- computed from the same
     edge_src/edge_dst/project_code arrays already built, no extra query.

DENSITY NOT DISCS (ruling 6f866d9d, tip (h), thread 2397dac3) plus two follow-ups the
operator flagged for "another day" (Thoth mail 10892's own thread, folded into the
same tip since all three ship together here):
  8. `edge_weight` -- a new index-aligned Float32 array parallel to edge_src/edge_dst/
     edge_type_code (added to `_ARRAY_ORDER`, a real wire-format change agreed with
     Seshat by DM before commit, mail 11014/11016). RAW, never a pre-normalised 0-1
     curve -- Seshat's own steer: she already log2-normalises client-side for
     cluster_edges' color-brightness mix, and raw keeps that flexibility rather than
     baking one curve into the wire. Flat 1.0 for every individual link for now: her
     own live tip (off Thoth mail 11011) retires "strongest N" edge filtering
     entirely -- every edge now draws at every zoom, alpha scaled by on-screen
     count -- so there is no live consumer asking this array to differentiate
     individual edges yet. The field exists on the wire because the dispatch asked
     for it and a future consumer may want it; `type_pair_edges`' own `count` below
     is Seshat's actual next real-weight consumer.
  9. `type_pair_edges` -- one record per ((project,type) bucket pair, link class) with
     a live count, the SAME shape as `cluster_edges` one level finer: cluster_edges is
     project-to-project only (same-project pairs excluded), this is bucket-to-bucket
     (same-BUCKET pairs excluded, but same-project-different-type IS included) --
     Thoth's own "the mid tier now draws no edges because the stream has no
     (project,type)-pair aggregate" follow-up (mail 10892's thread, second note).
     `count` doubles as this record's own weight (a live edge count is already a real
     ranking, unlike the individual-edge case `edge_weight` above exists to fix).
 10. THE NAMELESS-AGENT LABEL FIX (Thoth mail 10892's thread, first follow-up,
     ruling e1cb9e3b(c)): an Agent with no `handle` assertion used to fall through to
     `type_name + canonical` ("Agent agent:b5f0f4b4...") -- the exact "labels show
     ids" shape the whole legibility pass was meant to kill, just for the one type
     that can genuinely lack a name (an anonymous swarm session that never
     `claim_name`'d). Fixed per the ruling's own rule: lineage handle + generation
     when the Agent's OWN lineage currently holds a Seat (`seats.held_seat`, the same
     door orient()/mount() use), else -- THE AGENT IDENTITY FIX (operator ruling,
     grounds 9163b1c7): "<Patronym> <ROMAN> · <model short>" from the `patronym`
     assertion, the canonical's own generation suffix, and a short-formed
     `source_model`, a trailing " ⌊ sub" marker for a sidechain fork
     (`is_sidechain`) -- superseding an earlier "Agent · <model> in <project>" whose
     own `model` lookup came back empty for 7,618 live osiris agents, rendering
     "Agent · ? in osiris". Never the raw id either way -- a patronym-less agent
     (disclosed as possible, not observed) falls back to its own canonical short
     id rather than a bare "?". Resolved for the SMALL handle-less-Agent subset
     only (Thoth's own live count: 49,712 of 49,766 labels already read as
     titles), not a query added to every row.
"""
from __future__ import annotations

import json
import math
import struct
import uuid
from collections import defaultdict
from typing import Any

import asyncpg

from src.ontology.link_classes import link_class
from src.orchestrator.capture import CONTESTED_SQL

SCHEMA_VERSION = 1
STATUS_RETIRED = 1 << 0
STATUS_CONTESTED = 1 << 1
_LABEL_MAX_CHARS = 40

def _short_label(
    type_name: str, canonical: str, handle: str | None, name: str | None,
    title: str | None, *, agent_fallback: str | None = None,
) -> str:
    """THE LEGIBILITY PASS (ruling e1cb9e3b, tip 2g/2c): the client never guesses a
    label. Agent by handle (never the id), SoftwareProject by its own repo name
    (canonical minus the 'repo:' scheme), Person by name, everything else its own
    type plus a short TITLE -- `title` is the object's own current summary/title/
    subject/name assertion (whichever wins by confidence then recency, one query --
    see fetch_snapshot's own correlated subquery), never canonical, for any type
    that actually carries one (Decision/Thread/Message/Commit and friends). Fixed
    live (Thoth mail 10892): the first cut of this function fell through to
    canonical for every type outside Agent/SoftwareProject/Person, so a Decision
    read "Decision decision:91da77..." on the deployed space -- the operator's own
    original "labels show ids" complaint in a new coat. Canonical is the LAST
    resort now, only when no title-shaped assertion exists at all. One line (any
    embedded newline/whitespace run collapsed to a single space -- a Decision's own
    summary is often multi-line prose), hard-truncated at 40 characters with an
    ellipsis, matching Seshat's own tip 1c formatting rule exactly so the two
    renderings never disagree.

    `agent_fallback` (THE NAMELESS-AGENT LABEL FIX, ruling e1cb9e3b(c)): a caller-
    resolved "lineage handle + generation" or "Agent · <model> in <project>" string
    for a handle-less Agent (see `fetch_snapshot`'s own resolution -- a Seat lookup
    and a couple of assertion reads this pure function has no pool to make itself).
    Tried BEFORE the generic title/canonical fallback chain so a nameless Agent never
    reads its own summary/subject assertion (it wouldn't have one) or, worse,
    canonical -- the exact id-shaped label this whole rule exists to kill."""
    if type_name == "Agent" and handle:
        label = handle
    elif type_name == "Agent" and agent_fallback:
        label = agent_fallback
    elif type_name == "SoftwareProject":
        label = canonical.removeprefix("repo:") or canonical
    elif type_name == "Person" and name:
        label = name
    elif title:
        label = f"{type_name} {' '.join(title.split())}"
    else:
        label = f"{type_name} {canonical}"
    if len(label) > _LABEL_MAX_CHARS:
        label = label[:_LABEL_MAX_CHARS - 1] + "…"
    return label


def _centroid_and_radius(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """A group's own centroid plus the MAX member distance from it -- a tight, exact
    bound (never a percentile estimate) so a client-drawn LOD circle always fully
    contains every real member, per tip 2i."""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    radius = max(math.hypot(px - cx, py - cy) for px, py in points)
    return cx, cy, radius


# name -> struct format code; order here IS the wire order.
_ARRAY_ORDER: tuple[tuple[str, str], ...] = (
    ("x", "f"), ("y", "f"),
    ("type_code", "H"), ("project_code", "H"),
    ("weight", "f"), ("status_flag", "B"),
    ("edge_src", "I"), ("edge_dst", "I"), ("edge_type_code", "B"), ("edge_weight", "f"),
)


def encode_snapshot(
    *,
    object_ids: list[str], x: list[float], y: list[float],
    type_code: list[int], project_code: list[int], weight: list[float],
    status_flag: list[int], edge_src: list[int], edge_dst: list[int],
    edge_type_code: list[int], edge_weight: list[float], types: list[str],
    projects: list[str], edge_types: list[str], link_type_class: list[str],
    labels: list[str],
    watermark: int = 0,
    project_aggregates: list[dict[str, Any]] | None = None,
    type_aggregates: list[dict[str, Any]] | None = None,
    cluster_edges: list[dict[str, Any]] | None = None,
    type_pair_edges: list[dict[str, Any]] | None = None,
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
        "edge_weight": edge_weight,
    }
    for name in ("x", "y", "type_code", "project_code", "weight", "status_flag"):
        if len(arrays[name]) != count:
            raise ValueError(
                f"{name} has {len(arrays[name])} entries, expected {count} (count)")
    for name in ("edge_src", "edge_dst", "edge_type_code", "edge_weight"):
        if len(arrays[name]) != edge_count:
            raise ValueError(
                f"{name} has {len(arrays[name])} entries, expected {edge_count} "
                f"(edge_count)")
    if len(link_type_class) != len(edge_types):
        raise ValueError(
            f"link_type_class has {len(link_type_class)} entries, expected "
            f"{len(edge_types)} (len(edge_types))")
    if len(labels) != count:
        raise ValueError(f"labels has {len(labels)} entries, expected {count} (count)")
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
        "link_type_class": link_type_class, "object_ids": object_ids,
        "labels": labels, "watermark": watermark,
        "project_aggregates": project_aggregates or [],
        "type_aggregates": type_aggregates or [],
        "cluster_edges": cluster_edges or [],
        "type_pair_edges": type_pair_edges or [],
        "arrays": offsets,
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
         "link_type_class", "object_ids", "labels", "watermark",
         "project_aggregates", "type_aggregates", "cluster_edges", "type_pair_edges")
    }
    for name, meta in header["arrays"].items():
        code = str(meta["dtype"])
        n = int(meta["length"])
        off = int(meta["offset"])
        out[name] = list(struct.unpack_from(f"<{n}{code}", body, off))
    return out


def _model_short(model: str) -> str:
    """claude-sonnet-5 -> sonnet-5 -- the vendor prefix carries no information a
    reader of THIS house's own labels needs; every model source_model ever
    asserts here is a Claude one, so a fixed prefix strip is exact, not a guess."""
    return model.removeprefix("claude-")


async def _nameless_agent_fallbacks(
    pool: asyncpg.Pool, rows: list[asyncpg.Record],
) -> dict[uuid.UUID, str]:
    """THE NAMELESS-AGENT LABEL FIX (ruling e1cb9e3b(c), corrected per operator
    ruling grounds 9163b1c7): resolved ONLY for the small handle-less-Agent subset
    of `rows` -- a per-id Seat lookup plus a couple of assertion reads this module
    has no reason to pay for every ordinary, already-named object.

    Lineage handle + generation when the Agent's OWN lineage currently holds a
    Seat (unchanged) -- that's the recognisable, load-bearing name a reader
    actually wants for a live seat-holder. Otherwise (THE AGENT IDENTITY FIX,
    live count: 7,618 osiris agents were rendering "Agent · ? in osiris" because
    the OLD fallback's own `model` lookup came back empty for this subset and
    `patronym`, which DOES carry a real name for almost all of them, was never
    read): "<Patronym> <ROMAN> · <model short>" built from the `patronym`
    assertion, the canonical's own generation suffix (`_generation`/`_to_roman`,
    the SAME parse the seat-branch above already trusts), and `source_model`
    short-formed (`_model_short`) -- a sidechain fork (`is_sidechain`) gets a
    trailing " ⌊ sub" marker on top of that same label, disclosure not
    suppression (mailbox.py's own is_sidechain disclosure rule, obligation
    706c27dc). NEVER "?": a patronym-less agent (no live count above zero at
    last check, but disclosed rather than assumed impossible) falls back to its
    own canonical short id -- still a real, resolvable name, never a raw "?"."""
    nameless = [r for r in rows if r["type"] == "Agent" and not r["handle"]]
    if not nameless:
        return {}
    from src.orchestrator.agents import _generation, _to_roman
    from src.orchestrator.seats import held_seat

    ids = [r["id"] for r in nameless]
    detail_rows = await pool.fetch(
        "SELECT o.id, "
        "  (SELECT a.value #>> '{}' FROM current_assertions a WHERE a.object_id=o.id "
        "   AND a.name='source_model' "
        "   ORDER BY a.confidence DESC, a.observed_at DESC LIMIT 1) AS model, "
        "  (SELECT a.value #>> '{}' FROM current_assertions a WHERE a.object_id=o.id "
        "   AND a.name='patronym' "
        "   ORDER BY a.confidence DESC, a.observed_at DESC LIMIT 1) AS patronym, "
        "  (SELECT a.value #>> '{}' FROM current_assertions a WHERE a.object_id=o.id "
        "   AND a.name='seat_generation' "
        "   ORDER BY a.confidence DESC, a.observed_at DESC LIMIT 1) AS seat_generation, "
        "  EXISTS(SELECT 1 FROM current_assertions a WHERE a.object_id=o.id "
        "   AND a.name='is_sidechain' AND a.value #>> '{}' = 'true') AS is_sidechain "
        "FROM objects o WHERE o.id = ANY($1::uuid[])", ids)
    detail_by_id = {r["id"]: r for r in detail_rows}

    out: dict[uuid.UUID, str] = {}
    for r in nameless:
        oid, canonical = r["id"], r["canonical"]
        seat = await held_seat(pool, canonical)
        if seat and seat.get("handle"):
            # THE SEAT-GENERATION FIX (Thoth mail 11309, w306 review): a
            # seat-succession canonical (agent:seat-<id>-g<N>) stamps its own
            # ordinal as a `seat_generation` ASSERTION (`agents.seat_label`'s
            # own authoritative source), never encoded in the canonical's own
            # roman/g-suffix string the way an ordinary lineage id is --
            # `_generation(canonical)` silently returned 1 for any such id
            # below the "-g40" numeric-overflow threshold (a "-g3" or "-g45"
            # canonical parses as a brand-new root, not a real generation),
            # dropping the numeral and printing a bare handle. Read the real
            # assertion first, falling back to the canonical parse only when
            # it's genuinely absent (an agent claimed before the seat ruling,
            # `seat_label`'s own documented fallback case).
            detail = detail_by_id.get(oid)
            seat_gen_raw = detail["seat_generation"] if detail else None
            gen = int(seat_gen_raw) if seat_gen_raw else _generation(canonical)[1]
            # UPPERCASE (Thoth mail 11283: "unify the roman case... 'Thoth CVI'
            # and 'Sekhmet XXXVIII' is how the fleet already writes them") -- was
            # lowercase `_to_roman`'s own raw output; the fleet's own mail/seat
            # displays already write these uppercase, so this was the label
            # scheme's own outlier, not the other way around.
            out[oid] = (
                f"{seat['handle']} {_to_roman(gen).upper()}" if gen > 1 else seat["handle"])
            continue
        detail = detail_by_id.get(oid)
        patronym = detail["patronym"] if detail else None
        if not patronym:
            out[oid] = canonical.removeprefix("agent:")
            continue
        gen = _generation(canonical)[1]
        label = f"{patronym} {_to_roman(gen).upper()}" if gen > 1 else patronym
        model = detail["model"] if detail else None
        if model:
            label = f"{label} · {_model_short(model)}"
        if detail and detail["is_sidechain"]:
            label = f"{label} ⌊ sub"
        out[oid] = label
    return out


async def fetch_snapshot(pool: asyncpg.Pool) -> bytes:
    """The live query: every already-placed object's position/type/project/weight/
    status, plus every live edge between two objects both in that set -- encoded via
    `encode_snapshot`. Row order is `o.created_at ASC, o.id ASC` (stable within one
    call) but is NOT persisted as a registry anywhere -- see the module docstring for
    why deltas key on object id rather than this order.

    THE DELTA CURSOR FIX (thread fa3a4d42, Thoth mail 10664): the header carries the
    OUTBOX WATERMARK this snapshot was built at, read FIRST -- before the position/
    edge queries below -- so an event landing mid-snapshot is still covered by the
    main query (its row is simply included) rather than falling into the gap between
    "already in the snapshot" and "cursor starts after it." The one-sided risk this
    ordering accepts is a rare harmless replay (the client re-applies a delta whose
    effect the snapshot already carried), never a silent drop."""
    watermark = int(await pool.fetchval("SELECT COALESCE(max(id), 0) FROM outbox"))
    rows = await pool.fetch(
        "SELECT o.id, o.type, o.canonical, "
        "  (SELECT (a.value #>> '{}')::float8 FROM current_assertions a "
        "   WHERE a.object_id=o.id AND a.name='graph_x') AS x, "
        "  (SELECT (a.value #>> '{}')::float8 FROM current_assertions a "
        "   WHERE a.object_id=o.id AND a.name='graph_y') AS y, "
        "  (SELECT a.value #>> '{}' FROM current_assertions a "
        "   WHERE a.object_id=o.id AND a.name='handle' "
        "   ORDER BY a.confidence DESC, a.observed_at DESC LIMIT 1) AS handle, "
        "  (SELECT a.value #>> '{}' FROM current_assertions a "
        "   WHERE a.object_id=o.id AND a.name='name' "
        "   ORDER BY a.confidence DESC, a.observed_at DESC LIMIT 1) AS name, "
        "  (SELECT a.value #>> '{}' FROM current_assertions a "
        "   WHERE a.object_id=o.id AND a.name IN ('summary','title','subject','name') "
        "   ORDER BY a.confidence DESC, a.observed_at DESC LIMIT 1) AS title, "
        "  COALESCE(p.canonical, ap.canonical) AS project_canonical, "
        f"  {CONTESTED_SQL} AS contested "
        "FROM objects o "
        "LEFT JOIN links l ON l.from_id=o.id AND l.type='in_repo' "
        "  AND (l.valid_until IS NULL OR l.valid_until > now()) "
        "LEFT JOIN objects p ON p.id=l.to_id AND p.type='SoftwareProject' "
        "LEFT JOIN current_assertions pa ON pa.object_id=o.id AND pa.name='project' "
        "LEFT JOIN objects ap ON ap.type='SoftwareProject' "
        "  AND ap.canonical = 'repo:' || (pa.value #>> '{}') "
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
    agent_fallback_by_id = await _nameless_agent_fallbacks(pool, rows)

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
    labels: list[str] = []

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
        labels.append(_short_label(
            r["type"], r["canonical"], r["handle"], r["name"], r["title"],
            agent_fallback=agent_fallback_by_id.get(oid)))

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

    # DENSITY NOT DISCS tip (h), item 8: a flat per-edge weight -- RAW, not a
    # pre-normalised 0-1 curve (Seshat, mail 11016: she already log2-normalises
    # client-side for cluster_edges' own color-brightness mix, and raw keeps that
    # flexibility rather than baking one curve into the wire). 1.0 for every
    # individual link for now -- her own live tip off Thoth mail 11011 retires
    # "strongest N" filtering entirely (every edge draws at every zoom, alpha
    # scaled by on-screen count instead), so there is no live consumer asking this
    # array to differentiate individual edges yet; a real per-edge count/weight is
    # exactly what `type_pair_edges`' own `count` field already provides one level
    # up, which she names as her actual next consumer.
    edge_weights: list[float] = [1.0 for _ in edge_src]

    types = [name for name, _ in sorted(type_index.items(), key=lambda kv: kv[1])]
    projects = [name for name, _ in sorted(project_index.items(), key=lambda kv: kv[1])]
    edge_types = [name for name, _ in sorted(edge_type_index.items(), key=lambda kv: kv[1])]
    link_type_class = [link_class(name) for name in edge_types]

    # THE LEGIBILITY PASS, tip 2i: LOD aggregates, computed from the positions already
    # resident above -- no extra query.
    proj_points: dict[int, list[tuple[float, float]]] = defaultdict(list)
    type_points: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for i in range(len(object_ids)):
        pt = (xs[i], ys[i])
        proj_points[project_codes[i]].append(pt)
        type_points[(project_codes[i], type_codes[i])].append(pt)
    project_aggregates = []
    for pcode, pts in proj_points.items():
        cx, cy, radius = _centroid_and_radius(pts)
        project_aggregates.append({
            "project": pcode, "count": len(pts),
            "cx": round(cx, 2), "cy": round(cy, 2), "radius": round(radius, 2),
        })
    type_aggregates = []
    for (pcode, tcode), pts in type_points.items():
        cx, cy, radius = _centroid_and_radius(pts)
        type_aggregates.append({
            "project": pcode, "type": tcode, "count": len(pts),
            "cx": round(cx, 2), "cy": round(cy, 2), "radius": round(radius, 2),
        })

    # THE LEGIBILITY PASS, tip 2j: one record per (project pair, link class), same
    # edge/project arrays already built -- no extra query.
    cluster_counts: dict[tuple[int, int, str], int] = defaultdict(int)
    for i in range(len(edge_src)):
        p1, p2 = project_codes[edge_src[i]], project_codes[edge_dst[i]]
        if p1 == p2:
            continue
        a, b = (p1, p2) if p1 <= p2 else (p2, p1)
        cluster_counts[(a, b, link_type_class[edge_type_codes[i]])] += 1
    cluster_edges = [
        {"a": a, "b": b, "class": cls, "count": n}
        for (a, b, cls), n in cluster_counts.items()
    ]

    # DENSITY NOT DISCS tip (h), item 9: one record per (project,type)-bucket pair,
    # the SAME shape one level finer -- same-BUCKET pairs excluded (a self-loop has
    # nothing to draw a mid-tier line between), but a same-project different-type
    # pair IS included (unlike cluster_edges' cross-project-only rule), since the
    # mid tier needs edges BETWEEN type clusters within one project too.
    type_pair_counts: dict[tuple[tuple[int, int], tuple[int, int], str], int] = defaultdict(int)
    for i in range(len(edge_src)):
        b1 = (project_codes[edge_src[i]], type_codes[edge_src[i]])
        b2 = (project_codes[edge_dst[i]], type_codes[edge_dst[i]])
        if b1 == b2:
            continue
        bucket_a, bucket_b = (b1, b2) if b1 <= b2 else (b2, b1)
        type_pair_counts[(bucket_a, bucket_b, link_type_class[edge_type_codes[i]])] += 1
    type_pair_edges = [
        {"a": {"project": bucket_a[0], "type": bucket_a[1]},
         "b": {"project": bucket_b[0], "type": bucket_b[1]},
         "class": cls, "count": n}
        for (bucket_a, bucket_b, cls), n in type_pair_counts.items()
    ]

    return encode_snapshot(
        object_ids=object_ids, x=xs, y=ys, type_code=type_codes,
        project_code=project_codes, weight=weights, status_flag=statuses,
        edge_src=edge_src, edge_dst=edge_dst, edge_type_code=edge_type_codes,
        edge_weight=edge_weights,
        types=types, projects=projects, edge_types=edge_types,
        link_type_class=link_type_class, labels=labels, watermark=watermark,
        project_aggregates=project_aggregates, type_aggregates=type_aggregates,
        cluster_edges=cluster_edges, type_pair_edges=type_pair_edges,
    )


async def outbox_watermark(pool: asyncpg.Pool) -> int:
    """The current tip of the outbox, right now -- the same read `fetch_snapshot` uses
    to stamp a snapshot's own watermark, exposed separately for a client that connects
    to `/graph/stream/deltas` with no cursor of its own (thread fa3a4d42): resolve to
    THIS, never 0, so a fresh connection with no prior snapshot starts listening from
    now instead of replaying the whole outbox."""
    return int(await pool.fetchval("SELECT COALESCE(max(id), 0) FROM outbox"))


async def resolve_deltas_start_cursor(
    pool: asyncpg.Pool, *, since: int | None, last_event_id: str | None,
) -> int:
    """THE DELTA CURSOR FIX (thread fa3a4d42, Thoth mail 10664): where a fresh
    `/graph/stream/deltas` connection starts, in order -- `since` (a client passing
    /graph/stream's own watermark), then the standard SSE `Last-Event-ID` reconnect
    header, then -- and only then -- `outbox_watermark`: "now", never the backlog.
    Pulled out of the route itself so this decision is directly unit-testable without
    standing up a real SSE connection."""
    if since is not None:
        return since
    if last_event_id is not None:
        try:
            return int(last_event_id)
        except ValueError:
            pass
    return await outbox_watermark(pool)


_DELTAS_PAGE_LIMIT = 500


async def deltas_since(
    pool: asyncpg.Pool, cursor: int, *, limit: int = _DELTAS_PAGE_LIMIT,
) -> tuple[list[dict[str, Any]], int]:
    """Poll the outbox table for graph-relevant events past `cursor` (an outbox id) --
    the SAME poll-and-diff idiom /cases/{id}/stream, /console/stream and /pane/{id}/
    stream already use (app.py), never a new push mechanism. Returns
    `(deltas, new_cursor)`; an empty list with an unchanged cursor means nothing
    graph-relevant happened since the last poll. Each delta is keyed by object id (a
    string) -- see the module docstring for why, not array index.

    THE DELTA CURSOR FIX (thread fa3a4d42, Thoth mail 10664): one page at a time
    (`limit`, never the whole backlog), coalesced to the LATEST event per object
    within that page (a live-state view only cares about an object's current
    position/status, not every intermediate event it passed through), positions
    resolved for the whole page with plain joins instead of two correlated subqueries
    PER ROW. `new_cursor` advances to the highest outbox id actually SEEN in the page
    -- including rows coalesced away -- so nothing already read is ever re-scanned,
    even though only the survivor per object is reported."""
    rows = await pool.fetch(
        "WITH raw AS ("
        "  SELECT o.id AS outbox_id, o.object_id, o.event_type "
        "  FROM outbox o "
        "  WHERE o.id > $1 AND o.event_type IN "
        "    ('object_created','property_added','object_merged') "
        "    AND o.object_id IS NOT NULL "
        "  ORDER BY o.id ASC "
        "  LIMIT $2"
        ") "
        "SELECT DISTINCT ON (r.object_id) "
        "  r.object_id, r.outbox_id, r.event_type, "
        "  (gx.value #>> '{}')::float8 AS x, (gy.value #>> '{}')::float8 AS y, "
        "  max(r.outbox_id) OVER () AS page_max "
        "FROM raw r "
        "LEFT JOIN current_assertions gx ON gx.object_id=r.object_id AND gx.name='graph_x' "
        "LEFT JOIN current_assertions gy ON gy.object_id=r.object_id AND gy.name='graph_y' "
        "ORDER BY r.object_id, r.outbox_id DESC",
        cursor, limit)
    if not rows:
        return [], cursor
    new_cursor = max(int(r["page_max"]) for r in rows)
    deltas: list[dict[str, Any]] = []
    for r in rows:
        op = "retired" if r["event_type"] == "object_merged" else "moved"
        delta: dict[str, Any] = {"op": op, "id": str(r["object_id"])}
        if r["x"] is not None and r["y"] is not None:
            delta["x"] = float(r["x"])
            delta["y"] = float(r["y"])
        deltas.append(delta)
    return deltas, new_cursor
