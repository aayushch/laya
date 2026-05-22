# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Diagnostics export API — ZIP bundle for support and troubleshooting."""

import io
import json
import platform
import re
import zipfile
from pathlib import Path

import structlog
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from laya.config import (
    LAYA_CONFIG_FILE,
    LAYA_LOG_DIR,
    LAYA_REPOS_FILE,
    LAYA_RULES_FILE,
    LAYA_TEAM_FILE,
    MIGRATIONS_DIR,
)
from laya.db.sqlite import get_db

log = structlog.get_logger()
router = APIRouter()

# Pattern to redact API keys / secrets in config JSON
_SECRET_PATTERN = re.compile(
    r"(key|token|secret|password|api_key|apikey)",
    re.IGNORECASE,
)

# Fields containing personal data that should be stripped from team.json
_PERSONAL_FIELDS = {"name", "email"}


def _redact_secrets(obj: dict | list | str | int | float | bool | None) -> dict | list | str | int | float | bool | None:
    """Recursively redact values whose keys look like secrets."""
    if isinstance(obj, dict):
        return {
            k: "***REDACTED***" if isinstance(v, str) and _SECRET_PATTERN.search(k) else _redact_secrets(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_secrets(item) for item in obj]
    return obj


def _anonymize_team(data: dict | list | None) -> dict | list | None:
    """Strip personal details from team data, keeping only structure and roles."""
    if data is None:
        return None
    if isinstance(data, list):
        return [_anonymize_team_member(m) if isinstance(m, dict) else m for m in data]
    if isinstance(data, dict) and "members" in data:
        return {**data, "members": [_anonymize_team_member(m) if isinstance(m, dict) else m for m in data["members"]]}
    return data


def _anonymize_team_member(member: dict) -> dict:
    """Replace personal fields with placeholders, keep role/notes."""
    out = {}
    for k, v in member.items():
        if k in _PERSONAL_FIELDS:
            out[k] = f"***{k.upper()}***"
        else:
            out[k] = v
    return out


def _read_config_file(path: Path) -> dict | list | None:
    """Read and parse a JSON config file, returning None if missing."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


async def _get_db_stats() -> dict:
    """Gather table row counts, schema version, and applied migrations."""
    db = await get_db()
    stats: dict = {"tables": {}}

    # Schema version
    try:
        async with db.execute("SELECT MAX(version) as v FROM schema_version") as cursor:
            row = await cursor.fetchone()
            stats["schema_version"] = row[0] if row else None
    except Exception:
        stats["schema_version"] = None

    # Applied migrations list
    try:
        async with db.execute("SELECT version, applied_at FROM schema_version ORDER BY version") as cursor:
            rows = await cursor.fetchall()
            stats["applied_migrations"] = [{"version": r[0], "applied_at": r[1]} for r in rows]
    except Exception:
        stats["applied_migrations"] = None

    # Row counts for all known tables
    for table in (
        "events", "action_cards", "action_log", "audit_log",
        "workspace_sessions", "workspace_events", "chat_messages",
        "entities", "daily_summaries",
        "spaces", "sources", "space_api_keys", "space_repos",
        "chat_conversations",
    ):
        try:
            async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:  # noqa: S608
                row = await cursor.fetchone()
                stats["tables"][table] = row[0] if row else 0
        except Exception:
            stats["tables"][table] = None

    return stats


def _read_log_tail(log_file: Path, max_lines: int = 500) -> str:
    """Read the last N lines from a log file."""
    if not log_file.exists():
        return ""
    try:
        lines = log_file.read_text().splitlines()
        return "\n".join(lines[-max_lines:])
    except OSError:
        return ""


def _collect_log_files(zf: zipfile.ZipFile) -> None:
    """Add engine.log and any rotated backups to the ZIP."""
    main_log = LAYA_LOG_DIR / "engine.log"
    content = _read_log_tail(main_log)
    if content:
        zf.writestr("logs/engine.log", content)

    # Rotated logs: engine.log.1 through engine.log.5
    for i in range(1, 6):
        rotated = LAYA_LOG_DIR / f"engine.log.{i}"
        content = _read_log_tail(rotated, max_lines=200)
        if content:
            zf.writestr(f"logs/engine.log.{i}", content)


async def _get_spaces_summary() -> list[dict] | None:
    """Gather space config summary (no user content)."""
    db = await get_db()
    try:
        async with db.execute(
            "SELECT space_id, name, is_default, paused, router_model, stager_model, chat_model, coding_agent FROM spaces"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "space_id": r[0], "name": r[1], "is_default": bool(r[2]),
                    "paused": bool(r[3]), "router_model": r[4], "stager_model": r[5],
                    "chat_model": r[6], "coding_agent": r[7],
                }
                for r in rows
            ]
    except Exception:
        return None


async def _get_sources_summary() -> list[dict] | None:
    """Gather source registration summary."""
    db = await get_db()
    try:
        async with db.execute(
            "SELECT source_id, name, platform, space_id FROM sources"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {"source_id": r[0], "name": r[1], "platform": r[2], "space_id": r[3]}
                for r in rows
            ]
    except Exception:
        return None


@router.get("/diagnostics/export")
async def export_diagnostics():
    """Export a ZIP bundle containing system info, config, logs, and DB stats."""
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. System info
        system_info = {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "engine_version": "0.1.0",
            "machine": platform.machine(),
        }
        zf.writestr("system_info.json", json.dumps(system_info, indent=2))

        # 2. Config files (redacted)
        for name, path in [
            ("settings.json", LAYA_CONFIG_FILE),
            ("rules.json", LAYA_RULES_FILE),
            ("repos.json", LAYA_REPOS_FILE),
        ]:
            data = _read_config_file(path)
            if data is not None:
                redacted = _redact_secrets(data)
                zf.writestr(f"config/{name}", json.dumps(redacted, indent=2))

        # Team config — anonymized (no names/emails)
        team_data = _read_config_file(LAYA_TEAM_FILE)
        if team_data is not None:
            anonymized = _anonymize_team(team_data)
            zf.writestr("config/team.json", json.dumps(anonymized, indent=2))

        # 3. Log files (current + rotated backups)
        _collect_log_files(zf)

        # 4. DB stats
        db_stats = await _get_db_stats()
        zf.writestr("db_stats.json", json.dumps(db_stats, indent=2))

        # 5. Health snapshot
        try:
            from laya.api.health import health_check
            health = await health_check()
        except Exception:
            health = {"error": "health check failed"}
        zf.writestr("health.json", json.dumps(health, indent=2))

        # 6. Spaces & sources summary (topology, no user content)
        spaces = await _get_spaces_summary()
        sources = await _get_sources_summary()
        if spaces is not None or sources is not None:
            zf.writestr("spaces_summary.json", json.dumps(
                {"spaces": spaces, "sources": sources}, indent=2
            ))

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=laya-diagnostics.zip"},
    )
