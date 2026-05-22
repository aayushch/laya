# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""OS keychain integration for storing LLM API keys."""

import os

import structlog

log = structlog.get_logger()

SERVICE_NAME = "laya-engine"

# Map of provider names to environment variable names
KEY_ENV_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def store_api_key(provider: str, api_key: str) -> bool:
    """Store an API key in the OS keychain. Returns True on success."""
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, provider, api_key)
        # Also set in environment for current session
        if provider in KEY_ENV_MAP:
            os.environ[KEY_ENV_MAP[provider]] = api_key
        log.info("api_key_stored", provider=provider)
        return True
    except Exception as e:
        log.error("api_key_store_failed", provider=provider, error=str(e))
        return False


def get_api_key(provider: str) -> str | None:
    """Retrieve an API key from the OS keychain."""
    try:
        import keyring

        return keyring.get_password(SERVICE_NAME, provider)
    except Exception as e:
        log.warning("api_key_read_failed", provider=provider, error=str(e))
        return None


def delete_api_key(provider: str) -> bool:
    """Remove an API key from the OS keychain."""
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, provider)
        if provider in KEY_ENV_MAP and KEY_ENV_MAP[provider] in os.environ:
            del os.environ[KEY_ENV_MAP[provider]]
        log.info("api_key_deleted", provider=provider)
        return True
    except Exception:
        return False


def load_all_keys_to_env() -> dict[str, bool]:
    """Load all stored API keys into environment variables.

    Called on engine startup. LiteLLM reads keys from env vars.
    Returns dict of provider -> whether key was found.
    """
    results = {}
    for provider, env_var in KEY_ENV_MAP.items():
        key = get_api_key(provider)
        if key:
            os.environ[env_var] = key
            results[provider] = True
            log.info("api_key_loaded", provider=provider)
        else:
            results[provider] = False
    return results


def has_api_key(provider: str) -> bool:
    """Check if an API key exists without retrieving its value."""
    return get_api_key(provider) is not None


# ---------------------------------------------------------------------------
# Space-scoped API keys
# ---------------------------------------------------------------------------


def store_space_api_key(key_ref: str, api_key: str) -> bool:
    """Store a space-specific API key in the OS keychain.

    key_ref is a unique label like 'laya_anthropic_space_abc123'.
    """
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, key_ref, api_key)
        log.info("space_api_key_stored", key_ref=key_ref)
        return True
    except Exception as e:
        log.error("space_api_key_store_failed", key_ref=key_ref, error=str(e))
        return False


def get_space_api_key(key_ref: str) -> str | None:
    """Retrieve a space-specific API key from the OS keychain."""
    try:
        import keyring

        return keyring.get_password(SERVICE_NAME, key_ref)
    except Exception as e:
        log.warning("space_api_key_read_failed", key_ref=key_ref, error=str(e))
        return None


def delete_space_api_key(key_ref: str) -> bool:
    """Remove a space-specific API key from the OS keychain."""
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, key_ref)
        log.info("space_api_key_deleted", key_ref=key_ref)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# MCP bearer token
# ---------------------------------------------------------------------------

MCP_TOKEN_KEY = "laya_mcp_bearer"


def store_mcp_token(token: str) -> bool:
    """Store the MCP HTTP bearer token. Overwrites any existing token."""
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, MCP_TOKEN_KEY, token)
        log.info("mcp_token_stored")
        return True
    except Exception as e:
        log.error("mcp_token_store_failed", error=str(e))
        return False


def get_mcp_token() -> str | None:
    """Retrieve the current MCP bearer token, or None if not set."""
    try:
        import keyring

        return keyring.get_password(SERVICE_NAME, MCP_TOKEN_KEY)
    except Exception as e:
        log.warning("mcp_token_read_failed", error=str(e))
        return None


def delete_mcp_token() -> bool:
    """Remove the MCP bearer token from the keychain."""
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, MCP_TOKEN_KEY)
        log.info("mcp_token_deleted")
        return True
    except Exception:
        return False
