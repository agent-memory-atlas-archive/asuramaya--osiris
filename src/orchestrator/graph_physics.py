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
"anything over threshold". A hub's OWN organic FR position is overridden as a final,
disclosed step -- as of v8 (THE HUB ZONE, `_HUB_ZONE_ID`) this is its own extra
level-1 vertex with its own radius budget, run through the SAME
`_separate_extents` pass as every real project, never the raw centroid-of-everything
a v7-style "snap to the mean, jitter by 1 unit" scheme used: that scheme's own
1-unit jitter radius had no separation guarantee against whatever real content
happened to already occupy that centroid, and the first real hierarchical migration
attempt (bcc6b3f3) hit exactly that -- eleven hubs squeezed into a 2-unit disc that
collided with a dense project sitting at the same point, caught by
`_verify_min_separation` rather than shipped silently.

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

HIERARCHICAL PHYSICS (v8, Thoth mail 11128, decision dca4fcc1): the FLAT whole-graph
FR above (v7) ran clean -- 50,317 placed, 202 MB, 253 s, no OOM -- but FAILED the
layout's own acceptance the moment Thoth measured real positions: nn p50 2.8 against
a 15-unit floor, every project's members spread to ~1,000 units while project
centroids sat only 82-224 units apart. The root cause was never a declump bug --
`_declump`'s 30-iteration cap simply cannot converge when a whole graph's worth of
projects are allowed to overlap by construction; the fix is to STOP them overlapping,
never to push the convergence budget higher. Two-level scheme:

LEVEL 1, the project-contracted graph: one vertex per project with >=1 active member,
edges an aggregated cross-project semantic link count (`_cross_project_edges`), FR
over that small graph (`_level1_layout`), then `_separate_extents` -- an
extent-aware repulsion pass (each project vertex carries its own radius budget
`_level1_radius`, R_p = k*sqrt(N_p)*spacing) that pushes every pair of project
DISCS apart until their centroid distance is at least R_a + R_b + a gutter, the
exact same deterministic-correction shape `_declump` itself uses, just keyed on a
per-vertex radius instead of one shared floor. This is what actually stops the
"one white blob" -- no two projects' own member clouds can ever occupy the same
world-space region once this pass has run.

LEVEL 2, one project at a time: `_level2_layout_for_project` reuses
`_build_physics_graph` UNCHANGED, scoped to just that project's own members (semantic
springs, district/community gravity, the collapsed-container fix's 1/member-count
container weight) -- the project's own vertex is included as an ordinary participant,
so its FINAL FR position (not the origin) is what the whole subgraph gets recentred
on, then rescaled so the 95th-percentile member radius matches that project's own
`R_p` from level 1, then translated onto its level-1 centroid.

CROSS-PROJECT BRIDGING (item 3): a member with a live semantic edge to a member of a
DIFFERENT project gets a small post-hoc nudge (`_apply_bridge_nudges`) toward that
other project's own level-1 centroid, so a bridging member settles nearer the facing
edge of its own project's disc. Disclosed simplification -- folding this directly into
level 2's own FR would mean merging two very different coordinate scales (a project's
own local spread vs. the whole graph's level-1 extent) inside one force integration;
a bounded post-hoc nudge sidesteps that without needing a third coordinate system.

UNFILED OBJECTS (item 4): `_place_unfiled` -- one with a live semantic link to an
already-placed object lands at the mean position of those neighbours; one with NONE
scatters as a proper 2D Gaussian (Box-Muller polar form, not a fixed-radius ring)
centred on the whole placed cloud's own centroid, std set from that cloud's own
spread -- density falls off smoothly outward instead of forming the "ring spike"
Thoth's own measurement flagged on the v7 layout.

VERIFIED DECLUMP (item 2): `_verify_min_separation` runs AFTER the final global
`_declump` pass and raises `DeclumpVerificationFailed` loudly if any pair is still
closer than `_MIN_SEPARATION - _MIN_SEP_EPSILON` -- declump's own 30-iteration cap is
silent about non-convergence (it just stops), which is exactly how v7's nn p50 2.8
went unnoticed until Thoth measured it live. The hierarchical layout is designed so
declump only ever does bounded local cleanup at reasonable density and should always
converge; this is the tripwire for "it didn't," not an expected outcome.
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
    _neighbor_cell_indices,
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
_SEED_SPACING = 15.0  # same NODE_SPACING scale graph_layout.py's own declump uses,
                      # for a numerically comparable starting scatter.
