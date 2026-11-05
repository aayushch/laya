# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Filesystem permission policy tests (issue #61).

Covers the three rules in ``laya/security/permissions.py``: private modes at
creation time, a one-time migration for pre-existing data, and no
re-tightening on ordinary use.
"""

import contextlib
import json
import logging
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import pytest

import laya.config as config_module
from laya.config import (
    DEFAULT_REPOS,
    DEFAULT_RULES,
    DEFAULT_SETTINGS,
    DEFAULT_TEAM,
    ensure_directories,
    load_settings,
    save_repos,
    save_rules,
    save_settings,
    save_team,
)
from laya.db.sqlite import connect as sqlite_connect, disconnect as sqlite_disconnect
from laya.logging_setup import PrivateRotatingFileHandler
from laya.security.permissions import (
    DIR_MODE,
    FILE_MODE,
    MIGRATION_MARKER_NAME,
    MIGRATION_VERSION,
    _read_migration_status,
    atomic_write_private,
    migrate_managed_filesystem_permissions,
    open_private_file,
    resolve_app_root,
    secure_directory,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX filesystem permissions tests require a Unix-like system",
)


@contextlib.contextmanager
def umask_context(mask: int):
    """Temporarily apply a process umask and guarantee restoration."""
    old_mask = os.umask(mask)
    try:
        yield
    finally:
        os.umask(old_mask)


def get_mode(path: Path | str) -> int:
    """Return the permission bits of path without dereferencing symbolic links."""
    return stat.S_IMODE(os.lstat(path).st_mode)


def walk_modes(root: Path) -> list[tuple[Path, int]]:
    """Return (path, mode) for every non-symlink entry under root, root included."""
    out: list[tuple[Path, int]] = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        dp = Path(dirpath)
        if not os.path.islink(dp):
            out.append((dp, get_mode(dp)))
        for fname in filenames:
            fp = dp / fname
            if not os.path.islink(fp):
                out.append((fp, get_mode(fp)))
    return out


def write_marker(data_dir: Path, status: str | None, version: str = MIGRATION_VERSION) -> Path:
    """Write a migration marker directly, bypassing the helpers under test."""
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / MIGRATION_MARKER_NAME
    payload: dict = {"version": version}
    if status is not None:
        payload["status"] = status
    marker.write_text(json.dumps(payload))
    os.chmod(marker, 0o600)
    return marker


def patch_config_paths(home: Path):
    """Point every laya.config path at ``home``; returns the patch list and path dict."""
    data_dir = home / "data"
    logs_dir = home / "logs"
    docs_dir = data_dir / "documents"
    db_path = data_dir / "laya.db"
    paths = {
        "home": home,
        "data_dir": data_dir,
        "logs_dir": logs_dir,
        "docs_dir": docs_dir,
        "db_path": db_path,
        "config_file": home / "settings.json",
        "team_file": home / "team.json",
        "rules_file": home / "rules.json",
        "repos_file": home / "repos.json",
    }
    patches = [
        patch("laya.config.LAYA_HOME", home),
        patch("laya.config.LAYA_DATA_DIR", data_dir),
        patch("laya.config.LAYA_LOG_DIR", logs_dir),
        patch("laya.config.DOCUMENTS_DIR", docs_dir),
        patch("laya.config.DB_PATH", db_path),
        patch("laya.config.LAYA_CONFIG_FILE", paths["config_file"]),
        patch("laya.config.LAYA_TEAM_FILE", paths["team_file"]),
        patch("laya.config.LAYA_RULES_FILE", paths["rules_file"]),
        patch("laya.config.LAYA_REPOS_FILE", paths["repos_file"]),
        patch("laya.db.sqlite.DB_PATH", db_path),
        patch("laya.logging_setup.LAYA_LOG_DIR", logs_dir),
    ]
    return patches, paths


@pytest.fixture
def isolated_env(tmp_path):
    """Isolated Laya directory tree with every config path patched to it."""
    patches, paths = patch_config_paths(tmp_path / ".laya")
    for p in patches:
        p.start()
    try:
        yield paths
    finally:
        for p in patches:
            p.stop()


class TestCreationTimeModes:
    """Rule 1: everything Laya creates is private from the moment it exists."""

    def test_a_new_directory_creation(self, isolated_env):
        """With umask 022, newly created Laya directories are 0700."""
        with umask_context(0o022):
            ensure_directories()

        for key in ("home", "data_dir", "logs_dir", "docs_dir"):
            assert get_mode(isolated_env[key]) == DIR_MODE

    def test_b_new_sensitive_file_creation(self, isolated_env):
        """Newly created configuration files are 0600."""
        with umask_context(0o022):
            save_settings(DEFAULT_SETTINGS)
            save_team(DEFAULT_TEAM)
            save_rules(DEFAULT_RULES)
            save_repos(DEFAULT_REPOS)

        for key in ("config_file", "team_file", "rules_file", "repos_file"):
            assert get_mode(isolated_env[key]) == FILE_MODE

    def test_e_configuration_overwrites(self, isolated_env):
        """Re-saving a configuration file yields a 0600 file even if it had drifted."""
        save_settings(DEFAULT_SETTINGS)
        assert get_mode(isolated_env["config_file"]) == FILE_MODE

        os.chmod(isolated_env["config_file"], 0o644)
        updated = dict(DEFAULT_SETTINGS)
        updated["setup_complete"] = True
        save_settings(updated)

        assert get_mode(isolated_env["config_file"]) == FILE_MODE
        assert load_settings()["setup_complete"] is True

    @pytest.mark.asyncio
    async def test_f_sqlite_database_and_sidecars(self, isolated_env):
        """A new database is 0600 and SQLite gives its WAL/SHM sidecars the same mode.

        No code tightens the sidecars; this proves the mode inheritance the
        policy relies on.
        """
        with umask_context(0o022):
            db = await sqlite_connect()
            try:
                assert get_mode(isolated_env["db_path"]) == FILE_MODE

                await db.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, val TEXT)")
                await db.commit()
                # An uncommitted write keeps the WAL and SHM files present.
                await db.execute("INSERT INTO t (val) VALUES ('secure_data')")

                wal_file = isolated_env["db_path"].with_name("laya.db-wal")
                shm_file = isolated_env["db_path"].with_name("laya.db-shm")
                assert wal_file.exists() and shm_file.exists()
                assert get_mode(wal_file) == FILE_MODE
                assert get_mode(shm_file) == FILE_MODE

                await db.commit()
                async with db.execute("SELECT val FROM t WHERE id = 1") as cursor:
                    row = await cursor.fetchone()
                    assert row[0] == "secure_data"
            finally:
                await sqlite_disconnect()

    def test_i_modes_under_different_umasks(self, tmp_path):
        """Every creating helper yields 0700/0600 regardless of the process umask."""
        for i, mask in enumerate((0o022, 0o077, 0o000, 0o027)):
            home = tmp_path / f"laya_{i}"
            atomic_file = home / "atomic.json"
            stream_file = home / "nested" / "stream.bin"

            with umask_context(mask):
                secure_directory(home)
                atomic_write_private(atomic_file, '{"test": true}')
                with open_private_file(stream_file, "wb") as f:
                    f.write(b"data")

            assert get_mode(home) == DIR_MODE
            assert get_mode(home / "nested") == DIR_MODE
            assert get_mode(atomic_file) == FILE_MODE
            assert get_mode(stream_file) == FILE_MODE

    def test_j_log_files_and_rotation(self, isolated_env):
        """Initial and rotated log files are 0600 under a permissive umask."""
        log_dir = isolated_env["logs_dir"]
        log_dir.mkdir(parents=True)
        log_file = log_dir / "engine.log"

        with umask_context(0o022):
            handler = PrivateRotatingFileHandler(log_file, maxBytes=200, backupCount=3, encoding="utf-8")
            logger = logging.getLogger("test_perm_logger")
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)
            for i in range(20):
                logger.info("A" * 60 + f" line {i}")
            handler.flush()
            handler.close()
            logger.removeHandler(handler)

        assert get_mode(log_file) == FILE_MODE
        rotated = list(log_dir.glob("engine.log.*"))
        assert rotated
        for rf in rotated:
            assert get_mode(rf) == FILE_MODE

    def test_o_directory_mode_at_creation_time(self, tmp_path):
        """os.mkdir receives 0700 directly; no directory is ever created permissive."""
        new_dir = tmp_path / "a" / "b" / "private_dir"
        mkdir_modes: list[int] = []
        original_mkdir = os.mkdir

        def spy_mkdir(path, mode=0o777, *, dir_fd=None):
            mkdir_modes.append(mode)
            original_mkdir(path, mode, dir_fd=dir_fd) if dir_fd is not None else original_mkdir(path, mode)

        with umask_context(0o000), patch("os.mkdir", spy_mkdir):
            secure_directory(new_dir)

        assert len(mkdir_modes) == 3
        assert all(m == DIR_MODE for m in mkdir_modes)
        assert get_mode(new_dir) == DIR_MODE
        assert get_mode(new_dir.parent) == DIR_MODE

    def test_t_mode_failure_is_not_fatal(self, tmp_path):
        """When fchmod fails (exFAT, foreign-uid bind mount) the operation still succeeds."""
        target_dir = tmp_path / "home" / "data"
        target_file = target_dir / "settings.json"
        stream_file = target_dir / "upload.bin"

        def failing_fchmod(fd, mode):
            raise PermissionError("Operation not permitted")

        with patch("os.fchmod", failing_fchmod):
            secure_directory(target_dir)
            atomic_write_private(target_file, "{}")
            with open_private_file(stream_file, "wb") as f:
                f.write(b"x")

        assert target_dir.is_dir()
        assert target_file.read_text() == "{}"
        assert stream_file.read_bytes() == b"x"


class TestNoRetightenOnUse:
    """Rule 3: ordinary reads and writes never chmod existing paths."""

    def test_n2_existing_directory_left_alone(self, tmp_path):
        """secure_directory on an existing directory issues no chmod and keeps its mode."""
        d = tmp_path / "existing"
        d.mkdir(mode=0o755)
        os.chmod(d, 0o755)

        with patch("os.chmod") as chmod, patch("os.fchmod") as fchmod:
            assert secure_directory(d) == d

        chmod.assert_not_called()
        fchmod.assert_not_called()
        assert get_mode(d) == 0o755

    def test_s_existing_file_at_directory_path_rejected(self, tmp_path):
        """A regular file where a managed directory should be is an error, not silently used."""
        f = tmp_path / "data"
        f.write_text("not a directory")
        with pytest.raises(NotADirectoryError):
            secure_directory(f)

    def test_u_ensure_directories_is_memoized(self, isolated_env):
        """After one successful call, ensure_directories does not touch the filesystem."""
        ensure_directories()
        with patch.object(config_module, "secure_directory") as spy:
            ensure_directories()
            ensure_directories()
        spy.assert_not_called()

    def test_v_ensure_directories_rechecks_when_paths_change(self, tmp_path):
        """Repointing the config paths invalidates the memo."""
        patches_a, paths_a = patch_config_paths(tmp_path / "a")
        patches_b, paths_b = patch_config_paths(tmp_path / "b")

        for p in patches_a:
            p.start()
        try:
            ensure_directories()
        finally:
            for p in patches_a:
                p.stop()
        assert paths_a["docs_dir"].is_dir()

        for p in patches_b:
            p.start()
        try:
            ensure_directories()
        finally:
            for p in patches_b:
                p.stop()
        assert paths_b["docs_dir"].is_dir()


class TestSymlinks:
    def test_g_symbolic_link_safety(self, isolated_env, tmp_path):
        """Symlinked files are never written through, and the migration never follows links."""
        outside_target = tmp_path / "outside_target.txt"
        outside_target.write_text("external data")
        os.chmod(outside_target, 0o644)
        outside_dir = tmp_path / "outside_dir"
        outside_dir.mkdir()
        os.chmod(outside_dir, 0o755)

        ensure_directories()
        symlink_file = isolated_env["home"] / "settings.json"
        symlink_dir = isolated_env["data_dir"] / "documents_link"
        os.symlink(outside_target, symlink_file)
        os.symlink(outside_dir, symlink_dir)

        assert migrate_managed_filesystem_permissions(isolated_env["home"]) is True
        assert get_mode(outside_target) == 0o644
        assert get_mode(outside_dir) == 0o755

        with pytest.raises(PermissionError):
            atomic_write_private(symlink_file, "malicious payload")
        with pytest.raises(PermissionError):
            with open_private_file(symlink_file, "w") as f:
                f.write("malicious payload")
        assert outside_target.read_text() == "external data"

    def test_m_symlinked_home_works_end_to_end(self, tmp_path):
        """A relocated ~/.laya (symlink) resolves to its target and every helper works there."""
        storage = tmp_path / "external_storage" / "laya-data"
        storage.mkdir(parents=True)
        sym_home = tmp_path / ".laya"
        os.symlink(storage, sym_home)

        resolved = resolve_app_root(sym_home)
        assert resolved == storage

        patches, paths = patch_config_paths(resolved)
        for p in patches:
            p.start()
        try:
            with umask_context(0o022):
                ensure_directories()
                save_settings(DEFAULT_SETTINGS)
            assert migrate_managed_filesystem_permissions(sym_home) is True
        finally:
            for p in patches:
                p.stop()

        assert (storage / "data" / "documents").is_dir()
        assert get_mode(storage / "data") == DIR_MODE
        assert get_mode(storage / "settings.json") == FILE_MODE
        assert (storage / "data" / MIGRATION_MARKER_NAME).exists()

    def test_n_symlinked_managed_subdirectory_is_followed(self, tmp_path):
        """A symlinked directory inside the tree (e.g. chromadb on another disk) keeps working."""
        real_chroma = tmp_path / "bigdisk" / "chromadb"
        real_chroma.mkdir(parents=True)
        home = tmp_path / ".laya"
        data_dir = home / "data"
        data_dir.mkdir(parents=True)
        os.symlink(real_chroma, data_dir / "chromadb")

        assert secure_directory(data_dir / "chromadb") == data_dir / "chromadb"
        with umask_context(0o022):
            secure_directory(data_dir / "chromadb" / "index")
        assert (real_chroma / "index").is_dir()
        assert get_mode(real_chroma / "index") == DIR_MODE

    def test_r_directory_outside_home_accepted(self, tmp_path):
        """secure_directory works on any path, including temp dirs behind OS symlinks (/var)."""
        laya_tmp = tmp_path / "laya_data"
        assert secure_directory(laya_tmp) == laya_tmp
        assert get_mode(laya_tmp) == DIR_MODE

    def test_w_resolve_app_root_real_dir_unchanged(self, tmp_path):
        real = tmp_path / "laya"
        real.mkdir()
        assert resolve_app_root(real) == real

    def test_x_resolve_app_root_follows_symlink(self, tmp_path):
        storage = tmp_path / "storage" / "laya-data"
        storage.mkdir(parents=True)
        sym = tmp_path / ".laya"
        os.symlink(storage, sym)
        resolved = resolve_app_root(sym)
        assert resolved == storage
        assert not os.path.islink(resolved)

    def test_x2_resolve_app_root_follows_chain(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        mid = tmp_path / "mid"
        os.symlink(real, mid)
        chain = tmp_path / ".laya"
        os.symlink(mid, chain)
        assert resolve_app_root(chain) == real

    def test_x3_resolve_app_root_rejects_non_directory_target(self, tmp_path):
        file_target = tmp_path / "notadir"
        file_target.write_text("data")
        sym = tmp_path / ".laya"
        os.symlink(file_target, sym)
        with pytest.raises(PermissionError):
            resolve_app_root(sym)

    def test_x4_resolve_app_root_missing_target_allowed(self, tmp_path):
        missing = tmp_path / "future_storage"
        sym = tmp_path / ".laya"
        os.symlink(missing, sym)
        assert resolve_app_root(sym) == missing

    @pytest.mark.asyncio
    async def test_p_sqlite_symlink_rejection(self, isolated_env, tmp_path):
        """Database init fails when DB_PATH is a symlink; the external database is untouched."""
        external_db = tmp_path / "external.db"
        ext_conn = await aiosqlite.connect(str(external_db))
        await ext_conn.execute("CREATE TABLE secret (val TEXT)")
        await ext_conn.execute("INSERT INTO secret VALUES ('external_data')")
        await ext_conn.commit()
        await ext_conn.close()

        isolated_env["data_dir"].mkdir(parents=True, exist_ok=True)
        db_symlink = isolated_env["db_path"]
        os.symlink(external_db, db_symlink)

        with patch("laya.db.sqlite.DB_PATH", db_symlink):
            with pytest.raises(PermissionError, match="symbolic link"):
                await sqlite_connect()

        check_conn = await aiosqlite.connect(str(external_db))
        async with check_conn.execute("SELECT val FROM secret") as cur:
            row = await cur.fetchone()
            assert row is not None and row[0] == "external_data"
        await check_conn.close()

    def test_au_migration_never_chmods_symlink_target(self, tmp_path):
        home = tmp_path / ".laya"
        (home / "data").mkdir(parents=True)
        external = tmp_path / "external_secret.txt"
        external.write_text("sensitive")
        os.chmod(external, 0o644)
        os.symlink(external, home / "settings.json")

        migrate_managed_filesystem_permissions(home)
        assert get_mode(external) == 0o644

    def test_ad_nested_symlink_skipped_not_followed(self, tmp_path):
        home = tmp_path / ".laya"
        docs_dir = home / "data" / "documents"
        docs_dir.mkdir(parents=True)
        external = tmp_path / "sensitive.txt"
        external.write_text("external content")
        os.chmod(external, 0o644)
        os.symlink(external, docs_dir / "secret_link.txt")

        assert migrate_managed_filesystem_permissions(home) is True
        assert get_mode(external) == 0o644


class TestMigration:
    """Rule 2: pre-existing data is tightened once, outcome recorded in the marker."""

    def test_c_existing_permissive_directories(self, isolated_env):
        for key in ("home", "data_dir", "logs_dir", "docs_dir"):
            isolated_env[key].mkdir(parents=True, exist_ok=True)
            os.chmod(isolated_env[key], 0o755)

        ensure_directories()  # must not fail on, or tighten, existing directories
        assert get_mode(isolated_env["home"]) == 0o755
        assert migrate_managed_filesystem_permissions(isolated_env["home"]) is True

        for key in ("home", "data_dir", "logs_dir", "docs_dir"):
            assert get_mode(isolated_env[key]) == DIR_MODE

    def test_d_existing_permissive_files(self, isolated_env):
        isolated_env["home"].mkdir(parents=True)
        content = json.dumps({"models": {"router": "test-router"}}, indent=2)
        isolated_env["config_file"].write_text(content, encoding="utf-8")
        os.chmod(isolated_env["config_file"], 0o644)

        assert migrate_managed_filesystem_permissions(isolated_env["home"]) is True
        assert get_mode(isolated_env["config_file"]) == FILE_MODE
        assert json.loads(isolated_env["config_file"].read_text())["models"]["router"] == "test-router"

    def test_h_idempotency(self, isolated_env):
        """Running the migration repeatedly changes nothing but modes, and only once."""
        save_settings(DEFAULT_SETTINGS)
        save_team(DEFAULT_TEAM)
        before_settings = isolated_env["config_file"].read_text()
        before_team = isolated_env["team_file"].read_text()
        marker = isolated_env["data_dir"] / MIGRATION_MARKER_NAME

        for _ in range(5):
            marker.unlink(missing_ok=True)
            assert migrate_managed_filesystem_permissions(isolated_env["home"]) is True
            assert get_mode(isolated_env["home"]) == DIR_MODE
            assert get_mode(isolated_env["config_file"]) == FILE_MODE
            assert get_mode(isolated_env["team_file"]) == FILE_MODE

        assert isolated_env["config_file"].read_text() == before_settings
        assert isolated_env["team_file"].read_text() == before_team

    def test_k_migration_deep_scan_and_marker(self, isolated_env):
        docs_sub = isolated_env["docs_dir"] / "subfolder"
        docs_sub.mkdir(parents=True)
        doc_file = docs_sub / "sample.pdf"
        doc_file.write_bytes(b"%PDF-1.4 sample content")
        os.chmod(docs_sub, 0o755)
        os.chmod(doc_file, 0o644)
        marker = isolated_env["data_dir"] / MIGRATION_MARKER_NAME
        assert not marker.exists()

        assert migrate_managed_filesystem_permissions(isolated_env["home"]) is True
        assert get_mode(marker) == FILE_MODE
        assert _read_migration_status(marker, MIGRATION_VERSION) == "completed"
        assert get_mode(docs_sub) == DIR_MODE
        assert get_mode(doc_file) == FILE_MODE
        assert migrate_managed_filesystem_permissions(isolated_env["home"]) is True

    def test_l_user_external_documents_not_altered(self, tmp_path):
        external_dir = tmp_path / "user_documents"
        external_dir.mkdir()
        external_file = external_dir / "user_report.docx"
        external_file.write_bytes(b"user doc")
        os.chmod(external_dir, 0o755)
        os.chmod(external_file, 0o644)

        migrate_managed_filesystem_permissions(tmp_path / ".laya")
        assert get_mode(external_dir) == 0o755
        assert get_mode(external_file) == 0o644

    def test_y_completed_marker_skips_everything(self, tmp_path):
        """A completed marker (legacy format without status) means no chmod at all."""
        home = tmp_path / ".laya"
        write_marker(home / "data", status=None)
        cfg = home / "settings.json"
        cfg.write_text("{}")
        os.chmod(cfg, 0o644)

        with patch("os.chmod") as chmod, patch("os.fchmod") as fchmod:
            assert migrate_managed_filesystem_permissions(home) is True
        chmod.assert_not_called()
        fchmod.assert_not_called()
        assert get_mode(cfg) == 0o644

    def test_z_failure_writes_failed_marker(self, tmp_path):
        """When a chmod fails, the marker records 'failed' and the result is False."""
        home = tmp_path / ".laya"
        data_dir = home / "data"
        data_dir.mkdir(parents=True, mode=0o700)
        home.chmod(0o700)
        cfg = home / "settings.json"
        cfg.write_text("{}")
        real_os_open = os.open

        def selective_failing_open(path, flags, mode=0o666, **kwargs):
            # Fail the read-only open the migration uses on settings.json; let
            # the write open used for the marker succeed.
            if str(path).endswith("settings.json") and not (flags & (os.O_WRONLY | os.O_RDWR)):
                raise OSError("Permission denied")
            return real_os_open(path, flags, mode, **kwargs)

        with patch("os.open", selective_failing_open):
            assert migrate_managed_filesystem_permissions(home) is False

        marker = data_dir / MIGRATION_MARKER_NAME
        assert _read_migration_status(marker, MIGRATION_VERSION) == "failed"
        assert json.loads(marker.read_text())["failed_count"] == 1

    def test_as_failed_marker_skips_until_deleted(self, tmp_path):
        """A failed marker stops all work; deleting it makes the next run try again."""
        home = tmp_path / ".laya"
        data_dir = home / "data"
        marker = write_marker(data_dir, status="failed")
        cfg = home / "settings.json"
        cfg.write_text("{}")
        os.chmod(cfg, 0o644)

        with patch("os.chmod") as chmod, patch("os.fchmod") as fchmod:
            assert migrate_managed_filesystem_permissions(home) is False
        chmod.assert_not_called()
        fchmod.assert_not_called()
        assert get_mode(cfg) == 0o644
        assert _read_migration_status(marker, MIGRATION_VERSION) == "failed"

        marker.unlink()
        assert migrate_managed_filesystem_permissions(home) is True
        assert get_mode(cfg) == FILE_MODE
        assert _read_migration_status(marker, MIGRATION_VERSION) == "completed"

    @pytest.mark.parametrize("stale_status", ["in_progress", "incomplete"])
    def test_ar_non_terminal_marker_reruns_full_migration(self, tmp_path, stale_status):
        """An interrupted or legacy-incomplete marker re-runs everything, deep scan included."""
        home = tmp_path / ".laya"
        data_dir = home / "data"
        chroma_file = data_dir / "chromadb" / "test.bin"
        chroma_file.parent.mkdir(parents=True)
        chroma_file.write_bytes(b"data")
        os.chmod(chroma_file, 0o644)
        marker = write_marker(data_dir, status=stale_status)

        assert migrate_managed_filesystem_permissions(home) is True
        assert get_mode(chroma_file) == FILE_MODE
        assert _read_migration_status(marker, MIGRATION_VERSION) == "completed"

    def test_at_deleting_marker_reruns_migration(self, tmp_path):
        home = tmp_path / ".laya"
        data_dir = home / "data"
        chroma_file = data_dir / "chromadb" / "data.bin"
        chroma_file.parent.mkdir(parents=True)
        chroma_file.write_bytes(b"data")
        os.chmod(chroma_file, 0o644)
        marker = write_marker(data_dir, status="completed")

        assert migrate_managed_filesystem_permissions(home) is True
        assert get_mode(chroma_file) == 0o644  # completed: nothing touched

        marker.unlink()
        assert migrate_managed_filesystem_permissions(home) is True
        assert get_mode(chroma_file) == FILE_MODE

    def test_aa_migration_covers_n8n_dir(self, tmp_path):
        home = tmp_path / ".laya"
        (home / "data").mkdir(parents=True)
        n8n_dir = home / "n8n"
        n8n_dir.mkdir()
        os.chmod(n8n_dir, 0o755)
        creds = n8n_dir / "credentials.json"
        creds.write_text('{"key": "secret"}')
        os.chmod(creds, 0o644)

        assert migrate_managed_filesystem_permissions(home) is True
        assert get_mode(n8n_dir) == DIR_MODE
        assert get_mode(creds) == FILE_MODE

    def test_ab_migration_covers_all_log_files(self, tmp_path):
        home = tmp_path / ".laya"
        (home / "data").mkdir(parents=True)
        logs_dir = home / "logs"
        logs_dir.mkdir()
        log_names = ["engine.log", "engine.log.1", "engine-stdout.log", "n8n.log"]
        for name in log_names:
            (logs_dir / name).write_text("log data")
            os.chmod(logs_dir / name, 0o644)

        assert migrate_managed_filesystem_permissions(home) is True
        for name in log_names:
            assert get_mode(logs_dir / name) == FILE_MODE

    def test_ac_migration_succeeds_on_symlinked_root(self, tmp_path):
        storage = tmp_path / "external_storage" / "laya"
        storage.mkdir(parents=True)
        sym_home = tmp_path / ".laya"
        os.symlink(storage, sym_home)
        (storage / "data").mkdir()
        cfg = storage / "settings.json"
        cfg.write_text("{}")
        os.chmod(cfg, 0o644)

        assert migrate_managed_filesystem_permissions(sym_home) is True
        assert get_mode(cfg) == FILE_MODE

    def test_am_migration_fresh_home_creates_data_dir(self, tmp_path):
        home = tmp_path / ".laya"
        home.mkdir(mode=0o700)
        assert migrate_managed_filesystem_permissions(home) is True
        assert get_mode(home / "data") == DIR_MODE

    def test_an_migration_home_without_data_dir(self, tmp_path):
        home = tmp_path / ".laya"
        home.mkdir(mode=0o700)
        cfg = home / "settings.json"
        cfg.write_text("{}")
        os.chmod(cfg, 0o644)

        assert migrate_managed_filesystem_permissions(home) is True
        assert get_mode(home / "data") == DIR_MODE
        assert get_mode(cfg) == FILE_MODE

    def test_ae_migration_secures_chromadb_tree_offline(self, tmp_path):
        """Every ChromaDB file and directory is tightened and the store still opens afterwards.

        Explicit embeddings keep this independent of the default ONNX model download.
        """
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        home = tmp_path / ".laya"
        home.mkdir(mode=0o700)
        chroma_dir = home / "data" / "chromadb"
        chroma_dir.mkdir(parents=True)

        with umask_context(0o000):
            client = chromadb.PersistentClient(path=str(chroma_dir), settings=ChromaSettings(anonymized_telemetry=False))
            coll = client.get_or_create_collection("offline_test", metadata={"hnsw:space": "cosine"})
            coll.add(
                ids=["doc-1", "doc-2"],
                documents=["offline permission test", "second doc"],
                embeddings=[[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]],
            )
            coll.add(ids=["doc-3"], documents=["post-init write"], embeddings=[[0.9, 0.1, 0.2, 0.3]])
            assert coll.count() == 3
        assert any(mode != FILE_MODE and mode != DIR_MODE for _, mode in walk_modes(chroma_dir))

        assert migrate_managed_filesystem_permissions(home) is True

        for path, mode in walk_modes(chroma_dir):
            expected = DIR_MODE if path.is_dir() else FILE_MODE
            assert mode == expected, f"{path} has mode {oct(mode)}"

        client2 = chromadb.PersistentClient(path=str(chroma_dir), settings=ChromaSettings(anonymized_telemetry=False))
        assert client2.get_or_create_collection("offline_test", metadata={"hnsw:space": "cosine"}).count() == 3

    def test_ap_second_run_issues_no_fchmod(self, tmp_path):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        home = tmp_path / ".laya"
        home.mkdir(mode=0o700)
        chroma_dir = home / "data" / "chromadb"
        chroma_dir.mkdir(parents=True)
        with umask_context(0o000):
            client = chromadb.PersistentClient(path=str(chroma_dir), settings=ChromaSettings(anonymized_telemetry=False))
            client.get_or_create_collection("scan_test").add(ids=["x"], documents=["test"], embeddings=[[0.1, 0.2]])

        real_fchmod = os.fchmod
        counts = [0]

        def counting_fchmod(fd, mode, *a, **kw):
            counts[0] += 1
            return real_fchmod(fd, mode, *a, **kw)

        with patch("os.fchmod", counting_fchmod):
            assert migrate_managed_filesystem_permissions(home) is True
        assert counts[0] > 0

        counts[0] = 0
        with patch("os.fchmod", counting_fchmod):
            assert migrate_managed_filesystem_permissions(home) is True
        assert counts[0] == 0


class TestMarkerReading:
    """The marker can never suppress the migration unless it is a trustworthy regular file."""

    def test_af_marker_valid(self, tmp_path):
        marker = write_marker(tmp_path / "data", status=None)
        assert _read_migration_status(marker, MIGRATION_VERSION) == "completed"

    def test_ag_marker_missing(self, tmp_path):
        (tmp_path / "data").mkdir(mode=0o700)
        assert _read_migration_status(tmp_path / "data" / MIGRATION_MARKER_NAME, MIGRATION_VERSION) == "not_started"

    def test_ah_marker_malformed_json(self, tmp_path):
        (tmp_path / "data").mkdir(mode=0o700)
        marker = tmp_path / "data" / MIGRATION_MARKER_NAME
        marker.write_bytes(b"not valid json {{{{")
        assert _read_migration_status(marker, MIGRATION_VERSION) == "invalid"

    def test_ai_marker_symlink(self, tmp_path):
        (tmp_path / "data").mkdir(mode=0o700)
        real_file = tmp_path / "real_marker"
        real_file.write_text(json.dumps({"version": MIGRATION_VERSION}))
        marker = tmp_path / "data" / MIGRATION_MARKER_NAME
        os.symlink(real_file, marker)
        assert _read_migration_status(marker, MIGRATION_VERSION) == "not_started"

    def test_aj_marker_oversized(self, tmp_path):
        from laya.security.permissions import _MARKER_MAX_BYTES

        (tmp_path / "data").mkdir(mode=0o700)
        marker = tmp_path / "data" / MIGRATION_MARKER_NAME
        marker.write_bytes(b"x" * (_MARKER_MAX_BYTES + 1))
        assert _read_migration_status(marker, MIGRATION_VERSION) == "not_started"

    def test_ak_marker_wrong_version(self, tmp_path):
        marker = write_marker(tmp_path / "data", status="completed", version="filesystem_permissions_v0")
        assert _read_migration_status(marker, MIGRATION_VERSION) == "invalid"

    def test_al_symlinked_data_dir(self, tmp_path):
        real_data = tmp_path / "real_data"
        write_marker(real_data, status="completed")
        sym_data = tmp_path / "sym_data"
        os.symlink(real_data, sym_data)
        assert _read_migration_status(sym_data / MIGRATION_MARKER_NAME, MIGRATION_VERSION) == "not_started"


class TestChromaAncestorCheck:
    def _capture_warnings(self, chroma_dir: Path) -> list[str]:
        from laya.db import chromadb_store

        warned: list[str] = []
        original_log = chromadb_store.log

        class _CapturingLog:
            def warning(self, event, **kw):
                warned.append(kw.get("path", ""))

            def __getattr__(self, name):
                return getattr(original_log, name)

        with patch.object(chromadb_store, "log", _CapturingLog()):
            chromadb_store._verify_chromadb_ancestor_chain(chroma_dir)
        return warned

    def test_av_warns_when_ancestor_not_private(self, tmp_path):
        chroma_dir = tmp_path / "data" / "chromadb"
        chroma_dir.mkdir(parents=True, mode=0o700)
        os.chmod(chroma_dir.parent, 0o755)
        assert any(str(chroma_dir.parent) in p for p in self._capture_warnings(chroma_dir))

    def test_aw_silent_when_all_private(self, tmp_path):
        chroma_dir = tmp_path / "data" / "chromadb"
        chroma_dir.mkdir(parents=True, mode=0o700)
        os.chmod(chroma_dir.parent, 0o700)
        os.chmod(tmp_path, 0o700)
        assert self._capture_warnings(chroma_dir) == []
