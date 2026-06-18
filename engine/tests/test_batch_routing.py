# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for batch-routing guards: circuit-breaker + local-provider skip.

Batch routing balloons the router prompt to ~10x; on a local reasoning model that
overflows the loaded context window and the call truncates/errors. These guards stop
a large backlog drain from re-paying that doomed cost every poll cycle.
"""

from unittest.mock import patch

import laya.pipeline.queue as queue_mod
from laya.pipeline.queue import (
    _batch_routing_allowed,
    _router_is_local_provider,
    _trip_batch_breaker,
)


def _reset_breaker():
    queue_mod._batch_routing_disabled_until = 0.0


def test_breaker_starts_closed():
    _reset_breaker()
    assert _batch_routing_allowed() is True


def test_trip_disables_batch_routing():
    _reset_breaker()
    _trip_batch_breaker("partial_batch_result", got=3, expected=10)
    assert _batch_routing_allowed() is False
    _reset_breaker()


def test_trip_uses_configured_cooldown():
    """Tripping pushes the deadline out by the cooldown window."""
    _reset_breaker()
    with patch("laya.pipeline.queue.time.monotonic", return_value=1000.0):
        _trip_batch_breaker("batch_route_error", error="boom")
    assert queue_mod._batch_routing_disabled_until == 1000.0 + queue_mod._BATCH_BREAKER_COOLDOWN
    # Still tripped just before the deadline, re-enabled at/after it.
    with patch("laya.pipeline.queue.time.monotonic", return_value=1000.0 + queue_mod._BATCH_BREAKER_COOLDOWN - 1):
        assert _batch_routing_allowed() is False
    with patch("laya.pipeline.queue.time.monotonic", return_value=1000.0 + queue_mod._BATCH_BREAKER_COOLDOWN):
        assert _batch_routing_allowed() is True
    _reset_breaker()


def test_local_provider_detected():
    """A custom/local router model is detected so batch routing is skipped."""
    with patch("laya.llm.client._get_model_for_role", return_value="lmstudio-local/qwen3.5-9b"):
        with patch("laya.llm.client._resolve_custom_provider", return_value=("openai/qwen3.5-9b", {})):
            assert _router_is_local_provider() is True


def test_cloud_provider_not_local():
    """A cloud router model resolves to no custom provider, so batching stays on."""
    with patch("laya.llm.client._get_model_for_role", return_value="anthropic/claude-haiku-4-5"):
        with patch("laya.llm.client._resolve_custom_provider", return_value=None):
            assert _router_is_local_provider() is False
