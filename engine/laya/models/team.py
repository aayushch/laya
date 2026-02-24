"""Pydantic models for team configuration."""

from enum import Enum

from pydantic import BaseModel, Field


class TeamRole(str, Enum):
    manager = "manager"
    teammate = "teammate"
    external = "external"
    bot = "bot"


class TeamMember(BaseModel):
    name: str
    email: str
    role: TeamRole
    notes: str = ""


class TeamConfig(BaseModel):
    members: list[TeamMember] = Field(default_factory=list)
