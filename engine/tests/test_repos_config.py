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
