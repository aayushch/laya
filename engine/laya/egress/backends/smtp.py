# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

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
            credentials = await self._load_credentials(request)
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
            # Port 465 is implicit TLS (SMTPS) — connect with TLS and do NOT
            # STARTTLS. Other ports (587/25) use STARTTLS when use_tls. The old
            # code hardcoded STARTTLS, so every port-465 provider that validated
            # fine then failed to send (review §2 egress — P4-18).
            implicit_ssl = smtp_port == 465

            smtp = aiosmtplib.SMTP(
                hostname=smtp_host,
                port=smtp_port,
                use_tls=implicit_ssl,
            )
            await smtp.connect()

            if use_tls and not implicit_ssl:
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
        creds = await self._load_credentials(None)
        return creds is not None

    def supports(self, platform: str, action_type: str) -> bool:
        """SMTP supports email actions for the 'smtp' platform."""
        return platform == "smtp" and action_type in ("send_email", "forward")

    async def _load_credentials(self, request) -> dict | None:
        """Load SMTP credentials from the keychain.

        Credentials are stored under ``smtp:{connection_id}`` (by
        connections._store_in_keychain). The old code read a nonexistent
        ``smtp:default`` key, so connect() validated fine but every send failed
        to find credentials (review §2 egress — P4-18). Resolve the connection_id
        from the request, or fall back to the (first) configured SMTP connection.
        """
        from laya.egress.connections import _get_from_keychain
        from laya.db.sqlite import get_db

        connection_id = getattr(request, "connection_id", None) if request else None
        if not connection_id:
            try:
                db = await get_db()
                rows = await db.execute_fetchall(
                    "SELECT connection_id FROM egress_connections "
                    "WHERE platform = 'smtp' LIMIT 1"
                )
                if rows:
                    connection_id = rows[0]["connection_id"]
            except Exception:
                return None
        if not connection_id:
            return None
        return _get_from_keychain(connection_id, "smtp")
