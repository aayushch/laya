"""Tests for OS keychain integration."""

import os
from unittest.mock import MagicMock, patch

import pytest

from laya.security.keychain import (
    delete_api_key,
    get_api_key,
    has_api_key,
    load_all_keys_to_env,
    store_api_key,
)


@pytest.fixture
def mock_keyring():
    """Mock the keyring module."""
    mock = MagicMock()
    mock.get_password = MagicMock(return_value=None)
    mock.set_password = MagicMock()
    mock.delete_password = MagicMock()
    with patch.dict("sys.modules", {"keyring": mock}):
        yield mock


def test_store_and_retrieve(mock_keyring):
    """Store a key, then retrieve it."""
    mock_keyring.get_password.return_value = "sk-ant-test123"

    result = store_api_key("anthropic", "sk-ant-test123")
    assert result is True
    mock_keyring.set_password.assert_called_once_with("laya-engine", "anthropic", "sk-ant-test123")

    # Verify env var was set
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-test123"

    key = get_api_key("anthropic")
    assert key == "sk-ant-test123"

    # Clean up
    os.environ.pop("ANTHROPIC_API_KEY", None)


def test_store_sets_env_var(mock_keyring):
    """Storing a key sets the corresponding environment variable."""
    store_api_key("openai", "sk-openai-test")
    assert os.environ.get("OPENAI_API_KEY") == "sk-openai-test"
    os.environ.pop("OPENAI_API_KEY", None)


def test_missing_key_returns_none(mock_keyring):
    """Getting a non-existent key returns None."""
    mock_keyring.get_password.return_value = None
    assert get_api_key("anthropic") is None


def test_has_api_key_true(mock_keyring):
    """has_api_key returns True when key exists."""
    mock_keyring.get_password.return_value = "some-key"
    assert has_api_key("anthropic") is True


def test_has_api_key_false(mock_keyring):
    """has_api_key returns False when key doesn't exist."""
    mock_keyring.get_password.return_value = None
    assert has_api_key("anthropic") is False


def test_delete_api_key(mock_keyring):
    """Delete a key from keychain and env."""
    os.environ["ANTHROPIC_API_KEY"] = "to-delete"
    result = delete_api_key("anthropic")
    assert result is True
    mock_keyring.delete_password.assert_called_once_with("laya-engine", "anthropic")
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_load_all_keys_to_env(mock_keyring):
    """load_all_keys_to_env loads stored keys into environment."""
    def mock_get(service, provider):
        return {"anthropic": "sk-ant-1", "openai": None, "google": "ggl-2"}.get(provider)

    mock_keyring.get_password.side_effect = mock_get

    results = load_all_keys_to_env()
    assert results["anthropic"] is True
    assert results["openai"] is False
    assert results["google"] is True

    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-1"
    assert os.environ.get("GEMINI_API_KEY") == "ggl-2"

    # Clean up
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("GEMINI_API_KEY", None)


def test_store_graceful_on_keyring_error():
    """store_api_key returns False if keyring raises."""
    mock_kr = MagicMock()
    mock_kr.set_password.side_effect = Exception("Keychain locked")
    with patch.dict("sys.modules", {"keyring": mock_kr}):
        result = store_api_key("anthropic", "test-key")
        assert result is False
