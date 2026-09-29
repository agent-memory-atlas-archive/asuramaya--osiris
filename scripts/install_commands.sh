#!/bin/sh
# Installs the slash-command docs (commands/*.md, task #204's structural fix) into
# ~/.claude/commands/: the SAME mechanism install_gate_hook.sh already uses for the
# pre-commit hook: copy a tracked file from the repo to its machine-wide destination,
# idempotent, per-file compare, never touching anything this repo doesn't own.
#
# WHY THIS EXISTS: the slash-command surface used to live ONLY at
# ~/.claude/commands/*.md, outside every git repo entirely. The original reasoning was
# that no CI gate could see it there, so the parity gate had to read the live machine
# copy instead. That same property (a file the git diff being committed cannot contain)
# let one agent's unfinished edit fail every OTHER agent's pre-commit gate: THREE
# workers blocked at once on 2026-09-04, all on the same live seat.md race. The fix:
# commands/*.md is now the SOURCE OF TRUTH, tracked in the repo; this script is how it
# reaches the machine.
#
# IDEMPOTENT: re-running when every file is already installed and current is a silent
# no-op (exit 0, one confirming line per file). A file present on the machine but not
# in commands/ (a local, un-tracked slash command) is left alone: this only ever
# copies FROM the repo, never deletes anything it doesn't own.
#
# NO NAME COLLISIONS WITH CLAUDE CODE: a command whose name is in
# commands/RESERVED_NAMES.txt (Claude Code's built-in commands and aliases, refreshed by
# scripts/refresh_reserved_names.py) is REFUSED, named on stderr, and the run exits 1.
# There is no exception list: a colliding command gets renamed, never tolerated.
#
# RETIRED NAMES: /resume, /settings, /status and /stop used to ship here and shadowed
# Claude Code's own built-ins. RETIRED below lists every osiris-authored version of each (sha256 of
# every revision git ever held for commands/<name>). A retired file in the target is
# removed ONLY when its bytes hash to one of those versions; anything else under that
# name is somebody's own command and is left alone, with a line saying so.
set -eu

# name sha256, one per line: every version of the file osiris ever shipped.
RETIRED='
resume.md fe8787669183e5757c7ee1c1153d5266249cac91f7a0789925567010ffbe041a
status.md 3e22068bb7d014c9892df49ee8c90f318141a72837385710bafc0a8ac0fa00d1
status.md f28372f40b7a4bfa77f9d7526f5d240291c5a8352a908c7be4d5774c113b4466
stop.md b4a094d8eb2bb1f345491c808e803976ca53355da56ce7aa0ff9e45a6ff8a2af
settings.md be6a1ba293444243eb3dfde72ca574b1ca15f04b89cce86e681981c02bc996ed
'

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum < "$1" | cut -d' ' -f1
    else
        shasum -a 256 < "$1" | cut -d' ' -f1
    fi
}

# first word of every non-blank, non-comment line
names_in() {
    [ -f "$1" ] || return 0
    grep -v '^[[:space:]]*#' "$1" | awk 'NF { print $1 }'
}

TOPLEVEL="$(git rev-parse --show-toplevel)"
SOURCE_DIR="$TOPLEVEL/commands"
TARGET_DIR="${CLAUDE_COMMANDS_DIR:-$HOME/.claude/commands}"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "install_commands: SOURCE MISSING — $SOURCE_DIR not found. Nothing installed." >&2
    exit 1
fi

mkdir -p "$TARGET_DIR"

RESERVED_NAMES="$(names_in "$SOURCE_DIR/RESERVED_NAMES.txt")"

installed=0
current=0
refused=0
for src in "$SOURCE_DIR"/*.md; do
    [ -e "$src" ] || continue
    name="$(basename "$src")"
    target="$TARGET_DIR/$name"
    stem="${name%.md}"
    if printf '%s\n' "$RESERVED_NAMES" | grep -qxF "$stem"; then
        echo "install_commands: REFUSED $name: /$stem is a Claude Code built-in command" \
             "or alias (commands/RESERVED_NAMES.txt); rename it" >&2
        refused=$((refused + 1))
        continue
    fi
    if [ -f "$target" ] && cmp -s "$src" "$target"; then
        current=$((current + 1))
        continue
    fi
    cp "$src" "$target"
    installed=$((installed + 1))
done

retired=0
for name in $(printf '%s\n' "$RETIRED" | awk 'NF { print $1 }' | sort -u); do
    target="$TARGET_DIR/$name"
    [ -f "$target" ] || continue
    # a name brought back into commands/ is live again, never retired
    [ -e "$SOURCE_DIR/$name" ] && continue
    sum="$(sha256_of "$target")"
    if printf '%s\n' "$RETIRED" | grep -qxF "$name $sum"; then
        rm -f "$target"
        retired=$((retired + 1))
        echo "install_commands: retired $name (an osiris-authored version; /${name%.md} is a Claude Code built-in)"
    else
        echo "install_commands: left $name alone: not an osiris-authored version, so not ours to remove"
    fi
done

echo "install_commands: $installed installed/updated, $current already current, $retired retired — $TARGET_DIR"
if [ "$refused" -gt 0 ]; then
    echo "install_commands: $refused refused for a reserved name (see above)" >&2
    exit 1
fi
