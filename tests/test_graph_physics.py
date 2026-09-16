"""THE PHYSICS LAYOUT (operator ruling d7d55257, Thoth mail 11047), now HIERARCHICAL
(v8, Thoth mail 11128): a project-contracted level 1 (extent-aware separation so no
two projects ever overlap) plus a per-project level 2 (springs, district gravity,
the collapsed-container fix, rescaled onto its own level-1 disc), cross-project
bridge nudges, unfiled-object placement, and a VERIFIED declump floor."""
from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
from src.actions.core import Actions
from src.orchestrator import graph_physics
from src.orchestrator.graph_layout import _LAYOUT_VERSION, _MIN_SEPARATION, _grid_cells
from src.orchestrator.graph_physics import (
    _PHYSICS_LAYOUT_VERSION,
    DeclumpVerificationFailed,
    _apply_bridge_nudges,
    _build_physics_graph,
    _cross_project_edges,
    _detect_communities,
    _level1_layout,
    _level1_radius,
    _memory_guard,
    _physics_positions,
    _place_unfiled,
    _project_membership,
    _seed_positions,
    _semantic_weight,
    _separate_extents,
    _verify_min_separation,
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


def test_build_physics_graph_container_edges_are_scaled_by_member_count(
) -> None:
    """THE COLLAPSED-CONTAINER FIX (Thoth mail 11111): container weight is
    _CONTAINER_SPRING_WEIGHT / member_count, never flat -- a container with more
    members pulls each one weaker so sibling repulsion can actually spread them
    out post-FR."""
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
    # proj has 2 members (a, b) -- weight = _CONTAINER_SPRING_WEIGHT / 2
    expected = graph_physics._CONTAINER_SPRING_WEIGHT / 2
    assert container_w == pytest.approx(expected)
    assert semantic_w != graph_physics._CONTAINER_SPRING_WEIGHT
    assert semantic_w > 0


def test_build_physics_graph_container_weight_shrinks_as_membership_grows() -> None:
    proj = uuid.uuid4()

    class _Row(dict):
        def __getitem__(self, key: str) -> object:
            return dict.__getitem__(self, key)

    def container_weight_for(n_members: int) -> float:
        members = [uuid.uuid4() for _ in range(n_members)]
        object_ids = [*members, proj]
        link_rows = [_Row(from_id=m, to_id=proj, type="in_repo") for m in members]
        g, _ = _build_physics_graph(object_ids, link_rows, {})
        return float(g.es["weight"][0])

    assert container_weight_for(2) > container_weight_for(200)


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


def test_build_physics_graph_membership_gives_an_assertion_only_member_an_edge(
) -> None:
    """THE MEMBERSHIP UNION FIX (Thoth mail 11221): a member whose project comes
    ONLY from the `project` assertion (no in_repo link, so NO container edge from
    the link_rows loop) would otherwise be an isolated vertex FR has no reason to
    pull toward its own project -- `membership` gives it a synthetic edge."""
    a, proj = uuid.uuid4(), uuid.uuid4()
    object_ids = [a, proj]
    g, vertex_ids = _build_physics_graph(object_ids, [], {}, membership={a: proj})
    assert vertex_ids == object_ids  # no communities -> no synthetic vertices
    assert g.ecount() == 1
    edge = g.es[0]
    assert {edge.source, edge.target} == {0, 1}  # a <-> proj


async def test_project_membership_reflects_live_in_repo_links(actions: Actions) -> None:
    proj = await actions.create_or_find_object(
        "SoftwareProject", "repo:gp-membership", "test")
    member = await actions.create_or_find_object("Thread", "thread:gp-member", "test")
    await actions.create_link(
        member, proj, "in_repo", "test", datetime.now(UTC), 1.0)
    membership = await _project_membership(actions)
    assert membership.get(member) == proj


async def test_project_membership_falls_back_to_the_project_assertion(
    actions: Actions,
) -> None:
    """THE MEMBERSHIP UNION FIX (ruling d7d55257, Thoth mail 11221): 16,226 live
    objects carried a `project` assertion with ZERO carrying an in_repo link --
    minted with a project but never actually linked in_repo. A member with NO
    in_repo link but a `project` assertion naming the repo (mapped to the repo
    object's own canonical `repo:<name>`) must still resolve to that project."""
    proj = await actions.create_or_find_object(
        "SoftwareProject", "repo:gp-union", "test")
    member = await actions.create_or_find_object("Thread", "thread:gp-union-member", "test")
    now = datetime.now(UTC)
    await actions.assert_property(member, "project", "gp-union", "test", now, 1.0)
    membership = await _project_membership(actions)
    assert membership.get(member) == proj


async def test_project_membership_prefers_in_repo_over_the_assertion_on_disagreement(
    actions: Actions,
) -> None:
    proj_a = await actions.create_or_find_object(
        "SoftwareProject", "repo:gp-union-a", "test")
    # proj_b exists purely so the assertion's own name-to-canonical mapping has a
    # real object to (wrongly) resolve to -- never read back directly.
    await actions.create_or_find_object("SoftwareProject", "repo:gp-union-b", "test")
    member = await actions.create_or_find_object("Thread", "thread:gp-union-conflict", "test")
    now = datetime.now(UTC)
    await actions.create_link(member, proj_a, "in_repo", "test", now, 1.0)
    await actions.assert_property(member, "project", "gp-union-b", "test", now, 1.0)
    membership = await _project_membership(actions)
    assert membership.get(member) == proj_a


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


async def test_detect_communities_is_deterministic_across_repeated_calls(
    actions: Actions, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE LEIDEN SEED FIX (bbox compactness follow-up, decision cc2f2ea7): igraph's
    `community_leiden` draws from Python's own unseeded `random` module by default,
    so the SAME population could partition differently call to call -- measured
    live, a 4x bbox swing on identical code. A project with several plausible,
    roughly-balanced clusters (not one dominant pair) is what actually exercises
    Leiden's own randomness; calling `_detect_communities` twice over the exact
    same input must return the exact same partition now that it reseeds from
    `pid.int` before each project's own Leiden call."""
    monkeypatch.setattr(graph_physics, "_COMMUNITY_MIN_MEMBERS", 3)
    monkeypatch.setattr(graph_physics, "_COMMUNITY_MIN_SIZE", 2)

    proj = await actions.create_or_find_object(
        "SoftwareProject", "repo:gp-communities-deterministic", "test")
    now = datetime.now(UTC)
    clusters = [
        [await actions.create_or_find_object("Thread", f"thread:gp-det-{c}-{i}", "test")
         for i in range(3)]
        for c in range(4)
    ]
    members = [oid for cluster in clusters for oid in cluster]
    for oid in members:
        await actions.create_link(oid, proj, "in_repo", "test", now, 1.0)
    for cluster in clusters:
        for i in range(len(cluster)):
            for j in range(i + 1, len(cluster)):
                await actions.create_link(cluster[i], cluster[j], "cites", "test", now, 1.0)

    link_rows = await graph_physics._live_link_rows(actions)
    membership = await _project_membership(actions)
    active = set(members)

    first = _detect_communities(link_rows, membership, active)
    second = _detect_communities(link_rows, membership, active)
    assert first == second


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

    # tolerance matches `_verify_min_separation`'s own PROPORTIONAL floor (Thoth
    # mail 11191; already enforced inside `_physics_positions` itself, which would
    # have raised DeclumpVerificationFailed otherwise) -- a bare 1e-6 assumed exact
    # convergence, which the declump's own docstring already disclaims ("a few
    # thousandths short after rounding"); this population's unfiled objects seed
    # via a Gaussian fog (item 4) rather than the old deterministic sunflower
    # scatter, so a near-exact residual is expected, not a regression.
    pts = list(positions.values())
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dist = ((pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2) ** 0.5
            assert dist >= _MIN_SEPARATION * graph_physics._PHYSICS_VERIFY_FAIL_RATIO


async def test_level2_layout_does_not_collapse_a_large_project_into_one_cell(
    actions: Actions,
) -> None:
    """THE COLLAPSED-CONTAINER FIX (Thoth mail 11111), re-checked against the
    hierarchical scheme's own per-project FR pass -- 1,000 members, hermetic DB, no
    semantic edges at all, so container gravity is the ONLY force differentiating
    them. Acceptance (Thoth's own line): no post-FR cell holds more than roughly
    50 points for any project."""
    proj = await actions.create_or_find_object(
        "SoftwareProject", "repo:gp-collapse-check", "test")
    now = datetime.now(UTC)
    members = []
    for i in range(1000):
        m = await actions.create_or_find_object("Thread", f"thread:gp-collapse-{i}", "test")
        await actions.create_link(m, proj, "in_repo", "test", now, 1.0)
        members.append(m)

    link_rows = await graph_physics._live_link_rows(actions)
    membership = await _project_membership(actions)
    communities = _detect_communities(link_rows, membership, {proj, *members})

    member_pos = graph_physics._level2_raw_layout_for_project(
        proj, members, link_rows, communities)
    pos = np.array([member_pos[m] for m in members])
    cells = _grid_cells(pos, _MIN_SEPARATION)
    worst_cell = max(len(v) for v in cells.values())
    assert worst_cell <= 100, (
        f"worst post-FR cell holds {worst_cell} of {len(members)} members -- "
        "container gravity is still collapsing this project's own siblings")


def test_level1_radius_grows_with_membership() -> None:
    assert _level1_radius(4) < _level1_radius(400)
    assert _level1_radius(0) >= _MIN_SEPARATION  # floored, never zero or negative


def test_project_community_buckets_splits_by_detected_community_and_noise() -> None:
    pid = uuid.uuid4()
    a, b, c, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    communities = {a: (pid, 0), b: (pid, 0), c: (pid, 1)}  # d has none for pid
    buckets = graph_physics._project_community_buckets(pid, [a, b, c, d], communities)
    assert buckets[0] == [a, b]
    assert buckets[1] == [c]
    assert buckets[graph_physics._NOISE_COMMUNITY_KEY] == [d]


def test_project_community_buckets_single_bucket_when_no_real_communities() -> None:
    pid = uuid.uuid4()
    members = [uuid.uuid4() for _ in range(5)]
    buckets = graph_physics._project_community_buckets(pid, members, {})
    assert len(buckets) == 1
    assert buckets[graph_physics._NOISE_COMMUNITY_KEY] == members


async def test_level2_raw_layout_for_project_recurses_and_separates_real_communities(
    actions: Actions, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE RECURSIVE HIERARCHY FIX (live specimen, fourth real migration attempt --
    a ~12,000-member project kept failing verification no matter the iteration
    budget): two real, tightly-linked clusters within ONE project, monkeypatched
    thresholds so Leiden actually finds them at hermetic scale. The recursive path
    must keep each cluster's own members close together while keeping the two
    clusters well separated from each other -- the same "no overlap by
    construction" guarantee level 1 already gives real projects, one level
    deeper."""
    monkeypatch.setattr(graph_physics, "_COMMUNITY_MIN_MEMBERS", 3)
    monkeypatch.setattr(graph_physics, "_COMMUNITY_MIN_SIZE", 2)

    proj = await actions.create_or_find_object(
        "SoftwareProject", "repo:gp-recurse-check", "test")
    now = datetime.now(UTC)
    cluster_a, cluster_b = [], []
    for i in range(6):
        m = await actions.create_or_find_object("Thread", f"thread:gp-recurse-a{i}", "test")
        await actions.create_link(m, proj, "in_repo", "test", now, 1.0)
        cluster_a.append(m)
    for i in range(6):
        m = await actions.create_or_find_object("Thread", f"thread:gp-recurse-b{i}", "test")
        await actions.create_link(m, proj, "in_repo", "test", now, 1.0)
        cluster_b.append(m)
    for i in range(len(cluster_a) - 1):
        await actions.create_link(cluster_a[i], cluster_a[i + 1], "cites", "test", now, 1.0)
    for i in range(len(cluster_b) - 1):
        await actions.create_link(cluster_b[i], cluster_b[i + 1], "cites", "test", now, 1.0)

    members = [*cluster_a, *cluster_b]
    link_rows = await graph_physics._live_link_rows(actions)
    membership = await _project_membership(actions)
    communities = _detect_communities(link_rows, membership, {proj, *members})
    buckets = graph_physics._project_community_buckets(proj, members, communities)
    assert len(buckets) > 1, "test setup should have produced >1 real community bucket"

    pos = graph_physics._level2_raw_layout_for_project(proj, members, link_rows, communities)
    assert set(pos.keys()) == set(members)

    a_pts = np.array([pos[m] for m in cluster_a])
    b_pts = np.array([pos[m] for m in cluster_b])
    # Compares against what `_separate_extents` actually guarantees (min cross-
    # cluster distance vs. min within-cluster distance), not a naive centroid/max-
    # radius check -- the algorithm's own reference point for each community's
    # radius is its recentred FR position, not the point cloud's simple mean.
    cross = np.linalg.norm(a_pts[:, None, :] - b_pts[None, :, :], axis=-1)
    within_a = np.linalg.norm(a_pts[:, None, :] - a_pts[None, :, :], axis=-1)
    within_b = np.linalg.norm(b_pts[:, None, :] - b_pts[None, :, :], axis=-1)
    np.fill_diagonal(within_a, np.inf)
    np.fill_diagonal(within_b, np.inf)
    assert cross.min() > max(within_a.min(), within_b.min()), (
        "a cross-cluster pair sits closer together than points WITHIN a cluster -- "
        "the two communities are not meaningfully separated")


def test_separate_extents_pushes_overlapping_discs_apart() -> None:
    pos = np.array([[0.0, 0.0], [1.0, 0.0]])  # two discs, centroids 1 unit apart
    radii = np.array([50.0, 50.0])  # each wants 100+gutter of clearance
    out = _separate_extents(pos, radii, gutter=10.0, iterations=50)
    dist = float(np.linalg.norm(out[0] - out[1]))
    assert dist >= 110.0 - 1e-3


def test_separate_extents_leaves_already_separated_discs_alone() -> None:
    pos = np.array([[0.0, 0.0], [1000.0, 0.0]])
    radii = np.array([10.0, 10.0])
    out = _separate_extents(pos, radii, gutter=5.0, iterations=50)
    assert np.allclose(out, pos)


async def test_cross_project_edges_aggregates_by_unordered_project_pair(
    actions: Actions,
) -> None:
    proj_a = await actions.create_or_find_object("SoftwareProject", "repo:gp-cross-a", "test")
    proj_b = await actions.create_or_find_object("SoftwareProject", "repo:gp-cross-b", "test")
    now = datetime.now(UTC)
    m_a = await actions.create_or_find_object("Thread", "thread:gp-cross-ma", "test")
    m_b1 = await actions.create_or_find_object("Thread", "thread:gp-cross-mb1", "test")
    m_b2 = await actions.create_or_find_object("Thread", "thread:gp-cross-mb2", "test")
    await actions.create_link(m_a, proj_a, "in_repo", "test", now, 1.0)
    await actions.create_link(m_b1, proj_b, "in_repo", "test", now, 1.0)
    await actions.create_link(m_b2, proj_b, "in_repo", "test", now, 1.0)
    await actions.create_link(m_a, m_b1, "cites", "test", now, 1.0)
    await actions.create_link(m_a, m_b2, "cites", "test", now, 1.0)

    link_rows = await graph_physics._live_link_rows(actions)
    membership = await _project_membership(actions)
    edges = _cross_project_edges(link_rows, membership, {proj_a, proj_b})
    key = (proj_a, proj_b) if str(proj_a) <= str(proj_b) else (proj_b, proj_a)
    assert edges.get(key) == 2.0


def test_level1_layout_places_a_single_project_at_the_origin_ish() -> None:
    pid = uuid.uuid4()
    out = _level1_layout([pid], {pid: 20.0}, {})
    assert pid in out
    assert out[pid].shape == (2,)


def test_apply_bridge_nudges_moves_a_bridging_member_toward_the_other_centroid() -> None:
    a, b, proj_a, proj_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    positions = {a: np.array([0.0, 0.0]), b: np.array([100.0, 0.0])}
    membership = {a: proj_a, b: proj_b}
    centroids = {proj_a: np.array([0.0, 0.0]), proj_b: np.array([100.0, 0.0])}

    class _Row(dict):
        def __getitem__(self, key: str) -> object:
            return dict.__getitem__(self, key)

    link_rows = [_Row(from_id=a, to_id=b, type="cites")]
    before = positions[a].copy()
    _apply_bridge_nudges(positions, link_rows, membership, centroids)
    assert positions[a][0] > before[0]  # nudged toward proj_b's centroid (positive x)


def test_place_unfiled_with_neighbours_lands_at_their_mean_position() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # anchors far enough apart (200 units) that their mean (the naive target for a)
    # sits well clear of both -- isolates "lands at the mean" from THE
    # UNFILED-VS-PLACED FIX's own anchor-declump nudge, checked separately below.
    placed = {b: np.array([0.0, 0.0]), c: np.array([200.0, 0.0])}

    class _Row(dict):
        def __getitem__(self, key: str) -> object:
            return dict.__getitem__(self, key)

    link_rows = [_Row(from_id=a, to_id=b, type="cites"), _Row(from_id=a, to_id=c, type="cites")]
    out = _place_unfiled([a], link_rows, placed)
    assert np.allclose(out[a], [100.0, 0.0])


def test_place_unfiled_never_lands_within_min_sep_of_an_already_placed_point() -> None:
    """THE UNFILED-VS-PLACED FIX (live specimen, third real migration attempt on
    bcf0ca63): a naive neighbour-mean placement dropped an unfiled object right on
    top of an already-densely-packed project -- pre-declump positions 205 units
    apart converged to 0.058 apart post-declump because the single GLOBAL declump
    pass at the very end couldn't always finish that local cleanup. `_place_unfiled`
    now runs its own local anchor-mode declump against every already-placed
    position, so its OWN output must already respect the floor before the caller
    ever merges it in."""
    a, b = uuid.uuid4(), uuid.uuid4()
    # b sits exactly where a's only neighbour (also at the origin) would naively
    # place it -- the naive mean is 0 units from an already-placed point.
    placed = {b: np.array([0.0, 0.0])}

    class _Row(dict):
        def __getitem__(self, key: str) -> object:
            return dict.__getitem__(self, key)

    link_rows = [_Row(from_id=a, to_id=b, type="cites")]
    out = _place_unfiled([a], link_rows, placed)
    assert np.linalg.norm(out[a] - placed[b]) >= _MIN_SEPARATION - 1e-6


def test_place_unfiled_edgeless_scatters_away_from_a_fixed_ring() -> None:
    """The exact failure Thoth's own measurement flagged on v7 -- a fixed-radius
    ring shell -- must NOT reproduce here: many edgeless unfiled objects should
    land at a SPREAD of distances from the cloud centre, not all at one radius."""
    placed = {uuid.uuid4(): np.array([float(i), 0.0]) for i in range(50)}
    unfiled = [uuid.uuid4() for _ in range(200)]
    out = _place_unfiled(unfiled, [], placed)
    center = np.array(list(placed.values())).mean(axis=0)
    radii = np.array([np.linalg.norm(out[oid] - center) for oid in unfiled])
    assert radii.std() > 1.0  # a real spread, not one shared radius


def test_verify_min_separation_passes_for_a_well_spread_population() -> None:
    ids: list[uuid.UUID | None] = [uuid.uuid4() for _ in range(50)]
    pos = _seed_positions(ids)
    worst = graph_physics._worst_pair_distance(pos, _MIN_SEPARATION)
    _verify_min_separation(worst, min_sep=_MIN_SEPARATION)  # no raise


def test_verify_min_separation_raises_on_two_coincident_points() -> None:
    pos = np.array([[0.0, 0.0], [0.001, 0.0]])
    worst = graph_physics._worst_pair_distance(pos, _MIN_SEPARATION)
    with pytest.raises(DeclumpVerificationFailed):
        _verify_min_separation(worst, min_sep=_MIN_SEPARATION)


def test_verify_min_separation_tolerates_a_residual_above_the_proportional_floor() -> None:
    """PROPORTIONAL VERIFICATION (Thoth mail 11191): a residual comfortably above
    `_PHYSICS_VERIFY_FAIL_RATIO * min_sep` (e.g. 13.6 of 15, Thoth's own "a
    convergence residual, not a collapse; invisible") must NOT raise -- only a
    genuine collapse well below that line does."""
    residual = _MIN_SEPARATION * (graph_physics._PHYSICS_VERIFY_FAIL_RATIO + 0.05)
    _verify_min_separation(residual, min_sep=_MIN_SEPARATION)  # no raise

    collapse = _MIN_SEPARATION * (graph_physics._PHYSICS_VERIFY_FAIL_RATIO - 0.05)
    with pytest.raises(DeclumpVerificationFailed):
        _verify_min_separation(collapse, min_sep=_MIN_SEPARATION)


def test_worst_pair_distance_returns_min_sep_for_a_well_spread_population() -> None:
    ids: list[uuid.UUID | None] = [uuid.uuid4() for _ in range(50)]
    pos = _seed_positions(ids)
    assert graph_physics._worst_pair_distance(pos, _MIN_SEPARATION) == _MIN_SEPARATION


def test_declump_until_converged_never_flakes_on_many_random_small_unfiled_pops() -> None:
    """Live flake specimen (full-suite serial gate, e7cf6c59 follow-up): a hermetic
    3-object all-unfiled population occasionally left one pair 14.24 units apart
    after `_declump`'s own default 30 iterations, under the old fixed-epsilon
    floor. Reproduced here directly (no DB) over many random small populations --
    `_place_unfiled`'s own Gaussian fog is exactly what generated the flaky
    starting configuration -- to confirm the converge-or-budget declump actually
    closes the gap rather than just moving it to a rarer seed."""
    for _trial in range(300):
        for n in (2, 3, 4):
            ids = [uuid.uuid4() for _ in range(n)]
            out = graph_physics._place_unfiled(ids, [], {})
            pos = np.array([out[oid] for oid in ids])
            _declumped, worst, _iters = graph_physics._declump_until_converged_or_budget(
                pos, ids, min_sep=_MIN_SEPARATION, budget_secs=5.0)
            _verify_min_separation(worst)  # raises on failure -- the assertion


def test_place_hubs_in_zone_spreads_hubs_at_least_min_sep_apart() -> None:
    """THE HUB ZONE fix (live specimen, first bcc6b3f3 migration attempt): the old
    "jitter by 1 unit around the centroid" scheme packed every hub into a 2-unit
    disc regardless of population -- this replacement must give hubs a REAL
    min-sep-respecting spread among themselves before declump ever runs, and NEVER
    rescale that spread down below the floor."""
    hubs = [uuid.uuid4() for _ in range(11)]
    center = np.array([500.0, -300.0])
    out = graph_physics._place_hubs_in_zone(hubs, center)
    assert set(out.keys()) == set(hubs)
    pts = np.array(list(out.values()))
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            assert np.linalg.norm(pts[i] - pts[j]) >= _MIN_SEPARATION - 1e-6
    radii = np.linalg.norm(pts - center, axis=1)
    assert float(radii.max()) <= graph_physics._hub_zone_radius(len(hubs)) + 1e-6


def test_place_hubs_in_zone_single_hub_lands_near_center() -> None:
    hub = uuid.uuid4()
    center = np.array([1.0, 2.0])
    out = graph_physics._place_hubs_in_zone([hub], center)
    assert np.linalg.norm(out[hub] - center) <= graph_physics._hub_zone_radius(1) + 1e-6


def test_place_hubs_in_zone_empty_returns_empty() -> None:
    assert graph_physics._place_hubs_in_zone([], np.zeros(2)) == {}


def test_hub_zone_radius_matches_the_real_sunflower_extent() -> None:
    n = 11
    center = np.zeros(2)
    out = graph_physics._place_hubs_in_zone([uuid.uuid4() for _ in range(n)], center)
    real_extent = max(float(np.linalg.norm(p)) for p in out.values())
    assert graph_physics._hub_zone_radius(n) == pytest.approx(real_extent)


async def test_physics_positions_empty_population_returns_empty(actions: Actions) -> None:
    # a fresh hermetic DB per test -- no active objects yet at this point isn't
    # guaranteed (other fixtures may seed some), so only assert the no-crash shape.
    positions = await _physics_positions(actions)
    assert isinstance(positions, dict)


async def test_physics_positions_multi_project_end_to_end_acceptance(
    actions: Actions,
) -> None:
    """THE HIERARCHICAL PHYSICS acceptance (Thoth mail 11128), at hermetic scale --
    3 projects, ~40 members each, one cross-project bridge, one unfiled object.
    v7's own live failure was every project's members spreading to ~1,000 units
    while centroids sat only 82-224 apart; the acceptance line here is the direct
    hermetic analogue -- every pair of project centroids at least as far apart as
    the sum of their own radius budgets, which v7 never enforced at all."""
    now = datetime.now(UTC)
    projects = []
    all_members: dict[uuid.UUID, list[uuid.UUID]] = {}
    for p in range(3):
        proj = await actions.create_or_find_object(
            "SoftwareProject", f"repo:gp-e2e-{p}", "test")
        projects.append(proj)
        members = []
        for i in range(40):
            m = await actions.create_or_find_object("Thread", f"thread:gp-e2e-{p}-{i}", "test")
            await actions.create_link(m, proj, "in_repo", "test", now, 1.0)
            members.append(m)
            if i > 0:
                await actions.create_link(members[i - 1], m, "cites", "test", now, 1.0)
        all_members[proj] = members
    # one real cross-project bridge
    await actions.create_link(
        all_members[projects[0]][0], all_members[projects[1]][0], "cites", "test", now, 1.0)
    unfiled = await actions.create_or_find_object("Thread", "thread:gp-e2e-unfiled", "test")
    await actions.create_link(unfiled, all_members[projects[2]][0], "cites", "test", now, 1.0)

    positions = await _physics_positions(actions)
    for oid in [*projects, *[m for ms in all_members.values() for m in ms], unfiled]:
        assert oid in positions

    radii = {pid: graph_physics._level1_radius(len(all_members[pid])) for pid in projects}
    for i in range(len(projects)):
        for j in range(i + 1, len(projects)):
            a, b = projects[i], projects[j]
            dist = math.dist(positions[a], positions[b])
            assert dist >= radii[a] + radii[b] - 1e-6, (
                f"project centroids {dist:.1f} apart, under the "
                f"{radii[a] + radii[b]:.1f} radius-sum floor -- projects overlap")

    pts = np.array(list(positions.values()))
    tree_dists = []
    for i in range(len(pts)):
        deltas = np.linalg.norm(pts - pts[i], axis=1)
        deltas[i] = np.inf
        tree_dists.append(float(deltas.min()))
    p50 = float(np.percentile(tree_dists, 50))
    assert p50 >= _MIN_SEPARATION - 1e-6  # the hard floor v7 failed to enforce at all


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
    # PROPORTIONAL VERIFICATION diagnostics (Thoth mail 11191) ride the receipt
    # "either way" -- present on a successful run too, not just a refusal.
    assert receipts[-1]["declump_worst_residual"] > 0
    assert receipts[-1]["declump_iterations"] >= 0

    placed = await positions_for(actions, [a, b])
    assert a in placed and b in placed

    rows = await actions.pool.fetch(
        "SELECT (value #>> '{}')::int AS v FROM current_assertions "
        "WHERE object_id = ANY($1::uuid[]) AND name=$2",
        [a, b], _LAYOUT_VERSION_PROP)
    assert all(r["v"] == _PHYSICS_LAYOUT_VERSION for r in rows)


async def test_run_physics_migrate_verify_only_writes_nothing(actions: Actions) -> None:
    """THE VERIFY-ONLY DOOR (ruling 6befd2a5, Thoth mail 11178): computes and
    verifies but never reaches the write step -- a real migration door's own
    positions_for lookup for the SAME objects must come back empty."""
    from src.orchestrator.graph_layout import positions_for

    now = datetime.now(UTC)
    a = await actions.create_or_find_object("Thread", "thread:gp-verify-only-a", "test")
    b = await actions.create_or_find_object("Thread", "thread:gp-verify-only-b", "test")
    await actions.create_link(a, b, "cites", "test", now, 1.0)

    receipts = [r async for r in run_physics_migrate(actions, verify_only=True)]
    assert receipts[-1]["done"] is True
    assert receipts[-1]["verify_only"] is True
    assert receipts[-1]["placed"] >= 2
    assert "peak_rss_kb" not in receipts[-1]
    # THE ACCEPTANCE METRICS (Thoth mail 11208) ride the receipt too.
    assert "layout_bbox_min" in receipts[-1]
    assert "layout_bbox_max" in receipts[-1]
    assert "layout_project_stats" in receipts[-1]

    placed = await positions_for(actions, [a, b])
    assert placed == {}


def test_layout_acceptance_metrics_reports_purity_gap_and_bbox_for_two_projects() -> None:
    """THE ACCEPTANCE METRICS (Thoth mail 11208, required after the first real v8
    write measured a 163k-unit bbox with every centroid near-coincident and 0.32
    same-project purity): two well-separated projects, hand-built positions, should
    report a positive centroid gap and full same-project purity."""
    proj_a, proj_b = uuid.uuid4(), uuid.uuid4()
    members_a = [uuid.uuid4() for _ in range(6)]
    members_b = [uuid.uuid4() for _ in range(6)]
    object_ids = [*members_a, *members_b]
    membership = {m: proj_a for m in members_a} | {m: proj_b for m in members_b}
    groups = {proj_a: members_a, proj_b: members_b}
    project_ids = [proj_a, proj_b]
    radii = {proj_a: 20.0, proj_b: 20.0}

    pos = np.array(
        [[0.0, 0.0] for _ in members_a] + [[500.0, 0.0] for _ in members_b])
    # spread each cluster a little so r50/r95 aren't degenerate zeros
    pos = pos + np.array([[i * 2.0, 0.0] for i in range(len(object_ids))])

    metrics = graph_physics._layout_acceptance_metrics(
        pos, object_ids, membership, groups, project_ids, radii)
    assert metrics["layout_min_top10_centroid_gap"] > 0  # well clear of R_a+R_b
    assert metrics["layout_biggest_project_5nn_purity"] == 1.0  # tight, isolated cluster
    assert len(metrics["layout_project_stats"]) == 2
    assert metrics["layout_bbox_min"] is not None
    assert metrics["layout_bbox_max"] is not None


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
