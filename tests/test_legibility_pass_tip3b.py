"""THE LEGIBILITY PASS, TIP 3b (Thoth mail 10953): four flaws from her own live-Chrome
review of TIP 3 (w282, deployed as e81af83) -- see test_legibility_pass_tip3.py for the
tip's own three original pieces (LOD by zoom, edge budget, the at-fit halo), unchanged
here except where a flaw fix below revises one of them.

  1. Glyph labels collided at far (small projects pile in the centre) and at mid (type
     glyphs) -- fixed with a screen-space de-overlap pass, largest count first, colliding
     labels hidden until zoomed (same greedy-declutter shape positionLabels() already used
     for node labels, applied to the glyph labels too).
  2. Mid tier still drew a capped-but-real per-object edge set, reading as "the solid green
     cobweb" behind the type glyphs -- her own instruction: aggregated (project,type)
     edges or nothing, never per-object lines. No such aggregate exists on the wire, so mid
     draws nothing now, same as far.
  3. The omnibox agent-handle fallback still said "No matches" on the deployed page despite
     idToNode carrying thousands of matching Agent labels -- the `hits.length === 0` gate
     (plus a second sequential await) was fragile enough that the exact failure mode
     couldn't be pinned down with certainty, so the gate is gone outright: one Promise.all,
     one token check, the client-side scan runs unconditionally once the graph is loaded,
     deduped against whatever the server found.
  4. The unfiled bucket (no in_repo link) got its own project glyph at far, its raw count
     dwarfing every real project beside it -- fixed by skipping a glyph/label for it
     entirely; its own connected members still read through the ordinary at-fit halo.

Same static-source-guard convention as every prior legibility tip file.
"""
from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).parent.parent / "src" / "ui" / "static"
_SPACE_JS = (_STATIC / "space.js").read_text()
_CONSOLE_JS = (_STATIC / "console.js").read_text()


# --- flaw 1: glyph label de-overlap ---------------------------------------------------

def test_glyph_labels_declutter_by_screen_space_overlap() -> None:
    body = _SPACE_JS.split("function positionAggregateLabels(tier)", 1)[1][:500]
    assert "placeGlyphLabels(projectGlyphMeshes, projectLabelDivs, showProject);" in body
    assert "placeGlyphLabels(typeGlyphMeshes, typeLabelDivs, showType);" in body


def test_glyph_label_placement_sorts_by_count_descending_first() -> None:
    body = _SPACE_JS.split("function placeGlyphLabels(meshes, divs, show)", 1)[1][:700]
    assert "meshes[b].userData.agg.count - meshes[a].userData.agg.count" in body
    assert "if (aggOverlapsPlaced(x, y)) { div.hidden = true; continue; }" in body


def test_glyph_declutter_state_resets_every_frame_not_accumulated() -> None:
    body = _SPACE_JS.split("function positionAggregateLabels(tier)", 1)[1][:300]
    assert "_aggPlaced.length = 0;" in body


# --- flaw 2: no per-object edges at mid -------------------------------------------------

def test_mid_tier_draws_no_per_object_edges_any_more() -> None:
    body = _SPACE_JS.split("function applyEdgeBudget(edgeList)", 1)[1][:400]
    assert 'if (tier === "near") return edgeList;' in body
    assert "return []; // far and mid: no per-object edges, ever" in body
    # the old capped-semantic-per-node mid path is gone outright, not just unreachable
    assert "EDGE_BUDGET_PER_NODE_MID" not in _SPACE_JS
    assert 'if (e.edgeClass !== "semantic") continue;' not in _SPACE_JS


# --- flaw 3: omnibox fallback runs unconditionally, not gated on empty hits -------------

def test_omnibox_agent_scan_runs_unconditionally_not_gated_on_empty_hits() -> None:
    body = _CONSOLE_JS.split("OMNI_SEARCH_TIMER = setTimeout(async () => {", 1)[1][:2000]
    assert "Promise.all([" in body
    assert "if (myToken !== OMNI_SEARCH_TOKEN) return;" in body
    # only ONE token check now -- the old sequential second await/check is gone
    assert body.count("if (myToken !== OMNI_SEARCH_TOKEN) return;") == 1
    assert "if (hits.length === 0) {" not in body


def test_omnibox_agent_scan_dedupes_against_server_hits() -> None:
    body = _CONSOLE_JS.split("OMNI_SEARCH_TIMER = setTimeout(async () => {", 1)[1][:2800]
    assert "const seenIds = new Set(hits.filter(h => h && h.id).map(h => h.id));" in body
    assert "n.type === 'Agent' && !seenIds.has(n.id)" in body


def test_omnibox_agent_scan_still_reads_the_fallback_safe_label() -> None:
    body = _CONSOLE_JS.split("OMNI_SEARCH_TIMER = setTimeout(async () => {", 1)[1][:2800]
    assert "n.label || `${n.type} ${n.id.slice(0, 8)}`" in body


# --- flaw 4: unfiled renders as the halo, not a competing glyph ------------------------

def test_unfiled_project_gets_no_glyph_or_label() -> None:
    body = _SPACE_JS.split("function buildLODGlyphs()", 1)[1][:900]
    assert 'if (name === "unfiled") continue;' in body
