# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Centralized filesystem permission policy and enforcement."""

import json
import os
import stat
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

# Intended POSIX permission modes
DIR_MODE = 0o700
FILE_MODE = 0o600


def is_symlink(path: Path | str) -> bool:
    """Return True if path is a symbolic link."""
    try:
        return os.path.islink(path)
    except OSError:
        return False


def secure_directory(
    path: Path | str,
    mode: int = DIR_MODE,
    laya_home: Path | None = None,
) -> Path:
    """Ensure directory exists with restricted permissions (0700).

    Does not follow symbolic links. If the directory already exists, its mode
    is tightened to the specified mode. If the directory is within laya_home,
    intermediate parent directories down from laya_home are also secured.
    """
    p = Path(path)
    if is_symlink(p):
        log.warning("symlink_directory_skipped", path=str(p))
        return p

    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)

    if sys.platform != "win32":
        try:
            st = os.lstat(p)
            if stat.S_ISDIR(st.st_mode):
                current_mode = stat.S_IMODE(st.st_mode)
                if current_mode != mode:
                    os.chmod(p, mode)
        except OSError as e:
            log.warning("secure_directory_failed", path=str(p), error=str(e))
            raise

    if laya_home is None:
        try:
            from laya.config import LAYA_HOME
            laya_home = LAYA_HOME
        except Exception:
            laya_home = None

    if laya_home is not None:
        try:
            home = Path(os.path.abspath(laya_home))
            p_abs = Path(os.path.abspath(p))
            if home == p_abs or home in p_abs.parents:
                cur = p_abs.parent
                while cur != home and home in cur.parents:
                    if not is_symlink(cur) and cur.is_dir() and sys.platform != "win32":
                        st = os.lstat(cur)
                        if stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) != mode:
                            os.chmod(cur, mode)
                    cur = cur.parent
                if cur == home and not is_symlink(cur) and cur.is_dir() and sys.platform != "win32":
                    st = os.lstat(cur)
                    if stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) != mode:
                        os.chmod(cur, mode)
        except Exception:
            pass

    return p


def secure_file(path: Path | str, mode: int = FILE_MODE) -> Path:
    """Ensure a sensitive file has restricted permissions (0600).

    Does not follow symbolic links. If path is a regular file, its mode
    is tightened to the specified mode.
    """
    p = Path(path)
    if is_symlink(p):
        log.warning("symlink_file_skipped", path=str(p))
        return p

    if sys.platform != "win32":
        try:
            st = os.lstat(p)
            if stat.S_ISREG(st.st_mode):
                current_mode = stat.S_IMODE(st.st_mode)
                if current_mode != mode:
                    os.chmod(p, mode)
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("secure_file_failed", path=str(p), error=str(e))
            raise

    return p


def atomic_write_private(
    path: Path | str,
    content: str | bytes,
    encoding: str = "utf-8",
    mode: int = FILE_MODE,
    dir_mode: int = DIR_MODE,
) -> Path:
    """Atomically write content to a file with restricted permissions (0600).

    Uses a temporary file in the same directory, secures its permissions at
    creation time via descriptor, and renames it to the destination.
    Refuses to write through symbolic links.
    """
    p = Path(path)
    if is_symlink(p):
        raise PermissionError(f"Target path is a symbolic link, refusing to overwrite: {p}")

    secure_directory(p.parent, mode=dir_mode)

    tmp_path = p.parent / f".{p.name}.tmp-{uuid.uuid4().hex}"
    data = content.encode(encoding) if isinstance(content, str) else content

    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(tmp_path), flags, mode)
        try:
            if sys.platform != "win32":
                try:
                    os.fchmod(fd, mode)
                except OSError:
                    pass

            total_written = 0
            while total_written < len(data):
                written = os.write(fd, data[total_written:])
                if written == 0:
                    raise OSError(f"Failed to write to temporary file: {tmp_path}")
                total_written += written

            os.fsync(fd)
        finally:
            os.close(fd)

        os.replace(tmp_path, p)
        if sys.platform != "win32":
            secure_file(p, mode=mode)
        return p
    except Exception:
        if tmp_path.exists():
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def write_private_json(path: Path | str, data: Any, indent: int = 2) -> Path:
    """Atomically write JSON data to a file with restricted permissions (0600)."""
    serialized = json.dumps(data, indent=indent) + "\n"
    return atomic_write_private(path, serialized, encoding="utf-8", mode=FILE_MODE)


