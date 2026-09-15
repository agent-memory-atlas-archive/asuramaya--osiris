"""THE LEGIBILITY PASS, TIP 1 (ruling e1cb9e3b, Thoth DM 10708, thread 71c4ca0d). The
operator's own screenshots plus Thoth's own live measurement at fit (9 world units/px,
median nearest-neighbour 19 units = 2px, average node radius 8.4px = 75 units -- every node
covering ~60 neighbours; a focus on a degree-8 Decision reaching only itself and fitting the
camera to a point at 300x). Six pieces, all off d0d50d2: (a) world-unit node size with a
screen floor/cap, (b) a log-scale degree curve, (c) labels are real names not a wall of
garbage text, (d) focus hides (not dims) and is never empty, (e) one search (the header
omnibox) plus a shared type-visibility flag for header pills and the legend, (f) canvas
controls move off the table drawer's bottom edge. Mirrors the existing static-source-guard
convention -- no browser test harness exists in this repo; the live render (before/after at
fit and at one cluster) was verified via claude-in-chrome and reported on thread 71c4ca0d,
not re-proven here.

AMENDED (operator via Thoth mail 10726, ruling amending e1cb9e3b) before this tip even
shipped its first review: (1) a single CLICK on a node is the WHOLE gesture -- select,
inspector, hide, fit, one act; no double-click, no Enter, click on empty canvas clears;
(2) the hidden/fitted state renders within 100ms of the click from the client-side edge
index, the inspector fetch fills in after and never gates the visual; (3) the lens is
upstream by default until roots (no depth cap), downstream is a toggle off by default,
grounded_by/decided_in/answers added to the walk; (4) an EGO RELAYOUT while focused --
focused object at centre, ancestors ranked leftward by hop (roots farthest left), siblings
spread within their rank, spacing in screen pixels converted to world at the current zoom,
temporary, Clear restores the real stored positions.
"""
from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).parent.parent / "src" / "ui" / "static"
_SPACE_JS = (_STATIC / "space.js").read_text()
_CONSOLE_JS = (_STATIC / "console.js").read_text()
_INDEX_HTML = (_STATIC / "index.html").read_text()
_SPACE_HTML = (_STATIC / "space.html").read_text()
_OSIRIS_CSS = (_STATIC / "osiris.css").read_text()


# --- (a)+(b): world-unit radius, floor/cap in screen px, log-scale degree curve -----------

def test_node_radius_is_computed_in_world_units_not_pixels() -> None:
    assert "function nodeRadiusWorld(nd)" in _SPACE_JS
    assert "const CATEGORY_BASE_WORLD = { agent: 8, object: 5 };" in _SPACE_JS


def test_screen_floor_and_cap_exist_and_are_reasonable() -> None:
    assert "const NODE_MIN_SCREEN_PX = 1.5;" in _SPACE_JS
    assert "const NODE_MAX_SCREEN_PX = 48;" in _SPACE_JS


def test_degree_factor_is_log_scale_not_the_old_asymptotic_curve() -> None:
    body = _SPACE_JS.split("function degreeFactor(nd)", 1)[1].split("\n  }\n", 1)[0]
    assert "Math.log2(" in body
    assert "1 / (1 +" not in body  # the old asymptotic 1/(1+x) shape is gone


def test_degree_curve_matches_thoths_own_anchors() -> None:
    # degree 3 -> ~1x, 30 -> ~2x, 300 -> ~3.5x, 20,000 -> ~6x (Thoth DM 10708).
    import math
    k, d0 = 0.43, 6
    def factor(d: float) -> float:
        return 1 + max(0.0, k * math.log2(max(d, 1e-9) / d0))
    assert abs(factor(3) - 1.0) < 0.05
    assert abs(factor(30) - 2.0) < 0.15
    assert abs(factor(300) - 3.5) < 0.2
    assert abs(factor(20000) - 6.0) < 0.2
    assert f"const DEGREE_LOG_K = {k};" in _SPACE_JS
    assert f"const DEGREE_LOG_D0 = {d0};" in _SPACE_JS


# --- (c): labels are names, per-type formatting, hard truncation, a hover card ------------

