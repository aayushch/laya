"""Tests for the Jev router fast path (laya/llm/jev.py)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.llm import jev
from laya.models.event import LayaEvent
from laya.pipeline.router import run_batch_router, run_router
from tests.conftest import insert_test_event

ON = {"enabled": True, "model": "typesafe/jev-1.13", "threshold": 0.7}


def answers(conf=0.9, research=0.05, persona="ENGINEER"):
    return {
        "category": {"type": "choice", "choice": "CODE", "confidence": conf},
        "persona": {"type": "choice", "choice": persona, "confidence": 0.95},
        "priority": {"type": "choice", "choice": "HIGH", "confidence": 0.8},
        "research": {"type": "noul", "noul": research},
    }


def jev_http(ans):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "answers": ans,
        "usage": {"input_tokens": 700, "cost": 0.00003},
    }
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    return patch("laya.http_client.get_client", return_value=client), client


def test_accept_confident():
    out = jev.accept(answers(), 0.7)
    assert (out.category.value, out.persona.value, out.priority.value) == (
        "CODE",
        "ENGINEER",
        "HIGH",
    )
    assert out.confidence == 0.8 and out.requires_research is False


@pytest.mark.parametrize(
    "ans",
    [
        answers(conf=0.5),  # unsure
        answers(research=0.3),  # needs (or may need) research -> LLM writes the plan
        answers(persona="WIZARD"),  # outside our enums
        {"category": {}},  # malformed
    ],
)
def test_accept_defers(ans):
    assert jev.accept(ans, 0.7) is None


def test_extract_entities(sample_event):
    ev = sample_event.model_copy(deep=True)
    ev.content.body = (
        "Same as BUG-1234 and PAY-9; fix in PR #891, cc @maya. Mail a@b.com"
    )
    vals = [(e.entity_type, e.value) for e in jev.extract_entities(ev)]
    assert vals == [
        ("ticket", "BUG-1234"),
        ("ticket", "PAY-9"),
        ("pull_request", "PR-891"),
        ("person", "@maya"),
    ]


def test_private_source(slack_event):
    ev = slack_event.model_copy(deep=True)
    ev.content.metadata["slack_channel_type"] = "dm"
    assert jev.is_private_source(ev)
    ev.content.metadata["slack_channel_type"] = "channel"
    assert not jev.is_private_source(ev)


@pytest.mark.asyncio
async def test_classify_disabled_makes_no_call(sample_event):
    p, client = jev_http(answers())
    with p, patch.object(jev, "get_jev_config", return_value={**ON, "enabled": False}):
        assert await jev.classify(sample_event, "teammate") is None
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_classify_skips_private_source_when_router_is_local(sample_event):
    ev: LayaEvent = sample_event.model_copy(deep=True)
    ev.source.platform = "gmail"
    p, client = jev_http(answers())
    with (
        p,
        patch.object(jev, "get_jev_config", return_value=ON),
        patch.object(jev, "_api_key", return_value="k"),
        patch("laya.llm.client._get_model_for_role", return_value="ollama/gemma4"),
    ):
        assert await jev.classify(ev, "teammate") is None
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_run_router_uses_jev_and_skips_llm(db, sample_event, mock_chromadb):
    await insert_test_event(db, event_id=sample_event.event_id)
    p, client = jev_http(answers())
    llm = AsyncMock()
    with (
        p,
        patch.object(jev, "get_jev_config", return_value=ON),
        patch.object(jev, "_api_key", return_value="k"),
        patch("laya.pipeline.router.llm_call", llm),
        patch(
            "laya.pipeline.feedback.query_feedback_patterns",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        out = await run_router(sample_event, "teammate")

    llm.assert_not_called()
    assert out.persona.value == "ENGINEER"
    sent = client.post.call_args.kwargs["json"]
    assert sent["model"] == "typesafe/jev-1.13" and set(sent["questions"]) == {
        "category",
        "persona",
        "priority",
        "research",
    }
    async with db.execute(
        "SELECT router_output, processed FROM events WHERE event_id = ?",
        (sample_event.event_id,),
    ) as cur:
        row = await cur.fetchone()
    assert (
        row["processed"] == 1 and json.loads(row["router_output"])["priority"] == "HIGH"
    )
    async with db.execute(
        "SELECT model_used, input_tokens FROM audit_log WHERE event_id = ? AND step = 'route'",
        (sample_event.event_id,),
    ) as cur:
        assert tuple(await cur.fetchone()) == ("typesafe/jev-1.13", 700)


@pytest.mark.asyncio
async def test_run_router_falls_back_to_llm_when_unsure(
    db, sample_event, mock_llm_router, mock_chromadb
):
    await insert_test_event(db, event_id=sample_event.event_id)
    p, _ = jev_http(answers(conf=0.4))
    with (
        p,
        patch.object(jev, "get_jev_config", return_value=ON),
        patch.object(jev, "_api_key", return_value="k"),
        patch(
            "laya.pipeline.feedback.query_feedback_patterns",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        out = await run_router(sample_event, "teammate")
    # The mocked LLM answer (conftest MOCK_ROUTER_RESPONSE) carries a research plan.
    assert out.requires_research is True and out.research_plan


@pytest.mark.asyncio
async def test_batch_router_all_settled_by_jev(
    db, sample_event, bot_event, mock_chromadb
):
    for ev in (sample_event, bot_event):
        await insert_test_event(db, event_id=ev.event_id)
    p, client = jev_http(answers())
    llm = AsyncMock()
    items = [
        {
            "event_id": ev.event_id,
            "event": ev,
            "actor_relationship": "teammate",
            "space_id": None,
        }
        for ev in (sample_event, bot_event)
    ]
    with (
        p,
        patch.object(jev, "get_jev_config", return_value=ON),
        patch.object(jev, "_api_key", return_value="k"),
        patch("laya.pipeline.router.llm_call", llm),
    ):
        out = await run_batch_router(items)
    llm.assert_not_called()
    assert set(out) == {sample_event.event_id, bot_event.event_id}
    assert client.post.call_count == 2
