# Product Hunt Launch Package

## Tagline (60 chars max)

**Your AI command centre for Jira, Slack, Gmail, GitHub & more**

---

## Short Description (260 chars max)

Laya intercepts events from your dev tools, researches context with AI agents, and surfaces ready-to-approve Action Cards. By the time you open a notification, the answer is already staged. Local-first, open source, and it learns from your corrections.

---

## Full Description

### The Problem

You have unread notifications stacking up across all your tools. A Jira ticket, a Slack thread about the same bug, a Bitbucket PR with the fix, and a Gmail thread asking about the timeline. Same issue, four platforms, four different names for it.

You spend your morning just piecing the story together before you can make a single decision.

### What Laya Does

Laya sits between your tools and your attention. It intercepts events from Jira, Slack, Gmail, Bitbucket, GitHub, Calendar, Linear, Outlook, and Notion -- then uses AI to do the work you'd normally do manually:

**1. Classifies and prioritizes.** A fast LLM routes each event to a specialized persona (Engineer, Comms, Ops, Sales, HR, Finance) with the right priority.

**2. Researches context.** Laya links related items across platforms automatically. "BUG-1234" in Jira connects to "PR-891" in Bitbucket connects to "the payment bug" in Slack -- all without you lifting a finger.

**3. Stages actions.** By the time you see the notification, Laya has already drafted a reply, suggested a PR comment, or prepared a meeting brief. You just approve or dismiss.

**The key difference from other AI tools:** Most AI assistants and agents are prompt-based -- you have to ask them to summarize an email, draft a reply, or review a PR. Laya eliminates that step entirely. It intercepts events as they happen and automatically runs the intelligence pipeline, so the analysis and staged actions are ready before you even open the app. No prompts to write, no tools to invoke.

### Key Features

- **Action Cards** -- Not notifications. Ready-to-approve decisions with research already done
- **Agent Workspaces** -- Complex tasks (bug fixes, code reviews) open interactive sessions with Claude Code, Gemini CLI, or Codex
- **Omni Summary** -- Cross-platform view answering "where am I right now?" with four temporal layers
- **Daily Briefing** -- Morning summary of overnight activity, pending items, and today's calendar
- **Coherence Search** -- Search for any person, ticket, or PR and trace their activity across every platform with AI-generated narratives
- **Privacy-first** -- Runs entirely on your machine. SQLite + ChromaDB locally. API keys in your OS keychain. Optional Ollama support for fully local LLM processing
- **Classification Learning** -- Laya extracts rules from your corrections and improves automatically over time
- **Budget Tracking** -- Monitor LLM costs by feature with monthly caps

### How It Works

```
Your Tools --> n8n (local) --> Laya Engine (Python/FastAPI) --> Action Cards (Tauri + Svelte)
```

Three processes run on your machine:
1. **n8n** handles all external API connections (webhooks, polling, action execution)
2. **Laya Engine** does classification, research, staging, and memory
3. **Tauri desktop app** renders Action Cards, workspaces, dashboards, and settings

### Tech Stack

Built with Tauri v2 (Rust), Svelte 5, FastAPI, SQLite, ChromaDB, and LiteLLM. Supports Anthropic, OpenAI, Google, and Ollama models. Coding agents: Claude Code, Gemini CLI, or Codex CLI.

### Open Source

Laya is fully open source. Clone it, run `scripts/setup-dev.sh`, and you're up in minutes. Pre-built releases available for macOS, Windows, and Linux.

GitHub: https://github.com/aayushch/laya

---

## Topics/Categories for Product Hunt

1. Productivity
2. Artificial Intelligence
3. Developer Tools
4. Open Source
5. Task Management

---

## Maker's First Comment

Hey Product Hunt! I'm Aayush, the maker of Laya.

I built Laya because I was drowning. Not in work -- in notifications about work.

Every morning I'd open Slack to find a wall of unread messages. Then Jira with a stack of ticket updates. Gmail with threads about the same issues. Bitbucket with PRs related to those tickets. The worst part wasn't the volume -- it was the fragmentation. The same bug existed as a Jira ticket, a Slack thread (called something completely different), a PR on Bitbucket (named after the technical fix), and an email thread asking about the timeline.

I'd spent my morning just building context before I could make a single decision.

So I built Laya. The core idea is simple: by the time you look at a notification, the research should already be done. Laya intercepts events from your tools, links related items across platforms using semantic matching, routes them through specialized AI personas, and presents you with Action Cards -- approve or dismiss, one decision, back to work.

A few things I'm particularly proud of:

- **It's truly local-first.** Your data stays on your machine. SQLite for structured data, ChromaDB for vector search, OS keychain for API keys. The only external calls are to your chosen LLM provider.
- **It learns from you.** When you correct a classification (wrong priority, wrong persona), Laya extracts generalizable rules and applies them to future events automatically.
- **Context Association actually works.** "BUG-1234" in Jira gets automatically linked to the Slack thread where your teammate called it "the payment thing" and the PR titled "fix: null check in payment handler." Three layers: explicit cross-references, semantic similarity, and LLM verification.

It's open source and free. I'd love your feedback -- especially on what integrations and features would make this useful for your workflow.

GitHub: https://github.com/aayushch/laya

---

## Suggested Launch Day: Tuesday, Wednesday, or Thursday

Avoid Monday (competition from weekend builds) and Friday (lower traffic). Tuesday or Wednesday tend to perform best for developer tools.

## Pre-Launch Checklist

- [ ] Deploy landing page (you have the artifacts -- get them hosted on Vercel/Netlify/GitHub Pages)
- [ ] Record a 1-2 minute demo video (Loom or Screen Studio) showing: feed with Action Cards, approving an action, Omni summary, Coherence search
- [ ] Prepare 4-6 high-quality screenshots: Feed view, Card workspace, Omni summary, Settings/Spaces, Coherence search, Dashboard
- [ ] Gather 5-10 early users/supporters to upvote and comment on launch day
- [ ] Schedule social media posts (Twitter/X, LinkedIn, Reddit) to go live within 1 hour of PH launch
- [ ] Be available to respond to every comment on launch day (PH algorithm favors engagement)