_SEMANTIC_TYPE_WEIGHT: dict[str, float] = {}  # hook for future per-type tuning
_DEFAULT_SEMANTIC_WEIGHT = 1.0  # every semantic type shares this today -- "weight by
                                # type" is a real mechanism (the dict above), just
                                # empty pending real per-type importance data; degree
                                # normalisation is the only differentiating signal now.
_PHYSICS_LAYOUT_VERSION = 8  # graph_layout._LAYOUT_VERSION must match this -- bumped
                             # together so the incremental heartbeat and this one-shot
                             # migration always agree on what "current" means.
_DEFAULT_PHYSICS_MAX_BYTES = 2_000_000_000  # layout.physics_max_bytes' own default
                                            # (Thoth mail 11097) -- see _memory_guard's
                                            # own docstring for what this actually checks.

# HIERARCHICAL PHYSICS (v8, Thoth mail 11128) -----------------------------------
_LEVEL1_RADIUS_K = 0.75  # R_p = k * sqrt(N_p) * _NODE_SPACING -- empirically sized so
                         # a project's own members, hex-packed at the min-sep floor,
                         # roughly fill a disc of this radius (area argument: N*s^2 ~=
                         # pi*R^2*0.9 packing factor -> k ~= 0.6; 0.75 leaves FR's own
                         # non-uniform spread (denser center, sparser fringe) headroom
                         # without pushing the acceptance's nn p50 [15,25] band high).
_LEVEL1_GUTTER = 3 * _MIN_SEPARATION  # extra clearance beyond R_a+R_b between any two
                                      # project discs -- the acceptance line only
                                      # requires >= R_a+R_b; a real gutter keeps a
                                      # bridging member's own facing-edge nudge (below)
                                      # from ever pushing it into the NEXT project.
_LEVEL1_FR_ITERATIONS = 500  # a small graph (one vertex per project) -- generous
                             # iteration budget costs nothing at this vertex count.
_LEVEL1_SEPARATION_ITERATIONS = 300  # bounded like `_declump`'s own cap; separating
                                     # one pair can nudge another back together, this
                                     # many passes is enough to converge in practice
                                     # for a fleet-scale project count.
_LEVEL1_SEED_SPACING = 200.0  # starting scatter scale for the level-1 FR seed --
                              # `_separate_extents` corrects the real spacing
                              # regardless, this only needs to be roughly the right
                              # order of magnitude so FR's own repulsion has room.
_LEVEL2_FR_ITERATIONS = 200  # one project's own members only -- converges faster
                             # than the old whole-graph v7 pass at the same iteration
                             # count, since there's no longer a 50,000-vertex graph
                             # to relax in a single FR call.
_CROSS_PROJECT_BRIDGE_NUDGE = 0.15  # fraction of the remaining distance a bridging
                                    # member is nudged toward the OTHER project's own
                                    # centroid (item 3) -- small enough that the
                                    # member stays inside its own project's disc
                                    # (bounded by `_LEVEL1_GUTTER`'s own clearance),
                                    # large enough to visibly favour the facing edge.
_UNFILED_FOG_MIN_STD = 5 * _MIN_SEPARATION  # floor for the density-falloff Gaussian's
                                            # own spread when nothing has been placed
                                            # yet to measure a real cloud from (an
                                            # empty-graph edge case, never the live
                                            # population).
_MIN_SEP_EPSILON = 0.5  # numerical slack `_verify_min_separation` allows below the
                        # hard floor -- float rounding across `_declump`'s own
                        # iterations can leave a pair a few thousandths short even
                        # when it fully converged; this is tolerance for that, never
                        # a loophole for a real violation.
_VERIFY_MAX_CANDIDATES = 2000  # a cell-pair candidate count above this is treated as
                               # an outright verification failure rather than paying
                               # for the full pairwise check -- this many points
                               # sharing a min-sep neighbourhood already means
                               # declump did not converge; see
                               # `_verify_min_separation`'s own docstring.
_HUB_ZONE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")  # sentinel level-1
                            # vertex for THE HUB ZONE (live specimen, first real
                            # migration attempt on bcc6b3f3: hubs snapped to the raw
                            # centroid-of-everything with only a 1-unit jitter
                            # collided with a dense project's own disc sitting right
                            # at that centroid -- DeclumpVerificationFailed caught it,
                            # a pair 1.664 units apart under the 15-unit floor. A
                            # fixed low-valued UUID, not drawn from the same uuid4
                            # generator that mints every real object id, so a
                            # collision is not just unlikely, it needs one of this
                            # house's own object ids to have been minted OUTSIDE
                            # `uuid.uuid4()` -- giving the hub cluster its OWN radius
                            # budget and running it through the SAME
                            # `_separate_extents` pass as every real project is what
                            # actually guarantees it never lands inside one again.


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


