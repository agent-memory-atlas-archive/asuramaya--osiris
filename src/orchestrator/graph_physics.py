"""THE PHYSICS LAYOUT (operator ruling d7d55257 PHYSICS AND CONTAINERS, Thoth mail
11047, thread 7b8568e6) -- a wholesale replacement of graph_layout.py's sunflower/
declump placement scheme with a real force simulation over the WHOLE active graph,
run once per migration (never per-tick): semantic edges are springs (weight by type,
degree-normalised so a hub's own many springs don't each pull at full strength);
CONTAINER edges (src.ontology.link_classes.CONTAINER_LINK_TYPES -- in_repo, works_in,
acts_for, spawned_by, holds, member_of) are NOT springs -- a flat, weak, non-degree-
normalised pull toward the container, so a member of several containers settles near
their weighted mean and a container's own final position is simply wherever that pull
leaves it (no separate centroid-computation step -- the same one force simulation
does both, since a node connected only to its own members is, by construction, pulled
toward their mean). Every OTHER structural type (dispatch/governance/authorship)
stays excluded from the graph entirely, unchanged from THE READING LAYER (ruling
c5953bb1).

NESTED COMMUNITIES: a project with more than `_COMMUNITY_MIN_MEMBERS` active members
gets its own internal semantic subgraph run through Leiden community detection
(igraph's built-in `community_leiden`, no separate leidenalg dependency needed); a
member landing in a real (>=3-member) community loses its direct member->project
container edge in favour of member->community and community->project weak edges, so
the community reads as its own sub-cluster ("a super cluster reads as districts") --
communities are SYNTHETIC vertices with no `object_id`, present only to shape the one
shared force simulation, stripped before anything is ever written back.

HUBS: reuses graph_layout.py's own `_hub_ids` (structural-degree >= threshold)
unchanged as "universal hub" -- rather than inventing a second, narrower
cross-project-breadth metric, the existing measured threshold already selects the
unambiguous cases (principal Persons, the biggest projects) this ruling means by
"anything over threshold". A hub's OWN organic FR position (high-degree nodes already
tend toward a layout's centroid) is overridden as a final, disclosed step: snapped to
the real-vertex center of mass, tiny deterministic jitter so two hubs never coincide
before `_declump` runs.

SEEDED, DETERMINISTIC: every real vertex's FR starting position is
`graph_layout._sunflower_point` keyed on its own stable creation-order rank (the
`object_ids` query's own `ORDER BY created_at, id`) -- a re-run over the same
population lands on the same layout, matching every earlier layout version's own
determinism guarantee. Synthetic community vertices seed at a small deterministic
jitter around the origin (structural scaffolding only, no meaning of their own).

ENDS WITH THE SAME DECLUMP FLOOR (`graph_layout._declump`) every earlier version
used -- FR's own repulsion approaches but never guarantees a minimum separation
within a bounded iteration count, this is the deterministic correction that does.
THE OOM (Thoth mail 11097, kernel-confirmed: anon-rss 26.3 GB, process killed):
`_declump`'s OLD form built a full (n,n,2) pairwise array over the WHOLE
population -- 40 GB at n=50,087, since this migration passes every active object
at once (never 1000 at a time the way the heartbeat's own incremental batches do).
Fixed in graph_layout.py itself (a spatial-hash grid, `_grid_cells`/
`_neighbor_cell_indices`, cell size = min_sep, no O(n^2) memory anywhere) so both
this migration and the heartbeat share the fix. `run_physics_migrate` ALSO guards
its own remaining quadratic-shaped steps against `layout.physics_max_bytes`
(default 2 GB) before running them, and logs peak RSS on its final receipt --
belt-and-suspenders against a future reintroduction, not because anything left
here still allocates that way today.

WRITE PATH: reuses `graph_layout._bulk_assert_positions` unchanged (graph_x/graph_y/
graph_layout_v as ordinary property assertions, GRAPH_LAYOUT_SOURCE the sole writer),
chunked to keep any one multi-row statement a bounded size.

A GENUINELY DIFFERENT EXECUTION SHAPE FROM `run_layout_migrate`: that function loops
`layout_batch` (bounded per-tick batches, the SAME algorithm the cron heartbeat uses
for incremental new-object placement) to quiescence. This migration is a single
global computation over the WHOLE population in one pass -- springs pull across the
entire graph, not just within a batch, so it cannot be sliced into independent 1000-
object batches the way the old sunflower scheme could. `run_physics_migrate` below is
therefore its own function, not a variant of `run_layout_migrate`, but shares the SAME
advisory lock (`graph_layout._LAYOUT_LOCK_KEY`) so it and the routine cron heartbeat
(or a `run_layout_migrate` catch-up run) can never race each other -- held for this
function's ENTIRE run, not released between steps, since a heartbeat tick placing
even one "new" object mid-computation with the OLD incremental algorithm would need
undoing, not just racing.

ONGOING INCREMENTAL PLACEMENT (item 6, "heartbeat for new objects") is NOT in this
module -- it is graph_layout.layout_batch's own placement rule for `unplaced_regular`,
updated in that module to seed a new object at its container's (or already-placed
neighbours') own centroid and run a few bounded local relax iterations with
everything else pinned, instead of the old sunflower disc. See that module's own
docstring for the detail; kept there rather than here since it reuses `relax()`'s
existing local-batch machinery almost unchanged.
"""
from __future__ import annotations