def test_labels_resolve_real_names_off_the_wire_header_now() -> None:
    # TIP 1b (Thoth mail 10755): "swap the client label fallback for the header labels" --
    # Khnum's own `labels` array (graph_stream.py's _short_label, tip 2g) is the source now,
    # synchronous off nd.label, not a per-node /objects/{id} fetch for the general case.
    assert "function labelTextFor(nd)" in _SPACE_JS
    assert "label: snap.labels ? snap.labels[i] : undefined," in _SPACE_JS
    body = _SPACE_JS.split("function labelTextFor(nd)", 1)[1][:400]
    assert 'if (nd.type !== "Commit") return nd.label || fallbackLabel(nd);' in body


def test_commit_labels_use_the_subject_line_not_the_canonical_id() -> None:
    # review flaw #6: Khnum's own server-side label falls back to "Commit commit:<sha>"
    # (no subject property on the wire snapshot) -- a narrow client-side upgrade for Commit
    # only, never a per-node fetch for any other type.
    assert "async function fetchCommitSubject(nd)" in _SPACE_JS
    body = _SPACE_JS.split("async function fetchCommitSubject(nd)", 1)[1][:500]
    assert 'obj.properties.find((p) => p.name === "subject");' in body
    assert "text = `Commit: ${subject.value}`;" in body


def test_labels_are_hard_truncated_at_forty_chars_with_an_ellipsis() -> None:
    assert "const LABEL_MAX = 40;" in _SPACE_JS
    body = _SPACE_JS.split("function truncateLabel(s)", 1)[1].split("\n  }\n", 1)[0]
    assert "flat.slice(0, LABEL_MAX - 1) + \"…\"" in body


def test_commit_subject_is_cached_per_id_fetched_at_most_once() -> None:
    body = _SPACE_JS.split("function labelTextFor(nd)", 1)[1][:600]
    assert "_commitSubjectCache.get(nd.id)" in body
    assert "_commitSubjectInFlight.has(nd.id)" in body


def test_hover_card_exists_and_shows_label_plus_type_and_project() -> None:
    assert 'hoverEl.className = "hover-card";' in _SPACE_JS
    body = _SPACE_JS.split("function updateHoverCard(nd)", 1)[1].split("\n  }\n", 1)[0]
    assert "labelTextFor(nd)" in body
    assert "nd.type" in body and "nd.project" in body
    # never fires while dragging (would fight a pan) and is debounced, not per-mousemove
    mm_body = _SPACE_JS.split('addEventListener("mousemove"', 1)[1][:400]
    assert "if (dragging)" in mm_body
    assert "setTimeout(() => {" in mm_body


def test_hover_card_css_exists_in_both_pages() -> None:
    for html in (_INDEX_HTML, _SPACE_HTML):
        assert ".hover-card {" in html


# --- (d): focus hides (per-instance aVisible), never dims; never empty --------------------

def test_focus_uses_a_per_instance_visibility_flag_not_a_dim_scalar() -> None:
    assert 'geo.setAttribute("aVisible", visibleAttr);' in _SPACE_JS
    assert "attribute float aVisible;" in _SPACE_JS
    assert "* aVisible;" in _SPACE_JS


def test_a_focused_node_with_no_semantic_edges_still_lights_its_structural_neighbours() -> None:
    body = _SPACE_JS.split("async function focusObject(id, opts)", 1)[1][:2200]
    assert "if (pathReachable.size <= 1) {" in body
    assert 'if (e.edgeClass !== "structural") continue;' in body
    assert "pathReachable.add(other);" in body


def test_base_edge_layer_hides_edges_touching_an_invisible_node() -> None:
    body = _SPACE_JS.split("function nodeVisible(nd)", 1)[1][:400]
    assert "hiddenNodeTypes.has(nd.type)" in body
    # CONSOLE CHROME CLEANUP piece 2 (decision 31717ca7): the repo selector's own
    # hidden-set must ALSO gate edge-geometry visibility here, the same as applyDim's
    # own per-instance flag — an edge touching a project-hidden node must not still draw.
    assert "hiddenProjects.has(nd.project)" in body
    assert "pathReachable.has(nd.id)" in body
    build_body = _SPACE_JS.split("function buildEdgeLines(nodes, edgeList)", 1)[1][:700]
    assert "nodeVisible(byId.get(e.source))" in build_body
    assert "nodeVisible(byId.get(e.target))" in build_body


