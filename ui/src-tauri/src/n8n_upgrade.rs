// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

//! Moves an existing n8n install to the n8n version and Node version this
//! Laya build pins (`n8n::N8N_VERSION`, `runtime::NODE_VERSION`), with a
//! database backup and a rollback to the previous install if anything fails.
//! The design is in `engine/docs/plan-n8n-upgrade.md`, section 6.
//!
//! # The runtime record
//!
//! The state lives under the key `n8n_runtime` in
//! `~/.laya/data/.permissions_migrated`. The engine's filesystem permission
//! migration (`engine/laya/security/permissions.py`) owns the other keys of
//! that file. Both writers read the whole file, change only their own keys,
//! and replace the file atomically. The engine's reader refuses files larger
//! than 4096 bytes, so `write_record` keeps the file below that size.
//!
//! The record's top-level fields (`n8n_version`, `node_path`, `node_version`,
//! `expression_engine`) always describe the n8n install that is in
//! `~/.laya/n8n_module` right now and the Node it must run on. `spawn_n8n`
//! reads them through `active_runtime`. They change only when an upgrade
//! commits.
//!
//! # When the upgrade runs
//!
//! `decide` applies the rules in plan section 6.5. A failed upgrade is never
//! retried automatically for the same target. The user can request one retry
//! from the Status page, which sets `retry_requested`.
//!
//! # Directories used during an upgrade
//!
//! - `~/.laya/runtimes/node-next`: the new Node, downloaded before anything
//!   live changes.
//! - `~/.laya/n8n_module.next`: the new n8n, installed with the new Node.
//! - `~/.laya/n8n/upgrade-backup`: copies of n8n's database files and its
//!   encryption key file `config`.
//! - `~/.laya/n8n_module.prev` and `~/.laya/node.prev`: the previous n8n and
//!   managed Node, renamed out of the way when the new ones are moved in.
//!
//! Every step that changes live state first writes its phase to the record.
//! If Laya quits or crashes, the next launch finds the record `in_progress`
//! and `recover` puts the previous install back, whatever phase was reached.
//! A `.prev` directory whose live directory is missing also leads to
//! `recover` (`Layout::interrupted_swap`), even if the record was lost.
//!
//! # Crash and power-cut safety
//!
//! - Only one Laya process runs this code at a time (`SetupLock`).
//! - The record is replaced atomically and flushed to disk before the step
//!   it announces starts (`replace_durably`).
//! - The backup's files are flushed before `.complete` is written, and a
//!   backup without `.complete` is never restored.
//! - The live database is changed only after the backup is complete and
//!   the record says `backed_up`.
//! - Restoring is idempotent. A crash during `recover` leads to another
//!   `recover` on the next launch.
//! - The new n8n and Node are flushed to disk (`flush_tree`) before the
//!   commit deletes the backup and the previous install.

use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::Ordering;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::n8n::{self, ExpressionEngine, N8nRuntime};
use crate::process_util::laya_home;
use crate::runtime;

/// The engine's reader (`_read_marker_raw` in `permissions.py`) treats a
/// larger marker file as missing.
const MARKER_MAX_BYTES: usize = 4096;

/// Upgrade errors stored in the record are cut to this many characters.
const ERROR_MAX_CHARS: usize = 800;

/// Time limit for `n8n list:workflow`, which runs the database migrations.
const MIGRATION_TIMEOUT: Duration = Duration::from_secs(60 * 60);

/// How long the new n8n must keep running after its first answer before the
/// upgrade commits.
const START_CHECK_STABLE: Duration = Duration::from_secs(15);

/// Files in `~/.laya/n8n/.n8n` that the upgrade backs up and restores.
/// `config` holds the key n8n encrypts stored credentials with. The `-wal`
/// and `-shm` files are included because an n8n that was killed rather than
/// stopped keeps committed transactions in the `-wal` file.
const BACKUP_FILES: &[&str] = &[
    "database.sqlite",
    "database.sqlite-wal",
    "database.sqlite-shm",
    "config",
];

/// Written into the backup directory after every file was copied and its size
/// checked. A backup without it is incomplete and is never restored.
const BACKUP_COMPLETE_FILE: &str = ".complete";

/// The upgrade target this build installs, for example
/// `n8n-2.46.6+node-24.22.2`. A failed upgrade blocks automatic retries only
/// while the target stays the same.
pub fn target_id() -> String {
    format!("n8n-{}+node-{}", n8n::N8N_VERSION, runtime::NODE_VERSION)
}

// ── Paths ───────────────────────────────────────────────────────────────

/// Every path the upgrade reads or writes, relative to one Laya home
/// directory. Tests build one over a temporary directory.
#[derive(Clone, Debug)]
pub(crate) struct Layout {
    home: PathBuf,
}

impl Layout {
    pub(crate) fn current() -> Self {
        Layout { home: laya_home() }
    }

    #[cfg(test)]
    fn at(home: &Path) -> Self {
        Layout {
            home: home.to_path_buf(),
        }
    }

    fn marker(&self) -> PathBuf {
        self.home.join("data").join(".permissions_migrated")
    }
    fn module(&self) -> PathBuf {
        self.home.join("n8n_module")
    }
    fn module_next(&self) -> PathBuf {
        self.home.join("n8n_module.next")
    }
    fn module_prev(&self) -> PathBuf {
        self.home.join("n8n_module.prev")
    }
    fn node(&self) -> PathBuf {
        self.home.join("node")
    }
    fn node_prev(&self) -> PathBuf {
        self.home.join("node.prev")
    }
    fn node_next(&self) -> PathBuf {
        self.home.join("runtimes").join("node-next")
    }
    fn n8n_db_dir(&self) -> PathBuf {
        self.home.join("n8n").join(".n8n")
    }
    fn backup(&self) -> PathBuf {
        self.home.join("n8n").join("upgrade-backup")
    }
    fn upgrade_log(&self) -> PathBuf {
        self.home.join("logs").join("n8n-upgrade.log")
    }
    fn managed_node_binary(&self) -> PathBuf {
        runtime::node_binary_in(&self.node())
    }
    fn managed_node_version(&self) -> Option<String> {
        std::fs::read_to_string(runtime::version_marker(&self.node()))
            .ok()
            .map(|s| s.trim().to_string())
    }
    fn setup_lock(&self) -> PathBuf {
        self.home.join(".n8n-setup.lock")
    }

    /// True when a `.prev` directory exists but its live directory does not.
    /// Only `swap_in` creates `.prev` directories, and it renames the live
    /// directory away first. So this state means an attempt stopped in the
    /// middle of `swap_in` or of `restore_dirs`. It is checked as well as the
    /// record, so the previous install is put back even if the record was
    /// lost.
    fn interrupted_swap(&self) -> bool {
        (self.module_prev().exists() && !self.module().exists())
            || (self.node_prev().exists() && !self.node().exists())
    }
}

// ── The runtime record ──────────────────────────────────────────────────

/// The `n8n_runtime` object in the marker file. See the module comment.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Default)]
pub struct RuntimeRecord {
    /// `completed`, `in_progress` or `failed`.
    pub status: String,
    pub n8n_version: String,
    pub node_path: String,
    pub node_version: String,
    /// `vm`, `quickjs`, or None for n8n's own default.
    #[serde(default)]
    pub expression_engine: Option<String>,
    /// Target identifier of the last failed attempt.
    #[serde(default)]
    pub failed_target: Option<String>,
    /// Set by the Status page's "Retry upgrade on next restart" button.
    #[serde(default)]
    pub retry_requested: bool,
    /// The most recent attempt. Kept after it ends so the Status page can
    /// show what happened.
    #[serde(default)]
    pub attempt: Option<Attempt>,
}

/// One upgrade attempt.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Default)]
pub struct Attempt {
    pub target: String,
    /// `staging`, `backed_up`, `swapped`, `migrating`, `start_check` or
    /// `committed`.
    pub phase: String,
    pub started_at: String,
    #[serde(default)]
    pub finished_at: Option<String>,
    #[serde(default)]
    pub error: Option<String>,
    /// Whether `~/.laya/node` existed when the attempt started. If it did,
    /// it belongs to the previous install and is restored on rollback. If it
    /// did not, a `~/.laya/node` found during rollback is the new one and is
    /// deleted.
    #[serde(default)]
    pub had_managed_node: bool,
    /// Process id of the running `n8n list:workflow` migration, so a launch
    /// after a crash can stop it before restoring the database.
    #[serde(default)]
    pub child_pid: Option<u32>,
}

impl RuntimeRecord {
    fn runtime(&self) -> Option<N8nRuntime> {
        if self.node_path.is_empty() {
            return None;
        }
        Some(N8nRuntime {
            node_path: PathBuf::from(&self.node_path),
            expression_engine: self.expression_engine.clone(),
        })
    }
}

/// Serializes marker writes inside this process. The setup thread and the
/// Status page commands can both write the record.
static MARKER_LOCK: Mutex<()> = Mutex::new(());

/// Read the marker file as a JSON object. A missing, unreadable, oversized or
/// non-object file reads as an empty object, and a symlink is never followed.
fn read_marker_object(path: &Path) -> serde_json::Map<String, serde_json::Value> {
    let empty = serde_json::Map::new();
    let Ok(meta) = std::fs::symlink_metadata(path) else {
        return empty;
    };
    if !meta.file_type().is_file() || meta.len() as usize > MARKER_MAX_BYTES {
        return empty;
    }
    let Ok(text) = std::fs::read_to_string(path) else {
        return empty;
    };
    match serde_json::from_str::<serde_json::Value>(&text) {
        Ok(serde_json::Value::Object(map)) => map,
        _ => empty,
    }
}

fn read_record_at(layout: &Layout) -> Option<RuntimeRecord> {
    let map = read_marker_object(&layout.marker());
    let value = map.get("n8n_runtime")?.clone();
    serde_json::from_value(value).ok()
}

/// The current runtime record, if one was written.
pub fn read_record() -> Option<RuntimeRecord> {
    read_record_at(&Layout::current())
}

/// Write `record` as the `n8n_runtime` key of the marker file, keeping every
/// other key. The file is written to a temporary file with mode 0600 and
/// renamed over the marker, so a reader sees the old or the new file, never
/// a partial one. Renaming replaces a symlink rather than writing through it.
fn write_record_at(layout: &Layout, record: &RuntimeRecord) -> Result<(), String> {
    let _guard = MARKER_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let path = layout.marker();
    let dir = path
        .parent()
        .ok_or_else(|| "Bad marker path".to_string())?
        .to_path_buf();
    crate::fs_private::mkdir_private(&dir)
        .map_err(|e| format!("Failed to create {}: {e}", dir.display()))?;

    let mut record = record.clone();
    if let Some(a) = record.attempt.as_mut() {
        a.error = a.error.take().map(|e| truncate_chars(&e, ERROR_MAX_CHARS));
    }
    let mut map = read_marker_object(&path);
    let serialized = loop {
        map.insert(
            "n8n_runtime".to_string(),
            serde_json::to_value(&record).map_err(|e| e.to_string())?,
        );
        let text = serde_json::to_string_pretty(&serde_json::Value::Object(map.clone()))
            .map_err(|e| e.to_string())?
            + "\n";
        if text.len() <= MARKER_MAX_BYTES {
            break text;
        }
        // Shorten the error first. If the file is still too large without
        // it, the paths themselves are too long to record.
        match record.attempt.as_mut().and_then(|a| a.error.as_mut()) {
            Some(err) if err.chars().count() > 100 => *err = truncate_chars(err, 100),
            _ => {
                return Err(format!(
                    "The n8n runtime record does not fit in {} bytes",
                    MARKER_MAX_BYTES
                ))
            }
        }
    };

    replace_file_private(&path, &serialized)
}

