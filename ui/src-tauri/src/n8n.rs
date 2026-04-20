//! n8n process lifecycle management.
//!
//! Manages n8n as a local Node.js process instead of a Docker container.
//! n8n is installed via npm into ~/.laya/n8n_module/ and stores its data
//! in ~/.laya/n8n/.

use std::io::BufRead;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

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

/// Shared HTTP client for n8n health checks — avoids creating a new TCP
/// connection (and `reqwest::blocking::Client`) on every call.
fn shared_client() -> &'static reqwest::blocking::Client {
    static CLIENT: OnceLock<reqwest::blocking::Client> = OnceLock::new();
    CLIENT.get_or_init(|| {
        reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(2))
            .pool_idle_timeout(Duration::from_secs(60))
            .build()
            .expect("failed to build shared reqwest client")
    })
}

/// Port for the Laya-managed n8n instance.
/// Chosen to avoid conflicts with a user's own n8n (default 5678)
/// and is not assigned to any well-known service.
pub const N8N_PORT: u16 = 45678;

// ── Path helpers ────────────────────────────────────────────────────────

fn home_dir() -> Option<PathBuf> {
    #[cfg(unix)]
    {
        std::env::var_os("HOME").map(PathBuf::from)
    }
    #[cfg(windows)]
    {
        std::env::var_os("USERPROFILE").map(PathBuf::from)
    }
}

fn laya_home() -> PathBuf {
    home_dir().unwrap_or_default().join(".laya")
}

/// Where the n8n npm package is installed: ~/.laya/n8n_module/
fn n8n_module_dir() -> PathBuf {
    laya_home().join("n8n_module")
}

/// Where n8n stores its data (DB, encryption key, credentials): ~/.laya/n8n/
fn n8n_data_dir() -> PathBuf {
    laya_home().join("n8n")
}

/// Path to the n8n binary inside the local npm install.
fn n8n_bin() -> PathBuf {
    if cfg!(target_os = "windows") {
        n8n_module_dir()
            .join("node_modules")
            .join(".bin")
            .join("n8n.cmd")
    } else {
        n8n_module_dir()
            .join("node_modules")
            .join(".bin")
            .join("n8n")
    }
}

// ── Node.js detection ───────────────────────────────────────────────────

/// Well-known Node.js install directories on macOS / Linux.
/// macOS .app bundles inherit a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin)
/// that does NOT include these, so we must search them explicitly.
const NODE_SEARCH_DIRS: &[&str] = &[
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/usr/bin",
];

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

