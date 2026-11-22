# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""GET/PUT /team — Team configuration endpoints."""

import structlog
from fastapi import APIRouter

from laya.config import (
    GITHUB_FILTER_IDENTITY_ERROR,
    get_self_user,
    github_action_filter_enabled,
    github_logins,
    load_team,
    save_team,
)
from laya.models.team import TeamConfig

log = structlog.get_logger()
router = APIRouter()


def _github_filter_warning() -> dict[str, str]:
    if github_action_filter_enabled("github") and not github_logins(get_self_user()):
        return {"github_filter_warning": GITHUB_FILTER_IDENTITY_ERROR}
    return {}


@router.get("/team")
async def get_team() -> dict:
    """Return current team.json contents."""
    return {**load_team(), **_github_filter_warning()}


@router.put("/team")
async def update_team(team: TeamConfig) -> dict:
    """Replace team.json with the provided configuration."""
    data = team.model_dump(mode="json")
    save_team(data)
    log.info("team_updated", member_count=len(team.members))
    return {"status": "updated", "member_count": len(team.members), **_github_filter_warning()}
