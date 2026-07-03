# Developer Community Posts

## Dev.to / Hashnode Post: Technical Deep Dive

### Title
Building a Three-Layer Entity Resolution System for Cross-Platform Notifications

### Tags
#opensource #ai #python #productivity

### Body

When I started building [Laya](https://github.com/aayushch/laya) -- an open-source AI command centre for managing work notifications -- the hardest problem wasn't classification or action staging. It was entity resolution: figuring out that "BUG-1234" in Jira, "the payment bug" in Slack, and "fix: null check in handler" in a PR are all the same thing.

Here's how the three-layer approach works, with code-level detail.

#### Layer 1: Explicit References

The simplest layer. During event ingestion, we extract cross-platform identifiers using regex patterns:

- Jira ticket IDs: `[A-Z]+-\d+` (e.g., PROD-1234)
- PR/issue numbers: `#\d+` with repo context
- URLs to other platforms: full URL parsing
- Git commit SHAs in messages

These create deterministic entity links. If a PR body says "fixes PROD-1234," we create a hard link between the PR entity and the Jira entity. No AI needed.

**Hit rate:** ~35% of cross-platform connections.

#### Layer 2: Semantic Similarity (ChromaDB)

We embed every event's key content (title + summary) using ChromaDB's built-in ONNX embeddings (or optionally nomic-embed-text via sentence-transformers).

When a new event arrives, we query ChromaDB for similar events:

```python
results = collection.query(
    query_texts=[event_summary],
    n_results=10,
    where={"space_id": space_id}
)
```

Items with distance < 0.20 are auto-linked. This catches connections like:
- "NPE in PaymentHandler.process()" ↔ "the payment processing bug is back"
- "deploy staging environment" ↔ "staging deploy failed on latest push"

**Hit rate:** ~45% of remaining connections (after Layer 1).

**False positive rate:** ~8%. This is why we need Layer 3.

#### Layer 3: LLM Verification

For borderline similarity (0.20 < distance < 0.30), we ask an LLM to verify. The prompt includes both items' content and asks: "Are these about the same specific issue, project, or entity?"

The LLM distinguishes cases that embeddings can't:
- "fix: payment handler null check" vs. "planning: payment system redesign" (different projects, similar embeddings)
- "Sarah's feedback on the auth PR" vs. "Sarah's PTO request" (same person, completely different topics)

**Cost:** ~$0.001 per verification (using Haiku). Typical daily volume: 20-50 verifications.

#### The Learning Loop

Users can correct entity links -- splitting incorrectly grouped items or merging missed ones. After 10+ corrections accumulate, an LLM extracts generalizable rules:

- "PRs from the payments-service repo should always link to PROD-* tickets"
- "Messages in #incidents about 'the X thing' usually refer to the most recent PROD ticket about X"

These rules get injected into future classification and entity resolution prompts. The system genuinely improves over time.

#### Results

After 2-3 weeks of corrections, the system achieves ~92% accuracy on cross-platform entity resolution. The remaining 8% are edge cases (ambiguous references, very new projects without enough training data) that get caught by periodic user review.

Daily cost for entity resolution: ~$0.02-0.05 in LLM calls (Layer 3 verification only).

**Try it yourself:** https://github.com/aayushch/laya

---

## Discord / Community Forum Post (Short)

### For: Developer Discord servers, Indie Hackers, etc.

**Title:** Open source AI command centre for dev tool notifications

**Body:**

Just open-sourced Laya -- a desktop app that intercepts notifications from Jira, Slack, Gmail, GitHub (and 5 more platforms), connects related items across platforms, and surfaces Action Cards with staged actions.

The pitch: instead of wading through dozens of notifications to understand a handful of things, you review a manageable set of Action Cards where the context is already reconstructed. And unlike prompt-based AI tools, Laya works proactively -- events flow in, intelligence runs automatically, no prompts needed.

Key features:
- Cross-platform entity resolution (three layers: explicit refs + vector similarity + LLM verification)
- Multi-persona AI (Engineer, Comms, Ops, Sales, HR, Finance)
- Interactive coding agent workspaces (Claude Code, Gemini CLI, Codex)
- Rolling cross-platform summary (Omni)
- Local-first (SQLite, ChromaDB, n8n -- all on your machine)
- Learns from your corrections

Stack: Tauri v2, Svelte 5, FastAPI, SQLite, ChromaDB, LiteLLM, n8n

Open source, free, macOS/Windows/Linux.

GitHub: https://github.com/aayushch/laya

---

## Indie Hackers Post

### Title
I built an open-source AI command centre to solve my notification chaos -- here's the journey

### Body

**The problem I had:**

I was spending my morning just reading notifications across Jira, Slack, Gmail, and Bitbucket. The real killer wasn't volume -- it was that the same issue would appear on multiple platforms with different names, and I'd have to mentally reconstruct the connections.

**What I built:**

Laya -- a local-first desktop app that intercepts events from 9+ platforms, uses AI to classify, connect, and stage actions, then presents Action Cards I can approve or dismiss. A flood of notifications becomes a manageable set of decisions.

**The technical approach:**

Three processes run on your machine:
1. n8n (local Node.js) handles all external API connections
2. Python engine (FastAPI) does classification, entity resolution, and action staging
3. Tauri desktop app (Rust + Svelte 5) renders the UI

AI is tiered: cheap model for classification (80% of work), expensive model for user-facing output (20%), optional local model (Ollama) for sensitive data. Daily cost: ~$0.50.

**What I learned:**

1. **Proactive beats reactive.** Most AI tools wait for a prompt -- "summarize this," "draft a reply." Laya automates that first step: events flow in, the intelligence pipeline runs automatically, and the analysis is ready before you open the app. Eliminating the "what should I even look at?" step turned out to be the biggest time saver.

2. **n8n saved months of work.** OAuth flows, token refresh, webhook management -- building that from scratch would have been brutal. Using n8n as the sole integration gateway was the best architectural decision.

3. **Entity resolution is the hard problem.** Classification was straightforward (LLMs are good at it). Figuring out that "the payment thing" in Slack = "PROD-1234" in Jira = "fix: null check in handler" in a PR -- that required three complementary approaches.

4. **Learning from corrections is a multiplier.** Spending 10% of development time on the feedback-to-rules pipeline (where user corrections get extracted into generalizable rules) made the entire system significantly more accurate over a few weeks.

5. **Local-first is harder but necessary.** The data flowing through Laya (Slack DMs, email bodies, code diffs) is too sensitive for a third-party server. Local-first meant more work (SQLite instead of Postgres, ChromaDB embedded instead of hosted, credential management in OS keychain) but it's a genuine differentiator.

**Where it's at now:**

Open source, pre-built releases for macOS/Windows/Linux, 65+ database migrations, 23 API endpoints, 9 outbound platforms.

GitHub: https://github.com/aayushch/laya

If you're building something in the developer tools space, happy to chat about the architecture or share learnings.
