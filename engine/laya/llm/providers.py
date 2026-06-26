# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Custom model provider discovery and management (LMStudio, Ollama, OpenAI-compatible)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from laya.config import load_settings
from laya.http_client import get_client

log = structlog.get_logger()

# Cache: provider_id -> (timestamp, models)
_discovery_cache: dict[str, tuple[float, list[DiscoveredModel]]] = {}
_DISCOVERY_CACHE_TTL = 300  # 5 minutes


@dataclass
class DiscoveredModel:
    """A model discovered from a custom provider."""

    key: str  # Full key: "provider_id/model_name"
    display_name: str
    model_type: str = "llm"  # "llm" or "embedding"
    provider_id: str = ""
    max_context_length: int | None = None
    supports_tool_calling: bool = False
    supports_structured_output: bool = False
    supports_vision: bool = False
    supports_reasoning: bool = False  # "thinking" model (Qwen3, DeepSeek-R1, etc.)
    params_string: str | None = None
    quantization: str | None = None
    loaded: bool = False


def get_custom_provider(provider_id: str) -> dict | None:
    """Get a custom provider config by ID from settings."""
    settings = load_settings()
    for p in settings.get("custom_providers", []):
        if p.get("id") == provider_id:
            return p
    return None


def get_all_custom_providers() -> list[dict]:
    """Get all custom provider configs from settings."""
    settings = load_settings()
    return settings.get("custom_providers", [])


def _get_provider_api_key(provider: dict) -> str | None:
    """Get API key for a custom provider from keychain."""
    key_ref = provider.get("api_key_ref")
    if not key_ref:
        return None
    from laya.security.keychain import get_api_key

    return get_api_key(key_ref)


async def discover_models(provider: dict) -> list[DiscoveredModel]:
    """Discover models from a custom provider based on its type."""
    ptype = provider.get("provider_type", "openai_compatible")
    try:
        if ptype == "lmstudio":
            return await _discover_lmstudio(provider)
        elif ptype == "ollama":
            return await _discover_ollama(provider)
        else:
            return await _discover_openai_compatible(provider)
    except Exception as e:
        log.warning("model_discovery_failed", provider=provider.get("id"), error=str(e))
        return []


async def discover_models_cached(provider: dict) -> list[DiscoveredModel]:
    """Discover models with caching."""
    pid = provider.get("id", "")
    now = time.time()
    if pid in _discovery_cache:
        cached_time, cached_models = _discovery_cache[pid]
        if now - cached_time < _DISCOVERY_CACHE_TTL:
            return cached_models
    models = await discover_models(provider)
    _discovery_cache[pid] = (now, models)
    return models


def invalidate_discovery_cache(provider_id: str) -> None:
    """Clear discovery cache for a provider."""
    _discovery_cache.pop(provider_id, None)


async def test_provider_connectivity(provider: dict) -> dict[str, Any]:
    """Test a custom provider's connectivity and capabilities.

    Returns dict with: reachable, models_count, llm_count, embedding_count,
    inference_ok, latency_ms, error.
    """
    result: dict[str, Any] = {
        "reachable": False,
        "models_count": 0,
        "llm_count": 0,
        "embedding_count": 0,
        "inference_ok": False,
        "latency_ms": 0,
        "error": None,
    }

    start = time.monotonic()
    try:
        models = await discover_models(provider)
        result["reachable"] = True
        result["models_count"] = len(models)
        result["llm_count"] = sum(1 for m in models if m.model_type == "llm")
        result["embedding_count"] = sum(1 for m in models if m.model_type == "embedding")
    except Exception as e:
        result["error"] = str(e)
        result["latency_ms"] = int((time.monotonic() - start) * 1000)
        return result

    # Try a tiny inference if we found LLM models
    llm_models = [m for m in models if m.model_type == "llm" and m.loaded]
    if not llm_models:
        llm_models = [m for m in models if m.model_type == "llm"]

    if llm_models:
        test_model = llm_models[0]
        try:
            ptype = provider.get("provider_type", "openai_compatible")
            base_url = provider["base_url"].rstrip("/")
            model_name = test_model.key.split("/", 1)[1]

            if ptype == "ollama":
                api_url = f"{base_url}/api/chat"
                payload = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": "Say hi"}],
                    "stream": False,
                    "options": {"num_predict": 5},
                }
            else:
                api_url = f"{base_url}/v1/chat/completions"
                payload = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": "Say hi"}],
                    "max_tokens": 5,
                    "stream": False,
                }

            headers: dict[str, str] = {"Content-Type": "application/json"}
            api_key = _get_provider_api_key(provider)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            resp = await get_client().post(api_url, json=payload, headers=headers, timeout=30.0)
            result["inference_ok"] = resp.status_code == 200
        except Exception as e:
            result["inference_ok"] = False
            log.debug("inference_test_failed", error=str(e))

    result["latency_ms"] = int((time.monotonic() - start) * 1000)
    return result


