"""n8n webhook executor backend — primary execution layer for egress actions."""

from __future__ import annotations

import json

import httpx
import structlog
import tenacity

from laya.config import get_n8n_config
from laya.db.sqlite import get_db
from laya.egress.backends.base import EgressBackend
from laya.egress.models import EgressRequest, EgressResult
from laya.http_client import get_client

log = structlog.get_logger()


class N8nBackend(EgressBackend):
    """Primary execution backend. Routes actions to n8n executor workflows via webhooks.

    This backend handles all platforms that have corresponding n8n executor workflows.
    It builds the expected n8n payload format, POSTs to the webhook URL, and parses
    the response.
    """

    async def execute(
        self, request: EgressRequest, credentials: dict
    ) -> EgressResult:
        """Execute an action by POSTing to the platform's n8n executor webhook."""
        webhook_url = await self._resolve_webhook_url(
            request.platform, request.space_id
        )
        if not webhook_url:
            return EgressResult(
                success=False,
                error=f"No n8n executor webhook configured for platform '{request.platform}'",
            )

        n8n_payload = await self._build_payload(request)

        return await self._post_to_n8n(webhook_url, n8n_payload, request)

    async def health_check(self) -> bool:
        """Check if n8n is reachable."""
        n8n_config = get_n8n_config()
        base_url = n8n_config["base_url"].rstrip("/")
        try:
            resp = await get_client().get(f"{base_url}/healthz", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    def supports(self, platform: str, action_type: str) -> bool:
        """n8n supports any platform that has a configured webhook."""
        n8n_config = get_n8n_config()
        webhooks = n8n_config.get("webhooks", {})
        return platform in webhooks

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    async def _resolve_webhook_url(
        self, platform: str, space_id: str | None
    ) -> str | None:
        """Resolve the best executor webhook URL for a platform.

        Priority:
        1. Executor source registered in the same space as the request
        2. Any executor source registered for this platform
        3. Global config from settings.json
        """
        n8n_config = get_n8n_config()
        base_url = n8n_config["base_url"].rstrip("/")

        # Try space-specific executor from sources table
        db = await get_db()
        executor_rows = await db.execute_fetchall(
            """SELECT webhook_path, space_id FROM sources
               WHERE source_type = 'executor' AND platform = ? AND webhook_path IS NOT NULL""",
            (platform,),
        )

        webhook_path: str | None = None

        if executor_rows:
            # Prefer same-space executor
            if space_id:
                same_space = [
                    r for r in executor_rows if r["space_id"] == space_id
                ]
                if same_space:
                    webhook_path = same_space[0]["webhook_path"]

            # Fall back to any executor for this platform
            if not webhook_path:
                webhook_path = executor_rows[0]["webhook_path"]

        # Fall back to global config
        if not webhook_path:
            webhooks = n8n_config.get("webhooks", {})
            webhook_path = webhooks.get(platform)

        if not webhook_path:
            return None

        return f"{base_url}/webhook/{webhook_path}"

    async def _build_payload(self, request: EgressRequest) -> dict:
        """Build the n8n executor payload from an EgressRequest.

        Enriches the payload with event context (actor info, subject) when
        a source_event_id is available.
        """
        payload = dict(request.payload)

        # Normalise None values to empty strings for n8n JS compatibility
        for key in (
            "body", "subject", "to", "message", "comment",
            "content", "title", "description",
        ):
            if key in payload and payload[key] is None:
                payload[key] = ""

        # Platform-specific normalization
        payload = self._normalize_platform_payload(request.platform, request.action_type, payload)

        # Fetch event context for richer executor payloads
        event_ctx = await self._fetch_event_context(request.source_event_id)

        return {
            "action_id": f"egr_{request.source_card_id or 'direct'}",
            "source_event_id": request.source_event_id,
            "target": {
                "platform": request.platform,
                "connection_id": None,
            },
            "action_type": request.action_type,
            "payload": payload,
            "event_actor_email": event_ctx.get("actor_email", ""),
            "event_actor_name": event_ctx.get("actor_name", ""),
            "event_subject": event_ctx.get("subject_title", ""),
            "event_platform": event_ctx.get("source_platform", request.platform),
        }

    def _normalize_platform_payload(
        self, platform: str, action_type: str, payload: dict
    ) -> dict:
        """Apply platform-specific field normalization.

        Handles common LLM field-name variants and type coercions so executor
        workflows receive consistent field names.
        """
        if platform == "github":
            # Ensure "comment" field for comment actions
            if "comment" not in payload and action_type in ("comment", "close_issue"):
                payload["comment"] = (
                    payload.pop("body", None)
                    or payload.pop("message", None)
                    or payload.pop("content", None)
                    or payload.pop("text", None)
                    or ""
                )
            # Coerce issue_number / pr_number to int
            for key in ("issue_number", "pr_number"):
                if key in payload:
                    try:
                        payload[key] = int(payload[key])
                    except (ValueError, TypeError):
                        pass

        elif platform == "gmail":
            # Ensure "body" exists — LLMs use various key names
            if "body" not in payload:
                payload["body"] = (
                    payload.pop("message", None)
                    or payload.pop("content", None)
                    or payload.pop("text", None)
                    or payload.pop("reply_body", None)
                    or payload.pop("email_body", None)
                    or payload.pop("reply", None)
                    or ""
                )

        elif platform == "slack":
            # Normalize message field
            if "message" not in payload and "text" not in payload:
                payload["message"] = (
                    payload.pop("body", None)
                    or payload.pop("content", None)
                    or ""
                )

        return payload

    async def _fetch_event_context(self, event_id: str | None) -> dict:
        """Fetch original event context for enriching executor payloads."""
        if not event_id:
            return {}

        db = await get_db()
        rows = await db.execute_fetchall(
            """SELECT actor_email, actor_name, subject_title, source_platform,
                      content_metadata
               FROM events WHERE event_id = ?""",
            (event_id,),
        )
        if not rows:
            return {}

        ctx = dict(rows[0])

        # Fall back to metadata for actor email (e.g., gmail_from)
        if not ctx.get("actor_email"):
            try:
                meta = json.loads(ctx.get("content_metadata") or "{}")
                ctx["actor_email"] = meta.get("gmail_from") or meta.get("from") or ""
            except (json.JSONDecodeError, AttributeError):
                pass

        return ctx

    async def _post_to_n8n(
        self, webhook_url: str, payload: dict, request: EgressRequest
    ) -> EgressResult:
        """POST to n8n webhook with retry logic."""

        @tenacity.retry(
            wait=tenacity.wait_exponential(multiplier=1, min=1, max=3),
            stop=tenacity.stop_after_attempt(2),
            retry=tenacity.retry_if_exception_type(
                (httpx.ConnectError, httpx.TimeoutException)
            ),
            reraise=True,
        )
        async def _do_post():
            return await get_client().post(
                webhook_url, json=payload, timeout=30.0
            )

        try:
            resp = await _do_post()

            try:
                resp_data = resp.json()
            except Exception:
                return EgressResult(
                    success=False,
                    error=f"n8n returned non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}",
                )

            if resp_data.get("success"):
                result_data = resp_data.get("result", {})
                result_url = (
                    result_data.get("pr_url")
                    or result_data.get("url")
                    or result_data.get("message_url")
                )
                return EgressResult(
                    success=True,
                    result_url=result_url,
                    result_data=result_data,
                )
            else:
                return EgressResult(
                    success=False,
                    error=resp_data.get("error", f"n8n returned status {resp.status_code}"),
                )

        except httpx.TimeoutException:
            return EgressResult(
                success=False,
                error="n8n request timed out",
                retryable=True,
            )
        except httpx.ConnectError:
            return EgressResult(
                success=False,
                error="n8n unreachable (connection refused)",
                retryable=True,
            )
        except Exception as e:
            log.error(
                "n8n_backend_error",
                platform=request.platform,
                action=request.action_type,
                error=str(e),
            )
            return EgressResult(success=False, error=str(e))
