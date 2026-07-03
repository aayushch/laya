# Reddit Posts

## Post 1: r/selfhosted

### Title
Laya -- self-hosted AI command centre that connects Jira, Slack, Gmail, GitHub notifications and stages actions for you

### Body
Been building this for a while and figured this community would appreciate it.

**What it is:** A desktop app that intercepts events from your dev tools (Jira, Slack, Gmail, GitHub, Bitbucket, Calendar, Linear, Outlook, Notion), classifies them with AI, links related items across platforms, and presents Action Cards with staged actions.

**Why r/selfhosted cares:**

- Runs entirely on your machine. SQLite + ChromaDB for data, n8n (local Node.js) for integrations. No cloud backend, no accounts, no telemetry.
- API keys stored in your OS keychain (macOS Keychain, libsecret, Windows Credential Manager)
- Optional Ollama support for fully local LLM processing -- zero data leaves your machine
- Three-tier privacy classification: metadata (always processed), content (configurable), and sensitive data (DMs/emails -- can force local-only)
- Pre-built releases for macOS, Windows, and Linux. Also runs from source.

**Stack:** Tauri v2 (Rust) + Svelte 5 + Python FastAPI + SQLite + ChromaDB + n8n + LiteLLM

**The self-hosted angle:** n8n handles all external API connections. You configure OAuth credentials once in n8n, and everything stays local. No proxy servers, no SaaS middlemen. The app manages n8n as a local process -- starts on launch, stops on quit.

GitHub: https://github.com/aayushch/laya

Happy to answer questions about the architecture or privacy model.

---

## Post 2: r/opensource

### Title
I built an open-source AI command centre for managing work notifications across Jira/Slack/Gmail/GitHub -- here's what I learned

### Body
After months of building Laya, I wanted to share what the project looks like and some of the interesting technical decisions.

**The problem:** Dozens of notifications per day across multiple tools. Same issue, different name on every platform. Too much of every morning lost to context reconstruction before making a single decision.

**The solution:** Laya intercepts events, links related items across platforms using semantic matching + LLM verification, and surfaces Action Cards with pre-staged actions.

**Interesting technical bits:**

1. **Zero-prompt architecture.** Unlike most AI tools where you invoke the intelligence ("summarize this," "draft a reply"), Laya intercepts events as they arrive and runs the intelligence pipeline automatically. By the time you open it, the analysis is done. No prompts, no manual invocations.

2. **Three-layer entity resolution.** Connecting "BUG-1234" (Jira) to "the payment bug" (Slack) to "fix: null check in handler" (PR) uses: explicit cross-references, ChromaDB vector similarity, and LLM confirmation for edge cases. Any single layer has too many false positives.

3. **Classification learning.** When you correct a priority or persona assignment, the system extracts generalizable rules and injects them into future LLM prompts. It genuinely improves over time.

4. **Three-tier model architecture.** Fast LLM (Haiku) for classification, strong LLM (Sonnet) for user-facing output, optional local LLM (Ollama) for sensitive data. Keeps daily costs under $0.50 for typical usage.

5. **n8n as integration gateway.** Instead of building 11 custom API integrations, I use n8n as the sole boundary to external services. Credential management, OAuth flows, webhook handling -- all solved. Users can add custom integrations via n8n's visual editor.

**Stack:** Tauri v2, Svelte 5, FastAPI, SQLite, ChromaDB, LiteLLM, n8n

**License:** Open source

GitHub: https://github.com/aayushch/laya

Would love feedback, contributions, or just a conversation about the approach.

---

## Post 3: r/devops (or r/programming)

### Title
Open source tool that uses AI to deduplicate and synthesize notifications across Jira, Slack, Gmail, and GitHub

### Body
I built Laya because I was wasting my morning just building context across platforms. The same Jira ticket has a Slack thread (different name), a PR (technical name), and an email thread (yet another name). Dozens of notifications, a handful of actual things to deal with.

**What Laya does differently from unified inboxes:**

- **Deduplication**: Semantic matching links the Jira ticket, Slack thread, PR, and email about the same issue into one group
- **Classification**: LLM routes events to specialized personas (Engineer, Comms, Ops) with priority scoring
- **Action staging**: By the time you see a notification, the reply/comment/review is already drafted
- **Learning**: Correcting classifications trains the system over time -- rules are extracted and applied to future events

**Architecture:**

```
Your Tools --> n8n (local) --> Python Engine (FastAPI) --> Action Cards (Tauri + Svelte)
```

n8n handles all OAuth/webhook complexity. Engine does classification + entity resolution + staging. Desktop app renders cards.

**Privacy:** Everything local. SQLite, ChromaDB, OS keychain. LLM calls use your own API keys. Optional Ollama for fully local processing.

9 outbound platforms supported (Gmail, Slack, Jira, GitHub, Bitbucket, Calendar, Linear, Outlook, Notion) -- all actions require explicit user approval.

Open source: https://github.com/aayushch/laya

---

## Post 4: r/SideProject

### Title
I built an open-source AI command centre because I was drowning in notifications -- not in work, but in notifications about work

### Body
Every morning: a wall of Slack messages. A stack of Jira updates. A thread of emails. A batch of PR notifications. Most of them about the same few things, just scattered across platforms with different names.

So I built Laya. It's a local-first desktop app that:

1. Intercepts events from Jira, Slack, Gmail, GitHub, Bitbucket, Calendar, Linear, Outlook, Notion
2. Links related items across platforms ("BUG-1234" in Jira = "the payment thing" in Slack = "fix: null check" in the PR)
3. Classifies each event by category and priority using specialized AI personas
4. Stages actions (draft replies, PR comments, status updates)
5. Presents Action Cards -- approve or dismiss

Instead of wading through a flood of notifications to understand a handful of things, I review a manageable set of Action Cards with the context already reconstructed.

**Built with:** Tauri v2, Svelte 5, FastAPI, SQLite, ChromaDB, n8n, LiteLLM

**Open source and free:** https://github.com/aayushch/laya

The feature I'm most proud of is "Coherence" -- you can search for any person, ticket, or project and trace their activity across every connected platform with AI-generated narratives. Makes "what's Sarah working on?" a 10-second question instead of a 15-minute investigation.

Happy to answer questions or hear about your notification management workflows.

---

## Subreddit Strategy

| Subreddit | Best Post | Why |
|-----------|-----------|-----|
| r/selfhosted | Post 1 | Privacy-first, local-only, no cloud dependency |
| r/opensource | Post 2 | Technical decisions, learning system, contribution welcome |
| r/devops | Post 3 | Architecture diagram, deduplication, practical workflow |
| r/programming | Post 3 | Technical depth, multi-platform entity resolution |
| r/SideProject | Post 4 | Personal story, builder's perspective |
| r/ProductivityApps | Post 4 (adapted) | Focus on time saved, before/after workflow |
| r/MacApps | Post 1 (adapted) | Tauri native app, macOS keychain, .dmg available |
| r/linuxapps | Post 1 (adapted) | AppImage/.deb, local-first, Ollama support |

**Timing:** Post on weekday mornings (EST). Don't cross-post to multiple subreddits on the same day -- space them 2-3 days apart to avoid looking spammy.

**Engagement:** Respond to every comment within the first 2 hours. Be genuine, answer technical questions in depth, and don't be defensive about criticism.
