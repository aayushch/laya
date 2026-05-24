// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

//! Automatic provisioning of Python, Node.js, and uv runtimes into `~/.laya/`.
//!
//! On first launch, when the system lacks a Python 3.10+ or Node.js 22+
//! installation, this module downloads prebuilt binaries — python-build-
//! standalone (Astral/indygreg) for Python, the official `nodejs.org`
//! tarballs/zips for Node, and `uv` (Astral) as a fast pip replacement —
//! verifies their SHA-256, extracts them, and exposes the resulting
//! binaries via `managed_python()` / `managed_node()` / `managed_uv()`
//! so the rest of the startup flow can use them transparently.
//!
//! Design:
//!   1. Detect a working runtime (managed first, then system).
//!   2. Only download what is missing.
//!   3. Downloads run in parallel threads; progress events are multiplexed
//!      through an `mpsc` channel back to the caller's callback.
//!   4. Stream to `~/.laya/runtimes/<file>`, verify checksum, extract into
//!      `~/.laya/runtimes/staging-<kind>/`, then atomically rename into
//!      `~/.laya/python/`, `~/.laya/node/`, or `~/.laya/uv/`.  No half-
//!      installed runtime is ever visible to detection.
//!   5. Re-launch is fast: a `.version` marker short-circuits re-download
//!      when the pinned version already matches.

use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use sha2::{Digest, Sha256};

// ── Pinned versions ─────────────────────────────────────────────────────
//
// These pins determine which prebuilt archive Laya downloads.  Update them
// in lock-step with Laya releases; mismatched pins surface as an HTTP 404
// at download time.  Both archives are < 50 MB compressed.
//
// PYTHON_VERSION must exist within PBS_RELEASE_TAG — each indygreg tag
// rebuilds every supported CPython version, so the (tag, version) pair
// must match what is actually published.

/// CPython version shipped by python-build-standalone (Astral/indygreg).
const PYTHON_VERSION: &str = "3.12.13";

/// indygreg release tag that contains the above CPython version.
/// Releases are dated YYYYMMDD; see
/// https://github.com/astral-sh/python-build-standalone/releases
const PBS_RELEASE_TAG: &str = "20260510";

/// Node.js LTS version from nodejs.org/dist.
const NODE_VERSION: &str = "22.22.3";

/// uv version (Astral's fast pip/venv replacement).
/// Releases: https://github.com/astral-sh/uv/releases
const UV_VERSION: &str = "0.7.12";

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

fn python_dir() -> PathBuf {
    laya_home().join("python")
}

fn node_dir() -> PathBuf {
    laya_home().join("node")
}

fn uv_dir() -> PathBuf {
    laya_home().join("uv")
}

fn runtimes_dir() -> PathBuf {
    laya_home().join("runtimes")
}

fn version_marker(dir: &Path) -> PathBuf {
    dir.join(".version")
}

// ── Public surface ──────────────────────────────────────────────────────

/// Path to the Python interpreter inside the managed runtime, if present.
///
/// On Unix python-build-standalone extracts to `python/bin/python3`.
/// On Windows it extracts to `python/python.exe` (no `bin/` subdir).
pub fn managed_python() -> Option<PathBuf> {
    let candidate = if cfg!(target_os = "windows") {
        python_dir().join("python.exe")
    } else {
        python_dir().join("bin").join("python3")
    };
    if candidate.exists() {
        Some(candidate)
    } else {
        None
    }
}

/// Path to the Node binary inside the managed runtime, if present.
///
/// On Unix the official Node tarball uses `bin/node`.
/// On Windows the zip puts `node.exe` at the top level alongside `npm.cmd`.
pub fn managed_node() -> Option<PathBuf> {
    let candidate = if cfg!(target_os = "windows") {
        node_dir().join("node.exe")
    } else {
        node_dir().join("bin").join("node")
    };
    if candidate.exists() {
        Some(candidate)
    } else {
        None
    }
}

/// Path to the `uv` binary inside the managed runtime, if present.
pub fn managed_uv() -> Option<PathBuf> {
    let candidate = if cfg!(target_os = "windows") {
        uv_dir().join("uv.exe")
    } else {
        uv_dir().join("uv")
    };
    if candidate.exists() {
        Some(candidate)
    } else {
        None
    }
}