/// Find a Node.js 22+ installation, preferring the highest version.
/// Returns (absolute_path, version_string).
///
/// When multiple Node.js installations exist (e.g. distro `/usr/bin/node`
/// v20 and user-installed `/usr/local/bin/node` v24), the old logic
/// returned whichever came first in the candidate list. Now we probe
/// all candidates and pick the newest one that meets the minimum version.
pub fn find_node() -> Result<(String, String), String> {
    let mut candidates: Vec<PathBuf> = vec![PathBuf::from("node")];
    if !cfg!(target_os = "windows") {
        for dir in NODE_SEARCH_DIRS {
            candidates.push(PathBuf::from(dir).join("node"));
        }
        // Also check `n` install location (PREFIX defaults to /usr/local)
        let n_node = PathBuf::from("/usr/local/bin/node");
        if !candidates.iter().any(|c| c == &n_node) && n_node.exists() {
            candidates.push(n_node);
        }
        // Also check nvm default if it exists
        if let Some(home) = home_dir() {
            let nvm_node = home.join(".nvm/versions/node");
            if nvm_node.is_dir() {
                if let Ok(entries) = std::fs::read_dir(&nvm_node) {
                    let mut versions: Vec<PathBuf> = entries
                        .filter_map(|e| e.ok())
                        .map(|e| e.path())
                        .filter(|p| p.join("bin/node").exists())
                        .collect();
                    versions.sort();
                    if let Some(latest) = versions.last() {
                        candidates.push(latest.join("bin/node"));
                    }
                }
            }
        }
    }

    // Probe all candidates and collect (abs_path, version_display, major, minor).
    let mut found: Vec<(String, String, u32, u32)> = Vec::new();

    for candidate in &candidates {
        let name = candidate.to_string_lossy();
        let mut cmd = Command::new(candidate);
        no_window(&mut cmd);
        cmd.arg("--version")
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        // AppImage may set LD_LIBRARY_PATH / PATH that cause the wrong
        // node to be resolved or linked against stale libs.
        sanitize_node_cmd(&mut cmd);

        if let Ok(output) = cmd.output() {
            if output.status.success() {
                let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
                if let Some(ver) = version.strip_prefix('v') {
                    let parts: Vec<&str> = ver.split('.').collect();
                    if parts.len() >= 2 {
                        let major: u32 = parts[0].parse().unwrap_or(0);
                        let minor: u32 = parts[1].parse().unwrap_or(0);
                        if major >= 22 {
                            let abs_path = if candidate.is_absolute() && candidate.exists() {
                                candidate.to_string_lossy().to_string()
                            } else {
                                which(&name)
                                    .map(|p| p.to_string_lossy().to_string())
                                    .unwrap_or_else(|| name.to_string())
                            };
                            // De-duplicate by resolved path
                            if !found.iter().any(|(p, _, _, _)| p == &abs_path) {
                                found.push((abs_path, version, major, minor));
                            }
                        }
                    }
                }
            }
        }
    }

    // Pick the highest version
    found.sort_by(|a, b| (a.2, a.3).cmp(&(b.2, b.3)));
    if let Some((path, version, _, _)) = found.pop() {
        return Ok((path, version));
    }

    Err("Node.js 22+ not found. Please install Node.js LTS from https://nodejs.org".to_string())
}