/// Remove the `n8n_runtime` key from the marker file, keeping every other
/// key. Deletes the file if no key is left.
fn remove_record_at(layout: &Layout) -> Result<(), String> {
    let _guard = MARKER_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let path = layout.marker();
    let mut map = read_marker_object(&path);
    if map.remove("n8n_runtime").is_none() {
        return Ok(());
    }
    if map.is_empty() {
        return std::fs::remove_file(&path).map_err(|e| format!("Delete {}: {e}", path.display()));
    }
    let text = serde_json::to_string_pretty(&serde_json::Value::Object(map))
        .map_err(|e| e.to_string())?
        + "\n";
    replace_file_private(&path, &text)
}

/// Write `text` to a temporary file with mode 0600 next to `path`, then
/// rename it over `path`.
fn replace_file_private(path: &Path, text: &str) -> Result<(), String> {
    let dir = path
        .parent()
        .ok_or_else(|| "Bad marker path".to_string())?
        .to_path_buf();
    let tmp = dir.join(format!(".permissions_migrated.tmp-{}", std::process::id()));
    {
        let mut f = crate::fs_private::create_file_private(&tmp)
            .map_err(|e| format!("Failed to create {}: {e}", tmp.display()))?;
        f.write_all(text.as_bytes())
            .and_then(|_| f.sync_all())
            .map_err(|e| format!("Failed to write {}: {e}", tmp.display()))?;
    }
    replace_durably(&tmp, path).inspect_err(|_| {
        let _ = std::fs::remove_file(&tmp);
    })
}

/// Rename the file `from` over `to` so that the rename itself is on disk
/// when this returns, retried for up to 5 seconds like `rename_with_retry`.
/// The record's phase must reach the disk before the step it announces
/// changes anything. Otherwise a power cut could leave, for example, a
/// migrated database with a record that still says `staging`.
///
/// On Unix the rename is followed by a flush of the directory, which holds
/// the file names. On Windows `MoveFileExW` with `MOVEFILE_WRITE_THROUGH`
/// does not return until the rename is on disk.
fn replace_durably(from: &Path, to: &Path) -> Result<(), String> {
    let mut last = String::new();
    for attempt in 0..10 {
        #[cfg(unix)]
        let result = std::fs::rename(from, to);
        #[cfg(windows)]
        let result = move_file_write_through(from, to);
        match result {
            Ok(()) => {
                if let Some(dir) = to.parent() {
                    sync_dir(dir);
                }
                return Ok(());
            }
            Err(e) => {
                last = e.to_string();
                if attempt < 9 {
                    std::thread::sleep(Duration::from_millis(500));
                }
            }
        }
    }
    Err(format!(
        "Rename {} -> {}: {last}",
        from.display(),
        to.display()
    ))
}

