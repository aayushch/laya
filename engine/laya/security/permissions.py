# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Centralized filesystem permission policy and enforcement."""

import errno as _errno
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

# Evaluated at import time so test patches on os.mkdir do not affect this.
_DIR_FD_SUPPORTED: bool = (
    sys.platform != "win32"
    and os.mkdir in os.supports_dir_fd
    and os.open in os.supports_dir_fd
)


def is_symlink(path: Path | str) -> bool:
    """Return True if path is a symbolic link."""
    try:
        return os.path.islink(path)
    except OSError:
        return False


def _assert_no_symlink_in_path(p: Path, boundary: Path | None = None) -> None:
    """Raise PermissionError if p or any ancestor down to boundary is a symlink.

    The boundary is canonicalized with realpath so that OS-level symlinks above
    it (e.g. macOS /var -> /private/var) are not flagged.  Only components at
    or below the resolved boundary are inspected.  If p is not under the
    boundary, only p itself is checked (not its OS-managed ancestors).
    """
    # Canonicalize the trusted boundary; never resolve p (that defeats detection).
    limit = Path(os.path.realpath(boundary if boundary is not None else Path.home()))
    cur = Path(os.path.abspath(p))
    try:
        cur.relative_to(limit)
        under_boundary = True
    except ValueError:
        under_boundary = False

    while True:
        if os.path.islink(cur):
            raise PermissionError(f"Symlink detected in managed path: {cur}")
        # Stop at boundary, or immediately when p is outside it (only p checked).
        if cur == limit or not under_boundary:
            break
        parent = cur.parent
        if parent == cur:  # filesystem root
            break
        cur = parent


def _mkdir_private(p: Path, mode: int = DIR_MODE, boundary: Path | None = None) -> None:
    """Create p and all missing parents with restricted permissions.

    On POSIX systems that support dir_fd, components at or below the trusted
    boundary are traversed with descriptor-relative os.open(O_NOFOLLOW|O_DIRECTORY)
    so that a symlink inserted mid-traversal raises ELOOP instead of being
    followed.  Components above the boundary (OS-managed, e.g. /var on macOS)
    are opened with symlink-following so that legitimate OS aliases are not
    rejected.  fchmod via the open descriptor bypasses umask.

    Raises PermissionError if any path component at or below the boundary is a
    symlink (ELOOP/ENOTDIR).
    """
    if sys.platform == "win32":
        # Windows lacks O_NOFOLLOW and dir_fd; individual os.mkdir calls.
        to_create: list[Path] = []
        cur = Path(os.path.abspath(p))
        while not cur.exists() and not os.path.islink(cur):
            to_create.append(cur)
            parent = cur.parent
            if parent == cur:
                break
            cur = parent
        for d in reversed(to_create):
            os.mkdir(str(d), mode)
        return

    O_DIR = getattr(os, "O_DIRECTORY", 0)
    O_NF = getattr(os, "O_NOFOLLOW", 0)

    if not _DIR_FD_SUPPORTED:
        raise PermissionError(
            "Platform does not support dir_fd operations required for "
            "TOCTOU-safe managed directory creation"
        )

    # Canonicalize the trusted boundary; resolve OS-level aliases above it.
    limit = Path(os.path.realpath(boundary if boundary is not None else Path.home()))
    target = Path(os.path.abspath(p))
    parts = list(target.parts)  # e.g. ['/', 'home', 'user', '.laya', 'data']

    # above_count: number of parts (including '/') that are AT or ABOVE the boundary.
    # Components with index < above_count get symlink-following opens; at/below get O_NOFOLLOW.
    try:
        target.relative_to(limit)
        above_count = len(limit.parts)  # boundary parts are the "above" prefix
    except ValueError:
        # target is outside the trusted boundary — follow symlinks for all components.
        above_count = len(parts)

    # Open filesystem root — always a real directory.
    fd = os.open(parts[0], os.O_RDONLY | O_DIR)
    for idx, part in enumerate(parts[1:], start=1):
        new_fd: int | None = None
        use_nofollow = idx >= above_count
        open_flags = os.O_RDONLY | O_DIR | (O_NF if use_nofollow else 0)
        try:
            try:
                new_fd = os.open(part, open_flags, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode, dir_fd=fd)
                except FileExistsError:
                    pass  # created concurrently
                new_fd = os.open(part, open_flags, dir_fd=fd)
                os.fchmod(new_fd, mode)
            except OSError as exc:
                if use_nofollow and exc.errno in (_errno.ELOOP, _errno.ENOTDIR):
                    raise PermissionError(
                        f"Symlink or non-directory at managed path component: {part}"
                    ) from exc
                raise
        except BaseException:
            if new_fd is not None:
                try:
                    os.close(new_fd)
                except OSError:
                    pass
            os.close(fd)
            raise
        os.close(fd)
        fd = new_fd  # type: ignore[assignment]
    os.close(fd)


