"""render_units — generalizes render_backup_timers.py's own pattern (Wave 21, thread
f04cce36 piece 3) past the 5 backup-lane timers to every unit-backed setting WAVE 22
(ruling 7be61879, thread 40d6eef3) registers: MemoryMax for the four persistent dev-box
daemons, osiris-pulse's own --watch interval, the console's --host/--port, and
osiris-pg-autotune.timer's own schedule (osiris-preflight.timer was already covered —
it's one of BACKUP_TIMER_UNITS from piece 3).

SAME FAILS-OPEN DISCIPLINE render_backup_timers.py established: a settings-read hiccup
degrades every unit to its shipped default, printed to stderr, never raised. A key with
no configured override (its stored value equal to its own spec default) renders BYTE-
IDENTICAL to the shipped file — every substitution function below is written to be a true
no-op when handed the value already baked into the file it touches, so "unset" falls out
of matching defaults rather than a sentinel each caller has to special-case.

ONE SUBSTITUTION FUNCTION PER SHAPE, not a generic templating engine — same "each file
passes through render, never re-templated wholesale" discipline the backup lane already
used: `_sub_oncalendar` (the timer lane, reused verbatim), `_sub_memory_max` (in-place
replace when a MemoryMax= line exists, insert one before [Install] when adding a
genuinely new cap, drop the line when the value is empty), and two CLI-arg regex
substitutions for `deploy/user/osiris-{pulse,console}.service`'s own ExecStart= line."""
from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _sub_oncalendar(text: str, value: Any) -> str:
    if not value:
        return text
    return "\n".join(
        f"OnCalendar={value}" if line.startswith("OnCalendar=") else line
        for line in text.splitlines()
    ) + "\n"


def _sub_memory_max(text: str, value: Any) -> str:
    value = (value or "").strip()
    lines = text.splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith("MemoryMax="):
            replaced = True
            if value:
                out.append(f"MemoryMax={value}")
            # else: drop the line entirely — an explicit "no cap".
        else:
            out.append(line)
    if value and not replaced:
        idx = next((i for i, ln in enumerate(out) if ln.strip() == "[Install]"), len(out))
        out.insert(idx, f"MemoryMax={value}")
    return "\n".join(out) + "\n"


def _sub_watch_arg(text: str, value: Any) -> str:
    if value is None:
        return text
    return re.sub(r"--watch \d+", f"--watch {int(value)}", text)


def _sub_console_host(text: str, value: Any) -> str:
    if value is None:
        return text
    return re.sub(r"--host \S+", f"--host {value}", text)


def _sub_console_port(text: str, value: Any) -> str:
    if value is None:
        return text
    return re.sub(r"--port \d+", f"--port {int(value)}", text)


# timer unit (relative to deploy/) -> the settings key controlling its OnCalendar= line.
# The matching .service copies across byte-for-byte, same as render_backup_timers.py.
_TIMER_SCHEDULE_KEYS: dict[str, str] = {}


def _timer_schedule_keys() -> dict[str, str]:
    if not _TIMER_SCHEDULE_KEYS:
        from src.config.settings_registry import BACKUP_TIMER_UNITS

        for unit in BACKUP_TIMER_UNITS:
            _TIMER_SCHEDULE_KEYS[unit] = f"backup.timer_schedule.{unit}"
        _TIMER_SCHEDULE_KEYS["osiris-pg-autotune.timer"] = "daemon.osiris_pg_autotune.schedule"
    return _TIMER_SCHEDULE_KEYS


# deploy/user/<name>.service -> the (key, substitution) pairs applied to it, in order.
_DAEMON_SERVICE_SUBS: dict[str, list[tuple[str, Callable[[str, Any], str]]]] = {
    "osiris-mcp.service": [("daemon.osiris_mcp.memory_max", _sub_memory_max)],
    "osiris-worker.service": [("daemon.osiris_worker.memory_max", _sub_memory_max)],
    "osiris-pulse.service": [
        ("daemon.osiris_pulse.memory_max", _sub_memory_max),
        ("daemon.osiris_pulse.watch_interval_secs", _sub_watch_arg),
    ],
    "osiris-console.service": [
        ("daemon.osiris_console.memory_max", _sub_memory_max),
        ("daemon.osiris_console.host", _sub_console_host),
        ("daemon.osiris_console.port", _sub_console_port),
    ],
}


async def _configured_values() -> dict[str, Any]:
    from src.config.settings import get_settings
    from src.db.pool import create_pool
    from src.orchestrator.settings_service import list_settings

    settings = get_settings()
    pool = await create_pool(settings.database_url, min_size=1, max_size=1,
                             application_name="osiris-script:render-units")
    try:
        items = await list_settings(pool)
        return {item["key"]: item["value"] for item in items}
    finally:
        await pool.close()


def render(deploy_dir: Path, out_dir: Path, values: dict[str, Any]) -> int:
    """Pure — no DB, no network. `values` is every registered setting's own current value
    (its spec default when unset, `settings_service.list_settings`'s own shape). Returns
    how many rendered files differ from their own shipped source."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = 0

    for unit, key in _timer_schedule_keys().items():
        src = deploy_dir / unit
        if not src.is_file():
            continue
        text = src.read_text()
        new_text = _sub_oncalendar(text, values.get(key))
        if new_text != text:
            rendered += 1
        (out_dir / unit).write_text(new_text)
        service_name = unit.removesuffix(".timer") + ".service"
        service = deploy_dir / service_name
        if service.is_file():
            shutil.copy2(service, out_dir / service_name)

    user_dir = deploy_dir / "user"
    if user_dir.is_dir():
        out_user_dir = out_dir / "user"
        out_user_dir.mkdir(parents=True, exist_ok=True)
        for name, subs in _DAEMON_SERVICE_SUBS.items():
            src = user_dir / name
            if not src.is_file():
                continue
            text = src.read_text()
            new_text = text
            for key, fn in subs:
                new_text = fn(new_text, values.get(key))
            if new_text != text:
                rendered += 1
            (out_user_dir / name).write_text(new_text)

    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        values = asyncio.run(_configured_values())
    except Exception as exc:  # noqa: BLE001 — see module docstring: fails open
        print(f"render_units: settings unavailable ({exc}) — using shipped defaults "
              "for every unit", file=sys.stderr)
        values = {}

    rendered = render(args.deploy_dir, args.out, values)
    print(f"render_units: {rendered} unit(s) carry a configured override, written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
