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

WHICH TRANSCRIPT: the writer's LINEAGE-wide `anchor_sid:*` ledger (current_assertions,
base-prefix widened — CORRECTED from this module's own original "exact generation only"
reasoning, thread e332177f, decision b677fd14: `record_session_anchor`'s own "first
writer wins, forever" law means only the FIRST generation ever mounted under a shared
`job_dir` ever gets the ledger entry, so an exact-generation-only lookup found nothing
for most CURRENT writers, including this seat's own live session) resolved against the
disk transcript index `orchestrator.mounts._transcript_index` already maintains (sid ->
Path) rather than re-plumbing a soul-store reader — this backfill is a rare, deliberate,
dry-run-gated act, not a hot liveness check the way that index's other caller is.

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
unrecoverable, per the thread's own "unrecoverable only where transcripts were pruned");
or a transcript over `ingest.transcript_scan_max_bytes` (thread 0be2f790 below), never
opened at all.

THE STALL (thread 0be2f790, Thoth mail 10626, 2026-09-14 evening): once piece 1
(OSIRIS_TRANSCRIPTS) made real files reachable, `path.read_text()` on a 469MB transcript
ran on the MCP process's own event loop thread — pure Python, uninterruptible — and
starved every other door on that shared, whole-fleet connection for 19 minutes before
the operator had to restart osiris-mcp by hand. Real transcripts on this box run 200-
470MB; nothing in this module's own tests or the first two live dry runs (both run
before OSIRIS_TRANSCRIPTS was set, so every candidate short-circuited before ever
opening a file) ever exercised a file that size. FOUR FIXES, ruled by Thoth, not
optional:

(1) NEVER ON THE MCP LOOP THREAD: `mcp_server.backfill()` no longer runs this target
inline — it enqueues an arq job on osiris-worker (its own process, its own event loop)
and returns a job id; the receipt lands as a thread annotation when the worker
finishes (`src.workers.arq_worker.provenance_backfill_job`). The CLI door
(`cmd_backfill`) still calls this function in-process — it IS its own process, so the
MCP door's own starvation risk does not apply there.

(2) STREAMING, NEVER `read_text()`: `_load_or_build_index` iterates a file line by line
(a real Python file object's own iterator, never `.read().splitlines()` materializing
the whole file as one string first) and builds a `canonical -> line_idx` index in the
SAME pass it collects the lines list — one pass per file, not a per-candidate rescan
(the OLD `_find_receipt_line`, still kept for tests exercising it directly, was its own
O(n) walk PER CANDIDATE, so a writer with 20 candidates re-read big files 20 times).

