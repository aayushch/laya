use std::path::PathBuf;
use std::process::Command;
use std::sync::OnceLock;

/// Result of attempting to start n8n.
#[derive(Debug)]
pub enum N8nStartResult {
    Started,
    AlreadyRunning,
    DockerNotAvailable(String),
    ComposeNotFound(String),
    StartFailed(String),
}

// ── Docker binary resolution ──────────────────────────────────────────
//
// macOS .app bundles inherit a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin)
// that does NOT include /usr/local/bin where Docker Desktop symlinks live.
// We resolve the docker binary once at startup and cache it.

/// Well-known Docker CLI locations on macOS, Linux, and Windows.
const DOCKER_SEARCH_PATHS: &[&str] = &[
    // Docker Desktop for Mac (symlink target)
    "/Applications/Docker.app/Contents/Resources/bin/docker",
    // Common symlink location
    "/usr/local/bin/docker",
    // Homebrew on Apple Silicon
    "/opt/homebrew/bin/docker",
    // Linux package managers
    "/usr/bin/docker",
    // Rancher Desktop
    "/Applications/Rancher Desktop.app/Contents/Resources/resources/linux/bin/docker",
];

/// Cached resolved docker binary path.
static DOCKER_BIN: OnceLock<Option<String>> = OnceLock::new();

/// Resolve the docker binary path once, checking PATH first then well-known locations.
fn docker_bin() -> Option<&'static str> {
    DOCKER_BIN
        .get_or_init(|| {
            // First: try bare "docker" (works in dev / terminal-launched apps)
            if let Ok(output) = Command::new("docker")
                .arg("--version")
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
            {
                if output.success() {
                    return Some("docker".to_string());
                }
            }

            // Second: check well-known absolute paths
            for path in DOCKER_SEARCH_PATHS {
                if std::path::Path::new(path).exists() {
                    return Some(path.to_string());
                }
            }

            None
        })
        .as_deref()
}

/// Create a Command using the resolved docker binary.
fn docker_cmd() -> Result<Command, String> {
    match docker_bin() {
        Some(bin) => Ok(Command::new(bin)),
        None => Err("Docker CLI not found".to_string()),
    }
}

// ── Tauri commands (called from the frontend) ─────────────────────────

/// Check if Docker is available on the system.
#[tauri::command]
pub fn check_docker() -> Result<bool, String> {
    Ok(is_docker_available())
}

/// Get the status of the laya-n8n container.
#[tauri::command]
pub fn n8n_status() -> Result<String, String> {
    let mut cmd = docker_cmd()?;
    match cmd
        .args(["inspect", "--format", "{{.State.Status}}", "laya-n8n"])
        .output()
    {
        Ok(output) => {
            if output.status.success() {
                Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
            } else {
                Ok("not_found".to_string())
            }
        }
        Err(e) => Err(format!("Failed to inspect container: {}", e)),
    }
}

/// Start the n8n container using docker compose (Tauri command for UI).
#[tauri::command]
pub fn start_n8n() -> Result<String, String> {
    let compose_path = find_compose_file()?;
    run_compose_up(&compose_path)
}

/// Stop all containers defined in docker-compose.yml (Tauri command for UI).
#[tauri::command]
pub fn stop_n8n() -> Result<String, String> {
    let compose_path = find_compose_file()?;
    run_compose_down(&compose_path)
}

// ── Internal helpers ──────────────────────────────────────────────────

