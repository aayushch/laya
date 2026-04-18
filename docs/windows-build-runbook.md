# Windows Build Implementation Runbook (Laya)

> **Audience**: A future Claude Code instance running natively on a clean Windows 11 machine, with this repository checked out and standard tools (Bash via Git Bash or PowerShell, Read/Write/Edit). This document is self-contained — read it top-to-bottom and execute each phase in order.

---

## 1. What you are doing

Implementing Windows compatibility for **Laya**, a local-first Tauri + SvelteKit + Python desktop app. Currently builds and runs on macOS and Linux but has never been built or installed on Windows. The deliverable is a Windows `.msi` and NSIS `setup.exe` installer that installs and runs cleanly on Windows 11.

### Goals
- Produce Windows installers via CI (GitHub Actions) and locally for verification
- Installed app launches, completes first-run setup, runs the engine + n8n, shuts down cleanly without orphans
- Artifacts published as part of the existing `v*` release tag flow

### Non-goals (do NOT work on these)
- Developer setup on Windows (`scripts/setup-dev.ps1` etc.)
- Migrating `~/.laya` → `%APPDATA%\Laya`
- Using a real production code-signing certificate (only wire the env vars)

---

## 2. Background — what's already correct

Re-audit of the repo as of this writing (`git log` shows latest = `a3d3f79 Fixed UI issues with Coherence`) shows ~half of the items in the older `docs/windows-compatibility.md` audit have been quietly fixed. **Do not re-do these:**

- `home_dir()` uses `USERPROFILE` on Windows (`sidecar.rs:18-23`, `n8n.rs:33-41`)
- `venv_python()` uses `Scripts/python.exe` on Windows (`sidecar.rs:36-41`, `n8n.rs:293-299`)
- `n8n_bin()` uses `n8n.cmd` on Windows (`n8n.rs:60-70`)
- `find_python()` has a Windows candidate list (`sidecar.rs:168-191`)
- `find_node()` skips Unix paths and nvm on Windows (`n8n.rs:107-133`)
- `find_npm()` uses `npm.cmd` on Windows (`n8n.rs:254-258`)
- uvloop is excluded on Windows (`engine/requirements.txt:6`)
- `subprocess_helper.py` SIGSTOP/SIGCONT short-circuit on Windows (lines 158, 170)
- `_start_parent_watchdog()` returns early on Windows (`engine/laya/main.py:62-63`) — but we will enable a real Windows path
- `_kill_stale_engine()` Unix block is gated (`engine/laya/main.py:104`) — same
- `tauri-plugin-decorum` supports Windows in v1.x

### Real remaining gaps
| # | Severity | File | Issue |
|---|----------|------|-------|
| G1 | Critical (build) | `.github/workflows/release.yml` | No Windows matrix entry |
| G2 | Critical (build) | `ui/src-tauri/tauri.conf.json` | No `bundle.windows` config |
| G3 | Critical (build) | `ui/src-tauri/icons/icon.ico` | 16×16 only |
| G4 | Critical (runtime) | `ui/src-tauri/src/n8n.rs:240-247` | `augmented_path()` uses `:` |
| G5 | Critical (runtime) | `sidecar.rs:226-238`, `n8n.rs:85-96` | `which()` shells out to `which` binary |
| G6 | High (UX) | every `Command::new` site | Console window flashes |
| G7 | High (UX) | `lib.rs:719-723` | Engine bare-kill on Windows |
| G8 | High (UX) | `n8n.rs:730-735` | n8n bare-kill, child node.exe leaks |
| G9 | High (UX) | `lib.rs:381-397, 732-735` | Port-cleanup is `#[cfg(unix)]` only |
| G10 | High (UX) | `engine/laya/main.py:62-63` | Parent-watchdog disabled on Windows |
| G11 | High (UX) | `engine/laya/main.py:104` | `_kill_stale_engine` lsof-only |
| G12 | Critical (runtime) | n8n native addons | sqlite3/better-sqlite3 need MSVC; verify prebuilt fallback |

---

## 3. Prerequisites — install everything before starting

Run from an **elevated PowerShell** (right-click → Run as Administrator). Use `winget` where possible. Verify each step before moving on.

### 3.1 Visual Studio Build Tools 2022 (~6–8 GB)
Required by the Rust MSVC toolchain AND by `node-gyp` for n8n's native addons.