/// Strip AppImage-injected environment variables before invoking system
/// Node.js, same rationale as `sanitize_python_cmd` in sidecar.rs.
fn sanitize_node_cmd(cmd: &mut Command) {
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

/// Get the directory containing the node binary (for augmenting PATH).
fn node_bin_dir() -> Option<String> {
    find_node()
        .ok()
        .and_then(|(path, _)| {
            PathBuf::from(&path)
                .parent()
                .map(|p| p.to_string_lossy().to_string())
        })
}

/// Build a PATH with the correct node directory *first*, so that
/// `#!/usr/bin/env node` (used by the n8n binary) resolves to the
/// version we selected in `find_node()`.
///
/// The old logic only added missing directories. That failed when the
/// desired dir (e.g. `/usr/local/bin`) was already in PATH but came
/// *after* a dir with an older node (e.g. `/usr/bin`).  Now we always
/// put the selected node's directory at the front, and strip AppImage-
/// injected paths from LD_LIBRARY_PATH (handled by sanitize_node_cmd).
fn augmented_path() -> String {
    // PATH separator and join character are OS-specific — using ':' on Windows
    // would split drive letters (e.g. "C:\...").
    let sep_char = if cfg!(windows) { ';' } else { ':' };
    let sep_str = if cfg!(windows) { ";" } else { ":" };
    let current = std::env::var("PATH").unwrap_or_default();
    let mut front: Vec<String> = Vec::new();

    // The directory containing the node binary we chose MUST come first
    // so that `#!/usr/bin/env node` picks it up.
    if let Some(dir) = node_bin_dir() {
        front.push(dir);
    }

    // NODE_SEARCH_DIRS are Unix-only paths (/usr/local/bin, /opt/homebrew/bin,
    // /usr/bin). On Windows, rely on the PATH established by the Node installer.
    if !cfg!(windows) {
        for dir in NODE_SEARCH_DIRS {
            let d = dir.to_string();
            if !front.contains(&d) && std::path::Path::new(dir).is_dir() {
                front.push(d);
            }
        }
    }

    // Remove the front dirs from their current position to avoid duplicates,
    // then prepend them.
    let remaining: Vec<&str> = current
        .split(sep_char)
        .filter(|p| !front.iter().any(|f| f == p))
        .collect();

    let mut parts = front;
    parts.extend(remaining.into_iter().map(String::from));
    parts.join(sep_str)
}

/// Resolve the npm binary from the same directory as the node binary.
fn find_npm() -> Result<String, String> {
    let (node_path, _) = find_node()?;

    let npm_name = if cfg!(target_os = "windows") {
        "npm.cmd"
    } else {
        "npm"
    };

    // Try npm alongside node (e.g., /opt/homebrew/bin/npm)
    if let Some(parent) = std::path::Path::new(&node_path).parent() {
        let npm_path = parent.join(npm_name);
        if npm_path.exists() {
            return Ok(npm_path.to_string_lossy().to_string());
        }
    }

    // Fall back to bare "npm" on PATH
    let mut probe = Command::new(npm_name);
    no_window(&mut probe);
    let ok = probe
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if ok {
        return Ok(npm_name.to_string());
    }

    Err("npm not found. It should be installed alongside Node.js.".to_string())
}

// ── n8n installation ────────────────────────────────────────────────────

/// Check if n8n is installed in ~/.laya/n8n_module/.
pub fn is_n8n_installed() -> bool {
    n8n_bin().exists()
}

/// Path to the Python binary in the Laya managed venv.
/// Used to give `node-gyp` a Python that has `setuptools` (which
/// provides the `distutils` shim removed in Python 3.12+).
fn venv_python() -> PathBuf {
    if cfg!(target_os = "windows") {
        laya_home().join("venv").join("Scripts").join("python.exe")
    } else {
        laya_home().join("venv").join("bin").join("python")
    }
}

/// Install n8n via npm into ~/.laya/n8n_module/.
/// Calls `on_line` for each line of npm output (for progress reporting).
pub fn install_n8n<F: FnMut(&str)>(mut on_line: F) -> Result<(), String> {
    let npm = find_npm()?;

    let module_dir = n8n_module_dir();
    std::fs::create_dir_all(&module_dir)
        .map_err(|e| format!("Failed to create {}: {e}", module_dir.display()))?;

    std::fs::create_dir_all(n8n_data_dir())
        .map_err(|e| format!("Failed to create n8n data directory: {e}"))?;

    let path = augmented_path();

    // Resolve a Python with setuptools for node-gyp (distutils shim).
    let venv_py = venv_python();
    let dev_venv_py = {
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        manifest.join("..").join("..").join("engine").join(".venv").join("bin").join("python")
    };
    let gyp_python = if venv_py.exists() {
        Some(venv_py)
    } else if cfg!(dev) && dev_venv_py.exists() {
        Some(dev_venv_py)
    } else {
        None
    };

    // ── Attempt 1: full install (native addons compile → isolated-vm sandboxing)
    log::info!("Installing n8n via npm into {}", module_dir.display());
    on_line("Installing n8n (this may take a few minutes)...");

    let attempt1 = run_npm_install(&npm, &module_dir, &path, &gyp_python, false, &mut on_line);

    if attempt1.is_ok() && n8n_bin().exists() {
        log::info!("n8n installed successfully (with native addons)");
        on_line("n8n installed successfully");
        return Ok(());
    }

    // ── Attempt 2: skip native compilation, remove isolated-vm
    // Native addon build can fail for many reasons (missing Xcode CLT,
    // broken xcodebuild, wrong Node version, missing compiler, etc.).
    // Fall back to --ignore-scripts and remove isolated-vm so n8n uses
    // its non-sandboxed code execution mode.
    log::warn!("Full install failed, retrying without native addons...");
    on_line("Retrying without native compilation...");

    // Clean up the failed install
    let node_modules = module_dir.join("node_modules");
    if node_modules.exists() {
        let _ = std::fs::remove_dir_all(&node_modules);
    }

    run_npm_install(&npm, &module_dir, &path, &gyp_python, true, &mut on_line)?;

    if !n8n_bin().exists() {
        return Err("n8n binary not found after npm install".to_string());
    }

    // --ignore-scripts skipped ALL native addon builds. We need to rebuild
    // the ones n8n actually requires (sqlite3, better-sqlite3) while leaving
    // the problematic one (isolated-vm) alone.
    on_line("Building database drivers...");
    rebuild_native_addon(&npm, &module_dir, &path, &gyp_python, "sqlite3", &mut on_line);
    rebuild_native_addon(&npm, &module_dir, &path, &gyp_python, "better-sqlite3", &mut on_line);

    // Replace isolated-vm with a stub module. The --ignore-scripts flag
    // skipped its native compilation, and it's the one that fails (Xcode CLT,
    // node-gyp issues, etc.). Newer n8n versions hard-require the module at
    // load time, so we can't delete it. The stub exports the expected classes
    // but throws on use — n8n catches this during async init and falls back
    // to non-sandboxed expression evaluation.
    stub_isolated_vm(&module_dir);

    log::info!("n8n installed successfully (without native addons)");
    on_line("n8n installed (without sandboxed execution)");
    Ok(())
}

/// Replace `isolated-vm` with a JS stub that loads without crashing but
/// throws when actually used.  Newer n8n versions hard-require the module
/// at load time (`require("isolated-vm")` at top of isolated-vm-bridge.js),
/// so we can't just delete it.  The stub exports the expected `Isolate`
/// class whose constructor throws, which n8n catches during async
/// initialization and falls back to non-sandboxed expression evaluation.
fn stub_isolated_vm(module_dir: &std::path::Path) {
    let ivm_dir = module_dir.join("node_modules").join("isolated-vm");

    // Remove the real module (with its broken native binary)
    if ivm_dir.exists() {
        let _ = std::fs::remove_dir_all(&ivm_dir);
    }

    if let Err(e) = std::fs::create_dir_all(&ivm_dir) {
        log::warn!("Failed to create isolated-vm stub dir: {}", e);
        return;
    }

    let stub_js = r#"'use strict';
// Stub: native isolated-vm addon was not compiled.
// Exports the expected API surface but throws on use,
// causing n8n to fall back to non-sandboxed evaluation.
class Isolate {
  constructor() {
    throw new Error('isolated-vm native addon not available');
  }
}
class Reference {
  constructor() {
    throw new Error('isolated-vm native addon not available');
  }
}
module.exports = { Isolate, Reference };
"#;

    let pkg_json = r#"{"name":"isolated-vm","version":"0.0.0-stub","main":"isolated-vm.js"}"#;

    if let Err(e) = std::fs::write(ivm_dir.join("isolated-vm.js"), stub_js) {
        log::warn!("Failed to write isolated-vm stub: {}", e);
    }
    if let Err(e) = std::fs::write(ivm_dir.join("package.json"), pkg_json) {
        log::warn!("Failed to write isolated-vm stub package.json: {}", e);
    }

    log::info!("Installed isolated-vm stub (native addon unavailable)");
}

/// Rebuild a single native addon that was skipped by --ignore-scripts.
/// Best-effort — logs a warning on failure but doesn't abort the install.
fn rebuild_native_addon<F: FnMut(&str)>(
    npm: &str,
    module_dir: &std::path::Path,
    path: &str,
    gyp_python: &Option<PathBuf>,
    package: &str,
    on_line: &mut F,
) {
    let pkg_dir = module_dir.join("node_modules").join(package);
    if !pkg_dir.exists() {
        return; // Package not installed (may not be a dependency in this n8n version)
    }

    log::info!("Rebuilding native addon: {}", package);

    let mut cmd = Command::new(npm);
    no_window(&mut cmd);
    sanitize_node_cmd(&mut cmd);
    cmd.args(["rebuild", package])
        .current_dir(module_dir)
        .env("PATH", path);

    if let Some(ref py) = gyp_python {
        cmd.env("npm_config_python", py);
    }

    match cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).output() {
        Ok(output) => {
            if output.status.success() {
                log::info!("Rebuilt {} successfully", package);
                on_line(&format!("{} ready", package));
            } else {
                let stderr = String::from_utf8_lossy(&output.stderr);
                log::warn!("Failed to rebuild {} (non-fatal): {}", package, stderr.chars().take(200).collect::<String>());
                on_line(&format!("{} build failed (will try prebuilt)", package));
            }
        }
        Err(e) => {
            log::warn!("Failed to run npm rebuild {}: {}", package, e);
        }
    }
}

