"""THE LEGIBILITY PASS, TIP 4: "DENSITY NOT DISCS" (operator ruling, Thoth mail 11011,
off w284's own deploy eca1efc): the operator's own word on three screenshots of TIP 3's
LOD glyph discs -- retire the tiers-as-discs mechanism wholesale in favour of density
itself reading legibly. Replaces test_legibility_pass_tip3.py outright (deleted -- every
assertion there concerned the glyph-disc mechanism this tip retires) and drops the five
glyph-specific tests from test_legibility_pass_tip3b.py (its own remaining three, the
omnibox fallback fix, are untouched by this tip and still live there).

Five pieces:

  (a) NO DISCS: every object draws at every zoom now, as an additive point sprite,
      world-sized with a 1px floor -- a project far out reads as a haze whose brightness
      is its own count (buildScene/makeInstancedCircleMaterial). The pick mesh stays fully
      opaque (GPU id-decode would break under additive blending); only the draw mesh is
      additive. An optional thin outline ring per real project (buildClusterRings), no
      fill, at far only.
  (b) LABELS ONLY: "tier" no longer decides WHAT is drawn, only which LABELS show --
      far: project names at centroids; mid: PLUS each cluster's own dominant type; near:
      individual object titles (unchanged mechanism, now just reads zoomLOD() the same
      way). Same screen-space de-overlap TIP 3b's own glyph labels used, carried forward
      (placeTierLabels/tierLabelOverlaps).
  (c) EDGES: every edge draws at every tier now too, alpha scaled by 1/(edges on screen)
      (edgeFadeUniforms.uDensityScale) so a dense web fades toward a haze instead of
      either painting solid or vanishing outright. Far additionally draws the aggregated
      cluster_edges (unchanged from TIP 3). Cross-cluster edges (different real projects,
      neither "unfiled") tessellate as a quadratic Bezier bent toward the midpoint of the
      two projects' own centroids, so a fan of individual cross-cluster lines bundles into
      one readable ribbon.
  (d) FOCUS DISABLES TIERING: zoomLOD() returns "near" outright whenever pathFocusId is
      set, whatever the camera's own zoom happens to be -- the operator's own second
      complaint ("a focus whose fit lands in the far band shows a blob instead of the
      tree") is a direct consequence of a wide ego layout's own camera-fit viewSize
      coincidentally landing in the far/mid wpp band under the OLD tier-decides-rendering
      design; with tiering gone for objects/edges and labels alone still tiered, this one
      override keeps a focus always at full per-object detail.
  (e) THE HALO stays: unchanged from TIP 3, verified still present and still wired the
      same way.

Same static-source-guard convention as every prior legibility tip file.
"""
from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).parent.parent / "src" / "ui" / "static"
_SPACE_JS = (_STATIC / "space.js").read_text()
_INDEX_HTML = (_STATIC / "index.html").read_text()
_SPACE_HTML = (_STATIC / "space.html").read_text()


# --- retirement: the old glyph-disc mechanism is gone outright, not just unreachable ------

def test_the_old_glyph_disc_mechanism_is_gone_entirely() -> None:
    for needle in (
        "function buildLODGlyphs()", "function disposeGlyphs()", "function rescaleGlyphs()",
        "function pickGlyphAt(", "function drillIntoGlyph(", "function applyEdgeBudget(",
        "function positionAggregateLabels(", "function placeGlyphLabels(",
        "function aggOverlapsPlaced(", "projectGlyphGroup", "typeGlyphGroup",
        "projectGlyphMeshes", "typeGlyphMeshes", "GLYPH_BASE_PX", "glyphScreenRadiusPx",
        "EDGE_BUDGET_PER_NODE_MID", "screenToWorld",
    ):
        assert needle not in _SPACE_JS, f"{needle} should have been removed"


# --- piece (a): no discs, additive point sprites, always drawn ---------------------------

def test_node_min_screen_floor_is_a_literal_one_pixel() -> None:
    assert "const NODE_MIN_SCREEN_PX = 1;" in _SPACE_JS


def test_draw_mesh_is_additive_pick_mesh_stays_opaque() -> None:
    body = _SPACE_JS.split("function makeInstancedCircleMaterial(opts)", 1)[1][:700]
    assert "blending: THREE.AdditiveBlending" in body
    assert "const built = makeInstancedCircleMaterial({ additive: true });" in _SPACE_JS
    # the pick mesh's own call passes no options -- stays opaque, exact-colour decodable
    assert "const builtPick = makeInstancedCircleMaterial();" in _SPACE_JS


