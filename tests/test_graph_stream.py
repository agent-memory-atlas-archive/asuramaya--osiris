"""NAVIGABLE SPACE, THE SERVER piece B (rulings f832c3a4 + 0a3d6719, thread
b6cb1d7c0b36): the whole-graph typed-array snapshot and its outbox-backed delta poll.
Shape frozen by DM with Seshat (mail 10439/10449/10451) before this was written."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from src.actions.core import Actions
from src.api.app import create_app
from src.orchestrator.graph_layout import layout_batch
from src.orchestrator.graph_stream import (
    _short_label,
    decode_snapshot,
    deltas_since,
    encode_snapshot,
    fetch_snapshot,
    outbox_watermark,
    resolve_deltas_start_cursor,
)

HELPERS = Path(__file__).parent.parent / "helpers"


@pytest_asyncio.fixture
async def client(actions: Actions) -> AsyncIterator[httpx.AsyncClient]:
    from src.orchestrator.manifests import load_manifests

    app = create_app(actions.pool)
    app.state.pool = actions.pool
    app.state.manifests = load_manifests(HELPERS)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- pure encode/decode: the round trip the dispatch itself names as a test -----------


def test_snapshot_round_trips_through_the_decoder_with_the_exact_count() -> None:
    data = encode_snapshot(
        object_ids=["a", "b", "c"], x=[1.0, 2.0, 3.0], y=[4.0, 5.0, 6.0],
        type_code=[0, 1, 0], project_code=[0, 0, 1], weight=[2.0, 0.0, 5.0],
        status_flag=[0, 2, 0], edge_src=[0, 1], edge_dst=[1, 2], edge_type_code=[0, 1],
        edge_weight=[0.5, 1.0],
        types=["Thread", "Decision"], projects=["repo:x", "repo:y"],
        edge_types=["cites", "in_repo"], link_type_class=["semantic", "structural"],
        labels=["Thread abc", "Decision def", "Thread ghi"],
        created_at=[100.0, 200.0, 300.0],
        project_aggregates=[{"project": 0, "count": 2, "cx": 1.5, "cy": 4.5, "radius": 1.0}],
        cluster_edges=[{"a": 0, "b": 1, "class": "semantic", "count": 1}],
        type_pair_edges=[{"a": {"project": 0, "type": 0}, "b": {"project": 0, "type": 1},
                           "class": "semantic", "count": 1}],
    )
    out = decode_snapshot(data)
    assert out["count"] == 3
    assert out["edge_count"] == 2
    assert out["object_ids"] == ["a", "b", "c"]
    assert out["types"] == ["Thread", "Decision"]
    assert out["projects"] == ["repo:x", "repo:y"]
    assert out["edge_types"] == ["cites", "in_repo"]
    assert out["link_type_class"] == ["semantic", "structural"]
    assert out["labels"] == ["Thread abc", "Decision def", "Thread ghi"]
    assert out["project_aggregates"] == [
        {"project": 0, "count": 2, "cx": 1.5, "cy": 4.5, "radius": 1.0}]
    assert out["type_aggregates"] == []
    assert out["cluster_edges"] == [{"a": 0, "b": 1, "class": "semantic", "count": 1}]
    assert out["type_pair_edges"] == [
        {"a": {"project": 0, "type": 0}, "b": {"project": 0, "type": 1},
         "class": "semantic", "count": 1}]
    assert out["x"] == pytest.approx([1.0, 2.0, 3.0])
    assert out["y"] == pytest.approx([4.0, 5.0, 6.0])
    assert out["type_code"] == [0, 1, 0]
    assert out["project_code"] == [0, 0, 1]
    assert out["weight"] == pytest.approx([2.0, 0.0, 5.0])
    assert out["status_flag"] == [0, 2, 0]
    assert out["edge_src"] == [0, 1]
    assert out["edge_dst"] == [1, 2]
    assert out["edge_type_code"] == [0, 1]
    assert out["edge_weight"] == pytest.approx([0.5, 1.0])
    assert out["created_at"] == pytest.approx([100.0, 200.0, 300.0])


def test_snapshot_with_no_edges_still_round_trips() -> None:
    data = encode_snapshot(
        object_ids=["only"], x=[0.0], y=[0.0], type_code=[0], project_code=[0],
        weight=[0.0], status_flag=[0], edge_src=[], edge_dst=[], edge_type_code=[],
        edge_weight=[],
        types=["Thread"], projects=["unfiled"], edge_types=[], link_type_class=[],
        labels=["Thread only"], created_at=[0.0],
    )
    out = decode_snapshot(data)
    assert out["count"] == 1
    assert out["edge_count"] == 0
    assert out["edge_src"] == []
    assert out["edge_weight"] == []
    assert out["project_aggregates"] == []
    assert out["type_aggregates"] == []
    assert out["cluster_edges"] == []
    assert out["type_pair_edges"] == []


def test_encode_snapshot_rejects_a_mismatched_node_column_length() -> None:
    with pytest.raises(ValueError, match="x has"):
        encode_snapshot(
            object_ids=["a", "b"], x=[1.0], y=[1.0, 2.0], type_code=[0, 0],
            project_code=[0, 0], weight=[0.0, 0.0], status_flag=[0, 0],
            edge_src=[], edge_dst=[], edge_type_code=[], edge_weight=[], types=[],
            projects=[], edge_types=[], link_type_class=[], labels=["a", "b"],
            created_at=[0.0, 0.0],
        )


def test_encode_snapshot_rejects_a_mismatched_edge_column_length() -> None:
    with pytest.raises(ValueError, match="edge_dst has"):
        encode_snapshot(
            object_ids=["a"], x=[1.0], y=[1.0], type_code=[0], project_code=[0],
            weight=[0.0], status_flag=[0], edge_src=[0, 0], edge_dst=[0],
            edge_type_code=[0, 0], edge_weight=[0.0, 0.0], types=[], projects=[],
            edge_types=[], link_type_class=[], labels=["a"], created_at=[0.0],
        )


def test_encode_snapshot_rejects_a_mismatched_labels_length() -> None:
    with pytest.raises(ValueError, match="labels has"):
        encode_snapshot(
            object_ids=["a", "b"], x=[1.0, 2.0], y=[1.0, 2.0], type_code=[0, 0],
            project_code=[0, 0], weight=[0.0, 0.0], status_flag=[0, 0],
            edge_src=[], edge_dst=[], edge_type_code=[], edge_weight=[], types=[],
            projects=[], edge_types=[], link_type_class=[], labels=["only-one"],
            created_at=[0.0, 0.0],
        )


# --- _short_label: THE LEGIBILITY PASS, tip 2g/2c --------------------------------------


def test_short_label_agent_uses_agent_fallback_never_the_raw_handle() -> None:
    """THE IDENTITY FORMAT IS ALWAYS USED FOR AGENT (Thoth mail 11317): a raw
    `handle` assertion used to win outright, bypassing the resolved identity
    format -- now `agent_fallback` (required for type Agent) always wins."""
    assert _short_label(
        "Agent", "agent:deadbeef-g1", "Khnum", None, None,
        agent_fallback="Khnum VII") == "Khnum VII"


def test_short_label_agent_with_no_fallback_falls_through_to_title() -> None:
    """A degenerate case (agent_fallback somehow unresolved) still never reads
    the raw handle as a label of its own -- falls through to the generic
    title/canonical chain like any other type."""
    assert _short_label("Agent", "agent:deadbeef-g1", "Khnum", None, "some title") == (
        "Agent some title")


def test_short_label_software_project_strips_the_repo_scheme() -> None:
    assert _short_label("SoftwareProject", "repo:osiris", None, None, None) == "osiris"


def test_short_label_person_uses_name() -> None:
    assert _short_label(
        "Person", "principal:xyz", None, "Ada Lovelace", None) == "Ada Lovelace"


def test_short_label_uses_title_when_present_never_canonical() -> None:
    """THE LIVE FIX (Thoth mail 10892): a Decision/Thread/Message (or any type
    carrying a summary/title/subject/name assertion) must show that TITLE, not
    "Decision decision:91da77..." -- the operator's original complaint in a new
    coat, caught live on the deployed space."""
    assert _short_label(
        "Decision", "decision:91da776625f9", None, None,
        "THE LEGIBILITY PASS lands") == "Decision THE LEGIBILITY PASS lands"


def test_short_label_collapses_embedded_newlines_to_one_line() -> None:
    assert _short_label(
        "Thread", "thread:x", None, None, "line one\nline two") == "Thread line one line two"


def test_short_label_falls_back_to_type_plus_canonical_only_when_no_title(
) -> None:
    assert _short_label(
        "Thread", "thread:abc123", None, None, None) == "Thread thread:abc123"


def test_short_label_agent_without_a_handle_falls_back_to_title_then_canonical() -> None:
    assert _short_label("Agent", "agent:deadbeef-g1", None, None, None) == (
        "Agent agent:deadbeef-g1")
    assert _short_label("Agent", "agent:deadbeef-g1", None, None, "a real title") == (
        "Agent a real title")


def test_short_label_hard_truncates_at_40_chars_with_an_ellipsis() -> None:
    long_canonical = "thread:" + "x" * 60
    label = _short_label("Thread", long_canonical, None, None, None)
    assert len(label) == 40
    assert label.endswith("…")


def test_short_label_truncation_never_clips_the_sidechain_marker() -> None:
    """AGENT LABEL COSMETICS (thread 065031d1, Thoth DM 11536): a hard slice at 40
    chars used to be able to land inside (or just past) the trailing ' ⌊ sub'
    marker, producing unreadable noise like ' ⌊ su…' or dropping it outright --
    the marker must always survive whole, even if that means the label runs a
    few characters past the nominal ceiling."""
    long_fallback = "x" * 45 + " ⌊ sub"
    label = _short_label("Agent", "agent:deadbeef-g1", None, None, None,
                         agent_fallback=long_fallback)
    assert label.endswith(" ⌊ sub")
    assert "…" in label


# --- DB-backed: fetch_snapshot ---------------------------------------------------------


async def test_fetch_snapshot_includes_only_already_placed_objects(
    actions: Actions,
) -> None:
    a = await actions.create_or_find_object("Thread", "thread:gs-a", "test")
    b = await actions.create_or_find_object("Thread", "thread:gs-b", "test")
    await actions.create_link(a, b, "cites", "test", datetime.now(UTC), 1.0)
    await layout_batch(actions, limit=1000)

    data = await fetch_snapshot(actions.pool)
    out = decode_snapshot(data)
    assert str(a) in out["object_ids"]
    assert str(b) in out["object_ids"]
    assert out["count"] == len(out["object_ids"])
    assert out["edge_count"] >= 1
    assert "cites" in out["edge_types"]
    idx = out["edge_types"].index("cites")
    assert out["link_type_class"][idx] == "semantic"


async def test_fetch_snapshot_positions_match_graph_x_graph_y(actions: Actions) -> None:
    from src.orchestrator.graph_layout import positions_for

    oid = await actions.create_or_find_object("Thread", "thread:gs-pos", "test")
    await layout_batch(actions, limit=1000)
    expected = (await positions_for(actions, [oid]))[oid]

    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(oid))
    assert (out["x"][idx], out["y"][idx]) == pytest.approx(expected)


async def test_fetch_snapshot_excludes_unplaced_objects(actions: Actions) -> None:
    oid = await actions.create_or_find_object("Thread", "thread:gs-unplaced", "test")
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    assert str(oid) not in out["object_ids"]


async def test_fetch_snapshot_created_at_is_index_aligned_and_real(
    actions: Actions,
) -> None:
    # WAVE 26, LINEAGES ARE TIME (thread 3683a12a): a real epoch timestamp per object,
    # index-aligned to object_ids same as x/y -- a genuinely later-created object reads
    # a later created_at, never a placeholder.
    before = datetime.now(UTC).timestamp()
    oid = await actions.create_or_find_object("Thread", "thread:gs-created-at", "test")
    await layout_batch(actions, limit=1000)
    after = datetime.now(UTC).timestamp()

    out = decode_snapshot(await fetch_snapshot(actions.pool))
    assert len(out["created_at"]) == out["count"]
    idx = out["object_ids"].index(str(oid))
    # Float32 on the wire, not float64: at today's epoch magnitude (~1.79e9) the ULP is
    # 128s, so a value can round up to ~64s past its real value -- tolerance reflects
    # that honestly rather than asserting a sub-second precision the wire doesn't carry.
    assert before - 100 <= out["created_at"][idx] <= after + 100


async def test_fetch_snapshot_project_falls_back_to_the_project_assertion(
    actions: Actions,
) -> None:
    """THE MEMBERSHIP UNION FIX (ruling d7d55257, Thoth mail 11221): a member with
    NO in_repo link but a `project` assertion must still surface under that
    project's own canonical in the snapshot -- the renderer's labels, counts and
    filter all key on this same `projects` array."""
    await actions.create_or_find_object("SoftwareProject", "repo:gs-union", "test")
    member = await actions.create_or_find_object("Thread", "thread:gs-union-member", "test")
    now = datetime.now(UTC)
    await actions.assert_property(member, "project", "gs-union", "test", now, 1.0)
    await layout_batch(actions, limit=1000)

    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(member))
    pcode = out["project_code"][idx]
    assert out["projects"][pcode] == "repo:gs-union"
    assert out["projects"][pcode] != "unfiled"


async def test_fetch_snapshot_project_follows_a_merge(actions: Actions) -> None:
    """MEMBERSHIP FOLLOWS A MERGE (thread 826a1a13): a member's own in_repo link can
    name a SoftwareProject that has since been folded into a survivor -- the snapshot
    must key the object under the SURVIVOR's own live canonical, never the now-merged
    dupe's, the same rule graph_physics' own membership union follows."""
    survivor = await actions.create_or_find_object(
        "SoftwareProject", "repo:gs-merge-survivor", "test")
    dupe = await actions.create_or_find_object(
        "SoftwareProject", "repo:gs-merge-dupe", "test")
    member = await actions.create_or_find_object("Thread", "thread:gs-merge-member", "test")
    now = datetime.now(UTC)
    await actions.create_link(member, dupe, "in_repo", "test", now, 1.0)
    await actions.merge_objects(survivor, dupe, "test merge", "test")
    await layout_batch(actions, limit=1000)

    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(member))
    pcode = out["project_code"][idx]
    assert out["projects"][pcode] == "repo:gs-merge-survivor"


async def test_fetch_snapshot_project_follows_a_merge_via_the_project_assertion(
    actions: Actions,
) -> None:
    """The same fold, reached through the `project`-assertion fallback instead of a
    live in_repo link."""
    survivor = await actions.create_or_find_object(
        "SoftwareProject", "repo:gs-merge-survivor-2", "test")
    dupe = await actions.create_or_find_object(
        "SoftwareProject", "repo:gs-merge-dupe-2", "test")
    member = await actions.create_or_find_object(
        "Thread", "thread:gs-merge-assertion-member", "test")
    now = datetime.now(UTC)
    await actions.assert_property(member, "project", "gs-merge-dupe-2", "test", now, 1.0)
    await actions.merge_objects(survivor, dupe, "test merge", "test")
    await layout_batch(actions, limit=1000)

    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(member))
    pcode = out["project_code"][idx]
    assert out["projects"][pcode] == "repo:gs-merge-survivor-2"


async def test_fetch_snapshot_watermark_matches_the_live_outbox_tip(
    actions: Actions,
) -> None:
    """THE DELTA CURSOR FIX (thread fa3a4d42): the header's own watermark is what a
    client passes to /graph/stream/deltas?since= to pick up only what changed after
    this exact snapshot -- it must agree with outbox_watermark's own live read."""
    await actions.create_or_find_object("Thread", "thread:gs-watermark", "test")
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    assert out["watermark"] == await outbox_watermark(actions.pool)


