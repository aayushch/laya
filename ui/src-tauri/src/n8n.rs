//! n8n process lifecycle management.
//!
//! Manages n8n as a local Node.js process instead of a Docker container.
//! n8n is installed via npm into ~/.laya/n8n_module/ and stores its data
//! in ~/.laya/n8n/.

use std::io::BufRead;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

/// Port for the Laya-managed n8n instance.
/// Chosen to avoid conflicts with a user's own n8n (default 5678)
/// and is not assigned to any well-known service.
pub const N8N_PORT: u16 = 45678;

// ── Path helpers ────────────────────────────────────────────────────────

fn home_dir() -> Option<PathBuf> {
    #[cfg(unix)]
    {
        std::env::var_os("HOME").map(PathBuf::from)
    }
    #[cfg(windows)]
    {
        std::env::var_os("USERPROFILE").map(PathBuf::from)
    }
}

fn laya_home() -> PathBuf {
    home_dir().unwrap_or_default().join(".laya")
}

/// Where the n8n npm package is installed: ~/.laya/n8n_module/
fn n8n_module_dir() -> PathBuf {
    laya_home().join("n8n_module")
}

/// Where n8n stores its data (DB, encryption key, credentials): ~/.laya/n8n/
fn n8n_data_dir() -> PathBuf {
    laya_home().join("n8n")
}

/// Path to the n8n binary inside the local npm install.
fn n8n_bin() -> PathBuf {
    if cfg!(target_os = "windows") {
        n8n_module_dir()
            .join("node_modules")
            .join(".bin")
            .join("n8n.cmd")
    } else {
        n8n_module_dir()
            .join("node_modules")
            .join(".bin")
            .join("n8n")
    }
}

// ── Node.js detection ───────────────────────────────────────────────────

/// Well-known Node.js install directories on macOS / Linux.
/// macOS .app bundles inherit a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin)
/// that does NOT include these, so we must search them explicitly.
const NODE_SEARCH_DIRS: &[&str] = &[
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/usr/bin",
];

/// Resolve a bare command name to its absolute path via `which`.
fn which(name: &str) -> Option<PathBuf> {
    let p = PathBuf::from(name);
    if p.is_absolute() && p.exists() {
        return Some(p);
    }
    Command::new("which")
        .arg(name)
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| PathBuf::from(String::from_utf8_lossy(&o.stdout).trim()))
}

/// Find a Node.js 18+ installation.
/// Returns (absolute_path, version_string).
pub fn find_node() -> Result<(String, String), String> {
    // Build candidate list: bare name first (works from terminal),
    // then absolute paths in well-known directories.
    let mut candidates: Vec<PathBuf> = vec![PathBuf::from("node")];
    if !cfg!(target_os = "windows") {
        for dir in NODE_SEARCH_DIRS {
            candidates.push(PathBuf::from(dir).join("node"));
        }
        // Also check nvm default if it exists
        if let Some(home) = home_dir() {
            let nvm_node = home.join(".nvm/versions/node");
            if nvm_node.is_dir() {
                // Find the latest installed nvm version
                if let Ok(entries) = std::fs::read_dir(&nvm_node) {
                    let mut versions: Vec<PathBuf> = entries
                        .filter_map(|e| e.ok())
                        .map(|e| e.path())
                        .filter(|p| p.join("bin/node").exists())
                        .collect();
                    versions.sort();
                    if let Some(latest) = versions.last() {
                        candidates.push(latest.join("bin/node"));
                    }
                }
            }
        }
    }

    for candidate in &candidates {
        let name = candidate.to_string_lossy();
        if let Ok(output) = Command::new(candidate)
            .arg("--version")
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .output()
        {
            if output.status.success() {
                let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
                if let Some(ver) = version.strip_prefix('v') {
                    let parts: Vec<&str> = ver.split('.').collect();
                    if parts.len() >= 2 {
                        let major: u32 = parts[0].parse().unwrap_or(0);
                        if major >= 18 {
                            // Resolve to absolute path
                            let abs_path = if candidate.is_absolute() && candidate.exists() {
                                candidate.to_string_lossy().to_string()
                            } else {
                                which(&name)
                                    .map(|p| p.to_string_lossy().to_string())
                                    .unwrap_or_else(|| name.to_string())
                            };
                            return Ok((abs_path, version));
                        }
                    }
                }
            }
        }
    }

    Err("Node.js 18+ not found. Please install Node.js from https://nodejs.org".to_string())
}

