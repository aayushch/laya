"""Automated n8n provisioning — owner account, API key, workflow import."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path

import httpx
import structlog

from laya.config import get_n8n_config
from laya.security.keychain import get_api_key, has_api_key, store_api_key

log = structlog.get_logger()

# Default owner credentials — read from env (set in docker-compose.yml),
# falling back to hardcoded defaults.
_DEFAULT_EMAIL = os.environ.get("LAYA_N8N_OWNER_EMAIL", "laya@local.host")
_DEFAULT_PASSWORD = os.environ.get("LAYA_N8N_OWNER_PASSWORD", "LayaAutoAdmin2026!")
_DEFAULT_FIRST_NAME = "Laya"
_DEFAULT_LAST_NAME = "Admin"

# Where Laya's bundled n8n workflows live.
# In repo: engine/../../n8n/workflows
# In bundled app: engine/n8n_workflows (sibling to laya/ inside the engine resource)
_ENGINE_ROOT = Path(__file__).parent.parent.parent  # -> engine/ or .../resources/engine/
_REPO_WORKFLOWS = _ENGINE_ROOT.parent / "n8n" / "workflows"
_BUNDLED_WORKFLOWS = _ENGINE_ROOT / "n8n_workflows"
WORKFLOWS_DIR = _REPO_WORKFLOWS if _REPO_WORKFLOWS.is_dir() else _BUNDLED_WORKFLOWS


def _generate_password() -> str:
    """Generate a random 32-char password for the n8n admin account."""
    return secrets.token_urlsafe(32)


async def _wait_for_n8n(base_url: str, timeout: float = 30.0) -> bool:
    """Poll n8n until both /healthz AND /rest/settings respond with 200.

    n8n registers /healthz before the full REST API is loaded, so checking
    only /healthz can return True while /api/v1/ still returns 404.
    /rest/settings becomes available when the full Express app is ready.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                health = await client.get(f"{base_url}/healthz")
                if health.status_code == 200:
                    settings = await client.get(f"{base_url}/rest/settings")
                    if settings.status_code == 200:
                        return True
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        await asyncio.sleep(1.0)
    return False


async def _needs_setup(base_url: str) -> bool | None:
    """Check if n8n needs initial owner setup via GET /rest/settings.

    Returns:
        True — n8n needs setup (no owner account)
        False — n8n is already set up
        None — could not determine (error)
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/rest/settings")
        if resp.status_code == 200:
            data = resp.json()
            user_mgmt = data.get("userManagement", {})
            # n8n returns showSetupOnLoad=true when no owner exists.
            # Empty userManagement dict also means setup is needed (newer n8n).
            if not user_mgmt or user_mgmt.get("showSetupOnLoad", False):
                return True
            return False
        log.warning("n8n_settings_check_unexpected", status=resp.status_code, body=resp.text[:200])
        return None
    except Exception as e:
        log.warning("n8n_settings_check_error", error=str(e))
        return None


async def _try_login(base_url: str, email: str, password: str) -> dict | None:
    """Attempt to login to n8n. Returns cookies on success, None on failure."""
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            resp = await client.post(
                f"{base_url}/rest/login",
                json={"emailOrLdapLoginId": email, "password": password},
            )
        if resp.status_code == 200:
            log.info("n8n_login_success")
            return dict(resp.cookies)
        log.info("n8n_login_rejected", status=resp.status_code, body=resp.text[:200])
        return None
    except Exception as e:
        log.warning("n8n_login_error", error=str(e))
        return None


async def _create_owner(base_url: str, email: str, password: str) -> dict | None:
    """Create the n8n owner account via the internal setup endpoint.

    Returns cookies from the response (auto-login) or None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.post(
                f"{base_url}/rest/owner/setup",
                json={
                    "email": email,
                    "firstName": _DEFAULT_FIRST_NAME,
                    "lastName": _DEFAULT_LAST_NAME,
                    "password": password,
                },
            )
        if resp.status_code == 200:
            log.info("n8n_owner_created", email=email)
            return dict(resp.cookies)
        log.warning("n8n_owner_setup_failed", status=resp.status_code, body=resp.text[:200])
        return None
    except Exception as e:
        log.error("n8n_owner_setup_error", error=str(e))
        return None


