//! Python environment and engine lifecycle management.
//!
//! In development: spawns `python -m laya.main` from the engine's dev venv.
//! In production: manages a venv at `~/.laya/venv/`, installs requirements
//! from the bundled `requirements.txt`, and runs the engine from the bundled
//! Python source in `Contents/Resources/engine/`.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::Duration;

#[cfg(unix)]
use std::os::unix::process::CommandExt;

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
        let resources = exe_dir
            .parent()                   // Contents/
            .map(|p| p.join("Resources").join("resources").join("engine"))
            .unwrap_or_else(|| exe_dir.join("engine"));
        if resources.exists() {
            return resources;
        }
        // Linux / other: next to executable
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
    /// Whether Node.js 18+ is available on the system
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

// ── Python detection ────────────────────────────────────────────────────

/// Find a Python 3.10+ interpreter on the system.
/// Returns (path, version_string).
pub fn find_python() -> Result<(PathBuf, String), String> {
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
            if let Ok(output) = Command::new(name).args(["--version"]).output() {
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

/// Simple `which` implementation — resolve a command name to its absolute path.
fn which(name: &str) -> Option<PathBuf> {
    // If it's already an absolute path, use it directly
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

// ── Venv management ─────────────────────────────────────────────────────

/// Create a venv at ~/.laya/venv using the given Python interpreter.
pub fn create_venv(python: &PathBuf) -> Result<(), String> {
    let venv = venv_dir();
    log::info!("Creating venv at {} using {}", venv.display(), python.display());

    std::fs::create_dir_all(laya_home())
        .map_err(|e| format!("Failed to create ~/.laya: {e}"))?;

    let output = Command::new(python)
        .args(["-m", "venv", &venv.to_string_lossy()])
        .output()
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

    let mut child = Command::new(&python)
        .args([
            "-m", "pip",
            "install", "-r", &req.to_string_lossy(),
            "--progress-bar", "off",
            "--log", &log_path.to_string_lossy(),
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
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
    cmd.arg("-m").arg("laya.main").current_dir(&engine);

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

    let log_file = std::fs::File::create(log_dir.join("engine.log"))
        .map_err(|e| format!("Failed to create engine log: {e}"))?;
    let stderr_file = log_file
        .try_clone()
        .map_err(|e| format!("Failed to clone log handle: {e}"))?;

    let mut cmd = Command::new(&python);
    cmd.arg("-m")
        .arg("laya.main")
        .current_dir(&engine)
        .stdout(log_file)
        .stderr(stderr_file)
        // Prevent Python from writing __pycache__/*.pyc into the signed .app bundle,
        // which would invalidate the code signature.
        .env("PYTHONDONTWRITEBYTECODE", "1");

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
