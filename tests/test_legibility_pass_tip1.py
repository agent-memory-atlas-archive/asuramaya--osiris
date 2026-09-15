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

def test_labels_resolve_real_names_not_the_old_wire_less_fallback() -> None:
    assert "function labelTextFor(nd)" in _SPACE_JS
    assert "async function fetchNodeLabel(nd)" in _SPACE_JS
    assert 'fetch(`/objects/${nd.id}`)' in _SPACE_JS


def test_label_formatting_is_per_type() -> None:
    body = _SPACE_JS.split("async function fetchNodeLabel(nd)", 1)[1][:700]
    assert 'const NAME_TYPES = new Set(["Agent", "SoftwareProject", "Person"]);' in _SPACE_JS
    assert "NAME_TYPES.has(nd.type) ? title : `${nd.type}: ${title}`" in body


def test_labels_are_hard_truncated_at_forty_chars_with_an_ellipsis() -> None:
    assert "const LABEL_MAX = 40;" in _SPACE_JS
    body = _SPACE_JS.split("function truncateLabel(s)", 1)[1].split("\n  }\n", 1)[0]
    assert "flat.slice(0, LABEL_MAX - 1) + \"…\"" in body


def test_label_names_are_cached_per_id_fetched_at_most_once() -> None:
    body = _SPACE_JS.split("function labelTextFor(nd)", 1)[1][:600]
    assert "_labelCache.get(nd.id)" in body
    assert "_labelInFlight.has(nd.id)" in body


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
    body = _SPACE_JS.split("async function focusObject(id, opts)", 1)[1][:1600]
    assert "if (pathReachable.size <= 1) {" in body
    assert "if (e.source === id) pathReachable.add(e.target);" in body


def test_base_edge_layer_hides_edges_touching_an_invisible_node() -> None:
    body = _SPACE_JS.split("function nodeVisible(nd)", 1)[1][:400]
    assert "hiddenNodeTypes.has(nd.type)" in body
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


def test_header_omnibox_graph_hits_select_on_click_and_focus_on_enter() -> None:
    body = _CONSOLE_JS.split("const graphHits = hits.filter", 1)[1][:600]
    assert "run: () => { switchSurface('browse').then(() => selectFromOmni(h.id)); }" in body
    assert "runFocus: () => { switchSurface('browse'); focus(h.id); }" in body
    assert "async function selectFromOmni(id)" in _CONSOLE_JS
    # Enter (not a plain click) is the only path that reaches runFocus.
    assert "execOmniItem(OMNI_SEL, true)" in _CONSOLE_JS
    exec_body = _CONSOLE_JS.split("function execOmniItem(idx, enterKey)", 1)[1][:200]
    assert "if (enterKey && item.runFocus) item.runFocus();" in exec_body


def test_header_type_filters_and_the_legend_drive_the_same_visibility_flag() -> None:
    assert "function setHiddenTypes(types)" in _SPACE_JS
    assert "function syncSpaceTypeFilter()" in _CONSOLE_JS
    body = _CONSOLE_JS.split("function toggleEntityType(t)", 1)[1][:300]
    assert "syncSpaceTypeFilter();" in body
    legend_body = _SPACE_JS.split("[data-legend-node-type]", 1)[1][:400]
    assert "hiddenNodeTypes.delete(type)" in legend_body


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
