"""triggers: a UNIQUE constraint on helper_id (thread <console-startup-blocks-on-backup>,
Thoth mail 10214) — the substrate `project_triggers`'s new DELETE+UPSERT rebuild (replacing
TRUNCATE, which needed ACCESS EXCLUSIVE and blocked console startup behind a concurrent
pg_dump's own lock on this table) needs to target with `ON CONFLICT (helper_id)`.

LOCK SAFETY (Practice 513326d6): `triggers` holds one row per helper manifest — tens, not
millions — so a plain `ALTER TABLE ... ADD CONSTRAINT ... UNIQUE` (which validates existing
rows under an ACCESS EXCLUSIVE lock for the statement's own near-instant duration at this
size) is the right tool here, not a CREATE INDEX CONCURRENTLY dance sized for a table this
migration will never see. No backfill: any accidental duplicate helper_id would refuse the
migration outright rather than silently pick a survivor — checked against the live table
before writing this (none exist; every manifest's own `id` is already unique by construction,
`load_manifests`'s own duplicate-id guard).

Revision ID: 0068
Revises: 0067
"""
from __future__ import annotations

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE triggers ADD CONSTRAINT triggers_helper_id_key UNIQUE (helper_id)")


def downgrade() -> None:
    op.execute("ALTER TABLE triggers DROP CONSTRAINT triggers_helper_id_key")
