"""THE GRAPH VISUALIZER (wave B item 1) + NAVIGABLE SPACE, THE SERVER piece A (rulings
f832c3a4 + 0a3d6719, thread b6cb1d7c0b36), the DECLUMP FIX (Thoth mail 10582,
PRIORITY), THE READING LAYER (Thoth mail 10595, ruling c5953bb1), and THE LEGIBILITY
PASS (ruling e1cb9e3b, tip 2h): the layout heartbeat places every object under a
rank-based sunflower rule (never a hash-into-a-fixed-circle), nudged by a bounded
intra-project SEMANTIC-only relax that ends with a hard minimum-separation pass;
project centers come from a weighted force layout over the contracted project graph,
never a rank; within a project, placement is by semantic adjacency (connected inner
disc vs zero-semantic-edge outer halo), never a type ring."""
from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

from src.actions.core import Actions
from src.orchestrator.graph_layout import (
    _LAYOUT_VERSION_PROP,
    _MIN_SEPARATION,
    _adjacency_ranks,
    _declump,
    _hub_ids,
    _intra_project_neighbors,
    _neighbors_of,
    _place_projects,
    _relax_projects,
    _release_layout_lock,
    _try_acquire_layout_lock,
    adjacency_position,
    layout_batch,
    positions_for,
    project_center,
    relax,
    run_layout_migrate,
    unplaced_batch,
)

# --- pure placement rule: deterministic, idempotent, never overlapping ---------------


def test_relax_positions_every_unplaced_node() -> None:
    ids = [uuid.uuid4() for _ in range(12)]
    neighbors = {ids[i]: {ids[(i + 1) % 12], ids[(i - 1) % 12]} for i in range(12)}
    out = relax(ids, neighbors, {}, iterations=20)
    assert set(out.keys()) == set(ids)
    for x, y in out.values():
        assert math.isfinite(x) and math.isfinite(y)


def test_relax_is_deterministic_for_the_same_seed() -> None:
    ids = [uuid.uuid4() for _ in range(8)]
    neighbors = {ids[i]: {ids[(i + 1) % 8]} for i in range(8)}
    a = relax(ids, neighbors, {}, iterations=15, seed=7)
    b = relax(ids, neighbors, {}, iterations=15, seed=7)
    assert a == b


def test_relax_respects_fixed_anchors_never_moving_them() -> None:
    center, anchor = uuid.uuid4(), uuid.uuid4()
    out = relax([center], {center: {anchor}}, {anchor: (500.0, 500.0)}, iterations=40)
    x, y = out[center]
    # pulled toward the anchor, not left near the random seed origin
    assert x > 50 and y > 50


def test_relax_pulls_unconnected_nodes_apart_not_together() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    out = relax([a, b], {a: set(), b: set()}, {}, iterations=30)
    dist = math.dist(out[a], out[b])
    assert dist > 10  # repulsion, not a coincidental overlap


def test_relax_with_init_starts_from_the_given_base_not_a_random_seed() -> None:
    lone = uuid.uuid4()
    out = relax([lone], {lone: set()}, {}, iterations=0, init={lone: (123.0, 456.0)})
    assert out[lone] == (123.0, 456.0)


def test_project_center_is_a_pure_function_of_rank_alone() -> None:
    assert project_center(3) == project_center(3)
    assert project_center(3) != project_center(4)


def test_project_center_ranks_never_collide_across_a_realistic_range() -> None:
    """The declump fix's whole point at the project level: no two ranks in a
    realistic range (dozens of real projects) land on the same point, or anywhere
    near it relative to the spacing constant."""
    points = [project_center(r) for r in range(60)]
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            assert math.dist(points[i], points[j]) > 100


def test_adjacency_position_is_deterministic_and_pure() -> None:
    center = project_center(3)
    p1 = adjacency_position(center, True, 7)
    p2 = adjacency_position(center, True, 7)
    assert p1 == p2


def test_adjacency_position_halo_band_clears_the_connected_bands_own_extent() -> None:
    """THE LEGIBILITY PASS (ruling e1cb9e3b): a zero-semantic-edge object sits in an
    outer halo, strictly beyond even a large connected disc's own worst-case extent."""
    center = project_center(0)
    connected_pos = adjacency_position(center, True, 5000)
    halo_pos = adjacency_position(center, False, 0)
    r_connected = math.dist(center, connected_pos)
    r_halo = math.dist(center, halo_pos)
    assert r_halo > r_connected


