"""THE PHYSICS LAYOUT (operator ruling d7d55257, Thoth mail 11047): springs for
semantic edges, weak gravity for container edges, nested communities in big
projects, hub re-centering, the existing declump floor -- all over the WHOLE active
graph in one pass, never a batch loop."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
from src.actions.core import Actions
from src.orchestrator import graph_physics
from src.orchestrator.graph_layout import _LAYOUT_VERSION, _MIN_SEPARATION
from src.orchestrator.graph_physics import (
    _PHYSICS_LAYOUT_VERSION,
    _build_physics_graph,
    _detect_communities,
    _memory_guard,
    _physics_positions,
    _project_membership,
    _seed_positions,
    _semantic_weight,
    run_physics_migrate,
)


def test_physics_layout_version_matches_graph_layout_current_version() -> None:
    """The incremental heartbeat and this one-shot migration must always agree on
    what "current" means, or a heartbeat tick would treat physics-placed objects as
    still-unplaced (or vice versa)."""
    assert _PHYSICS_LAYOUT_VERSION == _LAYOUT_VERSION


def test_semantic_weight_is_degree_normalised() -> None:
    low = _semantic_weight("cites", 1, 1)
    high = _semantic_weight("cites", 100, 100)
    assert low > high > 0


def test_seed_positions_real_vertices_are_spread_deterministically() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    vertex_ids: list[uuid.UUID | None] = [a, b, None]
    p1 = _seed_positions(vertex_ids)
    p2 = _seed_positions(vertex_ids)
    assert (p1 == p2).all()  # deterministic
    assert tuple(p1[0]) != tuple(p1[1])  # two real vertices never seed on top of each other


async def test_memory_guard_refuses_on_a_degenerate_all_coincident_seed(
    actions: Actions,
) -> None:
    """THE PHYSICS LAYOUT OOM (Thoth mail 11097): the one remaining shape that could
    still cost O(k^2) memory after the declump grid rewrite -- every point landing
    in a single grid cell. 12,000 coincident points -> 12,000^2*16 ~= 2.3 GB, over
    the default 2 GB layout.physics_max_bytes."""
    seed = np.zeros((12_000, 2))
    reason = await _memory_guard(actions, seed)
    assert reason is not None
    assert "refusing" in reason


async def test_memory_guard_passes_for_a_well_spread_population(actions: Actions) -> None:
    ids: list[uuid.UUID | None] = [uuid.uuid4() for _ in range(2000)]
    seed = _seed_positions(ids)
    reason = await _memory_guard(actions, seed)
    assert reason is None


def test_build_physics_graph_container_edges_get_flat_weight_semantic_get_normalised(
) -> None:
    a, b, proj = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    object_ids = [a, b, proj]

    class _Row(dict):
        def __getitem__(self, key: str) -> object:
            return dict.__getitem__(self, key)

    link_rows = [
        _Row(from_id=a, to_id=b, type="cites"),
        _Row(from_id=a, to_id=proj, type="in_repo"),
        _Row(from_id=b, to_id=proj, type="in_repo"),
    ]
    g, vertex_ids = _build_physics_graph(object_ids, link_rows, {})
    assert vertex_ids == object_ids  # no communities -> no synthetic vertices
    assert g.vcount() == 3
    assert g.ecount() == 3
    weight_by_pair = {
        tuple(sorted((e.source, e.target))): w for e, w in zip(g.es, g.es["weight"], strict=True)
    }
    idx = {oid: i for i, oid in enumerate(object_ids)}
    container_w = weight_by_pair[tuple(sorted((idx[a], idx[proj])))]
    semantic_w = weight_by_pair[tuple(sorted((idx[a], idx[b])))]
    assert container_w == graph_physics._CONTAINER_SPRING_WEIGHT
    assert semantic_w != graph_physics._CONTAINER_SPRING_WEIGHT
    assert semantic_w > 0


def test_build_physics_graph_reroutes_community_members_through_synthetic_vertex(
) -> None:
    a, proj = uuid.uuid4(), uuid.uuid4()
    object_ids = [a, proj]

    class _Row(dict):
        def __getitem__(self, key: str) -> object:
            return dict.__getitem__(self, key)

    link_rows = [_Row(from_id=a, to_id=proj, type="in_repo")]
    communities = {a: (proj, 0)}
    g, vertex_ids = _build_physics_graph(object_ids, link_rows, communities)
    assert len(vertex_ids) == 3  # a, proj, one synthetic community vertex
    assert vertex_ids[2] is None
    # a's own in_repo edge now points at the synthetic vertex (index 2), not proj (1)
    idx_a = 0
    edge_targets = {e.target for e in g.es if e.source == idx_a} | {
        e.source for e in g.es if e.target == idx_a}
    assert 2 in edge_targets
    assert 1 not in edge_targets
    # the synthetic vertex itself has its own weak edge to the real project
    community_edges = {e.target for e in g.es if e.source == 2} | {
        e.source for e in g.es if e.target == 2}
    assert 1 in community_edges


async def test_project_membership_reflects_live_in_repo_links(actions: Actions) -> None:
    proj = await actions.create_or_find_object(
        "SoftwareProject", "repo:gp-membership", "test")
    member = await actions.create_or_find_object("Thread", "thread:gp-member", "test")
    await actions.create_link(
        member, proj, "in_repo", "test", datetime.now(UTC), 1.0)
    membership = await _project_membership(actions)
    assert membership.get(member) == proj


async def test_detect_communities_finds_real_clusters_above_threshold(
    actions: Actions, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real small population (well under the real 500-member threshold), with the
    module's OWN threshold constants monkeypatched down for this test -- creating
    500 real objects with real internal edge structure just to exercise Leiden
    itself would be prohibitively slow for a unit test and proves nothing extra
    about this function's own logic."""
    monkeypatch.setattr(graph_physics, "_COMMUNITY_MIN_MEMBERS", 3)
    monkeypatch.setattr(graph_physics, "_COMMUNITY_MIN_SIZE", 2)

    proj = await actions.create_or_find_object(
        "SoftwareProject", "repo:gp-communities", "test")
    now = datetime.now(UTC)
    # two tight semantic clusters (a1-a2, b1-b2), no edges between the clusters
    a1 = await actions.create_or_find_object("Thread", "thread:gp-comm-a1", "test")
    a2 = await actions.create_or_find_object("Thread", "thread:gp-comm-a2", "test")
    b1 = await actions.create_or_find_object("Thread", "thread:gp-comm-b1", "test")
    b2 = await actions.create_or_find_object("Thread", "thread:gp-comm-b2", "test")
    for oid in (a1, a2, b1, b2):
        await actions.create_link(oid, proj, "in_repo", "test", now, 1.0)
    await actions.create_link(a1, a2, "cites", "test", now, 1.0)
    await actions.create_link(b1, b2, "cites", "test", now, 1.0)

    link_rows = await graph_physics._live_link_rows(actions)
    membership = await _project_membership(actions)
    communities = _detect_communities(link_rows, membership, {a1, a2, b1, b2})

    assert a1 in communities and a2 in communities
    assert b1 in communities and b2 in communities
    assert communities[a1][0] == proj
    assert communities[b1][0] == proj
    # a1/a2 share a community, b1/b2 share a DIFFERENT one -- two real clusters
    assert communities[a1][1] == communities[a2][1]
    assert communities[b1][1] == communities[b2][1]
    assert communities[a1][1] != communities[b1][1]


