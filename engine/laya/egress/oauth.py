"""OAuth proxy for Gmail and Microsoft 365.

Handles the full OAuth dance so users never need to touch the n8n dashboard.
Flow: Laya Settings → auth URL → provider consent → callback → token exchange → n8n provisioning.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import structlog

from laya.egress.connections import (
    EGRESS_KEYCHAIN_SERVICE,
    _provision_to_n8n,
    _store_in_keychain,
)
from laya.db.sqlite import get_db
from laya.egress.registry import get_capabilities

log = structlog.get_logger()

# In-memory state store for CSRF protection (state_token -> {platform, timestamp})
_oauth_states: dict[str, dict] = {}
_STATE_TTL = 600  # 10 minutes


# ---------------------------------------------------------------------------
# OAuth provider configs
# ---------------------------------------------------------------------------

OAUTH_PROVIDERS: dict[str, dict] = {
    "gmail": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.readonly",
        ],
        "n8n_type": "gmailOAuth2Api",
    },
    "calendar": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events",
        ],
        "n8n_type": "googleCalendarOAuth2Api",
    },
    "outlook": {
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": [
            "openid",
            "profile",
            "email",
            "https://graph.microsoft.com/Mail.ReadWrite",
            "https://graph.microsoft.com/Mail.Send",
            "offline_access",
        ],
        "n8n_type": "microsoftOutlookOAuth2Api",
    },
    "outlook_calendar": {
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": [
            "openid",
            "profile",
            "email",
            "https://graph.microsoft.com/Calendars.ReadWrite",
            "offline_access",
        ],
        "n8n_type": "microsoftOutlookOAuth2Api",
    },
}


# ---------------------------------------------------------------------------
# OAuth client credentials (stored in keychain or config)
# ---------------------------------------------------------------------------


def _get_oauth_client(platform: str) -> tuple[str, str] | None:
    """Get OAuth client_id and client_secret for a platform.

    These are stored in OS keychain under 'laya-egress:oauth:{platform}:client'.
    Set them via: keyring.set_password("laya-egress", "oauth:gmail:client",
                                       json.dumps({"client_id": "...", "client_secret": "..."}))

    Returns (client_id, client_secret) or None if not configured.
    """
    try:
        import keyring

        raw = keyring.get_password(EGRESS_KEYCHAIN_SERVICE, f"oauth:{platform}:client")
        if raw:
            data = json.loads(raw)
            return data.get("client_id"), data.get("client_secret")
    except Exception:
        pass
    return None


def store_oauth_client(platform: str, client_id: str, client_secret: str) -> bool:
    """Store OAuth client credentials for a platform."""
    try:
        import keyring

        keyring.set_password(
            EGRESS_KEYCHAIN_SERVICE,
            f"oauth:{platform}:client",
            json.dumps({"client_id": client_id, "client_secret": client_secret}),
        )
        return True
    except Exception as e:
        log.error("oauth_client_store_failed", platform=platform, error=str(e))
        return False


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------


def build_auth_url(platform: str, redirect_uri: str) -> dict:
    """Build the OAuth authorization URL for a platform.

    Returns:
        {"auth_url": "https://...", "state": "..."} on success
        {"error": "..."} on failure
    """
    provider = OAUTH_PROVIDERS.get(platform)
    if not provider:
        return {"error": f"OAuth not supported for platform: {platform}"}

    client = _get_oauth_client(platform)
    if not client:
        return {
            "error": (
                f"OAuth client not configured for {platform}. "
                "Store client_id and client_secret in Settings > Integrations > OAuth Apps."
            ),
            "needs_setup": True,
        }

    client_id, _ = client

    # Generate CSRF state token
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {"platform": platform, "timestamp": time.time()}
    _cleanup_expired_states()

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(provider["scopes"]),
        "state": state,
        "access_type": "offline",  # Google: request refresh token
        "prompt": "consent",       # Google: always show consent to get refresh token
    }

    auth_url = f"{provider['auth_url']}?{urlencode(params)}"

    return {"auth_url": auth_url, "state": state}


async def handle_callback(
    code: str, state: str, redirect_uri: str
) -> dict:
    """Handle OAuth callback — exchange code for tokens, store, provision to n8n.

    Returns:
        {"status": "connected", "connection_id": "...", "platform": "..."} on success
        {"error": "..."} on failure
    """
    # Validate state
    state_data = _oauth_states.pop(state, None)
    if not state_data:
        return {"error": "Invalid or expired state token. Please try again."}

    if time.time() - state_data["timestamp"] > _STATE_TTL:
        return {"error": "State token expired. Please try again."}

    platform = state_data["platform"]
    provider = OAUTH_PROVIDERS.get(platform)
    if not provider:
        return {"error": f"Unknown OAuth platform: {platform}"}

    client = _get_oauth_client(platform)
    if not client:
        return {"error": "OAuth client credentials not found"}

    client_id, client_secret = client

    # Exchange code for tokens
    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                provider["token_url"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                },
                timeout=15.0,
            )

        if resp.status_code != 200:
            return {"error": f"Token exchange failed: HTTP {resp.status_code} — {resp.text[:200]}"}

        token_data = resp.json()
    except Exception as e:
        return {"error": f"Token exchange failed: {str(e)}"}

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)

    if not access_token:
        return {"error": "No access_token in token response"}

    # Store tokens in keychain
    import uuid

    connection_id = f"conn_{uuid.uuid4().hex[:12]}"
    token_store = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "obtained_at": time.time(),
        "client_id": client_id,
        "client_secret": client_secret,
    }

    _store_in_keychain(connection_id, platform, token_store)

    # Provision to n8n as OAuth credential
    n8n_cred_id = await _provision_oauth_to_n8n(
        platform, provider["n8n_type"], client_id, client_secret, token_data
    )

    # Assign credential to n8n workflows and activate them
    if n8n_cred_id:
        await _setup_n8n_workflows(platform, provider["n8n_type"], n8n_cred_id)

    # Record in DB
    capabilities = [c.action_type for c in get_capabilities(platform)]
    now = datetime.now(timezone.utc).isoformat()

    db = await get_db()
    user_email = _decode_user_email_from_token(token_data)
    display_name = f"{user_email or platform.title()}"
    await db.execute(
        """INSERT INTO egress_connections
           (connection_id, platform, name, n8n_credential_id, space_id,
            status, capabilities, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            connection_id, platform, display_name, n8n_cred_id, None,
            "connected", json.dumps(capabilities), now, now,
        ),
    )
    await db.commit()

    log.info("oauth_connection_created", platform=platform, connection_id=connection_id)

    return {
        "status": "connected",
        "connection_id": connection_id,
        "platform": platform,
        "capabilities": capabilities,
    }