#[cfg(windows)]
fn move_file_write_through(from: &Path, to: &Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    #[link(name = "kernel32")]
    extern "system" {
        fn MoveFileExW(existing: *const u16, new: *const u16, flags: u32) -> i32;
    }
    const MOVEFILE_REPLACE_EXISTING: u32 = 0x1;
    const MOVEFILE_WRITE_THROUGH: u32 = 0x8;
    let a: Vec<u16> = from.as_os_str().encode_wide().chain(Some(0)).collect();
    let b: Vec<u16> = to.as_os_str().encode_wide().chain(Some(0)).collect();
    let ok = unsafe {
        MoveFileExW(
            a.as_ptr(),
            b.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if ok == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

fn write_record(record: &RuntimeRecord) -> Result<(), String> {
    write_record_at(&Layout::current(), record)
}

// ── Decision ────────────────────────────────────────────────────────────

/// What setup does with n8n on this launch.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Decision {
    /// n8n is not installed. Install it on the managed Node.
    FreshInstall,
    /// A previous attempt was interrupted. Restore the previous install.
    Recover,
    /// The installed n8n and its Node match the target.
    UpToDate,
    /// Run the upgrade.
    Upgrade,
    /// The last attempt for this target failed and no retry was requested.
    Blocked,
}

/// The rules of plan section 6.5, applied in order.
///
/// `installed` is the version in the installed n8n `package.json`.
/// `managed_node` is the path of the managed Node binary and
/// `managed_node_version` the contents of its `.version` file.
/// `interrupted_swap` is true when `n8n_module.prev` or `node.prev` exists
/// but the live directory it was renamed from does not (see
/// `Layout::interrupted_swap`).
///
/// An interrupted attempt is checked first, before the installed version.
/// `swap_in` renames `n8n_module` to `n8n_module.prev` before it renames the
/// new n8n into place. A crash between those two renames leaves no
/// `n8n_module`, and treating that as a fresh install would put a new n8n on
/// top of the old database with no rollback.
fn decide_from(
    installed: Option<&str>,
    record: Option<&RuntimeRecord>,
    managed_node: &Path,
    managed_node_version: Option<&str>,
    target: &str,
    interrupted_swap: bool,
) -> Decision {
    if interrupted_swap || record.is_some_and(|r| r.status == "in_progress") {
        return Decision::Recover;
    }
    let Some(installed) = installed else {
        return Decision::FreshInstall;
    };
    let Some(record) = record else {
        return Decision::Upgrade;
    };
    let matches_target = installed == n8n::N8N_VERSION
        && Path::new(&record.node_path) == managed_node
        && managed_node_version == Some(runtime::NODE_VERSION);
    if matches_target && record.status == "completed" {
        return Decision::UpToDate;
    }
    if record.status == "failed"
        && record.failed_target.as_deref() == Some(target)
        && !record.retry_requested
    {
        return Decision::Blocked;
    }
    Decision::Upgrade
}

/// The decision for this machine right now.
pub fn decide() -> Decision {
    let layout = Layout::current();
    decide_at(&layout)
}

fn decide_at(layout: &Layout) -> Decision {
    let installed = n8n::installed_n8n_version_in(&layout.module());
    let record = read_record_at(layout);
    decide_from(
        installed.as_deref(),
        record.as_ref(),
        &layout.managed_node_binary(),
        layout.managed_node_version().as_deref(),
        &target_id(),
        layout.interrupted_swap(),
    )
}

/// True when this launch will run the upgrade or recover an interrupted one.
/// While it is true, the launch path does not start n8n or the engine; the
/// setup flow on the splash screen does both after the upgrade.
pub fn upgrade_pending() -> bool {
    matches!(decide(), Decision::Upgrade | Decision::Recover)
}

/// The Node and expression engine n8n runs with. With a runtime record, that
/// is what the record says. Without one, it is the Node an older Laya build
/// used (`find_old_runtime_node`) when n8n is installed, or the managed Node
/// when it is not.
pub fn active_runtime() -> Option<N8nRuntime> {
    if let Some(runtime) = read_record().and_then(|r| r.runtime()) {
        return Some(runtime);
    }
    let node = if n8n::is_n8n_installed() {
        n8n::find_old_runtime_node().ok()?.0
    } else {
        n8n::find_node().ok()?.0
    };
    Some(N8nRuntime {
        node_path: PathBuf::from(node),
        expression_engine: None,
    })
}

/// Install n8n where none is installed (`Decision::FreshInstall`), on the
/// managed Node. `_lock` is the setup lock.
///
/// n8n is installed into `n8n_module.next` and renamed to `n8n_module` only
/// when the install is complete, its files are on disk and the record is
/// written. `decide` reads the installed version from `n8n_module`, so a
/// crash at any point before the rename leaves no `n8n_module` and the next
/// launch installs again. Without the staging directory, a crash during
/// `npm install` would leave a partial n8n that later launches treat as
/// installed.
pub fn install_fresh(
    _lock: &SetupLock,
    on_line: &mut dyn FnMut(&str),
) -> Result<ExpressionEngine, String> {
    let layout = Layout::current();
    let (node, _) = n8n::find_node()?;
    if layout.module_next().exists() {
        remove_dir_robust(&layout.module_next())?;
    }
    let engine = n8n::install_n8n_into(&layout.module_next(), Path::new(&node), &mut *on_line)?;
    let mut log = UpgradeLog::open(&layout);
    flush_tree(&layout.module_next(), &mut log);
    record_fresh_install(engine)?;
    // A directory without `node_modules/n8n/package.json` (an install that
    // an older Laya build left unfinished) can be in the way.
    if layout.module().exists() {
        remove_dir_robust(&layout.module())?;
    }
    rename_with_retry(&layout.module_next(), &layout.module())?;
    sync_dir(&layout.home);
    Ok(engine)
}

/// Record a fresh install: n8n `N8N_VERSION` on the managed Node.
fn record_fresh_install(engine: ExpressionEngine) -> Result<(), String> {
    let (node_path, node_version) = n8n::find_node()?;
    write_record(&RuntimeRecord {
        status: "completed".to_string(),
        n8n_version: n8n::N8N_VERSION.to_string(),
        node_path,
        node_version,
        expression_engine: Some(engine.as_str().to_string()),
        failed_target: None,
        retry_requested: false,
        attempt: None,
    })
}

/// Delete directories that a committed upgrade leaves if Laya quit between
/// writing the `completed` record and deleting them. Only called when the
/// decision is `UpToDate`, where none of them can still be needed.
pub fn remove_committed_leftovers() {
    let layout = Layout::current();
    for dir in [
        layout.module_prev(),
        layout.module_next(),
        layout.node_prev(),
        layout.node_next(),
        layout.backup(),
    ] {
        if dir.exists() {
            log::info!("Removing upgrade leftover {}", dir.display());
            let _ = remove_dir_robust(&dir);
        }
    }
}

// ── Test hooks ──────────────────────────────────────────────────────────
//
// These environment variables let `engine/docs/test-plan-n8n-upgrade.md`
// exercise the failure paths. Each one only takes effect while an upgrade is
// running, and each logs a warning when it does.

/// `LAYA_N8N_UPGRADE_FAULT=<step>` makes the named step fail.
fn fault(step: &str, log: &mut UpgradeLog) -> Result<(), String> {
    if std::env::var("LAYA_N8N_UPGRADE_FAULT").ok().as_deref() == Some(step) {
        let msg = format!("Test hook LAYA_N8N_UPGRADE_FAULT={step}: failing this step on purpose");
        log.warn(&msg);
        return Err(msg);
    }
    Ok(())
}

/// `LAYA_N8N_FORCE_QUICKJS=1` treats `isolated-vm` as not loadable, so the
/// install records the `quickjs` expression engine.
pub fn force_quickjs() -> bool {
    let on = force_quickjs_requested();
    if on {
        log::warn!("Test hook LAYA_N8N_FORCE_QUICKJS=1 is active");
    }
    on
}

fn force_quickjs_requested() -> bool {
    std::env::var("LAYA_N8N_FORCE_QUICKJS").ok().as_deref() == Some("1")
}

/// `LAYA_N8N_UPGRADE_PAUSE=<phase>` waits 300 seconds right after the record
/// is written with that phase, so a test can kill Laya at a known point.
fn pause(phase: &str, log: &mut UpgradeLog) {
    if std::env::var("LAYA_N8N_UPGRADE_PAUSE").ok().as_deref() == Some(phase) {
        log.warn(&format!(
            "Test hook LAYA_N8N_UPGRADE_PAUSE={phase}: pausing for 300 seconds"
        ));
        std::thread::sleep(Duration::from_secs(300));
    }
}

// ── Upgrade log ─────────────────────────────────────────────────────────

/// Appends to `~/.laya/logs/n8n-upgrade.log` and mirrors each line to the
/// shell log.
struct UpgradeLog {
    file: Option<std::fs::File>,
}

impl UpgradeLog {
    fn open(layout: &Layout) -> Self {
        let path = layout.upgrade_log();
        if let Some(dir) = path.parent() {
            let _ = crate::fs_private::mkdir_private(dir);
        }
        UpgradeLog {
            file: crate::fs_private::append_file_private(&path).ok(),
        }
    }

    fn write(&mut self, level: &str, line: &str) {
        if let Some(f) = self.file.as_mut() {
            let _ = writeln!(f, "{} {level} {line}", utc_now_iso());
        }
    }

    fn info(&mut self, line: &str) {
        log::info!("n8n upgrade: {line}");
        self.write("INFO", line);
    }

    fn warn(&mut self, line: &str) {
        log::warn!("n8n upgrade: {line}");
        self.write("WARN", line);
    }

    /// Output of a child process: written to the file only.
    fn output(&mut self, line: &str) {
        self.write("OUT ", line);
    }
}

// ── The upgrade ─────────────────────────────────────────────────────────

/// The n8n install that was active when the attempt started.
#[derive(Clone, Debug)]
struct PreviousRuntime {
    n8n_version: String,
    node_path: String,
    node_version: String,
    expression_engine: Option<String>,
}

/// An upgrade that has written its `in_progress` record. Created by `begin`
/// in the preflight step, finished by `finish` in the automation step.
pub struct UpgradeSession {
    layout: Layout,
    record: RuntimeRecord,
    previous: PreviousRuntime,
    log: UpgradeLog,
    /// The record as it was before `begin`, or None if there was none.
    /// `abort` writes it back.
    original: Option<RuntimeRecord>,
}

/// What happened to the upgrade, for the automation step of the splash.
pub enum UpgradeOutcome {
    /// The new n8n is running and the record says `completed`.
    Completed(String),
    /// The previous install is back in place and the record says `failed`.
    /// The automation step shows a warning with this text.
    RolledBack(String),
    /// The previous install could not be restored. The record stays
    /// `in_progress`, so the next launch tries the restore again. n8n is not
    /// started. The automation step shows an error with this text.
    Fatal(String),
}

/// The warning text for a failed upgrade, shown on the splash screen.
pub fn failure_warning(record: &RuntimeRecord) -> String {
    format!(
        "The n8n upgrade failed. Laya is still using n8n {}. Details are on the Status page.",
        record.n8n_version
    )
}

/// Step 1 of plan 6.6: record the previous runtime and write the
/// `in_progress` record with phase `staging`. Nothing live changes here.
///
/// `_lock` is the setup lock (`acquire_setup_lock`). The caller holds it until
/// `finish`, `abort` or `fail_before_swap` has returned.
pub fn begin(_lock: &SetupLock) -> Result<UpgradeSession, String> {
    let layout = Layout::current();
    let mut log = UpgradeLog::open(&layout);
    log.info(&format!(
        "=== Upgrade attempt to {} started ===",
        target_id()
    ));

    let n8n_version =
        n8n::installed_n8n_version_in(&layout.module()).unwrap_or_else(|| "unknown".to_string());
    let existing = read_record_at(&layout);
    let previous = match existing.as_ref().filter(|r| !r.node_path.is_empty()) {
        Some(r) => PreviousRuntime {
            n8n_version: n8n_version.clone(),
            node_path: r.node_path.clone(),
            node_version: r.node_version.clone(),
            expression_engine: r.expression_engine.clone(),
        },
        None => {
            let (node_path, node_version) = n8n::find_old_runtime_node().unwrap_or_default();
            PreviousRuntime {
                n8n_version: n8n_version.clone(),
                node_path,
                node_version,
                expression_engine: None,
            }
        }
    };
    log.info(&format!(
        "Previous runtime: n8n {} on {} ({})",
        previous.n8n_version,
        if previous.node_path.is_empty() {
            "no Node found"
        } else {
            &previous.node_path
        },
        previous.node_version
    ));

    // Leftovers that would make the swap fail. `.next` directories come from
    // an attempt that never reached its swap. `.prev` directories and a backup
    // can only come from a committed upgrade whose cleanup did not finish, or
    // from a rollback whose previous n8n did not start (the backup is then a
    // copy of the database that is live now). A rollback that did not finish
    // leaves the record `in_progress`, which leads to `recover`, not here.
    for dir in [
        layout.module_next(),
        layout.node_next(),
        layout.module_prev(),
        layout.node_prev(),
        layout.backup(),
    ] {
        if dir.exists() {
            log.info(&format!("Removing leftover {}", dir.display()));
            remove_dir_robust(&dir)?;
        }
    }

    let record = RuntimeRecord {
        status: "in_progress".to_string(),
        n8n_version: previous.n8n_version.clone(),
        node_path: previous.node_path.clone(),
        node_version: previous.node_version.clone(),
        expression_engine: previous.expression_engine.clone(),
        failed_target: existing.as_ref().and_then(|r| r.failed_target.clone()),
        retry_requested: false,
        attempt: Some(Attempt {
            target: target_id(),
            phase: "staging".to_string(),
            started_at: utc_now_iso(),
            finished_at: None,
            error: None,
            had_managed_node: layout.node().exists(),
            child_pid: None,
        }),
    };
    write_record_at(&layout, &record)?;
    n8n::UPGRADE_IN_PROGRESS.store(true, Ordering::SeqCst);
    let mut session = UpgradeSession {
        layout,
        record,
        previous,
        log,
        original: existing,
    };
    pause("staging", &mut session.log);
    Ok(session)
}

impl UpgradeSession {
    fn set_phase(&mut self, phase: &str) -> Result<(), String> {
        if let Some(a) = self.record.attempt.as_mut() {
            a.phase = phase.to_string();
        }
        write_record_at(&self.layout, &self.record)?;
        self.log.info(&format!("Phase: {phase}"));
        pause(phase, &mut self.log);
        Ok(())
    }

    fn set_child_pid(&mut self, pid: Option<u32>) {
        if let Some(a) = self.record.attempt.as_mut() {
            a.child_pid = pid;
        }
        if let Err(e) = write_record_at(&self.layout, &self.record) {
            self.log
                .warn(&format!("Could not record the migration process id: {e}"));
        }
    }

    fn had_managed_node(&self) -> bool {
        self.record
            .attempt
            .as_ref()
            .map(|a| a.had_managed_node)
            .unwrap_or(false)
    }
}

/// Cancel an attempt that has not reached the n8n install, because setup
/// stopped for an unrelated reason (the Python environment failed, or Laya is
/// quitting). Nothing live changed, so this is not recorded as a failed
/// upgrade: the staging directories are deleted and the record is put back
/// as it was before `begin`. The next setup run tries the upgrade again.
pub fn abort(mut session: UpgradeSession, reason: &str) {
    session
        .log
        .warn(&format!("Upgrade cancelled before any change: {reason}"));
    // The record goes back first. If Laya stops before the staging
    // directories are deleted, the next launch sees the original record and
    // `begin` deletes them then.
    let restored = match &session.original {
        Some(original) => write_record_at(&session.layout, original),
        None => remove_record_at(&session.layout),
    };
    if let Err(e) = restored {
        session
            .log
            .warn(&format!("Could not restore the runtime record: {e}"));
    }
    for dir in [session.layout.module_next(), session.layout.node_next()] {
        if dir.exists() {
            let _ = remove_dir_robust(&dir);
        }
    }
    n8n::UPGRADE_IN_PROGRESS.store(false, Ordering::SeqCst);
}

/// Step 2 of plan 6.6: download and verify the new Node into
/// `~/.laya/runtimes/node-next`. Progress goes to the preflight step.
pub fn stage_node<F: FnMut(runtime::RuntimeProgress)>(
    session: &mut UpgradeSession,
    mut on_progress: F,
) -> Result<(), String> {
    fault("stage_node", &mut session.log)?;
    session.log.info(&format!(
        "Downloading Node.js {} into {}",
        runtime::NODE_VERSION,
        session.layout.node_next().display()
    ));
    let dest = session.layout.node_next();
    runtime::provision_node_into(&dest, &mut on_progress).inspect_err(|e| {
        session.log.warn(&format!("Node download failed: {e}"));
    })?;
    let node = runtime::node_binary_in(&dest);
    let version = n8n::probe_node_version(&node)
        .ok_or_else(|| format!("The downloaded Node.js at {} does not run", node.display()))?;
    session.log.info(&format!("Staged Node.js {version}"));
    Ok(())
}

/// End an attempt that failed before anything live changed: delete the
/// staging directories and write the `failed` record. The previous runtime is
/// untouched, so the caller starts it normally.
pub fn fail_before_swap(mut session: UpgradeSession, error: &str) -> String {
    let warning = fail_before_swap_ref(&mut session, error);
    n8n::UPGRADE_IN_PROGRESS.store(false, Ordering::SeqCst);
    warning
}

/// Write the `failed` record with the previous runtime. Returns the write
/// error, if any; the record then still says `in_progress`.
fn mark_failed(session: &mut UpgradeSession, error: &str) -> Result<(), String> {
    let target = target_id();
    session.record.status = "failed".to_string();
    session.record.n8n_version = session.previous.n8n_version.clone();
    session.record.node_path = session.previous.node_path.clone();
    session.record.node_version = session.previous.node_version.clone();
    session.record.expression_engine = session.previous.expression_engine.clone();
    session.record.failed_target = Some(target);
    session.record.retry_requested = false;
    if let Some(a) = session.record.attempt.as_mut() {
        a.finished_at = Some(utc_now_iso());
        a.error = Some(error.to_string());
        a.child_pid = None;
    }
    write_record_at(&session.layout, &session.record).inspect_err(|e| {
        session
            .log
            .warn(&format!("Could not write the failed record: {e}"));
    })
}

/// Where a failure happened, which decides how it is undone.
enum StepError {
    /// Nothing live changed. Only staging directories need deleting.
    BeforeBackup(String),
    /// The backup exists or the directories were swapped. Undo with
    /// `rollback`.
    AfterBackup(String),
}

/// Steps 3 to 11 of plan 6.6, then the rollback if one of them fails.
/// `on_line` receives progress lines for the automation step.
pub fn finish(mut session: UpgradeSession, on_line: &mut dyn FnMut(&str)) -> UpgradeOutcome {
    let result = run_steps(&mut session, on_line);
    let outcome = match result {
        Ok(engine) => {
            let msg = format!(
                "n8n {} running on Node.js {} (expressions: {})",
                n8n::N8N_VERSION,
                runtime::NODE_VERSION,
                engine.as_str()
            );
            session
                .log
                .info(&format!("=== Upgrade committed: {msg} ==="));
            UpgradeOutcome::Completed(msg)
        }
        Err(StepError::BeforeBackup(e)) => {
            UpgradeOutcome::RolledBack(fail_before_swap_ref(&mut session, &e))
        }
        Err(StepError::AfterBackup(e)) => {
            session
                .log
                .warn(&format!("Upgrade failed: {e}. Rolling back."));
            on_line("The upgrade failed. Restoring the previous n8n...");
            rollback(&mut session, &e, on_line)
        }
    };
    n8n::UPGRADE_IN_PROGRESS.store(false, Ordering::SeqCst);
    outcome
}

/// The shared body of `fail_before_swap`, for callers that keep the session.
fn fail_before_swap_ref(session: &mut UpgradeSession, error: &str) -> String {
    session
        .log
        .warn(&format!("Upgrade failed before any change: {error}"));
    for dir in [
        session.layout.module_next(),
        session.layout.node_next(),
        session.layout.backup(),
    ] {
        if dir.exists() {
            if let Err(e) = remove_dir_robust(&dir) {
                session
                    .log
                    .warn(&format!("Could not delete {}: {e}", dir.display()));
            }
        }
    }
    // Nothing live changed, so if this write fails the record stays
    // `in_progress` and the next launch's `recover` only deletes staging
    // directories again.
    let _ = mark_failed(session, error);
    failure_warning(&session.record)
}

fn run_steps(
    session: &mut UpgradeSession,
    on_line: &mut dyn FnMut(&str),
) -> Result<ExpressionEngine, StepError> {
    use StepError::{AfterBackup, BeforeBackup};
    let layout = session.layout.clone();
    let new_node = runtime::node_binary_in(&layout.node_next());

    // Step 3: install the new n8n next to the old one.
    fault("stage_n8n", &mut session.log).map_err(BeforeBackup)?;
    // The install itself checks the variable (n8n::install_n8n_into); the
    // upgrade log records it here, like the other test hooks.
    if force_quickjs_requested() {
        session
            .log
            .warn("Test hook LAYA_N8N_FORCE_QUICKJS=1: isolated-vm is treated as not loadable");
    }
    session.log.info(&format!(
        "Installing n8n {} into {}",
        n8n::N8N_VERSION,
        layout.module_next().display()
    ));
    let engine = {
        let log = &mut session.log;
        n8n::install_n8n_into(&layout.module_next(), &new_node, |line| {
            log.output(line);
            on_line(line);
        })
        .map_err(|e| BeforeBackup(format!("Installing n8n {} failed: {e}", n8n::N8N_VERSION)))?
    };
    session.log.info(&format!(
        "Installed; expression engine: {}",
        engine.as_str()
    ));

    // Step 4: stop n8n.
    on_line("Stopping n8n...");
    n8n::shutdown_n8n();
    n8n::stop_orphaned_n8n().map_err(BeforeBackup)?;

    // Step 5: count workflows in the current database.
    let db = layout.n8n_db_dir().join("database.sqlite");
    let before = if db.exists() { count_db(&db) } else { None };
    match before {
        Some((w, m)) => session.log.info(&format!(
            "Before migration: {w} workflows, {m} applied migrations"
        )),
        None => session
            .log
            .warn("Could not count workflows before the upgrade; the count check is skipped"),
    }

    // Step 6: disk space.
    let needed = backup_size(&layout) + 1_073_741_824;
    match free_bytes(&layout.n8n_db_dir()) {
        Some(free) if free < needed => {
            return Err(BeforeBackup(format!(
                "Not enough disk space for the upgrade: {} MB free, {} MB needed",
                free / 1_048_576,
                needed / 1_048_576
            )))
        }
        Some(_) => {}
        None => session
            .log
            .warn("Could not read free disk space; continuing without the check"),
    }

    // Step 7: back up.
    on_line("Backing up the n8n database...");
    create_backup(&layout).map_err(|e| BeforeBackup(format!("Backup failed: {e}")))?;
    session.set_phase("backed_up").map_err(AfterBackup)?;
    session
        .log
        .info(&format!("Backup written to {}", layout.backup().display()));
    fault("after_backup", &mut session.log).map_err(AfterBackup)?;

    // Step 8: swap.
    session.set_phase("swapped").map_err(AfterBackup)?;
    swap_in(&layout, &mut || pause("swap_half", &mut session.log))
        .map_err(|e| AfterBackup(format!("Moving the new n8n into place failed: {e}")))?;
    fault("after_swap", &mut session.log).map_err(AfterBackup)?;

    let new_runtime = N8nRuntime {
        node_path: layout.managed_node_binary(),
        expression_engine: Some(engine.as_str().to_string()),
    };

    // Step 9: migrate.
    session.set_phase("migrating").map_err(AfterBackup)?;
    fault("migrate", &mut session.log).map_err(AfterBackup)?;
    on_line("Migrating the n8n database (this can take several minutes)...");
    run_migrations(session, &new_runtime, on_line).map_err(AfterBackup)?;
    if db.exists() {
        let after = count_db(&db);
        if let Some((w, m)) = after {
            session.log.info(&format!(
                "After migration: {w} workflows, {m} applied migrations"
            ));
        }
        if let (Some((wb, _)), Some((wa, _))) = (before, after) {
            if wb != wa {
                return Err(AfterBackup(format!(
                    "The database had {wb} workflows before the migration and {wa} after"
                )));
            }
        }
    }

    // Step 10: start check.
    session.set_phase("start_check").map_err(AfterBackup)?;
    on_line("Starting the new n8n...");
    match n8n::startup_n8n_with(&new_runtime) {
        n8n::N8nStartResult::Started | n8n::N8nStartResult::AlreadyRunning => {}
        other => return Err(AfterBackup(format!("The new n8n did not start: {other:?}"))),
    }
    n8n::wait_for_n8n_stable(Duration::from_secs(180), START_CHECK_STABLE)
        .map_err(|e| AfterBackup(format!("The new n8n failed its start check: {e}")))?;
    fault("start_check", &mut session.log).map_err(AfterBackup)?;

    // Step 11: commit. The new files are written to disk first, because the
    // commit deletes the backup and the previous install.
    on_line("Writing the new n8n to disk...");
    flush_tree(&layout.module(), &mut session.log);
    flush_tree(&layout.node(), &mut session.log);
    let node_version = n8n::probe_node_version(&new_runtime.node_path)
        .unwrap_or_else(|| format!("v{}", runtime::NODE_VERSION));
    session.record.status = "completed".to_string();
    session.record.n8n_version = n8n::N8N_VERSION.to_string();
    session.record.node_path = new_runtime.node_path.to_string_lossy().to_string();
    session.record.node_version = node_version;
    session.record.expression_engine = new_runtime.expression_engine.clone();
    session.record.failed_target = None;
    session.record.retry_requested = false;
    if let Some(a) = session.record.attempt.as_mut() {
        a.phase = "committed".to_string();
        a.finished_at = Some(utc_now_iso());
        a.error = None;
        a.child_pid = None;
    }
    write_record_at(&layout, &session.record).map_err(AfterBackup)?;
    for dir in [layout.backup(), layout.module_prev(), layout.node_prev()] {
        if dir.exists() {
            if let Err(e) = remove_dir_robust(&dir) {
                session.log.warn(&format!(
                    "Could not delete {} after the upgrade: {e}. It is deleted on the next launch.",
                    dir.display()
                ));
            }
        }
    }
    Ok(engine)
}

/// Move the staged n8n and Node into place. The order lets `restore_dirs`
/// undo any prefix of these renames. `after_first_rename` runs when
/// `n8n_module` has been renamed away and the new one is not yet in place;
/// the test hook `LAYA_N8N_UPGRADE_PAUSE=swap_half` pauses there.
fn swap_in(layout: &Layout, after_first_rename: &mut dyn FnMut()) -> Result<(), String> {
    rename_with_retry(&layout.module(), &layout.module_prev())?;
    after_first_rename();
    rename_with_retry(&layout.module_next(), &layout.module())?;
    if layout.node().exists() {
        rename_with_retry(&layout.node(), &layout.node_prev())?;
    }
    rename_with_retry(&layout.node_next(), &layout.node())?;
    sync_dir(&layout.home);
    if let Some(runtimes) = layout.node_next().parent() {
        sync_dir(runtimes);
    }
    Ok(())
}

/// Put the previous n8n module and managed Node back, whatever subset of
/// `swap_in` ran. Safe to run more than once, including after a crash in the
/// middle of an earlier run.
///
/// Errors that leave the previous install out of place are returned. A
/// staging directory (`n8n_module.next`, `runtimes/node-next`) that cannot be
/// deleted is only logged: the previous install works without deleting it,
/// and `begin` deletes it before the next attempt.
fn restore_dirs(layout: &Layout, had_managed_node: bool) -> Result<(), String> {
    if layout.module_prev().exists() {
        if layout.module().exists() {
            remove_dir_robust(&layout.module())?;
        }
        rename_with_retry(&layout.module_prev(), &layout.module())?;
        sync_dir(&layout.home);
    }
    if had_managed_node {
        if layout.node_prev().exists() {
            if layout.node().exists() {
                remove_dir_robust(&layout.node())?;
            }
            rename_with_retry(&layout.node_prev(), &layout.node())?;
        }
    } else if layout.node().exists() {
        // There was no managed Node before the attempt, so this one is new.
        remove_dir_robust(&layout.node())?;
    }
    sync_dir(&layout.home);
    for staging in [layout.module_next(), layout.node_next()] {
        if staging.exists() {
            if let Err(e) = remove_dir_robust(&staging) {
                log::warn!("Could not delete {}: {e}", staging.display());
            }
        }
    }
    Ok(())
}

/// Undo an attempt that reached the backup: restore the database and the
/// directories, write the `failed` record, start the previous n8n, and delete
/// the backup once that n8n passes its start check.
fn rollback(
    session: &mut UpgradeSession,
    error: &str,
    on_line: &mut dyn FnMut(&str),
) -> UpgradeOutcome {
    let layout = session.layout.clone();
    n8n::shutdown_n8n();
    if let Err(e) = n8n::stop_orphaned_n8n() {
        session.log.warn(&e);
    }
    stop_active_child();

    let phase = session
        .record
        .attempt
        .as_ref()
        .map(|a| a.phase.clone())
        .unwrap_or_default();
    match restore_database(&layout) {
        Ok(true) => session.log.info("Database restored from the backup"),
        Ok(false) => {
            if matches!(phase.as_str(), "swapped" | "migrating" | "start_check") {
                return fatal(
                    session,
                    &format!(
                        "{error}. The database backup in {} is incomplete, so the database could not be restored.",
                        layout.backup().display()
                    ),
                );
            }
            session
                .log
                .info("No complete backup; the database was not modified");
        }
        Err(e) => {
            return fatal(
                session,
                &format!("{error}. Restoring the database failed: {e}"),
            )
        }
    }
    if let Err(e) = restore_dirs(&layout, session.had_managed_node()) {
        return fatal(
            session,
            &format!("{error}. Restoring the previous n8n directories failed: {e}"),
        );
    }
    session.log.info("Previous n8n and Node.js restored");
    // The previous n8n must not start while the record still says
    // `in_progress`. The next launch would restore the backup again and
    // discard everything n8n wrote in between.
    if let Err(e) = mark_failed(session, error) {
        // `fatal` writes the record once more, so it must say `in_progress`.
        session.record.status = "in_progress".to_string();
        return fatal(
            session,
            &format!("{error}. The previous n8n is restored, but recording that failed: {e}"),
        );
    }

    on_line("Starting the previous n8n...");
    let warning = failure_warning(&session.record);
    let Some(previous) = session.record.runtime() else {
        session
            .log
            .warn("The previous n8n has no Node.js to run on; it was not started");
        return UpgradeOutcome::RolledBack(warning);
    };
    let started = matches!(
        n8n::startup_n8n_with(&previous),
        n8n::N8nStartResult::Started | n8n::N8nStartResult::AlreadyRunning
    ) && n8n::wait_for_n8n_stable(Duration::from_secs(120), Duration::from_secs(10))
        .is_ok();
    if started {
        session.log.info("The previous n8n is running");
        if let Err(e) = remove_dir_robust(&layout.backup()) {
            session
                .log
                .warn(&format!("Could not delete the backup: {e}"));
        }
    } else {
        session.log.warn(&format!(
            "The previous n8n did not start. The database backup is kept in {}",
            layout.backup().display()
        ));
    }
    UpgradeOutcome::RolledBack(warning)
}

/// Record a rollback that could not finish. The record stays `in_progress`,
/// so the next launch runs the restore again.
fn fatal(session: &mut UpgradeSession, message: &str) -> UpgradeOutcome {
    session
        .log
        .warn(&format!("Rollback could not finish: {message}"));
    if let Some(a) = session.record.attempt.as_mut() {
        a.error = Some(message.to_string());
        a.child_pid = None;
    }
    let _ = write_record_at(&session.layout, &session.record);
    UpgradeOutcome::Fatal(format!(
        "The n8n upgrade failed and the previous n8n could not be restored: {message}. \
         Laya tries the restore again on the next launch. Details are in {}.",
        session.layout.upgrade_log().display()
    ))
}

/// Handle `Decision::Recover`: an attempt was interrupted. Stop every process
/// it left running, restore the previous install, and mark the attempt
/// failed. Returns the same outcomes as `finish`. `_lock` is the setup lock.
///
/// Usually the record says `in_progress` and holds the previous runtime. If
/// the decision came only from `Layout::interrupted_swap` (the record says
/// something else or is missing), an `in_progress` record is written first,
/// so a crash during this restore leads to another restore. In that case:
/// - The previous runtime comes from the record if it names a Node, and from
///   `find_old_runtime_node` if it does not.
/// - `had_managed_node` is true. `restore_dirs` then puts `node.prev` back if
///   it exists and never deletes `~/.laya/node`, since without the record it
///   cannot tell which Node is new.
pub fn recover(_lock: &SetupLock, on_line: &mut dyn FnMut(&str)) -> UpgradeOutcome {
    let layout = Layout::current();
    let mut log = UpgradeLog::open(&layout);
    log.info("=== Recovering an interrupted upgrade attempt ===");
    on_line("Restoring n8n after an interrupted upgrade...");

    let existing = read_record_at(&layout);
    let record = match existing {
        Some(r) if r.status == "in_progress" => r,
        other => {
            log.warn(
                "The record does not say in_progress, but a previous n8n or Node directory \
                 is out of place; restoring it",
            );
            let mut r = other
                .filter(|r| !r.node_path.is_empty())
                .unwrap_or_else(|| {
                    let (node_path, node_version) =
                        n8n::find_old_runtime_node().unwrap_or_default();
                    RuntimeRecord {
                        n8n_version: "unknown".to_string(),
                        node_path,
                        node_version,
                        ..Default::default()
                    }
                });
            r.status = "in_progress".to_string();
            r.retry_requested = false;
            r.attempt = Some(Attempt {
                target: target_id(),
                phase: "unknown".to_string(),
                started_at: utc_now_iso(),
                had_managed_node: true,
                ..Default::default()
            });
            if let Err(e) = write_record_at(&layout, &r) {
                log.warn(&format!("Could not write the in_progress record: {e}"));
            }
            r
        }
    };

    if let Some(pid) = record.attempt.as_ref().and_then(|a| a.child_pid) {
        stop_recorded_process(pid, &mut log);
    }
    stop_leftover_n8n_processes(&layout, &mut log);
    n8n::UPGRADE_IN_PROGRESS.store(true, Ordering::SeqCst);
    let previous = PreviousRuntime {
        // A synthesized record does not know the previous n8n version. It is
        // in `n8n_module.prev` if the swap started, else in `n8n_module`.
        n8n_version: if record.n8n_version == "unknown" {
            n8n::installed_n8n_version_in(&layout.module_prev())
                .or_else(|| n8n::installed_n8n_version_in(&layout.module()))
                .unwrap_or_else(|| "unknown".to_string())
        } else {
            record.n8n_version.clone()
        },
        node_path: record.node_path.clone(),
        node_version: record.node_version.clone(),
        expression_engine: record.expression_engine.clone(),
    };
    let mut session = UpgradeSession {
        layout,
        original: None,
        record,
        previous,
        log,
    };
    let outcome = rollback(
        &mut session,
        "The upgrade was interrupted because Laya quit or crashed while it was running",
        on_line,
    );
    n8n::UPGRADE_IN_PROGRESS.store(false, Ordering::SeqCst);
    outcome
}

/// Copy the files in `BACKUP_FILES` that exist into the backup directory,
/// check each copy's size, and write `BACKUP_COMPLETE_FILE`. On macOS APFS,
/// `std::fs::copy` clones the file, so this takes no time or space.
fn create_backup(layout: &Layout) -> Result<(), String> {
    let dir = layout.backup();
    if dir.exists() {
        remove_dir_robust(&dir)?;
    }
    crate::fs_private::mkdir_private(&dir)
        .map_err(|e| format!("Failed to create {}: {e}", dir.display()))?;
    for name in BACKUP_FILES {
        let src = layout.n8n_db_dir().join(name);
        if !src.exists() {
            continue;
        }
        let dst = dir.join(name);
        std::fs::copy(&src, &dst).map_err(|e| format!("Copy {}: {e}", src.display()))?;
        let a = std::fs::metadata(&src).map_err(|e| e.to_string())?.len();
        let b = std::fs::metadata(&dst).map_err(|e| e.to_string())?.len();
        if a != b {
            return Err(format!(
                "Copy of {} has {b} bytes instead of {a}",
                src.display()
            ));
        }
        sync_file(&dst).map_err(|e| format!("Flush {}: {e}", dst.display()))?;
    }
    // The copies are on disk before `.complete` exists, and `.complete` is
    // on disk before the record says `backed_up`. A power cut can therefore
    // never leave a `.complete` backup whose files were not fully written.
    let complete = dir.join(BACKUP_COMPLETE_FILE);
    crate::fs_private::create_file_private(&complete)
        .map_err(|e| format!("Failed to finish the backup: {e}"))?;
    sync_file(&complete).map_err(|e| format!("Flush {}: {e}", complete.display()))?;
    sync_dir(&dir);
    if let Some(parent) = dir.parent() {
        sync_dir(parent);
    }
    Ok(())
}

/// Replace the live database files with the backup. Returns Ok(false) when
/// there is no complete backup, in which case nothing is changed.
fn restore_database(layout: &Layout) -> Result<bool, String> {
    let dir = layout.backup();
    if !dir.join(BACKUP_COMPLETE_FILE).exists() {
        return Ok(false);
    }
    let live = layout.n8n_db_dir();
    for name in [
        "database.sqlite",
        "database.sqlite-wal",
        "database.sqlite-shm",
    ] {
        let p = live.join(name);
        if p.exists() {
            std::fs::remove_file(&p).map_err(|e| format!("Delete {}: {e}", p.display()))?;
        }
    }
    for name in BACKUP_FILES {
        let src = dir.join(name);
        if src.exists() {
            let dst = live.join(name);
            std::fs::copy(&src, &dst).map_err(|e| format!("Copy {}: {e}", src.display()))?;
            // The restored files must be on disk before the caller writes
            // the `failed` record and deletes the backup.
            sync_file(&dst).map_err(|e| format!("Flush {}: {e}", dst.display()))?;
        }
    }
    sync_dir(&live);
    Ok(true)
}

/// Combined size of the files the backup copies.
fn backup_size(layout: &Layout) -> u64 {
    BACKUP_FILES
        .iter()
        .filter_map(|n| std::fs::metadata(layout.n8n_db_dir().join(n)).ok())
        .map(|m| m.len())
        .sum()
}

/// Count rows in n8n's `workflow_entity` and `migrations` tables, using the
/// managed venv's Python and its `sqlite3` module. Returns None when Python
/// is unavailable or the query fails. n8n must not be running.
fn count_db(db: &Path) -> Option<(u64, u64)> {
    let python = n8n::venv_python();
    if !python.exists() {
        return None;
    }
    let script = "import sqlite3,sys\n\
                  c=sqlite3.connect(sys.argv[1])\n\
                  w=c.execute('select count(*) from workflow_entity').fetchone()[0]\n\
                  m=c.execute('select count(*) from migrations').fetchone()[0]\n\
                  c.close()\n\
                  print(w,m)\n";
    let mut cmd = Command::new(python);
    n8n::no_window(&mut cmd);
    crate::process_util::sanitize_ld_library_path(&mut cmd);
    let out = cmd
        .args(["-I", "-c", script])
        .arg(db)
        .env_remove("PYTHONHOME")
        .env_remove("PYTHONPATH")
        .stderr(Stdio::null())
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&out.stdout);
    let mut parts = text.split_whitespace();
    let w = parts.next()?.parse().ok()?;
    let m = parts.next()?.parse().ok()?;
    Some((w, m))
}

/// Process id of the running `npm install` or migration command, for
/// `stop_active_child`.
static ACTIVE_CHILD: Mutex<Option<u32>> = Mutex::new(None);

/// Record (Some) or clear (None) the process `stop_active_child` stops.
pub(crate) fn register_child(pid: Option<u32>) {
    *ACTIVE_CHILD.lock().unwrap_or_else(|e| e.into_inner()) = pid;
}

/// Run `n8n list:workflow --onlyId` on the new runtime. Every n8n command
/// that opens the database first applies pending migrations, and exits with
/// an error if one fails. Lines that mention a migration are shown on the
/// splash; every line goes to the upgrade log.
fn run_migrations(
    session: &mut UpgradeSession,
    runtime: &N8nRuntime,
    on_line: &mut dyn FnMut(&str),
) -> Result<(), String> {
    let mut cmd = n8n::n8n_command(&session.layout.module(), runtime);
    cmd.args(["list:workflow", "--onlyId"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(unix)]
    unsafe {
        use std::os::unix::process::CommandExt;
        cmd.pre_exec(|| {
            libc::umask(0o077);
            Ok(())
        });
    }
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Failed to run n8n list:workflow: {e}"))?;
    let pid = child.id();
    *ACTIVE_CHILD.lock().unwrap_or_else(|e| e.into_inner()) = Some(pid);
    session.set_child_pid(Some(pid));

    let (tx, rx) = std::sync::mpsc::channel::<String>();
    let mut readers = Vec::new();
    if let Some(out) = child.stdout.take() {
        let tx = tx.clone();
        readers.push(std::thread::spawn(move || {
            for line in std::io::BufReader::new(out).lines().map_while(Result::ok) {
                let _ = tx.send(line);
            }
        }));
    }
    if let Some(err) = child.stderr.take() {
        let tx = tx.clone();
        readers.push(std::thread::spawn(move || {
            for line in std::io::BufReader::new(err).lines().map_while(Result::ok) {
                let _ = tx.send(line);
            }
        }));
    }
    drop(tx);

    let started = Instant::now();
    let mut tail: Vec<String> = Vec::new();
    let status = loop {
        match rx.recv_timeout(Duration::from_millis(500)) {
            Ok(line) => {
                session.log.output(&line);
                if line.to_lowercase().contains("migration") {
                    on_line(&line);
                }
                tail.push(line);
                if tail.len() > 5 {
                    tail.remove(0);
                }
                continue;
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                break child.wait().map_err(|e| e.to_string())?;
            }
        }
        if let Ok(Some(status)) = child.try_wait() {
            break status;
        }
        if started.elapsed() > MIGRATION_TIMEOUT {
            let _ = child.kill();
            let _ = child.wait();
            *ACTIVE_CHILD.lock().unwrap_or_else(|e| e.into_inner()) = None;
            return Err(format!(
                "The database migration did not finish within {} minutes",
                MIGRATION_TIMEOUT.as_secs() / 60
            ));
        }
    };
    for r in readers {
        let _ = r.join();
    }
    *ACTIVE_CHILD.lock().unwrap_or_else(|e| e.into_inner()) = None;
    session.set_child_pid(None);

    if !status.success() {
        return Err(format!(
            "n8n list:workflow exited with code {}: {}",
            status.code().unwrap_or(-1),
            tail.join(" | ")
        ));
    }
    session.log.info(&format!(
        "Migrations finished in {} s",
        started.elapsed().as_secs()
    ));
    Ok(())
}

/// Stop the `npm install` or migration command if one is running. Called
/// from the app's exit handler, so quitting Laya never leaves a migration
/// writing to the database. The next launch then restores the backup.
pub fn stop_active_child() {
    let pid = ACTIVE_CHILD
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .take();
    if let Some(pid) = pid {
        log::warn!("Stopping the n8n migration process (pid {pid})");
        kill_pid(pid);
    }
}

/// Stop a migration process left by a crashed launch, if it still runs and
/// its command line shows it is Laya's n8n.
fn stop_recorded_process(pid: u32, log: &mut UpgradeLog) {
    #[cfg(unix)]
    let cmdline = crate::process_cmdline(pid as i32);
    #[cfg(windows)]
    let cmdline = crate::process_cmdline(pid);
    if cmdline.contains("laya") && cmdline.contains("n8n") {
        log.warn(&format!(
            "Stopping the migration process left by the interrupted attempt (pid {pid})"
        ));
        kill_pid(pid);
        std::thread::sleep(Duration::from_secs(2));
    }
}

fn kill_pid(pid: u32) {
    #[cfg(unix)]
    unsafe {
        libc::kill(pid as i32, libc::SIGKILL);
    }
    #[cfg(windows)]
    {
        let mut tk = Command::new("taskkill");
        n8n::no_window(&mut tk);
        let _ = tk.args(["/T", "/F", "/PID", &pid.to_string()]).status();
    }
}

// ── Filesystem helpers ──────────────────────────────────────────────────

/// `std::fs::rename`, retried for up to 5 seconds. On Windows a rename fails
/// while any process (an antivirus scanner, the search indexer) has a file in
/// the tree open; such locks are usually released within a second.
fn rename_with_retry(from: &Path, to: &Path) -> Result<(), String> {
    let mut last = String::new();
    for attempt in 0..10 {
        match std::fs::rename(from, to) {
            Ok(()) => return Ok(()),
            Err(e) => {
                last = e.to_string();
                if attempt < 9 {
                    std::thread::sleep(Duration::from_millis(500));
                }
            }
        }
    }
    Err(format!(
        "Rename {} -> {}: {last}",
        from.display(),
        to.display()
    ))
}

/// `std::fs::remove_dir_all`, retried for up to 5 seconds for the same
/// reason as `rename_with_retry`. On Windows, read-only attributes (npm
/// packages can ship read-only files) are cleared before the second try.
fn remove_dir_robust(dir: &Path) -> Result<(), String> {
    let mut last = String::new();
    for attempt in 0..10 {
        match std::fs::remove_dir_all(dir) {
            Ok(()) => return Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(e) => {
                last = e.to_string();
                #[cfg(windows)]
                {
                    if attempt == 0 {
                        clear_readonly(dir);
                    }
                }
                if attempt < 9 {
                    std::thread::sleep(Duration::from_millis(500));
                }
            }
        }
    }
    Err(format!("Delete {}: {last}", dir.display()))
}

#[cfg(windows)]
fn clear_readonly(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if let Ok(meta) = std::fs::symlink_metadata(&path) {
            if meta.is_dir() {
                clear_readonly(&path);
            } else if meta.permissions().readonly() {
                let mut perms = meta.permissions();
                #[allow(clippy::permissions_set_readonly_false)]
                perms.set_readonly(false);
                let _ = std::fs::set_permissions(&path, perms);
            }
        }
    }
}

/// Free bytes available to this user on the volume holding `path`.
#[cfg(unix)]
fn free_bytes(path: &Path) -> Option<u64> {
    use std::os::unix::ffi::OsStrExt;
    let c = std::ffi::CString::new(path.as_os_str().as_bytes()).ok()?;
    let mut st: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(c.as_ptr(), &mut st) } != 0 {
        return None;
    }
    #[allow(clippy::unnecessary_cast)]
    Some(st.f_bavail as u64 * st.f_frsize as u64)
}

