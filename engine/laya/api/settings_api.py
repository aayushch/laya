"""GET/PUT /settings and API key management endpoints."""

import httpx
import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from laya.config import get_n8n_config, load_repos, load_settings, save_repos, save_settings
from laya.integrations.n8n_bootstrap import ensure_n8n_ready
from laya.security.keychain import delete_api_key, has_api_key, store_api_key

log = structlog.get_logger()
router = APIRouter()


@router.get("/settings/setup-status")
async def get_setup_status() -> dict:
    """Check whether first-run setup has been completed."""
    settings = load_settings()
    return {
        "setup_complete": settings.get("setup_complete", False),
        "has_api_key": has_api_key("anthropic") or has_api_key("openai") or has_api_key("google"),
    }


@router.get("/settings")
async def get_settings() -> dict:
    """Return current settings with API key presence indicators."""
    settings = load_settings()
    settings["api_keys"] = {
        "anthropic": has_api_key("anthropic"),
        "openai": has_api_key("openai"),
        "google": has_api_key("google"),
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
    return {"status": "stored" if success else "failed", "provider": req.provider}


@router.delete("/settings/api-key/{provider}")
async def remove_api_key(provider: str) -> dict:
    """Remove an API key from the OS keychain."""
    success = delete_api_key(provider)
    return {"status": "deleted" if success else "failed", "provider": provider}


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
async def get_repos() -> dict:
    """Return configured repositories."""
    return load_repos()


@router.put("/repos")
async def update_repos(body: dict) -> dict:
    """Update configured repositories."""
    save_repos(body)
    log.info("repos_updated")
    return {"status": "updated"}