async def _create_api_key(base_url: str, cookies: dict) -> str | None:
    """Create an n8n API key using session authentication."""
    try:
        async with httpx.AsyncClient(timeout=10.0, cookies=cookies) as client:
            resp = await client.post(
                f"{base_url}/rest/api-keys/",
                json={
                    "label": "laya-engine",
                    "scopes": [
                        "workflow:create", "workflow:read", "workflow:update",
                        "workflow:delete", "workflow:list", "workflow:execute",
                        "credential:create", "credential:read",
                        "credential:delete", "credential:list",
                    ],
                    "expiresAt": None,
                },
            )
        if resp.status_code in (200, 201):
            body = resp.json()
            # Response may wrap in {"data": {...}} or return directly
            data = body.get("data", body) if isinstance(body, dict) else body
            raw_key = data.get("rawApiKey") or data.get("apiKey", "")
            if raw_key:
                log.info("n8n_api_key_created")
                return raw_key
            log.warning("n8n_api_key_empty_response", data=str(data)[:200])
        else:
            log.warning("n8n_api_key_create_failed", status=resp.status_code, body=resp.text[:200])
        return None
    except Exception as e:
        log.error("n8n_api_key_create_error", error=str(e))
        return None


async def _test_existing_api_key(base_url: str) -> bool:
    """Check if the stored n8n API key is still valid."""
    api_key = get_api_key("n8n")
    if not api_key:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{base_url}/api/v1/credentials",
                headers={"X-N8N-API-KEY": api_key},
            )
        return resp.status_code == 200
    except Exception:
        return False