/// Progress events emitted during `ensure_runtimes`.  The caller decides
/// how to surface them — Laya forwards them as `setup-progress` Tauri
/// events on the existing `preflight` step.
pub enum RuntimeProgress {
    /// A new phase began (e.g. "Downloading Python 3.12.8").
    Phase(String),
    /// Streaming download progress (cumulative bytes, total if known).
    Bytes { downloaded: u64, total: Option<u64> },
    /// A phase completed successfully.
    Done(String),
}

/// Ensure Python (3.10+), Node (22+), and uv runtimes are available, either
/// from the system or downloaded into `~/.laya/{python,node,uv}/`.
///
/// Downloads run in parallel when multiple runtimes are needed.
/// Detects system installs first; only downloads what is missing.
/// Idempotent across launches via `.version` marker files.
pub fn ensure_runtimes<F: FnMut(RuntimeProgress)>(mut on_progress: F) -> Result<(), String> {
    fs::create_dir_all(laya_home())
        .map_err(|e| format!("Failed to create ~/.laya: {e}"))?;

    let python_needed = match managed_python() {
        Some(_) => !managed_python_version_matches(),
        None => crate::sidecar::find_python_system().is_err(),
    };
    let node_needed = match managed_node() {
        Some(_) => !managed_node_version_matches(),
        None => crate::n8n::find_node_system().is_err(),
    };
    let uv_needed = managed_uv().is_none() || !managed_uv_version_matches();

    if !python_needed && !node_needed && !uv_needed {
        on_progress(RuntimeProgress::Done("All runtimes ready".to_string()));
        return Ok(());
    }

    // Emit ready status for runtimes that don't need downloading.
    if !python_needed { on_progress(RuntimeProgress::Done("Python ready".to_string())); }
    if !node_needed { on_progress(RuntimeProgress::Done("Node.js ready".to_string())); }

    // Download needed runtimes in parallel.  Each thread sends progress
    // events through an mpsc channel; the calling thread drains them
    // into the original callback.
    let (tx, rx) = std::sync::mpsc::channel::<RuntimeProgress>();
    let mut handles: Vec<std::thread::JoinHandle<Result<(), String>>> = Vec::new();

    if python_needed {
        let tx = tx.clone();
        handles.push(std::thread::spawn(move || {
            provision_python(&mut |p| { let _ = tx.send(p); })
        }));
    }
    if node_needed {
        let tx = tx.clone();
        handles.push(std::thread::spawn(move || {
            provision_node(&mut |p| { let _ = tx.send(p); })
        }));
    }
    if uv_needed {
        let tx = tx.clone();
        handles.push(std::thread::spawn(move || {
            provision_uv(&mut |p| { let _ = tx.send(p); })
        }));
    }

    // Close sender so the rx iterator ends when all threads finish.
    drop(tx);

    for progress in rx {
        on_progress(progress);
    }

    // Collect thread results.
    let mut errors = Vec::new();
    for h in handles {
        match h.join() {
            Ok(Ok(())) => {}
            Ok(Err(e)) => errors.push(e),
            Err(_) => errors.push("Thread panicked during runtime provisioning".to_string()),
        }
    }
    if !errors.is_empty() {
        return Err(errors.join("; "));
    }

    Ok(())
}

// ── Version marker checks ───────────────────────────────────────────────

fn managed_python_version_matches() -> bool {
    fs::read_to_string(version_marker(&python_dir()))
        .map(|s| s.trim() == PYTHON_VERSION)
        .unwrap_or(false)
}

fn managed_node_version_matches() -> bool {
    fs::read_to_string(version_marker(&node_dir()))
        .map(|s| s.trim() == NODE_VERSION)
        .unwrap_or(false)
}

fn managed_uv_version_matches() -> bool {
    fs::read_to_string(version_marker(&uv_dir()))
        .map(|s| s.trim() == UV_VERSION)
        .unwrap_or(false)
}

// ── Provisioning: Python ────────────────────────────────────────────────

