"""n8n webhook executor backend — primary execution layer for egress actions."""

from __future__ import annotations

import json
import re
import time

import httpx
import structlog
import tenacity

from laya.config import get_n8n_config
from laya.db.sqlite import get_db
from laya.egress.backends.base import EgressBackend
from laya.egress.enrichment import enrich_payload_from_event
from laya.egress.models import EgressRequest, EgressResult
from laya.http_client import get_client
from laya.security.keychain import get_api_key

log = structlog.get_logger()

# Cache: user-defined webhook path → (production path, timestamp).
# Refreshed every 5 minutes.
_webhook_path_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 300

# Matches standard Jira Cloud accountIds: 24-char hex or "digits:uuid" format.
_JIRA_ACCOUNT_ID_RE = re.compile(
    r"^[0-9a-f]{24}$|^\d+:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


async def _resolve_jira_assignee(
    assignee: str, domain: str, email: str, api_token: str
) -> str:
    """Resolve an email or display name to a Jira Cloud accountId.

    Skips the lookup if the value already looks like an accountId.
    Raises ``ValueError`` with a user-facing message on failure.
    """
    if _JIRA_ACCOUNT_ID_RE.match(assignee):
        return assignee

    resp = await get_client().get(
        f"{domain}/rest/api/3/user/search",
        params={"query": assignee},
        auth=(email, api_token),
        timeout=10.0,
    )

    if resp.status_code != 200:
        raise ValueError(
            f"Jira user search failed (HTTP {resp.status_code}): "
            f"could not resolve assignee '{assignee}'"
        )

    users = resp.json()
    if not users:
        raise ValueError(
            f"No Jira user found matching '{assignee}'. "
            f"Check the email or name and try again."
        )

    return users[0]["accountId"]


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

        try:
            n8n_payload = await self._build_payload(request)
        except ValueError as exc:
            return EgressResult(success=False, error=str(exc))

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
        """Build the n8n executor envelope from an EgressRequest.

        Payload enrichment (event-derived identifiers + platform-specific
        normalization) is delegated to
        ``laya.egress.enrichment.enrich_payload_from_event`` so that both
        preview and execute paths see the same enriched payload.

        This method adds only the n8n-specific wrapping:
        - Connection-derived Jira base URL (needed for REST endpoints in
          the executor workflow; stays here because it's not event-derived).
        - The outer envelope the n8n webhook expects (``action_id``,
          ``target``, event context fields).
        """
        payload, event_ctx = await enrich_payload_from_event(request)

        # Jira: retrieve connection credentials once for base URL injection
        # and assignee resolution (email/name → accountId).
        if request.platform == "jira" and request.connection_id:
            jira_creds = None
            try:
                from laya.egress.connections import _get_from_keychain
                jira_creds = _get_from_keychain(request.connection_id, "jira")
            except Exception:
                pass

            if jira_creds and jira_creds.get("domain") and not payload.get("jira_base_url"):
                payload["jira_base_url"] = jira_creds["domain"].rstrip("/")

            if (
                request.action_type in ("assign", "create_issue")
                and payload.get("assignee")
                and jira_creds
                and all(jira_creds.get(k) for k in ("domain", "email", "apiToken"))
            ):
                payload["assignee"] = await _resolve_jira_assignee(
                    payload["assignee"],
                    jira_creds["domain"].rstrip("/"),
                    jira_creds["email"],
                    jira_creds["apiToken"],
                )

        # Gmail send_email: build the raw MIME message + base64url in Python
        # so the n8n workflow doesn't need a fragile JS expression.
        if request.platform == "gmail" and request.action_type == "send_email":
            from laya.egress.platforms.gmail import build_api_payload
            payload = build_api_payload(request.action_type, payload)

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
