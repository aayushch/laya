# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Filesystem permission policy for Laya-managed data (``~/.laya``).

Directories are owner-only (0700) and sensitive files are owner-only (0600).
Three rules keep enforcement cheap and predictable:

1. Everything Laya creates gets its private mode at creation time: directories
   through ``mkdir`` plus an ``fchmod`` on the fresh descriptor, files through
   ``os.open`` plus ``fchmod``.  The ``fchmod`` is what makes the result
   independent of the process umask.
2. Data that existed before this policy is tightened once by
   :func:`migrate_managed_filesystem_permissions`, which records its outcome
   in a marker file so it does not walk large trees on every startup.
3. Ordinary reads and writes never re-check or re-tighten modes.  A mode that
   drifts after the migration (a backup tool, a manual chmod) stays as the
   user left it until the marker file is deleted and the migration re-runs.

Failing to set a mode is never fatal: it is logged and the operation
continues, so Laya still starts on filesystems that do not support POSIX
modes (bind mounts owned by another uid, exFAT, read-only mounts).

Symbolic links: ``~/.laya`` itself may be a symlink to another location (see
:func:`resolve_app_root`).  Write helpers refuse to write through a symlinked
*file*.  The migration never follows symlinks, so it cannot chmod anything
outside the managed tree.  Symlinked directories inside the tree (for example
``data/chromadb`` relocated to a larger disk) are followed on ordinary use.
"""

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

# Intended POSIX permission modes.
DIR_MODE = 0o700
FILE_MODE = 0o600

_IS_WINDOWS = sys.platform == "win32"
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)

# Evaluated at import time so test patches on os.open do not affect it.
_DIR_FD_SUPPORTED: bool = not _IS_WINDOWS and os.open in os.supports_dir_fd


def is_symlink(path: Path | str) -> bool:
    """Return True if path is a symbolic link."""
    try:
        return os.path.islink(path)
    except OSError:
        return False


def resolve_app_root(laya_home: Path) -> Path:
    """Return the real directory behind ``laya_home``.

    Users relocate their data by symlinking ``~/.laya`` elsewhere
    (``~/.laya -> /mnt/storage/laya-data``).  The symlink is resolved once,
    here, and the resolved directory is what every other helper treats as the
    application root.  A plain directory is returned unchanged; a dangling
    symlink resolves to its (future) target so it can be created on first use.

    Raises PermissionError when the symlink points at something that exists
    but is not a directory, because no data can live there.
    """
    if not os.path.islink(str(laya_home)):
        return laya_home

    target = Path(os.path.realpath(laya_home))
    if target.exists() and not target.is_dir():
        raise PermissionError(
            f"Application root symlink target is not a directory: {laya_home} -> {target}"
        )
    return target


def _fchmod_quiet(fd: int, mode: int, path: Path | str) -> None:
    """Apply ``mode`` to an open descriptor; log and continue on failure.

    Used only on descriptors Laya just created, so a failure means the
    filesystem cannot represent POSIX modes rather than a permission problem.
    """
    if _IS_WINDOWS:
        return
    try:
        os.fchmod(fd, mode)
    except OSError as e:
        log.warning("private_mode_not_applied", path=str(path), mode=oct(mode), error=str(e))


def _mkdir_private(p: Path, mode: int) -> None:
    """Create ``p`` and any missing parents, each with ``mode``.

    Every directory is created by ``mkdir`` with the requested mode and then
    ``fchmod``-ed through a freshly opened descriptor, so the result does not
    depend on the umask and no directory is ever briefly world-readable.
    Symlinked existing ancestors are followed; the missing tail is created in
    place.
    """
    missing: list[Path] = []
    cur = Path(os.path.abspath(p))
    while not cur.exists():
        missing.append(cur)
        parent = cur.parent
        if parent == cur:
            break
        cur = parent

    for d in reversed(missing):
        try:
            os.mkdir(d, mode)
        except FileExistsError:
            continue  # created concurrently by another process
        if _IS_WINDOWS:
            continue
        try:
            fd = os.open(d, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
        except OSError as e:
            log.warning("private_mode_not_applied", path=str(d), mode=oct(mode), error=str(e))
            continue
        try:
            _fchmod_quiet(fd, mode, d)
        finally:
            os.close(fd)


def secure_directory(path: Path | str, mode: int = DIR_MODE) -> Path:
    """Ensure ``path`` exists as a directory, creating it privately if missing.

    An existing directory is left exactly as it is (see the module docstring:
    tightening existing data is the migration's job).  Raises
    NotADirectoryError if the path exists but is not a directory.
    """
    p = Path(path)
    try:
        st = os.stat(p)
    except FileNotFoundError:
        _mkdir_private(p, mode)
        return p
    if not stat.S_ISDIR(st.st_mode):
        raise NotADirectoryError(f"Managed path exists but is not a directory: {p}")
    return p


def atomic_write_private(
    path: Path | str,
    content: str | bytes,
    encoding: str = "utf-8",
    mode: int = FILE_MODE,
) -> Path:
    """Atomically replace ``path`` with ``content`` as a private (0600) file.

    The content is written to a uniquely named temporary file in the same
    directory, created with ``mode`` and ``fchmod``-ed, fsynced, then renamed
    over the destination.  The rename keeps the temporary file's mode, so the
    destination is private without a further chmod.  Refuses to write through
    a symbolic link.
    """
    p = Path(path)
    if is_symlink(p):
        raise PermissionError(f"Target path is a symbolic link, refusing to overwrite: {p}")

    secure_directory(p.parent)

    tmp_path = p.parent / f".{p.name}.tmp-{uuid.uuid4().hex}"
    data = content.encode(encoding) if isinstance(content, str) else content

    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW, mode)
        try:
            _fchmod_quiet(fd, mode, tmp_path)
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
        return p
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_private_json(path: Path | str, data: Any, indent: int = 2) -> Path:
    """Atomically write JSON data to a private (0600) file."""
    serialized = json.dumps(data, indent=indent) + "\n"
    return atomic_write_private(path, serialized, encoding="utf-8", mode=FILE_MODE)


@contextmanager
def open_private_file(
    path: Path | str,
    mode: str = "w",
    encoding: str | None = None,
    errors: str | None = None,
    file_mode: int = FILE_MODE,
):
    """Open ``path`` for writing (or reading) as a private (0600) file.

    A file created by this call gets ``file_mode`` at creation time.  An
    existing file keeps whatever mode it has.  Refuses to open a symbolic link.
    ``mode`` accepts the usual ``"w"``, ``"a"``, ``"r"`` and ``"+"`` variants,
    with or without ``"b"``.
    """
    p = Path(path)
    if is_symlink(p):
        raise PermissionError(f"Target path is a symbolic link: {p}")

    secure_directory(p.parent)

    if _IS_WINDOWS:
        with open(p, mode=mode, encoding=encoding, errors=errors) as f:
            yield f
        return

    flags = _O_NOFOLLOW
    if "+" in mode:
        flags |= os.O_RDWR
        if "w" in mode:
            flags |= os.O_CREAT | os.O_TRUNC
        elif "a" in mode:
            flags |= os.O_CREAT | os.O_APPEND
    elif "w" in mode:
        flags |= os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    elif "a" in mode:
        flags |= os.O_WRONLY | os.O_CREAT | os.O_APPEND
    else:
        flags |= os.O_RDONLY

    fd = os.open(str(p), flags, file_mode)
    try:
        if flags & os.O_CREAT:
            _fchmod_quiet(fd, file_mode, p)
        f = open(fd, mode=mode, encoding=encoding, errors=errors)
    except Exception:
        os.close(fd)
        raise

    with f:
        yield f


# ---------------------------------------------------------------------------
# One-time migration of pre-existing data
# ---------------------------------------------------------------------------

# Stable version identifier for the one-time permission migration.
# Changing this value triggers a re-run on the next startup.
MIGRATION_VERSION = "filesystem_permissions_v1"

# Marker file name, relative to the data directory.
MIGRATION_MARKER_NAME = ".permissions_migrated"

# Marker statuses that stop the migration from running again.  Any other
# content (missing, unreadable, another version, or a status such as
# "in_progress" left by an interrupted run) makes the next startup run the
# full migration, which is safe because every step is idempotent.
_TERMINAL_STATUSES = ("completed", "failed")

_MARKER_MAX_BYTES = 4096


def _read_marker_raw(marker: Path) -> bytes | None:
    """Return the marker's bytes, or None if it is missing or untrustworthy.

    On POSIX both the data directory and the marker are opened with
    O_NOFOLLOW, and the marker must be a regular file no larger than
    ``_MARKER_MAX_BYTES``.  Anything else (symlink, directory, oversized)
    reads as "no marker" so it can never suppress the migration.
    """
    if _IS_WINDOWS:
        if not marker.exists() or is_symlink(str(marker)):
            return None
        try:
            return marker.read_bytes()
        except Exception:
            return None

    dir_fd: int | None = None
    marker_fd: int | None = None
    try:
        try:
            dir_fd = os.open(str(marker.parent), os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
        except OSError:
            return None
        try:
            if _DIR_FD_SUPPORTED:
                marker_fd = os.open(marker.name, os.O_RDONLY | _O_NOFOLLOW, dir_fd=dir_fd)
            else:
                marker_fd = os.open(str(marker), os.O_RDONLY | _O_NOFOLLOW)
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
    """Return the status recorded in ``marker`` for ``version``.

    ``"not_started"`` when there is no usable marker, ``"invalid"`` when it is
    unparsable or records another version, otherwise the recorded ``status``
    string.  A marker without a ``status`` key counts as ``"completed"``: that
    format was only ever written after a fully successful run.
    """
    raw = _read_marker_raw(marker)
    if raw is None:
        return "not_started"
    try:
        data = json.loads(raw.decode("utf-8"))
        if data.get("version") != version:
            return "invalid"
        return str(data.get("status", "completed"))
    except Exception:
        return "invalid"


def _write_migration_status(marker: Path, version: str, status: str, failed_count: int = 0) -> None:
    """Atomically write a versioned migration status record to ``marker``.

    Only ``version``, ``status`` and ``failed_count`` belong to this
    migration. Every other top-level key of an existing marker is kept: the
    Tauri shell stores the n8n runtime record under ``n8n_runtime`` in the
    same file (``ui/src-tauri/src/n8n_upgrade.rs``), and losing it would make
    the shell treat n8n as never installed by this Laya version.
    """
    payload: dict = {}
    raw = _read_marker_raw(marker)
    if raw is not None:
        try:
            existing = json.loads(raw.decode("utf-8"))
            if isinstance(existing, dict):
                payload.update(existing)
        except Exception:
            pass
    payload.pop("failed_count", None)
    payload["version"] = version
    payload["status"] = status
    if failed_count:
        payload["failed_count"] = failed_count
    write_private_json(marker, payload)


def migrate_managed_filesystem_permissions(laya_home: Path | None = None) -> bool:
    """Tighten every pre-existing Laya-managed path once; record the outcome.

    Directories become 0700 and sensitive files 0600: the application root,
    ``data/``, ``logs/``, ``n8n/``, the config files, the SQLite database and
    its sidecars, every log file, n8n's credential and database files, and a
    full walk of ``data/documents``, ``data/chromadb`` and ``tmp``.

    The marker file ``data/.permissions_migrated`` holds the outcome:

    - ``completed``: every path was secured; nothing runs on later startups.
    - ``failed``: at least one chmod failed (bind mount owned by another uid,
      exFAT, read-only mount, ...).  Nothing runs on later startups either,
      except a one-line warning naming the marker.  Deleting the marker makes
      the next startup try again.

    A missing, unreadable or foreign marker, or one left as ``in_progress``
    by an interrupted run, means the migration runs in full.  That is safe
    because each step only ``fchmod``s a path whose mode differs from the
    target; nothing is deleted, renamed or rewritten.

    Every chmod goes through a descriptor opened with O_NOFOLLOW, and the tree
    walk opens each child relative to its parent descriptor, so symlinks are
    skipped rather than followed and nothing outside the tree is modified.
    An intentionally symlinked root (``~/.laya -> /mnt/storage``) is resolved
    first by :func:`resolve_app_root`.

    Returns True when the migration is complete, False when it failed now or
    on an earlier run.  Windows has no POSIX modes; the migration is skipped
    there and returns True.
    """
    if _IS_WINDOWS:
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

    try:
        home = resolve_app_root(home)
    except (PermissionError, OSError) as e:
        log.warning("permission_migration_root_unusable", error=str(e))
        return False

    data_dir = home / "data"
    logs_dir = home / "logs"
    docs_dir = data_dir / "documents"
    chroma_dir = data_dir / "chromadb"
    tmp_dir = home / "tmp"
    n8n_dir = home / "n8n"
    marker = data_dir / MIGRATION_MARKER_NAME

    status = _read_migration_status(marker, MIGRATION_VERSION)
    if status == "completed":
        return True
    if status == "failed":
        log.warning(
            "permission_migration_failed_previously",
            marker=str(marker),
            message="A previous run could not secure every managed file. "
                    "Delete the marker file to retry on the next startup.",
        )
        return False

    # The data directory must exist so the marker can be written at the end.
    try:
        secure_directory(data_dir)
    except OSError as e:
        log.warning("permission_migration_data_dir_unusable", path=str(data_dir), error=str(e))
        return False

    failures: list[dict] = []

    def _fail(path: Path, err: OSError, kind: str) -> None:
        failures.append({"path": str(path), "error": str(err), "kind": kind})

    def _fchmod_dir(d: Path) -> None:
        # O_DIRECTORY rejects regular files; O_NOFOLLOW rejects symlinks.
        try:
            fd = os.open(str(d), os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
        except FileNotFoundError:
            return
        except OSError as e:
            if e.errno in (_errno.ELOOP, _errno.ENOTDIR):
                return  # symlink or non-directory: skip
            _fail(d, e, "directory")
            return
        try:
            st = os.fstat(fd)
            if stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) != DIR_MODE:
                os.fchmod(fd, DIR_MODE)
        except OSError as e:
            _fail(d, e, "directory")
        finally:
            os.close(fd)

    def _fchmod_file_fd(fd: int, path: Path) -> None:
        try:
            st = os.fstat(fd)
            if stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) != FILE_MODE:
                os.fchmod(fd, FILE_MODE)
        except OSError as e:
            _fail(path, e, "file")
        finally:
            os.close(fd)

    def _fchmod_file(f: Path) -> None:
        # O_NOFOLLOW rejects a symlink as the final component.  O_NONBLOCK
        # keeps an open on a FIFO from blocking until a writer appears.
        try:
            fd = os.open(str(f), os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK)
        except FileNotFoundError:
            return
        except OSError as e:
            if e.errno == _errno.ELOOP:
                return  # symlink: skip
            _fail(f, e, "file")
            return
        _fchmod_file_fd(fd, f)

    def _secure_tree(parent_fd: int, parent_path: Path) -> None:
        """Secure everything under ``parent_fd``, opening children relative to it."""
        try:
            entries = os.listdir(parent_fd)
        except OSError as e:
            _fail(parent_path, e, "listdir")
            return

        for name in entries:
            child_path = parent_path / name
            # Try as a directory first: ENOTDIR means a regular file, ELOOP a symlink.
            try:
                if _DIR_FD_SUPPORTED:
                    child_fd = os.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd)
                else:
                    child_fd = os.open(str(child_path), os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
            except FileNotFoundError:
                continue
            except OSError as dir_err:
                if dir_err.errno == _errno.ELOOP:
                    continue
                if dir_err.errno != _errno.ENOTDIR:
                    _fail(child_path, dir_err, "open")
                    continue
                try:
                    if _DIR_FD_SUPPORTED:
                        file_fd = os.open(name, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK, dir_fd=parent_fd)
                    else:
                        file_fd = os.open(str(child_path), os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK)
                except FileNotFoundError:
                    continue
                except OSError as e:
                    if e.errno != _errno.ELOOP:
                        _fail(child_path, e, "file_open")
                    continue
                _fchmod_file_fd(file_fd, child_path)
                continue

            try:
                st = os.fstat(child_fd)
                if stat.S_ISDIR(st.st_mode):
                    if stat.S_IMODE(st.st_mode) != DIR_MODE:
                        os.fchmod(child_fd, DIR_MODE)
                    _secure_tree(child_fd, child_path)
            except OSError as e:
                _fail(child_path, e, "directory")
            finally:
                os.close(child_fd)

    # Top-level managed directories.
    for d in (home, data_dir, logs_dir, docs_dir, n8n_dir):
        _fchmod_dir(d)

    # Configuration files.
    for cfg in (
        home / "settings.json",
        home / "team.json",
        home / "rules.json",
        home / "repos.json",
        data_dir / "workflow_versions.json",
    ):
        _fchmod_file(cfg)

    # SQLite database and sidecars.
    db_file = data_dir / "laya.db"
    _fchmod_file(db_file)
    for suffix in ("-wal", "-shm", "-journal"):
        _fchmod_file(Path(str(db_file) + suffix))

    # Every file in logs/: engine.log, engine-stdout.log, n8n.log, rotated backups.
    try:
        logs_fd = os.open(str(logs_dir), os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
    except OSError:
        logs_fd = None
    if logs_fd is not None:
        try:
            for entry_name in os.listdir(logs_fd):
                _fchmod_file(logs_dir / entry_name)
        except OSError as e:
            _fail(logs_dir, e, "log_scan")
        finally:
            os.close(logs_fd)

    # n8n state that holds credentials.
    try:
        n8n_fd = os.open(str(n8n_dir), os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
    except OSError:
        n8n_fd = None
    if n8n_fd is not None:
        try:
            for n8n_file in ("credentials.json", "n8n.sqlite", ".env"):
                _fchmod_file(n8n_dir / n8n_file)
        finally:
            os.close(n8n_fd)

    # Full walk of the large trees.
    for tree_dir in (docs_dir, chroma_dir, tmp_dir):
        try:
            tree_fd = os.open(str(tree_dir), os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
        except FileNotFoundError:
            continue
        except OSError as e:
            if e.errno == _errno.ELOOP:
                continue  # relocated via symlink: outside the managed tree
            _fail(tree_dir, e, "tree_open")
            continue
        try:
            st = os.fstat(tree_fd)
            if stat.S_ISDIR(st.st_mode):
                if stat.S_IMODE(st.st_mode) != DIR_MODE:
                    os.fchmod(tree_fd, DIR_MODE)
                _secure_tree(tree_fd, tree_dir)
        except OSError as e:
            _fail(tree_dir, e, "tree")
        finally:
            os.close(tree_fd)

    succeeded = not failures
    final_status = "completed" if succeeded else "failed"
    try:
        _write_migration_status(marker, MIGRATION_VERSION, final_status, failed_count=len(failures))
    except OSError as e:
        log.warning("permission_migration_marker_write_failed", marker=str(marker), error=str(e))
        succeeded = False

    if not succeeded:
        log.warning(
            "permission_migration_failed",
            marker=str(marker),
            failed_count=len(failures),
            failures=failures[:20],
            message="Some managed files could not be secured. "
                    "Delete the marker file to retry on the next startup.",
        )
    return succeeded
