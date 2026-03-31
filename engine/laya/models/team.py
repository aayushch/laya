"""Pydantic models for team configuration."""

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class TeamRole(str, Enum):
    self_ = "self"
    manager = "manager"
    teammate = "teammate"
    external = "external"
    bot = "bot"


class TeamMember(BaseModel):
    name: str
    email: str
    role: TeamRole
    notes: str = ""
    aliases: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)


class TeamConfig(BaseModel):
    members: list[TeamMember] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_most_one_self(self) -> "TeamConfig":
        count = sum(1 for m in self.members if m.role == TeamRole.self_)
        if count > 1:
            raise ValueError("Only one team member can have the 'self' role")
        return self
