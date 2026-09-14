"""THE READING LAYER, part C: HARMONY (ruling c5953bb1, Thoth DM 10596, thread 71c4ca0d).
The operator's own word: "inspector, table and graph in harmony". One selection state
lives in console.js (FOCUS, shared with the canvas via onSpaceFocus/window.OsirisSpace --
already built in the earlier NAVIGABLE SPACE, INTEGRATION piece, mail 10550); this part
wires the remaining three requirements: the table filters to the reachable set while a
real focus is on; a table row click selects and pans the graph (or, while a focus is
already active, walks a fresh focus from that row -- "its rows select and focus"); every
object reference in the inspector already walks the focus (built in part B's own inspect(),
re-confirmed here). Mirrors the existing static-source-guard convention.
"""
from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).parent.parent / "src" / "ui" / "static"
_SPACE_JS = (_STATIC / "space.js").read_text()
_CONSOLE_JS = (_STATIC / "console.js").read_text()


def test_table_filters_to_the_reachable_set_while_a_focus_is_on() -> None:
    body = _CONSOLE_JS.split("function renderEntityExplorerStage()", 1)[1].split(
        "\n}\n", 1)[0]
    assert "space.pathFocusId" in body
    assert "space.pathReachable" in body
    assert "filtered.filter(function(o) { return reachable.has(o.id); });" in body


def test_table_filter_reads_off_the_space_apis_own_live_getters() -> None:
    # pathReachable/pathFocusId are real getters on the api object (not a stale snapshot),
    # so console.js always sees the CURRENT focus state without any extra wiring.
    assert "get pathReachable() { return pathReachable; }" in _SPACE_JS
    assert "get pathFocusId() { return pathFocusId; }" in _SPACE_JS


def test_a_table_row_click_selects_and_pans_by_default() -> None:
    body = _CONSOLE_JS.split("function inspectOnly(id)", 1)[1][:1300]
    assert "space.selectObject(id, { pan: true });" in body


def test_a_table_row_click_focuses_instead_while_a_focus_is_already_active() -> None:
    body = _CONSOLE_JS.split("function inspectOnly(id)", 1)[1][:1300]
    assert "if (space && space.pathFocusId) space.focusObject(id);" in body


def test_select_with_pan_recenters_the_camera_without_changing_zoom() -> None:
    body = _SPACE_JS.split("async function selectObject(id, opts)", 1)[1].split(
        "\n  }\n", 1)[0]
    assert "if (opts && opts.pan)" in body
    assert "camera.position.x = nd.x;" in body
    assert "camera.position.y = nd.y;" in body
    # no viewSize/updateFrustum touch -- a pan, never a re-zoom.
    assert "viewSize" not in body
    assert "updateFrustum" not in body


def test_a_plain_canvas_click_does_not_pan_only_table_rows_do() -> None:
    click_body = _SPACE_JS.split('addEventListener("click", (ev) => {', 1)[1][:300]
    assert "selectObject(hit.id)" in click_body
    assert "selectObject(hit.id, { pan: true })" not in click_body


def test_every_inspector_reference_already_walks_the_focus() -> None:
    # built in part B's own inspect() -- re-confirmed here since it's exactly what part C's
    # "harmony" asks for (upstream_ids, readers, links all route through Osiris.loadRels'
    # own pick callback).
    body = _SPACE_JS.split("async function inspect(id)", 1)[1][:1300]
    assert "Osiris.loadRels(relsEl, id, (pickId) => focusObject(pickId), () => {});" in body
