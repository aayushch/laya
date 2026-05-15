"""GET/PUT /settings and API key management endpoints."""

import asyncio
import time

import httpx
import structlog
from fastapi import APIRouter, Query
from pydantic import BaseModel

from laya.config import get_n8n_config, load_repos, load_settings, save_repos, save_settings, get_all_custom_providers
from laya.http_client import get_client
from laya.integrations.n8n_bootstrap import ensure_n8n_ready
from laya.security.keychain import delete_api_key, get_api_key, has_api_key, store_api_key

log = structlog.get_logger()
router = APIRouter()

# ---------------------------------------------------------------------------
# Model list cache: provider -> (timestamp, models)
# ---------------------------------------------------------------------------
_model_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
_MODEL_CACHE_TTL = 600  # 10 minutes

# Mapping from our provider names to LiteLLM provider names
_PROVIDER_TO_LITELLM = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "gemini",
    "openrouter": "openrouter",
}

# Display names for providers
_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "openrouter": "OpenRouter",
}

# Non-chat model patterns to filter out
_EXCLUDE_PATTERNS = (
    "dall-e", "tts", "whisper", "embedding", "moderation",
    "image-generation", "audio", "gpt-image", "babbage", "davinci",
    "1024-x-", "1536-x-", "256-x-",
)


def _invalidate_model_cache(provider: str) -> None:
    """Remove cached models for a provider."""
    _model_cache.pop(provider, None)


def _generate_label(model_id: str) -> str:
    """Generate a human-readable label from a model ID."""
    # For openrouter models, strip the openrouter/ prefix for display
    display = model_id
    if display.startswith("openrouter/"):
        display = display[len("openrouter/"):]
    # For gemini/ prefixed models in the static list, keep as-is
    return display


def _is_chat_model(model_id: str) -> bool:
    """Filter out non-chat models (image gen, TTS, embeddings, etc.)."""
    lower = model_id.lower()
    return not any(pat in lower for pat in _EXCLUDE_PATTERNS)


def _fetch_models_for_provider(provider: str) -> list[dict[str, str]]:
    """Fetch available models for a provider. Runs synchronously (call from executor)."""
    import litellm

    litellm_provider = _PROVIDER_TO_LITELLM.get(provider)
    if not litellm_provider:
        return []

    api_key = get_api_key(provider)
    if not api_key:
        return []

    models: list[str] = []

    # Try dynamic fetch first
    try:
        models = litellm.get_valid_models(
            custom_llm_provider=litellm_provider,
            api_key=api_key,
            check_provider_endpoint=True,
        )
        log.info("models_fetched_dynamic", provider=provider, count=len(models))
    except Exception as e:
        log.warning("models_dynamic_fetch_failed", provider=provider, error=str(e))

    # Fall back to static list if dynamic fetch returned nothing
    if not models:
        static = litellm.models_by_provider.get(litellm_provider, set())
        models = list(static)
        log.info("models_fetched_static", provider=provider, count=len(models))

    # Filter to chat models and sort
    models = sorted([m for m in models if _is_chat_model(m)])

    return [{"id": m, "name": _generate_label(m)} for m in models]


@router.get("/settings/setup-status")
async def get_setup_status() -> dict:
    """Check whether first-run setup has been completed."""
    settings = load_settings()
    has_key = has_api_key("anthropic") or has_api_key("openai") or has_api_key("google") or has_api_key("openrouter")
    has_custom = len(settings.get("custom_providers", [])) > 0
    return {
        "setup_complete": settings.get("setup_complete", False),
        "has_api_key": has_key or has_custom,
    }


@router.get("/settings")
async def get_settings() -> dict:
    """Return current settings with API key presence indicators."""
    settings = load_settings()
    settings["api_keys"] = {
        "anthropic": has_api_key("anthropic"),
        "openai": has_api_key("openai"),
        "google": has_api_key("google"),
        "openrouter": has_api_key("openrouter"),
        "n8n": has_api_key("n8n"),
    }
    return settings


