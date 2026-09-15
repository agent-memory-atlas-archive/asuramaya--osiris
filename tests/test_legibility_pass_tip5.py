"""THE LAST RENDERER (operator ruling d7d55257, Thoth mail 11066) -- explicitly the LAST/
frozen renderer tip, replacing BOTH DM 11048 (THE DRILL) and DM 11060 (5 live-review flaws)
in one shot. "Kill LOD entirely" -- retires TIP 3/TIP 4's own zoomLOD/tier-label/cluster-
edge/halo/cluster-ring machinery outright, replaces it with one renderer, three rules at
every zoom: (1) constant SCREEN-px point sizing on a steep degree curve with a tone-mapped
saturation cap, (2) an edge draws only when both ends are visible, no structural-hop
exception, alpha floor ~0.06, (3) labels for the top-N objects by degree inside the current
viewport, de-overlapped, at every zoom. "Focus is a separate scene" (implemented here via
the existing per-instance aVisible hide mechanism rather than a literal second THREE.Scene
-- see the WIP decision for that interpretation call) keeps THE DRILL's own container/
anchor/stub model, reframed to work without the tier system. Mirrors the repo's existing
static-source-guard convention: string/substring proofs against the served JS, no browser
harness. Live verification is explicitly DEFERRED per Thoth's own instruction, pending
Khnum's physics layout landing -- these tests prove the code shape, not a rendered frame.
"""
from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).parent.parent / "src" / "ui" / "static"
_SPACE_JS = (_STATIC / "space.js").read_text()


# --- "kill LOD entirely" -- every named tier/cluster/halo mechanism is gone ----------------

def test_lod_tier_system_is_retired_outright() -> None:
    for dead in (
        "function zoomLOD(",
        "LOD_FAR_FRACTION",
        "LOD_MID_FRACTION",
        "function buildCentroidIndex(",
        "function dominantTypePerProject(",
        "function buildTierLabels(",
        "function positionTierLabels(",
        "function buildClusterEdgeLines(",
        "function buildTypePairEdgeLines(",
        "function buildClusterRings(",
        "function buildHalo(",
        "function isAtFit(",
        "function updateHaloVisibility(",
        "function refreshLOD(",
        "HALO_RADIUS_FACTOR",
        "CLUSTER_RING_SEGMENTS",
        "fitViewSize",
        "function nodeRadiusWorld(",
        "CATEGORY_BASE_WORLD",
        "NODE_MIN_SCREEN_PX",
        "NODE_MAX_SCREEN_PX",
        "uMinRadiusWorld",
        "uMaxRadiusWorld",
        "EDGE_BUNDLE_STRENGTH",
        "EDGE_DENSITY_TARGET",
        "uDensityScale: {",
    ):
        assert dead not in _SPACE_JS, f"{dead!r} should have been fully removed"


def test_tip4_own_tests_were_deleted_not_left_to_rot() -> None:
    assert not (Path(__file__).parent / "test_legibility_pass_tip4.py").exists()


# --- rule 1: constant screen-px sizing on the steep degree curve, uncapped -----------------

def test_point_size_is_a_constant_screen_px_degree_curve() -> None:
    # px = 2 * degree^log10(2) -- verified against Thoth's own four anchors: degree 1 -> 2px,
    # 100 -> 8px, 1,000 -> 16px, 10,000 -> 32px (d^log10(2) = 2^log10(d), so at d=10^k the
    # curve is exactly 2*2^k).
    assert "const DEGREE_PX_BASE = 2;" in _SPACE_JS
    assert "const DEGREE_PX_EXPONENT = Math.log10(2);" in _SPACE_JS
    body = _SPACE_JS.split("function nodeScreenPx(nd)", 1)[1][:200]
    assert "DEGREE_PX_BASE * Math.pow(Math.max(nd.degree || 0, 1), DEGREE_PX_EXPONENT)" in body


def test_degree_px_curve_hits_thoths_own_anchors() -> None:
    base, exponent = 2.0, __import__("math").log10(2.0)
    for degree, expected_px in ((1, 2.0), (100, 8.0), (1000, 16.0), (10000, 32.0)):
        px = base * (max(degree, 1) ** exponent)
        assert abs(px - expected_px) < 1e-9, (degree, px, expected_px)


