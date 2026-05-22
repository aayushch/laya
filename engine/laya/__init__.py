# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Laya engine package.

Import `_telemetry_suppression` first — it sets env vars to disable telemetry
for bundled third-party libraries (ChromaDB, HuggingFace, PostHog, Scarf, ...)
before any of them can be imported elsewhere in the package.
"""

from laya import _telemetry_suppression as _telemetry_suppression  # noqa: F401
