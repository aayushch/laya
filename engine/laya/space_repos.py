# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Which repositories a space's ingestion workflow polls.

Every GitHub/Bitbucket ingestion clone reads the global repo list from
``GET /repos``. When it also sends its own n8n workflow id, the engine scopes
that list to the clone's space according to the space's "Sources watch only
assigned repos" switch (``spaces.ingest_assigned_repos_only``, off by default):

- Switch on: the clone polls only the space's assigned repos (``space_repos``)
  that pass the request's platform/host filter. That can be none; the list is
  never widened.
- Switch off: the space's assignments only steer engineer tasks. The clone
  polls the filtered list minus the repos *claimed* by other spaces. A space
  claims a repo when its switch is on, the repo is assigned to it, and it has
  its own ingestion source of the same platform watching the same server. A
  paused space keeps its claims, so its repos go unpolled while it is paused.

Assignments are keyed by the repo's name in repos.json; ``PUT /repos`` carries
them through renames and drops them with removed repos
(``match_repo_changes`` / ``apply_repo_changes``).
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

import structlog

from laya.db.sqlite import get_db, transaction

log = structlog.get_logger()

# Workflow ids that asked for repos without a matching ``sources`` row. Each is
# logged once: the clone polls every couple of minutes and the answer doesn't
# change until the source is registered.
_unknown_workflow_ids: set[str] = set()


def normalize_host(value: str) -> str:
    """Reduce a host or base URL to a bare lowercase hostname for comparison.

    Repo entries store a bare hostname (scheme/port stripped at remote-URL parse
    time in the Tauri shell), but callers may hold a full server base URL —
    tolerate scheme, port, path, and trailing slash so both spellings compare equal.
    """
    v = (value or "").strip().lower()
    if "://" in v:
        v = urlparse(v).netloc or v
    return v.split("/", 1)[0].rsplit(":", 1)[0] if v else ""


def _repo_identities(repo: dict) -> set[tuple]:
    """Keys that identify the repo behind a repos.json entry, whatever its name.

    The local path (trailing slash ignored) and the remote (platform, remote_id
    and normalized host) each count when non-empty.
    """
    keys: set[tuple] = set()
    path = repo.get("path")
    if isinstance(path, str) and path.rstrip("/"):
        keys.add(("path", path.rstrip("/")))
    remote_id = repo.get("remote_id")
    if isinstance(remote_id, str) and remote_id:
        host = repo.get("host")
        keys.add((
            "remote",
            repo.get("platform") or "",
            remote_id,
            normalize_host(host if isinstance(host, str) else ""),
        ))
    return keys


def match_repo_changes(
    old_repos: list[dict], new_repos: list[dict]
) -> tuple[dict[str, str], set[str]]:
    """Compare two repos.json lists: (renames old_name -> new_name, removed names).

    Space assignments (``space_repos``) are keyed by the repo's editable name,
    and the Repos settings page PUTs the whole list, so a rename shows up only
    as one name vanishing and another appearing. A rename is recognised by the
    repo's path or remote identity instead: a vanished entry becomes the one
    appeared entry it matches, provided no other vanished entry matches that
    one too. Every other vanished name is removed. Names present in both lists
    need nothing, whatever else changed on them.
    """
    old = [r for r in old_repos if isinstance(r, dict) and isinstance(r.get("name"), str)]
    new = [r for r in new_repos if isinstance(r, dict) and isinstance(r.get("name"), str)]
    old_names = {r["name"] for r in old}
    new_names = {r["name"] for r in new}
    vanished = [(r["name"], _repo_identities(r)) for r in old if r["name"] not in new_names]
    appeared = [(r["name"], _repo_identities(r)) for r in new if r["name"] not in old_names]

    renames: dict[str, str] = {}
    removed: set[str] = set()
    for name, keys in vanished:
        matches = [(a_name, a_keys) for a_name, a_keys in appeared if keys & a_keys]
        if len(matches) == 1:
            a_name, a_keys = matches[0]
            if sum(1 for _, v_keys in vanished if v_keys & a_keys) == 1:
                renames[name] = a_name
                continue
        removed.add(name)
    return renames, removed


