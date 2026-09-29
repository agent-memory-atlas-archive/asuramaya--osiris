"""THE BACKGROUND ENCRYPTION PASS AND ITS PROGRESS RECORD: the worker's own resumable,
throttled version of the one-time "encrypt every row written before the key existed"
migration, so nobody has to run a script for it.

`soul_store.encrypt_existing_soul_lines` stays the manual door (a dry-run census and a
whole-table pass in one call). This module is what runs by itself: `encrypt_tick` does a
bounded slice of the same work per call and writes a small JSON progress record after
every batch, so a restart resumes from the saved cursor and anything that wants the
state (the status route, the readiness stepper) reads one tiny file instead of counting
the table.

WHY A FILE, NOT A TABLE: the readiness reader and the worker are separate processes on
one box, the record is a handful of scalars, and the sibling receipts this house already
keeps (restore drills, offload runs) are the same shape and location. A missing or
unreadable record reads as "no pass has started", never as an error.

HOW IT FINDS PLAINTEXT: a Fernet token always begins with the same five bytes
(`soul_crypto.FERNET_TOKEN_PREFIX`), so plaintext rows are selected in SQL with a
five-byte prefix check, never by decrypting every row. That is also the safe direction
for this job: a row that already carries the prefix is never touched, even if it belongs
to a different key, so this pass can never encrypt a token a second time.

THROTTLING: each tick has a wall-clock budget, batches are small, and after every batch
the pass sleeps for as long as the batch took (an at-most-half duty cycle), so live
ingest sharing the same database is never starved."""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
from cryptography.fernet import MultiFernet

from src.ingest.soul_crypto import FERNET_TOKEN_PREFIX, is_encrypted

_PROGRESS_ENV = "OSIRIS_SOUL_ENCRYPT_PROGRESS_FILE"
_DEFAULT_PROGRESS_FILE = "~/.local/state/osiris/soul_encrypt_progress.json"

TICK_BUDGET_SECS = 20.0      # wall clock one tick may spend before it yields
BATCH_SIZE = 500             # rows per UPDATE batch; small so a batch is a short lock
COLD_BATCH_SIZE = 10         # cold-tier rows are whole compressed sessions, far larger
REPROBE_SECS = 6 * 3600.0    # how often a finished pass double-checks for stragglers
EXACT_COUNT_BELOW = 100_000  # tables smaller than this are counted exactly; larger ones sampled
SAMPLE_PERCENT = 0.5         # share of the table's pages read to estimate the plaintext share

States = ("no_key", "pending", "running", "complete", "error")


def _progress_path() -> Path:
    env = os.environ.get(_PROGRESS_ENV)
    return Path(env).expanduser() if env else Path(_DEFAULT_PROGRESS_FILE).expanduser()


def read_progress() -> dict[str, Any]:
    """The saved record, or {} when none exists or it cannot be read. Never writes."""
    try:
        return dict(json.loads(_progress_path().read_text()))
    except (OSError, ValueError):
        return {}


def _write_progress(record: dict[str, Any]) -> None:
    """Atomic replace, so a reader never sees a half-written file."""
    path = _progress_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2))
    tmp.replace(path)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def shape_encryption(record: dict[str, Any], *, key_present: bool,
                     total_estimate: int | None = None) -> dict[str, Any]:
    """The reader-facing `encryption` block (the same keys the status route and the
    readiness stepper show): pure, so it is unit-testable without a database. With no
    key the state is `no_key`; with a key but no record yet it is `pending`, and
    `rows_total` falls back to the caller's table-size estimate (never the number of
    plaintext rows, which nothing has counted yet)."""
    if not key_present:
        state = "no_key"
    elif not record:
        state = "pending"
    else:
        state = str(record.get("state") or "pending")
    remaining = record.get("rows_remaining")
    if state == "complete":
        remaining = 0
    return {
        "state": state,
        "rows_done": record.get("rows_done", 0),
        "rows_remaining": remaining,
        "rows_total": record.get("rows_total", total_estimate),
        "rate_per_sec": record.get("rate_per_sec"),
        "eta_seconds": record.get("eta_seconds") if state == "running" else None,
        "rows_estimated": bool(record.get("rows_estimated")) and state != "complete",
        "started_at": record.get("started_at"),
        "updated_at": record.get("updated_at"),
        "last_error": record.get("last_error"),
    }


