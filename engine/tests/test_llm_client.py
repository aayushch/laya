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
        assert _get_model_for_role("router") == "google/gemini-2.0-flash"


def test_model_prefix_ollama_passthrough():
    with patch("laya.llm.client.load_settings", return_value={"models": {"router": "ollama/llama3"}}):
        assert _get_model_for_role("router") == "ollama/llama3"


def test_model_prefix_already_prefixed():
    with patch("laya.llm.client.load_settings", return_value={"models": {"router": "anthropic/claude-3-opus"}}):
        assert _get_model_for_role("router") == "anthropic/claude-3-opus"


def test_model_default_when_missing():
    with patch("laya.llm.client.load_settings", return_value={"models": {}}):
        result = _get_model_for_role("router")
        assert result == "anthropic/claude-haiku-4-5-20251001"


# --- LLM call ---


@pytest.mark.asyncio
async def test_llm_call_success(db_full):
    """Successful LLM call returns content, parsed JSON, and logs to audit."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"result": "test"}'
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 50

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
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
    async with db_full.execute("SELECT * FROM audit_log WHERE event_id = 'evt_test'") as cursor:
        row = await cursor.fetchone()
        assert row is not None
        assert row["step"] == "route"
        assert row["success"] == 1
        assert row["input_tokens"] == 100


@pytest.mark.asyncio
async def test_llm_call_logs_audit_on_failure(db_full):
    """Failed LLM call still writes audit log with success=False."""
    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=Exception("API down")):
        with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
            with pytest.raises(Exception, match="API down"):
                await llm_call(
                    role="router",
                    messages=[{"role": "user", "content": "test"}],
                    event_id="evt_fail",
                    step="route",
                )

    async with db_full.execute("SELECT * FROM audit_log WHERE event_id = 'evt_fail'") as cursor:
        row = await cursor.fetchone()
        assert row is not None
        assert row["success"] == 0
        assert "API down" in row["error"]


@pytest.mark.asyncio
async def test_llm_call_without_schema(db_full):
    """LLM call without response_schema returns content but no parsed dict."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hello, world!"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 50
    mock_response.usage.completion_tokens = 10

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"chat": "claude-sonnet-4-5-20250929"}}):
            result = await llm_call(
                role="chat",
                messages=[{"role": "user", "content": "hello"}],
                step="chat",
            )

    assert result.content == "Hello, world!"
    assert result.parsed is None


@pytest.mark.asyncio
async def test_llm_call_malformed_json(db_full):
    """LLM call with schema but malformed JSON returns content with parsed=None."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "not valid json {"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 50

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
            result = await llm_call(
                role="router",
                messages=[{"role": "user", "content": "test"}],
                response_schema={"name": "test", "schema": {"type": "object"}},
                step="route",
            )

    assert result.content == "not valid json {"
    assert result.parsed is None