def test_adjacency_position_ranks_within_one_band_never_collide_at_realistic_scale() -> None:
    """The declump fix's whole point at the object level: a (project, connected) band
    in the low thousands (this house's own real worst case is in the tens of
    thousands) still gets every rank a distinct, well-separated point."""
    center = project_center(0)
    points = [adjacency_position(center, True, r) for r in range(500)]
    seen: set[tuple[float, float]] = set()
    for p in points:
        rounded = (round(p[0], 3), round(p[1], 3))
        assert rounded not in seen, "two ranks landed on the exact same point"
        seen.add(rounded)
    # a sampled spot-check of near neighbors (rank r and r+1) never collapse together
    for r in range(0, 490, 37):
        assert math.dist(points[r], points[r + 1]) > 0.5


def test_intra_project_neighbors_drops_cross_project_edges() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    proj_x, proj_y = uuid.uuid4(), uuid.uuid4()
    neighbors = {a: {b, c}}
    proj_type: dict[uuid.UUID, tuple[uuid.UUID | None, str]] = {
        a: (proj_x, "Thread"), b: (proj_x, "Thread"), c: (proj_y, "Thread")}
    out = _intra_project_neighbors([a], neighbors, proj_type)
    assert out[a] == {b}


# --- _declump: the hard minimum-separation pass (Thoth mail 10582) -------------------


def test_declump_separates_two_coincident_points() -> None:
    import numpy as np

    ids = [uuid.uuid4(), uuid.uuid4()]
    pos = np.array([[10.0, 10.0], [10.0, 10.0]])
    out = _declump(pos, np.zeros((0, 2)), ids)
    assert math.dist(out[0], out[1]) >= _MIN_SEPARATION - 1e-6


def test_declump_leaves_already_separated_points_alone() -> None:
    import numpy as np

    ids = [uuid.uuid4(), uuid.uuid4()]
    pos = np.array([[0.0, 0.0], [1000.0, 1000.0]])
    out = _declump(pos, np.zeros((0, 2)), ids)
    assert out[0].tolist() == [0.0, 0.0]
    assert out[1].tolist() == [1000.0, 1000.0]


def test_declump_pushes_a_node_away_from_a_fixed_anchor() -> None:
    import numpy as np

    ids = [uuid.uuid4()]
    pos = np.array([[5.0, 5.0]])
    anchors = np.array([[5.0, 5.0]])
    out = _declump(pos, anchors, ids)
    assert math.dist(out[0], anchors[0]) >= _MIN_SEPARATION - 1e-6


def test_relax_never_leaves_two_strongly_attracted_nodes_stacked() -> None:
    """The exact regression Thoth's mail described: strong mutual attraction (many
    shared edges, tight ideal length) used to be able to collapse two nodes onto
    (almost) the same point; the declump pass now guarantees it can't."""
    a, b = uuid.uuid4(), uuid.uuid4()
    out = relax([a, b], {a: {b}, b: {a}}, {}, iterations=50,
                init={a: (0.0, 0.0), b: (0.01, 0.0)})
    assert math.dist(out[a], out[b]) >= _MIN_SEPARATION - 1e-6


# --- DB-backed: unplaced_batch / layout_batch / positions_for -------------------------


async def test_unplaced_batch_finds_objects_with_no_layout_marker_yet(actions: Actions) -> None:
    oid = await actions.create_or_find_object("Thread", "thread:gl-unpos", "test")
    batch = await unplaced_batch(actions)
    assert oid in batch


async def test_layout_batch_stamps_graph_x_graph_y_and_the_version_marker(
    actions: Actions,
) -> None:
    a = await actions.create_or_find_object("Thread", "thread:gl-a", "test")
    b = await actions.create_or_find_object("Thread", "thread:gl-b", "test")
    await actions.create_link(a, b, "cites", "test", datetime.now(UTC), 1.0)

    placed = await layout_batch(actions, limit=1000)
    assert placed >= 2

    positions = await positions_for(actions, [a, b])
    assert a in positions and b in positions
    assert all(math.isfinite(v) for v in (*positions[a], *positions[b]))

    marker = await actions.pool.fetchval(
        "SELECT value #>> '{}' FROM current_assertions WHERE object_id=$1 AND name=$2",
        a, _LAYOUT_VERSION_PROP)
    assert marker is not None


