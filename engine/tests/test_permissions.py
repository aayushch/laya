# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Automated tests for filesystem permission policy (Issue #61)."""

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

from laya.config import (
    DEFAULT_REPOS,
    DEFAULT_RULES,
    DEFAULT_SETTINGS,
    DEFAULT_TEAM,
    ensure_directories,
    load_repos,
    load_rules,
    load_settings,
    load_team,
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
    atomic_write_private,
    is_symlink,
    migrate_managed_filesystem_permissions,
    open_private_file,
    secure_directory,
    secure_file,
    secure_sqlite_files,
    write_private_json,
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


@pytest.fixture
def isolated_env(tmp_path):
    """Fixture providing isolated Laya directory tree and patched config paths."""
    home = tmp_path / ".laya"
    data_dir = home / "data"
    logs_dir = home / "logs"
    docs_dir = data_dir / "documents"
    db_path = data_dir / "laya.db"
    config_file = home / "settings.json"
    team_file = home / "team.json"
    rules_file = home / "rules.json"
    repos_file = home / "repos.json"

    patches = [
        patch("laya.config.LAYA_HOME", home),
        patch("laya.config.LAYA_DATA_DIR", data_dir),
        patch("laya.config.LAYA_LOG_DIR", logs_dir),
        patch("laya.config.DOCUMENTS_DIR", docs_dir),
        patch("laya.config.DB_PATH", db_path),
        patch("laya.config.LAYA_CONFIG_FILE", config_file),
        patch("laya.config.LAYA_TEAM_FILE", team_file),
        patch("laya.config.LAYA_RULES_FILE", rules_file),
        patch("laya.config.LAYA_REPOS_FILE", repos_file),
        patch("laya.db.sqlite.DB_PATH", db_path),
        patch("laya.logging_setup.LAYA_LOG_DIR", logs_dir),
    ]

    for p in patches:
        p.start()

    yield {
        "home": home,
        "data_dir": data_dir,
        "logs_dir": logs_dir,
        "docs_dir": docs_dir,
        "db_path": db_path,
        "config_file": config_file,
        "team_file": team_file,
        "rules_file": rules_file,
        "repos_file": repos_file,
    }

    for p in patches:
        p.stop()


class TestFilesystemPermissions:
    def test_a_new_directory_creation(self, isolated_env):
        """Test A: With umask 022, newly created Laya directories must have 0700 permissions."""
        with umask_context(0o022):
            ensure_directories()

        assert get_mode(isolated_env["home"]) == DIR_MODE
        assert get_mode(isolated_env["data_dir"]) == DIR_MODE
        assert get_mode(isolated_env["logs_dir"]) == DIR_MODE
        assert get_mode(isolated_env["docs_dir"]) == DIR_MODE

    def test_b_new_sensitive_file_creation(self, isolated_env):
        """Test B: Newly created configuration and data files must have 0600 permissions."""
        with umask_context(0o022):
            save_settings(DEFAULT_SETTINGS)
            save_team(DEFAULT_TEAM)
            save_rules(DEFAULT_RULES)
            save_repos(DEFAULT_REPOS)

        assert get_mode(isolated_env["config_file"]) == FILE_MODE
        assert get_mode(isolated_env["team_file"]) == FILE_MODE
        assert get_mode(isolated_env["rules_file"]) == FILE_MODE
        assert get_mode(isolated_env["repos_file"]) == FILE_MODE

    def test_c_existing_permissive_directories(self, isolated_env):
        """Test C: Pre-existing permissive directories (0755) must be tightened to 0700."""
        for d in (isolated_env["home"], isolated_env["data_dir"], isolated_env["logs_dir"], isolated_env["docs_dir"]):
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o755)
            assert get_mode(d) == 0o755

        ensure_directories()
        migrate_managed_filesystem_permissions(isolated_env["home"])

        assert get_mode(isolated_env["home"]) == DIR_MODE
        assert get_mode(isolated_env["data_dir"]) == DIR_MODE
        assert get_mode(isolated_env["logs_dir"]) == DIR_MODE
        assert get_mode(isolated_env["docs_dir"]) == DIR_MODE

    def test_d_existing_permissive_files(self, isolated_env):
        """Test D: Pre-existing permissive files (0644) must be tightened to 0600 without data loss."""
        isolated_env["home"].mkdir(parents=True, exist_ok=True)
        sample_settings = {"models": {"router": "test-router"}}
        content = json.dumps(sample_settings, indent=2)

        isolated_env["config_file"].write_text(content, encoding="utf-8")
        os.chmod(isolated_env["config_file"], 0o644)
        assert get_mode(isolated_env["config_file"]) == 0o644

        migrate_managed_filesystem_permissions(isolated_env["home"])

        assert get_mode(isolated_env["config_file"]) == FILE_MODE
        loaded = json.loads(isolated_env["config_file"].read_text(encoding="utf-8"))
        assert loaded["models"]["router"] == "test-router"

    def test_e_configuration_overwrites(self, isolated_env):
        """Test E: Overwriting a configuration file must preserve or restore 0600 permissions."""
        save_settings(DEFAULT_SETTINGS)
        assert get_mode(isolated_env["config_file"]) == FILE_MODE

        # Simulate external process or permissive touch loosening permissions
        os.chmod(isolated_env["config_file"], 0o644)
        assert get_mode(isolated_env["config_file"]) == 0o644

        updated = dict(DEFAULT_SETTINGS)
        updated["setup_complete"] = True
        save_settings(updated)

        assert get_mode(isolated_env["config_file"]) == FILE_MODE
        loaded = load_settings()
        assert loaded["setup_complete"] is True

    @pytest.mark.asyncio
    async def test_f_sqlite_database_and_auxiliary_files(self, isolated_env):
        """Test F: SQLite database and WAL/SHM sidecars must have 0600 permissions."""
        with umask_context(0o022):
            db = await sqlite_connect()
            try:
                assert get_mode(isolated_env["db_path"]) == FILE_MODE

                # Execute write transaction to trigger WAL/SHM creation
                await db.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, val TEXT)")
                await db.execute("INSERT INTO test_table (val) VALUES ('secure_data')")
                await db.commit()

                wal_file = isolated_env["db_path"].with_name("laya.db-wal")
                shm_file = isolated_env["db_path"].with_name("laya.db-shm")

                if wal_file.exists():
                    assert get_mode(wal_file) == FILE_MODE
                if shm_file.exists():
                    assert get_mode(shm_file) == FILE_MODE

                # Verify reads work
                async with db.execute("SELECT val FROM test_table WHERE id = 1") as cursor:
                    row = await cursor.fetchone()
                    assert row[0] == "secure_data"
            finally:
                await sqlite_disconnect()

    def test_g_symbolic_link_safety(self, isolated_env, tmp_path):
        """Test G: Symbolic links pointing outside managed directory must never modify target."""
        outside_target = tmp_path / "outside_target.txt"
        outside_target.write_text("external data")
        os.chmod(outside_target, 0o644)
        assert get_mode(outside_target) == 0o644

        outside_dir = tmp_path / "outside_dir"
        outside_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(outside_dir, 0o755)
        assert get_mode(outside_dir) == 0o755

        ensure_directories()

        # Place symlinks at managed paths pointing to external resources
        symlink_file = isolated_env["home"] / "settings.json"
        symlink_dir = isolated_env["data_dir"] / "documents_link"
        os.symlink(outside_target, symlink_file)
        os.symlink(outside_dir, symlink_dir)

        assert is_symlink(symlink_file)
        assert is_symlink(symlink_dir)

        # Run permission enforcement
        migrate_managed_filesystem_permissions(isolated_env["home"])

        # External targets must remain unmodified
        assert get_mode(outside_target) == 0o644
        assert get_mode(outside_dir) == 0o755

        # Attempting to write through the symlink must fail safely
        with pytest.raises(PermissionError):
            atomic_write_private(symlink_file, "malicious payload")

        with pytest.raises(PermissionError):
            with open_private_file(symlink_file, "w") as f:
                f.write("malicious payload")

        assert outside_target.read_text() == "external data"

    def test_h_idempotency(self, isolated_env):
        """Test H: Repeated permission enforcement must be idempotent and preserve contents."""
        save_settings(DEFAULT_SETTINGS)
        save_team(DEFAULT_TEAM)

        before_settings = isolated_env["config_file"].read_text()
        before_team = isolated_env["team_file"].read_text()

        for _ in range(5):
            ensure_directories()
            migrate_managed_filesystem_permissions(isolated_env["home"], force=True)

            assert get_mode(isolated_env["home"]) == DIR_MODE
            assert get_mode(isolated_env["config_file"]) == FILE_MODE
            assert get_mode(isolated_env["team_file"]) == FILE_MODE

        assert isolated_env["config_file"].read_text() == before_settings
        assert isolated_env["team_file"].read_text() == before_team

    def test_i_file_permissions_under_different_umasks(self, isolated_env):
        """Test I: Restrictive permissions are enforced consistently regardless of process umask."""
        test_masks = [0o022, 0o077, 0o000, 0o027]

        for i, mask in enumerate(test_masks):
            test_home = isolated_env["home"].parent / f"test_laya_{i}"
            test_file = test_home / f"test_{i}.json"

            with umask_context(mask):
                secure_directory(test_home)
                atomic_write_private(test_file, '{"test": true}')

            assert get_mode(test_home) == DIR_MODE
            assert get_mode(test_file) == FILE_MODE

    def test_j_log_files_and_rotation(self, isolated_env):
        """Test J: Newly created log files and rotated logs must maintain 0600 permissions."""
        log_dir = isolated_env["logs_dir"]
        log_file = log_dir / "engine.log"

        with umask_context(0o022):
            handler = PrivateRotatingFileHandler(
                log_file,
                maxBytes=200,
                backupCount=3,
                encoding="utf-8",
            )
            logger = logging.getLogger("test_perm_logger")
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)

            # Write enough data to trigger multiple rotations
            for i in range(20):
                logger.info("A" * 60 + f" line {i}")
            handler.flush()
            handler.close()
            logger.removeHandler(handler)

        assert log_file.exists()
        assert get_mode(log_file) == FILE_MODE

        # Check all rotated files (.1, .2, etc.)
        rotated_files = list(log_dir.glob("engine.log.*"))
        assert len(rotated_files) > 0
        for rf in rotated_files:
            assert get_mode(rf) == FILE_MODE

    def test_k_migration_deep_scan_and_marker(self, isolated_env):
        """Test K: Migration deep scans subdirectories and files, then writes marker."""
        docs_sub = isolated_env["docs_dir"] / "subfolder"
        docs_sub.mkdir(parents=True, exist_ok=True)
        doc_file = docs_sub / "sample.pdf"
        doc_file.write_bytes(b"%PDF-1.4 sample content")

        os.chmod(docs_sub, 0o755)
        os.chmod(doc_file, 0o644)
        assert get_mode(docs_sub) == 0o755
        assert get_mode(doc_file) == 0o644

        marker = isolated_env["data_dir"] / ".permissions_migrated"
        assert not marker.exists()

        success = migrate_managed_filesystem_permissions(isolated_env["home"])
        assert success is True
        assert marker.exists()
        assert get_mode(marker) == FILE_MODE

        assert get_mode(docs_sub) == DIR_MODE
        assert get_mode(doc_file) == FILE_MODE

        # Subsequent migration with marker present should succeed immediately
        assert migrate_managed_filesystem_permissions(isolated_env["home"]) is True

    def test_l_user_external_documents_not_altered(self, tmp_path):
        """Test L: User documents outside Laya home are never modified or chmodded."""
        external_dir = tmp_path / "user_documents"
        external_dir.mkdir(parents=True, exist_ok=True)
        external_file = external_dir / "user_report.docx"
        external_file.write_bytes(b"user doc")

        os.chmod(external_dir, 0o755)
        os.chmod(external_file, 0o644)

        laya_home = tmp_path / ".laya"
        migrate_managed_filesystem_permissions(laya_home)

        # External directory and file must remain untouched
        assert get_mode(external_dir) == 0o755
        assert get_mode(external_file) == 0o644
