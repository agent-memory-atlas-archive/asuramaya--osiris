"""APPROVE THE OSIRIS MCP SERVER ONCE, FOR EVERY DIRECTORY: Claude Code asks the user to approve
a project's `.mcp.json` servers per directory (the answer lives in that directory's own
`.claude/settings.local.json`). This repo commits a `.mcp.json`, so every git worktree and every
onboarded repo is a new, unapproved project and the prompt comes back again and again.

The user-level settings file (`~/.claude/settings.json`) has a key for exactly this,
`enabledMcpjsonServers`: names listed there are approved from any `.mcp.json`, in any directory.
`ensure_mcp_approval` makes sure `osiris` is in it. It runs from `osiris deploy`, the same place
that installs the slash commands and hooks.

MERGE, NEVER CLOBBER: every other key (and every other approved server) is preserved as is. A
settings file that is not valid JSON is left untouched and reported, never overwritten. A user's
explicit `disabledMcpjsonServers` entry for osiris is reported and left alone: that is a choice
they made, not a default to override. Idempotent: an already-approved box is byte-for-byte
unchanged."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SERVER = "osiris"


def _home() -> Path:
    """The directory whose `.claude/` and `.claude.json` are meant. `OSIRIS_CLAUDE_HOME`
    stands in for HOME so a test (or a second Claude profile) can point elsewhere; the real
    default is the user's own home."""
    return Path(os.environ.get("OSIRIS_CLAUDE_HOME") or Path.home())


def _settings_path() -> Path:
    return _home() / ".claude" / "settings.json"


def _claude_json_path() -> Path:
    return _home() / ".claude.json"


def user_scope_registered() -> bool:
    """Whether osiris is registered at USER scope (`claude mcp add --scope user`), which the
    Claude CLI keeps in `~/.claude.json` and which needs no per-directory approval at all.
    Read only; that file belongs to the CLI and is never written here."""
    try:
        servers = json.loads(_claude_json_path().read_text()).get("mcpServers") or {}
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(servers, dict) and SERVER in servers


def _indent_of(text: str) -> int:
    """The file's own indentation (2 by default), so a merge does not reformat the user's file."""
    for line in text.splitlines():
        stripped = line.lstrip(" ")
        if stripped.startswith('"') and len(line) - len(stripped) > 0:
            return len(line) - len(stripped)
    return 2


def ensure_mcp_approval(*, settings_path: Path | None = None) -> dict[str, Any]:
    """Returns `{"changed": bool, "note": str, ...}`; never raises for a bad file."""
    path = settings_path or _settings_path()
    indent = 2
    data: dict[str, Any] = {}
    if path.exists():
        try:
            text = path.read_text()
            data = json.loads(text) if text.strip() else {}
            indent = _indent_of(text)
        except (OSError, ValueError) as exc:
            return {"changed": False, "ok": False, "path": str(path),
                    "note": f"{path} is not valid JSON ({exc}); left untouched. Add "
                            f'"enabledMcpjsonServers": ["{SERVER}"] to it by hand'}
        if not isinstance(data, dict):
            return {"changed": False, "ok": False, "path": str(path),
                    "note": f"{path} is not a JSON object; left untouched"}
    enabled = data.get("enabledMcpjsonServers")
    if enabled is not None and not isinstance(enabled, list):
        return {"changed": False, "ok": False, "path": str(path),
                "note": f"enabledMcpjsonServers in {path} is not a list; left untouched"}
    out: dict[str, Any] = {"ok": True, "path": str(path), "changed": False}
    disabled = data.get("disabledMcpjsonServers")
    if isinstance(disabled, list) and SERVER in disabled:
        out["note"] = (f"{SERVER} is listed in disabledMcpjsonServers in {path}: that is an "
                       "explicit choice, so it was left alone and approval was not added")
        return out
    if enabled is not None and SERVER in enabled:
        out["note"] = f"{SERVER} already approved for every project ({path})"
        return out
    data["enabledMcpjsonServers"] = [*(enabled or []), SERVER]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=indent, ensure_ascii=False) + "\n")
    if path.exists():
        tmp.chmod(path.stat().st_mode & 0o777)
    tmp.replace(path)
    out["changed"] = True
    out["note"] = (f"approved {SERVER} for every project: added it to "
                   f"enabledMcpjsonServers in {path}")
    return out


def approval_line() -> str:
    """The one-line report `osiris deploy` prints."""
    from src.orchestrator.onboard import _USER_SCOPE_CMD

    result = ensure_mcp_approval()
    line = f"mcp approval: {result['note']}"
    if not user_scope_registered():
        line += ("\nNOTE: osiris is not registered at user scope; run `" + _USER_SCOPE_CMD
                 + "` once so every project reaches it without a per-repo file")
    return line
