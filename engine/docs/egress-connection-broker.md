# Egress Connection Broker

> Detailed design for the unified credential management system.
> Part of the [Egress Architecture](egress-architecture.md).

---

## 1. Overview

The Connection Broker is the **single pane of glass** for all platform credentials.
Users interact with Laya Settings only. The Broker handles:

1. Receiving credentials from the user (API keys, OAuth tokens, SMTP configs)
2. Validating they work (test API call to the platform)
3. Provisioning them to wherever execution backends need them (n8n, OS keychain)
4. Monitoring connection health (periodic validation, token expiry tracking)
5. Handling token refresh for OAuth platforms

### Design Goals

- **One button per platform**: User clicks "Connect Jira" → enters credentials → done.
- **Zero n8n exposure**: User never opens the n8n dashboard for credential management.
- **Graceful degradation**: If a connection expires, Laya shows clear status and re-auth flow.
- **Space-aware**: Different spaces can use different credentials for the same platform.

---

## 2. Credential Flows

### 2.1 Flow A: API-Key Platforms

**Applies to**: Jira, GitHub, Bitbucket, Slack, Linear, Discord, GitLab, Notion

```
User clicks "Connect Jira" in Settings > Integrations
  |
  +-- Settings UI shows credential form
  |   (email, API token, domain -- fields defined in platforms.py)
  |
  +-- POST /egress/connections
  |   Body: {platform: "jira", name: "Jira Main", credentials: {email, apiToken, domain}}
  |
  +-- Connection Broker:
  |   |
  |   +-- 1. VALIDATE: Make test API call to Jira
  |   |   GET https://{domain}/rest/api/3/myself
  |   |   Headers: Authorization: Basic {email}:{apiToken}
  |   |   If fails -> return error with specific message
  |   |
  |   +-- 2. STORE: Save credentials to OS keychain
  |   |   keyring.set_password("laya-egress", "jira:{connection_id}", json.dumps(credentials))
  |   |   Keychain is the source of truth for credentials.
  |   |
  |   +-- 3. PROVISION TO N8N: Create credential via n8n REST API
  |   |   POST /api/v1/credentials
  |   |   Body: {name: "Laya - Jira Main", type: "jiraSoftwareCloudApi", data: {email, apiToken, domain}}
  |   |   Store the returned n8n credential ID for future reference.
  |   |
  |   +-- 4. LINK WORKFLOWS: Ensure executor workflows reference this credential
  |   |   If jira-executor workflow exists and has unlinked Jira nodes,
  |   |   update the workflow to use this credential ID.
  |   |
  |   +-- 5. RECORD: Insert into connections table in SQLite
  |   |   (connection_id, platform, name, n8n_credential_id, status, capabilities, created_at)
  |   |
  |   +-- 6. RETURN: {status: "connected", capabilities: ["comment", "transition", "create_issue", ...]}
  |
  +-- UI shows: "Jira connected -- 6 actions available"
```

### 2.2 Flow B: OAuth Platforms

**Applies to**: Gmail, Google Calendar, Microsoft 365 (Outlook Email + Calendar)

Today, OAuth platforms require users to manually set up credentials in the n8n dashboard.
This is the biggest UX pain point. The Connection Broker solves it by **owning the OAuth
dance** and injecting the resulting tokens into n8n.

**Prerequisites**:
- Laya registers an OAuth application with each provider (Google Cloud Console, Azure AD)
- The OAuth app's redirect URI points to Laya's callback endpoint
- Client ID and client secret are stored in Laya's config

