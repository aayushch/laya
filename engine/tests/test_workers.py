# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for Workers (ENGINEER, COMMS, OPS, SALES, HR, FINANCE) and orchestration."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.models.classification import Persona, RouterOutput
from laya.pipeline.workers import _dispatch_worker, run_workers
from laya.workers.base import WorkerResult
from tests.conftest import MOCK_COMMS_RESPONSE, MOCK_ROUTER_RESPONSE


@pytest.fixture
def mock_llm_worker():
    """Patch litellm for worker LLM calls (stager role)."""
    response_data = {
        "task_prompt": "Fix the NPE in PaymentService.java",
        "target_files": ["PaymentService.java"],
        "expected_output": "Null check added",
    }
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(response_data)
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 500
    mock_response.usage.completion_tokens = 200

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-sonnet-4-5-20250929"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    yield


@pytest.fixture
def mock_llm_comms_worker():
    """Patch litellm for COMMS worker calls."""
    response_data = {
        "draft_reply": "Hi Mike, thanks for the heads up!",
        "tone": "professional",
        "reasoning": "Standard acknowledgment of information.",
    }
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(response_data)
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 300
    mock_response.usage.completion_tokens = 100

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-sonnet-4-5-20250929"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    yield


@pytest.fixture
def mock_llm_ops_worker():
    """Patch litellm for OPS worker calls."""
    response_data = {
        "briefing": "Morning standup prep for payments team",
        "talking_points": ["NPE fix in progress", "PR-891 needs review"],
        "open_items": ["BUG-1234 investigation"],
        "reasoning": "Calendar prep based on recent events.",
    }
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(response_data)
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 400
    mock_response.usage.completion_tokens = 150

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-sonnet-4-5-20250929"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    yield


@pytest.fixture
def mock_memory():
    """Mock ChromaDB memory_search for workers. The five drafting personas now
    share one module (workers/persona.py — P7-2); ENGINEER stays separate."""
    with patch("laya.pipeline.related_context.memory_search", new_callable=AsyncMock, return_value=[]):
        with patch("laya.pipeline.related_context.memory_search", new_callable=AsyncMock, return_value=[]):
            yield


@pytest.fixture
def mock_llm_sales_worker():
    """Patch litellm for SALES worker calls."""
    response_data = {
        "draft_reply": "Hi Dana, thanks for sending the revised proposal — let's sync Thursday.",
        "tone": "warm-professional",
        "account_context": "Acme is mid-renewal; previous quote expired last week.",
        "reasoning": "Customer re-engaged; keep tone warm and schedule follow-up.",
    }
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(response_data)
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 300
    mock_response.usage.completion_tokens = 100

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-sonnet-4-5-20250929"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    yield


@pytest.fixture
def mock_llm_hr_worker():
    """Patch litellm for HR worker calls."""
    response_data = {
        "draft_reply": "Hi Jordan, approving the PTO request. Let's find coverage before you leave.",
        "tone": "supportive-professional",
        "sensitivity_note": "none",
        "reasoning": "Routine PTO approval; no confidentiality flags.",
    }
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(response_data)
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 300
    mock_response.usage.completion_tokens = 100

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-sonnet-4-5-20250929"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    yield


@pytest.fixture
def mock_llm_finance_worker():
    """Patch litellm for FINANCE worker calls."""
    response_data = {
        "briefing": "Q1 vendor invoice from Acme Cloud exceeds monthly plan by $1,240.",
        "key_figures": ["Invoice total: $4,240", "Budget: $3,000", "Overrun: +41%"],
        "open_items": ["Decide whether to approve or challenge overage."],
        "suggested_actions": ["Flag for review with FP&A before approving."],
        "reasoning": "Invoice is materially above plan; warrants scrutiny before approval.",
    }
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(response_data)
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 400
    mock_response.usage.completion_tokens = 150

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-sonnet-4-5-20250929"}}):
            with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                    yield


