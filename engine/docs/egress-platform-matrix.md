# Egress Platform Capability Matrix

> Complete reference of supported platforms, action types, required payloads,
> and n8n executor workflow details.
> Part of the [Egress Architecture](egress-architecture.md).

---

## 1. Full Capability Matrix

### Email Platforms

| Action | Gmail | Outlook | SMTP (Generic) | n8n Node |
|--------|-------|---------|-----------------|----------|
| Send new email | Yes | Yes | Yes | `n8n-nodes-base.gmail` / `microsoftOutlook` / `aiosmtplib` |
| Reply to thread | Yes (thread_id) | Yes (conversation_id) | Yes (In-Reply-To header) | Same |
| Forward email | Yes | Yes | Yes | Same |
| Send with attachments | Yes (base64) | Yes (base64) | Yes (MIME) | Same |
| CC/BCC | Yes | Yes | Yes | Same |
| Archive email | Yes (remove INBOX label) | Yes (move to Archive) | No | Same |
| Star/Flag email | Yes (add STARRED label) | Yes (set flag) | No | Same |
| Label/Categorize | Yes (add/remove labels) | Yes (categories) | No | Same |
| Mark read/unread | Yes | Yes | Via IMAP | Same |

### Jira

| Action | Supported | n8n Node | Required Payload |
|--------|-----------|----------|-----------------|
| Comment on issue | Yes | `n8n-nodes-base.jira` | `issue_key`, `comment` |
| Transition issue | Yes | `n8n-nodes-base.jira` | `issue_key`, `target_status` |
| Create issue | Yes | `n8n-nodes-base.jira` | `project`, `type`, `summary` |
| Assign issue | Yes | `n8n-nodes-base.jira` | `issue_key`, `assignee` |
| Update fields | Yes | `n8n-nodes-base.jira` | `issue_key`, `fields` (dict) |
| Add attachment | Yes | `n8n-nodes-base.httpRequest` | `issue_key`, `file` (base64) |
| Link issues | Yes | `n8n-nodes-base.jira` | `issue_key`, `link_type`, `target_key` |
| Add watcher | Yes | `n8n-nodes-base.httpRequest` | `issue_key`, `watcher_id` |

### GitHub

| Action | Supported | n8n Node | Required Payload |
|--------|-----------|----------|-----------------|
| Comment on issue | Yes | `n8n-nodes-base.github` | `owner`, `repo`, `issue_number`, `comment` |
| Close/reopen issue | Yes | `n8n-nodes-base.github` | `owner`, `repo`, `issue_number` |
| Create issue | Yes | `n8n-nodes-base.github` | `owner`, `repo`, `title`, `body` |
| Approve PR | Yes | `n8n-nodes-base.httpRequest` | `owner`, `repo`, `pr_number`, `event: "APPROVE"` |
| Request changes on PR | Yes | `n8n-nodes-base.httpRequest` | `owner`, `repo`, `pr_number`, `body`, `event: "REQUEST_CHANGES"` |
| Comment on PR | Yes | `n8n-nodes-base.github` | `owner`, `repo`, `pr_number`, `comment` |
| Comment on PR line | Yes | `n8n-nodes-base.httpRequest` | `owner`, `repo`, `pr_number`, `path`, `line`, `body` |
| Merge PR | Yes | `n8n-nodes-base.httpRequest` | `owner`, `repo`, `pr_number`, `merge_method` |
| Create PR | Yes | `n8n-nodes-base.github` | `owner`, `repo`, `title`, `body`, `head`, `base` |
| Add labels | Yes | `n8n-nodes-base.github` | `owner`, `repo`, `issue_number`, `labels` |

### Bitbucket

| Action | Supported | n8n Node | Required Payload |
|--------|-----------|----------|-----------------|
| Comment on PR | Yes | `n8n-nodes-base.httpRequest` | `workspace`, `repo`, `pr_id`, `comment` |
| Approve PR | Yes | `n8n-nodes-base.httpRequest` | `workspace`, `repo`, `pr_id` |
| Unapprove PR | Yes | `n8n-nodes-base.httpRequest` | `workspace`, `repo`, `pr_id` |
| Decline PR | Yes | `n8n-nodes-base.httpRequest` | `workspace`, `repo`, `pr_id` |
| Merge PR | Yes | `n8n-nodes-base.httpRequest` | `workspace`, `repo`, `pr_id`, `merge_strategy` |
| Create PR | Yes | `n8n-nodes-base.httpRequest` | `workspace`, `repo`, `title`, `source_branch`, `dest_branch` |
| Comment on PR line | Yes | `n8n-nodes-base.httpRequest` | `workspace`, `repo`, `pr_id`, `path`, `line`, `body` |

