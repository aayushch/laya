"""Pydantic models for Spaces and Sources."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SpaceCreate(BaseModel):
    """Request body for creating a space."""

    name: str = Field(min_length=1, max_length=50)
    description: str | None = None
    icon: str = "📁"
    color: str = "#F97316"
    router_model: str | None = None
    stager_model: str | None = None
    chat_model: str | None = None
    coding_agent: str | None = None


class SpaceUpdate(BaseModel):
    """Request body for updating a space."""

    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    router_model: str | None = None
    stager_model: str | None = None
    chat_model: str | None = None
    coding_agent: str | None = None


class SpaceResponse(BaseModel):
    """Full space for API responses."""

    space_id: str
    name: str
    description: str | None = None
    icon: str = "📁"
    color: str = "#F97316"
    router_model: str | None = None
    stager_model: str | None = None
    chat_model: str | None = None
    coding_agent: str | None = None
    is_default: bool = False
    paused: bool = False
    position: int = 0
    source_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class SourceCreate(BaseModel):
    """Request body for creating a source."""

    name: str = Field(min_length=1, max_length=100)
    platform: str
    workflow_id: str
    space_id: str = "default"
    source_type: str = "ingestion"  # "ingestion" or "executor"
    webhook_path: str | None = None  # Required for executor sources (e.g. "gmail-executor")


class SourceResponse(BaseModel):
    """Full source for API responses."""

    source_id: str
    name: str
    platform: str
    workflow_id: str
    space_id: str
    space_name: str | None = None
    source_type: str = "ingestion"
    webhook_path: str | None = None
    created_at: str | None = None


class SourceAssignment(BaseModel):
    """Request body for reassigning a source to a different space."""

    space_id: str


class SpaceReposRequest(BaseModel):
    """Request body for setting repositories assigned to a space."""

    repo_names: list[str] = Field(default_factory=list)


class SpaceApiKeyRequest(BaseModel):
    """Request body for saving a space-specific API key."""

    provider: str
    api_key: str
