# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Audit-log coverage for chat-driven tool calls.

Verifies that consequential operations performed via chat LLM tool calls
(card mutations, egress execution) and tool failures are recorded in
audit_log, mirroring the existing UI/REST audit trail.
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import insert_test_card


async def _audit_rows(db, step=None):
    """Fetch audit_log rows, optionally filtered by step, with metadata parsed."""
    if step:
        rows = await db.execute_fetchall(
            "SELECT * FROM audit_log WHERE step = ?", (step,)
        )
    else:
        rows = await db.execute_fetchall("SELECT * FROM audit_log")
    out = []
    for r in rows:
        d = dict(r)
        d["metadata"] = json.loads(d["metadata"]) if d.get("metadata") else {}
        out.append(d)
    return out


@pytest.mark.asyncio
class TestChatCardMutationAudit:
    async def test_dismiss_card_via_chat_is_audited(self, db):
        """card_tools.dismiss_card writes a lifecycle audit entry tagged source=chat."""
        from laya.llm.tools import card_tools

        await insert_test_card(db, card_id="card_dismiss", status="pending")
        result = await card_tools.dismiss_card("card_dismiss")
        assert result["success"] is True

        rows = await _audit_rows(db, step="lifecycle")
        assert len(rows) == 1
        entry = rows[0]
        assert entry["card_id"] == "card_dismiss"
        assert entry["success"]
        assert entry["metadata"]["source"] == "chat"
        assert entry["metadata"]["action"] == "dismissed"
        assert entry["metadata"]["previous_status"] == "pending"

    async def test_other_card_writes_are_audited(self, db):
        """archive/done/reopen all flow through the same audited helper."""
        from laya.llm.tools import card_tools

        await insert_test_card(db, card_id="card_arch", status="pending")
        await card_tools.archive_card("card_arch")

        rows = await _audit_rows(db, step="lifecycle")
        assert len(rows) == 1
        assert rows[0]["metadata"]["action"] == "archived"

    async def test_card_not_found_is_not_audited(self, db):
        """A no-op mutation (missing card) records nothing — only executed changes."""
        from laya.llm.tools import card_tools

        result = await card_tools.dismiss_card("card_missing")
        assert "error" in result
        assert await _audit_rows(db, step="lifecycle") == []


@pytest.mark.asyncio
class TestChatEgressAudit:
    async def _prime_pending(self, action_type="send_email", platform="gmail"):
        """Insert a pending egress request and return its token."""
        import time

        from laya.egress import tool_handlers
        from laya.egress.models import EgressRequest

        request = EgressRequest(
            platform=platform, action_type=action_type, payload={}, space_id=None
        )
        token = "egr_testtoken"
        tool_handlers._pending_requests[token] = (request, time.time() + 300)
        return token

    async def test_successful_egress_is_audited(self, db, monkeypatch):
        from laya.egress import tool_handlers
        from laya.egress.models import EgressResult

        async def fake_execute(request):
            return EgressResult(success=True, result_url="https://x/1")

        monkeypatch.setattr("laya.egress.execute", fake_execute)
        token = await self._prime_pending(action_type="send_email", platform="gmail")

        out = json.loads(await tool_handlers.handle_confirm_egress({"execute_token": token}, None))
        assert out["status"] == "done"

        rows = await _audit_rows(db, step="execute")
        assert len(rows) == 1
        assert rows[0]["success"]
        assert rows[0]["metadata"]["action_type"] == "send_email"
        assert rows[0]["metadata"]["target_platform"] == "gmail"
        assert rows[0]["metadata"]["source"] == "chat"
        assert rows[0]["metadata"]["result_url"] == "https://x/1"

    async def test_failed_egress_is_audited(self, db, monkeypatch):
        from laya.egress import tool_handlers
        from laya.egress.models import EgressResult

        async def fake_execute(request):
            return EgressResult(success=False, error="boom", retryable=True)

        monkeypatch.setattr("laya.egress.execute", fake_execute)
        token = await self._prime_pending(action_type="comment", platform="jira")

        out = json.loads(await tool_handlers.handle_confirm_egress({"execute_token": token}, None))
        assert out["status"] == "failed"

        rows = await _audit_rows(db, step="execute")
        assert len(rows) == 1
        assert not rows[0]["success"]  # SQLite stores bool as 0/1
        assert rows[0]["error"] == "boom"
        assert rows[0]["metadata"]["action_type"] == "comment"


@pytest.mark.asyncio
class TestToolFailureAudit:
    async def test_handler_crash_is_audited_and_returns_error(self, db, monkeypatch):
        """A raising handler records a step=tool failure but still returns JSON to the LLM."""
        from laya.llm.tools import executor

        async def boom():
            raise RuntimeError("kaboom")

        monkeypatch.setattr(executor, "_TOOL_HANDLERS", {"boom": boom})

        result = json.loads(await executor.execute_tool("boom", {}))
        assert "error" in result  # LLM still gets a graceful error

        rows = await _audit_rows(db, step="tool")
        assert len(rows) == 1
        assert not rows[0]["success"]  # SQLite stores bool as 0/1
        assert rows[0]["metadata"]["tool"] == "boom"
        assert rows[0]["metadata"]["source"] == "chat"
        assert "kaboom" in (rows[0]["error"] or "")


@pytest.mark.asyncio
class TestRestMarkDoneAudit:
    async def test_mark_done_rest_endpoint_is_audited(self, db):
        """POST /cards/{id}/done now writes a lifecycle entry like its siblings."""
        await insert_test_card(db, card_id="card_done", status="pending")

        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/cards/card_done/done")
        assert resp.status_code == 200

        rows = await _audit_rows(db, step="lifecycle")
        assert len(rows) == 1
        assert rows[0]["card_id"] == "card_done"
        assert rows[0]["metadata"]["action"] == "done"
        assert rows[0]["metadata"]["previous_status"] == "pending"
