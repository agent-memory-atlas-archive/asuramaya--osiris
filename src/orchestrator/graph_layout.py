"""THE GRAPH VISUALIZER (wave B item 1, thread 8839) -- extended for NAVIGABLE SPACE, THE
SERVER, piece A (rulings f832c3a4 + 0a3d6719, operator 2026-09-14, thread b6cb1d7c0b36):
server-side placement over the WHOLE graph, run incrementally by the heartbeat -- positions
stored as current_assertions (graph_x/graph_y), never computed live in the browser or the
renderer. This module feeds the /graph endpoints (supernodes/clusters/viewport) and the
whole-graph typed-array stream (graph_stream.py).

DECLUMP FIX (Thoth mail 10582, PRIORITY -- the operator's own screenshot of the deployed
space showed stacked nodes, collapsed rings, thick edge bundles instead of a spread cloud):
the FIRST version of this placement rule put every object of one type in one project on a
SINGLE fixed-radius circle at a random hash angle -- fine for a handful of objects, but this
house's own real population has (project, type) groups running into the THOUSANDS (Agent in
the unfiled bucket: 18,978; Commit in the osiris project itself: 6,273) and a fixed
circumference simply cannot hold that many points apart. Project centers had the same
disease one level up: a hash into a wide flat spiral-index range gives no guaranteed
MINIMUM separation between two projects, so the extent ends up both sparse in places and
badly clumped in others (measured at ~260,000 units wide for ~49k objects before this fix).

ONE MECHANISM, applied at both levels, replaces the old fixed-circle-plus-hash-angle rule:
a SUNFLOWER/FERMAT SPIRAL keyed on a STABLE RANK (never a hash) -- radius grows with
sqrt(rank), so N points pack into a radius proportional to sqrt(N) with a guaranteed
minimum pairwise spacing, and the rank itself is permanent once assigned (creation-order
via `ROW_NUMBER() OVER (... ORDER BY created_at, id)`, computed by the DATABASE, not
derivable from an object's own id alone) -- a later-created sibling only ever takes a
HIGHER, previously-unused rank, so an existing object's or project's position never moves
once placed, the same incrementality guarantee the id-hash version had, just resolved
against an immutable ORDER instead of an immutable VALUE.
  - PROJECT CENTERS: every active SoftwareProject's rank by creation order; the `unfiled`
    sentinel is pinned to rank 0 so it can never collide with a real project's index; real
    projects start at 1. Spacing sized (measured live before picking the constant) to
    comfortably contain even the worst real project's own extent (osiris itself, ~11.7k
    objects) without two projects' discs ever touching.
  - OBJECTS WITHIN A (project, type) GROUP: same sunflower, keyed on the object's own rank
    within that exact group, offset outward from the type's existing base radius (still
    schema.py's declared type order, unchanged) -- so small groups still read as a tight
    ring near that base radius, and only a group large enough to need it spirals outward
    past it (Thoth's own "beyond one ring's capacity" framing, expressed here as a single
    formula rather than a two-tier ring-then-disc special case).

A bounded intra-project relax pass still nudges each tick's batch toward already-placed
same-project neighbors (unchanged rule: cross-project edges never attract) -- but now ends
with a HARD MINIMUM-SEPARATION PASS: the Fruchterman-Reingold repulsion term APPROACHES a
floor over enough iterations but never GUARANTEES one within the few iterations this
heartbeat actually runs, so strong attraction could still leave two connected nodes
uncomfortably close (or, at the limit, exactly coincident) -- this pass is a direct,
deterministic correction, not another force-simulation step, so the guarantee holds
regardless of how attraction behaved before it ran.

INCREMENTAL, NEVER REVISITED: unchanged mechanism -- a tick only ever considers objects
still missing the CURRENT `graph_layout_v` marker (bumped to 3 here, forcing the same kind
of one-time migration the 1->2 bump already did), so a re-run over already-placed objects
moves nothing.

WRITE PATH: unchanged -- graph_x/graph_y/graph_layout_v land as ordinary property
assertions via one multi-row UPDATE+INSERT per property per tick, safe only because
GRAPH_LAYOUT_SOURCE is this triple's sole writer (see the prior version's own note, still
true here).
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
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
_LAYOUT_VERSION = 3  # bump this to force one migration pass over every already-placed object
_RELAX_ITERATIONS = 6  # "a FEW iterations" -- a nudge on top of the deterministic base,
                       # never enough to erase the sunflower structure
_RING_BASE = 40.0
_RING_GAP = 50.0
_UNFILED_KEY = "unfiled"  # the same sentinel /graph/supernodes already uses for no-in_repo
_GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))

# spacing constants, sized against this house's OWN measured population (live, via
# /graph/supernodes and /graph/clusters, never guessed) before the DECLUMP FIX landed:
# worst single (project,type) group is Agent/unfiled at 18,978 (sunflower radius at that
# rank, spacing 15.0, is ~2,067); worst real project is osiris itself at 11,768 objects
# (Commit alone 6,273, radius ~1,188 at the same spacing) across 40 real projects total.
_NODE_SPACING = 15.0  # minimum pairwise spacing within one (project, type) sunflower disc
_MIN_SEPARATION = _NODE_SPACING  # hard floor the post-relax declump pass enforces
_PROJECT_SPACING = 5000.0  # minimum spacing between two projects' sunflower rank-points --
                           # >2x the worst measured single-project content radius (~2,067),
                           # so two adjacent worst-case projects' own discs can never touch
_TYPE_RING_INDEX: dict[str, int] = {t.name: i for i, t in enumerate(_OBJECT_TYPES)}


def _hash01(key: str) -> float:
    """A deterministic pseudo-random float in [0,1) from a stable hash of `key` -- never
    Python's own hash() (salted per-process, so it would jitter every restart); sha256
    keeps every derived value reproducible across ticks, processes, and reruns."""
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _sunflower_point(rank: int, spacing: float) -> tuple[float, float]:
    """The ONE placement primitive this module builds everything else from: a
    golden-angle sunflower/Fermat spiral point at a given non-negative integer RANK,
    at a given spacing scale. Pure function of (rank, spacing) alone -- the caller is
    responsible for making sure `rank` itself is a STABLE, permanent value (creation-
    order, never a hash) so recomputing this for the same rank always lands on the
    same point, and a later-arriving sibling only ever gets a higher rank, never
    disturbing an earlier one's placement."""
    r = spacing * math.sqrt(rank + 0.5)
    theta = rank * _GOLDEN_ANGLE
    return r * math.cos(theta), r * math.sin(theta)


