"""LiteLLM wrapper with model selection, retries, and audit logging."""

import json
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import structlog
import tenacity
from pydantic import BaseModel

from laya.config import load_settings
from laya.db.sqlite import get_db

log = structlog.get_logger()


@dataclass
class ToolCall:
    """A tool call from the LLM response."""

    id: str
    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    """Structured response from an LLM call."""

    content: str
    parsed: dict[str, Any] | None = None
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    finish_reason: str = "stop"
    truncated: bool = False
    tool_calls: list | None = None  # List of ToolCall objects
    raw_message_dict: dict | None = None  # Raw message for tool loop continuation


async def _get_space_model(role: str, space_id: str) -> str | None:
    """Look up a space-specific model override for the given role.

    Returns None if the space has no override for this role.
    """
    from laya.db.sqlite import get_db

    db = await get_db()
    rows = await db.execute_fetchall(
        f"SELECT {role}_model FROM spaces WHERE space_id = ?",
        (space_id,),
    )
    if rows and rows[0][f"{role}_model"]:
        return rows[0][f"{role}_model"]
    return None


async def _get_space_api_key(provider: str, space_id: str) -> str | None:
    """Look up a space-specific API key for the given provider.

    Returns None if the space has no key override for this provider.
    """
    from laya.db.sqlite import get_db
    from laya.security.keychain import get_space_api_key

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT key_ref FROM space_api_keys WHERE space_id = ? AND provider = ?",
        (space_id, provider),
    )
    if rows:
        return get_space_api_key(rows[0]["key_ref"])
    return None


def _get_model_for_role(role: str) -> str:
    """Look up the configured model for a given role (router, stager, chat).

    Adds provider prefix if not already present.
    """
    settings = load_settings()
    models = settings.get("models", {})
    model_name = models.get(role, "claude-haiku-4-5")

    # If the model string already has a provider prefix, use as-is
    if "/" in model_name:
        return model_name

    return _add_provider_prefix(model_name)


def _add_provider_prefix(model_name: str) -> str:
    """Add provider prefix to a model name if not already present."""
    if "/" in model_name:
        return model_name
    if model_name.startswith("claude"):
        return f"anthropic/{model_name}"
    elif model_name.startswith(("gpt", "o1", "o3", "o4")):
        return f"openai/{model_name}"
    elif model_name.startswith("gemini"):
        return f"gemini/{model_name}"
    return model_name


def _resolve_custom_provider(model: str) -> tuple[str, dict[str, Any]] | None:
    """Check if a model string references a custom provider.

    Custom provider models use format: {provider_id}/{model_name}
    e.g., "lmstudio-local/qwen2.5-7b-instruct"

    Returns (litellm_model_string, extra_kwargs) or None if not a custom provider.
    """
    if "/" not in model:
        return None

    prefix = model.split("/")[0]

    # Skip known cloud provider prefixes
    if prefix in ("anthropic", "openai", "gemini", "openrouter", "ollama"):
        return None

    from laya.llm.providers import get_custom_provider, _get_provider_api_key

    provider = get_custom_provider(prefix)
    if not provider:
        return None

    model_name = model.split("/", 1)[1]
    ptype = provider.get("provider_type", "openai_compatible")
    base_url = provider["base_url"].rstrip("/")

    extra: dict[str, Any] = {
        "timeout": float(provider.get("default_timeout", 120)),
    }

    if ptype == "ollama":
        litellm_model = f"ollama/{model_name}"
        extra["api_base"] = base_url
    else:
        # Both lmstudio and openai_compatible use OpenAI-compat endpoint
        litellm_model = f"openai/{model_name}"
        extra["api_base"] = f"{base_url}/v1"

    # API key from keychain (optional for local providers)
    api_key = _get_provider_api_key(provider)
    if api_key:
        extra["api_key"] = api_key
    else:
        extra["api_key"] = "not-needed"  # LiteLLM requires a non-empty value

    return litellm_model, extra