async def refresh_access_token(connection_id: str, platform: str) -> bool:
    """Refresh an expired OAuth access token using the stored refresh token.

    Called by the health monitor when a token is about to expire.
    """
    provider = OAUTH_PROVIDERS.get(platform)
    if not provider:
        return False

    # Get stored tokens
    try:
        import keyring

        raw = keyring.get_password(EGRESS_KEYCHAIN_SERVICE, f"{platform}:{connection_id}")
        if not raw:
            return False
        token_store = json.loads(raw)
    except Exception:
        return False

    refresh_token = token_store.get("refresh_token")
    client_id = token_store.get("client_id")
    client_secret = token_store.get("client_secret")

    if not all([refresh_token, client_id, client_secret]):
        return False

    # Exchange refresh token
    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                provider["token_url"],
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=15.0,
            )

        if resp.status_code != 200:
            log.error("oauth_refresh_failed", platform=platform, status=resp.status_code)
            return False

        new_tokens = resp.json()
    except Exception as e:
        log.error("oauth_refresh_error", platform=platform, error=str(e))
        return False

    new_access_token = new_tokens.get("access_token")
    if not new_access_token:
        return False

    # Update keychain
    token_store["access_token"] = new_access_token
    token_store["obtained_at"] = time.time()
    if new_tokens.get("refresh_token"):
        token_store["refresh_token"] = new_tokens["refresh_token"]

    _store_in_keychain(connection_id, platform, token_store)

    # Update n8n credential if we have one
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT n8n_credential_id FROM egress_connections WHERE connection_id = ?",
        (connection_id,),
    )
    if rows and rows[0]["n8n_credential_id"]:
        await _update_n8n_oauth_token(rows[0]["n8n_credential_id"], new_access_token)

    log.info("oauth_token_refreshed", platform=platform, connection_id=connection_id)
    return True


# ---------------------------------------------------------------------------
# n8n OAuth credential provisioning
# ---------------------------------------------------------------------------


