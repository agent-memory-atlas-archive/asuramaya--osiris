"""Approving the osiris MCP server once for every directory (src.orchestrator.mcp_approval),
and onboarding skipping a per-repo .mcp.json when osiris is already registered at user scope.
Every test uses a scratch home (the suite points OSIRIS_CLAUDE_HOME at one); the real
~/.claude is never read or written."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from src.orchestrator import mcp_approval
from src.orchestrator.onboard import onboard


@pytest.fixture
def home() -> Path:
    return Path(os.environ["OSIRIS_CLAUDE_HOME"])


def _settings(home: Path) -> Path:
    return home / ".claude" / "settings.json"


def _write(path: Path, data: object, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=indent) + "\n")


def test_it_adds_osiris_and_keeps_every_other_key_and_the_files_own_layout(home: Path) -> None:
    original = {"model": "x", "permissions": {"allow": ["Bash(ls)"]}, "theme": "dark"}
    _write(_settings(home), original, indent=4)

    out = mcp_approval.ensure_mcp_approval()

    assert out["changed"] is True
    saved = json.loads(_settings(home).read_text())
    assert saved["enabledMcpjsonServers"] == ["osiris"]
    assert {k: saved[k] for k in original} == original
    assert list(saved)[:3] == ["model", "permissions", "theme"]  # order kept, new key last
    assert '\n    "model"' in _settings(home).read_text()  # the file's own 4-space indent


def test_other_approved_servers_are_kept(home: Path) -> None:
    _write(_settings(home), {"enabledMcpjsonServers": ["github", "linear"]})

    mcp_approval.ensure_mcp_approval()

    assert json.loads(_settings(home).read_text())["enabledMcpjsonServers"] == [
        "github", "linear", "osiris"]


def test_it_is_idempotent_and_the_second_run_changes_nothing(home: Path) -> None:
    _write(_settings(home), {"model": "x"})
    mcp_approval.ensure_mcp_approval()
    first = _settings(home).read_bytes()
    mtime = _settings(home).stat().st_mtime_ns

    again = mcp_approval.ensure_mcp_approval()

    assert again["changed"] is False
    assert "already approved" in again["note"]
    assert _settings(home).read_bytes() == first
    assert _settings(home).stat().st_mtime_ns == mtime


def test_a_missing_settings_file_is_created_with_just_the_approval(home: Path) -> None:
    assert not _settings(home).exists()

    out = mcp_approval.ensure_mcp_approval()

    assert out["changed"] is True
    assert json.loads(_settings(home).read_text()) == {"enabledMcpjsonServers": ["osiris"]}


def test_a_file_that_is_not_valid_json_is_left_untouched_and_reported(home: Path) -> None:
    _settings(home).parent.mkdir(parents=True)
    _settings(home).write_text('{"model": "x",  // a comment\n}')
    before = _settings(home).read_bytes()

    out = mcp_approval.ensure_mcp_approval()

    assert out["changed"] is False and out["ok"] is False
    assert "left untouched" in out["note"]
    assert _settings(home).read_bytes() == before


def test_a_wrongly_shaped_key_is_left_untouched(home: Path) -> None:
    _write(_settings(home), {"enabledMcpjsonServers": "osiris"})
    before = _settings(home).read_bytes()

    out = mcp_approval.ensure_mcp_approval()

    assert out["ok"] is False and out["changed"] is False
    assert _settings(home).read_bytes() == before


def test_an_explicit_disable_is_respected(home: Path) -> None:
    _write(_settings(home), {"disabledMcpjsonServers": ["osiris"]})
    before = _settings(home).read_bytes()

    out = mcp_approval.ensure_mcp_approval()

    assert out["changed"] is False
    assert "explicit choice" in out["note"]
    assert _settings(home).read_bytes() == before


def test_the_files_permissions_are_preserved(home: Path) -> None:
    _write(_settings(home), {"model": "x"})
    _settings(home).chmod(0o600)

    mcp_approval.ensure_mcp_approval()

    assert oct(_settings(home).stat().st_mode & 0o777) == "0o600"


def test_user_scope_registration_is_read_from_the_cli_owned_file_and_never_written(
    home: Path,
) -> None:
    claude_json = home / ".claude.json"
    assert mcp_approval.user_scope_registered() is False
    claude_json.write_text(json.dumps({"mcpServers": {"other": {}}}))
    assert mcp_approval.user_scope_registered() is False
    claude_json.write_text(json.dumps({"mcpServers": {"osiris": {"type": "http"}}}))
    before = claude_json.read_bytes()
    assert mcp_approval.user_scope_registered() is True
    claude_json.write_text("not json")
    assert mcp_approval.user_scope_registered() is False
    claude_json.write_bytes(before)
    mcp_approval.ensure_mcp_approval()
    assert claude_json.read_bytes() == before


def test_the_deploy_line_says_what_changed_and_nudges_when_not_registered_at_user_scope(
    home: Path,
) -> None:
    line = mcp_approval.approval_line()
    assert line.startswith("mcp approval: approved osiris for every project")
    assert "claude mcp add --scope user" in line  # not registered: the one-liner is named

    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"osiris": {}}}))
    quiet = mcp_approval.approval_line()
    assert "already approved" in quiet
    assert "claude mcp add" not in quiet


def test_the_real_home_is_never_touched_by_the_suite() -> None:
    assert os.environ["OSIRIS_CLAUDE_HOME"] != str(Path.home())
    assert mcp_approval._settings_path().is_relative_to(Path(os.environ["OSIRIS_CLAUDE_HOME"]))


# --- onboarding -----------------------------------------------------------------------------


def test_onboarding_writes_no_mcp_json_when_osiris_is_already_registered_at_user_scope(
    home: Path, tmp_path: Path,
) -> None:
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"osiris": {}}}))
    repo = tmp_path / "repo"
    repo.mkdir()

    result = onboard(repo, osiris_home=tmp_path)

    assert not (repo / ".mcp.json").exists()
    assert "already registered" in result["checklist"]


def test_repo_pinned_still_writes_one_even_when_registered(home: Path, tmp_path: Path) -> None:
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"osiris": {}}}))
    repo = tmp_path / "repo"
    repo.mkdir()

    onboard(repo, repo_pinned=True, osiris_home=tmp_path)

    assert (repo / ".mcp.json").exists()


def test_onboarding_still_writes_one_when_osiris_is_not_registered(
    home: Path, tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    onboard(repo, osiris_home=tmp_path)

    assert (repo / ".mcp.json").exists()
