# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Connections REST API — manage platform credentials via n8n."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.integrations.n8n_client import (
    N8nApiError,
    N8nApiKeyMissing,
    create_credential,
    delete_credential,
    list_credentials,
    test_api_access,
)
from laya.integrations.platforms import PLATFORMS, SUPPORTED_N8N_TYPES, get_platform_by_n8n_type

log = structlog.get_logger()
router = APIRouter()


class CreateConnectionRequest(BaseModel):
    platform: str
    name: str
    credentials: dict[str, str]


@router.get("/connections/platforms")
async def get_platforms() -> dict:
    """Return the platform registry with credential field schemas."""
    return {"platforms": PLATFORMS}


@router.get("/connections")
async def get_connections() -> dict:
    """List n8n credentials, filtered to Laya-supported types."""
    try:
        all_creds = await list_credentials()
    except N8nApiKeyMissing as e:
        raise HTTPException(status_code=422, detail=str(e))
    except N8nApiError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    connections = []
    for cred in all_creds:
        cred_type = cred.get("type", "")
        if cred_type not in SUPPORTED_N8N_TYPES:
            continue
        platform_info = get_platform_by_n8n_type(cred_type)
        platform_key = platform_info[0] if platform_info else None
        platform_label = platform_info[1]["label"] if platform_info else cred_type
        connections.append({
            "id": str(cred.get("id", "")),
            "name": cred.get("name", ""),
            "type": cred_type,
            "platform": platform_key,
            "platform_label": platform_label,
            "created_at": cred.get("createdAt", ""),
            "updated_at": cred.get("updatedAt", ""),
        })

    return {"connections": connections}


@router.post("/connections")
async def create_connection(body: CreateConnectionRequest) -> dict:
    """Create a platform credential in n8n."""
    if body.platform not in PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {body.platform}")

    platform = PLATFORMS[body.platform]

    if platform.get("oauth"):
        raise HTTPException(
            status_code=400,
            detail=f"{platform['label']} requires OAuth setup in n8n directly",
        )

    required_keys = {f["key"] for f in platform["fields"]}
    provided_keys = set(body.credentials.keys())
    missing = required_keys - provided_keys
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required fields: {', '.join(sorted(missing))}",
        )

    try:
        result = await create_credential(
            name=body.name,
            n8n_type=platform["n8n_type"],
            data=body.credentials,
            node_type=platform["n8n_node"],
        )
    except N8nApiKeyMissing as e:
        raise HTTPException(status_code=422, detail=str(e))
    except N8nApiError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    log.info("connection_created", platform=body.platform, name=body.name)
    return {
        "status": "created",
        "id": str(result.get("id", "")),
        "name": body.name,
        "platform": body.platform,
    }


@router.delete("/connections/{credential_id}")
async def remove_connection(credential_id: str) -> dict:
    """Delete a credential from n8n."""
    try:
        await delete_credential(credential_id)
    except N8nApiKeyMissing as e:
        raise HTTPException(status_code=422, detail=str(e))
    except N8nApiError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    log.info("connection_deleted", credential_id=credential_id)
    return {"status": "deleted", "id": credential_id}


@router.post("/connections/test")
async def test_connection() -> dict:
    """Test n8n API accessibility (requires n8n API key)."""
    return await test_api_access()
