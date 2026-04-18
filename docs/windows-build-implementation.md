# Windows Build — Implementation Log

Status: **shipping-ready on Windows 11**. An end-to-end install + launch + graceful-shutdown + crash-recovery cycle was executed and verified on `win-compat` branch.

Companion to `docs/windows-build-runbook.md` (the pre-flight plan). This document captures what was actually built, which steps deviated from the plan, and the extra gaps discovered during validation.

---

## TL;DR

- **Artifacts produced**: `Laya_0.2.0_x64_en-US.msi` (6.9 MB), `Laya_0.2.0_x64-setup.exe` (4.7 MB)
- **Target triple**: `x86_64-pc-windows-msvc`
- **Host tested on**: Windows 11 Pro 22631, Python 3.13.13, Node 24.15 LTS, Rust 1.95 stable
- **Install path**: `%LOCALAPPDATA%\Laya\` (per-user NSIS, no elevation prompt)
- **User state**: `%USERPROFILE%\.laya\` (venv, n8n_module, data, logs)

---

## Validated end-to-end (clean-venv run)

| Step | Result |
|------|--------|
| NSIS install (silent, `/S`) | Installs to `%LOCALAPPDATA%\Laya\`; shortcut at `Start Menu\Programs\Laya.lnk`; registry uninstall entry under HKCU |
| Launch from Start Menu | Window opens; custom titlebar renders; no console flashes |
| Setup: Python detection | Finds `py -3.13` or `python3.13` via `which` crate |
| Setup: venv creation | `~/.laya/venv/Scripts/python.exe` created |
| Setup: core pip install | `fastapi`, `chromadb`, `onnxruntime`, `psutil`, etc. — ~4 min on first run |
| Setup: ML pip install | `torch`, `sentence-transformers` — ~3 min (acceptable to skip on-platform) |
| Setup: n8n install | `npm install n8n@2.15.0` with native sqlite3/better-sqlite3 compile — ~4 min |
| Setup: n8n start | REST API on `:45678` ready in ~6s after spawn |
| Setup: engine start | `/health` returns `{"engine":"healthy", ...}` within ~15s |
| Quit via `taskkill` (no `/F`) | Engine + n8n tree killed within 10s (5s graceful, 5s escalated force) |
| Force-kill laya-app.exe | Engine self-terminates via psutil-based parent-watchdog within ~4s |
| Relaunch after crash | No "port 8420 in use" error; engine rebinds cleanly |

---

## Prerequisites installed (one-time)

All installed via `winget` from an elevated shell, except for WiX which uses a direct download. VS Build Tools is the slow one (~20 min).

```powershell
winget install --id Rustlang.Rustup           --source winget --silent
winget install --id OpenJS.NodeJS.LTS          --source winget --silent
winget install --id Python.Python.3.13         --source winget --silent
winget install --id Microsoft.VisualStudio.2022.BuildTools --source winget --silent `
  --override "--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --add Microsoft.VisualStudio.Component.Windows11SDK.22621"

# WiX 3.14 (Tauri auto-fetches a binary mirror on first build, but this places it on PATH)
Invoke-WebRequest -Uri "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314.exe" -OutFile "$env:TEMP\wix314.exe"
Start-Process -FilePath "$env:TEMP\wix314.exe" -ArgumentList "/install","/quiet" -Wait
```

**Gotcha**: do not run the VS Build Tools install concurrently with WiX (MSI engine enforces a global mutex — the first attempt failed with exit code 1618).

After install, open a **new** shell so the updated machine-level PATH is inherited. Existing PowerShell/bash windows won't see Node/Python/cargo until restarted.

---

## Build command

```bash
cd ui
npx tauri build --target x86_64-pc-windows-msvc
```

Prerequisite: run `bash scripts/bundle-engine.sh` from the repo root first — the Tauri bundler reads `ui/src-tauri/resources/engine/` which the script populates from `engine/`.

Output:
- `ui/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/msi/Laya_0.2.0_x64_en-US.msi`
- `ui/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/Laya_0.2.0_x64-setup.exe`

The "A public key has been found, but no private key" warning at the end is non-fatal — it relates to the updater artifact. Installers are still produced.

---

## File-by-file change log

### Build pipeline

