"""THE GRAPH VISUALIZER (wave B item 1) + NAVIGABLE SPACE, THE SERVER piece A (rulings
f832c3a4 + 0a3d6719, thread b6cb1d7c0b36), plus the DECLUMP FIX (Thoth mail 10582,
PRIORITY): the layout heartbeat places every object under a rank-based sunflower rule
(never a hash-into-a-fixed-circle), nudged by a bounded intra-project relax that ends
with a hard minimum-separation pass."""
from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

from src.actions.core import Actions
from src.orchestrator.graph_layout import (
    _LAYOUT_VERSION_PROP,
    _MIN_SEPARATION,
    _declump,
    _intra_project_neighbors,
    _release_layout_lock,
    _try_acquire_layout_lock,
    base_position,
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


def test_base_position_is_deterministic_and_pure() -> None:
    center = project_center(3)
    p1 = base_position(center, "Thread", 7)
    p2 = base_position(center, "Thread", 7)
    assert p1 == p2


def test_base_position_never_lands_two_types_on_the_same_base_radius() -> None:
    center = project_center(0)
    thread_pos = base_position(center, "Thread", 0)
    decision_pos = base_position(center, "Decision", 0)
    r_thread = math.dist(center, thread_pos)
    r_decision = math.dist(center, decision_pos)
    assert round(r_thread, 6) != round(r_decision, 6)


def test_base_position_ranks_within_one_group_never_collide_at_realistic_scale() -> None:
    """The declump fix's whole point at the object level: a (project, type) group in
    the low thousands (this house's own real worst case is in the tens of thousands)
    still gets every rank a distinct, well-separated point."""
    center = project_center(0)
    points = [base_position(center, "Thread", r) for r in range(500)]
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
    neighbors = {a: {b, c}}
    proj_type: dict[uuid.UUID, tuple[str | None, str]] = {
        a: ("repo:x", "Thread"), b: ("repo:x", "Thread"), c: ("repo:y", "Thread")}
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
