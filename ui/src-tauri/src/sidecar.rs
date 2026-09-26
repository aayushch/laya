// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

//! Python environment and engine lifecycle management.
//!
//! In development: spawns `python -m laya.main` from the engine's dev venv.
//! In production: manages a venv at `~/.laya/venv/`, installs requirements
//! from the bundled `requirements.txt`, and runs the engine from the bundled
//! Python source in `Contents/Resources/engine/`.

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::Duration;

#[cfg(unix)]
use std::os::unix::process::CommandExt;

pub fn engine_url() -> String {
    let port = std::env::var("LAYA_ENGINE_PORT").unwrap_or_else(|_| "8420".to_string());
    format!("http://127.0.0.1:{}", port)
}

pub fn engine_port() -> u16 {
    std::env::var("LAYA_ENGINE_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8420)
}

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

// ── Path helpers ────────────────────────────────────────────────────────

use crate::process_util::laya_home;

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
        let mac_resources = exe_dir
            .parent()                   // Contents/
            .map(|p| p.join("Resources").join("resources").join("engine"))
            .unwrap_or_else(|| exe_dir.join("engine"));
        if mac_resources.exists() {
            return mac_resources;
        }
        // Linux AppImage / .deb: exe is at usr/bin/, resources at
        // usr/lib/<productName>/resources/engine/
        let linux_resources = exe_dir
            .parent()                   // usr/
            .map(|p| p.join("lib").join("Laya").join("resources").join("engine"))
            .unwrap_or_else(|| exe_dir.join("engine"));
        if linux_resources.exists() {
            return linux_resources;
        }
        // Windows MSI / NSIS: exe is at <install_dir>\laya-app.exe,
        // resources at <install_dir>\resources\engine\
        let win_resources = exe_dir.join("resources").join("engine");
        if win_resources.exists() {
            return win_resources;
        }
        // Fallback: next to executable
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
    /// Whether Node.js 22+ is available on the system
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

    let venv_ready = venv_is_usable();
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

// ── AppImage environment sanitization ───────────────────────────────────

/// When running inside an AppImage, the runtime sets PYTHONHOME, PYTHONPATH,
/// and LD_LIBRARY_PATH which confuse the *system* Python (it can't find its
/// own stdlib — notably `encodings`). Strip these before calling out to the
/// host Python. Without this fix, `python -m venv` fails with:
///   "ModuleNotFoundError: No module named 'encodings'"
fn sanitize_python_cmd(cmd: &mut Command) {
    cmd.env_remove("PYTHONHOME").env_remove("PYTHONPATH");
    crate::process_util::sanitize_ld_library_path(cmd);
}

// ── Python detection ────────────────────────────────────────────────────

/// Oldest CPython minor version (3.x) Laya's engine supports.
const MIN_PYTHON_MINOR: u32 = 10;

/// System Pythons up to this minor are preferred (first detection pass):
/// every package, ML included, ships wheels for them on all our platforms.
const PREFERRED_MAX_PYTHON_MINOR: u32 = 13;

/// Newest CPython minor version (3.x) Laya will use from the *system* at
/// all (second detection pass), and the newest a venv may be built on.
///
/// Why a ceiling: core deps are installed with `--only-binary :all:`, so a
/// Python newer than what PyPI publishes wheels for fails the whole setup
/// (e.g. 3.15 → "Wheels are required for `aiohttp`", issue #14). Anything
/// newer than this is skipped so `ensure_runtimes` provisions the pinned,
/// tested managed CPython instead. 3.14 stays accepted because earlier
/// releases accepted it and existing installs (notably Homebrew 3.14 on
/// Apple Silicon) must keep their working venv. Raise this only after
/// verifying every package in requirements.txt ships wheels for it.
const MAX_PYTHON_MINOR: u32 = 14;

/// `sysconfig.get_platform()` values for which PyPI lacks wheels for core
/// deps (chromadb, tiktoken, litellm→fastuuid, grpcio, httptools) and torch.
///
/// Why: on Windows-on-ARM the (x64-only) Laya build runs under emulation and
/// would happily pick up a native ARM64 Python from PATH, whose venv can
/// never satisfy `--only-binary :all:` (issue #14). Rejecting it makes
/// `ensure_runtimes` download the managed x64 CPython, which runs fine under
/// emulation. 32-bit Windows Python has the same wheel gap. We deliberately
/// do NOT require "Python arch == app arch" globally: on macOS/Linux an
/// arm64 Python under an x64 app is fine (the engine is a separate process
/// and arm64 wheels exist there).
const UNSUPPORTED_PYTHON_PLATFORMS: &[&str] = &["win-arm64", "win32"];

/// Facts about an interpreter, gathered by actually running it.
#[derive(Debug, Clone, PartialEq)]
struct PythonProbe {
    minor: u32,
    /// Full version string, e.g. "3.12.13" or "3.15.0rc2".
    version: String,
    /// `sysconfig.get_platform()`, e.g. "win-amd64", "win-arm64",
    /// "macosx-11.0-arm64", "linux-x86_64". Note: `platform.machine()` is
    /// NOT usable for this — under Windows' x64 emulation it reports the
    /// host's "ARM64" even for an x64 interpreter.
    platform: String,
    /// `sys.executable` — the real interpreter path. Matters for the `py`
    /// launcher, where the command name is not the interpreter we'd want
    /// to hand to `uv venv --python`.
    executable: PathBuf,
}

/// Printed one fact per line so paths with spaces parse unambiguously.
const PROBE_SCRIPT: &str = "import sys, sysconfig, platform\n\
print(sys.version_info[0]); print(sys.version_info[1])\n\
print(platform.python_version()); print(sysconfig.get_platform())\n\
print(sys.executable)";

/// Parse `PROBE_SCRIPT` output. Returns None for anything that isn't CPython 3.
fn parse_probe_output(stdout: &str) -> Option<PythonProbe> {
    let mut lines = stdout.lines().map(str::trim);
    let major: u32 = lines.next()?.parse().ok()?;
    let minor: u32 = lines.next()?.parse().ok()?;
    let version = lines.next()?.to_string();
    let platform = lines.next()?.to_string();
    let executable = lines.next().filter(|s| !s.is_empty())?;
    if major != 3 {
        return None;
    }
    Some(PythonProbe { minor, version, platform, executable: PathBuf::from(executable) })
}

/// Run `program [prefix_args] -c PROBE_SCRIPT` and parse the result.
fn probe_python(program: &Path, prefix_args: &[&str]) -> Option<PythonProbe> {
    let mut cmd = Command::new(program);
    cmd.args(prefix_args).args(["-c", PROBE_SCRIPT]);
    no_window(&mut cmd);
    sanitize_python_cmd(&mut cmd);
    let output = cmd.output().ok()?;
    if !output.status.success() {
        return None;
    }
    parse_probe_output(&String::from_utf8_lossy(&output.stdout))
}

/// Why an interpreter is unsuitable for Laya's venv, or Ok if it is suitable.
/// `max_minor` is None for the managed runtime (pinned by us; no ceiling).
fn check_python_supported(probe: &PythonProbe, max_minor: Option<u32>) -> Result<(), String> {
    if probe.minor < MIN_PYTHON_MINOR {
        return Err(format!("Python {} is older than 3.{}", probe.version, MIN_PYTHON_MINOR));
    }
    if let Some(max) = max_minor {
        if probe.minor > max {
            return Err(format!(
                "Python {} is newer than the latest tested version (3.{}); prebuilt packages are not available for it yet",
                probe.version, max
            ));
        }
    }
    if UNSUPPORTED_PYTHON_PLATFORMS.contains(&probe.platform.as_str()) {
        return Err(format!(
            "Python {} is a {} build; required packages have no prebuilt wheels for that platform",
            probe.version, probe.platform
        ));
    }
    Ok(())
}

/// Find a supported Python interpreter, preferring Laya's managed install at
/// `~/.laya/python/` (if present) over whatever the user has on `PATH`.
///
/// The managed install is pinned to a known-good version that has been
/// tested against Laya's requirements, so picking it first avoids surprise
/// behavior changes when the user upgrades their system Python.
pub fn find_python() -> Result<(PathBuf, String), String> {
    if let Some(managed) = crate::runtime::managed_python() {
        if let Some(probe) = probe_python(&managed, &[]) {
            if check_python_supported(&probe, None).is_ok() {
                return Ok((managed, probe.version));
            }
        }
    }
    find_python_system()
}

/// Search the system for a supported interpreter (3.10–3.14, not a
/// platform lacking wheels), ignoring any managed install in
/// `~/.laya/python/`.  Used by the runtime provisioner to decide whether a
/// download is necessary; the regular `find_python()` entry point is what
/// the rest of the codebase should call.
pub fn find_python_system() -> Result<(PathBuf, String), String> {
    // (program, prefix args).
    let candidates: Vec<(&str, &[&str])> = if cfg!(target_os = "windows") {
        vec![
            // The python.org installer doesn't create `python3.X.exe`; each
            // version is reachable via the `py` launcher. Ask for versions
            // explicitly — a bare `py` picks the newest install, e.g. a 3.15
            // that can't install our deps even though a 3.12 is present.
            ("py", &["-3.14"]),
            ("py", &["-3.13"]),
            ("py", &["-3.12"]),
            ("py", &["-3.11"]),
            ("py", &["-3.10"]),
            ("python3.13", &[]),
            ("python3.12", &[]),
            ("python3.11", &[]),
            ("python3.10", &[]),
            ("python3", &[]),
            ("python", &[]),
        ]
    } else {
        // Unchanged from before issue #14 — the probe below adds the
        // version ceiling; ordering here must not change which interpreter
        // existing macOS/Linux installs pick.
        vec![
            // Prefer versioned binaries so we find 3.13 even if python3 -> 3.14
            ("python3.13", &[]),
            ("python3.12", &[]),
            ("python3.11", &[]),
            ("python3.10", &[]),
            ("/opt/homebrew/bin/python3.13", &[]),
            ("/opt/homebrew/bin/python3.12", &[]),
            ("/opt/homebrew/bin/python3.11", &[]),
            ("/opt/homebrew/bin/python3.10", &[]),
            ("/usr/local/bin/python3.13", &[]),
            ("/usr/local/bin/python3.12", &[]),
            ("/usr/local/bin/python3.11", &[]),
            ("/usr/local/bin/python3.10", &[]),
            ("python3", &[]),
            ("/opt/homebrew/bin/python3", &[]),
            ("/usr/local/bin/python3", &[]),
            ("/usr/bin/python3", &[]),
            ("python", &[]),
        ]
    };

    // Probe lazily, in order, and stop at the first match — never probe all
    // candidates up front. On macOS without the Command Line Tools,
    // executing `/usr/bin/python3` pops the "install developer tools" GUI
    // dialog, so it must only run when nothing earlier matched (as before).
    // Results are cached so the second pass doesn't re-spawn interpreters.
    let mut cache: Vec<Option<Option<(PathBuf, PythonProbe)>>> = vec![None; candidates.len()];

    // First pass: prefer 3.10–3.13 (known to work with ML packages).
    // Second pass: accept up to MAX_PYTHON_MINOR (3.14).
    let mut rejected: Vec<String> = Vec::new();
    for max in [PREFERRED_MAX_PYTHON_MINOR, MAX_PYTHON_MINOR] {
        for (i, (name, args)) in candidates.iter().enumerate() {
            let probed = cache[i].get_or_insert_with(|| {
                let program = which(name)?;
                let probe = probe_python(&program, args)?;
                Some((program, probe))
            });
            let Some((program, probe)) = probed else { continue };
            match check_python_supported(probe, Some(max)) {
                Ok(()) => {
                    // For the `py` launcher, the command isn't the interpreter:
                    // `uv venv --python py.exe` would resolve the launcher's
                    // default (newest) version. Use the real sys.executable.
                    // Everywhere else keep the resolved command path, exactly
                    // as before, so macOS/Linux pick the same path they always did.
                    let path = if args.is_empty() { program.clone() } else { probe.executable.clone() };
                    log::info!(
                        "Using system Python {} ({}) at {} (via {})",
                        probe.version, probe.platform, path.display(), name
                    );
                    return Ok((path, probe.version.clone()));
                }
                Err(reason) => {
                    if max == MAX_PYTHON_MINOR {
                        log::info!("Skipping {} (via {}): {}", probe.executable.display(), name, reason);
                        let entry = format!("{} ({})", probe.executable.display(), reason);
                        if !rejected.contains(&entry) {
                            rejected.push(entry);
                        }
                    }
                }
            }
        }
    }

    let mut msg = format!(
        "No compatible Python found (need Python 3.{MIN_PYTHON_MINOR}–3.{MAX_PYTHON_MINOR}{}). \
         Laya normally downloads its own Python automatically.",
        if cfg!(target_os = "windows") { ", 64-bit x86 build" } else { "" }
    );
    if !rejected.is_empty() {
        msg.push_str(&format!(" Skipped: {}.", rejected.join("; ")));
    }
    Err(msg)
}

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

// ── Venv management ─────────────────────────────────────────────────────

/// Whether `~/.laya/venv` exists at all (usable or not).
pub fn venv_exists() -> bool {
    venv_dir().exists()
}

/// Whether `~/.laya/venv` exists AND its interpreter is one we'd still pick.
///
/// Why not just `venv_python().exists()`: a venv built on an unsupported
/// interpreter (e.g. Windows ARM64 Python 3.15, issue #14) would otherwise
/// be reused on every launch, so users stay stuck on the failing
/// `pip install` even after the interpreter selection is fixed. Reporting
/// such a venv as not-ready makes setup rebuild it via `create_venv`.
fn venv_is_usable() -> bool {
    let python = venv_python();
    if !python.exists() {
        return false;
    }
    // Apply the same ceiling as system detection: an x64 venv built on a
    // too-new Python (3.15) is just as stuck as an ARM64 one. The managed
    // runtime pin is guaranteed to be within the ceiling (see the
    // `managed_python_pin_is_within_supported_range` test).
    match probe_python(&python, &[]) {
        Some(probe) => match check_python_supported(&probe, Some(MAX_PYTHON_MINOR)) {
            Ok(()) => true,
            Err(reason) => {
                log::warn!("Existing venv at {} is unusable: {}", venv_dir().display(), reason);
                false
            }
        },
        None => {
            log::warn!("Existing venv interpreter {} failed to run", python.display());
            false
        }
    }
}

/// Create a venv at ~/.laya/venv using the given Python interpreter.
/// Prefers `uv venv` (near-instant) with fallback to `python -m venv`.
///
/// Any existing venv is removed first, together with the deps hash.
pub fn create_venv(python: &PathBuf) -> Result<(), String> {
    let venv = venv_dir();
    log::info!("Creating venv at {} using {}", venv.display(), python.display());

    std::fs::create_dir_all(laya_home())
        .map_err(|e| format!("Failed to create ~/.laya: {e}"))?;

    // We only get here when the venv is missing or unusable (see
    // venv_is_usable). Start from scratch: `python -m venv` over an existing
    // venv from a different interpreter leaves a mixed environment, and uv's
    // overwrite behaviour varies by version. The deps hash lives OUTSIDE the
    // venv, so it must go too — otherwise a stale hash from a previous
    // successful install would make setup skip installing into the new,
    // empty venv.
    if venv.exists() {
        log::info!("Removing existing venv at {}", venv.display());
        std::fs::remove_dir_all(&venv).map_err(|e| {
            format!(
                "Failed to remove the old Python environment at {} ({e}). \
                 Close any running Laya engine processes and try again.",
                venv.display()
            )
        })?;
    }
    let _ = std::fs::remove_file(deps_hash_path());

    // Try uv venv first — it's near-instant (no pip/setuptools bootstrap).
    if let Some(uv) = find_uv() {
        log::info!("Using uv for venv creation: {}", uv.display());
        let mut cmd = Command::new(&uv);
        no_window(&mut cmd);
        cmd.args(["venv", &venv.to_string_lossy(), "--python", &python.to_string_lossy()]);
        sanitize_python_cmd(&mut cmd);

        let output = cmd.output();
        match output {
            Ok(out) if out.status.success() => return Ok(()),
            Ok(out) => {
                let stderr = String::from_utf8_lossy(&out.stderr);
                log::warn!("uv venv failed, falling back to python -m venv: {stderr}");
            }
            Err(e) => {
                log::warn!("uv venv failed to start, falling back: {e}");
            }
        }
    }

    // Fallback: python -m venv
    let mut cmd = Command::new(python);
    no_window(&mut cmd);
    cmd.args(["-m", "venv", &venv.to_string_lossy()]);
    sanitize_python_cmd(&mut cmd);

    let output = cmd.output()
        .map_err(|e| format!("Failed to run python -m venv: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("venv creation failed: {stderr}"));
    }

    Ok(())
}

fn find_uv() -> Option<PathBuf> {
    crate::runtime::managed_uv()
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

/// Run package install for a given requirements file.
/// Prefers `uv pip install` (10-100x faster) with fallback to regular pip.
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

    if let Some(uv) = find_uv() {
        return uv_pip_install(&uv, req, log_name, on_line);
    }
    legacy_pip_install(req, log_name, on_line)
}

/// Install packages using uv (Astral's fast pip replacement).
fn uv_pip_install<F: FnMut(&str)>(
    uv: &Path,
    req: &Path,
    log_name: &str,
    on_line: &mut F,
) -> Result<bool, String> {
    let python = venv_python();
    if !python.exists() {
        return Err("Python not found in venv — venv may be corrupt".to_string());
    }

    let log_dir = laya_home().join("logs");
    std::fs::create_dir_all(&log_dir).ok();
    let log_path = log_dir.join(log_name);

    log::info!("Installing from {} via uv (log: {})", req.display(), log_path.display());

    let mut cmd = Command::new(uv);
    no_window(&mut cmd);
    cmd.args([
            "pip", "install",
            "-r", &req.to_string_lossy(),
            "--python", &python.to_string_lossy(),
            "--only-binary", ":all:",
            // Keep the streamed output / log free of ANSI escape codes.
            "--color", "never",
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    sanitize_python_cmd(&mut cmd);

    run_install_cmd(&mut cmd, &log_path, on_line, "uv pip install")
}

/// Install packages using regular pip (fallback when uv is unavailable).
fn legacy_pip_install<F: FnMut(&str)>(
    req: &Path,
    log_name: &str,
    on_line: &mut F,
) -> Result<bool, String> {
    let python = venv_python();
    if !python.exists() {
        return Err("Python not found in venv — venv may be corrupt".to_string());
    }

    let log_dir = laya_home().join("logs");
    std::fs::create_dir_all(&log_dir).ok();
    let log_path = log_dir.join(log_name);

    log::info!("Installing from {} via pip (log: {})", req.display(), log_path.display());

    let mut cmd = Command::new(&python);
    no_window(&mut cmd);
    // No --log: run_install_cmd tees the streamed stdout/stderr to log_path,
    // so a pip --log here would be a second writer racing the same file.
    cmd.args([
            "-m", "pip",
            "install", "-r", &req.to_string_lossy(),
            "--only-binary", ":all:",
            "--progress-bar", "off",
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    sanitize_python_cmd(&mut cmd);

    run_install_cmd(&mut cmd, &log_path, on_line, "pip install")
}

/// Shared logic for running an install command and streaming output.
fn run_install_cmd<F: FnMut(&str)>(
    cmd: &mut Command,
    log_path: &Path,
    on_line: &mut F,
    label: &str,
) -> Result<bool, String> {
    let mut child = cmd.spawn()
        .map_err(|e| format!("Failed to start {label}: {e}"))?;

    // Tee streamed output to the log file. uv (unlike pip) has no --log flag,
    // so without this the log file would never be written; the legacy pip path
    // no longer passes --log either, so this is the single writer for both.
    let mut log = std::fs::File::create(log_path).ok();
    let mut output_lines: Vec<String> = Vec::new();

    if let Some(stdout) = child.stdout.take() {
        let stderr = child.stderr.take();
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            if let Ok(line) = line {
                on_line(&line);
                if let Some(f) = log.as_mut() { let _ = writeln!(f, "{line}"); }
                output_lines.push(line);
            }
        }
        if let Some(stderr) = stderr {
            let reader = BufReader::new(stderr);
            for line in reader.lines() {
                if let Ok(line) = line {
                    on_line(&line);
                    if let Some(f) = log.as_mut() { let _ = writeln!(f, "{line}"); }
                    output_lines.push(line);
                }
            }
        }
    }

    let status = child.wait().map_err(|e| format!("{label} wait failed: {e}"))?;
    if !status.success() {
        let tail: Vec<&str> = output_lines.iter().rev().take(5).map(|s| s.as_str()).collect();
        let detail = tail.into_iter().rev().collect::<Vec<_>>().join("\n");
        let msg = format!(
            "{label} failed (exit code {}).\nSee full log: {}\n\nLast output:\n{}",
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
    if ml_req.exists() {
        // uv runs quiet (no live progress bar in non-TTY), so the torch wheel
        // (~hundreds of MB) downloads with no output. Surface a hint so the
        // footer holds an explanatory message instead of looking frozen.
        on_line("Downloading large ML packages (torch, sentence-transformers) — this can take several minutes...");
    }
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
    no_window(&mut cmd);
    cmd.arg("-m")
        .arg("laya.main")
        .current_dir(&engine)
        .env("LAYA_PARENT_PID", std::process::id().to_string());

    #[cfg(unix)]
    unsafe {
        cmd.pre_exec(|| {
            // Make the engine its OWN process-group leader (pgid == its own pid).
            // In dev, `uvicorn --reload` spawns a multiprocessing WORKER
            // subprocess that actually holds laya.db and the :8420 socket — that
            // worker is our GRANDCHILD. Signalling only the direct child (the
            // reload supervisor) on quit leaves the worker orphaned, keeping the
            // DB open and the port bound. Owning a dedicated group lets the Exit
            // handler kill(-pgid) the entire engine subtree in one shot.
            // (The old setpgid(0, getppid()) tried to JOIN laya-app's group, but
            // laya-app is usually not a group leader — the dev shell owns the
            // group — so it silently failed and left us no reliable group handle.)
            libc::setpgid(0, 0);
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

    let mut cmd = Command::new(&python);
    no_window(&mut cmd);
    cmd.arg("-m")
        .arg("laya.main")
        .current_dir(&engine)
        // Pipe stdout/stderr so pipe_to_rotating() can capture them into a
        // size-capped, rotating engine-stdout.log (see below).
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // Prevent Python from writing __pycache__/*.pyc into the signed .app bundle,
        // which would invalidate the code signature.
        .env("PYTHONDONTWRITEBYTECODE", "1")
        // LAYA_PARENT_PID lets the engine's parent-watchdog monitor the actual
        // laya-app process, not the intermediate Python launcher (uvicorn spawns
        // a worker subprocess on Windows, so os.getppid() points at the wrong PID).
        .env("LAYA_PARENT_PID", std::process::id().to_string());
    // AppImage env vars break even the venv Python (see sanitize_python_cmd).
    sanitize_python_cmd(&mut cmd);

    #[cfg(unix)]
    unsafe {
        cmd.pre_exec(|| {
            // Own process group (pgid == our pid) so the Exit handler can
            // kill(-pgid) the whole engine subtree. Prod runs uvicorn without
            // --reload (single process), but this keeps dev/prod symmetric and
            // still cleanly reaps any helper subprocesses the engine spawns.
            // See spawn_dev_engine() for the full rationale.
            libc::setpgid(0, 0);
            Ok(())
        });
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn engine: {e}"))?;

    // Capture the engine's stdout/stderr into a size-capped, rotating log so
    // engine-stdout.log can't grow unbounded within a long session (the old
    // File::create sink was only truncated at launch). 10 MB × 3 backups.
    if let Err(e) = crate::rotating_log::pipe_to_rotating(
        &mut child,
        &log_dir,
        "engine-stdout.log",
        10 * 1024 * 1024,
        3,
    ) {
        log::warn!("Failed to set up rotating engine log capture: {e}");
    }

    Ok(child)
}

/// Outcome of waiting for a freshly spawned engine to answer `/health`.
pub enum EngineWait {
    Ready,
    /// The engine process exited before `/health` ever answered. `status` is
    /// the exit status description (`exit status: 1`, `signal: 9`) and
    /// `last_line` is the final non-empty line it wrote to stdout/stderr —
    /// for a Python crash that is the `SomeError: ...` line of the traceback.
    Exited { status: String, last_line: Option<String> },
    TimedOut,
}

impl EngineWait {
    /// One-line, user-facing description suitable for the setup screen. The
    /// last log line is included verbatim because the setup screen is often
    /// the only thing a user looks at before filing a bug report.
    pub fn describe(&self) -> String {
        match self {
            EngineWait::Ready => "Engine is running".to_string(),
            EngineWait::TimedOut => "Engine started but is not responding".to_string(),
            EngineWait::Exited { status, last_line } => {
                // Dev mode inherits the terminal instead of piping into the
                // log file, so the traceback is only in the terminal.
                let where_to_look = if cfg!(dev) {
                    "the terminal running the app".to_string()
                } else {
                    engine_stdout_log_path().display().to_string()
                };
                match last_line {
                    Some(line) => format!(
                        "Engine exited during startup ({status}): {line} — full traceback in {where_to_look}"
                    ),
                    None => format!(
                        "Engine exited during startup ({status}) — see {where_to_look}"
                    ),
                }
            }
        }
    }
}

/// Where `spawn_prod_engine` captures the engine's stdout/stderr.
fn engine_stdout_log_path() -> PathBuf {
    laya_home().join("logs").join("engine-stdout.log")
}

/// The most informative line of the captured engine log, trimmed to a length
/// that still fits on the setup screen.
///
/// Returns `None` in dev mode: `spawn_dev_engine` inherits the terminal
/// instead of piping into the log, so any file present was written by an
/// earlier packaged run and would describe the wrong process.
fn engine_log_last_line() -> Option<String> {
    if cfg!(dev) {
        return None;
    }
    let content = std::fs::read_to_string(engine_stdout_log_path()).ok()?;
    let line = pick_error_line(&content)?;
    const MAX: usize = 300;
    if line.chars().count() > MAX {
        Some(format!("{}…", line.chars().take(MAX).collect::<String>()))
    } else {
        Some(line.to_string())
    }
}

/// Choose the line that best explains a crash. A Python traceback ends with
/// `SomeError: message`, but interpreter warnings emitted at shutdown append
/// their source context (`  warnings.warn(`) after it, so the literal last
/// line is often noise. Prefer the last line that reads as an exception or an
/// `ERROR`/`CRITICAL` log line, and fall back to the last non-empty line.
fn pick_error_line(content: &str) -> Option<&str> {
    let lines: Vec<&str> = content.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
    lines
        .iter()
        .rev()
        .find(|l| looks_like_error_line(l))
        .or_else(|| lines.last())
        .copied()
}

fn looks_like_error_line(line: &str) -> bool {
    if line.starts_with("ERROR") || line.starts_with("CRITICAL") || line.starts_with("FATAL") {
        return true;
    }
    // `pkg.mod.NameError: detail` or a bare `KeyboardInterrupt`.
    let head = line.split(':').next().unwrap_or("");
    let is_dotted_ident = !head.is_empty()
        && head.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '.');
    is_dotted_ident
        && ["Error", "Exception", "Exit", "Interrupt"]
            .iter()
            .any(|suffix| head.ends_with(suffix))
}

/// Poll the engine's /health endpoint until it responds, the engine process
/// exits, or the timeout elapses.
///
/// `engine` is the shared child handle (the `EngineProcess` state). Checking it
/// each iteration distinguishes "crashed at import" from "still booting": an
/// engine that dies on startup (e.g. an ImportError from a bad dependency
/// resolution) would otherwise sit through the full timeout and surface as a
/// generic "not responding", with the real traceback buried in the log file.
pub fn wait_for_engine(
    timeout: Duration,
    engine: Option<&std::sync::Mutex<Option<Child>>>,
) -> EngineWait {
    let start = std::time::Instant::now();
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();

    while start.elapsed() < timeout {
        if let Some(exit) = engine.and_then(child_exit_status) {
            log::error!("Engine process exited before becoming healthy: {exit}");
            // The log-capture threads copy the pipes until the child closes
            // them; give them a moment to land the traceback's final lines.
            std::thread::sleep(Duration::from_millis(300));
            return EngineWait::Exited {
                status: exit,
                last_line: engine_log_last_line(),
            };
        }

        match client.get(format!("{}/health", engine_url())).send() {
            Ok(resp) if resp.status().is_success() => {
                log::info!("Engine is ready");
                return EngineWait::Ready;
            }
            _ => {
                std::thread::sleep(Duration::from_millis(500));
            }
        }
    }
    log::error!("Engine failed to start within {:?}", timeout);
    EngineWait::TimedOut
}

/// Non-blocking check that the shared engine child exists and has not exited.
pub fn child_is_running(engine: &std::sync::Mutex<Option<Child>>) -> bool {
    let Ok(mut guard) = engine.try_lock() else { return false };
    match guard.as_mut() {
        Some(child) => matches!(child.try_wait(), Ok(None)),
        None => false,
    }
}

/// Non-blocking check of the shared engine child. Returns the exit status
/// description if the process has already terminated, `None` if it is still
/// running, was never stored, or the lock is unavailable.
fn child_exit_status(engine: &std::sync::Mutex<Option<Child>>) -> Option<String> {
    let mut guard = engine.try_lock().ok()?;
    let child = guard.as_mut()?;
    match child.try_wait() {
        Ok(Some(status)) => Some(status.to_string()),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    fn probe(minor: u32, version: &str, platform: &str) -> PythonProbe {
        PythonProbe {
            minor,
            version: version.into(),
            platform: platform.into(),
            executable: PathBuf::from("python"),
        }
    }

    #[test]
    fn parse_probe_output_reads_all_fields_including_spaced_path() {
        let out = "3\r\n12\r\n3.12.13\r\nwin-amd64\r\nC:\\Program Files\\Python312\\python.exe\r\n";
        let p = parse_probe_output(out).expect("parses");
        assert_eq!(p.minor, 12);
        assert_eq!(p.version, "3.12.13");
        assert_eq!(p.platform, "win-amd64");
        assert_eq!(p.executable, PathBuf::from("C:\\Program Files\\Python312\\python.exe"));
    }

    #[test]
    fn parse_probe_output_rejects_non_python3_and_garbage() {
        assert_eq!(parse_probe_output("2\n7\n2.7.18\nlinux-x86_64\n/usr/bin/python\n"), None);
        assert_eq!(parse_probe_output("Python was not found; run without arguments...\n"), None);
        assert_eq!(parse_probe_output("3\n12\n3.12.1\nwin-amd64\n"), None); // truncated
    }

    /// Issue #14: ARM64 Python 3.15 on Windows-on-ARM was accepted and every
    /// wheels-only install then failed.
    #[test]
    fn rejects_issue_14_interpreter() {
        let err = check_python_supported(&probe(15, "3.15.0rc2", "win-arm64"), Some(MAX_PYTHON_MINOR))
            .unwrap_err();
        assert!(err.contains("newer than the latest tested"), "{err}");
    }

    #[test]
    fn rejects_too_new_system_python_even_on_supported_platform() {
        assert!(check_python_supported(&probe(15, "3.15.0", "win-amd64"), Some(MAX_PYTHON_MINOR)).is_err());
        assert!(check_python_supported(&probe(15, "3.15.0", "macosx-11.0-arm64"), Some(MAX_PYTHON_MINOR)).is_err());
    }

    /// No regression for existing installs: 3.14 (e.g. Homebrew on Apple
    /// Silicon) was accepted by the old second pass and must still be —
    /// both for detection and for keeping an existing venv.
    #[test]
    fn python_3_14_is_second_pass_only() {
        let p = probe(14, "3.14.2", "macosx-14.0-arm64");
        assert!(check_python_supported(&p, Some(PREFERRED_MAX_PYTHON_MINOR)).is_err());
        assert_eq!(check_python_supported(&p, Some(MAX_PYTHON_MINOR)), Ok(()));
    }

    #[test]
    fn rejects_platforms_without_wheels_even_at_supported_version() {
        for plat in ["win-arm64", "win32"] {
            let err = check_python_supported(&probe(12, "3.12.13", plat), Some(MAX_PYTHON_MINOR)).unwrap_err();
            assert!(err.contains(plat), "{err}");
        }
    }

    #[test]
    fn accepts_supported_interpreters() {
        for (minor, plat) in [
            (10, "win-amd64"),
            (13, "win-amd64"),
            (12, "macosx-11.0-arm64"),
            (12, "macosx-10.13-universal2"),
            (11, "linux-aarch64"),
            (12, "linux-x86_64"),
        ] {
            let p = probe(minor, &format!("3.{minor}.0"), plat);
            assert_eq!(check_python_supported(&p, Some(MAX_PYTHON_MINOR)), Ok(()), "{plat} 3.{minor}");
        }
    }

    #[test]
    fn rejects_too_old_python() {
        assert!(check_python_supported(&probe(9, "3.9.18", "win-amd64"), None).is_err());
    }

    /// venv_is_usable() applies MAX_PYTHON_MINOR, so a venv built from the
    /// managed runtime would be rebuilt on every launch if the pin exceeded it.
    #[test]
    fn managed_python_pin_is_within_supported_range() {
        let minor: u32 = crate::runtime::PYTHON_VERSION.split('.').nth(1).unwrap().parse().unwrap();
        assert!(
            (MIN_PYTHON_MINOR..=MAX_PYTHON_MINOR).contains(&minor),
            "runtime::PYTHON_VERSION {} is outside 3.{}–3.{}",
            crate::runtime::PYTHON_VERSION, MIN_PYTHON_MINOR, MAX_PYTHON_MINOR
        );
    }

    /// The managed runtime has no version ceiling in find_python(), but the
    /// platform check still applies.
    #[test]
    fn no_ceiling_mode_still_checks_platform() {
        assert_eq!(check_python_supported(&probe(15, "3.15.0", "win-amd64"), None), Ok(()));
        assert!(check_python_supported(&probe(12, "3.12.13", "win-arm64"), None).is_err());
    }

    /// A child that dies before ever answering `/health` must be reported as
    /// `Exited` with its exit status, not as a timeout.
    #[cfg(unix)]
    #[test]
    fn wait_for_engine_reports_child_exit() {
        let child = Command::new("sh")
            .args(["-c", "echo 'ImportError: boom' >&2; exit 3"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn sh");
        let engine = Mutex::new(Some(child));
        // Let the child finish so the exit check fires on the first poll,
        // regardless of whether a real engine happens to be listening.
        std::thread::sleep(Duration::from_millis(200));

        let outcome = wait_for_engine(Duration::from_secs(5), Some(&engine));
        match outcome {
            EngineWait::Exited { status, .. } => assert!(status.contains('3'), "status: {status}"),
            EngineWait::Ready => panic!("dead child reported as ready"),
            EngineWait::TimedOut => panic!("dead child reported as timeout"),
        }
    }

    #[test]
    fn pick_error_line_prefers_exception_over_trailing_warning_context() {
        let log = "Traceback (most recent call last):\n  File \"x.py\", line 1\n\
ImportError: cannot import name 'McpError' from 'mcp.shared.exceptions'\n\
/usr/lib/python3.13/multiprocessing/resource_tracker.py:324: UserWarning: leaked semaphore\n\
  warnings.warn(\n";
        assert_eq!(
            pick_error_line(log),
            Some("ImportError: cannot import name 'McpError' from 'mcp.shared.exceptions'")
        );
    }

    #[test]
    fn pick_error_line_recognises_uvicorn_bind_error() {
        let log = "INFO:     Will watch for changes\n\
ERROR:    [Errno 48] error while attempting to bind on address ('127.0.0.1', 8420): address already in use\n\
INFO:     Waiting for child process\n";
        assert!(pick_error_line(log).unwrap().starts_with("ERROR:"));
    }

    #[test]
    fn pick_error_line_falls_back_to_last_nonempty_line() {
        assert_eq!(pick_error_line("hello\nworld\n\n"), Some("world"));
        assert_eq!(pick_error_line("\n \n"), None);
    }

    /// A child that is still running is never reported as `Exited`; with no
    /// engine listening the wait simply times out.
    #[cfg(unix)]
    #[test]
    fn wait_for_engine_does_not_mistake_running_child_for_exit() {
        let child = Command::new("sleep")
            .arg("30")
            .spawn()
            .expect("spawn sleep");
        let engine = Mutex::new(Some(child));

        let outcome = wait_for_engine(Duration::from_millis(1200), Some(&engine));
        assert!(!matches!(outcome, EngineWait::Exited { .. }));

        if let Some(mut child) = engine.lock().ok().and_then(|mut g| g.take()) {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}
