"""osiris's slash commands never share a name with a Claude Code built-in.

commands/*.md (and deploy/commands/*.md) install into ~/.claude/commands/, the same
namespace Claude Code's own built-ins and aliases live in. /resume, /status and /stop
collided with Claude Code 2.1.284; they were retired (resume/stop fold into `/seat`,
status became `/osiris`). commands/RESERVED_NAMES.txt is the vendored built-in list
(scripts/refresh_reserved_names.py regenerates it from the installed binary); this file
is the gate that keeps a new collision from shipping.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from scripts.refresh_reserved_names import extract_names, read_reserved

ROOT = Path(__file__).resolve().parent.parent
COMMAND_DIRS = [ROOT / "commands", ROOT / "deploy" / "commands"]
INSTALLER = ROOT / "scripts" / "install_commands.sh"
RETIRED_NAMES = ("resume", "status", "stop")


def _known_collisions() -> set[str]:
    return read_reserved(ROOT / "commands" / "KNOWN_COLLISIONS.txt")


def _stems() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for d in COMMAND_DIRS:
        for p in sorted(d.glob("*.md")):
            out.setdefault(p.stem, p)
    return out


def test_reserved_list_is_loaded_and_holds_the_known_built_ins() -> None:
    reserved = read_reserved()
    assert len(reserved) > 100, "commands/RESERVED_NAMES.txt looks truncated"
    # the three that actually collided, plus aliases the original sweep called out
    for name in (*RETIRED_NAMES, "cost", "stats", "restart", "remote", "name", "peers",
                 "help", "clear", "config", "settings"):
        assert name in reserved, f"{name!r} missing from RESERVED_NAMES.txt"


def test_no_slash_command_name_is_a_claude_code_built_in() -> None:
    reserved = read_reserved()
    stems = _stems()
    collisions = {s for s in stems if s in reserved}
    new = sorted(collisions - _known_collisions())
    assert not new, (
        "these slash commands share a name with a Claude Code built-in command or alias "
        "(commands/RESERVED_NAMES.txt) and would shadow it or be shadowed by it: "
        + ", ".join(f"/{s} ({stems[s].relative_to(ROOT)})" for s in new))


def test_known_collisions_only_shrink() -> None:
    """KNOWN_COLLISIONS.txt must name exactly the live collisions: an entry whose
    command was renamed (or whose name Claude Code dropped) comes out in the same commit."""
    reserved = read_reserved()
    live = {s for s in _stems() if s in reserved}
    stale = sorted(_known_collisions() - live)
    assert not stale, f"KNOWN_COLLISIONS.txt lists names that no longer collide: {stale}"


def test_retired_names_stay_retired() -> None:
    for d in COMMAND_DIRS:
        for name in RETIRED_NAMES:
            assert not (d / f"{name}.md").exists(), (
                f"{d.relative_to(ROOT)}/{name}.md is back; /{name} is a Claude Code built-in")


def _installer_retired_shas() -> set[tuple[str, str]]:
    text = INSTALLER.read_text()
    block = text.split("RETIRED='", 1)[1].split("'", 1)[0]
    return {(a, b) for a, b in (line.split() for line in block.splitlines() if line.strip())}


def test_installer_knows_every_version_osiris_ever_shipped() -> None:
    """The installer only removes a retired file whose bytes match a version osiris wrote.
    Every revision git holds for commands/<retired>.md must be in its list, or a machine
    still carrying that revision keeps a shadowing copy forever."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    known = _installer_retired_shas()
    missing: list[str] = []
    for name in RETIRED_NAMES:
        path = f"commands/{name}.md"
        revs = subprocess.run(
            ["git", "log", "--all", "--format=%H", "--", path], cwd=ROOT,
            capture_output=True, text=True, check=False, timeout=60).stdout.split()
        for rev in revs:
            shown = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT,
                                   capture_output=True, check=False, timeout=60)
            if shown.returncode != 0:  # the deleting commit itself
                continue
            sha = hashlib.sha256(shown.stdout).hexdigest()
            if (f"{name}.md", sha) not in known:
                missing.append(f"{name}.md@{rev[:8]} {sha}")
    assert not missing, f"install_commands.sh RETIRED is missing: {missing}"


def _synthetic_repo(tmp_path: Path, commands: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    (repo / "commands").mkdir(parents=True)
    (repo / "scripts").mkdir()
    for name, body in commands.items():
        (repo / "commands" / name).write_text(body)
    for extra in ("RESERVED_NAMES.txt", "KNOWN_COLLISIONS.txt"):
        shutil.copy(ROOT / "commands" / extra, repo / "commands" / extra)
    shutil.copy(INSTALLER, repo / "scripts" / "install_commands.sh")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True,
                   timeout=60)
    return repo


def _install(repo: Path, target: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CLAUDE_COMMANDS_DIR": str(target)}
    return subprocess.run(["sh", "scripts/install_commands.sh"], cwd=repo, env=env,
                          capture_output=True, text=True, check=False, timeout=60)


def test_installer_refuses_a_reserved_name(tmp_path: Path) -> None:
    repo = _synthetic_repo(tmp_path, {"seat.md": "seat doc\n", "help.md": "mine\n"})
    target = tmp_path / "target"
    out = _install(repo, target)
    assert out.returncode == 1
    assert "REFUSED help.md" in out.stderr and "/help" in out.stderr
    assert not (target / "help.md").exists()
    assert (target / "seat.md").read_text() == "seat doc\n"


def _last_shipped(path: str) -> bytes | None:
    """The newest bytes git holds for `path`, skipping the commit that deleted it."""
    revs = subprocess.run(["git", "log", "--all", "--format=%H", "--", path], cwd=ROOT,
                          capture_output=True, text=True, check=False, timeout=60).stdout.split()
    for rev in revs:
        shown = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT,
                               capture_output=True, check=False, timeout=60)
        if shown.returncode == 0:
            return shown.stdout
    return None


def test_installer_retires_only_osiris_authored_copies(tmp_path: Path) -> None:
    repo = _synthetic_repo(tmp_path, {"seat.md": "seat doc\n"})
    target = tmp_path / "target"
    target.mkdir()
    stop_body = _last_shipped("commands/stop.md")
    if stop_body is None:
        pytest.skip("no git history for commands/stop.md in this checkout")
    assert ("stop.md", hashlib.sha256(stop_body).hexdigest()) in _installer_retired_shas()
    (target / "stop.md").write_bytes(stop_body)
    (target / "status.md").write_text("somebody's own /status command\n")

    out = _install(repo, target)
    assert out.returncode == 0, out.stderr
    assert "retired stop.md" in out.stdout
    assert not (target / "stop.md").exists()
    assert "left status.md alone" in out.stdout
    assert (target / "status.md").read_text() == "somebody's own /status command\n"


def test_extraction_patterns_catch_every_command_object_shape() -> None:
    blob = (
        'name:"alpha",aliases:["al"],description:"x"'
        'type:"local-jsx",name:"beta",aliases:["be"],get description(){}'
        'name:"gamma",type:"local",description:"x"'
        'aliases:["de"],name:"delta",progressMessage:"x"'
        'ps({name:"epsilon",aliases:["ep"],isEnabled:()=>!0})'
    )
    assert extract_names(blob) == {"alpha", "al", "beta", "be", "gamma", "delta", "de",
                                   "epsilon", "ep"}