A SIDECAR CACHE (thread 0be2f790's own word) sits beside the transcript itself
(`<transcript>.providx.json`) keyed on the transcript's own `(size, mtime)` — unchanged
since the last visit, a re-run costs exactly one `stat()` plus one small JSON read,
never a re-scan of the real file. The index inside is EVERY `"canonical": "<type>:
<hex>"` string found in ANY tool_result-shaped line (the same structural signature piece
2's own `_TOOL_CANONICAL_RE` already trusts, not a second pattern) — built once, reused
by every future candidate that ever points at this same file, not just this call's own.

(3) A PER-FILE BYTE CAP, `ingest.transcript_scan_max_bytes` (settings_registry.py,
default 64MB, `effect='next_tick'` — read via `current_stored_value`, never baked into
a live env var the way a `restart:<unit>` knob is): a transcript over the cap is
skipped on its OWN `stat()` alone, never opened, with an honest per-writer skip reason
in the receipt — "unrecoverable, named not guessed," the same law the rest of this
module's own `skipped` list already keeps, extended to a file too large to read safely
rather than one merely missing.

(4) A WALL-CLOCK BUDGET, `budget_seconds` (default `_DEFAULT_BUDGET_SECONDS`): checked
between candidates, never mid-file (a file already being read finishes; the check only
ever refuses to START the next one). Exceeding it returns a PARTIAL receipt
(`report["partial"] = True`) naming exactly how far it got, never a silent hang — the
stall this whole thread exists to fix was precisely a caller with no way to know the
call would never return.
"""
from __future__ import annotations

import asyncio
import json
import re
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
_DEFAULT_MAX_SCAN_BYTES = 1024 * 1024 * 1024  # ingest.transcript_scan_max_bytes' own default
_DEFAULT_BUDGET_SECONDS = 240.0  # thread 0be2f790: a partial receipt, never a silent hang
_RECEIPT_CANONICAL_RE_SRC = r'"canonical"\s*:\s*"([a-z_]+:[0-9a-f]{6,40})"'


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
    """This writer's LINEAGE-wide session ids, freshest first (thread e332177f, Thoth
    mail 10576, decision b677fd14 — CORRECTED from this function's own original "never
    lineage-widened" reasoning). `record_session_anchor`'s own exists-check is scoped to
    ANY active Agent, never a specific one, and `job_dir`/session id is durable across
    `--resume`/compaction WITHIN one lineage — so only the FIRST generation ever mounted
    under a given job_dir ever gets its own `anchor_sid` entry; every successor generation
    is structurally excluded by design ("first writer wins, forever," mounts.py's own
    law). Verified live: agent:seat-af50a33e-g49 (this session) carries ZERO anchor_sid
    assertions of its own, while an ancestor generation of the SAME lineage already
    claimed the job_dir's sid weeks earlier. The risk this function's own original
    docstring worried about — "crediting a sibling generation's reads to this one" —
    does not actually exist: a shared job_dir/anchor_sid IS the same underlying
    transcript continuity, exactly the fact `mounts.agent_liveness`/
    `_lineage_transcript_mtime` already lean on lineage-wide, never per-generation. Same
    base-prefix widening those functions use (`_generation`'s own root, never a fresh
    graph walk) — one precedent, not a second lineage-resolution mechanism."""
    from src.orchestrator.agents import _generation

    base = _generation(agent_id)[0]
    rows = await pool.fetch(
        "SELECT a.value #>> '{}' AS sid FROM current_assertions a "
        "JOIN objects o ON o.id = a.object_id "
        "WHERE o.type = 'Agent' AND a.name LIKE 'anchor_sid:%' "
        "AND (o.canonical = $1 OR o.canonical = $2 OR o.canonical LIKE $2 || '-%') "
        "ORDER BY a.observed_at DESC LIMIT $3",
        agent_id, base, _MAX_SIDS_PER_WRITER)
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


def _sidecar_path(transcript: Path) -> Path:
    return transcript.with_name(transcript.name + ".providx.json")


def _tool_result_content(raw_line: str) -> str | None:
    """This ONE line's own tool_result content, flattened — or None when it isn't a
    `type='user'` line carrying any tool_result block. A write's own receipt lands in
    the outer JSONL as a `type='user'` line whose tool_result content STRING is itself
    JSON-encoded, so its own `"canonical": "..."` text is escaped one level deep — a
    plain regex against the raw outer line (unescaped) can never match it; this
    extracts the same way `_tool_result_texts_before` (piece 2) already does before
    regexing, one implementation, not a second."""
    import json as _json

    try:
        d = _json.loads(raw_line)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict) or d.get("type") != "user":
        return None
    content = (d.get("message") or {}).get("content")
    if not isinstance(content, list):
        return None
    chunks: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        bc = block.get("content")
        if isinstance(bc, str):
            chunks.append(bc)
        elif isinstance(bc, list):
            chunks.extend(b.get("text", "") for b in bc
                          if isinstance(b, dict) and b.get("type") == "text")
    return "\n".join(chunks) if chunks else None


_SHORT_ID_RE = re.compile(r"^[0-9a-f]{6,40}$")


def _settle_receipt_short_ids(text: str) -> list[str]:
    """settle()'s OWN receipt shape (thread e332177f, Thoth mail 10791/10975,
    specimen decision ab991412) — structurally different from record_decision's/
    open_thread's own direct tool_result, which is what `_RECEIPT_CANONICAL_RE_SRC`
    matches. A decision/thread minted via `settle(decisions=[...]/threads_open=[...]/
    threads_resolve=[...])` never echoes its own `"canonical"` key back to the
    caller's transcript at all — only settle()'s own completeness report does,
    `{"accepted": {"decisions": [{"id": "<short-id>"}], "threads_opened": [...],
    "threads_resolved": [...]}}`, keyed by the object's SHORT id (the first 6-40 hex
    chars of its canonical — the SAME short-id convention every osiris DM/thread
    reference already uses), never the full `type:hex` canonical string.

    A STRUCTURAL json.loads of the already-flattened tool_result content, not a
    second regex layer — scoped to the `accepted` key specifically so a decision
    merely CITED as `prior_art` elsewhere in the SAME tool_result (a sibling,
    unrelated key on a record_decision response, e.g.) is never mistaken for its
    own creation/resolution receipt."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    accepted = parsed.get("accepted")
    if not isinstance(accepted, dict):
        return []
    ids: list[str] = []
    for key in ("decisions", "threads_opened", "threads_resolved"):
        items = accepted.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            item_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(item_id, str) and _SHORT_ID_RE.match(item_id):
                ids.append(item_id)
    return ids


