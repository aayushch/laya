use std::process::Command;

/// Check if Docker is available on the system.
#[tauri::command]
pub fn check_docker() -> Result<bool, String> {
    match Command::new("docker").arg("info").output() {
        Ok(output) => Ok(output.status.success()),
        Err(_) => Ok(false),
    }
}

/// Get the status of the laya-n8n container.
#[tauri::command]
pub fn n8n_status() -> Result<String, String> {
    match Command::new("docker")
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

/// Start the n8n container using docker compose.
#[tauri::command]
pub fn start_n8n() -> Result<String, String> {
    // Find the docker-compose.yml relative to the app
    let compose_path = find_compose_file()?;

    match Command::new("docker")
        .args(["compose", "-f", &compose_path, "up", "-d", "n8n"])
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

/// Stop all containers defined in docker-compose.yml.
#[tauri::command]
pub fn stop_n8n() -> Result<String, String> {
    let compose_path = find_compose_file()?;

    match Command::new("docker")
        .args(["compose", "-f", &compose_path, "down"])
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

/// Locate the docker-compose.yml file.
fn find_compose_file() -> Result<String, String> {
    // Check common locations relative to LAYA_HOME
    if let Ok(home) = std::env::var("HOME") {
        let laya_compose = std::path::PathBuf::from(&home)
            .join(".laya")
            .join("docker-compose.yml");
        if laya_compose.exists() {
            return Ok(laya_compose.to_string_lossy().to_string());
        }
    }

    // Fallback: check the project root (development mode)
    if let Ok(cwd) = std::env::current_dir() {
        let dev_compose = cwd.join("docker-compose.yml");
        if dev_compose.exists() {
            return Ok(dev_compose.to_string_lossy().to_string());
        }
    }

    Err("docker-compose.yml not found".to_string())
}
