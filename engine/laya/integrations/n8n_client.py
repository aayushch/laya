"""n8n REST API client for credential management."""

from __future__ import annotations

import httpx
import structlog

from laya.config import get_n8n_config
from laya.http_client import get_client
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
    resp = await get_client().get(
        f"{_base_url()}/api/v1/credentials",
        headers=_get_headers(),
        timeout=10.0,
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
    resp = await get_client().post(
        f"{_base_url()}/api/v1/credentials",
        headers=_get_headers(),
        json=body,
        timeout=10.0,
    )
    if resp.status_code not in (200, 201):
        raise N8nApiError(resp.status_code, resp.text)
    return resp.json()


async def delete_credential(credential_id: str) -> bool:
    """DELETE /api/v1/credentials/{id} — delete a credential from n8n."""
    resp = await get_client().delete(
        f"{_base_url()}/api/v1/credentials/{credential_id}",
        headers=_get_headers(),
        timeout=10.0,
    )
    if resp.status_code == 404:
        raise N8nApiError(404, f"Credential {credential_id} not found")
    if resp.status_code not in (200, 204):
        raise N8nApiError(resp.status_code, resp.text)
    return True


async def list_workflows() -> list[dict]:
    """GET /api/v1/workflows — list all workflows from n8n."""
    resp = await get_client().get(
        f"{_base_url()}/api/v1/workflows",
        headers=_get_headers(),
        params={"limit": 250},
        timeout=10.0,
    )
    if resp.status_code != 200:
        raise N8nApiError(resp.status_code, resp.text)
    data = resp.json()
    items = data.get("data", data) if isinstance(data, dict) else data
    return items


async def get_workflow(workflow_id: str) -> dict:
    """GET /api/v1/workflows/{id} — fetch full workflow details including nodes."""
    resp = await get_client().get(
        f"{_base_url()}/api/v1/workflows/{workflow_id}",
        headers=_get_headers(),
        timeout=10.0,
    )
    if resp.status_code != 200:
        raise N8nApiError(resp.status_code, resp.text)
    return resp.json()


async def check_workflow_readiness(workflow_id: str) -> dict:
    """Check if a workflow is ready to be activated.

    Fetches full workflow details and inspects each node for missing credentials.
    Returns {"ready": bool, "issues": [str]}.
    """
    wf = await get_workflow(workflow_id)
    issues: list[str] = []

    nodes = wf.get("nodes", [])
    if not nodes:
        issues.append("Workflow has no nodes")
        return {"ready": False, "issues": issues}

    # Fetch all configured credential IDs for quick lookup
    creds = await list_credentials()
    configured_cred_ids = {str(c.get("id", "")) for c in creds}

    for node in nodes:
        node_name = node.get("name", node.get("type", "Unknown"))
        node_creds = node.get("credentials", {})
        # Each credential entry maps a type to {id, name}
        for cred_type, cred_ref in node_creds.items():
            cred_id = str(cred_ref.get("id", ""))
            if not cred_id or cred_id not in configured_cred_ids:
                issues.append(f'"{node_name}" is missing {cred_type} credentials')

    return {"ready": len(issues) == 0, "issues": issues}


_ALLOWED_SETTINGS_KEYS = {"executionOrder", "callerPolicy", "errorWorkflow", "timezone", "saveManualExecutions", "saveExecutionProgress"}


async def update_workflow(workflow_id: str, payload: dict) -> dict:
    """PUT /api/v1/workflows/{id} — update a workflow's nodes, settings, etc."""
    # Strip settings keys that the n8n API rejects as unknown
    if "settings" in payload and isinstance(payload["settings"], dict):
        payload["settings"] = {
            k: v for k, v in payload["settings"].items()
            if k in _ALLOWED_SETTINGS_KEYS
        }
    resp = await get_client().put(
        f"{_base_url()}/api/v1/workflows/{workflow_id}",
        headers=_get_headers(),
        json=payload,
        timeout=10.0,
    )
    if resp.status_code not in (200, 201):
        raise N8nApiError(resp.status_code, resp.text)
    return resp.json()


async def unarchive_workflow(workflow_id: str) -> dict:
    """POST /api/v1/workflows/{id}/unarchive — unarchive a workflow."""
    resp = await get_client().post(
        f"{_base_url()}/api/v1/workflows/{workflow_id}/unarchive",
        headers=_get_headers(),
        timeout=10.0,
    )
    if resp.status_code not in (200, 201):
        raise N8nApiError(resp.status_code, resp.text)
    return resp.json()


async def activate_workflow(workflow_id: str, active: bool) -> dict:
    """Activate or deactivate an n8n workflow.

    Uses POST /api/v1/workflows/{id}/activate or /deactivate.
    """
    action = "activate" if active else "deactivate"
    resp = await get_client().post(
        f"{_base_url()}/api/v1/workflows/{workflow_id}/{action}",
        headers=_get_headers(),
        timeout=10.0,
    )
    if resp.status_code not in (200, 201):
        raise N8nApiError(resp.status_code, resp.text)
    return resp.json()


async def test_api_access() -> dict:
    """Test whether the n8n API is accessible with the current key."""
    try:
        headers = _get_headers()
    except N8nApiKeyMissing:
        return {"status": "no_api_key", "message": "n8n API key not configured"}

    try:
        resp = await get_client().get(
            f"{_base_url()}/api/v1/credentials",
            headers=headers,
            timeout=5.0,
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
