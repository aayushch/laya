# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

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
    conversation_id: str | None = None


class ChatRequest(BaseModel):
    message: str
    space_id: str | None = None
    conversation_id: str | None = None
    card_context: str | None = None  # Optional card context injected into system prompt (used by Omni card view)
    # When the chat is anchored to a specific set of cards (Omni → View Cards),
    # the client passes the card IDs so the conversation can be tagged and
    # re-opened when the user returns to the same view.
    card_ids: list[str] | None = None


class ChatResponse(BaseModel):
    message: ChatMessage
    referenced_cards: list[str] = Field(default_factory=list)
    referenced_events: list[str] = Field(default_factory=list)


class Conversation(BaseModel):
    conversation_id: str
    title: str
    space_id: str | None = None
    created_at: str
    updated_at: str
    preview: str = ""
    message_count: int = 0


class CreateConversationRequest(BaseModel):
    title: str = "New Chat"
    space_id: str | None = None
