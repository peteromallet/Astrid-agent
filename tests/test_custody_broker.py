from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from astrid.core.execution import custody_broker
from astrid.core.execution import process_group


def _exercise_registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pid = 43123
    identity = {"pid": pid, "birth_id": "birth-43123", "uid": os.getuid()}
    pre = {"pid": pid, "uid": os.getuid(), "pidversion": 7, "words": [1] * 8, "sha256": "sha256:" + "1" * 64}
    post = {"pid": pid, "uid": os.getuid(), "pidversion": 8, "words": [2] * 8, "sha256": "sha256:" + "2" * 64}
    token_calls = 0

    def token_details(_connection):
        nonlocal token_calls
        token_calls += 1
        return pre if token_calls == 1 else post

    monkeypatch.setattr(custody_broker.sys, "platform", "darwin")
    monkeypatch.setattr(custody_broker, "_token_details", token_details)
    broker = custody_broker.RoleBoundCustodyBroker(
        role="generic_pack_host",
        identity_provider=lambda observed_pid: identity if observed_pid == pid else None,
        ledger_root=tmp_path / "ledger",
    )
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(str(broker.socket_path))
    frame = {
        "version": custody_broker.PROTOCOL_VERSION,
        "command": "register_pre_exec",
        "run_id": broker.run_id,
        "role": broker.role,
        "pid": pid,
        "ppid": os.getpid(),
        "argv_digest": "sha256:" + "a" * 64,
    }
    custody_broker._send_frame(connection, frame)
    ack = custody_broker._read_frame(connection)
    broker.wait_until_sealed()
    connection.close()
    return broker, identity, ack


def test_registration_is_kernel_authenticated_durable_before_ack_and_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, identity, ack = _exercise_registration(tmp_path, monkeypatch)
    assert ack["status"] == "registered"
    assert ack["pid"] == identity["pid"]
    assert ack["ledger_state_digest"].startswith("sha256:")
    events = [json.loads(line) for line in broker.journal_path.read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "registration_pre_exec",
        "registration_ack_checkpoint",
        "registration_post_exec",
        "admission_sealed",
    ]
    assert all(
        event["predecessor_digest"] == (None if index == 0 else events[index - 1]["event_digest"])
        for index, event in enumerate(events)
    )
    ledger = json.loads(broker.ledger_path.read_text())
    assert ledger["state"] == "sealed"
    assert ledger["registration"]["audit_token_pidversion"] == 8


def test_registration_waits_for_chained_exec_token_to_stabilize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = 43124
    identity = {"pid": pid, "birth_id": "birth-43124", "uid": os.getuid()}
    pre = {"pid": pid, "uid": os.getuid(), "pidversion": 10, "words": [1] * 8, "sha256": "sha256:" + "1" * 64}
    launcher = {"pid": pid, "uid": os.getuid(), "pidversion": 11, "words": [2] * 8, "sha256": "sha256:" + "2" * 64}
    final = {"pid": pid, "uid": os.getuid(), "pidversion": 12, "words": [3] * 8, "sha256": "sha256:" + "3" * 64}
    calls = 0

    def token_details(_connection):
        nonlocal calls
        calls += 1
        if calls == 1:
            return pre
        if calls == 2:
            return launcher
        return final

    monkeypatch.setattr(custody_broker.sys, "platform", "darwin")
    monkeypatch.setattr(custody_broker, "_token_details", token_details)
    monkeypatch.setattr(custody_broker, "POST_EXEC_STABILITY_SECONDS", 0.01)
    broker = custody_broker.RoleBoundCustodyBroker(
        role="generic_pack_host",
        identity_provider=lambda observed_pid: identity if observed_pid == pid else None,
        ledger_root=tmp_path / "ledger",
    )
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(str(broker.socket_path))
    frame = {
        "version": custody_broker.PROTOCOL_VERSION,
        "command": "register_pre_exec",
        "run_id": broker.run_id,
        "role": broker.role,
        "pid": pid,
        "ppid": os.getpid(),
        "argv_digest": "sha256:" + "a" * 64,
    }
    custody_broker._send_frame(connection, frame)
    custody_broker._read_frame(connection)
    broker.wait_until_sealed()
    connection.close()
    assert calls >= 4
    assert broker.registration["audit_token_pidversion"] == 12
    assert broker.registration["audit_token_words"] == final["words"]


