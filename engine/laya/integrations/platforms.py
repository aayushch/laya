# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Platform credential registry for n8n integrations."""

from __future__ import annotations

PLATFORMS: dict[str, dict] = {
    # --- Development ---
    "github": {
        "label": "GitHub",
        "category": "development",
        "icon": "github",
        "n8n_type": "githubApi",
        "n8n_node": "n8n-nodes-base.github",
        "oauth": False,
        "workflows": ["Laya - GitHub Ingestion", "Laya - GitHub Executor"],
        "fields": [
            {
                "key": "user",
                "label": "GitHub Username",
                "type": "text",
                "placeholder": "your-username",
            },
            {
                "key": "accessToken",
                "label": "Personal Access Token",
                "type": "password",
                "placeholder": "ghp_...",
                "help": "Generate at github.com/settings/tokens (classic or fine-grained)",
            },
        ],
        "n8n_defaults": {"server": "https://api.github.com"},
    },
    "gitlab": {
        "label": "GitLab",
        "category": "development",
        "icon": "gitlab",
        "n8n_type": "gitlabApi",
        "n8n_node": "n8n-nodes-base.gitlab",
        "oauth": False,
        "workflows": [],
        "fields": [
            {
                "key": "accessToken",
                "label": "Personal Access Token",
                "type": "password",
                "help": "Generate at gitlab.com/-/user_settings/personal_access_tokens",
            },
            {
                "key": "server",
                "label": "GitLab URL",
                "type": "text",
                "placeholder": "https://gitlab.com",
                "help": "Your GitLab instance URL (default: gitlab.com)",
            },
        ],
        "n8n_defaults": {"server": "https://gitlab.com"},
    },
    "bitbucket": {
        "label": "Bitbucket",
        "category": "development",
        "icon": "bitbucket",
        "n8n_type": "bitbucketAccessTokenApi",
        "n8n_node": "n8n-nodes-base.bitbucket",
        "oauth": False,
        "workflows": ["Laya - Bitbucket Ingestion", "Laya - Bitbucket Executor"],
        "fields": [
            {"key": "email", "label": "Atlassian Email", "type": "text", "placeholder": "you@company.com"},
            {
                "key": "accessToken",
                "label": "App Password",
                "type": "password",
                "help": "Generate at bitbucket.org/account/settings/app-passwords",
            },
        ],
    },
    "bitbucket_server": {
        "label": "Bitbucket Server",
        "category": "development",
        "icon": "bitbucket",
        # No native n8n Bitbucket Server node exists — workflows use plain HTTP
        # Request nodes with a generic header credential (Authorization: Bearer).
        # n8n_node stays empty so credential injection matches ONLY the nodes
        # that carry an httpHeaderAuth placeholder block in the template.
        "n8n_type": "httpHeaderAuth",
        "n8n_node": "",
        "oauth": False,
        "workflows": ["Laya - Bitbucket Server Ingestion", "Laya - Bitbucket Server Executor"],
        "fields": [
            {
                "key": "server",
                "label": "Server URL",
                "type": "text",
                "placeholder": "https://bitbucket.your-company.com",
                "help": "Base URL of your Bitbucket Server / Data Center instance",
            },
            {
                "key": "accessToken",
                "label": "HTTP Access Token",
                "type": "password",
                "help": "Create under Profile -> Manage account -> HTTP access tokens (Repository write for PR actions)",
            },
            {
                "key": "allowInsecureSsl",
                "label": "Skip TLS certificate verification",
                "type": "checkbox",
                "help": "Enable if your server uses a self-signed or internal-CA certificate that this machine does not trust. Connections are still encrypted, but the server's identity is not verified.",
            },
        ],
        # httpHeaderAuth's n8n schema is {name, value} — it can't store our
        # server/accessToken fields directly. Values are format-templates over
        # the connection's fields (see _provision_to_n8n).
        "n8n_credential_template": {
            "name": "Authorization",
            "value": "Bearer {accessToken}",
        },
        # Non-secret fields the cloned workflows need at runtime; written to the
        # metadata table as {platform}-config:{workflow_id} at clone time so the
        # ingestion clone can build REST URLs and the /repos?host= filter.
        "workflow_config_fields": ["server", "allowInsecureSsl"],
    },
    # --- Project Management ---
    "linear": {
        "label": "Linear",
        "category": "project_management",
        "icon": "linear",
        "n8n_type": "linearApi",
        "n8n_node": "n8n-nodes-base.linear",
        "oauth": False,
        "workflows": ["Laya - Linear Ingestion", "Laya - Linear Executor"],
        "fields": [
            {
                "key": "apiKey",
                "label": "API Key",
                "type": "password",
                "help": "Generate at linear.app/settings/api",
            },
        ],
    },
    "jira": {
        "label": "Jira Cloud",
        "category": "project_management",
        "icon": "jira",
        "n8n_type": "jiraSoftwareCloudApi",
        "n8n_node": "n8n-nodes-base.jira",
        "oauth": False,
        "workflows": ["Laya - Jira Ingestion", "Laya - Jira Executor"],
        "fields": [
            {"key": "email", "label": "Atlassian Email", "type": "text", "placeholder": "you@company.com"},
            {
                "key": "apiToken",
                "label": "API Token",
                "type": "password",
                "help": "Generate at id.atlassian.com/manage-profile/security/api-tokens",
            },
            {"key": "domain", "label": "Jira Domain", "type": "text", "placeholder": "https://your-company.atlassian.net"},
        ],
    },
    "notion": {
        "label": "Notion",
        "category": "project_management",
        "icon": "notion",
        "n8n_type": "notionApi",
        "n8n_node": "n8n-nodes-base.notion",
        "oauth": False,
        "workflows": ["Laya - Notion Ingestion", "Laya - Notion Executor"],
        "fields": [
            {
                "key": "apiKey",
                "label": "Internal Integration Token",
                "type": "password",
                "placeholder": "secret_...",
                "help": "Create an integration at notion.so/my-integrations",
            },
        ],
    },
    # --- Communication ---
    "slack": {
        "label": "Slack",
        "category": "communication",
        "icon": "slack",
        "n8n_type": "slackOAuth2Api",
        "n8n_node": "n8n-nodes-base.slack",
        "oauth": True,
        "workflows": ["Laya - Slack Ingestion", "Laya - Slack Executor"],
        "fields": [],
    },
    "discord": {
        "label": "Discord",
        "category": "communication",
        "icon": "discord",
        "n8n_type": "discordApi",
        "n8n_node": "n8n-nodes-base.discord",
        "oauth": False,
        "workflows": [],
        "fields": [
            {
                "key": "botToken",
                "label": "Bot Token",
                "type": "password",
                "help": "From discord.com/developers/applications -> Bot -> Token",
            },
        ],
    },
    # --- Email ---
    "gmail": {
        "label": "Gmail",
        "category": "email",
        "icon": "gmail",
        "n8n_type": "gmailOAuth2",
        "n8n_node": "n8n-nodes-base.gmail",
        "oauth": True,
        "workflows": ["Laya - Gmail Ingestion", "Laya - Gmail Executor"],
        "fields": [],
    },
    "outlook": {
        "label": "Outlook / Microsoft 365",
        "category": "email",
        "icon": "outlook",
        "n8n_type": "microsoftOutlookOAuth2Api",
        "n8n_node": "n8n-nodes-base.microsoftOutlook",
        "oauth": True,
        "workflows": [
            "Laya - Outlook Email Ingestion", "Laya - Outlook Email Executor",
        ],
        "fields": [],
    },
    # --- Calendar ---
    "calendar": {
        "label": "Google Calendar",
        "category": "calendar",
        "icon": "calendar",
        "n8n_type": "googleCalendarOAuth2Api",
        "n8n_node": "n8n-nodes-base.googleCalendar",
        "oauth": True,
        "workflows": ["Laya - Google Calendar Ingestion", "Laya - Google Calendar Executor"],
        "fields": [],
    },
    "outlook_calendar": {
        "label": "Outlook Calendar",
        "category": "calendar",
        "icon": "calendar",
        "n8n_type": "microsoftOutlookOAuth2Api",
        "n8n_node": "n8n-nodes-base.microsoftOutlook",
        "oauth": True,
        "workflows": ["Laya - Outlook Calendar Ingestion", "Laya - Outlook Calendar Executor"],
        "fields": [],
    },
}

# Set of n8n credential types we recognize
SUPPORTED_N8N_TYPES: set[str] = {p["n8n_type"] for p in PLATFORMS.values()}

# Category labels for UI grouping
CATEGORY_LABELS: dict[str, str] = {
    "development": "Development",
    "project_management": "Project Management",
    "communication": "Communication",
    "email": "Email",
    "calendar": "Calendar",
}

# Ordered category list for consistent UI rendering
CATEGORY_ORDER: list[str] = ["development", "project_management", "communication", "email", "calendar"]


def get_platform_by_n8n_type(n8n_type: str) -> tuple[str, dict] | None:
    """Look up a platform entry by its n8n credential type string."""
    for key, platform in PLATFORMS.items():
        if platform["n8n_type"] == n8n_type:
            return key, platform
    return None
