# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""OAuth proxy for Gmail and Microsoft 365.

Handles the full OAuth dance so users never need to touch the n8n dashboard.
Flow: Laya Settings → auth URL → provider consent → callback → token exchange → n8n provisioning.
"""

from __future__ import annotations

import base64
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
from laya.db.timeutil import db_now
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
        # Must stay in lockstep with the Google Cloud Console verification
        # submission — Google rejects verification if the consent screen shows
        # scopes not declared there. gmail.modify already covers every call we
        # make (messages get/list, send, label modify); adding gmail.readonly
        # or gmail.send would be redundant and widen the CASA audit surface.
        "scopes": [
            "https://www.googleapis.com/auth/gmail.modify",
        ],
        "n8n_type": "gmailOAuth2",
    },
    "calendar": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        # Same lockstep constraint as gmail above. calendar.events covers event
        # CRUD + trigger polling — the full "auth/calendar" scope (calendar list
        # /settings management) is unused and not in the verification submission.
        "scopes": [
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
    "slack": {
        "auth_url": "https://slack.com/oauth/v2/authorize",
        "token_url": "https://slack.com/api/oauth.v2.access",
        "scopes": [],
        "user_scopes": [
            "channels:history",
            "channels:read",
            "groups:history",
            "groups:read",
            "im:history",
            "im:read",
            "mpim:history",
            "mpim:read",
            "chat:write",
            "reactions:write",
            "users:read",
        ],
        "n8n_type": "slackOAuth2Api",
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


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and S256 code_challenge (RFC 7636)."""
    code_verifier = secrets.token_urlsafe(96)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------