import math
import resource
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import igraph as ig
import numpy as np

from src.actions.core import Actions
from src.ontology.link_classes import CONTAINER_LINK_TYPES, STRUCTURAL_LINK_TYPES
from src.orchestrator.graph_layout import (
    _MIN_SEPARATION,
    _bulk_assert_positions,
    _declump,
    _grid_cells,
    _hash01,
    _hub_ids,
    _release_layout_lock,
    _sunflower_point,
    _try_acquire_layout_lock,
)

_CONTAINER_SPRING_WEIGHT = 0.05  # flat, NOT degree-normalised -- "weak gravity", never
                                # a real spring; small enough that a member's own
                                # semantic springs (typically >= a few tenths after
                                # degree normalisation) still dominate its final
                                # resting position, container pull only matters when a
                                # member has few or no semantic edges of its own.
_COMMUNITY_MIN_MEMBERS = 500  # Thoth's own dispatch figure -- a project this size or
                              # smaller reads fine as one cluster; above it, internal
                              # structure ("districts") becomes worth drawing.
_COMMUNITY_MIN_SIZE = 3  # a detected community below this size is noise, not a real
                         # district -- its members fold back to a direct project edge
                         # rather than mint a near-empty synthetic vertex.
_FR_ITERATIONS = 300  # bounded for real-population runtime (measured live per the
                      # dispatch's own item 7 "run time" acceptance figure, tuned
                      # after that first measurement if this proves too slow or too
                      # coarse -- not claimed optimal on paper alone).
_SEED_SPACING = 15.0  # same NODE_SPACING scale graph_layout.py's own declump uses,
                      # for a numerically comparable starting scatter.
_SEMANTIC_TYPE_WEIGHT: dict[str, float] = {}  # hook for future per-type tuning
_DEFAULT_SEMANTIC_WEIGHT = 1.0  # every semantic type shares this today -- "weight by
                                # type" is a real mechanism (the dict above), just
                                # empty pending real per-type importance data; degree
                                # normalisation is the only differentiating signal now.
_PHYSICS_LAYOUT_VERSION = 7  # graph_layout._LAYOUT_VERSION must match this -- bumped
                             # together so the incremental heartbeat and this one-shot
                             # migration always agree on what "current" means.
_DEFAULT_PHYSICS_MAX_BYTES = 2_000_000_000  # layout.physics_max_bytes' own default
                                            # (Thoth mail 11097) -- see _memory_guard's
                                            # own docstring for what this actually checks.


