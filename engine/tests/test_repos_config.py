# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for repos.json configuration loading and API endpoints."""

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from laya.config import load_repos, save_repos


class TestReposConfig:
    def test_load_repos_creates_default(self, tmp_path):
        """load_repos creates default repos.json if it doesn't exist."""
        repos_file = tmp_path / "repos.json"
        with patch("laya.config.LAYA_REPOS_FILE", repos_file):
            result = load_repos()

        assert result == {"repos": []}
        assert repos_file.exists()

    def test_load_repos_reads_existing(self, tmp_path):
        """load_repos reads existing repos.json."""
        repos_file = tmp_path / "repos.json"
        repos_data = {"repos": [{"name": "test", "path": "/tmp/test", "platform": "github", "remote_id": "org/test"}]}
        repos_file.write_text(json.dumps(repos_data))

        with patch("laya.config.LAYA_REPOS_FILE", repos_file):
            result = load_repos()

        assert len(result["repos"]) == 1
        assert result["repos"][0]["name"] == "test"

    def test_save_repos(self, tmp_path):
        """save_repos writes repos.json."""
        repos_file = tmp_path / "repos.json"
        repos_data = {"repos": [{"name": "myrepo", "path": "/home/user/myrepo", "platform": "", "remote_id": ""}]}

        with patch("laya.config.LAYA_REPOS_FILE", repos_file):
            save_repos(repos_data)

        saved = json.loads(repos_file.read_text())
        assert saved["repos"][0]["name"] == "myrepo"


@pytest.mark.asyncio
class TestReposAPI:
    async def test_get_repos(self, db):
        """GET /repos returns repos config."""
        repos_data = {"repos": [{"name": "test", "path": "/tmp", "platform": "", "remote_id": ""}]}
        with patch("laya.api.settings_api.load_repos", return_value=repos_data):
            from laya.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/repos")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["repos"]) == 1

    async def test_get_repos_host_filter(self, db):
        """GET /repos?host= returns only repos on that host; cloud repos
        (empty host) never match a host filter."""
        repos_data = {"repos": [
            {"name": "cloud", "path": "/c", "platform": "bitbucket", "remote_id": "ws/cloud"},
            {"name": "onprem", "path": "/o", "platform": "bitbucket",
             "remote_id": "src/xrecon", "host": "bb.internal.example.com"},
            {"name": "gh", "path": "/g", "platform": "github",
             "remote_id": "org/gh", "host": "github.com"},
        ]}
        with patch("laya.api.settings_api.load_repos", return_value=repos_data):
            from laya.main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/repos", params={"host": "bb.internal.example.com"})
                resp_url = await client.get(
                    # Callers may pass a full base URL; scheme/port/slash are tolerated
                    "/repos", params={"host": "https://BB.internal.example.com:8443/"}
                )
                resp_both = await client.get(
                    "/repos", params={"platform": "bitbucket", "host": "github.com"}
                )

        assert [r["name"] for r in resp.json()["repos"]] == ["onprem"]
        assert [r["name"] for r in resp_url.json()["repos"]] == ["onprem"]
        assert resp_both.json()["repos"] == []

    async def test_put_repos(self, db):
        """PUT /repos saves repos config."""
        new_repos = {"repos": [{"name": "new", "path": "/new", "platform": "github", "remote_id": "org/new"}]}
        with patch("laya.api.settings_api.save_repos") as mock_save:
            with patch("laya.api.settings_api.load_repos", return_value=new_repos):
                from laya.main import app
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.put("/repos", json=new_repos)

        assert resp.status_code == 200
        mock_save.assert_called_once()
