"""NAVIGABLE SPACE, THE SERVER piece B (rulings f832c3a4 + 0a3d6719, thread
b6cb1d7c0b36): the whole-graph typed-array snapshot and its outbox-backed delta poll.
Shape frozen by DM with Seshat (mail 10439/10449/10451) before this was written."""
from __future__ import annotations

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
        types=["Thread", "Decision"], projects=["repo:x", "repo:y"],
        edge_types=["cites", "in_repo"], link_type_class=["semantic", "structural"],
        labels=["Thread abc", "Decision def", "Thread ghi"],
        project_aggregates=[{"project": 0, "count": 2, "cx": 1.5, "cy": 4.5, "radius": 1.0}],
        cluster_edges=[{"a": 0, "b": 1, "class": "semantic", "count": 1}],
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
    assert out["x"] == pytest.approx([1.0, 2.0, 3.0])
    assert out["y"] == pytest.approx([4.0, 5.0, 6.0])
    assert out["type_code"] == [0, 1, 0]
    assert out["project_code"] == [0, 0, 1]
    assert out["weight"] == pytest.approx([2.0, 0.0, 5.0])
    assert out["status_flag"] == [0, 2, 0]
    assert out["edge_src"] == [0, 1]
    assert out["edge_dst"] == [1, 2]
    assert out["edge_type_code"] == [0, 1]


def test_snapshot_with_no_edges_still_round_trips() -> None:
    data = encode_snapshot(
        object_ids=["only"], x=[0.0], y=[0.0], type_code=[0], project_code=[0],
        weight=[0.0], status_flag=[0], edge_src=[], edge_dst=[], edge_type_code=[],
        types=["Thread"], projects=["unfiled"], edge_types=[], link_type_class=[],
        labels=["Thread only"],
    )
    out = decode_snapshot(data)
    assert out["count"] == 1
    assert out["edge_count"] == 0
    assert out["edge_src"] == []
    assert out["project_aggregates"] == []
    assert out["type_aggregates"] == []
    assert out["cluster_edges"] == []


def test_encode_snapshot_rejects_a_mismatched_node_column_length() -> None:
    with pytest.raises(ValueError, match="x has"):
        encode_snapshot(
            object_ids=["a", "b"], x=[1.0], y=[1.0, 2.0], type_code=[0, 0],
            project_code=[0, 0], weight=[0.0, 0.0], status_flag=[0, 0],
            edge_src=[], edge_dst=[], edge_type_code=[], types=[], projects=[],
            edge_types=[], link_type_class=[], labels=["a", "b"],
        )


def test_encode_snapshot_rejects_a_mismatched_edge_column_length() -> None:
    with pytest.raises(ValueError, match="edge_dst has"):
        encode_snapshot(
            object_ids=["a"], x=[1.0], y=[1.0], type_code=[0], project_code=[0],
            weight=[0.0], status_flag=[0], edge_src=[0, 0], edge_dst=[0],
            edge_type_code=[0, 0], types=[], projects=[], edge_types=[],
            link_type_class=[], labels=["a"],
        )


def test_encode_snapshot_rejects_a_mismatched_labels_length() -> None:
    with pytest.raises(ValueError, match="labels has"):
        encode_snapshot(
            object_ids=["a", "b"], x=[1.0, 2.0], y=[1.0, 2.0], type_code=[0, 0],
            project_code=[0, 0], weight=[0.0, 0.0], status_flag=[0, 0],
            edge_src=[], edge_dst=[], edge_type_code=[], types=[], projects=[],
            edge_types=[], link_type_class=[], labels=["only-one"],
        )


# --- _short_label: THE LEGIBILITY PASS, tip 2g ----------------------------------------


def test_short_label_agent_uses_handle_never_the_id() -> None:
    assert _short_label("Agent", "agent:deadbeef-g1", "Khnum", None) == "Khnum"


def test_short_label_software_project_strips_the_repo_scheme() -> None:
    assert _short_label("SoftwareProject", "repo:osiris", None, None) == "osiris"


def test_short_label_person_uses_name() -> None:
    assert _short_label("Person", "principal:xyz", None, "Ada Lovelace") == "Ada Lovelace"


def test_short_label_falls_back_to_type_plus_canonical() -> None:
    assert _short_label("Thread", "thread:abc123", None, None) == "Thread thread:abc123"


def test_short_label_agent_without_a_handle_falls_back() -> None:
    assert _short_label("Agent", "agent:deadbeef-g1", None, None) == (
        "Agent agent:deadbeef-g1")


def test_short_label_hard_truncates_at_40_chars_with_an_ellipsis() -> None:
    long_canonical = "thread:" + "x" * 60
    label = _short_label("Thread", long_canonical, None, None)
    assert len(label) == 40
    assert label.endswith("…")


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
