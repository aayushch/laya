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
    connection_id: str | None = None
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
        connection_id=body.connection_id,
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


import re


def _clean_ai_draft(raw: str) -> str:
    """Strip thinking/reasoning preamble that some models emit despite instructions."""
    text = raw.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    # Defense-in-depth: strip <think> blocks (also done in llm_call)
    if "<think>" in text:
        text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()

    lines = text.split("\n")
    first_line_lower = lines[0].strip().lower() if lines else ""

    # Detect reasoning preamble — models sometimes dump chain-of-thought as plain text
    thinking_patterns = [
        r"^(?:#{1,3}\s*)?(?:thinking|thought|analysis|reasoning|approach|plan|evaluation|step)\s*(?:process)?:?\s*$",
        r"^\*{1,2}(?:thinking|thought|analysis|reasoning)\s*(?:process)?:?\*{1,2}\s*$",
        r"^(?:let me|i will|i need to|first,? let)",
        r"^here'?s\s+(?:a|my|the)\s+(?:thinking|thought|analysis|reasoning|approach|plan|draft)",
        r"^\d+\.\s+\*{0,2}(?:analyze|identify|draft|refine|check|review|evaluate|understand|consider|step)\b",
    ]

    is_thinking = any(re.match(pat, first_line_lower) for pat in thinking_patterns)

    if is_thinking:
        draft_start_patterns = [
            r"^(?:hi|hello|hey|dear|good morning|good afternoon|good evening)\b",
            r"^(?:subject|re):\s",
            r"^(?:thanks|thank you|i hope|i wanted|just wanted|following up|as discussed|per our|please find)\b",
        ]
        # Find the last blank-line-separated block that starts with a draft
        # pattern. Earlier blocks may be examples inside the reasoning itself.
        block_starts: list[int] = []
        for i, line in enumerate(lines):
            if i == 0:
                continue
            stripped = line.strip().lower()
            if any(re.match(pat, stripped) for pat in draft_start_patterns):
                # Only count if preceded by a blank line (= start of a new block)
                if i > 0 and lines[i - 1].strip() == "":
                    block_starts.append(i)

        if block_starts:
            text = "\n".join(lines[block_starts[-1]:]).strip()
        else:
            # Fallback: take everything after last "---" or double blank line
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip() == "---" or (
                    i > 0 and lines[i - 1].strip() == "" and lines[i].strip() == ""
                ):
                    candidate = "\n".join(lines[i + 1:]).strip()
                    if candidate:
                        text = candidate
                        break

    # Strip leading "Subject: ..." or "To: ..." header lines
    while text:
        first_line = text.split("\n", 1)[0]
        if re.match(r"^(?:subject|to|from|cc|bcc)\s*:", first_line, re.IGNORECASE):
            text = text.split("\n", 1)[1].strip() if "\n" in text else ""
        else:
            break

    return text.strip()


class AiAssistRequest(BaseModel):
    platform: str
    action_type: str
    context: dict






from laya.egress.tools import get_find_contact_tool

_MAX_TOOL_ROUNDS = 3


def _build_ai_assist_system_prompt(kind: str, platform: str) -> str:
    from laya.config import get_self_user
    from laya.egress.registry import get_compose_guidance

    identity_block = ""
    self_user = get_self_user()
    if self_user:
        identity_block = (
            f"\nSENDER IDENTITY:\n"
            f"- Name: {self_user['name']}\n"
            f"- Email: {self_user['email']}\n"
            f"Use this name in the email signature (e.g. 'Best regards,\\n{self_user['name']}').\n"
        )

    guidance = get_compose_guidance(platform)
    platform_block = f"\nPLATFORM:\n{guidance}\n" if guidance else ""

    tool_block = (
        "\nCONTACT LOOKUP:\n"
        "You have access to a find_contact tool. When the user mentions a person by "
        "name, handle, or alias, you MUST call find_contact to resolve their details "
        "(email address, Slack handle, etc.) before producing your final response. "
        "You may call it multiple times for different people. "
        "Never guess or fabricate contact details — always use the tool.\n"
    )

    return (
        f"You are a writing assistant. Draft {kind} based on the context below.\n"
        f"{identity_block}{platform_block}{tool_block}\n"
        "RULES:\n"
        "- The body/message/description field must contain ONLY the message body text.\n"
        "- Do NOT include headers like 'Subject:', 'To:', or 'From:' inside the body.\n"
        "- Fill in every field you can. Use empty string for fields you truly cannot determine.\n"
        "- Match the tone of the user's draft if one exists.\n"
        "- Be concise and ready to send."
    )