async def test_layout_batch_never_repositions_an_already_positioned_object(
    actions: Actions,
) -> None:
    a = await actions.create_or_find_object("Thread", "thread:gl-stable", "test")
    await layout_batch(actions, limit=1000)
    first = (await positions_for(actions, [a]))[a]

    b = await actions.create_or_find_object("Thread", "thread:gl-stable-friend", "test")
    await actions.create_link(a, b, "cites", "test", datetime.now(UTC), 1.0)
    await layout_batch(actions, limit=1000)
    second = (await positions_for(actions, [a]))[a]
    assert first == second


async def test_layout_batch_returns_zero_when_the_graph_is_fully_positioned(
    actions: Actions,
) -> None:
    await actions.create_or_find_object("Thread", "thread:gl-only", "test")
    n1 = await layout_batch(actions, limit=1000)
    assert n1 >= 1
    n2 = await layout_batch(actions, limit=1000)
    assert n2 == 0


async def test_layout_batch_with_no_explicit_limit_reads_the_settings_table(
    actions: Actions,
) -> None:
    """`layout.batch_size` (Thoth mail 10609) genuinely reads live -- not the env-
    overlay path, which only covers effect='immediate' keys."""
    from src.orchestrator.settings_service import write_setting

    await actions.create_or_find_object("Thread", "thread:gl-settings-a", "test")
    await actions.create_or_find_object("Thread", "thread:gl-settings-b", "test")
    await write_setting(actions.pool, "layout.batch_size", 1, actor="analyst:operator")

    n = await layout_batch(actions)  # no explicit limit -- must read the table
    assert n == 1  # capped to the stored batch_size regardless of total population


# --- THE MIGRATION DOOR (Thoth mail 10609) --------------------------------------------


async def test_layout_lock_round_trips(actions: Actions) -> None:
    async with actions.pool.acquire() as conn:
        assert await _try_acquire_layout_lock(conn) is True
        # a SECOND session (a fresh connection) can't also acquire it
        async with actions.pool.acquire() as conn2:
            assert await _try_acquire_layout_lock(conn2) is False
        await _release_layout_lock(conn)
        # released -- a fresh session can now acquire it
        async with actions.pool.acquire() as conn3:
            assert await _try_acquire_layout_lock(conn3) is True
            await _release_layout_lock(conn3)


async def test_run_layout_migrate_places_everything_and_yields_a_receipt_per_batch(
    actions: Actions,
) -> None:
    """Never assumes the DB fixture is empty (migrations/fixtures may already seed
    real objects) -- proves the ACTUAL acceptance shape: drains to quiescence, the
    receipts' own running total matches, and every object THIS test created ends up
    positioned."""
    ids = []
    for i in range(3):
        oid = await actions.create_or_find_object("Thread", f"thread:gl-migrate-{i}", "test")
        ids.append(oid)

    receipts = [r async for r in run_layout_migrate(actions, limit=1000)]
    assert receipts[-1]["placed"] == 0  # drained to quiescence
    assert receipts[-1]["total_placed"] == sum(r["placed"] for r in receipts[:-1])
    assert await unplaced_batch(actions) == []
    positions = await positions_for(actions, ids)
    assert set(positions.keys()) == set(ids)


async def test_run_layout_migrate_refuses_while_the_lock_is_held(
    actions: Actions,
) -> None:
    await actions.create_or_find_object("Thread", "thread:gl-migrate-locked", "test")
    async with actions.pool.acquire() as holder:
        assert await _try_acquire_layout_lock(holder) is True
        try:
            receipts = [r async for r in run_layout_migrate(actions)]
            assert receipts == [{
                "error": "the layout heartbeat (or another migrate run) currently "
                        "holds the layout lock -- try again shortly"}]
        finally:
            await _release_layout_lock(holder)


