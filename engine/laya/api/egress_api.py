"""Egress REST API — execute platform actions, manage connections."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import laya.egress as egress
from laya.egress.models import EgressRequest

log = structlog.get_logger()
router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ExecuteRequest(BaseModel):
    platform: str
    action_type: str
    payload: dict
    source_card_id: str | None = None
    source_event_id: str | None = None
    space_id: str | None = None


class PreviewRequest(BaseModel):
    platform: str
    action_type: str
    payload: dict
    source_event_id: str | None = None
    space_id: str | None = None


class ConnectRequest(BaseModel):
    platform: str
    name: str | None = None
    credentials: dict
    space_id: str | None = None


# ---------------------------------------------------------------------------
# Action endpoints
# ---------------------------------------------------------------------------


@router.post("/egress/execute")
async def execute_action(body: ExecuteRequest) -> dict:
    """Execute an outbound platform action.

    Used by the UI compose/inline editor for direct execution
    (card-triggered actions still go through /actions/execute which
    handles card lifecycle, then delegates to egress internally).
    """
    request = EgressRequest(
        platform=body.platform,
        action_type=body.action_type,
        payload=body.payload,
        source_card_id=body.source_card_id,
        source_event_id=body.source_event_id,
        space_id=body.space_id,
    )

    result = await egress.execute(request)

    if not result.success:
        raise HTTPException(status_code=502, detail=result.error or "Egress action failed")

    return {
        "status": "done",
        "result_url": result.result_url,
        "result_data": result.result_data,
    }


@router.post("/egress/preview")
async def preview_action(body: PreviewRequest) -> dict:
    """Preview what an action would do without executing."""
    request = EgressRequest(
        platform=body.platform,
        action_type=body.action_type,
        payload=body.payload,
        source_event_id=body.source_event_id,
        space_id=body.space_id,
    )

    preview = await egress.preview(request)

    return {
        "platform": preview.platform,
        "action_type": preview.action_type,
        "summary": preview.summary,
        "details": preview.details,
        "warnings": preview.warnings,
        "estimated_impact": preview.estimated_impact,
    }


@router.get("/egress/capabilities/{platform}")
async def get_capabilities(platform: str) -> dict:
    """Get available actions for a platform."""
    caps = await egress.get_capabilities(platform)

    return {
        "platform": platform,
        "capabilities": [
            {
                "action_type": c.action_type,
                "label": c.label,
                "requires_fields": c.requires_fields,
                "optional_fields": c.optional_fields,
                "description": c.description,
                "confirmation_required": c.confirmation_required,
            }
            for c in caps
        ],
    }


# ---------------------------------------------------------------------------
# Connection endpoints
# ---------------------------------------------------------------------------


@router.get("/egress/connections")
async def list_connections() -> dict:
    """List all configured platform connections with status."""
    connections = await egress.list_connections()

    return {
        "connections": [
            {
                "connection_id": c.connection_id,
                "platform": c.platform,
                "name": c.name,
                "status": c.status,
                "capabilities": c.capabilities,
                "space_id": c.space_id,
                "error_message": c.error_message,
                "last_validated_at": c.last_validated_at,
                "created_at": c.created_at,
            }
            for c in connections
        ],
    }


@router.post("/egress/connections")
async def create_connection(body: ConnectRequest) -> dict:
    """Create a new platform connection (API-key flow)."""
    result = await egress.connect(
        platform=body.platform,
        credentials=body.credentials,
        name=body.name,
        space_id=body.space_id,
    )

    if result.status != "connected":
        raise HTTPException(status_code=400, detail=result.error or "Connection failed")

    return {
        "status": result.status,
        "connection_id": result.connection_id,
        "capabilities": result.capabilities,
    }


@router.delete("/egress/connections/{connection_id}")
async def delete_connection(connection_id: str) -> dict:
    """Remove a platform connection."""
    await egress.disconnect(connection_id)
    return {"status": "deleted", "connection_id": connection_id}


@router.post("/egress/connections/test/{connection_id}")
async def test_connection_endpoint(connection_id: str) -> dict:
    """Test if a connection's credentials are still valid."""
    from laya.egress.connections import test_connection

    valid, error = await test_connection(connection_id)
    return {
        "connection_id": connection_id,
        "valid": valid,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Email provider auto-detection
# ---------------------------------------------------------------------------

WELL_KNOWN_PROVIDERS: dict[str, dict] = {
    "gmail.com": {
        "provider": "Gmail",
        "method": "oauth",
        "redirect_platform": "gmail",
        "note": "Uses OAuth — click 'Connect Gmail' instead.",
    },
    "outlook.com": {
        "provider": "Microsoft 365",
        "method": "oauth",
        "redirect_platform": "outlook",
        "note": "Uses OAuth — click 'Connect Outlook' instead.",
    },
    "hotmail.com": {
        "provider": "Microsoft 365",
        "method": "oauth",
        "redirect_platform": "outlook",
        "note": "Uses OAuth — click 'Connect Outlook' instead.",
    },
    "live.com": {
        "provider": "Microsoft 365",
        "method": "oauth",
        "redirect_platform": "outlook",
        "note": "Uses OAuth — click 'Connect Outlook' instead.",
    },
    "protonmail.com": {
        "provider": "ProtonMail",
        "method": "app_password",
        "smtp_host": "127.0.0.1",
        "smtp_port": 1025,
        "imap_host": "127.0.0.1",
        "imap_port": 1143,
        "use_tls": False,
        "note": "Requires ProtonMail Bridge running locally. Download at proton.me/mail/bridge",
    },
    "proton.me": {
        "provider": "ProtonMail",
        "method": "app_password",
        "smtp_host": "127.0.0.1",
        "smtp_port": 1025,
        "imap_host": "127.0.0.1",
        "imap_port": 1143,
        "use_tls": False,
        "note": "Requires ProtonMail Bridge running locally. Download at proton.me/mail/bridge",
    },
    "fastmail.com": {
        "provider": "Fastmail",
        "method": "app_password",
        "smtp_host": "smtp.fastmail.com",
        "smtp_port": 587,
        "imap_host": "imap.fastmail.com",
        "imap_port": 993,
        "use_tls": True,
        "note": "Use an app password from Settings > Privacy & Security > Integrations.",
    },
    "yahoo.com": {
        "provider": "Yahoo Mail",
        "method": "app_password",
        "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": 587,
        "imap_host": "imap.mail.yahoo.com",
        "imap_port": 993,
        "use_tls": True,
        "note": "Generate an app password at login.yahoo.com/account/security",
    },
    "icloud.com": {
        "provider": "iCloud Mail",
        "method": "app_password",
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587,
        "imap_host": "imap.mail.me.com",
        "imap_port": 993,
        "use_tls": True,
        "note": "Generate an app-specific password at appleid.apple.com/account/manage (requires 2FA).",
    },
    "me.com": {
        "provider": "iCloud Mail",
        "method": "app_password",
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587,
        "imap_host": "imap.mail.me.com",
        "imap_port": 993,
        "use_tls": True,
        "note": "Generate an app-specific password at appleid.apple.com/account/manage (requires 2FA).",
    },
    "zoho.com": {
        "provider": "Zoho Mail",
        "method": "app_password",
        "smtp_host": "smtp.zoho.com",
        "smtp_port": 587,
        "imap_host": "imap.zoho.com",
        "imap_port": 993,
        "use_tls": True,
        "note": "Enable IMAP in Zoho settings, then use an app-specific password.",
    },
    "aol.com": {
        "provider": "AOL Mail",
        "method": "app_password",
        "smtp_host": "smtp.aol.com",
        "smtp_port": 587,
        "imap_host": "imap.aol.com",
        "imap_port": 993,
        "use_tls": True,
        "note": "Generate an app password at login.aol.com/account/security",
    },
}


@router.get("/egress/connections/detect")
async def detect_email_provider(email: str) -> dict:
    """Auto-detect SMTP/IMAP settings from an email address domain."""
    domain = email.split("@")[-1].lower() if "@" in email else email.lower()

    config = WELL_KNOWN_PROVIDERS.get(domain)
    if config:
        return {"detected": True, **config}

    return {
        "detected": False,
        "provider": "Unknown",
        "method": "manual",
        "smtp_host": "",
        "smtp_port": 587,
        "imap_host": "",
        "imap_port": 993,
        "use_tls": True,
        "note": "Enter your SMTP and IMAP server settings manually.",
    }


# ---------------------------------------------------------------------------
# OAuth endpoints (Phase 3 — placeholder for future implementation)
# ---------------------------------------------------------------------------


@router.get("/egress/connections/oauth/start")
async def oauth_start(platform: str) -> dict:
    """Start an OAuth flow for a platform (Gmail, Google Calendar, Outlook).

    Returns the authorization URL to redirect the user's browser to.
    Requires OAuth client credentials to be configured first.
    """
    from laya.egress.oauth import build_auth_url

    redirect_uri = "http://127.0.0.1:8420/egress/connections/oauth/callback"
    result = build_auth_url(platform, redirect_uri)

    if "error" in result:
        status_code = 422 if result.get("needs_setup") else 400
        raise HTTPException(status_code=status_code, detail=result["error"])

    return result


@router.get("/egress/connections/oauth/callback")
async def oauth_callback(code: str, state: str) -> dict:
    """Handle OAuth redirect callback — exchange code for tokens, store, provision to n8n."""
    from laya.egress.oauth import handle_callback

    redirect_uri = "http://127.0.0.1:8420/egress/connections/oauth/callback"
    result = await handle_callback(code, state, redirect_uri)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@router.post("/egress/connections/oauth/setup")
async def oauth_setup(body: dict) -> dict:
    """Store OAuth client credentials for a platform.

    Body: {"platform": "gmail", "client_id": "...", "client_secret": "..."}
    This must be done before starting an OAuth flow.
    """
    from laya.egress.oauth import store_oauth_client

    platform = body.get("platform")
    client_id = body.get("client_id")
    client_secret = body.get("client_secret")

    if not all([platform, client_id, client_secret]):
        raise HTTPException(status_code=400, detail="Missing platform, client_id, or client_secret")

    success = store_oauth_client(platform, client_id, client_secret)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to store OAuth credentials")

    return {"status": "stored", "platform": platform}
