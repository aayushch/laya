// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

//! Shared process/path plumbing for the sidecar and n8n runtimes.
//!
//! `home_dir` / `laya_home` were copy-pasted verbatim into sidecar.rs, n8n.rs and
//! runtime.rs, and the AppImage LD_LIBRARY_PATH scrub was duplicated between the
//! node and python spawn sanitizers. Centralized here so they can't drift (review
//! §5.8 — P7-10).

use std::path::PathBuf;
use std::process::Command;

/// The user's home directory (`HOME` on Unix, `USERPROFILE` on Windows).
pub(crate) fn home_dir() -> Option<PathBuf> {
    #[cfg(unix)]
    {
        std::env::var_os("HOME").map(PathBuf::from)
    }
    #[cfg(windows)]
    {
        std::env::var_os("USERPROFILE").map(PathBuf::from)
    }
}

/// Command-line flag that turns `laya-app.exe` into the one-shot Ctrl+C
/// helper (`ctrl_c_helper_main`) instead of starting the app.
#[cfg(windows)]
const CTRL_C_HELPER_FLAG: &str = "--laya-send-ctrl-c";

/// Ask a console process to exit cleanly by sending it Ctrl+C, the Windows
/// counterpart of SIGTERM. Node (n8n) and Python (uvicorn) both treat it as
/// SIGINT and run their shutdown handlers. Returns true when the event was
/// sent; the caller still waits for the exit and force-kills on timeout.
///
/// `taskkill` without `/F` only posts WM_CLOSE to a process's windows. The
/// engine and n8n run with `CREATE_NO_WINDOW` and have no window, so for them
/// it never does anything. A console control event is the only clean stop
/// they can receive.
///
/// The event is sent by a short-lived copy of this executable
/// (`ctrl_c_helper_main`), not by this process. Sending it requires attaching
/// to the target's console, and every process attached to that console
/// receives the event. A GUI process that received it would be terminated by
/// the default handler, so the app itself never attaches.
#[cfg(windows)]
pub(crate) fn send_ctrl_c(pid: u32) -> bool {
    use std::os::windows::process::CommandExt;
    const DETACHED_PROCESS: u32 = 0x0000_0008;
    let Ok(exe) = std::env::current_exe() else {
        return false;
    };
    let mut cmd = Command::new(exe);
    cmd.arg(CTRL_C_HELPER_FLAG)
        .arg(pid.to_string())
        .creation_flags(DETACHED_PROCESS)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    matches!(cmd.status(), Ok(s) if s.success())
}

/// Entry point of the Ctrl+C helper started by `send_ctrl_c`. Returns None
/// when this process was not started as the helper (the normal app start);
/// otherwise the exit code: 0 when the event was sent.
///
/// The helper has no console of its own (`DETACHED_PROCESS`). It attaches to
/// the target's console, ignores the event itself, sends Ctrl+C to every
/// process on that console (the target and the processes it started, such as
/// n8n's task runner), and exits.
#[cfg(windows)]
pub fn ctrl_c_helper_main() -> Option<i32> {
    let mut args = std::env::args().skip(1);
    if args.next().as_deref() != Some(CTRL_C_HELPER_FLAG) {
        return None;
    }
    let Some(pid) = args.next().and_then(|p| p.parse::<u32>().ok()) else {
        return Some(2);
    };
    #[link(name = "kernel32")]
    extern "system" {
        fn FreeConsole() -> i32;
        fn AttachConsole(pid: u32) -> i32;
        fn SetConsoleCtrlHandler(handler: *const core::ffi::c_void, add: i32) -> i32;
        fn GenerateConsoleCtrlEvent(event: u32, group: u32) -> i32;
    }
    const CTRL_C_EVENT: u32 = 0;
    unsafe {
        FreeConsole();
        if AttachConsole(pid) == 0 {
            return Some(1);
        }
        // A null handler with TRUE makes this process ignore Ctrl+C, so the
        // helper stays alive to exit with its own code.
        SetConsoleCtrlHandler(core::ptr::null(), 1);
        // Group 0 means every process attached to this console.
        let sent = GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0) != 0;
        // The event is delivered asynchronously; leaving the console right
        // away can drop it for processes that have not handled it yet.
        std::thread::sleep(std::time::Duration::from_millis(200));
        FreeConsole();
        Some(if sent { 0 } else { 1 })
    }
}