async def _active_object_ids(actions: Actions) -> list[uuid.UUID]:
    """Every active object's id, oldest-created first -- this ORDER is itself the
    stable creation-order rank the deterministic FR seed keys on (index into this
    list), the same convention every earlier layout version used."""
    rows = await actions.pool.fetch(
        "SELECT id FROM objects WHERE status NOT IN ('archived','merged','retired') "
        "ORDER BY created_at, id")
    return [r["id"] for r in rows]


async def _live_link_rows(actions: Actions) -> list[asyncpg.Record]:
    return await actions.pool.fetch(  # type: ignore[no-any-return]
        "SELECT from_id, to_id, type FROM links "
        "WHERE valid_until IS NULL OR valid_until > now()")


async def _project_membership(actions: Actions) -> dict[uuid.UUID, uuid.UUID]:
    """object_id -> project_id via a LIVE in_repo link, DISTINCT ON the object (lowest
    link id wins a rare multi-project membership) -- used ONLY to decide which
    project's own community detection a member is eligible for; every live container
    edge (not just in_repo) still drives this member's own gravity in the final
    graph, this is a narrower question."""
    rows = await actions.pool.fetch(
        "SELECT DISTINCT ON (l.from_id) l.from_id AS object_id, l.to_id AS project_id "
        "FROM links l WHERE l.type='in_repo' "
        "  AND (l.valid_until IS NULL OR l.valid_until > now()) "
        "ORDER BY l.from_id, l.id")
    return {r["object_id"]: r["project_id"] for r in rows}


def _semantic_weight(link_type: str, deg_u: int, deg_v: int) -> float:
    base = _SEMANTIC_TYPE_WEIGHT.get(link_type, _DEFAULT_SEMANTIC_WEIGHT)
    return base / math.sqrt(max(1, deg_u) * max(1, deg_v))


def _detect_communities(
    link_rows: list[asyncpg.Record],
    membership: dict[uuid.UUID, uuid.UUID],
    active: set[uuid.UUID],
) -> dict[uuid.UUID, tuple[uuid.UUID, int]]:
    """object_id -> (project_id, community_local_id) for every member of a real
    (>= `_COMMUNITY_MIN_SIZE`) detected community in a project over
    `_COMMUNITY_MIN_MEMBERS` -- Leiden over that project's OWN internal semantic
    subgraph only (an edge strictly between two members of the same project, and not
    a container/structural type). A project at or under the threshold, or a member in
    a community too small to be a real district, is simply absent from the returned
    dict -- the caller treats that as "attach directly to the project", no special
    casing needed on this function's own side."""
    by_project: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for oid, pid in membership.items():
        if oid in active:
            by_project[pid].append(oid)

    out: dict[uuid.UUID, tuple[uuid.UUID, int]] = {}
    for pid, members in by_project.items():
        if len(members) <= _COMMUNITY_MIN_MEMBERS:
            continue
        local_idx = {oid: i for i, oid in enumerate(members)}
        edges: list[tuple[int, int]] = []
        for r in link_rows:
            f, t, lt = r["from_id"], r["to_id"], r["type"]
            if f not in local_idx or t not in local_idx or lt in STRUCTURAL_LINK_TYPES:
                continue
            edges.append((local_idx[f], local_idx[t]))
        sub = ig.Graph()
        sub.add_vertices(len(members))
        sub.add_edges(edges)
        clustering = sub.community_leiden(objective_function="modularity", n_iterations=2)
        sizes = clustering.sizes()
        for oid in members:
            comm = clustering.membership[local_idx[oid]]
            if sizes[comm] >= _COMMUNITY_MIN_SIZE:
                out[oid] = (pid, comm)
    return out