```
User clicks "Connect Gmail" in Settings > Integrations
  |
  +-- UI calls: GET /egress/connections/oauth/start?platform=gmail
  |
  +-- Connection Broker:
  |   +-- Generates state token (CSRF protection)
  |   +-- Builds authorization URL:
  |       https://accounts.google.com/o/oauth2/v2/auth?
  |         client_id={LAYA_GOOGLE_CLIENT_ID}
  |         &redirect_uri=http://127.0.0.1:8420/egress/connections/oauth/callback
  |         &response_type=code
  |         &scope=https://www.googleapis.com/auth/gmail.modify
  |         &state={state_token}
  |         &access_type=offline
  |         &prompt=consent
  |   +-- Returns: {auth_url: "https://accounts.google.com/...", state: "..."}
  |
  +-- UI opens auth_url in user's browser (or popup)
  |
  +-- User grants access on Google's consent screen
  |
  +-- Google redirects to: http://127.0.0.1:8420/egress/connections/oauth/callback
  |   ?code=AUTH_CODE&state={state_token}
  |
  +-- Connection Broker handles callback:
      |
      +-- 1. VALIDATE STATE: Check state token matches (CSRF protection)
      |
      +-- 2. EXCHANGE CODE: POST to Google's token endpoint
      |   POST https://oauth2.googleapis.com/token
      |   Body: {code, client_id, client_secret, redirect_uri, grant_type: "authorization_code"}
      |   Response: {access_token, refresh_token, expires_in, token_type}
      |
      +-- 3. STORE: Save refresh_token in OS keychain
      |   keyring.set_password("laya-egress", "gmail:{connection_id}:refresh_token", refresh_token)
      |   keyring.set_password("laya-egress", "gmail:{connection_id}:access_token", access_token)
      |
      +-- 4. PROVISION TO N8N: Create OAuth2 credential in n8n
      |   POST /api/v1/credentials
      |   Body: {
      |     name: "Laya - Gmail",
      |     type: "gmailOAuth2Api",
      |     data: {
      |       clientId: LAYA_GOOGLE_CLIENT_ID,
      |       clientSecret: LAYA_GOOGLE_CLIENT_SECRET,
      |       accessToken: access_token,
      |       refreshToken: refresh_token,
      |       expiresIn: expires_in,
      |       tokenType: "Bearer",
      |       oauthTokenData: {
      |         access_token, refresh_token, expires_in,
      |         scope: "...", token_type: "Bearer"
      |       }
      |     }
      |   }
      |
      +-- 5. LINK WORKFLOWS: Update gmail-executor and gmail-ingestion to use this credential
      |
      +-- 6. SCHEDULE REFRESH: Register background task to refresh access_token before expiry
      |   (Google access tokens expire in 1 hour; refresh 5 minutes before)
      |
      +-- 7. RECORD: Insert into connections table
      |
      +-- 8. REDIRECT: Send user back to Settings UI with success message
          GET /settings?connection=gmail&status=connected
```

### 2.3 Flow C: Generic Email (SMTP/IMAP)

**Applies to**: ProtonMail, Fastmail, Yahoo, iCloud, Zoho, AOL, custom mail servers

```
User clicks "Connect Email (Other)" in Settings > Integrations
  |
  +-- UI shows email address input with "Auto-detect" button
  |
  +-- User enters: user@protonmail.com
  |
  +-- Auto-detect:
  |   POST /egress/connections/detect?email=user@protonmail.com
  |   Broker looks up domain in WELL_KNOWN_PROVIDERS
  |   Returns: {provider: "ProtonMail", smtp_host: "127.0.0.1", smtp_port: 1025,
  |             imap_host: "127.0.0.1", imap_port: 1143, method: "app_password",
  |             note: "Requires ProtonMail Bridge running locally"}
  |
  +-- UI pre-fills SMTP/IMAP form with detected settings
  |   User enters app password (ProtonMail Bridge password)
  |
  +-- POST /egress/connections
  |   Body: {platform: "smtp", credentials: {
  |     email: "user@protonmail.com",
  |     smtp_host: "127.0.0.1", smtp_port: 1025,
  |     imap_host: "127.0.0.1", imap_port: 1143,
  |     username: "user@protonmail.com", password: "bridge-password",
  |     use_tls: true
  |   }}
  |
  +-- Connection Broker:
      +-- 1. VALIDATE SMTP: Connect, EHLO, STARTTLS, AUTH
      +-- 2. VALIDATE IMAP: Connect, LOGIN, SELECT INBOX
      +-- 3. STORE: Save to OS keychain
      +-- 4. NO N8N: SMTP backend runs in-process, no n8n provisioning needed
      +-- 5. RECORD: Insert into connections table
      +-- 6. RETURN: {status: "connected", capabilities: ["send_email", "reply"]}
```

---

## 3. Well-Known Email Providers

The Connection Broker includes auto-detection for common email providers:

