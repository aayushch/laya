# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Space scoping of GET /repos for ingestion workflows.

An ingestion workflow passes its own n8n workflow id. The engine maps it to the
source's space and returns the repos that space should watch. A space with the
"Sources watch only assigned repos" switch on gets only its assigned repos
that pass the request's filter, possibly none. A space with the switch off
gets the filtered list minus the repos claimed by switch-on spaces that have
their own ingestion source of the same platform on the same server.

PUT /repos carries a space's assignments through repo renames and drops them
with removed repos. PUT /spaces/{id}/repos replaces a space's assignments in
one step that a poll never sees half done.
"""

import asyncio
import copy
import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

REPOS = {"repos": [
    {"name": "work-api", "path": "/w/api", "platform": "github", "remote_id": "acme/api"},
    {"name": "work-web", "path": "/w/web", "platform": "github", "remote_id": "acme/web"},
    {"name": "dotfiles", "path": "/p/dot", "platform": "github", "remote_id": "me/dotfiles"},
    {"name": "bb-cloud", "path": "/b/c", "platform": "bitbucket", "remote_id": "ws/cloud"},
    {"name": "onprem-a", "path": "/o/a", "platform": "bitbucket",
     "remote_id": "PROJ/a", "host": "bb-a.example.com"},
    {"name": "onprem-b", "path": "/o/b", "platform": "bitbucket_server",
     "remote_id": "PROJ/b", "host": "bb-b.example.com"},
]}


async def _space(db, space_id: str, name: str) -> None:
    await db.execute("INSERT INTO spaces (space_id, name) VALUES (?, ?)", (space_id, name))


async def _source(db, workflow_id: str, platform: str, space_id: str,
                  source_type: str = "ingestion") -> None:
    await db.execute(
        "INSERT INTO sources (source_id, name, platform, workflow_id, space_id, source_type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (f"src_{workflow_id}", f"{platform} {source_type}", platform, workflow_id,
         space_id, source_type),
    )


async def _assign(db, space_id: str, *repo_names: str) -> None:
    for pos, name in enumerate(repo_names):
        await db.execute(
            "INSERT INTO space_repos (space_id, repo_name, position) VALUES (?, ?, ?)",
            (space_id, name, pos),
        )


async def _only_assigned(db, space_id: str, on: bool = True) -> None:
    await db.execute(
        "UPDATE spaces SET ingest_assigned_repos_only = ? WHERE space_id = ?",
        (1 if on else 0, space_id),
    )


async def _server_config(db, platform: str, workflow_id: str, server: str) -> None:
    await db.execute(
        "INSERT INTO metadata (key, value, space_id) VALUES (?, ?, 'default')",
        (f"{platform}-config:{workflow_id}", json.dumps({"server": server})),
    )


async def _get(params: dict, repos: dict = REPOS) -> list[str]:
    with patch("laya.api.settings_api.load_repos", return_value=repos):
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/repos", params=params)
    assert resp.status_code == 200
    return [r["name"] for r in resp.json()["repos"]]


@pytest.mark.asyncio
class TestRepoScopingByWorkflow:
    async def test_no_workflow_id_returns_the_filtered_list(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles")
        await db.commit()

        assert await _get({"platform": "github"}) == ["work-api", "work-web", "dotfiles"]

    async def test_unknown_workflow_id_returns_the_filtered_list(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_template"}) == [
            "work-api", "work-web", "dotfiles",
        ]

    async def test_overlong_workflow_id_is_rejected(self, db):
        with patch("laya.api.settings_api.load_repos", return_value=REPOS):
            from laya.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/repos", params={"platform": "github", "workflow_id": "w" * 65},
                )
        assert resp.status_code == 422

    async def test_unknown_workflow_id_of_real_length_returns_the_filtered_list(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles")
        await _only_assigned(db, "sp_personal")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "u" * 36}) == [
            "work-api", "work-web", "dotfiles",
        ]

    async def test_unknown_workflow_ids_keep_no_state(self, db):
        import laya.space_repos
        from laya.space_repos import scope_repos_to_workflow

        repos = [r for r in REPOS["repos"] if r["platform"] == "github"]
        with patch("laya.space_repos.log") as log:
            for i in range(300):
                assert await scope_repos_to_workflow(f"wf_unknown_{i:03d}", repos) == repos

        assert not hasattr(laya.space_repos, "_unknown_workflow_ids")
        log.debug.assert_called()
        log.info.assert_not_called()

    async def test_space_with_assigned_repos_gets_only_those(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles", "bb-cloud")
        await _only_assigned(db, "sp_personal")
        await db.commit()

        # The platform filter still applies: bb-cloud is assigned but is not a GitHub repo.
        assert await _get({"platform": "github", "workflow_id": "wf_personal_gh"}) == ["dotfiles"]

    async def test_assignments_of_another_platform_do_not_narrow_the_list(self, db):
        """With the switch off, a space's own assignments never narrow its
        list, whatever platform they are on."""
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "bb-cloud")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_personal_gh"}) == [
            "work-api", "work-web", "dotfiles",
        ]

    async def test_repos_claimed_by_another_space_are_left_out(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_default_gh", "github", "default")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles")
        await _only_assigned(db, "sp_personal")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}) == [
            "work-api", "work-web",
        ]
        assert await _get({"platform": "github", "workflow_id": "wf_personal_gh"}) == ["dotfiles"]

    async def test_agent_only_assignment_does_not_claim(self, db):
        """Repos assigned to a space with no ingestion source of this platform
        only steer engineer tasks, so the other spaces keep polling them."""
        await _space(db, "sp_agents", "Agents")
        await _source(db, "wf_default_gh", "github", "default")
        await _source(db, "wf_agents_jira", "jira", "sp_agents")
        await _assign(db, "sp_agents", "work-web")
        await _only_assigned(db, "sp_agents")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}) == [
            "work-api", "work-web", "dotfiles",
        ]

    async def test_executor_source_does_not_claim(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_default_gh", "github", "default")
        await _source(db, "wf_personal_gh_exec", "github", "sp_personal", source_type="executor")
        await _assign(db, "sp_personal", "dotfiles")
        await _only_assigned(db, "sp_personal")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}) == [
            "work-api", "work-web", "dotfiles",
        ]

    async def test_space_own_assignment_wins_over_claims(self, db):
        """A repo assigned to two switch-on spaces that both ingest the
        platform is polled by both; each event lands in whichever space posts
        it first."""
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_default_gh", "github", "default")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "default", "work-api", "dotfiles")
        await _assign(db, "sp_personal", "dotfiles")
        await _only_assigned(db, "default")
        await _only_assigned(db, "sp_personal")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}) == [
            "work-api", "dotfiles",
        ]
        assert await _get({"platform": "github", "workflow_id": "wf_personal_gh"}) == ["dotfiles"]

    async def test_bitbucket_server_claims_match_the_source_server(self, db):
        """Bitbucket Server workflows select repos by host. A repo is claimed
        only by a space whose Bitbucket Server source watches the same server,
        whatever the repo's own platform label says."""
        await _space(db, "sp_other", "Other")
        await _source(db, "wf_default_bbs", "bitbucket_server", "default")
        await _server_config(db, "bitbucket_server", "wf_default_bbs", "https://bb-a.example.com/")
        await _source(db, "wf_other_bbs", "bitbucket_server", "sp_other")
        await _server_config(db, "bitbucket_server", "wf_other_bbs", "https://bb-a.example.com:8443")
        await _assign(db, "sp_other", "onprem-a")
        await _only_assigned(db, "sp_other")
        await db.commit()

        assert await _get({"host": "bb-a.example.com", "workflow_id": "wf_default_bbs"}) == []
        assert await _get({"host": "bb-a.example.com", "workflow_id": "wf_other_bbs"}) == [
            "onprem-a",
        ]

    async def test_bitbucket_server_source_on_another_server_does_not_claim(self, db):
        await _space(db, "sp_other", "Other")
        await _source(db, "wf_default_bbs", "bitbucket_server", "default")
        await _server_config(db, "bitbucket_server", "wf_default_bbs", "https://bb-a.example.com")
        await _source(db, "wf_other_bbs", "bitbucket_server", "sp_other")
        await _server_config(db, "bitbucket_server", "wf_other_bbs", "https://bb-b.example.com")
        await _assign(db, "sp_other", "onprem-a", "onprem-b")
        await _only_assigned(db, "sp_other")
        await db.commit()

        assert await _get({"host": "bb-a.example.com", "workflow_id": "wf_default_bbs"}) == [
            "onprem-a",
        ]

    async def test_assignment_without_switch_does_not_narrow(self, db):
        """With the switch off, assignments only steer engineer tasks: the
        space's source keeps polling the whole filtered list."""
        await _source(db, "wf_default_gh", "github", "default")
        await _assign(db, "default", "work-api")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}) == [
            "work-api", "work-web", "dotfiles",
        ]

    async def test_switch_on_polls_only_assigned_repos(self, db):
        await _source(db, "wf_default_gh", "github", "default")
        await _assign(db, "default", "dotfiles")
        await _only_assigned(db, "default")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}) == ["dotfiles"]

    async def test_switch_on_without_matching_assignment_polls_nothing(self, db):
        """The switch never widens the list: a space whose assignments are
        all filtered out by the request polls no repos."""
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "bb-cloud")
        await _only_assigned(db, "sp_personal")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_personal_gh"}) == []

    async def test_space_with_switch_off_does_not_claim(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_default_gh", "github", "default")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}) == [
            "work-api", "work-web", "dotfiles",
        ]

    async def test_space_with_switch_on_claims(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_default_gh", "github", "default")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles")
        await _only_assigned(db, "sp_personal")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}) == [
            "work-api", "work-web",
        ]

    async def test_bitbucket_cloud_source_follows_the_switch(self, db):
        """A Bitbucket Cloud source whose only assignment is an on-prem repo
        labelled bitbucket. With the switch off it gets the whole filtered
        list. With it on it gets only that repo, which the cloud workflow then
        drops by host, so the source polls nothing: the switch never widens."""
        await _source(db, "wf_default_bb", "bitbucket", "default")
        await _assign(db, "default", "onprem-a")
        await db.commit()

        assert await _get({"platform": "bitbucket", "workflow_id": "wf_default_bb"}) == [
            "bb-cloud", "onprem-a",
        ]

        await _only_assigned(db, "default")
        await db.commit()

        assert await _get({"platform": "bitbucket", "workflow_id": "wf_default_bb"}) == [
            "onprem-a",
        ]

    async def test_paused_space_keeps_its_claims(self, db):
        """A paused space's workflows are deactivated, so the repos it claims
        are polled by nobody until it resumes."""
        await _space(db, "sp_personal", "Personal")
        await db.execute("UPDATE spaces SET paused = 1 WHERE space_id = 'sp_personal'")
        await _source(db, "wf_default_gh", "github", "default")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles")
        await _only_assigned(db, "sp_personal")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}) == [
            "work-api", "work-web",
        ]

    async def test_source_with_failed_workflow_update_does_not_claim(self, db):
        """A clone whose last template update failed may no longer exist in
        n8n, so its space's assignments must not hide repos from the others."""
        await _space(db, "sp_b", "B")
        await _source(db, "wf_default_gh", "github", "default")
        await _source(db, "wf_b_gh", "github", "sp_b")
        await db.execute(
            "UPDATE sources SET workflow_update_error = ? WHERE workflow_id = ?",
            ("Workflow no longer exists in n8n", "wf_b_gh"),
        )
        await _assign(db, "sp_b", "dotfiles")
        await _only_assigned(db, "sp_b")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}) == [
            "work-api", "work-web", "dotfiles",
        ]

    async def test_lookup_failure_returns_the_filtered_list(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles")
        await db.commit()

        with patch("laya.space_repos.invariant_read", side_effect=RuntimeError("db closed")):
            assert await _get({"platform": "github", "workflow_id": "wf_personal_gh"}) == [
                "work-api", "work-web", "dotfiles",
            ]

    async def test_scoping_error_is_logged_with_its_type_and_returns_the_filtered_list(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles")
        await db.commit()

        with patch("laya.space_repos.invariant_read", side_effect=KeyError("space_id")), \
                patch("laya.api.settings_api.log.warning") as warning:
            assert await _get({"platform": "github", "workflow_id": "wf_personal_gh"}) == [
                "work-api", "work-web", "dotfiles",
            ]

        warning.assert_called_once()
        assert warning.call_args.args == ("repos_scope_failed",)
        assert warning.call_args.kwargs["workflow_id"] == "wf_personal_gh"
        assert warning.call_args.kwargs["error_type"] == "KeyError"
        assert warning.call_args.kwargs["exc_info"] is True


def _edit(repo_name: str, **changes) -> dict:
    """A copy of REPOS with the entry called ``repo_name`` updated by ``changes``."""
    new = copy.deepcopy(REPOS)
    for r in new["repos"]:
        if r["name"] == repo_name:
            r.update(changes)
    return new


def _without(*names: str) -> dict:
    return {"repos": [copy.deepcopy(r) for r in REPOS["repos"] if r["name"] not in names]}


async def _put(old: dict, new: dict) -> None:
    """PUT /repos with ``new`` while repos.json still holds ``old``."""
    with patch("laya.api.settings_api.load_repos", return_value=old), \
            patch("laya.api.settings_api.save_repos") as save:
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put("/repos", json=new)
    assert resp.status_code == 200
    save.assert_called_once_with(new)


async def _rows(db) -> list[tuple[str, str, int]]:
    rows = await db.execute_fetchall(
        "SELECT space_id, repo_name, position FROM space_repos ORDER BY space_id, position"
    )
    return [(r["space_id"], r["repo_name"], r["position"]) for r in rows]


@pytest.mark.asyncio
class TestRepoAssignmentsFollowPutRepos:
    """Space assignments are keyed by the repo's editable name, so PUT /repos
    carries them through renames and drops them with removed repos."""

    async def test_rename_keeps_the_assignment_at_its_position(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_default_gh", "github", "default")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "work-web", "dotfiles")
        await _only_assigned(db, "sp_personal")
        await db.commit()

        new = _edit("dotfiles", name="my-dotfiles")
        await _put(REPOS, new)

        assert await _rows(db) == [
            ("sp_personal", "work-web", 0), ("sp_personal", "my-dotfiles", 1),
        ]
        assert await _get({"platform": "github", "workflow_id": "wf_personal_gh"}, new) == [
            "work-web", "my-dotfiles",
        ]
        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}, new) == [
            "work-api",
        ]

    async def test_rename_with_a_new_path_follows_the_remote(self, db):
        await _space(db, "sp_personal", "Personal")
        await _assign(db, "sp_personal", "dotfiles")
        await db.commit()

        await _put(REPOS, _edit("dotfiles", name="dots", path="/home/me/dots"))

        assert await _rows(db) == [("sp_personal", "dots", 0)]

    async def test_removed_repo_leaves_every_space(self, db):
        await _space(db, "sp_personal", "Personal")
        await _assign(db, "default", "work-api", "dotfiles")
        await _assign(db, "sp_personal", "dotfiles")
        await db.commit()

        await _put(REPOS, _without("dotfiles"))

        assert await _rows(db) == [("default", "work-api", 0)]

    async def test_entry_edited_into_another_repo_drops_the_assignment(self, db):
        await _space(db, "sp_personal", "Personal")
        await _assign(db, "sp_personal", "dotfiles")
        await db.commit()

        await _put(REPOS, _edit("dotfiles", name="notes", path="/p/notes", remote_id="me/notes"))

        assert await _rows(db) == []

    async def test_two_entries_on_the_old_path_count_as_a_removal(self, db):
        await _space(db, "sp_personal", "Personal")
        await _assign(db, "sp_personal", "dotfiles")
        await db.commit()

        new = _without("dotfiles")
        new["repos"] += [
            {"name": "dot-a", "path": "/p/dot", "platform": "github", "remote_id": "me/dot-a"},
            {"name": "dot-b", "path": "/p/dot/", "platform": "github", "remote_id": "me/dot-b"},
        ]
        await _put(REPOS, new)

        assert await _rows(db) == []

    async def test_rename_onto_a_name_the_space_already_holds(self, db):
        """A space can hold a name no repo carries. Renaming a repo onto that
        name leaves one row instead of clashing on the primary key."""
        await _space(db, "sp_personal", "Personal")
        await _assign(db, "sp_personal", "my-dotfiles", "dotfiles")
        await db.commit()

        await _put(REPOS, _edit("dotfiles", name="my-dotfiles"))

        assert await _rows(db) == [("sp_personal", "my-dotfiles", 0)]

    async def test_reordered_entries_and_new_paths_leave_assignments_alone(self, db):
        await _space(db, "sp_personal", "Personal")
        await _assign(db, "default", "work-api", "onprem-a")
        await _assign(db, "sp_personal", "dotfiles")
        await db.commit()
        before = await _rows(db)

        new = {"repos": list(reversed(_edit("dotfiles", path="/home/me/dotfiles")["repos"]))}
        await _put(REPOS, new)

        assert await _rows(db) == before


async def _put_space_repos(space_id: str, repo_names: list[str]):
    from laya.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.put(f"/spaces/{space_id}/repos", json={"repo_names": repo_names})


@pytest.mark.asyncio
class TestPutSpaceRepos:
    async def test_reassignment_is_never_seen_half_applied(self, db):
        """PUT /spaces/{id}/repos deletes a space's assignments, then inserts
        the new ones. A poll that lands in between sees the old or the new
        assignments, never a space with none."""
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_default_gh", "github", "default")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles")
        await _only_assigned(db, "sp_personal")
        await db.commit()

        deleted = asyncio.Event()
        execute = db.execute

        async def pause_after_delete(sql, *args, **kwargs):
            cursor = await execute(sql, *args, **kwargs)
            if sql.startswith("DELETE FROM space_repos"):
                deleted.set()
                # Holds the writer between its DELETE and its INSERTs so the
                # poll below runs while the assignments are half replaced.
                await asyncio.sleep(0.05)
            return cursor

        async def poll() -> list[str]:
            await deleted.wait()
            return await _get({"platform": "github", "workflow_id": "wf_default_gh"})

        # asyncio.Lock binds to the event loop of its first contended acquire,
        # and every test runs on its own loop. A fresh lock keeps the module's
        # lock unbound, so a later test that waits on it does not fail with
        # "bound to a different event loop".
        with patch("laya.db.sqlite._invariant_lock", asyncio.Lock()), \
                patch.object(db, "execute", pause_after_delete):
            put, polled = await asyncio.gather(
                _put_space_repos("sp_personal", ["dotfiles"]),
                asyncio.wait_for(poll(), timeout=5),
            )

        assert put.status_code == 200
        assert polled == ["work-api", "work-web"]

    async def test_duplicate_repo_names_are_stored_once(self, db):
        await _space(db, "sp_personal", "Personal")
        await db.commit()

        resp = await _put_space_repos("sp_personal", ["dotfiles", "dotfiles"])

        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        assert await _rows(db) == [("sp_personal", "dotfiles", 0)]