def project_center(rank: int) -> tuple[float, float]:
    """A project's center on the plane -- a sunflower point at its own permanent
    creation-order rank (see `_project_ranks`), never a hash of its canonical. Two
    projects can never collide: the sunflower's own geometry guarantees consecutive
    ranks are at least `_PROJECT_SPACING`-ish apart, comfortably clear of even the
    largest measured project's own content radius."""
    return _sunflower_point(rank, _PROJECT_SPACING)


def _type_ring_index(type_name: str) -> int:
    """Base ring index for an object's TYPE around its project's center -- schema.py's
    own declared object-type order (already a stable, code-defined enumeration every
    other consumer of OBJECT_TYPES reuses), so every project draws the same type at
    the same base radius. An extension type outside the static catalog still needs a
    stable index: falls back to a hash-derived ring beyond the known count --
    deterministic, and a shared base ring between two unknown types is a label
    overlap, never a positioning bug (the sunflower disc built on top of it still
    keeps individual OBJECTS apart)."""
    idx = _TYPE_RING_INDEX.get(type_name)
    if idx is not None:
        return idx
    return len(_TYPE_RING_INDEX) + int(_hash01(f"type:{type_name}") * 20)


def base_position(
    center: tuple[float, float], type_name: str, rank_in_group: int,
) -> tuple[float, float]:
    """The deterministic placement rule itself: a sunflower disc for the object's own
    (project, type) group, keyed on its permanent rank within that group, offset
    outward from the type's own base radius (so a small group still reads as a tight
    ring near that radius, and only a group large enough to need it spirals past it).
    A pure function of (center, type, rank) alone -- recomputing it for the same
    rank always lands on the same point."""
    cx, cy = center
    base_r = _RING_BASE + _type_ring_index(type_name) * _RING_GAP
    lx, ly = _sunflower_point(rank_in_group, _NODE_SPACING)
    local_r = math.hypot(lx, ly)
    angle = math.atan2(ly, lx)
    radius = base_r + local_r
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