async def test_fetch_snapshot_labels_index_align_with_object_ids(actions: Actions) -> None:
    """THE LEGIBILITY PASS, tip 2g."""
    oid = await actions.create_or_find_object("Thread", "thread:gs-label", "test")
    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(oid))
    assert out["labels"][idx] == "Thread thread:gs-label"


async def test_fetch_snapshot_labels_use_the_real_title_not_the_canonical(
    actions: Actions,
) -> None:
    """THE LIVE FIX (Thoth mail 10892): a Decision, a Thread, and a Commit each
    carrying a title-shaped assertion (summary/title/subject) must show it, never
    the bare canonical -- the exact regression caught live on the deployed space."""
    now = datetime.now(UTC)
    decision = await actions.create_or_find_object(
        "Decision", "decision:gs-title-a", "test")
    await actions.assert_property(
        decision, "summary", "a real decision summary", "test", now, 0.9)
    thread_obj = await actions.create_or_find_object("Thread", "thread:gs-title-b", "test")
    await actions.assert_property(
        thread_obj, "summary", "a real thread summary", "test", now, 0.9)
    commit_obj = await actions.create_or_find_object("Commit", "commit:gs-title-c", "test")
    await actions.assert_property(
        commit_obj, "subject", "a real commit subject", "test", now, 0.9)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))

    for oid, expected in (
        (decision, "Decision a real decision summary"),
        (thread_obj, "Thread a real thread summary"),
        (commit_obj, "Commit a real commit subject"),
    ):
        idx = out["object_ids"].index(str(oid))
        assert out["labels"][idx] == expected


