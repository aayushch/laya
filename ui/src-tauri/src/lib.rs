mod n8n;
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
async fn pick_repo_folder(app: tauri::AppHandle) -> Result<RepoDetection, String> {
    use tauri_plugin_dialog::DialogExt;
    let file_path = app
        .dialog()
        .file()
        .set_title("Select a git repository")
        .blocking_pick_folder()
        .ok_or_else(|| "cancelled".to_string())?;
    let path = file_path
        .as_path()
        .ok_or_else(|| "cancelled".to_string())?
        .to_string_lossy()
        .trim_end_matches('/')
        .to_string();
    if path.is_empty() {
        return Err("cancelled".to_string());
    }

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

// ── Setup commands ──────────────────────────────────────────────────────

/// Check if the Python environment is ready.
#[tauri::command]
fn check_environment() -> sidecar::EnvStatus {
    sidecar::check_environment()
}

/// Event payload emitted during setup for frontend progress display.
#[derive(serde::Serialize, Clone)]
struct SetupEvent {
    step: &'static str,   // "python", "venv", "deps", "engine"
    status: &'static str, // "running", "done", "error"
    message: String,
}

/// Run full environment setup with fail-fast preflight checks.
///
/// Steps:
/// 1. Preflight — verify Python 3.10+ and Node.js 18+ are installed
/// 2. Environment — create Python venv
/// 3. Dependencies — pip install requirements
/// 4. Automation — install n8n via npm + start it
/// 5. Engine — start the Laya engine
///
/// Emits `setup-progress` events throughout.
#[tauri::command]
fn setup_environment(app: tauri::AppHandle) {
    use tauri::Emitter;

    std::thread::spawn(move || {
        let emit = |step: &'static str, status: &'static str, msg: &str| {
            let _ = app.emit("setup-progress", SetupEvent {
                step,
                status,
                message: msg.to_string(),
            });
        };

        // ── Step 1: Preflight checks ────────────────────────────────
        // Fail fast if required runtimes are missing, before spending
        // minutes on pip install only to fail at the n8n step.
        emit("preflight", "running", "Checking for Python...");

        let python_path = match sidecar::find_python() {
            Ok((path, ver)) => {
                emit("preflight", "running", &format!("Python {} found. Checking for Node.js...", ver));
                path
            }
            Err(e) => {
                emit("preflight", "error", &format!("Python 3.10+ is required. {}", e));
                return;
            }
        };

        match n8n::find_node() {
            Ok((_, ver)) => {
                emit("preflight", "done", &format!("Python and Node.js {} found", ver));
            }
            Err(e) => {
                emit("preflight", "error", &format!("Node.js 22+ is required. {}", e));
                return;
            }
        };

        // ── Step 2: Set up environment ──────────────────────────────
        if !sidecar::check_environment().venv_ready {
            emit("environment", "running", "Creating Python environment...");
            match sidecar::create_venv(&python_path) {
                Ok(()) => emit("environment", "done", "Python environment created"),
                Err(e) => {
                    emit("environment", "error", &e);
                    return;
                }
            }
        } else {
            emit("environment", "done", "Python environment ready");
        }

        // ── Step 3: Install dependencies ────────────────────────────
        let env = sidecar::check_environment();
        if !env.deps_installed {
            emit("deps", "running", "Installing packages (this may take a few minutes)...");

            let app_clone = app.clone();
            match sidecar::install_requirements(|line| {
                let _ = app_clone.emit("setup-progress", SetupEvent {
                    step: "deps",
                    status: "running",
                    message: line.to_string(),
                });
            }) {
                Ok(()) => emit("deps", "done", "All packages installed"),
                Err(e) => {
                    emit("deps", "error", &e);
                    return;
                }
            }
        } else {
            emit("deps", "done", "Packages up to date");
        }

        // ── Step 4: Set up automation (n8n) ─────────────────────────
        if !n8n::is_n8n_installed() {
            emit("automation", "running", "Installing n8n...");
            let app_clone = app.clone();
            match n8n::install_n8n(|line| {
                let _ = app_clone.emit("setup-progress", SetupEvent {
                    step: "automation",
                    status: "running",
                    message: line.to_string(),
                });
            }) {
                Ok(()) => {}
                Err(e) => {
                    emit("automation", "error", &e);
                    return;
                }
            }
        }

        if APP_EXITING.load(Ordering::Relaxed) {
            log::info!("App is exiting, aborting setup");
            return;
        }

        // Start n8n
        emit("automation", "running", "Starting n8n...");
        match n8n::startup_n8n() {
            n8n::N8nStartResult::Started | n8n::N8nStartResult::AlreadyRunning => {
                // Wait for the full REST API, not just /healthz.
                // On a fresh install n8n can take 15-30s to initialise its DB
                // before /rest/settings responds.  Without this the engine's
                // ensure_n8n_ready() races and skips provisioning.
                emit("automation", "running", "Waiting for n8n API...");
                if n8n::wait_for_n8n_api(std::time::Duration::from_secs(60)) {
                    emit("automation", "done", "n8n running");
                } else {
                    // Non-fatal: engine has its own retry logic, but warn the user.
                    log::warn!("n8n REST API did not become ready in time — engine will retry");
                    emit("automation", "done", "n8n started (API still loading)");
                }
            }
            other => {
                log::warn!("n8n startup: {:?}", other);
                emit("automation", "error", &format!("Failed to start n8n: {:?}", other));
                return;
            }
        }

        // ── Step 5: Start engine ────────────────────────────────────
        if APP_EXITING.load(Ordering::Relaxed) {
            log::info!("App is exiting, skipping engine spawn");
            return;
        }
        emit("engine", "running", "Starting Laya engine...");
        match sidecar::spawn_engine() {
            Ok(child) => {
                if let Some(state) = app.try_state::<EngineProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        *guard = Some(child);
                    }
                }

                if sidecar::wait_for_engine(std::time::Duration::from_secs(60)) {
                    emit("engine", "done", "Engine is running");
                } else {
                    emit("engine", "error", "Engine started but is not responding");
                }
            }
            Err(e) => {
                emit("engine", "error", &e);
            }
        }
    });
}

