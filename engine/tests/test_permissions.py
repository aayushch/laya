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

from laya.security.permissions import (
    _assert_no_symlink_in_path,
    _read_migration_marker,
    _read_migration_status,
    resolve_app_root,
    MIGRATION_VERSION,
)
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
            secure_directory(sym_home, boundary=tmp_path)

        # The real target behind the symlink must be unchanged
        assert real_home.exists()

    def test_n_symlinked_managed_parent_raises(self, tmp_path):
        """Test N: secure_directory raises when an ancestor directory is a symlink."""
        real_home = tmp_path / "real_home"
        real_home.mkdir()
        sym_home = tmp_path / "sym_home"
        os.symlink(real_home, sym_home)

        # data_dir's parent is a symlink — must be rejected
        data_dir = sym_home / "data"
        with pytest.raises(PermissionError, match="Symlink detected"):
            secure_directory(data_dir, boundary=tmp_path)

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

    # -----------------------------------------------------------------------
    # macOS /var boundary regression tests (Issue #65 CI fix)
    # -----------------------------------------------------------------------

    def test_r_temp_dir_outside_home_boundary_accepted(self, tmp_path):
        """Test R: A real directory outside the home boundary must not be rejected.

        On macOS, pytest temp dirs live under /var/folders/... which contains
        a system-managed /var -> /private/var symlink.  Passing the temp root
        as the explicit boundary must allow secure_directory to succeed.
        """
        laya_tmp = tmp_path / "laya_data"
        # Must not raise even though tmp_path may be outside Path.home()
        result = secure_directory(laya_tmp, boundary=tmp_path)
        assert result == laya_tmp
        assert laya_tmp.is_dir()
        assert get_mode(laya_tmp) == DIR_MODE

    def test_s_symlink_below_temp_boundary_rejected(self, tmp_path):
        """Test S: A symlink inside the temp boundary is still rejected."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        sym = tmp_path / "sym"
        os.symlink(real_dir, sym)

        # Symlink as the direct target
        with pytest.raises(PermissionError, match="Symlink detected"):
            secure_directory(sym, boundary=tmp_path)

        # Symlink as an ancestor of the target
        child = sym / "child"
        with pytest.raises(PermissionError, match="Symlink detected"):
            secure_directory(child, boundary=tmp_path)

    def test_t_symlinked_laya_home_still_rejected(self, tmp_path):
        """Test T: Symlinked LAYA_HOME is always rejected regardless of boundary."""
        real_home = tmp_path / "real_home"
        real_home.mkdir()
        sym_home = tmp_path / "sym_home"
        os.symlink(real_home, sym_home)

        with pytest.raises(PermissionError, match="Symlink detected"):
            secure_directory(sym_home, boundary=tmp_path)

    def test_u_symlinked_managed_ancestor_still_rejected(self, tmp_path):
        """Test U: A symlink in any managed ancestor is rejected."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        sym_dir = tmp_path / "sym"
        os.symlink(real_dir, sym_dir)

        with pytest.raises(PermissionError, match="Symlink detected"):
            secure_directory(sym_dir / "sub" / "dir", boundary=tmp_path)

    def test_v_assert_no_symlink_canonicalizes_boundary(self, tmp_path):
        """Test V: _assert_no_symlink_in_path accepts a real path whose boundary
        resolves through an OS symlink (simulates macOS /var -> /private/var).
        """
        # Create a symlink that acts like the macOS /var -> /private/var alias.
        real_root = tmp_path / "private"
        real_root.mkdir()
        alias = tmp_path / "var_alias"
        os.symlink(real_root, alias)

        # A real directory inside the alias-resolved space must be accepted
        # when the boundary is set to alias (canonical resolution will resolve
        # alias to real_root, then check descendants of real_root).
        real_child = real_root / "folders" / "work"
        real_child.mkdir(parents=True)

        # The child accessed via the alias path is outside the resolved boundary
        # (alias itself), but real_child directly is inside: check that a real
        # descendant of the canonicalized boundary is accepted.
        _assert_no_symlink_in_path(real_child, boundary=alias)  # must not raise

        # But a symlink inside that space must still be rejected.
        sym_inside = real_root / "sym_inside"
        os.symlink(real_root / "folders", sym_inside)
        with pytest.raises(PermissionError, match="Symlink detected"):
            _assert_no_symlink_in_path(sym_inside, boundary=alias)

    # -----------------------------------------------------------------------
    # resolve_app_root and intentional root-level symlink support (req #4)
    # -----------------------------------------------------------------------

    def test_w_resolve_app_root_real_dir_unchanged(self, tmp_path):
        """Test W: A real directory is returned as-is by resolve_app_root."""
        real = tmp_path / "laya"
        real.mkdir()
        assert resolve_app_root(real) == real

    def test_x_resolve_app_root_follows_single_symlink(self, tmp_path):
        """Test X: An intentional root symlink (one hop) is followed to its real target."""
        storage = tmp_path / "storage" / "laya-data"
        storage.mkdir(parents=True)
        sym = tmp_path / ".laya"
        os.symlink(storage, sym)

        resolved = resolve_app_root(sym)
        assert resolved == storage
        assert not os.path.islink(str(resolved))

    def test_x2_resolve_app_root_rejects_chain(self, tmp_path):
        """Test X2: A symlink chain (symlink -> symlink) is rejected."""
        real = tmp_path / "real"
        real.mkdir()
        mid = tmp_path / "mid"
        os.symlink(real, mid)
        chain = tmp_path / ".laya"
        os.symlink(mid, chain)

        with pytest.raises(PermissionError, match="chain"):
            resolve_app_root(chain)

    def test_x3_resolve_app_root_rejects_non_directory_target(self, tmp_path):
        """Test X3: A symlink pointing to a file (not a directory) is rejected."""
        file_target = tmp_path / "notadir"
        file_target.write_text("data")
        sym = tmp_path / ".laya"
        os.symlink(file_target, sym)

        with pytest.raises(PermissionError):
            resolve_app_root(sym)

    def test_x4_resolve_app_root_missing_target_allowed(self, tmp_path):
        """Test X4: A symlink to a non-existent path is allowed (will be created)."""
        missing = tmp_path / "future_storage"
        sym = tmp_path / ".laya"
        os.symlink(missing, sym)

        result = resolve_app_root(sym)
        assert result == missing

    # -----------------------------------------------------------------------
    # Migration one-time behavior (req #3)
    # -----------------------------------------------------------------------

    def test_y_migration_checks_marker_first(self, tmp_path):
        """Test Y: When the migration marker is present, no chmod operations run."""
        import json
        home = tmp_path / ".laya"
        data_dir = home / "data"
        data_dir.mkdir(parents=True)

        # Pre-write a valid marker
        marker = data_dir / ".permissions_migrated"
        marker.write_text(json.dumps({"version": MIGRATION_VERSION}))
        os.chmod(str(marker), 0o600)

        # Create a permissive config file — migration should NOT touch it
        cfg = home / "settings.json"
        cfg.write_text("{}")
        os.chmod(str(cfg), 0o644)

        chmod_calls: list[str] = []
        real_chmod = os.chmod

        def spy_chmod(path, mode, *args, **kwargs):
            chmod_calls.append(str(path))
            real_chmod(path, mode, *args, **kwargs)

        with patch("os.chmod", spy_chmod):
            result = migrate_managed_filesystem_permissions(home)

        assert result is True
        # No chmod calls should have been issued once the marker was found.
        assert chmod_calls == [], f"Unexpected chmod calls with valid marker: {chmod_calls}"
        # Belt-and-suspenders: the permissive file must remain untouched.
        assert get_mode(cfg) == 0o644

    def test_z_migration_incomplete_writes_incomplete_marker(self, tmp_path):
        """Test Z: When an operation fails, an incomplete (not completed) marker is written."""
        home = tmp_path / ".laya"
        data_dir = home / "data"
        data_dir.mkdir(parents=True, mode=0o700)
        home.chmod(0o700)

        cfg = home / "settings.json"
        cfg.write_text("{}")

        real_os_open = os.open

        def selective_failing_open(path, flags, mode=0o666, **kwargs):
            # Fail read-only opens for settings.json (from _try_fchmod_file).
            # Let write opens (O_WRONLY, used for marker writes) succeed.
            if str(path).endswith("settings.json") and not (flags & (os.O_WRONLY | os.O_RDWR)):
                raise OSError("Permission denied")
            return real_os_open(path, flags, mode, **kwargs)

        with patch("os.open", selective_failing_open):
            result = migrate_managed_filesystem_permissions(home)

        assert result is False
        marker = data_dir / ".permissions_migrated"
        assert marker.exists(), "Incomplete marker must be written after failed migration"
        assert not _read_migration_marker(marker, MIGRATION_VERSION), \
            "Marker must not show completed status"
        assert _read_migration_status(marker, MIGRATION_VERSION) == "incomplete", \
            "Marker must show incomplete status"

    def test_aa_migration_covers_n8n_dir(self, tmp_path):
        """Test AA: The n8n data directory is included in the migration."""
        home = tmp_path / ".laya"
        data_dir = home / "data"
        data_dir.mkdir(parents=True)
        n8n_dir = home / "n8n"
        n8n_dir.mkdir()
        os.chmod(str(n8n_dir), 0o755)
        assert get_mode(n8n_dir) == 0o755

        creds = n8n_dir / "credentials.json"
        creds.write_text('{"key": "secret"}')
        os.chmod(str(creds), 0o644)

        result = migrate_managed_filesystem_permissions(home)

        assert result is True
        assert get_mode(n8n_dir) == DIR_MODE
        assert get_mode(creds) == FILE_MODE

    def test_ab_migration_covers_all_log_files(self, tmp_path):
        """Test AB: All log files (engine.log, engine-stdout.log, n8n.log, rotated) are secured."""
        home = tmp_path / ".laya"
        data_dir = home / "data"
        logs_dir = home / "logs"
        data_dir.mkdir(parents=True)
        logs_dir.mkdir()

        log_names = ["engine.log", "engine.log.1", "engine-stdout.log", "n8n.log"]
        for name in log_names:
            f = logs_dir / name
            f.write_text("log data")
            os.chmod(str(f), 0o644)

        result = migrate_managed_filesystem_permissions(home)

        assert result is True
        for name in log_names:
            assert get_mode(logs_dir / name) == FILE_MODE, f"{name} not secured"

    def test_ac_migration_succeeds_on_symlinked_root(self, tmp_path):
        """Test AC: Migration works correctly when ~/ .laya is a symlink to external storage."""
        storage = tmp_path / "external_storage" / "laya"
        storage.mkdir(parents=True)
        sym_home = tmp_path / ".laya"
        os.symlink(storage, sym_home)

        data_dir = storage / "data"
        data_dir.mkdir()
        cfg = storage / "settings.json"
        cfg.write_text("{}")
        os.chmod(str(cfg), 0o644)

        result = migrate_managed_filesystem_permissions(sym_home)

        assert result is True
        assert get_mode(cfg) == FILE_MODE

    def test_ad_nested_symlink_skipped_not_followed(self, tmp_path):
        """Test AD: A symlink inside the managed tree is skipped, not followed."""
        home = tmp_path / ".laya"
        data_dir = home / "data"
        docs_dir = data_dir / "documents"
        docs_dir.mkdir(parents=True)

        # External sensitive file
        external = tmp_path / "sensitive.txt"
        external.write_text("external content")
        os.chmod(str(external), 0o644)

        # Nested symlink inside documents
        link = docs_dir / "secret_link.txt"
        os.symlink(external, link)

        result = migrate_managed_filesystem_permissions(home)

        # Migration completes (the symlink is skipped, not a failure)
        assert result is True
        # External file is unmodified
        assert get_mode(external) == 0o644

    # -----------------------------------------------------------------------
    # ChromaDB offline permission test (req #7)
    # -----------------------------------------------------------------------

    def test_ae_chromadb_offline_write_permissions(self, tmp_path):
        """Test AE: ChromaDB files from offline (no embedding model) writes are secured."""
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        from laya.db.chromadb_store import _secure_chromadb_tree

        chroma_dir = tmp_path / "chromadb_offline"
        chroma_dir.mkdir(mode=0o700)

        # Open under permissive umask so ChromaDB creates files at 0644/0755
        with umask_context(0o000):
            client = chromadb.PersistentClient(
                path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            coll = client.get_or_create_collection(
                "offline_test",
                metadata={"hnsw:space": "cosine"},
            )
            # Use explicit embeddings — no network model required
            coll.add(
                ids=["doc-1", "doc-2"],
                documents=["offline permission test", "second doc"],
                embeddings=[[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]],
            )
            # Additional write after initialization
            coll.add(
                ids=["doc-3"],
                documents=["post-init write"],
                embeddings=[[0.9, 0.1, 0.2, 0.3]],
            )
            # Verify data persisted before checking permissions
            assert coll.count() == 3

        # Enforce permissions
        _secure_chromadb_tree(chroma_dir)

        # All directories and files must be restricted
        for dirpath, dirnames, filenames in os.walk(chroma_dir, followlinks=False):
            dp = Path(dirpath)
            if not os.path.islink(str(dp)):
                assert get_mode(dp) == DIR_MODE, f"Directory {dp} has wrong mode"
            for fname in filenames:
                fp = dp / fname
                if not os.path.islink(str(fp)):
                    assert get_mode(fp) == FILE_MODE, f"File {fp} has wrong mode"

        # Reopen and verify persistence is intact
        client2 = chromadb.PersistentClient(
            path=str(chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        coll2 = client2.get_or_create_collection(
            "offline_test",
            metadata={"hnsw:space": "cosine"},
        )
        assert coll2.count() == 3

    # -----------------------------------------------------------------------
    # _read_migration_marker TOCTOU regression tests (req §1)
    # -----------------------------------------------------------------------

    def test_af_marker_valid_returns_true(self, tmp_path):
        """Test AF: A correctly formatted marker returns True."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(mode=0o700)
        marker = data_dir / ".permissions_migrated"
        marker.write_text(json.dumps({"version": MIGRATION_VERSION}))
        os.chmod(str(marker), 0o600)

        assert _read_migration_marker(marker, MIGRATION_VERSION) is True

    def test_ag_marker_missing_returns_false(self, tmp_path):
        """Test AG: A missing marker returns False without error."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(mode=0o700)
        marker = data_dir / ".permissions_migrated"

        assert _read_migration_marker(marker, MIGRATION_VERSION) is False

    def test_ah_marker_malformed_json_returns_false(self, tmp_path):
        """Test AH: A marker with invalid JSON returns False."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(mode=0o700)
        marker = data_dir / ".permissions_migrated"
        marker.write_bytes(b"not valid json {{{{")
        os.chmod(str(marker), 0o600)

        assert _read_migration_marker(marker, MIGRATION_VERSION) is False

    def test_ai_marker_symlink_returns_false(self, tmp_path):
        """Test AI: A symlinked marker never signals success."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(mode=0o700)
        real_file = tmp_path / "real_marker"
        real_file.write_text(json.dumps({"version": MIGRATION_VERSION}))

        marker = data_dir / ".permissions_migrated"
        os.symlink(real_file, marker)

        assert _read_migration_marker(marker, MIGRATION_VERSION) is False

    def test_aj_marker_oversized_returns_false(self, tmp_path):
        """Test AJ: A marker larger than 4 KiB returns False."""
        from laya.security.permissions import _MARKER_MAX_BYTES
        data_dir = tmp_path / "data"
        data_dir.mkdir(mode=0o700)
        marker = data_dir / ".permissions_migrated"
        marker.write_bytes(b"x" * (_MARKER_MAX_BYTES + 1))
        os.chmod(str(marker), 0o600)

        assert _read_migration_marker(marker, MIGRATION_VERSION) is False

    def test_ak_marker_wrong_version_returns_false(self, tmp_path):
        """Test AK: A marker with a different version string returns False."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(mode=0o700)
        marker = data_dir / ".permissions_migrated"
        marker.write_text(json.dumps({"version": "filesystem_permissions_v0"}))
        os.chmod(str(marker), 0o600)

        assert _read_migration_marker(marker, MIGRATION_VERSION) is False

    def test_al_symlinked_data_dir_returns_false(self, tmp_path):
        """Test AL: A symlinked data_dir is rejected; marker read returns False."""
        real_data = tmp_path / "real_data"
        real_data.mkdir(mode=0o700)
        marker = real_data / ".permissions_migrated"
        marker.write_text(json.dumps({"version": MIGRATION_VERSION}))
        os.chmod(str(marker), 0o600)

        sym_data = tmp_path / "sym_data"
        os.symlink(real_data, sym_data)
        sym_marker = sym_data / ".permissions_migrated"

        # The symlinked data_dir itself is rejected by O_NOFOLLOW.
        assert _read_migration_marker(sym_marker, MIGRATION_VERSION) is False

    # -----------------------------------------------------------------------
    # Migration data-dir initialization (req §2)
    # -----------------------------------------------------------------------

    def test_am_migration_fresh_home_creates_data_dir(self, tmp_path):
        """Test AM: Migration creates a missing data directory at 0700."""
        home = tmp_path / ".laya"
        home.mkdir(mode=0o700)
        data_dir = home / "data"

        assert not data_dir.exists()
        result = migrate_managed_filesystem_permissions(home)

        assert result is True
        assert data_dir.exists()
        assert get_mode(data_dir) == DIR_MODE

    def test_an_migration_home_without_data_dir(self, tmp_path):
        """Test AN: Migration with existing home but no data dir initializes correctly."""
        home = tmp_path / ".laya"
        home.mkdir(mode=0o700)
        cfg = home / "settings.json"
        cfg.write_text("{}")
        os.chmod(str(cfg), 0o644)
        data_dir = home / "data"

        result = migrate_managed_filesystem_permissions(home)

        assert result is True
        assert data_dir.exists()
        assert get_mode(data_dir) == DIR_MODE
        assert get_mode(cfg) == FILE_MODE

    def test_ao_chromadb_startup_no_tree_scan(self, tmp_path, monkeypatch):
        """Test AO: connect_chromadb does NOT call _secure_chromadb_tree on every startup."""
        import chromadb as chroma_mod
        from laya.db import chromadb_store

        scan_calls: list[str] = []
        real_scan = chromadb_store._secure_chromadb_tree

        def spy_scan(root):
            scan_calls.append(str(root))
            real_scan(root)

        monkeypatch.setattr(chromadb_store, "_secure_chromadb_tree", spy_scan)
        monkeypatch.setattr(chromadb_store, "CHROMADB_DIR", tmp_path / "chroma")

        chromadb_store.connect_chromadb()
        chromadb_store.disconnect_chromadb()

        assert scan_calls == [], (
            f"connect_chromadb called _secure_chromadb_tree unexpectedly: {scan_calls}"
        )

    def test_ap_migration_scans_chromadb_tree(self, tmp_path):
        """Test AP: Migration (first run) secures chromadb files; subsequent runs skip."""
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        home = tmp_path / ".laya"
        home.mkdir(mode=0o700)
        chroma_dir = home / "data" / "chromadb"
        chroma_dir.mkdir(parents=True)

        # Create chromadb files with permissive permissions
        with umask_context(0o000):
            client = chromadb.PersistentClient(
                path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            coll = client.get_or_create_collection("scan_test")
            coll.add(ids=["x"], documents=["test"], embeddings=[[0.1, 0.2]])

        # First migration run — should secure the tree and write marker.
        # The traversal uses os.fchmod (not os.chmod); count fchmod calls.
        fchmod_count_first = [0]
        real_fchmod = os.fchmod

        def counting_fchmod(fd, mode, *a, **kw):
            fchmod_count_first[0] += 1
            return real_fchmod(fd, mode, *a, **kw)

        with patch("os.fchmod", counting_fchmod):
            result1 = migrate_managed_filesystem_permissions(home)
        assert result1 is True
        assert fchmod_count_first[0] > 0, "First run must issue at least one fchmod"

        # Second migration run — completed marker present, zero fchmod calls.
        fchmod_count_second = [0]

        def counting_fchmod2(fd, mode, *a, **kw):
            fchmod_count_second[0] += 1
            return real_fchmod(fd, mode, *a, **kw)

        with patch("os.fchmod", counting_fchmod2):
            result2 = migrate_managed_filesystem_permissions(home)
        assert result2 is True
        assert fchmod_count_second[0] == 0, "Second run must skip all fchmod calls"

    # -----------------------------------------------------------------------
    # Migration state machine tests (req §2)
    # -----------------------------------------------------------------------

    def test_aq_incomplete_migration_skips_deep_scan_on_next_startup(self, tmp_path):
        """Test AQ: After an incomplete migration, subsequent startups skip the deep scan.

        The shallow scan runs and may succeed; the deep scan (large tree) is skipped
        to avoid rescanning on every startup.  The chroma file (deep-scan scope) must
        remain at its original mode.
        """
        home = tmp_path / ".laya"
        data_dir = home / "data"
        chroma_dir = data_dir / "chromadb"
        chroma_dir.mkdir(parents=True)

        # Pre-seed chromadb with a permissive file to detect if deep scan runs
        chroma_file = chroma_dir / "test.bin"
        chroma_file.write_bytes(b"data")
        os.chmod(str(chroma_file), 0o644)

        # Write an incomplete marker directly
        marker = data_dir / ".permissions_migrated"
        marker.write_text(
            json.dumps({"version": MIGRATION_VERSION, "status": "incomplete"})
        )
        os.chmod(str(marker), 0o600)

        result = migrate_managed_filesystem_permissions(home)

        # The shallow scan ran and found nothing to fail on → returns True (updated to completed)
        # The key invariant is that the deep-scan file was NOT touched.
        assert get_mode(chroma_file) == 0o644, "Deep scan must be skipped for incomplete state"
        # Marker must not have been written with "in_progress" still (it completed or is incomplete)
        final_status = _read_migration_status(marker, MIGRATION_VERSION)
        assert final_status in ("completed", "incomplete"), \
            f"Unexpected status: {final_status}"

    def test_ar_in_progress_marker_requires_repair(self, tmp_path):
        """Test AR: An in-progress marker (crashed migration) requires force=True to retry."""
        home = tmp_path / ".laya"
        data_dir = home / "data"
        data_dir.mkdir(parents=True)

        marker = data_dir / ".permissions_migrated"
        marker.write_text(
            json.dumps({"version": MIGRATION_VERSION, "status": "in_progress"})
        )
        os.chmod(str(marker), 0o600)

        # Normal startup → should skip and return False (requires repair)
        result = migrate_managed_filesystem_permissions(home)
        assert result is False

        # force=True → should retry and succeed
        result_forced = migrate_managed_filesystem_permissions(home, force=True)
        assert result_forced is True
        assert _read_migration_status(marker, MIGRATION_VERSION) == "completed"

    def test_as_incomplete_shallow_scan_runs_on_next_startup(self, tmp_path):
        """Test AS: After incomplete migration, shallow-scan paths are still secured."""
        home = tmp_path / ".laya"
        data_dir = home / "data"
        data_dir.mkdir(parents=True)

        cfg = home / "settings.json"
        cfg.write_text("{}")
        os.chmod(str(cfg), 0o644)

        # Pre-write an incomplete marker
        marker = data_dir / ".permissions_migrated"
        marker.write_text(
            json.dumps({"version": MIGRATION_VERSION, "status": "incomplete"})
        )
        os.chmod(str(marker), 0o600)

        # Startup without force: shallow scan runs
        result = migrate_managed_filesystem_permissions(home)

        # Shallow scan secured the config file even though status was incomplete
        assert get_mode(cfg) == FILE_MODE
        # Result is False because final write preserves "incomplete" (or some errors remain)
        # — settings.json succeeded, so this specific setup completes fully
        # The marker status updates to reflect the new run
        new_status = _read_migration_status(marker, MIGRATION_VERSION)
        assert new_status in ("completed", "incomplete")

    def test_at_force_retries_full_migration(self, tmp_path):
        """Test AT: force=True retries the full migration including deep scan."""
        home = tmp_path / ".laya"
        data_dir = home / "data"
        chroma_dir = data_dir / "chromadb"
        chroma_dir.mkdir(parents=True)

        chroma_file = chroma_dir / "data.bin"
        chroma_file.write_bytes(b"data")
        os.chmod(str(chroma_file), 0o644)

        # Pre-write completed marker (simulates already-migrated system)
        marker = data_dir / ".permissions_migrated"
        marker.write_text(json.dumps({"version": MIGRATION_VERSION, "status": "completed"}))
        os.chmod(str(marker), 0o600)

        # force=True bypasses the completed status and runs full migration
        result = migrate_managed_filesystem_permissions(home, force=True)
        assert result is True
        # Deep scan should have run, securing the chroma file
        assert get_mode(chroma_file) == FILE_MODE

    def test_au_fchmod_does_not_follow_symlink_to_external_target(self, tmp_path):
        """Test AU: fd-based fchmod rejects a symlink; external target is never chmod'd."""
        home = tmp_path / ".laya"
        data_dir = home / "data"
        data_dir.mkdir(parents=True)

        external = tmp_path / "external_secret.txt"
        external.write_text("sensitive")
        os.chmod(str(external), 0o644)

        # Replace settings.json with a symlink to the external file
        sym = home / "settings.json"
        os.symlink(external, sym)

        migrate_managed_filesystem_permissions(home)

        # External file must remain untouched
        assert get_mode(external) == 0o644, "Symlink target must never be chmod'd"

    # -----------------------------------------------------------------------
    # ChromaDB ancestor chain test (req §4)
    # -----------------------------------------------------------------------

    def test_av_chromadb_ancestor_chain_warns_when_not_private(self, tmp_path):
        """Test AV: _verify_chromadb_ancestor_chain calls log.warning for permissive ancestors."""
        from laya.db import chromadb_store

        chroma_dir = tmp_path / "data" / "chromadb"
        chroma_dir.mkdir(parents=True, mode=0o700)
        # Make data/ directory permissive to trigger the warning
        os.chmod(str(chroma_dir.parent), 0o755)

        warned_paths: list[str] = []

        import structlog
        original_log = chromadb_store.log

        class _CapturingLog:
            def warning(self, event, **kw):
                warned_paths.append(kw.get("path", ""))

            def __getattr__(self, name):
                return getattr(original_log, name)

        with patch.object(chromadb_store, "log", _CapturingLog()):
            chromadb_store._verify_chromadb_ancestor_chain(chroma_dir)

        assert any(str(chroma_dir.parent) in p for p in warned_paths), \
            f"Expected warning for permissive data dir; got warnings for: {warned_paths}"

    def test_aw_ancestor_chain_silent_when_all_private(self, tmp_path):
        """Test AW: _verify_chromadb_ancestor_chain emits no warning when all 0700."""
        from laya.db import chromadb_store

        chroma_dir = tmp_path / "data" / "chromadb"
        chroma_dir.mkdir(parents=True, mode=0o700)
        os.chmod(str(chroma_dir.parent), 0o700)
        os.chmod(str(tmp_path), 0o700)

        warned: list[bool] = []
        original_log = chromadb_store.log

        class _CapturingLog:
            def warning(self, event, **kw):
                warned.append(True)

            def __getattr__(self, name):
                return getattr(original_log, name)

        with patch.object(chromadb_store, "log", _CapturingLog()):
            chromadb_store._verify_chromadb_ancestor_chain(chroma_dir)

        assert not warned, "No warning expected when all ancestors are 0700"