#[cfg(windows)]
fn free_bytes(path: &Path) -> Option<u64> {
    use std::os::windows::ffi::OsStrExt;
    #[link(name = "kernel32")]
    extern "system" {
        fn GetDiskFreeSpaceExW(
            directory: *const u16,
            free_to_caller: *mut u64,
            total: *mut u64,
            total_free: *mut u64,
        ) -> i32;
    }
    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut free: u64 = 0;
    let mut total: u64 = 0;
    let mut total_free: u64 = 0;
    let ok = unsafe { GetDiskFreeSpaceExW(wide.as_ptr(), &mut free, &mut total, &mut total_free) };
    if ok == 0 {
        None
    } else {
        Some(free)
    }
}

/// Flush one file's contents to disk. On Windows `FlushFileBuffers` needs a
/// handle with write access, so the file is opened for writing (it is not
/// truncated or changed).
fn sync_file(path: &Path) -> std::io::Result<()> {
    #[cfg(windows)]
    let f = std::fs::OpenOptions::new().write(true).open(path)?;
    #[cfg(not(windows))]
    let f = std::fs::File::open(path)?;
    f.sync_all()
}

/// Flush a directory's entries (the names of files created, renamed or
/// deleted in it) to disk. This exists on Unix only. Windows cannot open a
/// directory as a file. NTFS records name changes in its metadata journal,
/// which it replays in order after a power cut, and the record file itself
/// is renamed with `MOVEFILE_WRITE_THROUGH`. Errors are ignored, because some
/// filesystems do not support flushing a directory.
fn sync_dir(dir: &Path) {
    #[cfg(unix)]
    if let Ok(f) = std::fs::File::open(dir) {
        let _ = f.sync_all();
    }
    #[cfg(not(unix))]
    let _ = dir;
}