// ── Main app ────────────────────────────────────────────────────────────

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{TrayIcon, TrayIconBuilder},
    Manager,
};

struct EngineProcess(Mutex<Option<std::process::Child>>);

/// Set to `true` when the app is shutting down.  Background threads
/// (e.g. `setup_environment`) check this before spawning new processes
/// to avoid orphaning them after the Exit handler has already run.
static APP_EXITING: AtomicBool = AtomicBool::new(false);

/// Poll the engine health endpoint and update the tray tooltip.
fn start_health_polling(tray: TrayIcon) {
    std::thread::spawn(move || {
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(3))
            .pool_idle_timeout(Duration::from_secs(60))
            .build()
            .unwrap();

        loop {
            std::thread::sleep(Duration::from_secs(30));

            let tooltip = match client.get("http://127.0.0.1:8420/health").send() {
                Ok(resp) => {
                    if let Ok(body) = resp.json::<serde_json::Value>() {
                        let engine = body.get("engine").and_then(|v| v.as_str()).unwrap_or("unknown");
                        let n8n = body.get("n8n").and_then(|v| v.as_str()).unwrap_or("unknown");

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

/// Best-effort kill of any process listening on the given TCP port.
/// Used as a safety net during shutdown to catch orphaned engine processes.
#[cfg(unix)]
fn kill_process_on_port(port: u16) {
    if let Ok(output) = std::process::Command::new("lsof")
        .args(["-ti", &format!("tcp:{}", port)])
        .output()
    {
        if output.status.success() {
            let pids = String::from_utf8_lossy(&output.stdout);
            for pid_str in pids.split_whitespace() {
                if let Ok(pid) = pid_str.parse::<i32>() {
                    log::info!("Killing orphaned process on port {} (pid {})", port, pid);
                    unsafe { libc::kill(pid, libc::SIGTERM); }
                }
            }
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            {
                use tauri_plugin_log::{Target, TargetKind};

                let mut log_builder = tauri_plugin_log::Builder::default();

                if cfg!(debug_assertions) {
                    log_builder = log_builder
                        .level(log::LevelFilter::Debug)
                        .targets([
                            Target::new(TargetKind::LogDir { file_name: None }),
                            Target::new(TargetKind::Stdout),
                        ]);
                } else {
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

            // Set the window icon on Linux (GTK doesn't auto-derive it
            // from the bundle like macOS/Windows do).
            #[cfg(target_os = "linux")]
            {
                if let Some(window) = app.get_webview_window("main") {
                    let icon_bytes = include_bytes!("../icons/icon.png");
                    if let Ok(icon) = tauri::image::Image::from_bytes(icon_bytes) {
                        let _ = window.set_icon(icon);
                    }
                }
            }

            // --- System tray ---
            let dashboard =
                MenuItem::with_id(app, "dashboard", "Dashboard", true, None::<&str>)?;
            let separator = PredefinedMenuItem::separator(app)?;
            let show = MenuItem::with_id(app, "show", "Show Laya", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&dashboard, &separator, &show, &quit])?;

            let tray_icon = {
                let bytes = include_bytes!("../icons/trayTemplate@2x.png");
                tauri::image::Image::from_bytes(bytes).expect("failed to load tray icon")
            };

            let tray = TrayIconBuilder::new()
                .icon(tray_icon)
                .icon_as_template(true)
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

            start_health_polling(tray);

            // --- Start n8n as a local process ---
            match n8n::startup_n8n() {
                n8n::N8nStartResult::Started => {
                    log::info!("n8n process started successfully");
                }
                n8n::N8nStartResult::AlreadyRunning => {
                    log::info!("n8n was already running");
                }
                n8n::N8nStartResult::NodeNotFound(msg) => {
                    log::warn!("Node.js not available: {}", msg);
                }
                n8n::N8nStartResult::N8nNotInstalled(msg) => {
                    log::warn!("n8n not installed: {}", msg);
                }
                n8n::N8nStartResult::StartFailed(msg) => {
                    log::error!("Failed to start n8n: {}", msg);
                }
            }

            // --- Spawn engine ---
            // If the environment is ready (dev venv or managed venv), spawn immediately.
            // If not (first run in production), the frontend drives setup via
            // check_environment + setup_environment, which spawns the engine when done.
            app.manage(EngineProcess(Mutex::new(None)));

            let env = sidecar::check_environment();
            let can_spawn = cfg!(dev) || (env.venv_ready && env.deps_installed && env.engine_source_found);

            if can_spawn {
                match sidecar::spawn_engine() {
                    Ok(child) => {
                        if let Some(state) = app.try_state::<EngineProcess>() {
                            if let Ok(mut guard) = state.0.lock() {
                                *guard = Some(child);
                            }
                        }
                        std::thread::spawn(|| {
                            sidecar::wait_for_engine(Duration::from_secs(30));
                        });
                    }
                    Err(e) => {
                        log::error!("Failed to start engine: {}", e);
                    }
                }
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            n8n::check_node,
            n8n::check_n8n_installed,
            n8n::n8n_status,
            n8n::start_n8n,
            n8n::stop_n8n,
            pick_repo_folder,
            check_environment,
            setup_environment,
        ])
        .on_page_load(|webview, payload| {
            use tauri::webview::PageLoadEvent;
            if let PageLoadEvent::Finished = payload.event() {
                let url = payload.url().to_string();
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
            match event {
                #[cfg(target_os = "macos")]
                tauri::RunEvent::WindowEvent {
                    label,
                    event: tauri::WindowEvent::CloseRequested { api, .. },
                    ..
                } => {
                    api.prevent_close();
                    if let Some(window) = app.get_webview_window(&label) {
                        let _ = window.hide();
                    }
                    log::info!("Window hidden (macOS close-to-dock behavior)");
                }
                #[cfg(target_os = "macos")]
                tauri::RunEvent::Reopen { .. } => {
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }
                tauri::RunEvent::Exit => {
                    // Signal background threads (e.g. setup_environment) to stop
                    // spawning new processes.
                    APP_EXITING.store(true, Ordering::Relaxed);

                    // Gracefully stop engine (if we have a handle)
                    let mut killed_engine = false;
                    if let Some(state) = app.try_state::<EngineProcess>() {
                        if let Ok(mut guard) = state.0.lock() {
                            if let Some(ref mut child) = *guard {
                                let pid = child.id();
                                log::info!("Sending SIGTERM to engine process (pid {})", pid);
                                killed_engine = true;

                                #[cfg(unix)]
                                {
                                    unsafe { libc::kill(pid as i32, libc::SIGTERM); }

                                    let start = std::time::Instant::now();
                                    loop {
                                        match child.try_wait() {
                                            Ok(Some(_)) => {
                                                log::info!("Engine exited gracefully");
                                                break;
                                            }
                                            Ok(None) => {
                                                if start.elapsed() > Duration::from_secs(5) {
                                                    log::warn!("Engine did not exit in time, sending SIGKILL");
                                                    let _ = child.kill();
                                                    let _ = child.wait();
                                                    break;
                                                }
                                                std::thread::sleep(Duration::from_millis(100));
                                            }
                                            Err(e) => {
                                                log::error!("Error waiting for engine: {}", e);
                                                let _ = child.kill();
                                                break;
                                            }
                                        }
                                    }
                                }

                                #[cfg(not(unix))]
                                {
                                    log::info!("Killing engine process");
                                    let _ = child.kill();
                                    let _ = child.wait();
                                }
                            }
                        }
                    }

                    // Safety net: if setup_environment spawned the engine after
                    // our cleanup above (race), or if we never got a handle,
                    // kill any process listening on the engine port (8420).
                    #[cfg(unix)]
                    if !killed_engine {
                        kill_process_on_port(8420);
                    }

                    log::info!("Stopping n8n process");
                    n8n::shutdown_n8n();
                }
                _ => {}
            }
        });
}
