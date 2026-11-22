# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Reclaiming the engine port from an engine left by an earlier launch."""

import os
import signal
import socket
import subprocess
import sys

import psutil
import pytest

from laya import main

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses lsof and POSIX signals")

HOST = "127.0.0.1"

# A process that listens on the port given as argv[1] and stands in for a
# stale engine. Its command line names laya, which is how _is_laya_process
# recognises an engine. With argv[2] == "ignore-term" it ignores SIGTERM, like
# an engine whose graceful shutdown is still waiting for in-flight work.
_STALE_ENGINE = """
import signal, socket, sys, time  # stand-in for a stale laya engine
if sys.argv[2] == "ignore-term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
s = socket.socket()
s.bind(("127.0.0.1", int(sys.argv[1])))
s.listen()
print("listening", flush=True)
time.sleep(60)
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _listening(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex((HOST, port)) == 0


def _start_stale_engine(port: int, on_term: str) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", _STALE_ENGINE, str(port), on_term],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout.readline().strip() == "listening"
    return proc


@pytest.mark.parametrize("on_term", ["exit", "ignore-term"])
def test_port_is_free_when_kill_stale_engine_returns(monkeypatch, on_term):
    monkeypatch.setattr(main, "STALE_ENGINE_GRACE_SECONDS", 1.0, raising=False)
    port = _free_port()
    proc = _start_stale_engine(port, on_term)
    try:
        assert _listening(port)
        main._kill_stale_engine(HOST, port)
        assert not _listening(port)
        assert proc.wait(timeout=5) is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_escalation_does_not_signal_a_reused_pid(monkeypatch):
    port = _free_port()
    proc = _start_stale_engine(port, "ignore-term")
    get_ident = psutil.Process._get_ident
    kill = os.kill
    reused = False
    signals = []

    def identify(process):
        pid, created = get_ident(process)
        return (pid, created + 1) if pid == proc.pid and reused else (pid, created)

    def wait_for_port_release(*_):
        nonlocal reused
        reused = True
        return False

    def observe_signal(pid, sig):
        if pid == proc.pid:
            signals.append(sig)
            if sig == signal.SIGKILL:
                return
        kill(pid, sig)

    monkeypatch.setattr(psutil.Process, "_get_ident", identify)
    monkeypatch.setattr(main, "_wait_for_port_release", wait_for_port_release)
    monkeypatch.setattr(os, "kill", observe_signal)
    try:
        main._kill_stale_engine(HOST, port)
        assert signals == [signal.SIGTERM]
    finally:
        kill(proc.pid, signal.SIGKILL)
        proc.wait()
