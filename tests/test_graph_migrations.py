"""DRAWING THE WHOLE GRAPH, THE MIGRATIONS (thread 325ef660): hermetic coverage for
the three name-dispatched graph-shape repairs in graph_migrations.py."""
from __future__ import annotations

from datetime import UTC, datetime

from src.actions.core import Actions
from src.orchestrator.graph_migrations import (
    migrate_assertion_links,
    migrate_file_the_unfiled,
    migrate_repo_seats_fix,
    run_migration,
)


async def test_run_migration_unknown_target_reports_valid_targets(actions: Actions) -> None:
    out = await run_migration(actions.pool, "nope", actor="test")
    assert "error" in out
    assert "repo_seats_fix" in out["valid_targets"]


# --- repo_seats_fix ----------------------------------------------------------------


async def test_repo_seats_fix_requires_because_to_apply(actions: Actions) -> None:
    out = await migrate_repo_seats_fix(actions, actor="test", dry_run=False, because="")
    assert "error" in out


async def test_repo_seats_fix_no_repo_seats_object_is_a_clean_noop(actions: Actions) -> None:
    out = await migrate_repo_seats_fix(actions, actor="test", dry_run=True)
    assert out["already_clean"] is True


async def test_repo_seats_fix_refiles_agents_and_edges_then_retires_repo_seats(
    actions: Actions,
) -> None:
    now = datetime.now(UTC)
    osiris = await actions.create_or_find_object("SoftwareProject", "repo:osiris", "test")
    seats = await actions.create_or_find_object("SoftwareProject", "repo:seats", "test")
    agent = await actions.create_or_find_object("Agent", "agent:gm-seats-agent", "test")
    await actions.assert_property(agent, "project", "seats", "test", now, 0.9)
    await actions.create_link(agent, seats, "works_in", "test", now, 1.0)
    thread = await actions.create_or_find_object("Thread", "thread:gm-seats-thread", "test")
    await actions.create_link(thread, seats, "in_repo", "test", now, 1.0)

    dry = await migrate_repo_seats_fix(actions, actor="test", dry_run=True)
    assert dry["agents_scanned"] == 1
    assert dry["edges_scanned"] == 2
    # a dry run touches nothing
    still_seats = await actions.pool.fetchval(
        "SELECT value #>> '{}' FROM current_assertions WHERE object_id=$1 AND name='project'",
        agent)
    assert still_seats == "seats"

    out = await migrate_repo_seats_fix(
        actions, actor="test", dry_run=False, because="test cleanup")
    assert out["agents_scanned"] == 1
    assert out["edges_scanned"] == 2
    assert out["osiris_seats_edges_after"] == 0
    assert out["retired"] == "repo:seats"

    new_project = await actions.pool.fetchval(
        "SELECT value #>> '{}' FROM current_assertions WHERE object_id=$1 AND name='project'",
        agent)
    assert new_project == "osiris"
    now2 = datetime.now(UTC)
    assert await actions.pool.fetchval(
        "SELECT 1 FROM links WHERE from_id=$1 AND to_id=$2 AND type='works_in' "
        "AND (valid_until IS NULL OR valid_until > $3)", agent, osiris, now2)
    assert await actions.pool.fetchval(
        "SELECT 1 FROM links WHERE from_id=$1 AND to_id=$2 AND type='in_repo' "
        "AND (valid_until IS NULL OR valid_until > $3)", thread, osiris, now2)
    seats_status = await actions.pool.fetchval(
        "SELECT status FROM objects WHERE canonical='repo:seats'")
    assert seats_status == "retired"

    # idempotent: a repeat call finds nothing left to touch (repo:seats is retired,
    # not active, so its own edges are no longer scanned; no agent still reads "seats")
    again = await migrate_repo_seats_fix(actions, actor="test", dry_run=True)
    assert again["agents_scanned"] == 0
    assert again["edges_scanned"] == 0


# --- file_the_unfiled --------------------------------------------------------------


async def test_file_the_unfiled_majority_vote_files_the_object(actions: Actions) -> None:
    now = datetime.now(UTC)
    proj = await actions.create_or_find_object("SoftwareProject", "repo:gm-fileme", "test")
    filed_member = await actions.create_or_find_object("Thread", "thread:gm-filed", "test")
    await actions.create_link(filed_member, proj, "in_repo", "test", now, 1.0)
    unfiled = await actions.create_or_find_object("Thread", "thread:gm-unfiled", "test")
    await actions.create_link(unfiled, filed_member, "cites", "test", now, 1.0)

    dry = await migrate_file_the_unfiled(actions, actor="test", dry_run=True)
    assert any(p["object"] == str(unfiled)[:8] for p in dry["plan"])

    out = await migrate_file_the_unfiled(
        actions, actor="test", dry_run=False, because="test cleanup")
    assert out["filed"] >= 1
    project_name = await actions.pool.fetchval(
        "SELECT value #>> '{}' FROM current_assertions WHERE object_id=$1 AND name='project'",
        unfiled)
    assert project_name == "gm-fileme"