/// Write every file under `dir` to disk. Called on the new n8n and Node just
/// before the upgrade commits. After the commit the backup and the previous
/// install are deleted. If a power cut then lost files that npm had written
/// but the operating system had not yet saved, n8n would no longer start and
/// nothing would be left to roll back to. Failures are logged, not returned.
///
/// - Linux: one `syncfs` call writes out the whole filesystem holding `dir`.
/// - macOS: every file and directory gets `fsync`. A final `sync_all` (which
///   Rust implements with `F_FULLFSYNC` on macOS) then empties the drive's
///   own write cache once.
/// - Windows: every file gets `FlushFileBuffers` through `sync_file`.
fn flush_tree(dir: &Path, log: &mut UpgradeLog) {
    if !dir.exists() {
        return;
    }
    let started = Instant::now();
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::io::AsRawFd;
        match std::fs::File::open(dir) {
            Ok(f) => {
                if unsafe { libc::syncfs(f.as_raw_fd()) } != 0 {
                    log.warn(&format!(
                        "syncfs on {} failed: {}",
                        dir.display(),
                        std::io::Error::last_os_error()
                    ));
                }
            }
            Err(e) => log.warn(&format!("Could not open {}: {e}", dir.display())),
        }
    }
    #[cfg(not(target_os = "linux"))]
    {
        let mut flushed = 0u64;
        let mut failed = 0u64;
        flush_walk(dir, &mut flushed, &mut failed);
        #[cfg(target_os = "macos")]
        if let Ok(f) = std::fs::File::open(dir) {
            let _ = f.sync_all();
        }
        if failed > 0 {
            log.warn(&format!(
                "{failed} files under {} could not be flushed to disk",
                dir.display()
            ));
        }
        log.info(&format!("Flushed {flushed} files under {}", dir.display()));
    }
    log.info(&format!(
        "Flushing {} to disk took {} s",
        dir.display(),
        started.elapsed().as_secs()
    ));
}

