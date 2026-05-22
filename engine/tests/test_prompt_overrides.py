# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for custom prompt override loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from laya.llm.prompts.overrides import (
    PROMPT_FILES,
    _overrides,
    get_override_status,
    get_prompt,
    load_custom_prompts,
)


@pytest.fixture(autouse=True)
def _clean_overrides():
    """Ensure overrides are empty before and after each test."""
    _overrides.clear()
    yield
    _overrides.clear()


@pytest.fixture()
def prompts_dir(tmp_path, monkeypatch):
    """Provide a temporary prompts directory and patch the module to use it."""
    import laya.llm.prompts.overrides as mod

    d = tmp_path / "prompts"
    d.mkdir()
    monkeypatch.setattr(mod, "LAYA_PROMPTS_DIR", d)
    return d


def test_get_prompt_returns_default_when_no_override():
    default = "You are the Router."
    assert get_prompt("router", default) == default


def test_get_prompt_returns_custom_when_override_exists(prompts_dir):
    (prompts_dir / "router.md").write_text("Custom router prompt", encoding="utf-8")
    load_custom_prompts()
    assert get_prompt("router", "default") == "Custom router prompt"


def test_load_ignores_empty_files(prompts_dir):
    (prompts_dir / "router.md").write_text("   \n  \n", encoding="utf-8")
    loaded = load_custom_prompts()
    assert "router" not in loaded
    assert get_prompt("router", "default") == "default"


def test_load_ignores_unrecognized_files(prompts_dir):
    (prompts_dir / "unknown_prompt.md").write_text("content", encoding="utf-8")
    loaded = load_custom_prompts()
    assert loaded == {}


def test_reload_picks_up_new_files(prompts_dir):
    load_custom_prompts()
    assert get_prompt("stager", "default") == "default"

    (prompts_dir / "stager.md").write_text("Custom stager", encoding="utf-8")
    loaded = load_custom_prompts()
    assert "stager" in loaded
    assert get_prompt("stager", "default") == "Custom stager"


def test_reload_removes_deleted_overrides(prompts_dir):
    f = prompts_dir / "router.md"
    f.write_text("Custom router", encoding="utf-8")
    load_custom_prompts()
    assert get_prompt("router", "default") == "Custom router"

    f.unlink()
    load_custom_prompts()
    assert get_prompt("router", "default") == "default"


def test_multiple_overrides(prompts_dir):
    (prompts_dir / "router.md").write_text("Custom router", encoding="utf-8")
    (prompts_dir / "stager.md").write_text("Custom stager", encoding="utf-8")
    loaded = load_custom_prompts()
    assert set(loaded.keys()) == {"router", "stager"}
    assert get_prompt("router", "d") == "Custom router"
    assert get_prompt("stager", "d") == "Custom stager"
    assert get_prompt("omni", "default omni") == "default omni"


def test_get_override_status(prompts_dir):
    (prompts_dir / "router.md").write_text("Custom", encoding="utf-8")
    load_custom_prompts()

    status = get_override_status()
    by_key = {s["key"]: s for s in status}
    assert by_key["router"]["overridden"] is True
    assert by_key["stager"]["overridden"] is False
    assert len(status) == len(PROMPT_FILES)


def test_no_prompts_dir_does_not_crash(tmp_path, monkeypatch):
    import laya.llm.prompts.overrides as mod

    monkeypatch.setattr(mod, "LAYA_PROMPTS_DIR", tmp_path / "nonexistent")
    loaded = load_custom_prompts()
    assert loaded == {}


def test_group_summary_timestamp_replacement(prompts_dir):
    """Custom group_summary prompts can use {timestamp} or omit it."""
    custom = "Summarize the entity.\n{timestamp}\nBe concise."
    (prompts_dir / "group_summary_initial.md").write_text(custom, encoding="utf-8")
    load_custom_prompts()

    prompt = get_prompt("group_summary_initial", "DEFAULT")
    result = prompt.replace("{timestamp}", "Current date/time: 2026-05-14 10:00:00 UTC")
    assert "Current date/time: 2026-05-14 10:00:00 UTC" in result
    assert "{timestamp}" not in result


def test_group_summary_no_timestamp_in_custom(prompts_dir):
    """Custom prompt without {timestamp} — .replace() is a no-op, no crash."""
    custom = "Just summarize the entity."
    (prompts_dir / "group_summary_initial.md").write_text(custom, encoding="utf-8")
    load_custom_prompts()

    prompt = get_prompt("group_summary_initial", "DEFAULT")
    result = prompt.replace("{timestamp}", "Current date/time: 2026-05-14 10:00:00 UTC")
    assert result == "Just summarize the entity."


def test_strips_whitespace(prompts_dir):
    (prompts_dir / "router.md").write_text("\n\n  Custom router prompt  \n\n", encoding="utf-8")
    load_custom_prompts()
    assert get_prompt("router", "default") == "Custom router prompt"


def test_ignores_subdirectories(prompts_dir):
    sub = prompts_dir / "subdir"
    sub.mkdir()
    (sub / "router.md").write_text("Should not load", encoding="utf-8")
    loaded = load_custom_prompts()
    assert loaded == {}
