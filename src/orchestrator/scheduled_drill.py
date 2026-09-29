"""THE RESTORE DRILL, ON ITS OWN SCHEDULE: a backup that was never restored is a hope, so the
drill is part of setup, not a chore. It runs from the offload tick (`offload_runner.
run_offload_tick`) for a target that is present right now AND already holds a successful
offload, once for the first time straight after that first offload, then again every
`DRILL_INTERVAL_DAYS` days. Receipts land in the same file the manual `soul-key
restore-drill` writes (`soul_key.record_restore_drill`), which is what the setup stepper reads.

A FAILED drill is retried no sooner than `RETRY_AFTER_FAILURE_HOURS` later, so a broken target
is reported promptly without a restore attempt on every 15-minute tick. The drill restores
into its own scratch directory and never touches the live vault
(`scripts.osiris_offbox_restore_drill.run_drill`)."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

DRILL_INTERVAL_DAYS = 7
RETRY_AFTER_FAILURE_HOURS = 6


def _parse(ts: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except (TypeError, ValueError):
        return None


def drill_due(drill_receipt: dict[str, Any], offload_receipt: dict[str, Any],
              *, now: datetime | None = None) -> bool:
    """Pure. Due only once the target has a successful offload; then when it never passed, or
    last passed more than a week ago; never within the retry window of a failed attempt."""
    now = now or datetime.now(UTC)
    if not offload_receipt.get("last_successful_offload"):
        return False
    last_attempt = _parse(drill_receipt.get("last_attempt_at"))
    last_passed = _parse(drill_receipt.get("last_passed_at"))
    failed_since_pass = bool(drill_receipt.get("last_error"))
    if (failed_since_pass and last_attempt
            and now - last_attempt < timedelta(hours=RETRY_AFTER_FAILURE_HOURS)):
        return False
    if last_passed is None:
        return True
    return now - last_passed >= timedelta(days=DRILL_INTERVAL_DAYS)


def _real_run_drill(repo_url: str) -> str | None:
    from scripts.osiris_offbox_restore_drill import run_drill

    return run_drill(repo_url)


async def run_due_drills(
    present_targets: list[dict[str, Any]], offload_receipts: dict[str, Any], *,
    run_drill: Callable[[str], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Drills every present target that is due one, one at a time (each is a real restore).
    Never raises: a drill that blows up is recorded as a failed attempt. The real drill is
    resolved at call time, never bound as a default, so a test can replace that one name."""
    from src.orchestrator.soul_key import record_restore_drill, restore_drill_receipts

    run_drill = run_drill or _real_run_drill
    drill_receipts = restore_drill_receipts()
    out: list[dict[str, Any]] = []
    for target in present_targets:
        url = str(target.get("path_or_url") or "")
        name = target.get("name", "<unnamed>")
        if not url or not drill_due(
                drill_receipts.get(url, {}), offload_receipts.get(name, {})):
            continue
        try:
            fail = await asyncio.to_thread(run_drill, url)
        except Exception as exc:  # noqa: BLE001 - one target's drill must not sink the tick
            fail = f"{type(exc).__name__}: {exc}"
        record_restore_drill(url, ok=fail is None, error=fail)
        out.append({"name": name, "ok": fail is None, **({"error": fail} if fail else {})})
    return out