/// The recursive part of `flush_tree` for macOS and Windows. Symbolic links
/// are skipped, not followed.
#[cfg(not(target_os = "linux"))]
fn flush_walk(dir: &Path, flushed: &mut u64, failed: &mut u64) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        *failed += 1;
        return;
    };
    for entry in entries.flatten() {
        let Ok(kind) = entry.file_type() else {
            *failed += 1;
            continue;
        };
        let path = entry.path();
        if kind.is_dir() {
            flush_walk(&path, flushed, failed);
        } else if kind.is_file() {
            match flush_file_contents(&path) {
                Ok(()) => *flushed += 1,
                Err(_) => *failed += 1,
            }
        }
    }
    #[cfg(unix)]
    if flush_file_contents(dir).is_err() {
        *failed += 1;
    }
}

/// `fsync` without `F_FULLFSYNC` on Unix, because `flush_tree` empties the
/// drive cache once at the end instead of once per file. `sync_file` on
/// Windows.
#[cfg(not(target_os = "linux"))]
fn flush_file_contents(path: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::io::AsRawFd;
        let f = std::fs::File::open(path)?;
        if unsafe { libc::fsync(f.as_raw_fd()) } != 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }
    #[cfg(windows)]
    {
        sync_file(path)
    }
}

// ── Setup lock ──────────────────────────────────────────────────────────

/// An exclusive lock on `~/.laya/.n8n-setup.lock`, held by the setup flow
/// while it installs, upgrades or restores n8n. Two Laya processes started at
/// the same time would otherwise both run the upgrade, or one would restore
/// the backup while the other migrates.
///
/// The operating system releases the lock when the file is closed, which
/// includes the process crashing or being killed, so a crash never leaves a
/// stale lock. The file is never deleted: deleting it while another process
/// holds the lock would let a third process lock a new file of the same name.
pub struct SetupLock {
    _file: std::fs::File,
}

/// Take the setup lock without waiting. Fails if another Laya process holds
/// it.
pub fn acquire_setup_lock() -> Result<SetupLock, String> {
    acquire_setup_lock_at(&Layout::current())
}

fn acquire_setup_lock_at(layout: &Layout) -> Result<SetupLock, String> {
    let busy = "Another Laya window is setting up or upgrading n8n. Close this window and use the other one."
        .to_string();
    crate::fs_private::mkdir_private(&layout.home)
        .map_err(|e| format!("Failed to create {}: {e}", layout.home.display()))?;
    let path = layout.setup_lock();
    #[cfg(unix)]
    {
        use std::os::unix::io::AsRawFd;
        let file = crate::fs_private::append_file_private(&path)
            .map_err(|e| format!("Failed to open {}: {e}", path.display()))?;
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err(busy);
        }
        Ok(SetupLock { _file: file })
    }
    #[cfg(windows)]
    {
        // Share mode 0 refuses every other open of the file while this
        // handle is open. Error 32 is ERROR_SHARING_VIOLATION.
        use std::os::windows::fs::OpenOptionsExt;
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .share_mode(0)
            .open(&path)
            .map_err(|e| {
                if e.raw_os_error() == Some(32) {
                    busy.clone()
                } else {
                    format!("Failed to open {}: {e}", path.display())
                }
            })?;
        Ok(SetupLock { _file: file })
    }
}

