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


def _read_config_file(path: Path) -> dict | list | None:
    """Read and parse a JSON config file, returning None if missing."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


async def _get_db_stats() -> dict:
    """Gather table row counts and schema version."""
    db = await get_db()
    stats: dict = {"tables": {}}

    # Schema version
    try:
        async with db.execute("SELECT MAX(version) as v FROM schema_version") as cursor:
            row = await cursor.fetchone()
            stats["schema_version"] = row[0] if row else None
    except Exception:
        stats["schema_version"] = None

    # Row counts for each table
    for table in ("events", "action_cards", "action_log", "audit_log",
                  "workspace_sessions", "workspace_events", "chat_messages"):
        try:
            async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:  # noqa: S608
                row = await cursor.fetchone()
                stats["tables"][table] = row[0] if row else 0
        except Exception:
            stats["tables"][table] = None

    return stats


def _read_log_tail(max_lines: int = 500) -> str:
    """Read the last N lines from the engine log file."""
    log_file = LAYA_LOG_DIR / "engine.log"
    if not log_file.exists():
        return ""
    try:
        lines = log_file.read_text().splitlines()
        return "\n".join(lines[-max_lines:])
    except OSError:
        return ""


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
            ("team.json", LAYA_TEAM_FILE),
            ("rules.json", LAYA_RULES_FILE),
            ("repos.json", LAYA_REPOS_FILE),
        ]:
            data = _read_config_file(path)
            if data is not None:
                redacted = _redact_secrets(data)
                zf.writestr(f"config/{name}", json.dumps(redacted, indent=2))

        # 3. Log tail
        log_content = _read_log_tail()
        if log_content:
            zf.writestr("logs/engine.log", log_content)

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

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=laya-diagnostics.zip"},
    )