def _level1_radius(n_members: int) -> float:
    """R_p = k * sqrt(N_p) * spacing (Thoth mail 11128 item 1) -- a project's own
    radius budget in the level-1 contracted layout."""
    return max(_MIN_SEPARATION, _LEVEL1_RADIUS_K * math.sqrt(max(1, n_members)) * _MIN_SEPARATION)


def _cross_project_edges(
    link_rows: list[asyncpg.Record], membership: dict[uuid.UUID, uuid.UUID],
    project_ids: set[uuid.UUID],
) -> dict[tuple[uuid.UUID, uuid.UUID], float]:
    """One aggregated weighted edge per unordered (project_a, project_b) pair,
    counting every live semantic (non-container, non-structural) link between a
    member of one and a member of the other -- level 1's own spring weights, and
    the same population `_apply_bridge_nudges` walks again for item 3's per-member
    nudge."""
    counts: dict[tuple[uuid.UUID, uuid.UUID], int] = defaultdict(int)
    for r in link_rows:
        f, t, lt = r["from_id"], r["to_id"], r["type"]
        if lt in CONTAINER_LINK_TYPES or lt in STRUCTURAL_LINK_TYPES:
            continue
        pf, pt = membership.get(f), membership.get(t)
        if pf is None or pt is None or pf == pt or pf not in project_ids or pt not in project_ids:
            continue
        key = (pf, pt) if str(pf) <= str(pt) else (pt, pf)
        counts[key] += 1
    return {k: float(v) for k, v in counts.items()}


def _separate_extents(
    pos: np.ndarray, radii: np.ndarray, *,
    gutter: float = _LEVEL1_GUTTER, iterations: int = _LEVEL1_SEPARATION_ITERATIONS,
) -> np.ndarray:
    """Push every pair of discs (a centroid plus its own radius) apart until their
    centroid distance is at least the sum of their radii plus `gutter` -- level 1's
    own extent-aware repulsion (Thoth mail 11128 item 1), the same deterministic
    push-by-the-deficit shape `graph_layout._declump` uses for a shared floor, keyed
    here on each vertex's own radius instead. THIS is what stops two projects'
    member clouds from ever occupying the same world-space region -- FR's own
    repulsion alone (as v7 showed) only ever approaches separation, never
    guarantees it, and a whole project's own hundreds-of-units spread makes that gap
    catastrophic rather than cosmetic."""
    n = len(pos)
    if n < 2:
        return pos
    pos = pos.copy()
    for _ in range(iterations):
        diff = pos[:, None, :] - pos[None, :, :]
        dist = np.sqrt((diff ** 2).sum(axis=-1))
        want = radii[:, None] + radii[None, :] + gutter
        np.fill_diagonal(dist, np.inf)
        violation = want - dist
        np.fill_diagonal(violation, 0.0)  # never -inf: that would multiply against a
                                          # 0 direction vector below and raise an
                                          # "invalid value" warning for a value
                                          # `mask` was already going to discard
        if np.all(violation <= 1e-6):
            break
        mask = violation > 0
        safe_dist = np.where(dist > 1e-9, dist, 1e-9)
        direction = diff / safe_dist[..., None]
        push = np.where(mask[..., None], direction * (violation[..., None] / 2), 0.0)
        pos = pos + push.sum(axis=1)
    return pos


def _level1_layout(
    project_ids: list[uuid.UUID], radii: dict[uuid.UUID, float],
    cross_edges: dict[tuple[uuid.UUID, uuid.UUID], float],
) -> dict[uuid.UUID, np.ndarray]:
    """One vertex per project, FR over the cross-project semantic aggregate, then
    `_separate_extents` so no two project discs ever overlap -- see the module
    docstring's HIERARCHICAL PHYSICS section for the full shape."""
    if not project_ids:
        return {}
    idx = {pid: i for i, pid in enumerate(project_ids)}
    g = ig.Graph()
    g.add_vertices(len(project_ids))
    edges: list[tuple[int, int]] = []
    weights: list[float] = []
    for (a, b), w in cross_edges.items():
        edges.append((idx[a], idx[b]))
        weights.append(w)
    g.add_edges(edges)
    seed = np.array([_sunflower_point(i, _LEVEL1_SEED_SPACING) for i in range(len(project_ids))])
    coords = g.layout_fruchterman_reingold(
        weights=weights if weights else None, niter=_LEVEL1_FR_ITERATIONS,
        seed=seed.tolist(), grid=True)
    pos = np.array(coords.coords)
    radii_arr = np.array([radii[pid] for pid in project_ids])
    pos = _separate_extents(pos, radii_arr)
    return {pid: pos[i] for i, pid in enumerate(project_ids)}


