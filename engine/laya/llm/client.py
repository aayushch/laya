# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""LiteLLM wrapper with model selection, retries, and audit logging."""

import asyncio
import json
import re
import sqlite3
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

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", flags=re.DOTALL)

# Lenient default output cap for all LLM calls. max_tokens is a CEILING, not a
# target: a well-behaved model stops at finish_reason=stop when its output is
# complete, so a high default costs nothing for normal completions. A low cap, by
# contrast, truncates structured (JSON-schema) output mid-document on verbose local
# models (e.g. Gemma 3 / LMStudio), yielding invalid JSON and stager retry loops.
# Pipeline stages rely on this default rather than per-stage caps.
DEFAULT_MAX_TOKENS = 65536

# Turn terminators that local/self-hosted runtimes (LMStudio/Ollama/llama.cpp) sometimes fail
# to treat as a stop — e.g. Gemma emits <end_of_turn> but GGUF often flags it NORMAL (not
# CONTROL) and its eos is <eos>, so generation runs past the end of the content and pads the
# rest of max_tokens with `\n`. We pass these as `stop` strings for custom providers so the
# runtime halts the moment one is emitted as text. They never appear inside valid model output,
# so they're risk-free for well-behaved models. See engine/docs/local-model-newline-padding.md.
_LOCAL_STOP_SEQUENCES = ["<end_of_turn>", "<eos>", "<|im_end|>", "<|eot_id|>"]

# Mild, windowed repetition penalty for structured-output calls on custom providers — breaks the
# degenerate newline-loop variant (where the terminator is never emitted as text) so the model
# picks the structural token that closes the JSON instead of looping on `\n`. Windowed via
# llama.cpp's repeat_last_n, so it doesn't punish legitimately-recurring JSON tokens (`"`, `,`).
_LOCAL_REPEAT_PENALTY = 1.15


