# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for processing rules — automated event→action wiring."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


# --- Extended operators (superset of filter rule operators) ---

class ProcessingRuleOperator(str, Enum):
    equals = "equals"
    not_equals = "not_equals"
    contains = "contains"
    not_contains = "not_contains"
    starts_with = "starts_with"
    ends_with = "ends_with"
    in_ = "in"
    not_in = "not_in"
    matches = "matches"
    gt = "gt"
    gte = "gte"
    lt = "lt"
    lte = "lte"
    exists = "exists"
    not_exists = "not_exists"


# --- Conditions ---

class ProcessingSimpleCondition(BaseModel):
    field: str
    operator: ProcessingRuleOperator
    value: str | list[str] | float | bool | None = None


class ProcessingAllCondition(BaseModel):
    all: list[ProcessingCondition]


class ProcessingAnyCondition(BaseModel):
    any: list[ProcessingCondition]


class ProcessingNotCondition(BaseModel):
    not_: ProcessingCondition = Field(alias="not")

    model_config = {"populate_by_name": True}


ProcessingCondition = Annotated[
    Union[ProcessingSimpleCondition, ProcessingAllCondition, ProcessingAnyCondition, ProcessingNotCondition],
    Field(discriminator=None),
]

ProcessingAllCondition.model_rebuild()
ProcessingAnyCondition.model_rebuild()
ProcessingNotCondition.model_rebuild()


# --- Actions ---

class SetStatusAction(BaseModel):
    type: Literal["set_status"] = "set_status"
    status: Literal["dismissed", "archived", "done"]
    reason: str | None = None


class SetPriorityAction(BaseModel):
    type: Literal["set_priority"] = "set_priority"
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class BookmarkAction(BaseModel):
    type: Literal["bookmark"] = "bookmark"


class RunEntityAgentAction(BaseModel):
    type: Literal["run_entity_agent"] = "run_entity_agent"
    prompt_template: str | None = None


class ExecuteEgressAction(BaseModel):
    type: Literal["execute_egress"] = "execute_egress"
    platform: str
    action_type: str
    payload_template: dict[str, str] = Field(default_factory=dict)
    connection_id: str | None = None


class SendNotificationAction(BaseModel):
    type: Literal["send_notification"] = "send_notification"
    title_template: str
    body_template: str


class AddTagAction(BaseModel):
    type: Literal["add_tag"] = "add_tag"
    tag_name: str
    create_if_missing: bool = True


ProcessingRuleAction = Annotated[
    Union[
        SetStatusAction,
        SetPriorityAction,
        BookmarkAction,
        RunEntityAgentAction,
        ExecuteEgressAction,
        SendNotificationAction,
        AddTagAction,
    ],
    Field(discriminator="type"),
]


# --- Rule model ---

class ProcessingRule(BaseModel):
    id: int | None = None
    name: str
    description: str | None = None
    space_id: str | None = None
    enabled: bool = True
    position: int = 0
    condition: ProcessingCondition
    actions: list[ProcessingRuleAction]
    rate_limit: int = 0
    cooldown_secs: int = 0
    max_daily: int = 0
    last_fired_at: str | None = None
    fire_count: int = 0
    error_count: int = 0
    last_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CreateProcessingRuleRequest(BaseModel):
    name: str
    description: str | None = None
    space_id: str | None = None
    enabled: bool = True
    condition: ProcessingCondition
    actions: list[ProcessingRuleAction]
    rate_limit: int = 0
    cooldown_secs: int = 0
    max_daily: int = 0


class UpdateProcessingRuleRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    space_id: str | None = None
    enabled: bool | None = None
    condition: ProcessingCondition | None = None
    actions: list[ProcessingRuleAction] | None = None
    rate_limit: int | None = None
    cooldown_secs: int | None = None
    max_daily: int | None = None
