"""Shared test fixtures for Laya Engine tests."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
import pytest_asyncio

from laya.config import MIGRATIONS_DIR
from laya.db.migrate import run_migrations
from laya.models.classification import Persona, RouterOutput
from laya.models.event import LayaEvent
from laya.models.rules import RulesConfig
from laya.models.team import TeamConfig


@pytest.fixture(autouse=True)
def _reset_http_client():
    """Reset the shared httpx client between tests to prevent state leakage."""
    import laya.http_client as hc
    old = hc._client
    hc._client = None
    yield
    hc._client = old


@pytest_asyncio.fixture
async def db(tmp_path):
    """In-memory SQLite database with ALL migrations applied."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")

    # Apply all migrations using the migration runner
    await run_migrations(conn)

    # Patch get_db to return this connection
    with patch("laya.db.sqlite._db", conn):
        with patch("laya.db.sqlite.get_db", return_value=conn):
            yield conn

    await conn.close()


@pytest.fixture
def sample_event() -> LayaEvent:
    """A sample Jira event for testing."""
    return LayaEvent(
        event_id="evt_test-001",
        timestamp=datetime(2026, 2, 22, 14, 30, 0, tzinfo=timezone.utc),
        source={"platform": "jira", "raw_event_type": "issue_assigned"},
        actor={"name": "Sarah Chen", "email": "sarah@company.com"},
        subject={"type": "ticket", "id": "BUG-1234", "title": "NPE in PaymentService"},
        content={"body": "NullPointerException", "attachments": [], "metadata": {}},
    )


@pytest.fixture
def bot_event() -> LayaEvent:
    """An event from a bot actor."""
    return LayaEvent(
        event_id="evt_test-002",
        timestamp=datetime(2026, 2, 22, 14, 31, 0, tzinfo=timezone.utc),
        source={"platform": "jira", "raw_event_type": "issue_updated"},
        actor={"name": "CI Bot", "email": "ci-bot@company.com"},
        subject={"type": "ticket", "id": "BUG-1234", "title": "NPE"},
        content={"body": "Build changed", "attachments": [], "metadata": {}},
    )


@pytest.fixture
def slack_event() -> LayaEvent:
    """A Slack event with metadata."""
    return LayaEvent(
        event_id="evt_test-003",
        timestamp=datetime(2026, 2, 22, 14, 32, 0, tzinfo=timezone.utc),
        source={"platform": "slack", "raw_event_type": "message_received"},
        actor={"name": "Mike", "email": "mike@company.com"},
        subject={"type": "thread", "id": "thread-random", "title": "Hey everyone"},
        content={
            "body": "Hey everyone!",
            "attachments": [],
            "metadata": {"slack_channel": "random", "slack_channel_type": "public"},
        },
    )


@pytest.fixture
def sample_team() -> dict:
    """Team config with 3 members."""
    return {
        "members": [
            {"name": "Sarah Chen", "email": "sarah@company.com", "role": "teammate", "notes": "Backend"},
            {"name": "Mike Torres", "email": "mike@company.com", "role": "manager", "notes": "EM"},
            {"name": "CI Bot", "email": "ci@company.com", "role": "bot", "notes": "Jenkins"},
        ]
    }


@pytest.fixture
def sample_rules() -> dict:
    """Rules config with bot filter and channel filter."""
    return {
        "rules": [
            {
                "name": "Ignore bot messages",
                "enabled": True,
                "condition": {"field": "actor.email", "operator": "contains", "value": "bot"},
                "action": "drop",
            },
            {
                "name": "Mute #random",
                "enabled": True,
                "condition": {
                    "all": [
                        {"field": "source.platform", "operator": "equals", "value": "slack"},
                        {"field": "content.metadata.slack_channel", "operator": "equals", "value": "random"},
                    ]
                },
                "action": "drop",
            },
        ]
    }


@pytest.fixture
def mock_team(sample_team):
    """Patch load_team to return sample_team."""
    with patch("laya.pipeline.ingest.load_team", return_value=sample_team):
        with patch("laya.config.load_team", return_value=sample_team):
            yield sample_team


@pytest.fixture
def mock_rules(sample_rules):
    """Patch load_rules to return sample_rules."""
    with patch("laya.pipeline.rules.load_rules", return_value=sample_rules):
        with patch("laya.config.load_rules", return_value=sample_rules):
            yield sample_rules


# --- Router fixtures ---