async def apply_repo_changes(renames: dict[str, str], removed: set[str]) -> None:
    """Move space assignments to renamed repos and drop those of removed repos."""
    if not renames and not removed:
        return
    async with transaction() as db:
        for old_name, new_name in renames.items():
            await db.execute(
                "UPDATE OR IGNORE space_repos SET repo_name = ? WHERE repo_name = ?",
                (new_name, old_name),
            )
            # Clears the rows the UPDATE skipped because their space already
            # held the new name.
            await db.execute("DELETE FROM space_repos WHERE repo_name = ?", (old_name,))
        for name in removed:
            # Removed names are deleted so that a repo added later under the
            # same name doesn't inherit the assignment.
            await db.execute("DELETE FROM space_repos WHERE repo_name = ?", (name,))
    log.info("repo_assignments_reconciled", renamed=len(renames), removed=len(removed))


async def _source_server_host(db, platform: str, workflow_id: str) -> str:
    """The server host a clone watches, or "" for a cloud platform.

    Host-driven platforms (Bitbucket Server) store the server base URL in the
    ``{platform}-config:{workflow_id}`` metadata row written at clone time;
    cloud platforms have no such row.
    """
    rows = await db.execute_fetchall(
        "SELECT value FROM metadata WHERE key = ? AND space_id = 'default'",
        (f"{platform}-config:{workflow_id}",),
    )
    if not rows:
        return ""
    try:
        cfg = json.loads(rows[0]["value"])
    except (TypeError, ValueError):
        return ""
    server = cfg.get("server") if isinstance(cfg, dict) else None
    return normalize_host(server) if isinstance(server, str) else ""


async def scope_repos_to_workflow(workflow_id: str, repos: list[dict]) -> list[dict]:
    """Narrow an already platform/host-filtered repo list to one clone's space.

    A clone in a space with the switch on gets the space's assigned repos from
    ``repos``, possibly none. Any other clone gets ``repos`` minus the repos
    that switch-on spaces claim. Returns ``repos`` unchanged when the workflow
    id has no ``sources`` row (a template run by hand, or a clone registered
    under another id).
    """
    db = await get_db()
    # LEFT JOIN so a source whose space row is missing still counts as a
    # known source with the switch off, rather than dropping out of the
    # lookup and skipping the claims below.
    rows = await db.execute_fetchall(
        """SELECT s.space_id, s.platform,
                  COALESCE(sp.ingest_assigned_repos_only, 0) AS only_assigned
           FROM sources s
           LEFT JOIN spaces sp ON sp.space_id = s.space_id
           WHERE s.workflow_id = ?""",
        (workflow_id,),
    )
    if not rows:
        if workflow_id not in _unknown_workflow_ids:
            _unknown_workflow_ids.add(workflow_id)
            log.info("repos_scope_unknown_workflow", workflow_id=workflow_id)
        return repos
    space_id = rows[0]["space_id"]
    platform = rows[0]["platform"]

    if rows[0]["only_assigned"]:
        assigned = {
            r["repo_name"]
            for r in await db.execute_fetchall(
                "SELECT repo_name FROM space_repos WHERE space_id = ?", (space_id,)
            )
        }
        return [r for r in repos if r.get("name") in assigned]

    # A source whose last template update failed claims nothing: its clone may
    # no longer exist in n8n, and if it still runs it polls the unscoped list.
    # Letting it claim risks a repo nobody polls; skipping it risks at most a
    # duplicate poll.
    candidates = await db.execute_fetchall(
        """SELECT sr.repo_name, s.workflow_id
           FROM space_repos sr
           JOIN spaces sp ON sp.space_id = sr.space_id
           JOIN sources s ON s.space_id = sr.space_id
           WHERE sr.space_id != ? AND s.platform = ? AND s.source_type = 'ingestion'
             AND s.workflow_update_error IS NULL
             AND sp.ingest_assigned_repos_only = 1""",
        (space_id, platform),
    )
    if not candidates:
        return repos

    own_host = await _source_server_host(db, platform, workflow_id)
    hosts: dict[str, str] = {}
    claimed: set[str] = set()
    for c in candidates:
        other_wf = c["workflow_id"]
        if other_wf not in hosts:
            hosts[other_wf] = await _source_server_host(db, platform, other_wf)
        # A source on a different server never polls this repo, so its space's
        # assignment must not hide the repo from this clone.
        if hosts[other_wf] == own_host:
            claimed.add(c["repo_name"])
    return [r for r in repos if r.get("name") not in claimed]
