# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Redact secret-bearing fields from nested payloads before they hit logs or exports.

Validation-error logs and diagnostics exports walk request/config JSON. Several
API bodies carry credentials (api_key, client_secret, connection tokens), and
Pydantic's error ``input`` can echo those values. Matching fields are replaced
so callers can keep structure without writing secrets to disk.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "***REDACTED***"

# Exact names after camelCase / hyphen normalization (clientSecret to client_secret).
_SECRET_EXACT = frozenset({
    "api_key",
    "api_keys",
    "apikey",
    "authorization",
    "auth",
    "bearer",
    "client_secret",
    "credentials",
    "access_token",
    "id_token",
    "key",
    "passwd",
    "password",
    "private_key",
    "pwd",
    "refresh_token",
    "secret",
    "session_secret",
    "token",
})

# Suffixes catch nested provider fields such as jira_api_token or slack_bot_token.
_SECRET_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_authorization",
    "_auth",
    "_credentials",
    "_key",
    "_passwd",
    "_password",
    "_secret",
    "_token",
)

_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


def _normalize_key(name: str) -> str:
    """Fold hyphen and camelCase keys onto a comparable snake_case form."""
    return _CAMEL_BOUNDARY.sub(r"\1_\2", name).replace("-", "_").lower()


def is_secret_key(name: str) -> bool:
    """True when a JSON/object key is a credential-shaped field name."""
    if not name:
        return False
    normalized = _normalize_key(name)
    if normalized in _SECRET_EXACT:
        return True
    return any(normalized.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def redact_secrets(obj: Any) -> Any:
    """Return a copy of ``obj`` with secret-keyed values replaced by REDACTED.

    A matching key redacts the entire value (dict, list, or scalar). Nested
    non-secret objects are walked so a ``credentials`` map and a sibling
    ``api_key`` string are both covered in one pass.
    """
    if isinstance(obj, Mapping):
        return {
            k: REDACTED if is_secret_key(str(k)) else redact_secrets(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_secrets(item) for item in obj]
    if isinstance(obj, tuple):
        return [redact_secrets(item) for item in obj]
    return obj


def _loc_field(loc: Any) -> str | None:
    if isinstance(loc, (list, tuple)) and loc:
        last = loc[-1]
        return last if isinstance(last, str) else None
    if isinstance(loc, str):
        return loc
    return None


def _redact_error_input(loc: Any, value: Any) -> Any:
    field = _loc_field(loc)
    if field and is_secret_key(field) and not isinstance(value, (Mapping, list, tuple)):
        return REDACTED
    return redact_secrets(value)


def _sanitize_one(err: Any) -> dict[str, Any]:
    if not isinstance(err, Mapping):
        return {"msg": str(err)}
    loc = err.get("loc")
    item: dict[str, Any] = {
        "type": err.get("type"),
        "loc": list(loc) if isinstance(loc, (list, tuple)) else loc,
        "msg": err.get("msg"),
    }
    if "input" in err:
        item["input"] = _redact_error_input(loc, err["input"])
    return item


def sanitize_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Keep loc/type/msg for debugging; redact secret-bearing ``input`` values.

    Each error is sanitized independently so a single unserializable entry
    cannot force the handler to log the raw ``exc.errors()`` list.
    """
    out: list[dict[str, Any]] = []
    for err in errors:
        try:
            out.append(_sanitize_one(err))
        except Exception:
            out.append({"msg": "validation_error"})
    return out
