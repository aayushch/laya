// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

//! Filesystem helpers that enforce restrictive permissions (0700/0600) on Unix.
//!
//! Every directory under `~/.laya/` that Laya creates must be 0700.
//! Every sensitive file (logs, config, databases) must be 0600.
//! These helpers create or tighten paths with those modes, bypassing the
//! process umask on Unix with explicit `set_permissions` calls.

use std::fs;
use std::io;
use std::path::Path;

#[cfg(unix)]
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};

/// Create `path` and all missing parents with mode 0700 on Unix.
///
/// On Windows delegates to `fs::create_dir_all` (permissions work differently).
pub fn mkdir_private(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        // DirBuilder::mode sets the mode argument to mkdir(2). Because umask can
        // only mask bits (not add them), 0o700 is preserved under any umask.
        fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(path)?;
        // Tighten the leaf directory in case it already existed with wider perms.
        set_dir_private(path)?;
    }
    #[cfg(not(unix))]
    {
        fs::create_dir_all(path)?;
    }
    Ok(())
}

/// Tighten an existing directory to 0700 on Unix. No-op on Windows.
pub fn set_dir_private(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

/// Open or create a file at `path` with mode 0600 on Unix (truncates on open).
///
/// The caller is responsible for ensuring the parent directory exists.
pub fn create_file_private(path: &Path) -> io::Result<fs::File> {
    let mut opts = fs::OpenOptions::new();
    opts.write(true).create(true).truncate(true);
    #[cfg(unix)]
    opts.mode(0o600);
    opts.open(path)
}

/// Open or create a file at `path` for appending with mode 0600 on Unix.
pub fn append_file_private(path: &Path) -> io::Result<fs::File> {
    let mut opts = fs::OpenOptions::new();
    opts.write(true).create(true).append(true);
    #[cfg(unix)]
    opts.mode(0o600);
    opts.open(path)
}

/// Tighten an existing file to 0600 on Unix. No-op on Windows or if missing.
pub fn set_file_private(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    if path.exists() {
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[cfg(unix)]
    fn mode(path: &Path) -> u32 {
        use std::os::unix::fs::MetadataExt;
        path.metadata().unwrap().mode() & 0o777
    }

    #[test]
    #[cfg(unix)]
    fn mkdir_private_creates_with_0700() {
        let dir = std::env::temp_dir().join(format!("laya_fsp_mkdir_{}", std::process::id()));
        let sub = dir.join("a").join("b");
        let _ = fs::remove_dir_all(&dir);

        // Under permissive umask 000, mkdir with mode 0777 gives 0777.
        // Our mkdir_private must give 0700 regardless.
        let old_mask = unsafe { libc::umask(0o000) };
        let result = mkdir_private(&sub);
        unsafe { libc::umask(old_mask) };
        result.unwrap();

        assert_eq!(mode(&sub), 0o700, "leaf must be 0700");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    #[cfg(unix)]
    fn set_dir_private_tightens_existing() {
        let dir = std::env::temp_dir().join(format!("laya_fsp_tight_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o755)).unwrap();
        assert_eq!(mode(&dir), 0o755);

        set_dir_private(&dir).unwrap();
        assert_eq!(mode(&dir), 0o700);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    #[cfg(unix)]
    fn create_file_private_creates_with_0600() {
        let dir = std::env::temp_dir().join(format!("laya_fsp_file_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let file_path = dir.join("secret.log");

        let old_mask = unsafe { libc::umask(0o000) };
        let mut f = create_file_private(&file_path).unwrap();
        unsafe { libc::umask(old_mask) };
        f.write_all(b"secret").unwrap();
        drop(f);

        assert_eq!(mode(&file_path), 0o600, "new file must be 0600");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    #[cfg(unix)]
    fn set_file_private_tightens_existing_file() {
        let dir = std::env::temp_dir().join(format!("laya_fsp_chmod_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let file_path = dir.join("cfg.json");
        fs::write(&file_path, b"{}").unwrap();
        fs::set_permissions(&file_path, fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(mode(&file_path), 0o644);

        set_file_private(&file_path).unwrap();
        assert_eq!(mode(&file_path), 0o600);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    #[cfg(unix)]
    fn mkdir_private_is_idempotent() {
        let dir = std::env::temp_dir().join(format!("laya_fsp_idem_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        mkdir_private(&dir).unwrap();
        // Second call must succeed without error
        mkdir_private(&dir).unwrap();
        assert_eq!(mode(&dir), 0o700);
        let _ = fs::remove_dir_all(&dir);
    }
}
