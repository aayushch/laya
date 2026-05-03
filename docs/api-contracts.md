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

### `GET /diagnostics`

Returns system diagnostics for troubleshooting.

**Response (200):**
```json
{
  "engine_version": "...",
  "python_version": "...",
  "database": {"size_mb": 42, "migration_version": 59},
  "chromadb": {"collection_count": 3, "total_embeddings": 1200},
  "n8n": {"status": "healthy", "workflow_count": 21},
  "system": {"platform": "darwin", "memory_mb": 1024}
}
```

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
