"""POOL-STARVATION DEADLOCK (osiris-mcp wedge, 2026-09-29).

Measured live: all 8 osiris-mcp connections busy, 5 idle-in-transaction on
`pg_advisory_xact_lock(hashtext('mint:agent:...'))` (five different lineages), the other 3
churning through lock_timeout waits. Each holder was a mint_lock body (live_succession,
driven by the statusline /heartbeat burst after a reboot; register_agent at mount;
record_bridge_anchor at automount) that, while holding its locked connection, re-entered
`actions.pool` for a SECOND connection. N holders >= pool size, each waiting for one more
slot: every tool call on the server hung forever. record_decision had the same shape one
level down: its atomic() body held a transaction (and assert_property's advisory xact
locks) while its grounds/commit checks read through `a.pool`.

These tests fan each shape out past a deliberately tiny pool (max_size=2). On the old code
every one of them parks forever (bounded here by asyncio.wait_for, so it fails instead of
hanging the suite); fixed, the locked bodies run on their own lock's connection and the
fan-out never needs more than one slot per holder.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from src.actions.core import Actions
from src.db.pool import PinnedPool, PoolStarved, create_pool, pin
from src.orchestrator.agents import mint_lock, register_agent, resolve_identity
from src.orchestrator.capture import record_decision
from src.orchestrator.handshake import record_bridge_anchor
from src.orchestrator.seats import ensure_seat

_DEADLINE_S = 20.0  # the fixed paths finish in well under a second; old code never does


@pytest.fixture
async def small(actions: Actions, pg_dsn: str) -> AsyncIterator[Actions]:
    """An Actions over a max_size=2 pool against the same freshly reset database the
    `actions` fixture just prepared (depending on it is what runs the reset)."""
    pool = await create_pool(pg_dsn, min_size=1, max_size=2, acquire_timeout=_DEADLINE_S)
    try:
        yield Actions(pool)
    finally:
        await pool.close()


async def _bounded(*aws: Awaitable[Any]) -> list[Any]:
    try:
        return await asyncio.wait_for(asyncio.gather(*aws), timeout=_DEADLINE_S)
    except (TimeoutError, PoolStarved) as exc:
        pytest.fail(f"pool-starvation deadlock: the fan-out never finished ({exc!r})")


async def test_gathered_bridge_anchors_do_not_starve_a_small_pool(small: Actions) -> None:
    """record_bridge_anchor's mint_lock body read and wrote through actions.pool: two
    concurrent holders on a two-slot pool each waited for a third slot, forever."""
    now = datetime.now(UTC)
    agents = [f"agent:starve{i:02d}" for i in range(4)]
    for a in agents:
        await small.create_or_find_object("Agent", a, a)
    out = await _bounded(*(
        record_bridge_anchor(small, agent_id=a, bridge_session_id=f"bridge-{a}", actor=a)
        for a in agents))
    assert out == [True] * 4
    rows = await small.pool.fetchval(
        "SELECT count(*) FROM current_assertions WHERE name='bridge_session_id'")
    assert rows == 4
    assert now  # the writes committed when each lock released


async def test_gathered_ensure_seats_do_not_starve_a_small_pool(small: Actions) -> None:
    out = await _bounded(*(
        ensure_seat(small, house="starve", handle=f"seat{i}", source="test")
        for i in range(4)))
    assert all(o["minted"] for o in out)
    assert len({o["seat_id"] for o in out}) == 4


async def test_gathered_register_agents_do_not_starve_a_small_pool(
    small: Actions, tmp_path: Any,
) -> None:
    """register_agent (mount's path) holds mint_lock across its lineage walk and mint."""
    idents = []
    for i in range(4):
        cwd = tmp_path / f"p{i}"
        cwd.mkdir()
        idents.append(resolve_identity(cwd=str(cwd), session=f"starve{i:02d}x",
                                       project_label=f"starveproj{i}"))
    out = await _bounded(*(register_agent(small, ident, actor="test") for ident in idents))
    assert len(set(out)) == 4


async def test_gathered_record_decisions_with_grounds_do_not_starve_a_small_pool(
    small: Actions,
) -> None:
    """record_decision's atomic() body held its transaction (and assert_property's advisory
    xact locks) while its grounds check read through `a.pool`, a second slot."""
    ground = await small.create_or_find_object("Decision", "decision:starve-ground", "test")
    out = await _bounded(*(
        record_decision(small, f"starvation fan-out decision number {i}", grounds=[ground],
                        source="agent:starve", unlinked_because="test fixture")
        for i in range(4)))
    assert len(set(out)) == 4


async def test_mint_lock_body_never_draws_a_second_slot(small: Actions) -> None:
    """The structural property itself: inside the lock, the yielded pool IS the lock's
    connection, and an Actions built on it (or a bound one's .pool) stays there."""
    async with mint_lock(small.pool, "agent:starve-pin") as locked:
        assert isinstance(locked, PinnedPool)
        async with locked.acquire() as c1:
            pid = await c1.fetchval("SELECT pg_backend_pid()")
        assert await locked.fetchval("SELECT pg_backend_pid()") == pid
        assert await Actions(locked).pool.fetchval("SELECT pg_backend_pid()") == pid
        async with Actions(locked).atomic() as bound:
            assert await bound.pool.fetchval("SELECT pg_backend_pid()") == pid


async def test_pinned_call_failure_does_not_poison_the_locked_transaction(
    small: Actions,
) -> None:
    """Each pinned call runs in its own savepoint: a best-effort read that fails inside a
    lock body (swallowed by the caller, as several bodies do) must not abort the lock's
    transaction for everything after it, the per-call isolation the pool used to give."""
    async with mint_lock(small.pool, "agent:starve-savepoint") as locked:
        with pytest.raises(asyncpg.PostgresError):
            await locked.fetchval("SELECT 1/0")
        assert await locked.fetchval("SELECT 41 + 1") == 42


async def test_pin_is_idempotent_on_the_same_connection(small: Actions) -> None:
    async with small.pool.acquire() as conn:
        p = pin(small.pool, conn)
        assert pin(p, conn) is p


async def test_starved_acquire_fails_loud_and_named(pg_dsn: str) -> None:
    """Defense in depth: with every slot held, an acquire (explicit or the implicit one
    behind pool.fetchval) raises PoolStarved within the bound instead of parking forever."""
    pool = await create_pool(pg_dsn, min_size=1, max_size=1, acquire_timeout=0.3)
    try:
        async with pool.acquire():
            with pytest.raises(PoolStarved, match="max_size=1"):
                async with pool.acquire():
                    pass
            with pytest.raises(PoolStarved):
                await pool.fetchval("SELECT 1")
        assert await pool.fetchval("SELECT 1") == 1  # released: healthy again
    finally:
        await pool.close()


async def test_pool_without_a_bound_keeps_the_explicit_timeout(pg_dsn: str) -> None:
    """No acquire_timeout = asyncpg's own behavior; an explicit per-call timeout still
    applies and still surfaces as the named error."""
    pool = await create_pool(pg_dsn, min_size=1, max_size=1)
    try:
        async with pool.acquire():
            with pytest.raises(PoolStarved):
                async with pool.acquire(timeout=0.2):
                    pass
    finally:
        await pool.close()


async def test_watchdog_dump_names_a_parked_coroutine() -> None:
    """The watchdog's dump now includes asyncio task stacks: a coroutine parked on an
    await shows up by name and frame, which the thread-only dump never did."""
    from src.mcp_server import _format_asyncio_task_stacks

    gate = asyncio.Event()

    async def _parked_in_a_second_acquire_for_test() -> None:
        await gate.wait()

    task = asyncio.create_task(_parked_in_a_second_acquire_for_test(),
                               name=f"starve-{uuid.uuid4().hex[:6]}")
    await asyncio.sleep(0)
    try:
        dump = _format_asyncio_task_stacks()
        assert task.get_name() in dump
        assert "_parked_in_a_second_acquire_for_test" in dump
    finally:
        gate.set()
        await task