def test_mesh_visibility_is_never_toggled_by_zoom_any_more() -> None:
    assert "mesh.visible = " not in _SPACE_JS
    assert "pickMesh.visible = " not in _SPACE_JS


def test_cluster_rings_are_a_real_spatial_boundary_not_a_count_icon_far_only() -> None:
    body = _SPACE_JS.split("function buildClusterRings()", 1)[1][:1200]
    assert "agg.cx + Math.cos(a0) * agg.radius" in body
    assert '(projectNames[agg.project] || "unfiled") === "unfiled") continue;' in body
    assert 'clusterRingLines.visible = zoomLOD() === "far";' in body


# --- piece (b): tiers apply to labels only, far/mid/near --------------------------------

def test_far_shows_project_names_mid_adds_the_dominant_type_near_shows_nothing_tiered() -> None:
    body = _SPACE_JS.split("function positionTierLabels(tier)", 1)[1][:300]
    assert 'placeTierLabels(projectLabelEntries, tier === "far" || tier === "mid");' in body
    assert 'placeTierLabels(typeLabelEntries, tier === "mid");' in body


def test_dominant_type_is_the_single_highest_count_type_per_project_not_every_pair() -> None:
    body = _SPACE_JS.split("function dominantTypePerProject()", 1)[1][:500]
    assert "if (!cur || agg.count > cur.count) best.set(agg.project, agg);" in body
    build_body = _SPACE_JS.split("function buildTierLabels()", 1)[1][:1400]
    assert "if (!dom) continue;" in build_body
    # one type label per PROJECT (keyed by agg.project), not one per (project,type) pair
    assert "typeLabelEntries.push({ div: tdiv, x: dom.cx, y: dom.cy, " \
        "count: dom.count });" in build_body


def test_unfiled_gets_no_project_label_or_dominant_type_label() -> None:
    body = _SPACE_JS.split("function buildTierLabels()", 1)[1][:600]
    assert 'if (name === "unfiled") continue;' in body


def test_tier_labels_declutter_by_screen_space_overlap_largest_count_first() -> None:
    body = _SPACE_JS.split("function placeTierLabels(entries, show)", 1)[1][:500]
    assert "entries[b].count - entries[a].count" in body
    assert "if (tierLabelOverlaps(x, y)) { e.div.hidden = true; continue; }" in body


def test_tier_label_declutter_state_resets_every_frame() -> None:
    body = _SPACE_JS.split("function positionTierLabels(tier)", 1)[1][:200]
    assert "_tierPlaced.length = 0;" in body


def test_object_titles_are_still_a_near_tier_concern() -> None:
    body = _SPACE_JS.split("function positionLabels()", 1)[1][:1400]
    assert 'if (tier !== "near") { div.hidden = true; continue; }' in body
    assert "positionTierLabels(tier);" in body


def test_glyph_label_css_class_carried_forward_for_the_tier_labels() -> None:
    for html in (_INDEX_HTML, _SPACE_HTML):
        assert ".lod-glyph-label" in html


# --- piece (c): edges -- density-scaled alpha, far cluster edges, cross-cluster bundling --

def test_every_tier_draws_the_full_edge_set_no_tier_gated_cutoff() -> None:
    # buildEdgeLines itself no longer branches on zoomLOD() at all -- it's called with the
    # already-filtered edge list, same for every tier.
    body = _SPACE_JS.split("function buildEdgeLines(nodes, edgeList)", 1)[1][:2600]
    assert "zoomLOD()" not in body


def test_edge_alpha_is_density_scaled_by_the_on_screen_count() -> None:
    body = _SPACE_JS.split("function buildEdgeLines(nodes, edgeList)", 1)[1][:600]
    assert "edgeFadeUniforms.uDensityScale.value = Math.max(EDGE_DENSITY_MIN," in body
    assert "EDGE_DENSITY_TARGET / Math.max(1, visible.length)" in body
    shader_body = _SPACE_JS.split("uniform float uDensityScale;", 1)[1][:700]
    assert "* uDensityScale;" in shader_body


def test_edge_material_is_additive_now() -> None:
    body = _SPACE_JS.split("function makeEdgeFadeMaterial()", 1)[1][:300]
    assert "blending: THREE.AdditiveBlending" in body