```powershell
winget install --id Microsoft.VisualStudio.2022.BuildTools --silent --override "--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

Manual: https://visualstudio.microsoft.com/visual-cpp-build-tools/ → "Desktop development with C++" workload. Reboot if prompted.

### 3.2 Rust toolchain
```powershell
winget install --id Rustlang.Rustup --silent
# Then in a NEW PowerShell:
rustup default stable
rustup target add x86_64-pc-windows-msvc
```
Verify: `rustc --version`, `cargo --version`.

### 3.3 Node.js 22 LTS
```powershell
winget install --id OpenJS.NodeJS.LTS --silent
```
Verify in a new PowerShell: `node --version` (≥ 22), `npm --version`.

### 3.4 Python 3.13
```powershell
winget install --id Python.Python.3.13 --silent
```
Verify: `python --version`. If it shows the Microsoft Store stub, use `py -3.13` and prepend the real install dir to PATH.

### 3.5 Git for Windows (likely already present)
```powershell
winget install --id Git.Git --silent
```
Provides Git Bash, used by `bundle-engine.sh`.

### 3.6 WiX Toolset 3.14 (for `.msi`)
NSIS is bundled by Tauri itself; only WiX needs separate install.
```powershell
Invoke-WebRequest -Uri "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314.exe" -OutFile "$env:TEMP\wix314.exe"
Start-Process -FilePath "$env:TEMP\wix314.exe" -ArgumentList "/install /quiet" -Wait
$env:PATH += ";C:\Program Files (x86)\WiX Toolset v3.14\bin"
```

### 3.7 WebView2 Runtime (preinstalled on Windows 11)
Verify: `Get-Package | Where-Object Name -like "*WebView2*"`. If missing, install Evergreen Standalone from https://developer.microsoft.com/en-us/microsoft-edge/webview2/

### 3.8 ImageMagick (for icon regeneration; optional — Tauri CLI also works)
```powershell
winget install --id ImageMagick.ImageMagick --silent
```

### 3.9 Verification — capture and save
```powershell
$prereqs = @"
rustc:  $(rustc --version)
cargo:  $(cargo --version)
node:   $(node --version)
npm:    $(npm --version)
python: $(python --version)
git:    $(git --version)
"@
$prereqs | Out-File -FilePath windows-prereqs.txt
Get-Content windows-prereqs.txt
```

---

## 4. Phase 0 — Smoke-test the current build (DO NOT skip)

Build the unmodified codebase. This confirms the toolchain works and isolates compile-time issues from runtime ones.

```powershell
cd <repo-root>\ui
npm ci
cd ..
& "C:\Program Files\Git\bin\bash.exe" scripts/bundle-engine.sh
cd ui
npx tauri build --target x86_64-pc-windows-msvc 2>&1 | Tee-Object -FilePath ..\windows-build-phase0.log
```

**Expected**: build likely succeeds. Outputs:
- `ui\src-tauri\target\release\bundle\msi\Laya_*_x64_en-US.msi`
- `ui\src-tauri\target\release\bundle\nsis\Laya_*_x64-setup.exe`

**If anything fails**, fix that single error before proceeding. Do NOT start Phase 1 until the unmodified codebase produces installer artifacts (with potentially-stale icon warnings is fine).

Common failures:
- `link.exe not found` → Build Tools not installed (Step 3.1)
- `candle.exe not found` → WiX not on PATH (Step 3.6)
- `bash: command not found` → use the explicit path shown above

---

## 5. Phase 1 — Make the build pipeline produce Windows installers

### 5.1 Add Windows to CI matrix
File: `.github/workflows/release.yml`. In `jobs.build.strategy.matrix.include`, append:

```yaml
          - platform: windows-latest
            target: x86_64-pc-windows-msvc
```

(`tauri-action` auto-installs WiX/NSIS on the runner.)

Optionally also add Windows code-signing env vars in the `env:` block under "Build and release":

```yaml
          # Windows code signing (no-op without secrets)
          WINDOWS_CERTIFICATE: ${{ secrets.WINDOWS_CERTIFICATE }}
          WINDOWS_CERTIFICATE_PASSWORD: ${{ secrets.WINDOWS_CERTIFICATE_PASSWORD }}