async def _project_ranks(actions: Actions) -> dict[str, int]:
    """Every active project's own PERMANENT rank by creation order --
    `ROW_NUMBER() OVER (ORDER BY created_at, id)`, so an existing project's rank never
    changes: a new project can only ever take a higher, previously-unused index.
    `_UNFILED_KEY` is pinned to rank 0 so it can never collide with a real project's
    own index regardless of how many projects exist."""
    rows = await actions.pool.fetch(
        "SELECT canonical, row_number() OVER (ORDER BY created_at, id) AS rnk "
        "FROM objects WHERE type='SoftwareProject' AND status='active'")
    ranks: dict[str, int] = {_UNFILED_KEY: 0}
    ranks.update({r["canonical"]: int(r["rnk"]) for r in rows})
    return ranks


async def _group_ranks(actions: Actions, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Each id's own PERMANENT rank within its (project, type) group --
    `ROW_NUMBER() OVER (PARTITION BY project, type ORDER BY created_at, id)`, computed
    over the WHOLE active population in one indexed window-function scan (this
    house's own current ~49k-object scale: a sub-second query) so a rank, once
    assigned to an id by this formula, can never change -- a later-created sibling
    only ever takes a higher, not-yet-used rank in the SAME group. The inner
    DISTINCT ON mirrors `_project_and_type`'s own multi-in_repo-link tie-break
    (lowest link id wins) so a rare multi-membership object is never double-counted
    into its own group, which would otherwise corrupt every rank after it."""
    if not ids:
        return {}
    rows = await actions.pool.fetch(
        "WITH members AS ("
        "  SELECT DISTINCT ON (o.id) o.id, o.type, o.created_at, "
        "    COALESCE(p.canonical, $2) AS project_key "
        "  FROM objects o "
        "  LEFT JOIN links l ON l.from_id=o.id AND l.type='in_repo' "
        "    AND (l.valid_until IS NULL OR l.valid_until > now()) "
        "  LEFT JOIN objects p ON p.id=l.to_id AND p.type='SoftwareProject' "
        "  WHERE o.status NOT IN ('archived','merged','retired') "
        "  ORDER BY o.id, l.id"
        "), ranked AS ("
        "  SELECT id, row_number() OVER ("
        "    PARTITION BY project_key, type ORDER BY created_at, id"
        "  ) - 1 AS rank_in_group "
        "  FROM members"
        ") SELECT id, rank_in_group FROM ranked WHERE id = ANY($1::uuid[])",
        ids, _UNFILED_KEY)
    return {r["id"]: int(r["rank_in_group"]) for r in rows}


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


def _declump(
    pos: np.ndarray, anchor_pos: np.ndarray, ids: list[uuid.UUID], *,
    min_sep: float = _MIN_SEPARATION, iterations: int = 30,
) -> np.ndarray:
    """The HARD MINIMUM-SEPARATION pass (Thoth mail 10582, PRIORITY): a direct,
    deterministic correction, never another force-simulation step -- `relax()`'s own
    repulsion approaches but does not GUARANTEE a floor within a bounded iteration
    count, so strong attraction could still leave two connected nodes (or a node and
    a fixed anchor) closer than `min_sep`, at the limit exactly coincident. Pushes
    every pair closer than `min_sep` apart by exactly the deficit (split evenly
    between two movable points; the FULL deficit onto the movable side of a
    movable/anchor pair, since the anchor never moves) -- iterates a bounded few
    times since separating one pair can nudge another pair together, and stops the
    moment a full pass finds nothing left to fix."""
    for _ in range(iterations):
        moved = False

        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(delta, axis=2)
        np.fill_diagonal(dist, np.inf)
        too_close = dist < min_sep
        if too_close.any():
            moved = True
            safe = np.where(dist < 1e-9, 1.0, dist)
            direction = delta / safe[:, :, None]
            for i, j in zip(*np.where(dist < 1e-9), strict=True):
                if i < j:
                    a = _hash01(f"declump:{ids[i]}:{ids[j]}") * 2 * math.pi
                    direction[i, j] = (math.cos(a), math.sin(a))
                    direction[j, i] = (-math.cos(a), -math.sin(a))
            deficit = np.where(too_close, min_sep - np.minimum(dist, min_sep), 0.0)
            pos = pos + (deficit[:, :, None] / 2 * direction).sum(axis=1)

        if len(anchor_pos):
            d2 = pos[:, None, :] - anchor_pos[None, :, :]
            dist2 = np.linalg.norm(d2, axis=2)
            too_close2 = dist2 < min_sep
            if too_close2.any():
                moved = True
                safe2 = np.where(dist2 < 1e-9, 1.0, dist2)
                direction2 = d2 / safe2[:, :, None]
                for i, k in zip(*np.where(dist2 < 1e-9), strict=True):
                    a = _hash01(f"declump-anchor:{ids[i]}:{k}") * 2 * math.pi
                    direction2[i, k] = (math.cos(a), math.sin(a))
                deficit2 = np.where(
                    too_close2, min_sep - np.minimum(dist2, min_sep), 0.0)
                pos = pos + (deficit2[:, :, None] * direction2).sum(axis=1)

        if not moved:
            break
    return pos


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
    uniform scatter (NAVIGABLE SPACE piece A: the deterministic sunflower base
    position, so the relax pass nudges toward neighbors from a real starting point
    rather than replacing it with noise; every id in `unplaced` must appear in `init`
    when it is given). `seed`/random init stays the default for every caller that
    doesn't pass one (this module's own pure-function unit tests keep working
    unchanged).

    Ends with `_declump`'s hard minimum-separation pass (Thoth mail 10582) -- see its
    own docstring; this is what actually guarantees two connected nodes never end up
    stacked, since the FR iterations above only ever approach that floor.

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

    pos = _declump(pos, ua_p, unplaced)

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


async def layout_batch(actions: Actions, *, limit: int | None = None) -> int:
    """One heartbeat tick: place up to `limit` objects still missing the current layout
    version -- a deterministic sunflower base position (project center by rank, object
    by its own rank within the (project, type) group), nudged by a few iterations of
    intra-project edge attraction anchored on already-placed same-project neighbors,
    then hard-declumped. Returns how many objects were newly positioned (0 when the
    graph is fully placed under the current version -- the tick's own natural
    quiescence, no flag needed).

    `limit=None` (every real caller -- the cron heartbeat and `run_layout_migrate`)
    reads `layout.batch_size` off the LIVE settings table (Thoth mail 10609, product
    law: every action has a door) via `current_stored_value` -- effect='next_tick' is
    genuine here, not the env-overlay path that only covers effect='immediate' keys --
    falling back to `_BATCH_SIZE` when the key has never been written. Passing an
    explicit `limit` (every test in this module) bypasses the settings lookup
    entirely, same as before."""
    if limit is None:
        from src.orchestrator.settings_service import current_stored_value
        stored = await current_stored_value(actions.pool, "layout.batch_size")
        limit = int(stored) if isinstance(stored, int | float) else _BATCH_SIZE
    unplaced = await unplaced_batch(actions, limit)
    if not unplaced:
        return 0
    neighbors = await _neighbors_of(actions, unplaced)
    unplaced_set = set(unplaced)
    neighbor_ids = sorted(
        ({nb for nbs in neighbors.values() for nb in nbs} - unplaced_set), key=str)
    proj_type = await _project_and_type(actions, unplaced + neighbor_ids)
    project_ranks = await _project_ranks(actions)
    group_ranks = await _group_ranks(actions, unplaced)
    base = {}
    for oid in unplaced:
        proj_canonical, type_name = proj_type.get(oid, (None, "Unknown"))
        center = project_center(project_ranks.get(proj_canonical or _UNFILED_KEY, 0))
        base[oid] = base_position(center, type_name, group_ranks.get(oid, 0))
    anchors = await positions_for(actions, neighbor_ids)
    intra = _intra_project_neighbors(unplaced, neighbors, proj_type)
    placed = relax(unplaced, intra, anchors, iterations=_RELAX_ITERATIONS, init=base)
    now = datetime.now(UTC)
    await _bulk_assert_positions(actions, placed, now)
    return len(placed)


_LAYOUT_LOCK_KEY = "graph_layout_batch"  # advisory-lock name shared by the cron
                                        # heartbeat and run_layout_migrate below


async def _try_acquire_layout_lock(conn: asyncpg.Connection) -> bool:
    """SESSION-scoped `pg_try_advisory_lock`, deliberately -- a transaction-scoped
    lock would release the instant the acquiring query's own tiny transaction
    commits, defeating the entire point of holding it for a whole migration run.
    The historical outage this house learned from (#172: a connection returned to
    the pool while still holding a session lock wedged the fleet for 15 minutes) is
    avoided by construction here, not by avoiding session locks altogether: the ONLY
    caller, `run_layout_migrate`, always releases via `_release_layout_lock` in a
    `finally` BEFORE the `async with actions.pool.acquire()` block that owns this
    connection ever exits -- the lock is never left to the pool's own connection
    reset to clean up."""
    return bool(await conn.fetchval(
        "SELECT pg_try_advisory_lock(hashtext($1))", _LAYOUT_LOCK_KEY))


async def _release_layout_lock(conn: asyncpg.Connection) -> None:
    await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", _LAYOUT_LOCK_KEY)


async def run_layout_migrate(
    actions: Actions, *, limit: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """THE MIGRATION DOOR (Thoth mail 10609): loop `layout_batch` until
    `unplaced_batch` runs dry, yielding one receipt per batch as it happens rather
    than collecting a final report -- a `graph_layout_v` bump otherwise waits on the
    cron heartbeat's own 1000-objects/5-minute pace (hours for a real migration).
    Refuses outright (yields a single `{"error": ...}` receipt, does no work) if the
    cron heartbeat is mid-tick and already holds `_LAYOUT_LOCK_KEY` -- see
    `_try_acquire_layout_lock`'s own docstring for why this is session-scoped and
    safe. The SAME `layout_batch` the cron heartbeat calls -- never a second
    implementation of the placement logic, just a tighter loop around it."""
    async with actions.pool.acquire() as lock_conn:
        if not await _try_acquire_layout_lock(lock_conn):
            yield {"error": "the layout heartbeat (or another migrate run) currently "
                            "holds the layout lock -- try again shortly"}
            return
        try:
            batch_no = 0
            total_placed = 0
            while True:
                n = await layout_batch(actions, limit=limit)
                batch_no += 1
                total_placed += n
                yield {"batch": batch_no, "placed": n, "total_placed": total_placed}
                if n == 0:
                    break
        finally:
            await _release_layout_lock(lock_conn)
