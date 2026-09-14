"""THE GRAPH VISUALIZER (wave B item 1, thread 8839) -- extended for NAVIGABLE SPACE, THE
SERVER, piece A (rulings f832c3a4 + 0a3d6719, operator 2026-09-14, thread b6cb1d7c0b36):
server-side placement over the WHOLE graph, run incrementally by the heartbeat -- positions
stored as current_assertions (graph_x/graph_y), never computed live in the browser or the
renderer. This module feeds the /graph endpoints (supernodes/clusters/viewport) and, from
piece B on, the whole-graph typed-array stream.

THE PLACEMENT RULE (operator's own shape, not a force-layout guess): every project gets its
own center, spread deterministically over the plane by a sunflower/Fermat spiral keyed on a
stable hash of the project's OWN canonical -- never that project's rank among its peers, so
adding or removing an unrelated project never moves this one. Every object TYPE gets a ring
around its project's center, at a radius fixed by the type's own declared position in
schema.py's object-type catalog (a stable, code-defined order every project reuses
identically). Every object sits on its type's ring at an angle from a stable hash of its OWN
id -- never a grid, never dependent on how many siblings share that ring or the order they
were discovered in. All three of those are PURE functions of (project canonical, type name,
object id) alone: recomputing any of them, for any object, at any time, reproduces the exact
same point -- which is what makes "place every object, incrementally, idempotently" survive
being spread over many ticks rather than one global pass.

A SHORT, BOUNDED relax pass then pulls each tick's newly-placed batch toward its already-
placed same-PROJECT neighbors (never cross-project -- ruling's own "edge attraction within a
project"), starting FROM the ring/hash base position rather than a random seed, few
iterations, anchors (already-placed neighbors) held fixed. This is the same vectorized numpy
Fruchterman-Reingold `relax()` wave B item 1 already used for its own local relax -- reused
here as a nudge on top of a deterministic base, not the sole placement rule it used to be.

INCREMENTAL, NEVER REVISITED: a tick only ever considers objects still missing the
`graph_layout_v` marker (bumped whenever this module's placement RULE itself changes, not
its per-tick progress) -- so a re-run over already-placed objects moves nothing, and a
version bump alone is what causes the ONE migration pass over objects placed under wave B's
prior (pure force-relax) scheme; nothing else about this file's contract to arq_worker's cron
(`layout_batch(actions, limit=...) -> int`, 0 meaning fully placed) changed.

WRITE PATH: positions are still recorded as ordinary property assertions (graph_x, graph_y,
graph_layout_v), append-only and superseding exactly like every other assertion in this
system. The one difference from the general-purpose assert_property path: this module
covers a whole tick's batch with one multi-row statement per property instead of one call
per object, because GRAPH_LAYOUT_SOURCE is this triple's only ever writer (documented on
thread b6cb1d7c0b36's scope note before this was written) -- a full pass at ~41k objects
through the general per-object path would be tens of thousands of sequential round trips.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import UTC, datetime

import numpy as np

from src.actions.core import Actions
from src.ontology.schema import _OBJECT_TYPES

GRAPH_LAYOUT_SOURCE = "cron:graph_layout"
_BATCH_SIZE = 1000
_ITERATIONS = 50
_IDEAL_EDGE_LEN = 60.0
_MAX_STEP = 10.0

# NAVIGABLE SPACE, piece A additions ---------------------------------------------------
_LAYOUT_VERSION_PROP = "graph_layout_v"
_LAYOUT_VERSION = 2  # bump this to force one migration pass over every already-placed object
_RELAX_ITERATIONS = 6  # "a FEW iterations" -- a nudge on top of the deterministic base,
                       # never enough to erase the ring/spiral structure
_PROJECT_SLOT_RANGE = 50_000  # spiral index domain for a project's center (see project_center)
_PROJECT_SPACING = 400.0
_RING_BASE = 40.0
_RING_GAP = 50.0
_UNFILED_KEY = "unfiled"  # the same sentinel /graph/supernodes already uses for no-in_repo
_GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))
_TYPE_RING_INDEX: dict[str, int] = {t.name: i for i, t in enumerate(_OBJECT_TYPES)}


def _hash01(key: str) -> float:
    """A deterministic pseudo-random float in [0,1) from a stable hash of `key` -- never
    Python's own hash() (salted per-process, so it would jitter every restart); sha256
    keeps every derived position reproducible across ticks, processes, and reruns, which
    the whole placement scheme depends on."""
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def project_center(canonical: str) -> tuple[float, float]:
    """A project's center on the plane -- a sunflower/Fermat spiral point at an index
    drawn from a stable hash of the project's OWN canonical, never its rank among peers,
    so creating or retiring an unrelated project never moves this one. Bounded spiral
    range (`_PROJECT_SLOT_RANGE`) keeps magnitudes sane; a same-slot collision between two
    unrelated projects is a cosmetic overlap, never a positioning bug (and vanishingly
    unlikely at this house's actual project count over a 50k-slot domain)."""
    slot = int(_hash01(f"project:{canonical}") * _PROJECT_SLOT_RANGE)
    r = _PROJECT_SPACING * math.sqrt(slot + 0.5)
    theta = slot * _GOLDEN_ANGLE
    return r * math.cos(theta), r * math.sin(theta)


def _type_ring_index(type_name: str) -> int:
    """Ring index for an object's TYPE around its project's center -- schema.py's own
    declared object-type order (already a stable, code-defined enumeration every other
    consumer of OBJECT_TYPES reuses), so every project draws the same type at the same
    ring, never a per-project rediscovery order. An extension type outside the static
    catalog still needs a stable index: falls back to a hash-derived ring beyond the
    known count -- deterministic, and a shared ring between two unknown types is a label
    overlap, never a positioning bug."""
    idx = _TYPE_RING_INDEX.get(type_name)
    if idx is not None:
        return idx
    return len(_TYPE_RING_INDEX) + int(_hash01(f"type:{type_name}") * 20)


def base_position(
    project_canonical: str | None, type_name: str, object_id: uuid.UUID,
) -> tuple[float, float]:
    """The deterministic placement rule itself: project as center, type as a ring around
    it, the object on that ring at an angle from a stable hash of its OWN id -- never a
    grid, never dependent on sibling count or discovery order. A pure function of
    (project, type, id) alone: recomputing it for the same object always lands on the
    same point, which is what lets the layout heartbeat visit any object at any time
    without ever needing to touch one it has already placed."""
    cx, cy = project_center(project_canonical or _UNFILED_KEY)
    radius = _RING_BASE + _type_ring_index(type_name) * _RING_GAP
    angle = _hash01(f"angle:{object_id}") * 2 * math.pi
    return cx + radius * math.cos(angle), cy + radius * math.sin(angle)


async def unplaced_batch(actions: Actions, limit: int = _BATCH_SIZE) -> list[uuid.UUID]:
    """Object ids still missing the CURRENT layout-version marker, oldest first
    (created_at) -- a stable, deterministic order so successive ticks make real
    progress. Deliberately keyed on the marker, not on graph_x's mere presence: an object
    positioned under a PRIOR layout version (missing today's marker even though it
    already carries graph_x/graph_y from that older scheme) is swept up here too, which
    is the entire mechanism behind the one-time migration a version bump causes."""
    rows = await actions.pool.fetch(
        "SELECT o.id FROM objects o "
        "WHERE o.status NOT IN ('archived','merged','retired') "
        "  AND NOT EXISTS (SELECT 1 FROM current_assertions a "
        "    WHERE a.object_id=o.id AND a.name=$2 AND (a.value #>> '{}')::int = $3) "
        "ORDER BY o.created_at ASC LIMIT $1",
        limit, _LAYOUT_VERSION_PROP, _LAYOUT_VERSION)
    return [r["id"] for r in rows]


async def positions_for(
    actions: Actions, ids: list[uuid.UUID],
) -> dict[uuid.UUID, tuple[float, float]]:
    """Every id in `ids` that already carries BOTH graph_x and graph_y -- a partial write
    (one landed, not the other) is treated as unpositioned rather than trusted half-done."""
    if not ids:
        return {}
    rows = await actions.pool.fetch(
        "SELECT object_id, name, value #>> '{}' AS v FROM current_assertions "
        "WHERE object_id = ANY($1::uuid[]) AND name IN ('graph_x','graph_y')",
        ids)
    xs: dict[uuid.UUID, float] = {}
    ys: dict[uuid.UUID, float] = {}
    for r in rows:
        (xs if r["name"] == "graph_x" else ys)[r["object_id"]] = float(r["v"])
    return {oid: (xs[oid], ys[oid]) for oid in xs if oid in ys}


async def _project_and_type(
    actions: Actions, ids: list[uuid.UUID],
) -> dict[uuid.UUID, tuple[str | None, str]]:
    """Each id's own (project canonical or None, object type) -- the same `in_repo` ->
    SoftwareProject membership /graph/supernodes already reads, DISTINCT ON the object so
    a rare multi-project membership still yields exactly one (deterministic: the lowest
    link id) rather than fanning an id out into two placement candidates."""
    if not ids:
        return {}
    rows = await actions.pool.fetch(
        "SELECT DISTINCT ON (o.id) o.id, o.type, p.canonical AS project_canonical "
        "FROM objects o "
        "LEFT JOIN links l ON l.from_id=o.id AND l.type='in_repo' "
        "  AND (l.valid_until IS NULL OR l.valid_until > now()) "
        "LEFT JOIN objects p ON p.id=l.to_id AND p.type='SoftwareProject' "
        "WHERE o.id = ANY($1::uuid[]) "
        "ORDER BY o.id, l.id",
        ids)
    return {r["id"]: (r["project_canonical"], r["type"]) for r in rows}


async def _neighbors_of(
    actions: Actions, ids: list[uuid.UUID],
) -> dict[uuid.UUID, set[uuid.UUID]]:
    if not ids:
        return {}
    rows = await actions.pool.fetch(
        "SELECT from_id, to_id FROM links "
        "WHERE from_id = ANY($1::uuid[]) OR to_id = ANY($1::uuid[])",
        ids)
    idset = set(ids)
    out: dict[uuid.UUID, set[uuid.UUID]] = {i: set() for i in ids}
    for r in rows:
        f, t = r["from_id"], r["to_id"]
        if f in idset:
            out[f].add(t)
        if t in idset:
            out[t].add(f)
    return out


def relax(
    unplaced: list[uuid.UUID], neighbors: dict[uuid.UUID, set[uuid.UUID]],
    anchors: dict[uuid.UUID, tuple[float, float]], *, iterations: int = _ITERATIONS,
    seed: int = 42, init: dict[uuid.UUID, tuple[float, float]] | None = None,
) -> dict[uuid.UUID, tuple[float, float]]:
    """Vectorized numpy Fruchterman-Reingold, LOCAL to `unplaced`: those nodes repel each
    other and are pulled toward their neighbors (both other unplaced nodes and fixed
    `anchors`, which never move) -- deterministic seed, so a re-run of the same batch
    reproduces the same layout rather than jittering on every retry.

    `init`, when given, seeds `pos` from these exact coordinates instead of a random
    uniform scatter (NAVIGABLE SPACE piece A: the deterministic ring/hash base position,
    so the relax pass nudges toward neighbors from a real starting point rather than
    replacing it with noise; every id in `unplaced` must appear in `init` when it is
    given). `seed`/random init stays the default for every caller that doesn't pass one
    (piece A's own pure-function unit tests keep working unchanged).

    MATRIX operations over the whole batch at once, never a Python double-loop over pairs
    -- measured live: the naive per-pair Python/numpy-scalar version didn't finish 300
    nodes in 60s (numpy's per-call overhead dominates at that granularity); this version
    positions 1000 nodes in a small fraction of a second by computing the full NxN
    displacement in one broadcast per iteration, the same way every real force-layout
    implementation (fcose included) actually does it."""
    n = len(unplaced)
    idx = {nid: i for i, nid in enumerate(unplaced)}
    if init is not None:
        pos = np.array([init[nid] for nid in unplaced], dtype=np.float64)
    else:
        rng = np.random.default_rng(seed)
        pos = rng.uniform(-50, 50, size=(n, 2))

    # the edge list as index pairs INTO `pos` (unplaced-unplaced) and a separate
    # unplaced-index/anchor-position list (unplaced-anchor) — built once, outside the
    # iteration loop, since the topology never changes across iterations.
    uu_pairs: list[tuple[int, int]] = []
    seen_pairs: set[tuple[int, int]] = set()
    ua_idx: list[int] = []
    ua_pos: list[tuple[float, float]] = []
    for nid, nbs in neighbors.items():
        i = idx[nid]
        for nb in nbs:
            if nb in idx:
                j = idx[nb]
                pair = (min(i, j), max(i, j))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    uu_pairs.append(pair)
            elif nb in anchors:
                ua_idx.append(i)
                ua_pos.append(anchors[nb])
    uu = np.array(uu_pairs, dtype=np.int64) if uu_pairs else np.zeros((0, 2), dtype=np.int64)
    ua_i = np.array(ua_idx, dtype=np.int64) if ua_idx else np.zeros(0, dtype=np.int64)
    ua_p = np.array(ua_pos, dtype=np.float64) if ua_pos else np.zeros((0, 2))

    for _ in range(iterations):
        # repulsion: every node pushes every other node away, O(n^2) but as ONE matrix op.
        delta = pos[:, None, :] - pos[None, :, :]                       # (n, n, 2)
        dist = np.maximum(np.linalg.norm(delta, axis=2), 0.01)          # (n, n)
        np.fill_diagonal(dist, np.inf)                                  # a node never repels itself
        repel = (_IDEAL_EDGE_LEN ** 2) / dist                           # (n, n)
        disp = (delta / dist[:, :, None] * repel[:, :, None]).sum(axis=1)  # (n, 2)

        # attraction along edges — unplaced-unplaced (both ends move, opposite signs) and
        # unplaced-anchor (only the unplaced end moves), each a single vectorized pass.
        if len(uu):
            a, b = uu[:, 0], uu[:, 1]
            d = pos[a] - pos[b]
            dd = np.maximum(np.linalg.norm(d, axis=1), 0.01)
            force = (dd ** 2 / _IDEAL_EDGE_LEN)[:, None] * (d / dd[:, None])
            np.subtract.at(disp, a, force)
            np.add.at(disp, b, force)
        if len(ua_i):
            d = pos[ua_i] - ua_p
            dd = np.maximum(np.linalg.norm(d, axis=1), 0.01)
            force = (dd ** 2 / _IDEAL_EDGE_LEN)[:, None] * (d / dd[:, None])
            np.subtract.at(disp, ua_i, force)

        dn = np.maximum(np.linalg.norm(disp, axis=1), 1e-9)
        step = np.minimum(dn, _MAX_STEP)
        pos = pos + disp / dn[:, None] * step[:, None]

    return {nid: (float(pos[idx[nid], 0]), float(pos[idx[nid], 1])) for nid in unplaced}


def _intra_project_neighbors(
    unplaced: list[uuid.UUID],
    neighbors: dict[uuid.UUID, set[uuid.UUID]],
    proj_type: dict[uuid.UUID, tuple[str | None, str]],
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """`neighbors`, filtered to same-PROJECT pairs only -- the ruling's own "edge
    attraction within a project": a cross-project edge never pulls either endpoint,
    regardless of how it would have pulled under the old whole-graph relax."""
    out: dict[uuid.UUID, set[uuid.UUID]] = {}
    for nid in unplaced:
        proj = proj_type.get(nid, (None, "Unknown"))[0]
        out[nid] = {
            nb for nb in neighbors.get(nid, set())
            if proj_type.get(nb, (object(), ""))[0] == proj
        }
    return out


async def _bulk_assert_positions(
    actions: Actions, placed: dict[uuid.UUID, tuple[float, float]], observed_at: datetime,
) -> None:
    """Records graph_x, graph_y, and the version marker for a whole tick's batch --
    append-only and superseding exactly like assert_property (an existing current row for
    this source+name is flipped non-current, never mutated in place), just covering the
    batch with one multi-row statement per property instead of one call per object (see
    the module docstring's WRITE PATH section for why that's safe here)."""
    if not placed:
        return
    ids = list(placed.keys())
    columns: list[tuple[str, list[object]]] = [
        ("graph_x", [round(placed[i][0], 2) for i in ids]),
        ("graph_y", [round(placed[i][1], 2) for i in ids]),
        (_LAYOUT_VERSION_PROP, [_LAYOUT_VERSION for _ in ids]),
    ]
    async with actions.pool.acquire() as conn, conn.transaction():
        for name, values in columns:
            await conn.execute(
                "UPDATE assertions SET is_current=false WHERE object_id = ANY($1::uuid[]) "
                "  AND name=$2 AND source_id=$3 AND is_current",
                ids, name, GRAPH_LAYOUT_SOURCE)
            await conn.execute(
                "INSERT INTO assertions (object_id, name, value, source_id, observed_at, "
                "  confidence, is_current) "
                "SELECT oid, $2, val::jsonb, $3, $4, $5, true "
                "FROM unnest($1::uuid[], $6::text[]) AS t(oid, val)",
                ids, name, GRAPH_LAYOUT_SOURCE, observed_at, 0.9,
                [json.dumps(v) for v in values])


async def layout_batch(actions: Actions, *, limit: int = _BATCH_SIZE) -> int:
    """One heartbeat tick: place up to `limit` objects still missing the current layout
    version -- a deterministic project/type/id base position, nudged by a few iterations
    of intra-project edge attraction anchored on already-placed same-project neighbors.
    Returns how many objects were newly positioned (0 when the graph is fully placed
    under the current version -- the tick's own natural quiescence, no flag needed)."""
    unplaced = await unplaced_batch(actions, limit)
    if not unplaced:
        return 0
    neighbors = await _neighbors_of(actions, unplaced)
    unplaced_set = set(unplaced)
    neighbor_ids = sorted(
        ({nb for nbs in neighbors.values() for nb in nbs} - unplaced_set), key=str)
    proj_type = await _project_and_type(actions, unplaced + neighbor_ids)
    base = {oid: base_position(*proj_type.get(oid, (None, "Unknown")), oid) for oid in unplaced}
    anchors = await positions_for(actions, neighbor_ids)
    intra = _intra_project_neighbors(unplaced, neighbors, proj_type)
    placed = relax(unplaced, intra, anchors, iterations=_RELAX_ITERATIONS, init=base)
    now = datetime.now(UTC)
    await _bulk_assert_positions(actions, placed, now)
    return len(placed)