async def estimate_total_rows(pool: asyncpg.Pool) -> int | None:
    """Planner's own row estimate for the hot table: a catalog read, no scan. -1 (never
    analyzed) reads as unknown."""
    value = await pool.fetchval(
        "SELECT reltuples::bigint FROM pg_class WHERE oid = 'soul_lines'::regclass")
    return int(value) if value is not None and value >= 0 else None


_PLAINTEXT_HOT = "substring(raw_line from 1 for 5) <> $1"
_PLAINTEXT_COLD = "substring(content_gzip from 1 for 5) <> $1"


async def _initial_counts(
    pool: asyncpg.Pool, *, exact_below: int = EXACT_COUNT_BELOW,
) -> tuple[int, bool]:
    """(plaintext rows to encrypt, whether that number is an estimate). NEVER a full scan of
    a big table: counting two million rows takes minutes and would stall the first status
    after a deploy. Small tables are counted exactly; a big one is estimated from the
    planner's row count times the plaintext share of a small page sample, and the number
    corrects itself as the pass runs (the pass ends when it runs out of plaintext rows,
    whatever the estimate said). The cold tier holds far fewer rows and is counted exactly."""
    cold = int(await pool.fetchval(
        f"SELECT count(*) FROM soul_lines_cold WHERE {_PLAINTEXT_COLD}",
        FERNET_TOKEN_PREFIX) or 0)
    reltuples = await estimate_total_rows(pool)
    if reltuples is None or reltuples < exact_below:
        hot = int(await pool.fetchval(
            f"SELECT count(*) FROM soul_lines WHERE {_PLAINTEXT_HOT}",
            FERNET_TOKEN_PREFIX) or 0)
        return hot + cold, False
    sample = await pool.fetchrow(
        "SELECT count(*) AS seen, "
        f"count(*) FILTER (WHERE {_PLAINTEXT_HOT}) AS plain "
        f"FROM soul_lines TABLESAMPLE SYSTEM ({SAMPLE_PERCENT})", FERNET_TOKEN_PREFIX)
    seen, plain = int(sample["seen"]), int(sample["plain"])
    share = plain / seen if seen else 1.0  # an empty sample: assume the worst, all plaintext
    return int(reltuples * share) + cold, True


async def _any_plaintext(pool: asyncpg.Pool) -> bool:
    hot = await pool.fetchval(
        f"SELECT 1 FROM soul_lines WHERE {_PLAINTEXT_HOT} LIMIT 1", FERNET_TOKEN_PREFIX)
    if hot:
        return True
    cold = await pool.fetchval(
        f"SELECT 1 FROM soul_lines_cold WHERE {_PLAINTEXT_COLD} LIMIT 1", FERNET_TOKEN_PREFIX)
    return bool(cold)


async def _hot_batch(
    pool: asyncpg.Pool, fernet: MultiFernet, cursor: list[Any] | None, batch_size: int,
) -> tuple[int, list[Any] | None, bool]:
    """Encrypts one keyset-paginated batch of plaintext rows after `cursor`. Returns
    (rows_encrypted, new_cursor, exhausted). The cursor is the primary key of the last
    row SEEN (encrypted or not), so it only ever moves forward; a row appended by live
    ingest lands after it and arrives already encrypted."""
    if cursor is None:
        rows = await pool.fetch(
            "SELECT harness, anchor_sid, line_idx, raw_line FROM soul_lines "
            f"WHERE {_PLAINTEXT_HOT} ORDER BY harness, anchor_sid, line_idx LIMIT $2",
            FERNET_TOKEN_PREFIX, batch_size)
    else:
        rows = await pool.fetch(
            "SELECT harness, anchor_sid, line_idx, raw_line FROM soul_lines "
            f"WHERE {_PLAINTEXT_HOT} AND (harness, anchor_sid, line_idx) > ($2, $3, $4) "
            "ORDER BY harness, anchor_sid, line_idx LIMIT $5",
            FERNET_TOKEN_PREFIX, cursor[0], cursor[1], cursor[2], batch_size)
    if not rows:
        return 0, cursor, True
    updates = [
        (fernet.encrypt(bytes(r["raw_line"])), r["harness"], r["anchor_sid"], r["line_idx"])
        for r in rows if not is_encrypted(bytes(r["raw_line"]))]
    if updates:
        async with pool.acquire() as conn:
            await conn.executemany(
                "UPDATE soul_lines SET raw_line=$1 WHERE harness=$2 AND anchor_sid=$3 "
                "AND line_idx=$4", updates)
    last = rows[-1]
    return len(updates), [last["harness"], last["anchor_sid"], last["line_idx"]], (
        len(rows) < batch_size)


