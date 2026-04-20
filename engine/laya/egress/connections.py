"""Connection Broker — single pane of glass for all platform credentials.

Users interact with Laya Settings only. The broker handles:
1. Receiving credentials (API keys, OAuth tokens, SMTP configs)
2. Validating them (test API call to the platform)
3. Provisioning to wherever backends need them (n8n, OS keychain)
4. Tracking connection health
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import httpx
import structlog

from laya.db.sqlite import get_db
from laya.egress.models import Connection, ConnectionResult
from laya.egress.registry import get_capabilities
from laya.integrations.platforms import PLATFORMS

log = structlog.get_logger()

EGRESS_KEYCHAIN_SERVICE = "laya-egress"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_connection(
    platform: str,
    credentials: dict,
    name: str | None = None,
    space_id: str | None = None,
) -> ConnectionResult:
    """Create a new platform connection.

    Steps:
    1. Validate credentials with a test API call
    2. Store in OS keychain (source of truth)
    3. Provision to n8n (create credential via n8n REST API)
    4. Record in SQLite metadata table
    """
    if platform not in PLATFORMS and platform != "smtp":
        return ConnectionResult(status="failed", error=f"Unknown platform: {platform}")

    connection_id = f"conn_{uuid.uuid4().hex[:12]}"
    display_name = name or f"Laya - {PLATFORMS.get(platform, {}).get('label', platform)}"

    # Step 1: Validate
    valid, error = await _validate_credentials(platform, credentials)
    if not valid:
        return ConnectionResult(status="failed", error=error)

    # Step 2: Store in keychain
    if not _store_in_keychain(connection_id, platform, credentials):
        return ConnectionResult(status="failed", error="Failed to store credentials in keychain")

    # Step 3: Provision to n8n (skip for SMTP — handled by SMTP backend directly)
    n8n_credential_id = None
    provision_error = None
    if platform != "smtp":
        n8n_credential_id = await _provision_to_n8n(platform, display_name, credentials)
        if n8n_credential_id is None:
            provision_error = f"Failed to create n8n credential for {platform}"
            log.warning("n8n_provision_failed", platform=platform, name=display_name)

    # Step 4: Clone and activate workflows for this connection
    workflow_errors: list[str] = []
    if n8n_credential_id:
        try:
            activated, workflow_errors = await _clone_workflows_for_connection(
                platform, connection_id, display_name, n8n_credential_id
            )
            log.info("workflows_cloned", platform=platform, count=activated,
                     connection_id=connection_id)
        except Exception as e:
            workflow_errors = [str(e)]
            log.warning("workflow_clone_failed", platform=platform, error=str(e))

    # Determine final status
    all_errors = []
    if provision_error:
        all_errors.append(provision_error)
    all_errors.extend(workflow_errors)

    status = "error" if all_errors else "connected"
    error_message = "; ".join(all_errors) if all_errors else None

    # Step 5: Record in SQLite
    capabilities = [c.action_type for c in get_capabilities(platform)]
    now = datetime.now(timezone.utc).isoformat()

    db = await get_db()
    await db.execute(
        """INSERT INTO egress_connections
           (connection_id, platform, name, n8n_credential_id, space_id,
            status, capabilities, error_message, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            connection_id,
            platform,
            display_name,
            n8n_credential_id,
            space_id,
            status,
            json.dumps(capabilities),
            error_message,
            now,
            now,
        ),
    )
    await db.commit()

    log.info(
        "connection_created",
        connection_id=connection_id,
        platform=platform,
        n8n_credential_id=n8n_credential_id,
        status=status,
    )

    return ConnectionResult(
        status=status,
        connection_id=connection_id,
        capabilities=capabilities,
        error=error_message,
    )


async def remove_connection(connection_id: str) -> None:
    """Remove a platform connection — clean up keychain, n8n, and SQLite."""
    # Virtual executor-source connections — remove the source row
    if connection_id.startswith("exec_"):
        source_id = connection_id.removeprefix("exec_")
        db = await get_db()
        await db.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
        await db.commit()
        log.info("executor_source_removed", source_id=source_id)
        return

    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT platform, n8n_credential_id FROM egress_connections WHERE connection_id = ?",
        (connection_id,),
    )
    if not rows:
        return

    row = rows[0]
    platform = row["platform"]
    n8n_cred_id = row["n8n_credential_id"]

    # Remove from keychain
    _remove_from_keychain(connection_id, platform)

    # Deactivate and delete cloned workflows for this connection
    await _remove_connection_workflows(connection_id)

    # Remove n8n credential
    if n8n_cred_id:
        try:
            from laya.integrations.n8n_client import delete_credential

            await delete_credential(n8n_cred_id)
        except Exception as e:
            log.warning("n8n_credential_delete_failed", error=str(e))

    # Remove from SQLite
    await db.execute(
        "DELETE FROM egress_connections WHERE connection_id = ?",
        (connection_id,),
    )
    await db.commit()

    log.info("connection_removed", connection_id=connection_id, platform=platform)


