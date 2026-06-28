# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Lock in the platform-adapter interface contract.

Each ``platforms/<name>.py`` must expose a ``Platform`` subclass with a matching
``name``, a non-empty ``capabilities`` list, and the three behavior methods. The
``Platform`` ABC enforces required members at instantiation; these tests assert
the registry wiring stays consistent and guard the dispatch quirks (smtp absent,
the three calendar keys resolving)."""

from __future__ import annotations

import abc

import pytest

from laya.egress import platforms
from laya.egress.platforms.base import Platform


def test_base_is_abstract():
    """Platform cannot be instantiated directly."""
    with pytest.raises(TypeError):
        Platform()


@pytest.mark.parametrize("key,adapter", list(platforms.registry_platforms().items()))
def test_adapter_contract(key: str, adapter: Platform):
    """Every registered adapter is a Platform, names itself correctly, declares
    capabilities, and implements the three behavior methods."""
    assert isinstance(adapter, Platform)
    assert adapter.name == key, f"{type(adapter).__name__}.name={adapter.name!r} != registry key {key!r}"
    assert adapter.capabilities, f"{key} has no capabilities"
    for method in ("identifiers_from_event", "normalize_payload", "validate_payload"):
        assert callable(getattr(adapter, method))
    # Behavior methods must be concrete (not still abstract).
    assert not getattr(type(adapter), "__abstractmethods__", frozenset())


def test_missing_method_fails_instantiation():
    """A subclass that omits a required method cannot be instantiated — proving
    the ABC actually enforces the interface (the whole point of this refactor)."""

    class Broken(Platform):
        name = "broken"
        capabilities = []
        # normalize_payload + validate_payload intentionally missing

        def identifiers_from_event(self, *a, **k):
            return {}

    assert issubclass(Broken, abc.ABC)
    with pytest.raises(TypeError):
        Broken()


def test_smtp_absent_from_dispatch():
    """SMTP egress goes through SmtpBackend, not n8n enrichment — so it must not
    resolve via for_platform (enrichment would otherwise normalize smtp payloads)."""
    assert platforms.for_platform("smtp") is None
    # …but smtp IS a registered platform (data-only adapter).
    assert "smtp" in platforms.registry_platforms()


def test_calendar_keys_resolve():
    """All three calendar dispatch keys resolve to a working adapter."""
    for key in ("calendar", "google_calendar", "outlook_calendar"):
        assert isinstance(platforms.for_platform(key), Platform)
    # google_calendar / calendar share the Google leaf; outlook_calendar is distinct.
    assert platforms.for_platform("calendar") is platforms.for_platform("google_calendar")
    assert platforms.for_platform("outlook_calendar").name == "outlook_calendar"
