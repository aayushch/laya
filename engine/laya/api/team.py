"""GET/PUT /team — Team configuration endpoints."""

import structlog
from fastapi import APIRouter

from laya.config import load_team, save_team
from laya.models.team import TeamConfig

log = structlog.get_logger()
router = APIRouter()


@router.get("/team")
async def get_team() -> dict:
    """Return current team.json contents."""
    return load_team()


@router.put("/team")
async def update_team(team: TeamConfig) -> dict:
    """Replace team.json with the provided configuration."""
    data = team.model_dump(mode="json")
    save_team(data)
    log.info("team_updated", member_count=len(team.members))
    return {"status": "updated", "member_count": len(team.members)}