MOCK_ROUTER_RESPONSE = {
    "category": "CODE",
    "persona": "ENGINEER",
    "priority": "HIGH",
    "confidence": 0.92,
    "entities": [
        {"entity_type": "ticket", "value": "BUG-1234", "platform": "jira"},
        {"entity_type": "file_path", "value": "PaymentService.java", "platform": None},
    ],
    "research_plan": [
        "Check git blame for recent changes to PaymentService.java",
        "Look for similar NPE issues in the project",
        "Review the null-safety of the customer ID parameter",
    ],
    "requires_research": True,
    "secondary_persona": None,
    "reasoning": "Jira bug report about an NPE — needs code investigation.",
}

MOCK_COMMS_RESPONSE = {
    "category": "COMMS",
    "persona": "COMMS",
    "priority": "MEDIUM",
    "confidence": 0.85,
    "entities": [
        {"entity_type": "person", "value": "Mike", "platform": "slack"},
    ],
    "research_plan": [
        "Check recent conversation context in the channel",
        "Look up any related tickets or PRs mentioned",
    ],
    "requires_research": False,
    "secondary_persona": None,
    "reasoning": "Slack message in a public channel — informational.",
}


@pytest.fixture
def mock_chromadb():
    """Patch ChromaDB embed and search functions."""
    with patch("laya.pipeline.router.embed_document", new_callable=AsyncMock) as mock_embed:
        with patch("laya.pipeline.router.memory_search", new_callable=AsyncMock, return_value=[]) as mock_search:
            yield {"embed_document": mock_embed, "memory_search": mock_search}


