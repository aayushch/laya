//! Python engine process lifecycle management.
//!
//! In development: spawns `python -m laya.main` from the engine venv.
//! In production: launches the PyInstaller-bundled `laya-engine` sidecar binary.

use std::path::PathBuf;
use std::process::{Child, Command};
use std::time::Duration;

/// Return the user's home directory via the HOME (Unix) or USERPROFILE (Windows) env var.
fn home_dir() -> Option<PathBuf> {
    #[cfg(unix)]
    { std::env::var_os("HOME").map(PathBuf::from) }
    #[cfg(windows)]
    { std::env::var_os("USERPROFILE").map(PathBuf::from) }
}

/// Find the engine directory (relative to the Tauri project root).
/// Only used in dev mode.
fn engine_dir() -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir.join("..").join("..").join("engine")
}

/// Locate the Python binary inside the engine's venv (dev mode).
fn python_bin() -> PathBuf {
    let engine = engine_dir();
    if cfg!(target_os = "windows") {
        engine.join(".venv").join("Scripts").join("python.exe")
    } else {
        engine.join(".venv").join("bin").join("python")
    }
}

/// Locate the bundled sidecar binary (production mode).
///
/// Tauri places sidecar binaries next to the main executable in the bundle.
/// The binary name includes a target-triple suffix that Tauri resolves at
/// build time, but on disk it's just `laya-engine` (or `.exe` on Windows).
fn sidecar_bin() -> PathBuf {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));

    if cfg!(target_os = "windows") {
        exe_dir.join("laya-engine.exe")
    } else if cfg!(target_os = "macos") {
        // In .app bundle: Laya.app/Contents/MacOS/laya-engine
        exe_dir.join("laya-engine")
    } else {
        exe_dir.join("laya-engine")
    }
}

/// Spawn the engine process.
///
/// In dev mode: prefers venv Python, falls back to sidecar binary.
/// In production: prefers sidecar binary, falls back to venv Python.
pub fn spawn_engine() -> Result<Child, String> {
    let python = python_bin();
    let sidecar = sidecar_bin();

    if cfg!(dev) {
        // Dev mode: prefer venv python (sidecar may be a placeholder)
        if python.exists() {
            return spawn_venv(&python);
        }
        if sidecar.exists() {
            return spawn_sidecar(&sidecar);
        }
    } else {
        // Production: prefer bundled sidecar
        if sidecar.exists() {
            return spawn_sidecar(&sidecar);
        }
        if python.exists() {
            return spawn_venv(&python);
        }
    }

    Err(format!(
        "Engine binary not found.\n\
         Looked for sidecar at: {}\n\
         Looked for venv at: {}\n\
         Run scripts/setup-dev.sh (dev) or scripts/build.sh (production).",
        sidecar.display(),
        python.display()
    ))
}

/// Spawn the PyInstaller-bundled sidecar binary.
///
/// Redirects stdout/stderr to `~/.laya/logs/sidecar.log` so that early
/// crashes (before Python logging is configured) are still captured.
fn spawn_sidecar(bin: &PathBuf) -> Result<Child, String> {
    log::info!("Spawning engine sidecar: {}", bin.display());

    let log_dir = home_dir()
        .ok_or("Cannot determine home directory")?
        .join(".laya")
        .join("logs");
    std::fs::create_dir_all(&log_dir)
        .map_err(|e| format!("Failed to create log dir: {}", e))?;

    let log_file = std::fs::File::create(log_dir.join("sidecar.log"))
        .map_err(|e| format!("Failed to create sidecar log: {}", e))?;
    let stderr_file = log_file
        .try_clone()
        .map_err(|e| format!("Failed to clone log file handle: {}", e))?;

    Command::new(bin)
        .stdout(log_file)
        .stderr(stderr_file)
        .spawn()
        .map_err(|e| format!("Failed to spawn engine sidecar: {}", e))
}

/// Spawn the engine from the dev venv.
fn spawn_venv(python: &PathBuf) -> Result<Child, String> {
    let engine = engine_dir();
    log::info!(
        "Spawning engine (dev): {} -m laya.main (cwd: {})",
        python.display(),
        engine.display()
    );

    Command::new(python)
        .arg("-m")
        .arg("laya.main")
        .current_dir(&engine)
        .spawn()
        .map_err(|e| format!("Failed to spawn engine: {}", e))
}

/// Poll the engine's /health endpoint until it responds or timeout.
pub fn wait_for_engine(timeout: Duration) -> bool {
    let start = std::time::Instant::now();
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();

    while start.elapsed() < timeout {
        match client.get("http://127.0.0.1:8420/health").send() {
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
