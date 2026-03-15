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

/// pip binary inside the managed venv.
fn venv_pip() -> PathBuf {
    if cfg!(target_os = "windows") {
        venv_dir().join("Scripts").join("pip.exe")
    } else {
        venv_dir().join("bin").join("pip")
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

    EnvStatus {
        python_path,
        python_version,
        venv_ready,
        deps_installed,
        engine_source_found,
    }
}

// ── Python detection ────────────────────────────────────────────────────

/// Find a Python 3.10+ interpreter on the system.
/// Returns (path, version_string).
pub fn find_python() -> Result<(PathBuf, String), String> {
    let candidates: Vec<&str> = if cfg!(target_os = "windows") {
        vec!["python3", "python", "py"]
    } else {
        vec![
            "python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
            "python",
        ]
    };

    for name in candidates {
        if let Ok(output) = Command::new(name).args(["--version"]).output() {
            if output.status.success() {
                let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
                if let Some(ver) = version.strip_prefix("Python ") {
                    let parts: Vec<&str> = ver.split('.').collect();
                    if parts.len() >= 2 {
                        let major: u32 = parts[0].parse().unwrap_or(0);
                        let minor: u32 = parts[1].parse().unwrap_or(0);
                        if major == 3 && minor >= 10 {
                            // Resolve the full path
                            let path = which(name).unwrap_or_else(|| PathBuf::from(name));
                            return Ok((path, ver.to_string()));
                        }
                    }
                }
            }
        }
    }

    Err("Python 3.10+ not found. Please install Python 3.10 or newer.".to_string())
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

/// Check if installed deps match the bundled requirements.txt (by hash).
fn deps_up_to_date() -> bool {
    let req_path = requirements_path();
    let hash_path = deps_hash_path();

    let current_hash = match std::fs::read_to_string(&req_path) {
        Ok(content) => simple_hash(&content),
        Err(_) => return false,
    };

    match std::fs::read_to_string(&hash_path) {
        Ok(stored) => stored.trim() == current_hash,
        Err(_) => false,
    }
}

/// Save the current requirements hash after successful install.
fn save_deps_hash() {
    if let Ok(content) = std::fs::read_to_string(requirements_path()) {
        let hash = simple_hash(&content);
        let _ = std::fs::write(deps_hash_path(), hash);
    }
}

/// Simple string hash (not cryptographic — just for change detection).
fn simple_hash(s: &str) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut h = DefaultHasher::new();
    s.hash(&mut h);
    format!("{:016x}", h.finish())
}

/// Install requirements into the managed venv.
///
/// Calls `on_line` for each line of pip output (for progress reporting).
pub fn install_requirements<F: FnMut(&str)>(mut on_line: F) -> Result<(), String> {
    let pip = venv_pip();
    let req = requirements_path();

    if !pip.exists() {
        return Err("pip not found in venv — venv may be corrupt".to_string());
    }
    if !req.exists() {
        return Err(format!("requirements.txt not found at {}", req.display()));
    }

    log::info!("Installing requirements from {}", req.display());

    let mut child = Command::new(&pip)
        .args([
            "install", "-r", &req.to_string_lossy(),
            "--quiet",       // reduce noise
            "--progress-bar", "off",
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to start pip: {e}"))?;

    // Stream stderr (pip writes progress there)
    if let Some(stderr) = child.stderr.take() {
        let reader = BufReader::new(stderr);
        for line in reader.lines() {
            if let Ok(line) = line {
                on_line(&line);
            }
        }
    }

    let status = child.wait().map_err(|e| format!("pip wait failed: {e}"))?;
    if !status.success() {
        return Err("pip install failed — check logs for details".to_string());
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
        .stderr(stderr_file);

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