def _level2_layout_for_project(
    pid: uuid.UUID, members: list[uuid.UUID], link_rows: list[asyncpg.Record],
    communities: dict[uuid.UUID, tuple[uuid.UUID, int]], centroid: np.ndarray, radius: float,
) -> dict[uuid.UUID, np.ndarray]:
    """One project's own internal FR pass -- reuses `_build_physics_graph` UNCHANGED
    (semantic springs, district/community gravity, THE COLLAPSED-CONTAINER FIX's
    1/member-count container weight), scoped to just `[pid, *members]` so container
    and semantic edges outside this project are dropped by that function's own
    `idx` membership check. The project's own vertex is an ordinary participant, so
    its FINAL FR position -- not the origin -- is what the whole subgraph recenters
    on (`pos[0]` since `pid` is always first in `object_ids`), exactly mirroring how
    v7's flat layout let a container's own position be "wherever the pull leaves
    it". Rescaled so the 95th-percentile member radius from that recentred point
    matches this project's own `radius` (a `_LEVEL1_RADIUS_K`-sized disc), then
    translated onto `centroid`."""
    object_ids = [pid, *members]
    g, vertex_ids = _build_physics_graph(object_ids, link_rows, communities)
    seed = _seed_positions(vertex_ids)
    coords = g.layout_fruchterman_reingold(
        weights=g.es["weight"] if g.ecount() else None,
        niter=_LEVEL2_FR_ITERATIONS, seed=seed.tolist(), grid=True)
    pos = np.array(coords.coords)[: len(object_ids)]
    pos = pos - pos[0]
    member_pos = pos[1:]
    if len(member_pos) == 0:
        return {}
    extent = float(np.percentile(np.linalg.norm(member_pos, axis=1), 95))
    scale = radius / extent if extent > 1e-6 else 1.0
    member_pos = member_pos * scale + centroid
    return {oid: member_pos[i] for i, oid in enumerate(members)}


def _apply_bridge_nudges(
    positions: dict[uuid.UUID, np.ndarray], link_rows: list[asyncpg.Record],
    membership: dict[uuid.UUID, uuid.UUID], project_centroids: dict[uuid.UUID, np.ndarray],
) -> None:
    """Cross-project semantic edges (Thoth mail 11128 item 3): a weak nudge toward
    the OTHER project's own level-1 centroid so a bridging member settles nearer the
    facing edge of its own project's disc -- mutates `positions` in place, applied
    once after both levels place everything. Disclosed simplification: folding this
    into level 2's own FR would mean reconciling two very different coordinate
    scales inside one force integration; a bounded post-hoc nudge (capped at
    `_CROSS_PROJECT_BRIDGE_NUDGE` of the remaining distance, well inside
    `_LEVEL1_GUTTER`'s own clearance) sidesteps that."""
    nudges: dict[uuid.UUID, np.ndarray] = defaultdict(lambda: np.zeros(2))
    counts: dict[uuid.UUID, int] = defaultdict(int)
    for r in link_rows:
        f, t, lt = r["from_id"], r["to_id"], r["type"]
        if lt in CONTAINER_LINK_TYPES or lt in STRUCTURAL_LINK_TYPES:
            continue
        pf, pt = membership.get(f), membership.get(t)
        if pf is None or pt is None or pf == pt:
            continue
        if f in positions and pt in project_centroids:
            nudges[f] = nudges[f] + (project_centroids[pt] - positions[f])
            counts[f] += 1
        if t in positions and pf in project_centroids:
            nudges[t] = nudges[t] + (project_centroids[pf] - positions[t])
            counts[t] += 1
    for oid, total in nudges.items():
        direction = total / counts[oid]
        positions[oid] = positions[oid] + direction * _CROSS_PROJECT_BRIDGE_NUDGE