def secure_directory(
    path: Path | str,
    mode: int = DIR_MODE,
    laya_home: Path | None = None,
    boundary: Path | None = None,
) -> Path:
    """Ensure directory exists with restricted permissions (0700).

    Raises PermissionError if path or any of its ancestors at or below the
    trusted boundary is a symlink.  Creates missing directories one component
    at a time using the requested mode so that no intermediate directory is
    ever created with permissive defaults.

    boundary: explicit trusted-root for symlink validation; if omitted, laya_home
    is used when supplied, otherwise it defaults to the resolved user home dir.
    Callers that create directories under a temporary test root should pass that
    root as boundary so OS-level symlinks above it (e.g. macOS /var) are skipped.
    """
    p = Path(path)

    # Resolve laya_home early so it can serve as the symlink-check boundary.
    if laya_home is None:
        try:
            from laya.config import LAYA_HOME
            laya_home = LAYA_HOME
        except Exception:
            pass

    effective_boundary = boundary if boundary is not None else laya_home
    _assert_no_symlink_in_path(p, boundary=effective_boundary)  # raises on symlink below boundary

    if not p.exists():
        _mkdir_private(p, mode, boundary=effective_boundary)

    if sys.platform != "win32":
        O_DIR = getattr(os, "O_DIRECTORY", 0)
        O_NF = getattr(os, "O_NOFOLLOW", 0)
        try:
            # Open with O_NOFOLLOW so a symlink placed between the check above
            # and the chmod below raises ELOOP rather than being followed.
            dfd = os.open(str(p), os.O_RDONLY | O_DIR | O_NF)
            try:
                st = os.fstat(dfd)
                if stat.S_ISDIR(st.st_mode):
                    if stat.S_IMODE(st.st_mode) != mode:
                        os.fchmod(dfd, mode)
            finally:
                os.close(dfd)
        except OSError as e:
            log.warning("secure_directory_failed", path=str(p), error=str(e))
            raise

    if laya_home is not None:
        try:
            home = Path(os.path.abspath(laya_home))
            p_abs = Path(os.path.abspath(p))
            if home == p_abs or home in p_abs.parents:
                cur = p_abs.parent
                while cur != home and home in cur.parents:
                    if cur.is_dir() and sys.platform != "win32":
                        st = os.lstat(cur)
                        if stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) != mode:
                            os.chmod(cur, mode)
                    cur = cur.parent
                if cur == home and cur.is_dir() and sys.platform != "win32":
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


# Stable version identifier for the one-time permission migration.
# Changing this value triggers a re-run on the next startup.
MIGRATION_VERSION = "filesystem_permissions_v1"