async def test_file_the_unfiled_tie_stays_unfiled_and_is_counted(actions: Actions) -> None:
    now = datetime.now(UTC)
    proj_a = await actions.create_or_find_object("SoftwareProject", "repo:gm-tie-a", "test")
    proj_b = await actions.create_or_find_object("SoftwareProject", "repo:gm-tie-b", "test")
    unfiled = await actions.create_or_find_object("Thread", "thread:gm-tie-unfiled", "test")
    await actions.create_link(unfiled, proj_a, "cites", "test", now, 1.0)
    await actions.create_link(unfiled, proj_b, "cites", "test", now, 1.0)

    out = await migrate_file_the_unfiled(actions, actor="test", dry_run=True)
    tie_entries = [t for t in out["ties_plan"] if t["object"] == str(unfiled)[:8]]
    assert len(tie_entries) == 1
    assert out["ties"] >= 1


async def test_file_the_unfiled_no_neighbour_project_stays_unfiled(actions: Actions) -> None:
    unfiled = await actions.create_or_find_object("Thread", "thread:gm-lonely", "test")
    out = await migrate_file_the_unfiled(actions, actor="test", dry_run=True)
    assert str(unfiled)[:8] not in [p["object"] for p in out["plan"]]
    assert out["still_unfiled"] >= 1


# --- assertion_links -----------------------------------------------------------------


async def test_assertion_links_requires_because_to_apply(actions: Actions) -> None:
    out = await migrate_assertion_links(actions, actor="test", dry_run=False, because="  ")
    assert "error" in out


async def test_assertion_links_recorded_by_resolves_a_real_agent_source(
    actions: Actions,
) -> None:
    now = datetime.now(UTC)
    agent = await actions.create_or_find_object("Agent", "agent:gm-recorder", "test")
    decision = await actions.create_or_find_object("Decision", "decision:gm-recorded", "test")
    await actions.assert_property(
        decision, "summary", "a real decision", "agent:gm-recorder", now, 0.9)

    dry = await migrate_assertion_links(actions, actor="test", dry_run=True)
    assert dry["receipt"]["recorded_by"]["minted"] >= 1

    out = await migrate_assertion_links(
        actions, actor="test", dry_run=False, because="test cleanup")
    assert out["receipt"]["recorded_by"]["minted"] >= 1
    assert await actions.pool.fetchval(
        "SELECT 1 FROM links WHERE from_id=$1 AND to_id=$2 AND type='recorded_by' "
        "AND (valid_until IS NULL OR valid_until > now())", decision, agent)

    # idempotent: a repeat call finds it already present, mints nothing new for this pair
    again = await migrate_assertion_links(actions, actor="test", dry_run=True)
    assert again["receipt"]["recorded_by"]["already_present"] >= 1


async def test_assertion_links_supersedes_mints_one_edge_from_either_property(
    actions: Actions,
) -> None:
    now = datetime.now(UTC)
    newer = await actions.create_or_find_object("Decision", "decision:gm-newer", "test")
    older = await actions.create_or_find_object("Decision", "decision:gm-older", "test")
    await actions.assert_property(
        newer, "supersedes", "decision:gm-older", "test", now, 0.9)

    out = await migrate_assertion_links(
        actions, actor="test", dry_run=False, because="test cleanup")
    assert out["receipt"]["supersedes"]["minted"] >= 1
    assert await actions.pool.fetchval(
        "SELECT 1 FROM links WHERE from_id=$1 AND to_id=$2 AND type='supersedes' "
        "AND (valid_until IS NULL OR valid_until > now())", newer, older)
    # the OTHER direction was never minted
    assert not await actions.pool.fetchval(
        "SELECT 1 FROM links WHERE from_id=$1 AND to_id=$2 AND type='supersedes' "
        "AND (valid_until IS NULL OR valid_until > now())", older, newer)


async def test_assertion_links_closed_by_skips_when_already_present(actions: Actions) -> None:
    now = datetime.now(UTC)
    thread = await actions.create_or_find_object("Thread", "thread:gm-closed", "test")
    closer = await actions.create_or_find_object("Agent", "agent:gm-closer", "test")
    await actions.assert_property(thread, "resolved_in", "agent:gm-closer", "test", now, 0.9)
    await actions.create_link(thread, closer, "closed_by", "test", now, 1.0)

    out = await migrate_assertion_links(actions, actor="test", dry_run=True)
    assert out["receipt"]["closed_by"]["already_present"] >= 1
    assert out["receipt"]["closed_by"]["minted"] == 0


async def test_assertion_links_vendor_of_resolves_against_a_real_software_project(
    actions: Actions,
) -> None:
    now = datetime.now(UTC)
    vendor_proj = await actions.create_or_find_object(
        "SoftwareProject", "repo:gm-vendor-proj", "test")
    ref = await actions.create_or_find_object("Reference", "ref:gm-vendored", "test")
    await actions.assert_property(ref, "vendor", "gm-vendor-proj", "test", now, 0.9)

    out = await migrate_assertion_links(
        actions, actor="test", dry_run=False, because="test cleanup")
    assert out["receipt"]["vendor_of"]["minted"] >= 1
    assert await actions.pool.fetchval(
        "SELECT 1 FROM links WHERE from_id=$1 AND to_id=$2 AND type='vendor_of' "
        "AND (valid_until IS NULL OR valid_until > now())", vendor_proj, ref)


async def test_assertion_links_unresolvable_source_is_skipped_not_guessed(
    actions: Actions,
) -> None:
    now = datetime.now(UTC)
    thread = await actions.create_or_find_object("Thread", "thread:gm-ghost-owner", "test")
    await actions.assert_property(
        thread, "owner", "seat:gm-no-such-seat", "test", now, 0.9)

    out = await migrate_assertion_links(actions, actor="test", dry_run=True)
    assert out["receipt"]["owned_by"]["skipped_unresolvable"] >= 1
