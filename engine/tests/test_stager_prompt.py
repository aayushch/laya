# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Golden-prompt tests for the platform-gated stager system prompt (review §3 — P6-9).

Pins that every event still gets the universal directives while the conditional
blocks (email links/phishing, issue_* update rules, PR lifecycle) ship only for
their platform family — so the token cut can't silently drop an operative rule.
"""

from datetime import datetime, timezone

from laya.llm.prompts import overrides
from laya.llm.prompts.stager import (
    CARD_STATUS_RULES,
    WorkItemTimeline,
    build_stager_messages,
    build_stager_system_prompt,
    build_timeline_block,
    get_stager_json_schema,
)
from laya.models.card import LLM_ASSIGNABLE_STATUSES, STATUS_EFFECTS
from laya.models.card_lifecycle import VALID_STATUS_TRANSITIONS
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent

# Universal directives that must survive on EVERY platform.
UNIVERSAL = [
    "You are the Stager for Laya",
    "privacy_tier",
    "Do NOT emit identifier fields",   # payload rule
    "relationship: self",              # actor/user framing
    "Participant Roles",
    "## Context Matching",
    "Never use emoji",
]

# Distinctive anchors for each conditional block.
EMAIL_ANCHOR = "## Actionable Links (open_url)"
PHISHING_ANCHOR = "phishing or social engineering"
ISSUE_ANCHOR = "**Status changes** (issue_status_changed"
PR_ANCHOR = "### PR lifecycle (Bitbucket / GitHub)"


class TestUniversalAlwaysPresent:
    def test_universal_directives_on_every_platform(self):
        for platform in ["gmail", "outlook", "jira", "linear", "github",
                          "bitbucket", "slack", "google_calendar", "", None]:
            prompt = build_stager_system_prompt(platform)
            for anchor in UNIVERSAL:
                assert anchor in prompt, f"{anchor!r} missing for {platform!r}"


class TestPlatformGating:
    def test_email_platform_has_only_email_block(self):
        for p in ("gmail", "outlook"):
            prompt = build_stager_system_prompt(p)
            assert EMAIL_ANCHOR in prompt and PHISHING_ANCHOR in prompt
            assert ISSUE_ANCHOR not in prompt
            assert PR_ANCHOR not in prompt

    def test_issue_platform_has_only_issue_block(self):
        for p in ("jira", "linear"):
            prompt = build_stager_system_prompt(p)
            assert ISSUE_ANCHOR in prompt
            assert EMAIL_ANCHOR not in prompt
            assert PR_ANCHOR not in prompt

    def test_code_platform_has_only_pr_block(self):
        for p in ("github", "bitbucket"):
            prompt = build_stager_system_prompt(p)
            assert PR_ANCHOR in prompt
            assert EMAIL_ANCHOR not in prompt
            assert ISSUE_ANCHOR not in prompt

    def test_chat_and_unknown_platforms_get_no_conditional_blocks(self):
        for p in ("slack", "google_calendar", "", None):
            prompt = build_stager_system_prompt(p)
            assert EMAIL_ANCHOR not in prompt
            assert ISSUE_ANCHOR not in prompt
            assert PR_ANCHOR not in prompt


class TestTokenReduction:
    def test_non_email_prompts_are_smaller_than_email(self):
        gmail = len(build_stager_system_prompt("gmail"))
        for p in ("jira", "github", "slack"):
            assert len(build_stager_system_prompt(p)) < gmail

    def test_slack_drops_all_three_blocks(self):
        gmail = len(build_stager_system_prompt("gmail"))
        slack = len(build_stager_system_prompt("slack"))
        # slack loses email+issue+pr; gmail keeps email — slack must be well under.
        assert slack < gmail - 1000


# ---------------------------------------------------------------------------
# Card status rules, timeline block and schema
# ---------------------------------------------------------------------------

RULES_ANCHOR = "## Card Status"


def _event(platform: str) -> LayaEvent:
    return LayaEvent(
        event_id="evt_1",
        timestamp=datetime(2026, 2, 22, 14, 30, 0, tzinfo=timezone.utc),
        source={"platform": platform, "raw_event_type": "something_happened"},
        actor={"name": "Sarah Chen", "email": "sarah@company.com"},
        subject={"type": "ticket", "id": "X-1", "title": "A title"},
        content={"body": "Body", "attachments": [], "metadata": {}},
    )


def _router_output() -> RouterOutput:
    return RouterOutput(
        category="CODE", persona="ENGINEER", priority="HIGH", confidence=0.9,
        entities=[], requires_research=False, research_plan=[],
    )


def _timeline(n: int = 2, **kwargs) -> WorkItemTimeline:
    cards = [
        {
            "card_id": f"card_{i}",
            "header": f"Header {i}",
            "summary": f"Summary {i}",
            "status": "ready" if i % 2 else "done",
            "created_at": f"2026-02-22 1{i}:00:00",
            "source_raw_event_type": "pr_updated",
            "actor_name": "Sarah Chen",
        }
        for i in range(1, n + 1)
    ]
    return WorkItemTimeline(cards=cards, event_time="2026-02-22 14:30:00", **kwargs)


class TestCardStatusRules:
    def test_rules_are_sent_for_every_platform(self):
        """The rules name kinds of work item, never a platform, so the same text
        goes out for every platform, including one Laya has no adapter for."""
        for platform in ("gmail", "jira", "github", "slack", "notion", "calendar", "brand_new"):
            messages = build_stager_messages(_event(platform), _router_output())
            assert messages[0]["content"].endswith(CARD_STATUS_RULES), platform

    def test_rules_name_no_platform(self):
        lowered = CARD_STATUS_RULES.lower()
        for name in ("github", "bitbucket", "jira", "linear", "gmail", "outlook", "slack", "notion"):
            assert name not in lowered

    def test_rules_are_left_out_when_the_feature_is_off(self):
        messages = build_stager_messages(_event("jira"), _router_output(), card_status=False)
        assert RULES_ANCHOR not in messages[0]["content"]

    def test_rules_survive_a_stager_prompt_override(self, monkeypatch):
        """A user file that replaces the stager prompt must not remove the rules."""
        monkeypatch.setitem(overrides._overrides, "stager", "My own stager prompt.")
        messages = build_stager_messages(_event("jira"), _router_output())
        assert messages[0]["content"].startswith("My own stager prompt.")
        assert messages[0]["content"].endswith(CARD_STATUS_RULES)

    def test_rules_have_their_own_override_key(self, monkeypatch):
        assert overrides.PROMPT_FILES["stager_card_status"] == "stager_card_status.md"
        monkeypatch.setitem(overrides._overrides, "stager_card_status", "My status rules.")
        messages = build_stager_messages(_event("jira"), _router_output())
        assert messages[0]["content"].endswith("My status rules.")
        assert RULES_ANCHOR not in messages[0]["content"]

    def test_rules_name_every_value_the_stager_may_return(self):
        for value in (*STATUS_EFFECTS, *LLM_ASSIGNABLE_STATUSES):
            assert f'"{value}"' in CARD_STATUS_RULES, value

    def test_rules_name_every_status_laya_has(self):
        """The model is told the full list so it does not invent a status. A
        status added to the lifecycle later must be added to the rules too."""
        for status in VALID_STATUS_TRANSITIONS:
            assert status in CARD_STATUS_RULES, status


class TestCardStatusSchema:
    def test_card_status_is_the_last_required_property(self):
        schema = get_stager_json_schema()["schema"]
        assert list(schema["properties"])[-1] == "card_status"
        assert schema["required"][-1] == "card_status"

    def test_enums_match_the_model_constants(self):
        props = get_stager_json_schema()["schema"]["properties"]["card_status"]["properties"]
        assert props["event_effect"]["enum"] == list(STATUS_EFFECTS)
        assert props["status"]["enum"] == list(LLM_ASSIGNABLE_STATUSES)

    def test_reason_comes_first(self):
        card_status = get_stager_json_schema()["schema"]["properties"]["card_status"]
        assert list(card_status["properties"])[0] == "reason"
        assert card_status["additionalProperties"] is False

    def test_schema_without_card_status(self):
        schema = get_stager_json_schema(card_status=False)["schema"]
        assert "card_status" not in schema["properties"]
        assert "card_status" not in schema["required"]

    def test_schema_is_built_fresh_on_every_call(self):
        """get_stager_json_schema adds to the base schema, so the base must not
        be a shared object that grows with each call."""
        get_stager_json_schema()
        assert get_stager_json_schema()["schema"]["required"].count("card_status") == 1


class TestTimelineBlock:
    def test_no_block_without_earlier_cards(self):
        assert build_timeline_block(None) == ""
        assert build_timeline_block(WorkItemTimeline()) == ""
        messages = build_stager_messages(_event("jira"), _router_output(), None, None, WorkItemTimeline())
        assert "[WORK ITEM TIMELINE]" not in messages[1]["content"]

    def test_block_lines(self):
        block = build_timeline_block(_timeline(2, prior_state="concluded"))
        lines = block.split("\n")
        assert lines[0] == "[WORK ITEM TIMELINE]"
        assert lines[1].startswith("This work item already has 2 earlier card(s), oldest first.")
        assert lines[2] == "#1 | 2026-02-22 11:00 | pr_updated | Sarah Chen | ready | Header 1"
        # The summary is shown only under a ready card.
        assert lines[3] == "    Summary 1"
        assert lines[4] == "#2 | 2026-02-22 12:00 | pr_updated | Sarah Chen | done | Header 2"
        assert lines[5] == "This event's time: 2026-02-22 14:30 (UTC)."
        assert lines[6] == "Before this event the work item was: concluded."
        assert lines[7] == "[END WORK ITEM TIMELINE]"

    def test_block_never_shows_a_card_id(self):
        assert "card_" not in build_timeline_block(_timeline(3))

    def test_omitted_cards_are_counted(self):
        block = build_timeline_block(_timeline(2, omitted=5))
        assert "already has 7 earlier card(s). The 5 oldest are not shown." in block

    def test_long_summary_is_cut(self):
        timeline = _timeline(1)
        timeline.cards[0]["summary"] = "word " * 100
        summary_line = build_timeline_block(timeline).split("\n")[3]
        assert len(summary_line.strip()) == 160
        assert summary_line.endswith("…")

    def test_block_is_in_the_user_message(self):
        messages = build_stager_messages(_event("jira"), _router_output(), None, None, _timeline(2))
        assert build_timeline_block(_timeline(2)) in messages[1]["content"]
