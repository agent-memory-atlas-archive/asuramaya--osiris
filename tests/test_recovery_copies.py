"""Off-box copies of the security-key recovery file (src.orchestrator.recovery_copies) and
recovering from one (soul_key.soul_key_recover's recovery_file). The security key is the same
deterministic fake the other key tests use; ssh/scp is always an injected fake."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from src.ingest import soul_crypto, systemd_credential
from src.orchestrator import key_setup, recovery_copies
from src.orchestrator import soul_key as soul_key_orch

from tests.test_soul_crypto import _FakeFido2Client


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdgcfg"))
    for name in ("OSIRIS_SOUL_KEY", "OSIRIS_SOUL_KEY_FILE", "OSIRIS_RESTIC_PASSWORD",
                 "OSIRIS_RESTIC_PASSWORD_FILE", "CREDENTIALS_DIRECTORY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(soul_crypto, "_installed_user_unit_env_value", lambda name: None)
    monkeypatch.setattr(soul_crypto, "_systemd_creds_available", lambda: False)
    monkeypatch.setattr(systemd_credential, "systemd_creds_available", lambda: False)
    monkeypatch.setenv(recovery_copies._RECEIPTS_ENV, str(tmp_path / "copies.json"))


@pytest.fixture
def enrolled(monkeypatch: pytest.MonkeyPatch) -> Path:
    """A box with a key and an enrolled recovery file; returns the recovery file's path."""
    client = _FakeFido2Client()
    monkeypatch.setattr(soul_crypto, "_find_fido2_device", lambda: object())
    monkeypatch.setattr(soul_crypto, "_fido2_client", lambda device, rp_id: client)
    key_setup.ensure_key_setup()
    assert "error" not in soul_key_orch.soul_key_enroll_recovery(rp_id="localhost")
    return soul_crypto.recovery_file_path()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    path = tmp_path / "vault"
    path.mkdir()
    return path


def test_nothing_is_copied_before_a_recovery_method_is_enrolled(vault: Path) -> None:
    assert recovery_copies.copy_to_vault(vault) is None
    assert recovery_copies.copy_to_target({"name": "n", "kind": "local"}) is None
    assert recovery_copies.copy_status()["enrolled"] is False


def test_the_vault_copy_is_a_private_plain_file_and_idempotent(
    enrolled: Path, vault: Path,
) -> None:
    first = recovery_copies.copy_to_vault(vault)

    assert first == {"dest": "(vault)", "ok": True}
    copy = vault / "osiris-recovery" / "soul.key.recovery.json"
    assert copy.read_bytes() == enrolled.read_bytes()
    assert oct(copy.stat().st_mode & 0o777) == "0o600"
    mtime = copy.stat().st_mtime_ns
    assert recovery_copies.copy_to_vault(vault) == {"dest": "(vault)", "ok": True}
    assert copy.stat().st_mtime_ns == mtime  # already current: not rewritten
    assert recovery_copies.copy_status()["vault"] is True


def test_a_missing_vault_directory_is_recorded_not_raised(enrolled: Path, tmp_path: Path) -> None:
    out = recovery_copies.copy_to_vault(tmp_path / "no-vault")
    assert out is not None and out["ok"] is False
    assert "does not exist" in out["error"]
    assert recovery_copies.copy_status()["vault"] is False


def test_a_present_local_target_gets_a_plain_copy_beside_its_repository(
    enrolled: Path, tmp_path: Path,
) -> None:
    mount = tmp_path / "drive"
    mount.mkdir()
    target = {"name": "docked", "kind": "local", "expected_mountpoint": str(mount),
              "path_or_url": f"local:{mount}/restic-repo"}

    out = recovery_copies.copy_to_target(target)

    assert out == {"dest": "docked", "ok": True}
    assert (mount / "osiris-recovery" / "soul.key.recovery.json").read_bytes() == \
        enrolled.read_bytes()
    status = recovery_copies.copy_status()
    assert status["destinations"] == ["docked"]
    assert status["current"] is True
    assert status["count"] == 1


def test_a_target_kind_that_cannot_hold_a_plain_file_is_reported_and_never_counted(
    enrolled: Path,
) -> None:
    out = recovery_copies.copy_to_target(
        {"name": "cloud", "kind": "restic", "path_or_url": "s3:s3.amazonaws.com/bucket"})

    assert out is not None and out["ok"] is False
    assert "cannot hold a plain file" in out["error"]
    assert recovery_copies.copy_status()["count"] == 0


def test_the_vault_copy_alone_never_counts_as_off_box(enrolled: Path, vault: Path) -> None:
    recovery_copies.copy_to_vault(vault)
    status = recovery_copies.copy_status()
    assert status["vault"] is True
    assert status["count"] == 0
    assert status["current"] is False


def test_sftp_targets_go_through_the_injected_copier_and_are_not_recopied_within_a_week(
    enrolled: Path,
) -> None:
    calls: list[tuple[Path, str]] = []

    def _fake(src: Path, url: str) -> str | None:
        calls.append((src, url))
        return None

    target = {"name": "nas", "kind": "restic", "path_or_url": "sftp:nas.local:/backups/osiris"}
    first = recovery_copies.copy_to_target(target, sftp_copy=_fake)
    again = recovery_copies.copy_to_target(target, sftp_copy=_fake)

    assert first == {"dest": "nas", "ok": True}
    assert again is None
    assert len(calls) == 1
    assert calls[0][1] == "sftp:nas.local:/backups/osiris"
    assert recovery_copies.copy_status()["destinations"] == ["nas"]