```

### 5.2 Add Windows bundle config
File: `ui/src-tauri/tauri.conf.json`. Inside `bundle`, add a `windows` key (after `macOS`):

```json
    "windows": {
      "wix": {
        "language": "en-US"
      },
      "nsis": {
        "installMode": "perUser",
        "installerIcon": "icons/icon.ico"
      }
    }
```

`installMode: perUser` avoids elevation prompts at install time.

### 5.3 Regenerate `icon.ico` as multi-resolution
Current file is 16×16 only. Use Tauri's icon generator (idempotent on PNG sources):

```powershell
cd ui
npx tauri icon src-tauri/icons/icon.png
```

Review the diff with `git diff src-tauri/icons/`. The macOS-specific files (`icon_macos.png`, `icon.icns`, tray templates, Square*Logo.png) should be left alone; restore them from git if overwritten:

```powershell
git checkout src-tauri/icons/icon_macos.png src-tauri/icons/icon.icns src-tauri/icons/trayTemplate.png src-tauri/icons/trayTemplate@2x.png src-tauri/icons/Square*.png src-tauri/icons/StoreLogo.png
```

Verify `icon.ico` now contains multiple resolutions:
```powershell
magick identify ui\src-tauri\icons\icon.ico
```

### 5.4 Verify Phase 1
```powershell
cd ui
npx tauri build --target x86_64-pc-windows-msvc
```

Both `Laya_*_x64-setup.exe` and `Laya_*_x64_en-US.msi` should exist. Install the NSIS one (per-user, no elevation), confirm Add/Remove Programs lists Laya with the proper icon. Don't try to launch yet — runtime fixes come in Phase 2/3.

---

## 6. Phase 2 — Critical runtime fixes

### 6.1 Add the `which` crate
File: `ui/src-tauri/Cargo.toml`. Under `[dependencies]`, add:
```toml
which = "6"
```

### 6.2 Replace `which()` in `sidecar.rs`
File: `ui/src-tauri/src/sidecar.rs`, lines 226-238.

Replace the entire function with:
```rust
fn which(name: &str) -> Option<PathBuf> {
    let p = PathBuf::from(name);
    if p.is_absolute() && p.exists() {
        return Some(p);
    }
    ::which::which(name).ok()
}
```

### 6.3 Replace `which()` in `n8n.rs`
File: `ui/src-tauri/src/n8n.rs`, lines 85-96.

Replace with:
```rust
fn which(name: &str) -> Option<PathBuf> {
    let p = PathBuf::from(name);
    if p.is_absolute() && p.exists() {
        return Some(p);
    }
    ::which::which(name).ok()
}
```

### 6.4 Fix PATH separator in `augmented_path()`
File: `ui/src-tauri/src/n8n.rs`, lines 220-248.

Replace the entire function body with:
```rust
fn augmented_path() -> String {
    let sep_char = if cfg!(windows) { ';' } else { ':' };
    let sep_str = if cfg!(windows) { ";" } else { ":" };
    let current = std::env::var("PATH").unwrap_or_default();
    let mut front: Vec<String> = Vec::new();

    if let Some(dir) = node_bin_dir() {
        front.push(dir);
    }

    // NODE_SEARCH_DIRS is Unix-only; on Windows rely on PATH from the installer.
    if !cfg!(windows) {
        for dir in NODE_SEARCH_DIRS {
            let d = dir.to_string();
            if !front.contains(&d) && std::path::Path::new(dir).is_dir() {
                front.push(d);
            }
        }
    }

    let remaining: Vec<&str> = current
        .split(sep_char)
        .filter(|p| !front.iter().any(|f| f == p))
        .collect();

    let mut parts = front;
    parts.extend(remaining.into_iter().map(String::from));
    parts.join(sep_str)
}
```

### 6.5 Suppress console window flashes (CREATE_NO_WINDOW)
Every `Command::new(...)` flashes a console window on Windows. Add a helper to **each** of `sidecar.rs`, `n8n.rs`, and `lib.rs`:

```rust
#[cfg(windows)]
fn no_window(cmd: &mut std::process::Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn no_window(_cmd: &mut std::process::Command) {}
```

Then call `no_window(&mut cmd);` after every `Command::new(...)` in:

**`sidecar.rs`**:
- `find_python` line ~197 (after the `cmd.args(["--version"]);` line, before `sanitize_python_cmd`)
- `create_venv` line ~250 (right after `let mut cmd = Command::new(python);`)
- `pip_install` line ~329 (right after `let mut cmd = Command::new(&python);`)
- `spawn_dev_engine` line ~443 (right after `let mut cmd = Command::new(&python);`)
- `spawn_prod_engine` line ~485 (right after `let mut cmd = Command::new(&python);`)

**`n8n.rs`**:
- `find_node` line ~140 (after `let mut cmd = Command::new(candidate);`)
- `find_npm` line ~269 — refactor: the chained call needs to be broken up:
  ```rust
  let mut probe = Command::new(npm_name);
  no_window(&mut probe);
  let ok = probe.arg("--version").stdout(Stdio::null()).stderr(Stdio::null()).status().map(|s| s.success()).unwrap_or(false);
  if ok { return Ok(npm_name.to_string()); }
  ```
- `rebuild_native_addon` line ~446 (after `let mut cmd = Command::new(npm);`)
- `run_npm_install` line ~482 (after `let mut cmd = Command::new(npm);`)
- `spawn_n8n` line ~621 (after `let mut cmd = Command::new(&bin);`)

**`lib.rs`**:
- The git invocation in `pick_repo_folder` line ~62 — refactor to use a `let mut cmd = Command::new("git"); no_window(&mut cmd); cmd.args(...)` pattern
- The Windows shutdown paths added in Phase 3 will use `no_window` too

### 6.6 Verify Phase 2
```powershell
cd ui
npx tauri build --target x86_64-pc-windows-msvc
```
Reinstall, launch the app. The setup wizard should now find Python and Node correctly, attempt venv creation, and attempt n8n install. **No console window flashes**.

If n8n install hangs or fails on `npm install n8n@2.15.0`:
- Inspect `~/.laya/logs/` (Tauri streams it)
- The fallback path in `n8n.rs:341-378` (`--ignore-scripts` then `npm rebuild sqlite3 better-sqlite3`) should kick in
- With Build Tools (Step 3.1) installed, native addon compile should succeed
- Without Build Tools (end-user case), prebuilt binaries from npmjs.org should be downloaded

If both fail on a clean machine without Build Tools, add error messaging in `install_n8n` directing users to install Build Tools (acceptable Phase-2.5 follow-up; do not block Phase 3 on this).

---

## 7. Phase 3 — Process lifecycle and orphan cleanup

### 7.1 Graceful engine shutdown on Windows
File: `ui/src-tauri/src/lib.rs`, lines 719-724.

Replace:
```rust
                                #[cfg(not(unix))]
                                {
                                    log::info!("Killing engine process");
                                    let _ = child.kill();
                                    let _ = child.wait();
                                }
```

With:
```rust
                                #[cfg(windows)]
                                {
                                    let pid = child.id();
                                    log::info!("Stopping engine process (pid {})", pid);

                                    let mut tk = std::process::Command::new("taskkill");
                                    no_window(&mut tk);
                                    let _ = tk.args(["/PID", &pid.to_string()]).status();

                                    let start = std::time::Instant::now();
                                    loop {
                                        match child.try_wait() {
                                            Ok(Some(_)) => {
                                                log::info!("Engine exited gracefully");
                                                break;
                                            }
                                            Ok(None) => {
                                                if start.elapsed() > Duration::from_secs(5) {
                                                    log::warn!("Engine did not exit; force-killing");
                                                    let mut force = std::process::Command::new("taskkill");
                                                    no_window(&mut force);
                                                    let _ = force.args(["/PID", &pid.to_string(), "/F", "/T"]).status();
                                                    let _ = child.wait();
                                                    break;
                                                }
                                                std::thread::sleep(Duration::from_millis(100));
                                            }
                                            Err(_) => {
                                                let _ = child.kill();
                                                break;
                                            }
                                        }
                                    }
                                }
```

### 7.2 Graceful n8n shutdown on Windows
File: `ui/src-tauri/src/n8n.rs`, lines 730-736.

Replace:
```rust
            #[cfg(not(unix))]
            {
                log::info!("Killing n8n process");
                let _ = child.kill();
                let _ = child.wait();
            }
```

With:
```rust
            #[cfg(windows)]
            {
                log::info!("Stopping n8n process tree (pid {})", pid);
                let mut tk = Command::new("taskkill");
                no_window(&mut tk);
                let _ = tk.args(["/T", "/PID", &pid.to_string()]).status();

                let start = std::time::Instant::now();
                loop {
                    match child.try_wait() {
                        Ok(Some(_)) => { log::info!("n8n exited gracefully"); break; }
                        Ok(None) => {
                            if start.elapsed() > Duration::from_secs(5) {
                                log::warn!("n8n did not exit; force-killing tree");
                                let mut force = Command::new("taskkill");
                                no_window(&mut force);
                                let _ = force.args(["/T", "/F", "/PID", &pid.to_string()]).status();
                                let _ = child.wait();
                                break;
                            }
                            std::thread::sleep(Duration::from_millis(100));
                        }
                        Err(_) => { let _ = child.kill(); break; }
                    }
                }
            }
```

The `/T` flag kills the entire process tree (n8n's `n8n.cmd` spawns a `node.exe` child).

### 7.3 Windows port-cleanup safety net
File: `ui/src-tauri/src/lib.rs`.

Below the existing `#[cfg(unix)] fn kill_process_on_port(port: u16)` (lines 381-397), add:

```rust
#[cfg(windows)]
fn kill_process_on_port(port: u16) {
    let mut netstat = std::process::Command::new("netstat");
    no_window(&mut netstat);
    if let Ok(output) = netstat.args(["-ano", "-p", "tcp"]).output() {
        if !output.status.success() { return; }
        let stdout = String::from_utf8_lossy(&output.stdout);
        let needle = format!(":{}", port);
        for line in stdout.lines() {
            if line.contains(&needle) && line.contains("LISTENING") {
                if let Some(pid) = line.split_whitespace().last().and_then(|s| s.parse::<u32>().ok()) {
                    log::info!("Killing orphaned process on port {} (pid {})", port, pid);
                    let mut tk = std::process::Command::new("taskkill");
                    no_window(&mut tk);
                    let _ = tk.args(["/F", "/PID", &pid.to_string()]).status();
                }
            }
        }
    }
}
```

Then update the call site at line ~732. Replace:
```rust
                    #[cfg(unix)]
                    if !killed_engine {
                        kill_process_on_port(8420);
                    }
```
With:
```rust
                    if !killed_engine {
                        kill_process_on_port(8420);
                    }
```

### 7.4 Add `psutil` to engine requirements
File: `engine/requirements.txt`. Append:
```
psutil>=6.0.0
```
(Verify it isn't already a transitive dep with `pip show psutil` after installing into a fresh venv.)

### 7.5 Enable Python parent-watchdog on Windows
File: `engine/laya/main.py`, lines 54-88.

Replace the entire `_start_parent_watchdog` function with:

```python
def _start_parent_watchdog() -> None:
    """Monitor parent process and shut down if it disappears."""
    import threading
    import time

    parent_pid = os.getppid()
    if parent_pid <= 1:
        return

    if sys.platform == "win32":
        try:
            import psutil
        except ImportError:
            log.warning("parent_watchdog_unavailable_no_psutil")
            return

        def _watch_win():
            while True:
                time.sleep(2)
                try:
                    if not psutil.pid_exists(parent_pid):
                        log.info("parent_exited", original_parent=parent_pid)
                        os.kill(os.getpid(), signal.SIGTERM)
                        return
                except Exception:
                    pass

        t = threading.Thread(target=_watch_win, daemon=True, name="parent-watchdog")
        t.start()
        log.info("parent_watchdog_started", parent_pid=parent_pid, mode="psutil")
        return

    def _watch():
        while True:
            time.sleep(2)
            current_parent = os.getppid()
            if current_parent != parent_pid:
                log.info(
                    "parent_exited",
                    original_parent=parent_pid,
                    new_parent=current_parent,
                )
                os.kill(os.getpid(), signal.SIGTERM)
                return

    t = threading.Thread(target=_watch, daemon=True, name="parent-watchdog")
    t.start()
    log.info("parent_watchdog_started", parent_pid=parent_pid)
```

### 7.6 Enable Python `_kill_stale_engine` on Windows
File: `engine/laya/main.py`, lines 91-125.

Replace the entire function with:

```python
def _kill_stale_engine(host: str, port: int) -> None:
    """If another process is holding our port, kill it before we proceed."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        sock.close()
        return
    except OSError:
        sock.close()

    log.warning("port_in_use", host=host, port=port)

    if sys.platform == "win32":
        try:
            import psutil
            my_pid = os.getpid()
            for conn in psutil.net_connections(kind="tcp"):
                if (conn.laddr and conn.laddr.port == port
                        and conn.status == psutil.CONN_LISTEN
                        and conn.pid and conn.pid != my_pid):
                    log.warning("killing_stale_engine", pid=conn.pid, port=port)
                    try:
                        psutil.Process(conn.pid).terminate()
                    except Exception:
                        pass
            import time
            time.sleep(1)
        except Exception as e:
            log.warning("stale_engine_cleanup_failed", error=str(e))
        return

    import subprocess
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-s", "tcp:listen"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = [int(p) for p in result.stdout.strip().split("\n") if p.strip()]
        my_pid = os.getpid()
        for pid in pids:
            if pid != my_pid:
                log.warning("killing_stale_engine", pid=pid, port=port)
                os.kill(pid, signal.SIGTERM)
        if pids:
            import time
            time.sleep(1)
    except Exception as e:
        log.warning("stale_engine_cleanup_failed", error=str(e))
```

### 7.7 Verify Phase 3
Rebuild, reinstall. Then:
```powershell
# 1. Launch app, wait for engine + n8n to be ready
# 2. Quit via tray menu → wait 10s → check Task Manager
Get-Process | Where-Object Name -in 'python','node' | Format-Table Name, Id, StartTime
# Expect: no laya-related python.exe or node.exe
```
Then test crash recovery:
```powershell
# 1. Launch app
# 2. Find the Laya.exe PID and force-kill via Task Manager
# 3. Within ~3 seconds, the engine python.exe should self-terminate (parent-watchdog)
# 4. Relaunch app — should work without "port 8420 in use"
```

---

## 8. Phase 4 — Polish (do these last)

### 8.1 Custom titlebar visual check
After Phase 3 verification works, check:
- The 38px overlay titlebar from `tauri-plugin-decorum` renders cleanly
- Dragging the titlebar moves the window
- Hovering the maximize button shows Snap Layouts on Windows 11

If the overlay misbehaves on Windows, fall back: in `lib.rs:467-471`, gate `create_overlay_titlebar()` to non-Windows, and remove `decorations: false` from `tauri.conf.json` for Windows builds (this means Windows users get a native titlebar — acceptable for v1).

### 8.2 Self-signed cert smoke-test (optional)
For local install testing, generate a self-signed cert and configure tauri to use it. SmartScreen will still warn; that's expected without an EV cert. Don't commit any cert.

---

## 9. End-to-end validation checklist

Run on a **clean Windows 11 VM** or freshly-reset user account (no Python, no Node, no Build Tools).

| # | Step | Pass criteria |
|---|------|---------------|
| 1 | CI green on Windows job | `.msi` and `setup.exe` published to GitHub release |
| 2 | Download `setup.exe`, run it | No elevation prompt; installs to `%LOCALAPPDATA%\Programs\Laya\` |
| 3 | Launch app from Start Menu | Window opens, custom (or native) titlebar visible |
| 4 | Setup wizard step "Python" | If missing, prompts user to install; after install + retry → succeeds |
| 5 | Setup wizard step "Node" | Same as Python |
| 6 | Setup wizard step "venv" | `C:\Users\<u>\.laya\venv\Scripts\python.exe` exists |
| 7 | Setup wizard step "deps" | Core requirements install; ML may fail (acceptable) |
| 8 | Setup wizard step "n8n" | npm install completes (with prebuilds OR after stub fallback) |
| 9 | n8n REST API reachable | `curl http://127.0.0.1:45678/rest/settings` returns 200 |
| 10 | Engine reachable | `curl http://127.0.0.1:8420/health` returns 200 |
| 11 | Quit via tray → wait 10s → Task Manager | No `python.exe`/`node.exe` belonging to Laya |
| 12 | Force-kill `Laya.exe` from Task Manager | Engine `python.exe` exits within ~3s (parent-watchdog) |
| 13 | Relaunch | No "port in use" error |
| 14 | Store API key in Settings | Survives restart; visible in `Credential Manager → Web Credentials` |
| 15 | Push test event via n8n | Card appears in feed |
| 16 | No console flashes | Throughout 1–15, no flashing windows |

---

## 10. Debugging guide

### Build phase
| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `error: linker 'link.exe' not found` | VS Build Tools missing | Install Step 3.1 |
| `error: failed to find tool. rustc?` | rustup PATH not refreshed | Open new shell |
| `tauri build` → `candle.exe not found` | WiX not on PATH | Install Step 3.6 |
| `npm ci` → sharp/node-gyp errors | Build Tools missing | Install 3.1, then `npm cache clean --force` |
| Icon validation errors | `icon.ico` 16×16 only | Run Phase 1.3 |
| `bash: command not found` in Phase 0 | Git Bash not on PATH | Use `& "C:\Program Files\Git\bin\bash.exe" ...` |

### Runtime phase
| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Blank window | WebView2 missing | Install Evergreen |
| Setup says "n8n install failed" | sqlite3/better-sqlite3 prebuilt missing | Inspect `~/.laya/logs/` for npm errors; user may need Build Tools |
| Engine never reaches /health | Port 8420 occupied or Python launch failed | `~/.laya/logs/engine-stdout.log` |
| Console windows flash | Phase 6.5 incomplete | Audit each `Command::new` |
| Orphan node.exe after quit | Phase 7.2 not applied | Confirm `taskkill /T` runs |
| Engine survives Tauri force-kill | Phase 7.5 not applied | Verify `psutil` is installed in venv |

### Logs to capture for any bug report
- `%USERPROFILE%\.laya\logs\engine-stdout.log`
- `%USERPROFILE%\.laya\logs\n8n.log`
- `%USERPROFILE%\.laya\logs\pip-install.log`
- `%USERPROFILE%\.laya\logs\Laya.log` (Tauri plugin-log)
- `Get-Process | Where-Object Name -in 'python','node','Laya'`

---

## 11. File reference table

| File | Phase(s) | Type of change |
|------|----------|----------------|
| `.github/workflows/release.yml` | 5.1, 8.2 | Add Windows matrix entry; signing env vars |
| `ui/src-tauri/tauri.conf.json` | 5.2 | Add `bundle.windows` section |
| `ui/src-tauri/icons/icon.ico` | 5.3 | Regenerate multi-res |
| `ui/src-tauri/Cargo.toml` | 6.1 | Add `which = "6"` |
| `ui/src-tauri/src/sidecar.rs` | 6.2, 6.5 | Replace `which()`; `no_window` helper + calls |
| `ui/src-tauri/src/n8n.rs` | 6.3, 6.4, 6.5, 7.2 | Replace `which()`; fix PATH separator; `no_window` calls; Windows shutdown |
| `ui/src-tauri/src/lib.rs` | 6.5, 7.1, 7.3 | `no_window` helper; Windows engine shutdown; Windows port cleanup |
| `engine/laya/main.py` | 7.5, 7.6 | Windows parent-watchdog; Windows stale-engine cleanup |
| `engine/requirements.txt` | 7.4 | Add `psutil>=6.0.0` |

---

## 12. Things you must NOT modify

- `scripts/setup-dev.sh`, `scripts/dev.sh`, `n8n/import.sh` — developer setup, out of scope
- `ui/src/` Svelte frontend — pure HTTP/WS, OS-agnostic
- `engine/laya/agents/subprocess_helper.py` — already guards SIGSTOP/SIGCONT
- AppImage sanitization (`sanitize_python_cmd`, `sanitize_node_cmd`) — harmless on Windows
- `~/.laya` directory naming — works on Windows; defer to a future task
- macOS `.app`/cocoa code paths — already correctly gated with `#[cfg(target_os = "macos")]`
- Linux `.AppImage`/GTK code paths — already gated with `#[cfg(target_os = "linux")]`

---

## 13. Suggested commit boundaries

To keep PRs reviewable:
1. **PR 1 (Phase 1)**: CI matrix + tauri.conf.json + icon.ico → ships Windows installer artifacts (won't run yet, but builds)
2. **PR 2 (Phase 2)**: which crate + PATH separator + no_window + n8n addon verification notes
3. **PR 3 (Phase 3)**: Process lifecycle (engine + n8n shutdown, port cleanup, parent-watchdog, psutil)
4. **PR 4 (Phase 4)**: Titlebar polish, code-signing scaffolding

Each PR should leave the macOS and Linux builds green.

---

End of runbook. If you hit something not covered here, capture the error and the file/line, then make the most-conservative fix (do not refactor surrounding code).