def _place_unfiled(
    unfiled_ids: list[uuid.UUID], link_rows: list[asyncpg.Record],
    placed: dict[uuid.UUID, np.ndarray],
) -> dict[uuid.UUID, np.ndarray]:
    """Unfiled objects (Thoth mail 11128 item 4): one with a live semantic link to
    an already-placed object lands at the mean position of those neighbours; one
    with none scatters as a proper isotropic 2D Gaussian (Box-Muller polar form)
    centred on the whole placed cloud's own centroid, std from that cloud's own
    spread -- density falls off smoothly outward instead of the fixed-radius ring
    Thoth's own measurement flagged as a spike on the v7 layout."""
    neighbours: dict[uuid.UUID, list[np.ndarray]] = defaultdict(list)
    unfiled_set = set(unfiled_ids)
    for r in link_rows:
        f, t, lt = r["from_id"], r["to_id"], r["type"]
        if lt in CONTAINER_LINK_TYPES or lt in STRUCTURAL_LINK_TYPES:
            continue
        if f in unfiled_set and t in placed:
            neighbours[f].append(placed[t])
        if t in unfiled_set and f in placed:
            neighbours[t].append(placed[f])

    if placed:
        all_pos = np.array(list(placed.values()))
        cloud_center = all_pos.mean(axis=0)
        cloud_std = max(float(all_pos.std()), _UNFILED_FOG_MIN_STD)
    else:
        cloud_center = np.zeros(2)
        cloud_std = _UNFILED_FOG_MIN_STD

    out: dict[uuid.UUID, np.ndarray] = {}
    for oid in unfiled_ids:
        pts = neighbours.get(oid)
        if pts:
            out[oid] = np.array(pts).mean(axis=0)
        else:
            u1 = max(_hash01(f"fog-r1:{oid}"), 1e-9)
            u2 = _hash01(f"fog-r2:{oid}")
            radius = math.sqrt(-2.0 * math.log(u1)) * cloud_std
            angle = u2 * 2 * math.pi
            out[oid] = cloud_center + np.array(
                [radius * math.cos(angle), radius * math.sin(angle)])
    return out


class DeclumpVerificationFailed(Exception):
    """Raised by `_verify_min_separation` when the post-declump population still
    holds a pair closer than `_MIN_SEPARATION - _MIN_SEP_EPSILON` -- `_declump`'s
    own iteration cap is silent about non-convergence (it just stops), which is
    exactly how v7's nn p50 2.8 went unnoticed until Thoth measured it live (mail
    11128 item 2, "VERIFY it ... fail loudly if not"). The hierarchical layout is
    designed so declump only ever does bounded local cleanup at reasonable density
    and should always converge; this is the tripwire for "it didn't"."""


def _verify_min_separation(pos: np.ndarray, *, min_sep: float = _MIN_SEPARATION) -> None:
    if len(pos) < 2:
        return
    cells = _grid_cells(pos, min_sep)
    for (cx, cy), _idxs in cells.items():
        candidates = _neighbor_cell_indices(cells, cx, cy)
        if len(candidates) < 2:
            continue
        if len(candidates) > _VERIFY_MAX_CANDIDATES:
            raise DeclumpVerificationFailed(
                f"cell ({cx},{cy}) holds {len(candidates)} candidate points after "
                "declump -- too dense to verify cheaply, treated as a failure")
        pts = pos[candidates]
        diffs = pts[:, None, :] - pts[None, :, :]
        dists = np.sqrt((diffs ** 2).sum(axis=-1))
        np.fill_diagonal(dists, np.inf)
        local_min = float(dists.min())
        if local_min < min_sep - _MIN_SEP_EPSILON:
            raise DeclumpVerificationFailed(
                f"post-declump verification found a pair {local_min:.3f} units apart "
                f"in cell ({cx},{cy}), under the {min_sep} floor (epsilon "
                f"{_MIN_SEP_EPSILON}) -- declump did not converge")


def _hub_zone_radius(n_hubs: int) -> float:
    """The REAL max radius `_place_hubs_in_zone`'s own raw (never rescaled)
    sunflower spread needs for `n_hubs` points at `_MIN_SEPARATION` -- unlike
    `_level1_radius`'s area-based packing formula (asymptotically accurate for a
    real project's hundreds-to-thousands of members), a small hub count needs the
    EXACT sunflower extent: the packing-density assumption underestimates badly at
    this scale, and `_place_hubs_in_zone` never rescales DOWN to fit a smaller
    budget (that would recreate the very bug this zone exists to fix), so the
    radius fed into `_separate_extents` has to already match what the raw
    placement actually needs, not the other way around."""
    if n_hubs <= 1:
        return _MIN_SEPARATION
    return _MIN_SEPARATION * math.sqrt(n_hubs - 1 + 0.5)


