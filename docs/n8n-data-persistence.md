# n8n Data Persistence

How n8n stores data in Laya, what survives upgrades, and backup strategies.

## Current Setup

Laya installs n8n locally via npm into `~/.laya/n8n_module/` and runs it as a Node.js process on port 45678. All n8n runtime data is stored in `~/.laya/n8n/`.

## Data Location

| Item | Path |
|------|------|
| n8n installation | `~/.laya/n8n_module/node_modules/n8n/` |
| n8n binary | `~/.laya/n8n_module/node_modules/.bin/n8n` |
| n8n data directory | `~/.laya/n8n/` |

## What n8n Stores

| File/Directory | Contents |
|---------------|----------|
| `database.sqlite` | Workflows, executions, credentials (encrypted), user accounts, settings |
| `encryptionKey` | AES key used to encrypt credentials at rest |
| `config` | n8n runtime configuration |
| `nodes/` | Community nodes (if installed) |

### Credentials

n8n stores credentials (API keys, OAuth tokens, passwords) **encrypted in its own SQLite database**, using an AES encryption key stored at `~/.laya/n8n-data/encryptionKey`.

n8n does **not** use the macOS Keychain or Windows Credential Manager. All credential data lives in the n8n data directory.

If the data directory is deleted, both the encrypted credentials **and** the encryption key are lost — credentials cannot be recovered.

## Installation Strategy

The Tauri app installs n8n using a two-attempt strategy:

1. **Attempt 1 (full):** `npm install n8n` with native addon compilation (isolated-vm). This provides the best sandboxing for code execution nodes. Requires a working C++ toolchain (Xcode CLT on macOS) and `setuptools` (for the `distutils` shim needed by `node-gyp` on Python 3.12+).

2. **Attempt 2 (fallback):** If native compilation fails, retries with `--ignore-scripts` to skip `node-gyp` builds. n8n still works but code execution nodes run without isolated-vm sandboxing.

## What Survives and What Doesn't

### Safe Operations (data preserved)

| Operation | Data safe? | Reason |
|-----------|-----------|--------|
| Laya app update (new .dmg/.msi) | Yes | Data is in `~/.laya/`, not in the app bundle |
| Reinstalling n8n (npm install) | Yes | Data directory is separate from the npm install directory |
| macOS/Windows OS update | Yes | User home directory is preserved |

### Destructive Operations (data lost)

| Operation | Data lost? | Why |
|-----------|-----------|-----|
| Deleting `~/.laya/n8n/` | **Yes** | Direct data deletion |
| Deleting `~/.laya/` entirely | **Yes** | Removes all Laya data including n8n |

## Backup Strategies

### Option 1: Automated Workflow Export

Have the Laya engine periodically export n8n workflows via the n8n REST API and save them to `~/.laya/backups/`:

```
GET /api/v1/workflows  →  ~/.laya/backups/n8n-workflows-<date>.json
```

**Pros:**
- Workflows are recoverable even if the data directory is deleted
- Human-readable JSON backup
- Can be version-controlled or synced to cloud storage

**Cons:**
- Credentials are NOT included in workflow exports (n8n strips them)
- Execution history is not exported
- Requires re-import and credential re-entry after a restore

### Option 2: Directory Backup

Back up the entire `~/.laya/n8n/` directory:

```bash
# Back up
tar czf ~/n8n-backup.tar.gz -C ~/.laya/n8n-data .

# Restore
tar xzf ~/n8n-backup.tar.gz -C ~/.laya/n8n-data
```

**Pros:**
- Full backup including credentials and encryption key
- Can restore completely without re-entering credentials

**Cons:**
- Backup includes the encryption key (security consideration — should be protected)
- Backup grows with execution history