/// Get the directory containing the node binary (for augmenting PATH).
fn node_bin_dir() -> Option<String> {
    find_node()
        .ok()
        .and_then(|(path, _)| {
            PathBuf::from(&path)
                .parent()
                .map(|p| p.to_string_lossy().to_string())
        })
}

/// Build an augmented PATH that includes the node binary directory and
/// other well-known locations. This is critical for macOS .app bundles
/// where PATH is minimal (/usr/bin:/bin:/usr/sbin:/sbin).
///
/// Both `npm` (which invokes `node` internally) and the n8n binary
/// (which uses `#!/usr/bin/env node`) need `node` on PATH.
fn augmented_path() -> String {
    let current = std::env::var("PATH").unwrap_or_default();
    let mut extra_dirs: Vec<String> = Vec::new();

    // Add the directory where we found node
    if let Some(dir) = node_bin_dir() {
        if !current.contains(&dir) {
            extra_dirs.push(dir);
        }
    }

    // Also add well-known dirs that might contain npm/npx/node
    for dir in NODE_SEARCH_DIRS {
        if !current.contains(dir) && std::path::Path::new(dir).is_dir() {
            extra_dirs.push(dir.to_string());
        }
    }

    if extra_dirs.is_empty() {
        current
    } else {
        format!("{}:{}", extra_dirs.join(":"), current)
    }
}

/// Resolve the npm binary from the same directory as the node binary.
fn find_npm() -> Result<String, String> {
    let (node_path, _) = find_node()?;

    let npm_name = if cfg!(target_os = "windows") {
        "npm.cmd"
    } else {
        "npm"
    };

    // Try npm alongside node (e.g., /opt/homebrew/bin/npm)
    if let Some(parent) = std::path::Path::new(&node_path).parent() {
        let npm_path = parent.join(npm_name);
        if npm_path.exists() {
            return Ok(npm_path.to_string_lossy().to_string());
        }
    }

    // Fall back to bare "npm" on PATH
    if Command::new(npm_name)
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
    {
        return Ok(npm_name.to_string());
    }

    Err("npm not found. It should be installed alongside Node.js.".to_string())
}

// ── n8n installation ────────────────────────────────────────────────────

/// Check if n8n is installed in ~/.laya/n8n_module/.
pub fn is_n8n_installed() -> bool {
    n8n_bin().exists()
}

/// Path to the Python binary in the Laya managed venv.
/// Used to give `node-gyp` a Python that has `setuptools` (which
/// provides the `distutils` shim removed in Python 3.12+).
fn venv_python() -> PathBuf {
    if cfg!(target_os = "windows") {
        laya_home().join("venv").join("Scripts").join("python.exe")
    } else {
        laya_home().join("venv").join("bin").join("python")
    }
}

