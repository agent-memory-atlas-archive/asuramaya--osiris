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
    decode_snapshot,
    deltas_since,
    encode_snapshot,
    fetch_snapshot,
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
        edge_types=["cites", "in_repo"],
    )
    out = decode_snapshot(data)
    assert out["count"] == 3
    assert out["edge_count"] == 2
    assert out["object_ids"] == ["a", "b", "c"]
    assert out["types"] == ["Thread", "Decision"]
    assert out["projects"] == ["repo:x", "repo:y"]
    assert out["edge_types"] == ["cites", "in_repo"]
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
        types=["Thread"], projects=["unfiled"], edge_types=[],
    )
    out = decode_snapshot(data)
    assert out["count"] == 1
    assert out["edge_count"] == 0
    assert out["edge_src"] == []


def test_encode_snapshot_rejects_a_mismatched_node_column_length() -> None:
    with pytest.raises(ValueError, match="x has"):
        encode_snapshot(
            object_ids=["a", "b"], x=[1.0], y=[1.0, 2.0], type_code=[0, 0],
            project_code=[0, 0], weight=[0.0, 0.0], status_flag=[0, 0],
            edge_src=[], edge_dst=[], edge_type_code=[], types=[], projects=[],
            edge_types=[],
        )


def test_encode_snapshot_rejects_a_mismatched_edge_column_length() -> None:
    with pytest.raises(ValueError, match="edge_dst has"):
        encode_snapshot(
            object_ids=["a"], x=[1.0], y=[1.0], type_code=[0], project_code=[0],
            weight=[0.0], status_flag=[0], edge_src=[0, 0], edge_dst=[0],
            edge_type_code=[0, 0], types=[], projects=[], edge_types=[],
        )


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


# --- DB-backed: deltas_since ------------------------------------------------------------


async def test_deltas_since_reports_a_newly_created_object(actions: Actions) -> None:
    deltas, cursor0 = await deltas_since(actions.pool, 0)
    oid = await actions.create_or_find_object("Thread", "thread:gs-delta", "test")

    deltas, cursor1 = await deltas_since(actions.pool, cursor0)
    assert cursor1 > cursor0
    assert any(d["id"] == str(oid) for d in deltas)


async def test_deltas_since_is_empty_when_nothing_changed(actions: Actions) -> None:
    await actions.create_or_find_object("Thread", "thread:gs-quiet", "test")
    _, cursor = await deltas_since(actions.pool, 0)
    deltas, cursor2 = await deltas_since(actions.pool, cursor)
    assert deltas == []
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