/// Run `npm install` and stream output. Returns Ok(()) on success.
fn run_npm_install<F: FnMut(&str)>(
    npm: &str,
    module_dir: &std::path::Path,
    path: &str,
    gyp_python: &Option<PathBuf>,
    ignore_scripts: bool,
    on_line: &mut F,
) -> Result<(), String> {
    let mut cmd = Command::new(npm);
    no_window(&mut cmd);
    let prefix = module_dir.to_string_lossy();

    if ignore_scripts {
        cmd.args(["install", "--prefix", &prefix, "--ignore-scripts", "n8n@2.15.0"]);
    } else {
        cmd.args(["install", "--prefix", &prefix, "n8n@2.15.0"]);
    }

    cmd.env("PATH", path);
    sanitize_node_cmd(&mut cmd);

    if let Some(ref py) = gyp_python {
        cmd.env("npm_config_python", py);
    }

    let mut child = cmd
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to run npm install: {e}"))?;

    let (tx, rx) = std::sync::mpsc::channel::<String>();

    let stdout_tx = tx.clone();
    let stdout_thread = child.stdout.take().map(|stdout| {
        std::thread::spawn(move || {
            let reader = std::io::BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                let _ = stdout_tx.send(line);
            }
        })
    });

    let stderr_tx = tx;
    let stderr_thread = child.stderr.take().map(|stderr| {
        std::thread::spawn(move || {
            let reader = std::io::BufReader::new(stderr);
            for line in reader.lines().map_while(Result::ok) {
                let _ = stderr_tx.send(line);
            }
        })
    });

    let mut output_lines: Vec<String> = Vec::new();
    for line in rx {
        on_line(&line);
        output_lines.push(line);
    }

    if let Some(h) = stdout_thread { let _ = h.join(); }
    if let Some(h) = stderr_thread { let _ = h.join(); }

    let status = child.wait().map_err(|e| format!("npm wait failed: {e}"))?;
    if !status.success() {
        let tail: Vec<&str> = output_lines.iter().rev().take(5).map(|s| s.as_str()).collect();
        let detail = tail.into_iter().rev().collect::<Vec<_>>().join("\n");
        return Err(format!(
            "npm install failed (exit code {}).\n{}",
            status.code().unwrap_or(-1),
            detail,
        ));
    }

    Ok(())
}