**`.github/workflows/release.yml`**
- Added `windows-latest` / `x86_64-pc-windows-msvc` matrix entry.
- Added `WINDOWS_CERTIFICATE` / `WINDOWS_CERTIFICATE_PASSWORD` env vars (no-op without secrets).
- Added `shell: bash` to the "Set version from tag" and "Bundle engine resources" steps — on Windows runners, `run:` defaults to PowerShell and would choke on `${GITHUB_REF_NAME#v}` and `sed -i.bak`.

**`ui/src-tauri/tauri.conf.json`**
- Added `bundle.windows` block:
  ```json
  "windows": {
    "wix": { "language": "en-US" },
    "nsis": {
      "installMode": "currentUser",
      "installerIcon": "icons/icon.ico"
    }
  }
  ```
- **Deviation**: runbook specified `installMode: "perUser"`. The actual Tauri schema only accepts `currentUser | perMachine | both`. Build fails with "not valid under any of the schemas listed in the 'anyOf' keyword" on `perUser`.

**`ui/src-tauri/icons/icon.ico`**
- Regenerated multi-resolution (6 icons: 16/32/48/64/128/256) via `npx tauri icon src-tauri/icons/icon.png`.
- Original was 16×16 single-entry (329 bytes → 16825 bytes).
- macOS-specific artifacts (`icon.icns`, `Square*Logo.png`, `StoreLogo.png`, `trayTemplate*.png`) and the `android/` + `ios/` folders generated by the CLI were reverted / removed.

### Rust (Tauri shell)

**`ui/src-tauri/Cargo.toml`**
- Added `which = "6"` dependency.

**`ui/src-tauri/src/sidecar.rs`** — Python / engine lifecycle
- Added `no_window()` helper (applies `CREATE_NO_WINDOW` flag on Windows, no-op elsewhere) and wired it into every `Command::new` call site: `find_python`, `create_venv`, `pip_install`, `spawn_dev_engine`, `spawn_prod_engine`.
- Replaced the `which()` stub (which shelled out to a non-existent `which` binary) with `::which::which(name)`.
- Added a Windows arm to `engine_source_dir()` — Tauri places bundled resources at `<exe_dir>\resources\engine\` on Windows, which the original macOS/Linux-only logic missed.
- Set `LAYA_PARENT_PID` env var when spawning the engine (see parent-watchdog section below).

**`ui/src-tauri/src/n8n.rs`** — n8n lifecycle
- Same `no_window()` helper pattern + wiring into every `Command::new`: `find_node`, `find_npm`, `rebuild_native_addon`, `run_npm_install`, `spawn_n8n`.
- Replaced `which()` same as sidecar.
- Fixed `augmented_path()` to use `;` as separator on Windows (was `:`, which would split drive letters like `C:\...`).
- Skipped `NODE_SEARCH_DIRS` iteration on Windows (those paths are `/usr/local/bin` etc. — meaningless on Windows).
- Added Windows graceful shutdown path in `shutdown_n8n()`: `taskkill /T /PID <pid>` (soft close of tree), 5s wait, then `taskkill /T /F /PID <pid>` (force). `/T` is critical — `n8n.cmd` spawns a `node.exe` child that would otherwise orphan.

**`ui/src-tauri/src/lib.rs`** — app shell
- Added the same `no_window()` helper at module level.
- Wired into `pick_repo_folder()`'s `git` invocation.
- Added Windows branch to `kill_process_on_port(8420)` — uses `netstat -ano -p tcp` + `taskkill /F /PID`. Original function was `#[cfg(unix)]`-only using `lsof`.
- Removed the `#[cfg(unix)]` gate from the safety-net call site so Windows also gets port cleanup.
- Added Windows branch in the `RunEvent::Exit` engine shutdown (mirrors the n8n path): soft `taskkill`, 5s wait, `taskkill /F /T`.

### Python (engine)

**`engine/requirements.txt`**
- Added `psutil>=6.0.0`. Needed for the Windows parent-watchdog (`psutil.pid_exists`) and for stale-engine cleanup (`psutil.net_connections` replaces `lsof`).