```python
WELL_KNOWN_PROVIDERS = {
    # Domain suffix -> provider config
    "gmail.com": {
        "provider": "Gmail",
        "method": "oauth",             # Use OAuth flow, not SMTP
        "redirect": "gmail",           # Redirect to Gmail OAuth connect
        "note": None,
    },
    "outlook.com": {
        "provider": "Microsoft 365",
        "method": "oauth",
        "redirect": "outlook",
        "note": None,
    },
    "hotmail.com": {
        "provider": "Microsoft 365",
        "method": "oauth",
        "redirect": "outlook",
        "note": None,
    },
    "protonmail.com": {
        "provider": "ProtonMail",
        "method": "app_password",
        "smtp_host": "127.0.0.1",
        "smtp_port": 1025,
        "imap_host": "127.0.0.1",
        "imap_port": 1143,
        "use_tls": false,              # Bridge handles encryption
        "note": "Requires ProtonMail Bridge running locally. Download at proton.me/mail/bridge",
    },
    "proton.me": {
        # Same as protonmail.com
    },
    "fastmail.com": {
        "provider": "Fastmail",
        "method": "app_password",
        "smtp_host": "smtp.fastmail.com",
        "smtp_port": 587,
        "imap_host": "imap.fastmail.com",
        "imap_port": 993,
        "use_tls": true,
        "note": "Use an app password from fastmail.com/settings/security/tokens",
    },
    "yahoo.com": {
        "provider": "Yahoo Mail",
        "method": "app_password",
        "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": 587,
        "imap_host": "imap.mail.yahoo.com",
        "imap_port": 993,
        "use_tls": true,
        "note": "Generate an app password at login.yahoo.com/account/security",
    },
    "icloud.com": {
        "provider": "iCloud Mail",
        "method": "app_password",
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587,
        "imap_host": "imap.mail.me.com",
        "imap_port": 993,
        "use_tls": true,
        "note": "Generate an app-specific password at appleid.apple.com/account/manage",
    },
    "me.com": {
        # Same as icloud.com
    },
    "zoho.com": {
        "provider": "Zoho Mail",
        "method": "app_password",
        "smtp_host": "smtp.zoho.com",
        "smtp_port": 587,
        "imap_host": "imap.zoho.com",
        "imap_port": 993,
        "use_tls": true,
        "note": "Enable IMAP in Zoho settings, then use an app-specific password",
    },
    "aol.com": {
        "provider": "AOL Mail",
        "method": "app_password",
        "smtp_host": "smtp.aol.com",
        "smtp_port": 587,
        "imap_host": "imap.aol.com",
        "imap_port": 993,
        "use_tls": true,
        "note": "Generate an app password at login.aol.com/account/security",
    },
}
```

For unrecognized domains, the UI shows a manual SMTP/IMAP form with all fields empty,
plus a link to "How to find your email server settings".

---

## 4. Connection Health Monitoring

### 4.1 Periodic Validation

A background task runs every 30 minutes to check connection health:

```python
async def check_connection_health():
    """Periodic health check for all active connections."""
    connections = await list_connections()
    for conn in connections:
        if conn.platform in OAUTH_PLATFORMS:
            # Check if access token is expired or about to expire
            # If so, attempt refresh
            await _refresh_if_needed(conn)
        else:
            # For API key platforms, make a lightweight test call
            # (e.g., GET /myself for Jira, GET /user for GitHub)
            healthy = await _test_connection(conn)
            if not healthy:
                await _mark_connection_error(conn, "API key may be expired or revoked")
```

### 4.2 Token Refresh

For OAuth platforms, access tokens expire (Google: 1 hour, Microsoft: 1 hour).
The broker refreshes them proactively:

```python
async def refresh_oauth_token(connection: Connection):
    """Refresh an OAuth access token using the stored refresh token."""
    refresh_token = keyring.get_password("laya-egress", f"{conn.platform}:{conn.id}:refresh_token")

    # Exchange refresh token for new access token
    response = await httpx.post(token_endpoint, data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    })

    new_access_token = response.json()["access_token"]

    # Update in keychain
    keyring.set_password("laya-egress", f"{conn.platform}:{conn.id}:access_token", new_access_token)

    # Update in n8n (update the credential's oauthTokenData)
    await n8n_client.update_credential(conn.n8n_credential_id, {
        "oauthTokenData": {"access_token": new_access_token, ...}
    })
```

