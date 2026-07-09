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
