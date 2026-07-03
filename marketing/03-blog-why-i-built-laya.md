# Why I Built Laya: An AI Command Centre for the Notification Hell We All Live In

*A blog post about building an open-source tool to fix cross-platform notification chaos.*

---

Every morning starts the same way.

I open Slack. Dozens of unread messages across multiple channels. A thread about a production bug. A DM asking about a timeline. A channel announcement I need to respond to.

Then Jira. A stack of ticket updates. Three of them are about the same bug from Slack, but the ticket title is "NPE in PaymentHandler.process()" -- a name nobody used in the Slack conversation where it was just "the payment thing."

Then Bitbucket. A PR titled "fix: add null check in payment processing pipeline." Same bug again, third name. I need to review this, but I need the context from the Jira ticket and the Slack thread to do it properly.

Then Gmail. An email from the product manager asking "what's the status of the payment issue?" -- referencing the same bug by yet another name.

Four platforms. One issue. Four names. And I haven't even started working yet.

## The Real Problem Isn't Volume -- It's Fragmentation

The problem with unified inboxes, notification aggregator, and "productivity" tool out there is that they solve the wrong problem. They assume the issue is having too many notifications in too many places, so they put them all in one place.

But that just gives you one place with too many notifications.

The real problem is threefold:

**1. Redundancy.** The same event triggers notifications on 3-4 platforms. A Jira ticket update pings Jira, Slack (via integration), email (via Jira notifications), and sometimes the linked PR on Bitbucket. You end up processing the same information four times.

**2. Context fragmentation.** The same issue, project, or decision is scattered across platforms, each with its own vocabulary. Humans naturally give things different names depending on context -- a colloquial shorthand in Slack, a formal title in Jira, a technical description in a PR. No existing tool connects these.

**3. Work-before-work.** Before you can make a decision, you need to reconstruct context. That means opening 3-4 tabs, reading threads, cross-referencing ticket IDs, and mentally building a picture of what happened. This is significant overhead every single day.

I needed a tool that didn't just collect notifications -- it needed to understand them, connect them, and do the reconstruction work for me. So I built one.

## What Laya Does

Laya is a desktop app that sits between your tools and your attention. It intercepts events from Jira, Slack, Gmail, GitHub, Bitbucket, Calendar, Linear, Outlook, and Notion -- then does three things before you ever see a notification:

### 1. Classify and prioritize

A fast LLM (the "router") classifies each event: What category is this? (Code, Comms, Ops, Finance, People) Which specialized persona should handle it? (Engineer, Comms, Ops, Sales, HR, Finance) How urgent is it? The router also generates a research plan -- what additional context would be needed to make a decision.

### 2. Connect across platforms

This is the part I'm most proud of. Laya builds cross-platform coherence using three layers:

- **Explicit links:** Deterministic cross-references from platform data. If a PR description mentions "BUG-1234", that's a direct link.
- **Semantic similarity:** ChromaDB vector search finds items that are about the same thing, even if they use different words. The Slack message about "the payment thing" has high semantic similarity to the Jira ticket "NPE in PaymentHandler."
- **LLM verification:** For borderline matches (similarity score between 0.20-0.30), an LLM confirms whether two items are actually related.

The result: "BUG-1234" in Jira, "the payment thing" in Slack, "fix: null check in payment handler" in Bitbucket, and "status of the payment issue" in Gmail all get linked automatically.

### 3. Stage actions

A stronger LLM (the "stager") synthesizes all the connected context and drafts what you'd probably do next: a reply to the email with a status update, a review comment on the PR, a Jira status change. These show up as Action Cards -- you approve, dismiss, or open a workspace for complex tasks.

By the time I open Laya in the morning, I don't have a flood of notifications. I have a manageable set of Action Cards, each with the research already done and actions already staged. A few clear decisions instead of a morning lost to context reconstruction.

## Why "Proactive" Is the Key Word

There's a distinction that matters here, and it's easy to miss because the AI space is so noisy right now.

Most AI tools -- even good ones -- are prompt-based. You open an AI email client and say "summarize this thread." You ask a coding assistant to "review this PR." You invoke an agent with "what's the status of this project?" The intelligence is real, but the first step is always yours: you identify what needs attention, you invoke the tool, you frame the request.

Laya eliminates that entire first step. Events flow in from your tools continuously. The classification, context linking, and action staging happen automatically, in the background, as events arrive. By the time you open the app, the work isn't waiting for your prompt -- it's already done.

This is the difference between a reactive assistant and a proactive system. You don't tell Laya "summarize the overnight Slack activity." It already has. You don't ask it to "find the PR related to this Jira ticket." It already linked them. The cognitive load of figuring out what to ask, and when to ask it, disappears.

## The Technical Choices That Matter

### Local-first, no exceptions

I made a deliberate choice: Laya runs entirely on your machine. SQLite for structured data. ChromaDB (embedded) for vector search. n8n as a local Node.js process for all external API connections. Your OS keychain for credentials.

The only external calls are to LLM APIs (using your own keys) and your tool APIs (through n8n). No Laya server. No telemetry. No accounts.