def _strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> blocks that thinking models embed in content."""
    if "<think>" not in text:
        return text
    result = _THINK_BLOCK_RE.sub("", text).strip()
    if result.startswith("<think>"):
        result = result[len("<think>"):].strip()
    return result


def _extract_json(content: str, allow_completion: bool = False) -> Any | None:
    """Best-effort parse of a JSON object/array from model output.

    Beyond a strict json.loads() this handles two things verbose/non-stopping models add:
    1. ```json … ``` markdown fences some models wrap around the JSON.
    2. A complete JSON document followed by trailing junk — non-stopping local models
       (e.g. Gemma on LMStudio, whose <end_of_turn> isn't recognized as a stop) keep
       generating after the object closes, usually padding `\n`. A simple .strip() handles
       *pure* trailing whitespace; the balanced-brace scan below also recovers a complete
       object/array followed by non-whitespace junk.

    When `allow_completion` is set and the balanced-brace scan fails, a last-resort pass
    (`_complete_json`) rebuilds an object the model left *unterminated* because it padded
    whitespace before emitting the closing brackets — the Gemma-on-LMStudio failure where the
    turn stops (finish_reason=stop, NOT length) mid-padding, so the object never closed and no
    truncation retry ever fires. That completion is opt-in because it changes the "unterminated
    → None" contract other call sites (e.g. the truncation detector) rely on to trigger a retry.

    Returns the parsed value, or None when no object/array could be recovered (the document was
    genuinely truncated before a complete value — a real retry candidate).
    """
    if not content:
        return None
    s = content.strip()
    # Strip ```json … ``` fences that some models wrap around the JSON.
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Salvage: walk from the first opener to its matching closer (respecting strings and
    # escapes) and parse just that span, ignoring whatever the model padded afterwards. A
    # genuinely-unterminated document never balances → None.
    start = next((i for i, c in enumerate(s) if c in "{["), -1)
    if start == -1:
        return None
    opener = s[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:i + 1])
                except json.JSONDecodeError:
                    return None
    # The first opener never balanced. If the caller allows it, try to close a document the
    # model left unterminated because it padded whitespace instead of emitting the closers.
    if allow_completion:
        return _complete_json(s, start)
    return None


def _complete_json(s: str, start: int) -> Any | None:
    """Recover JSON left unterminated by a non-stopping model that padded trailing whitespace
    instead of emitting the closing brackets.

    The Gemma-on-LMStudio failure: the model emits the whole object, then pads `\n  ` (newline +
    indent) until it finally hits a recognized stop — so it halts with finish_reason=stop having
    never written the final `}`. The object is complete in every way except the closers, so we
    strip the whitespace padding (and a dangling comma), then append the brackets needed to
    balance. `json.loads` is the final guard: a genuinely-broken document (unbalanced, or cut
    mid-value) still returns None.

    Bails when the padding-stripped body ends *inside a string* — there the trailing whitespace
    is real value content, not structural padding, so completing it would corrupt/truncate the
    value. That's the signature of a genuine mid-content truncation, which the caller handles
    via the doubling retry instead.
    """
    body = s[start:].rstrip()
    if body.endswith(","):  # model stopped right after a separator, before the next value
        body = body[:-1].rstrip()
    if not body:
        return None
    stack: list[str] = []
    in_str = False
    esc = False
    for c in body:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            stack.append("}")
        elif c == "[":
            stack.append("]")
        elif c == "}" or c == "]":
            if stack:
                stack.pop()
    # Ended mid-string, or already balanced (a balanced-but-invalid doc, e.g. trailing junk the
    # strict/scan passes already rejected) — nothing safe to append.
    if in_str or not stack:
        return None
    try:
        return json.loads(body + "".join(reversed(stack)))
    except json.JSONDecodeError:
        return None


def _looks_like_padding(content: str) -> bool:
    """True when the tail of `content` is dominated by repeated whitespace / a single repeated
    character — the signature of a non-stopping model padding to max_tokens (e.g. Gemma on
    LMStudio spewing `\n`) rather than a genuinely truncated document. Used to skip the
    doubling-retry, which on a padder would just generate *more* padding and fail again."""
    tail = content[-200:]
    if not tail:
        return False
    stripped = tail.strip()
    # Almost-all-whitespace tail, or the same character over and over.
    return len(stripped) <= 2 or len(set(stripped)) <= 1


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
    try:
        rows = await db.execute_fetchall(
            f"SELECT {role}_model FROM spaces WHERE space_id = ?",
            (space_id,),
        )
    except sqlite3.OperationalError:
        # Role doesn't have a per-space column (e.g. group_summary)
        return None
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
    Roles that share a model with another role (e.g. group_summary → router)
    fall back to that role's model before the global default.
    """
    _ROLE_FALLBACKS = {
        "group_summary": "router",
    }

    settings = load_settings()
    models = settings.get("models", {})
    model_name = models.get(role)

    if not model_name and role in _ROLE_FALLBACKS:
        model_name = models.get(_ROLE_FALLBACKS[role])

    if not model_name:
        model_name = "claude-haiku-4-5"

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
        # Whether this provider may serve reasoning/"thinking" models (Qwen3, DeepSeek-R1,
        # etc.). When true we disable thinking for structured-output calls — see llm_call.
        "supports_reasoning": caps.get("supports_reasoning", ptype == "lmstudio"),
    }


# ── max_tokens context-window safety ─────────────────────────────────────
# The lenient DEFAULT_MAX_TOKENS (65536) is deliberately high so structured output
# never truncates. But strict servers 400 when it exceeds what they can serve: vLLM
# rejects `prompt + max_tokens > max_model_len`; OpenAI/Anthropic reject `max_tokens`
# above the model's max output (e.g. 65536*2=131072 from the truncation-retry exceeds
# even Opus's 128K cap). Local servers (LMStudio/Ollama) clamp silently and don't need
# this. We clamp ourselves so the same value is safe everywhere.
_OUTPUT_MARGIN = 512   # headroom left below the context window for the prompt estimate
_MIN_OUTPUT = 512      # never clamp output below this, even on a near-full context


def _estimate_prompt_tokens(messages: list[dict], model: str) -> int:
    """Best-effort prompt token count for context-window math. Prefers LiteLLM's
    tokenizer; falls back to the chars/4 heuristic also used in llm_call_streaming."""
    try:
        import litellm

        return int(litellm.token_counter(model=model, messages=messages))
    except Exception:
        return sum(len(str(m.get("content", ""))) for m in messages) // 4