def build_auth_url(
    platform: str,
    redirect_uri: str,
    connection_name: str | None = None,
    space_id: str | None = None,
    channel_names: str | None = None,
) -> dict:
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

    # Generate CSRF state token + PKCE
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = _generate_pkce()
    _oauth_states[state] = {
        "platform": platform,
        "timestamp": time.time(),
        "code_verifier": code_verifier,
        "connection_name": connection_name,
        "space_id": space_id,
        "channel_names": channel_names,
    }
    _cleanup_expired_states()

    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }

    # Slack OAuth v2 uses "user_scope" for user-token scopes (separate from bot "scope").
    # Other providers use the standard "scope" param.
    if provider.get("user_scopes"):
        params["user_scope"] = ",".join(provider["user_scopes"])
        if provider["scopes"]:
            params["scope"] = " ".join(provider["scopes"])
    else:
        params["scope"] = " ".join(provider["scopes"])
        params["access_type"] = "offline"    # Google: request refresh token
        params["prompt"] = "consent"         # Google: always show consent to get refresh token
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"

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
    code_verifier = state_data.get("code_verifier")
    connection_name = state_data.get("connection_name")
    space_id = state_data.get("space_id")
    channel_names = state_data.get("channel_names")
    provider = OAUTH_PROVIDERS.get(platform)
    if not provider:
        return {"error": f"Unknown OAuth platform: {platform}"}

    client = _get_oauth_client(platform)
    if not client:
        return {"error": "OAuth client credentials not found"}

    client_id, client_secret = client

    # Exchange code for tokens
    try:
        token_request_data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            token_request_data["code_verifier"] = code_verifier

        async with httpx.AsyncClient() as http:
            resp = await http.post(
                provider["token_url"],
                data=token_request_data,
                timeout=15.0,
            )

        if resp.status_code != 200:
            return {"error": f"Token exchange failed: HTTP {resp.status_code} — {resp.text[:200]}"}

        token_data = resp.json()
    except Exception as e:
        return {"error": f"Token exchange failed: {str(e)}"}

    # Slack OAuth v2 nests user tokens under "authed_user" — lift them to top level
    # so the rest of the flow can treat all providers uniformly.
    if platform == "slack" and "authed_user" in token_data:
        authed = token_data["authed_user"]
        token_data["access_token"] = authed.get("access_token")
        token_data["refresh_token"] = authed.get("refresh_token")
        token_data["expires_in"] = authed.get("expires_in", 0)
        token_data["token_type"] = authed.get("token_type", "bearer")
        token_data["scope"] = authed.get("scope", "")

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
    all_errors: list[str] = []
    n8n_cred_id = await _provision_oauth_to_n8n(
        platform, provider["n8n_type"], client_id, client_secret, token_data
    )
    if not n8n_cred_id:
        all_errors.append(f"Failed to create n8n credential for {platform}")

    # Clone and activate workflows for this connection
    user_email = _decode_user_email_from_token(token_data)
    display_name = connection_name or user_email or platform.title()

    if n8n_cred_id:
        from laya.egress.connections import _clone_workflows_for_connection
        activated, workflow_errors = await _clone_workflows_for_connection(
            platform, connection_id, display_name, n8n_cred_id,
            space_id=space_id,
        )
        all_errors.extend(workflow_errors)

        if platform == "slack" and channel_names and activated > 0:
            await _store_slack_channel_metadata(connection_id, channel_names)

    # Determine final status
    status = "error" if all_errors else "connected"
    error_message = "; ".join(all_errors) if all_errors else None

    # Record in DB
    capabilities = [c.action_type for c in get_capabilities(platform)]
    now = db_now()

    db = await get_db()
    await db.execute(
        """INSERT INTO egress_connections
           (connection_id, platform, name, n8n_credential_id, space_id,
            status, capabilities, error_message, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            connection_id, platform, display_name, n8n_cred_id, space_id,
            status, json.dumps(capabilities), error_message, now, now,
        ),
    )
    await db.commit()

    log.info("oauth_connection_created", platform=platform,
             connection_id=connection_id, status=status)

    return {
        "status": status,
        "connection_id": connection_id,
        "platform": platform,
        "capabilities": capabilities,
        "error_message": error_message,
    }


async def _store_slack_channel_metadata(connection_id: str, channel_names_csv: str) -> None:
    """Store Slack channel config in metadata, keyed by the ingestion workflow_id."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT workflow_id FROM sources WHERE connection_id = ? AND source_type = 'ingestion'",
        (connection_id,),
    )
    if not rows:
        log.warning("slack_channels_no_ingestion_workflow", connection_id=connection_id)
        return

    channels = [
        ch.lstrip("#").strip().lower()
        for ch in channel_names_csv.split(",")
        if ch.strip()
    ]
    if not channels:
        return

    workflow_id = rows[0]["workflow_id"]
    await db.execute(
        """INSERT INTO metadata (key, value, space_id) VALUES (?, ?, 'default')
           ON CONFLICT (key, space_id) DO UPDATE SET value = excluded.value""",
        (f"slack-channels:{workflow_id}", json.dumps(channels)),
    )
    await db.commit()
    log.info("slack_channels_stored", workflow_id=workflow_id, channels=channels)


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
        # Base oAuth2Api schema requires serverUrl + additionalBody fields
        cred_data: dict = {
            "clientId": client_id,
            "clientSecret": client_secret,
            "oauthTokenData": oauth_token_data,
            "serverUrl": "",
            "sendAdditionalBodyProperties": False,
            "additionalBodyProperties": "",
        }

        # Microsoft Outlook/Calendar credentials require additional fields
        if n8n_type == "microsoftOutlookOAuth2Api":
            user_email = _decode_user_email_from_token(token_data)
            cred_data.update({
                "serverUrl": "https://graph.microsoft.com",
                "userPrincipalName": user_email,
            })

        # Determine n8n node type
        node_type_map = {
            "gmailOAuth2": "n8n-nodes-base.gmail",
            "googleCalendarOAuth2Api": "n8n-nodes-base.googleCalendar",
            "microsoftOutlookOAuth2Api": "n8n-nodes-base.microsoftOutlook",
            "slackOAuth2Api": "n8n-nodes-base.slack",
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
        "additionalFields": {},
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
    "slack": [
        "n8n-nodes-base.slack",
    ],
}