async def test_fetch_snapshot_nameless_agent_falls_back_to_seat_handle_and_generation(
    actions: Actions,
) -> None:
    """THE NAMELESS-AGENT LABEL FIX (ruling e1cb9e3b(c)): a handle-less Agent whose
    lineage holds an active Seat labels as "<seat handle> <generation>", never its
    own canonical id. EVERY AGENT LABEL CARRIES A NUMERAL (Thoth mail 11326):
    generation 1 shows "I" too, not a bare handle."""
    now = datetime.now(UTC)
    seat = await actions.create_or_find_object("Seat", "seat:gs-nameless-a", "test")
    await actions.assert_property(seat, "handle", "Nefer", "test", now, 0.9)
    agent = await actions.create_or_find_object("Agent", "agent:gs-nameless-a-g1", "test")
    await actions.create_link(agent, seat, "holds", "test", now, 1.0)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    assert out["labels"][idx] == "Nefer I"


async def test_fetch_snapshot_nameless_seat_holder_generation_is_uppercase_roman(
    actions: Actions,
) -> None:
    """Thoth mail 11283: "unify the roman case... 'Thoth CVI' and 'Sekhmet
    XXXVIII' is how the fleet already writes them" -- the seat-branch's own
    generation display, previously lowercase."""
    now = datetime.now(UTC)
    seat = await actions.create_or_find_object("Seat", "seat:gs-nameless-roman", "test")
    await actions.assert_property(seat, "handle", "Thoth", "test", now, 0.9)
    agent = await actions.create_or_find_object("Agent", "agent:gs-nameless-roman-vii", "test")
    await actions.create_link(agent, seat, "holds", "test", now, 1.0)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    assert out["labels"][idx] == "Thoth VII"