def test_sizing_is_a_shader_uniform_no_world_unit_scheme_no_cap() -> None:
    assert "attribute float aRadiusPx;" in _SPACE_JS
    assert "uniform float uWorldPerPx;" in _SPACE_JS
    assert "transformed *= aRadiusPx * uWorldPerPx * aVisible;" in _SPACE_JS
    # no min/max clamp anywhere in the sizing formula -- uncapped past the curve, per the
    # ruling's own explicit words.
    assert "clamp(aRadiusWorld" not in _SPACE_JS


# --- rule 1b: per-pixel saturation cap via HDR render target + Reinhard tone-map -----------

def test_scene_renders_offscreen_hdr_then_tone_maps_with_a_bounded_curve() -> None:
    assert "new THREE.WebGLRenderTarget(1, 1, {" in _SPACE_JS
    assert "type: THREE.HalfFloatType" in _SPACE_JS
    body = _SPACE_JS.split("const toneMapMaterial = new THREE.ShaderMaterial(", 1)[1][:1200]
    assert "color / (color + vec3(1.0))" in body  # Reinhard: bounded in [0,1) for any x >= 0


def test_tone_map_target_is_sized_on_init_and_resize() -> None:
    assert "function resizeSceneTarget()" in _SPACE_JS
    # called once during setup and again on every window resize, so the offscreen target
    # always matches the actual canvas resolution (dpr included).
    assert _SPACE_JS.count("resizeSceneTarget();") >= 2


def test_render_path_falls_back_gracefully_if_the_render_target_cannot_be_created() -> None:
    # a GPU without float-render-target support must not crash the whole renderer -- direct
    # renderer.render(scene, camera) is the fallback when sceneTarget is null.
    body = _SPACE_JS.split("function renderScene()", 1)[1][:400]
    assert "if (sceneTarget)" in body
    assert "renderer.render(scene, camera);" in body


def test_point_opacity_was_raised_now_that_tone_mapping_owns_the_saturation_cap() -> None:
    assert "NODE_POINT_OPACITY" in _SPACE_JS
    assert "0.55" not in _SPACE_JS.split("NODE_POINT_OPACITY", 1)[1][:80]


# --- rule 2: an edge draws only when both ends are visible, no structural-hop exception ----

def test_base_edge_layer_requires_both_ends_visible_no_exception() -> None:
    body = _SPACE_JS.split("function buildEdgeLines(nodes, edgeList)", 1)[1][:700]
    assert "nodeVisible(byId.get(e.source)) && nodeVisible(byId.get(e.target))" in body


def test_focus_overlay_edges_also_require_both_ends_reachable_no_structural_carve_out() -> None:
    body = _SPACE_JS.split("function updatePathEdges()", 1)[1][:1400]
    assert "if (!onPath) continue;" in body
    assert "structuralOfFocus" not in _SPACE_JS


def test_edge_alpha_floor_is_the_new_006_not_the_old_004() -> None:
    assert "uMinAlpha: { value: 0.06 }" in _SPACE_JS
    assert "uMinAlpha: { value: 0.04 }" not in _SPACE_JS


def test_edges_are_no_longer_bundled_or_density_scaled() -> None:
    # TIP 3/4's own cross-cluster quadratic-Bezier bundling and density-target budget are
    # fully retired -- buildEdgeLines is back to a straight-line-only, fixed-size geometry.
    body = _SPACE_JS.split("function buildEdgeLines(nodes, edgeList)", 1)[1][:2500]
    assert "EDGE_CURVE_SEGMENTS" not in body
    assert "bundle" not in body.lower()


# --- rule 3: top-N-by-degree labels inside the current viewport, every zoom, no tiers ------