def _build_physics_graph(
    object_ids: list[uuid.UUID],
    link_rows: list[asyncpg.Record],
    communities: dict[uuid.UUID, tuple[uuid.UUID, int]],
) -> tuple[ig.Graph, list[uuid.UUID | None]]:
    """The one shared graph every force in this layout acts on: real active objects
    (index-aligned to `object_ids`) plus one synthetic vertex per real (project,
    community) pair found by `_detect_communities` (appended after, no `object_id` of
    their own -- `vertex_ids[i] is None` marks a synthetic row). Returns the graph and
    a vertex-index -> object_id-or-None list the caller strips synthetic rows with
    after layout."""
    idx = {oid: i for i, oid in enumerate(object_ids)}

    # total live-link degree per real object (ANY type) -- the same "weight" concept
    # graph_stream.py's own node weight array already computes, reused here as the
    # semantic-spring degree-normalisation denominator.
    degree = [0] * len(object_ids)
    for r in link_rows:
        f, t = r["from_id"], r["to_id"]
        if f in idx and t in idx:
            degree[idx[f]] += 1
            degree[idx[t]] += 1

    community_vertex: dict[tuple[uuid.UUID, int], int] = {}
    vertex_ids: list[uuid.UUID | None] = list(object_ids)
    for key in sorted(set(communities.values()), key=lambda k: (str(k[0]), k[1])):
        community_vertex[key] = len(vertex_ids)
        vertex_ids.append(None)

    # THE COLLAPSED-CONTAINER FIX (Thoth mail 11111, ruling 853d0f9c): a flat
    # container weight pulled EVERY member of a shared container toward the exact
    # same point with equal strength regardless of how many siblings it had --
    # for a container with thousands of members and few or no semantic edges to
    # differentiate them, that isn't "weak gravity" in aggregate, it's a landslide
    # (a live specimen: 6,131 points collapsed into one post-FR grid cell). The
    # SAME edge weight (_CONTAINER_SPRING_WEIGHT) is now divided by the
    # container's own live member count (1/N, not 1/sqrt(N) -- measured live: a
    # 1,000-member test container still landed 264 points in one post-FR cell at
    # 1/sqrt(N), well over the ~50-point acceptance line; 1/N brings it comfortably
    # under), so a container with N members pulls each one at 1/N strength --
    # sibling repulsion (which every vertex exerts on every other regardless of
    # edges) then actually wins for a large container, letting members spread out
    # around it instead of collapsing onto it. Counted by FINAL destination
    # (`dst_i`, already resolved to a synthetic community vertex where one
    # applies) so a member routed through a district's own vertex is counted
    # against THAT vertex's member count, not the whole project's.
    container_edges: list[tuple[int, int]] = []
    dst_member_counts: dict[int, int] = defaultdict(int)
    for r in link_rows:
        f, t, lt = r["from_id"], r["to_id"], r["type"]
        if f not in idx or t not in idx or lt not in CONTAINER_LINK_TYPES:
            continue
        i, j = idx[f], idx[t]
        comm = communities.get(f)
        reroute = lt == "in_repo" and comm is not None and comm[0] == t
        dst_i = community_vertex[comm] if reroute and comm is not None else j
        container_edges.append((i, dst_i))
        dst_member_counts[dst_i] += 1

    edges: list[tuple[int, int]] = []
    weights: list[float] = []
    for r in link_rows:
        f, t, lt = r["from_id"], r["to_id"], r["type"]
        if f not in idx or t not in idx or lt in CONTAINER_LINK_TYPES:
            continue
        if lt not in STRUCTURAL_LINK_TYPES:
            i, j = idx[f], idx[t]
            edges.append((i, j))
            weights.append(_semantic_weight(lt, degree[i], degree[j]))

    for i, dst_i in container_edges:
        w = _CONTAINER_SPRING_WEIGHT / max(1, dst_member_counts[dst_i])
        edges.append((i, dst_i))
        weights.append(w)

    for (_pid, _comm), cvi in community_vertex.items():
        pi = idx.get(_pid)
        if pi is not None:
            w = _CONTAINER_SPRING_WEIGHT / max(1, dst_member_counts.get(cvi, 1))
            edges.append((cvi, pi))
            weights.append(w)

    g = ig.Graph()
    g.add_vertices(len(vertex_ids))
    g.add_edges(edges)
    g.es["weight"] = weights
    return g, vertex_ids