async def _cold_batch(pool: asyncpg.Pool, fernet: MultiFernet, batch_size: int) -> int:
    """Encrypts up to `batch_size` plaintext cold-tier blobs. No cursor: an encrypted row
    stops matching the filter, so the next call simply finds the next ones."""
    rows = await pool.fetch(
        "SELECT harness, anchor_sid, content_gzip FROM soul_lines_cold "
        f"WHERE {_PLAINTEXT_COLD} LIMIT $2", FERNET_TOKEN_PREFIX, batch_size)
    for r in rows:
        await pool.execute(
            "UPDATE soul_lines_cold SET content_gzip=$1 WHERE harness=$2 AND anchor_sid=$3",
            fernet.encrypt(bytes(r["content_gzip"])), r["harness"], r["anchor_sid"])
    return len(rows)


async def encrypt_tick(
    pool: asyncpg.Pool, fernet: MultiFernet, *,
    budget_secs: float = TICK_BUDGET_SECS, batch_size: int = BATCH_SIZE,
    cold_batch_size: int = COLD_BATCH_SIZE,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """One bounded slice of the background pass. Resumable (the cursor lives in the
    progress record), idempotent (a finished pass only re-probes every `REPROBE_SECS`),
    and safe to run alongside live ingest. Returns the updated record.

    States written: `running` while rows remain, `complete` once a probe finds no
    plaintext anywhere, `error` (with `last_error`, cursor kept) if a batch raised, in
    which case the exception is re-raised so the worker's own watch sees the failure and
    the next tick retries from the same cursor."""
    record = read_progress()
    now_wall = time.time()
    if record.get("state") == "complete":
        if now_wall - float(record.get("verified_epoch") or 0.0) < REPROBE_SECS:
            return record
        if not await _any_plaintext(pool):
            record["verified_epoch"] = now_wall
            record["updated_at"] = _now()
            _write_progress(record)
            return record
        record = {}  # stragglers found: start a fresh pass
    if not record.get("started_at"):
        total, estimated = await _initial_counts(pool)
        record = {
            "state": "running", "started_at": _now(), "rows_total": total,
            "rows_estimated": estimated,
            "rows_done": 0, "rows_remaining": total, "active_secs": 0.0,
            "cursor": None, "rate_per_sec": None, "eta_seconds": None, "last_error": None,
        }
    record["state"] = "running"
    started = clock()
    done_this_tick = 0
    try:
        while clock() - started < budget_secs:
            batch_started = clock()
            encrypted, cursor, hot_exhausted = await _hot_batch(
                pool, fernet, record.get("cursor"), batch_size)
            record["cursor"] = cursor
            if encrypted == 0 and hot_exhausted:
                encrypted = await _cold_batch(pool, fernet, cold_batch_size)
                if encrypted == 0:
                    if await _any_plaintext(pool):
                        record["cursor"] = None  # a straggler behind the cursor
                        continue
                    record.update(state="complete", rows_remaining=0, eta_seconds=0,
                                  verified_epoch=now_wall, last_error=None)
                    break
            done_this_tick += encrypted
            record["rows_done"] = int(record.get("rows_done", 0)) + encrypted
            if record["rows_done"] > int(record.get("rows_total", 0)):
                record["rows_total"] = record["rows_done"]  # an estimate that ran low
            record["rows_remaining"] = max(
                0, int(record.get("rows_total", 0)) - record["rows_done"])
            spent = clock() - batch_started
            record["active_secs"] = float(record.get("active_secs", 0.0)) + spent
            if record["active_secs"] > 0 and record["rows_done"]:
                rate = record["rows_done"] / record["active_secs"]
                record["rate_per_sec"] = round(rate, 1)
                # the duty cycle is one half, so wall-clock ETA is twice the busy time
                record["eta_seconds"] = int(record["rows_remaining"] / rate * 2)
            record["updated_at"] = _now()
            _write_progress(record)
            await sleep(spent)  # at most half the time on the database, ever
    except Exception as exc:
        record.update(state="error", last_error=f"{type(exc).__name__}: {exc}",
                      updated_at=_now())
        _write_progress(record)
        raise
    record["updated_at"] = _now()
    _write_progress(record)
    return record