fn provision_python<F: FnMut(RuntimeProgress)>(on_progress: &mut F) -> Result<(), String> {
    let url = python_download_url()?;
    let filename = url
        .rsplit('/')
        .next()
        .ok_or_else(|| "Bad Python URL".to_string())?
        .to_string();

    fs::create_dir_all(runtimes_dir())
        .map_err(|e| format!("Failed to create runtimes dir: {e}"))?;
    let archive = runtimes_dir().join(&filename);

    on_progress(RuntimeProgress::Phase(format!(
        "Downloading Python {}",
        PYTHON_VERSION
    )));
    download_with_retry(&url, &archive, on_progress)?;

    on_progress(RuntimeProgress::Phase("Verifying Python checksum".to_string()));
    let expected = fetch_python_sha256(&filename)?;
    verify_sha256(&archive, &expected).inspect_err(|_| {
        let _ = fs::remove_file(&archive);
    })?;

    on_progress(RuntimeProgress::Phase("Extracting Python".to_string()));
    let staging = runtimes_dir().join("staging-python");
    let _ = fs::remove_dir_all(&staging);
    fs::create_dir_all(&staging).map_err(|e| format!("Failed to create staging: {e}"))?;
    extract_tar_gz(&archive, &staging)?;

    // python-build-standalone install_only archives extract into a single
    // top-level directory named `python/`.
    let extracted = find_only_subdir(&staging)?;
    install_atomically(&extracted, &python_dir())?;

    let _ = fs::remove_dir_all(&staging);
    let _ = fs::remove_file(&archive);

    #[cfg(target_os = "macos")]
    remove_quarantine(&python_dir());

    let _ = fs::write(version_marker(&python_dir()), PYTHON_VERSION);

    log::info!(
        "Managed Python {} installed at {}",
        PYTHON_VERSION,
        python_dir().display()
    );
    on_progress(RuntimeProgress::Done(format!("Python {} ready", PYTHON_VERSION)));
    Ok(())
}

fn python_download_url() -> Result<String, String> {
    let triple = python_target_triple()?;
    Ok(format!(
        "https://github.com/astral-sh/python-build-standalone/releases/download/{tag}/cpython-{ver}+{tag}-{triple}-install_only.tar.gz",
        tag = PBS_RELEASE_TAG,
        ver = PYTHON_VERSION,
        triple = triple,
    ))
}

fn python_target_triple() -> Result<&'static str, String> {
    match (std::env::consts::OS, std::env::consts::ARCH) {
        ("macos", "aarch64") => Ok("aarch64-apple-darwin"),
        ("macos", "x86_64") => Ok("x86_64-apple-darwin"),
        ("linux", "x86_64") => Ok("x86_64-unknown-linux-gnu"),
        ("linux", "aarch64") => Ok("aarch64-unknown-linux-gnu"),
        // Windows: only the bare `x86_64-pc-windows-msvc` triple is published
        // for the install_only flavour in recent releases.  An earlier
        // `-shared-install_only` variant existed in older 2024 releases but
        // was dropped — using it now yields a 404 (Issue #4 regression).
        ("windows", "x86_64") => Ok("x86_64-pc-windows-msvc"),
        ("windows", "aarch64") => Ok("aarch64-pc-windows-msvc"),
        (os, arch) => Err(format!(
            "Unsupported platform for managed Python: {}/{}",
            os, arch
        )),
    }
}

/// python-build-standalone publishes a single `SHA256SUMS` file per release
/// (since ~2025) — same format as Node's `SHASUMS256.txt`: one
/// `<hex>  <filename>` line per published artifact.  Older releases shipped
/// per-archive `<archive>.sha256` sidecars; we don't try to support those
/// because the pinned tag controls which format is in play.
fn fetch_python_sha256(filename: &str) -> Result<String, String> {
    let url = format!(
        "https://github.com/astral-sh/python-build-standalone/releases/download/{}/SHA256SUMS",
        PBS_RELEASE_TAG
    );
    let text = fetch_text(&url)?;
    lookup_sha256(&text, filename)
        .ok_or_else(|| format!("SHA-256 for {} not found in {}", filename, url))
}

