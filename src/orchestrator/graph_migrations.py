"""DRAWING THE WHOLE GRAPH, THE MIGRATIONS (thread 325ef660, Thoth mail 11407/11423):
three name-dispatched repair doors, the SAME dry-run-default/idempotent/compensating-
event shape `backfill.py` already established for exactly this class of work
(`BACKFILL_TARGETS`/`run_backfill`) -- a separate registry here (`MIGRATION_TARGETS`/
`run_migration`), not a new entry in that one, because these three are graph-shape
repairs feeding the physics layout and the renderer, not the identity/provenance
backfill's own population. `osiris graph-migrate <name> [--dry-run/--apply]` is the CLI
door (cli.py) -- named to avoid colliding with the pre-existing `osiris migrate`
(alembic's env-correct schema tool, unrelated).

Each target: `dry_run` defaults True; `dry_run=False` REQUIRES a non-blank `because`
(the same audit-trail contract every backfill target already holds itself to); every
write is a compensating event (`assert_property`/`create_link`/`invalidate_link`),
never a delete; a repeat call after a real write finds nothing left to do."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

import asyncpg

from src.actions.core import Actions
from src.parsers.base import EvidenceClass
from src.parsers.evidence import confidence_for

MIGRATION_TARGETS = frozenset({
    "repo_seats_fix", "file_the_unfiled", "assertion_links",
})

_TIER = EvidenceClass.DIRECT_OBSERVATION
_CONF = confidence_for(_TIER)
_EC = _TIER.value
_SOURCE = "graph_migrations"


async def run_migration(
    pool: asyncpg.Pool, name: str, *, actor: str, dry_run: bool = True,
    because: str | None = None,
) -> dict[str, Any]:
    """Dispatch on `name` -- see `MIGRATION_TARGETS` for the full set."""
    actions = Actions(pool)
    if name == "repo_seats_fix":
        return await migrate_repo_seats_fix(
            actions, actor=actor, dry_run=dry_run, because=because)
    if name == "file_the_unfiled":
        return await migrate_file_the_unfiled(
            actions, actor=actor, dry_run=dry_run, because=because)
    if name == "assertion_links":
        return await migrate_assertion_links(
            actions, actor=actor, dry_run=dry_run, because=because)
    return {"error": f"unknown migration {name!r}", "valid_targets": sorted(MIGRATION_TARGETS)}


async def migrate_repo_seats_fix(
    actions: Actions, *, actor: str, dry_run: bool = True, because: str | None = None,
) -> dict[str, Any]:
    """THE repo:seats BUG (DRAWING THE WHOLE GRAPH, thread 325ef660): `repo:seats` is
    a phantom SoftwareProject minted from ~/.osiris/seats, the bare seat-office
    CONTAINER, never a real project -- the SAME "seats" basename `offices.
    is_bare_office_root` already guards `seats.resolve_project` against (ruling
    577988ed), reached here through a different door (git-ingest's own
    `sessions._repo_from_cwd`, fixed alongside this migration so the derivation
    itself stops producing new damage while this repairs the historical kind).

    Compensating, never a delete: every Agent whose CURRENT `project` assertion
    reads "seats" is re-stamped to "osiris" (the fleet's own house -- the bare
    container belongs to no ONE seat, so there is no per-seat house to derive,
    only the shared fleet root); every live works_in/in_repo edge INTO repo:seats
    is invalidated and re-minted pointing at repo:osiris instead. Once every edge
    is moved, repo:seats is retired via `projects.retire_project` (a real status
    flip, never a raw DELETE) -- never before every edge off it is moved, so it is
    never retired while still load-bearing.

    DRY RUN IS THE DEFAULT. `dry_run=False` REQUIRES a non-blank `because`.
    Idempotent: a repeat call finds no "seats"-stamped agents and no live edges
    into repo:seats (already retired) left to touch."""
    if not dry_run and not (because or "").strip():
        return {"error": "migrating without a because is an un-audited repair — cite "
                         "the evidence/ruling that authorizes it"}
    pool = actions.pool
    seats_row = await pool.fetchrow(
        "SELECT id, status FROM objects WHERE canonical='repo:seats' AND type='SoftwareProject'")
    if seats_row is None:
        return {"dry_run": dry_run, "already_clean": True, "note": "no repo:seats object exists"}
    seats_id, seats_status = seats_row["id"], seats_row["status"]
    osiris_id = await pool.fetchval(
        "SELECT id FROM objects WHERE canonical='repo:osiris' AND type='SoftwareProject' "
        "AND status='active'")
    if osiris_id is None:
        return {"error": "repo:osiris is not an active SoftwareProject — refusing to "
                         "re-file into a target that isn't there"}

    agent_rows = await pool.fetch(
        "SELECT o.id, o.canonical FROM objects o "
        "JOIN current_assertions a ON a.object_id=o.id "
        "WHERE a.name='project' AND a.value #>> '{}' = 'seats' AND o.type='Agent'")
    edge_rows = (await pool.fetch(
        "SELECT l.from_id, l.type, o.type AS from_type "
        "FROM links l JOIN objects o ON o.id=l.from_id "
        "WHERE l.to_id=$1 AND l.type IN ('works_in','in_repo') "
        "AND (l.valid_until IS NULL OR l.valid_until > now())", seats_id)
                if seats_status == "active" else [])

    now = datetime.now(UTC)
    agents_plan = [{"agent": str(r["id"])[:8], "canonical": r["canonical"]} for r in agent_rows]
    edges_plan = [
        {"from": str(r["from_id"])[:8], "from_type": r["from_type"], "type": r["type"]}
        for r in edge_rows]

    if not dry_run:
        for r in agent_rows:
            await actions.assert_property(
                r["id"], "project", "osiris", actor, now, _CONF, evidence_class=_EC)
        for r in edge_rows:
            await actions.invalidate_link(
                r["from_id"], seats_id, r["type"], actor, now,
                reason="migrate_repo_seats_fix: repo:seats is a phantom project minted "
                       "from the bare seat-office container, never a real repo")
            await actions.create_link(
                r["from_id"], osiris_id, r["type"], actor, now, _CONF, evidence_class=_EC)

    osiris_seats_edges_after = None
    retired = None
    if not dry_run:
        osiris_seats_edges_after = await pool.fetchval(
            "SELECT count(*) FROM links WHERE to_id=$1 "
            "AND (valid_until IS NULL OR valid_until > now())", seats_id)
        if osiris_seats_edges_after == 0 and seats_status == "active":
            from src.orchestrator.projects import retire_project

            result = await retire_project(
                actions, project="repo:seats", actor=actor,
                because=f"{because} (migrate_repo_seats_fix: every live edge into this "
                        "phantom project has been re-filed into repo:osiris)")
            retired = result.get("retired_project") or result.get("error")

    return {
        "dry_run": dry_run,
        "agents_scanned": len(agent_rows), "agents_plan": agents_plan,
        "edges_scanned": len(edge_rows), "edges_plan": edges_plan,
        "osiris_seats_edges_after": osiris_seats_edges_after,
        "retired": retired,
        "because": because if not dry_run else None,
    }


async def migrate_file_the_unfiled(
    actions: Actions, *, actor: str, dry_run: bool = True, because: str | None = None,
) -> dict[str, Any]:
    """FILE THE UNFILED (DRAWING THE WHOLE GRAPH, thread 325ef660): ~7,300 active,
    non-SoftwareProject objects carry neither a `project` assertion nor a live
    in_repo/works_in link -- the physics layout's own "unfiled fog" (THE LONG EDGES
    RULING, ruling d9c10467), placed only by neighbour-centroid pull today, never
    actually filed.

    MAJORITY PROJECT OVER DIRECT NEIGHBOURS, ANY LINK TYPE: every live edge touching
    an unfiled object (either direction, any type -- membership is a vote here, not
    a spring; THE READING LAYER's structural/semantic split governs the LAYOUT, not
    this tally) contributes one vote for that neighbour's own project -- itself,
    when the neighbour IS an active SoftwareProject, else the neighbour's own
    in_repo/works_in target. TIE OR EMPTY STAYS UNFILED, AND IS COUNTED: a tie
    between two-or-more top projects, or an unfiled object with no neighbour that
    resolves to any project at all, is left exactly as it was -- never a guess
    between equally-supported candidates. Two unfiled objects linked to EACH OTHER
    cast no vote for one another (neither carries a project to lend); this stays a
    single hop, never a chained/transitive resolution.

    The winning project asserts as `project` (the bare name, matching every other
    `project` assertion's own shape in this codebase, e.g. `resolve_and_persist_
    seated_project`'s), source=`graph_migrations` -- the vote tally itself is the
    evidence, carried in full in this function's own receipt (`plan`), not a second
    copy embedded in the assertion row.

    DRY RUN IS THE DEFAULT. `dry_run=False` REQUIRES a non-blank `because`.
    Idempotent: a repeat call finds no unfiled objects left that this run actually
    filed (a still-unfiled tie/empty object stays a legitimate candidate for a
    LATER run, once more links exist to break the tie)."""
    if not dry_run and not (because or "").strip():
        return {"error": "migrating without a because is an un-audited repair — cite "
                         "the evidence/ruling that authorizes it"}
    pool = actions.pool
    unfiled_rows = await pool.fetch(
        "SELECT o.id FROM objects o "
        "WHERE o.status NOT IN ('archived','merged','retired') "
        "AND o.type != 'SoftwareProject' "
        "AND NOT EXISTS (SELECT 1 FROM current_assertions a "
        "  WHERE a.object_id=o.id AND a.name='project') "
        "AND NOT EXISTS (SELECT 1 FROM links l WHERE l.from_id=o.id "
        "  AND l.type IN ('in_repo','works_in') "
        "  AND (l.valid_until IS NULL OR l.valid_until > now()))")
    unfiled_ids: set[uuid.UUID] = {r["id"] for r in unfiled_rows}
    if not unfiled_ids:
        return {"dry_run": dry_run, "scanned": 0}

    neighbour_edge_rows = await pool.fetch(
        "SELECT from_id, to_id FROM links "
        "WHERE (valid_until IS NULL OR valid_until > now()) "
        "AND (from_id = ANY($1::uuid[]) OR to_id = ANY($1::uuid[]))",
        list(unfiled_ids))
    neighbours_by_unfiled: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for r in neighbour_edge_rows:
        f, t = r["from_id"], r["to_id"]
        if f in unfiled_ids and t not in unfiled_ids:
            neighbours_by_unfiled[f].add(t)
        if t in unfiled_ids and f not in unfiled_ids:
            neighbours_by_unfiled[t].add(f)

    all_neighbour_ids = {n for s in neighbours_by_unfiled.values() for n in s}
    project_by_neighbour: dict[uuid.UUID, uuid.UUID] = {}
    if all_neighbour_ids:
        sw_rows = await pool.fetch(
            "SELECT id FROM objects WHERE id = ANY($1::uuid[]) "
            "AND type='SoftwareProject' AND status='active'", list(all_neighbour_ids))
        for r in sw_rows:
            project_by_neighbour[r["id"]] = r["id"]
        remaining = [n for n in all_neighbour_ids if n not in project_by_neighbour]
        if remaining:
            link_rows = await pool.fetch(
                "SELECT l.from_id AS oid, l.to_id AS pid FROM links l "
                "JOIN objects o ON o.id=l.to_id AND o.type='SoftwareProject' "
                "  AND o.status='active' "
                "WHERE l.type IN ('in_repo','works_in') AND l.from_id = ANY($1::uuid[]) "
                "AND (l.valid_until IS NULL OR l.valid_until > now())", remaining)
            for r in link_rows:
                project_by_neighbour.setdefault(r["oid"], r["pid"])

    filed: dict[uuid.UUID, uuid.UUID] = {}
    filed_votes: dict[uuid.UUID, int] = {}
    ties: list[dict[str, Any]] = []
    still_unfiled: list[str] = []
    for oid in unfiled_ids:
        tally: Counter[uuid.UUID] = Counter()
        for n in neighbours_by_unfiled.get(oid, ()):
            pid = project_by_neighbour.get(n)
            if pid is not None:
                tally[pid] += 1
        if not tally:
            still_unfiled.append(str(oid)[:8])
            continue
        top_count = tally.most_common(1)[0][1]
        winners = [pid for pid, c in tally.items() if c == top_count]
        if len(winners) > 1:
            ties.append({
                "object": str(oid)[:8],
                "candidates": sorted(str(w)[:8] for w in winners), "votes": top_count})
            still_unfiled.append(str(oid)[:8])
            continue
        filed[oid] = winners[0]
        filed_votes[oid] = top_count

    canon_by_pid: dict[uuid.UUID, str] = {}
    if filed:
        rows = await pool.fetch(
            "SELECT id, canonical FROM objects WHERE id = ANY($1::uuid[])",
            list(set(filed.values())))
        canon_by_pid = {r["id"]: r["canonical"] for r in rows}

    now = datetime.now(UTC)
    by_project_tally: Counter[str] = Counter()
    filed_plan: list[dict[str, Any]] = []
    for oid, pid in filed.items():
        name = canon_by_pid.get(pid, str(pid)).removeprefix("repo:")
        by_project_tally[name] += 1
        filed_plan.append({
            "object": str(oid)[:8], "project": name, "votes": filed_votes[oid]})
        if not dry_run:
            await actions.assert_property(
                oid, "project", name, _SOURCE, now, _CONF, evidence_class=_EC)

    return {
        "dry_run": dry_run,
        "scanned": len(unfiled_ids),
        "filed": len(filed), "by_project": dict(by_project_tally),
        "ties": len(ties), "ties_plan": ties,
        "still_unfiled": len(still_unfiled),
        "plan": filed_plan,
        "because": because if not dry_run else None,
    }


async def _resolve_ref(
    pool: asyncpg.Pool, value: str | None, *, object_type: str | None = None,
) -> uuid.UUID | None:
    """A stored assertion VALUE (a canonical string, e.g. "agent:xyz"/"seat:xyz", or a
    raw uuid) resolved to a real, active object id -- exact canonical match first,
    then a raw uuid parse. Never a name/fuzzy lookup: an assertion recorded a specific
    reference at write time, this only confirms it still resolves, it never guesses a
    NEW one. `object_type`, when given, narrows both attempts to that type."""
    if not value:
        return None
    if object_type:
        row = await pool.fetchval(
            "SELECT id FROM objects WHERE canonical=$1 AND type=$2 AND status='active'",
            value, object_type)
    else:
        row = await pool.fetchval(
            "SELECT id FROM objects WHERE canonical=$1 AND status='active'", value)
    if row is not None:
        return row  # type: ignore[no-any-return]
    try:
        oid = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    if object_type:
        row2 = await pool.fetchval(
            "SELECT id FROM objects WHERE id=$1 AND type=$2 AND status='active'",
            oid, object_type)
    else:
        row2 = await pool.fetchval(
            "SELECT id FROM objects WHERE id=$1 AND status='active'", oid)
    return row2  # type: ignore[no-any-return]


async def _link_exists(
    pool: asyncpg.Pool, from_id: uuid.UUID, to_id: uuid.UUID, type_: str,
) -> bool:
    return bool(await pool.fetchval(
        "SELECT 1 FROM links WHERE from_id=$1 AND to_id=$2 AND type=$3 "
        "AND (valid_until IS NULL OR valid_until > now())", from_id, to_id, type_))


async def _mint_links_from_property(
    actions: Actions, *, subject_type: str, prop_name: str, link_type: str,
    target_type: str | None = None, dry_run: bool, now: datetime,
) -> dict[str, int]:
    """The shared shape behind four of the seven ASSERTION LINKS sub-migrations
    (owned_by/closed_by/admitted_by/acknowledges) -- the other three (recorded_by,
    supersedes, vendor_of) each need a real property VALUE, and this reads exactly
    that: every `subject_type` object's CURRENT `prop_name` assertion's own VALUE,
    resolved via `_resolve_ref` (optionally narrowed to `target_type`), mints
    `(subject) -[link_type]-> (target)` unless that exact live edge already exists.
    recorded_by is NOT this shape -- it needs the assertion ROW's own `source_id`
    (who wrote it), never its value (what it says); supersedes needs a custom
    from/to normalisation across two property names; vendor_of's value is a
    free-text name, not a canonical/uuid `_resolve_ref` can read directly. Three
    buckets, never silently merged: minted, skipped_unresolvable (the value no
    longer resolves to a real object), already_present (idempotent re-run)."""
    pool = actions.pool
    rows = await pool.fetch(
        "SELECT o.id AS subject_id, a.value #>> '{}' AS value "
        "FROM objects o JOIN current_assertions a ON a.object_id=o.id "
        "WHERE o.type=$1 AND o.status='active' AND a.name=$2", subject_type, prop_name)
    minted = skipped_unresolvable = already_present = 0
    for r in rows:
        target_id = await _resolve_ref(pool, r["value"], object_type=target_type)
        if target_id is None:
            skipped_unresolvable += 1
            continue
        if await _link_exists(pool, r["subject_id"], target_id, link_type):
            already_present += 1
            continue
        minted += 1
        if not dry_run:
            await actions.create_link(
                r["subject_id"], target_id, link_type, _SOURCE, now, _CONF,
                evidence_class=_EC)
    return {"minted": minted, "skipped_unresolvable": skipped_unresolvable,
           "already_present": already_present}


async def _mint_recorded_by(
    actions: Actions, *, subject_type: str, dry_run: bool, now: datetime,
) -> dict[str, int]:
    """recorded_by's own shape, distinct from `_mint_links_from_property`: the target
    is the assertion ROW's own `source_id` column (WHO wrote the object's current
    `summary`), never the assertion's `value` (WHAT it says) -- a `source_id` of
    "agent:xyz" resolved via `_resolve_ref` the same way any other canonical is."""
    pool = actions.pool
    rows = await pool.fetch(
        "SELECT o.id AS subject_id, a.source_id AS source_id "
        "FROM objects o JOIN current_assertions a ON a.object_id=o.id "
        "WHERE o.type=$1 AND o.status='active' AND a.name='summary'", subject_type)
    minted = skipped_unresolvable = already_present = 0
    for r in rows:
        agent_id = await _resolve_ref(pool, r["source_id"], object_type="Agent")
        if agent_id is None:
            skipped_unresolvable += 1
            continue
        if await _link_exists(pool, r["subject_id"], agent_id, "recorded_by"):
            already_present += 1
            continue
        minted += 1
        if not dry_run:
            await actions.create_link(
                r["subject_id"], agent_id, "recorded_by", _SOURCE, now, _CONF,
                evidence_class=_EC)
    return {"minted": minted, "skipped_unresolvable": skipped_unresolvable,
           "already_present": already_present}


async def migrate_assertion_links(
    actions: Actions, *, actor: str, dry_run: bool = True, because: str | None = None,
) -> dict[str, Any]:
    """ASSERTION LINKS (DRAWING THE WHOLE GRAPH, thread 325ef660): seven property-
    pair-to-real-link mints, each idempotent and independently reported (minted /
    skipped_unresolvable / already_present) so a partial resolve rate in one never
    hides behind another's. `recorded_by`/`owned_by`/`admitted_by`/`acknowledges`/
    `vendor_of` are STRUCTURAL, `supersedes` SEMANTIC (link_classes.py, this same
    migration's own tip) -- attribution/membership edges versus a real content
    claim, the same split every other type in this codebase already sorts by.

    recorded_by: every active Decision/Thread's CURRENT `summary` assertion's own
    `source_id` (the agent that actually wrote it), when that source_id resolves to
    a real Agent.

    supersedes: THE ONE SPECIAL CASE (custom, not the shared helper) -- Decision.
    supersedes/superseded_by are a PROPERTY PAIR by deliberate design (ruling
    dd04d7dd, "no link-retraction primitive needed" for the event-sourced property
    itself); this migration does not change that design or retire the properties,
    it ADDS a real `supersedes` link alongside them purely so the renderer's path
    lens (space.js's own PATH_EDGE_TYPES, which already names `supersedes`) has an
    edge to walk. Both properties read, normalised to ONE outgoing edge per pair
    (A supersedes B mints A->B once, whichever property named it) so a pair
    asserted from either side is never double-counted.

    owned_by: Thread.owner (a Seat or Agent canonical). closed_by: Thread.
    resolved_in, WHERE MISSING ONLY -- closed_by is an existing, actively-minted
    link type (`_mint_closed_by` and friends); this only fills the historical gap
    where the property exists but the link never landed, never a second edge
    alongside a real one. admitted_by: Thread.admitted_by. acknowledges: Decision.
    prior_art_acknowledged. vendor_of: Reference.vendor -- the one genuinely fuzzy
    resolution here (a free-text vendor NAME, not a canonical/uuid), resolved
    against an active SoftwareProject's own `repo:<name>` canonical; a vendor
    string that never names a real project abstains, honestly, rather than
    minting a link to a guess.

    DRY RUN IS THE DEFAULT. `dry_run=False` REQUIRES a non-blank `because`."""
    if not dry_run and not (because or "").strip():
        return {"error": "migrating without a because is an un-audited repair — cite "
                         "the evidence/ruling that authorizes it"}
    pool = actions.pool
    now = datetime.now(UTC)
    receipt: dict[str, dict[str, int]] = {}

    recorded_by_d = await _mint_recorded_by(actions, subject_type="Decision", now=now,
                                            dry_run=dry_run)
    recorded_by_t = await _mint_recorded_by(actions, subject_type="Thread", now=now,
                                            dry_run=dry_run)
    receipt["recorded_by"] = {
        k: recorded_by_d[k] + recorded_by_t[k] for k in recorded_by_d}

    # supersedes -- the one custom case, see the docstring above.
    pair_rows = await pool.fetch(
        "SELECT o.id AS subject_id, a.name, a.value #>> '{}' AS value "
        "FROM objects o JOIN current_assertions a ON a.object_id=o.id "
        "WHERE o.type='Decision' AND o.status='active' "
        "AND a.name IN ('supersedes','superseded_by')")
    supersedes_edges: set[tuple[uuid.UUID, uuid.UUID]] = set()
    supersedes_unresolvable = 0
    for r in pair_rows:
        other_id = await _resolve_ref(pool, r["value"], object_type="Decision")
        if other_id is None:
            supersedes_unresolvable += 1
            continue
        edge = (r["subject_id"], other_id) if r["name"] == "supersedes" \
            else (other_id, r["subject_id"])
        supersedes_edges.add(edge)
    supersedes_minted = supersedes_already = 0
    for from_id, to_id in supersedes_edges:
        if await _link_exists(pool, from_id, to_id, "supersedes"):
            supersedes_already += 1
            continue
        supersedes_minted += 1
        if not dry_run:
            await actions.create_link(
                from_id, to_id, "supersedes", _SOURCE, now, _CONF, evidence_class=_EC)
    receipt["supersedes"] = {
        "minted": supersedes_minted, "skipped_unresolvable": supersedes_unresolvable,
        "already_present": supersedes_already}

    receipt["owned_by"] = await _mint_links_from_property(
        actions, subject_type="Thread", prop_name="owner", link_type="owned_by",
        dry_run=dry_run, now=now)
    receipt["closed_by"] = await _mint_links_from_property(
        actions, subject_type="Thread", prop_name="resolved_in", link_type="closed_by",
        dry_run=dry_run, now=now)
    receipt["admitted_by"] = await _mint_links_from_property(
        actions, subject_type="Thread", prop_name="admitted_by", link_type="admitted_by",
        target_type="Agent", dry_run=dry_run, now=now)
    receipt["acknowledges"] = await _mint_links_from_property(
        actions, subject_type="Decision", prop_name="prior_art_acknowledged",
        link_type="acknowledges", target_type="Decision", dry_run=dry_run, now=now)

    # vendor_of -- the one fuzzy resolution: a free-text vendor NAME against an
    # active SoftwareProject's own repo:<name> canonical, never a raw uuid parse
    # (a vendor string is never one).
    vendor_rows = await pool.fetch(
        "SELECT o.id AS subject_id, a.value #>> '{}' AS value "
        "FROM objects o JOIN current_assertions a ON a.object_id=o.id "
        "WHERE o.type='Reference' AND o.status='active' AND a.name='vendor'")
    vendor_minted = vendor_unresolvable = vendor_already = 0
    for r in vendor_rows:
        name = (r["value"] or "").strip()
        target_id = (await pool.fetchval(
            "SELECT id FROM objects WHERE canonical=$1 AND type='SoftwareProject' "
            "AND status='active'", f"repo:{name}")) if name else None
        if target_id is None:
            vendor_unresolvable += 1
            continue
        if await _link_exists(pool, target_id, r["subject_id"], "vendor_of"):
            vendor_already += 1
            continue
        vendor_minted += 1
        if not dry_run:
            await actions.create_link(
                target_id, r["subject_id"], "vendor_of", _SOURCE, now, _CONF,
                evidence_class=_EC)
    receipt["vendor_of"] = {
        "minted": vendor_minted, "skipped_unresolvable": vendor_unresolvable,
        "already_present": vendor_already}

    return {"dry_run": dry_run, "receipt": receipt, "because": because if not dry_run else None}
