"""GET/PUT /settings and API key management endpoints."""

import asyncio
import time

import httpx
import structlog
from fastapi import APIRouter, Query
from pydantic import BaseModel

from laya.config import get_n8n_config, load_repos, load_settings, save_repos, save_settings
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
    return {
        "setup_complete": settings.get("setup_complete", False),
        "has_api_key": has_api_key("anthropic") or has_api_key("openai") or has_api_key("google") or has_api_key("openrouter"),
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
    save_settings(current)
    log.info("settings_updated")
    return {"status": "updated"}


class ApiKeyRequest(BaseModel):
    provider: str
    api_key: str


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

    return {"providers": result}


@router.post("/settings/n8n/test")
async def test_n8n_connection(body: dict | None = None) -> dict:
    """Test n8n connectivity. Optionally test a specific webhook path."""
    n8n_config = get_n8n_config()
    base_url = (body.get("base_url") if body else None) or n8n_config["base_url"]
    base_url = base_url.rstrip("/")

    result: dict = {"base_url": base_url, "health": "unknown", "webhook": None}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/healthz")
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
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{base_url}/webhook-test/{webhook_path}",
                    json={"test": True},
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
