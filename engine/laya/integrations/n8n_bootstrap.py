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

log = structlog.get_logger()

# Default owner credentials — falling back to hardcoded defaults.
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
            "credential:create", "credential:read",
            "credential:delete", "credential:list",
        ]

    try:
        resp = await get_client().post(
            f"{base_url}/rest/api-keys",
            json={"label": "laya-engine", "scopes": scopes, "expiresAt": 4102444800},
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


async def _get_workflow_full(base_url: str, api_key: str, workflow_id: str) -> dict | None:
    """Fetch the full workflow definition including node credentials."""
    try:
        resp = await get_client().get(
            f"{base_url}/api/v1/workflows/{workflow_id}",
            headers={"X-N8N-API-KEY": api_key},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


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
        if node.get("credentials"):
            # New workflow already specifies credentials (e.g. placeholder) — skip
            continue
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
    """Load the mapping of workflow name → deployed laya_version."""
    try:
        return json.loads(_VERSIONS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_deployed_versions(versions: dict[str, str]) -> None:
    """Persist the workflow name → deployed laya_version mapping."""
    _VERSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _VERSIONS_FILE.write_text(json.dumps(versions, indent=2) + "\n")


# Fields that n8n's PUT /api/v1/workflows/{id} accepts.
_N8N_PUT_ALLOWED_FIELDS = {"name", "nodes", "connections", "settings", "staticData", "tags"}


async def _update_workflow(
    base_url: str, api_key: str, workflow_id: str, new_data: dict,
) -> bool:
    """Update an existing workflow in-place, preserving credentials and active state.

    Returns True on success.
    """
    headers = {"X-N8N-API-KEY": api_key, "Content-Type": "application/json"}

    # Fetch the full existing workflow to get credentials and active state
    existing = await _get_workflow_full(base_url, api_key, workflow_id)
    if not existing:
        log.warning("n8n_workflow_update_fetch_failed", workflow_id=workflow_id)
        return False

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

    resp = await get_client().put(
        f"{base_url}/api/v1/workflows/{workflow_id}",
        headers=headers,
        json=put_body,
        timeout=10.0,
    )
    if resp.status_code not in (200, 201):
        log.warning("n8n_workflow_update_failed", workflow_id=workflow_id,
                     status=resp.status_code, body=resp.text[:200])
        return False

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

    return True


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

    deployed_versions = _load_deployed_versions()
    templates_needing_update: list[str] = []

    for workflow_file in sorted(WORKFLOWS_DIR.glob("*.json")):
        try:
            workflow_data = json.loads(workflow_file.read_text())
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

    # Propagate template updates to any cloned workflow instances
    changed = 0
    if templates_needing_update:
        try:
            changed = await _propagate_to_clones(base_url, api_key)
            if changed:
                log.info("n8n_clones_updated", count=changed)
        except Exception as e:
            log.warning("n8n_clone_propagation_failed", error=str(e))

    _save_deployed_versions(deployed_versions)
    return changed


async def _propagate_to_clones(base_url: str, api_key: str) -> int:
    """Propagate template workflow updates to all cloned instances.

    Clones are identified by having a connection_id in the sources table.
    For each clone, we find its template (by matching platform + source_type
    to a template workflow name), then apply the template's structural changes
    while preserving the clone's credentials, webhook paths, and name.
    """
    from laya.db.sqlite import get_db
    from laya.integrations.platforms import PLATFORMS

    db = await get_db()
    clone_rows = await db.execute_fetchall(
        """SELECT source_id, name, platform, workflow_id, source_type, webhook_path, connection_id
           FROM sources WHERE connection_id IS NOT NULL AND workflow_id IS NOT NULL""",
    )
    if not clone_rows:
        return 0

    # Build template name → workflow data map from bundled JSON
    template_data_map: dict[str, dict] = {}
    if WORKFLOWS_DIR.exists():
        for wf_file in WORKFLOWS_DIR.glob("*.json"):
            try:
                data = json.loads(wf_file.read_text())
                template_data_map[data.get("name", "")] = data
            except Exception:
                continue

    # Build platform+source_type → template name mapping
    type_to_template: dict[tuple[str, str], str] = {}
    for platform_key, config in PLATFORMS.items():
        for wf_name in config.get("workflows", []):
            source_type = "executor" if "executor" in wf_name.lower() else "ingestion"
            type_to_template[(platform_key, source_type)] = wf_name

    updated = 0
    for row in clone_rows:
        platform = row["platform"]
        source_type = row["source_type"]
        wf_id = row["workflow_id"]
        clone_name = row["name"]

        template_name = type_to_template.get((platform, source_type))
        if not template_name or template_name not in template_data_map:
            continue

        template = template_data_map[template_name]

        # Build update data from template but preserve clone's name
        import copy
        update_data = copy.deepcopy(template)
        update_data["name"] = clone_name

        # Preserve clone's webhook paths in the update data
        clone_webhook_path = row["webhook_path"]
        if clone_webhook_path:
            for node in update_data.get("nodes", []):
                if (node.get("type") == "n8n-nodes-base.webhook"
                        and node.get("parameters", {}).get("httpMethod")):
                    node["parameters"]["path"] = clone_webhook_path
                    break

        # _update_workflow handles credential merging automatically
        if await _update_workflow(base_url, api_key, wf_id, update_data):
            updated += 1
            log.info("n8n_clone_updated", name=clone_name, workflow_id=wf_id)
        else:
            log.warning("n8n_clone_update_failed", name=clone_name, workflow_id=wf_id)

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