async def test_layout_batch_keeps_two_projects_separated(actions: Actions) -> None:
    now = datetime.now(UTC)
    proj_a = await actions.create_or_find_object("SoftwareProject", "repo:gl-proj-a", "test")
    proj_b = await actions.create_or_find_object("SoftwareProject", "repo:gl-proj-b", "test")
    a = await actions.create_or_find_object("Thread", "thread:gl-in-a", "test")
    b = await actions.create_or_find_object("Thread", "thread:gl-in-b", "test")
    await actions.create_link(a, proj_a, "in_repo", "test", now, 1.0)
    await actions.create_link(b, proj_b, "in_repo", "test", now, 1.0)

    await layout_batch(actions, limit=1000)
    pos = await positions_for(actions, [a, b])
    assert math.dist(pos[a], pos[b]) > 10


async def test_layout_batch_separates_many_objects_of_the_same_type_in_one_project(
    actions: Actions,
) -> None:
    """THE REGRESSION ITSELF, proven at DB scale: a (project, type) group well past
    what a single fixed-radius ring could hold apart now places every member with
    real minimum separation, never a stack."""
    now = datetime.now(UTC)
    proj = await actions.create_or_find_object(
        "SoftwareProject", "repo:gl-crowded-project", "test")
    ids = []
    for i in range(40):
        oid = await actions.create_or_find_object("Thread", f"thread:gl-crowd-{i}", "test")
        await actions.create_link(oid, proj, "in_repo", "test", now, 1.0)
        ids.append(oid)

    placed = 0
    while True:
        n = await layout_batch(actions, limit=1000)
        placed += n
        if n == 0:
            break

    pos = await positions_for(actions, ids)
    points = [pos[oid] for oid in ids]
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            assert math.dist(points[i], points[j]) >= _MIN_SEPARATION - 1e-6


async def test_layout_batch_migrates_an_object_placed_under_a_prior_version(
    actions: Actions,
) -> None:
    """An object carrying graph_x/graph_y from an older layout (missing today's version
    marker) is swept up by the very next tick and re-placed under the current scheme --
    the mechanism a version bump relies on for its one-time migration pass."""
    from src.orchestrator import graph_layout as gl

    oid = await actions.create_or_find_object("Thread", "thread:gl-old-scheme", "test")
    now = datetime.now(UTC)
    await actions.assert_property(oid, "graph_x", -999.0, gl.GRAPH_LAYOUT_SOURCE, now, 0.9)
    await actions.assert_property(oid, "graph_y", -999.0, gl.GRAPH_LAYOUT_SOURCE, now, 0.9)

    assert oid in await unplaced_batch(actions)
    n = await layout_batch(actions, limit=1000)
    assert n >= 1
    pos = (await positions_for(actions, [oid]))[oid]
    assert pos != (-999.0, -999.0)
    assert oid not in await unplaced_batch(actions)


# --- THE READING LAYER (Thoth mail 10595, ruling c5953bb1) ---------------------------


async def test_neighbors_of_semantic_only_drops_structural_edges(actions: Actions) -> None:
    now = datetime.now(UTC)
    proj = await actions.create_or_find_object("SoftwareProject", "repo:gl-rl-proj", "test")
    a = await actions.create_or_find_object("Thread", "thread:gl-rl-a", "test")
    b = await actions.create_or_find_object("Thread", "thread:gl-rl-b", "test")
    await actions.create_link(a, proj, "in_repo", "test", now, 1.0)  # structural
    await actions.create_link(a, b, "cites", "test", now, 1.0)  # semantic

    all_neighbors = await _neighbors_of(actions, [a])
    semantic_neighbors = await _neighbors_of(actions, [a], semantic_only=True)
    assert proj in all_neighbors[a] and b in all_neighbors[a]
    assert proj not in semantic_neighbors[a]
    assert b in semantic_neighbors[a]


async def test_layout_batch_never_pulls_two_objects_together_over_a_structural_edge(
    actions: Actions,
) -> None:
    """The exact regression THE READING LAYER fixes: a shared structural hub (here, a
    project acting as the hub every member links to via in_repo) must never pull
    members toward it via relax -- only a real semantic edge should ever attract."""
    now = datetime.now(UTC)
    proj = await actions.create_or_find_object("SoftwareProject", "repo:gl-rl-hub", "test")
    members = []
    for i in range(6):
        oid = await actions.create_or_find_object("Thread", f"thread:gl-rl-member-{i}", "test")
        await actions.create_link(oid, proj, "in_repo", "test", now, 1.0)
        members.append(oid)

    while await layout_batch(actions, limit=1000) > 0:
        pass

    proj_pos = (await positions_for(actions, [proj]))[proj]
    member_pos = await positions_for(actions, members)
    # members carry no semantic edge at all (only the structural in_repo link), so
    # they sit in the outer HALO band around the project center, not collapsed onto
    # the project's own position the way an in_repo-as-attraction bug would do
    for oid in members:
        assert math.dist(proj_pos, member_pos[oid]) > 10