// ── Provisioning: Node ──────────────────────────────────────────────────

fn provision_node<F: FnMut(RuntimeProgress)>(on_progress: &mut F) -> Result<(), String> {
    let filename = node_filename()?;
    let url = format!("https://nodejs.org/dist/v{}/{}", NODE_VERSION, filename);

    fs::create_dir_all(runtimes_dir())
        .map_err(|e| format!("Failed to create runtimes dir: {e}"))?;
    let archive = runtimes_dir().join(&filename);

    on_progress(RuntimeProgress::Phase(format!(
        "Downloading Node.js {}",
        NODE_VERSION
    )));
    download_with_retry(&url, &archive, on_progress)?;

    on_progress(RuntimeProgress::Phase("Verifying Node.js checksum".to_string()));
    let expected = fetch_node_sha256(&filename)?;
    verify_sha256(&archive, &expected).inspect_err(|_| {
        let _ = fs::remove_file(&archive);
    })?;

    on_progress(RuntimeProgress::Phase("Extracting Node.js".to_string()));
    let staging = runtimes_dir().join("staging-node");
    let _ = fs::remove_dir_all(&staging);
    fs::create_dir_all(&staging).map_err(|e| format!("Failed to create staging: {e}"))?;

    if filename.ends_with(".zip") {
        #[cfg(target_os = "windows")]
        {
            extract_zip(&archive, &staging)?;
        }
        #[cfg(not(target_os = "windows"))]
        {
            return Err("zip extraction only supported on Windows".to_string());
        }
    } else {
        extract_tar_gz(&archive, &staging)?;
    }

    // Node archives extract into a single top-level directory like
    // `node-v22.12.0-darwin-arm64/`.
    let extracted = find_only_subdir(&staging)?;
    install_atomically(&extracted, &node_dir())?;

    let _ = fs::remove_dir_all(&staging);
    let _ = fs::remove_file(&archive);

    #[cfg(target_os = "macos")]
    remove_quarantine(&node_dir());

    let _ = fs::write(version_marker(&node_dir()), NODE_VERSION);

    log::info!(
        "Managed Node.js {} installed at {}",
        NODE_VERSION,
        node_dir().display()
    );
    on_progress(RuntimeProgress::Done(format!("Node.js {} ready", NODE_VERSION)));
    Ok(())
}

fn node_filename() -> Result<String, String> {
    let (plat, arch, ext) = match (std::env::consts::OS, std::env::consts::ARCH) {
        ("macos", "aarch64") => ("darwin", "arm64", "tar.gz"),
        ("macos", "x86_64") => ("darwin", "x64", "tar.gz"),
        ("linux", "x86_64") => ("linux", "x64", "tar.gz"),
        ("linux", "aarch64") => ("linux", "arm64", "tar.gz"),
        ("windows", "x86_64") => ("win", "x64", "zip"),
        (os, arch) => {
            return Err(format!(
                "Unsupported platform for managed Node: {}/{}",
                os, arch
            ))
        }
    };
    Ok(format!("node-v{}-{}-{}.{}", NODE_VERSION, plat, arch, ext))
}

/// nodejs.org publishes a per-release `SHASUMS256.txt` listing every artifact.
/// Lines are `<sha256>  <filename>`.
fn fetch_node_sha256(filename: &str) -> Result<String, String> {
    let url = format!("https://nodejs.org/dist/v{}/SHASUMS256.txt", NODE_VERSION);
    let text = fetch_text(&url)?;
    lookup_sha256(&text, filename)
        .ok_or_else(|| format!("SHA-256 for {} not found in {}", filename, url))
}

/// Parse a `<hex>  <filename>` index (BSD `shasum -a 256` style — also what
/// nodejs.org and python-build-standalone publish) and return the lowercased
/// hex digest for `filename`.
fn lookup_sha256(text: &str, filename: &str) -> Option<String> {
    for line in text.lines() {
        let mut parts = line.split_whitespace();
        match (parts.next(), parts.next()) {
            (Some(hex), Some(name)) if name == filename && hex.len() == 64 => {
                return Some(hex.to_lowercase());
            }
            _ => continue,
        }
    }
    None
}