def test_a_changed_recovery_file_makes_old_copies_stale_and_is_recopied(
    enrolled: Path,
) -> None:
    calls: list[str] = []
    target = {"name": "nas", "kind": "restic", "path_or_url": "sftp:nas.local:/backups/osiris"}
    recovery_copies.copy_to_target(
        target, sftp_copy=lambda src, url: calls.append(url) or None)
    blob = json.loads(enrolled.read_text())
    blob["restic_password_wrapped"] = "changed"
    enrolled.write_text(json.dumps(blob))

    stale = recovery_copies.copy_status()
    assert stale["count"] == 0  # the copy on the target is an older enrollment
    assert stale["current"] is False

    recovery_copies.copy_to_target(
        target, sftp_copy=lambda src, url: calls.append(url) or None)
    assert len(calls) == 2
    assert recovery_copies.copy_status()["current"] is True


def test_a_failed_attempt_keeps_the_earlier_good_receipt_and_says_why(enrolled: Path) -> None:
    target = {"name": "nas", "kind": "restic", "path_or_url": "sftp:nas.local:/backups/osiris"}
    recovery_copies.copy_to_target(target, sftp_copy=lambda src, url: None)
    good = recovery_copies.read_copy_receipts()["nas"]["sha256"]
    blob = json.loads(enrolled.read_text())
    blob["salt"] = "changed"
    enrolled.write_text(json.dumps(blob))

    out = recovery_copies.copy_to_target(
        target, sftp_copy=lambda src, url: "ssh: connection refused")

    assert out == {"dest": "nas", "ok": False, "error": "ssh: connection refused"}
    receipt = recovery_copies.read_copy_receipts()["nas"]
    assert receipt["sha256"] == good  # the copy that IS out there is still on record
    assert receipt["last_error"] == "ssh: connection refused"
    assert recovery_copies.copy_status()["count"] == 0


def test_sftp_urls_are_parsed_only_in_the_scp_style_form() -> None:
    assert recovery_copies._parse_sftp("sftp:user@nas.local:/b/osiris") == (
        "user@nas.local", "/b/osiris")
    assert recovery_copies._parse_sftp("sftp://nas.local//b/osiris") is None
    assert recovery_copies._parse_sftp("sftp:nas.local:relative") is None
    assert recovery_copies._parse_sftp("s3:bucket/path") is None
    assert "only sftp" in (recovery_copies._copy_sftp(Path("/x"), "rest:http://h/") or "")


async def test_a_sync_copies_to_the_vault_first_then_each_present_target(
    enrolled: Path, vault: Path, tmp_path: Path,
) -> None:
    mount = tmp_path / "drive"
    mount.mkdir()
    targets = [{"name": "docked", "kind": "local", "expected_mountpoint": str(mount)}]

    out = await recovery_copies.sync_recovery_copies(targets, vault)

    assert [r["dest"] for r in out] == ["(vault)", "docked"]
    assert all(r["ok"] for r in out)


async def test_a_sync_with_nothing_enrolled_does_nothing(vault: Path) -> None:
    assert await recovery_copies.sync_recovery_copies(
        [{"name": "d", "kind": "local", "expected_mountpoint": "/x"}], vault) == []


# --- recovering a machine that lost everything, from a plain copy --------------------------


def test_a_new_machine_recovers_from_a_copy_taken_off_a_backup_target(
    enrolled: Path, tmp_path: Path,
) -> None:
    from src.orchestrator import restic_credential

    password = restic_credential.get_restic_password()
    key = soul_crypto.read_key_bytes_at(Path(soul_crypto.soul_key_status()["path"]))
    copy = tmp_path / "from-the-drive.json"
    copy.write_bytes(enrolled.read_bytes())
    # the machine is lost: neither credential nor the recovery file survive
    Path(soul_crypto.soul_key_status()["path"]).unlink()
    restic_credential._password_file_path().unlink()
    enrolled.unlink()

    out = soul_key_orch.soul_key_recover(
        backend="file", rp_id="localhost", recovery_file=str(copy))

    assert "error" not in out
    assert out["restic_password_recovered"] is True
    assert soul_crypto.read_key_bytes_at(Path(soul_crypto.soul_key_status()["path"])) == key
    assert restic_credential.get_restic_password() == password
    assert oct(enrolled.stat().st_mode & 0o777) == "0o600"


def test_recovering_from_a_copy_refuses_to_replace_an_existing_recovery_file(
    enrolled: Path, tmp_path: Path,
) -> None:
    copy = tmp_path / "copy.json"
    copy.write_bytes(enrolled.read_bytes())
    Path(soul_crypto.soul_key_status()["path"]).unlink()

    out = soul_key_orch.soul_key_recover(
        backend="file", rp_id="localhost", recovery_file=str(copy))

    assert "already exists" in out["error"]


def test_recovering_from_a_copy_that_is_not_there_is_a_named_refusal(tmp_path: Path) -> None:
    out = soul_key_orch.soul_key_recover(
        rp_id="localhost", recovery_file=str(tmp_path / "nope.json"))
    assert "not found" in out["error"]


def test_the_status_block_reports_copies(enrolled: Path, vault: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    assert recovery_copies.copy_status()["count"] == 0
    recovery_copies.copy_to_target(
        {"name": "nas", "kind": "restic", "path_or_url": "sftp:h:/p/r"},
        sftp_copy=lambda s, u: None)
    assert recovery_copies.copy_status() == {
        "enrolled": True, "count": 1, "destinations": ["nas"], "current": True,
        "vault": False}
    assert os.path.exists(recovery_copies._receipts_path())