I built it this way because the data flowing through Laya is sensitive -- Slack DMs, email bodies, code diffs, internal discussions. Sending that to a third-party server is a non-starter for many teams, and it should be.

For users who need even more privacy, Laya supports Ollama for fully local LLM processing. And honestly, for the kind of work Laya actually does -- classification, context association, summarization, action staging -- the quality gap between frontier models and good local models is practically indistinguishable. These aren't tasks that demand frontier-level reasoning; they demand consistency and decent comprehension, which local models handle just fine. You get complete data isolation with little to no trade-off in quality.

### Learning from corrections

Static classifiers break. Your work changes, your team changes, your projects change. So Laya learns from you.

When you correct a classification -- wrong priority, wrong persona -- Laya records the correction. After enough corrections accumulate, an LLM extracts generalizable rules: "Events from the #incidents channel should always be HIGH priority," or "PRs from the payments-service repo should route to ENGINEER, not OPS."

These rules get injected into future classification prompts. The system gets better at your specific workflow over time.

### Agent workspaces for complex tasks

Not everything is a one-click approval. Bug fixes need investigation. Code reviews need context. So Laya has "workspaces" -- interactive sessions where you collaborate with a coding agent (Claude Code, Gemini CLI, or Codex CLI).

When the router determines a task needs research, the Action Card opens a workspace. The agent has access to your codebase, web search, semantic context from past events, and sandboxed file access. You see a timeline of its research steps and approve or redirect as it works.

Two-layer approval keeps humans in the loop: the agent proposes, you approve, the action executes.

## What I Learned Building This

### n8n was the right call

I debated building direct API integrations vs. using an integration platform. n8n won because:

1. **Credential management.** OAuth flows, token refresh, encrypted storage -- n8n handles all of this. Building it from scratch would have been months of work.
2. **Community workflows.** n8n has 400+ community-built integrations. Users can add platforms Laya doesn't explicitly support.
3. **Visual debugging.** When an integration breaks, you can open the n8n editor and see exactly which step failed. No digging through logs.

### The three-tier model approach works

Laya uses a tiered approach -- a fast model for classification and a stronger one for user-facing output. When I tested this with hosted models (Haiku across the stages), my daily LLM cost stayed under $1. But the more interesting finding was that I mostly didn't need hosted models at all. Running Qwen 3.5 9B locally across every stage works just fine -- which tells me a small model is sufficient to give Laya the brains it needs to do its thing. And Qwen 3.6 30B on a beefier machine works wonders. The takeaway: this isn't a problem that requires expensive frontier models, and for most people a local setup will be both cheaper and more private.

### Entity resolution is harder than classification

Getting the router to classify events correctly was straightforward -- LLMs are good at this. Getting the system to reliably link "the payment thing" in Slack to "BUG-1234" in Jira was much harder.

The three-layer approach (explicit + semantic + LLM) was necessary. Any single layer has too many false positives or false negatives. Together, they're surprisingly accurate -- and the learning system fills in the gaps over time.

## Built with Claude Code

There's a fitting symmetry to this project: Laya is a tool that uses coding agents to do work for you, and Laya itself was built almost entirely with one. The vast majority of this codebase -- the Python pipeline, the Svelte frontend, the Rust Tauri shell, the n8n workflows, the migrations, the tests -- was written with [Claude Code](https://claude.com/claude-code), Anthropic's agentic CLI.

It changed how I work. Instead of context-switching between docs, Stack Overflow, and three editor windows, I described what I wanted and iterated on real, working code. The cross-platform entity resolution that I'm most proud of? I designed the three-layer approach in conversation with Claude Code, watched it implement each layer, and refined it as I saw the results. The hardest parts of this project -- the ones I described above -- got built faster because I had an agent that could hold the whole architecture in context and make coherent changes across the engine, the UI, and the workflow definitions at once.

There's a nice recursion here. Once Laya was far enough along, parts of it were built using Laya itself -- Action Cards opening workspaces wired to Claude Code, the agent making changes to the very codebase that produced it. The tool started building the tool. Living the workflow I was trying to create made the design choices feel less abstract: every rough edge in the workspace experience was one I hit firsthand while shipping features.

## Try It

Laya is open source and free. It runs on macOS, Windows, and Linux.

**From source:**
```
git clone https://github.com/aayushch/laya
cd laya
scripts/setup-dev.sh
scripts/dev.sh
```

**Pre-built releases:** Check the GitHub releases page for installers (`.dmg`, `.msi`, `.deb`, AppImage).

I'm actively developing this and would love feedback -- especially on what integrations and workflows would make this useful for you.

GitHub: https://github.com/aayushch/laya

---

*Suggested publishing platforms: Dev.to, Hashnode, Medium, personal blog. Cross-post with canonical URL pointing to whichever you publish first.*

*SEO keywords to include in meta tags: AI notification manager, unified notification inbox, cross-platform context, developer productivity tool, open source notification tool, AI command center, Jira Slack Gmail integration*
