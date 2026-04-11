"""Research prompt template for building agent task descriptions from card context."""

from __future__ import annotations


def build_research_prompt(
    header: str,
    summary: str,
    intelligence: list[str],
    event_body: str,
    platform: str,
    user_question: str | None = None,
) -> str:
    """Build a research-oriented prompt from card context.

    Unlike the engineer worker prompt (which requires an LLM call to build
    a coding task), this directly composes a research prompt string from
    the card's existing data. The configured agent (Claude Code, Gemini CLI,
    etc.) executes the actual research using its available tools.
    """
    intel_section = ""
    if intelligence:
        bullets = "\n".join(f"- {point}" for point in intelligence)
        intel_section = f"\n\n## Key Points\n{bullets}"

    task = user_question or (
        "Analyze this topic thoroughly. Identify key facts, implications, "
        "and actionable insights. Summarize your findings clearly."
    )

    return f"""\
You are a research assistant. Your task is to investigate the following topic thoroughly.

## Context
Source platform: {platform}
Subject: {header}

## Summary
{summary}

## Original Content
{event_body}{intel_section}

## Research Task
{task}

## Instructions
- Use WebSearch and other available tools to gather current information
- Synthesize findings into a clear, structured analysis
- Highlight key takeaways and recommended actions
- If you find conflicting information, note the discrepancies
- Provide sources and references where possible
- Write your findings as a structured document in the current working directory
- IMPORTANT: Only write files inside the current working directory — do not modify files outside of it
"""
