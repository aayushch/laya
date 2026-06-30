# Laya API Contracts

## Overview

Laya has three communication boundaries:

1. **n8n <-> Laya Engine** (HTTP POST in both directions)
2. **Laya Engine <-> Tauri UI** (WebSocket + REST)
3. **Laya Engine <-> Coding Agents** (PTY subprocess stdin/stdout)

This document specifies the first two. The coding agent interface is defined in the codebase as the `CodingAgent` protocol.

## 1. n8n -> Laya Engine (Inbound Events)

### `POST /events`

Receives normalized events from n8n. See [event-schema.md](./event-schema.md) for the full schema.

**Request:**
```
POST http://localhost:8420/events
Content-Type: application/json

{
  "event_id": "evt_a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp": "2026-02-22T14:30:00Z",
  "source": {
    "platform": "jira",
    "connection_id": "jira_main",
    "raw_event_type": "issue_assigned"
  },
  "actor": {
    "name": "Sarah Chen",
    "email": "sarah@company.com",
    "platform_handle": "schen"
  },
  "subject": {
    "type": "ticket",
    "id": "BUG-1234",
    "title": "NPE in PaymentService on null customer ID",
    "url": "https://company.atlassian.net/browse/BUG-1234"
  },
  "content": {
    "body": "The payment service throws a NullPointerException when...",
    "attachments": [],
    "metadata": {
      "jira_project": "PAYMENTS",
      "jira_labels": ["backend", "production"]
    }
  }
}
```

**Response (202 Accepted):**
```json
{
  "event_id": "evt_a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "queued"
}
```

**Error Response (400 Bad Request):**
```json
{
  "error": "validation_error",
  "message": "Missing required field: actor.email",
  "event_id": "evt_a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

## 2. Laya Engine -> n8n (Outbound Actions)

### `POST /webhook/<workflow-id>`

Sends approved actions to n8n for execution. Each platform has its own executor workflow with a unique webhook URL.

**Request:**
```
POST http://localhost:45678/webhook/<bitbucket-executor-webhook-id>
Content-Type: application/json

{
  "action_id": "act_x1y2z3-a4b5-c6d7-e8f9-012345678901",
  "source_event_id": "evt_a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "target": {
    "platform": "bitbucket",
    "connection_id": "bb_main"
  },
  "action_type": "create_pull_request",
  "payload": {
    "repository": "payments-service",
    "source_branch": "fix/BUG-1234-npe-null-customer",
    "target_branch": "main",
    "title": "Fix NPE on null customer ID in PaymentService",
    "body": "Resolves BUG-1234. Added null-safety check...",
    "reviewers": ["sarah@company.com"]
  }
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "result": {
    "pr_id": 903,
    "pr_url": "https://bitbucket.org/team/payments-service/pull-requests/903"
  }
}
```

**Error Response (500):**
```json
{
  "success": false,
  "error": "Bitbucket API returned 403: insufficient permissions"
}
```

## 3. Laya Engine <-> Tauri UI (REST Endpoints)

### `GET /cards`

Fetch Action Cards for the feed.

**Query Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `status` | string | `all` | Filter by status: `pending`, `reviewing`, `agent_running`, `awaiting_input`, `staged`, `approved`, `executing`, `completed`, `failed`, `dismissed`, `all` |
| `priority` | string | `all` | Filter by priority: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`, `all` |
| `limit` | int | 50 | Max cards to return |
| `offset` | int | 0 | Pagination offset |
| `sort` | string | `priority_time` | Sort order: `priority_time` (priority desc, then time desc), `time` (newest first), `time_asc` (oldest first) |

**Response (200):**
```json
{
  "cards": [
    {
      "card_id": "card_001",
      "event_id": "evt_a1b2c3d4",
      "created_at": "2026-02-22T14:31:00Z",
      "priority": "CRITICAL",
      "persona": "ENGINEER",
      "header": "NPE in PaymentService -- Fix Staged",
      "summary": "Null customer ID causes crash in payment flow.",
      "status": "awaiting_input",
      "privacy_tier": 2,
      "has_workspace": true
    }
  ],
  "total": 12,
  "limit": 50,
  "offset": 0
}
```

### `GET /cards/:card_id`

Fetch full detail for a single card.

**Response (200):**
```json
{
  "card_id": "card_001",
  "event_id": "evt_a1b2c3d4",
  "created_at": "2026-02-22T14:31:00Z",
  "priority": "CRITICAL",
  "persona": "ENGINEER",
  "header": "NPE in PaymentService -- Fix Staged",
  "summary": "Null customer ID causes crash in payment flow. Root cause: missing null check introduced in PR #891.",
  "intelligence_report": [
    "NullPointerException at PaymentService.java:142",
    "Line changed 3 days ago by @sarah in PR #891 (refactor)",
    "PR #891 removed a null-check that existed since 2024"
  ],
  "staged_output": {
    "type": "code_diff",
    "content": "--- a/PaymentService.java\n+++ b/PaymentService.java\n@@ -140,3 +140,5 @@..."
  },
  "suggested_actions": [
    {
      "action_id": "act_001",
      "label": "Create PR with fix",
      "action_type": "create_pull_request",
      "target_platform": "bitbucket",
      "payload": {}
    },
    {
      "action_id": "act_002",
      "label": "Comment on Jira ticket",
      "action_type": "add_comment",
      "target_platform": "jira",
      "payload": {}
    }
  ],
  "status": "awaiting_input",
  "privacy_tier": 2,
  "has_workspace": true,
  "resolved_at": null,
  "user_feedback": null
}
```