# Some executor workflows use generic HTTP Request nodes with
# nodeCredentialType=<platform>OAuth2Api (e.g. Gmail's archive/star/mark_read
# hit the Graph-style REST endpoint). Those nodes also need the OAuth
# credential bound, matched by nodeCredentialType rather than node type.
_PLATFORM_HTTP_CRED_TYPES: dict[str, str] = {
    # n8n registers the Gmail OAuth credential under the name "gmailOAuth2"
    # (see GmailOAuth2Api.credentials.js — the filename ends in "Api", but the
    # internal `name` field is just "gmailOAuth2").  HTTP Request nodes must
    # reference it by that registered name, not by the class name, or n8n
    # fails the lookup with 'Credential ... does not exist for type ...'.
    "gmail": "gmailOAuth2",
    "calendar": "googleCalendarOAuth2Api",
    "outlook": "microsoftOutlookOAuth2Api",
    "outlook_calendar": "microsoftOutlookOAuth2Api",
    "slack": "slackOAuth2Api",
}


async def _setup_n8n_workflows(
    platform: str, n8n_cred_type: str, n8n_cred_id: str
) -> list[str]:
    """Find n8n workflows for a platform, assign the credential, and activate them.

    After OAuth provisioning creates a credential in n8n, this function:
    1. Finds all Laya workflows related to the platform (ingestion + executor)
    2. Unarchives them if needed
    3. Assigns the new credential to nodes that need it
    4. Activates the workflows

    Returns list of error messages (empty on full success).
    """
    errors: list[str] = []
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
            return []

        workflows = await list_workflows()

        # Use explicit workflow names from PLATFORMS config
        from laya.integrations.platforms import PLATFORMS
        platform_config = PLATFORMS.get(platform, {})
        target_names = {n.lower() for n in platform_config.get("workflows", [])}

        for wf in workflows:
            wf_name = (wf.get("name") or "")
            if wf_name.lower() not in target_names:
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

            http_cred_type = _PLATFORM_HTTP_CRED_TYPES.get(platform)
            for node in nodes:
                node_type = node.get("type", "")
                is_target_native = node_type in target_node_types
                is_target_http = (
                    http_cred_type is not None
                    and node_type == "n8n-nodes-base.httpRequest"
                    and node.get("parameters", {}).get("nodeCredentialType") == http_cred_type
                )
                if not (is_target_native or is_target_http):
                    continue

                # Assign the credential to this node. For HTTP Request nodes the
                # credential must be bound under the nodeCredentialType key so
                # n8n resolves it at runtime.
                if "credentials" not in node:
                    node["credentials"] = {}
                cred_key = http_cred_type if is_target_http else n8n_cred_type
                node["credentials"][cred_key] = {
                    "id": n8n_cred_id,
                    "name": f"Laya - {platform.title()} (OAuth)",
                }

                # Skip parameter templating/migration for generic HTTP nodes —
                # they own their own params.
                if is_target_http:
                    modified = True
                    continue

                # Fill in skeleton node parameters if missing
                params = node.get("parameters", {})
                template = _NODE_PARAM_TEMPLATES.get(node_type)
                if template and not params.get("operation") and not params.get("sendTo") and not params.get("toRecipients"):
                    node["parameters"] = {**template, **{k: v for k, v in params.items() if v}}

                # Migrate v1 → v2: move fields from additionalFields to top level
                additional = params.get("additionalFields", {})
                for field in ("toRecipients", "subject", "bodyContent"):
                    if field not in params and field in additional:
                        params[field] = additional.pop(field)
                if "bodyContentType" in additional:
                    additional.pop("bodyContentType")  # not a valid v2 field

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
                    errors.append(f"Failed to activate \"{wf_name}\": {e}")
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
        errors.append(f"Workflow setup failed: {e}")
        log.error("n8n_workflow_setup_failed", platform=platform, error=str(e))
    return errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cleanup_expired_states() -> None:
    """Remove expired OAuth state tokens."""
    now = time.time()
    expired = [k for k, v in _oauth_states.items() if now - v["timestamp"] > _STATE_TTL]
    for k in expired:
        del _oauth_states[k]
