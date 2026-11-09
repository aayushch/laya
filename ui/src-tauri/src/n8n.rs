// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

//! n8n process lifecycle management.
//!
//! Manages n8n as a local Node.js process instead of a Docker container.
//! n8n is installed via npm into ~/.laya/n8n_module/ and stores its data
//! in ~/.laya/n8n/.
//!
//! n8n runs on the Node binary recorded in the n8n runtime record (see
//! `n8n_upgrade.rs`). After a fresh install or a completed upgrade that is
//! Laya's managed Node in `~/.laya/node`. After a failed upgrade it is the
//! Node the previous n8n was installed with, which may be a system Node.

use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

/// On Windows, attach CREATE_NO_WINDOW so spawned child processes do not
/// flash a console window. No-op on other platforms.
#[cfg(windows)]
pub(crate) fn no_window(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
pub(crate) fn no_window(_cmd: &mut Command) {}

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

/// The n8n version Laya installs. Changing it changes the upgrade target
/// (`n8n_upgrade::target_id`), so existing installs move to the new version
/// through the upgrade sequence in `n8n_upgrade.rs` on their next launch.
pub(crate) const N8N_VERSION: &str = "2.46.6";

// ── Path helpers ────────────────────────────────────────────────────────

use crate::process_util::{home_dir, laya_home};

/// Where the n8n npm package is installed: ~/.laya/n8n_module/
pub(crate) fn n8n_module_dir() -> PathBuf {
    laya_home().join("n8n_module")
}

/// Where n8n stores its data (DB, encryption key, credentials): ~/.laya/n8n/
pub(crate) fn n8n_data_dir() -> PathBuf {
    laya_home().join("n8n")
}

/// n8n's JavaScript entry point inside an npm install rooted at `module_dir`.
/// Laya runs it as `<node> <entry> <command>`, so the Node binary is chosen
/// explicitly instead of by the `#!/usr/bin/env node` line of the launcher
/// script in `node_modules/.bin`.
pub(crate) fn n8n_entry_in(module_dir: &Path) -> PathBuf {
    module_dir
        .join("node_modules")
        .join("n8n")
        .join("bin")
        .join("n8n")
}

/// The n8n version recorded in `node_modules/n8n/package.json` under
/// `module_dir`, or None when n8n is not installed there.
pub(crate) fn installed_n8n_version_in(module_dir: &Path) -> Option<String> {
    let pkg = module_dir
        .join("node_modules")
        .join("n8n")
        .join("package.json");
    let text = std::fs::read_to_string(pkg).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    value
        .get("version")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

// ── Node.js detection ───────────────────────────────────────────────────

/// Well-known Node.js install directories on macOS / Linux.
/// macOS .app bundles inherit a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin)
/// that does NOT include these, so we must search them explicitly.
const NODE_SEARCH_DIRS: &[&str] = &["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin"];

/// Resolve a bare command name to its absolute path using the `which` crate,
/// which also works on a stock Windows install.
fn which(name: &str) -> Option<PathBuf> {
    let p = PathBuf::from(name);
    if p.is_absolute() && p.exists() {
        return Some(p);
    }
    ::which::which(name).ok()
}

/// Laya's managed Node (`~/.laya/node`) and its `node --version` output, if
/// it is installed and runs. New n8n installs always use this Node.
pub fn find_node() -> Result<(String, String), String> {
    let managed = crate::runtime::managed_node()
        .ok_or_else(|| "Laya's Node.js runtime is not installed yet.".to_string())?;
    let version = probe_node_version(&managed).ok_or_else(|| {
        format!(
            "Laya's Node.js runtime at {} does not run.",
            managed.display()
        )
    })?;
    Ok((managed.to_string_lossy().to_string(), version))
}

/// Run `<path> --version` and return its output (e.g. `v24.22.2`), or None
/// when the binary does not run.
pub(crate) fn probe_node_version(path: &Path) -> Option<String> {
    let mut cmd = Command::new(path);
    no_window(&mut cmd);
    cmd.arg("--version")
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    sanitize_node_cmd(&mut cmd);
    let output = cmd.output().ok()?;
    if !output.status.success() {
        return None;
    }
    let raw = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if raw.starts_with('v') {
        Some(raw)
    } else {
        None
    }
}

/// The Node that an n8n installed by an older Laya build runs on, for
/// installs that have no n8n runtime record yet (see `n8n_upgrade.rs`).
///
/// Older builds used the managed Node in `~/.laya/node` when it existed, and
/// otherwise the newest system Node of version 22 or newer. This function
/// applies the same rule, so the upgrade knows which Node to restore if it
/// has to roll back. Nothing else uses it: new installs run on `find_node()`.
pub fn find_old_runtime_node() -> Result<(String, String), String> {
    if let Some(managed) = crate::runtime::managed_node() {
        if let Some(version) = probe_node_version(&managed) {
            return Ok((managed.to_string_lossy().to_string(), version));
        }
    }

    let mut candidates: Vec<PathBuf> = vec![PathBuf::from("node")];
    if !cfg!(target_os = "windows") {
        for dir in NODE_SEARCH_DIRS {
            candidates.push(PathBuf::from(dir).join("node"));
        }
        // nvm installs: the newest version directory.
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

    // Probe all candidates and keep (abs_path, version_display, major, minor).
    let mut found: Vec<(String, String, u32, u32)> = Vec::new();
    for candidate in &candidates {
        let Some(version) = probe_node_version(candidate) else {
            continue;
        };
        let parts: Vec<&str> = version.trim_start_matches('v').split('.').collect();
        if parts.len() < 2 {
            continue;
        }
        let major: u32 = parts[0].parse().unwrap_or(0);
        let minor: u32 = parts[1].parse().unwrap_or(0);
        if major < 22 {
            continue;
        }
        let name = candidate.to_string_lossy();
        let abs_path = if candidate.is_absolute() && candidate.exists() {
            name.to_string()
        } else {
            which(&name)
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_else(|| name.to_string())
        };
        if !found.iter().any(|(p, _, _, _)| p == &abs_path) {
            found.push((abs_path, version, major, minor));
        }
    }

    found.sort_by(|a, b| (a.2, a.3).cmp(&(b.2, b.3)));
    found
        .pop()
        .map(|(path, version, _, _)| (path, version))
        .ok_or_else(|| "No Node.js 22 or newer was found on this system.".to_string())
}

/// Strip AppImage-injected environment variables before invoking
/// Node.js, same rationale as `sanitize_python_cmd` in sidecar.rs.
pub(crate) fn sanitize_node_cmd(cmd: &mut Command) {
    crate::process_util::sanitize_ld_library_path(cmd);
}

/// Build a PATH for a child process with the directory of `node_path` first.
/// npm lifecycle scripts and node-gyp start `node` by name, so they must find
/// the same Node that runs npm or n8n. On macOS and Linux the well-known
/// install directories follow, because a macOS .app bundle inherits a
/// minimal PATH that lacks the tools node-gyp needs (python, make, cc).
pub(crate) fn path_with_node_first(node_path: &Path) -> String {
    // PATH separator and join character are OS-specific — using ':' on Windows
    // would split drive letters (e.g. "C:\...").
    let sep_char = if cfg!(windows) { ';' } else { ':' };
    let sep_str = if cfg!(windows) { ";" } else { ":" };
    let current = std::env::var("PATH").unwrap_or_default();
    let mut front: Vec<String> = Vec::new();

    if let Some(dir) = node_path.parent() {
        front.push(dir.to_string_lossy().to_string());
    }

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
        .filter(|p| !p.is_empty() && !front.iter().any(|f| f == p))
        .collect();

    let mut parts = front;
    parts.extend(remaining.into_iter().map(String::from));
    parts.join(sep_str)
}

// ── n8n installation ────────────────────────────────────────────────────

/// Check if n8n is installed in ~/.laya/n8n_module/.
pub fn is_n8n_installed() -> bool {
    n8n_entry_in(&n8n_module_dir()).exists()
}

/// Path to the Python binary in the Laya managed venv.
/// Used to give `node-gyp` a Python that has `setuptools` (which
/// provides the `distutils` shim removed in Python 3.12+), and by the
/// upgrade to count workflows with Python's `sqlite3` module.
pub(crate) fn venv_python() -> PathBuf {
    if cfg!(target_os = "windows") {
        laya_home().join("venv").join("Scripts").join("python.exe")
    } else {
        laya_home().join("venv").join("bin").join("python")
    }
}

/// The expression engine an n8n install supports, decided at install time.
///
/// `Vm` evaluates expressions inside `isolated-vm`, n8n's default. `QuickJs`
/// is the WebAssembly engine, used when the `isolated-vm` native add-on did
/// not build or does not load on this machine. Without a loadable
/// `isolated-vm`, n8n exits at startup under `Vm`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExpressionEngine {
    Vm,
    QuickJs,
}

impl ExpressionEngine {
    /// The value of n8n's `N8N_EXPRESSION_ENGINE` setting.
    pub fn as_str(self) -> &'static str {
        match self {
            ExpressionEngine::Vm => "vm",
            ExpressionEngine::QuickJs => "quickjs",
        }
    }
}

/// Install `n8n@N8N_VERSION` with npm into `module_dir`, running npm on the
/// Node distribution whose `node` binary is `node_path` (its bundled npm is
/// used). Calls `on_line` for each line of npm output.
///
/// Attempt 1 is a normal install, which compiles or downloads native
/// add-ons. Attempt 2 runs only if attempt 1 fails: it skips all install
/// scripts, rebuilds the database driver, and removes `isolated-vm`.
/// Returns the expression engine the resulting install supports.
pub fn install_n8n_into<F: FnMut(&str)>(
    module_dir: &Path,
    node_path: &Path,
    mut on_line: F,
) -> Result<ExpressionEngine, String> {
    let node_root = node_distribution_root(node_path)?;
    let npm_cli = crate::runtime::npm_cli_in(&node_root);
    if !npm_cli.exists() {
        return Err(format!("npm not found at {}", npm_cli.display()));
    }

    crate::fs_private::mkdir_private(module_dir)
        .map_err(|e| format!("Failed to create {}: {e}", module_dir.display()))?;
    crate::fs_private::mkdir_private(&n8n_data_dir())
        .map_err(|e| format!("Failed to create n8n data directory: {e}"))?;

    // Start a fresh install log for this run. run_npm_install and
    // rebuild_native_addon append to it, so both install attempts are kept.
    let log_dir = laya_home().join("logs");
    crate::fs_private::mkdir_private(&log_dir).ok();
    let _ = crate::fs_private::create_file_private(&n8n_install_log_path());

    let npm = NpmRunner {
        node: node_path.to_path_buf(),
        npm_cli,
        path: path_with_node_first(node_path),
        gyp_python: gyp_python(),
    };

    // ── Attempt 1: full install (native add-ons build, isolated-vm included)
    log::info!(
        "Installing n8n@{} via npm into {} with {}",
        N8N_VERSION,
        module_dir.display(),
        node_path.display()
    );
    on_line(&format!(
        "Installing n8n {} (this may take a few minutes)...",
        N8N_VERSION
    ));

    let attempt1 = run_npm_install(&npm, module_dir, false, &mut on_line);
    if attempt1.is_ok() && n8n_entry_in(module_dir).exists() {
        on_line("Checking the expression sandbox (isolated-vm)...");
        if !crate::n8n_upgrade::force_quickjs() && isolated_vm_loads(module_dir, node_path) {
            log::info!("n8n installed with isolated-vm; expression engine: vm");
            on_line("n8n installed");
            return Ok(ExpressionEngine::Vm);
        }
        log::warn!("isolated-vm does not load on this machine; using the quickjs engine");
        remove_isolated_vm(module_dir);
        on_line("n8n installed (expression engine: quickjs)");
        return Ok(ExpressionEngine::QuickJs);
    }
    if let Err(e) = &attempt1 {
        log::warn!("Full n8n install failed: {e}");
    }

    // ── Attempt 2: skip install scripts, remove isolated-vm.
    // Native add-on builds can fail for many reasons (missing Xcode command
    // line tools, missing C++ build tools on Windows, no prebuilt binary for
    // this platform). --ignore-scripts skips every build. The database driver
    // is then rebuilt on its own, and isolated-vm is removed so n8n runs its
    // expressions in the quickjs engine.
    on_line("Retrying without native compilation...");
    let node_modules = module_dir.join("node_modules");
    if node_modules.exists() {
        let _ = std::fs::remove_dir_all(&node_modules);
    }

    run_npm_install(&npm, module_dir, true, &mut on_line)?;
    if !n8n_entry_in(module_dir).exists() {
        return Err("n8n entry point not found after npm install".to_string());
    }

    on_line("Building database drivers...");
    rebuild_native_addon(&npm, module_dir, "sqlite3", &mut on_line);
    rebuild_native_addon(&npm, module_dir, "better-sqlite3", &mut on_line);
    remove_isolated_vm(module_dir);

    log::info!("n8n installed without native add-ons; expression engine: quickjs");
    on_line("n8n installed (expression engine: quickjs)");
    Ok(ExpressionEngine::QuickJs)
}

/// The root of the Node distribution that contains `node_path`: the
/// directory itself on Windows (`node.exe` sits at the top), its parent
/// elsewhere (`bin/node`).
fn node_distribution_root(node_path: &Path) -> Result<PathBuf, String> {
    let bin_dir = node_path
        .parent()
        .ok_or_else(|| format!("Bad Node path {}", node_path.display()))?;
    if cfg!(target_os = "windows") {
        Ok(bin_dir.to_path_buf())
    } else {
        bin_dir
            .parent()
            .map(|p| p.to_path_buf())
            .ok_or_else(|| format!("Bad Node path {}", node_path.display()))
    }
}

/// A Python with setuptools for node-gyp: the managed venv's Python, or in a
/// dev build the engine's own `.venv`.
fn gyp_python() -> Option<PathBuf> {
    let venv_py = venv_python();
    let dev_venv_py = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("engine")
        .join(".venv")
        .join("bin")
        .join("python");
    if venv_py.exists() {
        Some(venv_py)
    } else if cfg!(dev) && dev_venv_py.exists() {
        Some(dev_venv_py)
    } else {
        None
    }
}

/// Load `isolated-vm` from `module_dir` with `node_path` and evaluate one
/// expression in a new isolate. True only if that succeeds, which is what
/// n8n's `vm` expression engine needs at startup.
fn isolated_vm_loads(module_dir: &Path, node_path: &Path) -> bool {
    if !module_dir.join("node_modules").join("isolated-vm").exists() {
        return false;
    }
    let script = "const ivm=require('isolated-vm');\
                  const i=new ivm.Isolate({memoryLimit:16});\
                  const r=i.compileScriptSync('1+1').runSync(i.createContextSync());\
                  i.dispose();\
                  if(r!==2)process.exit(3);";
    let mut cmd = Command::new(node_path);
    no_window(&mut cmd);
    sanitize_node_cmd(&mut cmd);
    cmd.args(["-e", script])
        .current_dir(module_dir)
        .env("PATH", path_with_node_first(node_path))
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    match cmd.output() {
        Ok(out) if out.status.success() => true,
        Ok(out) => {
            log::warn!(
                "isolated-vm check failed (exit {}): {}",
                out.status.code().unwrap_or(-1),
                String::from_utf8_lossy(&out.stderr)
                    .chars()
                    .take(400)
                    .collect::<String>()
            );
            false
        }
        Err(e) => {
            log::warn!("isolated-vm check could not run: {e}");
            false
        }
    }
}

/// Delete `node_modules/isolated-vm` from an install. n8n 2.46 starts
/// without it under the `quickjs` expression engine.
fn remove_isolated_vm(module_dir: &Path) {
    let dir = module_dir.join("node_modules").join("isolated-vm");
    if dir.exists() {
        if let Err(e) = std::fs::remove_dir_all(&dir) {
            log::warn!("Failed to remove {}: {e}", dir.display());
        }
    }
}

/// How to run npm: `node npm-cli.js ...` with `PATH` led by that Node's
/// directory, and the Python node-gyp should use.
struct NpmRunner {
    node: PathBuf,
    npm_cli: PathBuf,
    path: String,
    gyp_python: Option<PathBuf>,
}

impl NpmRunner {
    fn command(&self, module_dir: &Path) -> Command {
        let mut cmd = Command::new(&self.node);
        no_window(&mut cmd);
        sanitize_node_cmd(&mut cmd);
        cmd.arg(&self.npm_cli)
            .current_dir(module_dir)
            .env("PATH", &self.path);
        if let Some(ref py) = self.gyp_python {
            cmd.env("npm_config_python", py);
        }
        cmd
    }
}

/// Rebuild a single native addon that was skipped by --ignore-scripts.
/// Best-effort — logs a warning on failure but doesn't abort the install.
fn rebuild_native_addon<F: FnMut(&str)>(
    npm: &NpmRunner,
    module_dir: &Path,
    package: &str,
    on_line: &mut F,
) {
    let pkg_dir = module_dir.join("node_modules").join(package);
    if !pkg_dir.exists() {
        return; // Not a dependency of this n8n version.
    }

    log::info!("Rebuilding native addon: {}", package);

    let mut cmd = npm.command(module_dir);
    cmd.args(["rebuild", package]);

    match cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).output() {
        Ok(output) => {
            if let Ok(mut f) = crate::fs_private::append_file_private(&n8n_install_log_path()) {
                let _ = writeln!(f, "\n=== npm rebuild {} ===", package);
                let _ = f.write_all(&output.stdout);
                let _ = f.write_all(&output.stderr);
            }
            if output.status.success() {
                log::info!("Rebuilt {} successfully", package);
                on_line(&format!("{} ready", package));
            } else {
                let stderr = String::from_utf8_lossy(&output.stderr);
                log::warn!(
                    "Failed to rebuild {} (non-fatal): {}",
                    package,
                    stderr.chars().take(200).collect::<String>()
                );
                on_line(&format!("{} build failed (will try prebuilt)", package));
            }
        }
        Err(e) => {
            log::warn!("Failed to run npm rebuild {}: {}", package, e);
        }
    }
}

