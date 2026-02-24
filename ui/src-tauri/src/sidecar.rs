//! Python engine process lifecycle management.
//!
//! Locates the engine venv, spawns `python -m laya.main`, and polls
//! the /health endpoint until the engine is ready.

use std::path::PathBuf;
use std::process::{Child, Command};
use std::time::Duration;

/// Find the engine directory (relative to the Tauri project root).
fn engine_dir() -> PathBuf {
    // In dev: <repo>/ui/src-tauri/../../engine
    // We resolve relative to the executable's parent in production,
    // but for dev we use the known repo layout.
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir.join("..").join("..").join("engine")
}

/// Locate the Python binary inside the engine's venv.
fn python_bin() -> PathBuf {
    let engine = engine_dir();
    if cfg!(target_os = "windows") {
        engine.join(".venv").join("Scripts").join("python.exe")
    } else {
        engine.join(".venv").join("bin").join("python")
    }
}

/// Spawn the Python engine as a child process.
pub fn spawn_engine() -> Result<Child, String> {
    let python = python_bin();
    if !python.exists() {
        return Err(format!(
            "Python venv not found at {}. Run scripts/setup-dev.sh first.",
            python.display()
        ));
    }

    let engine = engine_dir();
    log::info!("Spawning engine: {} -m laya.main (cwd: {})", python.display(), engine.display());

    Command::new(&python)
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
