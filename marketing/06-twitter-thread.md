# Twitter/X Launch Thread

## Thread

**Tweet 1 (Hook):**

I was spending my morning just reading notifications.

Not working. Not deciding. Just... reading. Across Jira, Slack, Gmail, Bitbucket, Calendar.

Dozens of notifications. A handful of actual things to deal with. Same issue, different names on different platforms.

So I built Laya. It's open source now.

---

**Tweet 2 (What it does):**

Laya intercepts events from your tools, connects related items across platforms, and surfaces Action Cards with the research already done.

By the time you see a notification, the draft reply is staged. Approve or dismiss.

A handful of Action Cards instead of a flood of notifications.

---

**Tweet 3 (Proactive vs prompt-based):**

Here's the key difference from other AI tools:

Most require you to ask. "Summarize this email." "Review this PR." "What's the status?"

Laya doesn't wait for a prompt. Events flow in, intelligence runs automatically, and staged actions are ready before you even open the app.

Proactive, not reactive.

---

**Tweet 4 (Entity resolution):**

The hardest problem was cross-platform entity resolution.

"BUG-1234" in Jira
"the payment thing" in Slack
"fix: null check in handler" in the PR
"status of the payment issue" in Gmail

Same issue. Four names. Laya links them automatically using 3 layers: explicit references + vector similarity + LLM verification.

---

**Tweet 5 (AI architecture):**

The AI architecture keeps costs under $0.50/day:

- Fast LLM (Haiku) classifies events -- handles 80% of volume
- Strong LLM (Sonnet) generates user-facing output -- only for cards that need synthesis
- Optional local LLM (Ollama) for sensitive data -- zero data leaves your machine

---

**Tweet 6 (Privacy):**

Fully local-first:
- SQLite + ChromaDB on your machine
- n8n runs locally for all integrations
- API keys in your OS keychain
- No Laya server, no accounts, no telemetry
- Three-tier privacy classification with configurable local-only processing

Your Slack DMs never touch a server you don't control.

---

**Tweet 7 (Features montage):**

Features that make it a command centre, not just an inbox:

- Omni: rolling cross-platform summary ("where am I right now?")
- Coherence: search any person/ticket across all platforms with AI narrative
- Agent Workspaces: Claude Code / Gemini / Codex for complex tasks
- Classification learning: improves from your corrections
- Daily briefings

---

**Tweet 8 (CTA):**

Laya is open source and free.

macOS, Windows, Linux. Pre-built releases or from source.

https://github.com/aayushch/laya

Star it, try it, break it, tell me what's missing.

---

## Standalone Tweets (for days after launch)

**Standalone 1 (Problem framing):**

The same Jira ticket is called:
- "PROD-1234" in the tracker
- "the payment bug" in Slack
- "null pointer in checkout" in the PR
- "that thing from last sprint" in standup

Cross-platform context is the real productivity killer. I built an open-source tool that solves it: https://github.com/aayushch/laya

---

**Standalone 2 (Technical insight):**

Interesting finding from building Laya: a three-tier LLM architecture (fast classifier + strong synthesizer + optional local) keeps AI costs under $0.50/day while handling a full day's worth of events across all your tools.

The cheap model handles 80% of the work. The expensive model only fires when quality matters.

Open source: https://github.com/aayushch/laya

---

**Standalone 3 (Before/after):**

Before Laya:
- Most of my morning reading notifications
- More time cross-referencing platforms
- Even more time drafting responses
- Finally start actual work... eventually

After Laya:
- Quick review of Action Cards
- Approve/dismiss staged actions
- Start actual work almost immediately

Open source: https://github.com/aayushch/laya

---

**Standalone 4 (Privacy angle):**

Hot take: your notification management tool shouldn't send your Slack DMs to a third-party server.

Laya runs entirely on your machine. SQLite, ChromaDB, local n8n. The only external calls are to LLM APIs using your own keys -- and even those are optional with Ollama.

https://github.com/aayushch/laya

---

## LinkedIn Post

I've been building something for the past few months, and it's now open source.

The problem: Every morning, I'd spend too much time just reading notifications. Jira, Slack, Gmail, Bitbucket, Calendar. Dozens of notifications, most of them about the same few issues -- just scattered across platforms with different names.

The real problem wasn't volume. It was fragmentation. The same bug is "PROD-1234" in Jira, "the payment thing" in Slack, "fix: null check in handler" in Bitbucket, and "status of the payment issue" in email. Before I could make a single decision, I needed to reconstruct context across 4 platforms.

So I built Laya: an AI command centre that intercepts events from your professional tools, connects related items across platforms using semantic matching, and presents ready-to-approve Action Cards.

Instead of a flood of notifications, I get a manageable set of Action Cards. Each one already has the cross-platform context, a priority assignment from a specialized AI persona (Engineer, Comms, Ops), and staged actions I can approve with one click.

Some features I'm proud of:
- Unlike prompt-based AI tools where you ask "summarize this email" or "review this PR," Laya works proactively -- events flow in, intelligence runs automatically, and the analysis is ready before you even open the app
- Cross-platform entity resolution that links items even when they use completely different names
- Classification learning that extracts rules from your corrections and improves over time
- "Omni" -- a rolling summary answering "where am I right now?" across all platforms
- Fully local-first: your data never leaves your machine

It's open source and free. macOS, Windows, and Linux.

If you're drowning in cross-platform notification chaos, give it a try: https://github.com/aayushch/laya

#opensource #developer #productivity #ai #devtools
