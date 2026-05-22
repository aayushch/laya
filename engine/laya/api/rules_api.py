# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""GET/PUT /rules — Event filter rules endpoints."""

import structlog
from fastapi import APIRouter

from laya.config import load_rules, save_rules
from laya.models.rules import RulesConfig

log = structlog.get_logger()
router = APIRouter()


@router.get("/rules")
async def get_rules() -> dict:
    """Return current rules.json contents."""
    return load_rules()


@router.put("/rules")
async def update_rules(rules: RulesConfig) -> dict:
    """Replace rules.json with the provided configuration."""
    data = rules.model_dump(mode="json")
    save_rules(data)
    log.info("rules_updated", rule_count=len(rules.rules))
    return {"status": "updated", "rule_count": len(rules.rules)}
