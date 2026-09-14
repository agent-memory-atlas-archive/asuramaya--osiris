"""THE GRAPH VISUALIZER (wave B item 1) + NAVIGABLE SPACE, THE SERVER piece A (rulings
f832c3a4 + 0a3d6719, thread b6cb1d7c0b36): the layout heartbeat places every object under
a deterministic project/type/id rule, nudged by a bounded intra-project relax."""
from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

from src.actions.core import Actions
from src.orchestrator.graph_layout import (
    _LAYOUT_VERSION_PROP,
    _intra_project_neighbors,
    base_position,
    layout_batch,
    positions_for,
    project_center,
    relax,
    unplaced_batch,
)

# --- pure placement rule: deterministic, idempotent, never a grid --------------------


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


def test_project_center_is_a_pure_function_of_canonical_alone() -> None:
    a1 = project_center("repo:osiris")
    a2 = project_center("repo:osiris")
    assert a1 == a2
    assert project_center("repo:osiris") != project_center("repo:other")


def test_base_position_is_deterministic_and_pure() -> None:
    oid = uuid.uuid4()
    p1 = base_position("repo:osiris", "Thread", oid)
    p2 = base_position("repo:osiris", "Thread", oid)
    assert p1 == p2


def test_base_position_never_lands_two_types_on_the_same_ring() -> None:
    oid = uuid.uuid4()
    thread_pos = base_position("repo:osiris", "Thread", oid)
    decision_pos = base_position("repo:osiris", "Decision", oid)
    cx, cy = project_center("repo:osiris")
    r_thread = math.dist((cx, cy), thread_pos)
    r_decision = math.dist((cx, cy), decision_pos)
    assert round(r_thread, 6) != round(r_decision, 6)


def test_base_position_unfiled_gets_its_own_stable_center() -> None:
    oid = uuid.uuid4()
    p1 = base_position(None, "Thread", oid)
    p2 = base_position(None, "Thread", oid)
    assert p1 == p2
    assert base_position(None, "Thread", oid) != base_position("repo:osiris", "Thread", oid)


def test_intra_project_neighbors_drops_cross_project_edges() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    neighbors = {a: {b, c}}
    proj_type = {a: ("repo:x", "Thread"), b: ("repo:x", "Thread"), c: ("repo:y", "Thread")}
    out = _intra_project_neighbors([a], neighbors, proj_type)
    assert out[a] == {b}


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


async def test_layout_batch_places_a_newcomer_at_its_own_deterministic_base(
    actions: Actions,
) -> None:
    """A newcomer's placement never depends on how many siblings already share its ring
    -- it lands within a bounded distance of its own pure-function base position, not an
    arbitrary point some other object's arrival happened to push it toward."""
    oid = await actions.create_or_find_object("Thread", "thread:gl-newcomer", "test")
    await layout_batch(actions, limit=1000)
    got = (await positions_for(actions, [oid]))[oid]
    expected = base_position(None, "Thread", oid)
    # a few relax iterations may nudge it, but never past a modest multiple of one edge
    assert math.dist(got, expected) < 200


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
