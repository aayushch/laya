# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for the dashboard API response."""

from __future__ import annotations

from pydantic import BaseModel


class DashboardStats(BaseModel):
    events_processed: int = 0
    events_filtered: int = 0
    cards_generated: int = 0
    cards_pending: int = 0
    cards_approved: int = 0
    cards_dismissed: int = 0
    cards_edited: int = 0
    actions_executed: int = 0
    actions_completed: int = 0
    actions_failed: int = 0


class TimeSavedEstimate(BaseModel):
    total_minutes: float = 0.0
    by_action_type: dict[str, float] = {}


class LLMCostEstimate(BaseModel):
    total_cost_usd: float = 0.0
    by_model: dict[str, float] = {}
    by_feature: dict[str, float] = {}
    by_step: dict[str, float] = {}
    total_input_tokens: int = 0
    total_output_tokens: int = 0


class SourceBreakdown(BaseModel):
    source: str
    count: int


class PersonaApprovalRate(BaseModel):
    persona: str
    total: int
    approved: int
    dismissed: int
    rate: float


class ResponseTimeStats(BaseModel):
    avg_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0


class DashboardResponse(BaseModel):
    stats: DashboardStats
    time_saved: TimeSavedEstimate
    llm_costs: LLMCostEstimate
    events_by_source: list[SourceBreakdown] = []
    approval_by_persona: list[PersonaApprovalRate] = []
    response_time: ResponseTimeStats
    period_days: int


class ThroughputBucket(BaseModel):
    minute: str
    ingested: int = 0
    processed: int = 0
    failed: int = 0
    avg_wait_s: float = 0.0
    p95_wait_s: float = 0.0


class ThroughputResponse(BaseModel):
    buckets: list[ThroughputBucket]
    window_minutes: int
