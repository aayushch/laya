# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Space scoping of GET /repos for ingestion workflows.

An ingestion workflow passes its own n8n workflow id. The engine maps it to the
source's space and returns the repos that space should watch: the space's
assigned repos when it has some for this platform, otherwise every repo except
those another space with its own ingestion source of the same platform has
claimed by assignment.
"""

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


async def _server_config(db, platform: str, workflow_id: str, server: str) -> None:
    await db.execute(
        "INSERT INTO metadata (key, value, space_id) VALUES (?, ?, 'default')",
        (f"{platform}-config:{workflow_id}", json.dumps({"server": server})),
    )


async def _get(params: dict) -> list[str]:
    with patch("laya.api.settings_api.load_repos", return_value=REPOS):
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

    async def test_space_with_assigned_repos_gets_only_those(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles", "bb-cloud")
        await db.commit()

        # The platform filter still applies: bb-cloud is assigned but is not a GitHub repo.
        assert await _get({"platform": "github", "workflow_id": "wf_personal_gh"}) == ["dotfiles"]

    async def test_assignments_of_another_platform_do_not_narrow_the_list(self, db):
        """A space whose assigned repos are all on other platforms has no
        assignment for this request, so it falls back to the unclaimed list
        instead of polling nothing."""
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
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}) == [
            "work-api", "work-web", "dotfiles",
        ]

    async def test_executor_source_does_not_claim(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_default_gh", "github", "default")
        await _source(db, "wf_personal_gh_exec", "github", "sp_personal", source_type="executor")
        await _assign(db, "sp_personal", "dotfiles")
        await db.commit()

        assert await _get({"platform": "github", "workflow_id": "wf_default_gh"}) == [
            "work-api", "work-web", "dotfiles",
        ]

    async def test_space_own_assignment_wins_over_claims(self, db):
        """A repo assigned to two spaces that both ingest the platform is
        polled by both: each space asked for it explicitly."""
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_default_gh", "github", "default")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "default", "work-api", "dotfiles")
        await _assign(db, "sp_personal", "dotfiles")
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
        await db.commit()

        assert await _get({"host": "bb-a.example.com", "workflow_id": "wf_default_bbs"}) == [
            "onprem-a",
        ]

    async def test_lookup_failure_returns_the_filtered_list(self, db):
        await _space(db, "sp_personal", "Personal")
        await _source(db, "wf_personal_gh", "github", "sp_personal")
        await _assign(db, "sp_personal", "dotfiles")
        await db.commit()

        with patch("laya.space_repos.get_db", side_effect=RuntimeError("db closed")):
            assert await _get({"platform": "github", "workflow_id": "wf_personal_gh"}) == [
                "work-api", "work-web", "dotfiles",
            ]