@router.post("/egress/ai-assist")
async def ai_assist(body: AiAssistRequest) -> dict:
    """Use LLM to draft content for the compose editor."""
    import json as _json

    from laya.llm.client import llm_call
    from laya.llm.tools.contact_tools import find_contact

    from laya.egress.registry import get_platform_hint

    kind = get_platform_hint(body.platform)

    ctx_parts: list[str] = []
    for key in ("to", "subject", "channel", "repo", "project", "summary", "title"):
        if val := body.context.get(key):
            ctx_parts.append(f"{key}: {val}")
    if body_text := body.context.get("body", ""):
        ctx_parts.append(f"current draft: {body_text}")

    ctx_block = "\n".join(ctx_parts) if ctx_parts else "(no context provided)"

    system_prompt = _build_ai_assist_system_prompt(kind, body.platform)
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": ctx_block},
    ]

    tools = [get_find_contact_tool()]
    from laya.egress.registry import get_draft_schema

    schema = get_draft_schema(body.platform)

    try:
        # Phase 1: Tool-calling rounds (no response_schema).
        # Passing response_schema alongside tools causes the model to skip
        # tool calls and produce structured output directly, hallucinating
        # contact details instead of calling find_contact.
        for iteration in range(_MAX_TOOL_ROUNDS):
            response = await llm_call(
                role="stager",
                messages=messages,
                step="egress_draft",
                temperature=0.4,
                max_tokens=65536,
                tools=tools,
            )

            if not response.tool_calls:
                break

            messages.append(response.raw_message_dict)

            for tc in response.tool_calls:
                if tc.name == "find_contact":
                    query = tc.arguments.get("query", "")
                    result = await find_contact(query)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": _json.dumps(result),
                    })
                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": _json.dumps({"error": f"Unknown tool: {tc.name}"}),
                    })

        # Phase 2: Final structured output (no tools).
        response = await llm_call(
            role="stager",
            messages=messages,
            step="egress_draft",
            temperature=0.4,
            max_tokens=65536,
            response_schema=schema,
        )

        from laya.egress.registry import get_body_field

        body_field = get_body_field(body.platform)

        if response.parsed and isinstance(response.parsed, dict):
            draft: dict[str, str] = {
                k: v for k, v in response.parsed.items()
                if isinstance(v, str) and v.strip()
            }
            if body_field != "body" and body_field not in draft and "body" in draft:
                draft[body_field] = draft.pop("body")
        else:
            draft_text = _clean_ai_draft(response.content)
            draft = {body_field: draft_text}
    except Exception as exc:
        log.error("ai_assist_failed", error=str(exc))
        raise HTTPException(status_code=502, detail="AI assist failed — check LLM configuration") from exc

    return {"draft": draft}


class PolishRequest(BaseModel):
    text: str
    platform: str


_POLISH_SCHEMA = {
    "name": "polish_output",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "polished": {
                "type": "string",
                "description": "The polished/rewritten text.",
            },
        },
        "required": ["polished"],
        "additionalProperties": False,
    },
}


@router.post("/egress/polish")
async def polish_text(body: PolishRequest) -> dict:
    """Polish/rewrite user-drafted text for the compose editor."""
    from laya.llm.client import llm_call
    from laya.llm.prompts.chat import build_polish_messages

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty")

    try:
        response = await llm_call(
            role="chat",
            messages=build_polish_messages(text, body.platform),
            step="compose_polish",
            temperature=0.4,
            max_tokens=2000,
            response_schema=_POLISH_SCHEMA,
        )
        if response.parsed and isinstance(response.parsed, dict):
            polished = response.parsed.get("polished", "")
        else:
            polished = _clean_ai_draft(response.content)
    except Exception as exc:
        log.error("compose_polish_failed", error=str(exc))
        raise HTTPException(status_code=502, detail="Polish failed — check LLM configuration") from exc

    return {"polished": polished}


@router.get("/egress/compose-platforms")
async def get_compose_platforms() -> dict:
    """Return all platforms with their composable capabilities for the compose UI."""
    from laya.egress.registry import get_compose_platforms_data

    return {"platforms": get_compose_platforms_data()}


