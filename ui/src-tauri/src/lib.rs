mod docker;
mod sidecar;

#[derive(serde::Serialize, Clone)]
pub struct RepoDetection {
    pub path: String,
    pub name: String,
    pub platform: String,
    pub remote_id: String,
}

fn parse_remote_url(url: &str) -> Option<(String, String)> {
    // SSH: git@github.com:org/repo.git
    if let Some(rest) = url.strip_prefix("git@") {
        let (host, path) = rest.split_once(':')?;
        let remote_id = path.trim_end_matches(".git").to_string();
        let platform = if host.contains("github.com") {
            "github"
        } else if host.contains("bitbucket.org") {
            "bitbucket"
        } else {
            return None;
        };
        return Some((platform.to_string(), remote_id));
    }
    // HTTPS: https://github.com/org/repo.git
    if url.starts_with("https://") || url.starts_with("http://") {
        let without_scheme = url.splitn(3, '/').nth(2)?;
        let (host, path) = without_scheme.split_once('/')?;
        let remote_id = path.trim_end_matches(".git").to_string();
        let platform = if host.contains("github.com") {
            "github"
        } else if host.contains("bitbucket.org") {
            "bitbucket"
        } else {
            return None;
        };
        return Some((platform.to_string(), remote_id));
    }
    None
}

#[tauri::command]
fn pick_repo_folder() -> Result<RepoDetection, String> {
    // Open native macOS folder picker via osascript
    let output = std::process::Command::new("osascript")
        .args(["-e", "POSIX path of (choose folder with prompt \"Select a git repository\")"])
        .output()
        .map_err(|e| format!("osascript error: {e}"))?;

    if !output.status.success() {
        return Err("cancelled".to_string());
    }

    let path = String::from_utf8_lossy(&output.stdout)
        .trim()
        .trim_end_matches('/')
        .to_string();
    if path.is_empty() {
        return Err("cancelled".to_string());
    }

    // Get git remote URL
    let git_out = std::process::Command::new("git")
        .args(["-C", &path, "remote", "get-url", "origin"])
        .output()
        .map_err(|e| format!("git error: {e}"))?;

    if !git_out.status.success() {
        return Err("Not a git repository or no remote named 'origin'".to_string());
    }

    let remote_url = String::from_utf8_lossy(&git_out.stdout).trim().to_string();
    let (platform, remote_id) = parse_remote_url(&remote_url)
        .ok_or_else(|| format!("Unrecognized remote URL: {remote_url}"))?;

    let name = std::path::Path::new(&path)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("repo")
        .to_string();

    Ok(RepoDetection { path, name, platform, remote_id })
}

use std::sync::Mutex;
use std::time::Duration;
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{TrayIcon, TrayIconBuilder},
    Manager,
};

struct EngineProcess(Mutex<Option<std::process::Child>>);

/// Poll the engine health endpoint and update the tray tooltip.
fn start_health_polling(tray: TrayIcon) {
    std::thread::spawn(move || {
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(3))
            .build()
            .unwrap();

        loop {
            std::thread::sleep(Duration::from_secs(30));

            let tooltip = match client.get("http://127.0.0.1:8420/health").send() {
                Ok(resp) => {
                    if let Ok(body) = resp.json::<serde_json::Value>() {
                        let engine = body.get("engine").and_then(|v| v.as_str()).unwrap_or("unknown");
                        let n8n = body.get("n8n").and_then(|v| v.as_str()).unwrap_or("unknown");

                        // Query pending card count
                        let pending = client
                            .get("http://127.0.0.1:8420/cards?status=pending&limit=1")
                            .send()
                            .ok()
                            .and_then(|r| r.json::<serde_json::Value>().ok())
                            .and_then(|b| b.get("total").and_then(|v| v.as_u64()))
                            .unwrap_or(0);

                        format!(
                            "Laya - Engine: {} | n8n: {} | {} pending",
                            engine, n8n, pending
                        )
                    } else {
                        "Laya - Engine: error parsing health".to_string()
                    }
                }
                Err(_) => "Laya - Engine offline".to_string(),
            };

            let _ = tray.set_tooltip(Some(&tooltip));
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // --- System tray ---
            let dashboard =
                MenuItem::with_id(app, "dashboard", "Dashboard", true, None::<&str>)?;
            let separator = PredefinedMenuItem::separator(app)?;
            let show = MenuItem::with_id(app, "show", "Show Laya", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&dashboard, &separator, &show, &quit])?;

            let tray = TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("Laya")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "dashboard" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                            let _ = window.eval("window.location.href='/dashboard'");
                        }
                    }
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            // Start background health polling for tray tooltip
            start_health_polling(tray);

            // --- Spawn Python engine ---
            match sidecar::spawn_engine() {
                Ok(child) => {
                    app.manage(EngineProcess(Mutex::new(Some(child))));

                    // Poll for readiness in a background thread
                    std::thread::spawn(|| {
                        sidecar::wait_for_engine(Duration::from_secs(30));
                    });
                }
                Err(e) => {
                    log::error!("Failed to start engine: {}", e);
                    // Store None — the app can still run, health badge will show unhealthy
                    app.manage(EngineProcess(Mutex::new(None)));
                }
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            docker::check_docker,
            docker::n8n_status,
            docker::start_n8n,
            docker::stop_n8n,
            pick_repo_folder,
        ])
        .on_window_event(|window, event| {
            // Kill engine when the app exits
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.try_state::<EngineProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(ref mut child) = *guard {
                            log::info!("Killing engine process");
                            let _ = child.kill();
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