### 4.3 Connection Status in UI

Settings > Integrations shows each platform with a clear status indicator:

```
GitHub          [Connected]     PAT ending in ...a3f7     6 actions available
Jira Cloud      [Connected]     aayush@company.com        6 actions available
Gmail           [Connected]     aayush@gmail.com          5 actions available    [Reconnect]
Slack           [Not Connected] --                        --                     [Connect]
Bitbucket       [Error]         Token expired 2h ago      --                     [Reconnect]
```

---

## 5. Credential Storage Architecture

### 5.1 Source of Truth: OS Keychain

All credentials are stored in the OS keychain (`keyring` library):
- macOS: Keychain Access
- Linux: Secret Service (GNOME Keyring / KDE Wallet)
- Windows: Windows Credential Manager

Key format: `laya-egress:{platform}:{connection_id}:{field}`

Examples:
```
laya-egress:jira:conn_abc123:credentials     -> {"email": "...", "apiToken": "...", "domain": "..."}
laya-egress:gmail:conn_def456:refresh_token  -> "1//0abc..."
laya-egress:gmail:conn_def456:access_token   -> "ya29.abc..."
laya-egress:smtp:conn_ghi789:credentials     -> {"smtp_host": "...", "password": "...", ...}
```

### 5.2 Secondary Storage: n8n

n8n credentials are **derived** from the keychain, not the other way around. If n8n's
database is lost, the broker can re-provision all credentials from the keychain.

### 5.3 SQLite Metadata

The `connections` table stores metadata (not secrets):

```sql
CREATE TABLE IF NOT EXISTS egress_connections (
    connection_id       TEXT PRIMARY KEY,
    platform            TEXT NOT NULL,
    name                TEXT NOT NULL,
    n8n_credential_id   TEXT,            -- n8n's internal credential ID (if provisioned)
    space_id            TEXT,            -- NULL = global, else space-specific
    status              TEXT NOT NULL DEFAULT 'connected',  -- connected | error | expired
    capabilities        TEXT,            -- JSON array of action_types
    error_message       TEXT,
    last_validated_at   TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
```

---

## 6. How Other Apps Solve This

| App | Approach | Lesson for Laya |
|-----|----------|-----------------|
| **Zapier** | "Connect Account" button per platform. OAuth or API key form. All stored in Zapier's vault. Execution layer never exposed to users. | Users never see the execution engine. Settings is the only credential surface. |
| **Raycast** | Each extension has a "Sign In" button. One click per integration. | Minimize friction: one button, not a multi-step wizard. |
| **Linear** | Integrations page with toggle switches. OAuth for connected services. One-click enable. | Show connected/disconnected clearly. Make reconnect easy. |
| **Nango** (open-source) | Unified auth layer for 250+ APIs. Handles OAuth dance, token refresh, credential storage. Apps call Nango's API. | If Laya's credential needs outgrow the Connection Broker, Nango could be adopted as the auth layer. Keep this as a future option. |
| **Retool** | "Resources" panel. Single config form per data source. Retool stores credentials and uses them for all queries. | Single form per platform. Never configure the same platform in two places. |

**Universal lesson**: Great apps hide execution complexity behind a single "Connect" button.
Users should never know that n8n, SMTP, or any other backend exists.

---

## 7. Migration from Current System

### What Changes

| Current (`connections_api.py`) | New (`egress/connections.py`) |
|-------------------------------|-------------------------------|
| Creates n8n credentials only | Creates n8n credentials AND stores in keychain |
| No validation before creation | Validates credentials with test API call |
| OAuth platforms say "set up in n8n" | OAuth handled entirely in Laya |
| No connection health tracking | Periodic health checks + status |
| No SMTP/IMAP support | Full SMTP/IMAP credential flow |

### Migration Steps

1. Create `egress/connections.py` implementing the Connection Broker
2. Create new API routes under `/egress/connections/` (alongside old `/connections/`)
3. Migrate existing connections from n8n-only to dual storage (keychain + n8n)
4. Update Settings UI to use new endpoints
5. Deprecate old `/connections/` routes
6. Add OAuth endpoints (`/oauth/start`, `/oauth/callback`)
7. Add SMTP detection endpoint (`/connections/detect`)