@router.get("/egress/email-suggestions")
async def email_suggestions(q: str = "") -> dict:
    """Search known email addresses from events and team config."""
    query = q.strip().lower()
    if len(query) < 2:
        return {"suggestions": []}

    from laya.db.sqlite import get_db

    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT DISTINCT actor_email FROM events
           WHERE actor_email IS NOT NULL AND actor_email != ''
             AND LOWER(actor_email) LIKE ?
           ORDER BY actor_email LIMIT 10""",
        (f"%{query}%",),
    )
    emails = [r["actor_email"] for r in rows]

    try:
        team = load_team()
        for m in team.get("members", []):
            email = m.get("email", "")
            if email and query in email.lower() and email not in emails:
                emails.append(email)
            for alias in m.get("aliases", []):
                if alias and query in alias.lower() and alias not in emails:
                    emails.append(alias)
    except Exception:
        pass

    return {"suggestions": emails[:15]}


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

    if result.status == "failed":
        raise HTTPException(status_code=400, detail=result.error or "Connection failed")

    return {
        "status": result.status,
        "connection_id": result.connection_id,
        "capabilities": result.capabilities,
        "error_message": result.error,
    }


@router.get("/egress/connections/names/{platform}")
async def get_connection_names(platform: str) -> dict:
    """Get existing connection names for a platform (for uniqueness check)."""
    from laya.db.sqlite import get_db
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT name FROM egress_connections WHERE platform = ?",
        (platform,),
    )
    return {"names": [r["name"] for r in rows]}


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
async def oauth_start(platform: str, connection_name: str | None = None) -> dict:
    """Start an OAuth flow for a platform (Gmail, Google Calendar, Outlook).

    Returns the authorization URL to redirect the user's browser to.
    Requires OAuth client credentials to be configured first.
    """
    from laya.egress.oauth import build_auth_url

    redirect_uri = "http://localhost:8420/egress/connections/oauth/callback"
    result = build_auth_url(platform, redirect_uri, connection_name=connection_name)

    if "error" in result:
        status_code = 422 if result.get("needs_setup") else 400
        raise HTTPException(status_code=status_code, detail=result["error"])

    return result


@router.get("/egress/connections/oauth/callback")
async def oauth_callback(code: str, state: str):
    """Handle OAuth redirect callback — exchange code for tokens, store, provision to n8n.

    Returns an HTML page that redirects the user back to Settings > Integrations.
    """
    from fastapi.responses import HTMLResponse
    from laya.egress.oauth import handle_callback

    redirect_uri = "http://localhost:8420/egress/connections/oauth/callback"
    result = await handle_callback(code, state, redirect_uri)

    if "error" in result:
        error_msg = result['error'].replace("'", "\\'").replace('"', '&quot;')
        return HTMLResponse(f"""<!DOCTYPE html><html><head><title>Connection Failed</title></head>
<body style="background:#1a1a1a;color:#eee;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center;max-width:400px">
<h2 style="color:#f87171">Connection Failed</h2>
<p style="color:#aaa;font-size:14px">{error_msg}</p>
<p style="color:#888;font-size:13px;margin-top:16px">You can close this window and try again.</p>
</div>
<script>
// If opened as popup, close after a delay so the parent's polling picks up the state
if (window.opener) setTimeout(() => window.close(), 4000);
</script>
</body></html>""")

    error_message = result.get("error_message")
    if error_message:
        safe_msg = error_message.replace("'", "\\'").replace('"', '&quot;')
        return HTMLResponse(f"""<!DOCTYPE html><html><head><title>Connected with issues</title></head>
<body style="background:#1a1a1a;color:#eee;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center;max-width:480px">
<h2 style="color:#fbbf24">Connected with issues</h2>
<p style="color:#aaa;font-size:14px">Authentication succeeded but workflow setup had errors:</p>
<p style="color:#f87171;font-size:13px;margin-top:8px">{safe_msg}</p>
<p style="color:#888;font-size:13px;margin-top:16px">Check Settings &gt; Integrations for details.</p>
</div>
<script>
if (window.opener) setTimeout(() => window.close(), 5000);
</script>
</body></html>""")

    return HTMLResponse("""<!DOCTYPE html><html><head><title>Connected</title></head>
<body style="background:#1a1a1a;color:#eee;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center">
<h2 style="color:#4ade80">Connected successfully!</h2>
<p style="color:#aaa;font-size:14px">You can close this window.</p>
</div>
<script>
// Close the popup — the parent window is already polling for the new connection
if (window.opener) setTimeout(() => window.close(), 1500);
</script>
</body></html>""")


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
