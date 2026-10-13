# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""OS keychain integration for storing LLM API keys."""

import os
import secrets
import time

import structlog

log = structlog.get_logger()

SERVICE_NAME = "laya-engine"

# Short TTL read cache. keyring.get_password() is a blocking ~10-100ms syscall,
# and it sits on the egress hot path (every Jira execution, n8n API call,
# webhook-cache refresh, OAuth health sweep) — uncached, it was the only blocking
# IO on the event loop there (review §4 — P5-2). Writes/deletes invalidate the
# key, so within-process reads stay correct; cross-process changes are bounded by
# the TTL. Values (including secrets) are already resident in this process.
_KEY_CACHE: dict[str, tuple[float, "str | None"]] = {}
_KEY_CACHE_TTL = 60.0  # seconds


def _cache_lookup(key: str) -> tuple[bool, "str | None"]:
    entry = _KEY_CACHE.get(key)
    if entry is not None and (time.monotonic() - entry[0]) < _KEY_CACHE_TTL:
        return True, entry[1]
    return False, None


def _cache_store(key: str, value: "str | None") -> None:
    _KEY_CACHE[key] = (time.monotonic(), value)


def _cache_drop(key: str) -> None:
    _KEY_CACHE.pop(key, None)

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
        _cache_store(provider, api_key)
        # Also set in environment for current session
        if provider in KEY_ENV_MAP:
            os.environ[KEY_ENV_MAP[provider]] = api_key
        log.info("api_key_stored", provider=provider)
        return True
    except Exception as e:
        log.error("api_key_store_failed", provider=provider, error=str(e))
        return False


def get_api_key(provider: str) -> str | None:
    """Retrieve an API key from the OS keychain (TTL-cached)."""
    hit, val = _cache_lookup(provider)
    if hit:
        return val
    try:
        import keyring

        val = keyring.get_password(SERVICE_NAME, provider)
        _cache_store(provider, val)
        return val
    except Exception as e:
        log.warning("api_key_read_failed", provider=provider, error=str(e))
        return None


def delete_api_key(provider: str) -> bool:
    """Remove an API key from the OS keychain."""
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, provider)
        _cache_drop(provider)
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
        _cache_store(key_ref, api_key)
        log.info("space_api_key_stored", key_ref=key_ref)
        return True
    except Exception as e:
        log.error("space_api_key_store_failed", key_ref=key_ref, error=str(e))
        return False


def get_space_api_key(key_ref: str) -> str | None:
    """Retrieve a space-specific API key from the OS keychain (TTL-cached)."""
    hit, val = _cache_lookup(key_ref)
    if hit:
        return val
    try:
        import keyring

        val = keyring.get_password(SERVICE_NAME, key_ref)
        _cache_store(key_ref, val)
        return val
    except Exception as e:
        log.warning("space_api_key_read_failed", key_ref=key_ref, error=str(e))
        return None


def delete_space_api_key(key_ref: str) -> bool:
    """Remove a space-specific API key from the OS keychain."""
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, key_ref)
        _cache_drop(key_ref)
        log.info("space_api_key_deleted", key_ref=key_ref)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# MCP bearer token
#
# Stored in the OS keychain alongside API keys.  In dev mode (engine spawned
# as an unsigned Python subprocess by `cargo tauri dev`), macOS Keychain
# access is occasionally flaky — keyring.get_password() can return None
# without raising, causing ensure_startup_token() to regenerate the token
# and break external MCP clients that have the old token.  This is a
# dev-mode-only issue: production builds run from a signed .app bundle with
# a stable code-signing identity, so Keychain access is reliable.
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


# ---------------------------------------------------------------------------
# Engine API token
# ---------------------------------------------------------------------------

ENGINE_TOKEN_KEY = "laya_engine_api_token"

# Environment variable through which a launcher supplies the engine API token.
# The Tauri shell generates a token per launch and sets this on the engine
# process; a developer can set it by hand to run the engine standalone.
ENGINE_TOKEN_ENV = "LAYA_ENGINE_TOKEN"

# Token pinned for the lifetime of this process. Set when the token comes from
# ENGINE_TOKEN_ENV, or when a freshly minted token cannot be written to the
# keychain. While set, the keychain is not consulted for the engine token.
_process_engine_token: str | None = None

# Characters allowed in an HTTP header token (RFC 7230 `tchar`). The webview
# presents the engine token inside `Sec-WebSocket-Protocol`, so a token with
# any other character can authenticate REST calls but never the `/ws`
# handshake. Tokens the shell or this module mint (hex, base64url) always pass.
_HEADER_TOKEN_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&'*+-.^_`|~"
)


def _warn_if_not_header_safe(token: str) -> None:
    if any(ch not in _HEADER_TOKEN_CHARS for ch in token):
        log.warning(
            "engine_token_not_header_safe",
            hint=(
                "The engine token contains characters not allowed in an HTTP header "
                "token, so the UI cannot present it on the WebSocket handshake and live "
                f"updates will not connect. Use only letters, digits and -._~ in {ENGINE_TOKEN_ENV}."
            ),
        )


def store_engine_token(token: str) -> bool:
    """Store the engine API bearer token in the OS keychain."""
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, ENGINE_TOKEN_KEY, token)
        _cache_store(ENGINE_TOKEN_KEY, token)
        log.info("engine_token_stored")
        return True
    except Exception as e:
        log.error("engine_token_store_failed", error=str(e))
        return False


