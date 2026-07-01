# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the LLM client wrapper."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.llm.client import LLMResponse, _get_model_for_role, llm_call


# --- Model name prefixing ---


def test_model_prefix_anthropic():
    with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
        assert _get_model_for_role("router") == "anthropic/claude-haiku-4-5-20251001"


def test_model_prefix_openai():
    with patch("laya.llm.client.load_settings", return_value={"models": {"router": "gpt-4o-mini"}}):
        assert _get_model_for_role("router") == "openai/gpt-4o-mini"


def test_model_prefix_google():
    with patch("laya.llm.client.load_settings", return_value={"models": {"router": "gemini-2.0-flash"}}):
        assert _get_model_for_role("router") == "gemini/gemini-2.0-flash"


def test_model_prefix_ollama_passthrough():
    with patch("laya.llm.client.load_settings", return_value={"models": {"router": "ollama/llama3"}}):
        assert _get_model_for_role("router") == "ollama/llama3"


def test_model_prefix_already_prefixed():
    with patch("laya.llm.client.load_settings", return_value={"models": {"router": "anthropic/claude-3-opus"}}):
        assert _get_model_for_role("router") == "anthropic/claude-3-opus"


def test_model_default_when_missing():
    with patch("laya.llm.client.load_settings", return_value={"models": {}}):
        result = _get_model_for_role("router")
        assert result == "anthropic/claude-haiku-4-5"


# --- LLM call ---


