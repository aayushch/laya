"""GET/PUT /settings and API key management endpoints."""

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from laya.config import load_repos, load_settings, save_repos, save_settings
from laya.security.keychain import delete_api_key, has_api_key, store_api_key

log = structlog.get_logger()
router = APIRouter()


@router.get("/settings")
async def get_settings() -> dict:
    """Return current settings with API key presence indicators."""
    settings = load_settings()
    settings["api_keys"] = {
        "anthropic": has_api_key("anthropic"),
        "openai": has_api_key("openai"),
        "google": has_api_key("google"),
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
    return {"status": "stored" if success else "failed", "provider": req.provider}


@router.delete("/settings/api-key/{provider}")
async def remove_api_key(provider: str) -> dict:
    """Remove an API key from the OS keychain."""
    success = delete_api_key(provider)
    return {"status": "deleted" if success else "failed", "provider": provider}


@router.get("/repos")
async def get_repos() -> dict:
    """Return configured repositories."""
    return load_repos()


@router.put("/repos")
async def update_repos(body: dict) -> dict:
    """Update configured repositories."""
    save_repos(body)
    log.info("repos_updated")
    return {"status": "updated"}