async def test_place_projects_pulls_a_linked_project_closer_than_an_unlinked_one(
    actions: Actions,
) -> None:
    """THE READING LAYER's own acceptance line: no two linked projects' centers
    farther apart than an unlinked pair with similar radii."""
    now = datetime.now(UTC)
    hub = await actions.create_or_find_object("SoftwareProject", "repo:gl-rl-hub-p", "test")
    linked = await actions.create_or_find_object("SoftwareProject", "repo:gl-rl-linked-p",
                                                  "test")
    unlinked = await actions.create_or_find_object(
        "SoftwareProject", "repo:gl-rl-unlinked-p", "test")

    hub_member = await actions.create_or_find_object("Thread", "thread:gl-rl-hub-m", "test")
    linked_member = await actions.create_or_find_object(
        "Thread", "thread:gl-rl-linked-m", "test")
    unlinked_member = await actions.create_or_find_object(
        "Thread", "thread:gl-rl-unlinked-m", "test")
    await actions.create_link(hub_member, hub, "in_repo", "test", now, 1.0)
    await actions.create_link(linked_member, linked, "in_repo", "test", now, 1.0)
    await actions.create_link(unlinked_member, unlinked, "in_repo", "test", now, 1.0)
    # a real cross-project reference: hub <-> linked, nothing to unlinked
    await actions.create_link(hub_member, linked_member, "cites", "test", now, 1.0)

    while await layout_batch(actions, limit=1000) > 0:
        pass

    pos = await positions_for(actions, [hub, linked, unlinked])
    dist_linked = math.dist(pos[hub], pos[linked])
    dist_unlinked = math.dist(pos[hub], pos[unlinked])
    assert dist_linked < dist_unlinked


async def test_place_projects_positions_every_unplaced_project(actions: Actions) -> None:
    a = await actions.create_or_find_object("SoftwareProject", "repo:gl-rl-direct-a", "test")
    b = await actions.create_or_find_object("SoftwareProject", "repo:gl-rl-direct-b", "test")
    out = await _place_projects(actions, [a, b])
    assert set(out.keys()) == {a, b}
    for x, y in out.values():
        assert math.isfinite(x) and math.isfinite(y)


async def test_place_projects_never_overlaps_two_projects_content_radii(
    actions: Actions,
) -> None:
    a_id = uuid.uuid4()
    b_id = uuid.uuid4()
    radius = {a_id: 100.0, b_id: 100.0}
    out = _relax_projects([a_id, b_id], {}, radius, {})
    dist = math.dist(out[a_id], out[b_id])
    assert dist >= radius[a_id] + radius[b_id] + 300.0 - 1e-6  # + the fixed gutter


async def test_hub_ids_finds_a_structural_high_degree_object(actions: Actions) -> None:
    from src.orchestrator.graph_layout import _HUB_DEGREE_THRESHOLD

    hub = await actions.create_or_find_object("Person", "principal:gl-rl-hub-person", "test")
    now = datetime.now(UTC)
    for i in range(_HUB_DEGREE_THRESHOLD + 1):
        agent = await actions.create_or_find_object("Agent", f"agent:gl-rl-hub-{i}", "test")
        await actions.create_link(agent, hub, "acts_for", "test", now, 1.0)

    found = await _hub_ids(actions, [hub])
    assert hub in found


async def test_hub_ids_excludes_an_ordinary_low_degree_object(actions: Actions) -> None:
    oid = await actions.create_or_find_object("Thread", "thread:gl-rl-not-a-hub", "test")
    found = await _hub_ids(actions, [oid])
    assert oid not in found