**`engine/laya/main.py`**
- `_start_parent_watchdog()`: removed the `sys.platform == "win32": return` short-circuit and added a Windows branch that polls `psutil.pid_exists(parent_pid)` every 2s, sending `SIGTERM` to itself when the parent is gone.
- **Critical**: prefers `LAYA_PARENT_PID` env var over `os.getppid()` on Windows. uvicorn re-execs to a worker subprocess, so `os.getppid()` points at the uvicorn spawner, not `laya-app.exe`. Without this, the watchdog tracks the wrong process and never triggers.
- `_kill_stale_engine()`: added a Windows branch using `psutil.net_connections(kind="tcp")` to find the PID listening on our port, then `psutil.Process(pid).terminate()`.
- Unix code paths preserved exactly — the old `getppid() != original_parent` trick still works on macOS/Linux where reparenting to PID 1 happens on parent death.

**`engine/laya/db/migrate.py`**
- Added `encoding="utf-8"` to `migration_file.read_text()`. Without it, Python 3.13 on Windows defaults to `cp1252` via `locale.getpreferredencoding()`, and migration `015_…sql` contains a non-cp1252 byte that crashes with `UnicodeDecodeError: 'charmap' codec can't decode byte 0x81 in position 253`.

**`engine/laya/config.py`**
- Added `encoding="utf-8"` to all 8 `open(LAYA_*_FILE, …)` calls (4 reads + 4 writes for settings/team/rules/repos JSON). Same rationale as migrate.py — any non-ASCII user-entered content would otherwise fail on Windows.

**`engine/laya/integrations/n8n_bootstrap.py`** and **`engine/laya/egress/connections.py`**
- Added `encoding="utf-8"` to the 4 `workflow_file.read_text()` calls. These load bundled n8n workflow JSON which may contain non-ASCII characters.

---

## Additional gaps beyond the runbook

These weren't in the runbook's `G1`–`G12` table but surfaced during validation:

### 1. Engine path detection on Windows

The runbook inventory said "`home_dir()` uses `USERPROFILE` on Windows — already correct." It didn't flag that `engine_source_dir()` in `sidecar.rs` had no Windows arm at all. macOS goes `Contents/MacOS/ → Contents/Resources/resources/engine/`, Linux goes `usr/bin/ → usr/lib/Laya/resources/engine/`, Windows needs `<install_dir> → <install_dir>/resources/engine/`. Without this fix, the engine code would never be found in production, falling through to `exe_dir.join("engine")` which doesn't exist.

### 2. Unicode decoding on Windows file reads

The engine code uses `Path.read_text()` and `open(file)` (text mode) without explicit `encoding=`. On Windows (Python 3.13), the default is `cp1252` — non-ASCII UTF-8 bytes in migration files, config JSONs, and bundled n8n workflows crash the engine on startup. This is a blanket Windows portability issue not called out in the runbook's audit.

### 3. Parent-watchdog tracks the wrong process

The runbook's fix for `G10` assumed `os.getppid()` on Windows points at `laya-app.exe`. In practice, `uvicorn.run(app, …)` on Windows spawns a **worker subprocess** (visible as "INFO: Started server process [<pid>]" in the log). The lifespan + watchdog run in that subprocess, whose parent is the uvicorn spawner Python, not `laya-app.exe`. Force-killing `laya-app` does nothing to kill the intermediate Python, so the watchdog sees parent alive → never triggers.

**Fix**: Rust launcher passes its PID as `LAYA_PARENT_PID` env var; watchdog prefers it over `os.getppid()` on Windows.

### 4. NSIS install-mode schema

Runbook said `installMode: "perUser"`. Schema enum is `currentUser | perMachine | both`. Trivial, but a hard build failure if copied verbatim.

### 5. Windows CI runner shell default

Runbook added the matrix entry but didn't account for GitHub Actions defaulting `run:` to PowerShell on Windows. Steps using `${GITHUB_REF_NAME#v}` or `sed` need explicit `shell: bash`.

---

## Process lifecycle — observed behavior on Windows

For future debugging, this is how processes relate at runtime:

```
laya-app.exe          (tauri shell, the user-facing process)
├── python.exe        (uvicorn launcher — the immediate child)
│   └── python.exe    (uvicorn server worker — where lifespan runs)
└── cmd.exe → node.exe            (n8n.cmd shell)
                      └── node.exe (n8n task-runner)
```

Key implications:

