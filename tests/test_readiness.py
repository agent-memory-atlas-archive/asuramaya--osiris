"""The first-run stepper's ordering/flag logic (src.orchestrator.readiness), tested
against synthetic inputs -- no database, no filesystem, no systemctl, per that
module's own docstring."""
from __future__ import annotations

from src.orchestrator.readiness import STEP_ORDER, compute_readiness_steps

NO_KEY = {"present": False}
KEY_NO_RECOVERY = {
    "present": True, "recovery_paths_enrolled": [], "legacy_plaintext_rows": 0,
}
KEY_READY = {
    "present": True, "recovery_paths_enrolled": ["fido2"], "legacy_plaintext_rows": 0,
    "recovery": {"enrolled": True, "stale": False, "verified": {
        "last_verified_at": "2026-09-29T00:00:00Z", "ok": True, "matches_live_key": True,
        "last_error": None}},
}
NO_RESTIC = {"present": False}
RESTIC_READY = {"present": True}


def _steps(**kw: object) -> dict[str, dict[str, object]]:
    defaults: dict[str, object] = dict(
        soul_key=NO_KEY, restic_key=NO_RESTIC, offload_targets=[],
        restore_drill_receipts={}, services_restarted=None,
    )
    defaults.update(kw)
    result = compute_readiness_steps(**defaults)  # type: ignore[arg-type]
    return {s["key"]: s for s in result}


def test_step_order_matches_the_declared_sequence() -> None:
    steps = compute_readiness_steps(
        soul_key=NO_KEY, restic_key=NO_RESTIC, offload_targets=[],
        restore_drill_receipts={}, services_restarted=None)
    assert [s["key"] for s in steps] == list(STEP_ORDER)


def test_fresh_install_flags_key_setup_as_the_current_step() -> None:
    steps = _steps()
    assert steps["key_set_up"]["status"] == "missing"
    assert steps["key_set_up"]["current"] is True
    # automatic: shown as progress, never a button
    assert steps["key_set_up"]["mode"] == "auto"
    assert steps["key_set_up"]["action"] is None
    # every later step is blocked on it, never independently "current"
    assert steps["services_restarted"]["current"] is False
    assert steps["services_restarted"]["status"] == "missing"
    assert steps["recovery_enrolled"]["status"] == "missing"
    assert steps["data_encrypted"]["status"] == "missing"


def test_key_present_with_no_recovery_flags_recovery_as_current() -> None:
    steps = _steps(soul_key=KEY_NO_RECOVERY, services_restarted=True)
    assert steps["key_set_up"]["status"] == "done"
    assert steps["services_restarted"]["status"] == "done"
    assert steps["recovery_enrolled"]["status"] == "missing"
    assert steps["recovery_enrolled"]["current"] is True


def test_services_not_yet_restarted_is_needs_attention_not_missing() -> None:
    steps = _steps(soul_key=KEY_READY, services_restarted=False)
    assert steps["services_restarted"]["status"] == "needs_attention"
    assert "restart" in steps["services_restarted"]["reason"].lower()
    assert steps["services_restarted"]["action"] is None
    assert steps["services_restarted"]["mode"] == "auto"


def test_services_restart_unknown_is_needs_attention_not_a_false_done() -> None:
    steps = _steps(soul_key=KEY_READY, services_restarted=None)
    assert steps["services_restarted"]["status"] == "needs_attention"


def test_an_older_status_shape_with_a_bare_count_still_reads_and_has_no_button() -> None:
    soul_key = dict(KEY_READY, legacy_plaintext_rows=42)
    steps = _steps(soul_key=soul_key, services_restarted=True)
    assert steps["data_encrypted"]["status"] == "missing"
    assert steps["data_encrypted"]["reason"] == "42 item(s) still stored in plain text."
    assert steps["data_encrypted"]["action"] is None


def _encryption(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "state": "running", "rows_done": 0, "rows_remaining": None, "rows_total": None,
        "rate_per_sec": None, "eta_seconds": None, "last_error": None}
    base.update(kw)
    return base


def test_background_encryption_shows_progress_and_no_button() -> None:
    soul_key = dict(KEY_READY, legacy_plaintext_rows=600, encryption=_encryption(
        rows_done=400, rows_remaining=600, rows_total=1000, rate_per_sec=50.0,
        eta_seconds=1440))
    step = _steps(soul_key=soul_key, services_restarted=True)["data_encrypted"]
    assert step["mode"] == "auto"
    assert step["action"] is None
    assert step["status"] == "missing"
    assert step["current"] is True
    assert "600 item(s) left, about 24 min left" in step["reason"]
    assert step["progress"] == {
        "done": 400, "total": 1000, "eta_seconds": 1440, "rate_per_sec": 50.0,
        "estimate": False}