async def test_layout_batch_pins_a_hub_at_rank_zero_of_its_own_group(
    actions: Actions,
) -> None:
    from src.orchestrator.graph_layout import _HUB_DEGREE_THRESHOLD

    now = datetime.now(UTC)
    # seed enough OTHER halo objects first (no semantic edge, same unfiled project --
    # same (project, connected=False) band the hub itself falls in, since acts_for is
    # structural, not semantic) so a naive creation-order rank would NOT be 0
    for i in range(5):
        await actions.create_or_find_object("Person", f"principal:gl-rl-pin-filler-{i}", "test")
    hub = await actions.create_or_find_object("Person", "principal:gl-rl-pin-hub", "test")
    for i in range(_HUB_DEGREE_THRESHOLD + 1):
        agent = await actions.create_or_find_object("Agent", f"agent:gl-rl-pin-{i}", "test")
        await actions.create_link(agent, hub, "acts_for", "test", now, 1.0)

    while await layout_batch(actions, limit=1000) > 0:
        pass

    from src.orchestrator.graph_layout import adjacency_position as _ap
    from src.orchestrator.graph_layout import project_center as _pc

    expected_rank_0 = _ap(_pc(0), False, 0)
    expected_naive_rank_5 = _ap(_pc(0), False, 5)
    got = (await positions_for(actions, [hub]))[hub]
    # a small declump/relax nudge is expected and fine -- what matters is landing
    # near rank 0's own point, not near where its true creation-order rank (5) would
    # otherwise have put it
    assert math.dist(expected_rank_0, got) < math.dist(expected_naive_rank_5, got)


# --- THE LEGIBILITY PASS (ruling e1cb9e3b, tip 2h): semantic adjacency, no type rings --


async def test_adjacency_ranks_splits_connected_and_halo_into_separate_bands(
    actions: Actions,
) -> None:
    a = await actions.create_or_find_object("Thread", "thread:gl-lp-connected", "test")
    b = await actions.create_or_find_object("Thread", "thread:gl-lp-connected-friend", "test")
    halo = await actions.create_or_find_object("Thread", "thread:gl-lp-halo", "test")
    await actions.create_link(a, b, "cites", "test", datetime.now(UTC), 1.0)  # semantic

    out = await _adjacency_ranks(actions, [a, halo])
    a_connected, a_rank = out[a]
    halo_connected, halo_rank = out[halo]
    assert a_connected is True
    assert halo_connected is False
    # each band ranks independently -- never assume the hermetic test DB's own
    # unfiled/halo band is empty, just that both ranks are real, non-negative values
    assert a_rank >= 0
    assert halo_rank >= 0


async def test_adjacency_ranks_object_with_only_a_structural_edge_is_halo(
    actions: Actions,
) -> None:
    now = datetime.now(UTC)
    proj = await actions.create_or_find_object("SoftwareProject", "repo:gl-lp-struct", "test")
    oid = await actions.create_or_find_object("Thread", "thread:gl-lp-struct-member", "test")
    await actions.create_link(oid, proj, "in_repo", "test", now, 1.0)  # structural only

    out = await _adjacency_ranks(actions, [oid])
    connected, _rank = out[oid]
    assert connected is False


async def test_layout_batch_clusters_semantically_connected_objects_closer_than_halo(
    actions: Actions,
) -> None:
    """THE LEGIBILITY PASS's own acceptance shape: within one project, an object with
    a real semantic edge sits closer to the project center than a same-project object
    with no semantic edge at all."""
    now = datetime.now(UTC)
    proj = await actions.create_or_find_object("SoftwareProject", "repo:gl-lp-cluster", "test")
    connected = await actions.create_or_find_object("Thread", "thread:gl-lp-cl-a", "test")
    connected_friend = await actions.create_or_find_object(
        "Thread", "thread:gl-lp-cl-b", "test")
    halo = await actions.create_or_find_object("Thread", "thread:gl-lp-cl-halo", "test")
    for oid in (connected, connected_friend, halo):
        await actions.create_link(oid, proj, "in_repo", "test", now, 1.0)
    await actions.create_link(connected, connected_friend, "cites", "test", now, 1.0)

    while await layout_batch(actions, limit=1000) > 0:
        pass

    proj_pos = (await positions_for(actions, [proj]))[proj]
    pos = await positions_for(actions, [connected, halo])
    assert math.dist(proj_pos, pos[connected]) < math.dist(proj_pos, pos[halo])
