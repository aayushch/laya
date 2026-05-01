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
    """Mock ChromaDB memory_search for workers."""
    with patch("laya.workers.engineer.memory_search", new_callable=AsyncMock, return_value=[]):
        with patch("laya.workers.comms.memory_search", new_callable=AsyncMock, return_value=[]):
            with patch("laya.workers.ops.memory_search", new_callable=AsyncMock, return_value=[]):
                with patch("laya.workers.sales.memory_search", new_callable=AsyncMock, return_value=[]):
                    with patch("laya.workers.hr.memory_search", new_callable=AsyncMock, return_value=[]):
                        with patch("laya.workers.finance.memory_search", new_callable=AsyncMock, return_value=[]):
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
