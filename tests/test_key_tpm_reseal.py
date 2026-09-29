"""Sealing an existing key to the machine's TPM (soul_crypto.soul_key_reseal_tpm): the same
key bytes stored a stronger way, never a rotation. No real TPM: systemd-creds is replaced by a
tagged fake so a test can see WHICH way a blob was sealed, and every credential lands in a
scratch directory, never the machine's own credential store."""
from __future__ import annotations

from pathlib import Path

import pytest
from src.ingest import soul_crypto
from src.orchestrator import key_setup

_USABLE = {"device_present": True, "usable": True, "tss_member": True, "creds_available": True}


@pytest.fixture(autouse=True)
def _scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdgcfg"))
    for name in ("OSIRIS_SOUL_KEY", "OSIRIS_SOUL_KEY_FILE", "OSIRIS_RESTIC_PASSWORD",
                 "OSIRIS_RESTIC_PASSWORD_FILE", "CREDENTIALS_DIRECTORY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(soul_crypto, "_installed_user_unit_env_value", lambda name: None)
    monkeypatch.setattr(soul_crypto, "_systemd_creds_available", lambda: True)
    monkeypatch.setattr(soul_crypto, "_is_tss_member", lambda: False)
    monkeypatch.setattr(soul_crypto, "_encrypt_with_systemd_creds",
                        lambda plain, *, with_key: with_key.encode() + b"|" + plain)
    monkeypatch.setattr(soul_crypto, "_decrypt_with_systemd_creds",
                        lambda blob: blob.split(b"|", 1)[1])


def _facts(monkeypatch: pytest.MonkeyPatch, **kw: bool) -> None:
    monkeypatch.setattr(soul_crypto, "tpm_facts", lambda: dict(_USABLE, **kw))


def _blob() -> bytes:
    path = soul_crypto._credential_path(soul_crypto._key_file_path())
    return path.read_bytes()


def test_reseal_moves_a_host_key_onto_the_tpm_without_changing_the_key(
        monkeypatch: pytest.MonkeyPatch) -> None:
    soul_crypto.soul_key_init(backend="host-cred")
    key_before = soul_crypto.get_soul_key()
    assert _blob().startswith(b"host|")
    _facts(monkeypatch)
    out = soul_crypto.soul_key_reseal_tpm()
    assert out == {"resealed": True, "backend": "host+tpm2"}
    assert _blob().startswith(b"host+tpm2|")
    assert soul_crypto.get_soul_key() == key_before
    assert soul_crypto.soul_key_status()["backend"] == "host+tpm2"


def test_reseal_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    soul_crypto.soul_key_init(backend="host-cred")
    _facts(monkeypatch)
    soul_crypto.soul_key_reseal_tpm()
    assert soul_crypto.soul_key_reseal_tpm() == {
        "resealed": False, "reason": "already sealed to the TPM"}


def test_reseal_waits_when_this_session_cannot_use_the_tpm_yet(
        monkeypatch: pytest.MonkeyPatch) -> None:
    soul_crypto.soul_key_init(backend="host-cred")
    _facts(monkeypatch, usable=False)  # joined the group, not logged back in
    out = soul_crypto.soul_key_reseal_tpm()
    assert out["resealed"] is False and "not usable" in out["reason"]
    assert _blob().startswith(b"host|")


def test_reseal_says_so_when_the_machine_has_no_tpm(monkeypatch: pytest.MonkeyPatch) -> None:
    soul_crypto.soul_key_init(backend="host-cred")
    _facts(monkeypatch, device_present=False, usable=False)
    assert soul_crypto.soul_key_reseal_tpm()["reason"] == "no TPM on this machine"


def test_reseal_refuses_a_plaintext_file_key(monkeypatch: pytest.MonkeyPatch) -> None:
    soul_crypto.soul_key_init(backend="file")
    _facts(monkeypatch)
    out = soul_crypto.soul_key_reseal_tpm()
    assert out["resealed"] is False and "file" in out["reason"]


def test_a_failed_seal_leaves_the_working_key_untouched(
        monkeypatch: pytest.MonkeyPatch) -> None:
    soul_crypto.soul_key_init(backend="host-cred")
    before = _blob()
    _facts(monkeypatch)

    def refuse(plain: bytes, *, with_key: str) -> bytes:
        if with_key == "host+tpm2":
            raise RuntimeError("TPM refused")
        return with_key.encode() + b"|" + plain

    monkeypatch.setattr(soul_crypto, "_encrypt_with_systemd_creds", refuse)
    out = soul_crypto.soul_key_reseal_tpm()
    assert out == {"resealed": False, "error": "TPM refused"}
    assert _blob() == before
    assert soul_crypto.soul_key_status()["backend"] == "host-cred"


def test_a_seal_that_does_not_read_back_the_same_key_is_never_written(
        monkeypatch: pytest.MonkeyPatch) -> None:
    soul_crypto.soul_key_init(backend="host-cred")
    before = _blob()
    _facts(monkeypatch)
    monkeypatch.setattr(soul_crypto, "_encrypt_with_systemd_creds",
                        lambda plain, *, with_key: b"x|WRONG")
    out = soul_crypto.soul_key_reseal_tpm()
    assert out["resealed"] is False and "same key" in out["error"]
    assert _blob() == before


def test_deploy_setup_reseals_once_the_tpm_is_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    soul_crypto.soul_key_init(backend="host-cred")
    _facts(monkeypatch)
    report = key_setup.ensure_key_setup()
    assert report["tpm_resealed"] is True
    assert soul_crypto.soul_key_status()["backend"] == "host+tpm2"


def test_init_hint_names_reseal_not_rotate() -> None:
    hint = soul_crypto.soul_key_init(backend="host-cred")["tss_hint"]
    assert "soul-key reseal" in hint
    assert "rotate" not in hint