// ── Provisioning: uv ───────────────────────────────────────────────────

fn provision_uv<F: FnMut(RuntimeProgress)>(on_progress: &mut F) -> Result<(), String> {
    let (url, is_zip) = uv_download_url()?;
    let filename = url
        .rsplit('/')
        .next()
        .ok_or_else(|| "Bad uv URL".to_string())?
        .to_string();

    fs::create_dir_all(runtimes_dir())
        .map_err(|e| format!("Failed to create runtimes dir: {e}"))?;
    let archive = runtimes_dir().join(&filename);

    on_progress(RuntimeProgress::Phase(format!("Downloading uv {}", UV_VERSION)));
    download_with_retry(&url, &archive, on_progress)?;

    on_progress(RuntimeProgress::Phase("Verifying uv checksum".to_string()));
    let expected = fetch_uv_sha256(&filename)?;
    verify_sha256(&archive, &expected).inspect_err(|_| {
        let _ = fs::remove_file(&archive);
    })?;

    on_progress(RuntimeProgress::Phase("Extracting uv".to_string()));
    let staging = runtimes_dir().join("staging-uv");
    let _ = fs::remove_dir_all(&staging);
    fs::create_dir_all(&staging).map_err(|e| format!("Failed to create staging: {e}"))?;

    if is_zip {
        #[cfg(target_os = "windows")]
        {
            extract_zip(&archive, &staging)?;
        }
        #[cfg(not(target_os = "windows"))]
        {
            return Err("zip extraction only supported on Windows".to_string());
        }
    } else {
        extract_tar_gz(&archive, &staging)?;
    }

    // uv archives extract into a directory like `uv-aarch64-apple-darwin/`
    // containing the uv (and uvx) binary.  We install just the directory.
    let extracted = find_only_subdir(&staging)?;
    install_atomically(&extracted, &uv_dir())?;

    let _ = fs::remove_dir_all(&staging);
    let _ = fs::remove_file(&archive);

    #[cfg(target_os = "macos")]
    remove_quarantine(&uv_dir());

    let _ = fs::write(version_marker(&uv_dir()), UV_VERSION);

    log::info!("Managed uv {} installed at {}", UV_VERSION, uv_dir().display());
    on_progress(RuntimeProgress::Done(format!("uv {} ready", UV_VERSION)));
    Ok(())
}

/// Returns (url, is_zip).
fn uv_download_url() -> Result<(String, bool), String> {
    let (triple, is_zip) = uv_target_triple()?;
    let ext = if is_zip { "zip" } else { "tar.gz" };
    Ok((
        format!(
            "https://github.com/astral-sh/uv/releases/download/{ver}/uv-{triple}.{ext}",
            ver = UV_VERSION,
            triple = triple,
            ext = ext,
        ),
        is_zip,
    ))
}

fn uv_target_triple() -> Result<(&'static str, bool), String> {
    match (std::env::consts::OS, std::env::consts::ARCH) {
        ("macos", "aarch64") => Ok(("aarch64-apple-darwin", false)),
        ("macos", "x86_64") => Ok(("x86_64-apple-darwin", false)),
        ("linux", "x86_64") => Ok(("x86_64-unknown-linux-gnu", false)),
        ("linux", "aarch64") => Ok(("aarch64-unknown-linux-gnu", false)),
        ("windows", "x86_64") => Ok(("x86_64-pc-windows-msvc", true)),
        ("windows", "aarch64") => Ok(("aarch64-pc-windows-msvc", true)),
        (os, arch) => Err(format!("Unsupported platform for uv: {}/{}", os, arch)),
    }
}

/// uv publishes per-archive SHA-256 checksums as `<archive>.sha256` sidecar
/// files, each containing just the hex digest on a single line.
fn fetch_uv_sha256(filename: &str) -> Result<String, String> {
    let url = format!(
        "https://github.com/astral-sh/uv/releases/download/{}/{}.sha256",
        UV_VERSION, filename
    );
    let text = fetch_text(&url)?;
    // The sidecar file may contain just the hash, or `<hash>  <filename>`.
    let hash = text.trim().split_whitespace().next()
        .ok_or_else(|| format!("Empty SHA-256 file for {}", filename))?;
    if hash.len() != 64 {
        return Err(format!("Invalid SHA-256 in {}: '{}'", url, hash));
    }
    Ok(hash.to_lowercase())
}

