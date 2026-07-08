# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Golden-contract tests for the intent-gated chat toolset (review §3 — P6-8).

These pin the token-reduction behavior so cuts can't silently regress: read +
card-write tools always ship; the heavy settings/rules/egress groups ship only
when the turn signals that intent; and an ordinary turn drops ~5.6K tokens.
"""

import json

from laya.egress.tools import get_egress_tool_definitions
from laya.llm.tools.definitions import (
    _read_tools,
    _rules_read_tools,
    _rules_write_tools,
    _settings_read_tools,
    _settings_write_tools,
    _write_tools,
    get_all_tool_definitions,
    select_chat_tools,
    select_tool_definitions,
)


def _names(defs):
    return {d["function"]["name"] for d in defs}


ALWAYS = _names(_read_tools()) | _names(_write_tools())
RULES = _names(_rules_read_tools()) | _names(_rules_write_tools())
SETTINGS = _names(_settings_read_tools()) | _names(_settings_write_tools())
EGRESS = _names(get_egress_tool_definitions())


class TestAlwaysPresent:
    def test_read_and_write_present_for_every_intent(self):
        for msg in ["what are my cards", "create a rule to archive spam",
                    "change my model setting", "reply to Sarah on slack", ""]:
            got = _names(select_tool_definitions(msg))
            assert ALWAYS <= got, f"read+write missing for {msg!r}"


class TestGatingSavesTokens:
    def test_plain_turn_ships_only_read_write(self):
        got = _names(select_tool_definitions("what happened with the payment cards today?"))
        assert got == ALWAYS
        assert not (got & RULES) and not (got & SETTINGS) and not (got & EGRESS)

    def test_plain_turn_is_much_smaller_than_full(self):
        plain = len(json.dumps(select_tool_definitions("summarize my day")))
        full = len(json.dumps(get_all_tool_definitions()))
        # ~2.8K vs ~8.4K tokens — the gated turn should be < 40% of the full set.
        assert plain < full * 0.4


class TestIntentInclusion:
    def test_rules_intent_includes_rules_group(self):
        got = _names(select_tool_definitions("create a rule to auto-archive newsletters"))
        assert RULES <= got

    def test_settings_intent_includes_settings_group(self):
        got = _names(select_tool_definitions("configure the stager model for this space"))
        assert SETTINGS <= got

    def test_egress_intent_includes_egress_group(self):
        got = _names(select_tool_definitions("reply to this thread and send it to slack"))
        assert EGRESS <= got

    def test_rules_intent_does_not_drag_in_egress(self):
        got = _names(select_tool_definitions("list my classification rules"))
        assert RULES <= got
        assert not (got & EGRESS)


class TestMultiTurnPersistence:
    def test_pending_rule_flow_keeps_rules_on_confirm(self):
        # Turn 1 established rule intent; the "yes" follow-up has no keyword but
        # the recent user turn does, so the rules group must still ship.
        history = [
            {"role": "user", "content": "make a rule that archives GitHub bot noise"},
            {"role": "assistant", "content": "Which field should it match?"},
        ]
        got = _names(select_chat_tools("yes, do it", history))
        assert RULES <= got

    def test_assistant_mention_alone_does_not_open_group(self):
        # Only user turns count — the assistant saying "rule" must not keep the
        # heavy rules group alive on an otherwise-plain user turn.
        history = [
            {"role": "user", "content": "what are my cards"},
            {"role": "assistant", "content": "You could set up a rule for that."},
        ]
        got = _names(select_chat_tools("show me today's summary", history))
        assert not (got & RULES)