async def test_fetch_snapshot_seat_generation_past_39_uses_the_display_roman(
    actions: Actions,
) -> None:
    """THE WRONG ROMAN FUNCTION (Thoth mail 11345, the w310 regression): the seat
    branch called `agents._to_roman`, the CANONICAL-ID-SUFFIX formatter -- capped
    at 39, falling back to a raw "g<n>" escape hatch above that (built for a
    mintable id, hex-collision-free, never meant to reach a human-facing label).
    Live specimens: "thoth G107", "Khnum G72", "Seshat G65" -- every current seat
    holder past generation 39. `agents._roman_display` (unbounded, already
    uppercase, the SAME function `seat_label` itself uses for "Thoth CVI") is the
    right one -- 107 renders as "CVII", never "G107"."""
    now = datetime.now(UTC)
    seat = await actions.create_or_find_object("Seat", "seat:gs-past-39", "test")
    await actions.assert_property(seat, "handle", "Thoth", "test", now, 0.9)
    agent = await actions.create_or_find_object("Agent", "agent:gs-past-39-holder", "test")
    await actions.assert_property(agent, "seat_generation", "107", "test", now, 0.9)
    await actions.create_link(agent, seat, "holds", "test", now, 1.0)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    label = out["labels"][idx]
    assert label == "Thoth CVII"
    assert not re.search(r"G\d+", label)


async def test_fetch_snapshot_seat_holder_label_folds_in_the_model_suffix(
    actions: Actions,
) -> None:
    """THE MODEL SUFFIX ON EVERY LABEL (Thoth mail 11345): the seat branch used to
    never read `source_model` at all -- "Thoth VII", never "Thoth VII ·
    sonnet-5" -- even when the assertion existed. Folded on now, uniformly with
    the patronym and canonical-stem branches."""
    now = datetime.now(UTC)
    seat = await actions.create_or_find_object("Seat", "seat:gs-seat-model", "test")
    await actions.assert_property(seat, "handle", "Seshat", "test", now, 0.9)
    agent = await actions.create_or_find_object("Agent", "agent:gs-seat-model-holder", "test")
    await actions.assert_property(agent, "seat_generation", "65", "test", now, 0.9)
    await actions.assert_property(agent, "source_model", "claude-haiku-4-5", "test", now, 0.9)
    await actions.create_link(agent, seat, "holds", "test", now, 1.0)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    assert out["labels"][idx] == "Seshat LXV · haiku-4-5"


