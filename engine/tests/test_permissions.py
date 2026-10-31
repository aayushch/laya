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

from laya.security.permissions import _assert_no_symlink_in_path
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

                # Create table and commit so the schema is checkpointed
                await db.execute(
                    "CREATE TABLE IF NOT EXISTS test_table "
                    "(id INTEGER PRIMARY KEY, val TEXT)"
                )
                await db.commit()

                # Insert without committing: keeps the write in the WAL so both
                # WAL and SHM files exist while we check them.
                await db.execute("INSERT INTO test_table (val) VALUES ('secure_data')")

                wal_file = isolated_env["db_path"].with_name("laya.db-wal")
                shm_file = isolated_env["db_path"].with_name("laya.db-shm")

                # Tighten permissions on sidecars that now exist
                secure_sqlite_files(isolated_env["db_path"])

                assert wal_file.exists(), "WAL file must exist while an uncommitted write is active"
                assert shm_file.exists(), "SHM file must exist while WAL is active"
                assert get_mode(wal_file) == FILE_MODE
                assert get_mode(shm_file) == FILE_MODE

                await db.commit()

                # Verify data integrity
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

    # -----------------------------------------------------------------------
    # Symlink-traversal tests (Issue #61 review)
    # -----------------------------------------------------------------------

    def test_m_symlinked_laya_home_raises(self, tmp_path):
        """Test M: secure_directory must raise PermissionError when the target is a symlink."""
        real_home = tmp_path / "real_laya"
        real_home.mkdir()
        sym_home = tmp_path / "sym_laya"
        os.symlink(real_home, sym_home)

        with pytest.raises(PermissionError, match="Symlink detected"):
            secure_directory(sym_home)

        # The real target behind the symlink must be unchanged
        assert get_mode(real_home) == get_mode(real_home)  # not modified

    def test_n_symlinked_managed_parent_raises(self, tmp_path):
        """Test N: secure_directory raises when an ancestor directory is a symlink."""
        real_home = tmp_path / "real_home"
        real_home.mkdir()
        sym_home = tmp_path / "sym_home"
        os.symlink(real_home, sym_home)

        # data_dir's parent is a symlink — must be rejected
        data_dir = sym_home / "data"
        with pytest.raises(PermissionError, match="Symlink detected"):
            secure_directory(data_dir)

        # Nothing should have been created inside the real target
        assert not (real_home / "data").exists()

    def test_n2_assert_no_symlink_helper(self, tmp_path):
        """Test N2: _assert_no_symlink_in_path raises for a symlink and its descendants."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        sym = tmp_path / "sym"
        os.symlink(real_dir, sym)

        with pytest.raises(PermissionError):
            _assert_no_symlink_in_path(sym, boundary=tmp_path)

        child = sym / "child"
        with pytest.raises(PermissionError):
            _assert_no_symlink_in_path(child, boundary=tmp_path)

        # A real path under tmp_path must NOT raise
        real_child = real_dir / "subdir"
        _assert_no_symlink_in_path(real_child, boundary=tmp_path)  # no exception

    def test_o_directory_mode_at_creation_time(self, tmp_path):
        """Test O: os.mkdir must receive mode 0700, not a permissive default."""
        new_dir = tmp_path / "private_dir"
        mkdir_modes: list[int] = []
        original_mkdir = os.mkdir

        def spy_mkdir(path, mode=0o777, *, dir_fd=None):
            mkdir_modes.append(mode)
            if dir_fd is not None:
                original_mkdir(path, mode, dir_fd=dir_fd)
            else:
                original_mkdir(path, mode)

        # Under umask 0o000 a call to mkdir(path, 0o777) produces 0o777;
        # our code must pass 0o700 so the mode is correct from the start.
        with umask_context(0o000), patch("os.mkdir", spy_mkdir):
            secure_directory(new_dir)

        assert mkdir_modes, "os.mkdir must have been called"
        assert all(m == DIR_MODE for m in mkdir_modes), (
            f"mkdir was called with modes {[oct(m) for m in mkdir_modes]}, "
            f"expected {oct(DIR_MODE)}"
        )
        # Final mode on disk must also be correct
        assert get_mode(new_dir) == DIR_MODE

    @pytest.mark.asyncio
    async def test_p_sqlite_symlink_rejection(self, isolated_env, tmp_path):
        """Test P: Database init must fail when DB_PATH is a symlink; external DB unchanged."""
        # Create an external database with identifiable content
        external_db = tmp_path / "external.db"
        ext_conn = await aiosqlite.connect(str(external_db))
        await ext_conn.execute("CREATE TABLE secret (val TEXT)")
        await ext_conn.execute("INSERT INTO secret VALUES ('external_data')")
        await ext_conn.commit()
        await ext_conn.close()

        # Place a symlink at DB_PATH pointing to the external database
        isolated_env["data_dir"].mkdir(parents=True, exist_ok=True)
        db_symlink = isolated_env["db_path"]
        os.symlink(external_db, db_symlink)

        with patch("laya.db.sqlite.DB_PATH", db_symlink):
            with pytest.raises(PermissionError, match="symbolic link"):
                await sqlite_connect()

        # External database must be completely unmodified
        check_conn = await aiosqlite.connect(str(external_db))
        async with check_conn.execute("SELECT val FROM secret") as cur:
            row = await cur.fetchone()
            assert row is not None and row[0] == "external_data"
        await check_conn.close()

    def test_q_chromadb_permissions_after_init(self, tmp_path):
        """Test Q: All ChromaDB files must have 0700/0600 after initialization."""
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        from laya.db.chromadb_store import _secure_chromadb_tree

        chroma_dir = tmp_path / "chromadb"
        chroma_dir.mkdir(mode=0o700)

        # Initialize ChromaDB under a permissive umask so it creates files at 0644/0755
        with umask_context(0o000):
            client = chromadb.PersistentClient(
                path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            coll = client.get_or_create_collection(
                "test_collection",
                metadata={"hnsw:space": "cosine"},
            )
            # Perform an actual write to ensure data files are created
            coll.add(documents=["test document"], ids=["doc1"])

        # Now enforce permissions as connect_chromadb() does
        _secure_chromadb_tree(chroma_dir)

        # All directories must be 0700, all files must be 0600
        for dirpath, dirnames, filenames in os.walk(chroma_dir, followlinks=False):
            dp = Path(dirpath)
            if not os.path.islink(dp):
                assert get_mode(dp) == DIR_MODE, f"Directory {dp} has wrong mode"
            for fname in filenames:
                fp = dp / fname
                if not os.path.islink(fp):
                    assert get_mode(fp) == FILE_MODE, f"File {fp} has wrong mode"
