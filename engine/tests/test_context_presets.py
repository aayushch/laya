"""Tests for context association strictness presets."""

import pytest

from laya.pipeline.context_presets import (
    PRESETS,
    _entity_refs_overlap,
    get_strictness,
    resolve_context_config,
)


class TestResolveContextConfig:
    def test_strict_preset(self):
        config = resolve_context_config({"strictness": "strict"})
        assert config["confidence_threshold"] == 0.15
        assert config["auto_confirm_threshold"] is None
        assert config["centroid_threshold"] == 0.18
        assert config["cross_platform_required"] is True
        assert config["entity_ref_overlap_mode"] == "hard_gate"
        assert config["always_llm"] is True

    def test_balanced_preset(self):
        config = resolve_context_config({"strictness": "balanced"})
        assert config["confidence_threshold"] == 0.22
        assert config["auto_confirm_threshold"] == 0.10
        assert config["cross_platform_required"] is False
        assert config["entity_ref_overlap_mode"] == "soft_boost"
        assert config["always_llm"] is False

    def test_lenient_preset(self):
        config = resolve_context_config({"strictness": "lenient"})
        assert config["confidence_threshold"] == 0.35
        assert config["auto_confirm_threshold"] == 0.18
        assert config["centroid_threshold"] == 0.35
        assert config["cross_platform_required"] is False
        assert config["entity_ref_overlap_mode"] == "disabled"

    def test_custom_uses_raw_values(self):
        config = resolve_context_config({
            "strictness": "custom",
            "confidence_threshold": 0.30,
            "auto_confirm_threshold": 0.15,
            "centroid_threshold": 0.28,
            "cross_platform_required": True,
            "entity_ref_overlap_mode": "soft_boost",
            "always_llm": True,
        })
        assert config["confidence_threshold"] == 0.30
        assert config["auto_confirm_threshold"] == 0.15
        assert config["centroid_threshold"] == 0.28
        assert config["cross_platform_required"] is True
        assert config["entity_ref_overlap_mode"] == "soft_boost"
        assert config["always_llm"] is True

    def test_missing_strictness_defaults_to_strict(self):
        config = resolve_context_config({})
        assert config == PRESETS["strict"]

    def test_unknown_strictness_falls_back_to_custom(self):
        config = resolve_context_config({"strictness": "unknown_value"})
        assert config["confidence_threshold"] == 0.22  # custom defaults

    def test_preset_overrides_raw_values(self):
        """Named preset ignores raw threshold values in the same config."""
        config = resolve_context_config({
            "strictness": "strict",
            "confidence_threshold": 0.50,  # should be ignored
        })
        assert config["confidence_threshold"] == 0.15


class TestGetStrictness:
    def test_returns_configured_value(self):
        assert get_strictness({"strictness": "lenient"}) == "lenient"

    def test_defaults_to_strict(self):
        assert get_strictness({}) == "strict"


class TestEntityRefsOverlap:
    def test_exact_match(self):
        assert _entity_refs_overlap("PR-123,PaymentService", "PaymentService,BUG-456")

    def test_no_overlap(self):
        assert not _entity_refs_overlap("PR-123,PaymentService", "PR-456,HousekeepingModule")

    def test_case_insensitive(self):
        assert _entity_refs_overlap("PaymentService", "paymentservice")

    def test_substring_match_long_tokens(self):
        assert _entity_refs_overlap("PaymentService", "PaymentServiceCrash")

    def test_substring_match_reverse(self):
        assert _entity_refs_overlap("PaymentServiceCrash", "PaymentService")

    def test_short_tokens_ignored(self):
        """Tokens <= 2 chars are filtered out."""
        assert not _entity_refs_overlap("to,re", "to,fix")

    def test_empty_refs(self):
        assert not _entity_refs_overlap("", "PaymentService")
        assert not _entity_refs_overlap("PaymentService", "")
        assert not _entity_refs_overlap("", "")

    def test_short_tokens_no_substring(self):
        """Substring pass only runs on tokens > 5 chars."""
        assert not _entity_refs_overlap("error", "errors")  # 5 chars, not >5

    def test_substring_with_longer_tokens(self):
        """Tokens > 5 chars DO trigger substring matching."""
        assert _entity_refs_overlap("payment", "payment-service")  # 7 chars > 5

    def test_no_false_positive_from_generic_words(self):
        """Different specific identifiers should not match."""
        assert not _entity_refs_overlap("BUG-123,UserService", "BUG-456,OrderService")
