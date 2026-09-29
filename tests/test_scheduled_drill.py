"""The restore drill on its own schedule (src.orchestrator.scheduled_drill): pure due-ness rules,
and the runner with an injected drill (a real restic restore never runs here)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.orchestrator import scheduled_drill as sd
from src.orchestrator import soul_key as soul_key_orch

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
OFFLOADED = {"last_successful_offload": (NOW - timedelta(hours=1)).isoformat()}


def _ago(**kw: float) -> str:
    return (NOW - timedelta(**kw)).isoformat()


def test_no_drill_before_the_first_successful_offload() -> None:
    assert sd.drill_due({}, {}, now=NOW) is False
    assert sd.drill_due({}, {"last_attempt_at": _ago(hours=1), "last_error": "x"},
                        now=NOW) is False


def test_the_first_drill_is_due_straight_after_the_first_offload() -> None:
    assert sd.drill_due({}, OFFLOADED, now=NOW) is True


def test_a_recent_pass_is_not_due_and_a_week_old_one_is() -> None:
    assert sd.drill_due({"last_passed_at": _ago(days=2)}, OFFLOADED, now=NOW) is False
    assert sd.drill_due({"last_passed_at": _ago(days=6, hours=23)}, OFFLOADED, now=NOW) is False
    assert sd.drill_due({"last_passed_at": _ago(days=7, minutes=1)}, OFFLOADED, now=NOW) is True


def test_a_failed_drill_waits_out_the_retry_window_then_tries_again() -> None:
    failed_recently = {"last_attempt_at": _ago(hours=1), "last_error": "boom"}
    assert sd.drill_due(failed_recently, OFFLOADED, now=NOW) is False
    failed_before = {"last_attempt_at": _ago(hours=7), "last_error": "boom"}
    assert sd.drill_due(failed_before, OFFLOADED, now=NOW) is True


def test_a_failure_after_an_old_pass_still_waits_out_the_retry_window() -> None:
    receipt = {"last_passed_at": _ago(days=9), "last_attempt_at": _ago(hours=1),
               "last_error": "boom"}
    assert sd.drill_due(receipt, OFFLOADED, now=NOW) is False


def test_garbage_timestamps_read_as_never_passed() -> None:
    assert sd.drill_due({"last_passed_at": "not a date"}, OFFLOADED, now=NOW) is True


async def test_due_targets_are_drilled_and_the_receipt_feeds_readiness() -> None:
    drilled: list[str] = []

    def _drill(url: str) -> str | None:
        drilled.append(url)
        return None

    targets = [{"name": "nas", "path_or_url": "sftp:nas:/r"},
               {"name": "cold", "path_or_url": "local:/mnt/x/r"}]
    offloads = {"nas": OFFLOADED}  # "cold" never offloaded successfully

    out = await sd.run_due_drills(targets, offloads, run_drill=_drill)

    assert out == [{"name": "nas", "ok": True}]
    assert drilled == ["sftp:nas:/r"]
    assert soul_key_orch.restore_drill_receipts()["sftp:nas:/r"]["last_passed_at"]
    # and it is not due again right away
    assert await sd.run_due_drills(targets, offloads, run_drill=_drill) == []


async def test_a_failing_or_crashing_drill_is_recorded_and_never_raises() -> None:
    def _crash(url: str) -> str | None:
        raise RuntimeError("restic exploded")

    out = await sd.run_due_drills(
        [{"name": "nas", "path_or_url": "sftp:nas:/r"}], {"nas": OFFLOADED}, run_drill=_crash)

    assert out == [{"name": "nas", "ok": False, "error": "RuntimeError: restic exploded"}]
    receipt = soul_key_orch.restore_drill_receipts()["sftp:nas:/r"]
    assert receipt["last_error"] == "RuntimeError: restic exploded"
    assert "last_passed_at" not in receipt


async def test_the_default_drill_is_the_one_tests_replace() -> None:
    """Every test in the suite runs with the real drill stubbed out; a target that is due
    reads as a failed attempt naming why, never a real restore."""
    out = await sd.run_due_drills(
        [{"name": "nas", "path_or_url": "sftp:nas:/r"}], {"nas": OFFLOADED})
    assert out[0]["ok"] is False
    assert "do not run inside tests" in out[0]["error"]
