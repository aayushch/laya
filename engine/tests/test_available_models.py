# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the available-models list (#25)."""

import litellm
import pytest
from httpx import ASGITransport, AsyncClient

from laya.api import settings_api
from laya.api.dashboard_api import MODEL_PRICING
from laya.api.settings_api import _fetch_models_for_provider

_COST = {
    "gemini/gemini-2.0-flash": {"deprecation_date": "2000-01-01"},
    "gemini/gemini-3.1-flash-lite": {"deprecation_date": "2999-01-01"},
    "gemini/gemini-3.8-flash": {},
    "openrouter/openai/gpt-4-turbo": {"deprecation_date": "2999-01-01"},
}


@pytest.fixture
def catalog(monkeypatch):
    """A fake litellm catalog, with a provider key present and an empty cache."""
    monkeypatch.setattr(litellm, "model_cost", _COST)
    monkeypatch.setattr(
        litellm, "models_by_provider",
        {
            "gemini": {"gemini/gemini-2.0-flash", "gemini/gemini-3.1-flash-lite", "gemini/gemini-3.8-flash"},
            "openrouter": {"openrouter/openai/gpt-4-turbo"},
        },
    )
    monkeypatch.setattr(settings_api, "get_api_key", lambda provider: "key")
    settings_api._model_cache.clear()
    yield
    settings_api._model_cache.clear()


def _ids(models):
    return {m["id"]: m.get("retires_on") for m in models}


class TestFetchModelsForProvider:
    def test_static_fallback_drops_retired_and_tags_future(self, catalog, monkeypatch):
        """The static list drops past-dated models and tags future-dated ones."""
        monkeypatch.setattr(litellm, "get_valid_models", lambda **kw: [])
        models, verified = _fetch_models_for_provider("google")
        assert verified is False
        assert _ids(models) == {
            "gemini/gemini-3.1-flash-lite": "2999-01-01",
            "gemini/gemini-3.8-flash": None,
        }

    def test_live_list_is_verified_and_never_filtered(self, catalog, monkeypatch):
        """A live list is verified and not filtered."""
        monkeypatch.setattr(
            litellm, "get_valid_models",
            lambda **kw: ["gemini/gemini-2.0-flash", "gemini/gemini-3.8-flash"],
        )
        models, verified = _fetch_models_for_provider("google")
        assert verified is True
        assert _ids(models) == {"gemini/gemini-2.0-flash": None, "gemini/gemini-3.8-flash": None}

    def test_openrouter_is_never_verified(self, catalog, monkeypatch):
        """OpenRouter lists are never verified."""
        monkeypatch.setattr(litellm, "get_valid_models", lambda **kw: ["openrouter/openai/gpt-4-turbo"])
        models, verified = _fetch_models_for_provider("openrouter")
        assert verified is False
        assert _ids(models) == {"openrouter/openai/gpt-4-turbo": "2999-01-01"}


@pytest.mark.asyncio
class TestAvailableModelsEndpoint:
    async def test_every_provider_entry_has_verified(self, catalog, monkeypatch):
        """Every provider group carries verified, cached or not."""
        monkeypatch.setattr(settings_api, "has_api_key", lambda p: p == "google")
        monkeypatch.setattr(settings_api, "get_all_custom_providers", lambda: [])
        monkeypatch.setattr(litellm, "get_valid_models", lambda **kw: [])

        from laya.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            fresh = (await client.get("/settings/available-models")).json()["providers"]
            cached = (await client.get("/settings/available-models")).json()["providers"]

        assert fresh == cached
        assert [(p["provider"], p["verified"]) for p in fresh] == [("google", False)]


def test_pricing_keys_use_audit_model_prefixes():
    """#25: pricing keys use the anthropic/, openai/ and gemini/ prefixes."""
    assert all(k.split("/")[0] in {"anthropic", "openai", "gemini"} for k in MODEL_PRICING)