async def list_all_connections() -> list[Connection]:
    """List all configured platform connections.

    Includes both explicit egress_connections AND executor sources from
    the sources table.  Executor sources are n8n workflows that can send
    emails / messages on behalf of the user but were never registered
    through the connection-broker flow.
    """
    db = await get_db()

    connections: list[Connection] = []

    # 1. Explicit egress connections
    rows = await db.execute_fetchall(
        """SELECT connection_id, platform, name, n8n_credential_id, space_id,
                  status, capabilities, error_message, last_validated_at,
                  created_at, updated_at
           FROM egress_connections
           ORDER BY created_at DESC""",
    )

    seen_platforms: set[str] = set()
    for r in rows:
        connections.append(
            Connection(
                connection_id=r["connection_id"],
                platform=r["platform"],
                name=r["name"],
                status=r["status"],
                capabilities=json.loads(r["capabilities"] or "[]"),
                n8n_credential_id=r["n8n_credential_id"],
                space_id=r["space_id"],
                error_message=r["error_message"],
                last_validated_at=r["last_validated_at"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
        )
        seen_platforms.add(r["platform"])

    # 2. Executor sources registered in the sources table (source_type =
    #    'executor' with a webhook_path, OR name containing "Executor" for
    #    auto-discovered entries that weren't tagged correctly).
    #    Exclude sources owned by a connection (those are managed by egress_connections).
    executor_rows = await db.execute_fetchall(
        """SELECT source_id, name, platform, workflow_id, space_id, created_at
           FROM sources
           WHERE connection_id IS NULL
             AND ((source_type = 'executor' AND webhook_path IS NOT NULL)
                  OR (name LIKE '%Executor%'))
           ORDER BY created_at DESC""",
    )

    for r in executor_rows:
        platform = r["platform"]
        caps = [c.action_type for c in get_capabilities(platform)]
        connections.append(
            Connection(
                connection_id=f"exec_{r['source_id']}",
                platform=platform,
                name=r["name"],
                status="connected",
                capabilities=caps,
                n8n_credential_id=None,
                space_id=r["space_id"],
                created_at=r["created_at"] or "",
            )
        )
        seen_platforms.add(platform)

    return connections


async def test_connection(connection_id: str) -> tuple[bool, str | None]:
    """Test if a connection's credentials are still valid."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT platform FROM egress_connections WHERE connection_id = ?",
        (connection_id,),
    )
    if not rows:
        return False, "Connection not found"

    platform = rows[0]["platform"]
    credentials = _get_from_keychain(connection_id, platform)
    if not credentials:
        return False, "Credentials not found in keychain"

    valid, error = await _validate_credentials(platform, credentials)

    # Update status
    now = datetime.now(timezone.utc).isoformat()
    new_status = "connected" if valid else "error"
    await db.execute(
        """UPDATE egress_connections
           SET status = ?, error_message = ?, last_validated_at = ?, updated_at = ?
           WHERE connection_id = ?""",
        (new_status, error, now, now, connection_id),
    )
    await db.commit()

    return valid, error


# ---------------------------------------------------------------------------
# Credential validation per platform
# ---------------------------------------------------------------------------


async def _validate_credentials(platform: str, credentials: dict) -> tuple[bool, str | None]:
    """Test credentials against the platform's API.

    Returns (True, None) on success or (False, error_message) on failure.
    """
    try:
        if platform == "jira":
            return await _validate_jira(credentials)
        elif platform == "github":
            return await _validate_github(credentials)
        elif platform == "bitbucket":
            return await _validate_bitbucket(credentials)
        elif platform == "slack":
            return await _validate_slack(credentials)
        elif platform == "linear":
            return await _validate_linear(credentials)
        elif platform == "gitlab":
            return await _validate_gitlab(credentials)
        elif platform == "discord":
            return await _validate_discord(credentials)
        elif platform == "smtp":
            return await _validate_smtp(credentials)
        elif platform in ("gmail", "calendar"):
            # OAuth platforms — tokens are pre-validated during OAuth flow
            return True, None
        else:
            # Unknown platform — skip validation
            return True, None
    except Exception as e:
        return False, f"Validation error: {str(e)}"


async def _validate_jira(creds: dict) -> tuple[bool, str | None]:
    """Validate Jira credentials by calling GET /rest/api/3/myself."""
    domain = creds.get("domain", "").strip().rstrip("/")
    email = creds.get("email", "").strip()
    token = creds.get("apiToken", "")

    if not all([domain, email, token]):
        return False, "Missing required fields: domain, email, apiToken"

    domain = domain.rstrip("/")

    if not domain.startswith(("http://", "https://")):
        return False, "Domain must start with https:// (e.g. https://your-company.atlassian.net)"

    url = f"{domain}/rest/api/3/myself"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, auth=(email, token), timeout=10.0)

    if resp.status_code == 200:
        return True, None
    elif resp.status_code == 401:
        return False, "Invalid credentials — check email and API token"
    elif resp.status_code == 403:
        return False, "Credentials valid but insufficient permissions"
    else:
        return False, f"Jira returned HTTP {resp.status_code}"


async def _validate_github(creds: dict) -> tuple[bool, str | None]:
    """Validate GitHub PAT by calling GET /user."""
    token = creds.get("accessToken", "")
    if not token:
        return False, "Missing accessToken"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=10.0,
        )

    if resp.status_code == 200:
        return True, None
    elif resp.status_code == 401:
        return False, "Invalid or expired token"
    else:
        return False, f"GitHub returned HTTP {resp.status_code}"


async def _validate_bitbucket(creds: dict) -> tuple[bool, str | None]:
    """Validate Bitbucket app password by calling GET /2.0/user."""
    email = creds.get("email", "")
    token = creds.get("accessToken", "")
    if not all([email, token]):
        return False, "Missing email or app password"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.bitbucket.org/2.0/user",
            auth=(email, token),
            timeout=10.0,
        )

    if resp.status_code == 200:
        return True, None
    elif resp.status_code == 401:
        return False, "Invalid username or app password"
    else:
        return False, f"Bitbucket returned HTTP {resp.status_code}"


async def _validate_slack(creds: dict) -> tuple[bool, str | None]:
    """Validate Slack bot token by calling auth.test."""
    token = creds.get("accessToken", "")
    if not token:
        return False, "Missing accessToken (bot token)"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )

    data = resp.json()
    if data.get("ok"):
        return True, None
    else:
        return False, f"Slack auth failed: {data.get('error', 'unknown error')}"


async def _validate_linear(creds: dict) -> tuple[bool, str | None]:
    """Validate Linear API key by querying the viewer."""
    key = creds.get("apiKey", "")
    if not key:
        return False, "Missing apiKey"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": key, "Content-Type": "application/json"},
            json={"query": "{ viewer { id name } }"},
            timeout=10.0,
        )

    if resp.status_code == 200:
        data = resp.json()
        if data.get("data", {}).get("viewer"):
            return True, None
        return False, "Invalid API key"
    return False, f"Linear returned HTTP {resp.status_code}"


async def _validate_gitlab(creds: dict) -> tuple[bool, str | None]:
    """Validate GitLab token by calling GET /api/v4/user."""
    token = creds.get("accessToken", "")
    base_url = (creds.get("baseUrl", "https://gitlab.com")).rstrip("/")
    if not token:
        return False, "Missing accessToken"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{base_url}/api/v4/user",
            headers={"PRIVATE-TOKEN": token},
            timeout=10.0,
        )

    if resp.status_code == 200:
        return True, None
    elif resp.status_code == 401:
        return False, "Invalid or expired token"
    else:
        return False, f"GitLab returned HTTP {resp.status_code}"


async def _validate_discord(creds: dict) -> tuple[bool, str | None]:
    """Validate Discord bot token by calling GET /users/@me."""
    token = creds.get("botToken", "")
    if not token:
        return False, "Missing botToken"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {token}"},
            timeout=10.0,
        )

    if resp.status_code == 200:
        return True, None
    elif resp.status_code == 401:
        return False, "Invalid bot token"
    else:
        return False, f"Discord returned HTTP {resp.status_code}"


async def _validate_smtp(creds: dict) -> tuple[bool, str | None]:
    """Validate SMTP credentials by connecting and authenticating."""
    try:
        import aiosmtplib

        port = int(creds.get("smtp_port", 587))
        use_tls = creds.get("use_tls", True)

        # Port 465 = implicit TLS (use_tls=True, no STARTTLS needed)
        # Port 587 = STARTTLS (start_tls=True handles the upgrade automatically)
        # Port 25  = plain (no TLS)
        smtp = aiosmtplib.SMTP(
            hostname=creds.get("smtp_host", ""),
            port=port,
            use_tls=(use_tls and port == 465),
            start_tls=(use_tls and port != 465),
        )
        await smtp.connect()
        await smtp.login(creds.get("username", ""), creds.get("password", ""))
        await smtp.quit()
        return True, None
    except ImportError:
        return False, "aiosmtplib not installed — SMTP support unavailable"
    except Exception as e:
        return False, f"SMTP connection failed: {str(e)}"


# ---------------------------------------------------------------------------
# Keychain helpers
# ---------------------------------------------------------------------------


def _store_in_keychain(connection_id: str, platform: str, credentials: dict) -> bool:
    """Store credentials in OS keychain."""
    try:
        import keyring

        key = f"{platform}:{connection_id}"
        keyring.set_password(EGRESS_KEYCHAIN_SERVICE, key, json.dumps(credentials))
        return True
    except Exception as e:
        log.error("keychain_store_failed", connection_id=connection_id, error=str(e))
        return False


def _get_from_keychain(connection_id: str, platform: str) -> dict | None:
    """Retrieve credentials from OS keychain."""
    try:
        import keyring

        key = f"{platform}:{connection_id}"
        raw = keyring.get_password(EGRESS_KEYCHAIN_SERVICE, key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _remove_from_keychain(connection_id: str, platform: str) -> None:
    """Remove credentials from OS keychain."""
    try:
        import keyring

        key = f"{platform}:{connection_id}"
        keyring.delete_password(EGRESS_KEYCHAIN_SERVICE, key)
    except Exception as e:
        log.warning("keychain_delete_failed", connection_id=connection_id, error=str(e))


# ---------------------------------------------------------------------------
# n8n provisioning
# ---------------------------------------------------------------------------


async def _provision_to_n8n(
    platform: str, name: str, credentials: dict
) -> str | None:
    """Create a credential in n8n and return its ID."""
    platform_config = PLATFORMS.get(platform)
    if not platform_config:
        return None

    try:
        from laya.integrations.n8n_client import create_credential

        # Merge any n8n-specific defaults (e.g. server URL)
        cred_data = {**platform_config.get("n8n_defaults", {}), **credentials}

        result = await create_credential(
            name=name,
            n8n_type=platform_config["n8n_type"],
            data=cred_data,
            node_type=platform_config["n8n_node"],
        )
        return str(result.get("id", ""))
    except Exception as e:
        log.error("n8n_provision_failed", platform=platform, error=str(e))
        return None


async def _clone_workflows_for_connection(
    platform: str,
    connection_id: str,
    connection_name: str,
    n8n_credential_id: str,
) -> tuple[int, list[str]]:
    """Clone bundled workflow templates for a specific connection.

    Idempotent — skips if sources already exist for this connection_id.

    For each template workflow (e.g., "Laya - Gmail Ingestion"):
    1. Read the bundled JSON template
    2. Rename to include connection display name
    3. Update webhook paths to be connection-specific
    4. Inject the connection's n8n credential into matching nodes
    5. Create as a new workflow in n8n via POST
    6. Register as a source with connection_id
    7. Activate the workflow

    Returns (activated_count, error_messages).
    """
    import copy

    from laya.integrations.n8n_bootstrap import (
        WORKFLOWS_DIR,
        _get_error_handler_id,
        _load_deployed_versions,
        _save_deployed_versions,
    )
    from laya.integrations.n8n_client import activate_workflow

    # Idempotency: skip if sources already exist for this connection
    db = await get_db()
    existing = await db.execute_fetchall(
        "SELECT source_id FROM sources WHERE connection_id = ?",
        (connection_id,),
    )
    if existing:
        log.debug("clone_already_exists", connection_id=connection_id, count=len(existing))
        return len(existing), []

    platform_config = PLATFORMS.get(platform, {})
    template_names = platform_config.get("workflows", [])
    if not template_names:
        return 0, []

    n8n_type = platform_config.get("n8n_type", "")
    n8n_node = platform_config.get("n8n_node", "")
    activated = 0
    errors: list[str] = []

    # Build a map of template name → JSON file
    template_files: dict[str, dict] = {}
    if WORKFLOWS_DIR.exists():
        for wf_file in WORKFLOWS_DIR.glob("*.json"):
            try:
                data = json.loads(wf_file.read_text(encoding="utf-8"))
                template_files[data.get("name", "")] = data
            except Exception:
                continue

    api_key = None
    try:
        from laya.security.keychain import get_api_key
        api_key = get_api_key("n8n")
    except Exception:
        pass

    if not api_key:
        return 0, ["n8n API key not configured"]

    from laya.config import get_n8n_config
    from laya.http_client import get_client
    base_url = get_n8n_config()["base_url"].rstrip("/")
    headers = {"X-N8N-API-KEY": api_key, "Content-Type": "application/json"}

    short_id = connection_id.replace("conn_", "")
    platform_label = platform_config.get("label", platform.title())

    # Fetch existing n8n workflows to prevent creating duplicates.
    # Without this check, restarts or retries can create orphan workflows
    # in n8n that the engine doesn't track, leading to double-ingestion.
    from laya.integrations.n8n_bootstrap import _get_existing_workflows
    existing_n8n_workflows = await _get_existing_workflows(base_url, api_key) or {}

    # The shared error handler's workflow ID is written into each ingestion
    # clone's settings.errorWorkflow so node failures route back to the engine.
    # Executor clones don't get wired (egress failures surface through
    # action_cards.last_error).
    error_handler_id = _get_error_handler_id()

    for template_name in template_names:
        template_data = template_files.get(template_name)
        if not template_data:
            errors.append(f"Template \"{template_name}\" not found in bundled workflows")
            continue

        wf_data = copy.deepcopy(template_data)

        # 1. Build workflow name: "Laya Gmail - Personal (Ingestion)"
        wf_type = "Executor" if "executor" in template_name.lower() else "Ingestion"
        if connection_name:
            wf_data["name"] = f"Laya {platform_label} - {connection_name} ({wf_type})"
        else:
            wf_data["name"] = f"Laya {platform_label} - {short_id} ({wf_type})"

        # Wire ingestion clones to the shared error handler so any node failure
        # (bad creds, API rate limit, code exception, engine POST failure) lands
        # in ingestion_errors on the engine.
        if error_handler_id and wf_type == "Ingestion":
            wf_data.setdefault("settings", {})["errorWorkflow"] = error_handler_id

        # 2. Update webhook paths and inject credentials
        for node in wf_data.get("nodes", []):
            # Update primary webhook path to be connection-specific
            if (node.get("type") == "n8n-nodes-base.webhook"
                    and node.get("parameters", {}).get("httpMethod")):
                old_path = node["parameters"].get("path", "")
                if old_path:
                    node["parameters"]["path"] = f"{old_path}-{short_id}"

            # Inject credential into matching nodes
            node_creds = node.get("credentials", {})
            params = node.get("parameters", {})
            node_type = node.get("type", "")
            node_cred_type = params.get("nodeCredentialType")
            # HTTP Request nodes (e.g. Gmail archive/star/mark_read) bind the
            # credential under the key named by nodeCredentialType, which can
            # differ from the platform's native n8n_type — Gmail's native type
            # is "gmailOAuth2" but its HTTP nodes use "gmailOAuth2Api".  Match
            # on the HTTP cred type for this platform and inject under that key,
            # otherwise n8n can't resolve the credential at runtime and the
            # node fails with "Credentials not found".
            from laya.egress.oauth import _PLATFORM_HTTP_CRED_TYPES
            http_cred_type = _PLATFORM_HTTP_CRED_TYPES.get(platform)
            is_http_match = (
                http_cred_type is not None
                and node_type == "n8n-nodes-base.httpRequest"
                and node_cred_type == http_cred_type
            )
            is_native_match = (
                n8n_type in node_creds
                or node_type == n8n_node
                or node_type.startswith(n8n_node)  # matches gmailTrigger, googleCalendarTrigger, etc.
                or node_cred_type == n8n_type
            )
            if is_http_match or is_native_match:
                if "credentials" not in node:
                    node["credentials"] = {}
                cred_key = http_cred_type if is_http_match else n8n_type
                node["credentials"][cred_key] = {
                    "id": n8n_credential_id,
                    "name": connection_name,
                }

        # 3. Create workflow in n8n (or reuse existing if same name already exists)
        target_name = wf_data["name"]
        existing_wf = existing_n8n_workflows.get(target_name)
        if existing_wf:
            # Workflow with this name already exists in n8n — reuse it
            wf_id = str(existing_wf["id"])
            log.info("workflow_clone_reused_existing",
                     name=target_name, id=wf_id, connection_id=connection_id)
        else:
            create_fields = {"name", "nodes", "connections", "settings", "staticData", "tags"}
            create_data = {k: v for k, v in wf_data.items() if k in create_fields}
            try:
                resp = await get_client().post(
                    f"{base_url}/api/v1/workflows",
                    headers=headers,
                    json=create_data,
                    timeout=10.0,
                )
                if resp.status_code not in (200, 201):
                    errors.append(f"Failed to create \"{target_name}\": HTTP {resp.status_code}")
                    continue
                created_wf = resp.json()
                wf_id = str(created_wf.get("id", ""))
            except Exception as e:
                errors.append(f"Failed to create \"{target_name}\": {e}")
                continue

        # 4. Activate. The client-side call occasionally raises even when the
        # server-side activation succeeded (observed on Windows: httpx errors
        # with empty `str(e)`). Verify via GET before surfacing a failure to
        # the user.
        try:
            await activate_workflow(wf_id, active=True)
            activated += 1
            log.info("workflow_cloned_and_activated",
                     name=wf_data["name"], id=wf_id, connection_id=connection_id)
        except Exception as e:
            err_detail = str(e) or f"{type(e).__name__}: {e!r}"

            try:
                verify_resp = await get_client().get(
                    f"{base_url}/api/v1/workflows/{wf_id}",
                    headers=headers,
                    timeout=10.0,
                )
                is_active = (
                    verify_resp.status_code == 200
                    and bool(verify_resp.json().get("active"))
                )
            except Exception:
                is_active = False

            if is_active:
                activated += 1
                log.info("workflow_cloned_and_activated",
                         name=wf_data["name"], id=wf_id, connection_id=connection_id)
            else:
                errors.append(f"Failed to activate \"{wf_data['name']}\": {err_detail}")
                log.warning("workflow_clone_activate_failed",
                            name=wf_data["name"], id=wf_id, error=err_detail)

        # 5. Register as source with connection_id
        is_executor = "executor" in template_name.lower()
        source_type = "executor" if is_executor else "ingestion"
        webhook_path = None
        if is_executor:
            for node in wf_data.get("nodes", []):
                if (node.get("type") == "n8n-nodes-base.webhook"
                        and node.get("parameters", {}).get("httpMethod")):
                    webhook_path = node["parameters"].get("path")
                    break

        db = await get_db()
        source_id = f"src_{uuid.uuid4().hex[:12]}"
        await db.execute(
            """INSERT INTO sources
               (source_id, name, platform, workflow_id, space_id,
                source_type, webhook_path, connection_id)
               VALUES (?, ?, ?, ?, 'default', ?, ?, ?)""",
            (source_id, wf_data["name"], platform, wf_id,
             source_type, webhook_path, connection_id),
        )
        await db.commit()

        # Track deployed version for this template
        bundled_version = (template_data.get("meta") or {}).get("laya_version")
        if bundled_version:
            deployed_versions = _load_deployed_versions()
            deployed_versions[template_name] = bundled_version
            _save_deployed_versions(deployed_versions)

    return activated, errors


async def _remove_connection_workflows(connection_id: str) -> None:
    """Deactivate and delete all cloned n8n workflows owned by a connection."""
    from laya.integrations.n8n_client import activate_workflow

    from laya.config import get_n8n_config
    from laya.http_client import get_client
    from laya.security.keychain import get_api_key

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT workflow_id FROM sources WHERE connection_id = ?",
        (connection_id,),
    )
    if not rows:
        return

    api_key = get_api_key("n8n")
    if not api_key:
        return
    base_url = get_n8n_config()["base_url"].rstrip("/")
    headers = {"X-N8N-API-KEY": api_key}

    for row in rows:
        wf_id = row["workflow_id"]
        if not wf_id:
            continue
        # Deactivate then delete
        try:
            await activate_workflow(wf_id, active=False)
        except Exception:
            pass
        try:
            await get_client().delete(
                f"{base_url}/api/v1/workflows/{wf_id}",
                headers=headers,
                timeout=10.0,
            )
            log.info("workflow_clone_deleted", workflow_id=wf_id,
                     connection_id=connection_id)
        except Exception as e:
            log.warning("workflow_clone_delete_failed",
                        workflow_id=wf_id, error=str(e))

    # Remove source rows
    await db.execute(
        "DELETE FROM sources WHERE connection_id = ?",
        (connection_id,),
    )
    await db.commit()