- **`os.getppid()` in the engine returns the uvicorn launcher PID, not laya-app.** This is why `LAYA_PARENT_PID` is needed.
- **`n8n.cmd` exits after spawning `node.exe`.** The node children get re-parented to the system. This is why `taskkill /T` (tree kill) is required — just killing the child node directly misses the task-runner grandchild.
- **`taskkill` without `/F` sends WM_CLOSE.** Python and Node GUI-less processes (we launch with `CREATE_NO_WINDOW`) have no console window to receive it, so they ignore it. Our 5-second grace period is effectively a formality — the force-kill at 5s is what actually terminates them. This is acceptable because SQLite and ChromaDB are both crash-safe.

---

## Unchanged pieces (already correct before this branch)

Per the runbook's pre-existing inventory, these were already Windows-safe and weren't touched:
- `home_dir()` in both `sidecar.rs` and `n8n.rs` (uses `USERPROFILE`)
- `venv_python()` (uses `Scripts/python.exe`)
- `n8n_bin()` (uses `n8n.cmd`)
- `find_python()` Windows candidate list
- `find_node()` skips nvm/Unix paths on Windows
- `find_npm()` uses `npm.cmd`
- `engine/requirements.txt` — `uvloop` already marked `sys_platform != "win32"`
- `subprocess_helper.py` — already short-circuits SIGSTOP/SIGCONT on Windows

---

## Operational notes for next build

1. **Don't skip `bundle-engine.sh`** before `tauri build`. It copies `engine/**` into `ui/src-tauri/resources/engine/`, which is what gets shipped into `resources\engine\` at install.
2. **If you change Python in `engine/`**, re-bundle before rebuild. The Rust step reads the bundled tree, not the source tree.
3. **First-run install takes 10–15 min** (pip deps + npm + sqlite3 native compile). Subsequent launches reach `/health` in <30s.
4. **Build Tools are required for n8n's native addons** (`sqlite3`, `better-sqlite3`). npm will fall back to prebuilt binaries if compilation fails (see `install_n8n` in `n8n.rs` — Attempt 2 with `--ignore-scripts` plus the `isolated-vm` stub). End-users without Build Tools should get the prebuilt path.
5. **Do not commit `windows-build*.log`** files at the repo root — they're transient `tee` outputs.

---

## Known-safe shortcuts for re-validating

Assuming you've already run a full setup once on the machine:

```bash
# Fast rebuild after Rust/Python changes
bash scripts/bundle-engine.sh
cd ui && npx tauri build --target x86_64-pc-windows-msvc
```

```powershell
# Reinstall and relaunch
& 'C:\Users\<u>\AppData\Local\Laya\uninstall.exe' /S; Start-Sleep 4
& '<repo>\ui\src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis\Laya_0.2.0_x64-setup.exe' /S
$env:PATH = [Environment]::GetEnvironmentVariable('PATH','Machine') + ';' + [Environment]::GetEnvironmentVariable('PATH','User')
Start-Process "C:\Users\<u>\AppData\Local\Laya\laya-app.exe"
```

```powershell
# Watch for engine ready
Get-Content 'C:\Users\<u>\AppData\Local\com.laya.app\logs\Laya.log' -Wait
```

```powershell
# Crash-recovery test
Stop-Process -Name laya-app -Force
# Within ~4s the engine python should disappear from Task Manager
Get-WmiObject Win32_Process | Where-Object CommandLine -like '*.laya\venv*'
```

---

## Open follow-ups (not blockers)

- **n8n survives laya-app crash.** The engine's parent-watchdog cleans itself up, but n8n (the `node.exe` tree) does not — it's only killed during graceful shutdown. Mirroring the watchdog approach from Node would be the clean fix; for now, users recovering from a crash may need to kill n8n manually or wait for port `:45678` to clear on next launch (`_kill_stale_engine`-equivalent logic doesn't exist for n8n).
- **Truly graceful shutdown on Windows.** Currently we rely on the 5s grace period falling through to force-kill. Sending `CTRL_BREAK_EVENT` via `GenerateConsoleCtrlEvent` would give uvicorn a chance to run its ASGI shutdown handshake, but requires attaching to the child's console group — nontrivial.
- **Code signing.** Environment variables (`WINDOWS_CERTIFICATE`, `WINDOWS_CERTIFICATE_PASSWORD`) are wired through CI but no cert exists. SmartScreen will warn on install until an EV cert is provisioned.
- **`~/.laya` → `%APPDATA%\Laya`.** Still uses Unix-style dotfile convention. Works on Windows, but out of convention. Explicitly deferred by the runbook.