// ── n8n process management ──────────────────────────────────────────────

/// Global n8n child process handle.
static N8N_PROCESS: Mutex<Option<Child>> = Mutex::new(None);

/// Result of attempting to start n8n.
#[derive(Debug)]
pub enum N8nStartResult {
    Started,
    AlreadyRunning,
    NodeNotFound(String),
    N8nNotInstalled(String),
    StartFailed(String),
}

/// Start the n8n process. Called on app startup.
pub fn startup_n8n() -> N8nStartResult {
    // 1. Already running? (check HTTP health)
    if is_n8n_running() {
        return N8nStartResult::AlreadyRunning;
    }

    // 2. Node.js available?
    if find_node().is_err() {
        return N8nStartResult::NodeNotFound(
            "Node.js is not installed. Install Node.js 22+ from https://nodejs.org to enable n8n integrations.".to_string(),
        );
    }

    // 3. n8n installed?
    if !is_n8n_installed() {
        return N8nStartResult::N8nNotInstalled(
            "n8n is not installed yet. It will be installed during setup.".to_string(),
        );
    }

    // 4. Start
    match spawn_n8n() {
        Ok(child) => {
            if let Ok(mut guard) = N8N_PROCESS.lock() {
                *guard = Some(child);
            }
            log::info!("n8n started on port {}", N8N_PORT);
            N8nStartResult::Started
        }
        Err(e) => N8nStartResult::StartFailed(e),
    }
}

