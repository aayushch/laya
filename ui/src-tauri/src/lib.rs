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
            {
                use tauri_plugin_log::{Target, TargetKind};

                let mut log_builder = tauri_plugin_log::Builder::default();

                if cfg!(debug_assertions) {
                    // Dev: log to file + stdout for easy debugging
                    log_builder = log_builder
                        .level(log::LevelFilter::Debug)
                        .targets([
                            Target::new(TargetKind::LogDir { file_name: None }),
                            Target::new(TargetKind::Stdout),
                        ]);
                } else {
                    // Release: log to file only (stdout causes a Terminal window on macOS)
                    log_builder = log_builder
                        .level(log::LevelFilter::Info)
                        .targets([
                            Target::new(TargetKind::LogDir { file_name: None }),
                        ]);
                }

                app.handle().plugin(log_builder.build())?;
            }

            // Set macOS dock icon (needed in dev mode where there's no .app bundle)
            #[cfg(target_os = "macos")]
            #[allow(deprecated)]
            {
                use cocoa::appkit::{NSApplication, NSImage};
                use cocoa::base::nil;
                use cocoa::foundation::NSData;
                let icon_bytes = include_bytes!("../icons/icon_macos.png");
                unsafe {
                    let ns_app = NSApplication::sharedApplication(nil);
                    let data = NSData::dataWithBytes_length_(
                        nil,
                        icon_bytes.as_ptr() as *const std::ffi::c_void,
                        icon_bytes.len() as u64,
                    );
                    let image = NSImage::initWithData_(NSImage::alloc(nil), data);
                    ns_app.setApplicationIconImage_(image);
                }
            }

            // --- System tray ---
            let dashboard =
                MenuItem::with_id(app, "dashboard", "Dashboard", true, None::<&str>)?;
            let separator = PredefinedMenuItem::separator(app)?;
            let show = MenuItem::with_id(app, "show", "Show Laya", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&dashboard, &separator, &show, &quit])?;

            let tray = TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
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

            // --- Start n8n via Docker Compose ---
            match docker::startup_n8n() {
                docker::N8nStartResult::Started => {
                    log::info!("n8n container started successfully");
                }
                docker::N8nStartResult::AlreadyRunning => {
                    log::info!("n8n container was already running");
                }
                docker::N8nStartResult::DockerNotAvailable(msg) => {
                    log::warn!("Docker not available: {}", msg);
                    // Show a non-blocking notification to the user via JS
                    if let Some(window) = app.get_webview_window("main") {
                        let escaped = msg.replace('\\', "\\\\").replace('\'', "\\'");
                        let _ = window.eval(&format!(
                            "setTimeout(() => window.__laya_docker_warning = '{}', 1000)",
                            escaped
                        ));
                    }
                }
                docker::N8nStartResult::ComposeNotFound(msg) => {
                    log::error!("docker-compose.yml not found: {}", msg);
                }
                docker::N8nStartResult::StartFailed(msg) => {
                    log::error!("Failed to start n8n: {}", msg);
                }
            }

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
        .on_page_load(|webview, payload| {
            use tauri::webview::PageLoadEvent;
            if let PageLoadEvent::Finished = payload.event() {
                let url = payload.url().to_string();
                // Only our app's own URLs are internal — other localhost
                // services (e.g. n8n on :5678) should get the back bar.
                let is_internal = url.starts_with("http://localhost:5173")
                    || url.starts_with("http://127.0.0.1:5173")
                    || url.starts_with("http://127.0.0.1:8420")
                    || url.starts_with("tauri://")
                    || url.starts_with("about:");

                if !is_internal {
                    let back_url = if cfg!(debug_assertions) {
                        "http://localhost:5173/feed"
                    } else {
                        "tauri://localhost/feed"
                    };
                    let js = format!(
                        r#"(function(){{
if(document.getElementById('__laya_nav'))return;
var bar=document.createElement('div');
bar.id='__laya_nav';
bar.style.cssText='position:fixed;top:0;left:0;right:0;z-index:2147483647;display:flex;align-items:center;gap:8px;padding:6px 12px;background:#1a1a1a;border-bottom:1px solid #333;font-family:-apple-system,BlinkMacSystemFont,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,0.3);';
var btn=document.createElement('button');
btn.textContent='\u{{2190}} Back to Laya';
btn.style.cssText='background:none;border:1px solid rgba(232,116,48,0.53);color:#e87430;padding:4px 12px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap;';
btn.onmouseover=function(){{btn.style.background='rgba(232,116,48,0.09)'}};
btn.onmouseout=function(){{btn.style.background='none'}};
btn.onclick=function(){{window.location.href='{back}'}};
bar.appendChild(btn);
var u=document.createElement('span');
u.textContent=window.location.hostname;
u.style.cssText='flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;color:#888;';
bar.appendChild(u);
document.documentElement.appendChild(bar);
if(document.body)document.body.style.marginTop=bar.offsetHeight+'px';
}})();"#,
                        back = back_url
                    );
                    let _ = webview.eval(&js);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                // Kill engine sidecar
                if let Some(state) = app.try_state::<EngineProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(ref mut child) = *guard {
                            log::info!("Killing engine process");
                            let _ = child.kill();
                        }
                    }
                }
                // Stop n8n container
                log::info!("Stopping n8n container");
                docker::shutdown_n8n();
            }
        });
}