def _decode_user_email_from_token(token_data: dict) -> str:
    """Try to extract the user's email from an OAuth id_token (JWT) or token response."""
    import base64

    # Try id_token first (contains user claims)
    id_token = token_data.get("id_token", "")
    if id_token:
        try:
            # Decode JWT payload (2nd segment) without verification — we just need the email
            payload = id_token.split(".")[1]
            # Fix padding
            payload += "=" * (4 - len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            return claims.get("email") or claims.get("preferred_username") or claims.get("upn", "")
        except Exception:
            pass
    return ""


async def _provision_oauth_to_n8n(
    platform: str,
    n8n_type: str,
    client_id: str,
    client_secret: str,
    token_data: dict,
) -> str | None:
    """Create an OAuth credential in n8n with the obtained tokens."""
    try:
        from laya.integrations.n8n_client import create_credential

        oauth_token_data = {
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "expires_in": token_data.get("expires_in", 3600),
            "token_type": token_data.get("token_type", "Bearer"),
            "scope": token_data.get("scope", ""),
        }

        # n8n expects OAuth credentials with specific structure
        cred_data: dict = {
            "clientId": client_id,
            "clientSecret": client_secret,
            "oauthTokenData": oauth_token_data,
        }

        # Microsoft Outlook/Calendar credentials require additional fields
        if n8n_type == "microsoftOutlookOAuth2Api":
            user_email = _decode_user_email_from_token(token_data)
            cred_data.update({
                "serverUrl": "https://graph.microsoft.com",
                "userPrincipalName": user_email,
                "sendAdditionalBodyProperties": False,
                "additionalBodyProperties": "",
            })

        # Determine n8n node type
        node_type_map = {
            "gmailOAuth2Api": "n8n-nodes-base.gmail",
            "googleCalendarOAuth2Api": "n8n-nodes-base.googleCalendar",
            "microsoftOutlookOAuth2Api": "n8n-nodes-base.microsoftOutlook",
        }
        node_type = node_type_map.get(n8n_type, "n8n-nodes-base.httpRequest")

        result = await create_credential(
            name=f"Laya - {platform.title()} (OAuth)",
            n8n_type=n8n_type,
            data=cred_data,
            node_type=node_type,
        )
        return str(result.get("id", ""))
    except Exception as e:
        log.error("n8n_oauth_provision_failed", platform=platform, error=str(e))
        return None


async def _update_n8n_oauth_token(n8n_credential_id: str, new_access_token: str) -> None:
    """Update an existing n8n credential's access token after refresh."""
    try:
        from laya.integrations.n8n_client import _get_headers
        from laya.http_client import get_client
        from laya.config import get_n8n_config

        n8n_config = get_n8n_config()
        base_url = n8n_config["base_url"].rstrip("/")
        url = f"{base_url}/api/v1/credentials/{n8n_credential_id}"

        # Fetch current credential to update only the token
        client = get_client()
        headers = _get_headers()
        resp = await client.get(url, headers=headers, timeout=10.0)
        if resp.status_code != 200:
            return

        cred_data = resp.json().get("data", {})
        if "oauthTokenData" in cred_data:
            cred_data["oauthTokenData"]["access_token"] = new_access_token

        await client.patch(
            url,
            headers=headers,
            json={"data": cred_data},
            timeout=10.0,
        )
    except Exception as e:
        log.warning("n8n_token_update_failed", cred_id=n8n_credential_id, error=str(e))


# ---------------------------------------------------------------------------
# n8n workflow setup — assign credentials and activate
# ---------------------------------------------------------------------------


# Default parameters for skeleton executor nodes that haven't been configured.
# These use n8n expressions to pull values from the incoming webhook payload.
_NODE_PARAM_TEMPLATES: dict[str, dict] = {
    "n8n-nodes-base.microsoftOutlook": {
        "resource": "message",
        "operation": "send",
        "toRecipients": '={{ $json.body.payload.to || $json.body.event_actor_email }}',
        "subject": '={{ $json.body.payload.subject || (($json.body.event_subject || "").toLowerCase().startsWith("re:") ? $json.body.event_subject : "Re: " + ($json.body.event_subject || "")) }}',
        "bodyContent": '={{ $json.body.payload.body || $json.body.payload.message || "" }}',
        "additionalFields": {"bodyContentType": "Text"},
    },
}

# Maps OAuth platform to n8n node types that need the credential.
_PLATFORM_NODE_TYPES: dict[str, list[str]] = {
    "gmail": [
        "n8n-nodes-base.gmail",
        "n8n-nodes-base.gmailTrigger",
    ],
    "calendar": [
        "n8n-nodes-base.googleCalendar",
        "n8n-nodes-base.googleCalendarTrigger",
    ],
    "outlook": [
        "n8n-nodes-base.microsoftOutlook",
        "n8n-nodes-base.microsoftOutlookTrigger",
    ],
    "outlook_calendar": [
        "n8n-nodes-base.microsoftOutlook",
        "n8n-nodes-base.microsoftOutlookTrigger",
    ],
}


async def _setup_n8n_workflows(
    platform: str, n8n_cred_type: str, n8n_cred_id: str
) -> None:
    """Find n8n workflows for a platform, assign the credential, and activate them.

    After OAuth provisioning creates a credential in n8n, this function:
    1. Finds all Laya workflows related to the platform (ingestion + executor)
    2. Unarchives them if needed
    3. Assigns the new credential to nodes that need it
    4. Activates the workflows
    """
    try:
        from laya.integrations.n8n_client import (
            activate_workflow,
            get_workflow,
            list_workflows,
            unarchive_workflow,
            update_workflow,
        )

        target_node_types = set(_PLATFORM_NODE_TYPES.get(platform, []))
        if not target_node_types:
            return

        workflows = await list_workflows()

        # Find workflows matching this platform by name convention (Laya - Outlook ...)
        # Skip IMAP workflows — the Graph API workflows (Outlook Email) handle
        # everything; IMAP is a legacy fallback.
        platform_keywords = {
            "gmail": ["gmail"],
            "calendar": ["google calendar", "calendar"],
            "outlook": ["outlook email"],
            "outlook_calendar": ["outlook calendar"],
        }
        keywords = platform_keywords.get(platform, [platform])

        for wf in workflows:
            wf_name = (wf.get("name") or "").lower()
            if not any(kw in wf_name for kw in keywords):
                continue
            if "laya" not in wf_name:
                continue

            wf_id = str(wf["id"])

            # Unarchive if needed
            if wf.get("isArchived"):
                try:
                    await unarchive_workflow(wf_id)
                    log.info("n8n_workflow_unarchived", workflow=wf_name)
                except Exception as e:
                    log.warning("n8n_workflow_unarchive_failed",
                                workflow=wf_name, error=str(e))
                    continue

            full_wf = await get_workflow(wf_id)
            nodes = full_wf.get("nodes", [])
            connections = full_wf.get("connections", {})
            modified = False

            for node in nodes:
                node_type = node.get("type", "")
                if node_type not in target_node_types:
                    continue

                # Assign the credential to this node
                if "credentials" not in node:
                    node["credentials"] = {}
                node["credentials"][n8n_cred_type] = {
                    "id": n8n_cred_id,
                    "name": f"Laya - {platform.title()} (OAuth)",
                }

                # Fill in skeleton node parameters if missing
                params = node.get("parameters", {})
                template = _NODE_PARAM_TEMPLATES.get(node_type)
                if template and not params.get("operation") and not params.get("sendTo") and not params.get("toRecipients"):
                    node["parameters"] = {**template, **{k: v for k, v in params.items() if v}}

                modified = True

            if modified:
                await update_workflow(wf_id, {
                    "name": full_wf["name"],
                    "nodes": nodes,
                    "connections": connections,
                    "settings": full_wf.get("settings", {}),
                })
                log.info("n8n_workflow_credentials_assigned",
                         workflow=wf_name, credential_id=n8n_cred_id)

            # Activate the workflow
            if not wf.get("active"):
                try:
                    await activate_workflow(wf_id, active=True)
                    log.info("n8n_workflow_activated", workflow=wf_name)
                except Exception as e:
                    log.warning("n8n_workflow_activate_failed",
                                workflow=wf_name, error=str(e))

            # Register/update as a source with correct platform and type
            is_executor = "executor" in wf_name
            source_type = "executor" if is_executor else "ingestion"
            webhook_path = None
            if is_executor:
                for node in nodes:
                    if node.get("type") == "n8n-nodes-base.webhook":
                        webhook_path = node.get("parameters", {}).get("path")
                        break

            db = await get_db()
            existing = await db.execute_fetchall(
                "SELECT source_id, platform FROM sources WHERE workflow_id = ?",
                (wf_id,),
            )
            if existing:
                # Update platform if it was auto-discovered as 'unknown'
                await db.execute(
                    """UPDATE sources SET platform = ?, source_type = ?, webhook_path = ?, name = ?
                       WHERE workflow_id = ?""",
                    (platform, source_type, webhook_path, full_wf["name"], wf_id),
                )
            else:
                import uuid
                source_id = f"src_{uuid.uuid4().hex[:12]}"
                await db.execute(
                    """INSERT INTO sources (source_id, name, platform, workflow_id, space_id, source_type, webhook_path)
                       VALUES (?, ?, ?, ?, 'default', ?, ?)""",
                    (source_id, full_wf["name"], platform, wf_id, source_type, webhook_path),
                )
            await db.commit()

    except Exception as e:
        log.error("n8n_workflow_setup_failed", platform=platform, error=str(e))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cleanup_expired_states() -> None:
    """Remove expired OAuth state tokens."""
    now = time.time()
    expired = [k for k, v in _oauth_states.items() if now - v["timestamp"] > _STATE_TTL]
    for k in expired:
        del _oauth_states[k]