/// Spawn the n8n process with the correct environment variables.
fn spawn_n8n() -> Result<Child, String> {
    let bin = n8n_bin();
    let data_dir = n8n_data_dir();

    std::fs::create_dir_all(&data_dir)
        .map_err(|e| format!("Failed to create n8n data dir: {e}"))?;

    let log_dir = laya_home().join("logs");
    std::fs::create_dir_all(&log_dir).ok();

    let log_file = std::fs::File::create(log_dir.join("n8n.log"))
        .map_err(|e| format!("Failed to create n8n log: {e}"))?;
    let stderr_file = log_file
        .try_clone()
        .map_err(|e| format!("Failed to clone log handle: {e}"))?;

    // The n8n binary is a shell script with `#!/usr/bin/env node`.
    // macOS .app bundles have a minimal PATH, so we must ensure the
    // directory containing `node` is on PATH for the child process.
    let path = augmented_path();
    log::info!("Spawning n8n: {} start (port {}, PATH: {})", bin.display(), N8N_PORT, path);

    let mut cmd = Command::new(&bin);
    no_window(&mut cmd);
    // Strip AppImage-injected LD_LIBRARY_PATH so n8n uses the correct
    // system libraries (see sanitize_node_cmd).
    sanitize_node_cmd(&mut cmd);
    cmd.arg("start")
        .env("PATH", &path)
        .env("N8N_USER_FOLDER", data_dir.to_string_lossy().as_ref())
        .env("N8N_PORT", N8N_PORT.to_string())
        .env("N8N_HOST", "127.0.0.1")
        .env(
            "WEBHOOK_URL",
            format!("http://localhost:{}/", N8N_PORT),
        )
        .env("N8N_SECURE_COOKIE", "false")
        .env("N8N_PUBLIC_API_DISABLED", "false")
        .env("N8N_RUNNERS_DISABLED", "true")
        // Disable all of n8n's upstream network chatter (diagnostics,
        // version-check pings, personalization survey, hiring banner,
        // frontend hooks, template gallery). Laya's Terms §9 commits to a
        // best-effort telemetry opt-out for every bundled third-party
        // component, and n8n ships with these turned on by default.
        .env("N8N_DIAGNOSTICS_ENABLED", "false")
        .env("N8N_VERSION_NOTIFICATIONS_ENABLED", "false")
        .env("N8N_TEMPLATES_ENABLED", "false")
        .env("N8N_PERSONALIZATION_ENABLED", "false")
        .env("N8N_HIRING_BANNER_ENABLED", "false")
        .env("EXTERNAL_FRONTEND_HOOKS_URLS", "")
        .env("N8N_DIAGNOSTICS_CONFIG_FRONTEND", "")
        .env("N8N_DIAGNOSTICS_CONFIG_BACKEND", "")
        .stdout(log_file)
        .stderr(stderr_file);

    // Create a new process group so we can kill all n8n children on shutdown.
    #[cfg(unix)]
    unsafe {
        use std::os::unix::process::CommandExt;
        cmd.pre_exec(|| {
            libc::setpgid(0, 0);
            Ok(())
        });
    }

    cmd.spawn()
        .map_err(|e| format!("Failed to spawn n8n: {e}"))
}

