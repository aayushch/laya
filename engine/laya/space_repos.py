# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Which repositories a space's ingestion workflow polls.

Every GitHub/Bitbucket ingestion clone reads the global repo list from
``GET /repos``. When it also sends its own n8n workflow id, the engine scopes
that list to the clone's space, so two connections of one platform in two
spaces don't both poll every repo:

- The space has repos assigned (``space_repos``) that pass the request's
  platform/host filter: the clone polls only those.
- Otherwise it polls every repo except those *claimed* by another space. A
  space claims a repo when the repo is assigned to it and the space has its
  own ingestion source of the same platform watching the same server. A repo
  assigned to a space without such a source (an assignment that only steers
  engineer tasks) is claimed by nobody, so it keeps being polled.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

import structlog

from laya.db.sqlite import get_db

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

    Returns ``repos`` unchanged when the workflow id has no ``sources`` row (a
    template run by hand, or a clone registered under another id).
    """
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT space_id, platform FROM sources WHERE workflow_id = ?", (workflow_id,)
    )
    if not rows:
        if workflow_id not in _unknown_workflow_ids:
            _unknown_workflow_ids.add(workflow_id)
            log.info("repos_scope_unknown_workflow", workflow_id=workflow_id)
        return repos
    space_id = rows[0]["space_id"]
    platform = rows[0]["platform"]
    if not space_id:
        return repos

    assigned = {
        r["repo_name"]
        for r in await db.execute_fetchall(
            "SELECT repo_name FROM space_repos WHERE space_id = ?", (space_id,)
        )
    }
    own = [r for r in repos if r.get("name") in assigned]
    if own:
        return own

    candidates = await db.execute_fetchall(
        """SELECT sr.repo_name, s.workflow_id
           FROM space_repos sr
           JOIN sources s ON s.space_id = sr.space_id
           WHERE sr.space_id != ? AND s.platform = ? AND s.source_type = 'ingestion'""",
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