def resolve_app_root(laya_home: Path) -> Path:
    """Follow an intentional root-level symlink to its real target directory.

    Supports relocating application data with a symlink such as:
        ~/.laya -> /mnt/storage/laya-data

    Rules:
    - A real directory is returned unchanged.
    - A single-level symlink to a real directory is followed and the resolved
      path is returned as the trusted application root.
    - A symlink chain (symlink -> symlink) is rejected — only one hop is allowed.
    - A symlink pointing to a non-directory is rejected.
    - A non-existent target is returned as-is (it will be created on first use).
    """
    if not os.path.islink(str(laya_home)):
        return laya_home

    raw = os.readlink(str(laya_home))
    target = Path(raw) if Path(raw).is_absolute() else (laya_home.parent / raw)
    target = Path(os.path.abspath(target))

    if os.path.islink(str(target)):
        raise PermissionError(
            f"Symlink chain not allowed at application root: {laya_home} -> {target}"
        )

    if target.exists() and not target.is_dir():
        raise PermissionError(
            f"Application root symlink target is not a directory: {target}"
        )

    return target


_MARKER_MAX_BYTES = 4096


def _read_marker_raw(marker: Path) -> bytes | None:
    """Open marker and its parent with O_NOFOLLOW, return raw bytes or None on any failure.

    Returns None if the marker or its parent directory is a symlink, missing,
    oversized, or not a regular file.  Caller is responsible for JSON parsing.
    """
    if sys.platform == "win32":
        if not marker.exists() or is_symlink(str(marker)):
            return None
        try:
            return marker.read_bytes()
        except Exception:
            return None

    O_NF = getattr(os, "O_NOFOLLOW", 0)
    O_DIR = getattr(os, "O_DIRECTORY", 0)
    data_dir = marker.parent

    dir_fd: int | None = None
    marker_fd: int | None = None
    try:
        try:
            dir_fd = os.open(str(data_dir), os.O_RDONLY | O_DIR | O_NF)
        except OSError:
            return None

        try:
            if _DIR_FD_SUPPORTED:
                marker_fd = os.open(marker.name, os.O_RDONLY | O_NF, dir_fd=dir_fd)
            else:
                marker_fd = os.open(str(marker), os.O_RDONLY | O_NF)
        except OSError:
            return None

        st = os.fstat(marker_fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > _MARKER_MAX_BYTES:
            return None

        return os.read(marker_fd, _MARKER_MAX_BYTES)
    finally:
        for _fd in (marker_fd, dir_fd):
            if _fd is not None:
                try:
                    os.close(_fd)
                except OSError:
                    pass


def _read_migration_status(marker: Path, version: str) -> str:
    """Return the migration status recorded in *marker* for *version*.

    Returns one of: ``"completed"``, ``"incomplete"``, ``"in_progress"``,
    ``"not_started"``, or ``"invalid"``.  A marker without an explicit
    ``status`` key is treated as ``"completed"`` for backward compatibility
    with the earlier single-state format.
    """
    raw = _read_marker_raw(marker)
    if raw is None:
        return "not_started"
    try:
        data = json.loads(raw.decode("utf-8"))
        if data.get("version") != version:
            return "invalid"
        # Legacy markers (no "status" key) were written only on full success.
        return data.get("status", "completed")
    except Exception:
        return "invalid"


def _read_migration_marker(marker: Path, version: str) -> bool:
    """Return True iff the marker records successful completion of *version*.

    On POSIX, opens the parent data directory then the marker file both with
    O_NOFOLLOW so a symlink inserted between check and open is rejected rather
    than followed.  Verifies the descriptor is a regular file, checks the
    size, reads the bytes, and validates the JSON.

    A symlinked, oversized, non-regular-file, or malformed marker never causes
    migration to be skipped.
    """
    return _read_migration_status(marker, version) == "completed"


def _write_migration_status(marker: Path, version: str, status: str, failed_count: int = 0) -> None:
    """Atomically write a versioned migration status record to *marker*."""
    payload: dict = {"version": version, "status": status}
    if failed_count:
        payload["failed_count"] = failed_count
    write_private_json(marker, payload)


def migrate_managed_filesystem_permissions(
    laya_home: Path | None = None,
    force: bool = False,
) -> bool:
    """One-time upgrade: enforce restrictive permissions across Laya-managed files.

    Secures directories to 0700 and sensitive files to 0600.  The marker file
    ``data/.permissions_migrated`` records the migration status so the deep
    scan does not repeat on normal startups.

    Migration states:
      - ``completed``  — fully succeeded; skip on next startup.
      - ``incomplete`` — some chmods failed (NFS, bind-mount); log warning,
                         skip deep scan, retry with ``force=True``.
      - ``in_progress``— previous run was interrupted; require repair.
      - ``not_started``— first run; execute full migration.

    All chmod operations use ``O_NOFOLLOW + os.fchmod`` so a symlink injected
    between path discovery and permission enforcement is rejected rather than
    followed.  Symlinked paths inside the managed tree are silently skipped.

    Supports an intentionally symlinked application root such as
    ``~/.laya -> /mnt/storage/laya-data`` via :func:`resolve_app_root`.

    Returns True if the migration is complete or was skipped successfully,
    False if it remains incomplete.
    """
    # POSIX-specific migration: Windows lacks O_NOFOLLOW and ACL management
    # is outside the scope of this migration.  Functionality is preserved.
    if sys.platform == "win32":
        log.info(
            "permission_migration_skipped_windows",
            message="POSIX filesystem permission migration is not applicable on Windows.",
        )
        return True

    if laya_home is None:
        from laya.config import LAYA_HOME
        home = LAYA_HOME
    else:
        home = Path(laya_home)

    # Follow an intentional root-level symlink (e.g. ~/.laya -> /mnt/storage).
    # Security failures here (chains, non-directory targets) abort the migration.
    try:
        home = resolve_app_root(home)
    except (PermissionError, OSError) as e:
        log.warning("migration_root_resolution_failed", error=str(e))
        return False

    data_dir = home / "data"
    logs_dir = home / "logs"
    docs_dir = data_dir / "documents"
    chroma_dir = data_dir / "chromadb"
    tmp_dir = home / "tmp"
    n8n_dir = home / "n8n"
    marker = data_dir / ".permissions_migrated"

    O_NF = getattr(os, "O_NOFOLLOW", 0)
    O_DIR = getattr(os, "O_DIRECTORY", 0)

    # --- Status check (before any filesystem mutation) ---
    run_deep_scan = True
    if not force:
        status = _read_migration_status(marker, MIGRATION_VERSION)
        if status == "completed":
            return True
        if status == "in_progress":
            log.warning(
                "permission_migration_interrupted",
                message="A previous migration was interrupted mid-run. "
                        "Use force=True to re-run the full migration.",
            )
            return False
        if status == "incomplete":
            log.warning(
                "permission_migration_incomplete_previous",
                message="A previous migration completed with errors; "
                        "skipping deep scan. Use force=True to retry everything.",
            )
            run_deep_scan = False
        # else: "not_started" or "invalid" — run full migration

    # Ensure the data directory exists with restricted permissions before
    # writing the in_progress marker.
    try:
        secure_directory(data_dir, DIR_MODE, laya_home=home)
    except (PermissionError, OSError) as e:
        log.warning("migration_data_dir_init_failed", path=str(data_dir), error=str(e))
        return False

    # Mark as in_progress so a crash mid-migration is detectable next startup.
    try:
        _write_migration_status(marker, MIGRATION_VERSION, "in_progress")
    except OSError as e:
        log.warning("migrate_marker_in_progress_write_failed", error=str(e))
        # Non-fatal: proceed without the in_progress sentinel.

    all_succeeded = True
    failures: list[dict] = []

    # --- fd-based chmod helpers (close lstat→chmod TOCTOU window) ---

    def _try_fchmod_dir(d: Path) -> None:
        nonlocal all_succeeded
        # O_DIRECTORY rejects regular files; O_NOFOLLOW rejects symlinks.
        try:
            fd = os.open(str(d), os.O_RDONLY | O_DIR | O_NF)
        except FileNotFoundError:
            return
        except OSError as e:
            if e.errno in (_errno.ELOOP, _errno.ENOTDIR):
                return  # symlink or non-directory — skip
            failures.append({"path": str(d), "error": str(e), "kind": "directory"})
            all_succeeded = False
            return
        try:
            st = os.fstat(fd)
            if stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) != DIR_MODE:
                os.fchmod(fd, DIR_MODE)
        except OSError as e:
            failures.append({"path": str(d), "error": str(e), "kind": "directory"})
            all_succeeded = False
        finally:
            os.close(fd)

    def _try_fchmod_file(f: Path) -> None:
        nonlocal all_succeeded
        # O_NOFOLLOW rejects symlinks as the final path component.
        try:
            fd = os.open(str(f), os.O_RDONLY | O_NF)
        except FileNotFoundError:
            return
        except OSError as e:
            if e.errno == _errno.ELOOP:
                return  # symlink — skip
            failures.append({"path": str(f), "error": str(e), "kind": "file"})
            all_succeeded = False
            return
        try:
            st = os.fstat(fd)
            if stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) != FILE_MODE:
                os.fchmod(fd, FILE_MODE)
        except OSError as e:
            failures.append({"path": str(f), "error": str(e), "kind": "file"})
            all_succeeded = False
        finally:
            os.close(fd)

    def _secure_tree_fd(parent_fd: int, parent_path: Path) -> None:
        """Recursively secure *parent_fd* using descriptor-relative opens.

        Every child is opened relative to the already-validated parent
        descriptor, so no path component is traversed without being checked.
        Symlinks are rejected by O_NOFOLLOW rather than followed.
        """
        nonlocal all_succeeded
        try:
            entries = os.listdir(parent_fd)
        except OSError as e:
            failures.append({"path": str(parent_path), "error": str(e), "kind": "listdir"})
            all_succeeded = False
            return

        for name in entries:
            child_path = parent_path / name

            # Try to open as a directory first.
            # ENOTDIR → regular file; ELOOP → symlink (skip).
            try:
                if _DIR_FD_SUPPORTED:
                    child_fd = os.open(name, os.O_RDONLY | O_DIR | O_NF, dir_fd=parent_fd)
                else:
                    child_fd = os.open(str(child_path), os.O_RDONLY | O_DIR | O_NF)
            except FileNotFoundError:
                continue
            except OSError as dir_err:
                if dir_err.errno == _errno.ELOOP:
                    continue  # symlink — skip without recording a failure
                if dir_err.errno != _errno.ENOTDIR:
                    failures.append({"path": str(child_path), "error": str(dir_err), "kind": "open"})
                    all_succeeded = False
                    continue
                # ENOTDIR: open as a regular file
                try:
                    if _DIR_FD_SUPPORTED:
                        file_fd = os.open(name, os.O_RDONLY | O_NF, dir_fd=parent_fd)
                    else:
                        file_fd = os.open(str(child_path), os.O_RDONLY | O_NF)
                except FileNotFoundError:
                    continue
                except OSError as e:
                    if e.errno == _errno.ELOOP:
                        continue
                    failures.append({"path": str(child_path), "error": str(e), "kind": "file_open"})
                    all_succeeded = False
                    continue
                try:
                    st = os.fstat(file_fd)
                    if stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) != FILE_MODE:
                        os.fchmod(file_fd, FILE_MODE)
                except OSError as e:
                    failures.append({"path": str(child_path), "error": str(e), "kind": "file"})
                    all_succeeded = False
                finally:
                    os.close(file_fd)
                continue

            # It is a directory.
            try:
                st = os.fstat(child_fd)
                if stat.S_ISDIR(st.st_mode):
                    if stat.S_IMODE(st.st_mode) != DIR_MODE:
                        os.fchmod(child_fd, DIR_MODE)
                    _secure_tree_fd(child_fd, child_path)
            except OSError as e:
                failures.append({"path": str(child_path), "error": str(e), "kind": "directory"})
                all_succeeded = False
            finally:
                os.close(child_fd)

    # --- Shallow scan: known managed paths ---

    # 1. Top-level managed directories (including n8n data directory)
    for d in (home, data_dir, logs_dir, docs_dir, n8n_dir):
        _try_fchmod_dir(d)

    # 2. Known configuration files
    for cfg in (
        home / "settings.json",
        home / "team.json",
        home / "rules.json",
        home / "repos.json",
        data_dir / "workflow_versions.json",
    ):
        _try_fchmod_file(cfg)

    # 3. SQLite database and sidecars
    db_file = data_dir / "laya.db"
    _try_fchmod_file(db_file)
    for suffix in ("-wal", "-shm", "-journal"):
        _try_fchmod_file(Path(str(db_file) + suffix))

    # 4. All log files in the logs directory — engine.log, engine-stdout.log,
    #    n8n.log, and any rotated backups — without hardcoding filenames.
    try:
        logs_fd = os.open(str(logs_dir), os.O_RDONLY | O_DIR | O_NF)
    except OSError:
        logs_fd = None
    if logs_fd is not None:
        try:
            for entry_name in os.listdir(logs_fd):
                _try_fchmod_file(logs_dir / entry_name)
        except OSError as e:
            failures.append({"path": str(logs_dir), "error": str(e), "kind": "log_scan"})
            all_succeeded = False
        finally:
            os.close(logs_fd)

    # 5. n8n sensitive state files (credentials, database, environment)
    try:
        n8n_fd = os.open(str(n8n_dir), os.O_RDONLY | O_DIR | O_NF)
    except OSError:
        n8n_fd = None
    if n8n_fd is not None:
        try:
            for n8n_file in ("credentials.json", "n8n.sqlite", ".env"):
                _try_fchmod_file(n8n_dir / n8n_file)
        finally:
            os.close(n8n_fd)

    # --- Deep scan: descriptor-relative traversal of large managed trees ---
    # Skipped when a previous migration already ran but left some failures;
    # avoids re-scanning large trees on every startup.  Use force=True to retry.
    if run_deep_scan:
        for tree_dir in (docs_dir, chroma_dir, tmp_dir):
            try:
                tree_fd = os.open(str(tree_dir), os.O_RDONLY | O_DIR | O_NF)
            except FileNotFoundError:
                continue
            except OSError as e:
                if e.errno == _errno.ELOOP:
                    continue  # symlink root — skip
                failures.append({"path": str(tree_dir), "error": str(e), "kind": "deep_scan_open"})
                all_succeeded = False
                continue
            try:
                st = os.fstat(tree_fd)
                if stat.S_ISDIR(st.st_mode):
                    if stat.S_IMODE(st.st_mode) != DIR_MODE:
                        os.fchmod(tree_fd, DIR_MODE)
                    _secure_tree_fd(tree_fd, tree_dir)
            except OSError as e:
                failures.append({"path": str(tree_dir), "error": str(e), "kind": "deep_scan"})
                all_succeeded = False
            finally:
                os.close(tree_fd)

    # --- Write final migration status ---
    final_status = "completed" if all_succeeded else "incomplete"
    try:
        _write_migration_status(marker, MIGRATION_VERSION, final_status, failed_count=len(failures))
    except OSError as e:
        log.warning("migrate_marker_write_failed", error=str(e))
        all_succeeded = False

    if not all_succeeded:
        log.warning(
            "permission_migration_incomplete",
            failed_count=len(failures),
            failures=failures[:20],
        )

    return all_succeeded