### `GET /cards/:card_id/workspace`

Fetch workspace state for a card.

**Response (200):**
```json
{
  "card_id": "card_001",
  "session": {
    "session_id": "sess_001",
    "agent_type": "claude_code",
    "status": "awaiting_input",
    "started_at": "2026-02-22T14:31:15Z",
    "updated_at": "2026-02-22T14:32:00Z"
  },
  "events": [
    {
      "event_id": "we_001",
      "timestamp": "2026-02-22T14:31:15Z",
      "event_type": "status_change",
      "actor": "system",
      "content": {"status": "agent_started", "agent": "claude_code"},
      "requires_input": false
    },
    {
      "event_id": "we_002",
      "timestamp": "2026-02-22T14:31:20Z",
      "event_type": "file_read",
      "actor": "agent",
      "content": {"file": "PaymentService.java", "lines": "130-155"},
      "requires_input": false
    },
    {
      "event_id": "we_003",
      "timestamp": "2026-02-22T14:32:00Z",
      "event_type": "approval_request",
      "actor": "agent",
      "content": {"message": "I need to modify 3 files. Should I proceed?", "files": ["PaymentService.java", "CustomerDAO.java", "PaymentServiceTest.java"]},
      "requires_input": true
    }
  ],
  "context": {
    "related_entities": [
      {"type": "pull_request", "id": "PR-891", "platform": "bitbucket"},
      {"type": "ticket", "id": "BUG-1200", "platform": "jira"}
    ],
    "repo": "payments-service",
    "branch": "fix/BUG-1234"
  }
}
```

### `POST /cards/run-agent`

Run a coding agent on a card.

**Request:**
```json
{
  "card_id": "card_001",
  "prompt": "Investigate the payment timeout issue",
  "agent_type": "claude_code",
  "directory": "/path/to/repo",
  "add_dirs": ["/path/to/other/repo"],
  "mode": "plan",
  "space_id": "default"
}
```

**Response (200):**
```json
{
  "status": "agent_running",
  "card_id": "card_001"
}
```

### `POST /entity/:entity_id/run-agent`

Run a coding agent at the entity group level, covering all cards in the group.

**Request:**
```json
{
  "prompt": "Summarize all related changes",
  "agent_type": "claude_code"
}
```

**Response (200):**
```json
{
  "status": "agent_running",
  "entity_id": "ENT-123",
  "session_id": "sess_002"
}
```

### `POST /workspace/:session_id/answer`

Answer an agent's question during execution.

**Request:**
```json
{
  "answers": [{"header": "Proceed?", "selected": "yes"}],
  "add_dirs": []
}
```

### `POST /workspace/:session_id/resume`

Resume a completed or paused session with a new prompt.

**Request:**
```json
{
  "prompt": "Now fix the test file too",
  "add_dirs": []
}
```

### `POST /workspace/:session_id/dismiss-questions`

Dismiss pending agent questions without answering.

### `GET /workspace/research-files/:card_id`

List files created by a research session.

**Response (200):**
```json
{
  "files": ["report.md", "data.json"],
  "directory": "~/.laya/tmp/research/card_001/"
}
```

### `GET /workspace/research-files/:card_id/read?path=report.md`

Read a specific research file (max 2 MB, UTF-8 text).

### `POST /actions/approve`

Approve an action from an Action Card.

**Request:**
```json
{
  "card_id": "card_001",
  "action_id": "act_001",
  "modifications": null
}
```

With user edits:
```json
{
  "card_id": "card_001",
  "action_id": "act_001",
  "modifications": {
    "title": "Fix NPE: add null-safety for customer ID"
  }
}
```

**Response (200):**
```json
{
  "card_id": "card_001",
  "action_id": "act_001",
  "status": "executing"
}
```

### `POST /actions/dismiss`

Dismiss an Action Card.

**Request:**
```json
{
  "card_id": "card_001",
  "reason": "not_relevant"
}
```

`reason` is optional. Values: `not_relevant`, `wrong_priority`, `bad_output`, `duplicate`, `other`.

**Response (200):**
```json
{
  "card_id": "card_001",
  "status": "dismissed"
}
```

### `GET /dashboard`

Fetch analytics data for the dashboard.

**Query Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `period` | string | `week` | Time period: `today`, `week`, `month`, `all` |

**Response (200):**
```json
{
  "period": "week",
  "events_processed": 47,
  "cards_generated": 38,
  "cards_approved": 29,
  "cards_edited": 3,
  "cards_dismissed": 6,
  "cards_pending": 4,
  "avg_response_time_ms": 42000,
  "estimated_time_saved_minutes": 390,
  "llm_cost_usd": 2.34,
  "by_source": {
    "jira": 18,
    "slack": 12,
    "gmail": 8,
    "bitbucket": 5,
    "calendar": 4
  },
  "by_persona": {
    "ENGINEER": {"total": 22, "approved": 18, "approval_rate": 0.82},
    "COMMS": {"total": 12, "approved": 9, "approval_rate": 0.75},
    "OPS": {"total": 4, "approved": 2, "approval_rate": 0.50}
  },
  "by_priority": {
    "CRITICAL": 3,
    "HIGH": 12,
    "MEDIUM": 18,
    "LOW": 5
  }
}
```

