# OAuth App Distribution Runbook

How to register, distribute, and (where applicable) verify a Laya-owned OAuth app on every
platform Laya integrates with, so that connecting an account is a one-click "Sign in with X"
experience — no user-side app creation, no API keys.

This is a vendor-side (manual console work) runbook. It produces the client IDs/secrets that
the engine will later bundle. **No code changes are described here** — the engineering plan
that consumes this runbook's output lives separately.

Researched and verified against current platform documentation in **June 2026**. Platform
policies change; re-verify the cited sources before acting on a section more than a few
months from now.

---

## 1. The two camps

Every platform below lets a vendor-owned OAuth app be authorized by **any** user with no
per-user setup. The split that matters for a desktop app is whether the platform supports
**public clients** (token exchange without a `client_secret`, i.e. PKCE or device flow):

| Platform | Secretless flow available? | Distribution gate | Verification/review |
|---|---|---|---|
| Google (Gmail, GCal) | ✅ PKCE + loopback | OAuth consent screen → production | OAuth verification + CASA for restricted Gmail scopes (in progress) |
| Microsoft (Outlook Mail/Cal) | ✅ PKCE + loopback | Multi-tenant registration (self-serve) | **Publisher verification** — free, but effectively mandatory for work/school accounts |
| Slack | ✅ PKCE (GA since 2026-03), user-token scopes only | "Activate Public Distribution" (self-serve) | None for OAuth; Marketplace listing optional |
| Linear | ✅ PKCE (`client_secret` optional) | None — public by default | None for OAuth; directory listing optional |
| GitHub | ✅ Device flow only | None — public app | None for OAuth; Marketplace optional |
| Jira (Atlassian 3LO) | ❌ secret required (no PKCE; [ECO-283](https://jira.atlassian.com/browse/ECO-283) still open) | "Enable sharing" toggle (self-serve) | Optional free Marketplace review (removes a warning banner only) |
| Bitbucket Cloud | ❌ secret required | None at all | None |
| Notion | ❌ secret required (Basic-auth token endpoint) | Public connection, "Any workspace" scope (self-serve) | None for OAuth; Marketplace listing optional |

**Decision (June 2026):** for the secret-required platforms (Jira, Bitbucket, Notion) the
client secret will be **embedded in the shipped app**, lightly obfuscated. No hosted
token-exchange broker. Trade-off accepted knowingly: an embedded secret is extractable by a
determined attacker, who could then impersonate Laya's app in a phishing flow. Mitigations:
request minimal scopes, monitor for abuse, rotate secrets if leaked (all three consoles allow
secret rotation). This is common practice for desktop apps (GitHub Desktop ships its OAuth
secret in the binary). User tokens are never affected differently either way — they live only
in the user's OS keychain.

GitHub uses the **device flow** (no secret, no infrastructure) rather than embedding.

---

## 2. Shared prerequisites (do once, before any registration)

1. **A custom domain Laya controls DNS for.**
   - Required for Microsoft publisher verification (`*.onmicrosoft.com` and `github.io`
     domains are not accepted — the publisher domain must be DNS-verified in the Entra
     tenant).
   - ⚠️ Current gap: the landing site lives at `https://aayushch.github.io/laya/` (GitHub
     Pages). Buy a domain (e.g. `getlaya.app` or similar), point it at the landing site
     (GitHub Pages supports custom domains), and use it consistently as the app
     homepage/publisher domain everywhere below.
2. **Public privacy policy and terms URLs.**
   - Already exist: `landing/privacy.html`, `landing/terms.html`. Once the custom domain is
     live, use those URLs (not the github.io ones) in every console form.
   - Slack's Marketplace (if ever pursued) requires the privacy policy to state: what data is
     collected, how it is used, retention, how users can access/transfer/delete their data,
     and a contact for data requests. Worth covering now so the policy never blocks a later
     listing.
3. **A support email** (used by Notion, Slack, Atlassian forms). A monitored address on the
   custom domain is ideal.
4. **Dedicated vendor accounts/workspaces to own each app**, so app ownership never depends
   on a personal account:
   - A Laya Slack workspace (the app's "development workspace").
   - A Laya Atlassian developer account (owns the 3LO app) and a Laya Bitbucket workspace
     (owns the OAuth consumer).
   - A Laya Linear workspace — Linear explicitly recommends a dedicated workspace because
     **every admin of the owning workspace can manage the OAuth app**.
   - A Laya Notion workspace (the connection's development workspace).
   - A GitHub org (e.g. the org that owns the repo) to own the GitHub App.
   - A Microsoft Entra tenant for Laya (created free with an Azure account).
5. **App branding assets**: square logo (512×512 PNG covers all consoles), one-line app
   description, homepage URL.

---

## 3. Per-platform runbooks

### 3.1 Google — Gmail + Google Calendar (in progress)

Status: OAuth app created; verification underway. Recorded here for completeness so this doc
is the single registry.

- Laya requests exactly two Google scopes: `gmail.modify` (restricted) and `calendar.events`
  (sensitive). `gmail.modify` is a superset covering every Gmail call we make (read, send,
  label modify), so `gmail.readonly`/`gmail.send` must NOT be requested alongside it —
  Google's verification review rejects consent screens showing scopes beyond the Cloud
  Console submission (this happened Jul 2026; runtime list lives in
  `engine/laya/egress/oauth.py` `OAUTH_PROVIDERS` and must stay in lockstep with the
  Console submission). The restricted `gmail.modify` scope requires Google's OAuth
  verification **plus an annual CASA security assessment** (paid, third-party lab);
  `calendar.events` is "sensitive" — verification but no CASA.
- Desktop flow: authorization code + PKCE with loopback redirect — already implemented in
  `engine/laya/egress/oauth.py`. Google "Desktop app" client type has a client_secret that is
  explicitly **not treated as confidential** by Google for installed apps; bundling it is
  sanctioned.
- ☐ Record: client_id, verification status, CASA assessment date + annual renewal reminder.
- Sources: <https://developers.google.com/identity/protocols/oauth2/native-app>,
  <https://support.google.com/cloud/answer/13463073> (verification),
  <https://support.google.com/cloud/answer/9110914> (restricted scopes / CASA).

### 3.2 Microsoft — Outlook Email + Outlook Calendar (Graph)

**The long pole. Start this first** — Partner Center business verification takes days to ~2
weeks, and without publisher verification work/school users cannot consent at all.

One app registration covers both Outlook Mail and Calendar (and personal + work accounts).

**A. App registration** (Entra admin center → App registrations → New):
1. Supported account types: **"Accounts in any organizational directory and personal
   Microsoft accounts"** (`signInAudience: AzureADandPersonalMicrosoftAccount`). The engine
   uses the `/common` authority.
2. Platform: **"Mobile and desktop applications"**. Add redirect URI `http://localhost`
   (plain HTTP is allowed for loopback only, and **the port is ignored at match time** — the
   existing `http://localhost:8420/egress/connections/oauth/callback` callback works; do not
   register multiple localhost URIs differing only by port). Microsoft recommends
   `http://127.0.0.1` for robustness, but the portal UI blocks adding it — it can only be
   added via the manifest's `replyUrlsWithType` if ever needed.
3. Enable **"Allow public client flows"** (Authentication blade). **Do not create a client
   secret** — public clients must not send one.
4. API permissions → Microsoft Graph → Delegated: `Mail.Read`, `Mail.Send`,
   `Calendars.ReadWrite`, `offline_access`, `User.Read`. No Microsoft-side per-scope review
   exists (no CASA equivalent) — publisher verification is the only vendor-side gate.
5. Branding: set name, logo, homepage, privacy + terms URLs.

**B. Publisher verification** (free):
1. Join the **Microsoft AI Cloud Partner Program** (formerly MPN) via Partner Center using a
   work account in the Laya tenant; complete business verification (this is the slow step).
   The **Partner Global Account** PartnerID is the one that counts.
2. Add the custom domain to the Entra tenant and DNS-verify it; set it as the app's
   **publisher domain** (Branding blade). The email domain used in Partner Center
   verification must match a DNS-verified tenant domain.
3. App registration → Branding → Publisher verification → enter the PGA PartnerID → verify.
   The verification step itself completes in minutes once prerequisites are met; the app then
   shows the blue "verified publisher" badge on consent screens.
4. Apps registered under a personal Microsoft account **cannot** be verified — make sure the
   registration lives in the Laya tenant under a work account.

**C. Work/school tenant reality (document for support, can't be engineered away):**
- Since Microsoft's "secure by default" rollouts (MC1097272 mid-2025; **MC1163922,
  Oct–Nov 2025**, which covers Mail/Calendar scopes), tenants on the Microsoft-managed
  default consent policy **block end-user self-consent to Mail.\*/Calendars.\* scopes even
  for verified publishers**. Users in such tenants get a "needs admin approval" prompt.
- Prepare an IT-admin one-pager: Laya's client_id, exact scopes, what Laya does with the
  data (local-first), and the tenant-wide admin consent URL:
  `https://login.microsoftonline.com/{tenant}/adminconsent?client_id={CLIENT_ID}`.
- Personal accounts (outlook.com / hotmail) are unaffected and work one-click immediately.
- Device code flow exists as a fallback but is increasingly blocked by Conditional Access
  policies and doesn't bypass consent rules — not worth building for Microsoft.

☐ Checklist: registration created (multi-tenant + personal) → public client flows on →
loopback redirect added → scopes added → branding set → Partner Center verified → publisher
domain set → publisher verification badge showing → test consent from (a) a personal
outlook.com account and (b) a foreign work tenant.

Sources: <https://learn.microsoft.com/en-us/entra/identity-platform/publisher-verification-overview>,
<https://learn.microsoft.com/en-us/entra/identity-platform/reply-url>,
<https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow>,
<https://mc.merill.net/message/MC1163922>,
<https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-admin-consent-workflow>.

### 3.3 Slack

Laya only uses **user-token scopes** (`OAUTH_CONFIGS["slack"]["user_scopes"]` in
`engine/laya/egress/oauth.py`; bot `scopes` is empty) — which means Slack can be fully
**secretless** via PKCE.

1. Create the app at <https://api.slack.com/apps> under the Laya workspace, with **granular
   user-token scopes** exactly matching the engine's `user_scopes` list. Request nothing
   extra — scope minimalism matters if a Marketplace listing is ever pursued.
2. **Enable PKCE** (OAuth & Permissions). ⚠️ This is a **one-way, irreversible** switch that
   marks the app a public client. After enabling, token exchange (`oauth.v2.access` or the
   newer `oauth.v2.user.access`, which Slack documents specifically for desktop clients)
   uses `code_verifier` instead of `client_secret`; `code_challenge_method` must be `S256`.
3. **Redirect URL — open item to resolve at registration time**: Slack's distribution
   checklist requires at least one **HTTPS** redirect URL. Verify whether Slack accepts
   `http://localhost:…` for a PKCE public client. If not, two options (no broker needed in
   either):
   - **Custom URI scheme** (`laya://oauth/callback`) — supported with PKCE for user-token
     scopes. Constraint: custom-scheme apps **always get rotating tokens**, with refresh
     tokens expiring after **30 days idle** — the engine's background refresher must keep
     them warm or the user re-authorizes monthly.
   - **Static bounce page** on the landing site (HTTPS) that immediately forwards
     `code`+`state` to `http://localhost:8420/...` via JS redirect. Pure static hosting, no
     secrets server-side.
4. **Activate Public Distribution** (Manage Distribution): self-serve checklist (HTTPS
   redirect, granular scopes, no hardcoded secrets) → instant; any workspace can then
   install via OAuth. **No review of any kind is required for this.**
5. Marketplace listing: **defer.** Only needed for discoverability and for Enterprise Grid
   orgs whose policy restricts installs to Marketplace-listed apps. Requirements when ready:
   5+ active workspaces, privacy policy, security questionnaire, demo video; review up to ~10
   weeks; free.
6. Workspace-side reality: any workspace can enable admin app-approval — users then see a
   "request approval" interstitial mid-OAuth. Surface this gracefully in support docs.

☐ Checklist: app created in Laya workspace → user scopes match engine → PKCE enabled →
redirect question resolved (record which option) → public distribution activated → test
install from a foreign workspace.

Sources: <https://docs.slack.dev/authentication/using-pkce/>,
<https://docs.slack.dev/changelog/2026/03/30/pkce/> (PKCE GA),
<https://docs.slack.dev/reference/methods/oauth.v2.user.access/>,
<https://docs.slack.dev/app-management/distribution/>,
<https://docs.slack.dev/slack-marketplace/slack-marketplace-review-guide/>.

### 3.4 Atlassian — Jira Cloud (OAuth 2.0 / 3LO)

1. <https://developer.atlassian.com/console> → Create → **OAuth 2.0 integration**.
2. Permissions: add Jira API scopes Laya needs (match what the engine uses today via API
   token — comments, transitions, issue read/write) **plus `offline_access`** (required for
   refresh tokens). Keep minimal.
3. Authorization: callback URL `http://localhost:8420/egress/connections/oauth/callback`
   (Atlassian accepts localhost callbacks).
4. **Distribution → "Enable sharing"**: fill vendor name + privacy policy URL (+ a
   personal-data handling declaration). Instant, self-serve, free, no review. Without this,
   only the app's creator can authorize.
5. Expect the consent screen to show an *"app has not yet been reviewed by Atlassian"*
   warning. Removing it = optional, free **"third-party integration" Marketplace listing**
   (manual review, no published SLA; a test instance speeds it up). Defer; revisit if the
   warning hurts conversion.
6. **Secret handling**: no PKCE — `client_secret` is required at the token endpoint for both
   exchange and refresh. The secret ships embedded in Laya (see §1 decision). Rotate from
   the console if ever leaked.
7. Token semantics to record for engineering:
   - Access tokens last 1 h; refresh tokens **rotate on every use** (persist the
     replacement), expire after **90 days of inactivity**, hard cap **365 days** → user must
     re-auth at most yearly.
   - API calls go through the gateway: `https://api.atlassian.com/ex/jira/{cloudId}/...`;
     obtain `cloudId` from `https://api.atlassian.com/oauth/token/accessible-resources`
     after auth. (Different base URL than the current API-token integration — engineering
     impact noted in the code plan.)
8. Org-side reality: Atlassian org admins can block third-party apps for managed accounts.

☐ Checklist: 3LO app created → scopes + offline_access → callback set → sharing enabled →
secret stored in the secrets registry (§4) → test authorization from a foreign Atlassian
account → `accessible-resources` returns the site.

Sources: <https://developer.atlassian.com/cloud/oauth/getting-started/implementing-oauth-3lo/>,
<https://developer.atlassian.com/cloud/oauth/getting-started/managing-oauth-apps/>,
<https://developer.atlassian.com/platform/marketplace/knowledge-base/listing-a-third-party-integration-on-the-atlassian-marketplace/>.

### 3.5 Bitbucket Cloud

The simplest of all — no distribution toggle, no review, nothing.

1. Laya vendor workspace → Workspace settings → **OAuth consumers** → Add consumer.
2. Callback URL: `http://localhost:8420/egress/connections/oauth/callback`. Permissions:
   minimal scopes for Laya's PR/repo actions.
3. Check **"This is a private consumer"** — counterintuitively this does *not* restrict who
   can authorize; it only disables the 2LO client-credentials grant so a leaked secret can't
   directly impersonate the owning workspace. Any Bitbucket user can authorize the consumer.
4. No PKCE; token exchange is Basic auth `client_id:secret` → secret ships embedded.
   Implicit and password grants were removed — there is no secret-free alternative.
5. Token semantics: access tokens 1 h; refresh tokens rotate on use; unused refresh tokens
   expire after **3 months**. API base is `https://api.bitbucket.org` with Bearer auth.
6. Workspace admins can restrict app access per workspace ("Grant access" flow) — possible
   support friction in org-controlled workspaces.

☐ Checklist: consumer created under vendor workspace → private-consumer checked → callback
set → secret stored in registry → test authorization from a foreign Bitbucket account.

Sources: <https://developer.atlassian.com/cloud/bitbucket/oauth-2/>,
<https://support.atlassian.com/bitbucket-cloud/docs/use-oauth-on-bitbucket-cloud/>.

### 3.6 GitHub — public GitHub App with device flow

GitHub App (not classic OAuth App): GitHub's recommended type — fine-grained permissions,
short-lived tokens, **exempt from org "OAuth app access restrictions"** (which are on by
default in new orgs and would gate a classic OAuth App out of org repos).

1. Org settings → Developer settings → **GitHub Apps** → New. Set it **public** ("Any
   account can install").
2. Permissions: fine-grained, matching Laya's actions (e.g. Pull requests R/W, Issues R/W,
   Contents R as needed). Webhook: off (Laya ingests via n8n polling, not app webhooks).
3. **Enable "Device Flow"** (checkbox in app settings — off by default since 2022).
4. **No client secret is used**: device flow token exchange needs only `client_id` +
   `device_code` + `grant_type=urn:ietf:params:oauth:grant-type:device_code`. The redirect
   (web) flow would require the secret even with PKCE — GitHub's July 2025 PKCE support is
   additive and does not create public clients — which is why device flow was chosen.
5. UX to expect (matches `gh auth login`): app shows an 8-character code, opens
   `https://github.com/login/device`, user pastes the code and approves; the app polls until
   granted. No verification or review is needed for anyone to authorize.
6. Token semantics: user access tokens expire in 8 h with 6-month refresh tokens by default;
   token expiration **can be disabled** in app settings. Decide at registration time and
   record it (recommendation: keep expiration on; the engine already refreshes).
7. Org reality: installing on an org requires an org owner (members trigger a
   request-approval flow). User tokens see only repos the installation covers.

☐ Checklist: public GitHub App created under the Laya org → permissions set → device flow
enabled → token-expiration choice recorded → test device-flow auth from a foreign account →
test member-requests-install flow on an org.

Sources: <https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app>,
<https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/differences-between-github-apps-and-oauth-apps>,
<https://github.blog/changelog/2025-07-14-pkce-support-for-oauth-and-github-app-authentication/>.

### 3.7 Linear

Cleanest story of all platforms: true PKCE public client, zero review.

1. In the **dedicated Laya Linear workspace** (see §2 — all workspace admins can manage the
   app): Settings → API → OAuth applications → New.
2. Redirect URI: `http://localhost:8420/egress/connections/oauth/callback`. Scopes: minimal
   (read, issues write as needed).
3. Use the **PKCE** variant of the flow; at the token endpoint `client_secret` is explicitly
   optional — **omit it**; nothing secret ships.
4. Token semantics (post-Oct 2025 apps): access tokens ~24 h; rotating refresh tokens with a
   30-minute replay grace window; a reused stale refresh token returns `invalid_grant` →
   full re-auth.
5. Workspace reality: Plus-plan workspaces can enable "Third-Party Application Approvals" —
   non-admin members hit an approval interstitial mid-OAuth.
6. Optional later: Integration Directory listing (review exists only for the listing, "formal
   companies" bar). Defer.

☐ Checklist: app created in dedicated workspace → redirect set → PKCE flow confirmed working
without secret → test from a foreign workspace.

Source: <https://linear.app/developers/oauth-2-0-authentication>,
<https://linear.app/docs/third-party-application-approvals>.

### 3.8 Notion

Notion shipped a new **Developer portal** (`app.notion.com/developers`) in May 2026 — public
connections are created there directly (the old internal→public toggle flow is gone; ignore
older third-party guides describing it).

1. Developer portal → **Public connections** → create. Development workspace: the Laya
   Notion workspace.
2. **Installation scope: "Any workspace"** — ⚠️ set at creation and **permanent**; required
   both for any-user OAuth and for any future Marketplace listing. Do not pick "Selected
   workspaces only".
3. Redirect URI: `http://localhost:8420/egress/connections/oauth/callback` (must include a
   protocol; no wildcards/fragments). Capabilities: read/update/insert as Laya needs.
4. Public connections are usable via their OAuth URL **without** a Marketplace listing. The
   docs contain one ambiguous sentence implying the authorization URL may populate only
   after "submitting for review" — in practice client ID/secret appear in the Configuration
   tab at creation. ☐ Verify in the portal at creation time; if a lightweight submission
   step exists, it is not the 5–10-day Marketplace security review.
5. **Secret handling**: no PKCE anywhere in Notion's OAuth docs; the token endpoint uses
   HTTP Basic auth (`client_id:client_secret`) for exchange, refresh, and introspection →
   secret ships embedded.
6. Token semantics to record for engineering: tokens are `ntn_`-prefixed; **no documented
   access-token TTL** (introspection returns no `exp`); `refresh_token` may be **null**;
   when present, refresh **rotates** both tokens (stale refresh → `invalid_grant` →
   re-auth). Since June 8 2026, every successful (re-)authorization mints a fresh token pair
   — always overwrite stored tokens.
7. Marketplace listing (5–10 business-day review): defer.

☐ Checklist: public connection created with "Any workspace" scope → redirect set →
capabilities minimal → confirm auth URL is live without review → secret stored in registry →
test authorization from a foreign Notion workspace.

Sources: <https://developers.notion.com/docs/authorization>,
<https://developers.notion.com/guides/get-started/public-connections.md>,
<https://developers.notion.com/guides/get-started/marketplace-listing.md>,
<https://developers.notion.com/reference/refresh-a-token>.

---

## 4. Registry (fill in as registrations complete)

This table is the hand-off artifact the engineering phase consumes. **client_id values are
public and belong here; client secrets do NOT go in this file or anywhere in the repo** —
store them in a password manager / the team secret store, and they enter the codebase only
via the (obfuscated) bundling step in the engineering plan.

| Platform | Flow | Client type | client_id | Secret location | Redirect | Refresh semantics | Verification status |
|---|---|---|---|---|---|---|---|
| Google (Gmail — `gmail.modify` only) | Auth code + PKCE, loopback | Installed (secret bundled, sanctioned) | ☐ | ☐ | `http://localhost:8420/...` | Non-rotating refresh token | ☐ verification / CASA in progress |
| Google (Calendar — `calendar.events` only) | same app as Gmail | — | ☐ | ☐ | same | same | same |
| Microsoft (Mail + Cal) | Auth code + PKCE, loopback | **Public — no secret** | ☐ | n/a | `http://localhost` (port ignored) | Non-rotating (MSAL-style) | ☐ Partner Center ☐ publisher domain ☐ verified badge |
| Slack | Auth code + PKCE | **Public — no secret** (PKCE enabled, irreversible) | ☐ | n/a | ☐ resolve: localhost vs `laya://` vs bounce page | Rotating if custom scheme (30-day idle expiry) | n/a (public distribution activated: ☐) |
| Jira | Auth code | Confidential — **embedded secret** | ☐ | ☐ | `http://localhost:8420/...` | Rotating; 90-day idle / 365-day absolute | sharing enabled: ☐ (Marketplace review deferred) |
| Bitbucket | Auth code | Confidential — **embedded secret** | ☐ | ☐ | `http://localhost:8420/...` | Rotating; 3-month idle expiry | n/a |
| GitHub | **Device flow** | Public GitHub App — no secret used | ☐ | n/a | n/a (device flow) | 8 h access / 6-month refresh, or expiration disabled: ☐ record choice | n/a |
| Linear | Auth code + PKCE | **Public — secret omitted** | ☐ | n/a | `http://localhost:8420/...` | Rotating, 24 h access, 30-min replay grace | n/a |
| Notion | Auth code | Confidential — **embedded secret** | ☐ | ☐ | `http://localhost:8420/...` | Rotating when refresh_token present; may be null; no documented TTL | n/a (Marketplace deferred) |

## 5. Suggested order of execution

1. **Buy/configure the custom domain** and re-point landing privacy/terms URLs (§2) — blocks Microsoft.
2. **Start Microsoft Partner Center business verification** — longest external lead time (~days–2 weeks); the rest of the Microsoft registration can proceed in parallel.
3. **Self-serve registrations in one sitting** (each <1 h of console work): Slack (resolve the redirect question while in the console), Atlassian/Jira, Bitbucket, GitHub App, Linear, Notion.
4. Fill in the §4 registry as each completes; perform each section's foreign-account authorization test.
5. Hand the completed registry to the engineering phase (bundled defaults, new `OAUTH_CONFIGS` entries, device flow, connect-UX migration — see the approved plan).