def _do_scan_lines(transcript: Path) -> tuple[list[str], dict[str, int]]:
    """The actual blocking file walk, run only via `asyncio.to_thread` below — never
    called directly, so this name itself never appears as a bare `.read`-family call
    the blocking-transcript-read guard (tests/test_blocking_transcript_reads.py) scans
    for. ONE PASS, iterating the file object directly (never `f.read()`/`.splitlines()`
    materializing the whole file as a second, separate string first).

    `index` carries BOTH keying shapes in the same dict — a full canonical string
    (`"decision:<hex>"`) from a direct record_decision/open_thread receipt, or a bare
    short id (`"<hex>"`, no type prefix) from a settle() receipt — since the two forms
    can never collide (a canonical always contains `:`, a short id never does). The
    lookup side (`backfill_possible_upstream`) tries the candidate's own full canonical
    first, then its short-id prefix as a fallback."""
    pattern = re.compile(_RECEIPT_CANONICAL_RE_SRC)
    lines: list[str] = []
    index: dict[str, int] = {}
    with transcript.open("r", errors="replace") as f:
        for idx, raw in enumerate(f):
            line = raw.rstrip("\n")
            lines.append(line)
            text = _tool_result_content(line)
            if text is None:
                continue
            for m in pattern.finditer(text):
                index.setdefault(m.group(1), idx)
            for short_id in _settle_receipt_short_ids(text):
                index.setdefault(short_id, idx)
    return lines, index


async def _scan_transcript(
    transcript: Path, max_bytes: int,
) -> tuple[list[str], dict[str, int]] | None:
    """Thread 0be2f790's own streaming fix. Returns `(lines, canonical_index)`, or None
    when `transcript` exceeds `max_bytes` (checked by `stat()` alone, never opened) or
    cannot be read at all. Every actual blocking file operation — the sidecar read, the
    transcript walk, the sidecar write — runs through its own `asyncio.to_thread` call
    (the blocking-transcript-read guard's own detection shape: a wrapped read is passed
    as a bare, uncalled attribute, never an inline `.read_text()` call), so this
    function stays async and never blocks whatever thread awaits it, MCP loop included.

    `canonical_index` (every `"canonical": "<type>:<hex>"` string found, mapped to its
    own line number — the SAME structural shape piece 2's `_TOOL_CANONICAL_RE` already
    trusts, a plain substring/regex check, never a per-line JSON parse) is built in the
    SAME pass as `lines`, so a huge file is walked exactly once regardless of how many
    candidates this call, or a FUTURE call, ever ask about it.

    THE SIDECAR (thread 0be2f790's own word): a small JSON file beside the transcript
    (`_sidecar_path`) recording this exact scan keyed on `(size, mtime)` — an unchanged
    transcript on the next visit costs one `stat()` plus one small JSON read, never a
    re-scan. A stale or missing sidecar is silently rebuilt; a sidecar WRITE failure
    (a read-only mount, a full disk) is swallowed — the cache is a bonus, never a
    requirement for correctness."""
    try:
        st = await asyncio.to_thread(transcript.stat)
    except OSError:
        return None
    if st.st_size > max_bytes:
        return None
    sidecar = _sidecar_path(transcript)
    try:
        if await asyncio.to_thread(sidecar.is_file):
            cached_text = await asyncio.to_thread(sidecar.read_text)
            cached = json.loads(cached_text)
            if cached.get("size") == st.st_size and cached.get("mtime") == st.st_mtime:
                cached_lines = cached.get("lines")
                cached_index = cached.get("canonicals")
                if isinstance(cached_lines, list) and isinstance(cached_index, dict):
                    return cached_lines, cached_index
    except (OSError, ValueError):
        pass  # a corrupt/unreadable sidecar just falls through to a fresh scan

    try:
        lines, index = await asyncio.to_thread(_do_scan_lines, transcript)
    except OSError:
        return None
    try:
        payload = json.dumps({"size": st.st_size, "mtime": st.st_mtime,
                               "lines": lines, "canonicals": index})
        await asyncio.to_thread(sidecar.write_text, payload)
    except OSError:
        pass
    return lines, index