### `GET /dashboard/throughput`

Per-bucket throughput and wait-time for the last `minutes` (default 60, clamped to `[10, 43200]`). Bucket granularity adapts to the window (1-min ≤120, 5-min ≤360, 1-hour ≤1440, else 1-day).

**Response (200):**
```json
{
  "buckets": [
    {"minute": "2026-06-29T14:30:00Z", "ingested": 4, "processed": 4, "failed": 0, "avg_wait_s": 12.4, "p95_wait_s": 31.0}
  ],
  "window_minutes": 60
}
```

### `GET /settings`

Fetch current configuration.

**Response (200):**
```json
{
  "models": {
    "router": "claude-haiku-4-5-20251001",
    "stager": "claude-sonnet-4-5-20250929",
    "chat": "claude-sonnet-4-5-20250929",
    "local": "ollama/llama3"
  },
  "coding_agent": "claude_code",
  "privacy": {
    "tier3_sources": ["gmail", "slack_dm"],
    "tier3_processing": "cloud_with_warning"
  },
  "briefing": {
    "enabled": true,
    "time": "07:00",
    "timezone": "America/New_York"
  },
  "notifications": {
    "enabled": true,
    "min_priority": "HIGH"
  },
  "connected_sources": {
    "jira": true,
    "bitbucket": true,
    "slack": true,
    "gmail": true,
    "calendar": true
  }
}
```

### `PUT /settings`

Update configuration. Partial updates supported.

**Request:**
```json
{
  "models": {
    "router": "gemini-1.5-flash"
  }
}
```

**Response (200):**
```json
{
  "status": "updated",
  "restart_required": false
}
```

### `GET /settings/agent-backends`

Per-agent availability and capability tier for the "use an installed CLI agent as the inference backend" picker. Tier `native` = the CLI enforces the JSON schema for us (Claude Code); `best_effort` = the schema is injected as text and validated/retried.

**Response (200):**
```json
{
  "backends": [
    {"agent_id": "claude_code", "available": true, "path": "/usr/local/bin/claude", "tier": "native", "hint": "..."},
    {"agent_id": "codex_cli", "available": false, "path": "", "tier": "best_effort", "hint": "..."},
    {"agent_id": "gemini_cli", "available": true, "path": "/usr/local/bin/gemini", "tier": "best_effort", "hint": "..."},
    {"agent_id": "pi_cli", "available": false, "path": "", "tier": "best_effort", "hint": "..."}
  ]
}
```

### `GET /health`

Health check endpoint.

**Response (200):**
```json
{
  "engine": "healthy",
  "n8n": "healthy",
  "sqlite": "healthy",
  "chromadb": "healthy",
  "ollama": "not_configured",
  "coding_agent": "available",
  "uptime_seconds": 3600
}
```

### `POST /chat`

Alternative to WebSocket for chat (for simple request/response).

**Request:**
```json
{
  "message": "What happened with BUG-1234?"
}
```

**Response (200):**
```json
{
  "response": "BUG-1234 is an NPE in PaymentService. I staged a fix 5 minutes ago -- see card #card_001 in your feed. The fix adds null-safety checks for customer ID. The PR is ready for your approval.",
  "referenced_cards": ["card_001"],
  "referenced_events": ["evt_a1b2c3d4"]
}
```

### `POST /cards/:card_id/bookmark`

Bookmark a card for quick access.

**Response (200):**
```json
{
  "card_id": "card_001",
  "bookmarked_at": "2026-03-15T10:00:00Z"
}
```

### `DELETE /cards/:card_id/bookmark`

Remove bookmark from a card.

**Response (200):**
```json
{
  "card_id": "card_001",
  "bookmarked_at": null
}
```

### `POST /trace`

Run a Coherence entity search.

**Request:**
```json
{
  "query": "BUG-1234",
  "space_id": null,
  "fuzzy": false
}
```

**Response (200):**
```json
{
  "trace_id": "trace_001",
  "query": "BUG-1234",
  "clusters": [
    {
      "cluster_id": "cluster_001",
      "entities": [
        {"type": "ticket", "id": "BUG-1234", "name": "NPE in PaymentService"}
      ],
      "cards": ["card_001", "card_005"],
      "chapters": [
        {"title": "Created", "start": "2026-02-20T10:00:00Z"},
        {"title": "Code Review", "start": "2026-02-21T14:00:00Z"}
      ],
      "narrative": null
    }
  ],
  "search_metadata": {
    "semantic_hits": 5,
    "fuzzy_hits": 2,
    "entity_hits": 1,
    "total_cards": 6
  }
}
```

### `GET /traces`

List saved traces.