async def test_fetch_snapshot_seat_holder_label_capitalises_a_lowercase_handle(
    actions: Actions,
) -> None:
    """AGENT LABEL COSMETICS (thread 065031d1, Thoth DM 11536): a seat's own `handle`
    assertion is stored in whatever casing it was first claimed with ('thoth' next
    to 'Khnum') -- the seat branch must display-case an all-lowercase one so the
    header never shows 'thoth CVII' beside 'Khnum LXXII'."""
    now = datetime.now(UTC)
    seat = await actions.create_or_find_object("Seat", "seat:gs-seat-lowercase", "test")
    await actions.assert_property(seat, "handle", "thoth", "test", now, 0.9)
    agent = await actions.create_or_find_object("Agent", "agent:gs-seat-lowercase-holder", "test")
    await actions.assert_property(agent, "seat_generation", "107", "test", now, 0.9)
    await actions.create_link(agent, seat, "holds", "test", now, 1.0)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    assert out["labels"][idx] == "Thoth CVII"


async def test_fetch_snapshot_seat_holder_label_leaves_mixed_case_handle_untouched(
    actions: Actions,
) -> None:
    """The capitalisation fix must never touch an already-cased handle ('Khnum') --
    only the all-lowercase shape."""
    now = datetime.now(UTC)
    seat = await actions.create_or_find_object("Seat", "seat:gs-seat-mixed-case", "test")
    await actions.assert_property(seat, "handle", "Khnum", "test", now, 0.9)
    agent = await actions.create_or_find_object("Agent", "agent:gs-seat-mixed-case-holder", "test")
    await actions.assert_property(agent, "seat_generation", "72", "test", now, 0.9)
    await actions.create_link(agent, seat, "holds", "test", now, 1.0)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    assert out["labels"][idx] == "Khnum LXXII"


async def test_fetch_snapshot_seat_succession_canonical_reads_the_real_generation(
    actions: Actions,
) -> None:
    """THE SEAT-GENERATION FIX (Thoth mail 11309, w306 review): a seat-succession
    canonical (agent:seat-<id>-g<N>) stamps its own ordinal as a seat_generation
    ASSERTION, not in the canonical string the way an ordinary lineage id is --
    _generation(canonical) silently read 1 for a low "-g3" suffix (it only
    recognizes "-g<N>" as a generation marker for N > 39, the numeric-overflow
    escape hatch), dropping the numeral and printing a bare "Sekhmet" instead of
    "Sekhmet III". live specimen: agent:seat-af50a33e-g45 and kin."""
    now = datetime.now(UTC)
    seat = await actions.create_or_find_object("Seat", "seat:gs-succession-gen", "test")
    await actions.assert_property(seat, "handle", "Sekhmet", "test", now, 0.9)
    agent = await actions.create_or_find_object(
        "Agent", "agent:seat-gs-succession-gen-g3", "test")
    await actions.assert_property(agent, "seat_generation", "3", "test", now, 0.9)
    await actions.create_link(agent, seat, "holds", "test", now, 1.0)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    assert out["labels"][idx] == "Sekhmet III"


async def test_fetch_snapshot_nameless_agent_without_a_seat_falls_back_to_patronym_and_model(
    actions: Actions,
) -> None:
    """THE AGENT IDENTITY FIX (operator ruling, grounds 9163b1c7): no handle, no
    held Seat -- "<patronym> <ROMAN> · <model short>", never "?". Live count: this
    is the 7,618-agent osiris shape the old "Agent · <model> in <project>"
    fallback broke on, since that scheme never read `patronym` at all."""
    now = datetime.now(UTC)
    agent = await actions.create_or_find_object("Agent", "agent:gs-nameless-b-vii", "test")
    await actions.assert_property(agent, "source_model", "claude-sonnet-5", "test", now, 0.9)
    await actions.assert_property(agent, "patronym", "Sekhmet", "test", now, 0.9)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    assert out["labels"][idx] == "Sekhmet VII · sonnet-5"


async def test_fetch_snapshot_nameless_agent_generation_one_shows_a_roman_numeral(
    actions: Actions,
) -> None:
    """EVERY AGENT LABEL CARRIES A NUMERAL (Thoth mail 11326, w308 live header
    check): generation 1 used to omit the roman suffix, indistinguishable from
    the old handle-bypass -- show "I" like every other generation."""
    now = datetime.now(UTC)
    agent = await actions.create_or_find_object("Agent", "agent:gs-nameless-gen1", "test")
    await actions.assert_property(agent, "source_model", "claude-opus-5", "test", now, 0.9)
    await actions.assert_property(agent, "patronym", "Imhotep", "test", now, 0.9)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    assert out["labels"][idx] == "Imhotep I · opus-5"


async def test_fetch_snapshot_nameless_sidechain_agent_gets_the_sub_marker(
    actions: Actions,
) -> None:
    now = datetime.now(UTC)
    agent = await actions.create_or_find_object("Agent", "agent:gs-nameless-sub-iii", "test")
    await actions.assert_property(agent, "source_model", "claude-sonnet-5", "test", now, 0.9)
    await actions.assert_property(agent, "patronym", "Seshat", "test", now, 0.9)
    await actions.assert_property(agent, "is_sidechain", True, "test", now, 0.9)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    assert out["labels"][idx] == "Seshat III · sonnet-5 ⌊ sub"


