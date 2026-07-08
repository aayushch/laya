# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Golden-prompt tests for the platform-gated stager system prompt (review §3 — P6-9).

Pins that every event still gets the universal directives while the conditional
blocks (email links/phishing, issue_* update rules, PR lifecycle) ship only for
their platform family — so the token cut can't silently drop an operative rule.
"""

from laya.llm.prompts.stager import build_stager_system_prompt

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