/// Install n8n via npm into ~/.laya/n8n_module/.
/// Calls `on_line` for each line of npm output (for progress reporting).
pub fn install_n8n<F: FnMut(&str)>(mut on_line: F) -> Result<(), String> {
    let npm = find_npm()?;

    let module_dir = n8n_module_dir();
    std::fs::create_dir_all(&module_dir)
        .map_err(|e| format!("Failed to create {}: {e}", module_dir.display()))?;

    // Also create n8n data directory
    std::fs::create_dir_all(n8n_data_dir())
        .map_err(|e| format!("Failed to create n8n data directory: {e}"))?;

    log::info!("Installing n8n via npm into {}", module_dir.display());
    on_line("Installing n8n (this may take a few minutes)...");

    let path = augmented_path();
    log::info!("npm install PATH: {}", path);

    let mut cmd = Command::new(&npm);
    cmd.args([
        "install",
        "--prefix",
        &module_dir.to_string_lossy(),
        "n8n",
    ])
    .env("PATH", &path);

    // Point node-gyp at a Python that has `setuptools` installed
    // (provides the `distutils` shim that node-gyp needs).
    // Without this, native addons like `isolated-vm` fail to compile
    // on Python 3.12+ where `distutils` was removed from stdlib.
    //
    // Preference order:
    // 1. Laya managed venv (~/.laya/venv/bin/python) — production
    // 2. Dev engine venv  (engine/.venv/bin/python)  — development
    // 3. Skip — npm will use system Python (may lack setuptools)
    let venv_py = venv_python();
    let dev_venv_py = {
        // In dev mode, the engine venv is at <repo>/engine/.venv/
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        manifest.join("..").join("..").join("engine").join(".venv").join("bin").join("python")
    };
    let gyp_python = if venv_py.exists() {
        Some(venv_py)
    } else if cfg!(dev) && dev_venv_py.exists() {
        Some(dev_venv_py)
    } else {
        None
    };
    if let Some(ref py) = gyp_python {
        log::info!("Setting node-gyp Python to {}", py.display());
        cmd.env("npm_config_python", py);
    } else {
        log::warn!("No venv Python found for node-gyp; native addons may fail to compile");
    }

    let mut child = cmd
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to run npm install: {e}"))?;

    // Read stdout + stderr for progress
    let mut output_lines: Vec<String> = Vec::new();

    if let Some(stdout) = child.stdout.take() {
        let stderr = child.stderr.take();
        let reader = std::io::BufReader::new(stdout);
        for line in reader.lines() {
            if let Ok(line) = line {
                on_line(&line);
                output_lines.push(line);
            }
        }
        if let Some(stderr) = stderr {
            let reader = std::io::BufReader::new(stderr);
            for line in reader.lines() {
                if let Ok(line) = line {
                    on_line(&line);
                    output_lines.push(line);
                }
            }
        }
    }

    let status = child.wait().map_err(|e| format!("npm wait failed: {e}"))?;
    if !status.success() {
        let tail: Vec<&str> = output_lines.iter().rev().take(5).map(|s| s.as_str()).collect();
        let detail = tail.into_iter().rev().collect::<Vec<_>>().join("\n");
        return Err(format!(
            "npm install n8n failed (exit code {}).\nLast output:\n{}",
            status.code().unwrap_or(-1),
            detail,
        ));
    }

    if !n8n_bin().exists() {
        return Err("n8n binary not found after npm install".to_string());
    }

    log::info!("n8n installed successfully at {}", n8n_bin().display());
    on_line("n8n installed successfully");
    Ok(())
}

// ── n8n process management ──────────────────────────────────────────────

/// Global n8n child process handle.
static N8N_PROCESS: Mutex<Option<Child>> = Mutex::new(None);

/// Result of attempting to start n8n.
#[derive(Debug)]
pub enum N8nStartResult {
    Started,
    AlreadyRunning,
    NodeNotFound(String),
    N8nNotInstalled(String),
    StartFailed(String),
}

/// Start the n8n process. Called on app startup.
pub fn startup_n8n() -> N8nStartResult {
    // 1. Already running? (check HTTP health)
    if is_n8n_running() {
        return N8nStartResult::AlreadyRunning;
    }

    // 2. Node.js available?
    if find_node().is_err() {
        return N8nStartResult::NodeNotFound(
            "Node.js is not installed. Install Node.js 18+ from https://nodejs.org to enable n8n integrations.".to_string(),
        );
    }

    // 3. n8n installed?
    if !is_n8n_installed() {
        return N8nStartResult::N8nNotInstalled(
            "n8n is not installed yet. It will be installed during setup.".to_string(),
        );
    }

    // 4. Start
    match spawn_n8n() {
        Ok(child) => {
            if let Ok(mut guard) = N8N_PROCESS.lock() {
                *guard = Some(child);
            }
            log::info!("n8n started on port {}", N8N_PORT);
            N8nStartResult::Started
        }
        Err(e) => N8nStartResult::StartFailed(e),
    }
}

