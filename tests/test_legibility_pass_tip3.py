"""THE LEGIBILITY PASS, TIP 3: LOD BY ZOOM (Thoth mail 10930, off w280's own deploy
6a9293d) -- the last legibility tip for the night, dispatched once TIP 1c's own six live-
review flaws were confirmed on the deployed page. Three pieces, all off Khnum's own tip
2i/2j wire aggregates (project_aggregates/type_aggregates/cluster_edges, already riding the
GET /graph/stream header, no server change needed here):

  1. LOD BY ZOOM: three tiers (far/mid/near), thresholds expressed as fractions of the
     fit-time scale (fitViewSize) rather than a hardcoded world-per-px number, so they
     self-scale to whatever a real graph's own extent happens to be. Far shows project
     glyphs (name + count); mid shows type glyphs; near is today's unchanged per-object
     rendering.
  2. EDGE BUDGET: far draws only the aggregated cluster_edges (between project centroids,
     not per-object edges at all); mid keeps semantic edges only, budgeted to a fixed N per
     node ("strongest" has no real weight on the wire yet, so first-N by array order is the
     documented stand-in); near is unchanged (full edge set, same filters as before this
     tip).
  3. THE HALO: a faint additive-blended wash behind every node with at least one real edge,
     visible only at the whole-graph FIT view itself (not the wider "far" tier band) and
     only while nothing is focused -- "the connected third reads first," before a reader
     zooms or clicks anything.

Same static-source-guard convention as every prior legibility tip file -- this renderer has
no browser test harness, so these prove the algorithm's shape is present and wired the way
the dispatch asked, verified for real via claude-in-chrome/Playwright against a live dev
server (see the tip's own commit message and mail report for that evidence).
"""
from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).parent.parent / "src" / "ui" / "static"
_SPACE_JS = (_STATIC / "space.js").read_text()
_INDEX_HTML = (_STATIC / "index.html").read_text()
_SPACE_HTML = (_STATIC / "space.html").read_text()


# --- piece 1: LOD BY ZOOM ------------------------------------------------------------------

def test_three_lod_tiers_are_fractions_of_the_fit_time_scale() -> None:
    assert "let fitViewSize = 1300;" in _SPACE_JS
    body = _SPACE_JS.split("function zoomLOD()", 1)[1][:400]
    assert "fitViewSize / wrap.clientHeight" in body
    assert 'return "far";' in body
    assert 'return "mid";' in body
    assert 'return "near";' in body


def test_fit_to_nodes_captures_the_fit_scale_for_lod_thresholds() -> None:
    body = _SPACE_JS.split("function fitToNodes(list)", 1)[1][:900]
    assert "fitViewSize = viewSize;" in body


def test_project_and_type_aggregates_are_read_off_khnums_own_wire_header() -> None:
    body = _SPACE_JS.split("async function fetchStreamSnapshot()", 1)[1][:2200]
    assert "projectAggregates: snap.project_aggregates || []" in body
    assert "typeAggregates: snap.type_aggregates || []" in body
    assert "clusterEdges: snap.cluster_edges || []" in body


def test_far_tier_builds_project_glyphs_mid_tier_builds_type_glyphs() -> None:
    body = _SPACE_JS.split("function buildLODGlyphs()", 1)[1][:2500]
    assert "for (const agg of projectAggregates)" in body
    assert "for (const agg of typeAggregates)" in body
    assert "projectGlyphGroup.add(m);" in body
    assert "typeGlyphGroup.add(m);" in body
    # name + count, per the dispatch's own wording
    assert "`${name} (${agg.count})`" in body
    assert "`${typeName} (${agg.count})`" in body


def test_real_object_mesh_is_hidden_outside_the_near_tier() -> None:
    body = _SPACE_JS.split("function refreshLOD()", 1)[1][:700]
    assert 'if (mesh) mesh.visible = tier === "near";' in body
    assert 'if (pickMesh) pickMesh.visible = tier === "near";' in body


