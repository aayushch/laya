# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for action execution (Engine -> n8n forwarding)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class N8nTarget(BaseModel):
    """Target platform details for n8n."""

    platform: str
    connection_id: str | None = None


class N8nActionPayload(BaseModel):
    """Payload sent to n8n webhook for action execution."""

    action_id: str
    source_event_id: str
    target: N8nTarget
    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecuteActionRequest(BaseModel):
    """Request body for POST /actions/execute."""

    card_id: str
    action_id: str
    modifications: dict[str, Any] | None = None


class ExecuteActionResponse(BaseModel):
    """Response body for POST /actions/execute."""

    card_id: str
    action_id: str
    status: str  # "executing", "completed", "failed"
    result_url: str | None = None
    error: str | None = None