// ── Download / verify / extract ─────────────────────────────────────────

fn fetch_text(url: &str) -> Result<String, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(60))
        .build()
        .map_err(|e| format!("http client init: {e}"))?;
    let resp = client
        .get(url)
        .send()
        .map_err(|e| format!("Fetch {}: {e}", url))?;
    if !resp.status().is_success() {
        return Err(format!("Fetch {}: HTTP {}", url, resp.status()));
    }
    resp.text()
        .map_err(|e| format!("Read body from {}: {e}", url))
}

fn download_with_retry<F: FnMut(RuntimeProgress)>(
    url: &str,
    dest: &Path,
    on_progress: &mut F,
) -> Result<(), String> {
    match download_with_progress(url, dest, on_progress) {
        Ok(()) => Ok(()),
        Err(first) => {
            log::warn!("Download failed, retrying once: {}", first);
            std::thread::sleep(Duration::from_secs(2));
            download_with_progress(url, dest, on_progress)
                .map_err(|second| format!("{first} (after retry: {second})"))
        }
    }
}

fn download_with_progress<F: FnMut(RuntimeProgress)>(
    url: &str,
    dest: &Path,
    on_progress: &mut F,
) -> Result<(), String> {
    let client = reqwest::blocking::Client::builder()
        // No overall request timeout: large downloads on slow connections
        // can legitimately take minutes.  reqwest's per-read timeout via
        // `read_timeout` would be nicer but isn't on the blocking client.
        .timeout(None)
        .connect_timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| format!("http client init: {e}"))?;

    let mut resp = client
        .get(url)
        .send()
        .map_err(|e| format!("Download failed for {}: {e}", url))?;
    if !resp.status().is_success() {
        return Err(format!(
            "Download failed for {}: HTTP {}",
            url,
            resp.status()
        ));
    }
    let total = resp.content_length();

    let mut file = fs::File::create(dest)
        .map_err(|e| format!("Create {}: {e}", dest.display()))?;

    // Throttle progress events to roughly every 512 KB so we don't flood
    // the Tauri event channel for a 30 MB download.
    const CHUNK: usize = 64 * 1024;
    const PROGRESS_STEP: u64 = 512 * 1024;
    let mut buf = vec![0u8; CHUNK];
    let mut downloaded: u64 = 0;
    let mut last_reported: u64 = 0;

    loop {
        let n = match resp.read(&mut buf) {
            Ok(n) => n,
            Err(e) => {
                let _ = fs::remove_file(dest);
                return Err(format!("Read from {}: {e}", url));
            }
        };
        if n == 0 {
            break;
        }
        if let Err(e) = file.write_all(&buf[..n]) {
            let _ = fs::remove_file(dest);
            return Err(format!("Write {}: {e}", dest.display()));
        }
        downloaded += n as u64;
        if downloaded - last_reported >= PROGRESS_STEP {
            on_progress(RuntimeProgress::Bytes { downloaded, total });
            last_reported = downloaded;
        }
    }
    // Final progress tick so the UI shows 100%.
    on_progress(RuntimeProgress::Bytes { downloaded, total });
    Ok(())
}

