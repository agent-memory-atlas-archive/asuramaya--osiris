"""THE RENDERER, REOPENED FOR FOCUS AND PREVIEW (operator ruling, grounds 5b37d219, Thoth
mail 11272). THE LAST RENDERER (ruling d7d55257) froze the renderer after w301; this
ruling reopens it specifically for focus/preview, not a blanket reversal -- the base dim
layer's own "no bundling, straight lines only" rule (mail 11066) stays exactly as it was.

Measured defect that triggered the ruling: focus walked PATH_EDGE_TYPES only, so focusing
a real 402-degree agent (Sekhmet) reached 43 nodes over succeeded_from/succeeds_seat and
nothing else -- the operator saw a wall of same-named labels and never what the agent
actually did.

Live-verified via claude-in-chrome against the deployed graph (main fc9e1b47): focusing
that exact agent went from 43 (path-only) to 60 reachable (43 base + 17 direct one-hop
members), with one "spawned_by (in) 381" group node (paged on click) and one container
anchor pulled out ("analyst:operator"). Expanding the group added 50 more (110 total),
correctly laid out as a tight, ordered, de-overlapped chain instead of the wide horizontal
smear a naive physics seed produced on the first attempt (caught live, fixed before
shipping). The SAME chain-compression fix also closed the true root cause of "the camera
does not refit to something legible": the pre-existing ranked-column path layout itself
spread a real 43-generation succession chain across a 544,433-world-unit span using the
full column width every hop; compressed to an 85,028-unit span (6.4x) once succession-only
ranks use the tight chain spacing too. Ring/halo, pinned larger label, and inspector header
all confirmed visible together in the same screenshot; hover card confirmed showing
"Agent · repo:osiris · gen 43" for that same node. Bundled quadratic curves confirmed via
geometry inspection (56 vertices = 2 curved edges x 28 verts each, from
BUNDLE_CURVE_SEGMENTS=14 x 2) and a screenshot showing genuinely bowed (not straight) lines.

Mirrors the repo's existing static-source-guard convention: string/substring proofs
against the served JS, no browser harness.
"""
from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).parent.parent / "src" / "ui" / "static"
_SPACE_JS = (_STATIC / "space.js").read_text()


# --- item 1: one-hop-all-types neighbourhood, grouped, paged, container anchors -----------

def test_one_hop_by_type_direction_scans_all_edge_types_both_directions() -> None:
    body = _SPACE_JS.split("function oneHopByTypeDirection(id)", 1)[1][:900]
    assert 'direction = "out";' in body
    assert 'direction = "in";' in body
    # a container-class neighbour is pulled OUT of the type/direction grouping entirely --
    # one anchor each, never bucketed.
    assert "if (isContainerFocus(otherId)) { containerNeighbors.set(otherId, nd); " \
        "continue; }" in body


def test_small_buckets_place_directly_large_buckets_page_like_the_container_drill() -> None:
    body = _SPACE_JS.split("function buildEgoGroups(id, hub)", 1)[1][:3900]
    assert "if (members.length <= DRILL_PAGE_SIZE && members.length <= budgetLeft) {" in body
    assert "const take = Math.min(ranked.length, DRILL_PAGE_SIZE * egoGroupPageCount, " \
        "Math.max(0, budgetLeft));" in body
    assert 'egoGroupEntries.push({ key: `more:${key}`, kind: "more"' in body


def test_focus_base_path_reachable_is_the_unchanged_provenance_walk() -> None:
    # the ORIGINAL PATH_EDGE_TYPES walk stays the base set; one-hop groups are additive.
    body = _SPACE_JS.split("async function focusObject(id, opts)", 1)[1][:3700]
    assert "focusBasePathReachable = new Set(pathReachable);" in body
    assert "renderFocusEgoGroups(id, hopsUp, hopsDown);" in body