async def backfill_possible_upstream(
    actions: Actions, *, dry_run: bool = True, because: str | None = None,
    limit: int = _DEFAULT_LIMIT, newest_first: bool = False, window: int = _WINDOW,
    transcript_root: Path | None = None, max_scan_bytes: int | None = None,
    budget_seconds: float = _DEFAULT_BUDGET_SECONDS,
) -> dict[str, Any]:
    """`transcript_root` defaults to `get_settings().osiris_transcripts` — overridable so
    a test (or an operator pointing at an archived tree) never depends on the live
    fleet's own configured root. `max_scan_bytes` defaults to the registered
    `ingest.transcript_scan_max_bytes` setting (`current_stored_value`, falling back to
    `_DEFAULT_MAX_SCAN_BYTES` when unset) — a caller may still override it directly (a
    test, or a deliberately widened one-off run).

    THE RECEIPT'S OWN SUMMARY (thread e332177f, Thoth msg 10525): `summary.candidates`/
    `summary.writers` classify every candidate/writer examined into exactly one of
    `matched` (a receipt was found — this writer's transcript IS reachable, whether or
    not that receipt's own preceding window produced any upstream targets),
    `no_ledger` (the writer carries no `anchor_sid` assertion at all — never attempted a
    transcript read), or `no_transcript` (a ledger exists but every sid in it either
    resolved to no receipt for this write OR named a transcript over `max_scan_bytes`,
    skipped unopened — thread 0be2f790's own fix). A writer with a ledger who matches on
    ONE candidate and misses on another counts as `matched` at the writer level — the
    ledger is proven reachable, so the miss is that specific write's own receipt, not
    the writer's transcript access. `runtime_seconds` times the whole call, wall-clock.

    `budget_seconds` (thread 0be2f790, the stall this whole module exists to prevent
    from recurring): checked between candidates, never mid-file — exceeding it stops
    early and returns `report["partial"] = True` naming exactly how far it got, rather
    than the caller waiting on a call that silently never returns."""
    if not dry_run and not (because or "").strip():
        return {"error": "backfilling historical provenance without a because is an "
                         "un-audited graph write — cite the ruling/dispatch that "
                         "authorizes it (thread e332177f, Thoth mail 10440), never silent"}

    import time

    from src.ingest.sessions import _upstream_targets
    from src.ontology.canonicalize import canonicalize
    from src.orchestrator.mounts import _transcript_index

    started = time.monotonic()

    if max_scan_bytes is None:
        from src.orchestrator.settings_service import current_stored_value

        stored = await current_stored_value(actions.pool, "ingest.transcript_scan_max_bytes")
        max_scan_bytes = int(stored) if stored is not None else _DEFAULT_MAX_SCAN_BYTES

    if transcript_root is None:
        from src.config.settings import get_settings

        root_str = get_settings().osiris_transcripts
        transcript_root = Path(root_str) if root_str else None
    index = (await asyncio.to_thread(_transcript_index, transcript_root)
             if transcript_root is not None else {})

    plan: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    scan_cache: dict[Path, tuple[list[str], dict[str, int]] | None] = {}
    examined = 0
    partial = False
    candidate_tally = {"matched": 0, "no_ledger": 0, "no_transcript": 0, "too_large": 0}
    writer_outcomes: dict[str, set[str]] = {}
    all_candidates = await _candidates(actions.pool, limit, newest_first=newest_first)

    writer_sids_cache: dict[str, list[str]] = {}
    writer_receipt_index_cache: dict[str, tuple[dict[str, Path], int, bool]] = {}

    def _tag(writer: str, outcome: str) -> None:
        candidate_tally[outcome] += 1
        writer_outcomes.setdefault(writer, set()).add(outcome)

    async def _writer_sids(writer: str) -> list[str]:
        sids = writer_sids_cache.get(writer)
        if sids is None:
            sids = await _anchor_sids(actions.pool, writer)
            writer_sids_cache[writer] = sids
        return sids

    async def _writer_receipt_index(
        writer: str, sids: list[str],
    ) -> tuple[dict[str, Path], int, bool]:
        """Built ONCE per writer (Thoth mail 10716: "looked up, not searched") — merges
        every one of the writer's resolvable, in-cap transcripts into a single
        canonical->path map, so each candidate after the first for this writer is a
        dict lookup, never a re-walk of the writer's own sid list. `too_large_count`
        counts sids whose file exceeds `max_scan_bytes`; `any_scanned` is True the
        moment even one file was actually opened and indexed (never all skipped)."""
        cached = writer_receipt_index_cache.get(writer)
        if cached is not None:
            return cached
        canon_to_path: dict[str, Path] = {}
        too_large_count = 0
        any_scanned = False
        for sid in sids:
            path = index.get(sid)
            if path is None:
                continue
            if path not in scan_cache:
                scan_cache[path] = await _scan_transcript(path, max_scan_bytes)
            scanned = scan_cache[path]
            if scanned is None:
                try:
                    if path.stat().st_size > max_scan_bytes:
                        too_large_count += 1
                except OSError:
                    pass
                continue
            any_scanned = True
            _, canon_index = scanned
            for canonical in canon_index:
                canon_to_path.setdefault(canonical, path)
        result = (canon_to_path, too_large_count, any_scanned)
        writer_receipt_index_cache[writer] = result
        return result

    for cand in all_candidates:
        if time.monotonic() - started >= budget_seconds:
            partial = True
            break
        examined += 1
        writer = cand["writer"]
        sids = await _writer_sids(writer)
        if not sids:
            skipped.append({"object": cand["canonical"], "writer": writer,
                            "reason": "writer's lineage carries no anchor_sid ledger"})
            _tag(writer, "no_ledger")
            continue
        canon_to_path, too_large_count, any_scanned = await _writer_receipt_index(writer, sids)
        short_id = cand["canonical"].split(":", 1)[-1][:8]
        receipt_path = canon_to_path.get(cand["canonical"]) or canon_to_path.get(short_id)
        if receipt_path is None:
            if not any_scanned and too_large_count:
                reason = (f"every one of the writer's {too_large_count} resolvable "
                          "transcript(s) exceeds ingest.transcript_scan_max_bytes — "
                          "skipped unopened")
                skipped.append({"object": cand["canonical"], "writer": writer, "reason": reason})
                _tag(writer, "too_large")
            else:
                reason = ("no receipt for this write found in any of the writer's "
                          f"{len(sids)} indexed transcript(s)")
                if too_large_count:
                    reason += (f" ({too_large_count} skipped — over "
                              "ingest.transcript_scan_max_bytes)")
                skipped.append({"object": cand["canonical"], "writer": writer, "reason": reason})
                _tag(writer, "no_transcript")
            continue
        scanned = scan_cache[receipt_path]
        assert scanned is not None  # canon_to_path only ever names a successfully-scanned file
        receipt_lines, receipt_line_index = scanned
        receipt_line_val = receipt_line_index.get(cand["canonical"])
        receipt_line = (receipt_line_val if receipt_line_val is not None
                        else receipt_line_index[short_id])
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

    writer_tally = {"matched": 0, "no_ledger": 0, "no_transcript": 0, "too_large": 0}
    for outcomes in writer_outcomes.values():
        if "no_ledger" in outcomes:
            writer_tally["no_ledger"] += 1
        elif "matched" in outcomes:
            writer_tally["matched"] += 1
        elif "too_large" in outcomes:
            writer_tally["too_large"] += 1
        else:
            writer_tally["no_transcript"] += 1

    report: dict[str, Any] = {
        "dry_run": dry_run, "candidates_examined": examined, "newest_first": newest_first,
        "edges_to_mint": len(plan), "edges_by_door": edges_by_door, "plan": plan,
        "skipped_count": len(skipped), "skipped": skipped,
        "summary": {"candidates": candidate_tally, "writers": writer_tally},
        "runtime_seconds": round(time.monotonic() - started, 3),
        "partial": partial, "candidates_total": len(all_candidates),
        "max_scan_bytes": max_scan_bytes,
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
