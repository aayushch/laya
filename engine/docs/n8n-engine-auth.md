# n8n → Engine Authentication & Workflow Update Tracking

**Status:** Implemented (issue #47, 2026-10-11)
**Related:** issue #13 (unauthenticated local REST API), PR #40 (app bearer token),
`engine/docs/engine-auth-followups.md`

## 1. What this protects

After PR #40 every REST route requires the app bearer token except the four the bundled n8n
workflows call. Until this change those four were open to any local process: it could post
synthetic events (LLM spend, Action Cards), post ingestion errors, and read repo and workflow
config. This document describes the credential n8n now presents, how it reaches both sides,
how enforcement is switched on without losing events on upgrade, and the per-clone update
tracking that enforcement depends on.

### Three distinct credentials

| Credential | Protects | Caller | Holder |
|---|---|---|---|
| MCP bearer token | `/mcp` SSE endpoint | external agents | keychain |
| App token (PR #40, #45) | every REST route and the `/ws` handshake | Tauri webview, tray | shell, per launch |
| **n8n token (this doc)** | the four n8n routes only | bundled workflows | shell, per launch |

The n8n token is a different secret from the app token on purpose. Workflow expressions can
read n8n's environment (`N8N_BLOCK_ENV_ACCESS_IN_NODE=false`, needed for `$env.LAYA_ENGINE_URL`),
and the shell strips the app token from n8n's environment for exactly that reason. A
compromised n8n therefore holds a token that opens four routes and nothing else.

## 2. Token lifecycle

| Step | Where |
|---|---|
| Mint | `ui/src-tauri/src/lib.rs` `n8n_token()`: a second `OnceLock`, same 32-byte hex generator as `engine_token()`. Dev builds honor a preset `LAYA_N8N_TOKEN`. |
| Share with the engine | `sidecar.rs` sets `LAYA_N8N_TOKEN` on both the dev and prod engine spawn |
| Share with n8n | `n8n.rs` `spawn_n8n()` sets the same `LAYA_N8N_TOKEN`, next to `LAYA_ENGINE_URL` |
| Engine reads | `keychain.ensure_n8n_token()`, called in the lifespan after `ensure_engine_token()`: pops the variable (so CLI agents the engine spawns don't inherit it) and pins it; else keychain `laya_n8n_token`; else mints. Records the source: `env`, `keychain`, `memory` |
| n8n sends | every engine-calling node has a header parameter `X-Laya-N8n-Token: ={{ $env.LAYA_N8N_TOKEN }}` |
| Engine checks | `security/engine_auth.py` `require_engine_auth`, see §3 |

One variable name for both processes. n8n reads it at execution time, so a per-launch token
is always current: an engine respawn and "Start n8n" on the Status page both reuse the value
held by the shell's `OnceLock`. There is no credential object in n8n, no API push, no
rotation UI and no grace window.

**Standalone engine / hand-started n8n.** The engine falls back to a keychain token, but n8n
cannot learn that value, so the operator sets `LAYA_N8N_TOKEN` on both processes (the same
way `LAYA_ENGINE_URL` must be set on n8n). Readiness (§4) refuses to enforce while the token
did not come from the environment, so a mismatch shows up on the Settings panel instead of as
rejected polls.

## 3. Engine-side check

`require_engine_auth` is the single router-level dependency. It keeps the app-token behaviour
for every route and adds a route table:

- `N8N_ROUTES`: `POST /events`, `POST /ingestion-errors`, `GET /repos`, plus `GET /metadata/<key>`
  (prefix; the list route `/metadata` and all metadata writes stay app-token only).
- On an n8n route: app bearer → OK; `X-Laya-N8n-Token` matches (`hmac.compare_digest` on
  bytes) → OK; neither → consult the mode.
- On every other route the n8n header is ignored, so the n8n token grants nothing there.

`GET /repos` is also called by the UI, which keeps sending the app bearer. "Accept either" is
a superset of both callers' previous access, so neither regresses.

### Modes (`security/n8n_auth.py`)

Stored in `settings.json` under `n8n_auth`: `{"mode": "log" | "enforce", "enforced_since"}`.

- **`log`**: a request with neither credential is logged (`n8n_auth_missing`), counted and let
  through.
- **`enforce`**: 401 with a human-readable detail pointing at the Settings panel.
- **Hatch**: `LAYA_N8N_AUTH=off` forces `log` for the session without touching the setting.

## 4. Readiness: when enforcement switches on

`n8n_auth.evaluate_readiness()` flips `log` → `enforce` when all of these hold, and never flips
back:

1. the engine's token came from the launcher environment (`source == "env"`), the only case
   where n8n is known to hold the same value;
2. the shared error handler is deployed at the bundled template version;
3. every ingestion template that has at least one clone is recorded at its bundled version in
   `workflow_versions.json`, i.e. the header-sending template has been pushed to its clones;
4. no `sources` row carries `workflow_update_error`.

A fresh install (no clones, no `workflow_versions.json`) needs only condition 1.

It runs at lifespan startup (right after the token is resolved), at the end of every
`import_workflows` (the startup sync), and after a manual "Update workflow". `status_report()`
returns the unmet conditions in plain language for the Settings panel.

**Upgrade path.** An existing install boots with clones that run the previous template and
send no header. Mode is `log`, so their polls proceed. The bumped `laya_version` on all twelve
templates makes the startup sync push the header-sending version to every clone and the error
handler; when that completes with nothing flagged, readiness flips to `enforce`. A clone that
fails later stays flagged on its row and is rejected with 401 until "Update workflow" repairs
it; the system does not downgrade.

## 5. Workflow templates

All 17 engine-calling nodes across 12 files carry the header: 11 × "POST to Laya Engine",
3 × "Get Repos" (GitHub, Bitbucket, Bitbucket Server), "Fetch Server Config" (Bitbucket Server),
"Fetch Channel Config" (Slack), "POST Repo Errors" (GitHub) and the error handler's "POST to
Laya". The 11 event POST nodes also set `retryOnFail` (3 tries, 5 s apart) so a brief engine
restart does not lose a poll; `POST /events` already ignores duplicates.
`tests/test_workflow_templates.py` guards both invariants.

Because the header is a plain parameter and not a credential object, nothing in the clone or
propagation path touches it: `_merge_credentials` and the platform credential matcher are
unaffected, and Bitbucket Server's own `httpHeaderAuth` credential cannot collide with it.

## 6. Per-clone workflow update tracking

`workflow_versions.json` keeps one deployed version per template. It cannot say which clone of
that template failed to take an update, and before this change a failed push was recorded as
deployed and never retried. Readiness depends on knowing that every clone really carries the
header, so failures are now tracked per clone.

- Migration `073_sources_workflow_update_status.sql`: `sources.workflow_update_error`,
  `sources.workflow_update_failed_at`. NULL while the clone is up to date.
- `_update_workflow` returns `(ok, reason)` with short user-facing reasons: "Workflow no longer
  exists in n8n", "n8n rejected the update (HTTP …)", "n8n unreachable".
- `update_clone(row, template, ctx)` is the per-clone body shared by startup propagation and the
  manual endpoint. It writes the result to the row; an unexpected exception is recorded as that
  clone's error rather than aborting the loop.
- `_propagate_to_clones` also re-pushes every flagged clone, whether or not its template's
  version changed (`force=True` pushes all). `import_workflows` records template versions only
  after propagation ran to completion.
- The error handler's version is recorded only after its update succeeded; its failure is
  kept in the metadata row `n8n-error-handler:update_error`.
- The platform credential matcher that was duplicated between `connections.py` and
  `n8n_bootstrap.py` is one `inject_platform_credential`.
- `POST /sources/{source_id}/update-workflow` re-pushes one clone and re-runs readiness.
  `SourceResponse` carries the two columns.
- Settings → Integrations → n8n: a warning icon with the reason on flagged rows and an
  always-present "Update workflow" button enabled only while the row is flagged.

## 7. Settings panel

The n8n status card has an "Engine credential" block fed by `GET /settings/n8n/auth-status`:
mode (Log mode / Enforced since …), where the token came from, requests without a valid token
since start and the last one, the error handler's last update failure, the hatch banner while
`LAYA_N8N_AUTH=off`, and the list of conditions still blocking enforcement.

## 8. Edge cases

| Case | What happens |
|---|---|
| Upgrade of an existing install | `log` until the startup sync pushed every clone; then `enforce` |
| Clone update fails during upgrade | Flagged on its row, retried next startup, "Update workflow"; readiness waits |
| Clone update fails after enforcement | Its polls get 401 (counted on the panel) until "Update workflow"; no downgrade |
| Engine respawned by the shell | Same token from the shell's `OnceLock` |
| n8n restarted from the Status page | Same token |
| Engine started by hand without `LAYA_N8N_TOKEN` | Keychain/minted token; readiness stays `log` and says why; set the variable on both processes |
| n8n started by hand without `LAYA_N8N_TOKEN` | Its calls carry an empty header; logged in `log` mode, rejected in `enforce`; the panel shows the count |
| Operator needs ingestion back now | `LAYA_N8N_AUTH=off` on the engine for that session |
| Editing `settings.json` to `log` by hand | Re-promoted to `enforce` at the next readiness check |
| Tests / local curl | Send the app bearer, or `X-Laya-N8n-Token` with the dev-preset value |

## 9. Optional follow-up: cursor safety when "POST to Laya" fails

Not part of this change. Gmail, Google Calendar and Outlook Email use n8n's built-in polling
triggers, whose "what's new" position advances before downstream nodes run, and that tracking
stays as is. Slack keeps its own `staticData.lastPollTs` cursor; Jira, GitHub, Bitbucket and
Bitbucket Server use a fixed look-back. For the workflows whose tracking we control, a cursor
committed only after the POST succeeded would make a failed POST re-fetch instead of lose the
event. Retry on the POST nodes (§5) already covers short engine outages for all workflows.
