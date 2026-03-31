"""SMTP executor backend — generic email for non-Gmail/Outlook providers.

This is the one execution path that runs inside the Laya process rather
than delegating to n8n. It exists because n8n's generic SMTP/IMAP nodes
don't handle email threading (In-Reply-To headers), diverse provider quirks,
or attachment encoding reliably enough.

From the Engine's perspective, this is invisible — it calls egress.execute()
and the router decides to use SMTP. The Engine never knows.
"""

from __future__ import annotations

import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import structlog

from laya.egress.backends.base import EgressBackend
from laya.egress.models import EgressRequest, EgressResult

log = structlog.get_logger()

EGRESS_KEYCHAIN_SERVICE = "laya-egress"


class SmtpBackend(EgressBackend):
    """Send email via SMTP. Fallback for providers without dedicated n8n executors."""

    async def execute(
        self, request: EgressRequest, credentials: dict
    ) -> EgressResult:
        """Send an email via SMTP."""
        if not credentials:
            credentials = self._load_credentials(request.space_id)
            if not credentials:
                return EgressResult(
                    success=False,
                    error="SMTP credentials not configured. Connect an email provider in Settings > Integrations.",
                )

        try:
            import aiosmtplib
        except ImportError:
            return EgressResult(
                success=False,
                error="aiosmtplib not installed — SMTP support unavailable",
            )

        payload = request.payload

        # Build MIME message
        msg = MIMEMultipart("alternative")
        msg["From"] = credentials.get("username", "")
        msg["To"] = payload.get("to", "")
        msg["Subject"] = payload.get("subject", "(no subject)")

        if payload.get("cc"):
            msg["Cc"] = payload["cc"]
        if payload.get("bcc"):
            msg["Bcc"] = payload["bcc"]

        # Threading headers for reply chains
        if payload.get("in_reply_to"):
            msg["In-Reply-To"] = payload["in_reply_to"]
            msg["References"] = payload.get("references", payload["in_reply_to"])

        # Body
        body_text = payload.get("body", "")
        msg.attach(MIMEText(body_text, "plain"))
        # Also attach HTML version if body contains HTML tags
        if "<" in body_text and ">" in body_text:
            msg.attach(MIMEText(body_text, "html"))

        try:
            smtp_host = credentials.get("smtp_host", "")
            smtp_port = int(credentials.get("smtp_port", 587))
            use_tls = credentials.get("use_tls", True)

            smtp = aiosmtplib.SMTP(
                hostname=smtp_host,
                port=smtp_port,
                use_tls=False,  # We'll STARTTLS manually if needed
            )
            await smtp.connect()

            if use_tls:
                await smtp.starttls()

            await smtp.login(
                credentials.get("username", ""),
                credentials.get("password", ""),
            )
            await smtp.send_message(msg)
            await smtp.quit()

            log.info(
                "smtp_email_sent",
                to=payload.get("to"),
                subject=payload.get("subject"),
            )

            return EgressResult(success=True, result_data={"sent_via": "smtp"})

        except Exception as e:
            log.error("smtp_send_failed", error=str(e))
            return EgressResult(
                success=False,
                error=f"SMTP send failed: {str(e)}",
                retryable=True,
            )

    async def health_check(self) -> bool:
        """Check if SMTP is configured."""
        creds = self._load_credentials(None)
        return creds is not None

    def supports(self, platform: str, action_type: str) -> bool:
        """SMTP supports email actions for the 'smtp' platform."""
        return platform == "smtp" and action_type in ("send_email", "forward")

    def _load_credentials(self, space_id: str | None) -> dict | None:
        """Load SMTP credentials from keychain."""
        try:
            import keyring

            # Look for any SMTP connection
            # In a full implementation, we'd query the egress_connections table
            # and resolve by space_id. For now, try the first SMTP connection.
            from laya.db.sqlite import get_db
            import asyncio

            # Synchronous fallback — keychain lookup doesn't need async
            raw = keyring.get_password(EGRESS_KEYCHAIN_SERVICE, "smtp:default")
            if raw:
                return json.loads(raw)
            return None
        except Exception:
            return None
