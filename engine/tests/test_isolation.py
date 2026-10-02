# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Guards for conftest's isolation from the developer's real Laya install (#19)."""

from pathlib import Path

import keyring

from laya import config
from laya.security.keychain import (
    delete_engine_token,
    delete_mcp_token,
    get_engine_token,
    get_mcp_token,
    store_engine_token,
    store_mcp_token,
)
from tests.conftest import _REAL_HOME, _TEST_HOME


def test_laya_home_is_throwaway():
    assert Path.home() == _TEST_HOME
    assert config.LAYA_HOME == _TEST_HOME / ".laya"
    assert not config.LAYA_HOME.is_relative_to(_REAL_HOME / ".laya")


def test_keyring_is_in_memory():
    assert type(keyring.get_keyring()).__name__ == "_InMemoryKeyring"


def test_mcp_token_roundtrip_stays_in_memory():
    store_mcp_token("lyat_isolation_check")
    assert get_mcp_token() == "lyat_isolation_check"
    delete_mcp_token()
    assert get_mcp_token() is None


def test_engine_token_roundtrip_stays_in_memory():
    store_engine_token("lyae_isolation_check")
    assert get_engine_token() == "lyae_isolation_check"
    delete_engine_token()
    assert get_engine_token() is None