# --- (e): one search (header omnibox), type filters share the visibility flag -------------

def test_the_in_canvas_find_a_node_box_is_gone() -> None:
    for html in (_INDEX_HTML, _SPACE_HTML):
        assert 'id="graph-search"' not in html
        assert 'id="graph-search-dd"' not in html
    assert "searchInput" not in _SPACE_JS
    assert "searchDd" not in _SPACE_JS


def test_header_omnibox_graph_hits_always_focus() -> None:
    # SUPERSEDED by THE LEGIBILITY PASS TIP 1's own amendment (mail 10726): select-vs-focus
    # (click selects, Enter focuses) is retired -- a Graph hit focuses either way now.
    body = _CONSOLE_JS.split("const graphHits = hits.filter", 1)[1][:400]
    assert "run: () => { switchSurface('browse'); focus(h.id); }" in body
    assert "selectFromOmni" not in _CONSOLE_JS
    assert "runFocus" not in _CONSOLE_JS


def test_header_type_filters_and_the_legend_drive_the_same_visibility_flag() -> None:
    assert "function setHiddenTypes(types)" in _SPACE_JS
    assert "function syncSpaceTypeFilter()" in _CONSOLE_JS
    body = _CONSOLE_JS.split("function toggleEntityType(t)", 1)[1][:300]
    assert "syncSpaceTypeFilter();" in body
    legend_body = _SPACE_JS.split("[data-legend-node-type]", 1)[1][:400]
    assert "hiddenNodeTypes.delete(type)" in legend_body


def test_header_repo_selector_drives_the_same_visibility_flag_by_project() -> None:
    """CONSOLE CHROME CLEANUP piece 2 (decision 31717ca7, thread 0be2f790's own operator-
    finding follow-up): setHiddenProjects is the repo pill's own sibling to
    setHiddenTypes above — same per-instance aVisible flag, filtered by nd.project
    instead of nd.type."""
    assert "function setHiddenProjects(projects)" in _SPACE_JS
    assert "function syncSpaceProjectFilter()" in _CONSOLE_JS
    body = _CONSOLE_JS.split("function applyRepoFilter()", 1)[1][:400]
    assert "syncSpaceProjectFilter();" in body
    apply_dim_body = _SPACE_JS.split("function applyDim()", 1)[1][:800]
    assert "hiddenProjects.has(nd.project)" in apply_dim_body


def test_legend_gains_a_node_types_section() -> None:
    body = _SPACE_JS.split("function renderLegend(edgeList, nodeList)", 1)[1][:1900]
    assert "legend-node-type" in body
    assert "node types" in body


# --- (f): canvas controls move to the top strip, the drawer owns the bottom edge ----------

def test_canvas_controls_sit_at_the_top_not_colliding_with_the_drawer() -> None:
    body = _OSIRIS_CSS.split(".graph-canvas-controls {", 1)[1][:200]
    assert "top: 14px;" in body
    assert "bottom: 14px;" not in body


def test_status_line_and_legend_panel_moved_off_the_drawers_bottom_band() -> None:
    assert "#space-status-line { position: absolute; bottom: 44px;" in _INDEX_HTML
    assert ".legend-panel { position: absolute; top: 52px; right: 14px;" in _INDEX_HTML


# --- AMENDMENT item 1: a single click is the whole gesture --------------------------------

def test_click_on_empty_canvas_still_clears() -> None:
    click_body = _SPACE_JS.split('addEventListener("click", (ev) => {', 1)[1][:300]
    assert "else clearFocus();" in click_body


# --- AMENDMENT item 2: the 100ms budget -- client-side, inspector fetch never gates it -----