def _seed_positions(vertex_ids: list[uuid.UUID | None]) -> np.ndarray:
    """Deterministic FR starting scatter: a real object seeds at
    `_sunflower_point` keyed on its own stable creation-order rank (its index in
    `object_ids`, i.e. its position in this same list before synthetic rows were
    appended); a synthetic community vertex seeds at a small deterministic jitter
    around the origin (structural scaffolding only)."""
    out = np.zeros((len(vertex_ids), 2))
    for i, oid in enumerate(vertex_ids):
        if oid is not None:
            out[i] = _sunflower_point(i, _SEED_SPACING)
        else:
            angle = _hash01(f"community-seed:{i}") * 2 * math.pi
            out[i] = (10.0 * math.cos(angle), 10.0 * math.sin(angle))
    return out


class MemoryBudgetExceeded(Exception):
    """Raised by `_memory_guard` when the POST-FR positions it's handed would need
    more than `layout.physics_max_bytes` on a hypothetical quadratic fallback --
    see that function's own docstring for why it checks post-FR, not the seed."""


def _fr_and_recenter(
    g: ig.Graph, vertex_ids: list[uuid.UUID | None], object_ids: list[uuid.UUID],
    hub_ids: set[uuid.UUID],
) -> np.ndarray:
    """Pure (no DB): seeded FR over the whole shared graph, then hubs snapped to the
    real-vertex center of mass. Split out from `_physics_positions` so a test can
    check THE COLLAPSED-CONTAINER FIX's own acceptance line directly -- "no cell
    holds more than ~50 points after FR for any project" -- against this function's
    own output, before the memory guard or declump ever run."""
    seed = _seed_positions(vertex_ids)
    # grid=True explicitly (Thoth mail 11111): "auto" is DOCUMENTED to already pick
    # the grid-based approximation at this vertex count (>= 1,000), but naming it
    # directly means this call's own real-range repulsion behavior is never at the
    # mercy of a future igraph version changing that heuristic's threshold.
    coords = g.layout_fruchterman_reingold(
        weights=g.es["weight"] if g.ecount() else None,
        niter=_FR_ITERATIONS, seed=seed.tolist(), grid=True)
    pos = np.array(coords.coords)

    real_mask = [oid is not None for oid in vertex_ids]
    real_pos = pos[real_mask]
    center = real_pos.mean(axis=0) if len(real_pos) else np.zeros(2)

    real_index = {oid: i for i, oid in enumerate(object_ids)}
    for oid in hub_ids:
        i = real_index.get(oid)
        if i is None:
            continue
        angle = _hash01(f"physics-hub:{oid}") * 2 * math.pi
        pos[i] = center + np.array([math.cos(angle), math.sin(angle)]) * 1.0

    return pos[: len(object_ids)]


async def _physics_positions(
    actions: Actions,
) -> dict[uuid.UUID, tuple[float, float]]:
    """The full computation, pure enough to unit-test without a live migration write:
    population + links -> communities -> the one shared graph -> seeded FR -> hub
    re-centering (`_fr_and_recenter`) -> the memory guard -> the existing declump
    floor. Real object positions only -- synthetic community rows never leave this
    function. Can raise `MemoryBudgetExceeded` (THE COLLAPSED-CONTAINER FIX, Thoth
    mail 11111) if FR's own output still lands too many points in one grid cell for
    the configured budget -- the caller (`run_physics_migrate`) turns that into a
    written refusal receipt rather than letting declump try anyway."""
    object_ids = await _active_object_ids(actions)
    if not object_ids:
        return {}
    link_rows = await _live_link_rows(actions)
    membership = await _project_membership(actions)
    active = set(object_ids)
    communities = _detect_communities(link_rows, membership, active)
    g, vertex_ids = _build_physics_graph(object_ids, link_rows, communities)
    hub_ids = await _hub_ids(actions, object_ids)

    real_positions = _fr_and_recenter(g, vertex_ids, object_ids, hub_ids)
    reason = await _memory_guard(actions, real_positions)
    if reason:
        raise MemoryBudgetExceeded(reason)

    declumped = _declump(
        real_positions, np.zeros((0, 2)), object_ids, min_sep=_MIN_SEPARATION)

    return {oid: (float(declumped[i, 0]), float(declumped[i, 1]))
            for i, oid in enumerate(object_ids)}


