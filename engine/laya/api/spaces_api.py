"""Spaces & Sources REST API — manage spaces, assign sources, configure per-space models."""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException

from laya.db.sqlite import get_db
from laya.integrations.n8n_client import (
    N8nApiError,
    N8nApiKeyMissing,
    activate_workflow,
    check_workflow_readiness,
    list_workflows,
)
from laya.config import load_repos
from laya.models.space import (
    SourceAssignment,
    SourceCreate,
    SourceResponse,
    SpaceApiKeyRequest,
    SpaceCreate,
    SpaceReposRequest,
    SpaceResponse,
    SpaceUpdate,
)
from laya.security.keychain import delete_space_api_key, get_space_api_key, store_space_api_key

log = structlog.get_logger()
router = APIRouter()


# ---------------------------------------------------------------------------
# Spaces CRUD
# ---------------------------------------------------------------------------


@router.get("/spaces")
async def list_spaces() -> dict:
    """List all spaces with source counts."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT s.*, COALESCE(src_count, 0) AS source_count
           FROM spaces s
           LEFT JOIN (
               SELECT space_id, COUNT(*) AS src_count FROM sources GROUP BY space_id
           ) sc ON s.space_id = sc.space_id
           ORDER BY s.position, s.created_at"""
    )
    spaces = []
    for r in rows:
        spaces.append(SpaceResponse(
            space_id=r["space_id"],
            name=r["name"],
            description=r["description"],
            icon=r["icon"],
            color=r["color"],
            router_model=r["router_model"],
            stager_model=r["stager_model"],
            chat_model=r["chat_model"],
            trace_model=r["trace_model"],
            omni_model=r["omni_model"],
            coding_agent=r["coding_agent"],
            is_default=bool(r["is_default"]),
            paused=bool(r["paused"]),
            position=r["position"],
            source_count=r["source_count"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        ))
    return {"spaces": spaces}


@router.post("/spaces")
async def create_space(body: SpaceCreate) -> SpaceResponse:
    """Create a new space."""
    db = await get_db()
    space_id = f"space_{uuid.uuid4().hex[:12]}"

    # Get next position
    pos_rows = await db.execute_fetchall("SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM spaces")
    position = pos_rows[0]["pos"]

    try:
        await db.execute(
            """INSERT INTO spaces (space_id, name, description, icon, color,
                                   router_model, stager_model, chat_model, trace_model,
                                   omni_model, coding_agent, position)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (space_id, body.name, body.description, body.icon, body.color,
             body.router_model, body.stager_model, body.chat_model, body.trace_model,
             body.omni_model, body.coding_agent, position),
        )
        await db.commit()
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail=f"Space name '{body.name}' already exists")
        raise

    log.info("space_created", space_id=space_id, name=body.name)
    return SpaceResponse(
        space_id=space_id,
        name=body.name,
        description=body.description,
        icon=body.icon,
        color=body.color,
        router_model=body.router_model,
        stager_model=body.stager_model,
        chat_model=body.chat_model,
        trace_model=body.trace_model,
        omni_model=body.omni_model,
        coding_agent=body.coding_agent,
        position=position,
        source_count=0,
    )


@router.put("/spaces/{space_id}")
async def update_space(space_id: str, body: SpaceUpdate) -> dict:
    """Update a space's properties."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT is_default FROM spaces WHERE space_id = ?", (space_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Space not found")

    updates: list[str] = []
    params: list = []
    for field in ("name", "description", "icon", "color", "router_model", "stager_model", "chat_model", "trace_model", "omni_model", "coding_agent"):
        value = getattr(body, field, None)
        if value is not None:
            updates.append(f"{field} = ?")
            params.append(value)

    if not updates:
        return {"status": "no_changes"}

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(space_id)

    try:
        await db.execute(
            f"UPDATE spaces SET {', '.join(updates)} WHERE space_id = ?",
            params,
        )
        await db.commit()
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail=f"Space name '{body.name}' already exists")
        raise

    log.info("space_updated", space_id=space_id)
    return {"status": "updated", "space_id": space_id}


@router.delete("/spaces/{space_id}")
async def delete_space(space_id: str) -> dict:
    """Delete a space. Sources and cards are moved to the Default space."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT is_default FROM spaces WHERE space_id = ?", (space_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Space not found")
    if rows[0]["is_default"]:
        raise HTTPException(status_code=400, detail="Cannot delete the Default space")

    # Move sources to default
    await db.execute(
        "UPDATE sources SET space_id = 'default' WHERE space_id = ?", (space_id,)
    )
    # Move cards to default
    await db.execute(
        "UPDATE action_cards SET space_id = 'default' WHERE space_id = ?", (space_id,)
    )
    # Move events to default
    await db.execute(
        "UPDATE events SET space_id = 'default' WHERE space_id = ?", (space_id,)
    )
    # Delete space API keys and repo assignments
    await db.execute("DELETE FROM space_api_keys WHERE space_id = ?", (space_id,))
    await db.execute("DELETE FROM space_repos WHERE space_id = ?", (space_id,))
    # Delete the space
    await db.execute("DELETE FROM spaces WHERE space_id = ?", (space_id,))
    await db.commit()

    log.info("space_deleted", space_id=space_id)
    return {"status": "deleted", "space_id": space_id}


# ---------------------------------------------------------------------------
# Pause / Unpause (flow control)
# ---------------------------------------------------------------------------


@router.put("/spaces/{space_id}/paused")
async def set_space_paused(space_id: str, body: dict) -> dict:
    """Pause or unpause a space by deactivating/activating all its source workflows.

    Body: {"paused": true/false}
    When pausing, deactivates all n8n ingestion workflows for the space.
    When unpausing, activates them (with readiness checks).
    """
    paused = body.get("paused")
    if paused is None:
        raise HTTPException(status_code=422, detail="'paused' field is required")

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT space_id FROM spaces WHERE space_id = ?", (space_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Space not found")

    # Get all ingestion source workflow IDs for this space
    source_rows = await db.execute_fetchall(
        """SELECT workflow_id, name FROM sources
           WHERE space_id = ? AND source_type = 'ingestion'""",
        (space_id,),
    )

    results = []
    errors = []
    for sr in source_rows:
        wf_id = sr["workflow_id"]
        try:
            if paused:
                await activate_workflow(wf_id, active=False)
            else:
                readiness = await check_workflow_readiness(wf_id)
                if not readiness["ready"]:
                    errors.append({
                        "workflow_id": wf_id,
                        "name": sr["name"],
                        "issues": readiness["issues"],
                    })
                    continue
                await activate_workflow(wf_id, active=True)
            results.append(wf_id)
        except (N8nApiError, N8nApiKeyMissing) as e:
            errors.append({
                "workflow_id": wf_id,
                "name": sr["name"],
                "error": str(e),
            })

    # Persist paused state
    await db.execute(
        "UPDATE spaces SET paused = ?, updated_at = CURRENT_TIMESTAMP WHERE space_id = ?",
        (1 if paused else 0, space_id),
    )
    await db.commit()

    log.info("space_paused_toggled", space_id=space_id, paused=paused,
             toggled=len(results), errors=len(errors))
    return {
        "status": "paused" if paused else "active",
        "space_id": space_id,
        "workflows_toggled": len(results),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Per-space API keys
# ---------------------------------------------------------------------------


@router.put("/spaces/{space_id}/api-key")
async def save_space_api_key(space_id: str, body: SpaceApiKeyRequest) -> dict:
    """Save a space-specific API key to the OS keychain."""
    db = await get_db()
    rows = await db.execute_fetchall("SELECT 1 FROM spaces WHERE space_id = ?", (space_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Space not found")

    key_ref = f"laya_{body.provider}_{space_id}"
    if not store_space_api_key(key_ref, body.api_key):
        raise HTTPException(status_code=500, detail="Failed to store API key in keychain")

    await db.execute(
        """INSERT OR REPLACE INTO space_api_keys (space_id, provider, key_ref)
           VALUES (?, ?, ?)""",
        (space_id, body.provider, key_ref),
    )
    await db.commit()

    log.info("space_api_key_saved", space_id=space_id, provider=body.provider)
    return {"status": "saved", "provider": body.provider}


@router.delete("/spaces/{space_id}/api-key/{provider}")
async def remove_space_api_key(space_id: str, provider: str) -> dict:
    """Remove a space-specific API key."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT key_ref FROM space_api_keys WHERE space_id = ? AND provider = ?",
        (space_id, provider),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No API key found for this space/provider")

    delete_space_api_key(rows[0]["key_ref"])
    await db.execute(
        "DELETE FROM space_api_keys WHERE space_id = ? AND provider = ?",
        (space_id, provider),
    )
    await db.commit()

    log.info("space_api_key_removed", space_id=space_id, provider=provider)
    return {"status": "removed", "provider": provider}


@router.get("/spaces/{space_id}/api-keys")
async def list_space_api_keys(space_id: str) -> dict:
    """List which providers have space-specific keys configured."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT provider, key_ref FROM space_api_keys WHERE space_id = ?", (space_id,)
    )
    providers = {}
    for r in rows:
        providers[r["provider"]] = {
            "configured": get_space_api_key(r["key_ref"]) is not None,
        }
    return {"providers": providers}


# ---------------------------------------------------------------------------
# Per-space repository assignments
# ---------------------------------------------------------------------------


@router.get("/spaces/{space_id}/repos")
async def list_space_repos(space_id: str) -> dict:
    """List repositories assigned to a space."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT repo_name, position FROM space_repos WHERE space_id = ? ORDER BY position",
        (space_id,),
    )
    # Enrich with full repo info from repos.json
    repos_data = load_repos()
    all_repos = {r["name"]: r for r in repos_data.get("repos", [])}
    result = []
    for r in rows:
        name = r["repo_name"]
        repo_info = all_repos.get(name)
        result.append({
            "repo_name": name,
            "position": r["position"],
            "path": repo_info["path"] if repo_info else None,
            "platform": repo_info.get("platform", "") if repo_info else "",
            "remote_id": repo_info.get("remote_id", "") if repo_info else "",
            "exists": repo_info is not None,
        })
    return {"repos": result}