async def test_fetch_snapshot_nameless_agent_without_a_patronym_falls_back_to_canonical(
    actions: Actions,
) -> None:
    """Never "?": a patronym-less, seat-less, handle-less Agent still resolves to a
    real, resolvable name -- its own canonical short id, not a raw "?"."""
    now = datetime.now(UTC)
    agent = await actions.create_or_find_object("Agent", "agent:gs-nameless-nopat", "test")
    await actions.assert_property(agent, "source_model", "claude-sonnet-5", "test", now, 0.9)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    # THE MODEL SUFFIX ON EVERY LABEL (Thoth mail 11345): folded onto the
    # canonical-stem fallback too, not just the patronym/seat branches.
    assert out["labels"][idx] == "gs-nameless-nopat · sonnet-5"
    assert "?" not in out["labels"][idx]


async def test_fetch_snapshot_agent_with_only_a_bare_handle_still_gets_the_identity_format(
    actions: Actions,
) -> None:
    """THE IDENTITY FORMAT IS ALWAYS USED FOR AGENT (Thoth mail 11317, live header
    check after w307): 258 live agents had a raw `handle` assertion (their
    lineage's own bare stamp, e.g. "Thoth") and no `patronym` -- _short_label's
    old handle-first branch used it directly as the label, no generation, no
    model, bypassing the identity resolver entirely. That raw handle must now
    only SEED the patronym slot, still producing a real "<name> <ROMAN>"."""
    now = datetime.now(UTC)
    agent = await actions.create_or_find_object(
        "Agent", "agent:gs-bare-handle-vii", "test")
    await actions.assert_property(agent, "handle", "Thoth", "test", now, 0.9)
    await actions.assert_property(agent, "source_model", "claude-sonnet-5", "test", now, 0.9)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    assert out["labels"][idx] == "Thoth VII · sonnet-5"


async def test_fetch_snapshot_agent_name_assertion_is_never_read_for_the_stem(
    actions: Actions,
) -> None:
    """STOP PATTERN-MATCHING NAME STRINGS (Thoth mail 11334, the w309
    regression): the w309 fix chased known auto-generated `name` shapes one
    regex at a time ("<model> in <project>", "<agent_type> spawn") and missed
    a third ("<model> · <description>", lineage.py:247, e.g. "claude-opus-4-8
    · Reconcile memory batch"). The `name` assertion is now never read for an
    Agent's stem at all, regardless of shape -- covers the open-ended set in
    one structural rule instead of chasing it one detector at a time."""
    now = datetime.now(UTC)
    agent = await actions.create_or_find_object("Agent", "agent:gs-name-never-read", "test")
    await actions.assert_property(
        agent, "name", "claude-opus-4-8 · Reconcile memory batches", "test", now, 0.9)
    await actions.assert_property(agent, "source_model", "claude-opus-4-8", "test", now, 0.9)
    await actions.assert_property(agent, "is_sidechain", True, "test", now, 0.9)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    label = out["labels"][idx]
    assert "Reconcile" not in label
    # no patronym and no handle either -- falls to the canonical stem, same as
    # any other totally nameless agent (test_fetch_snapshot_nameless_agent_
    # without_a_patronym_falls_back_to_canonical's own precedent), with the
    # model suffix and sub marker still folded on (Thoth mail 11345).
    assert label == "gs-name-never-read · opus-4-8 ⌊ sub"


async def test_fetch_snapshot_agent_handle_stripped_to_its_leading_alphabetic_run(
    actions: Actions,
) -> None:
    """Thoth mail 11334, the "thoth G58" specimen: a legacy/malformed `handle`
    assertion carrying trailing noise past the real name must not seed the
    stem verbatim -- only its own leading alphabetic run, the "stripped to
    letters" rule from the structural label formula. AGENT LABEL COSMETICS
    (thread 065031d1, Thoth DM 11536): that stripped stem is also DISPLAY-CASED
    now ('thoth' -> 'Thoth') -- a raw handle's own storage casing must never
    read differently from a patronym-derived stem, which is always correct
    case already."""
    now = datetime.now(UTC)
    agent = await actions.create_or_find_object("Agent", "agent:gs-handle-noise", "test")
    await actions.assert_property(agent, "handle", "thoth G58", "test", now, 0.9)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    label = out["labels"][idx]
    assert re.match(r"^Thoth [IVXLCDM]+$", label), label


async def test_fetch_snapshot_compound_patronym_never_gets_a_second_numeral(
    actions: Actions,
) -> None:
    """THE DOUBLE NUMERAL FIX (Thoth mail 11334, w309's live regression):
    `patronym_for` (lineage.py) mints a spawned child's own `patronym`
    assertion as the compound "<parent stem> <parent ROMAN>.<birth ordinal>"
    (e.g. "Thoth I.1") -- the w309 fix appended this child's OWN
    freshly-computed generation roman on top of that already-complete
    compound, producing "Thoth I.1 I". The compound's own embedded
    roman+ordinal is now read as the whole generation field; nothing is
    appended a second time."""
    now = datetime.now(UTC)
    agent = await actions.create_or_find_object("Agent", "agent:gs-compound-patronym", "test")
    await actions.assert_property(agent, "patronym", "Thoth I.1", "test", now, 0.9)
    await actions.assert_property(
        agent, "source_model", "claude-haiku-4-5-20251001", "test", now, 0.9)
    await actions.assert_property(agent, "is_sidechain", True, "test", now, 0.9)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    assert out["labels"][idx] == "Thoth I.1 · haiku-4-5-20251001 ⌊ sub"