def test_group_expand_click_never_wipes_its_own_just_set_state() -> None:
    # the SAME bug class caught in THE DRILL (mail 11241): a click handler sets
    # egoGroupExpandedKey/egoGroupPageCount then re-renders through the SAME shared path a
    # fresh focus uses -- renderFocusEgoGroups itself must never reset that state (only
    # focusObject, on an actual container change, does).
    body = _SPACE_JS.split("function focusObject(id, opts)", 1)[1][:3400]
    assert "if (egoGroupFocusId !== id) { egoGroupExpandedKey = null; " \
        "egoGroupPageCount = 1; }" in body
    render_body = _SPACE_JS.split("function renderFocusEgoGroups(id, hopsUp, hopsDown)", 1)[1][:400]
    assert "egoGroupExpandedKey = null" not in render_body
    assert "egoGroupPageCount = 1" not in render_body


def test_ego_group_debug_hooks_exist_for_live_verification() -> None:
    body = _SPACE_JS.split("const api = {", 1)[1]
    assert "oneHopByTypeDirection," in body
    assert "get egoGroupEntries()" in body
    assert "get egoContainerAnchorEntries()" in body
    assert "expandEgoGroup(key)" in body


# --- item 2: unmistakable focus -- ring/halo, pinned larger label, guaranteed refit -------

def test_focus_ring_is_a_real_overlay_sized_off_the_nodes_own_screen_radius() -> None:
    assert 'focusRingEl.className = "focus-ring";' in _SPACE_JS
    body = _SPACE_JS.split("function positionFocusRing()", 1)[1][:700]
    assert "const nd = pathFocusId ? idById.get(pathFocusId) : null;" in body
    assert "if (!nd) { focusRingEl.hidden = true; return; }" in body
    for html in ("index.html", "space.html"):
        page = (_STATIC / html).read_text()
        assert ".focus-ring {" in page


def test_focus_label_is_bigger_than_a_merely_lit_label() -> None:
    for html in ("index.html", "space.html"):
        page = (_STATIC / html).read_text()
        assert ".lbl.focus-label { font-size: 13px" in page
    body = _SPACE_JS.split("function positionLabels()", 1)[1][:1500]
    assert '(nd.id === pathFocusId ? " focus-label" : "")' in body
    assert "positionFocusRing();" in body


def test_every_focus_and_every_group_click_refits_the_camera() -> None:
    # live-verified regression: refitting only lived inside the original focusObject body;
    # a group/"more" click re-rendered through a path that never touched the camera. Now
    # the fit lives inside renderFocusEgoGroups, the ONE shared render path both use.
    body = _SPACE_JS.split("function renderFocusEgoGroups(id, hopsUp, hopsDown)", 1)[1][:1600]
    assert "for (const rid of pathReachable) {" in body
    assert "camera.position.x = (minX + maxX) / 2;" in body


def test_succession_only_ranks_use_the_tight_chain_width_not_the_full_column() -> None:
    # live-verified root cause of "the camera does not refit to something legible": a real
    # 43-generation succession chain in the ORIGINAL ranked-column path layout spanned
    # 544,433 world units at the full EGO_COL_SPACING_PX per hop; 85,028 (6.4x tighter)
    # once a pure single-file succession run uses CHAIN_SPACING_PX instead.
    body = _SPACE_JS.split(
        "function applyEgoLayout(focusId, hopsUp, hopsDown, extraSeed)", 1)[1][:4900]
    assert "const successionAdj = new Map();" in body
    assert "x += side * (isChainStep ? chainW : colW);" in body
    assert "const fixedIds = new Set([focusId, ...chainRankIds]);" in body


# --- item 3: hover card matches the label exactly, plus type/project/generation -----------

def test_hover_card_and_label_read_off_the_identical_identity_string() -> None:
    # labelTextFor(nd) is the SAME call pickLabels' own div.textContent uses -- label and
    # card were already structurally incapable of disagreeing before this tip; generation
    # is the new piece.
    body = _SPACE_JS.split("function updateHoverCard(nd)", 1)[1][:500]
    assert "${labelTextFor(nd)}" in body
    assert "const generation = computeGeneration(nd);" in body
    assert '" · gen " + generation' in body