def test_encryption_not_yet_counted_says_it_starts_by_itself_with_an_estimate() -> None:
    soul_key = dict(KEY_READY, legacy_plaintext_rows=None, encryption=_encryption(
        state="pending", rows_total=2_000_000))
    step = _steps(soul_key=soul_key, services_restarted=True)["data_encrypted"]
    assert step["reason"] == "Encryption starts automatically in the background."
    assert step["progress"]["estimate"] is True
    assert step["progress"]["total"] == 2_000_000


def test_encryption_that_hit_a_problem_needs_attention_with_the_reason() -> None:
    soul_key = dict(KEY_READY, encryption=_encryption(
        state="error", last_error="OSError: disk full"))
    step = _steps(soul_key=soul_key, services_restarted=True)["data_encrypted"]
    assert step["status"] == "needs_attention"
    assert "disk full" in step["reason"]
    assert step["action"] is None


def test_finished_encryption_is_done() -> None:
    soul_key = dict(KEY_READY, encryption=_encryption(state="complete", rows_remaining=0))
    assert _steps(soul_key=soul_key, services_restarted=True)["data_encrypted"][
        "status"] == "done"


def test_recovery_check_is_a_hands_step_until_a_touch_verifies_it() -> None:
    unverified = dict(KEY_READY, recovery={"enrolled": True, "stale": False, "verified": None})
    step = _steps(soul_key=unverified, services_restarted=True)["recovery_verified"]
    assert step["mode"] == "hands"
    assert step["status"] == "missing"
    assert step["action"] == {"kind": "verify_recovery"}


def test_a_failed_recovery_check_needs_attention_with_the_error() -> None:
    failed = dict(KEY_READY, recovery={"enrolled": True, "stale": False, "verified": {
        "last_verified_at": None, "ok": False, "last_error": "wrong Security Key"}})
    step = _steps(soul_key=failed, services_restarted=True)["recovery_verified"]
    assert step["status"] == "needs_attention"
    assert "wrong Security Key" in step["reason"]
    assert step["action"] == {"kind": "verify_recovery"}


def test_recovery_for_an_older_key_asks_to_enroll_again_not_to_verify() -> None:
    stale = dict(KEY_READY, recovery={"enrolled": True, "stale": True, "verified": {
        "last_verified_at": "2026-09-01T00:00:00Z", "ok": True}})
    step = _steps(soul_key=stale, services_restarted=True)["recovery_verified"]
    assert step["status"] == "needs_attention"
    assert step["action"] == {"kind": "enroll_recovery"}


def test_recovery_check_waits_for_enrollment() -> None:
    step = _steps(soul_key=KEY_NO_RECOVERY, services_restarted=True)["recovery_verified"]
    assert step["status"] == "missing"
    assert step["action"] is None


def test_only_the_steps_that_need_a_person_carry_an_action() -> None:
    steps = compute_readiness_steps(
        soul_key=KEY_NO_RECOVERY, restic_key=NO_RESTIC, offload_targets=[],
        restore_drill_receipts={}, services_restarted=None)
    for s in steps:
        if s["mode"] == "auto":
            assert s["action"] is None, s["key"]
    assert {s["key"] for s in steps if s["mode"] == "hands"} == {
        "recovery_enrolled", "recovery_verified", "offload_target_present",
        "restore_test_passed"}


def test_zero_legacy_rows_is_done_never_needs_attention() -> None:
    steps = _steps(soul_key=KEY_READY, services_restarted=True)
    assert steps["data_encrypted"]["status"] == "done"


def test_no_offload_targets_is_missing_with_a_configure_action() -> None:
    steps = _steps(soul_key=KEY_READY, restic_key=RESTIC_READY, services_restarted=True)
    assert steps["offload_target_present"]["status"] == "missing"
    assert steps["offload_target_present"]["action"] == {"kind": "configure_offload"}


def test_local_target_not_mounted_is_needs_attention_with_its_own_name() -> None:
    targets = [{"name": "nas", "kind": "local", "enabled": True,
                "presence": {"present": False}}]
    steps = _steps(soul_key=KEY_READY, restic_key=RESTIC_READY, services_restarted=True,
                   offload_targets=targets)
    assert steps["offload_target_present"]["status"] == "needs_attention"
    assert "nas" in steps["offload_target_present"]["reason"]