async def _resolve_max_output_ceiling(
    litellm_model: str,
    original_model: str,
    is_custom: bool,
    messages: list[dict],
) -> int | None:
    """Max output tokens this model/server will accept, or None when it can't be
    determined (then we don't clamp and rely on the server's own behavior)."""
    # Cloud models LiteLLM knows about: cap at the model's max OUTPUT tokens.
    if not is_custom:
        try:
            import litellm

            info = litellm.get_model_info(litellm_model) or {}
            out = info.get("max_output_tokens") or info.get("max_tokens")
            return out if isinstance(out, int) and out > 0 else None
        except Exception:
            return None

    # Custom/local providers: cap at (discovered context window − prompt estimate).
    # Populated for LMStudio (native API) and vLLM (max_model_len); None elsewhere.
    try:
        from laya.llm.providers import discover_models_cached, get_custom_provider

        provider = get_custom_provider(original_model.split("/")[0])
        if not provider:
            return None
        models = await discover_models_cached(provider)
        window = next(
            (m.max_context_length for m in models
             if m.key == original_model and m.max_context_length),
            None,
        )
        if not window:
            return None
        prompt_est = _estimate_prompt_tokens(messages, litellm_model)
        return max(window - prompt_est - _OUTPUT_MARGIN, _MIN_OUTPUT)
    except Exception:
        return None


# ── Current date/time injection ──────────────────────────────────────────

_DATETIME_PREFIX = "[Current date/time:"


def _inject_current_datetime(messages: list[dict]) -> list[dict]:
    """Prepend current date/time to the last user message (non-mutating).

    Targets the last user message because:
    - Generative flows: it's the only user message
    - Chat flows: it's the current turn (historical messages don't need it)
    - Tool loops: role "tool" messages don't have role "user", so the
      current turn's user message is still the last one
    """
    from laya.llm.prompts import current_timestamp_line

    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx is None:
        return messages

    content = messages[last_user_idx].get("content", "")
    if isinstance(content, str) and content.startswith(_DATETIME_PREFIX):
        return messages

    hint = f"[{current_timestamp_line()}]"
    out = list(messages)
    out[last_user_idx] = {**out[last_user_idx], "content": f"{hint}\n\n{content}"}
    return out


# ── Prompt caching ──────────────────────────────────────────────────────

# Providers where caching is opt-in via cache_control annotation.
# OpenAI caching is automatic; self-hosted engines handle KV cache internally.
_CACHE_CONTROL_PROVIDERS = frozenset({"anthropic", "gemini", "vertex_ai"})