async def _memory_guard(actions: Actions, positions: np.ndarray) -> str | None:
    """THE COLLAPSED-CONTAINER FIX (Thoth mail 11111): checked against POST-FR
    positions, not the seed -- a live specimen showed the seed (always sparse by
    the sunflower's own construction) passing clean while FR's own springs/gravity
    later collapsed thousands of container-only siblings onto one point, the exact
    case this guard exists to catch. Still a defensive check against the ONE shape
    that could still cost O(k^2) memory after graph_layout._declump's own grid
    rewrite (its real cost is O(n) for any reasonably spread population) -- the
    largest SINGLE grid cell's own point count `k`. Returns a written refusal
    reason, or None when safe."""
    from src.orchestrator.settings_service import current_stored_value

    cells = _grid_cells(positions, _MIN_SEPARATION)
    worst_k = max((len(v) for v in cells.values()), default=0)
    stored = await current_stored_value(actions.pool, "layout.physics_max_bytes")
    max_bytes = int(stored) if isinstance(stored, int | float) else _DEFAULT_PHYSICS_MAX_BYTES
    needed = worst_k * worst_k * 16
    if needed > max_bytes:
        return (f"refusing: the worst single post-FR grid cell holds {worst_k} points -- "
                f"a quadratic fallback there would need ~{needed} bytes, over "
                f"layout.physics_max_bytes={max_bytes}")
    return None


async def run_physics_migrate(actions: Actions) -> AsyncIterator[dict[str, Any]]:
    """THE PHYSICS LAYOUT's own migration door: a SINGLE global computation over the
    whole active population (never a batch loop -- see the module docstring for why),
    sharing `graph_layout._LAYOUT_LOCK_KEY` with the cron heartbeat and
    `run_layout_migrate` so nothing else touches graph_x/graph_y while this runs.
    Yields coarse stage receipts (not one per batch, since there are none) and a
    final `{"done": True, "placed": N, "peak_rss_kb": N}` -- `_physics_positions`'s
    own post-FR memory guard (THE COLLAPSED-CONTAINER FIX, Thoth mail 11111) can
    raise `MemoryBudgetExceeded` instead, turned here into a single `{"error": ...}`
    receipt with no write."""
    async with actions.pool.acquire() as lock_conn:
        if not await _try_acquire_layout_lock(lock_conn):
            yield {"error": "the layout heartbeat (or a migrate run) currently holds "
                            "the layout lock -- try again shortly"}
            return
        try:
            yield {"stage": "computing"}
            try:
                positions = await _physics_positions(actions)
            except MemoryBudgetExceeded as exc:
                yield {"error": str(exc)}
                return
            yield {"stage": "writing", "count": len(positions)}
            now = datetime.now(UTC)
            ids = list(positions.keys())
            chunk = 5000
            for start in range(0, len(ids), chunk):
                batch_ids = ids[start:start + chunk]
                await _bulk_assert_positions(
                    actions, {oid: positions[oid] for oid in batch_ids}, now)
            peak_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            yield {"done": True, "placed": len(positions), "peak_rss_kb": peak_rss_kb}
        finally:
            await _release_layout_lock(lock_conn)