def test_generation_is_hops_back_a_succeeded_from_chain_null_if_not_in_one() -> None:
    body = _SPACE_JS.split("function computeGeneration(nd)", 1)[1][:900]
    assert 'if (!succeededFromMembers.has(nd.id)) return null;' in body
    assert "return count + 1;" in body


# --- item 4: succession chains render as an ordered, de-overlapped chain ------------------

def test_succession_chain_gets_a_real_generation_order_not_a_radial_fan() -> None:
    body = _SPACE_JS.split("function orderSuccessionChain(focusId, members, edgeType)", 1)[1][:1400]
    assert "const dist = new Map([[focusId, 0]]);" in body
    assert ".sort((a, b) => dist.get(a.id) - dist.get(b.id));" in body


def test_chain_members_are_pinned_never_relaxed_back_into_a_smear() -> None:
    # live-verified: seeding a chain in order then letting the SAME O(n^2) repulsion that
    # spreads a wide rank run over it just as happily unfolds the chain back into the wide
    # smear this fix exists to prevent. pinned: true routes into applyEgoLayout's own
    # fixedIds set, which relaxPositions now accepts as a Set (not just one id).
    body = _SPACE_JS.split("function buildEgoGroups(id, hub)", 1)[1][:3900]
    assert "pinned: true" in body
    relax_body = _SPACE_JS.split(
        "function relaxPositions(seed, springs, fixedId)", 1)[1][:1500]
    assert "if (id === fixedId || (fixedId instanceof Set && fixedId.has(id))) " \
        "continue;" in relax_body


def test_chain_spacing_is_tighter_than_the_ordinary_rank_column_width() -> None:
    assert "const CHAIN_SPACING_PX = 22;" in _SPACE_JS  # < EGO_COL_SPACING_PX (150)
    assert 'const SUCCESSION_EDGE_TYPES = new Set(["succeeded_from", ' \
        '"succeeds_seat"]);' in _SPACE_JS


# --- item 5: cross-cluster focus edges over a screen-px threshold bundle as curves --------

def test_only_the_focus_overlay_bundles_the_base_dim_layer_stays_straight() -> None:
    # THE LAST RENDERER's own "no bundling, straight lines only" rule (mail 11066) is
    # UNCHANGED for buildEdgeLines (the base dim layer) -- this reopening is scoped to
    # updatePathEdges (the focus overlay) only, per the new ruling's own grounds.
    base_body = _SPACE_JS.split("function buildEdgeLines(nodes, edgeList)", 1)[1][:2500]
    assert "BUNDLE_CURVE_SEGMENTS" not in base_body
    assert "quadratic" not in base_body.lower()


def test_cross_cluster_edges_over_the_screen_px_threshold_bundle_as_curves() -> None:
    body = _SPACE_JS.split("function updatePathEdges()", 1)[1][:3000]
    assert "const crossCluster = a.project && b.project && a.project !== b.project;" in body
    assert "if (!crossCluster || screenLen <= BUNDLE_SCREEN_PX_THRESHOLD) {" in body
    assert "const ctrlX = midX + nx * bow, ctrlY = midY + ny * bow;" in body
    assert body.count("BUNDLE_CURVE_SEGMENTS") >= 2  # the const, and the loop bound


def test_bundle_alpha_falls_with_length_never_hits_true_zero() -> None:
    body = _SPACE_JS.split("function updatePathEdges()", 1)[1][:3000]
    assert "const alpha = Math.max(BUNDLE_ALPHA_FLOOR, 1 / (1 + over));" in body
    assert "const BUNDLE_ALPHA_FLOOR = 0.15;" in _SPACE_JS


def test_bundle_threshold_is_measured_in_real_screen_pixels_not_world_units() -> None:
    # a "long" edge depends on the CURRENT zoom, not a fixed world distance -- screenLen
    # divides the world distance by the live worldPerPx(), same convention nodeScreenPx
    # and the wheel/render instrumentation already use.
    body = _SPACE_JS.split("function updatePathEdges()", 1)[1][:900]
    assert "const wpp = worldPerPx();" in body
    assert "const screenLen = worldLen / wpp;" in body
