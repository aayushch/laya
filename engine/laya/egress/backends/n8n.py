"""n8n webhook executor backend — primary execution layer for egress actions."""

from __future__ import annotations

import json
import time

import httpx
import structlog
import tenacity

from laya.config import get_n8n_config, load_team
from laya.db.sqlite import get_db
from laya.egress.backends.base import EgressBackend
from laya.egress.models import EgressRequest, EgressResult
from laya.models.team import TeamConfig, TeamRole
from laya.http_client import get_client
from laya.security.keychain import get_api_key

log = structlog.get_logger()

# Cache: user-defined webhook path → (production path, timestamp).
# Refreshed every 5 minutes.
_webhook_path_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 300


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
            request.platform, request.space_id, request.connection_id
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
        """n8n supports any platform with a configured or default webhook.

        Checks both user settings and the built-in defaults, so that new
        platforms added to DEFAULT_SETTINGS aren't silently dropped when the
        user's settings.json has an older webhooks dict.
        """
        n8n_config = get_n8n_config()
        webhooks = n8n_config.get("webhooks", {})
        if platform in webhooks:
            return True
        # Fall back to built-in defaults (covers platforms missing from
        # user's settings.json due to shallow merge)
        from laya.config import DEFAULT_SETTINGS
        default_webhooks = DEFAULT_SETTINGS.get("n8n", {}).get("webhooks", {})
        return platform in default_webhooks

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    async def _resolve_webhook_url(
        self, platform: str, space_id: str | None,
        connection_id: str | None = None,
    ) -> str | None:
        """Resolve the best executor webhook URL for a platform.

        Priority:
        1. Executor source matching the specific connection_id
        2. Executor source registered in the same space as the request
        3. Any executor source registered for this platform
        4. Global config from settings.json

        n8n 2.x registers webhooks under ``{workflowId}/webhook/{path}``
        rather than just ``{path}``, so we look up the workflow ID via the
        n8n API and construct the full URL.
        """
        n8n_config = get_n8n_config()
        base_url = n8n_config["base_url"].rstrip("/")

        # Try connection/space-specific executor from sources table
        db = await get_db()
        executor_rows = await db.execute_fetchall(
            """SELECT webhook_path, space_id, workflow_id, connection_id FROM sources
               WHERE source_type = 'executor' AND platform = ? AND webhook_path IS NOT NULL""",
            (platform,),
        )

        webhook_path: str | None = None
        workflow_id: str | None = None

        if executor_rows:
            # Prefer exact connection match
            if connection_id:
                conn_match = [
                    r for r in executor_rows if r["connection_id"] == connection_id
                ]
                if conn_match:
                    webhook_path = conn_match[0]["webhook_path"]
                    workflow_id = conn_match[0]["workflow_id"]

            # Then try same-space executor
            if not webhook_path and space_id:
                same_space = [
                    r for r in executor_rows if r["space_id"] == space_id
                ]
                if same_space:
                    webhook_path = same_space[0]["webhook_path"]
                    workflow_id = same_space[0]["workflow_id"]

            # Fall back to any executor for this platform
            if not webhook_path:
                webhook_path = executor_rows[0]["webhook_path"]
                workflow_id = executor_rows[0]["workflow_id"]

        # Fall back to global config, then built-in defaults
        if not webhook_path:
            webhooks = n8n_config.get("webhooks", {})
            webhook_path = webhooks.get(platform)
        if not webhook_path:
            from laya.config import DEFAULT_SETTINGS
            default_webhooks = DEFAULT_SETTINGS.get("n8n", {}).get("webhooks", {})
            webhook_path = default_webhooks.get(platform)

        if not webhook_path:
            return None

        # n8n 2.x registers webhooks under a path that depends on how the
        # workflow was created:
        #   - Imported via API (no webhookId): {workflowId}/webhook/{path}
        #   - Duplicated in UI (has webhookId): just the webhookId UUID
        # We resolve the actual registered production path via the n8n API.
        production_path = await self._resolve_production_webhook_path(
            base_url, webhook_path, workflow_id
        )

        return f"{base_url}/webhook/{production_path}"

    async def _resolve_production_webhook_path(
        self, base_url: str, webhook_path: str, workflow_id: str | None
    ) -> str:
        """Resolve the actual production webhook path registered in n8n.

        n8n serves production webhooks at the user-defined ``path`` from
        the webhook node parameters (i.e. ``/webhook/{path}``).  The
        ``webhookId`` field is an internal identifier, not a URL component.

        We verify the workflow is active via the n8n API and cache the
        result for ``_CACHE_TTL`` seconds.
        """
        now = time.time()
        cached = _webhook_path_cache.get(webhook_path)
        if cached and (now - cached[1]) < _CACHE_TTL:
            return cached[0]

        # Populate cache from n8n API
        await self._refresh_webhook_cache(base_url)

        cached = _webhook_path_cache.get(webhook_path)
        if cached:
            return cached[0]

        # Fallback: use the path as-is
        return webhook_path

    async def _refresh_webhook_cache(self, base_url: str) -> None:
        """Fetch all active workflows from n8n and cache their production webhook paths."""
        api_key = get_api_key("n8n")
        if not api_key:
            return

        try:
            resp = await get_client().get(
                f"{base_url}/api/v1/workflows",
                headers={"X-N8N-API-KEY": api_key},
                timeout=10.0,
            )
            if resp.status_code != 200:
                return

            now = time.time()
            for wf in resp.json().get("data", []):
                if not wf.get("active"):
                    continue
                for node in wf.get("nodes") or []:
                    if node.get("type") != "n8n-nodes-base.webhook":
                        continue
                    params = node.get("parameters", {})
                    user_path = params.get("path")
                    if not user_path:
                        continue
                    # n8n production webhooks are served at the user-defined path
                    _webhook_path_cache[user_path] = (user_path, now)
        except Exception as e:
            log.warning("n8n_webhook_cache_refresh_failed", error=str(e))

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

        # For email replies triggered from a card (has source_event_id),
        # ensure "to" is the original sender — the stager LLM sometimes
        # sets it to the user's own email instead.  We trust event_actor_email
        # as the correct reply target when one exists.
        # Guard: skip override if actor_email is the user's own email (self role
        # in team.json) — the ingestion workflow may have captured the wrong
        # sender in some Outlook configurations.
        if (
            request.platform in ("gmail", "outlook", "smtp")
            and request.action_type in ("send_email", "reply")
            and request.source_event_id
            and event_ctx.get("actor_email")
        ):
            actor = event_ctx["actor_email"].lower()
            self_emails = self._get_self_emails()
            if actor not in self_emails:
                payload["to"] = event_ctx["actor_email"]

        # For Jira actions using httpRequest nodes, the executor workflow needs
        # the Jira instance base URL to construct REST API endpoints. Look it up
        # from the connection's stored credentials (domain field).
        if request.platform == "jira" and not payload.get("jira_base_url") and request.connection_id:
            try:
                from laya.egress.connections import _get_from_keychain
                jira_creds = _get_from_keychain(request.connection_id, "jira")
                if jira_creds and jira_creds.get("domain"):
                    payload["jira_base_url"] = jira_creds["domain"].rstrip("/")
            except Exception:
                pass  # Non-fatal — workflow falls back to placeholder URL

        # For Outlook archive/mark_read, enrich outlook_id from event metadata
        # so the executor workflow can target the correct message.
        if (
            request.platform == "outlook"
            and request.action_type in ("archive", "mark_read")
            and not payload.get("outlook_id")
            and request.source_event_id
        ):
            # Event ID format is "evt_outlook_<raw_outlook_id>"
            if request.source_event_id.startswith("evt_outlook_"):
                payload["outlook_id"] = request.source_event_id[len("evt_outlook_"):]

        return {
            "action_id": f"egr_{request.source_card_id or 'direct'}",
            "source_event_id": request.source_event_id,
            "target": {
                "platform": request.platform,
                "connection_id": request.connection_id,
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

        elif platform in ("gmail", "outlook"):
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

    def _get_self_emails(self) -> set[str]:
        """Return the set of emails (primary + aliases) for the 'self' team member."""
        try:
            team = TeamConfig(**load_team())
            for m in team.members:
                if m.role == TeamRole.self_:
                    emails = {m.email.lower()}
                    emails.update(a.lower() for a in m.aliases)
                    return emails
        except Exception:
            pass
        return set()

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
                ctx["actor_email"] = meta.get("gmail_from") or meta.get("outlook_from") or meta.get("from") or ""
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
                # Some n8n workflow nodes return empty bodies on success (e.g.
                # Jira comment).  Treat HTTP 2xx with empty/non-JSON body as
                # success rather than failing the action.
                if 200 <= resp.status_code < 300:
                    return EgressResult(success=True, result_data={})
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