def _mock_acompletion_response(content='{"result": "test"}', prompt_tokens=100, completion_tokens=50):
    """Create a mock litellm acompletion response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = prompt_tokens
    mock_response.usage.completion_tokens = completion_tokens
    return mock_response


@pytest.mark.asyncio
async def test_llm_call_success(db):
    """Successful LLM call returns content, parsed JSON, and logs to audit."""
    mock_response = _mock_acompletion_response()

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    result = await llm_call(
                        role="router",
                        messages=[{"role": "user", "content": "test"}],
                        response_schema={"name": "test", "schema": {"type": "object"}},
                        event_id="evt_test",
                        step="route",
                    )

    assert isinstance(result, LLMResponse)
    assert result.parsed == {"result": "test"}
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert "anthropic" in result.model

    # Check audit log
    async with db.execute("SELECT * FROM audit_log WHERE event_id = 'evt_test'") as cursor:
        row = await cursor.fetchone()
        assert row is not None
        assert row["step"] == "route"
        assert row["success"] == 1
        assert row["input_tokens"] == 100


@pytest.mark.asyncio
async def test_llm_call_logs_audit_on_failure(db):
    """Failed LLM call still writes audit log with success=False."""
    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=Exception("API down")):
        with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    with pytest.raises(Exception, match="API down"):
                        await llm_call(
                            role="router",
                            messages=[{"role": "user", "content": "test"}],
                            event_id="evt_fail",
                            step="route",
                        )

    async with db.execute("SELECT * FROM audit_log WHERE event_id = 'evt_fail'") as cursor:
        row = await cursor.fetchone()
        assert row is not None
        assert row["success"] == 0
        assert "API down" in row["error"]


@pytest.mark.asyncio
async def test_llm_call_without_schema(db):
    """LLM call without response_schema returns content but no parsed dict."""
    mock_response = _mock_acompletion_response(content="Hello, world!", prompt_tokens=50, completion_tokens=10)

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"chat": "claude-sonnet-4-5-20250929"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    result = await llm_call(
                        role="chat",
                        messages=[{"role": "user", "content": "hello"}],
                        step="chat",
                    )

    assert result.content == "Hello, world!"
    assert result.parsed is None


@pytest.mark.asyncio
async def test_llm_call_malformed_json(db):
    """LLM call with schema but malformed JSON returns content with parsed=None."""
    mock_response = _mock_acompletion_response(content="not valid json {")

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    result = await llm_call(
                        role="router",
                        messages=[{"role": "user", "content": "test"}],
                        response_schema={"name": "test", "schema": {"type": "object"}},
                        step="route",
                    )

    assert result.content == "not valid json {"
    assert result.parsed is None


# --- Reasoning suppression for structured-output calls ---


async def _capture_acompletion_kwargs(*, response_schema, custom_meta):
    """Run llm_call against a fake local provider and return the kwargs passed to
    litellm.acompletion. custom_meta=None simulates a cloud provider."""
    mock_response = _mock_acompletion_response()
    mock_ac = AsyncMock(return_value=mock_response)

    with patch("litellm.acompletion", mock_ac):
        with patch("laya.llm.client.load_settings", return_value={"models": {"router": "lmstudio-local/qwen3.5-9b"}}):
            with patch("laya.llm.client._get_custom_provider_meta", return_value=custom_meta):
                with patch(
                    "laya.llm.client._resolve_custom_provider",
                    return_value=("openai/qwen3.5-9b", {"api_base": "http://localhost:1234/v1", "api_key": "x"}),
                ):
                    with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                        with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                            await llm_call(
                                role="router",
                                messages=[{"role": "user", "content": "test"}],
                                response_schema=response_schema,
                                step="route",
                            )
    return mock_ac.call_args.kwargs


_REASONING_META = {
    "provider_type": "lmstudio",
    "supports_structured_output": True,
    "supports_tool_calling": True,
    "supports_reasoning": True,
}


@pytest.mark.asyncio
async def test_disables_thinking_for_reasoning_schema_call(db):
    """A schema call to a reasoning-capable local provider disables thinking so the
    <think> block can't eat the max_tokens budget before the JSON."""
    kwargs = await _capture_acompletion_kwargs(
        response_schema={"name": "test", "schema": {"type": "object"}},
        custom_meta=_REASONING_META,
    )
    assert kwargs["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


@pytest.mark.asyncio
async def test_no_thinking_flag_without_schema(db):
    """Without a schema (e.g. chat) we keep reasoning on — don't send the flag."""
    kwargs = await _capture_acompletion_kwargs(response_schema=None, custom_meta=_REASONING_META)
    assert "extra_body" not in kwargs


@pytest.mark.asyncio
async def test_no_thinking_flag_for_non_reasoning_provider(db):
    """A provider that isn't reasoning-capable shouldn't get the thinking kwarg.
    (extra_body may still carry repeat_penalty for custom+schema calls, but never the
    chat_template_kwargs thinking flag.)"""
    meta = {**_REASONING_META, "supports_reasoning": False}
    kwargs = await _capture_acompletion_kwargs(
        response_schema={"name": "test", "schema": {"type": "object"}}, custom_meta=meta
    )
    assert "chat_template_kwargs" not in kwargs.get("extra_body", {})


@pytest.mark.asyncio
async def test_no_thinking_flag_for_cloud_provider(db):
    """A None custom_meta (no reasoning capability) never gets the thinking kwarg."""
    kwargs = await _capture_acompletion_kwargs(
        response_schema={"name": "test", "schema": {"type": "object"}}, custom_meta=None
    )
    assert "chat_template_kwargs" not in kwargs.get("extra_body", {})


# --- max_tokens context-window clamping ---

_SCHEMA = {"name": "test", "schema": {"type": "object"}}


@pytest.mark.asyncio
async def test_max_tokens_clamped_to_cloud_output_cap(db):
    """A known cloud model clamps the lenient default down to its max output cap."""
    mock_ac = AsyncMock(return_value=_mock_acompletion_response())
    with patch("litellm.acompletion", mock_ac):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-opus-4-1-20250805"}}):
            with patch("litellm.get_model_info", return_value={"max_output_tokens": 8192}):
                with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                    with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                        await llm_call(
                            role="stager",
                            messages=[{"role": "user", "content": "test"}],
                            response_schema=_SCHEMA,
                            step="stage",
                        )
    # default max_tokens (DEFAULT_MAX_TOKENS=65536) clamped to the model's 8192 cap
    assert mock_ac.call_args.kwargs["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_max_tokens_clamped_to_local_context_window(db):
    """A local model clamps to (discovered context window − prompt estimate − margin)."""
    from laya.llm.providers import DiscoveredModel

    disc = DiscoveredModel(key="lmstudio-local/qwen3.5-9b", display_name="q", max_context_length=32768)
    mock_ac = AsyncMock(return_value=_mock_acompletion_response())
    with patch("litellm.acompletion", mock_ac):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "lmstudio-local/qwen3.5-9b"}}):
            with patch(
                "laya.llm.client._resolve_custom_provider",
                return_value=("openai/qwen3.5-9b", {"api_base": "http://x/v1", "api_key": "x"}),
            ):
                with patch("laya.llm.providers.get_custom_provider", return_value={"id": "lmstudio-local"}):
                    with patch("laya.llm.providers.discover_models_cached", new_callable=AsyncMock, return_value=[disc]):
                        with patch("laya.llm.client._estimate_prompt_tokens", return_value=2000):
                            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                                    await llm_call(
                                        role="stager",
                                        messages=[{"role": "user", "content": "test"}],
                                        response_schema=_SCHEMA,
                                        step="stage",
                                    )
    # 32768 (window) − 2000 (prompt) − 512 (_OUTPUT_MARGIN)
    assert mock_ac.call_args.kwargs["max_tokens"] == 32768 - 2000 - 512