@router.put("/settings")
async def update_settings(body: dict) -> dict:
    """Update settings with deep merge."""
    current = load_settings()
    for key, value in body.items():
        if isinstance(value, dict) and key in current and isinstance(current[key], dict):
            current[key] = {**current[key], **value}
        else:
            current[key] = value

    # Clamp omni.event_threshold to [0, 100]. 0 disables the trigger entirely
    # (for users who only want scheduled/rolling). Above 100 would let card
    # volume between runs grow unbounded — past ~100 the LLM's ability to
    # aggregate usefully degrades, so we cap it.
    omni_cfg = current.get("omni")
    if isinstance(omni_cfg, dict) and "event_threshold" in omni_cfg:
        try:
            et = int(omni_cfg["event_threshold"])
        except (TypeError, ValueError):
            et = 50
        omni_cfg["event_threshold"] = max(0, min(100, et))

    save_settings(current)
    log.info("settings_updated")
    return {"status": "updated"}


class ApiKeyRequest(BaseModel):
    provider: str
    api_key: str


class CustomProviderCreate(BaseModel):
    name: str
    base_url: str
    provider_type: str = "openai_compatible"  # lmstudio | ollama | openai_compatible
    api_key: str | None = None
    default_timeout: int = 120
    capabilities_override: dict | None = None


class CustomProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    provider_type: str | None = None
    api_key: str | None = None
    default_timeout: int | None = None
    capabilities_override: dict | None = None


@router.put("/settings/api-key")
async def set_api_key(req: ApiKeyRequest) -> dict:
    """Store an API key in the OS keychain."""
    success = store_api_key(req.provider, req.api_key)
    if success:
        _invalidate_model_cache(req.provider)
    return {"status": "stored" if success else "failed", "provider": req.provider}


@router.delete("/settings/api-key/{provider}")
async def remove_api_key(provider: str) -> dict:
    """Remove an API key from the OS keychain."""
    success = delete_api_key(provider)
    if success:
        _invalidate_model_cache(provider)
    return {"status": "deleted" if success else "failed", "provider": provider}


@router.get("/settings/available-models")
async def get_available_models(refresh: bool = Query(default=False)) -> dict:
    """Return available models grouped by provider.

    Checks which providers have API keys configured, then fetches
    available models via LiteLLM. Results are cached for 10 minutes.
    Pass ?refresh=true to bust the cache.
    """
    providers_to_query = [
        p for p in _PROVIDER_TO_LITELLM if has_api_key(p)
    ]

    now = time.time()
    result: list[dict] = []

    for provider in providers_to_query:
        # Check cache
        if not refresh and provider in _model_cache:
            cached_time, cached_models = _model_cache[provider]
            if now - cached_time < _MODEL_CACHE_TTL:
                result.append({
                    "provider": provider,
                    "label": _PROVIDER_LABELS.get(provider, provider),
                    "models": cached_models,
                })
                continue

        # Fetch in executor (litellm.get_valid_models is sync)
        try:
            models = await asyncio.get_event_loop().run_in_executor(
                None, _fetch_models_for_provider, provider
            )
            _model_cache[provider] = (now, models)
            result.append({
                "provider": provider,
                "label": _PROVIDER_LABELS.get(provider, provider),
                "models": models,
            })
        except Exception as e:
            log.error("available_models_failed", provider=provider, error=str(e))
            # Return stale cache if available
            if provider in _model_cache:
                _, cached_models = _model_cache[provider]
                result.append({
                    "provider": provider,
                    "label": _PROVIDER_LABELS.get(provider, provider),
                    "models": cached_models,
                })

    # Also include models from custom providers
    for custom_provider in get_all_custom_providers():
        try:
            from laya.llm.providers import discover_models_cached

            custom_models = await discover_models_cached(custom_provider)
            llm_models = [m for m in custom_models if m.model_type == "llm"]
            if llm_models:
                result.append({
                    "provider": custom_provider["id"],
                    "label": custom_provider["name"],
                    "models": [
                        {"id": m.key, "name": m.display_name}
                        for m in llm_models
                    ],
                })
        except Exception as e:
            log.warning(
                "custom_provider_model_fetch_failed",
                provider=custom_provider.get("id"),
                error=str(e),
            )

    return {"providers": result}


