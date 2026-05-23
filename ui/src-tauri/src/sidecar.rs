// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

//! Python environment and engine lifecycle management.
//!
//! In development: spawns `python -m laya.main` from the engine's dev venv.
//! In production: manages a venv at `~/.laya/venv/`, installs requirements
//! from the bundled `requirements.txt`, and runs the engine from the bundled
//! Python source in `Contents/Resources/engine/`.

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::Duration;

#[cfg(unix)]
use std::os::unix::process::CommandExt;

pub fn engine_url() -> String {
    let port = std::env::var("LAYA_ENGINE_PORT").unwrap_or_else(|_| "8420".to_string());
    format!("http://127.0.0.1:{}", port)
}

pub fn engine_port() -> u16 {
    std::env::var("LAYA_ENGINE_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8420)
}

/// On Windows, attach CREATE_NO_WINDOW so spawned child processes do not
/// flash a console window. No-op on other platforms.
#[cfg(windows)]
fn no_window(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn no_window(_cmd: &mut Command) {}

// ── Path helpers ────────────────────────────────────────────────────────

fn home_dir() -> Option<PathBuf> {
    #[cfg(unix)]
    { std::env::var_os("HOME").map(PathBuf::from) }
    #[cfg(windows)]
    { std::env::var_os("USERPROFILE").map(PathBuf::from) }
}

fn laya_home() -> PathBuf {
    home_dir().unwrap_or_default().join(".laya")
}

/// ~/.laya/venv
fn venv_dir() -> PathBuf {
    laya_home().join("venv")
}

/// Python binary inside the managed venv.
fn venv_python() -> PathBuf {
    if cfg!(target_os = "windows") {
        venv_dir().join("Scripts").join("python.exe")
    } else {
        venv_dir().join("bin").join("python")
    }
}


/// Engine source directory (dev: repo checkout, prod: bundled in .app).
fn engine_source_dir() -> PathBuf {
    if cfg!(dev) {
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        manifest.join("..").join("..").join("engine")
    } else {
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.canonicalize().ok())
            .and_then(|p| p.parent().map(|d| d.to_path_buf()))
            .unwrap_or_default();
        // macOS .app bundle: Contents/MacOS/ -> Contents/Resources/resources/engine
        // (Tauri nests bundle resources under a "resources" subdirectory)
        let mac_resources = exe_dir
            .parent()                   // Contents/
            .map(|p| p.join("Resources").join("resources").join("engine"))
            .unwrap_or_else(|| exe_dir.join("engine"));
        if mac_resources.exists() {
            return mac_resources;
        }
        // Linux AppImage / .deb: exe is at usr/bin/, resources at
        // usr/lib/<productName>/resources/engine/
        let linux_resources = exe_dir
            .parent()                   // usr/
            .map(|p| p.join("lib").join("Laya").join("resources").join("engine"))
            .unwrap_or_else(|| exe_dir.join("engine"));
        if linux_resources.exists() {
            return linux_resources;
        }
        // Windows MSI / NSIS: exe is at <install_dir>\laya-app.exe,
        // resources at <install_dir>\resources\engine\
        let win_resources = exe_dir.join("resources").join("engine");
        if win_resources.exists() {
            return win_resources;
        }
        // Fallback: next to executable
        exe_dir.join("engine")
    }
}

/// Path to the bundled requirements.txt.
fn requirements_path() -> PathBuf {
    engine_source_dir().join("requirements.txt")
}

/// Path to the optional ML requirements (torch, sentence-transformers, etc.).
fn requirements_ml_path() -> PathBuf {
    engine_source_dir().join("requirements-ml.txt")
}

/// Path to the requirements hash file (used to detect when deps need updating).
fn deps_hash_path() -> PathBuf {
    laya_home().join(".deps_hash")
}

// ── Environment status ──────────────────────────────────────────────────

#[derive(serde::Serialize, Clone, Debug)]
pub struct EnvStatus {
    /// System Python path (None if not found)
    pub python_path: Option<String>,
    /// Detected Python version string
    pub python_version: Option<String>,
    /// Whether ~/.laya/venv exists and is valid
    pub venv_ready: bool,
    /// Whether pip dependencies are installed and up-to-date
    pub deps_installed: bool,
    /// Whether the engine source is available
    pub engine_source_found: bool,
    /// Whether Node.js 22+ is available on the system
    pub node_found: bool,
    /// Whether n8n is installed in ~/.laya/n8n_module/
    pub n8n_installed: bool,
}

/// Check the full environment status.
pub fn check_environment() -> EnvStatus {
    let (python_path, python_version) = match find_python() {
        Ok((path, ver)) => (Some(path.to_string_lossy().to_string()), Some(ver)),
        Err(_) => (None, None),
    };

    let venv_ready = venv_python().exists();
    let deps_installed = venv_ready && deps_up_to_date();
    let engine_source_found = engine_source_dir().join("laya").join("main.py").exists();
    let node_found = crate::n8n::find_node().is_ok();
    let n8n_installed = crate::n8n::is_n8n_installed();

    EnvStatus {
        python_path,
        python_version,
        venv_ready,
        deps_installed,
        engine_source_found,
        node_found,
        n8n_installed,
    }
}

// ── AppImage environment sanitization ───────────────────────────────────

/// When running inside an AppImage, the runtime sets PYTHONHOME, PYTHONPATH,
/// and LD_LIBRARY_PATH which confuse the *system* Python (it can't find its
/// own stdlib — notably `encodings`). Strip these before calling out to the
/// host Python. Without this fix, `python -m venv` fails with:
///   "ModuleNotFoundError: No module named 'encodings'"
fn sanitize_python_cmd(cmd: &mut Command) {
    cmd.env_remove("PYTHONHOME")
        .env_remove("PYTHONPATH");

    // LD_LIBRARY_PATH: remove AppImage-injected paths (anything under /tmp/.mount_*)
    // but keep the rest so system libraries still resolve.
    if let Ok(ld) = std::env::var("LD_LIBRARY_PATH") {
        let cleaned: Vec<&str> = ld
            .split(':')
            .filter(|p| !p.starts_with("/tmp/.mount_"))
            .collect();
        if cleaned.is_empty() {
            cmd.env_remove("LD_LIBRARY_PATH");
        } else {
            cmd.env("LD_LIBRARY_PATH", cleaned.join(":"));
        }
    }
}

// ── Python detection ────────────────────────────────────────────────────

/// Find a Python 3.10+ interpreter, preferring Laya's managed install at
/// `~/.laya/python/` (if present) over whatever the user has on `PATH`.
///
/// The managed install is pinned to a known-good version that has been
/// tested against Laya's requirements, so picking it first avoids surprise
/// behavior changes when the user upgrades their system Python.
pub fn find_python() -> Result<(PathBuf, String), String> {
    if let Some(managed) = crate::runtime::managed_python() {
        if let Some(ver) = probe_python_version(&managed) {
            return Ok((managed, ver));
        }
    }
    find_python_system()
}

/// Search the system for a Python 3.10+ interpreter, ignoring any managed
/// install in `~/.laya/python/`.  Used by the runtime provisioner to decide
/// whether a download is necessary; the regular `find_python()` entry point
/// is what the rest of the codebase should call.
pub fn find_python_system() -> Result<(PathBuf, String), String> {
    let candidates: Vec<&str> = if cfg!(target_os = "windows") {
        vec!["python3.13", "python3.12", "python3.11", "python3.10", "python3", "python", "py"]
    } else {
        vec![
            // Prefer versioned binaries so we find 3.13 even if python3 -> 3.14
            "python3.13",
            "python3.12",
            "python3.11",
            "python3.10",
            "/opt/homebrew/bin/python3.13",
            "/opt/homebrew/bin/python3.12",
            "/opt/homebrew/bin/python3.11",
            "/opt/homebrew/bin/python3.10",
            "/usr/local/bin/python3.13",
            "/usr/local/bin/python3.12",
            "/usr/local/bin/python3.11",
            "/usr/local/bin/python3.10",
            "python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
            "python",
        ]
    };

    // First pass: prefer 3.10–3.13 (known to work with ML packages)
    // Second pass: accept any 3.10+
    for strict in [true, false] {
        for name in &candidates {
            let mut cmd = Command::new(name);
            cmd.args(["--version"]);
            no_window(&mut cmd);
            sanitize_python_cmd(&mut cmd);
            if let Ok(output) = cmd.output() {
                if output.status.success() {
                    let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
                    if let Some(ver) = version.strip_prefix("Python ") {
                        let parts: Vec<&str> = ver.split('.').collect();
                        if parts.len() >= 2 {
                            let major: u32 = parts[0].parse().unwrap_or(0);
                            let minor: u32 = parts[1].parse().unwrap_or(0);
                            if major == 3 && minor >= 10 {
                                if strict && minor > 13 {
                                    continue; // first pass skips 3.14+
                                }
                                let path = which(name).unwrap_or_else(|| PathBuf::from(name));
                                return Ok((path, ver.to_string()));
                            }
                        }
                    }
                }
            }
        }
    }

    Err("Python 3.10+ not found. Please install Python 3.13 or newer.".to_string())
}

/// Probe a specific interpreter and return its version string if it runs
/// successfully and reports Python 3.10+.
fn probe_python_version(path: &Path) -> Option<String> {
    let mut cmd = Command::new(path);
    cmd.args(["--version"]);
    no_window(&mut cmd);
    sanitize_python_cmd(&mut cmd);
    let output = cmd.output().ok()?;
    if !output.status.success() {
        return None;
    }
    let raw = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let ver = raw.strip_prefix("Python ")?;
    let parts: Vec<&str> = ver.split('.').collect();
    if parts.len() < 2 {
        return None;
    }
    let major: u32 = parts[0].parse().ok()?;
    let minor: u32 = parts[1].parse().ok()?;
    if major == 3 && minor >= 10 {
        Some(ver.to_string())
    } else {
        None
    }
}

/// Resolve a bare command name to its absolute path using the `which` crate.
/// The old implementation shelled out to a `which` binary, which doesn't
/// exist on a stock Windows install.
fn which(name: &str) -> Option<PathBuf> {
    let p = PathBuf::from(name);
    if p.is_absolute() && p.exists() {
        return Some(p);
    }
    ::which::which(name).ok()
}

// ── Venv management ─────────────────────────────────────────────────────

/// Create a venv at ~/.laya/venv using the given Python interpreter.
pub fn create_venv(python: &PathBuf) -> Result<(), String> {
    let venv = venv_dir();
    log::info!("Creating venv at {} using {}", venv.display(), python.display());

    std::fs::create_dir_all(laya_home())
        .map_err(|e| format!("Failed to create ~/.laya: {e}"))?;

    let mut cmd = Command::new(python);
    no_window(&mut cmd);
    cmd.args(["-m", "venv", &venv.to_string_lossy()]);
    // AppImage sets PYTHONHOME/PYTHONPATH/LD_LIBRARY_PATH which break the
    // system Python's stdlib resolution (see sanitize_python_cmd).
    sanitize_python_cmd(&mut cmd);

    let output = cmd.output()
        .map_err(|e| format!("Failed to run python -m venv: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("venv creation failed: {stderr}"));
    }

    Ok(())
}

/// Check if installed deps match the bundled requirements files (by hash).
fn deps_up_to_date() -> bool {
    let hash_path = deps_hash_path();

    let mut combined = String::new();
    if let Ok(content) = std::fs::read_to_string(requirements_path()) {
        combined.push_str(&content);
    } else {
        return false;
    }
    if let Ok(content) = std::fs::read_to_string(requirements_ml_path()) {
        combined.push_str(&content);
    }

    let current_hash = simple_hash(&combined);
    match std::fs::read_to_string(&hash_path) {
        Ok(stored) => stored.trim() == current_hash,
        Err(_) => false,
    }
}

/// Save the current requirements hash after successful install.
fn save_deps_hash() {
    let mut combined = String::new();
    if let Ok(c) = std::fs::read_to_string(requirements_path()) { combined.push_str(&c); }
    if let Ok(c) = std::fs::read_to_string(requirements_ml_path()) { combined.push_str(&c); }
    let hash = simple_hash(&combined);
    let _ = std::fs::write(deps_hash_path(), hash);
}

/// Simple string hash (not cryptographic — just for change detection).
fn simple_hash(s: &str) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut h = DefaultHasher::new();
    s.hash(&mut h);
    format!("{:016x}", h.finish())
}

/// Run pip install for a given requirements file.
/// Returns Ok(true) on success, Ok(false) if the file doesn't exist (skipped),
/// or Err on failure.
fn pip_install<F: FnMut(&str)>(
    req: &std::path::Path,
    log_name: &str,
    on_line: &mut F,
) -> Result<bool, String> {
    if !req.exists() {
        return Ok(false);
    }

    let python = venv_python();
    if !python.exists() {
        return Err("Python not found in venv — venv may be corrupt".to_string());
    }

    let log_dir = laya_home().join("logs");
    std::fs::create_dir_all(&log_dir).ok();
    let log_path = log_dir.join(log_name);

    log::info!("Installing from {} (log: {})", req.display(), log_path.display());

    let mut cmd = Command::new(&python);
    no_window(&mut cmd);
    cmd.args([
            "-m", "pip",
            "install", "-r", &req.to_string_lossy(),
            "--progress-bar", "off",
            "--log", &log_path.to_string_lossy(),
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // AppImage env vars break even the venv Python (see sanitize_python_cmd).
    sanitize_python_cmd(&mut cmd);

    let mut child = cmd.spawn()
        .map_err(|e| format!("Failed to start pip: {e}"))?;

    let mut output_lines: Vec<String> = Vec::new();

    if let Some(stdout) = child.stdout.take() {
        let stderr = child.stderr.take();
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            if let Ok(line) = line {
                on_line(&line);
                output_lines.push(line);
            }
        }
        if let Some(stderr) = stderr {
            let reader = BufReader::new(stderr);
            for line in reader.lines() {
                if let Ok(line) = line {
                    on_line(&line);
                    output_lines.push(line);
                }
            }
        }
    }

    let status = child.wait().map_err(|e| format!("pip wait failed: {e}"))?;
    if !status.success() {
        let tail: Vec<&str> = output_lines.iter().rev().take(5).map(|s| s.as_str()).collect();
        let detail = tail.into_iter().rev().collect::<Vec<_>>().join("\n");
        let msg = format!(
            "pip install failed (exit code {}).\nSee full log: {}\n\nLast output:\n{}",
            status.code().unwrap_or(-1),
            log_path.display(),
            detail
        );
        return Err(msg);
    }

    Ok(true)
}

/// Install all requirements into the managed venv.
///
/// 1. Core deps (required — fails the setup if these fail)
/// 2. ML deps (optional — logs a warning but continues if unavailable)
///
/// Calls `on_line` for each line of pip output (for progress reporting).
pub fn install_requirements<F: FnMut(&str)>(mut on_line: F) -> Result<(), String> {
    // Core dependencies — must succeed
    let req = requirements_path();
    if !req.exists() {
        return Err(format!("requirements.txt not found at {}", req.display()));
    }
    pip_install(&req, "pip-install.log", &mut on_line)?;

    // ML dependencies (torch, sentence-transformers, onnxruntime) — optional.
    // These may fail on platforms without compatible wheels (e.g., macOS x86_64).
    let ml_req = requirements_ml_path();
    match pip_install(&ml_req, "pip-install-ml.log", &mut on_line) {
        Ok(true) => {
            log::info!("ML dependencies installed successfully");
        }
        Ok(false) => {
            log::info!("No ML requirements file found — skipping");
        }
        Err(e) => {
            log::warn!("ML dependencies failed to install (non-fatal): {}", e);
            on_line("Note: ML packages unavailable on this platform — local embeddings disabled");
        }
    }

    save_deps_hash();
    Ok(())
}

// ── Engine spawning ─────────────────────────────────────────────────────

/// Spawn the engine process.
///
/// Dev mode: uses the repo's engine/.venv/bin/python.
/// Prod mode: uses ~/.laya/venv/bin/python with bundled engine source.
pub fn spawn_engine() -> Result<Child, String> {
    if cfg!(dev) {
        return spawn_dev_engine();
    }
    spawn_prod_engine()
}

fn spawn_dev_engine() -> Result<Child, String> {
    let engine = engine_source_dir();
    let python = engine.join(if cfg!(target_os = "windows") {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });

    if !python.exists() {
        return Err(format!("Dev venv not found at {}", python.display()));
    }

    log::info!("Spawning engine (dev): {} -m laya.main", python.display());

    let mut cmd = Command::new(&python);
    no_window(&mut cmd);
    cmd.arg("-m")
        .arg("laya.main")
        .current_dir(&engine)
        .env("LAYA_PARENT_PID", std::process::id().to_string());

    #[cfg(unix)]
    unsafe {
        cmd.pre_exec(|| {
            libc::setpgid(0, libc::getppid());
            Ok(())
        });
    }

    cmd.spawn()
        .map_err(|e| format!("Failed to spawn engine: {e}"))
}

fn spawn_prod_engine() -> Result<Child, String> {
    let python = venv_python();
    if !python.exists() {
        return Err("Managed venv not ready — run setup first".to_string());
    }

    let engine = engine_source_dir();
    if !engine.join("laya").join("main.py").exists() {
        return Err(format!("Engine source not found at {}", engine.display()));
    }

    log::info!(
        "Spawning engine: {} -m laya.main (engine: {})",
        python.display(),
        engine.display()
    );

    let log_dir = laya_home().join("logs");
    std::fs::create_dir_all(&log_dir)
        .map_err(|e| format!("Failed to create log dir: {e}"))?;

    let log_file = std::fs::File::create(log_dir.join("engine-stdout.log"))
        .map_err(|e| format!("Failed to create engine log: {e}"))?;
    let stderr_file = log_file
        .try_clone()
        .map_err(|e| format!("Failed to clone log handle: {e}"))?;

    let mut cmd = Command::new(&python);
    no_window(&mut cmd);
    cmd.arg("-m")
        .arg("laya.main")
        .current_dir(&engine)
        .stdout(log_file)
        .stderr(stderr_file)
        // Prevent Python from writing __pycache__/*.pyc into the signed .app bundle,
        // which would invalidate the code signature.
        .env("PYTHONDONTWRITEBYTECODE", "1")
        // LAYA_PARENT_PID lets the engine's parent-watchdog monitor the actual
        // laya-app process, not the intermediate Python launcher (uvicorn spawns
        // a worker subprocess on Windows, so os.getppid() points at the wrong PID).
        .env("LAYA_PARENT_PID", std::process::id().to_string());
    // AppImage env vars break even the venv Python (see sanitize_python_cmd).
    sanitize_python_cmd(&mut cmd);

    #[cfg(unix)]
    unsafe {
        cmd.pre_exec(|| {
            libc::setpgid(0, libc::getppid());
            Ok(())
        });
    }

    cmd.spawn()
        .map_err(|e| format!("Failed to spawn engine: {e}"))
}

/// Poll the engine's /health endpoint until it responds or timeout.
pub fn wait_for_engine(timeout: Duration) -> bool {
    let start = std::time::Instant::now();
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();

    while start.elapsed() < timeout {
        match client.get(format!("{}/health", engine_url())).send() {
            Ok(resp) if resp.status().is_success() => {
                log::info!("Engine is ready");
                return true;
            }
            _ => {
                std::thread::sleep(Duration::from_millis(500));
            }
        }
    }
    log::error!("Engine failed to start within {:?}", timeout);
    false
}