async def test_fetch_snapshot_compound_patronym_capitalises_a_lowercase_stem(
    actions: Actions,
) -> None:
    """AGENT LABEL COSMETICS (thread 065031d1, Thoth DM 11536), the live specimen
    (2,747 agents): `patronym_for` stamps a spawned child's compound patronym with
    the PARENT's own stem verbatim, so a parent claimed under a lowercase seat
    handle ('alfred') produces a compound like 'alfred I.1' for every one of its
    children too -- one hop removed from the seat branch's own casing bug, same
    fix applies."""
    now = datetime.now(UTC)
    agent = await actions.create_or_find_object(
        "Agent", "agent:gs-compound-patronym-lowercase", "test")
    await actions.assert_property(agent, "patronym", "alfred I.1", "test", now, 0.9)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    idx = out["object_ids"].index(str(agent))
    assert out["labels"][idx] == "Alfred I.1"


async def test_fetch_snapshot_edge_weight_is_raw_flat_for_now(actions: Actions) -> None:
    """DENSITY NOT DISCS tip (h), item 8 (Seshat, mail 11016): edge_weight is RAW,
    never a pre-normalised curve -- flat 1.0 for every individual link today, since
    her own live tip has no per-edge-weight consumer yet (she log2-normalises
    client-side when she does need a curve, from a raw value this array provides)."""
    now = datetime.now(UTC)
    a = await actions.create_or_find_object("Thread", "thread:gs-ew-a", "test")
    b = await actions.create_or_find_object("Thread", "thread:gs-ew-b", "test")
    await actions.create_link(a, b, "cites", "test", now, 1.0)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    assert out["edge_count"] >= 1
    assert all(w == pytest.approx(1.0) for w in out["edge_weight"])


async def test_fetch_snapshot_type_pair_edges_covers_same_project_cross_type(
    actions: Actions,
) -> None:
    """DENSITY NOT DISCS tip (h), item 9: unlike cluster_edges (cross-project only),
    type_pair_edges includes a same-project, different-type pair."""
    now = datetime.now(UTC)
    project = await actions.create_or_find_object(
        "SoftwareProject", "repo:gs-tpe", "test")
    person = await actions.create_or_find_object("Person", "principal:gs-tpe-person", "test")
    thread_obj = await actions.create_or_find_object("Thread", "thread:gs-tpe-thread", "test")
    for oid in (person, thread_obj):
        await actions.create_link(oid, project, "in_repo", "test", now, 1.0)
    await actions.create_link(person, thread_obj, "cites", "test", now, 1.0)

    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))

    pcode = out["project_code"][out["object_ids"].index(str(person))]
    tcode_person = out["type_code"][out["object_ids"].index(str(person))]
    tcode_thread = out["type_code"][out["object_ids"].index(str(thread_obj))]
    bucket_a = {"project": pcode, "type": tcode_person}
    bucket_b = {"project": pcode, "type": tcode_thread}
    found = [
        r for r in out["type_pair_edges"]
        if {tuple(sorted(r["a"].items())), tuple(sorted(r["b"].items()))} ==
           {tuple(sorted(bucket_a.items())), tuple(sorted(bucket_b.items()))}
    ]
    assert found and found[0]["count"] >= 1
    # cluster_edges must NOT carry this same-project pair (its own cross-project rule)
    assert not any(e["a"] == e["b"] == pcode for e in out["cluster_edges"])


async def test_fetch_snapshot_aggregates_carry_every_placed_object(
    actions: Actions,
) -> None:
    """THE LEGIBILITY PASS, tip 2i: every project_aggregates entry's own count sums to
    the snapshot's total object count -- nothing dropped, nothing double-counted."""
    await actions.create_or_find_object("Thread", "thread:gs-agg", "test")
    await layout_batch(actions, limit=1000)
    out = decode_snapshot(await fetch_snapshot(actions.pool))
    assert sum(a["count"] for a in out["project_aggregates"]) == out["count"]
    assert sum(a["count"] for a in out["type_aggregates"]) == out["count"]