**Note**: Bitbucket's n8n node (`n8n-nodes-base.bitbucket`) has limited operations.
The executor workflow uses `n8n-nodes-base.httpRequest` with Bitbucket API authentication
for full control.

### Slack

| Action | Supported | n8n Node | Required Payload |
|--------|-----------|----------|-----------------|
| Send message to channel | Yes | `n8n-nodes-base.slack` | `channel`, `message` |
| Reply to thread | Yes | `n8n-nodes-base.slack` | `channel`, `thread_ts`, `message` |
| React to message | Yes | `n8n-nodes-base.slack` | `channel`, `timestamp`, `emoji` |
| Upload file | Yes | `n8n-nodes-base.slack` | `channel`, `file` (base64), `initial_comment` |
| Update message | Yes | `n8n-nodes-base.slack` | `channel`, `ts`, `message` |
| Delete message | Yes | `n8n-nodes-base.slack` | `channel`, `ts` |

### Linear

| Action | Supported | n8n Node | Required Payload |
|--------|-----------|----------|-----------------|
| Create issue | Yes | `n8n-nodes-base.httpRequest` (GraphQL) | `team_id`, `title`, `description` |
| Comment on issue | Yes | `n8n-nodes-base.httpRequest` (GraphQL) | `issue_id`, `body` |
| Update status | Yes | `n8n-nodes-base.httpRequest` (GraphQL) | `issue_id`, `state_id` |
| Assign issue | Yes | `n8n-nodes-base.httpRequest` (GraphQL) | `issue_id`, `assignee_id` |
| Archive issue | Yes | `n8n-nodes-base.httpRequest` (GraphQL) | `issue_id` |

**Note**: Linear uses GraphQL exclusively. The executor uses `httpRequest` node with
`POST https://api.linear.app/graphql`.

### Google Calendar

| Action | Supported | n8n Node | Required Payload |
|--------|-----------|----------|-----------------|
| Create event | Yes | `n8n-nodes-base.googleCalendar` | `title`, `start`, `end` |
| Update event | Yes | `n8n-nodes-base.googleCalendar` | `event_id`, `fields` |
| Delete event | Yes | `n8n-nodes-base.googleCalendar` | `event_id` |
| RSVP / respond | Yes | `n8n-nodes-base.httpRequest` | `event_id`, `response` |

### Outlook Calendar

| Action | Supported | n8n Node | Required Payload |
|--------|-----------|----------|-----------------|
| Create event | Yes | `n8n-nodes-base.microsoftOutlook` | `title`, `start`, `end` |
| Update event | Yes | `n8n-nodes-base.microsoftOutlook` | `event_id`, `fields` |
| Delete event | Yes | `n8n-nodes-base.microsoftOutlook` | `event_id` |

### Notion

| Action | Supported | n8n Node | Required Payload |
|--------|-----------|----------|-----------------|
| Create page | Yes | `n8n-nodes-base.notion` | `database_id`, `title`, `properties` |
| Update page | Yes | `n8n-nodes-base.notion` | `page_id`, `properties` |

**Note**: Notion uses Internal Integration Tokens. The executor normalizes property types (title, rich_text, number, select, multi_select, date, checkbox, url, email, phone) from the payload.

---

## 2. n8n Executor Workflow Specifications

### 2.1 Workflow Pattern

All executor workflows follow the same architecture:

```
[Webhook]           Receives POST from egress n8n backend
    |
[Validate]          Check required fields present
    |
[Route Action]      Switch node routes by action_type
    |
[Execute]           Platform-specific node (Jira, Gmail, etc.)
    |
[Respond Success]   Return {success: true, result: {...}}
    |
[Respond Error]     Return {success: false, error: "..."}
```

### 2.2 Standard Payload Format

All executor workflows receive this payload from the egress module:

```json
{
  "action_id": "act_abc123",
  "source_event_id": "evt_jira_PROJ-123_...",
  "target": {
    "platform": "jira",
    "connection_id": null
  },
  "action_type": "comment",
  "payload": {
    "issue_key": "PROJ-123",
    "comment": "This has been fixed in PR #456."
  },
  "event_actor_email": "sarah@company.com",
  "event_actor_name": "Sarah Smith",
  "event_subject": "Fix payment timeout",
  "event_platform": "jira"
}
```

### 2.3 Jira Executor Workflow (`jira-executor.json`)

**NEW — needs to be built.**

Routes:

| action_type | Jira Node Operation | Key Parameters |
|-------------|--------------------|----------------|
| `comment` | Add Comment | `issueKey`, `comment` |
| `transition` | Transition Issue | `issueKey`, `transitionId` (resolved from `target_status` name) |
| `create_issue` | Create Issue | `project`, `issueType`, `summary`, `description` |
| `assign` | Update Issue | `issueKey`, `updateFields.assignee` |
| `update_fields` | Update Issue | `issueKey`, `updateFields` (generic) |

**Transition resolution**: The workflow first calls `GET /rest/api/3/issue/{key}/transitions`
to get available transitions, then matches `target_status` by name to find the correct
`transitionId`. This handles Jira's project-specific workflow configurations.

### 2.4 Slack Executor Workflow (`slack-executor.json`)

**NEW — needs to be built.**

Routes:

| action_type | Slack Node Operation | Key Parameters |
|-------------|---------------------|----------------|
| `send_message` | Send Message | `channel`, `text` |
| `reply_thread` | Send Message (with thread_ts) | `channel`, `thread_ts`, `text` |
| `react` | Add Reaction | `channel`, `timestamp`, `name` (emoji name) |
| `upload_file` | Upload File | `channel`, `file`, `filename`, `initial_comment` |
| `update_message` | Update Message | `channel`, `ts`, `text` |

### 2.5 Bitbucket Executor Workflow (`bitbucket-executor.json`)

**NEW — needs to be built.**

Since n8n's Bitbucket node is limited, this workflow uses `httpRequest` nodes with
Bitbucket API authentication:

| action_type | HTTP Method | API Endpoint |
|-------------|------------|--------------|
| `comment_pr` | POST | `/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}/comments` |
| `approve_pr` | POST | `/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}/approve` |
| `unapprove_pr` | DELETE | `/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}/approve` |
| `decline_pr` | POST | `/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}/decline` |
| `merge_pr` | POST | `/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}/merge` |
| `create_pr` | POST | `/2.0/repositories/{workspace}/{repo}/pullrequests` |

Authentication: Uses Bitbucket App Password via HTTP Basic Auth header, credentials
pulled from n8n's credential store.

### 2.6 GitHub Executor Extensions

**Existing workflow needs extensions.**

Current routes: `close_issue`, `comment` (issue comment only)

New routes to add:

| action_type | GitHub API | n8n Node |
|-------------|-----------|----------|
| `approve_pr` | POST `/repos/{owner}/{repo}/pulls/{pr}/reviews` body: `{event: "APPROVE"}` | httpRequest |
| `request_changes` | POST `/repos/{owner}/{repo}/pulls/{pr}/reviews` body: `{event: "REQUEST_CHANGES", body: "..."}` | httpRequest |
| `merge_pr` | PUT `/repos/{owner}/{repo}/pulls/{pr}/merge` body: `{merge_method: "squash"}` | httpRequest |
| `create_issue` | POST `/repos/{owner}/{repo}/issues` | github node |
| `create_pr` | POST `/repos/{owner}/{repo}/pulls` | httpRequest |
| `comment_pr` | POST `/repos/{owner}/{repo}/issues/{pr}/comments` (PRs are issues) | github node |
| `comment_pr_line` | POST `/repos/{owner}/{repo}/pulls/{pr}/comments` body: `{path, line, body}` | httpRequest |

### 2.7 Gmail Executor Extensions

**Existing workflow needs extensions.**

Current: `send` (send/reply email)

New routes to add:

| action_type | Gmail Node Operation | Notes |
|-------------|---------------------|-------|
| `forward` | Send (with original body prepended) | Rebuild subject with "Fwd:", include original |
| `archive` | Modify Labels (remove INBOX) | Uses Gmail "modify" operation |
| `star` | Modify Labels (add STARRED) | Same |
| `unstar` | Modify Labels (remove STARRED) | Same |
| `label` | Modify Labels (add label) | Requires label ID resolution |
| `mark_read` | Modify Labels (remove UNREAD) | Same |
| `send_with_attachments` | Send with attachments | Base64 encoded files in payload |

---

## 3. Metadata Requirements for Reverse Path

For each platform action, the executor needs specific identifiers that are preserved
in event `content.metadata` during ingestion:

### Email Platforms

| Metadata Key | Ingestion Source | Used By |
|-------------|------------------|---------|
| `gmail_threadId` | Gmail API response | Reply threading |
| `gmail_id` | Gmail API response | Forward, archive, star |
| `gmail_from` | Gmail API response | Reply-to address |
| `gmail_labelIds` | Gmail API response | Label management |
| `outlook_conversationId` | Outlook API | Reply threading |
| `outlook_id` | Outlook API | Archive, flag |

### Jira

| Metadata Key | Ingestion Source | Used By |
|-------------|------------------|---------|
| `jira_project` | Issue fields | Create issue (default project) |
| `jira_status` | Issue fields | Transition (current state) |
| `jira_assignee` | Issue fields | Assign |
| `jira_issue_type` | Issue fields | Create issue (default type) |
| Subject `id` (e.g., "PROJ-123") | Normalized subject | All actions |

### GitHub

| Metadata Key | Ingestion Source | Used By |
|-------------|------------------|---------|
| `github_number` | Issue/PR data | All issue/PR actions |
| `github_state` | Issue/PR data | Close/reopen |
| Subject URL | Normalized subject | Parse owner/repo |

### Bitbucket

| Metadata Key | Ingestion Source | Used By |
|-------------|------------------|---------|
| PR ID | URL parsing | All PR actions |
| Workspace/repo | URL parsing | All PR actions |
| `bitbucket_state` | PR data | Decline/merge |

### Slack

| Metadata Key | Ingestion Source | Used By |
|-------------|------------------|---------|
| `slack_channel` | Event data | Send message, reply |
| `slack_thread_ts` | Event data | Reply to thread |
| `slack_channel_type` | Event data | Permission checking |

---

## 4. SMTP/IMAP Protocol Reference

### 4.1 Email Threading via SMTP

For SMTP-based replies to maintain threading in recipients' email clients:

```
Original email headers:
  Message-ID: <abc123@gmail.com>

Reply headers (what we send):
  In-Reply-To: <abc123@gmail.com>
  References: <abc123@gmail.com>
  Subject: Re: Original Subject
```

The `In-Reply-To` and `References` headers tell email clients to group the reply
with the original message. The original `Message-ID` must be preserved during ingestion
(stored in event metadata).

### 4.2 Provider-Specific Quirks

| Provider | Quirk | Handling |
|----------|-------|---------|
| **ProtonMail** | No native IMAP/SMTP. Requires Bridge app. | Auto-detect: if domain is protonmail.com, use localhost:1025/1143 |
| **Gmail** | Deprecated "Less Secure Apps". Requires OAuth. | Redirect to Gmail OAuth flow, never use SMTP for Gmail |
| **Yahoo** | Requires "Allow apps that use less secure sign in" OR app passwords | Show instructions for generating app password |
| **iCloud** | Requires 2FA + app-specific password | Show instructions for apple.com app passwords |
| **Fastmail** | Best IMAP/SMTP standards compliance | Just works with app password |
| **Zoho** | IMAP must be explicitly enabled in settings | Show note in setup instructions |

### 4.3 Attachment Handling

For all email platforms, attachments flow as:

```
UI: User drops/selects files
  |
  v
API: Multipart form upload to /egress/attachments (temporary storage)
  |
  v
Egress: Reads file, base64 encodes
  |
  v
Backend:
  n8n (Gmail/Outlook): Passes base64 in payload, Gmail/Outlook node handles MIME
  SMTP: Builds MIMEMultipart with MIMEBase attachments directly
```

Max attachment size: 25MB per file (Gmail limit), 150MB total (Outlook limit).
SMTP limits vary by provider — warn if total exceeds 10MB (common limit).