class TestMatchRepoChanges:
    def test_same_path_is_a_rename(self):
        from laya.space_repos import match_repo_changes

        old = [{"name": "a", "path": "/r/a/"}]
        new = [{"name": "b", "path": "/r/a"}]
        assert match_repo_changes(old, new) == ({"a": "b"}, set())

    def test_same_remote_on_the_same_host_is_a_rename(self):
        from laya.space_repos import match_repo_changes

        old = [{"name": "a", "path": "/r/a", "platform": "bitbucket_server",
                "remote_id": "P/a", "host": "bb.example.com"}]
        new = [{"name": "b", "path": "/r/b", "platform": "bitbucket_server",
                "remote_id": "P/a", "host": "https://BB.example.com:7990/"}]
        assert match_repo_changes(old, new) == ({"a": "b"}, set())

    def test_same_remote_on_another_platform_or_host_is_a_removal(self):
        from laya.space_repos import match_repo_changes

        old = [{"name": "a", "path": "/r/a", "platform": "github", "remote_id": "o/a"}]
        other_platform = [{"name": "b", "path": "/r/b", "platform": "bitbucket", "remote_id": "o/a"}]
        other_host = [{"name": "b", "path": "/r/b", "platform": "github", "remote_id": "o/a",
                       "host": "ghe.example.com"}]
        assert match_repo_changes(old, other_platform) == ({}, {"a"})
        assert match_repo_changes(old, other_host) == ({}, {"a"})

    def test_empty_path_and_remote_never_match(self):
        from laya.space_repos import match_repo_changes

        old = [{"name": "a", "path": "", "platform": "", "remote_id": ""}]
        new = [{"name": "b", "path": "", "platform": "", "remote_id": ""}]
        assert match_repo_changes(old, new) == ({}, {"a"})

    def test_two_vanished_entries_matching_one_new_entry_are_removals(self):
        from laya.space_repos import match_repo_changes

        old = [{"name": "a", "path": "/r/x"}, {"name": "b", "path": "/r/x"}]
        new = [{"name": "c", "path": "/r/x"}]
        assert match_repo_changes(old, new) == ({}, {"a", "b"})

    def test_names_in_both_lists_need_nothing(self):
        from laya.space_repos import match_repo_changes

        old = [{"name": "a", "path": "/r/a"}, {"name": "a", "path": "/r/a2"},
               {"name": "b", "path": "/r/b"}]
        new = [{"name": "b", "path": "/r/b2"}, {"name": "a", "path": "/r/a"}]
        assert match_repo_changes(old, new) == ({}, set())

    def test_entries_without_a_string_name_are_skipped(self):
        from laya.space_repos import match_repo_changes

        old = [{"name": None, "path": "/r/a"}, {"path": "/r/b"}, {"name": "c", "path": "/r/c"}]
        new = [{"name": 5, "path": "/r/c"}, {"name": "d", "path": "/r/a"}]
        assert match_repo_changes(old, new) == ({}, {"c"})
