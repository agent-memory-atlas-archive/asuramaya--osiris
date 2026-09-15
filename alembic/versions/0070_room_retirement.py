"""room_retirement: ROOM RETIREMENT (thread 96f09d48, decision 31717ca7, Thoth DM
10792) — the operator's own word on decision 31717ca7: "scope really died and made
itself obsolete over the last few updates... gotta remove that too." The `rooms`
dimension (a saved composition scoped to one of a small set of named "workspaces")
never developed past two rooms in practice ("engineer", "analyst") and the header's
own repo selector (console chrome cleanup part 2) now does the scoping job the room
concept was meant to do.

REVERSIBLE, NOT A DELETE (constitution point 3, event-sourced kernel): `former_room_id`
preserves exactly which room each of the 31 currently room-scoped compositions came
from — the list was posted on thread e3723060 before this migration ran. Unwinding is
one UPDATE (`UPDATE compositions SET room_id = former_room_id WHERE former_room_id IS
NOT NULL`), never a re-derivation from memory. The `rooms` table itself is NOT dropped
— it stays as read-only history (the two room ids `former_room_id` points at still
resolve to real names via a join, exactly like the note posted on e3723060 did).

Revision ID: 0070
Revises: 0069
"""
from __future__ import annotations

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE compositions ADD COLUMN former_room_id uuid REFERENCES rooms(id)")
    op.execute(
        "UPDATE compositions SET former_room_id = room_id, room_id = NULL "
        "WHERE room_id IS NOT NULL")


def downgrade() -> None:
    op.execute(
        "UPDATE compositions SET room_id = former_room_id WHERE former_room_id IS NOT NULL")
    op.execute("ALTER TABLE compositions DROP COLUMN former_room_id")