def test_far_tier_still_draws_cluster_edges_between_project_centroids() -> None:
    body = _SPACE_JS.split("function buildClusterEdgeLines()", 1)[1][:1600]
    assert "centroidByProject.get(ce.a)" in body
    assert "centroidByProject.get(ce.b)" in body
    assert 'clusterEdgeLines.visible = zoomLOD() === "far";' in body


def test_cross_cluster_edges_bundle_toward_the_two_projects_own_centroid_midpoint() -> None:
    body = _SPACE_JS.split("function buildEdgeLines(nodes, edgeList)", 1)[1][:2600]
    assert 'if (na.project !== nb.project && ca && cb) {' in body
    assert "const cmx = (ca.cx + cb.cx) / 2, cmy = (ca.cy + cb.cy) / 2;" in body
    assert "const ctrlX = mx + (cmx - mx) * EDGE_BUNDLE_STRENGTH;" in body
    # quadratic Bezier tessellation, not a single straight segment, for the bundled case
    assert "for (let s = 1; s <= EDGE_CURVE_SEGMENTS; s++) {" in body


def test_same_project_or_unfiled_involved_edges_stay_straight() -> None:
    body = _SPACE_JS.split("function buildEdgeLines(nodes, edgeList)", 1)[1][:2600]
    assert 'na.project !== "unfiled" ? centroidByProjectName.get(na.project) : null;' in body
    assert "} else {\n        pushSeg(ax, ay, bx, by);\n      }" in body


def test_centroid_index_is_built_before_the_first_edge_layer_needs_it() -> None:
    # buildEdgeLines is called from inside buildScene, which the init sequence calls
    # AFTER buildCentroidIndex -- otherwise the very first paint's edges would silently
    # skip bundling for lack of a populated centroidByProjectName.
    body = _SPACE_JS.split("setStatus(\"loading the whole graph…\");", 1)[1][:800]
    idx_at = body.index("buildCentroidIndex();")
    scene_at = body.index("buildScene(nodes, edges);")
    assert idx_at < scene_at


# --- piece (d): focus disables tiering entirely -------------------------------------------

def test_a_focus_forces_near_tier_regardless_of_the_cameras_own_zoom() -> None:
    body = _SPACE_JS.split("function zoomLOD()", 1)[1][:200]
    assert 'if (pathFocusId) return "near";' in body


# --- piece (e): the halo stays, unchanged -------------------------------------------------

def test_halo_only_lights_nodes_with_at_least_one_real_edge() -> None:
    body = _SPACE_JS.split("function buildHalo()", 1)[1][:900]
    assert "idToNode.filter((nd) => (nd.degree || 0) > 0);" in body
    assert "THREE.AdditiveBlending" in body


def test_halo_is_gated_to_the_fit_view_itself_not_the_wider_far_band() -> None:
    body = _SPACE_JS.split("function isAtFit()", 1)[1][:400]
    assert "if (pathFocusId) return false;" in body
    assert "fitWpp * 0.98;" in body


def test_halo_visibility_refreshes_on_every_zoom_step() -> None:
    body = _SPACE_JS.split("function refreshLOD()", 1)[1][:300]
    assert "updateHaloVisibility();" in body


# --- rebuild wiring: context loss and live deltas ------------------------------------------

def test_context_restore_rebuilds_tier_labels_cluster_overlay_and_the_halo() -> None:
    body = _SPACE_JS.split('"webglcontextrestored"', 1)[1][:900]
    assert "buildTierLabels();" in body
    assert "buildClusterEdgeLines();" in body
    assert "buildClusterRings();" in body
    assert "buildHalo();" in body
    assert "refreshLOD();" in body


def test_a_delta_rebuild_refreshes_the_halo_not_the_aggregates() -> None:
    body = _SPACE_JS.split("function scheduleRebuild()", 1)[1][:700]
    assert "buildHalo();" in body
    assert "refreshLOD();" in body
    assert "buildTierLabels()" not in body  # aggregates stay the snapshot-time values


# --- the click handler is back to its pre-TIP-3 shape: one gesture, no tier branch --------

def test_click_handler_has_no_tier_branch_any_more() -> None:
    click_body = _SPACE_JS.split('addEventListener("click", (ev) => {', 1)[1][:300]
    assert "pickAt(ev.clientX, ev.clientY)" in click_body
    assert "focusObject(hit.id)" in click_body
    assert "zoomLOD()" not in click_body