@contextmanager
def open_private_file(
    path: Path | str,
    mode: str = "w",
    encoding: str | None = None,
    errors: str | None = None,
    file_mode: int = FILE_MODE,
    dir_mode: int = DIR_MODE,
):
    """Context manager for opening a private file with 0600 permissions.

    Refuses to open symbolic links for writing.
    """
    p = Path(path)
    if is_symlink(p):
        raise PermissionError(f"Target path is a symbolic link: {p}")

    secure_directory(p.parent, mode=dir_mode)

    if sys.platform == "win32":
        f = open(p, mode=mode, encoding=encoding, errors=errors)
        try:
            yield f
        finally:
            f.close()
        return

    flags = getattr(os, "O_NOFOLLOW", 0)
    is_write = "w" in mode or "a" in mode or "+" in mode
    if "r" in mode and "+" not in mode:
        flags |= os.O_RDONLY
    elif "w" in mode:
        flags |= os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    elif "a" in mode:
        flags |= os.O_WRONLY | os.O_CREAT | os.O_APPEND
    elif "+" in mode:
        flags |= os.O_RDWR | os.O_CREAT
        if "w" in mode:
            flags |= os.O_TRUNC
        elif "a" in mode:
            flags |= os.O_APPEND

    fd = os.open(str(p), flags, file_mode)
    try:
        if flags & os.O_CREAT:
            try:
                os.fchmod(fd, file_mode)
            except OSError:
                pass
        f = open(fd, mode=mode, encoding=encoding, errors=errors)
    except Exception:
        os.close(fd)
        raise

    try:
        yield f
    finally:
        f.close()
        if is_write and not is_symlink(p) and p.exists():
            secure_file(p, mode=file_mode)


def secure_sqlite_files(db_path: Path | str) -> None:
    """Ensure SQLite database and any existing sidecars have 0600 permissions."""
    p = Path(db_path)
    if sys.platform == "win32":
        return

    if p.exists() and not is_symlink(p):
        secure_file(p, FILE_MODE)

    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = p.with_name(p.name + suffix)
        if sidecar.exists() and not is_symlink(sidecar):
            secure_file(sidecar, FILE_MODE)


def migrate_managed_filesystem_permissions(
    laya_home: Path | None = None,
    force: bool = False,
) -> bool:
    """Enforce restrictive permissions across all Laya-managed files and directories.

    Secures directories to 0700 and sensitive files to 0600.
    Tracks completion via a marker file in the data directory so large document
    trees are not rescanned unnecessarily on every startup.
    Returns True if all operations succeeded, False otherwise.
    """
    if laya_home is None:
        from laya.config import LAYA_HOME
        home = LAYA_HOME
    else:
        home = Path(laya_home)

    data_dir = home / "data"
    logs_dir = home / "logs"
    docs_dir = data_dir / "documents"
    chroma_dir = data_dir / "chromadb"
    tmp_dir = home / "tmp"
    marker = data_dir / ".permissions_migrated"

    all_succeeded = True

    # 1. Enforce top-level directory permissions
    for d in (home, data_dir, logs_dir, docs_dir):
        try:
            secure_directory(d, DIR_MODE, laya_home=home)
        except OSError as e:
            log.warning("migrate_directory_failed", path=str(d), error=str(e))
            all_succeeded = False

    # 2. Enforce known configuration file permissions
    config_files = [
        home / "settings.json",
        home / "team.json",
        home / "rules.json",
        home / "repos.json",
        data_dir / "workflow_versions.json",
    ]
    for cfg in config_files:
        if cfg.exists() and not is_symlink(cfg):
            try:
                secure_file(cfg, FILE_MODE)
            except OSError as e:
                log.warning("migrate_config_file_failed", path=str(cfg), error=str(e))
                all_succeeded = False

    # 3. Enforce SQLite database and sidecars
    db_file = data_dir / "laya.db"
    if db_file.exists():
        try:
            secure_sqlite_files(db_file)
        except OSError as e:
            log.warning("migrate_sqlite_failed", path=str(db_file), error=str(e))
            all_succeeded = False

    # 4. Enforce engine log files
    if logs_dir.exists() and not is_symlink(logs_dir):
        try:
            for log_entry in logs_dir.glob("engine.log*"):
                if not is_symlink(log_entry):
                    secure_file(log_entry, FILE_MODE)
        except OSError as e:
            log.warning("migrate_logs_failed", error=str(e))
            all_succeeded = False

    # 5. Check if deep tree scan is needed
    if marker.exists() and not force:
        return all_succeeded

    # 6. Deep scan managed trees
    for tree_dir in (docs_dir, chroma_dir, tmp_dir):
        if tree_dir.exists() and not is_symlink(tree_dir):
            try:
                secure_directory(tree_dir, DIR_MODE, laya_home=home)
                for root, dirnames, filenames in os.walk(tree_dir, followlinks=False):
                    root_path = Path(root)
                    for dname in dirnames:
                        dpath = root_path / dname
                        if not is_symlink(dpath):
                            try:
                                secure_directory(dpath, DIR_MODE, laya_home=home)
                            except OSError as e:
                                log.warning("migrate_tree_dir_failed", path=str(dpath), error=str(e))
                                all_succeeded = False
                    for fname in filenames:
                        fpath = root_path / fname
                        if not is_symlink(fpath):
                            try:
                                secure_file(fpath, FILE_MODE)
                            except OSError as e:
                                log.warning("migrate_tree_file_failed", path=str(fpath), error=str(e))
                                all_succeeded = False
            except OSError as e:
                log.warning("migrate_tree_walk_failed", path=str(tree_dir), error=str(e))
                all_succeeded = False

    # 7. Record migration completion
    if all_succeeded:
        try:
            write_private_json(marker, {"migrated": True, "version": 1})
        except OSError as e:
            log.warning("migrate_marker_write_failed", error=str(e))
            all_succeeded = False
    else:
        log.warning("permission_migration_incomplete")
        if marker.exists():
            try:
                os.unlink(marker)
            except OSError:
                pass

    return all_succeeded
