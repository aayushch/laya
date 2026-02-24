"""Shared test fixtures for Laya Engine tests."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
import pytest_asyncio

from laya.config import MIGRATIONS_DIR
from laya.models.classification import Persona, RouterOutput
from laya.models.event import LayaEvent
from laya.models.rules import RulesConfig
from laya.models.team import TeamConfig


@pytest_asyncio.fixture
async def db(tmp_path):
    """In-memory SQLite database with the initial schema applied."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")

    # Apply 001_initial.sql
    migration = MIGRATIONS_DIR / "001_initial.sql"
    sql = migration.read_text()
    await conn.executescript(sql)

    # Patch get_db to return this connection
    with patch("laya.db.sqlite._db", conn):
        with patch("laya.db.sqlite.get_db", return_value=conn):
            yield conn

    await conn.close()


@pytest_asyncio.fixture
async def db_full(tmp_path):
    """In-memory SQLite with ALL M3 migrations applied (001 + 002 + 003)."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")

    for migration_name in ["001_initial.sql", "002_entities.sql", "003_audit.sql"]:
        migration = MIGRATIONS_DIR / migration_name
        if migration.exists():
            sql = migration.read_text()
            await conn.executescript(sql)

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


# --- M3 Router fixtures ---

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
            yield mock


@pytest_asyncio.fixture
async def db_m4(tmp_path):
    """In-memory SQLite with ALL M4 migrations applied (001-004)."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")

    for migration_name in ["001_initial.sql", "002_entities.sql", "003_audit.sql", "004_workspace.sql"]:
        migration = MIGRATIONS_DIR / migration_name
        if migration.exists():
            sql = migration.read_text()
            await conn.executescript(sql)

    with patch("laya.db.sqlite._db", conn):
        with patch("laya.db.sqlite.get_db", return_value=conn):
            yield conn

    await conn.close()


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
            yield mock