# ---------------------------------------------------------------------------
# Custom providers (self-hosted models)
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Generate a URL-safe slug from a provider name."""
    import re

    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "custom"


@router.get("/settings/custom-providers")
async def list_custom_providers() -> dict:
    """List all configured custom providers."""
    providers = get_all_custom_providers()
    return {"providers": providers}


@router.post("/settings/custom-providers")
async def add_custom_provider(body: CustomProviderCreate) -> dict:
    """Add a custom model provider (LMStudio, Ollama, etc.)."""
    settings = load_settings()
    providers = settings.get("custom_providers", [])

    # Generate unique ID
    base_id = _slugify(body.name)
    provider_id = base_id
    existing_ids = {p["id"] for p in providers}
    counter = 2
    while provider_id in existing_ids:
        provider_id = f"{base_id}-{counter}"
        counter += 1

    provider: dict = {
        "id": provider_id,
        "name": body.name,
        "base_url": body.base_url.rstrip("/"),
        "provider_type": body.provider_type,
        "default_timeout": body.default_timeout,
    }

    if body.capabilities_override:
        provider["capabilities_override"] = body.capabilities_override

    # Store API key in keychain if provided
    if body.api_key:
        key_ref = f"custom_{provider_id}"
        store_api_key(key_ref, body.api_key)
        provider["api_key_ref"] = key_ref

    providers.append(provider)
    settings["custom_providers"] = providers
    save_settings(settings)

    log.info("custom_provider_added", provider_id=provider_id, type=body.provider_type)
    return {"status": "created", "provider": provider}


@router.put("/settings/custom-providers/{provider_id}")
async def update_custom_provider(provider_id: str, body: CustomProviderUpdate) -> dict:
    """Update a custom model provider."""
    settings = load_settings()
    providers = settings.get("custom_providers", [])

    target = None
    for p in providers:
        if p["id"] == provider_id:
            target = p
            break

    if not target:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    if body.name is not None:
        target["name"] = body.name
    if body.base_url is not None:
        target["base_url"] = body.base_url.rstrip("/")
    if body.provider_type is not None:
        target["provider_type"] = body.provider_type
    if body.default_timeout is not None:
        target["default_timeout"] = body.default_timeout
    if body.capabilities_override is not None:
        target["capabilities_override"] = body.capabilities_override

    if body.api_key is not None:
        key_ref = f"custom_{provider_id}"
        store_api_key(key_ref, body.api_key)
        target["api_key_ref"] = key_ref

    settings["custom_providers"] = providers
    save_settings(settings)

    from laya.llm.providers import invalidate_discovery_cache

    invalidate_discovery_cache(provider_id)

    log.info("custom_provider_updated", provider_id=provider_id)
    return {"status": "updated", "provider": target}


@router.delete("/settings/custom-providers/{provider_id}")
async def delete_custom_provider(provider_id: str) -> dict:
    """Remove a custom model provider."""
    settings = load_settings()
    providers = settings.get("custom_providers", [])

    original_len = len(providers)
    providers = [p for p in providers if p["id"] != provider_id]

    if len(providers) == original_len:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    # Clean up keychain entry
    key_ref = f"custom_{provider_id}"
    delete_api_key(key_ref)

    settings["custom_providers"] = providers
    save_settings(settings)

    from laya.llm.providers import invalidate_discovery_cache

    invalidate_discovery_cache(provider_id)

    log.info("custom_provider_deleted", provider_id=provider_id)
    return {"status": "deleted", "provider_id": provider_id}


@router.post("/settings/custom-providers/{provider_id}/test")
async def test_custom_provider(provider_id: str) -> dict:
    """Test connectivity and inference for a custom provider."""
    from laya.llm.providers import get_custom_provider, test_provider_connectivity

    provider = get_custom_provider(provider_id)
    if not provider:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    result = await test_provider_connectivity(provider)
    return {"provider_id": provider_id, **result}


@router.get("/settings/custom-providers/{provider_id}/models")
async def list_provider_models(provider_id: str) -> dict:
    """List models from a specific custom provider with capability metadata."""
    from laya.llm.providers import get_custom_provider, discover_models_cached

    provider = get_custom_provider(provider_id)
    if not provider:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    models = await discover_models_cached(provider)
    return {
        "provider_id": provider_id,
        "provider_name": provider["name"],
        "models": [
            {
                "id": m.key,
                "name": m.display_name,
                "type": m.model_type,
                "max_context_length": m.max_context_length,
                "supports_tool_calling": m.supports_tool_calling,
                "supports_structured_output": m.supports_structured_output,
                "supports_vision": m.supports_vision,
                "params": m.params_string,
                "quantization": m.quantization,
                "loaded": m.loaded,
            }
            for m in models
        ],
    }


@router.post("/settings/n8n/test")
async def test_n8n_connection(body: dict | None = None) -> dict:
    """Test n8n connectivity. Optionally test a specific webhook path."""
    n8n_config = get_n8n_config()
    base_url = (body.get("base_url") if body else None) or n8n_config["base_url"]
    base_url = base_url.rstrip("/")

    result: dict = {"base_url": base_url, "health": "unknown", "webhook": None}

    try:
        resp = await get_client().get(f"{base_url}/healthz", timeout=5.0)
        result["health"] = "healthy" if resp.status_code == 200 else "unhealthy"
    except httpx.ConnectError:
        result["health"] = "unreachable"
    except httpx.TimeoutException:
        result["health"] = "timeout"
    except Exception as e:
        result["health"] = f"error: {str(e)}"

    webhook_path = body.get("webhook_path") if body else None
    if webhook_path and result["health"] == "healthy":
        try:
            resp = await get_client().post(
                f"{base_url}/webhook-test/{webhook_path}",
                json={"test": True},
                timeout=5.0,
            )
            result["webhook"] = {
                    "path": webhook_path,
                    "status_code": resp.status_code,
                    "reachable": resp.status_code < 500,
                }
        except Exception as e:
            result["webhook"] = {"path": webhook_path, "reachable": False, "error": str(e)}

    return result


@router.post("/settings/n8n/bootstrap")
async def bootstrap_n8n() -> dict:
    """Trigger n8n auto-provisioning (owner account + API key)."""
    result = await ensure_n8n_ready()
    return result


@router.get("/settings/detect-agents")
async def detect_agents() -> dict:
    """Auto-detect installed agent CLI binaries and return their paths."""
    from laya.config import detect_agent_paths

    paths = detect_agent_paths()
    return {"agent_paths": paths}


# ---------------------------------------------------------------------------
# Custom prompt overrides
# ---------------------------------------------------------------------------


@router.get("/prompts")
async def get_prompts() -> dict:
    """Return override status for all prompt keys."""
    from laya.llm.prompts.overrides import get_override_status

    return {"prompts": get_override_status()}


@router.post("/prompts/reload")
async def reload_prompts() -> dict:
    """Re-scan ~/.laya/prompts/ and hot-swap overrides."""
    from laya.llm.prompts.overrides import load_custom_prompts

    loaded = load_custom_prompts()
    return {"status": "reloaded", "overridden_keys": sorted(loaded.keys())}


@router.get("/repos")
async def get_repos(platform: str | None = Query(default=None)) -> dict:
    """Return configured repositories, optionally filtered by platform."""
    data = load_repos()
    if platform:
        data = dict(data)
        data["repos"] = [r for r in data.get("repos", []) if r.get("platform") == platform]
    return data


@router.put("/repos")
async def update_repos(body: dict) -> dict:
    """Update configured repositories."""
    save_repos(body)
    log.info("repos_updated")
    return {"status": "updated"}