@router.put("/spaces/{space_id}/repos")
async def set_space_repos(space_id: str, body: SpaceReposRequest) -> dict:
    """Set the repositories assigned to a space (replaces existing assignments)."""
    db = await get_db()
    rows = await db.execute_fetchall("SELECT 1 FROM spaces WHERE space_id = ?", (space_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Space not found")

    # Clear existing assignments
    await db.execute("DELETE FROM space_repos WHERE space_id = ?", (space_id,))

    # Insert new assignments
    for i, repo_name in enumerate(body.repo_names):
        await db.execute(
            "INSERT INTO space_repos (space_id, repo_name, position) VALUES (?, ?, ?)",
            (space_id, repo_name, i),
        )

    await db.commit()
    log.info("space_repos_updated", space_id=space_id, count=len(body.repo_names))
    return {"status": "updated", "count": len(body.repo_names)}


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@router.get("/sources")
async def list_sources() -> dict:
    """List all sources with space info."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT src.*, s.name AS space_name
           FROM sources src
           LEFT JOIN spaces s ON src.space_id = s.space_id
           ORDER BY src.created_at"""
    )
    sources = [
        SourceResponse(
            source_id=r["source_id"],
            name=r["name"],
            platform=r["platform"],
            workflow_id=r["workflow_id"],
            space_id=r["space_id"],
            space_name=r["space_name"],
            source_type=r["source_type"],
            webhook_path=r["webhook_path"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
    return {"sources": sources}


@router.get("/sources/available-workflows")
async def get_available_workflows() -> dict:
    """List n8n workflows that could be assigned as sources.

    Returns all Laya ingestion workflows from n8n, marking which ones
    are already registered as sources.
    """
    try:
        workflows = await list_workflows()
    except N8nApiKeyMissing as e:
        raise HTTPException(status_code=422, detail=str(e))
    except N8nApiError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    # Check which are already registered
    db = await get_db()
    source_rows = await db.execute_fetchall(
        "SELECT workflow_id, connection_id FROM sources"
    )
    registered_ids = {r["workflow_id"] for r in source_rows}
    wf_connection: dict[str, str | None] = {
        r["workflow_id"]: r["connection_id"] for r in source_rows
    }

    # Skip template workflows (only clones are user-facing)
    from laya.integrations.platforms import PLATFORMS
    template_names = set()
    for config in PLATFORMS.values():
        template_names.update(config.get("workflows", []))

    result = []
    for w in workflows:
        wf_id = str(w.get("id", ""))
        name = w.get("name", "")
        if name in template_names:
            continue

        platform = _parse_platform_from_name(name)
        source_type = _parse_source_type_from_name(name)
        result.append({
            "workflow_id": wf_id,
            "name": name,
            "platform": platform,
            "source_type": source_type,
            "active": w.get("active", False),
            "registered": wf_id in registered_ids,
            "connection_id": wf_connection.get(wf_id),
        })

    return {"workflows": result}


@router.put("/sources/workflows/{workflow_id}/active")
async def set_workflow_active(workflow_id: str, body: dict) -> dict:
    """Activate or deactivate an n8n workflow.

    Body: {"active": true/false}
    When activating, checks workflow readiness first (missing credentials, etc.).
    """
    active = body.get("active")
    if active is None:
        raise HTTPException(status_code=422, detail="'active' field is required")

    try:
        # Pre-check readiness before activation
        if active:
            readiness = await check_workflow_readiness(workflow_id)
            if not readiness["ready"]:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "Workflow is not ready to be activated",
                        "issues": readiness["issues"],
                    },
                )

        result = await activate_workflow(workflow_id, bool(active))
        log.info("workflow_toggled", workflow_id=workflow_id, active=active)
        return {"status": "ok", "workflow_id": workflow_id, "active": result.get("active", active)}
    except N8nApiKeyMissing as e:
        raise HTTPException(status_code=422, detail=str(e))
    except N8nApiError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except HTTPException:
        raise


@router.post("/sources")
async def create_source(body: SourceCreate) -> SourceResponse:
    """Register an n8n workflow as a source and assign it to a space."""
    db = await get_db()

    # Verify space exists
    space_rows = await db.execute_fetchall(
        "SELECT name FROM spaces WHERE space_id = ?", (body.space_id,)
    )
    if not space_rows:
        raise HTTPException(status_code=404, detail="Space not found")

    source_id = f"src_{uuid.uuid4().hex[:12]}"

    # Validate executor sources have a webhook_path
    if body.source_type == "executor" and not body.webhook_path:
        raise HTTPException(
            status_code=422,
            detail="Executor sources require a webhook_path (e.g. 'gmail-executor')",
        )

    try:
        await db.execute(
            """INSERT INTO sources (source_id, name, platform, workflow_id, space_id,
                                    source_type, webhook_path)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source_id, body.name, body.platform, body.workflow_id, body.space_id,
             body.source_type, body.webhook_path),
        )
        await db.commit()
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail="This workflow is already registered as a source")
        raise

    log.info("source_created", source_id=source_id, workflow_id=body.workflow_id,
             space_id=body.space_id, source_type=body.source_type)
    return SourceResponse(
        source_id=source_id,
        name=body.name,
        platform=body.platform,
        workflow_id=body.workflow_id,
        space_id=body.space_id,
        space_name=space_rows[0]["name"],
        source_type=body.source_type,
        webhook_path=body.webhook_path,
    )


@router.put("/sources/{source_id}/space")
async def reassign_source(source_id: str, body: SourceAssignment) -> dict:
    """Reassign a source to a different space."""
    db = await get_db()

    rows = await db.execute_fetchall("SELECT 1 FROM sources WHERE source_id = ?", (source_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Source not found")

    space_rows = await db.execute_fetchall("SELECT 1 FROM spaces WHERE space_id = ?", (body.space_id,))
    if not space_rows:
        raise HTTPException(status_code=404, detail="Space not found")

    await db.execute(
        "UPDATE sources SET space_id = ? WHERE source_id = ?",
        (body.space_id, source_id),
    )
    await db.commit()

    log.info("source_reassigned", source_id=source_id, space_id=body.space_id)
    return {"status": "reassigned", "source_id": source_id, "space_id": body.space_id}


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str) -> dict:
    """Unregister a source. Events/cards keep their space_id."""
    db = await get_db()
    rows = await db.execute_fetchall("SELECT 1 FROM sources WHERE source_id = ?", (source_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Source not found")

    await db.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
    await db.commit()

    log.info("source_deleted", source_id=source_id)
    return {"status": "deleted", "source_id": source_id}


# ---------------------------------------------------------------------------
# Bulk assignment
# ---------------------------------------------------------------------------


@router.put("/spaces/{space_id}/sources")
async def bulk_assign_sources(space_id: str, body: dict) -> dict:
    """Bulk assign multiple sources to a space.

    Body: {"source_ids": ["src_abc", "src_def"]}
    """
    db = await get_db()
    space_rows = await db.execute_fetchall("SELECT 1 FROM spaces WHERE space_id = ?", (space_id,))
    if not space_rows:
        raise HTTPException(status_code=404, detail="Space not found")

    source_ids = body.get("source_ids", [])
    if not source_ids:
        return {"status": "no_changes", "updated": 0}

    updated = 0
    for sid in source_ids:
        cursor = await db.execute(
            "UPDATE sources SET space_id = ? WHERE source_id = ?",
            (space_id, sid),
        )
        updated += cursor.rowcount

    await db.commit()
    log.info("sources_bulk_assigned", space_id=space_id, count=updated)
    return {"status": "updated", "updated": updated}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_platform_from_name(name: str) -> str:
    """Extract platform from workflow name like 'Laya - GitHub Ingestion'."""
    from laya.egress.registry import get_platform_keywords

    lower = name.lower()
    for keyword, platform in get_platform_keywords().items():
        if keyword in lower:
            return platform
    return "unknown"


def _parse_source_type_from_name(name: str) -> str:
    """Detect whether a workflow is an ingestion or executor type from its name."""
    lower = name.lower()
    if "executor" in lower:
        return "executor"
    return "ingestion"
