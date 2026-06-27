# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the CLI-agent inference backend (pure logic — no subprocess spawned).

Covers the model-id encoding, message→prompt mapping, JSON extraction + validation, and
— importantly — that every agent's argv carries the non-interactive / no-tool / no-approval
flags so a text-processing call can never block on a human or mutate anything.
"""

import pytest

from laya.llm import agent_backend as ab


# ── model-id encoding ────────────────────────────────────────────────────


def test_is_agent_model():
    assert ab.is_agent_model("agent/claude_code/claude-sonnet-4-6")
    assert ab.is_agent_model("agent/pi_cli")
    assert not ab.is_agent_model("anthropic/claude-haiku-4-5")
    assert not ab.is_agent_model("lmstudio-local/qwen")
    assert not ab.is_agent_model(None)


def test_parse_agent_model_id_with_model():
    assert ab.parse_agent_model_id("agent/claude_code/claude-sonnet-4-6") == (
        "claude_code", "claude-sonnet-4-6"
    )


def test_parse_agent_model_id_preserves_slashes_in_model():
    # A model slug with its own slashes must survive intact.
    assert ab.parse_agent_model_id("agent/pi_cli/lmstudio/qwen3.6-35b-a3b") == (
        "pi_cli", "lmstudio/qwen3.6-35b-a3b"
    )


def test_parse_agent_model_id_no_model_means_default():
    assert ab.parse_agent_model_id("agent/claude_code") == ("claude_code", None)
    # Trailing slash / blank model also means "agent default".
    assert ab.parse_agent_model_id("agent/claude_code/") == ("claude_code", None)


def test_parse_agent_model_id_rejects_non_agent():
    with pytest.raises(ValueError):
        ab.parse_agent_model_id("anthropic/claude-haiku-4-5")


# ── message → prompt mapping ─────────────────────────────────────────────


def test_split_messages_system_and_user():
    sys_text, prompt = ab._split_messages([
        {"role": "system", "content": "You classify events."},
        {"role": "user", "content": "PR #42 build failing."},
    ])
    assert sys_text == "You classify events."
    assert prompt == "PR #42 build failing."


def test_split_messages_multi_turn_labels_roles():
    _, prompt = ab._split_messages([
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ])
    assert "[user]" in prompt and "[assistant]" in prompt


def test_split_messages_flattens_content_blocks():
    sys_text, _ = ab._split_messages([
        {"role": "system", "content": [{"type": "text", "text": "cached sys"}]},
        {"role": "user", "content": "hi"},
    ])
    assert sys_text == "cached sys"


# ── JSON extraction + validation ─────────────────────────────────────────


def test_extract_json_plain():
    assert ab._extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_fences_and_think_and_prose():
    assert ab._extract_json('<think>reasoning</think>\n```json\n{"a": 1}\n```') == {"a": 1}
    assert ab._extract_json('Sure, here you go: {"a": 1} hope that helps') == {"a": 1}


def test_extract_json_invalid_returns_none():
    assert ab._extract_json("not json at all") is None
    assert ab._extract_json("") is None


def test_validate_against_schema_ok_and_missing():
    schema = {"schema": {"type": "object", "required": ["persona", "priority"]}}
    ok, _ = ab._validate_against_schema({"persona": "engineer", "priority": "high"}, schema)
    assert ok
    bad, err = ab._validate_against_schema({"persona": "engineer"}, schema)
    assert not bad and err


# ── capabilities ─────────────────────────────────────────────────────────


def test_capabilities_lists_all_agents_with_tiers():
    caps = {c["agent_id"]: c for c in ab.capabilities()}
    assert set(caps) == {"claude_code", "codex_cli", "gemini_cli", "pi_cli"}
    assert caps["claude_code"]["tier"] == "native"
    assert caps["pi_cli"]["tier"] == "best_effort"
    for c in caps.values():
        assert set(c) >= {"agent_id", "available", "path", "tier"}


# ── non-interactive / no-tool / no-approval flags (the safety contract) ──


def _args(agent_id, *, schema=None, native=False, model="m"):
    return ab._build_args(
        agent_id, agent_id, "sys", "prompt", model, schema, native
    )


def test_claude_args_are_noninteractive_and_toolless():
    schema = {"schema": {"type": "object"}}
    args, env = _args("claude_code", schema=schema, native=True)
    assert "-p" in args                                   # one-shot, exits
    assert "--disallowedTools" in args                    # no tools to approve
    # Must NOT use plan mode (it pollutes output with plan-and-wait framing).
    assert args[args.index("--permission-mode") + 1] == "default"
    assert "--mcp-config" not in args                     # no Laya tools / lower tax
    assert "--json-schema" in args                        # native structured output
    assert args[args.index("--model") + 1] == "m"


def test_pi_args_disable_tools_and_session():
    args, _ = _args("pi_cli")
    assert "-p" in args and "--no-tools" in args and "--no-session" in args


def test_gemini_args_readonly_and_trusts_workspace():
    args, env = _args("gemini_cli")
    assert args[args.index("--approval-mode") + 1] == "plan"   # read-only
    assert env.get("GEMINI_CLI_TRUST_WORKSPACE") == "true"     # skip trust prompt


def test_codex_args_exec_readonly_never_approve():
    args, _ = _args("codex_cli")
    assert "exec" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert args[args.index("--ask-for-approval") + 1] == "never"


def test_unknown_agent_rejected():
    with pytest.raises(ValueError):
        _args("bogus_cli")