**Response (200):**
```json
{
  "traces": [
    {
      "trace_id": "trace_001",
      "query": "BUG-1234",
      "created_at": "2026-03-15T10:00:00Z",
      "cluster_count": 2,
      "card_count": 6
    }
  ]
}
```

### `POST /traces/:trace_id/clusters/:cluster_id/narrative`

Generate an AI narrative for a trace cluster. Streams via WebSocket (`trace_narrative_start`, `trace_narrative_chunk`, `trace_narrative_done`).

**Response (200):**
```json
{
  "narrative": "BUG-1234 was reported on Feb 20 when the payment service began throwing NPEs..."
}
```

### `GET /traces/:trace_id/export`

Export a trace as markdown.

**Response (200):**
```
Content-Type: text/markdown
```

### `POST /egress/execute`

Execute an outbound action via the egress system.

**Request:**
```json
{
  "platform": "gmail",
  "action_type": "reply",
  "payload": {
    "thread_id": "thread_abc",
    "body": "Thanks for the update, I'll review the PR this afternoon."
  }
}
```

**Response (200):**
```json
{
  "success": true,
  "result": {
    "message_id": "msg_xyz",
    "url": "https://mail.google.com/..."
  }
}
```

### `POST /egress/preview`

Preview an action before executing.

**Request:** Same as `/egress/execute`.

**Response (200):**
```json
{
  "preview": {
    "platform": "gmail",
    "action_type": "reply",
    "summary": "Reply to thread 'Re: PaymentService NPE' with 1 paragraph",
    "payload": {}
  }
}
```

### `GET /egress/field-suggestions`

Autocomplete known emails/usernames (from past events and team config) for the Composer's recipient fields.

**Query Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `q` | string | `""` | Search term (min 2 chars; shorter returns empty) |
| `scope` | string | `all` | `all` (all events) or `platform` (filter by `source_platform`) |
| `platform` | string | `""` | Platform id, required when `scope=platform` |
| `sources` | string | `email` | Comma-separated: `email`, `username` |

**Response (200):**
```json
{"suggestions": ["sarah@company.com", "schen"]}
```

### `GET /classification/rules`

List classification rules (manual and learned).

**Query Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `space_id` | string | `null` | Filter by space |
| `field` | string | `null` | Filter by field: `priority`, `persona` |

**Response (200):**
```json
{
  "rules": [
    {
      "rule_id": "rule_001",
      "field": "priority",
      "source": "learned",
      "condition_text": "Jira tickets from CI bot with label 'flaky-test'",
      "target_value": "LOW",
      "enabled": true
    }
  ]
}
```

### `POST /classification/rules`

Create a manual classification rule.

**Request:**
```json
{
  "field": "priority",
  "condition_text": "Calendar events with 'standup' in title",
  "target_value": "LOW",
  "space_id": "default"
}
```

### `GET /classification/corrections`

List recent classification corrections.

**Response (200):**
```json
{
  "corrections": [
    {
      "card_id": "card_042",
      "field": "priority",
      "original_value": "HIGH",
      "corrected_value": "LOW",
      "card_summary": "CI bot: flaky test in payments-service",
      "created_at": "2026-03-14T16:00:00Z"
    }
  ]
}
```

### `GET /context-rules`

List context-grouping rules (the natural-language directives that steer context association). Both `learned` (extracted from link/unlink corrections) and `manual` rules are returned. Paginated because learned rules can grow large.

**Query Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `space_id` | string | `null` | Filter by space |
| `source` | string | `null` | Filter by origin: `learned`, `manual` |
| `active` | bool | `null` | Filter by active flag |
| `limit` | int | 20 | Max rules to return |
| `offset` | int | 0 | Pagination offset |

**Response (200):**
```json
{
  "rules": [
    {
      "id": 7,
      "space_id": null,
      "rule_text": "Group Jira tickets with the Bitbucket PRs that reference them in the branch name",
      "source": "learned",
      "active": true,
      "created_at": "2026-06-20T10:00:00Z",
      "updated_at": "2026-06-20T10:00:00Z"
    }
  ],
  "total": 41,
  "limit": 20,
  "offset": 0
}
```

### `POST /context-rules`

Create a manual context rule (`source` is forced to `manual`, `active` to true).

**Request:**
```json
{
  "rule_text": "Never group cards from different customers even if the subject matches",
  "space_id": "default"
}
```

**Response (200):**
```json
{"id": 12, "status": "created"}
```

### `PUT /context-rules/{rule_id}`

Update a rule's text and/or active flag. Returns `{"status": "no_changes"}` when the body is empty, 404 if the rule does not exist.

**Request:**
```json
{"rule_text": "Updated wording", "active": false}
```

### `DELETE /context-rules/{rule_id}`

Delete a context rule (404 if not found).

### `GET /omni`

Fetch the latest (or a specific version) Omni rolling summary snapshot.

**Query Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `space_id` | string | `default` | Space to fetch summary for |
| `version` | int | `null` | Specific snapshot version (latest if omitted) |

