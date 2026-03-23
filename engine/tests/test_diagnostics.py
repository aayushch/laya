"""Tests for diagnostics export endpoint."""

import io
import json
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import insert_test_event


@pytest.mark.asyncio
class TestDiagnosticsExport:
    """Tests for GET /diagnostics/export."""

    async def test_returns_zip(self, db):
        """Export returns a valid ZIP file."""
        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/diagnostics/export")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "laya-diagnostics.zip" in resp.headers.get("content-disposition", "")

        # Verify it's a valid ZIP
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert zf.testzip() is None

    async def test_contains_system_info(self, db):
        """ZIP contains system_info.json with expected fields."""
        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/diagnostics/export")

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        data = json.loads(zf.read("system_info.json"))
        assert "platform" in data
        assert "python_version" in data
        assert data["engine_version"] == "0.1.0"

    async def test_contains_db_stats(self, db):
        """ZIP contains db_stats.json with table counts."""
        from laya.main import app

        # Seed some data to verify counts
        await insert_test_event(db, event_id="evt_diag")

        # Patch get_db at the diagnostics module's import reference
        with patch("laya.api.diagnostics_api.get_db", return_value=db):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/diagnostics/export")

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        stats = json.loads(zf.read("db_stats.json"))
        assert "tables" in stats
        assert stats["tables"]["events"] >= 1

    async def test_contains_health(self, db):
        """ZIP contains health.json."""
        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/diagnostics/export")

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        health = json.loads(zf.read("health.json"))
        assert "engine" in health or "error" in health

    async def test_redacts_api_keys(self, db, tmp_path):
        """Config files have API keys redacted."""
        from laya.main import app

        # Create a fake config file with a secret
        fake_settings = {"models": {"router": "claude-haiku-4-5-20251001"}, "api_key": "sk-secret-12345"}
        config_file = tmp_path / "settings.json"
        config_file.write_text(json.dumps(fake_settings))

        with patch("laya.api.diagnostics_api.LAYA_CONFIG_FILE", config_file):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/diagnostics/export")

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        settings = json.loads(zf.read("config/settings.json"))
        assert settings["api_key"] == "***REDACTED***"
        assert settings["models"]["router"] == "claude-haiku-4-5-20251001"
