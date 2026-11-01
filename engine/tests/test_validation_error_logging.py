# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Regression tests: 422 logs must not contain request-body credentials (issue #55)."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

SECRET = "laya-test-secret-SHOULD-NOT-APPEAR-IN-LOGS-9f3a"


def _validation_log_kwargs(mock_warning) -> dict:
    for call in mock_warning.call_args_list:
        if call.args and call.args[0] == "request_validation_failed":
            return call.kwargs
    raise AssertionError("request_validation_failed was not logged")


def _logged_blob(mock_warning) -> str:
    return " ".join(repr(call) for call in mock_warning.call_args_list)


@pytest.mark.asyncio
class TestValidationErrorLogging:
    async def _post_422(self, path: str, payload: dict, headers: dict | None = None) -> tuple[object, dict, str]:
        from laya.main import app

        with patch("laya.main.log.warning") as mock_warning:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(path, json=payload, headers=headers or {})
        assert resp.status_code == 422
        return resp, _validation_log_kwargs(mock_warning), _logged_blob(mock_warning)

    async def _put_422(self, path: str, payload: dict, headers: dict | None = None) -> tuple[object, dict, str]:
        from laya.main import app

        with patch("laya.main.log.warning") as mock_warning:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(path, json=payload, headers=headers or {})
        assert resp.status_code == 422
        return resp, _validation_log_kwargs(mock_warning), _logged_blob(mock_warning)

    async def test_api_key_route_does_not_log_secret(self):
        """PUT /settings/api-key 422 must not log the supplied api_key."""
        resp, logged, blob = await self._put_422(
            "/settings/api-key",
            {"api_key": SECRET},
        )
        assert SECRET not in blob
        assert logged["path"] == "/settings/api-key"
        assert logged["method"] == "PUT"
        assert logged["status_code"] == 422
        assert "body" not in logged
        assert logged["errors"]
        detail = resp.json()["detail"]
        assert any(err.get("loc") == ["body", "provider"] for err in detail)

    async def test_custom_providers_route_does_not_log_secret(self):
        """POST /settings/custom-providers 422 must not log api_key."""
        _, logged, blob = await self._post_422(
            "/settings/custom-providers",
            {"base_url": "http://localhost:1234", "api_key": SECRET},
        )
        assert SECRET not in blob
        assert logged["path"] == "/settings/custom-providers"
        assert logged["method"] == "POST"
        assert "body" not in logged

    async def test_connections_route_does_not_log_credentials(self):
        """POST /connections 422 must not log the credentials map."""
        _, logged, blob = await self._post_422(
            "/connections",
            {
                "platform": "jira",
                "credentials": {"email": "user@example.com", "api_token": SECRET},
            },
        )
        assert SECRET not in blob
        assert "user@example.com" not in blob
        assert logged["path"] == "/connections"
        assert logged["method"] == "POST"
        assert "body" not in logged

    async def test_includes_request_id_when_present(self):
        _, logged, blob = await self._put_422(
            "/settings/api-key",
            {"api_key": SECRET},
            headers={"X-Request-ID": "req-55-debug"},
        )
        assert logged["request_id"] == "req-55-debug"
        assert SECRET not in blob

    async def test_omits_request_id_when_absent(self):
        _, logged, _ = await self._put_422(
            "/settings/api-key",
            {"api_key": SECRET},
        )
        assert "request_id" not in logged

    async def test_client_response_still_includes_validation_detail(self):
        """The HTTP 422 body still carries FastAPI's validation detail."""
        resp, _, _ = await self._put_422(
            "/settings/api-key",
            {"provider": 1, "api_key": SECRET},
        )
        detail = resp.json()["detail"]
        assert isinstance(detail, list)
        assert detail
        assert any("provider" in err.get("loc", []) for err in detail)