**Response (200):**
```json
{
  "snapshot_id": "omni_001",
  "space_id": "default",
  "version": 12,
  "generated_at": "2026-04-07T17:00:00Z",
  "snapshot_type": "scheduled",
  "sections": [
    {
      "type": "attention",
      "label": "Needs Attention",
      "items": [
        {
          "text": "PR #892 has been open for 3 days with no reviewer assigned",
          "source_cards": ["card_042"],
          "platforms": ["bitbucket"],
          "priority": "HIGH",
          "pinned": false
        }
      ]
    },
    {
      "type": "recent",
      "label": "Recent",
      "items": []
    },
    {
      "type": "period",
      "label": "This Week",
      "items": []
    },
    {
      "type": "milestone",
      "label": "Milestones",
      "items": []
    }
  ],
  "stats": {
    "events_processed": 47,
    "cards_acted_on": 12,
    "compression_ratio": 0.65
  },
  "card_ids": ["card_042", "card_043"]
}
```

### `GET /omni/history`

List snapshot versions for time-slider navigation.

**Query Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `space_id` | string | `default` | Space to list history for |
| `limit` | int | `30` | Max snapshots to return |

**Response (200):**
```json
{
  "snapshots": [
    {
      "snapshot_id": "omni_001",
      "version": 12,
      "generated_at": "2026-04-07T17:00:00Z",
      "snapshot_type": "scheduled",
      "events_processed": 47
    }
  ]
}
```

### `POST /omni/resynthesis`

Manually trigger a full Omni resynthesis.

**Query Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `space_id` | string | `default` | Space to resynthesis |

**Response (200):**
```json
{
  "status": "started"
}
```

### `GET /omni/pins`

List all pinned items for a space.

**Query Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `space_id` | string | `default` | Space to list pins for |

**Response (200):**
```json
{
  "pins": [
    {
      "pin_id": "pin_001",
      "space_id": "default",
      "item_text": "PR #892 has been open for 3 days",
      "source_card_ids": ["card_042"],
      "platforms": ["bitbucket"],
      "pinned_at": "2026-04-07T10:00:00Z"
    }
  ]
}
```

### `POST /omni/pin`

Pin an Omni item so it survives compression.

**Request:**
```json
{
  "space_id": "default",
  "item_text": "PR #892 has been open for 3 days",
  "source_card_ids": ["card_042"],
  "platforms": ["bitbucket"]
}
```

**Response (200):**
```json
{
  "pin_id": "pin_001",
  "status": "pinned"
}
```

### `DELETE /omni/pin/:pin_id`

Remove a pin.

**Response (200):**
```json
{
  "status": "unpinned"
}
```

**Error Response (404):**
```json
{
  "detail": "Pin not found"
}
```

### `GET /budget`

Returns LLM cost tracking data broken down by feature and pipeline step.

**Response (200):**
```json
{
  "total_cost": 12.45,
  "monthly_cap": 50.0,
  "is_paused": false,
  "features": [
    {"name": "pulse", "cost": 5.20, "steps": [...]},
    {"name": "coherence", "cost": 2.10, "steps": [...]},
    {"name": "omni", "cost": 1.80, "steps": [...]},
    {"name": "chat", "cost": 1.50, "steps": [...]},
    {"name": "briefing", "cost": 0.85, "steps": [...]},
    {"name": "egress", "cost": 0.50, "steps": [...]},
    {"name": "system", "cost": 0.50, "steps": [...]}
  ],
  "monthly_history": [...]
}
```

### `GET /agent-budget`