async def _get_existing_workflow_names(base_url: str, api_key: str) -> set[str] | None:
    """Return the set of workflow names already in n8n.

    Returns None (not empty set) when the API call fails, so callers can
    distinguish between "API unreachable / not ready" and "genuinely empty".
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/api/v1/workflows",
                headers={"X-N8N-API-KEY": api_key},
                params={"limit": 250},
            )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", data) if isinstance(data, dict) else data
            return {w["name"] for w in items if isinstance(w, dict) and "name" in w}
        log.warning("n8n_list_workflows_failed", status=resp.status_code)
        return None
    except Exception as e:
        log.warning("n8n_list_workflows_error", error=str(e))
        return None


async def import_workflows(base_url: str) -> int:
    """Import bundled Laya workflows into n8n via REST API.

    Skips workflows whose name already exists to avoid duplicates.
    Returns the number of workflows successfully imported.
    """
    api_key = get_api_key("n8n")
    if not api_key:
        log.warning("n8n_workflow_import_skipped", reason="no_api_key")
        return 0

    if not WORKFLOWS_DIR.exists():
        log.warning("n8n_workflow_import_skipped", reason="no_workflows_dir", path=str(WORKFLOWS_DIR))
        return 0

    existing_names = await _get_existing_workflow_names(base_url, api_key)
    if existing_names is None:
        # API not ready or key invalid — raise so sync_workflows_background retries
        raise RuntimeError("n8n workflow list unavailable; will retry")
    imported = 0
    headers = {"X-N8N-API-KEY": api_key, "Content-Type": "application/json"}

    for workflow_file in sorted(WORKFLOWS_DIR.glob("*.json")):
        try:
            workflow_data = json.loads(workflow_file.read_text())
            workflow_name = workflow_data.get("name", workflow_file.stem)
            if workflow_name in existing_names:
                log.info("n8n_workflow_already_exists", name=workflow_name)
                continue
            # Strip read-only fields that the n8n API rejects
            for field in ("active", "id", "createdAt", "updatedAt"):
                workflow_data.pop(field, None)
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{base_url}/api/v1/workflows",
                    headers=headers,
                    json=workflow_data,
                )
            if resp.status_code in (200, 201):
                imported += 1
                log.info("n8n_workflow_imported", name=workflow_file.stem)
            else:
                log.warning(
                    "n8n_workflow_import_failed",
                    name=workflow_file.stem,
                    status=resp.status_code,
                )
        except Exception as e:
            log.warning("n8n_workflow_import_error", name=workflow_file.stem, error=str(e))

    return imported


async def sync_workflows_background() -> None:
    """Background task: wait for n8n then import any missing workflows.

    Retries every 15 s for up to 10 minutes, so a slow-starting n8n
    (e.g. Docker cold-start) never causes workflows to be skipped.
    Idempotent — already-imported workflows are skipped by name.
    """
    n8n_config = get_n8n_config()
    base_url = n8n_config["base_url"].rstrip("/")
    deadline = asyncio.get_event_loop().time() + 600  # 10 min

    while asyncio.get_event_loop().time() < deadline:
        try:
            if await _wait_for_n8n(base_url, timeout=5.0) and get_api_key("n8n"):
                imported = await import_workflows(base_url)
                if imported:
                    log.info("n8n_background_workflows_imported", count=imported)
                else:
                    log.debug("n8n_background_workflows_nothing_new")
                return
        except Exception as e:
            log.debug("n8n_background_sync_retry", error=str(e))
        await asyncio.sleep(15.0)

    log.warning("n8n_background_workflow_sync_timeout")


async def ensure_n8n_ready() -> dict:
    """Ensure n8n is fully provisioned with owner account + API key.

    Idempotent — safe to call on every startup. Skips steps already done.

    Returns dict with:
        status: "ready" | "already_configured" | "unreachable" | "error"
        message: human-readable description
        has_api_key: bool
    """
    n8n_config = get_n8n_config()
    base_url = n8n_config["base_url"].rstrip("/")

    # Step 1: Wait for n8n to be healthy
    if not await _wait_for_n8n(base_url, timeout=10.0):
        log.info("n8n_unreachable", base_url=base_url)
        return {
            "status": "unreachable",
            "message": "n8n is not running or unreachable",
            "has_api_key": has_api_key("n8n"),
        }

    # Step 2: Check if we already have a valid API key.
    # Workflow imports for already-running instances are handled by
    # sync_workflows_background() to avoid double-import on startup.
    if await _test_existing_api_key(base_url):
        log.info("n8n_already_configured")
        return {
            "status": "already_configured",
            "message": "n8n is already configured with a valid API key",
            "has_api_key": True,
        }

    # Step 3: Ensure owner account exists and get session cookies.
    # Strategy: try creating owner first (succeeds on fresh n8n, fails if
    # owner already exists), then fall back to login.
    cookies = None
    stored_password = get_api_key("n8n_admin")
    password = stored_password or _DEFAULT_PASSWORD

    # Try creating owner — succeeds on fresh n8n (returns cookies via auto-login)
    log.info("n8n_attempting_owner_setup")
    cookies = await _create_owner(base_url, _DEFAULT_EMAIL, password)
    if cookies:
        # Fresh n8n — owner just created, store password for future logins
        store_api_key("n8n_admin", password)
    else:
        # Owner already exists — login with stored credentials
        log.info("n8n_owner_exists_attempting_login")
        cookies = await _try_login(base_url, _DEFAULT_EMAIL, password)
        if not cookies:
            log.warning("n8n_cannot_authenticate")
            return {
                "status": "error",
                "message": "n8n has an owner account but Laya cannot authenticate. Enter your n8n API key manually.",
                "has_api_key": False,
            }

    # Step 4: Create API key
    raw_key = await _create_api_key(base_url, cookies)
    if not raw_key:
        return {
            "status": "error",
            "message": "Authenticated to n8n but failed to create API key",
            "has_api_key": False,
        }

    # Step 5: Store API key in keychain
    store_api_key("n8n", raw_key)

    # Step 6: Import bundled workflows (best-effort; background task will retry)
    try:
        imported = await import_workflows(base_url)
    except RuntimeError as e:
        log.warning("n8n_workflow_import_deferred", reason=str(e))
        imported = 0
    log.info("n8n_bootstrap_complete", workflows_imported=imported)

    return {
        "status": "ready",
        "message": f"n8n provisioned successfully ({imported} workflows imported)",
        "has_api_key": True,
    }