def test_labels_are_top_n_by_degree_within_the_viewport_no_tier_gating() -> None:
    body = _SPACE_JS.split("function pickLabels()", 1)[1][:900]
    assert "nodeVisible(nd) && nd.x >= minX && nd.x <= maxX && nd.y >= minY && nd.y <= maxY" in body
    assert "(b.degree || 0) - (a.degree || 0)" in body
    assert "N_LABELS" in body
    assert "const N_LABELS = 40;" in _SPACE_JS


def test_position_labels_has_no_tier_branch_left() -> None:
    body = _SPACE_JS.split("function positionLabels()", 1)[1][:1600]
    # no more tier GATE -- the loop over labeledNodes runs unconditionally, no branch on any
    # zoom tier (a lingering explanatory comment mentioning "tier" in prose is fine; a real
    # tier conditional, e.g. `if (tier ===`, is not).
    assert "if (tier ===" not in body
    assert "positionTierLabels(" not in body
    # drill/anchor/stub divs still get positioned every frame, same pass.
    assert "positionDrillDivs();" in body
    assert "positionProjectAnchors();" in body
    assert "positionProjectStubs();" in body


def test_project_names_are_not_a_separate_label_pass_theyre_just_high_degree_objects() -> None:
    # "project names are just the highest-degree objects in view, no separate project-label
    # pass" -- proven by absence: no project-specific label-building function survives.
    assert "function buildTierLabels(" not in _SPACE_JS
    assert "projectLabelEntries" not in _SPACE_JS


# --- acceptance: focusing a container opens under 30 nodes with zero global geometry -------

def test_container_focus_short_circuits_before_the_ordinary_ego_walk() -> None:
    body = _SPACE_JS.split("async function focusObject(id, opts)", 1)[1][:800]
    assert "if (isContainerFocus(id)) { await renderContainerDrill(id, opts); return; }" in body


def test_container_threshold_matches_the_ego_cap() -> None:
    assert "const MAX_EGO_NODES = 300;" in _SPACE_JS
    body = _SPACE_JS.split("function isContainerFocus(id)", 1)[1][:300]
    assert "n > MAX_EGO_NODES" in body


def test_drill_hub_plus_one_count_node_per_member_type_paged_by_degree() -> None:
    assert "const DRILL_PAGE_SIZE = 50;" in _SPACE_JS
    body = _SPACE_JS.split("function renderContainerDrill", 1)[1][:2200]
    assert "drillMembersByType" in body
    assert "DRILL_PAGE_SIZE * drillPageCount" in body


# --- acceptance: project filter leaves zero foreign labels/edges/points --------------------

def test_hidden_projects_are_excluded_from_the_single_shared_visibility_gate() -> None:
    # nodeVisible is the one function pickLabels/buildEdgeLines/applyDim all route through,
    # so a project-filter leak in any one of them is structurally impossible now.
    body = _SPACE_JS.split("function nodeVisible(nd)", 1)[1][:400]
    assert "hiddenProjects.has(nd.project)" in body
    assert "revealedStubIds.has(nd.id)" in body


def test_project_filter_reset_clears_prior_scoped_reveals() -> None:
    body = _SPACE_JS.split("function setHiddenProjects(", 1)[1][:400]
    assert "revealedStubIds = new Set();" in body


# --- cross-project anchors and stub reveal, reframed without the tier system ---------------

def test_cross_project_anchors_render_only_when_reachable_spans_two_or_more_projects() -> None:
    body = _SPACE_JS.split("function buildProjectAnchors(focusId)", 1)[1][:1600]
    assert "if (byProject.size < 2) return;" in body


def test_project_stub_reveal_is_scoped_never_unhides_the_whole_project() -> None:
    body = _SPACE_JS.split("function revealProjectStub(entry)", 1)[1][:700]
    assert "revealedStubIds.add(" in body
    assert "hiddenProjects.delete(" not in body


# --- drill labels are the only interactive lod-glyph-label variant --------------------------

def test_drill_and_anchor_labels_accept_pointer_events_unlike_the_base_tier_class() -> None:
    index_html = (_STATIC / "index.html").read_text()
    space_html = (_STATIC / "space.html").read_text()
    for html in (index_html, space_html):
        assert ".ego-drill-label { pointer-events: auto;" in html
