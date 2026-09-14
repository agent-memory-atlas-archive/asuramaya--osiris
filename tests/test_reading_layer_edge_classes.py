"""THE READING LAYER, part A: EDGE CLASSES (ruling c5953bb1, Thoth DM 10596, thread
71c4ca0d). The operator's own word on his screenshot: "focus needs to really focus so I can
see long paths leading back and upstream", "inspector, table and graph in harmony".
Structural edges (containment/membership -- in_repo, works_in, governs, ...) are real but
not what a reader is tracing and their degree dwarfs everything else (repo:osiris alone:
20,352); they are not drawn at rest. Semantic edges (the actual provenance trail --
possible_upstream, cites, derived_from, spawned_by, succeeded_from, supersedes, resolves,
...) draw always, with alpha falling by ON-SCREEN length rather than by zoom level. A
legend toggles both classes and individual types. Mirrors the existing static-source-guard
convention -- no browser test harness exists in this repo, so these are string-presence
proofs against the served JS/HTML; the live render (structural hidden/shown correctly on
toggle, no crash on a ~20k-edge rebuild, click-to-focus still works throughout) was
verified via claude-in-chrome and is reported on the thread, not re-proven here.
"""
from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).parent.parent / "src" / "ui" / "static"
_SPACE_JS = (_STATIC / "space.js").read_text()
_INDEX_HTML = (_STATIC / "index.html").read_text()
_SPACE_HTML = (_STATIC / "space.html").read_text()


# --- classification: a default, disclosed rather than parked on (mail 10596's own
# instruction), swapped for Khnum's real per-request edge_classes the moment it lands -------

def test_a_default_structural_type_list_exists_and_is_disclosed() -> None:
    assert "const STRUCTURAL_EDGE_TYPES = new Set([" in _SPACE_JS
    for t in ("in_repo", "works_in", "governs", "holds", "acts_for", "member_of"):
        assert f'"{t}"' in _SPACE_JS
    assert "function classOfEdgeType(type)" in _SPACE_JS


def test_classification_prefers_khnums_real_header_field_over_the_fallback() -> None:
    body = _SPACE_JS.split("async function fetchStreamSnapshot()", 1)[1].split(
        "\n  return { nodes, edges };", 1)[0]
    assert "snap.edge_classes" in body
    assert "classOfEdgeType(type)" in body


# --- structural edges are NOT drawn at rest; the legend is the only way to opt back in ----

def test_structural_class_is_hidden_by_default() -> None:
    assert 'const hiddenEdgeClasses = new Set(["structural"]);' in _SPACE_JS


def test_edge_geometry_build_filters_by_hidden_classes_and_types() -> None:
    body = _SPACE_JS.split("function buildEdgeLines(nodes, edgeList)", 1)[1][:600]
    assert "!hiddenEdgeClasses.has(e.edgeClass)" in body
    assert "!hiddenEdgeTypes.has(e.type)" in body


# --- semantic edges fade by ON-SCREEN length (a GPU shader, not a per-zoom CPU rewrite --
# the same discipline mail 10581 already established for node sizing) ----------------------

def test_edge_fade_is_a_shader_not_a_per_zoom_material_opacity_write() -> None:
    assert "function makeEdgeFadeMaterial()" in _SPACE_JS
    assert "attribute vec3 otherPosition;" in _SPACE_JS
    assert "float screenLen = distance(pxA, pxB);" in _SPACE_JS
    # the old viewSize-driven opacity scalar is gone, not just unused
    assert "function updateEdgeStyle()" not in _SPACE_JS
    assert "edgeLines.material.opacity" not in _SPACE_JS


def test_rescale_for_zoom_no_longer_touches_edge_style_at_all() -> None:
    body = _SPACE_JS.split("function rescaleForZoom()", 1)[1].split("\n  }\n", 1)[0]
    assert "updateEdgeStyle" not in body
    assert "edgeLines" not in body


def test_viewport_uniform_is_kept_current_on_resize() -> None:
    body = _SPACE_JS.split('window.addEventListener("resize"', 1)[1][:400]
    assert "edgeFadeUniforms.uViewportPx.value.set" in body


# --- the legend: lists real classes/types present, toggles rebuild the edge geometry ------

def test_legend_markup_exists_in_both_pages() -> None:
    for html in (_INDEX_HTML, _SPACE_HTML):
        assert 'id="legend-btn"' in html
        assert 'id="legend-panel"' in html


def test_legend_toggle_button_shows_and_hides_the_panel() -> None:
    body = _SPACE_JS.split("if (legendBtn && legendPanel)", 1)[1][:200]
    assert "legendPanel.hidden = !legendPanel.hidden;" in body


def test_legend_checkboxes_rebuild_edge_lines_on_change() -> None:
    body = _SPACE_JS.split("function renderLegend(edgeList)", 1)[1]
    # one call for class-row toggles, one for type-row toggles
    assert body.count("buildEdgeLines(idToNode, edges);") == 2
