"""LiteLLM wrapper with model selection, retries, and audit logging."""

import json
import time
import uuid
from typing import Any

import structlog
import tenacity
from pydantic import BaseModel

from laya.config import load_settings
from laya.db.sqlite import get_db

log = structlog.get_logger()


class LLMResponse(BaseModel):
    """Structured response from an LLM call."""

    content: str
    parsed: dict[str, Any] | None = None
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


def _get_model_for_role(role: str) -> str:
    """Look up the configured model for a given role (router, stager, chat).

    Adds provider prefix if not already present.
    """
    settings = load_settings()
    models = settings.get("models", {})
    model_name = models.get(role, "claude-haiku-4-5-20251001")

    # If the model string already has a provider prefix, use as-is
    if "/" in model_name:
        return model_name

    # Auto-prefix based on model name pattern
    if model_name.startswith("claude"):
        return f"anthropic/{model_name}"
    elif model_name.startswith(("gpt", "o1", "o3", "o4")):
        return f"openai/{model_name}"
    elif model_name.startswith("gemini"):
        return f"gemini/{model_name}"

    return model_name


async def _log_to_audit(
    event_id: str | None,
    card_id: str | None,
    step: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    success: bool,
    error: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Write an entry to the audit_log table. Never raises."""
    try:
        db = await get_db()
        await db.execute(
            """INSERT INTO audit_log
               (log_id, event_id, card_id, step, model_used, input_tokens,
                output_tokens, latency_ms, success, error, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"audit_{uuid.uuid4().hex[:12]}",
                event_id,
                card_id,
                step,
                model,
                input_tokens,
                output_tokens,
                latency_ms,
                success,
                error,
                json.dumps(metadata) if metadata else None,
            ),
        )
        await db.commit()
    except Exception as e:
        log.warning("audit_log_failed", error=str(e))


async def llm_call(
    role: str,
    messages: list[dict[str, str]],
    response_schema: dict | None = None,
    event_id: str | None = None,
    card_id: str | None = None,
    step: str = "unknown",
    temperature: float = 0.0,
    max_tokens: int = 2000,
    num_retries: int = 3,
) -> LLMResponse:
    """Make an LLM call via LiteLLM with retries and audit logging.

    Args:
        role: Model role from settings (router, stager, chat).
        messages: Chat messages in OpenAI format.
        response_schema: Optional JSON schema for structured output.
        event_id: For audit logging.
        card_id: For audit logging.
        step: Pipeline step name for audit (route, stage, etc.).
        temperature: LLM temperature.
        max_tokens: Max output tokens.
        num_retries: Number of retries on failure.

    Returns:
        LLMResponse with content, parsed JSON (if schema), and usage info.
    """
    from litellm import acompletion

    model = _get_model_for_role(role)

    # Gemini 3+ models degrade with temperature < 1.0 — force it to 1.0
    effective_temperature = temperature
    model_name = model.split("/")[-1]  # strip provider prefix
    if model_name.startswith("gemini-3"):
        effective_temperature = 1.0

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": effective_temperature,
        "max_tokens": max_tokens,
        "timeout": 60.0,
    }

    if response_schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": response_schema,
        }

    # Tenacity retry with exponential backoff
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=30),
        stop=tenacity.stop_after_attempt(num_retries),
        retry=tenacity.retry_if_exception_type(Exception),
        reraise=True,
        before_sleep=lambda retry_state: log.warning(
            "llm_call_retrying",
            attempt=retry_state.attempt_number,
            model=model,
            error=str(retry_state.outcome.exception()) if retry_state.outcome else "unknown",
        ),
    )
    async def _call_with_retry():
        return await acompletion(**kwargs)

    start = time.monotonic()

    try:
        response = await _call_with_retry()
        elapsed_ms = int((time.monotonic() - start) * 1000)

        content = response.choices[0].message.content or ""
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

        # Parse JSON if structured output was requested
        parsed = None
        if response_schema and content:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                log.warning("llm_json_parse_failed", model=model, content_preview=content[:200])

        result = LLMResponse(
            content=content,
            parsed=parsed,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=elapsed_ms,
        )

        log.info(
            "llm_call_complete",
            role=role,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=elapsed_ms,
        )

        # Log successful call to audit
        await _log_to_audit(
            event_id=event_id,
            card_id=card_id,
            step=step,
            model=model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            success=True,
        )

        return result

    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.error("llm_call_failed", role=role, model=model, error=str(e))

        await _log_to_audit(
            event_id=event_id,
            card_id=card_id,
            step=step,
            model=model,
            input_tokens=0,
            output_tokens=0,
            latency_ms=elapsed_ms,
            success=False,
            error=str(e),
        )
        raise
