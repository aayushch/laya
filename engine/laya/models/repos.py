"""Pydantic models for repository configuration."""

from pydantic import BaseModel, Field


class RepoConfig(BaseModel):
    """A single configured local repository."""

    name: str
    path: str
    platform: str = ""  # bitbucket | github | gitlab | ""
    remote_id: str = ""  # e.g., "team/payments-service"


class ReposConfig(BaseModel):
    """repos.json structure."""

    repos: list[RepoConfig] = Field(default_factory=list)
