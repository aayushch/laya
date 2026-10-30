# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Automated n8n provisioning — owner account, API key, workflow import."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path

import httpx
import structlog

from laya.config import LAYA_DATA_DIR, get_n8n_config
from laya.http_client import get_client
from laya.security.keychain import get_api_key, has_api_key, store_api_key
from laya.security.permissions import FILE_MODE, secure_file, write_private_json

log = structlog.get_logger()

# Default owner credentials. The password is only needed on a *fresh* n8n
# install; it is randomly generated (then persisted to the OS keychain) unless
# the operator pins one via LAYA_N8N_OWNER_PASSWORD. The previous hardcoded
# literal was repo-public — n8n holds every connected platform's OAuth tokens
# and can run arbitrary code via its nodes, so any local process (or a
# DNS-rebinding page, since n8n doesn't validate Host) could log in (review §1.5).
_DEFAULT_EMAIL = os.environ.get("LAYA_N8N_OWNER_EMAIL", "laya@local.host")
_ENV_PASSWORD = os.environ.get("LAYA_N8N_OWNER_PASSWORD")  # optional operator override
_DEFAULT_FIRST_NAME = "Laya"
_DEFAULT_LAST_NAME = "Admin"

# Label of the public API key the bootstrap creates for the engine. n8n
# enforces unique labels per user, so this is also how a stale key from an
# earlier bootstrap is recognised and reclaimed.
_API_KEY_LABEL = "laya-engine"

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
            client = get_client()
            health = await client.get(f"{base_url}/healthz", timeout=3.0)
            if health.status_code == 200:
                settings = await client.get(f"{base_url}/rest/settings", timeout=3.0)
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
        resp = await get_client().get(f"{base_url}/rest/settings", timeout=5.0)
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
        resp = await get_client().post(
            f"{base_url}/rest/login",
            json={"emailOrLdapLoginId": email, "password": password},
            timeout=5.0,
            follow_redirects=True,
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
        resp = await get_client().post(
            f"{base_url}/rest/owner/setup",
            json={
                "email": email,
                "firstName": _DEFAULT_FIRST_NAME,
                "lastName": _DEFAULT_LAST_NAME,
                "password": password,
            },
            timeout=10.0,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            log.info("n8n_owner_created", email=email)
            return dict(resp.cookies)
        log.warning("n8n_owner_setup_failed", status=resp.status_code, body=resp.text[:200])
        return None
    except Exception as e:
        log.error("n8n_owner_setup_error", error=str(e))
        return None


async def _fetch_api_key_scopes(base_url: str, cookies: dict) -> list[str] | None:
    """Fetch the valid API key scopes for the current user's role (n8n 2.x+)."""
    try:
        resp = await get_client().get(
            f"{base_url}/rest/api-keys/scopes",
            cookies=cookies,
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            scopes = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(scopes, list) and scopes:
                log.info("n8n_api_key_scopes_fetched", count=len(scopes))
                return scopes
    except Exception as e:
        log.debug("n8n_api_key_scopes_fetch_error", error=str(e))
    return None


async def _create_api_key(base_url: str, cookies: dict) -> str | None:
    """Create an n8n API key using session authentication.

    Fetches valid scopes from n8n first (2.x+), falls back to hardcoded scopes
    for older versions.
    """
    # First, ask n8n what scopes are valid for this user's role
    scopes = await _fetch_api_key_scopes(base_url, cookies)

    if scopes is None:
        # Fallback for older n8n that doesn't have GET /scopes
        scopes = [
            "workflow:create", "workflow:read", "workflow:update",
            "workflow:delete", "workflow:list", "workflow:execute",
            "credential:create", "credential:read", "credential:update",
            "credential:delete", "credential:list",
        ]

    try:
        resp = await get_client().post(
            f"{base_url}/rest/api-keys",
            json={"label": _API_KEY_LABEL, "scopes": scopes, "expiresAt": 4102444800},
            cookies=cookies,
            timeout=10.0,
        )
        if resp.status_code in (200, 201):
            body = resp.json()
            data = body.get("data", body) if isinstance(body, dict) else body
            raw_key = data.get("rawApiKey") or data.get("apiKey", "")
            if raw_key:
                log.info("n8n_api_key_created")
                return raw_key
            log.warning("n8n_api_key_empty_response", data=str(data)[:200])
        else:
            log.warning("n8n_api_key_create_failed", status=resp.status_code,
                        body=resp.text[:300])
        return None
    except Exception as e:
        log.error("n8n_api_key_create_error", error=str(e))
        return None


async def _reclaim_api_key_label(base_url: str, cookies: dict) -> int:
    """Delete every n8n API key that carries Laya's label. Returns the count deleted.

    Called only after the stored key has been rejected by n8n, so a key still
    registered under the label is one the engine can no longer use: its secret
    is gone from the keychain (n8n shows a key's secret once, at creation).
    Leaving it in place makes the next creation fail, because n8n rejects a
    second key with the same label, and the bootstrap can never recover.

    Best-effort: any failure is logged and returns 0 so the caller still
    attempts creation, which then reports n8n's own error.
    """
    try:
        resp = await get_client().get(
            f"{base_url}/rest/api-keys",
            cookies=cookies,
            timeout=10.0,
        )
        if resp.status_code != 200:
            log.warning("n8n_api_key_list_failed", status=resp.status_code)
            return 0
        body = resp.json()
        keys = body.get("data", body) if isinstance(body, dict) else body
        if not isinstance(keys, list):
            return 0
    except Exception as e:
        log.warning("n8n_api_key_list_error", error=str(e))
        return 0

    deleted = 0
    for key in keys:
        if not isinstance(key, dict) or key.get("label") != _API_KEY_LABEL:
            continue
        key_id = key.get("id")
        if not key_id:
            continue
        try:
            resp = await get_client().delete(
                f"{base_url}/rest/api-keys/{key_id}",
                cookies=cookies,
                timeout=10.0,
            )
            if resp.status_code == 200:
                deleted += 1
                log.info("n8n_stale_api_key_deleted", key_id=key_id)
            else:
                log.warning("n8n_stale_api_key_delete_failed",
                            key_id=key_id, status=resp.status_code, body=resp.text[:300])
        except Exception as e:
            log.warning("n8n_stale_api_key_delete_error", key_id=key_id, error=str(e))
    return deleted


async def _test_existing_api_key(base_url: str) -> bool:
    """Check if the stored n8n API key is still valid."""
    api_key = get_api_key("n8n")
    if not api_key:
        return False
    try:
        resp = await get_client().get(
            f"{base_url}/api/v1/credentials",
            headers={"X-N8N-API-KEY": api_key},
            timeout=5.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


async def _get_existing_workflows(base_url: str, api_key: str) -> dict[str, dict] | None:
    """Return a mapping of workflow name → workflow summary for all workflows in n8n.

    Each value contains at least {id, name, meta}. Returns None (not empty dict)
    when the API call fails, so callers can distinguish "API unreachable" from
    "genuinely empty".
    """
    try:
        resp = await get_client().get(
            f"{base_url}/api/v1/workflows",
            headers={"X-N8N-API-KEY": api_key},
            params={"limit": 250},
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", data) if isinstance(data, dict) else data
            return {
                w["name"]: w
                for w in items
                if isinstance(w, dict) and "name" in w
            }
        log.warning("n8n_list_workflows_failed", status=resp.status_code)
        return None
    except Exception as e:
        log.warning("n8n_list_workflows_error", error=str(e))
        return None


def inject_platform_credential(
    nodes: list[dict], platform: str, cred_id: str, cred_name: str,
) -> int:
    """Bind a connection's n8n credential to every node of a workflow that needs it.

    Used both when a template is cloned for a new connection and when a
    bundled template update is propagated to existing clones, so the same
    nodes are matched in both cases. Returns the number of nodes bound.

    A node matches when any of these hold:
    - it already carries a credential slot of the platform's native n8n type
      (templates ship placeholders keyed by that type);
    - its node type is the platform's native node (or a variant of it, e.g.
      `gmailTrigger` for `gmail`);
    - it is an HTTP Request node whose `nodeCredentialType` is the platform's
      native type, or the platform's HTTP-request credential type from
      `_PLATFORM_HTTP_CRED_TYPES` (bound under that key, which n8n resolves
      at runtime; a credential bound under the wrong key fails with
      "Credentials not found").

    Platforms with no native node (`n8n_node == ""`, e.g. bitbucket_server)
    only match through the credential slot or `nodeCredentialType`; an
    unguarded `startswith("")` would match every node and spray the
    credential across the whole workflow.
    """
    from laya.egress.oauth import _PLATFORM_HTTP_CRED_TYPES
    from laya.integrations.platforms import PLATFORMS

    platform_config = PLATFORMS.get(platform, {})
    n8n_type = platform_config.get("n8n_type", "")
    n8n_node = platform_config.get("n8n_node", "")
    if not n8n_type:
        return 0
    http_cred_type = _PLATFORM_HTTP_CRED_TYPES.get(platform)

    bound = 0
    for node in nodes:
        node_creds = node.get("credentials", {})
        params = node.get("parameters", {})
        node_type = node.get("type", "")
        node_cred_type = params.get("nodeCredentialType")
        is_http_match = (
            http_cred_type is not None
            and node_type == "n8n-nodes-base.httpRequest"
            and node_cred_type == http_cred_type
        )
        is_native_match = (
            n8n_type in node_creds
            or (bool(n8n_node) and (node_type == n8n_node or node_type.startswith(n8n_node)))
            or node_cred_type == n8n_type
        )
        if is_http_match or is_native_match:
            node.setdefault("credentials", {})
            cred_key = http_cred_type if is_http_match else n8n_type
            node["credentials"][cred_key] = {"id": cred_id, "name": cred_name}
            bound += 1
    return bound


def _merge_credentials(old_nodes: list[dict], new_nodes: list[dict]) -> list[dict]:
    """Copy credential bindings from old workflow nodes into new workflow nodes.

    Matches by (node name, node type) first, then falls back to matching by
    node type alone. This preserves user-configured credentials even if a node
    is renamed in an update.
    """
    # Build lookup: (name, type) → credentials, and type → credentials (fallback)
    creds_by_name_type: dict[tuple[str, str], dict] = {}
    creds_by_type: dict[str, dict] = {}
    for node in old_nodes:
        node_creds = node.get("credentials")
        if node_creds:
            key = (node.get("name", ""), node.get("type", ""))
            creds_by_name_type[key] = node_creds
            creds_by_type[node.get("type", "")] = node_creds

    for node in new_nodes:
        existing_creds = node.get("credentials")
        if existing_creds:
            # Check if credentials are real (injected by cloning) or placeholders
            # from the template. Placeholders have id == type name (e.g.
            # "jiraSoftwareCloudApi" as both the key and the id value).
            is_placeholder = any(
                isinstance(v, dict) and v.get("id") == cred_type
                for cred_type, v in existing_creds.items()
            )
            if not is_placeholder:
                continue  # Real credentials already set — keep them

        key = (node.get("name", ""), node.get("type", ""))
        merged = creds_by_name_type.get(key) or creds_by_type.get(node.get("type", ""))
        if merged:
            node["credentials"] = merged

    return new_nodes


# ---------------------------------------------------------------------------
# Local version tracking — stored in ~/.laya/data/workflow_versions.json
# ---------------------------------------------------------------------------
_VERSIONS_FILE = LAYA_DATA_DIR / "workflow_versions.json"


def _load_deployed_versions() -> dict[str, str]:
    """Load the mapping of workflow name -> deployed laya_version."""
    try:
        if _VERSIONS_FILE.exists():
            secure_file(_VERSIONS_FILE, FILE_MODE)
        return json.loads(_VERSIONS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_deployed_versions(versions: dict[str, str]) -> None:
    """Persist the workflow name -> deployed laya_version mapping."""
    write_private_json(_VERSIONS_FILE, versions)


# ---------------------------------------------------------------------------
# Shared Error Handler workflow — singleton, wired as settings.errorWorkflow
# on every ingestion clone so n8n node failures flow to the engine.
# ---------------------------------------------------------------------------
_ERROR_HANDLER_NAME = "Laya - Error Handler"
_ERROR_HANDLER_FILENAME = "laya-error-handler.json"
# Special key stored in workflow_versions.json alongside per-template version
# entries. Value is the workflow ID n8n assigned to the deployed handler.
_ERROR_HANDLER_ID_KEY = "__error_handler_id__"


def _get_error_handler_id() -> str | None:
    """Return the currently-deployed error handler workflow ID, or None."""
    versions = _load_deployed_versions()
    return versions.get(_ERROR_HANDLER_ID_KEY)


# Metadata row (space 'default') holding the last failure to push the bundled
# error-handler template into n8n. The handler is a singleton with no `sources`
# row, so it cannot use the per-clone columns. NULL/absent means "up to date".
ERROR_HANDLER_UPDATE_ERROR_KEY = "n8n-error-handler:update_error"


async def _set_error_handler_update_error(reason: str | None) -> None:
    """Record (or clear, when reason is None) the error handler's last update failure."""
    from laya.db.sqlite import get_db

    try:
        db = await get_db()
        if reason is None:
            await db.execute(
                "DELETE FROM metadata WHERE key = ? AND space_id = 'default'",
                (ERROR_HANDLER_UPDATE_ERROR_KEY,),
            )
        else:
            await db.execute(
                """INSERT INTO metadata (key, value, space_id)
                   VALUES (?, ?, 'default')
                   ON CONFLICT (key, space_id) DO UPDATE SET value = excluded.value""",
                (ERROR_HANDLER_UPDATE_ERROR_KEY, reason),
            )
        await db.commit()
    except Exception as e:
        log.warning("n8n_error_handler_status_write_failed", error=str(e))


async def get_error_handler_update_error() -> str | None:
    """Return the error handler's last recorded update failure, or None."""
    from laya.db.sqlite import get_db

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT value FROM metadata WHERE key = ? AND space_id = 'default'",
        (ERROR_HANDLER_UPDATE_ERROR_KEY,),
    )
    return rows[0]["value"] if rows else None


def error_handler_bundled_version() -> str | None:
    """Return the laya_version of the bundled error-handler template, or None."""
    handler_file = WORKFLOWS_DIR / _ERROR_HANDLER_FILENAME
    try:
        data = json.loads(handler_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    return (data.get("meta") or {}).get("laya_version")


async def _ensure_error_handler_workflow(base_url: str, api_key: str) -> str | None:
    """Make sure the shared "Laya - Error Handler" workflow exists in n8n.

    Idempotent. Called at the top of import_workflows() so the handler's ID is
    known before any ingestion clone update injects settings.errorWorkflow.

    Self-heals: if the handler was deleted in the n8n UI, this re-creates it.
    Version bumps on the bundled handler JSON trigger an in-place update. The
    deployed version is recorded only after n8n accepted the update, so a
    failed push is retried on the next startup.
    """
    if not WORKFLOWS_DIR.exists():
        return None

    handler_file = WORKFLOWS_DIR / _ERROR_HANDLER_FILENAME
    if not handler_file.exists():
        log.warning("n8n_error_handler_template_missing", path=str(handler_file))
        return None

    try:
        handler_data = json.loads(handler_file.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("n8n_error_handler_template_parse_failed", error=str(e))
        return None

    bundled_version = (handler_data.get("meta") or {}).get("laya_version")
    versions = _load_deployed_versions()
    stored_id = versions.get(_ERROR_HANDLER_ID_KEY)

    existing_workflows = await _get_existing_workflows(base_url, api_key)
    if existing_workflows is None:
        log.warning("n8n_error_handler_bootstrap_skipped", reason="n8n_list_failed")
        return stored_id  # Best-effort: keep whatever we had last time.

    existing = existing_workflows.get(_ERROR_HANDLER_NAME)
    if existing:
        handler_id = str(existing["id"])
        deployed_version = versions.get(_ERROR_HANDLER_NAME)
        # Update stored ID (self-heal if user recreated it in the UI)
        if stored_id != handler_id:
            versions[_ERROR_HANDLER_ID_KEY] = handler_id
            _save_deployed_versions(versions)
        # If the bundled template is a newer version than what's deployed, push
        # an in-place update so the payload shape / node logic stays current.
        if bundled_version and deployed_version != bundled_version:
            ok, reason = await _update_workflow(base_url, api_key, handler_id, handler_data)
            if ok:
                versions[_ERROR_HANDLER_NAME] = bundled_version
                _save_deployed_versions(versions)
                await _set_error_handler_update_error(None)
                log.info("n8n_error_handler_updated",
                         workflow_id=handler_id, version=bundled_version)
            else:
                await _set_error_handler_update_error(reason)
                log.warning("n8n_error_handler_update_failed",
                            workflow_id=handler_id, version=bundled_version, reason=reason)
        log.debug("n8n_error_handler_ready", workflow_id=handler_id)
        return handler_id

    # Not present — POST it. n8n only accepts a subset of keys on create.
    create_fields = {"name", "nodes", "connections", "settings", "staticData", "tags"}
    create_body = {k: v for k, v in handler_data.items() if k in create_fields}
    headers = {"X-N8N-API-KEY": api_key, "Content-Type": "application/json"}

    try:
        resp = await get_client().post(
            f"{base_url}/api/v1/workflows",
            headers=headers,
            json=create_body,
            timeout=10.0,
        )
        if resp.status_code not in (200, 201):
            log.warning("n8n_error_handler_create_failed",
                        status=resp.status_code, body=resp.text[:300])
            return None
        created = resp.json()
        data = created.get("data", created) if isinstance(created, dict) else created
        handler_id = str(data.get("id", ""))
        if not handler_id:
            log.warning("n8n_error_handler_create_no_id")
            return None
    except Exception as e:
        log.warning("n8n_error_handler_create_error", error=str(e))
        return None

    # Activate the handler. It only runs when another workflow fails, so
    # leaving it active costs nothing.
    try:
        act_resp = await get_client().post(
            f"{base_url}/api/v1/workflows/{handler_id}/activate",
            headers=headers,
            timeout=10.0,
        )
        if act_resp.status_code not in (200, 201):
            log.warning("n8n_error_handler_activate_failed",
                        workflow_id=handler_id, status=act_resp.status_code)
    except Exception as e:
        log.warning("n8n_error_handler_activate_error", workflow_id=handler_id, error=str(e))

    versions[_ERROR_HANDLER_ID_KEY] = handler_id
    if bundled_version:
        versions[_ERROR_HANDLER_NAME] = bundled_version
    _save_deployed_versions(versions)
    await _set_error_handler_update_error(None)
    log.info("n8n_error_handler_created", workflow_id=handler_id, version=bundled_version)
    return handler_id


# Fields that n8n's PUT /api/v1/workflows/{id} accepts.
_N8N_PUT_ALLOWED_FIELDS = {"name", "nodes", "connections", "settings", "staticData", "tags"}

# User-facing reasons for a failed workflow update. They are shown on the
# Settings workflow list, so they name the situation without stack traces or
# internal ids.
REASON_WORKFLOW_MISSING = "Workflow no longer exists in n8n"
REASON_N8N_UNREACHABLE = "n8n unreachable"


async def _update_workflow(
    base_url: str, api_key: str, workflow_id: str, new_data: dict,
) -> tuple[bool, str | None]:
    """Update an existing workflow in-place, preserving credentials and active state.

    Returns (True, None) on success, or (False, reason) with a short
    user-facing reason the caller can store and display.
    """
    headers = {"X-N8N-API-KEY": api_key, "Content-Type": "application/json"}

    # Fetch the full existing workflow to get credentials and active state
    try:
        fetch_resp = await get_client().get(
            f"{base_url}/api/v1/workflows/{workflow_id}",
            headers={"X-N8N-API-KEY": api_key},
            timeout=10.0,
        )
    except Exception as e:
        log.warning("n8n_workflow_update_fetch_failed", workflow_id=workflow_id, error=str(e))
        return False, REASON_N8N_UNREACHABLE
    if fetch_resp.status_code == 404:
        log.warning("n8n_workflow_update_fetch_failed", workflow_id=workflow_id, status=404)
        return False, REASON_WORKFLOW_MISSING
    if fetch_resp.status_code != 200:
        log.warning("n8n_workflow_update_fetch_failed", workflow_id=workflow_id,
                    status=fetch_resp.status_code)
        return False, f"Couldn't read the workflow from n8n (HTTP {fetch_resp.status_code})"
    existing = fetch_resp.json()

    was_active = existing.get("active", False)

    # Deactivate before updating (n8n requires this for active workflows)
    if was_active:
        try:
            await get_client().post(
                f"{base_url}/api/v1/workflows/{workflow_id}/deactivate",
                headers=headers,
                timeout=10.0,
            )
        except Exception:
            pass  # Best effort — PUT may still work

    # Merge credentials from old nodes into new nodes
    old_nodes = existing.get("nodes", [])
    new_nodes = new_data.get("nodes", [])
    merged_nodes = _merge_credentials(old_nodes, new_nodes)

    # Build PUT body with only the fields n8n accepts
    put_body = {k: v for k, v in new_data.items() if k in _N8N_PUT_ALLOWED_FIELDS}
    put_body["nodes"] = merged_nodes
    # Ensure settings is present (required by n8n) — fall back to existing
    if "settings" not in put_body:
        put_body["settings"] = existing.get("settings", {})

    try:
        resp = await get_client().put(
            f"{base_url}/api/v1/workflows/{workflow_id}",
            headers=headers,
            json=put_body,
            timeout=10.0,
        )
    except Exception as e:
        log.warning("n8n_workflow_update_failed", workflow_id=workflow_id, error=str(e))
        return False, REASON_N8N_UNREACHABLE
    if resp.status_code not in (200, 201):
        log.warning("n8n_workflow_update_failed", workflow_id=workflow_id,
                     status=resp.status_code, body=resp.text[:200])
        detail = resp.text.strip()[:120]
        return False, f"n8n rejected the update (HTTP {resp.status_code}{': ' + detail if detail else ''})"

    # Reactivate if it was active before
    if was_active:
        try:
            act_resp = await get_client().post(
                f"{base_url}/api/v1/workflows/{workflow_id}/activate",
                headers=headers,
                timeout=10.0,
            )
            if act_resp.status_code not in (200, 201):
                log.warning("n8n_workflow_reactivate_failed", workflow_id=workflow_id,
                             status=act_resp.status_code, body=act_resp.text[:200])
            else:
                log.info("n8n_workflow_reactivated", workflow_id=workflow_id)
        except Exception as e:
            log.warning("n8n_workflow_reactivate_failed", workflow_id=workflow_id, error=str(e))

    return True, None


async def import_workflows(base_url: str) -> int:
    """Check bundled workflow versions and propagate updates to cloned instances.

    Workflows are NOT auto-created as templates in n8n. They are only created
    as connection-scoped clones when users connect via Settings > Integrations.

    On each startup, this function:
    1. Compares bundled template versions against deployed version records
    2. If any template has a newer version, propagates updates to all clones
       of that template, preserving credentials and webhook paths
    3. Updates the deployed version records

    Returns the number of clones updated.
    """
    api_key = get_api_key("n8n")
    if not api_key:
        log.warning("n8n_workflow_import_skipped", reason="no_api_key")
        return 0

    if not WORKFLOWS_DIR.exists():
        log.warning("n8n_workflow_import_skipped", reason="no_workflows_dir", path=str(WORKFLOWS_DIR))
        return 0

    # Deploy (or self-heal) the shared error-handler workflow BEFORE touching
    # ingestion templates so its ID is available when _propagate_to_clones
    # injects settings.errorWorkflow into each clone below.
    await _ensure_error_handler_workflow(base_url, api_key)

    deployed_versions = _load_deployed_versions()
    templates_needing_update: list[str] = []

    for workflow_file in sorted(WORKFLOWS_DIR.glob("*.json")):
        # The error handler is a singleton, not a per-connection clone template.
        # _ensure_error_handler_workflow already handles its version lifecycle.
        if workflow_file.name == _ERROR_HANDLER_FILENAME:
            continue
        try:
            workflow_data = json.loads(workflow_file.read_text(encoding="utf-8"))
            workflow_name = workflow_data.get("name", workflow_file.stem)
            bundled_version = (workflow_data.get("meta") or {}).get("laya_version")

            deployed_version = deployed_versions.get(workflow_name)
            if deployed_version == bundled_version:
                log.debug("n8n_template_up_to_date", name=workflow_name,
                          version=bundled_version)
                continue

            log.info("n8n_template_version_changed", name=workflow_name,
                     old_version=deployed_version, new_version=bundled_version)
            templates_needing_update.append(workflow_name)

            # Update the deployed version record
            if bundled_version:
                deployed_versions[workflow_name] = bundled_version
        except Exception as e:
            log.warning("n8n_workflow_version_check_error", name=workflow_file.stem, error=str(e))

    # Propagate template updates to any cloned workflow instances. Clones
    # flagged by an earlier failed push are retried here too, even when their
    # template's version did not change. The version record is written only
    # when propagation ran to completion: a clone that failed inside it is
    # flagged on its own row, but if propagation itself raised (n8n or the
    # database unavailable) nothing was flagged, so the versions must stay
    # unrecorded for the next startup to try again.
    changed = 0
    try:
        changed = await _propagate_to_clones(base_url, api_key, templates_needing_update)
        if changed:
            log.info("n8n_clones_updated", count=changed)
    except Exception as e:
        log.warning("n8n_clone_propagation_failed", error=str(e))
        return 0

    _save_deployed_versions(deployed_versions)

    # Every clone and the error handler have now been pushed (or flagged), so
    # this is the moment the token can start being enforced.
    from laya.security.n8n_auth import evaluate_readiness
    await evaluate_readiness()
    return changed


class PropagationContext:
    """Everything needed to rebuild any clone from its bundled template."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        template_data_map: dict[str, dict],
        type_to_template: dict[tuple[str, str], str],
        conn_cred_map: dict[str, tuple[str, str]],
        error_handler_id: str | None,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.template_data_map = template_data_map
        self.type_to_template = type_to_template
        self.conn_cred_map = conn_cred_map
        self.error_handler_id = error_handler_id

    def template_for(self, row) -> dict | None:
        """Return the bundled template a clone row was created from, or None."""
        template_name = self.type_to_template.get((row["platform"], row["source_type"]))
        if not template_name:
            return None
        return self.template_data_map.get(template_name)


async def load_propagation_context(base_url: str, api_key: str) -> PropagationContext:
    """Load the bundled templates and connection credentials that clone updates need."""
    from laya.db.sqlite import get_db
    from laya.integrations.platforms import PLATFORMS

    template_data_map: dict[str, dict] = {}
    if WORKFLOWS_DIR.exists():
        for wf_file in WORKFLOWS_DIR.glob("*.json"):
            try:
                data = json.loads(wf_file.read_text(encoding="utf-8"))
                template_data_map[data.get("name", "")] = data
            except Exception:
                continue

    type_to_template: dict[tuple[str, str], str] = {}
    for platform_key, config in PLATFORMS.items():
        for wf_name in config.get("workflows", []):
            source_type = "executor" if "executor" in wf_name.lower() else "ingestion"
            type_to_template[(platform_key, source_type)] = wf_name

    db = await get_db()
    cred_rows = await db.execute_fetchall(
        "SELECT connection_id, n8n_credential_id, name FROM egress_connections",
    )
    conn_cred_map: dict[str, tuple[str, str]] = {}
    for cr in cred_rows:
        if cr["n8n_credential_id"]:
            conn_cred_map[cr["connection_id"]] = (cr["n8n_credential_id"], cr["name"] or "")

    return PropagationContext(
        base_url=base_url,
        api_key=api_key,
        template_data_map=template_data_map,
        type_to_template=type_to_template,
        conn_cred_map=conn_cred_map,
        error_handler_id=_get_error_handler_id(),
    )


async def _record_clone_update_result(source_id: str, reason: str | None) -> None:
    """Store a clone's last update failure on its `sources` row, or clear it."""
    from laya.db.sqlite import get_db
    from laya.db.timeutil import db_now

    db = await get_db()
    if reason is None:
        await db.execute(
            "UPDATE sources SET workflow_update_error = NULL, workflow_update_failed_at = NULL "
            "WHERE source_id = ?",
            (source_id,),
        )
    else:
        await db.execute(
            "UPDATE sources SET workflow_update_error = ?, workflow_update_failed_at = ? "
            "WHERE source_id = ?",
            (reason, db_now(), source_id),
        )
    await db.commit()


async def update_clone(row, template: dict, ctx: PropagationContext) -> tuple[bool, str | None]:
    """Push the bundled template onto one clone and record the outcome on its row.

    The clone keeps its name, its executor webhook path and its connection's
    credential; everything else (nodes, connections, settings) comes from the
    template. Ingestion clones are wired to the shared error handler. Returns
    (True, None) on success or (False, reason) with the stored reason.
    """
    import copy

    source_type = row["source_type"]
    wf_id = row["workflow_id"]
    clone_name = row["name"]

    try:
        update_data = copy.deepcopy(template)
        update_data["name"] = clone_name

        # Ingestion clones get settings.errorWorkflow so their node failures
        # route to the shared Laya error handler. Executor clones run on
        # user-initiated egress and their failures already surface via
        # action_cards.last_error.
        if ctx.error_handler_id and source_type == "ingestion":
            update_data.setdefault("settings", {})["errorWorkflow"] = ctx.error_handler_id

        clone_webhook_path = row["webhook_path"]
        if clone_webhook_path:
            for node in update_data.get("nodes", []):
                if (node.get("type") == "n8n-nodes-base.webhook"
                        and node.get("parameters", {}).get("httpMethod")):
                    node["parameters"]["path"] = clone_webhook_path
                    break

        # Bind the connection's real credential directly from the template.
        # This is more reliable than merging from the old nodes, which may
        # still carry placeholders from an earlier broken sync.
        cred_info = ctx.conn_cred_map.get(row["connection_id"])
        if cred_info:
            cred_id, cred_name = cred_info
            inject_platform_credential(
                update_data.get("nodes", []), row["platform"], cred_id, cred_name,
            )

        ok, reason = await _update_workflow(ctx.base_url, ctx.api_key, wf_id, update_data)
    except Exception as e:
        log.warning("n8n_clone_update_error", name=clone_name, workflow_id=wf_id, error=str(e))
        ok, reason = False, REASON_N8N_UNREACHABLE

    await _record_clone_update_result(row["source_id"], reason)
    if ok:
        log.info("n8n_clone_updated", name=clone_name, workflow_id=wf_id)
    else:
        log.warning("n8n_clone_update_failed", name=clone_name, workflow_id=wf_id, reason=reason)
    return ok, reason


async def _propagate_to_clones(
    base_url: str, api_key: str, changed_templates: list[str], *, force: bool = False,
) -> int:
    """Push bundled template updates to the cloned workflow instances.

    Clones are the `sources` rows that carry a connection_id. A clone is
    pushed when its template is in `changed_templates`, when its row is
    flagged with a previous update failure, or when `force` is set (every
    clone). Returns the number of clones updated successfully.
    """
    from laya.db.sqlite import get_db

    db = await get_db()
    clone_rows = await db.execute_fetchall(
        """SELECT source_id, name, platform, workflow_id, source_type, webhook_path,
                  connection_id, workflow_update_error
           FROM sources WHERE connection_id IS NOT NULL AND workflow_id IS NOT NULL""",
    )
    if not clone_rows:
        return 0

    ctx = await load_propagation_context(base_url, api_key)
    if not ctx.error_handler_id:
        log.info("n8n_propagate_no_error_handler_id")

    updated = 0
    for row in clone_rows:
        template_name = ctx.type_to_template.get((row["platform"], row["source_type"]))
        template = ctx.template_data_map.get(template_name) if template_name else None
        if not template:
            continue
        flagged = row["workflow_update_error"] is not None
        if not (force or flagged or template_name in changed_templates):
            continue

        ok, _ = await update_clone(row, template, ctx)
        if ok:
            updated += 1

    return updated


async def sync_workflows_background() -> None:
    """Background task: wait for n8n then import/update workflows.

    Retries every 15 s for up to 10 minutes, so a slow-starting n8n
    never causes workflows to be skipped.
    Idempotent — up-to-date workflows are skipped by version.
    """
    n8n_config = get_n8n_config()
    base_url = n8n_config["base_url"].rstrip("/")
    deadline = asyncio.get_event_loop().time() + 600  # 10 min

    while asyncio.get_event_loop().time() < deadline:
        try:
            if await _wait_for_n8n(base_url, timeout=5.0) and get_api_key("n8n"):
                imported = await import_workflows(base_url)
                if imported:
                    log.info("n8n_background_workflows_synced", count=imported)
                else:
                    log.debug("n8n_background_workflows_up_to_date")
                return
        except Exception as e:
            log.debug("n8n_background_sync_retry", error=str(e))
        await asyncio.sleep(15.0)

    log.warning("n8n_background_workflow_sync_timeout")


async def provision_n8n_background() -> None:
    """Provision n8n (owner account + API key) without blocking engine startup.

    Called as a fire-and-forget task from the FastAPI lifespan instead of
    awaiting ensure_n8n_ready() inline — n8n can take minutes to finish its DB
    migrations on a slow/first launch, and blocking the lifespan keeps /health
    down past the Tauri shell's readiness budget (spurious "engine not
    responding" + retry loop).

    Retries until ensure_n8n_ready() reaches a terminal status or a 10-minute
    deadline. Only "unreachable" is retried; "ready"/"already_configured" mean
    success and "error" means manual intervention is needed (retrying won't
    help). Idempotent, like ensure_n8n_ready() itself.
    """
    deadline = asyncio.get_event_loop().time() + 600  # 10 min
    while asyncio.get_event_loop().time() < deadline:
        try:
            result = await ensure_n8n_ready()
            log.info("n8n_bootstrap", **result)
            if result.get("status") != "unreachable":
                return
        except Exception as e:
            log.warning("n8n_bootstrap_failed", error=str(e))
        await asyncio.sleep(15.0)

    log.warning("n8n_bootstrap_deadline_exceeded")


async def ensure_n8n_ready(*, _retries: int = 3, _backoff: float = 10.0) -> dict:
    """Ensure n8n is fully provisioned with owner account + API key.

    Idempotent — safe to call on every startup. Skips steps already done.

    On first launch n8n's REST API may still be initialising when the engine
    starts (the Tauri shell waits, but there's no hard guarantee).  If n8n is
    unreachable we retry with exponential backoff before giving up.

    Returns dict with:
        status: "ready" | "already_configured" | "unreachable" | "error"
        message: human-readable description
        has_api_key: bool
    """
    n8n_config = get_n8n_config()
    base_url = n8n_config["base_url"].rstrip("/")

    # Step 1: Wait for n8n to be healthy, with retries.
    # First attempt uses a short timeout; subsequent retries use longer
    # backoff periods to give n8n time to finish DB initialisation.
    for attempt in range(_retries):
        timeout = 10.0 + (_backoff * attempt)
        if await _wait_for_n8n(base_url, timeout=timeout):
            break
        if attempt < _retries - 1:
            log.info("n8n_not_ready_retrying", attempt=attempt + 1, next_timeout=timeout + _backoff)
            await asyncio.sleep(_backoff)
    else:
        log.info("n8n_unreachable", base_url=base_url, retries=_retries)
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
    # Existing installs reuse their keychain password; a pinned env password
    # wins on first boot; otherwise generate a fresh random one (review §1.5).
    password = stored_password or _ENV_PASSWORD or _generate_password()

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

    # Step 4: Create API key. Step 2 established that the stored key (if any)
    # no longer authenticates, so a key n8n still holds under Laya's label is
    # unusable and must go first, or n8n rejects the new one as a duplicate.
    await _reclaim_api_key_label(base_url, cookies)
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
