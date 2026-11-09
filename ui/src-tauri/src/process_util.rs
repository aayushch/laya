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