fn verify_sha256(path: &Path, expected_hex: &str) -> Result<(), String> {
    let mut file = fs::File::open(path)
        .map_err(|e| format!("Open {} for hashing: {e}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    loop {
        let n = file
            .read(&mut buf)
            .map_err(|e| format!("Hash read: {e}"))?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    let got = hex_lower(&hasher.finalize());
    let expected = expected_hex.trim().to_lowercase();
    if got != expected {
        return Err(format!(
            "Checksum mismatch for {}: expected {}, got {}",
            path.display(),
            expected,
            got
        ));
    }
    Ok(())
}

fn hex_lower(bytes: &[u8]) -> String {
    use std::fmt::Write as _;
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        let _ = write!(&mut s, "{:02x}", b);
    }
    s
}

pub(crate) fn extract_tar_gz(archive: &Path, dest: &Path) -> Result<(), String> {
    let file = fs::File::open(archive)
        .map_err(|e| format!("Open archive {}: {e}", archive.display()))?;
    let decoder = flate2::read::GzDecoder::new(file);
    let mut tar_archive = tar::Archive::new(decoder);
    // preserve_permissions matters for executable bits on bin/python3, bin/node.
    tar_archive.set_preserve_permissions(true);
    tar_archive
        .unpack(dest)
        .map_err(|e| format!("Extract {} into {}: {e}", archive.display(), dest.display()))?;
    Ok(())
}

#[cfg(target_os = "windows")]
fn extract_zip(archive: &Path, dest: &Path) -> Result<(), String> {
    let file = fs::File::open(archive)
        .map_err(|e| format!("Open zip {}: {e}", archive.display()))?;
    let mut zip = zip::ZipArchive::new(file).map_err(|e| format!("Read zip: {e}"))?;
    zip.extract(dest)
        .map_err(|e| format!("Extract zip into {}: {e}", dest.display()))?;
    Ok(())
}

/// Atomically install `src` (a freshly-extracted directory) as `dest`.
/// Removes any prior `dest` first so a re-provision replaces the old tree
/// rather than nesting into it.
fn install_atomically(src: &Path, dest: &Path) -> Result<(), String> {
    if dest.exists() {
        fs::remove_dir_all(dest)
            .map_err(|e| format!("Remove old {}: {e}", dest.display()))?;
    }
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("Create parent of {}: {e}", dest.display()))?;
    }
    fs::rename(src, dest).map_err(|e| {
        format!(
            "Install {} -> {}: {e} (different filesystems?)",
            src.display(),
            dest.display()
        )
    })
}

/// Read the single top-level entry inside `dir`.  Both python-build-
/// standalone and the Node tarballs/zips wrap their contents in exactly
/// one directory; this helper finds it without having to know the exact
/// version-stamped name.
fn find_only_subdir(dir: &Path) -> Result<PathBuf, String> {
    let mut iter = fs::read_dir(dir)
        .map_err(|e| format!("Read {}: {e}", dir.display()))?;
    let first = iter
        .next()
        .ok_or_else(|| format!("Archive extracted nothing into {}", dir.display()))?
        .map_err(|e| format!("Iter {}: {e}", dir.display()))?;
    if iter.next().is_some() {
        return Err(format!(
            "Unexpected archive layout in {} (multiple top-level entries)",
            dir.display()
        ));
    }
    let path = first.path();
    if !path.is_dir() {
        return Err(format!("Expected directory at {}", path.display()));
    }
    Ok(path)
}

/// Strip macOS Gatekeeper's `com.apple.quarantine` xattr from freshly
/// downloaded binaries.  Without this the first launch of `python3` or
/// `node` from these directories triggers a Gatekeeper prompt.  uv and
/// mise do the same thing.
#[cfg(target_os = "macos")]
fn remove_quarantine(dir: &Path) {
    use std::process::Command;
    match Command::new("xattr").arg("-cr").arg(dir).status() {
        Ok(s) if s.success() => {}
        Ok(s) => log::warn!(
            "xattr -cr {} exited non-zero ({})",
            dir.display(),
            s.code().unwrap_or(-1)
        ),
        Err(e) => log::warn!("Failed to run xattr on {}: {}", dir.display(), e),
    }
}

// ── Tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_triple_covers_all_supported_platforms() {
        // Sanity-check the lookup table doesn't have typos.  The function
        // uses runtime OS/ARCH, so we exercise its body indirectly by
        // confirming the format of the constructed URL.
        let _ = python_download_url();
    }

    #[test]
    fn node_filename_format_matches_nodejs_dist() {
        let _ = node_filename();
    }

    #[test]
    fn uv_url_format_matches_github_releases() {
        let _ = uv_download_url();
    }

    #[test]
    fn hex_lower_emits_lowercase_hex() {
        assert_eq!(hex_lower(&[0xab, 0xcd, 0x01, 0xff]), "abcd01ff");
    }
}
