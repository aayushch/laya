# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for agent binary detection, focused on the Cursor `agent` validator
(a generic binary name that must not be confused with unrelated programs)."""

import os
import stat
from unittest.mock import patch

import pytest

from laya import config


def _script(path, body: str):
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    config._cursor_probe_cache.clear()
    yield
    config._cursor_probe_cache.clear()


class TestIsCursorAgent:
    def test_symlink_into_cursor_install_needs_no_probe(self, tmp_path):
        real = tmp_path / ".local/share/cursor-agent/versions/1/cursor-agent"
        real.parent.mkdir(parents=True)
        _script(real, "echo 2026.09.23-86fc751")
        link = tmp_path / "bin" / "agent"
        link.parent.mkdir()
        link.symlink_to(real)
        with patch("laya.config.subprocess.run") as run:
            assert config._is_cursor_agent(str(link)) is True
        run.assert_not_called()

    def test_unrelated_agent_binary_rejected(self, tmp_path):
        cand = _script(tmp_path / "agent", "echo hello")
        assert config._is_cursor_agent(cand) is False

    def test_version_probe_accepts_cursor_build_id_and_caches(self, tmp_path):
        cand = _script(tmp_path / "agent", "echo 2026.09.23-86fc751")
        assert config._is_cursor_agent(cand) is True
        with patch("laya.config.subprocess.run") as run:
            assert config._is_cursor_agent(cand) is True
        run.assert_not_called()

    def test_missing_path_is_false(self, tmp_path):
        assert config._is_cursor_agent(str(tmp_path / "nope")) is False


class TestDetectAgentPaths:
    def test_skips_invalid_first_hit_for_cursor(self, tmp_path):
        bin0 = tmp_path / "bin0"; bin0.mkdir()
        bin1 = tmp_path / "bin1"; bin1.mkdir()
        _script(bin0 / "agent", "echo hello")
        cursor = _script(bin1 / "agent", "echo 2026.09.23-86fc751")
        path = os.pathsep.join([str(bin0), str(bin1)])
        with patch("laya.config._augmented_path", return_value=path):
            found = config.detect_agent_paths()
        assert found["cursor_cli"] == cursor

    def test_other_agents_still_use_first_hit(self, tmp_path):
        bin0 = tmp_path / "bin0"; bin0.mkdir()
        claude = _script(bin0 / "claude", "echo x")
        with patch("laya.config._augmented_path", return_value=str(bin0)):
            found = config.detect_agent_paths()
        assert found["claude_code"] == claude
        assert found["cursor_cli"] == ""


class TestFillMissingAgentPaths:
    def test_fills_only_empty_keys(self):
        detected = {k: f"/det/{k}" for k in config._AGENT_BINARIES}
        with patch("laya.config.detect_agent_paths", return_value=detected) as det:
            merged, changed = config.fill_missing_agent_paths({"claude_code": "/keep", "cursor_cli": ""})
        det.assert_called_once()
        assert changed is True
        assert merged["claude_code"] == "/keep"
        assert merged["cursor_cli"] == "/det/cursor_cli"
        assert merged["pi_cli"] == "/det/pi_cli"

    def test_no_detection_when_everything_set(self):
        full = {k: f"/set/{k}" for k in config._AGENT_BINARIES}
        with patch("laya.config.detect_agent_paths") as det:
            merged, changed = config.fill_missing_agent_paths(full)
        det.assert_not_called()
        assert changed is False and merged == full

    def test_unchanged_when_nothing_detected(self):
        empty = {k: "" for k in config._AGENT_BINARIES}
        with patch("laya.config.detect_agent_paths", return_value=dict(empty)):
            merged, changed = config.fill_missing_agent_paths({})
        assert changed is False
        assert merged == {}
