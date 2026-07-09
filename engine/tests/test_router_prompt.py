# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Golden-prompt tests for the platform-gated router system prompt (review §3 — P6-9)."""

from laya.llm.prompts.router import build_router_system_prompt

UNIVERSAL = [
    "You are the Router for Laya",
    "Persona selection:",
    "Priority guidelines:",
    "Actor relationship context (from team.json):",
    "GENERATE a research_plan ONLY",   # P6-10 directive is universal
]
JIRA_ANCHOR = "Jira lifecycle event guidelines:"
PR_ANCHOR = "Bitbucket PR lifecycle event guidelines:"


class TestUniversal:
    def test_universal_present_everywhere(self):
        for p in ["jira", "github", "gmail", "slack", "google_calendar", "", None]:
            prompt = build_router_system_prompt(p)
            for a in UNIVERSAL:
                assert a in prompt, f"{a!r} missing for {p!r}"


class TestGating:
    def test_issue_platform_gets_jira_only(self):
        p = build_router_system_prompt("jira")
        assert JIRA_ANCHOR in p and PR_ANCHOR not in p

    def test_code_platform_gets_pr_only(self):
        p = build_router_system_prompt("github")
        assert PR_ANCHOR in p and JIRA_ANCHOR not in p

    def test_email_chat_get_neither(self):
        for plat in ("gmail", "slack", "", None):
            p = build_router_system_prompt(plat)
            assert JIRA_ANCHOR not in p and PR_ANCHOR not in p

    def test_batch_union_includes_both(self):
        # A batch mixing a Jira and a GitHub event must carry both blocks.
        p = build_router_system_prompt(["jira", "github", "gmail"])
        assert JIRA_ANCHOR in p and PR_ANCHOR in p

    def test_batch_all_gmail_gets_neither(self):
        p = build_router_system_prompt(["gmail", "outlook", "slack"])
        assert JIRA_ANCHOR not in p and PR_ANCHOR not in p


class TestReduction:
    def test_gmail_smaller_than_jira(self):
        assert len(build_router_system_prompt("gmail")) < len(build_router_system_prompt("jira"))