def _get_custom_provider_meta(model: str) -> dict | None:
    """Get capability metadata for a custom provider model."""
    if "/" not in model:
        return None
    prefix = model.split("/")[0]
    if prefix in ("anthropic", "openai", "gemini", "openrouter", "ollama"):
        return None
    from laya.llm.providers import get_custom_provider

    provider = get_custom_provider(prefix)
    if not provider:
        return None
    ptype = provider.get("provider_type", "openai_compatible")
    caps = provider.get("capabilities_override", {})
    return {
        "provider_type": ptype,
        "supports_structured_output": caps.get("supports_structured_output", ptype == "lmstudio"),
        "supports_tool_calling": caps.get("supports_tool_calling", ptype == "lmstudio"),
    }


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
    space_id: str | None = None,
    tools: list[dict] | None = None,
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
        space_id: Optional space for model/key overrides.

    Returns:
        LLMResponse with content, parsed JSON (if schema), and usage info.
    """
    from litellm import acompletion

    # Resolve model: space override → global setting
    model = _get_model_for_role(role)
    if space_id:
        space_model = await _get_space_model(role, space_id)
        if space_model:
            model = _add_provider_prefix(space_model)

    # Resolve API key: space override → global (already in env)
    space_api_key = None
    if space_id:
        provider = model.split("/")[0] if "/" in model else None
        if provider:
            space_api_key = await _get_space_api_key(provider, space_id)

    # Resolve custom provider overrides (api_base, timeout, api_key)
    custom_provider_extra: dict[str, Any] = {}
    custom_meta = _get_custom_provider_meta(model)
    custom = _resolve_custom_provider(model)
    if custom:
        model, custom_provider_extra = custom

    # Gemini 3+ models degrade with temperature < 1.0 — force it to 1.0
    effective_temperature = temperature
    model_name = model.split("/")[-1]  # strip provider prefix
    if model_name.startswith("gemini-3"):
        effective_temperature = 1.0

    from laya.pipeline.queue import get_model_timeout, get_llm_retries

    # Use configured values (settings.json pipeline section)
    configured_timeout = get_model_timeout()
    # If caller didn't override num_retries from the default, use configured value
    if num_retries == 3:
        num_retries = get_llm_retries()

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": effective_temperature,
        "max_tokens": max_tokens,
        "timeout": configured_timeout,
    }

    # Merge custom provider overrides (api_base, api_key, etc.)
    # Use the higher of global pipeline timeout vs provider timeout
    provider_timeout = custom_provider_extra.pop("timeout", None)
    kwargs.update(custom_provider_extra)
    if provider_timeout is not None:
        kwargs["timeout"] = max(float(kwargs["timeout"]), float(provider_timeout))

    # Use space-specific API key if available (takes precedence)
    if space_api_key:
        kwargs["api_key"] = space_api_key

    # Structured output handling
    if response_schema:
        if custom_meta and not custom_meta.get("supports_structured_output", True):
            # Inject schema as text instruction for models without native support
            schema_text = json.dumps(
                response_schema.get("schema", response_schema), indent=2
            )
            instruction = (
                f"\n\nYou MUST respond with valid JSON matching this exact schema. "
                f"Output ONLY the JSON object, no other text.\n\nSchema:\n{schema_text}"
            )
            msg_list = list(messages)
            if msg_list and msg_list[0].get("role") == "system":
                msg_list[0] = {
                    **msg_list[0],
                    "content": msg_list[0]["content"] + instruction,
                }
            else:
                msg_list.insert(0, {"role": "system", "content": instruction})
            kwargs["messages"] = msg_list
        else:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": response_schema,
            }

    # Tools support
    if tools:
        if custom_meta and not custom_meta.get("supports_tool_calling", True):
            pass  # Skip tools for models that don't support them
        else:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

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
        finish_reason = response.choices[0].finish_reason or "stop"
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        truncated = finish_reason == "length"

        # If the response was truncated and we requested structured output,
        # retry once with doubled max_tokens to try to get complete JSON.
        if truncated and response_schema:
            doubled = max_tokens * 2
            log.warning(
                "llm_response_truncated",
                model=model,
                output_tokens=output_tokens,
                max_tokens=max_tokens,
                retrying_with=doubled,
            )
            kwargs["max_tokens"] = doubled

            retry_start = time.monotonic()
            response = await _call_with_retry()
            elapsed_ms += int((time.monotonic() - retry_start) * 1000)

            content = response.choices[0].message.content or ""
            finish_reason = response.choices[0].finish_reason or "stop"
            input_tokens += response.usage.prompt_tokens if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0
            truncated = finish_reason == "length"

            if truncated:
                log.warning(
                    "llm_response_still_truncated",
                    model=model,
                    output_tokens=output_tokens,
                    max_tokens=doubled,
                )

        # Parse JSON if structured output was requested
        parsed = None
        if response_schema and content:
            # Strip markdown fences that some models wrap around JSON
            stripped = content.strip()
            if stripped.startswith("```"):
                # Remove opening ```json or ``` and closing ```
                first_nl = stripped.find("\n")
                if first_nl != -1:
                    stripped = stripped[first_nl + 1:]
                if stripped.endswith("```"):
                    stripped = stripped[:-3].rstrip()
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                log.warning(
                    "llm_json_parse_failed",
                    model=model,
                    content_preview=content[:200],
                    truncated=truncated,
                )

        # Extract tool calls if present
        tool_calls_list = None
        raw_msg_dict = None
        msg = response.choices[0].message
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            tool_calls_list = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=(
                        json.loads(tc.function.arguments)
                        if isinstance(tc.function.arguments, str)
                        else tc.function.arguments
                    ),
                )
                for tc in msg.tool_calls
            ]
            raw_msg_dict = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": (
                                tc.function.arguments
                                if isinstance(tc.function.arguments, str)
                                else json.dumps(tc.function.arguments)
                            ),
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
            if msg.content:
                raw_msg_dict["content"] = msg.content

        result = LLMResponse(
            content=content,
            parsed=parsed,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=elapsed_ms,
            finish_reason=finish_reason,
            truncated=truncated,
            tool_calls=tool_calls_list,
            raw_message_dict=raw_msg_dict,
        )

        log.info(
            "llm_call_complete",
            role=role,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=elapsed_ms,
            finish_reason=finish_reason,
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


@dataclass
class StreamEvent:
    """A single event from the streaming LLM response."""

    type: str  # "chunk" | "tool_calls" | "done" | "error"
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    raw_message_dict: dict | None = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str = ""


async def llm_call_streaming(
    role: str,
    messages: list[dict[str, str]],
    step: str = "chat",
    temperature: float = 0.3,
    max_tokens: int = 2000,
    space_id: str | None = None,
    tools: list[dict] | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Streaming LLM call — yields content chunks as they arrive.

    When the LLM returns tool calls instead of content, yields a single
    StreamEvent(type="tool_calls") with the parsed tool calls, then stops.
    The caller is responsible for executing tools and re-calling.

    The final yield is always StreamEvent(type="done") with usage stats.
    """
    from litellm import acompletion

    # Resolve model
    model = _get_model_for_role(role)
    if space_id:
        space_model = await _get_space_model(role, space_id)
        if space_model:
            model = _add_provider_prefix(space_model)

    space_api_key = None
    if space_id:
        provider = model.split("/")[0] if "/" in model else None
        if provider:
            space_api_key = await _get_space_api_key(provider, space_id)

    custom_provider_extra: dict[str, Any] = {}
    custom_meta = _get_custom_provider_meta(model)
    custom = _resolve_custom_provider(model)
    if custom:
        model, custom_provider_extra = custom

    effective_temperature = temperature
    model_name = model.split("/")[-1]
    if model_name.startswith("gemini-3"):
        effective_temperature = 1.0

    from laya.pipeline.queue import get_model_timeout

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": effective_temperature,
        "max_tokens": max_tokens,
        "timeout": get_model_timeout(),
        "stream": True,
    }
    kwargs.update(custom_provider_extra)
    if space_api_key:
        kwargs["api_key"] = space_api_key

    if tools:
        if not (custom_meta and not custom_meta.get("supports_tool_calling", True)):
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

    start = time.monotonic()

    try:
        response = await acompletion(**kwargs)

        collected_content = ""
        collected_tool_calls: dict[int, dict] = {}  # index -> {id, name, arguments_str}
        finish_reason = "stop"

        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            # Finish reason
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason

            # Content chunks
            if delta.content:
                collected_content += delta.content
                yield StreamEvent(type="chunk", content=delta.content, model=model)

            # Tool call chunks (streamed incrementally)
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                for tc_chunk in delta.tool_calls:
                    idx = tc_chunk.index
                    if idx not in collected_tool_calls:
                        collected_tool_calls[idx] = {
                            "id": tc_chunk.id or "",
                            "name": tc_chunk.function.name if tc_chunk.function and tc_chunk.function.name else "",
                            "arguments_str": "",
                        }
                    if tc_chunk.id:
                        collected_tool_calls[idx]["id"] = tc_chunk.id
                    if tc_chunk.function:
                        if tc_chunk.function.name:
                            collected_tool_calls[idx]["name"] = tc_chunk.function.name
                        if tc_chunk.function.arguments:
                            collected_tool_calls[idx]["arguments_str"] += tc_chunk.function.arguments

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Estimate tokens from stream (usage may not be available in all providers)
        est_input_tokens = sum(len(m.get("content", "")) for m in messages) // 4
        est_output_tokens = len(collected_content) // 4

        # If tool calls were collected, yield them
        if collected_tool_calls:
            tool_calls_list = []
            raw_tcs = []
            for idx in sorted(collected_tool_calls.keys()):
                tc_data = collected_tool_calls[idx]
                try:
                    args = json.loads(tc_data["arguments_str"]) if tc_data["arguments_str"] else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls_list.append(
                    ToolCall(id=tc_data["id"], name=tc_data["name"], arguments=args)
                )
                raw_tcs.append({
                    "id": tc_data["id"],
                    "type": "function",
                    "function": {
                        "name": tc_data["name"],
                        "arguments": tc_data["arguments_str"],
                    },
                })

            raw_msg: dict[str, Any] = {"role": "assistant", "tool_calls": raw_tcs}
            if collected_content:
                raw_msg["content"] = collected_content

            yield StreamEvent(
                type="tool_calls",
                content=collected_content,
                tool_calls=tool_calls_list,
                raw_message_dict=raw_msg,
                model=model,
            )

        # Final done event
        yield StreamEvent(
            type="done",
            content=collected_content,
            model=model,
            input_tokens=est_input_tokens,
            output_tokens=est_output_tokens,
            latency_ms=elapsed_ms,
            finish_reason=finish_reason,
        )

        # Audit log
        await _log_to_audit(
            event_id=None,
            card_id=None,
            step=step,
            model=model,
            input_tokens=est_input_tokens,
            output_tokens=est_output_tokens,
            latency_ms=elapsed_ms,
            success=True,
        )

    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.error("llm_stream_failed", role=role, model=model, error=str(e))
        yield StreamEvent(type="error", content=str(e), model=model, latency_ms=elapsed_ms)
        await _log_to_audit(
            event_id=None,
            card_id=None,
            step=step,
            model=model,
            input_tokens=0,
            output_tokens=0,
            latency_ms=elapsed_ms,
            success=False,
            error=str(e),
        )