def _make_mock_llm_response(parsed_dict: dict):
    """Create a mock LiteLLM acompletion response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(parsed_dict)
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 500
    mock_response.usage.completion_tokens = 200
    return mock_response


@pytest.fixture
def mock_llm_router():
    """Patch litellm.acompletion to return a router classification."""
    mock_resp = _make_mock_llm_response(MOCK_ROUTER_RESPONSE)
    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp) as mock:
        with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    yield mock


@pytest.fixture
def sample_router_output_engineer() -> RouterOutput:
    """RouterOutput for ENGINEER persona with requires_research=True."""
    return RouterOutput(**MOCK_ROUTER_RESPONSE)


@pytest.fixture
def sample_router_output_comms() -> RouterOutput:
    """RouterOutput for COMMS persona with requires_research=False."""
    return RouterOutput(**MOCK_COMMS_RESPONSE)


@pytest.fixture
def mock_repos():
    """Patch load_repos to return sample repo config."""
    repos = {"repos": [{"name": "payments-service", "path": "/tmp/test-repo", "platform": "github", "remote_id": "org/payments-service"}]}
    with patch("laya.workers.engineer.load_repos", return_value=repos):
        with patch("laya.config.load_repos", return_value=repos):
            yield repos


@pytest.fixture
def mock_session_manager():
    """Patch session_manager functions for worker tests."""
    mock_agent = MagicMock()
    mock_agent.stream_events = MagicMock(return_value=_empty_async_iter())
    mock_agent.get_status = MagicMock(return_value=MagicMock(value="completed"))

    with patch("laya.agents.session_manager.start_session", new_callable=AsyncMock, return_value=("sess_test123", mock_agent)) as mock_start:
        with patch("laya.agents.session_manager.complete_session", new_callable=AsyncMock) as mock_complete:
            with patch("laya.agents.session_manager.store_workspace_event", new_callable=AsyncMock) as mock_store:
                yield {
                    "start_session": mock_start,
                    "complete_session": mock_complete,
                    "store_workspace_event": mock_store,
                    "agent": mock_agent,
                }


async def _empty_async_iter():
    """Empty async iterator for mocking stream_events."""
    return
    yield  # noqa: unreachable — makes this an async generator


@pytest.fixture
def mock_llm_comms():
    """Patch litellm.acompletion to return a comms classification."""
    mock_resp = _make_mock_llm_response(MOCK_COMMS_RESPONSE)
    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp) as mock:
        with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    yield mock


# --- Stager / Emit fixtures ---

MOCK_STAGER_RESPONSE = {
    "header": "Fix NPE in PaymentService.processPayment()",
    "summary": "A NullPointerException was found in PaymentService.java when processing payments with null customer IDs. The ENGINEER worker has identified the root cause and prepared a fix.",
    "intelligence_report": [
        "NPE occurs at line 42 of PaymentService.java in processPayment() method",
        "Root cause: customer ID not validated before calling getCustomerProfile()",
        "Similar bug was fixed in OrderService.java 2 weeks ago (BUG-1198)",
        "3 tests in PaymentServiceTest.java need updating for null-safety",
        "No other callers pass null customer IDs in production code",
    ],
    "staged_output": {
        "type": "code_fix",
        "content": "Add null check for customerId in PaymentService.processPayment() before line 42.",
    },
    "suggested_actions": [
        {
            "action_id": "act_comment_jira",
            "label": "Post Jira Comment",
            "action_type": "comment",
            "target_platform": "jira",
            "payload": "{\"body\": \"Investigation complete. NPE caused by null customer ID.\"}",
        },
        {
            "action_id": "act_transition_jira",
            "label": "Move to In Progress",
            "action_type": "transition",
            "target_platform": "jira",
            "payload": "{\"transition_id\": \"21\"}",
        },
    ],
    "privacy_tier": 2,
}


@pytest.fixture
def mock_llm_stager():
    """Patch litellm.acompletion to return a stager response."""
    mock_resp = _make_mock_llm_response(MOCK_STAGER_RESPONSE)
    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp) as mock:
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-sonnet-4-5-20250929"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    yield mock


@pytest.fixture
def sample_worker_result():
    """A sample ENGINEER WorkerResult with findings and session_id."""
    from laya.workers.base import WorkerResult

    return WorkerResult(
        persona="ENGINEER",
        findings={
            "root_cause": "Null customer ID in processPayment()",
            "affected_file": "PaymentService.java",
            "line_number": 42,
        },
        drafted_output={
            "task_prompt": "Fix the NPE by adding null check",
            "target_files": ["PaymentService.java"],
        },
        session_id="sess_test_eng",
    )


@pytest.fixture
def sample_worker_result_no_session():
    """A sample COMMS WorkerResult without a coding session."""
    from laya.workers.base import WorkerResult

    return WorkerResult(
        persona="COMMS",
        findings={
            "context": "Slack discussion about design review",
            "key_points": ["Need review by Friday", "Focus on API changes"],
        },
        drafted_output={
            "draft_reply": "I'll review the API changes by Friday.",
        },
        session_id=None,
    )


# --- Helper: insert test event ---

async def insert_test_event(db, event_id="evt_test", platform="jira",
                            raw_event_type="issue_assigned",
                            subject_type="ticket", subject_id="BUG-1234",
                            subject_title="NPE in PaymentService",
                            actor_name="Sarah", actor_email="sarah@company.com",
                            content_body="NullPointerException",
                            space_id=None):
    """Insert a test event row."""
    await db.execute(
        """INSERT INTO events
           (event_id, timestamp, source_platform, source_raw_event_type,
            subject_type, subject_id, subject_title, actor_name, actor_email,
            content_body, raw_json, processed, filtered, space_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, "2026-02-22T14:30:00Z", platform, raw_event_type,
         subject_type, subject_id, subject_title, actor_name, actor_email,
         content_body, "{}", True, False, space_id),
    )
    await db.commit()


async def insert_test_card(db, card_id="card_test", event_id="evt_test",
                           priority="HIGH", persona="ENGINEER", category="CODE",
                           status="pending", header="Test Card Header",
                           summary="Test summary", space_id=None,
                           entity_id=None):
    """Insert a card with its parent event for testing."""
    # Ensure parent event exists
    existing = await db.execute_fetchall(
        "SELECT event_id FROM events WHERE event_id = ?", (event_id,)
    )
    if not existing:
        await insert_test_event(db, event_id, space_id=space_id)

    intelligence = json.dumps(["Finding 1", "Finding 2"])
    staged_output = json.dumps({"type": "code_fix", "content": "Add null check"})
    suggested_actions = json.dumps([
        {"action_id": "act_1", "label": "Post Comment", "action_type": "comment",
         "target_platform": "jira", "payload": {"body": "Fix found"}}
    ])
    await db.execute(
        """INSERT INTO action_cards
           (card_id, event_id, priority, persona, category, header, summary,
            intelligence, staged_output, suggested_actions, status, privacy_tier,
            entity_id, space_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (card_id, event_id, priority, persona, category, header, summary,
         intelligence, staged_output, suggested_actions, status, 2,
         entity_id or f"jira:ticket:BUG-1234", space_id),
    )
    await db.commit()
