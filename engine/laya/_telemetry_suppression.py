"""Suppress telemetry from bundled third-party dependencies.

Laya's Terms guarantee that Laya itself does not emit telemetry and that it
makes a best-effort attempt to disable telemetry in every bundled third-party
component that offers an opt-out. This module sets the relevant environment
variables at import time, before any telemetry-emitting library is imported,
so the defaults apply even when users have not exported these in their shell.

`setdefault` is intentional — a user who explicitly wants telemetry enabled
(e.g. a contributor debugging ChromaDB) can still opt back in by exporting the
variable before launching Laya.

Best-effort, not a guarantee: a third-party library can rename, remove, or
ignore its opt-out at any release. We also add a defensive `Settings(...)`
at each ChromaDB client construction site (see `db/chromadb_store.py`) so the
opt-out survives if the env-var contract ever changes.
"""

from __future__ import annotations

import os

_SUPPRESS_ENV = {
    # ChromaDB anonymized telemetry (also backed by PostHog).
    "ANONYMIZED_TELEMETRY": "False",
    # PostHog is used by ChromaDB under the hood; honor its own kill-switch too.
    "POSTHOG_DISABLED": "True",
    # HuggingFace Hub telemetry — emitted by transformers / sentence-transformers
    # on every model load or download.
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    # Generic `Do Not Track` signal honored by LiteLLM, HF, and many npm tools.
    "DO_NOT_TRACK": "1",
    # Scarf analytics — injected by some PyPI/npm packages to count installs
    # and runtime pings.
    "SCARF_NO_ANALYTICS": "true",
    "SCARF_ANALYTICS": "false",
}

for _key, _value in _SUPPRESS_ENV.items():
    os.environ.setdefault(_key, _value)
