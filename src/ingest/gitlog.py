"""Git-history ingest — Osiris tracking its own genesis (and any repository).

Proof that the engine is a GENERAL substrate, not OSINT-only: a git history is just
another structured source. The same collector pattern every federator uses — a source →
graded objects/links through the Actions waist — maps a repo's commits, developers, and
the history DAG into the entity graph. So Osiris can model its own development, the first
commit onward. The git log is the authoritative record (facts land AUTHORITATIVE_API),
and the commit date is the observed-at clock (time-travel the graph by commit).

  SoftwareProject ──in_repo── Commit ──authored_by── Person(dev)
                              Commit ──follows──────► parent Commit

Run: `python -m src.ingest.gitlog [path] [limit]`.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.actions.core import Actions
from src.config.settings import get_settings
from src.db.pool import create_pool
from src.parsers.base import EvidenceClass
from src.parsers.evidence import confidence_for

_SOURCE = "git"
_EC = EvidenceClass.AUTHORITATIVE_API.value
_CONF = confidence_for(EvidenceClass.AUTHORITATIVE_API)
# unit-separator delimited fields, record-separated — robust against newlines in subjects.
# %b (body) carries the rationale: each commit message IS a decision record, so the body is
# project memory, not noise.
_FMT = "%H%x1f%an%x1f%ae%x1f%aI%x1f%P%x1f%s%x1f%b%x1e"

# Conventional Commits: `type(scope)!: summary`. The type+scope make a commit groupable
# ("what changed in the composer?"), so the changelog is a query, not a string-scan.
_CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?!?:\s*(?P<summary>.+)$")


@dataclass
class Commit:
    sha: str
    author_name: str
    author_email: str
    date: str  # ISO 8601 with offset
    parents: list[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""


# Machine trailers git appends to a body — provenance, NOT rationale. The body is memory
# (decision-mining, recall, and cross-repo derivation all read the `rationale` property), and
# these lines poison it: the Co-Authored-By / *-Session trailers made "claude" / "anthropic" /
# "noreply" the top cross-repo "concern" in all seven repos. Allow-listed keys only — a plain
# "Note:" or "Fixes:" body line is never treated as a trailer.
_TRAILER = re.compile(
    r"^\s*(?:co-authored-by|signed-off-by|[\w-]*-session)\s*:|^\s*🤖?\s*generated with\b", re.I)


def strip_trailers(body: str) -> str:
    """A commit body with its machine trailer lines removed — the human rationale only. Pure."""
    return "\n".join(ln for ln in body.splitlines() if not _TRAILER.match(ln)).strip()


def parse_subject(subject: str) -> dict[str, str]:
    """Pull the Conventional-Commit type/scope/summary from a subject line (empty dict if
    it isn't conventional). These become queryable Commit properties."""
    m = _CONVENTIONAL.match(subject.strip())
    if not m:
        return {}
    return {k: v for k, v in {
        "change_type": m.group("type"), "scope": m.group("scope"),
        "summary": m.group("summary"),
    }.items() if v}


def parse_git_log(raw: str) -> list[Commit]:
    """Pure: a delimited `git log` dump → commits (genesis first if --reverse)."""
    out: list[Commit] = []
    for record in raw.split("\x1e"):
        rec = record.strip("\n")
        if not rec.strip():
            continue
        f = rec.split("\x1f")
        if len(f) < 6:
            continue
        body = f[6].strip() if len(f) > 6 else ""
        out.append(Commit(f[0], f[1], f[2], f[3],
                          f[4].split() if f[4].strip() else [], f[5], body))
    return out


def _git(path: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", path, *args], capture_output=True, text=True, check=True
    ).stdout


def read_commits(path: str, *, limit: int | None = None) -> list[Commit]:
    """Read a repo's history, genesis first. `limit` takes the most-recent N (git -n).

    `--all` (thread b4297a47/1c8e3907, ingest registration phase 2's own measured
    constraint 3): a bare `git log` walks only the current checked-out branch (HEAD),
    so a repo with live topic/worktree branches — 17 live worktree-agent-* branches
    proved it — never gets every commit ingested even when the repo itself IS
    tracked. Safe to always include: `follows` links are derived from each commit's
    own parent SHA (`c.parents` below), never from this log's line order, and every
    object here is find-or-create on sha/canonical — a commit reachable from two
    branches is ingested once regardless of how many roots `--all` walks it from."""
    args = ["log", "--all", "--reverse", f"--pretty=format:{_FMT}"]
    if limit:
        args += ["-n", str(limit)]
    return parse_git_log(_git(path, *args))


def _dev_canonical(c: Commit) -> str:
    return f"dev:{(c.author_email or c.author_name).strip().lower()}"


def _identity_name(dev_canonical: str) -> str:
    """GIT IDENTITIES WEARING THE WRONG NAME (thread 0be2f790's own operator-finding
    follow-up, Thoth DM 10711, ruling 9d64cb25): a dev: identity's own `name` is
    derived ONCE from the identity itself — the email local part, literally whatever
    precedes '@' in the canonical's own `dev:<email>` — never from whichever commit
    author happened to write it last. Stable by construction: a pure function of the
    (immutable) canonical, so it never varies run to run regardless of which of the many
    callers (gitlog's own CLI, pulse's watch loop, tree_ingest's own one-source-id-per-
    agent-worktree calls, each a DIFFERENT source_id under the SAME shared ingest_repo)
    triggered this ingest, or what that particular commit's author name string said.
    A `dev:<name>` canonical with no `@` at all (the `_dev_canonical` fallback for a
    commit with no author email) has no "local part" to strip — the whole thing is the
    identity, unchanged."""
    email = dev_canonical.removeprefix("dev:")
    return email.split("@", 1)[0]


async def ingest_repo(
    actions: Actions, path: str = ".", *, limit: int | None = None,
    source_id: str = _SOURCE, case_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Ingest a repository's history into the entity graph. Idempotent (find-or-create
    on the commit sha / dev email), so re-running just adds new commits."""
    name = Path(_git(path, "rev-parse", "--show-toplevel").strip()).name
    commits = read_commits(path, limit=limit)
    latest = (
        datetime.fromisoformat(commits[-1].date) if commits else datetime.now(UTC)
    )

    repo = await actions.create_or_find_object(
        "SoftwareProject", f"repo:{name}", source_id, case_id
    )
    await actions.assert_property(repo, "name", name, source_id, latest, _CONF,
                                  case_id=case_id, evidence_class=_EC)

    # create_link is a plain append, so re-ingesting a repo (the normal way to pick up new
    # commits) would DUPLICATE every structural edge. Objects dedup on canonical, but the
    # authored_by/in_repo/follows links don't — dedup them so a re-ingest is truly idempotent.
    existing = {(r["from_id"], r["to_id"], r["type"]) for r in await actions.pool.fetch(
        "SELECT from_id, to_id, type FROM links "
        "WHERE type IN ('authored_by', 'in_repo', 'follows')")}

    async def _link(frm: uuid.UUID, to: uuid.UUID, typ: str, observed: datetime) -> None:
        if (frm, to, typ) in existing:
            return
        await actions.create_link(frm, to, typ, source_id, observed, _CONF,
                                  case_id=case_id, evidence_class=_EC)
        existing.add((frm, to, typ))

    # A DEV'S EMAIL/ALIAS SET IS CHECKED ONCE PER RUN, NOT REASSERTED PER COMMIT (operator
    # ruling, thread 2a280e07, mail 9240 — "fix the sources"): the naive per-commit assert
    # reasserted on EVERY commit by the same author, live-measured at 166,786/166,782 rows
    # for one Person — this repo's own git history is exactly the 8-12-minute-cron source
    # Thoth's dispatch named. `dev_info` accumulates, per dev canonical, EVERY distinct
    # author_name actually seen across this run's whole walk (not just the last commit) —
    # GIT IDENTITIES WEARING THE WRONG NAME (thread 0be2f790's own operator-finding
    # follow-up, Thoth DM 10711, ruling 9d64cb25) replaced the old "last commit's
    # author name becomes `name`" policy (which let whichever of many source_id-per-
    # agent-worktree ingest runs happened to write most recently silently flip a Person's
    # own displayed name — the operator's own git identity flipping to an agent's name was
    # exactly this) with a policy that never lets ANY commit author name touch `name` at
    # all: `_identity_name` derives it once from the (stable) canonical itself, and every
    # author name actually seen rides along as an `author_alias` instead — never lost,
    # never mistaken for the identity's own chosen name.
    dev_info: dict[str, dict[str, Any]] = {}
    for c in commits:
        observed = datetime.fromisoformat(c.date)
        short = c.sha[:12]

        dev = await actions.create_or_find_object("Person", _dev_canonical(c), source_id, case_id)
        key = _dev_canonical(c)
        info = dev_info.setdefault(key, {"id": dev, "names": set(), "email": None})
        info["names"].add(c.author_name)
        info["email"] = c.author_email or info["email"]
        info["observed"] = observed

        cm = await actions.create_or_find_object("Commit", f"commit:{short}", source_id, case_id)
        await actions.assert_property(cm, "subject", c.subject, source_id, observed, _CONF,
                                      case_id=case_id, evidence_class=_EC)
        await actions.assert_property(cm, "authored_date", c.date, source_id, observed, _CONF,
                                      case_id=case_id, evidence_class=_EC)
        # the structure that turns the log into queryable memory: type/scope (groupable) +
        # the rationale body (why, not just what).
        for prop, value in parse_subject(c.subject).items():  # not `name` — it shadows the repo
            await actions.assert_property(cm, prop, value, source_id, observed, _CONF,
                                          case_id=case_id, evidence_class=_EC)
        rationale = strip_trailers(c.body)
        if rationale:
            await actions.assert_property(cm, "rationale", rationale, source_id, observed, _CONF,
                                          case_id=case_id, evidence_class=_EC)
        if not c.parents:  # the first commit — the genesis
            await actions.assert_property(cm, "genesis", "true", source_id, observed, _CONF,
                                          case_id=case_id, evidence_class=_EC)

        await _link(cm, dev, "authored_by", observed)
        await _link(cm, repo, "in_repo", observed)
        for parent in c.parents:
            par = await actions.create_or_find_object(
                "Commit", f"commit:{parent[:12]}", source_id, case_id
            )
            await _link(cm, par, "follows", observed)

    for key, info in dev_info.items():
        dev, observed, author_email = info["id"], info["observed"], info["email"]
        identity_name = _identity_name(key)
        current_name = await actions.pool.fetchval(
            "SELECT a.value #>> '{}' FROM current_assertions a "
            "WHERE a.object_id=$1 AND a.name='name' AND a.source_id=$2 LIMIT 1",
            dev, source_id)
        if current_name != identity_name:
            await actions.assert_property(dev, "name", identity_name, source_id, observed,
                                          _CONF, case_id=case_id, evidence_class=_EC)
        # GIT IDENTITIES WEARING THE WRONG NAME (Thoth DM 10711, ruling 9d64cb25):
        # every author_name actually seen for this dev this run, other than the identity-
        # derived name itself, rides along as `author_alias` — additive across runs (merged
        # with whatever this SAME source already recorded), never overwritten, never
        # mistaken for the identity's own chosen name.
        # case-insensitive compare: `identity_name` is always lowercase (`_dev_canonical`
        # lowercases the whole canonical), but a commit author's own display name carries
        # its natural casing — "Ada" must not count as an alias of "ada" just because the
        # email-derived identity name is lowercase; "Ada Lovelace" genuinely is a different
        # name and still does.
        seen_aliases = {n for n in info["names"] if n and n.lower() != identity_name}
        if seen_aliases:
            existing_alias_value = await actions.pool.fetchval(
                "SELECT a.value FROM current_assertions a "
                "WHERE a.object_id=$1 AND a.name='author_alias' AND a.source_id=$2 LIMIT 1",
                dev, source_id)
            existing_aliases = set(existing_alias_value) if existing_alias_value else set()
            merged = sorted(existing_aliases | seen_aliases)
            if merged != sorted(existing_aliases):
                await actions.assert_property(
                    dev, "author_alias", merged, source_id, observed, _CONF,
                    case_id=case_id, evidence_class=_EC)
        if author_email:
            current_email = await actions.pool.fetchval(
                "SELECT a.value #>> '{}' FROM current_assertions a "
                "WHERE a.object_id=$1 AND a.name='email' AND a.source_id=$2 LIMIT 1",
                dev, source_id)
            if current_email != author_email:
                await actions.assert_property(
                    dev, "email", author_email, source_id, observed, _CONF,
                    case_id=case_id, evidence_class=_EC)

    return {"repo": name, "commits": len(commits), "developers": len(dev_info)}


def main() -> None:  # pragma: no cover - CLI
    import asyncio
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "."
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None

    async def run() -> None:
        pool = await create_pool(
            get_settings().database_url, application_name="osiris-script:ingest-gitlog")
        try:
            print(await ingest_repo(Actions(pool), path, limit=limit))
        finally:
            await pool.close()

    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    main()