@pytest.mark.asyncio
async def test_no_clamp_for_unknown_model(db):
    """An unknown model (LiteLLM has no info) is left unclamped — behavior unchanged."""
    mock_ac = AsyncMock(return_value=_mock_acompletion_response())
    with patch("litellm.acompletion", mock_ac):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "openai/some-unknown-model"}}):
            with patch("litellm.get_model_info", side_effect=Exception("unknown model")):
                with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                    with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                        await llm_call(
                            role="stager",
                            messages=[{"role": "user", "content": "test"}],
                            response_schema=_SCHEMA,
                            step="stage",
                            max_tokens=65536,
                        )
    assert mock_ac.call_args.kwargs["max_tokens"] == 65536


@pytest.mark.asyncio
async def test_truncation_retry_skipped_at_ceiling(db):
    """When max_tokens is already at the model's ceiling, a truncated response must NOT
    trigger a doubled retry (131072 would 400 on a cloud model)."""
    truncated = _mock_acompletion_response(content='{"partial":')
    truncated.choices[0].finish_reason = "length"
    mock_ac = AsyncMock(return_value=truncated)
    with patch("litellm.acompletion", mock_ac):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-opus-4-1-20250805"}}):
            with patch("litellm.get_model_info", return_value={"max_output_tokens": 8192}):
                with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                    with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                        result = await llm_call(
                            role="stager",
                            messages=[{"role": "user", "content": "test"}],
                            response_schema=_SCHEMA,
                            step="stage",
                        )
    # clamped to 8192; doubling → min(16384, 8192) = 8192, not > 8192 → no second call
    assert mock_ac.call_count == 1
    assert result.truncated is True
    assert mock_ac.call_args.kwargs["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_truncation_retry_bounded_by_ceiling(db):
    """A retry that IS allowed never requests more than the model's ceiling."""
    truncated = _mock_acompletion_response(content='{"partial":')
    truncated.choices[0].finish_reason = "length"
    mock_ac = AsyncMock(side_effect=[truncated, _mock_acompletion_response()])
    with patch("litellm.acompletion", mock_ac):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-opus-4-1-20250805"}}):
            with patch("litellm.get_model_info", return_value={"max_output_tokens": 6000}):
                with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                    with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                        result = await llm_call(
                            role="stager",
                            messages=[{"role": "user", "content": "test"}],
                            response_schema=_SCHEMA,
                            step="stage",
                            max_tokens=4000,
                        )
    # 4000 < 6000 → not clamped; truncated → doubled = min(8000, 6000) = 6000 (not 8000)
    assert mock_ac.call_count == 2
    assert mock_ac.call_args_list[1].kwargs["max_tokens"] == 6000
    assert result.parsed == {"result": "test"}


# --- _extract_json salvage (Gemma/LMStudio newline padding) ---


def test_extract_json_plain():
    from laya.llm.client import _extract_json

    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_trailing_newline_padding():
    """A complete object followed by a non-stopping model's newline padding still parses."""
    from laya.llm.client import _extract_json

    assert _extract_json('{"a": 1}' + "\n" * 200) == {"a": 1}


def test_extract_json_trailing_junk_after_complete_object():
    """A complete object followed by non-whitespace junk is salvaged via the brace scan."""
    from laya.llm.client import _extract_json

    assert _extract_json('{"a": 1}\n\nSome trailing commentary the model added') == {"a": 1}


def test_extract_json_markdown_fence():
    from laya.llm.client import _extract_json

    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_array():
    from laya.llm.client import _extract_json

    assert _extract_json("[1, 2, 3]\n\n\n") == [1, 2, 3]


def test_extract_json_brace_inside_string_not_confused():
    from laya.llm.client import _extract_json

    assert _extract_json('{"a": "}{"}\n\n') == {"a": "}{"}


def test_extract_json_incomplete_returns_none():
    """A genuinely unterminated document (never closes) is a real failure → None by default —
    completion is opt-in so the truncation detector keeps triggering its retry."""
    from laya.llm.client import _extract_json

    assert _extract_json('{"a": 1,') is None
    assert _extract_json('{"a": "unterminated' + "\n" * 50) is None


# --- completion salvage: object left unterminated by whitespace padding (allow_completion) ---

# The exact Gemma-on-LMStudio shape: a fully-formed object whose only missing part is the final
# `}`, because the model padded `\n  ` (newline + indent) after the last field and then stopped.
_PADDED_UNTERMINATED = (
    '{\n  "header": "Yi Ting Luah moved FERR-1496 to SPECS",\n'
    '  "suggested_tags": [\n    "data-source",\n    "scope-management"\n  ]'
    + "\n  " * 300
)


def test_extract_json_padded_unterminated_needs_completion():
    """Without allow_completion the padded-but-unterminated object is None (unchanged contract);
    with it, the missing closing brace is appended and the object is recovered."""
    from laya.llm.client import _extract_json

    assert _extract_json(_PADDED_UNTERMINATED) is None
    assert _extract_json(_PADDED_UNTERMINATED, allow_completion=True) == {
        "header": "Yi Ting Luah moved FERR-1496 to SPECS",
        "suggested_tags": ["data-source", "scope-management"],
    }


def test_complete_json_closes_nested_containers():
    """Completion balances every still-open container, not just the outermost."""
    from laya.llm.client import _extract_json

    assert _extract_json('{"a": {"b": [1, 2]' + "\n  " * 50, allow_completion=True) == {
        "a": {"b": [1, 2]}
    }


def test_complete_json_drops_dangling_comma():
    """A model that stops right after a separator (before the next value) still recovers."""
    from laya.llm.client import _extract_json

    assert _extract_json('{"a": 1,' + "\n" * 20, allow_completion=True) == {"a": 1}
    assert _extract_json('["a", "b",' + "\n" * 20, allow_completion=True) == ["a", "b"]


def test_complete_json_bails_inside_string():
    """A cut mid-string is a genuine truncation (padding would be real value content), so even
    with completion enabled it stays None rather than corrupting the value."""
    from laya.llm.client import _extract_json

    assert _extract_json('{"a": "unterminated' + "\n" * 50, allow_completion=True) is None


def test_complete_json_rejects_unrecoverable():
    """Completion never returns invalid JSON — json.loads is the final guard."""
    from laya.llm.client import _extract_json

    # Key with no value: appending `}` yields `{"a": "b", "c"}` which is invalid → None.
    assert _extract_json('{"a": "b", "c"' + "\n" * 20, allow_completion=True) is None


def test_extract_json_empty_returns_none():
    from laya.llm.client import _extract_json

    assert _extract_json("") is None
    assert _extract_json("\n\n\n") is None


def test_looks_like_padding_newlines():
    from laya.llm.client import _looks_like_padding

    assert _looks_like_padding('{"a": "te' + "\n" * 300) is True


def test_looks_like_padding_real_content():
    from laya.llm.client import _looks_like_padding

    assert _looks_like_padding('{"summary": "a genuinely long and varied response body"}') is False


# --- Stop sequences + repetition penalty for non-stopping local models ---


@pytest.mark.asyncio
async def test_stop_and_penalty_for_local_schema_call(db):
    """Schema calls to a custom/local provider pass the turn-terminator stop sequences and a
    windowed repeat_penalty so a non-stopping model (e.g. Gemma) can't pad newlines forever."""
    from laya.llm.client import _LOCAL_REPEAT_PENALTY, _LOCAL_STOP_SEQUENCES

    kwargs = await _capture_acompletion_kwargs(
        response_schema={"name": "test", "schema": {"type": "object"}},
        custom_meta={**_REASONING_META, "supports_reasoning": False},
    )
    assert kwargs["stop"] == _LOCAL_STOP_SEQUENCES
    assert kwargs["extra_body"]["repeat_penalty"] == _LOCAL_REPEAT_PENALTY


@pytest.mark.asyncio
async def test_stop_without_penalty_for_local_non_schema(db):
    """Without a schema (chat) the local provider still gets stop sequences, but no
    repeat_penalty — that's scoped to structured-output calls."""
    from laya.llm.client import _LOCAL_STOP_SEQUENCES

    kwargs = await _capture_acompletion_kwargs(
        response_schema=None,
        custom_meta={**_REASONING_META, "supports_reasoning": False},
    )
    assert kwargs["stop"] == _LOCAL_STOP_SEQUENCES
    assert "repeat_penalty" not in kwargs.get("extra_body", {})


@pytest.mark.asyncio
async def test_no_stop_for_cloud_provider(db):
    """Cloud providers manage their own stop tokens — we don't inject the local stop list."""
    mock_ac = AsyncMock(return_value=_mock_acompletion_response())
    with patch("litellm.acompletion", mock_ac):
        with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    await llm_call(
                        role="router",
                        messages=[{"role": "user", "content": "test"}],
                        response_schema={"name": "test", "schema": {"type": "object"}},
                        step="route",
                    )
    assert "stop" not in mock_ac.call_args.kwargs


async def _run_local_truncated(content):
    """Run a schema llm_call against a fake local provider whose single response is truncated
    (finish_reason=length) with the given content. Returns (mock_ac, result)."""
    truncated = _mock_acompletion_response(content=content)
    truncated.choices[0].finish_reason = "length"
    mock_ac = AsyncMock(return_value=truncated)
    meta = {
        "provider_type": "lmstudio",
        "supports_structured_output": True,
        "supports_tool_calling": True,
        "supports_reasoning": False,
    }
    with patch("litellm.acompletion", mock_ac):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "lmstudio-local/gemma-3-4b"}}):
            with patch(
                "laya.llm.client._resolve_custom_provider",
                return_value=("openai/gemma-3-4b", {"api_base": "http://x/v1", "api_key": "x"}),
            ):
                with patch("laya.llm.client._get_custom_provider_meta", return_value=meta):
                    with patch(
                        "laya.llm.client._resolve_max_output_ceiling",
                        new_callable=AsyncMock,
                        return_value=None,
                    ):
                        with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                            with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                                result = await llm_call(
                                    role="stager",
                                    messages=[{"role": "user", "content": "test"}],
                                    response_schema=_SCHEMA,
                                    step="stage",
                                )
    return mock_ac, result