def test_the_visual_work_is_synchronous_the_inspector_fetch_is_awaited_last() -> None:
    body = _SPACE_JS.split("async function focusObject(id, opts)", 1)[1]
    # every `await` inside focusObject's own body must be the final `await inspect(id);` --
    # no earlier await (a network call) can gate the synchronous select/hide/fit work above.
    fn_body = body.split("\n  async function inspect(id)", 1)[0]
    awaits = [ln.strip() for ln in fn_body.splitlines() if "await " in ln]
    assert awaits, "expected at least one await in focusObject"
    assert awaits[-1] == "await inspect(id);"
    assert len(awaits) == 1  # the ONLY await is the trailing inspector fetch


# --- AMENDMENT item 3: upstream until roots, downstream a toggle off by default -----------

def test_depth_is_unlimited_by_default_until_roots() -> None:
    assert "const FOCUS_DEPTH_DEFAULT = Infinity;" in _SPACE_JS


def test_downstream_is_a_toggle_off_by_default() -> None:
    assert "let includeDownstream = false;" in _SPACE_JS
    body = _SPACE_JS.split("async function focusObject(id, opts)", 1)[1][:900]
    assert "includeDownstream ? bfsHops(inAdjPath, id, focusDepth) : new Map([[id, 0]])" in body
    btn_body = _SPACE_JS.split("if (downstreamBtn) {", 1)[1][:500]
    assert "includeDownstream = !includeDownstream;" in btn_body
    assert 'downstreamBtn.textContent = "Downstream: off";' in _SPACE_JS


# --- AMENDMENT item 4: ego relayout while focused, temporary, restored on clear -----------

def test_ego_relayout_exists_and_ranks_ancestors_leftward_roots_farthest() -> None:
    assert "function applyEgoLayout(focusId, hopsUp, hopsDown)" in _SPACE_JS
    body = _SPACE_JS.split("function applyEgoLayout(focusId, hopsUp, hopsDown)", 1)[1][:1200]
    # ancestors (hopsUp) get a NEGATIVE signed rank -- more hops (closer to root) = further
    # negative = further left; downstream (hopsDown) gets a positive rank, mirrored right.
    assert "(byRank.get(-hop) || (byRank.set(-hop, []), byRank.get(-hop))).push(id);" in body
    assert "(byRank.get(hop) || (byRank.set(hop, []), byRank.get(hop))).push(id);" in body
    assert "const x = cx + signedHop * colW;" in body


def test_ego_layout_spacing_is_screen_pixels_converted_to_world_at_current_zoom() -> None:
    assert "const EGO_COL_SPACING_PX = 150;" in _SPACE_JS
    assert "const EGO_ROW_SPACING_PX = 34;" in _SPACE_JS
    body = _SPACE_JS.split("function applyEgoLayout(focusId, hopsUp, hopsDown)", 1)[1][:500]
    assert "const wpp = worldPerPx();" in body
    assert "const colW = EGO_COL_SPACING_PX * wpp, rowH = EGO_ROW_SPACING_PX * wpp;" in body


def test_ego_layout_is_temporary_clear_restores_the_stored_positions() -> None:
    assert "function restoreEgoLayout()" in _SPACE_JS
    restore_body = _SPACE_JS.split("function restoreEgoLayout()", 1)[1].split("\n  }\n", 1)[0]
    assert "nd.x = pos.x; nd.y = pos.y;" in restore_body
    clear_body = _SPACE_JS.split("function clearFocus()", 1)[1][:400]
    assert "restoreEgoLayout();" in clear_body
    apply_body = _SPACE_JS.split("function applyEgoLayout(focusId, hopsUp, hopsDown)", 1)[1][:200]
    assert "restoreEgoLayout();" in apply_body  # a fresh focus never layers onto a stale one


def test_ego_layout_moves_only_gpu_instances_for_the_moved_nodes_not_a_full_rebuild() -> None:
    # O(moved), never O(49k) -- the perf discipline this whole arc has held since mail 10581.
    assert "function syncMovedInstancePositions(movedIds)" in _SPACE_JS
    body = _SPACE_JS.split("function syncMovedInstancePositions(movedIds)", 1)[1][:700]
    assert "if (!movedIds.has(nd.id)) continue;" in body
    assert "mesh.setMatrixAt(i, dummy.matrix);" in body
    assert "pickMesh.setMatrixAt(i, dummy.matrix);" in body
