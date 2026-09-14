"""PROVENANCE BACKFILL (thread e332177f, wave 24 dispatch — Thoth mail 10440,
operator's word "dispatch everything to them", 2026-09-14): the three provenance legs
(piece 1 possible_upstream-at-the-door, piece 2 session-miner tool_result scan, piece 3
credence surfacing) are forward-only — a write made before each piece went live carries
no possible_upstream edges at all, not because it had no reads behind it but because
nothing was stamping them yet. This backfills HISTORICAL agent writes (Decision/Thread)
exactly, never a text-similarity guess, by finding each write's own RECEIPT inside its
writer's own transcript and re-running PIECE 2's own detector over what preceded it.

ONE DETECTOR, NOT A SECOND REGEX PATH: `_upstream_targets`/`_tool_result_texts_before`
(src/ingest/sessions.py) are imported and reused verbatim — this module only supplies
the piece the miner's own live path never needed: locating WHERE in a given transcript a
past write's receipt landed, and WHO gets named as the edge's source (the write's own
recorded actor, never `session-miner`).

FINDS EACH WRITE BY RECEIPT, structurally: a target Decision/Thread's own `canonical`
string is exactly what record_decision/open_thread hand back in their tool_result — the
SAME `"canonical": "<type>:<hex>"` shape `_TOOL_CANONICAL_RE` already parses on the
READING side of piece 2. Scanning forward through the writer's own transcript for the
first `user`-type line whose tool_result content contains that exact string locates the
write's own receipt line; everything the SAME window before it produced is the candidate
upstream set, identical in shape to the live path's own scan.

WHICH TRANSCRIPT: the writer Agent's own `anchor_sid:*` ledger (current_assertions,
EXACT canonical only — never lineage-widened; a decision demonstrably came from THIS
generation's own hand, and widening would risk crediting a sibling generation's reads to
this one) resolved against the disk transcript index `orchestrator.mounts._transcript_index`
already maintains (sid -> Path) rather than re-plumbing a soul-store reader — this backfill
is a rare, deliberate, dry-run-gated act, not a hot liveness check the way that index's
other caller is.

DOOR NAMING: the thread's own spec asks for `{door: 'backfill:<tool>', read_at}`.
`<tool>` here names the DETECTION METHOD (message id / canonical / cite / url — the same
four piece 2's `_upstream_targets` already distinguishes), prefixed `backfill:` so a
credence read can tell a backfilled edge from a live-path one at a glance. Flagged as a
judgment call, not a certainty: the alternative reading (`<tool>` naming the WRITING
verb, e.g. `backfill:record_decision`) was equally plausible from the thread text alone;
this module picks the reading that preserves piece 2's own finer-grained door taxonomy
rather than collapsing it, and names the choice here for review.

DRY RUN IS THE DEFAULT (restore_attribution's own mould, thread 3f7969a3). `dry_run=False`
refuses a blank `because` — mutating historical provenance is a deliberate act on the
record, same law. Idempotent: an existing possible_upstream edge on a candidate is left
alone, so a candidate is only ever examined until either it mints or every one of its
writer's ledger sids has been tried with no receipt found — safe to re-run.

UNRECOVERABLE, NAMED NOT GUESSED (`skipped`): a writer with no anchor_sid ledger; every
ledger sid resolving to a since-pruned or never-indexed file; a receipt line never found
in any of them (mined by session-miner before a durable mount, or otherwise genuinely
unrecoverable, per the thread's own "unrecoverable only where transcripts were pruned").
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from src.actions.core import Actions
from src.parsers.base import EvidenceClass
from src.parsers.evidence import confidence_for

_TARGET_TYPES = ("Decision", "Thread")
_WINDOW = 6
_DEFAULT_LIMIT = 200
_MAX_SIDS_PER_WRITER = 25  # mirrors mounts.MAX_ANCHOR_SIDS_FOR_LIVENESS_CHECK's own cap
_EC = EvidenceClass.DERIVED.value  # a transcript text scan is an inference, same as piece 2
_CONF = confidence_for(EvidenceClass.DERIVED)


async def _candidates(
    pool: asyncpg.Pool, limit: int, *, newest_first: bool = False,
) -> list[asyncpg.Record]:
    """Every Decision/Thread with an agent:-prefixed writer and NO possible_upstream
    out-edge yet — the live-path gap this backfill exists to close. Oldest first by
    default (the writes most likely to predate piece 1's own live wiring); `newest_first`
    (thread e332177f, Thoth's own follow-on, msg 10525 — "the oldest-first default made
    the sample blind: pre-ledger generations can never match") flips the order to sample
    the writers MOST likely to carry a live anchor_sid ledger instead, since the ledger is
    itself a recent mechanism — the two orders answer different questions and neither
    subsumes the other over one bounded `limit`."""
    order = "DESC" if newest_first else "ASC"
    return await pool.fetch(  # type: ignore[no-any-return]
        "SELECT o.id, o.canonical, o.type, a.source_id AS writer, o.created_at "
        "FROM objects o "
        "JOIN current_assertions a ON a.object_id = o.id AND a.name = 'summary' "
        "WHERE o.type = ANY($1) AND a.source_id LIKE 'agent:%' "
        "AND NOT EXISTS (SELECT 1 FROM links l WHERE l.from_id = o.id "
        "AND l.type = 'possible_upstream') "
        f"ORDER BY o.created_at {order} LIMIT $2",
        list(_TARGET_TYPES), limit)


async def _anchor_sids(pool: asyncpg.Pool, agent_id: str) -> list[str]:
    """This EXACT agent generation's own session ids, freshest first — never a lineage-
    widened read (see module docstring)."""
    rows = await pool.fetch(
        "SELECT a.value #>> '{}' AS sid FROM current_assertions a "
        "JOIN objects o ON o.id = a.object_id "
        "WHERE o.type = 'Agent' AND o.canonical = $1 AND a.name LIKE 'anchor_sid:%' "
        "ORDER BY a.observed_at DESC LIMIT $2",
        agent_id, _MAX_SIDS_PER_WRITER)
    return [str(r["sid"]) for r in rows if r["sid"]]


def _find_receipt_line(lines: list[str], canonical: str) -> int | None:
    """The first `user`-type line (forward scan — a write's receipt is the earliest tool
    result naming it in its own writer's transcript) whose tool_result content contains
    this object's own `canonical` string verbatim. Reuses `_tool_result_texts_before`
    (piece 2's own block-extraction, `window=1` from the line right after `idx`) rather
    than re-parsing tool_result content a second way — a raw-line substring check would
    miss a match hiding behind the outer JSONL's own escaping of the inner tool_result
    text. None when no line in `lines` does."""
    from src.ingest.sessions import _tool_result_texts_before

    for idx in range(len(lines)):
        texts = _tool_result_texts_before(lines, idx + 1, window=1)
        if texts and canonical in texts[0]:
            return idx
    return None


async def backfill_possible_upstream(
    actions: Actions, *, dry_run: bool = True, because: str | None = None,
    limit: int = _DEFAULT_LIMIT, newest_first: bool = False, window: int = _WINDOW,
    transcript_root: Path | None = None,
) -> dict[str, Any]:
    """`transcript_root` defaults to `get_settings().osiris_transcripts` — overridable so
    a test (or an operator pointing at an archived tree) never depends on the live
    fleet's own configured root.

    THE RECEIPT'S OWN SUMMARY (thread e332177f, Thoth msg 10525): `summary.candidates`/
    `summary.writers` classify every candidate/writer examined into exactly one of
    `matched` (a receipt was found — this writer's transcript IS reachable, whether or
    not that receipt's own preceding window produced any upstream targets),
    `no_ledger` (the writer carries no `anchor_sid` assertion at all — never attempted a
    transcript read), or `no_transcript` (a ledger exists but no sid in it resolved to a
    receipt for this write). A writer with a ledger who matches on ONE candidate and
    misses on another counts as `matched` at the writer level — the ledger is proven
    reachable, so the miss is that specific write's own receipt, not the writer's
    transcript access. `runtime_seconds` times the whole call, wall-clock."""
    if not dry_run and not (because or "").strip():
        return {"error": "backfilling historical provenance without a because is an "
                         "un-audited graph write — cite the ruling/dispatch that "
                         "authorizes it (thread e332177f, Thoth mail 10440), never silent"}

    import asyncio
    import time

    from src.ingest.sessions import _upstream_targets
    from src.ontology.canonicalize import canonicalize
    from src.orchestrator.mounts import _transcript_index

    started = time.monotonic()

    if transcript_root is None:
        from src.config.settings import get_settings

        root_str = get_settings().osiris_transcripts
        transcript_root = Path(root_str) if root_str else None
    index = (await asyncio.to_thread(_transcript_index, transcript_root)
             if transcript_root is not None else {})

    plan: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    file_cache: dict[Path, list[str]] = {}
    examined = 0
    candidate_tally = {"matched": 0, "no_ledger": 0, "no_transcript": 0}
    writer_outcomes: dict[str, set[str]] = {}

    def _tag(writer: str, outcome: str) -> None:
        candidate_tally[outcome] += 1
        writer_outcomes.setdefault(writer, set()).add(outcome)

    for cand in await _candidates(actions.pool, limit, newest_first=newest_first):
        examined += 1
        writer = cand["writer"]
        sids = await _anchor_sids(actions.pool, writer)
        if not sids:
            skipped.append({"object": cand["canonical"], "writer": writer,
                            "reason": "writer carries no anchor_sid ledger"})
            _tag(writer, "no_ledger")
            continue
        receipt_line: int | None = None
        receipt_lines: list[str] | None = None
        for sid in sids:
            path = index.get(sid)
            if path is None:
                continue
            lines = file_cache.get(path)
            if lines is None:
                try:
                    text = await asyncio.to_thread(path.read_text, errors="replace")
                except OSError:
                    continue
                lines = text.splitlines()
                file_cache[path] = lines
            found = _find_receipt_line(lines, cand["canonical"])
            if found is not None:
                receipt_line, receipt_lines = found, lines
                break
        if receipt_line is None or receipt_lines is None:
            skipped.append({"object": cand["canonical"], "writer": writer,
                            "reason": "no receipt for this write found in any of the "
                                     f"writer's {len(sids)} indexed transcript(s)"})
            _tag(writer, "no_transcript")
            continue
        _tag(writer, "matched")

        for target, props in await _upstream_targets(
            actions.pool, receipt_lines, receipt_line, window=window,
        ):
            is_url = bool(props.pop("_is_url", False))
            method = props.get("door", "unknown").rsplit(":", 1)[-1]
            door = f"backfill:{method}"
            if is_url:
                to_key = str(target)
                already = False
            else:
                assert isinstance(target, uuid.UUID)
                to_key = str(target)
                already = bool(await actions.pool.fetchval(
                    "SELECT 1 FROM links WHERE from_id=$1 AND to_id=$2 "
                    "AND type='possible_upstream'", cand["id"], target))
            if already:
                continue
            plan.append({
                "from": cand["canonical"], "from_id": str(cand["id"]), "writer": writer,
                "to": to_key, "is_url": is_url, "door": door,
            })

    edges_by_door: dict[str, int] = {}
    for item in plan:
        edges_by_door[item["door"]] = edges_by_door.get(item["door"], 0) + 1

    writer_tally = {"matched": 0, "no_ledger": 0, "no_transcript": 0}
    for outcomes in writer_outcomes.values():
        if "no_ledger" in outcomes:
            writer_tally["no_ledger"] += 1
        elif "matched" in outcomes:
            writer_tally["matched"] += 1
        else:
            writer_tally["no_transcript"] += 1

    report: dict[str, Any] = {
        "dry_run": dry_run, "candidates_examined": examined, "newest_first": newest_first,
        "edges_to_mint": len(plan), "edges_by_door": edges_by_door, "plan": plan,
        "skipped_count": len(skipped), "skipped": skipped,
        "summary": {"candidates": candidate_tally, "writers": writer_tally},
        "runtime_seconds": round(time.monotonic() - started, 3),
    }
    if dry_run or not plan:
        return report

    now = datetime.now(UTC)
    minted = 0
    for item in plan:
        from_id = uuid.UUID(item["from_id"])
        if item["is_url"]:
            to_id = await actions.create_or_find_object(
                "URL", canonicalize("URL", item["to"]), item["writer"])
        else:
            to_id = uuid.UUID(item["to"])
        exists = await actions.pool.fetchval(
            "SELECT 1 FROM links WHERE from_id=$1 AND to_id=$2 AND type='possible_upstream'",
            from_id, to_id)
        if exists:
            continue
        await actions.create_link(
            from_id, to_id, "possible_upstream", item["writer"], now, _CONF,
            evidence_class=_EC, properties={"door": item["door"], "read_at": now.isoformat()})
        minted += 1
    report.update({"minted": minted, "because": because,
                   "runtime_seconds": round(time.monotonic() - started, 3)})
    return report
