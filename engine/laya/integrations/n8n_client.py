"""n8n REST API client for credential management."""

from __future__ import annotations

import httpx
import structlog

from laya.config import get_n8n_config
from laya.security.keychain import get_api_key

log = structlog.get_logger()


class N8nApiError(Exception):
    """Raised when n8n API returns an error."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"n8n API error {status_code}: {detail}")


class N8nApiKeyMissing(Exception):
    """Raised when no n8n API key is configured."""

    pass


def _get_headers() -> dict[str, str]:
    """Build request headers with the n8n API key from keychain."""
    api_key = get_api_key("n8n")
    if not api_key:
        raise N8nApiKeyMissing("n8n API key not configured. Add it in Settings -> Integrations.")
    return {
        "X-N8N-API-KEY": api_key,
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    """Get the n8n base URL from config."""
    return get_n8n_config()["base_url"].rstrip("/")


async def list_credentials() -> list[dict]:
    """GET /api/v1/credentials — list all credentials from n8n."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{_base_url()}/api/v1/credentials",
            headers=_get_headers(),
        )
    if resp.status_code != 200:
        raise N8nApiError(resp.status_code, resp.text)
    data = resp.json()
    # n8n may wrap in {"data": [...]} or return array directly
    return data.get("data", data) if isinstance(data, dict) else data


async def create_credential(
    name: str,
    n8n_type: str,
    data: dict,
    node_type: str,
) -> dict:
    """POST /api/v1/credentials — create a credential in n8n."""
    body = {
        "name": name,
        "type": n8n_type,
        "data": data,
        "nodesAccess": [{"nodeType": node_type}],
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{_base_url()}/api/v1/credentials",
            headers=_get_headers(),
            json=body,
        )
    if resp.status_code not in (200, 201):
        raise N8nApiError(resp.status_code, resp.text)
    return resp.json()


async def delete_credential(credential_id: str) -> bool:
    """DELETE /api/v1/credentials/{id} — delete a credential from n8n."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(
            f"{_base_url()}/api/v1/credentials/{credential_id}",
            headers=_get_headers(),
        )
    if resp.status_code == 404:
        raise N8nApiError(404, f"Credential {credential_id} not found")
    if resp.status_code not in (200, 204):
        raise N8nApiError(resp.status_code, resp.text)
    return True


async def test_api_access() -> dict:
    """Test whether the n8n API is accessible with the current key."""
    try:
        headers = _get_headers()
    except N8nApiKeyMissing:
        return {"status": "no_api_key", "message": "n8n API key not configured"}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_base_url()}/api/v1/credentials",
                headers=headers,
            )
        if resp.status_code == 200:
            return {"status": "connected", "message": "n8n API accessible"}
        elif resp.status_code == 401:
            return {"status": "unauthorized", "message": "Invalid n8n API key"}
        else:
            return {"status": "error", "message": f"n8n returned {resp.status_code}"}
    except httpx.ConnectError:
        return {"status": "unreachable", "message": "Cannot connect to n8n"}
    except httpx.TimeoutException:
        return {"status": "timeout", "message": "n8n connection timed out"}
