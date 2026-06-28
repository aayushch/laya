# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Abstract base class for egress platform adapters.

Each ``platforms/<name>.py`` defines exactly one concrete ``Platform`` subclass
and exposes it as a module-level singleton ``PLATFORM = XPlatform()``. This
mirrors the backend side (``backends/base.py`` :: ``EgressBackend``), giving the
platform layer the same interface enforcement: a subclass that forgets a required
member fails at *instantiation* (import time), not silently at call time.

A ``Platform`` is the single source of truth for everything about a platform:
its payload-shaping behavior AND its declarative data (capabilities, terminal
events, compose guidance, draft schema, …). The registry
(``laya.egress.registry``) imports these singletons and delegates to them —
registry is a thin facade, and it imports platforms, never the reverse.
"""

from __future__ import annotations

import abc

from laya.egress.models import EgressCapability

# Global fallback draft schema (relocated from registry._DEFAULT_DRAFT_SCHEMA).
# ``Platform.draft_schema`` defaults to this; the registry facade also imports it
# for the no-adapter path.
DEFAULT_DRAFT_SCHEMA: dict = {
    "name": "draft_output",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "body": {
                "type": "string",
                "description": "The drafted message body text.",
            },
        },
        "required": ["body"],
        "additionalProperties": False,
    },
}


class Platform(abc.ABC):
    """One egress platform's interface: payload-shaping behavior + declarative data.

    **Required** members are ``@abc.abstractmethod`` and enforced at instantiation.
    A subclass satisfies the abstract ``name``/``capabilities`` properties with
    plain class attributes (``name = "github"``, ``capabilities = [...]``).

    **Optional** declarative data are overridable class attributes whose defaults
    match the registry's historical getter fallbacks — a platform omits any it
    does not need and transparently gets the default.
    """

    # ---- Required (enforced at instantiation) ----------------------------
    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Canonical platform key (e.g. ``"github"``)."""

    @property
    @abc.abstractmethod
    def capabilities(self) -> list[EgressCapability]:
        """The outbound actions this platform supports (mirrors its n8n executor)."""

    @abc.abstractmethod
    def identifiers_from_event(
        self,
        action_type: str,
        event_id: str | None,
        content_metadata: dict,
        event_row: dict,
        self_emails: set[str] | None = None,
    ) -> dict:
        """Derive identifier fields deterministically from the source event.

        Returns ``{}`` when nothing can be derived (e.g. no source event)."""

    @abc.abstractmethod
    def normalize_payload(self, action_type: str, payload: dict) -> dict:
        """Coerce LLM field variants and apply platform defaults."""

    @abc.abstractmethod
    def validate_payload(self, action_type: str, payload: dict) -> list[str]:
        """Return human-readable errors for payloads the executor would reject."""

    # ---- Optional declarative data (defaults == registry getter fallbacks)
    terminal_event_types: frozenset[str] = frozenset()        # is_terminal_event
    compose_guidance: str = ""                                # get_compose_guidance
    body_field: str = "body"                                  # get_body_field
    draft_schema: dict = DEFAULT_DRAFT_SCHEMA                  # get_draft_schema
    chapter_default: str = "Update"                           # get_chapter_default
    polish_guidance: str = "General professional correspondence."  # get_polish_guidance
    source_ref_config: dict = {}                              # format_source_ref

    @property
    def platform_hint(self) -> str:                           # get_platform_hint
        """Short human phrase like ``"a professional email"``; name-dependent
        default, so platforms override with a plain ``platform_hint = "…"``."""
        return f"a {self.name} message"