class TestEngineerWorker:
    @pytest.mark.asyncio
    async def test_engineer_returns_findings(
        self, db, sample_event, mock_llm_worker, mock_memory, mock_repos
    ):
        """ENGINEER worker returns WorkerResult with findings (no agent configured)."""
        router_output = RouterOutput(**MOCK_ROUTER_RESPONSE)
        # Default: coding_agent=none, so engineer returns prompt without spawning
        with patch("laya.workers.engineer.load_settings", return_value={"coding_agent": "none"}):
            result = await _dispatch_worker(Persona.ENGINEER, sample_event, router_output, card_id="card_test")

        assert result.persona == "ENGINEER"
        assert result.error is None
        assert result.findings.get("agent_prompt") is not None
        assert result.card_status == "ready"

    @pytest.mark.asyncio
    async def test_engineer_no_repo_returns_error(self, db, sample_event, mock_llm_worker, mock_memory):
        """ENGINEER returns error when no repo is configured."""
        router_output = RouterOutput(**MOCK_ROUTER_RESPONSE)
        with patch("laya.workers.engineer.load_repos", return_value={"repos": []}):
            with patch("laya.workers.engineer.load_settings", return_value={"coding_agent": "claude_code"}):
                result = await _dispatch_worker(Persona.ENGINEER, sample_event, router_output, card_id="card_test")

        assert result.persona == "ENGINEER"
        assert result.card_status == "ready"
        assert result.findings.get("agent_prompt") is not None


class TestCommsWorker:
    @pytest.mark.asyncio
    async def test_comms_returns_draft(self, db, sample_event, mock_llm_comms_worker, mock_memory):
        """COMMS worker returns drafted_output."""
        router_output = RouterOutput(**MOCK_COMMS_RESPONSE)
        result = await _dispatch_worker(Persona.COMMS, sample_event, router_output)

        assert result.persona == "COMMS"
        assert result.error is None
        assert result.drafted_output is not None

    @pytest.mark.asyncio
    async def test_comms_with_prior_findings(self, db, sample_event, mock_llm_comms_worker, mock_memory):
        """COMMS worker accepts prior_findings from ENGINEER."""
        router_output = RouterOutput(**MOCK_COMMS_RESPONSE)
        prior = {"agent_result": "NPE fixed by adding null check"}
        result = await _dispatch_worker(
            Persona.COMMS, sample_event, router_output, prior_findings=prior
        )

        assert result.persona == "COMMS"
        assert result.error is None


class TestOpsWorker:
    @pytest.mark.asyncio
    async def test_ops_returns_briefing(self, db, sample_event, mock_llm_ops_worker, mock_memory):
        """OPS worker returns structured briefing."""
        router_output = RouterOutput(**MOCK_ROUTER_RESPONSE)
        router_output.persona = Persona.OPS
        result = await _dispatch_worker(Persona.OPS, sample_event, router_output)

        assert result.persona == "OPS"
        assert result.error is None
        assert result.findings is not None


class TestWorkerOrchestration:
    @pytest.mark.asyncio
    async def test_run_workers_primary_only(self, db, sample_event, mock_llm_comms_worker, mock_memory):
        """run_workers dispatches only primary worker when no secondary."""
        router_output = RouterOutput(**MOCK_COMMS_RESPONSE)
        results = await run_workers(sample_event, router_output)

        assert len(results) == 1
        assert results[0].persona == "COMMS"

    @pytest.mark.asyncio
    async def test_run_workers_primary_and_secondary(
        self, db, sample_event, mock_llm_worker, mock_llm_comms_worker, mock_memory, mock_repos, mock_session_manager
    ):
        """run_workers dispatches primary then secondary when secondary_persona is set."""
        from laya.models.workspace import SessionStatus

        mock_session_manager["agent"].get_status.return_value = SessionStatus.COMPLETED

        router_output = RouterOutput(**MOCK_ROUTER_RESPONSE)
        router_output.secondary_persona = Persona.COMMS
        results = await run_workers(sample_event, router_output)

        assert len(results) == 2
        assert results[0].persona == "ENGINEER"
        assert results[1].persona == "COMMS"

    @pytest.mark.asyncio
    async def test_dispatch_unknown_persona(self, db, sample_event):
        """Dispatching an unknown persona returns error result."""
        router_output = RouterOutput(**MOCK_COMMS_RESPONSE)

        # Create a fake persona value
        result = await _dispatch_worker(Persona.COMMS, sample_event, router_output)
        # This succeeds — COMMS is known. Test the error path:
        assert result.persona == "COMMS"