def get_engine_token() -> str | None:
    """Return the engine API bearer token.

    The process-pinned token wins when present; otherwise the token is read
    from the OS keychain (TTL-cached).
    """
    if _process_engine_token:
        return _process_engine_token
    hit, val = _cache_lookup(ENGINE_TOKEN_KEY)
    if hit:
        return val
    try:
        import keyring

        val = keyring.get_password(SERVICE_NAME, ENGINE_TOKEN_KEY)
        _cache_store(ENGINE_TOKEN_KEY, val)
        return val
    except Exception as e:
        log.warning("engine_token_read_failed", error=str(e))
        return None


def delete_engine_token() -> bool:
    """Remove the engine API bearer token from the OS keychain."""
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, ENGINE_TOKEN_KEY)
        _cache_drop(ENGINE_TOKEN_KEY)
        log.info("engine_token_deleted")
        return True
    except Exception:
        return False


def ensure_engine_token() -> str:
    """Resolve the engine API token for this process and return it.

    Resolution order:
    1. ENGINE_TOKEN_ENV, when set. The variable is removed from the
       environment after it is read so that child processes the engine
       spawns (CLI agents) do not inherit the secret.
    2. The token stored in the OS keychain.
    3. A newly minted token, stored in the keychain. If the keychain write
       fails (no usable backend), the token is pinned in memory so the engine
       still authenticates consistently for this run.
    """
    global _process_engine_token

    env_token = os.environ.pop(ENGINE_TOKEN_ENV, "").strip()
    if env_token:
        _process_engine_token = env_token
        _warn_if_not_header_safe(env_token)
        log.info("engine_token_ready", source="env")
        return env_token

    if _process_engine_token:
        return _process_engine_token

    token = get_engine_token()
    if token:
        _warn_if_not_header_safe(token)
        log.info("engine_token_ready", source="keychain")
        return token

    token = secrets.token_urlsafe(32)
    if store_engine_token(token):
        log.info("engine_token_ready", source="keychain", minted=True)
    else:
        _process_engine_token = token
        log.warning(
            "engine_token_not_persisted",
            source="memory",
            hint=f"No usable keychain; set {ENGINE_TOKEN_ENV} to supply a known token.",
        )
    return token


# ---------------------------------------------------------------------------
# n8n token — the credential the bundled n8n workflows present to the engine.
# ---------------------------------------------------------------------------

N8N_TOKEN_KEY = "laya_n8n_token"

# Environment variable through which a launcher supplies the n8n token. The
# Tauri shell sets the same variable on the engine and on n8n at spawn; the
# workflows read it with `$env.LAYA_N8N_TOKEN`. A developer running both by
# hand sets it on both processes.
N8N_TOKEN_ENV = "LAYA_N8N_TOKEN"

# Token pinned for the lifetime of this process, with where it came from:
# "env" (launcher-supplied), "keychain", or "memory" (minted, not persisted).
# The source matters: only an env-supplied token is known to be shared with
# n8n, so only then can enforcement be switched on automatically.
_process_n8n_token: str | None = None
_n8n_token_source: str | None = None


def store_n8n_token(token: str) -> bool:
    """Store the n8n token in the OS keychain."""
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, N8N_TOKEN_KEY, token)
        _cache_store(N8N_TOKEN_KEY, token)
        log.info("n8n_token_stored")
        return True
    except Exception as e:
        log.error("n8n_token_store_failed", error=str(e))
        return False


def get_n8n_token() -> str | None:
    """Return the n8n token: the process-pinned value, else the keychain (TTL-cached)."""
    if _process_n8n_token:
        return _process_n8n_token
    hit, val = _cache_lookup(N8N_TOKEN_KEY)
    if hit:
        return val
    try:
        import keyring

        val = keyring.get_password(SERVICE_NAME, N8N_TOKEN_KEY)
        _cache_store(N8N_TOKEN_KEY, val)
        return val
    except Exception as e:
        log.warning("n8n_token_read_failed", error=str(e))
        return None


def delete_n8n_token() -> bool:
    """Remove the n8n token from the OS keychain."""
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, N8N_TOKEN_KEY)
        _cache_drop(N8N_TOKEN_KEY)
        return True
    except Exception:
        return False


def n8n_token_source() -> str | None:
    """Where this process's n8n token came from ("env", "keychain", "memory"), or None before resolution."""
    return _n8n_token_source


def ensure_n8n_token() -> str:
    """Resolve the n8n token for this process and return it.

    Same resolution order as `ensure_engine_token`:
    1. N8N_TOKEN_ENV, when set; the variable is removed from the environment
       so child processes the engine spawns (CLI agents) do not inherit it.
    2. The token stored in the OS keychain.
    3. A newly minted token, stored in the keychain, or pinned in memory when
       the keychain write fails.
    """
    global _process_n8n_token, _n8n_token_source

    env_token = os.environ.pop(N8N_TOKEN_ENV, "").strip()
    if env_token:
        _process_n8n_token = env_token
        _n8n_token_source = "env"
        log.info("n8n_token_ready", source="env")
        return env_token

    if _process_n8n_token:
        return _process_n8n_token

    token = get_n8n_token()
    if token:
        _n8n_token_source = "keychain"
        log.info("n8n_token_ready", source="keychain")
        return token

    token = secrets.token_urlsafe(32)
    if store_n8n_token(token):
        _n8n_token_source = "keychain"
        log.info("n8n_token_ready", source="keychain", minted=True)
    else:
        _process_n8n_token = token
        _n8n_token_source = "memory"
        log.warning(
            "n8n_token_not_persisted",
            source="memory",
            hint=f"No usable keychain; set {N8N_TOKEN_ENV} on the engine and n8n to supply a known token.",
        )
    return token
