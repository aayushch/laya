# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for Router classification output."""

from enum import Enum

from pydantic import BaseModel, Field


class Category(str, Enum):
    CODE = "CODE"
    COMMS = "COMMS"
    PEOPLE = "PEOPLE"
    FINANCE = "FINANCE"
    OPS = "OPS"


class Persona(str, Enum):
    ENGINEER = "ENGINEER"
    COMMS = "COMMS"
    OPS = "OPS"
    SALES = "SALES"
    HR = "HR"
    FINANCE = "FINANCE"


class Priority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ExtractedEntity(BaseModel):
    """An entity extracted from the event by the Router."""

    entity_type: str  # person, ticket, pull_request, file_path, repo, etc.
    value: str  # "BUG-1234", "/src/PaymentService.java", "@sarah"
    platform: str | None = None  # "jira", "bitbucket", etc.


class RouterOutput(BaseModel):
    """Full structured output from the Router LLM call."""

    category: Category
    persona: Persona
    priority: Priority
    confidence: float = Field(ge=0.0, le=1.0)

    entities: list[ExtractedEntity] = Field(default_factory=list)

    research_plan: list[str] = Field(
        default_factory=list, description="Ordered list of investigation steps"
    )
    requires_research: bool = False
    secondary_persona: Persona | None = None

    reasoning: str = ""