class TestSalesWorker:
    @pytest.mark.asyncio
    async def test_sales_returns_draft(self, db, sample_event, mock_llm_sales_worker, mock_memory):
        """SALES worker returns drafted_output with account_context."""
        router_output = RouterOutput(**MOCK_COMMS_RESPONSE)
        router_output.persona = Persona.SALES
        result = await _dispatch_worker(Persona.SALES, sample_event, router_output)

        assert result.persona == "SALES"
        assert result.error is None
        assert result.drafted_output is not None
        assert "account_context" in result.drafted_output


class TestHrWorker:
    @pytest.mark.asyncio
    async def test_hr_returns_draft(self, db, sample_event, mock_llm_hr_worker, mock_memory):
        """HR worker returns drafted_output with sensitivity_note."""
        router_output = RouterOutput(**MOCK_COMMS_RESPONSE)
        router_output.persona = Persona.HR
        result = await _dispatch_worker(Persona.HR, sample_event, router_output)

        assert result.persona == "HR"
        assert result.error is None
        assert result.drafted_output is not None
        assert "sensitivity_note" in result.drafted_output


class TestFinanceWorker:
    @pytest.mark.asyncio
    async def test_finance_returns_briefing(self, db, sample_event, mock_llm_finance_worker, mock_memory):
        """FINANCE worker returns structured briefing with key_figures."""
        router_output = RouterOutput(**MOCK_ROUTER_RESPONSE)
        router_output.persona = Persona.FINANCE
        result = await _dispatch_worker(Persona.FINANCE, sample_event, router_output)

        assert result.persona == "FINANCE"
        assert result.error is None
        assert result.findings is not None
        assert "key_figures" in result.findings


class TestResolveRepoPath:
    @pytest.mark.asyncio
    async def test_resolve_repo_by_entity(self, db, mock_repos):
        """_resolve_repo_path matches entity to repo."""
        from laya.workers.engineer import resolve_repo_path

        router_output = RouterOutput(**MOCK_ROUTER_RESPONSE)
        router_output.entities = [
            MagicMock(entity_type="repo", value="payments-service"),
        ]
        path, add_dirs = await resolve_repo_path(router_output)
        assert path == "/tmp/test-repo"

    @pytest.mark.asyncio
    async def test_resolve_repo_fallback_first(self, db, mock_repos):
        """_resolve_repo_path falls back to first repo when no entity match."""
        from laya.workers.engineer import resolve_repo_path

        router_output = RouterOutput(**MOCK_ROUTER_RESPONSE)
        router_output.entities = []
        path, add_dirs = await resolve_repo_path(router_output)
        assert path == "/tmp/test-repo"

    @pytest.mark.asyncio
    async def test_resolve_repo_no_repos_returns_none(self, db):
        """_resolve_repo_path returns None when no repos configured."""
        from laya.workers.engineer import resolve_repo_path

        router_output = RouterOutput(**MOCK_ROUTER_RESPONSE)
        with patch("laya.workers.engineer.load_repos", return_value={"repos": []}):
            path, add_dirs = await resolve_repo_path(router_output)
        assert path is None


# Two registered repos where the relevant one is deliberately SECOND, so a
# resolver that only ever falls back to repos[0] is caught (issue #46).
TWO_REPOS = {"repos": [
    {"name": "coordinator-api", "path": "/tmp/coordinator-api", "platform": "github",
     "remote_id": "org/coordinator-api"},
    {"name": "monorepo-go", "path": "/tmp/monorepo-go", "platform": "github",
     "remote_id": "org/monorepo-go"},
]}


def _event_json(title: str, body: str, metadata: dict | None = None, platform: str = "jira") -> str:
    """Serialized LayaEvent as stored in events.raw_json."""
    return json.dumps({
        "event_id": "evt_x",
        "timestamp": "2026-02-22T14:30:00Z",
        "source": {"platform": platform, "raw_event_type": "issue_updated"},
        "actor": {"name": "Sarah", "email": "sarah@company.com"},
        "subject": {"type": "ticket", "id": "INV-1", "title": title},
        "content": {"body": body, "attachments": [], "metadata": metadata or {}},
    })


def _router_json(entities: list[dict]) -> str:
    """Serialized RouterOutput as stored in events.router_output."""
    return json.dumps({**MOCK_ROUTER_RESPONSE, "entities": entities})


async def _seed(db, card_id: str, event_id: str, entity_id: str, raw_json: str | None, router_output: str | None):
    """Insert a card + event and overwrite the event's persisted JSON blobs."""
    from tests.conftest import insert_test_card
    await insert_test_card(db, card_id, event_id, entity_id=entity_id)
    await db.execute(
        "UPDATE events SET raw_json = ?, router_output = ? WHERE event_id = ?",
        (raw_json if raw_json is not None else "{}", router_output, event_id),
    )
    await db.commit()