// ── Leftover processes ──────────────────────────────────────────────────

/// Stop every process, other than this one, whose command line contains the
/// path of `~/.laya/n8n_module`. That path is also a prefix of
/// `n8n_module.next` and `n8n_module.prev`. Such a process is an n8n server,
/// a migration or an npm install that a crashed launch left running. It must
/// stop before the database and directories are restored: on Windows an open
/// file cannot be deleted or replaced, and on every platform a migration that
/// still runs would keep changing the database files.
///
/// This covers a migration whose process id never reached the record (Laya
/// crashed between starting it and writing the id).
fn stop_leftover_n8n_processes(layout: &Layout, log: &mut UpgradeLog) {
    let needle = layout.module().to_string_lossy().to_lowercase();
    let me = std::process::id();
    let mut stopped = false;
    for (pid, cmdline) in list_processes() {
        if pid != me && cmdline.to_lowercase().contains(&needle) {
            log.warn(&format!("Stopping leftover process {pid}: {cmdline}"));
            kill_pid(pid);
            stopped = true;
        }
    }
    if stopped {
        std::thread::sleep(Duration::from_secs(2));
    }
}

/// Every process id with its command line. Empty if the list cannot be read.
fn list_processes() -> Vec<(u32, String)> {
    #[cfg(unix)]
    let output = Command::new("ps")
        .args(["-A", "-ww", "-o", "pid=,command="])
        .stderr(Stdio::null())
        .output();
    #[cfg(windows)]
    let output = {
        let mut ps = Command::new("powershell");
        n8n::no_window(&mut ps);
        ps.args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }",
        ])
        .stderr(Stdio::null())
        .output()
    };
    let Ok(output) = output else {
        return Vec::new();
    };
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| {
            let line = line.trim_start();
            let (pid, rest) = line.split_once(' ')?;
            Some((pid.parse().ok()?, rest.trim().to_string()))
        })
        .collect()
}

fn truncate_chars(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        let mut t: String = s.chars().take(max).collect();
        t.push('…');
        t
    }
}

/// The current time in UTC as `YYYY-MM-DDTHH:MM:SSZ`.
fn utc_now_iso() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    format_utc(secs)
}