def test_node_labels_are_a_near_tier_concern_glyph_labels_take_over_above_that() -> None:
    body = _SPACE_JS.split("function positionLabels()", 1)[1][:1300]
    assert 'if (tier !== "near") { div.hidden = true; continue; }' in body
    assert "positionAggregateLabels(tier);" in body


def test_a_click_at_far_or_mid_drills_into_the_glyph_instead_of_focusing_an_object() -> None:
    click_body = _SPACE_JS.split('addEventListener("click", (ev) => {', 1)[1][:500]
    assert "pickGlyphAt(ev.clientX, ev.clientY, tier)" in click_body
    assert "drillIntoGlyph(hitGlyph)" in click_body


def test_drilling_into_a_glyph_lands_inside_the_next_tier_down() -> None:
    body = _SPACE_JS.split("function drillIntoGlyph(m)", 1)[1][:700]
    assert "ceilingWpp = fitWpp * LOD_MID_FRACTION * 0.85;" in body


# --- piece 2: EDGE BUDGET ------------------------------------------------------------------

def test_edge_budget_is_none_at_far_all_at_near_capped_semantic_at_mid() -> None:
    body = _SPACE_JS.split("function applyEdgeBudget(edgeList)", 1)[1][:800]
    assert 'if (tier === "far") return [];' in body
    assert 'if (tier === "near") return edgeList;' in body
    assert 'if (e.edgeClass !== "semantic") continue;' in body
    assert "EDGE_BUDGET_PER_NODE_MID" in body


def test_build_edge_lines_applies_the_budget_but_the_legend_sees_every_type_regardless() -> None:
    body = _SPACE_JS.split("function buildEdgeLines(nodes, rawEdgeList)", 1)[1][:2700]
    assert "const edgeList = applyEdgeBudget(rawEdgeList);" in body
    # the legend must not shrink/flicker as the zoom tier changes -- it reads the raw list
    assert "renderLegend(rawEdgeList, nodes);" in body


def test_far_tier_draws_cluster_edges_between_project_centroids_not_per_object_edges() -> None:
    body = _SPACE_JS.split("function buildClusterEdgeLines()", 1)[1][:1600]
    assert "centroidByProject.get(ce.a)" in body
    assert "centroidByProject.get(ce.b)" in body
    assert 'clusterEdgeLines.visible = zoomLOD() === "far";' in body


def test_cluster_edge_visibility_tracks_the_tier_on_every_lod_refresh() -> None:
    body = _SPACE_JS.split("function refreshLOD()", 1)[1][:700]
    assert 'if (clusterEdgeLines) clusterEdgeLines.visible = tier === "far";' in body


# --- piece 3: THE HALO -----------------------------------------------------------------

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


# --- context loss rebuilds every LOD-shaped GPU resource, not just the main scene ---------

def test_context_restore_rebuilds_lod_glyphs_cluster_edges_and_the_halo_too() -> None:
    body = _SPACE_JS.split('"webglcontextrestored"', 1)[1][:600]
    assert "buildLODGlyphs();" in body
    assert "buildClusterEdgeLines();" in body
    assert "buildHalo();" in body
    assert "refreshLOD();" in body


# --- a delta rebuild refreshes the halo (positions/degree can change) but not the ---------
# --- server-computed aggregates themselves (an accepted, documented staleness) ------------

def test_a_delta_rebuild_refreshes_the_halo_not_the_aggregates() -> None:
    body = _SPACE_JS.split("function scheduleRebuild()", 1)[1][:700]
    assert "buildHalo();" in body
    assert "refreshLOD();" in body
    assert "buildLODGlyphs()" not in body  # aggregates stay the snapshot-time values


# --- markup: the LOD glyph label style exists in both pages the renderer mounts in --------

def test_glyph_label_style_exists_in_both_pages() -> None:
    for html in (_INDEX_HTML, _SPACE_HTML):
        assert ".lod-glyph-label" in html