def test_cleanup_routes_only_through_registered_audit_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, identity, _ack = _exercise_registration(tmp_path, monkeypatch)
    observed = []
    monkeypatch.setattr(
        custody_broker,
        "_signal_token",
        lambda words, signum: observed.append((list(words), signum)),
    )
    broker.signal(signal.SIGTERM, expected_pid=identity["pid"])
    assert observed == [([2] * 8, signal.SIGTERM)]


def test_cleanup_fails_closed_for_changed_identity_or_unsealed_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, identity, _ack = _exercise_registration(tmp_path, monkeypatch)
    broker.identity_provider = lambda _pid: {**identity, "birth_id": "replacement"}
    monkeypatch.setattr(
        custody_broker,
        "_signal_token",
        lambda *_args: pytest.fail("changed identity was signalled"),
    )
    with pytest.raises(custody_broker.CustodyError, match="absent or changed"):
        broker.signal(signal.SIGKILL, expected_pid=identity["pid"])
    broker.state = "accepting"
    with pytest.raises(custody_broker.CustodyError, match="not sealed"):
        broker.signal(signal.SIGTERM, expected_pid=identity["pid"])


def test_registration_rejects_kernel_peer_token_for_another_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(custody_broker.sys, "platform", "darwin")
    monkeypatch.setattr(
        custody_broker,
        "_token_details",
        lambda _connection: {
            "pid": 99999,
            "uid": os.getuid(),
            "pidversion": 1,
            "words": [1] * 8,
            "sha256": "sha256:" + "1" * 64,
        },
    )
    broker = custody_broker.RoleBoundCustodyBroker(
        role="worker",
        identity_provider=lambda pid: {"pid": pid, "birth_id": "birth", "uid": os.getuid()},
        ledger_root=tmp_path / "ledger",
        timeout=0.2,
    )
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(0.5)
    connection.connect(str(broker.socket_path))
    custody_broker._send_frame(connection, {
        "version": custody_broker.PROTOCOL_VERSION,
        "command": "register_pre_exec",
        "run_id": broker.run_id,
        "role": broker.role,
        "pid": 43123,
        "ppid": os.getpid(),
        "argv_digest": "sha256:" + "a" * 64,
    })
    with pytest.raises((custody_broker.CustodyError, TimeoutError, OSError)):
        custody_broker._read_frame(connection)
    with pytest.raises(custody_broker.CustodyError, match="kernel identity differs"):
        broker.wait_until_sealed()
    connection.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin audit-token API")
def test_real_launch_registers_seals_and_signals_with_kernel_audit_token() -> None:
    process = process_group.popen_owned_group(
        ["/bin/sleep", "30"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    broker = process._astrid_custody_broker
    assert broker.state == "sealed"
    assert broker.registration["audit_token_pidversion"] != broker.registration["pre_exec_pidversion"]
    process_group.terminate_group(process, grace_seconds=0.1)
    assert process.returncode == -signal.SIGTERM


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin audit-token API")
def test_real_python_launch_seals_after_framework_launcher_and_signals() -> None:
    process = process_group.popen_owned_group(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    broker = process._astrid_custody_broker
    assert broker.state == "sealed"
    process_group.terminate_group(process, grace_seconds=0.1)
    assert process.returncode == -signal.SIGTERM


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin audit-token API")
def test_real_fast_python_launch_can_finish_during_seal() -> None:
    process = process_group.popen_owned_group(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process_group.release_group(process)
    assert process.returncode == 0
