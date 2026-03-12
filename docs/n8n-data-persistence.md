# n8n Data Persistence

How n8n stores data in Laya, what survives upgrades, what doesn't, and options for improving durability.

## Current Setup

Laya runs n8n as a Docker container (`laya-n8n`) via `docker-compose.yml`. Data is persisted using a **Docker named volume**:

```yaml
volumes:
  - n8n_data:/home/node/.n8n
```

Docker Compose prefixes the project name, so the actual volume is **`laya_n8n_data`**.

## Volume Location

| Layer | Path |
|-------|------|
| Docker volume name | `laya_n8n_data` |
| Docker host path | `/var/lib/docker/volumes/laya_n8n_data/_data` |
| Inside container | `/home/node/.n8n` |

On macOS, the Docker host path is inside Docker Desktop's Linux VM — not directly accessible from the macOS filesystem.

## What n8n Stores in the Volume

| File/Directory | Contents |
|---------------|----------|
| `database.sqlite` | Workflows, executions, credentials (encrypted), user accounts, settings |
| `encryptionKey` | AES key used to encrypt credentials at rest |
| `config` | n8n runtime configuration |
| `nodes/` | Community nodes (if installed) |

### Credentials

n8n stores credentials (API keys, OAuth tokens, passwords) **encrypted in its own SQLite database**, using an AES encryption key stored at `/home/node/.n8n/encryptionKey` inside the volume.

n8n does **not** use the macOS Keychain or Windows Credential Manager. All credential data lives entirely within the Docker volume.

If the volume is deleted, both the encrypted credentials **and** the encryption key are lost — credentials cannot be recovered.

## What Survives and What Doesn't

### Safe Operations (data preserved)

| Operation | Data safe? | Reason |
|-----------|-----------|--------|
| Laya app update (new .dmg/.msi) | Yes | Volume is in Docker, not in the app bundle |
| `docker compose down` (normal shutdown) | Yes | Named volumes survive container removal |
| Pulling new `n8nio/n8n:latest` image | Yes | Volume is independent of the image |
| Rebuilding/recreating the container | Yes | Volume is re-attached on next start |
| Deleting `~/.laya/` folder | Yes | Laya engine data is separate from the n8n volume |
| macOS/Windows OS update | Yes | Docker volumes persist across OS updates |

### Destructive Operations (data lost)

| Operation | Data lost? | Why |
|-----------|-----------|-----|
| `docker compose down -v` | **Yes** | The `-v` flag explicitly removes volumes |
| `docker volume rm laya_n8n_data` | **Yes** | Direct volume deletion |
| `docker system prune --volumes` | **Yes** | Prunes all unused volumes |
| Docker Desktop factory reset | **Yes** | Wipes the entire Docker VM |
| Uninstalling Docker Desktop with "Remove all data" | **Yes** | Deletes all Docker state including volumes |
| `docker system prune` (without `--volumes`) | No | Does not touch volumes by default |

## Options for Improving Durability

### Option 1: Bind Mount to ~/.laya/n8n-data

Replace the named volume with a bind mount to a user-visible directory:

```yaml
volumes:
  - ${HOME}/.laya/n8n-data:/home/node/.n8n
```

**Pros:**
- Data is visible on the host filesystem and easy to back up
- Survives Docker Desktop uninstall/reset
- Survives `docker system prune --volumes`
- Users can manually copy the directory to a new machine

**Cons:**
- File permission issues possible (container runs as `node` UID 1000)
- Slightly more complex path resolution across platforms
- On macOS, Docker file sharing for bind mounts has minor performance overhead

### Option 2: Automated Workflow Export

Have the Laya engine periodically export n8n workflows via the n8n REST API and save them to `~/.laya/backups/`:

```
GET /api/v1/workflows  →  ~/.laya/backups/n8n-workflows-<date>.json
```

**Pros:**
- Workflows are recoverable even if the volume is destroyed
- Human-readable JSON backup
- Can be version-controlled or synced to cloud storage

**Cons:**
- Credentials are NOT included in workflow exports (n8n strips them)
- Execution history is not exported
- Requires the engine to re-import and re-configure credentials after a restore

### Option 3: Volume Backup on Shutdown

On app shutdown, before running `docker compose down`, copy the volume contents to a backup location:

```bash
docker run --rm -v laya_n8n_data:/source -v ~/.laya/backups:/dest \
  alpine cp -a /source/. /dest/n8n-volume-backup/
```

**Pros:**
- Full backup including credentials and encryption key
- Automatic, no user action needed
- Can restore by copying back into the volume

**Cons:**
- Adds a few seconds to shutdown
- Backup includes the encryption key (security consideration — should be protected)
- Volume backup grows with execution history

### Option 4: Combine Bind Mount + Workflow Export

Use a bind mount (Option 1) for primary persistence, plus periodic workflow export (Option 2) as a safety net. This provides the best durability:

- Bind mount survives Docker resets
- Workflow exports survive even if `~/.laya/n8n-data` is deleted (credentials would need re-entry)

## Recommendation

**Short term:** Switch to a bind mount at `~/.laya/n8n-data` (Option 1). This is the simplest change that makes n8n data survive Docker resets and makes it easy for users to back up.

**Medium term:** Add automated workflow export (Option 2) as a backup layer. Workflows are the most valuable artifact — credentials can be re-entered, but complex workflow logic is hard to recreate.

## Inspecting the Volume

```bash
# Check volume exists
docker volume inspect laya_n8n_data

# List volume contents
docker run --rm -v laya_n8n_data:/data alpine ls -la /data

# Back up volume to a tar file
docker run --rm -v laya_n8n_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/n8n-backup.tar.gz -C /data .

# Restore volume from a tar file
docker run --rm -v laya_n8n_data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/n8n-backup.tar.gz -C /data
```