async def test_fetch_snapshot_cluster_edges_only_cover_cross_project_pairs(
    actions: Actions,
) -> None:
    """THE LEGIBILITY PASS, tip 2j: a real cross-project semantic edge shows up as one
    cluster_edges record naming both projects' own codes and the edge's class."""
    now = datetime.now(UTC)
    proj_a = await actions.create_or_find_object("SoftwareProject", "repo:gs-cl-a", "test")
    proj_b = await actions.create_or_find_object("SoftwareProject", "repo:gs-cl-b", "test")
    a = await actions.create_or_find_object("Thread", "thread:gs-cl-a-member", "test")
    b = await actions.create_or_find_object("Thread", "thread:gs-cl-b-member", "test")
    await actions.create_link(a, proj_a, "in_repo", "test", now, 1.0)
    await actions.create_link(b, proj_b, "in_repo", "test", now, 1.0)
    await actions.create_link(a, b, "cites", "test", now, 1.0)  # semantic, cross-project

    while await layout_batch(actions, limit=1000) > 0:
        pass

    out = decode_snapshot(await fetch_snapshot(actions.pool))
    # a's/b's own MEMBERSHIP project code (via in_repo), not proj_a's/proj_b's own
    # code as objects (a SoftwareProject carries no in_repo link of its own, so it
    # sits in the "unfiled" bucket -- a different axis from which project it names)
    pa = out["project_code"][out["object_ids"].index(str(a))]
    pb = out["project_code"][out["object_ids"].index(str(b))]
    assert out["projects"][pa] == "repo:gs-cl-a"
    assert out["projects"][pb] == "repo:gs-cl-b"
    lo, hi = (pa, pb) if pa <= pb else (pb, pa)
    matches = [e for e in out["cluster_edges"] if e["a"] == lo and e["b"] == hi]
    assert any(e["class"] == "semantic" and e["count"] >= 1 for e in matches)


# --- DB-backed: deltas_since ------------------------------------------------------------


async def test_deltas_since_reports_a_newly_created_object(actions: Actions) -> None:
    deltas, cursor0 = await deltas_since(actions.pool, 0)
    oid = await actions.create_or_find_object("Thread", "thread:gs-delta", "test")

    deltas, cursor1 = await deltas_since(actions.pool, cursor0)
    assert cursor1 > cursor0
    assert any(d["id"] == str(oid) for d in deltas)


async def test_deltas_since_coalesces_to_the_latest_event_per_object_and_covers_the_rest(
    actions: Actions,
) -> None:
    """THE DELTA CURSOR FIX (thread fa3a4d42): two events for the SAME object within
    one page collapse into ONE delta (a live-state view only cares about the current
    value), and the cursor advances past BOTH -- a later call must not re-surface the
    coalesced-away first event."""
    oid = await actions.create_or_find_object("Thread", "thread:gs-coalesce", "test")
    _, cursor = await deltas_since(actions.pool, 0)
    await actions.assert_property(oid, "graph_x", 1.0, "test", datetime.now(UTC), 1.0)
    await actions.assert_property(oid, "graph_x", 2.0, "test", datetime.now(UTC), 1.0)

    deltas, cursor2 = await deltas_since(actions.pool, cursor)
    matches = [d for d in deltas if d["id"] == str(oid)]
    assert len(matches) == 1

    deltas3, cursor3 = await deltas_since(actions.pool, cursor2)
    assert deltas3 == []
    assert cursor3 == cursor2


async def test_deltas_since_is_empty_when_nothing_changed(actions: Actions) -> None:
    """Never assumes the hermetic test DB's own outbox backlog fits in one page --
    the paging fix (thread fa3a4d42) means a real backlog beyond `limit` is expected
    to take several calls to drain, not one."""
    await actions.create_or_find_object("Thread", "thread:gs-quiet", "test")
    cursor = 0
    for _ in range(200):
        deltas, cursor = await deltas_since(actions.pool, cursor)
        if not deltas:
            break
    else:
        raise AssertionError("deltas_since never drained to quiescence")
    deltas2, cursor2 = await deltas_since(actions.pool, cursor)
    assert deltas2 == []
    assert cursor2 == cursor


# --- REST: GET /graph/stream (single response, safe to exercise via the test client) --


async def test_graph_stream_endpoint_returns_decodable_bytes(
    client: httpx.AsyncClient, actions: Actions,
) -> None:
    oid = await actions.create_or_find_object("Thread", "thread:gs-http", "test")
    await layout_batch(actions, limit=1000)

    r = await client.get("/graph/stream")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    out = decode_snapshot(r.content)
    assert str(oid) in out["object_ids"]


# --- resolve_deltas_start_cursor: the fix's own decision, pulled out for direct testing -

# thread fa3a4d42 -- a fresh connection never starts from cursor 0 any more.


async def test_resolve_cursor_prefers_since_over_everything(actions: Actions) -> None:
    await actions.create_or_find_object("Thread", "thread:gs-cursor-since", "test")
    cursor = await resolve_deltas_start_cursor(
        actions.pool, since=42, last_event_id="999")
    assert cursor == 42


async def test_resolve_cursor_falls_back_to_last_event_id(actions: Actions) -> None:
    cursor = await resolve_deltas_start_cursor(
        actions.pool, since=None, last_event_id="17")
    assert cursor == 17


async def test_resolve_cursor_defaults_to_the_live_watermark_never_zero(
    actions: Actions,
) -> None:
    """The exact regression this fix closes: no `since`, no Last-Event-ID must resolve
    to the CURRENT outbox tip, never cursor 0 (a full backlog replay)."""
    await actions.create_or_find_object("Thread", "thread:gs-cursor-default", "test")
    cursor = await resolve_deltas_start_cursor(
        actions.pool, since=None, last_event_id=None)
    assert cursor == await outbox_watermark(actions.pool)
    assert cursor > 0


async def test_resolve_cursor_falls_back_to_watermark_on_a_malformed_last_event_id(
    actions: Actions,
) -> None:
    await actions.create_or_find_object("Thread", "thread:gs-cursor-malformed", "test")
    cursor = await resolve_deltas_start_cursor(
        actions.pool, since=None, last_event_id="not-a-number")
    assert cursor == await outbox_watermark(actions.pool)