/// Path to the npm install log (distinct from the runtime `n8n.log` that the
/// n8n server process writes when it starts).
fn n8n_install_log_path() -> PathBuf {
    laya_home().join("logs").join("n8n-install.log")
}

/// Run `npm install` and stream output. Returns Ok(()) on success.
fn run_npm_install<F: FnMut(&str)>(
    npm: &NpmRunner,
    module_dir: &Path,
    ignore_scripts: bool,
    on_line: &mut F,
) -> Result<(), String> {
    let prefix = module_dir.to_string_lossy().to_string();
    let package = format!("n8n@{}", N8N_VERSION);

    // `--allow-remote=all`: npm 12 defaults `allow-remote` to "none" and refuses
    // any dependency resolved to a non-registry tarball URL (EALLOWREMOTE).
    // n8n's tree has one: n8n-nodes-base pins `xlsx` to
    // https://cdn.sheetjs.com/xlsx-0.20.2/xlsx-0.20.2.tgz. `root` is not enough
    // because xlsx is transitive. npm 10 accepts the flag silently and npm 11
    // only warns "Unknown cli config", so it is safe to pass unconditionally.
    // See https://github.com/aayushch/laya/issues/18.
    //
    // `--save-exact` makes `package.json` record the exact version instead
    // of a `^` range, so a later `npm install` in this directory cannot move
    // n8n to another version without going through the upgrade.
    let mut args: Vec<&str> = vec!["install", "--prefix", &prefix, "--save-exact"];
    if ignore_scripts {
        args.push("--ignore-scripts");
    }
    args.push("--allow-remote=all");
    args.push(&package);

    let mut cmd = npm.command(module_dir);
    cmd.args(&args);

    let mut child = cmd
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to run npm install: {e}"))?;
    // Registered so that quitting Laya stops the install instead of leaving
    // npm writing into the directory after the app has gone.
    crate::n8n_upgrade::register_child(Some(child.id()));

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

    // Tee npm's streamed output to the install log (append: install_n8n_into
    // may call this twice — native attempt then --ignore-scripts fallback).
    let mut log = crate::fs_private::append_file_private(&n8n_install_log_path()).ok();
    let mut output_lines: Vec<String> = Vec::new();
    for line in rx {
        on_line(&line);
        if let Some(f) = log.as_mut() {
            let _ = writeln!(f, "{line}");
        }
        output_lines.push(line);
    }

    if let Some(h) = stdout_thread {
        let _ = h.join();
    }
    if let Some(h) = stderr_thread {
        let _ = h.join();
    }

    let status = child.wait();
    crate::n8n_upgrade::register_child(None);
    let status = status.map_err(|e| format!("npm wait failed: {e}"))?;
    if !status.success() {
        let tail: Vec<&str> = output_lines
            .iter()
            .rev()
            .take(5)
            .map(|s| s.as_str())
            .collect();
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

/// True while `n8n_upgrade` is replacing n8n. `startup_n8n` refuses to start
/// n8n during that time, so a "Start" click on the Status page cannot open
/// the database while it is being backed up or migrated.
pub(crate) static UPGRADE_IN_PROGRESS: AtomicBool = AtomicBool::new(false);

/// Which Node runs n8n and which expression engine it uses.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct N8nRuntime {
    pub node_path: PathBuf,
    /// The `N8N_EXPRESSION_ENGINE` value, or None to leave n8n's own default
    /// (n8n 2.15.0 predates the setting).
    pub expression_engine: Option<String>,
}

/// Result of attempting to start n8n.
#[derive(Debug)]
pub enum N8nStartResult {
    Started,
    AlreadyRunning,
    NodeNotFound(String),
    N8nNotInstalled(String),
    StartFailed(String),
    /// The n8n port is held by a process that isn't Laya's n8n, so we refused
    /// to kill it and can't bind. Surfaced explicitly instead of failing
    /// invisibly (review §6 / P2-4).
    PortConflict(String),
}

/// Start n8n on the runtime recorded for the installed n8n
/// (`n8n_upgrade::active_runtime`).
pub fn startup_n8n() -> N8nStartResult {
    if UPGRADE_IN_PROGRESS.load(Ordering::SeqCst) {
        return N8nStartResult::StartFailed(
            "n8n is being upgraded. It starts when the upgrade finishes.".to_string(),
        );
    }
    match crate::n8n_upgrade::active_runtime() {
        Some(runtime) => startup_n8n_with(&runtime),
        None => N8nStartResult::NodeNotFound(
            "No Node.js runtime for n8n was found. Restart Laya to install it.".to_string(),
        ),
    }
}

/// Start n8n from `~/.laya/n8n_module` on the given runtime. Used directly by
/// the upgrade, which starts the new and the old runtime explicitly.
pub fn startup_n8n_with(runtime: &N8nRuntime) -> N8nStartResult {
    // 1. Already running? (check HTTP health)
    if is_n8n_running() {
        // If we hold a handle, we spawned it — reuse. Otherwise it's a
        // stale orphan from a previous session (e.g. dev-mode exit that
        // didn't fire RunEvent::Exit). Kill it and respawn so the new
        // process gets the correct environment variables.
        let we_own_it = N8N_PROCESS.lock().map(|g| g.is_some()).unwrap_or(false);
        if we_own_it {
            return N8nStartResult::AlreadyRunning;
        }
        if let Err(msg) = stop_orphaned_n8n() {
            return N8nStartResult::PortConflict(msg);
        }
    }

    // 2. Node.js available?
    if !runtime.node_path.exists() {
        return N8nStartResult::NodeNotFound(format!(
            "The Node.js runtime for n8n is missing: {}",
            runtime.node_path.display()
        ));
    }

    // 3. n8n installed?
    if !is_n8n_installed() {
        return N8nStartResult::N8nNotInstalled(
            "n8n is not installed yet. It will be installed during setup.".to_string(),
        );
    }

    // 4. Start
    match spawn_n8n(runtime) {
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

/// Stop a Laya n8n left running on `N8N_PORT` by an earlier session. Only a
/// process whose command line contains "laya" (the n8n entry point lives
/// under `~/.laya/`) is stopped; a user's own n8n on this port is left alone
/// and reported as an error.
pub(crate) fn stop_orphaned_n8n() -> Result<(), String> {
    if !is_n8n_running() {
        return Ok(());
    }
    log::warn!("Orphaned n8n on port {} — stopping it", N8N_PORT);
    crate::kill_process_on_port(N8N_PORT, "laya");
    for _ in 0..20 {
        if !is_n8n_running() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    Err(format!(
        "Port {} is in use by a process that isn't Laya's n8n. \
         Stop that process (or free the port) and restart Laya.",
        N8N_PORT
    ))
}

/// A `Command` that runs `<node> <n8n entry in module_dir>` with the
/// environment every Laya n8n process gets: data folder, loopback-only
/// networking, telemetry off, and the expression engine. `spawn_n8n` adds
/// `start` and the per-launch tokens; the upgrade adds a CLI command such as
/// `list:workflow`, which runs n8n's pending database migrations first.
pub(crate) fn n8n_command(module_dir: &Path, runtime: &N8nRuntime) -> Command {
    let data_dir = n8n_data_dir();
    let mut cmd = Command::new(&runtime.node_path);
    no_window(&mut cmd);
    // Strip AppImage-injected LD_LIBRARY_PATH so n8n uses the correct
    // system libraries (see sanitize_node_cmd).
    sanitize_node_cmd(&mut cmd);
    cmd.arg(n8n_entry_in(module_dir))
        .env("PATH", path_with_node_first(&runtime.node_path))
        .env("N8N_USER_FOLDER", data_dir.to_string_lossy().as_ref())
        .env("N8N_PORT", N8N_PORT.to_string())
        .env("N8N_HOST", "127.0.0.1")
        // N8N_HOST only sets the host n8n reports in URLs; the bind address
        // is N8N_LISTEN_ADDRESS, which defaults to "::" (every interface).
        // Bind loopback only so the editor, REST API and webhooks are not
        // reachable from the LAN.
        .env("N8N_LISTEN_ADDRESS", "127.0.0.1")
        .env("WEBHOOK_URL", format!("http://127.0.0.1:{}/", N8N_PORT))
        .env("N8N_SECURE_COOKIE", "false")
        .env("N8N_PUBLIC_API_DISABLED", "false")
        .env("N8N_RUNNERS_ENABLED", "false")
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
        // n8n 2.x blocks $env access in workflow expressions by default
        .env("N8N_BLOCK_ENV_ACCESS_IN_NODE", "false")
        // Workflow expressions can read n8n's environment (see above), so the
        // engine API token must not be in it. It is only present in the shell's
        // own environment when a developer presets it for a dev build.
        .env_remove(crate::ENGINE_TOKEN_ENV);
    match &runtime.expression_engine {
        Some(engine) => {
            cmd.env("N8N_EXPRESSION_ENGINE", engine);
        }
        None => {
            cmd.env_remove("N8N_EXPRESSION_ENGINE");
        }
    }
    if let Some(tz) = system_timezone(&runtime.node_path) {
        cmd.env("LAYA_TIMEZONE", tz);
    }
    cmd
}

/// The machine's IANA time zone name (for example `Europe/Berlin`), read
/// once from Node's `Intl` API and cached. Workflows read it as
/// `$env.LAYA_TIMEZONE`. Inside n8n's `quickjs` expression engine `Intl`
/// cannot report the time zone, so the bundled workflows must not call it
/// themselves. Node maps Windows time zone names to IANA names, so this
/// works on every platform.
fn system_timezone(node_path: &Path) -> Option<String> {
    static TZ: OnceLock<Option<String>> = OnceLock::new();
    TZ.get_or_init(|| {
        let mut cmd = Command::new(node_path);
        no_window(&mut cmd);
        sanitize_node_cmd(&mut cmd);
        let out = cmd
            .args([
                "-e",
                "process.stdout.write(Intl.DateTimeFormat().resolvedOptions().timeZone||'')",
            ])
            .stderr(Stdio::null())
            .output()
            .ok()?;
        let tz = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if out.status.success() && !tz.is_empty() {
            Some(tz)
        } else {
            None
        }
    })
    .clone()
}

/// Spawn the n8n server process with the correct environment variables.
fn spawn_n8n(runtime: &N8nRuntime) -> Result<Child, String> {
    let data_dir = n8n_data_dir();
    crate::fs_private::mkdir_private(&data_dir)
        .map_err(|e| format!("Failed to create n8n data dir: {e}"))?;

    let log_dir = laya_home().join("logs");
    crate::fs_private::mkdir_private(&log_dir).ok();

    log::info!(
        "Spawning n8n start on port {} with {} (expression engine: {})",
        N8N_PORT,
        runtime.node_path.display(),
        runtime
            .expression_engine
            .as_deref()
            .unwrap_or("n8n default")
    );

    let mut cmd = n8n_command(&n8n_module_dir(), runtime);
    cmd.arg("start")
        .env(
            "LAYA_ENGINE_URL",
            format!(
                "http://127.0.0.1:{}",
                std::env::var("LAYA_ENGINE_PORT").unwrap_or_else(|_| "8420".to_string())
            ),
        )
        // The bundled workflows read this with `$env.LAYA_N8N_TOKEN` and send
        // it as the X-Laya-N8n-Token header on every call to the engine.
        .env(crate::N8N_TOKEN_ENV, crate::n8n_token())
        // Pipe stdout/stderr so pipe_to_rotating() can capture them into a
        // size-capped, rotating n8n.log (see below).
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    // Create a new process group so we can kill all n8n children on shutdown.
    // Also set umask 0o077 in the child so n8n creates its database, encryption
    // key, and credential files with at most 0700/0600 (owner-only) permissions.
    // The parent process umask is unchanged.
    #[cfg(unix)]
    unsafe {
        use std::os::unix::process::CommandExt;
        cmd.pre_exec(|| {
            libc::umask(0o077);
            libc::setpgid(0, 0);
            Ok(())
        });
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn n8n: {e}"))?;

    // Capture n8n's stdout/stderr into a size-capped, rotating log so n8n.log
    // can't grow unbounded within a long session. 10 MB × 3 backups.
    if let Err(e) =
        crate::rotating_log::pipe_to_rotating(&mut child, &log_dir, "n8n.log", 10 * 1024 * 1024, 3)
    {
        log::warn!("Failed to set up rotating n8n log capture: {e}");
    }

    Ok(child)
}

/// Check if n8n is running by hitting its health endpoint.
pub fn is_n8n_running() -> bool {
    shared_client()
        .get(format!("http://127.0.0.1:{}/healthz", N8N_PORT))
        .send()
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

/// True if Laya started n8n and that process has not exited.
fn n8n_child_alive() -> bool {
    match N8N_PROCESS.lock() {
        Ok(mut guard) => match guard.as_mut() {
            Some(child) => matches!(child.try_wait(), Ok(None)),
            None => false,
        },
        Err(_) => false,
    }
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

/// Wait until n8n's REST API answers, then check that the n8n process
/// Laya started is still running `stable_for` later and the API still
/// answers. n8n with an `isolated-vm` add-on that does not load answers
/// `/rest/settings` briefly and then exits, so an answer alone does not
/// show that n8n is working.
pub fn wait_for_n8n_stable(timeout: Duration, stable_for: Duration) -> Result<(), String> {
    if !wait_for_n8n_api(timeout) {
        if !n8n_child_alive() {
            return Err("n8n exited during startup. See ~/.laya/logs/n8n.log.".to_string());
        }
        return Err(format!(
            "n8n did not answer within {} seconds. See ~/.laya/logs/n8n.log.",
            timeout.as_secs()
        ));
    }
    std::thread::sleep(stable_for);
    if !n8n_child_alive() {
        return Err("n8n started and then exited. See ~/.laya/logs/n8n.log.".to_string());
    }
    if !wait_for_n8n_api(Duration::from_secs(10)) {
        return Err("n8n stopped answering after startup. See ~/.laya/logs/n8n.log.".to_string());
    }
    Ok(())
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
                // /T stops the whole process tree, including any process
                // node.exe started.
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
                                let _ = force.args(["/T", "/F", "/PID", &pid.to_string()]).status();
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

/// Check if a Node.js runtime for n8n is available.
#[tauri::command]
pub fn check_node() -> Result<bool, String> {
    Ok(crate::n8n_upgrade::active_runtime().is_some())
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
        N8nStartResult::PortConflict(msg) => Err(msg),
    }
}

/// Stop the n8n process (Tauri command for UI).
#[tauri::command]
pub fn stop_n8n() -> Result<String, String> {
    shutdown_n8n();
    Ok("stopped".to_string())
}