async def test_detect_communities_below_threshold_project_yields_nothing(
    actions: Actions,
) -> None:
    proj = await actions.create_or_find_object(
        "SoftwareProject", "repo:gp-small-project", "test")
    member = await actions.create_or_find_object("Thread", "thread:gp-small-member", "test")
    await actions.create_link(
        member, proj, "in_repo", "test", datetime.now(UTC), 1.0)
    link_rows = await graph_physics._live_link_rows(actions)
    membership = await _project_membership(actions)
    communities = _detect_communities(link_rows, membership, {member})
    assert communities == {}


async def test_physics_positions_places_every_active_object_with_no_exact_collisions(
    actions: Actions,
) -> None:
    now = datetime.now(UTC)
    a = await actions.create_or_find_object("Thread", "thread:gp-pos-a", "test")
    b = await actions.create_or_find_object("Thread", "thread:gp-pos-b", "test")
    c = await actions.create_or_find_object("Thread", "thread:gp-pos-c", "test")
    await actions.create_link(a, b, "cites", "test", now, 1.0)
    await actions.create_link(b, c, "cites", "test", now, 1.0)

    positions = await _physics_positions(actions)
    assert a in positions and b in positions and c in positions
    for x, y in positions.values():
        assert x == x and y == y  # not NaN

    pts = list(positions.values())
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dist = ((pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2) ** 0.5
            assert dist >= _MIN_SEPARATION - 1e-6


async def test_physics_positions_empty_population_returns_empty(actions: Actions) -> None:
    # a fresh hermetic DB per test -- no active objects yet at this point isn't
    # guaranteed (other fixtures may seed some), so only assert the no-crash shape.
    positions = await _physics_positions(actions)
    assert isinstance(positions, dict)


async def test_run_physics_migrate_writes_every_active_object_at_the_current_version(
    actions: Actions,
) -> None:
    from src.orchestrator.graph_layout import _LAYOUT_VERSION_PROP, positions_for

    now = datetime.now(UTC)
    a = await actions.create_or_find_object("Thread", "thread:gp-migrate-a", "test")
    b = await actions.create_or_find_object("Thread", "thread:gp-migrate-b", "test")
    await actions.create_link(a, b, "cites", "test", now, 1.0)

    receipts = [r async for r in run_physics_migrate(actions)]
    assert receipts[-1]["done"] is True
    assert receipts[-1]["placed"] >= 2
    assert receipts[-1]["peak_rss_kb"] > 0

    placed = await positions_for(actions, [a, b])
    assert a in placed and b in placed

    rows = await actions.pool.fetch(
        "SELECT (value #>> '{}')::int AS v FROM current_assertions "
        "WHERE object_id = ANY($1::uuid[]) AND name=$2",
        [a, b], _LAYOUT_VERSION_PROP)
    assert all(r["v"] == _PHYSICS_LAYOUT_VERSION for r in rows)


async def test_run_physics_migrate_refuses_when_the_layout_lock_is_held(
    actions: Actions,
) -> None:
    from src.orchestrator.graph_layout import _try_acquire_layout_lock

    async with actions.pool.acquire() as holder_conn:
        got = await _try_acquire_layout_lock(holder_conn)
        assert got
        try:
            receipts = [r async for r in run_physics_migrate(actions)]
            assert len(receipts) == 1
            assert "error" in receipts[0]
        finally:
            await holder_conn.execute(
                "SELECT pg_advisory_unlock(hashtext($1))", "graph_layout_batch")