/// Spawn the n8n process with the correct environment variables.
fn spawn_n8n() -> Result<Child, String> {
    let bin = n8n_bin();
    let data_dir = n8n_data_dir();

    std::fs::create_dir_all(&data_dir)
        .map_err(|e| format!("Failed to create n8n data dir: {e}"))?;

    let log_dir = laya_home().join("logs");
    std::fs::create_dir_all(&log_dir).ok();

    let log_file = std::fs::File::create(log_dir.join("n8n.log"))
        .map_err(|e| format!("Failed to create n8n log: {e}"))?;
    let stderr_file = log_file
        .try_clone()
        .map_err(|e| format!("Failed to clone log handle: {e}"))?;

    // The n8n binary is a shell script with `#!/usr/bin/env node`.
    // macOS .app bundles have a minimal PATH, so we must ensure the
    // directory containing `node` is on PATH for the child process.
    let path = augmented_path();
    log::info!("Spawning n8n: {} start (port {}, PATH: {})", bin.display(), N8N_PORT, path);

    let mut cmd = Command::new(&bin);
    cmd.arg("start")
        .env("PATH", &path)
        .env("N8N_USER_FOLDER", data_dir.to_string_lossy().as_ref())
        .env("N8N_PORT", N8N_PORT.to_string())
        .env("N8N_HOST", "127.0.0.1")
        .env(
            "WEBHOOK_URL",
            format!("http://localhost:{}/", N8N_PORT),
        )
        .env("N8N_SECURE_COOKIE", "false")
        .env("N8N_PUBLIC_API_DISABLED", "false")
        .env("N8N_RUNNERS_DISABLED", "true")
        .stdout(log_file)
        .stderr(stderr_file);

    // Create a new process group so we can kill all n8n children on shutdown.
    #[cfg(unix)]
    unsafe {
        use std::os::unix::process::CommandExt;
        cmd.pre_exec(|| {
            libc::setpgid(0, 0);
            Ok(())
        });
    }

    cmd.spawn()
        .map_err(|e| format!("Failed to spawn n8n: {e}"))
}

/// Check if n8n is running by hitting its health endpoint.
pub fn is_n8n_running() -> bool {
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };

    client
        .get(format!("http://127.0.0.1:{}/healthz", N8N_PORT))
        .send()
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

/// Gracefully shut down the n8n process.
pub fn shutdown_n8n() {
    if let Ok(mut guard) = N8N_PROCESS.lock() {
        if let Some(ref mut child) = *guard {
            let pid = child.id();
            log::info!("Stopping n8n process (pid {})", pid);

            #[cfg(unix)]
            {
                // Send SIGTERM to the entire process group
                unsafe {
                    libc::kill(-(pid as i32), libc::SIGTERM);
                }

                let start = std::time::Instant::now();
                loop {
                    match child.try_wait() {
                        Ok(Some(_)) => {
                            log::info!("n8n exited gracefully");
                            break;
                        }
                        Ok(None) => {
                            if start.elapsed() > Duration::from_secs(5) {
                                log::warn!("n8n did not exit in time, sending SIGKILL");
                                unsafe {
                                    libc::kill(-(pid as i32), libc::SIGKILL);
                                }
                                let _ = child.wait();
                                break;
                            }
                            std::thread::sleep(Duration::from_millis(100));
                        }
                        Err(e) => {
                            log::error!("Error waiting for n8n: {}", e);
                            let _ = child.kill();
                            break;
                        }
                    }
                }
            }

            #[cfg(not(unix))]
            {
                log::info!("Killing n8n process");
                let _ = child.kill();
                let _ = child.wait();
            }

            *guard = None;
        }
    }
}

// ── Tauri commands (called from the frontend) ───────────────────────────

/// Check if Node.js is available on the system.
#[tauri::command]
pub fn check_node() -> Result<bool, String> {
    Ok(find_node().is_ok())
}

/// Check if n8n is installed.
#[tauri::command]
pub fn check_n8n_installed() -> Result<bool, String> {
    Ok(is_n8n_installed())
}

/// Get the status of the n8n process.
#[tauri::command]
pub fn n8n_status() -> Result<String, String> {
    if is_n8n_running() {
        Ok("running".to_string())
    } else {
        // Check if we have a process handle (started but not healthy yet)
        if let Ok(guard) = N8N_PROCESS.lock() {
            if guard.is_some() {
                return Ok("starting".to_string());
            }
        }
        Ok("not_running".to_string())
    }
}

/// Start the n8n process (Tauri command for UI).
#[tauri::command]
pub fn start_n8n() -> Result<String, String> {
    match startup_n8n() {
        N8nStartResult::Started => Ok("started".to_string()),
        N8nStartResult::AlreadyRunning => Ok("already_running".to_string()),
        N8nStartResult::NodeNotFound(msg) => Err(msg),
        N8nStartResult::N8nNotInstalled(msg) => Err(msg),
        N8nStartResult::StartFailed(msg) => Err(msg),
    }
}

/// Stop the n8n process (Tauri command for UI).
#[tauri::command]
pub fn stop_n8n() -> Result<String, String> {
    shutdown_n8n();
    Ok("stopped".to_string())
}
