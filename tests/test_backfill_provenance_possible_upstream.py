"""PROVENANCE BACKFILL (thread e332177f, wave 24 dispatch) — back-stamping
`possible_upstream` onto historical Decision/Thread writes by finding each write's own
receipt in its writer's transcript and reusing piece 2's own tool_result scan. Hermetic:
synthetic JSONL files under tmp_path (never the live fleet's own transcript root), real
Postgres for the graph reads/writes."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.actions.core import Actions
from src.orchestrator.provenance_backfill import backfill_possible_upstream


def _line(kind: str, content: Any, **extra: Any) -> str:
    d: dict[str, Any] = {"type": kind, "message": {"content": content}}
    d.update(extra)
    return json.dumps(d)


def _tool_result(content: Any) -> str:
    return _line("user", [{"type": "tool_result", "content": content}])


async def _mint_agent_with_sid(
    actions: Actions, agent_canon: str, sid: str, tmp_path: Path, lines: list[str],
) -> Path:
    now = datetime.now(UTC)
    obj = await actions.create_or_find_object("Agent", agent_canon, "test")
    await actions.assert_property(
        obj, f"anchor_sid:{sid[:8]}", sid, "test", now, 0.9,
        evidence_class="direct_observation")
    proj_dir = tmp_path / "-home-someone-code-testrepo"
    proj_dir.mkdir(parents=True, exist_ok=True)
    transcript = proj_dir / f"{sid}.jsonl"
    transcript.write_text("\n".join(lines) + "\n")
    return transcript


async def _links_from(actions: Actions, from_id: Any) -> list[dict[str, Any]]:
    rows = await actions.pool.fetch(
        "SELECT to_id, source_id, properties FROM links WHERE from_id=$1 "
        "AND type='possible_upstream'", from_id)
    return [dict(r) for r in rows]


async def test_dry_run_finds_the_writes_own_receipt_and_plans_an_edge(
    actions: Actions, tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    upstream = await actions.create_or_find_object("Decision", "decision:aaa111bbb222", "x")
    lines = [
        _tool_result(json.dumps({"canonical": "decision:aaa111bbb222", "id": "1"})),
        _tool_result(json.dumps({"canonical": "decision:ccc333ddd444"})),
    ]
    await _mint_agent_with_sid(actions, "agent:writer-a", "sidaaa11deadbeef", tmp_path, lines)
    decision = await actions.create_or_find_object(
        "Decision", "decision:ccc333ddd444", "agent:writer-a")
    await actions.assert_property(
        decision, "summary", "a decision with a real upstream read behind it",
        "agent:writer-a", now, 0.9)

    report = await backfill_possible_upstream(
        actions, dry_run=True, transcript_root=tmp_path)

    assert report["dry_run"] is True
    assert report["edges_to_mint"] == 1
    entry = report["plan"][0]
    assert entry["from"] == "decision:ccc333ddd444"
    assert entry["to"] == str(upstream)
    assert entry["door"].startswith("backfill:")
    # dry run writes nothing
    assert await _links_from(actions, decision) == []


async def test_live_mints_the_edge_sourced_to_the_original_writer_and_is_idempotent(
    actions: Actions, tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    upstream = await actions.create_or_find_object("Decision", "decision:eee555fff666", "x")
    lines = [
        _tool_result(json.dumps({"canonical": "decision:eee555fff666"})),
        _tool_result(json.dumps({"canonical": "decision:ggg777hhh888"})),
    ]
    await _mint_agent_with_sid(actions, "agent:writer-b", "sidbbb22deadbeef", tmp_path, lines)
    decision = await actions.create_or_find_object(
        "Decision", "decision:ggg777hhh888", "agent:writer-b")
    await actions.assert_property(
        decision, "summary", "another decision with a real upstream read",
        "agent:writer-b", now, 0.9)

    report = await backfill_possible_upstream(
        actions, dry_run=False, because="thread e332177f, testing the live path",
        transcript_root=tmp_path)
    assert report["minted"] == 1
    edges = await _links_from(actions, decision)
    assert len(edges) == 1
    assert edges[0]["to_id"] == upstream
    assert edges[0]["source_id"] == "agent:writer-b"  # the ORIGINAL writer, never a miner

    # idempotent: the candidate now carries an edge, so a second pass finds nothing to do
    again = await backfill_possible_upstream(
        actions, dry_run=False, because="second pass", transcript_root=tmp_path)
    assert again["edges_to_mint"] == 0
    assert len(await _links_from(actions, decision)) == 1


async def test_live_refuses_a_blank_because(actions: Actions) -> None:
    report = await backfill_possible_upstream(actions, dry_run=False, because="   ")
    assert "error" in report
    assert "because" in report["error"]


async def test_writer_with_no_anchor_sid_ledger_is_reported_skipped_not_silently_dropped(
    actions: Actions, tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    decision = await actions.create_or_find_object(
        "Decision", "decision:iii999jjj000", "agent:ledgerless-writer")
    await actions.assert_property(
        decision, "summary", "a decision from a writer with no ledger at all",
        "agent:ledgerless-writer", now, 0.9)

    report = await backfill_possible_upstream(
        actions, dry_run=True, transcript_root=tmp_path)

    assert report["edges_to_mint"] == 0
    assert report["skipped_count"] == 1
    assert report["skipped"][0]["object"] == "decision:iii999jjj000"
    assert "anchor_sid" in report["skipped"][0]["reason"]
