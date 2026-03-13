"""Pydantic models for the chat feature."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    message_id: str
    timestamp: str
    role: str  # "user" or "assistant"
    content: str
    referenced_cards: list[str] = Field(default_factory=list)
    referenced_events: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str
    space_id: str | None = None


class ChatResponse(BaseModel):
    message: ChatMessage
    referenced_cards: list[str] = Field(default_factory=list)
    referenced_events: list[str] = Field(default_factory=list)