def _place_hubs_in_zone(
    hub_order: list[uuid.UUID], center: np.ndarray,
) -> dict[uuid.UUID, np.ndarray]:
    """THE HUB ZONE (live fix, first bcc6b3f3 migration attempt -- see
    `_HUB_ZONE_ID`'s own docstring): hubs spread by raw `_sunflower_point` (a real
    minimum-pairwise-spacing guarantee among themselves, `hub_order`'s own stable
    creation-order rank), translated onto `center`, NEVER rescaled -- rescaling
    down to fit a smaller radius budget (the way `_level2_layout_for_project`
    rescales a project's own members) would shrink hub-to-hub spacing back below
    the floor this zone exists to guarantee; `_hub_zone_radius` sizes level 1's own
    separation budget to match this placement's real extent instead. Replaces the
    old "jitter by 1 unit around the raw centroid-of-everything" scheme, which had
    no radius budget of its own and could land inside whatever real content
    happened to sit at that centroid."""
    if not hub_order:
        return {}
    raw = np.array([_sunflower_point(i, _MIN_SEPARATION) for i in range(len(hub_order))])
    return {oid: raw[i] + center for i, oid in enumerate(hub_order)}


async def _physics_positions(
    actions: Actions,
) -> dict[uuid.UUID, tuple[float, float]]:
    """THE HIERARCHICAL PHYSICS pipeline (v8, Thoth mail 11128) -- pure enough to
    unit-test without a live migration write past the DB reads at the top:
    population + links -> communities -> level 1 (project-contracted FR +
    extent-aware separation) -> level 2 (one FR pass per project, rescaled onto its
    own level-1 disc) -> cross-project bridge nudges -> unfiled placement -> hub
    re-centering -> the memory guard -> the existing declump floor -> VERIFIED.
    Real object positions only. Can raise `MemoryBudgetExceeded` or
    `DeclumpVerificationFailed` -- the caller (`run_physics_migrate`) turns either
    into a written refusal receipt rather than letting a bad layout write."""
    object_ids = await _active_object_ids(actions)
    if not object_ids:
        return {}
    link_rows = await _live_link_rows(actions)
    membership = await _project_membership(actions)
    active = set(object_ids)
    communities = _detect_communities(link_rows, membership, active)

    groups: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    member_set: set[uuid.UUID] = set()
    for oid in object_ids:
        pid = membership.get(oid)
        if pid is not None and pid in active:
            groups[pid].append(oid)
            member_set.add(oid)
    project_ids = sorted(groups.keys(), key=str)
    project_id_set = set(project_ids)
    unfiled_ids = [oid for oid in object_ids if oid not in member_set and oid not in project_id_set]

    hub_ids = await _hub_ids(actions, object_ids)
    hub_order = [oid for oid in object_ids if oid in hub_ids]

    radii = {pid: _level1_radius(len(groups[pid])) for pid in project_ids}
    level1_ids = list(project_ids)
    if hub_order:
        radii[_HUB_ZONE_ID] = _hub_zone_radius(len(hub_order))
        level1_ids.append(_HUB_ZONE_ID)
    cross_edges = _cross_project_edges(link_rows, membership, project_id_set)
    centroids = _level1_layout(level1_ids, radii, cross_edges)

    positions: dict[uuid.UUID, np.ndarray] = {}
    for pid in project_ids:
        positions[pid] = centroids[pid]
        positions.update(_level2_layout_for_project(
            pid, groups[pid], link_rows, communities, centroids[pid], radii[pid]))

    _apply_bridge_nudges(positions, link_rows, membership, centroids)
    positions.update(_place_unfiled(unfiled_ids, link_rows, positions))

    if hub_order:
        positions.update(_place_hubs_in_zone(hub_order, centroids[_HUB_ZONE_ID]))

    real_positions = np.array([positions[oid] for oid in object_ids])
    reason = await _memory_guard(actions, real_positions)
    if reason:
        raise MemoryBudgetExceeded(reason)

    declumped = _declump(
        real_positions, np.zeros((0, 2)), object_ids, min_sep=_MIN_SEPARATION)
    _verify_min_separation(declumped)

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