/// `~/.laya` — the Laya data root.
pub(crate) fn laya_home() -> PathBuf {
    home_dir().unwrap_or_default().join(".laya")
}

/// Strip AppImage-injected `LD_LIBRARY_PATH` entries (anything under
/// `/tmp/.mount_*`) from a command about to be spawned, keeping the rest so the
/// child still resolves genuine system libraries. Shared by the node and python
/// spawn sanitizers, which both had this identical block.
pub(crate) fn sanitize_ld_library_path(cmd: &mut Command) {
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

/// Strip AppImage-injected `PYTHONHOME` / `PYTHONPATH` from a command that is
/// not itself Python but may start one: npm runs node-gyp, which runs Python
/// for any native add-on built from source. An AppImage points both variables
/// into its own mount, where every Python on the machine then fails to start,
/// so the add-on build fails. Only entries inside the AppImage are removed: a
/// user's own values pass through, and outside an AppImage nothing changes.
pub(crate) fn sanitize_appimage_python_env(cmd: &mut Command) {
    let appdir = std::env::var("APPDIR").ok();
    for name in ["PYTHONHOME", "PYTHONPATH"] {
        let Ok(value) = std::env::var(name) else {
            continue;
        };
        match without_appimage_entries(&value, appdir.as_deref()) {
            None => {}
            Some(cleaned) if cleaned.is_empty() => {
                cmd.env_remove(name);
            }
            Some(cleaned) => {
                cmd.env(name, cleaned);
            }
        }
    }
}

/// `value` (a colon-separated path list) without its entries inside the
/// AppImage: under a `/tmp/.mount_*` mount or under `appdir` (`APPDIR`, which
/// also covers `--appimage-extract-and-run`). None when no entry is inside it.
fn without_appimage_entries(value: &str, appdir: Option<&str>) -> Option<String> {
    let appdir = appdir
        .map(|d| d.trim_end_matches('/'))
        .filter(|d| !d.is_empty());
    let inside = |p: &str| {
        p.starts_with("/tmp/.mount_")
            || appdir.is_some_and(|d| p == d || p.starts_with(&format!("{d}/")))
    };
    if !value.split(':').any(inside) {
        return None;
    }
    let kept: Vec<&str> = value
        .split(':')
        .filter(|p| !p.is_empty() && !inside(p))
        .collect();
    Some(kept.join(":"))
}

#[cfg(test)]
mod tests {
    use super::without_appimage_entries;

    #[test]
    fn appimage_python_entries_are_removed() {
        let home = "/tmp/.mount_LayaAb12/usr/";
        assert_eq!(without_appimage_entries(home, None).as_deref(), Some(""));
        let path = "/tmp/.mount_LayaAb12/usr/share/pyshared/:";
        assert_eq!(without_appimage_entries(path, None).as_deref(), Some(""));
    }

    #[test]
    fn user_entries_are_kept() {
        let path = "/home/u/lib/py:/tmp/.mount_LayaAb12/usr/share/pyshared/:";
        assert_eq!(
            without_appimage_entries(path, None).as_deref(),
            Some("/home/u/lib/py")
        );
        assert_eq!(without_appimage_entries("/home/u/lib/py", None), None);
        assert_eq!(
            without_appimage_entries("/opt/python", Some("/opt/py")),
            None
        );
    }

    #[test]
    fn extracted_appimage_dir_counts_as_inside() {
        let appdir = Some("/tmp/appimage_extracted_1234/");
        let home = "/tmp/appimage_extracted_1234/usr/";
        assert_eq!(without_appimage_entries(home, appdir).as_deref(), Some(""));
    }
}
