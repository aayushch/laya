# Show HN Post

## Title

Show HN: Laya -- Open-source AI command centre for dev tool notifications (Jira, Slack, Gmail, GitHub)

---

## Post Body

I got tired of spending my morning piecing together what happened overnight across Jira, Slack, Gmail, and Bitbucket. Same bug, four platforms, four different names. Dozens of notifications to wade through to understand a handful of things.

So I built Laya -- a local-first desktop app that intercepts events from your professional tools, classifies them with LLM-powered personas, researches cross-platform context, and surfaces ready-to-approve Action Cards.

**What it does:**

- Events from Jira, Slack, Gmail, GitHub, Bitbucket, Calendar, Linear, Outlook, and Notion flow through n8n (local Node.js) into the Laya engine
- A fast LLM (router) classifies each event by category, persona, and priority
- Semantic matching + LLM verification links related items across platforms -- "BUG-1234" in Jira to "the payment bug" in Slack to "fix: null check" in the PR
- A stronger LLM (stager) synthesizes context and drafts actions (reply, comment, PR review)
- You see Action Cards: approve, dismiss, or open a workspace for complex tasks
- Complex cards (bug fixes, code reviews) can spawn interactive coding agent sessions (Claude Code, Gemini CLI, or Codex)

**What makes it different from unified inboxes:**

1. It doesn't just collect notifications -- it does the research and stages actions. By the time you see a card, the draft reply or PR comment is ready.
2. It's proactive, not prompt-based. Most AI tools wait for you to ask -- "summarize this email," "review this PR." Laya intercepts events as they arrive and automatically runs the intelligence pipeline. You never write a prompt; you just open the app and the work is done.
3. Cross-platform entity resolution actually works. Three layers: explicit cross-references from platform data, vector similarity (ChromaDB), and LLM confirmation for edge cases.
4. It learns from your corrections. When you fix a wrong priority or persona, the system extracts generalizable rules and injects them into future classifications.
5. "Omni" gives you a rolling cross-platform summary with four temporal layers (Attention, Recent, Period, Milestone) -- basically answers "where am I right now?" at any point.

**Privacy:**

Everything runs on your machine. SQLite for structured data, ChromaDB for vector search, OS keychain for credentials. The only external calls are to LLM APIs (your keys) and your tool APIs (through n8n). Optional Ollama integration for fully local LLM processing of sensitive data.

**Tech stack:**

- Desktop shell: Tauri v2 (Rust)
- Frontend: Svelte 5 + Skeleton UI + Tailwind v4
- Backend: Python 3.10+ / FastAPI / asyncio
- LLM: LiteLLM (Anthropic, OpenAI, Google, Ollama)
- Data: SQLite (aiosqlite, WAL) + ChromaDB (embedded)
- Integrations: n8n (local Node.js, 14+ inbound workflows, 9 outbound platforms)
- Agents: Claude Code / Gemini CLI / Codex CLI

**Getting started:**

```
git clone https://github.com/aayushch/laya
cd laya
scripts/setup-dev.sh
scripts/dev.sh
```

Pre-built releases for macOS, Windows, and Linux on the GitHub releases page.

Needs Python 3.10+, Node.js 20+, and Rust (for dev builds). Production builds bundle everything -- users just need the installer.

GitHub: https://github.com/aayushch/laya

Happy to answer questions about the architecture, LLM pipeline, or entity resolution approach.

---

## HN Submission Tips

- Post between 8-10 AM EST on a weekday (Tuesday-Thursday ideal)
- Title must start with "Show HN: "
- Keep the post factual and technical -- HN audience values specifics over marketing language
- Be ready to respond to comments within minutes for the first 2 hours
- Common HN questions to prepare for:
  - "What's the LLM cost per day for typical usage?" (have real numbers ready)
  - "Why n8n and not direct API integrations?" (answer: credential management, workflow flexibility, community ecosystem)
  - "How does this compare to X?" (research Shortwave, Linear, Superhuman, etc.)
  - "What about self-hosted LLMs?" (answer: Ollama support built in)
  - "Why Tauri instead of Electron?" (answer: memory footprint, native performance, Rust safety)
  - "Is this actually usable or a prototype?" (answer: pre-built releases, 65+ migrations, 23 API routers)
