# Laya: The AI Operating System

**A cadence for professional orchestration.**

Laya is a local-first desktop application that acts as an AI operating system for professionals. It intercepts events from your professional tools (Jira, Bitbucket, Slack, Gmail, Calendar), performs autonomous research and action-staging using LLM-powered agents, and presents you with ready-to-approve **Action Cards** -- so the answer is ready before you open the notification.

## How It Works

```
Your Tools (Jira, Slack, Gmail, Bitbucket, Calendar)
         |
         v
      n8n (local Docker) -- normalizes events
         |
         v
   Laya Engine (Python) -- classifies, researches, stages
         |
         v
    Laya UI (Tauri + Svelte) -- Action Cards you approve or dismiss
         |
         v
      n8n -- executes approved actions (creates PRs, sends replies, etc.)
```

## Key Features (v0.1)

- **Multi-persona brain:** Routes events to specialized AI personas (Engineer, Comms, Ops) with domain-specific tools and prompts
- **Card Workspaces:** Complex tasks (bug fixes, code reviews) open interactive workspaces where you collaborate with a coding agent (Claude Code, Gemini CLI, or Codex) through multiple approval steps
- **Cross-platform memory:** Entity resolution links "BUG-1234" in Jira to "PR-891" in Bitbucket to "the payment bug" in Slack
- **Daily Briefing:** Morning summary of overnight activity, pending cards, and today's calendar with context
- **Analytics Dashboard:** Track events processed, time saved, LLM costs, and approval rates
- **Chat sidebar:** Ask Laya questions about your events, projects, and context
- **Learning loop:** Laya improves its classification and priority based on your approval/dismiss patterns
- **Privacy-aware:** Three-tier data classification with cloud/local processing options

## Tech Stack

| Layer | Technology |
|---|---|
| Desktop Shell | Tauri v2 (Rust) |
| Frontend | Svelte 5 + Skeleton UI + Tailwind CSS |
| Backend | Python 3.11+ / FastAPI / LangGraph |
| LLM Interface | LiteLLM (supports Anthropic, OpenAI, Google, Ollama) |
| Integration Gateway | n8n (Docker) |
| Structured Storage | SQLite |
| Vector Storage | ChromaDB (embedded) |
| Embeddings | nomic-embed / all-MiniLM (local via sentence-transformers) |
| Coding Agents | Claude Code / Gemini CLI / OpenAI Codex CLI |

## Documentation

All design and architecture documents are in [`docs/`](./docs/):

- [**System Architecture**](./docs/architecture.md) -- Complete architecture with diagrams and component descriptions
- [**Event Schema**](./docs/event-schema.md) -- The Laya Event schema specification (inbound + outbound)
- [**API Contracts**](./docs/api-contracts.md) -- REST, WebSocket, and inter-service API definitions
- [**Database Schema**](./docs/database-schema.md) -- SQLite tables and ChromaDB collection design
- [**Project Structure**](./docs/project-structure.md) -- Repository layout, directory structure, config file schemas
- [**Implementation Plan**](./docs/implementation-plan.md) -- 8 milestones across ~19 weeks
- [**Decision Log**](./docs/decision-log.md) -- All 57 architectural decisions with rationale

## Concept Materials

The original concept documents that informed this architecture:

- [Laya Architecture Spec.md](./Laya%20Architecture%20Spec.md) -- Initial technical architecture vision
- [Laya Master Orchestrator.md](./Laya%20Master%20Orchestrator.md) -- Multi-persona system prompt design
- [Laya - The Professional AI Operating System.pdf](./Laya%20-%20The%20Professional%20AI%20Operating%20System.pdf) -- Concept slide deck

## Project Status

**Phase:** Pre-development (architecture and design complete)

**Next step:** Milestone 1 -- Skeleton (get Tauri + Python engine + n8n Docker running and talking to each other)
