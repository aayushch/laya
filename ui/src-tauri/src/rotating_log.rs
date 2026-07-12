// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

//! Size-capped, rotating capture of a child process's stdout/stderr.
//!
//! The engine and n8n write their stdout/stderr to `~/.laya/logs/engine-stdout.log`
//! and `~/.laya/logs/n8n.log`. Previously those sinks were plain `File::create`
//! handles — truncated at each launch but able to grow unbounded within a single
//! long-running session. This module pipes the child's output through a
//! [`RotatingWriter`] that mirrors the Python engine's `RotatingFileHandler`
//! policy (10 MB × N backups) so those files stay bounded.

use std::fs::OpenOptions;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Child;
use std::sync::{Arc, Mutex};
use std::thread;

/// A size-capped log writer. When a write would push the current file past
/// `max_bytes`, the file is rotated (`name` → `name.1` → `name.2` … keeping at
/// most `backups` old files) and a fresh file is opened.
pub struct RotatingWriter {
    dir: PathBuf,
    base: String,
    max_bytes: u64,
    backups: usize,
    file: std::fs::File,
    written: u64,
}

impl RotatingWriter {
    pub fn open(dir: &Path, base: &str, max_bytes: u64, backups: usize) -> io::Result<Self> {
        // Truncate on open so each launch starts with a fresh primary log (matches
        // the previous File::create behavior); rotation then bounds in-session growth.
        let file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(dir.join(base))?;
        Ok(Self {
            dir: dir.to_path_buf(),
            base: base.to_string(),
            max_bytes,
            backups,
            file,
            written: 0,
        })
    }

    fn rotate(&mut self) -> io::Result<()> {
        // Drop the oldest backup, then shift each remaining one up by one index,
        // finally move the live file to `.1`. `remove`/`rename` failures are
        // non-fatal — a missing intermediate backup must not stall logging.
        let _ = std::fs::remove_file(self.dir.join(format!("{}.{}", self.base, self.backups)));
        for i in (1..self.backups).rev() {
            let src = self.dir.join(format!("{}.{}", self.base, i));
            if src.exists() {
                let _ = std::fs::rename(&src, self.dir.join(format!("{}.{}", self.base, i + 1)));
            }
        }
        if self.backups > 0 {
            let _ = std::fs::rename(
                self.dir.join(&self.base),
                self.dir.join(format!("{}.1", self.base)),
            );
        }
        self.file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(self.dir.join(&self.base))?;
        self.written = 0;
        Ok(())
    }
}

impl Write for RotatingWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        // Rotate before writing if this chunk would overflow the cap. The
        // `written > 0` guard means a single chunk larger than `max_bytes` is
        // written whole to a fresh file rather than triggering endless rotation.
        if self.written > 0 && self.written + buf.len() as u64 > self.max_bytes {
            self.rotate()?;
        }
        let n = self.file.write(buf)?;
        self.written += n as u64;
        Ok(n)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.file.flush()
    }
}

/// Redirect a spawned child's stdout + stderr into a shared [`RotatingWriter`].
///
/// The command must have been configured with `Stdio::piped()` for both streams
/// before spawning. Spawns two detached reader threads that copy until the pipes
/// close (child exit). They hold only an `Arc` clone of the writer, so they do not
/// touch the `Child` handle, its process group, or the shutdown/kill path.
pub fn pipe_to_rotating(
    child: &mut Child,
    dir: &Path,
    base: &str,
    max_bytes: u64,
    backups: usize,
) -> io::Result<()> {
    let writer = Arc::new(Mutex::new(RotatingWriter::open(dir, base, max_bytes, backups)?));

    if let Some(stdout) = child.stdout.take() {
        spawn_copy(stdout, writer.clone());
    }
    if let Some(stderr) = child.stderr.take() {
        spawn_copy(stderr, writer.clone());
    }
    Ok(())
}

fn spawn_copy<R: Read + Send + 'static>(mut src: R, writer: Arc<Mutex<RotatingWriter>>) {
    thread::spawn(move || {
        let mut buf = [0u8; 8192];
        loop {
            match src.read(&mut buf) {
                Ok(0) => break, // pipe closed — child exited
                Ok(n) => {
                    if let Ok(mut w) = writer.lock() {
                        let _ = w.write_all(&buf[..n]);
                        let _ = w.flush();
                    }
                }
                Err(ref e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rotates_and_bounds_backups() {
        let dir = std::env::temp_dir().join(format!("laya_rotlog_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let base = "test.log";
        // 100-byte cap, keep 2 backups.
        let mut w = RotatingWriter::open(&dir, base, 100, 2).unwrap();

        // Each write is 60 bytes; the 2nd/3rd/... each trip the cap and rotate.
        let chunk = [b'x'; 60];
        for _ in 0..5 {
            w.write_all(&chunk).unwrap();
            w.flush().unwrap();
        }

        // Primary exists and is at or below the cap (last write started a fresh file).
        let primary = dir.join(base);
        assert!(primary.exists());
        assert!(std::fs::metadata(&primary).unwrap().len() <= 100);

        // At most `backups` rotated files survive; the (backups+1)-th never appears.
        assert!(dir.join(format!("{base}.1")).exists());
        assert!(dir.join(format!("{base}.2")).exists());
        assert!(!dir.join(format!("{base}.3")).exists());

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn oversized_chunk_written_whole_to_fresh_file() {
        let dir = std::env::temp_dir().join(format!("laya_rotlog_big_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let base = "big.log";
        let mut w = RotatingWriter::open(&dir, base, 50, 1).unwrap();

        // A single chunk larger than the cap must be written whole (no endless
        // rotation) since the file was empty.
        let big = vec![b'y'; 200];
        w.write_all(&big).unwrap();
        w.flush().unwrap();
        assert_eq!(std::fs::metadata(dir.join(base)).unwrap().len(), 200);

        std::fs::remove_dir_all(&dir).ok();
    }
}