@pytest.mark.asyncio
async def test_truncation_salvaged_skips_double(db):
    """A truncated response that is a COMPLETE JSON doc followed by newline padding is salvaged
    in place — no doubled retry, and truncated is cleared."""
    mock_ac, result = await _run_local_truncated('{"result": "test"}' + "\n" * 50)
    assert mock_ac.call_count == 1  # no doubled retry
    assert result.parsed == {"result": "test"}
    assert result.truncated is False


@pytest.mark.asyncio
async def test_padding_truncation_skips_double(db):
    """A truncated response that is unterminated JSON drowned in newline padding is recognized
    as a degenerate padder — no doubled retry (doubling would just generate more padding)."""
    mock_ac, result = await _run_local_truncated('{"result": "te' + "\n" * 300)
    assert mock_ac.call_count == 1  # no doubled retry
    assert result.parsed is None
    assert result.truncated is True


async def _run_local_stopped(content):
    """Run a schema llm_call against a fake local provider whose single response finishes
    NORMALLY (finish_reason=stop) with the given content — the actual Gemma-on-LMStudio shape,
    where padding stops on <eos> before the object closes so nothing marks it truncated."""
    stopped = _mock_acompletion_response(content=content)
    stopped.choices[0].finish_reason = "stop"
    mock_ac = AsyncMock(return_value=stopped)
    meta = {
        "provider_type": "lmstudio",
        "supports_structured_output": True,
        "supports_tool_calling": True,
        "supports_reasoning": False,
    }
    with patch("litellm.acompletion", mock_ac):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "lmstudio-local/gemma-4-e4b"}}):
            with patch(
                "laya.llm.client._resolve_custom_provider",
                return_value=("openai/gemma-4-e4b", {"api_base": "http://x/v1", "api_key": "x"}),
            ):
                with patch("laya.llm.client._get_custom_provider_meta", return_value=meta):
                    with patch(
                        "laya.llm.client._resolve_max_output_ceiling",
                        new_callable=AsyncMock,
                        return_value=None,
                    ):
                        with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                            with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                                result = await llm_call(
                                    role="stager",
                                    messages=[{"role": "user", "content": "test"}],
                                    response_schema=_SCHEMA,
                                    step="stage",
                                )
    return mock_ac, result


@pytest.mark.asyncio
async def test_stopped_padding_object_recovered(db):
    """The reported regression: Gemma emits a full object then pads `\n  ` and halts on <eos>
    (finish_reason=stop, so `truncated` is False and no doubling fires). The object never closed,
    yet the parse site's allow_completion recovers it instead of returning parsed=None."""
    content = '{"result": "test"}'[:-1] + "\n  " * 400  # drop the closing brace, then pad
    mock_ac, result = await _run_local_stopped(content)
    assert mock_ac.call_count == 1  # finish_reason=stop → no retry at all
    assert result.truncated is False
    assert result.parsed == {"result": "test"}
