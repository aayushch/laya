# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""MCP tool-scope filtering.

The Settings → MCP UI exposes three toggles (read / write / egress). A tool is
callable over MCP only if its category is enabled. Tool→category mapping is
derived live from `laya.llm.tools.definitions` so new tools added there are
picked up automatically with no constant updates here.
"""

from __future__ import annotations

from typing import TypedDict

from laya.llm.tools.definitions import (
    egress_tool_names,
    read_tool_names,
    write_tool_names,
)


class ToolScopes(TypedDict, total=False):
    read: bool
    write: bool
    egress: bool


def enabled_tool_names(scopes: ToolScopes) -> set[str]:
    """Return the set of tool names callable for the given scope toggles."""
    out: set[str] = set()
    if scopes.get("read"):
        out |= read_tool_names()
    if scopes.get("write"):
        out |= write_tool_names()
    if scopes.get("egress"):
        out |= egress_tool_names()
    return out


def scope_of(tool_name: str) -> str | None:
    """Return 'read' | 'write' | 'egress' for a tool, or None if unknown."""
    if tool_name in read_tool_names():
        return "read"
    if tool_name in write_tool_names():
        return "write"
    if tool_name in egress_tool_names():
        return "egress"
    return None