Returns window-based usage budgeting for agent **inference backends** (distinct from the monthly `$` budget above). Agents bill against time-windowed usage limits that reset, so this tracks per-agent token usage in the rolling window, the native rate-limit signal scraped from the agent (e.g. Claude Code's `rate_limit_event`), and the global pause state.

**Response (200):**
```json
{
  "enabled": true,
  "agents": [
    {
      "agent_id": "claude_code",
      "window_hours": 5.0,
      "window_token_limit": 5000000,
      "pause_at_percent": 85,
      "tokens_used": 1240000,
      "calls": 38,
      "percent": 24.8,
      "rate_limit": {"status": "allowed", "resets_at": 1751212800, "rate_limit_type": "five_hour"}
    }
  ],
  "is_paused": false,
  "paused_until": null,
  "paused_reason": null,
  "paused_workflow_count": 0
}
```

### `PUT /agent-budget`

Update the agent-usage budget config. When `enabled`, an immediate evaluation runs so a just-lowered limit takes effect right away.

**Request:**
```json
{
  "enabled": true,
  "agents": {
    "claude_code": {"window_token_limit": 5000000, "window_hours": 5, "pause_at_percent": 85}
  }
}
```

**Response (200):** `{"status": "ok", "enabled": true, "agents": {...}}`

### `POST /agent-budget/resume`

Manually re-enable ingestion workflows that an agent usage-limit pause deactivated (the scheduler also auto-resumes at the window reset).

**Response (200):** `{"status": "resumed", "resumed_count": 6, "errors": []}`

### `GET /ingestion-errors`

Returns events that failed during ingestion.

**Response (200):**
```json
{
  "errors": [
    {
      "error_id": "err_...",
      "event_data": "...",
      "error_message": "Invalid event schema",
      "error_type": "validation",
      "source_platform": "slack",
      "cleared": false,
      "created_at": "2026-05-01T10:00:00Z"
    }
  ]
}
```

### `GET /events/counts`

Total event count grouped by `processing_status` (drives the Audit page's live pipeline-state breakdown).

**Response (200):**
```json
{"counts": {"completed": 412, "filtered": 87, "dead": 3, "pending": 2}, "total": 504}
```

### `GET /events/filtered`

Paginated list of events dropped by a filter rule (terminal, no card produced — purely informational, not failures). `limit` (default 25) and `offset` query params.

**Response (200):**
```json
{
  "events": [
    {
      "event_id": "evt_...",
      "timestamp": "...",
      "source_platform": "slack",
      "subject_type": "message",
      "subject_title": "...",
      "subject_url": "...",
      "actor_name": "CI Bot",
      "filter_rule": "Ignore bots",
      "created_at": "..."
    }
  ],
  "total": 87,
  "limit": 25,
  "offset": 0
}
```

### `GET /events/filtered/export`

Export filtered events as JSON, optionally limited to the last `days` (default `0` = all time). Unpaginated, with richer columns than the list endpoint.

**Response (200):**
```json
{
  "kind": "filtered_events",
  "exported_at": "2026-06-29 14:30:00",
  "days": 7,
  "since": "2026-06-22T14:30:00+00:00",
  "count": 42,
  "events": [{"event_id": "evt_...", "source_raw_event_type": "...", "subject_id": "...", "actor_email": "...", "space_id": "...", "...": "..."}]
}
```

### `GET /events/dead`

Paginated list of events that exhausted all retries (`processing_status = 'dead'`). `limit`/`offset` query params; rows include `processing_attempts`, `manual_retries`, and `last_error`.

### `POST /events/dead/retry`

Re-enqueue dead events for a fresh retry cycle (resets `processing_attempts` to 0).

**Request:**
```json
{"event_ids": ["evt_a", "evt_b"], "all": false}
```

Pass `{"all": true}` to retry every dead event.

**Response (200):** `{"retried": 2}`

### `GET /audit-log/export`

Export audit-log entries as JSON, honoring the same `step` / `success` / `search` filters as the list view plus an optional `days` window (default `0` = all time). Unpaginated.

**Response (200):**
```json
{
  "kind": "audit_log",
  "exported_at": "2026-06-29 14:30:00",
  "days": 0,
  "since": null,
  "count": 1280,
  "entries": [{"...": "..."}]
}
```

### `GET /audit/failure-summary`

One-shot count of outstanding failures, fetched once on app startup to seed the Audit/Settings red-dot indicator. The client then stays in sync via the `audit_failure` WebSocket push (no polling).

**Response (200):**
```json
{"dead_events": 3, "ingestion_errors": 1, "has_failures": true}
```

### `GET /diagnostics`

Returns system diagnostics for troubleshooting.

**Response (200):**
```json
{
  "engine_version": "...",
  "python_version": "...",
  "database": {"size_mb": 42, "migration_version": 66},
  "chromadb": {"collection_count": 3, "total_embeddings": 1200},
  "n8n": {"status": "healthy", "workflow_count": 21},
  "system": {"platform": "darwin", "memory_mb": 1024}
}
```

### `GET /tags`

List all tags. Optional `is_system` query param filters to system or user tags.

**Response (200):**
```json
{
  "tags": [
    {"tag_id": 1, "name": "spam", "color": "#EF4444", "is_system": true, "created_at": "..."},
    {"tag_id": 4, "name": "follow-up", "color": "#3B82F6", "is_system": false, "created_at": "..."}
  ]
}
```

### `POST /tags`

Create a new (user) tag. Name is lowercased and must be unique (≤50 chars). Returns 409 if a tag with that name already exists.

**Request body:**
```json
{"name": "follow-up", "color": "#3B82F6"}
```

### `PUT /tags/{tag_id}`

Update a tag's name or color. Cannot rename system tags (403).

**Request body:**
```json
{"name": "later", "color": "#10B981"}
```

### `DELETE /tags/{tag_id}`

Delete a user tag. Cannot delete system tags (403). Cascades to remove all `tag_assignments` for the tag and refreshes ChromaDB metadata for affected cards.

### `POST /tags/assign`

Assign a tag to a card, entity, or context group. Soft cap of 10 tags per target. If `create_if_missing` is true and `tag_name_or_id` is a string, a new tag is created on the fly.

**Request body:**
```json
{
  "tag_name_or_id": "follow-up",
  "target_type": "card",
  "target_id": "card_abc123",
  "create_if_missing": true
}
```

Broadcasts a `tags_changed` WebSocket event.

### `DELETE /tags/unassign`

Remove a tag assignment.

**Request body:**
```json
{"tag_id": 4, "target_type": "card", "target_id": "card_abc123"}
```

### `GET /tags/for/{target_type}/{target_id}`

List tags assigned to a specific target. `target_type` ∈ `card | entity | context`.

**Response (200):**
```json
{
  "tags": [
    {"tag_id": 4, "tag_name": "follow-up", "color": "#3B82F6", "is_system": false, "assigned_by": "user"}
  ]
}
```

### `GET /processing-rules`

List all processing rules, optionally filtered by `space_id` (includes global rules with `space_id = NULL`).

**Response (200):**
```json
{
  "rules": [
    {
      "id": 1,
      "name": "Auto-archive bot PR notifications",
      "description": "...",
      "space_id": null,
      "enabled": true,
      "position": 0,
      "condition": {...},
      "actions": [{"type": "archive"}],
      "rate_limit": 0,
      "cooldown_secs": 0,
      "max_daily": 0,
      "last_fired_at": "...",
      "fire_count": 12,
      "error_count": 0,
      "last_error": null,
      "created_at": "...",
      "updated_at": "..."
    }
  ]
}
```

### `POST /processing-rules`

Create a new processing rule. Regex patterns in conditions are validated (length ≤500, must compile). Position is auto-assigned to MAX+1.

### `GET /processing-rules/{rule_id}`

Get a single rule.

### `PUT /processing-rules/{rule_id}`

Update fields on a rule. Re-enabling a rule resets `error_count` to 0.

### `DELETE /processing-rules/{rule_id}`

Delete a rule. Firings cascade.

### `PUT /processing-rules/{rule_id}/toggle`

Toggle `enabled` state. Re-enabling resets `error_count`.

### `PUT /processing-rules/reorder`

Bulk-update rule positions.

**Request body:**
```json
{"order": [3, 1, 4, 2]}
```

### `POST /processing-rules/preview-matches`

Count recent (last 7 days, up to 500) cards matching a candidate condition without saving the rule.

**Request body:**
```json
{"condition": {...}}
```

**Response (200):**
```json
{
  "match_count": 8,
  "sample_cards": [{"card_id": "...", "header": "...", "priority": "LOW", "persona": "OPS", "status": "pending"}],
  "period": "last 7 days",
  "scanned": 500,
  "skipped": 2
}
```

### `GET /processing-rules/{rule_id}/history`

Recent firings for a rule (default 20).

**Response (200):**
```json
{
  "rule_id": 1,
  "firings": [
    {
      "id": 42,
      "card_id": "card_...",
      "entity_id": "...",
      "event_id": "...",
      "fired_at": "...",
      "actions": [...],
      "results": [...],
      "error": null
    }
  ]
}
```

### `GET /processing-rules/firings`

Cross-rule firing log powering Settings → Rules → Activity (mirrors the audit-log endpoint). Each row is enriched (rule name, card header, platform) via LEFT JOINs so pruned references degrade to NULL. `outcome` is **derived** (not stored) from `results_json`/`error`.

**Query Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `rule_id` | int | `null` | Filter to a single rule |
| `outcome` | string | `null` | `success`, `error`, `skipped` |
| `search` | string | `null` | Matches rule name, card id/header, entity/event id, or error |
| `limit` | int | 50 | Max entries |
| `offset` | int | 0 | Pagination offset |

**Response (200):**
```json
{
  "entries": [
    {
      "id": 42,
      "rule_id": 1,
      "rule_name": "Auto-archive bot PR notifications",
      "fired_at": "2026-06-28T09:00:00Z",
      "card_id": "card_...",
      "card_header": "Dependabot bumped lodash",
      "status": "archived",
      "priority": "LOW",
      "entity_id": "...",
      "event_id": "...",
      "platform": "github",
      "outcome": "success",
      "action_types": ["archive"],
      "results": [{"type": "archive", "success": true}],
      "error": null,
      "skip_reason": null
    }
  ],
  "total": 128,
  "limit": 50,
  "offset": 0
}
```

### `GET /processing-rules/metadata-fields`

Discover `content_metadata` keys and sample values for a `platform` (required query param) so the rule builder can suggest metadata conditions. Scans the 100 most recent events for that platform.

**Response (200):**
```json
{"keys": {"jira_project": ["PAYMENTS", "PLATFORM"], "jira_labels": ["backend", "production"]}}
```

### `GET /processing-rules/field-options`

Distinct values observed in the events table for fields that support dropdown selection in the rule builder (platforms, raw event types, subject types, plus the persona/priority/category enums).

### `GET /processing-rules/settings`

Engine-wide processing-rules settings (currently just `auto_disable_threshold`).

### `PUT /processing-rules/settings`

Update engine-wide processing-rules settings. `auto_disable_threshold` is clamped to `[1, 100]`.

### `GET /metadata`

List metadata entries for a space. Values are JSON; the API does not interpret them.

**Query params:** `space_id` (default `default`), `prefix` (optional key prefix filter).

**Response (200):**
```json
{
  "items": [
    {"key": "ui.feed.density", "value": "compact", "space_id": "default"}
  ]
}
```

### `GET /metadata/{key}`

Get a single metadata value. Returns `{"value": null}` if the key is unset (200, not 404). Key supports `/` (path-style).

### `PUT /metadata/{key}`

Upsert a metadata value.

**Request body:**
```json
{"value": <any JSON>, "space_id": "default"}
```

### `DELETE /metadata/{key}`

Delete a metadata entry. `space_id` is a query param (default `default`).

## 4. Laya Engine <-> Tauri UI (WebSocket)

### Connection

```
WS ws://localhost:8420/ws
```

The WebSocket connection is established when the Tauri app launches and maintained throughout the session. Auto-reconnect on disconnect with exponential backoff.

### Engine -> UI Messages

**`card_created`** -- New Action Card available in the feed.
```json
{
  "type": "card_created",
  "card_id": "card_001",
  "payload": {
    "priority": "CRITICAL",
    "persona": "ENGINEER",
    "header": "NPE in PaymentService -- Fix Staged",
    "summary": "Null customer ID causes crash in payment flow.",
    "status": "pending",
    "has_workspace": true
  }
}
```

**`card_updated`** -- Card status or content changed.
```json
{
  "type": "card_updated",
  "card_id": "card_001",
  "payload": {
    "status": "executing",
    "result_url": "https://bitbucket.org/team/payments-service/pull-requests/903"
  }
}
```

**`agent_progress`** -- Streaming update from a coding agent session.
```json
{
  "type": "agent_progress",
  "card_id": "card_001",
  "session_id": "sess_001",
  "payload": {
    "event_type": "file_read",
    "content": {"file": "PaymentService.java", "lines": "130-155"},
    "timestamp": "2026-02-22T14:31:20Z"
  }
}
```

**`approval_request`** -- Agent needs user input.
```json
{
  "type": "approval_request",
  "card_id": "card_001",
  "session_id": "sess_001",
  "payload": {
    "message": "I found the root cause. I need to modify 3 files. Should I proceed?",
    "files": ["PaymentService.java", "CustomerDAO.java", "PaymentServiceTest.java"],
    "options": ["approve", "deny", "edit_scope"]
  }
}
```

**`agent_completed`** -- Agent session finished.
```json
{
  "type": "agent_completed",
  "card_id": "card_001",
  "session_id": "sess_001",
  "payload": {
    "findings": {},
    "staged_output": {}
  }
}
```

**`briefing_ready`** -- Daily briefing generated.
```json
{
  "type": "briefing_ready",
  "card_id": "card_briefing_20260223",
  "payload": {
    "header": "Morning Briefing -- Feb 23, 2026",
    "summary": "3 overnight events, 4 pending cards, 2 meetings today"
  }
}
```

**`health_status`** -- System health update.
```json
{
  "type": "health_status",
  "payload": {
    "engine": "healthy",
    "n8n": "healthy",
    "overall": "healthy"
  }
}
```

**`omni_updated`** -- Omni summary resynthesized (new snapshot available).
```json
{
  "type": "omni_updated",
  "payload": {
    "space_id": "default",
    "version": 13,
    "snapshot_type": "scheduled"
  }
}
```

**`trace_narrative_start`** -- Coherence narrative generation started.
```json
{
  "type": "trace_narrative_start",
  "trace_id": "trace_001",
  "cluster_id": "cluster_001"
}
```

**`trace_narrative_chunk`** -- Streaming narrative content.
```json
{
  "type": "trace_narrative_chunk",
  "trace_id": "trace_001",
  "cluster_id": "cluster_001",
  "payload": {
    "text": "BUG-1234 was reported on Feb 20..."
  }
}
```

**`trace_narrative_done`** -- Narrative generation complete.
```json
{
  "type": "trace_narrative_done",
  "trace_id": "trace_001",
  "cluster_id": "cluster_001"
}
```

**`audit_failure`** -- Outstanding-failure counts changed (keeps the Audit/Settings red-dot in sync without polling). `kind` identifies which source changed.
```json
{
  "type": "audit_failure",
  "payload": {
    "dead_events": 3,
    "ingestion_errors": 1,
    "kind": "dead_event"
  }
}
```

**`agent_budget_status`** -- Agent usage-limit pause/resume state changed (drives the footer agent-budget widget).
```json
{
  "type": "agent_budget_status",
  "paused": true
}
```

**`rules_changed`** -- A rule was created/updated/deleted from chat (or elsewhere); the UI reloads the affected rule list. `rule_type` ∈ `filter | classification | processing`.
```json
{
  "type": "rules_changed",
  "payload": {"rule_type": "processing"}
}
```

### UI -> Engine Messages

**`approve_action`** -- User approves an agent's request within a workspace.
```json
{
  "type": "approve_action",
  "card_id": "card_001",
  "session_id": "sess_001",
  "payload": {
    "approved": true
  }
}
```

**`deny_action`** -- User denies an agent's request.
```json
{
  "type": "deny_action",
  "card_id": "card_001",
  "session_id": "sess_001",
  "payload": {
    "reason": "Only modify PaymentService.java, skip the test file for now"
  }
}
```

**`user_input`** -- User sends a text response to the agent.
```json
{
  "type": "user_input",
  "card_id": "card_001",
  "session_id": "sess_001",
  "payload": {
    "message": "Also check if CustomerDAO has the same issue"
  }
}
```

**`session_control`** -- User pauses, resumes, or cancels an agent session.
```json
{
  "type": "session_control",
  "card_id": "card_001",
  "session_id": "sess_001",
  "payload": {
    "action": "pause | resume | cancel"
  }
}
```

**`chat_message`** -- User sends a chat message (alternative to REST POST /chat).
```json
{
  "type": "chat_message",
  "payload": {
    "message": "What's the status of BUG-1234?"
  }
}
```
