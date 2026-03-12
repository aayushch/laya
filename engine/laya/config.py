"""Laya Engine configuration and directory management."""

import json
import os
from pathlib import Path

# Laya data directory
LAYA_HOME = Path.home() / ".laya"
LAYA_DATA_DIR = LAYA_HOME / "data"
LAYA_LOG_DIR = LAYA_HOME / "logs"
LAYA_CONFIG_FILE = LAYA_HOME / "settings.json"
LAYA_TEAM_FILE = LAYA_HOME / "team.json"
LAYA_RULES_FILE = LAYA_HOME / "rules.json"
LAYA_REPOS_FILE = LAYA_HOME / "repos.json"

# Engine defaults
ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 8420
N8N_URL = "http://localhost:5678"
DB_PATH = LAYA_DATA_DIR / "laya.db"
MIGRATIONS_DIR = Path(__file__).parent / "db" / "migrations"

# Default settings
DEFAULT_SETTINGS = {
    "models": {
        "router": "claude-haiku-4-5",
        "stager": "claude-sonnet-4-6",
        "chat": "claude-sonnet-4-6",
        "local": "ollama/llama3",
    },
    "coding_agent": "claude_code",
    "privacy": {
        "tier3_sources": ["gmail", "outlook", "slack_dm"],
        "tier3_processing": "cloud_with_warning",
    },
    "briefing": {
        "enabled": True,
        "time": "07:00",
        "timezone": "America/New_York",
    },
    "notifications": {
        "enabled": True,
        "min_priority": "HIGH",
    },
    "retention": {
        "card_retention_days": 90,
    },
    "feed_preferences": {
        "statusFilters": [],
        "priorityFilters": [],
        "sortBy": "newest",
        "showArchived": False,
    },
    "setup_complete": False,
    "n8n": {
        "base_url": "http://localhost:5678",
        "webhooks": {
            "jira": "jira-executor",
            "bitbucket": "bitbucket-executor",
            "slack": "slack-executor",
            "gmail": "gmail-executor",
            "calendar": "calendar-executor",
            "outlook": "outlook-email-executor",
            "outlook_calendar": "outlook-calendar-executor",
        },
    },
}


DEFAULT_TEAM: dict = {"members": []}

DEFAULT_REPOS: dict = {"repos": []}

DEFAULT_RULES: dict = {
    "rules": [
        {
            "name": "Ignore bot messages",
            "enabled": True,
            "condition": {"field": "actor.email", "operator": "contains", "value": "bot"},
            "action": "drop",
        }
    ]
}


def ensure_directories() -> None:
    """Create ~/.laya/ directory structure if it doesn't exist."""
    LAYA_HOME.mkdir(exist_ok=True)
    LAYA_DATA_DIR.mkdir(exist_ok=True)
    LAYA_LOG_DIR.mkdir(exist_ok=True)


def load_settings() -> dict:
    """Load settings from ~/.laya/settings.json, falling back to defaults."""
    if LAYA_CONFIG_FILE.exists():
        with open(LAYA_CONFIG_FILE) as f:
            user_settings = json.load(f)
        # Merge user settings over defaults
        merged = {**DEFAULT_SETTINGS}
        for key, value in user_settings.items():
            if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        return merged
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    """Persist settings to ~/.laya/settings.json."""
    ensure_directories()
    with open(LAYA_CONFIG_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def get_n8n_config() -> dict:
    """Return the n8n config block from settings, with env var override."""
    settings = load_settings()
    n8n = settings.get("n8n", DEFAULT_SETTINGS["n8n"])
    env_url = os.getenv("N8N_URL")
    if env_url:
        n8n = {**n8n, "base_url": env_url}
    return n8n


def load_team() -> dict:
    """Load team config from ~/.laya/team.json, creating default if missing."""
    ensure_directories()
    if not LAYA_TEAM_FILE.exists():
        save_team(DEFAULT_TEAM)
    with open(LAYA_TEAM_FILE) as f:
        return json.load(f)


def save_team(team: dict) -> None:
    """Persist team config to ~/.laya/team.json."""
    ensure_directories()
    with open(LAYA_TEAM_FILE, "w") as f:
        json.dump(team, f, indent=2)


def load_rules() -> dict:
    """Load rules config from ~/.laya/rules.json, creating default if missing."""
    ensure_directories()
    if not LAYA_RULES_FILE.exists():
        save_rules(DEFAULT_RULES)
    with open(LAYA_RULES_FILE) as f:
        return json.load(f)


def save_rules(rules: dict) -> None:
    """Persist rules config to ~/.laya/rules.json."""
    ensure_directories()
    with open(LAYA_RULES_FILE, "w") as f:
        json.dump(rules, f, indent=2)


def load_repos() -> dict:
    """Load repos config from ~/.laya/repos.json, creating default if missing."""
    ensure_directories()
    if not LAYA_REPOS_FILE.exists():
        save_repos(DEFAULT_REPOS)
    with open(LAYA_REPOS_FILE) as f:
        return json.load(f)


def save_repos(repos: dict) -> None:
    """Persist repos config to ~/.laya/repos.json."""
    ensure_directories()
    with open(LAYA_REPOS_FILE, "w") as f:
        json.dump(repos, f, indent=2)
