"""session_reads: a durable per-agent read-set log (PROVENANCE PIECE 1, thread
bc6a5d455da2's sibling wave — thread da545039f2ba, ruling bb3e4422 "provenance by
channel, not by text").

The MCP server stamps one row here every time a session reads an id through a
tracked door (inbox lease/peek, recall, dossier, search/graph_search hit,
read_citation, ingest_reference) — `object_id` always a real `objects.id` (mail
reads resolve the ALREADY-EXISTING Message object mailbox.py mints at send time,
never a fresh mint from the read side — the same existence-checked law that
module's own send() path already holds itself to). At write time
(record_decision/open_thread/capture assertions/thread annotate), every object a
session touches gets `possible_upstream` links (an EXISTING `links.type` value,
no schema change needed there — `properties`/`source_id` already carry door+time
and the writing agent) to this session's own preceding read-set — credence.py's
second independence leg.

DURABLE, not in-process memory: the MCP server restarts multiple times an hour in
observed practice, and an in-memory read-set would lose most of its coverage to
routine restarts.

Revision ID: 0069
Revises: 0068
"""
from __future__ import annotations

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE session_reads (
            id         bigserial PRIMARY KEY,
            agent_id   text NOT NULL,
            door       text NOT NULL,
            object_id  uuid NOT NULL REFERENCES objects(id),
            read_at    timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX session_reads_agent_read_idx ON session_reads (agent_id, read_at)")


def downgrade() -> None:
    op.execute("DROP TABLE session_reads")