# ---------------------------------------------------------------------------
# Provider-specific discovery
# ---------------------------------------------------------------------------


async def _discover_lmstudio(provider: dict) -> list[DiscoveredModel]:
    """Use LMStudio native API for rich model discovery."""
    base_url = provider["base_url"].rstrip("/")
    headers: dict[str, str] = {}
    api_key = _get_provider_api_key(provider)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = await get_client().get(f"{base_url}/api/v1/models", headers=headers, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()

    models = []
    for m in data.get("models", []):
        caps = m.get("capabilities", {})
        quant = m.get("quantization", {})

        models.append(
            DiscoveredModel(
                key=f"{provider['id']}/{m['key']}",
                display_name=m.get("display_name", m["key"]),
                model_type=m.get("type", "llm"),
                provider_id=provider["id"],
                max_context_length=m.get("max_context_length"),
                supports_tool_calling=caps.get("trained_for_tool_use", False),
                supports_structured_output=True,  # LMStudio enforces via grammar
                supports_vision=caps.get("vision", False),
                # capabilities.reasoning is an object ({allowed_options, default}) for
                # thinking models, absent otherwise — coerce to a bool.
                supports_reasoning=bool(caps.get("reasoning")),
                params_string=m.get("params_string"),
                quantization=quant.get("name") if quant else None,
                loaded=len(m.get("loaded_instances", [])) > 0,
            )
        )

    log.info("lmstudio_models_discovered", provider=provider["id"], count=len(models))
    return models


async def _discover_ollama(provider: dict) -> list[DiscoveredModel]:
    """Use Ollama API for model discovery."""
    base_url = provider["base_url"].rstrip("/")

    resp = await get_client().get(f"{base_url}/api/tags", timeout=10.0)
    resp.raise_for_status()
    data = resp.json()

    caps_override = provider.get("capabilities_override", {})
    models = []
    for m in data.get("models", []):
        name = m.get("name", "")
        display = name.split(":")[0] if ":" in name else name

        details = m.get("details", {})
        params = details.get("parameter_size")

        models.append(
            DiscoveredModel(
                key=f"{provider['id']}/{name}",
                display_name=display,
                model_type="llm",
                provider_id=provider["id"],
                max_context_length=None,
                supports_tool_calling=caps_override.get("supports_tool_calling", False),
                supports_structured_output=caps_override.get("supports_structured_output", False),
                supports_vision=False,
                params_string=params,
                quantization=details.get("quantization_level"),
                loaded=True,  # Ollama only lists pulled models
            )
        )

    log.info("ollama_models_discovered", provider=provider["id"], count=len(models))
    return models


async def _discover_openai_compatible(provider: dict) -> list[DiscoveredModel]:
    """Use standard OpenAI /v1/models endpoint."""
    base_url = provider["base_url"].rstrip("/")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = _get_provider_api_key(provider)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = await get_client().get(f"{base_url}/v1/models", headers=headers, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()

    caps_override = provider.get("capabilities_override", {})
    models = []
    for m in data.get("data", []):
        model_id = m.get("id", "")
        models.append(
            DiscoveredModel(
                key=f"{provider['id']}/{model_id}",
                display_name=model_id,
                model_type="llm",
                provider_id=provider["id"],
                # vLLM advertises its context window here; most other OpenAI-compat
                # servers omit it (→ None, no clamp). Used to bound max_tokens so a
                # large request doesn't 400 against `prompt + max_tokens > max_model_len`.
                max_context_length=m.get("max_model_len"),
                supports_tool_calling=caps_override.get("supports_tool_calling", True),
                supports_structured_output=caps_override.get("supports_structured_output", True),
                supports_vision=False,
                loaded=True,
            )
        )

    log.info("openai_compat_models_discovered", provider=provider["id"], count=len(models))
    return models
