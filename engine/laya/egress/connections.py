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
    if platform != "smtp":
        n8n_credential_id = await _provision_to_n8n(platform, display_name, credentials)
        if n8n_credential_id is None:
            log.warning("n8n_provision_failed", platform=platform, name=display_name)
            # Non-fatal: credentials are in keychain, n8n can be retried

    # Step 4: Record in SQLite
    capabilities = [c.action_type for c in get_capabilities(platform)]
    now = datetime.now(timezone.utc).isoformat()

    db = await get_db()
    await db.execute(
        """INSERT INTO egress_connections
           (connection_id, platform, name, n8n_credential_id, space_id,
            status, capabilities, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            connection_id,
            platform,
            display_name,
            n8n_credential_id,
            space_id,
            "connected",
            json.dumps(capabilities),
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
    )

    return ConnectionResult(
        status="connected",
        connection_id=connection_id,
        capabilities=capabilities,
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

    # Remove from n8n
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
    executor_rows = await db.execute_fetchall(
        """SELECT source_id, name, platform, workflow_id, space_id, created_at
           FROM sources
           WHERE (source_type = 'executor' AND webhook_path IS NOT NULL)
              OR (name LIKE '%Executor%')
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
    domain = creds.get("domain", "").rstrip("/")
    email = creds.get("email", "")
    token = creds.get("apiToken", "")

    if not all([domain, email, token]):
        return False, "Missing required fields: domain, email, apiToken"

    url = f"https://{domain}/rest/api/3/myself"
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
    username = creds.get("username", "")
    password = creds.get("appPassword", "")
    if not all([username, password]):
        return False, "Missing username or appPassword"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.bitbucket.org/2.0/user",
            auth=(username, password),
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

        result = await create_credential(
            name=name,
            n8n_type=platform_config["n8n_type"],
            data=credentials,
            node_type=platform_config["n8n_node"],
        )
        return str(result.get("id", ""))
    except Exception as e:
        log.error("n8n_provision_failed", platform=platform, error=str(e))
        return None
