# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for event filter rules."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class RuleOperator(str, Enum):
    equals = "equals"
    not_equals = "not_equals"
    contains = "contains"
    starts_with = "starts_with"
    ends_with = "ends_with"
    in_ = "in"


class SimpleCondition(BaseModel):
    field: str
    operator: RuleOperator
    value: str | list[str]


class AllCondition(BaseModel):
    all: list[RuleCondition]


class AnyCondition(BaseModel):
    any: list[RuleCondition]


RuleCondition = Annotated[
    Union[SimpleCondition, AllCondition, AnyCondition],
    Field(discriminator=None),
]

# Resolve forward references
AllCondition.model_rebuild()
AnyCondition.model_rebuild()


class Rule(BaseModel):
    name: str
    enabled: bool = True
    condition: RuleCondition
    action: Literal["drop", "allow"] = "drop"


class RulesConfig(BaseModel):
    rules: list[Rule] = Field(default_factory=list)