def _apply_prompt_caching(model: str, messages: list[dict]) -> list[dict]:
    """Annotate system messages with cache_control for providers that support it.

    For Anthropic and Gemini, converts the system message content from a plain
    string to a content-block list with ``cache_control: {type: ephemeral}`` so
    LiteLLM can activate the provider's prompt caching.

    For other providers (OpenAI, Ollama, LMStudio, etc.) the messages are
    returned unchanged — caching is either automatic or engine-level.
    """
    provider = model.split("/")[0] if "/" in model else ""
    if provider not in _CACHE_CONTROL_PROVIDERS:
        return messages

    out: list[dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            # Already in content-block format — just ensure cache_control is set
            if isinstance(content, list):
                blocks = []
                for block in content:
                    if isinstance(block, dict) and "cache_control" not in block:
                        block = {**block, "cache_control": {"type": "ephemeral"}}
                    blocks.append(block)
                out.append({**msg, "content": blocks})
            else:
                out.append({
                    **msg,
                    "content": [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                })
        else:
            out.append(msg)
    return out


async def log_to_audit(
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


async def _prepare_call_kwargs(
    *,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    space_id: str | None,
    tools: list[dict] | None,
    response_schema: dict | None = None,
    stream: bool = False,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Resolve provider details and assemble the ``acompletion`` kwargs shared by
    ``llm_call`` and ``llm_call_streaming`` so the two can't drift (review §5.6 —
    P7-5). ``model`` is the role/space-resolved model string; the agent-backend
    branch is handled by each caller BEFORE this (they dispatch it differently).

    Crucially the max_tokens clamp lives here now — the streaming path previously
    lacked it and sent DEFAULT_MAX_TOKENS (65536) to strict vLLM servers, tripping
    the exact 400 the clamp was built to prevent.

    Returns ``(litellm_model, kwargs, meta)`` where ``meta`` carries
    ``{is_custom, max_output_ceiling}`` that the caller's truncation/salvage logic
    needs.
    """
    # Space API key: space override → global (already in env)
    space_api_key = None
    if space_id:
        provider = model.split("/")[0] if "/" in model else None
        if provider:
            space_api_key = await _get_space_api_key(provider, space_id)

    # Custom provider overrides (api_base, timeout, api_key)
    custom_provider_extra: dict[str, Any] = {}
    custom_meta = _get_custom_provider_meta(model)
    original_model = model  # role/space-resolved; custom providers: "{id}/{name}"
    custom = _resolve_custom_provider(model)
    if custom:
        model, custom_provider_extra = custom

    # Gemini 3+ models degrade with temperature < 1.0 — force it to 1.0
    effective_temperature = temperature
    if model.split("/")[-1].startswith("gemini-3"):
        effective_temperature = 1.0

    # Clamp max_tokens to what this model/server accepts so DEFAULT_MAX_TOKENS
    # never trips a 400 on strict providers and truncation-retry can't overflow
    # the output cap. None ⇒ unknown, so leave as-is (local servers self-clamp).
    max_output_ceiling = await _resolve_max_output_ceiling(
        litellm_model=model,
        original_model=original_model,
        is_custom=custom is not None,
        messages=messages,
    )
    if max_output_ceiling is not None and max_tokens > max_output_ceiling:
        log.debug(
            "llm_max_tokens_clamped",
            model=model,
            requested=max_tokens,
            ceiling=max_output_ceiling,
        )
        max_tokens = max_output_ceiling

    from laya.pipeline.queue import get_model_timeout

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": effective_temperature,
        "max_tokens": max_tokens,
        "timeout": get_model_timeout(),
    }
    if stream:
        kwargs["stream"] = True

    # Merge custom provider overrides; use the higher of pipeline vs provider timeout.
    provider_timeout = custom_provider_extra.pop("timeout", None)
    kwargs.update(custom_provider_extra)
    if provider_timeout is not None:
        kwargs["timeout"] = max(float(kwargs["timeout"]), float(provider_timeout))

    # Space-specific API key takes precedence.
    if space_api_key:
        kwargs["api_key"] = space_api_key

    # Structured output handling (no-op when response_schema is None, e.g. streaming).
    if response_schema:
        if custom_meta and not custom_meta.get("supports_structured_output", True):
            schema_text = json.dumps(
                response_schema.get("schema", response_schema), indent=2
            )
            instruction = (
                f"\n\nYou MUST respond with valid JSON matching this exact schema. "
                f"Output ONLY the JSON object, no other text.\n\nSchema:\n{schema_text}"
            )
            msg_list = list(kwargs["messages"])
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

    # Tools (skip for models that don't support tool calling).
    if tools and not (custom_meta and not custom_meta.get("supports_tool_calling", True)):
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    # Disable reasoning for structured-output calls on local reasoning models so a
    # <think> block doesn't eat the whole max_tokens budget before the JSON.
    if response_schema and custom_meta and custom_meta.get("supports_reasoning"):
        existing_extra = kwargs.get("extra_body") or {}
        existing_ctk = existing_extra.get("chat_template_kwargs") or {}
        kwargs["extra_body"] = {
            **existing_extra,
            "chat_template_kwargs": {**existing_ctk, "enable_thinking": False},
        }

    # Stop non-stopping local models padding max_tokens with newlines. Cloud
    # providers manage their own stop tokens — scope to custom providers.
    if custom is not None:
        existing_stop = kwargs.get("stop")
        if existing_stop:
            extra = existing_stop if isinstance(existing_stop, list) else [existing_stop]
            kwargs["stop"] = [*_LOCAL_STOP_SEQUENCES, *extra]
        else:
            kwargs["stop"] = list(_LOCAL_STOP_SEQUENCES)
        # Windowed repeat penalty only for schema calls (nudges off the newline loop).
        if response_schema:
            existing_extra = kwargs.get("extra_body") or {}
            kwargs["extra_body"] = {**existing_extra, "repeat_penalty": _LOCAL_REPEAT_PENALTY}

    # Inject current date/time (cache-safe) + annotate for prompt caching. Message
    # mutations run LAST so schema text-injection above is included.
    kwargs["messages"] = _inject_current_datetime(kwargs["messages"])
    kwargs["messages"] = _apply_prompt_caching(model, kwargs["messages"])

    meta = {"is_custom": custom is not None, "max_output_ceiling": max_output_ceiling}
    return model, kwargs, meta


async def llm_call(
    role: str,
    messages: list[dict[str, str]],
    response_schema: dict | None = None,
    event_id: str | None = None,
    card_id: str | None = None,
    step: str = "unknown",
    temperature: float = 0.0,
    max_tokens: int = DEFAULT_MAX_TOKENS,
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

    # Agent inference backend: a resolved model of `agent/<id>/<model_string>` means the
    # user picked an installed CLI agent as the LLM (no API key / no local VRAM). Dispatch
    # to the agent subprocess instead of LiteLLM, but keep the same audit-log + budget side
    # effects and LLMResponse shape so the contract is identical to the API path.
    from laya.llm import agent_backend

    if agent_backend.is_agent_model(model):
        agent_messages = _inject_current_datetime(messages)
        start = time.monotonic()
        try:
            ar = await agent_backend.agent_llm_call(
                model_id=model,
                messages=agent_messages,
                response_schema=response_schema,
                max_tokens=max_tokens,
                temperature=temperature,
                num_retries=num_retries,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            result = LLMResponse(
                content=ar.content,
                parsed=ar.parsed,
                model=ar.effective_model or model,
                input_tokens=ar.input_tokens,
                output_tokens=ar.output_tokens,
                latency_ms=elapsed_ms,
                finish_reason=ar.finish_reason,
                truncated=ar.truncated,
            )
            # Same audit semantics as the LiteLLM path: a schema request that yields no
            # parsed JSON is a failed call even though the process exited cleanly.
            _audit_success = True
            _audit_error = None
            if response_schema and not result.parsed:
                _audit_success = False
                _audit_error = "Structured output requested but agent response was not valid JSON"
            _meta: dict[str, Any] = {"backend": "agent", "agent_model": model}
            if ar.cost_usd is not None:
                _meta["cost_usd"] = ar.cost_usd
            await log_to_audit(
                event_id=event_id,
                card_id=card_id,
                step=step,
                model=model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
                success=_audit_success,
                error=_audit_error,
                metadata=_meta,
            )
            try:
                from laya.pipeline.budget import check_budget
                from laya.tasks import create_task as create_tracked_task

                create_tracked_task(check_budget())
            except Exception:
                pass
            # Agent usage-budget: persist the native rate-limit signal (Claude), then
            # evaluate window limits (may pause ingestion until the usage window resets).
            try:
                from laya.pipeline.agent_budget import evaluate_agent_budget, record_rate_limit
                from laya.tasks import create_task as create_tracked_task

                agent_id = agent_backend.parse_agent_model_id(model)[0]
                if ar.rate_limit_info:
                    await record_rate_limit(agent_id, ar.rate_limit_info)
                create_tracked_task(evaluate_agent_budget())
            except Exception:
                pass
            log.info(
                "llm_call_complete",
                role=role,
                model=model,
                backend="agent",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=elapsed_ms,
                finish_reason=result.finish_reason,
            )
            return result
        except Exception as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            log.error("llm_call_failed", role=role, model=model, backend="agent", error=str(e))
            await log_to_audit(
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

    # Build the shared acompletion kwargs. Model is already resolved above and
    # the agent backend was handled; everything else (space key, custom provider,
    # max_tokens clamp, schema, tools, stop-sequences, datetime/caching) lives in
    # the single seam both llm_call and llm_call_streaming go through so they can't
    # drift (review §5.6 — P7-5).
    model, kwargs, _prep = await _prepare_call_kwargs(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        space_id=space_id,
        tools=tools,
        response_schema=response_schema,
        stream=False,
    )
    is_custom = _prep["is_custom"]
    max_output_ceiling = _prep["max_output_ceiling"]
    max_tokens = kwargs["max_tokens"]  # possibly clamped by _prepare_call_kwargs

    from laya.pipeline.queue import get_llm_retries
    # If the caller didn't override num_retries from the default, use the
    # configured pipeline.llm_retries value (review §5.7).
    if num_retries == 3:
        num_retries = get_llm_retries()

    # Retry only transient failures. Deterministic 4xx (bad request, auth,
    # not-found, unprocessable, content-policy) fail identically on every attempt,
    # so retrying them just burns up to 9–18 doomed HTTP calls per event across
    # the nested retry layers before giving up (review §3.6).
    def _retryable(exc: BaseException) -> bool:
        import litellm
        non_retryable = tuple(
            e for e in (
                getattr(litellm, "BadRequestError", None),
                getattr(litellm, "AuthenticationError", None),
                getattr(litellm, "NotFoundError", None),
                getattr(litellm, "UnprocessableEntityError", None),
                getattr(litellm, "ContentPolicyViolationError", None),
                getattr(litellm, "PermissionDeniedError", None),
            )
            if isinstance(e, type)
        )
        return not (non_retryable and isinstance(exc, non_retryable))

    # Tenacity retry with exponential backoff
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=30),
        stop=tenacity.stop_after_attempt(num_retries),
        retry=tenacity.retry_if_exception(_retryable),
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

        # Thinking models (Qwen 3.5, DeepSeek-R1, etc.) may place their
        # output in reasoning_content instead of content when the response
        # is generated entirely within <think> tags.  LMStudio separates
        # these into distinct fields in the API response.  Fall back to
        # reasoning_content when content is empty.
        content = _strip_think_blocks(response.choices[0].message.content or "")
        if not content:
            content = getattr(response.choices[0].message, "reasoning_content", None) or ""
        finish_reason = response.choices[0].finish_reason or "stop"
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        truncated = finish_reason == "length"

        # If the response was truncated and we requested structured output, retry once
        # with a larger budget to try to get complete JSON — but bound the retry to the
        # model's output ceiling so doubling can't overflow into a 400 (65536*2=131072
        # exceeds even Opus's 128K cap). Skip the retry entirely if already at the ceiling.
        if truncated and response_schema:
            if _extract_json(content) is not None:
                # A non-stopping local model padded `\n` AFTER a complete JSON document and
                # only then hit the cap — not a real truncation. Salvage it instead of paying
                # for a doubled retry that (on a padder) just generates more padding.
                log.info(
                    "llm_truncation_salvaged",
                    model=model,
                    output_tokens=output_tokens,
                    max_tokens=max_tokens,
                )
                truncated = False
            elif is_custom and _looks_like_padding(content):
                # Degenerate newline loop with no recoverable JSON (the object never closed).
                # Doubling max_tokens would just pad more, so don't — accept the failure once.
                # The stop sequences + repeat_penalty above are what fix this going forward.
                log.warning(
                    "llm_padding_truncation_no_double",
                    model=model,
                    output_tokens=output_tokens,
                    max_tokens=max_tokens,
                )
            else:
                ceiling = max_output_ceiling if max_output_ceiling is not None else max_tokens * 2
                doubled = min(max_tokens * 2, ceiling)
                if doubled > max_tokens:
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

                    content = _strip_think_blocks(response.choices[0].message.content or "")
                    if not content:
                        content = getattr(response.choices[0].message, "reasoning_content", None) or ""
                    finish_reason = response.choices[0].finish_reason or "stop"
                    input_tokens += response.usage.prompt_tokens if response.usage else 0
                    # ADD, don't overwrite — the first (truncated) call's output
                    # tokens were really spent and must count toward cost/budget
                    # (review §3 accounting — P6-15).
                    output_tokens += response.usage.completion_tokens if response.usage else 0
                    truncated = finish_reason == "length"

                    if truncated:
                        log.warning(
                            "llm_response_still_truncated",
                            model=model,
                            output_tokens=output_tokens,
                            max_tokens=doubled,
                        )
                else:
                    # Already at the model's ceiling — doubling would 400. Accept the truncation.
                    log.warning(
                        "llm_response_truncated_at_ceiling",
                        model=model,
                        output_tokens=output_tokens,
                        max_tokens=max_tokens,
                    )

        # Parse JSON if structured output was requested. _extract_json handles markdown fences
        # and a complete document followed by trailing newline/junk padding from non-stopping
        # local models. allow_completion also rebuilds an object the model left *unterminated*
        # because it padded whitespace before the closing `}` and then stopped (Gemma on
        # LMStudio halts with finish_reason=stop mid-padding, so `truncated` is False and no
        # doubling retry fires — this is the only place that recovers it). It returns None only
        # when nothing recoverable is there (empty, or cut mid-value) — a real failure.
        parsed = None
        if response_schema and content:
            # Only allow the last-resort completion when this is a local/custom provider AND the
            # output carries the padding signature — otherwise a cloud model that returns prose
            # with a stray `{` would get "completed" into a bogus empty object. In the real
            # scenario the model padded whitespace and stopped before the closing brace, so the
            # normal strict+scan parse fails and completion is what recovers the object.
            padding_recovery = is_custom and _looks_like_padding(content)
            parsed = _extract_json(content, allow_completion=padding_recovery)
            if parsed is None:
                log.warning(
                    "llm_json_parse_failed",
                    model=model,
                    content_preview=content[:200],
                    truncated=truncated,
                )
            elif padding_recovery:
                # Recovered structured data from a response the model padded to (or past) the
                # closing brace. Surface it — the model burned output tokens on `\n` padding and
                # finish_reason may be `stop`, so it never counted as a truncation.
                log.info(
                    "llm_json_recovered_from_padding",
                    model=model,
                    output_tokens=output_tokens,
                    finish_reason=finish_reason,
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

        # Log call to audit — mark as failed if structured output was
        # requested but JSON parsing failed (the HTTP call succeeded but the
        # response is unusable by the caller).
        _audit_success = True
        _audit_error = None
        if response_schema and not result.parsed:
            _audit_success = False
            _audit_error = "Structured output requested but response was not valid JSON"

        await log_to_audit(
            event_id=event_id,
            card_id=card_id,
            step=step,
            model=model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            success=_audit_success,
            error=_audit_error,
        )

        # Check budget limit (fire-and-forget to avoid slowing the pipeline)
        try:
            from laya.pipeline.budget import check_budget
            from laya.tasks import create_task as create_tracked_task
            create_tracked_task(check_budget())
        except Exception:
            pass  # Never let budget check break the pipeline

        return result

    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.error("llm_call_failed", role=role, model=model, error=str(e))

        await log_to_audit(
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
    max_tokens: int = DEFAULT_MAX_TOKENS,
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

    # The agent inference backend can't drive a streaming tool-loop (chat / Coherence),
    # so those roles must stay on an API or local model. The Models UI keeps chat/trace as
    # dropdowns (not agent text inputs) to prevent this; this guard is a defensive backstop.
    from laya.llm import agent_backend

    if agent_backend.is_agent_model(model):
        msg = (
            "The selected agent backend doesn't support streaming chat/Coherence yet. "
            "Choose an API or local model for the chat and trace roles."
        )
        log.error("llm_stream_agent_unsupported", role=role, model=model)
        yield StreamEvent(type="error", content=msg, model=model)
        return

    # Shared prep — model already resolved above; agent backend rejected. This is
    # the same seam llm_call uses, so streaming now also gets the max_tokens clamp
    # it was silently missing (it was sending DEFAULT_MAX_TOKENS to strict vLLM
    # servers → 400) plus consistent provider/timeout handling (review §5.6 — P7-5).
    model, kwargs, _prep = await _prepare_call_kwargs(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        space_id=space_id,
        tools=tools,
        response_schema=None,
        stream=True,
    )
    # Ask the provider for real streaming usage in a final chunk instead of the
    # chars/4 estimate that excludes the entire tool block (review §3 — P6-17).
    kwargs["stream_options"] = {"include_usage": True}

    start = time.monotonic()

    try:
        response = await acompletion(**kwargs)

        collected_content = ""
        collected_tool_calls: dict[int, dict] = {}  # index -> {id, name, arguments_str}
        finish_reason = "stop"
        real_usage = None  # populated by the include_usage final chunk

        async for chunk in response:
            # The include_usage final chunk carries token counts and an empty
            # choices list — capture it before the delta guard skips the chunk.
            if getattr(chunk, "usage", None):
                real_usage = chunk.usage
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

        # Prefer the provider's real usage (includes the tool block); fall back to
        # a chars/4 estimate when a provider doesn't return stream usage (P6-17).
        if real_usage is not None:
            est_input_tokens = getattr(real_usage, "prompt_tokens", 0) or 0
            est_output_tokens = getattr(real_usage, "completion_tokens", 0) or 0
        else:
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
        await log_to_audit(
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
        await log_to_audit(
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