/// Check if `docker info` succeeds (daemon is running).
fn is_docker_available() -> bool {
    let mut cmd = match docker_cmd() {
        Ok(c) => c,
        Err(_) => return false,
    };
    cmd.arg("info")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// Run `docker compose up -d n8n`.
fn run_compose_up(compose_path: &str) -> Result<String, String> {
    let mut cmd = docker_cmd()?;
    match cmd
        .args(["compose", "-f", compose_path, "up", "-d", "n8n"])
        .output()
    {
        Ok(output) => {
            if output.status.success() {
                Ok("started".to_string())
            } else {
                let stderr = String::from_utf8_lossy(&output.stderr);
                Err(format!("Failed to start n8n: {}", stderr))
            }
        }
        Err(e) => Err(format!("Failed to run docker compose: {}", e)),
    }
}

/// Run `docker compose down`.
fn run_compose_down(compose_path: &str) -> Result<String, String> {
    let mut cmd = docker_cmd()?;
    match cmd
        .args(["compose", "-f", compose_path, "down", "--timeout", "5"])
        .output()
    {
        Ok(output) => {
            if output.status.success() {
                Ok("stopped".to_string())
            } else {
                let stderr = String::from_utf8_lossy(&output.stderr);
                Err(format!("Failed to stop n8n: {}", stderr))
            }
        }
        Err(e) => Err(format!("Failed to run docker compose: {}", e)),
    }
}

/// Ensure the compose file exists at `~/.laya/docker-compose.yml`,
/// extracting it from the bundled resources if needed.
fn ensure_compose_file() -> Result<String, String> {
    if let Ok(home) = std::env::var("HOME").or_else(|_| std::env::var("USERPROFILE")) {
        let laya_dir = PathBuf::from(&home).join(".laya");
        let compose_dest = laya_dir.join("docker-compose.yml");

        // If it already exists, use it
        if compose_dest.exists() {
            return Ok(compose_dest.to_string_lossy().to_string());
        }

        // Try to extract from Tauri bundle resources
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.to_path_buf()));

        let resource_candidates: Vec<PathBuf> = if let Some(ref dir) = exe_dir {
            if cfg!(target_os = "macos") {
                // In .app bundle: Contents/MacOS/../Resources/
                let resources_dir = dir.join("../Resources");
                vec![
                    resources_dir.join("resources/docker-compose.yml"),
                    resources_dir.join("docker-compose.yml"),
                    dir.join("resources/docker-compose.yml"),
                ]
            } else {
                vec![
                    dir.join("resources/docker-compose.yml"),
                    dir.join("docker-compose.yml"),
                ]
            }
        } else {
            vec![]
        };

        for candidate in &resource_candidates {
            if candidate.exists() {
                // Create ~/.laya/ if needed and copy
                if let Err(e) = std::fs::create_dir_all(&laya_dir) {
                    return Err(format!("Failed to create {}: {}", laya_dir.display(), e));
                }
                if let Err(e) = std::fs::copy(candidate, &compose_dest) {
                    return Err(format!("Failed to copy docker-compose.yml: {}", e));
                }
                log::info!(
                    "Extracted docker-compose.yml from bundle to {}",
                    compose_dest.display()
                );
                return Ok(compose_dest.to_string_lossy().to_string());
            }
        }
    }

    Err("docker-compose.yml not found in bundle or ~/.laya/".to_string())
}

/// Locate the docker-compose.yml file.
/// In production: extracts from bundle to ~/.laya/ if needed.
/// In development: also checks project root via CWD.
fn find_compose_file() -> Result<String, String> {
    // First try ~/.laya/ and bundle extraction
    if let Ok(path) = ensure_compose_file() {
        return Ok(path);
    }

    // Fallback: check the project root (development mode)
    if let Ok(cwd) = std::env::current_dir() {
        let dev_compose = cwd.join("docker-compose.yml");
        if dev_compose.exists() {
            return Ok(dev_compose.to_string_lossy().to_string());
        }
    }

    // Dev fallback: check relative to CARGO_MANIFEST_DIR
    let manifest_compose = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("docker-compose.yml");
    if manifest_compose.exists() {
        return Ok(manifest_compose.to_string_lossy().to_string());
    }

    Err(
        "docker-compose.yml not found. Looked in ~/.laya/, app bundle, and project root."
            .to_string(),
    )
}

/// Start n8n on app startup. Returns a descriptive result rather than
/// hard-failing, so the app can still launch without Docker.
pub fn startup_n8n() -> N8nStartResult {
    // 1. Is docker CLI findable at all?
    if docker_bin().is_none() {
        return N8nStartResult::DockerNotAvailable(
            "Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ to enable n8n integrations.".to_string()
        );
    }

    // 2. Is Docker daemon running?
    if !is_docker_available() {
        return N8nStartResult::DockerNotAvailable(
            "Docker is installed but not running. Please start Docker Desktop and relaunch Laya to enable n8n integrations.".to_string()
        );
    }

    // 3. Is n8n already running?
    if let Ok(status) = n8n_status() {
        if status == "running" {
            log::info!("n8n container already running");
            return N8nStartResult::AlreadyRunning;
        }
    }

    // 4. Find compose file
    let compose_path = match find_compose_file() {
        Ok(p) => p,
        Err(e) => return N8nStartResult::ComposeNotFound(e),
    };

    // 5. Start n8n
    match run_compose_up(&compose_path) {
        Ok(_) => {
            log::info!("n8n started via docker compose");
            N8nStartResult::Started
        }
        Err(e) => N8nStartResult::StartFailed(e),
    }
}

/// Stop n8n on app shutdown. Best-effort, does not fail loudly.
pub fn shutdown_n8n() {
    if docker_bin().is_none() || !is_docker_available() {
        return;
    }

    if let Ok(compose_path) = find_compose_file() {
        match run_compose_down(&compose_path) {
            Ok(_) => log::info!("n8n stopped via docker compose"),
            Err(e) => log::warn!("Failed to stop n8n on shutdown: {}", e),
        }
    }
}