def test_local_target_mounted_is_done() -> None:
    targets = [{"name": "nas", "kind": "local", "enabled": True,
                "presence": {"present": True}}]
    steps = _steps(soul_key=KEY_READY, restic_key=RESTIC_READY, services_restarted=True,
                   offload_targets=targets)
    assert steps["offload_target_present"]["status"] == "done"


def test_restic_target_with_no_live_presence_is_still_done() -> None:
    # restic presence is deliberately never live-checked (backup_settings.py's own
    # documented reason); a configured restic target must not be flagged missing for
    # lacking a signal that was never going to exist.
    targets = [{"name": "offsite", "kind": "restic", "enabled": True, "presence": None}]
    steps = _steps(soul_key=KEY_READY, restic_key=RESTIC_READY, services_restarted=True,
                   offload_targets=targets)
    assert steps["offload_target_present"]["status"] == "done"


def test_disabled_target_does_not_count_as_configured() -> None:
    targets = [{"name": "nas", "kind": "local", "enabled": False,
                "presence": {"present": True}}]
    steps = _steps(soul_key=KEY_READY, restic_key=RESTIC_READY, services_restarted=True,
                   offload_targets=targets)
    assert steps["offload_target_present"]["status"] == "missing"


def test_offload_run_done_once_any_target_ever_succeeded() -> None:
    targets = [{"name": "nas", "kind": "local", "enabled": True,
                "presence": {"present": True}, "last_successful_offload": "2026-09-01"}]
    steps = _steps(soul_key=KEY_READY, restic_key=RESTIC_READY, services_restarted=True,
                   offload_targets=targets)
    assert steps["offload_run"]["status"] == "done"


def test_offload_run_failed_attempt_surfaces_the_stored_error() -> None:
    targets = [{"name": "nas", "kind": "local", "enabled": True,
                "presence": {"present": True}, "last_error": "repository locked"}]
    steps = _steps(soul_key=KEY_READY, restic_key=RESTIC_READY, services_restarted=True,
                   offload_targets=targets)
    assert steps["offload_run"]["status"] == "needs_attention"
    assert "repository locked" in steps["offload_run"]["reason"]


def test_offload_never_attempted_is_plain_missing() -> None:
    targets = [{"name": "nas", "kind": "local", "enabled": True,
                "presence": {"present": True}}]
    steps = _steps(soul_key=KEY_READY, restic_key=RESTIC_READY, services_restarted=True,
                   offload_targets=targets)
    assert steps["offload_run"]["status"] == "missing"
    assert steps["offload_run"]["reason"] == (
        "Runs automatically once a backup target is present.")
    assert steps["offload_run"]["action"] is None


def test_restore_drill_passed_reads_from_the_receipts_dict() -> None:
    receipts = {"sftp:nas.local:/x": {"last_passed_at": "2026-09-01T00:00:00Z"}}
    steps = _steps(soul_key=KEY_READY, restic_key=RESTIC_READY, services_restarted=True,
                   restore_drill_receipts=receipts)
    assert steps["restore_test_passed"]["status"] == "done"


def test_restore_drill_never_passed_is_missing() -> None:
    receipts = {"sftp:nas.local:/x": {"last_attempt_at": "2026-09-01T00:00:00Z",
                                       "last_error": "repository not found"}}
    steps = _steps(soul_key=KEY_READY, restic_key=RESTIC_READY, services_restarted=True,
                   restore_drill_receipts=receipts)
    assert steps["restore_test_passed"]["status"] == "missing"


def test_fully_ready_install_has_no_current_step() -> None:
    targets = [{"name": "nas", "kind": "local", "enabled": True,
                "presence": {"present": True}, "last_successful_offload": "2026-09-01"}]
    receipts = {"sftp:nas.local:/x": {"last_passed_at": "2026-09-01T00:00:00Z"}}
    steps = compute_readiness_steps(
        soul_key=KEY_READY, restic_key=RESTIC_READY, offload_targets=targets,
        restore_drill_receipts=receipts, services_restarted=True)
    assert all(s["status"] == "done" for s in steps)
    assert not any(s["current"] for s in steps)


def test_exactly_one_step_is_current_when_something_is_missing() -> None:
    steps = _steps(soul_key=KEY_NO_RECOVERY, services_restarted=True)
    current = [s for s in steps.values() if s["current"]]
    assert len(current) == 1
    assert current[0]["key"] == "recovery_enrolled"