class TestResolveEntityRepoPath:
    """resolve_entity_repo_path reads the entity's persisted events (issue #46)."""

    ENTITY = "jira:ticket:INV-1"

    @pytest.fixture(autouse=True)
    def two_repos(self):
        with patch("laya.workers.engineer.load_repos", return_value=TWO_REPOS):
            yield

    @pytest.mark.asyncio
    async def test_router_repo_entity_picks_named_repo(self, db):
        from laya.workers.engineer import resolve_entity_repo_path

        await _seed(db, "c1", "e1", self.ENTITY,
                    _event_json("Invoice totals wrong", "Numbers are off."),
                    _router_json([{"entity_type": "repo", "value": "monorepo-go", "platform": "github"}]))
        path, add_dirs = await resolve_entity_repo_path(self.ENTITY)
        assert path == "/tmp/monorepo-go"
        assert add_dirs == ["/tmp/coordinator-api"]

    @pytest.mark.asyncio
    async def test_platform_metadata_repo_matches_remote_id(self, db):
        from laya.workers.engineer import resolve_entity_repo_path

        await _seed(db, "c1", "e1", self.ENTITY,
                    _event_json("PR #12", "Fix rounding", metadata={"repo": "org/monorepo-go"}, platform="github"),
                    None)
        path, _ = await resolve_entity_repo_path(self.ENTITY)
        assert path == "/tmp/monorepo-go"

    @pytest.mark.asyncio
    async def test_repo_named_only_in_body_matches_by_keyword(self, db):
        from laya.workers.engineer import resolve_entity_repo_path

        await _seed(db, "c1", "e1", self.ENTITY,
                    _event_json("Invoice totals wrong", "invoice-service in monorepo-go computes tax twice."),
                    _router_json([]))
        path, _ = await resolve_entity_repo_path(self.ENTITY)
        assert path == "/tmp/monorepo-go"

    @pytest.mark.asyncio
    async def test_signal_on_older_sibling_card_still_resolves(self, db):
        from laya.workers.engineer import resolve_entity_repo_path

        # Older card names the repo; the newest card is a terse follow-up.
        await _seed(db, "c_old", "e_old", self.ENTITY,
                    _event_json("Invoice totals wrong", "Bug is in monorepo-go."), _router_json([]))
        await db.execute("UPDATE action_cards SET created_at = '2026-01-01 00:00:00' WHERE card_id = 'c_old'")
        await db.commit()
        await _seed(db, "c_new", "e_new", self.ENTITY,
                    _event_json("Re: Invoice totals wrong", "Approved."), _router_json([]))
        path, _ = await resolve_entity_repo_path(self.ENTITY)
        assert path == "/tmp/monorepo-go"

    @pytest.mark.asyncio
    async def test_no_signals_falls_back_to_first_repo(self, db):
        from laya.workers.engineer import resolve_entity_repo_path

        # raw_json "{}" and NULL router_output must be tolerated, not raised.
        await _seed(db, "c1", "e1", self.ENTITY, "{}", None)
        path, add_dirs = await resolve_entity_repo_path(self.ENTITY)
        assert path == "/tmp/coordinator-api"
        assert add_dirs == ["/tmp/monorepo-go"]

    @pytest.mark.asyncio
    async def test_unknown_entity_falls_back_to_first_repo(self, db):
        from laya.workers.engineer import resolve_entity_repo_path

        path, _ = await resolve_entity_repo_path("jira:ticket:NOPE-1")
        assert path == "/tmp/coordinator-api"


class TestResolveRepoPathMetadata:
    """resolve_repo_path (single-event path) also honors platform metadata."""

    @pytest.mark.asyncio
    async def test_event_metadata_repo_wins_over_fallback(self, db, sample_event):
        from laya.workers.engineer import resolve_repo_path

        router_output = RouterOutput(**MOCK_ROUTER_RESPONSE)
        router_output.entities = []
        sample_event.content.metadata["repo"] = "org/monorepo-go"
        with patch("laya.workers.engineer.load_repos", return_value=TWO_REPOS):
            path, _ = await resolve_repo_path(router_output, event=sample_event)
        assert path == "/tmp/monorepo-go"
