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
        "fields": [
            {
                "key": "accessToken",
                "label": "Personal Access Token",
                "type": "password",
                "placeholder": "ghp_...",
                "help": "Generate at github.com/settings/tokens (classic or fine-grained)",
            },
        ],
    },
    "gitlab": {
        "label": "GitLab",
        "category": "development",
        "icon": "gitlab",
        "n8n_type": "gitlabApi",
        "n8n_node": "n8n-nodes-base.gitlab",
        "oauth": False,
        "fields": [
            {
                "key": "accessToken",
                "label": "Personal Access Token",
                "type": "password",
                "help": "Generate at gitlab.com/-/user_settings/personal_access_tokens",
            },
            {
                "key": "baseUrl",
                "label": "GitLab URL",
                "type": "text",
                "placeholder": "https://gitlab.com",
                "help": "Your GitLab instance URL (default: gitlab.com)",
            },
        ],
    },
    "bitbucket": {
        "label": "Bitbucket",
        "category": "development",
        "icon": "bitbucket",
        "n8n_type": "bitbucketApi",
        "n8n_node": "n8n-nodes-base.bitbucket",
        "oauth": False,
        "fields": [
            {"key": "username", "label": "Username", "type": "text"},
            {
                "key": "appPassword",
                "label": "App Password",
                "type": "password",
                "help": "Generate at bitbucket.org/account/settings/app-passwords",
            },
        ],
    },
    "linear": {
        "label": "Linear",
        "category": "development",
        "icon": "linear",
        "n8n_type": "linearApi",
        "n8n_node": "n8n-nodes-base.linear",
        "oauth": False,
        "fields": [
            {
                "key": "apiKey",
                "label": "API Key",
                "type": "password",
                "help": "Generate at linear.app/settings/api",
            },
        ],
    },
    # --- Project Management ---
    "jira": {
        "label": "Jira Cloud",
        "category": "project_management",
        "icon": "jira",
        "n8n_type": "jiraSoftwareCloudApi",
        "n8n_node": "n8n-nodes-base.jira",
        "oauth": False,
        "fields": [
            {"key": "email", "label": "Atlassian Email", "type": "text", "placeholder": "you@company.com"},
            {
                "key": "apiToken",
                "label": "API Token",
                "type": "password",
                "help": "Generate at id.atlassian.com/manage-profile/security/api-tokens",
            },
            {"key": "domain", "label": "Jira Domain", "type": "text", "placeholder": "your-company.atlassian.net"},
        ],
    },
    "notion": {
        "label": "Notion",
        "category": "project_management",
        "icon": "notion",
        "n8n_type": "notionApi",
        "n8n_node": "n8n-nodes-base.notion",
        "oauth": False,
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
        "n8n_type": "slackApi",
        "n8n_node": "n8n-nodes-base.slack",
        "oauth": False,
        "fields": [
            {
                "key": "accessToken",
                "label": "Bot Token",
                "type": "password",
                "placeholder": "xoxb-...",
                "help": "From api.slack.com/apps -> OAuth & Permissions -> Bot User OAuth Token",
            },
        ],
    },
    "discord": {
        "label": "Discord",
        "category": "communication",
        "icon": "discord",
        "n8n_type": "discordApi",
        "n8n_node": "n8n-nodes-base.discord",
        "oauth": False,
        "fields": [
            {
                "key": "botToken",
                "label": "Bot Token",
                "type": "password",
                "help": "From discord.com/developers/applications -> Bot -> Token",
            },
        ],
    },
    # --- Google (OAuth) ---
    "gmail": {
        "label": "Gmail",
        "category": "google",
        "icon": "gmail",
        "n8n_type": "gmailOAuth2Api",
        "n8n_node": "n8n-nodes-base.gmail",
        "oauth": True,
        "fields": [],
    },
    "calendar": {
        "label": "Google Calendar",
        "category": "google",
        "icon": "calendar",
        "n8n_type": "googleCalendarOAuth2Api",
        "n8n_node": "n8n-nodes-base.googleCalendar",
        "oauth": True,
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
    "google": "Google",
}

# Ordered category list for consistent UI rendering
CATEGORY_ORDER: list[str] = ["development", "project_management", "communication", "google"]


def get_platform_by_n8n_type(n8n_type: str) -> tuple[str, dict] | None:
    """Look up a platform entry by its n8n credential type string."""
    for key, platform in PLATFORMS.items():
        if platform["n8n_type"] == n8n_type:
            return key, platform
    return None