/// Check if n8n is running by hitting its health endpoint.
pub fn is_n8n_running() -> bool {
    shared_client()
        .get(format!("http://127.0.0.1:{}/healthz", N8N_PORT))
        .send()
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

/// Wait for n8n's full REST API to be ready (not just /healthz).
///
/// n8n's /healthz endpoint comes up before the Express REST API is loaded.
/// On a fresh install the REST API can take 15-30+ seconds to initialise its
/// database.  If we spawn the engine before /rest/settings responds, the
/// engine's `ensure_n8n_ready()` will time out and skip provisioning.
pub fn wait_for_n8n_api(timeout: Duration) -> bool {
    let start = std::time::Instant::now();
    let client = shared_client();
    let url = format!("http://127.0.0.1:{}/rest/settings", N8N_PORT);

    while start.elapsed() < timeout {
        match client.get(&url).send() {
            Ok(resp) if resp.status().is_success() => {
                log::info!("n8n REST API is ready (waited {:?})", start.elapsed());
                return true;
            }
            _ => {
                std::thread::sleep(Duration::from_secs(1));
            }
        }
    }
    log::warn!("n8n REST API not ready after {:?}", timeout);
    false
}

/// Gracefully shut down the n8n process.
pub fn shutdown_n8n() {
    if let Ok(mut guard) = N8N_PROCESS.lock() {
        if let Some(ref mut child) = *guard {
            let pid = child.id();
            log::info!("Stopping n8n process (pid {})", pid);

            #[cfg(unix)]
            {
                // Send SIGTERM to the entire process group
                unsafe {
                    libc::kill(-(pid as i32), libc::SIGTERM);
                }

                let start = std::time::Instant::now();
                loop {
                    match child.try_wait() {
                        Ok(Some(_)) => {
                            log::info!("n8n exited gracefully");
                            break;
                        }
                        Ok(None) => {
                            if start.elapsed() > Duration::from_secs(5) {
                                log::warn!("n8n did not exit in time, sending SIGKILL");
                                unsafe {
                                    libc::kill(-(pid as i32), libc::SIGKILL);
                                }
                                let _ = child.wait();
                                break;
                            }
                            std::thread::sleep(Duration::from_millis(100));
                        }
                        Err(e) => {
                            log::error!("Error waiting for n8n: {}", e);
                            let _ = child.kill();
                            break;
                        }
                    }
                }
            }

            #[cfg(windows)]
            {
                // /T kills the entire process tree. n8n's `n8n.cmd` spawns a
                // node.exe child; without /T the node.exe is orphaned.
                log::info!("Stopping n8n process tree (pid {})", pid);
                let mut tk = Command::new("taskkill");
                no_window(&mut tk);
                let _ = tk.args(["/T", "/PID", &pid.to_string()]).status();

                let start = std::time::Instant::now();
                loop {
                    match child.try_wait() {
                        Ok(Some(_)) => {
                            log::info!("n8n exited gracefully");
                            break;
                        }
                        Ok(None) => {
                            if start.elapsed() > Duration::from_secs(5) {
                                log::warn!("n8n did not exit; force-killing tree");
                                let mut force = Command::new("taskkill");
                                no_window(&mut force);
                                let _ = force
                                    .args(["/T", "/F", "/PID", &pid.to_string()])
                                    .status();
                                let _ = child.wait();
                                break;
                            }
                            std::thread::sleep(Duration::from_millis(100));
                        }
                        Err(e) => {
                            log::error!("Error waiting for n8n: {}", e);
                            let _ = child.kill();
                            break;
                        }
                    }
                }
            }

            *guard = None;
        }
    }
}

// ── Tauri commands (called from the frontend) ───────────────────────────

/// Check if Node.js is available on the system.
#[tauri::command]
pub fn check_node() -> Result<bool, String> {
    Ok(find_node().is_ok())
}

/// Check if n8n is installed.
#[tauri::command]
pub fn check_n8n_installed() -> Result<bool, String> {
    Ok(is_n8n_installed())
}

/// Get the status of the n8n process.
#[tauri::command]
pub fn n8n_status() -> Result<String, String> {
    if is_n8n_running() {
        Ok("running".to_string())
    } else {
        // Check if we have a process handle (started but not healthy yet)
        if let Ok(guard) = N8N_PROCESS.lock() {
            if guard.is_some() {
                return Ok("starting".to_string());
            }
        }
        Ok("not_running".to_string())
    }
}

/// Start the n8n process (Tauri command for UI).
#[tauri::command]
pub fn start_n8n() -> Result<String, String> {
    match startup_n8n() {
        N8nStartResult::Started => Ok("started".to_string()),
        N8nStartResult::AlreadyRunning => Ok("already_running".to_string()),
        N8nStartResult::NodeNotFound(msg) => Err(msg),
        N8nStartResult::N8nNotInstalled(msg) => Err(msg),
        N8nStartResult::StartFailed(msg) => Err(msg),
    }
}

/// Stop the n8n process (Tauri command for UI).
#[tauri::command]
pub fn stop_n8n() -> Result<String, String> {
    shutdown_n8n();
    Ok("stopped".to_string())
}
