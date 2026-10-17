# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the map-reduce document summary driver."""

from unittest.mock import AsyncMock, patch

import pytest

from laya.documents.summary import pack_windows, sample_windows, summarize_document, summary_role
from laya.llm.client import LLMResponse


def _chunks(n: int, size: int = 500):
    return [{"text": f"Chunk {i} " + "x" * size, "page_start": i + 1, "page_end": i + 1} for i in range(n)]


def _resp(parsed: dict) -> LLMResponse:
    return LLMResponse(content="", parsed=parsed, model="m", input_tokens=1, output_tokens=1, latency_ms=1)


class TestWindows:
    def test_pack_respects_window_size(self):
        windows = pack_windows(_chunks(10, 500), 1100)
        assert len(windows) == 5
        assert windows[0]["page_range"] == "1-2"

    def test_oversized_chunk_stands_alone(self):
        windows = pack_windows([{"text": "y" * 5000, "page_start": 1, "page_end": 1}, {"text": "z", "page_start": 2, "page_end": 2}], 1000)
        assert len(windows) == 2

    def test_sample_keeps_head_tail_and_middle(self):
        windows = [{"text": str(i), "page_range": None} for i in range(100)]
        sample, note = sample_windows(windows, 24)
        assert len(sample) == 24
        assert sample[:12] == windows[:12]
        assert sample[-6:] == windows[-6:]
        assert "sample of 24 of 100" in note

    def test_sample_passthrough(self):
        windows = [{"text": "a", "page_range": None}]
        assert sample_windows(windows, 24) == (windows, None)


class TestRole:
    def test_default_role_is_stager(self):
        with patch("laya.documents.summary.load_settings", return_value={"models": {"stager": "x"}}):
            assert summary_role() == "stager"

    def test_configured_role(self):
        with patch("laya.documents.summary.load_settings", return_value={"models": {"document_summary": "y"}}):
            assert summary_role() == "document_summary"


@pytest.mark.asyncio
class TestSummarizeDocument:
    async def test_single_window_one_call(self):
        llm = AsyncMock(return_value=_resp({"summary": "## Overview\nShort doc.", "key_points": ["a"], "identifiers": []}))
        with patch("laya.documents.summary.llm_call", llm), \
             patch("laya.documents.summary.load_settings", return_value={}):
            result = await summarize_document("doc_1", _chunks(2, 100), "T", "pdf", 2, "default")
        assert llm.await_count == 1
        assert llm.await_args.kwargs["step"] == "document_summary_reduce"
        assert result["summary"].startswith("## Overview")
        assert result["model"] == "m"

    async def test_map_then_reduce(self):
        calls = []

        async def fake(**kwargs):
            calls.append(kwargs["step"])
            if kwargs["step"] == "document_summary_map":
                return _resp({"summary": "part", "key_points": ["k"], "identifiers": ["PAY-1"], "people": []})
            return _resp({"summary": "merged", "key_points": ["k"], "identifiers": ["PAY-1"]})

        with patch("laya.documents.summary.llm_call", side_effect=fake), \
             patch("laya.documents.summary.load_settings", return_value={"documents": {"summary_window_chars": 1100}}):
            result = await summarize_document("doc_1", _chunks(6, 500), "T", "pdf", 6, None)
        assert calls.count("document_summary_map") == 3
        assert calls.count("document_summary_reduce") == 1
        assert result["summary"] == "merged"
        assert result["identifiers"] == ["PAY-1"]

    async def test_hierarchical_reduce_and_sample_note(self):
        calls = []

        async def fake(**kwargs):
            calls.append(kwargs["step"])
            return _resp({"summary": "s", "key_points": [], "identifiers": [], "people": []})

        with patch("laya.documents.summary.llm_call", side_effect=fake), \
             patch("laya.documents.summary.load_settings",
                   return_value={"documents": {"summary_window_chars": 600, "summary_max_windows": 24}}):
            result = await summarize_document("doc_1", _chunks(40, 500), "T", "pdf", 40, None)
        # 40 windows sampled to 24 maps, 3 group reduces, 1 final reduce.
        assert calls.count("document_summary_map") == 24
        assert calls.count("document_summary_reduce") == 4
        assert "sample of 24 of 40" in result["summary"]