/// Format seconds since the Unix epoch as `YYYY-MM-DDTHH:MM:SSZ`, using the
/// days-to-civil-date conversion from Howard Hinnant's date algorithms.
fn format_utc(secs: i64) -> String {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + if month <= 2 { 1 } else { 0 };
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

// ── Tauri commands (Status page) ────────────────────────────────────────

/// What the Status page shows about n8n's runtime.
#[derive(Serialize)]
pub struct RuntimeStatus {
    pub record: Option<RuntimeRecord>,
    pub target: String,
    pub target_n8n_version: String,
    pub target_node_version: String,
    pub installed_n8n_version: Option<String>,
    pub managed_node_path: String,
    pub upgrade_running: bool,
}

#[tauri::command]
pub fn n8n_runtime_status() -> RuntimeStatus {
    let layout = Layout::current();
    RuntimeStatus {
        record: read_record_at(&layout),
        target: target_id(),
        target_n8n_version: n8n::N8N_VERSION.to_string(),
        target_node_version: runtime::NODE_VERSION.to_string(),
        installed_n8n_version: n8n::installed_n8n_version_in(&layout.module()),
        managed_node_path: layout.managed_node_binary().to_string_lossy().to_string(),
        upgrade_running: n8n::UPGRADE_IN_PROGRESS.load(Ordering::SeqCst),
    }
}

fn set_retry_requested(value: bool) -> Result<(), String> {
    let mut record = read_record().ok_or_else(|| "No n8n runtime record".to_string())?;
    if record.status != "failed" {
        return Err("The n8n upgrade has not failed, so there is nothing to retry".to_string());
    }
    record.retry_requested = value;
    write_record(&record)
}

/// The Status page's "Retry upgrade on next restart" button.
#[tauri::command]
pub fn request_n8n_upgrade_retry() -> Result<(), String> {
    set_retry_requested(true)
}

/// The Status page's "Cancel" button next to a scheduled retry.
#[tauri::command]
pub fn cancel_n8n_upgrade_retry() -> Result<(), String> {
    set_retry_requested(false)
}

// ── Tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    /// A unique, empty directory under the system temp dir, deleted on drop.
    struct TempHome(PathBuf);
    impl TempHome {
        fn new(name: &str) -> Self {
            let p = std::env::temp_dir().join(format!(
                "laya-n8n-upgrade-{name}-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            std::fs::create_dir_all(&p).unwrap();
            TempHome(p)
        }
    }
    impl Drop for TempHome {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn write(path: &Path, text: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }

    fn read(path: &Path) -> String {
        std::fs::read_to_string(path).unwrap()
    }

    fn record(status: &str) -> RuntimeRecord {
        RuntimeRecord {
            status: status.to_string(),
            n8n_version: "2.15.0".to_string(),
            node_path: "/opt/homebrew/bin/node".to_string(),
            node_version: "v26.7.0".to_string(),
            ..Default::default()
        }
    }

    // ── decide_from (plan 6.5) ─────────────────────────────────────────

    const MANAGED: &str = "/home/u/.laya/node/bin/node";

    fn up_to_date() -> RuntimeRecord {
        RuntimeRecord {
            status: "completed".to_string(),
            n8n_version: n8n::N8N_VERSION.to_string(),
            node_path: MANAGED.to_string(),
            node_version: format!("v{}", runtime::NODE_VERSION),
            expression_engine: Some("vm".to_string()),
            ..Default::default()
        }
    }

    fn decide_with(
        installed: Option<&str>,
        rec: Option<&RuntimeRecord>,
        node_ver: Option<&str>,
    ) -> Decision {
        decide_from(
            installed,
            rec,
            Path::new(MANAGED),
            node_ver,
            &target_id(),
            false,
        )
    }

    #[test]
    fn rule1_not_installed_is_fresh_install() {
        assert_eq!(decide_with(None, None, None), Decision::FreshInstall);
        assert_eq!(
            decide_with(None, Some(&record("failed")), None),
            Decision::FreshInstall
        );
    }

    #[test]
    fn rule2_in_progress_is_recover() {
        assert_eq!(
            decide_with(Some("2.15.0"), Some(&record("in_progress")), None),
            Decision::Recover
        );
    }

    #[test]
    fn in_progress_without_n8n_module_is_recover_not_fresh_install() {
        // A crash between the two n8n renames of swap_in leaves no
        // n8n_module, only n8n_module.prev.
        assert_eq!(
            decide_with(None, Some(&record("in_progress")), None),
            Decision::Recover
        );
    }

    #[test]
    fn interrupted_swap_is_recover_even_without_a_record() {
        for rec in [None, Some(record("completed")), Some(record("failed"))] {
            for installed in [None, Some("2.15.0")] {
                assert_eq!(
                    decide_from(
                        installed,
                        rec.as_ref(),
                        Path::new(MANAGED),
                        None,
                        &target_id(),
                        true
                    ),
                    Decision::Recover
                );
            }
        }
    }

    #[test]
    fn setup_lock_is_exclusive_and_released_on_drop() {
        let home = TempHome::new("lock");
        let layout = Layout::at(&home.0);
        let first = acquire_setup_lock_at(&layout).expect("first lock");
        assert!(
            acquire_setup_lock_at(&layout).is_err(),
            "a second holder must be refused"
        );
        drop(first);
        // Other tests start child processes in parallel. A child holds a
        // copy of every open file between fork and exec, so the lock can
        // stay taken for a moment after `first` is dropped.
        let deadline = Instant::now() + Duration::from_secs(5);
        while acquire_setup_lock_at(&layout).is_err() {
            assert!(Instant::now() < deadline, "the lock was not released");
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(
            layout.setup_lock().exists(),
            "the lock file is never deleted"
        );
    }

    #[test]
    fn flush_tree_handles_files_dirs_and_symlinks() {
        let home = TempHome::new("flush");
        let dir = home.0.join("tree");
        write(&dir.join("a").join("b.js"), "x");
        write(&dir.join("c.json"), "{}");
        #[cfg(unix)]
        std::os::unix::fs::symlink(dir.join("c.json"), dir.join("link")).unwrap();
        let mut log = UpgradeLog { file: None };
        flush_tree(&dir, &mut log);
        flush_tree(&home.0.join("missing"), &mut log);
        assert_eq!(read(&dir.join("a").join("b.js")), "x");
    }

    #[test]
    fn list_processes_includes_this_process() {
        let me = std::process::id();
        assert!(list_processes().iter().any(|(pid, _)| *pid == me));
    }

    #[test]
    fn interrupted_swap_detection() {
        let home = TempHome::new("swapstate");
        let layout = Layout::at(&home.0);
        assert!(!layout.interrupted_swap());
        std::fs::create_dir_all(layout.module_prev()).unwrap();
        assert!(layout.interrupted_swap(), "module.prev without module");
        std::fs::create_dir_all(layout.module()).unwrap();
        assert!(!layout.interrupted_swap(), "module.prev next to module");
        std::fs::create_dir_all(layout.node_prev()).unwrap();
        assert!(layout.interrupted_swap(), "node.prev without node");
        std::fs::create_dir_all(layout.node()).unwrap();
        assert!(!layout.interrupted_swap());
    }

    #[test]
    fn rule3_matching_runtime_is_up_to_date() {
        let rec = up_to_date();
        assert_eq!(
            decide_with(
                Some(n8n::N8N_VERSION),
                Some(&rec),
                Some(runtime::NODE_VERSION)
            ),
            Decision::UpToDate
        );
    }

    #[test]
    fn rule3_requires_managed_node_version_and_path() {
        let rec = up_to_date();
        assert_eq!(
            decide_with(Some(n8n::N8N_VERSION), Some(&rec), Some("22.22.3")),
            Decision::Upgrade
        );
        let mut system = up_to_date();
        system.node_path = "/opt/homebrew/bin/node".to_string();
        assert_eq!(
            decide_with(
                Some(n8n::N8N_VERSION),
                Some(&system),
                Some(runtime::NODE_VERSION)
            ),
            Decision::Upgrade
        );
    }

    #[test]
    fn rule3_reads_installed_version_not_the_record() {
        let rec = up_to_date();
        assert_eq!(
            decide_with(Some("2.15.0"), Some(&rec), Some(runtime::NODE_VERSION)),
            Decision::Upgrade
        );
    }

    #[test]
    fn rule4_no_record_is_upgrade() {
        assert_eq!(decide_with(Some("2.15.0"), None, None), Decision::Upgrade);
        assert_eq!(
            decide_with(Some(n8n::N8N_VERSION), None, Some(runtime::NODE_VERSION)),
            Decision::Upgrade
        );
    }

    #[test]
    fn rule5_failed_same_target_is_blocked() {
        let mut rec = record("failed");
        rec.failed_target = Some(target_id());
        assert_eq!(
            decide_with(Some("2.15.0"), Some(&rec), None),
            Decision::Blocked
        );
    }

    #[test]
    fn rule6_retry_requested_or_new_target_upgrades() {
        let mut rec = record("failed");
        rec.failed_target = Some(target_id());
        rec.retry_requested = true;
        assert_eq!(
            decide_with(Some("2.15.0"), Some(&rec), None),
            Decision::Upgrade
        );

        let mut older = record("failed");
        older.failed_target = Some("n8n-2.46.5+node-24.22.1".to_string());
        assert_eq!(
            decide_with(Some("2.15.0"), Some(&older), None),
            Decision::Upgrade
        );
    }

    // ── marker file ─────────────────────────────────────────────────────

    #[test]
    fn write_record_keeps_other_keys() {
        let home = TempHome::new("merge");
        let layout = Layout::at(&home.0);
        write(
            &layout.marker(),
            r#"{"version":"filesystem_permissions_v1","status":"completed"}"#,
        );
        write_record_at(&layout, &record("failed")).unwrap();
        let v: serde_json::Value = serde_json::from_str(&read(&layout.marker())).unwrap();
        assert_eq!(v["version"], "filesystem_permissions_v1");
        assert_eq!(v["status"], "completed");
        assert_eq!(v["n8n_runtime"]["status"], "failed");
        assert_eq!(read_record_at(&layout).unwrap(), record("failed"));
    }

    #[test]
    fn write_record_creates_missing_file() {
        let home = TempHome::new("create");
        let layout = Layout::at(&home.0);
        write_record_at(&layout, &record("completed")).unwrap();
        let v: serde_json::Value = serde_json::from_str(&read(&layout.marker())).unwrap();
        assert!(v.get("version").is_none());
        assert_eq!(v["n8n_runtime"]["n8n_version"], "2.15.0");
    }

    #[cfg(unix)]
    #[test]
    fn write_record_file_is_private() {
        use std::os::unix::fs::PermissionsExt;
        let home = TempHome::new("mode");
        let layout = Layout::at(&home.0);
        write_record_at(&layout, &record("completed")).unwrap();
        let mode = std::fs::metadata(layout.marker())
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(mode, 0o600);
    }

    #[test]
    fn write_record_stays_under_the_size_limit() {
        let home = TempHome::new("size");
        let layout = Layout::at(&home.0);
        let mut rec = record("failed");
        rec.attempt = Some(Attempt {
            target: target_id(),
            phase: "migrating".to_string(),
            error: Some("x".repeat(10_000)),
            ..Default::default()
        });
        write_record_at(&layout, &rec).unwrap();
        assert!(read(&layout.marker()).len() <= MARKER_MAX_BYTES);
        let stored = read_record_at(&layout).unwrap();
        assert!(stored.attempt.unwrap().error.unwrap().chars().count() <= ERROR_MAX_CHARS + 1);
    }

    #[cfg(unix)]
    #[test]
    fn write_record_replaces_a_symlink_instead_of_following_it() {
        let home = TempHome::new("symlink");
        let layout = Layout::at(&home.0);
        let target = home.0.join("elsewhere.json");
        write(&target, "{}");
        std::fs::create_dir_all(layout.marker().parent().unwrap()).unwrap();
        std::os::unix::fs::symlink(&target, layout.marker()).unwrap();
        write_record_at(&layout, &record("completed")).unwrap();
        assert_eq!(read(&target), "{}");
        assert!(!std::fs::symlink_metadata(layout.marker())
            .unwrap()
            .file_type()
            .is_symlink());
    }

    #[test]
    fn remove_record_keeps_other_keys_and_deletes_an_empty_file() {
        let home = TempHome::new("remove");
        let layout = Layout::at(&home.0);
        write(
            &layout.marker(),
            r#"{"version":"filesystem_permissions_v1","status":"completed"}"#,
        );
        write_record_at(&layout, &record("in_progress")).unwrap();
        remove_record_at(&layout).unwrap();
        let v: serde_json::Value = serde_json::from_str(&read(&layout.marker())).unwrap();
        assert_eq!(v["version"], "filesystem_permissions_v1");
        assert!(v.get("n8n_runtime").is_none());

        let alone = TempHome::new("remove-alone");
        let layout = Layout::at(&alone.0);
        write_record_at(&layout, &record("in_progress")).unwrap();
        remove_record_at(&layout).unwrap();
        assert!(!layout.marker().exists());
    }

    #[test]
    fn unreadable_marker_reads_as_no_record() {
        let home = TempHome::new("garbage");
        let layout = Layout::at(&home.0);
        write(&layout.marker(), "not json");
        assert!(read_record_at(&layout).is_none());
    }

    // ── swap, restore, backup ───────────────────────────────────────────

    /// Builds a home with an old module and DB, a staged module and node,
    /// and optionally an old managed node.
    fn staged_home(name: &str, had_managed_node: bool) -> (TempHome, Layout) {
        let home = TempHome::new(name);
        let layout = Layout::at(&home.0);
        write(&layout.module().join("which"), "old-n8n");
        write(&layout.module_next().join("which"), "new-n8n");
        write(&layout.node_next().join("which"), "new-node");
        if had_managed_node {
            write(&layout.node().join("which"), "old-node");
        }
        write(&layout.n8n_db_dir().join("database.sqlite"), "old-db");
        write(&layout.n8n_db_dir().join("database.sqlite-wal"), "old-wal");
        write(&layout.n8n_db_dir().join("config"), "key");
        (home, layout)
    }

    fn assert_restored(layout: &Layout, had_managed_node: bool) {
        assert_eq!(read(&layout.module().join("which")), "old-n8n");
        assert!(!layout.module_prev().exists());
        assert!(!layout.module_next().exists());
        assert!(!layout.node_prev().exists());
        assert!(!layout.node_next().exists());
        if had_managed_node {
            assert_eq!(read(&layout.node().join("which")), "old-node");
        } else {
            assert!(!layout.node().exists());
        }
    }

    #[test]
    fn swap_then_restore_returns_the_old_tree_case_1_and_2() {
        let (_h, layout) = staged_home("swap12", false);
        swap_in(&layout, &mut || {}).unwrap();
        assert_eq!(read(&layout.module().join("which")), "new-n8n");
        assert_eq!(read(&layout.node().join("which")), "new-node");
        restore_dirs(&layout, false).unwrap();
        assert_restored(&layout, false);
    }

    #[test]
    fn swap_then_restore_returns_the_old_tree_case_3() {
        let (_h, layout) = staged_home("swap3", true);
        swap_in(&layout, &mut || {}).unwrap();
        assert_eq!(read(&layout.node().join("which")), "new-node");
        assert_eq!(read(&layout.node_prev().join("which")), "old-node");
        restore_dirs(&layout, true).unwrap();
        assert_restored(&layout, true);
    }

    /// Simulates a crash after each possible number of `swap_in` renames,
    /// with the `in_progress` record on disk. The next launch must decide
    /// `Recover`, and `restore_dirs` must put the previous install back,
    /// also when it is itself run twice (a crash during the restore).
    #[test]
    fn crash_after_any_number_of_swap_renames_recovers() {
        for had_managed_node in [true, false] {
            for renames in 0..=4 {
                let (_h, layout) = staged_home(&format!("crash{renames}"), had_managed_node);
                let mut rec = record("in_progress");
                rec.attempt = Some(Attempt {
                    target: target_id(),
                    phase: "swapped".to_string(),
                    had_managed_node,
                    ..Default::default()
                });
                write_record_at(&layout, &rec).unwrap();
                let steps: Vec<(PathBuf, PathBuf)> = {
                    let mut v = vec![
                        (layout.module(), layout.module_prev()),
                        (layout.module_next(), layout.module()),
                    ];
                    if had_managed_node {
                        v.push((layout.node(), layout.node_prev()));
                    }
                    v.push((layout.node_next(), layout.node()));
                    v
                };
                for (from, to) in steps.iter().take(renames) {
                    rename_with_retry(from, to).unwrap();
                }
                assert_eq!(
                    decide_at(&layout),
                    Decision::Recover,
                    "renames={renames} had_managed_node={had_managed_node}"
                );
                restore_dirs(&layout, had_managed_node).unwrap();
                restore_dirs(&layout, had_managed_node).unwrap();
                assert_restored(&layout, had_managed_node);
                assert!(!layout.interrupted_swap());
            }
        }
    }

    #[test]
    fn lost_record_with_n8n_module_renamed_away_is_recover() {
        let (_h, layout) = staged_home("lostrecord", true);
        rename_with_retry(&layout.module(), &layout.module_prev()).unwrap();
        assert_eq!(decide_at(&layout), Decision::Recover);
    }

    #[test]
    fn restore_handles_a_swap_interrupted_after_the_first_rename() {
        let (_h, layout) = staged_home("partial", true);
        rename_with_retry(&layout.module(), &layout.module_prev()).unwrap();
        restore_dirs(&layout, true).unwrap();
        assert_restored(&layout, true);
    }

    #[test]
    fn restore_handles_a_swap_interrupted_between_the_node_renames() {
        let (_h, layout) = staged_home("partialnode", true);
        rename_with_retry(&layout.module(), &layout.module_prev()).unwrap();
        rename_with_retry(&layout.module_next(), &layout.module()).unwrap();
        rename_with_retry(&layout.node(), &layout.node_prev()).unwrap();
        restore_dirs(&layout, true).unwrap();
        assert_restored(&layout, true);
    }

    #[test]
    fn restore_before_any_swap_only_deletes_staging() {
        let (_h, layout) = staged_home("staging", false);
        restore_dirs(&layout, false).unwrap();
        assert_restored(&layout, false);
    }

    #[test]
    fn restore_is_idempotent() {
        let (_h, layout) = staged_home("twice", true);
        swap_in(&layout, &mut || {}).unwrap();
        restore_dirs(&layout, true).unwrap();
        restore_dirs(&layout, true).unwrap();
        assert_restored(&layout, true);
    }

    #[test]
    fn backup_and_restore_round_trip() {
        let (_h, layout) = staged_home("backup", false);
        create_backup(&layout).unwrap();
        assert!(layout.backup().join(BACKUP_COMPLETE_FILE).exists());
        assert!(!layout.backup().join("database.sqlite-shm").exists());
        // The migration rewrote the database and created a -shm file.
        write(&layout.n8n_db_dir().join("database.sqlite"), "migrated-db");
        write(&layout.n8n_db_dir().join("database.sqlite-shm"), "new-shm");
        std::fs::remove_file(layout.n8n_db_dir().join("database.sqlite-wal")).unwrap();
        assert!(restore_database(&layout).unwrap());
        assert_eq!(read(&layout.n8n_db_dir().join("database.sqlite")), "old-db");
        assert_eq!(
            read(&layout.n8n_db_dir().join("database.sqlite-wal")),
            "old-wal"
        );
        assert!(!layout.n8n_db_dir().join("database.sqlite-shm").exists());
        assert_eq!(read(&layout.n8n_db_dir().join("config")), "key");
    }

    #[test]
    fn incomplete_backup_is_never_restored() {
        let (_h, layout) = staged_home("incomplete", false);
        write(&layout.backup().join("database.sqlite"), "partial");
        write(&layout.n8n_db_dir().join("database.sqlite"), "live");
        assert!(!restore_database(&layout).unwrap());
        assert_eq!(read(&layout.n8n_db_dir().join("database.sqlite")), "live");
    }

    // ── helpers ─────────────────────────────────────────────────────────

    #[test]
    fn format_utc_matches_known_dates() {
        assert_eq!(format_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(format_utc(951_782_400), "2000-02-29T00:00:00Z");
        assert_eq!(format_utc(1_794_225_164), "2026-11-09T11:52:44Z");
    }

    #[test]
    fn target_id_names_both_versions() {
        assert_eq!(
            target_id(),
            format!("n8n-{}+node-{}", n8n::N8N_VERSION, runtime::NODE_VERSION)
        );
    }

    #[test]
    fn free_bytes_reads_the_temp_volume() {
        assert!(free_bytes(&std::env::temp_dir()).unwrap_or(0) > 0);
    }
}
