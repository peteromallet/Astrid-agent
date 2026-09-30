from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest

from astrid.core.execution import generic_host


def _grant(*, channel: str = "channel-1", birth: str = "birth-1") -> dict[str, object]:
    return {
        "version": "runtime.local-worker-activation/v1",
        "operation_id": "operation-1",
        "channel_id": channel,
        "credential_file": "/private/worker.token",
        "executor_incarnation": "incarnation-1",
        "evidence_digest": "sha256:" + "a" * 64,
        "host": {"pid": os.getpid(), "birth_id": birth},
    }


def _local_grant():
    return {**_grant(), "acceptance_mode": "runtime-owner-receipt/v1", "activation_id": "activation-1"}


def _receive(host, *, require_receipt=False):
    return generic_host._await_worker_activation(
        host.detach(), operation_id="operation-1", channel_id="channel-1",
        credential_file="/private/worker.token", timeout_seconds=2,
        require_receipt=require_receipt,
    )


def _send(worker, value):
    worker.sendall(json.dumps(value).encode() + b"\n")


def test_local_receiver_waits_for_owner_receipt_before_final_ack(monkeypatch):
    monkeypatch.setattr(generic_host, "process_birth_identity", lambda pid=None: "birth-1")
    worker, host = socket.socketpair()
    worker.settimeout(2)
    with worker, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_receive, host)
        _send(worker, _local_grant())
        request = json.loads(worker.makefile("rb").readline())
        assert request == {
            "version": "astrid.local-worker-activation-request/v1",
            "operation_id": "operation-1", "channel_id": "channel-1",
            "grant": {key: _local_grant()[key] for key in (
                "activation_id", "credential_file", "executor_incarnation", "evidence_digest",
            )},
            "host": {"pid": os.getpid(), "birth_id": "birth-1"},
        }
        worker.settimeout(0.02)
        with pytest.raises(socket.timeout):
            worker.recv(1)  # An acceptance request alone cannot authorize ACK.
        assert not future.done()
        worker.settimeout(2)
        _send(worker, {**request, "version": "runtime.local-worker-activation-recorded/v1"})
        worker.shutdown(socket.SHUT_WR)
        ack = json.loads(worker.makefile("rb").readline())
        assert ack["version"] == "astrid.local-worker-activation-accepted/v1"
        assert ack["activation_id"] == "activation-1"
        assert future.result(timeout=2) == _local_grant()


@pytest.mark.parametrize("failure", [
    "callback", "disconnect", "malformed", "activation", "incarnation", "digest",
    "credential", "pid", "birth", "operation", "channel", "duplicate", "out_of_order",
])
def test_local_receiver_rejects_failed_or_mismatched_receipt_without_ack(monkeypatch, failure):
    monkeypatch.setattr(generic_host, "process_birth_identity", lambda pid=None: "birth-1")
    worker, host = socket.socketpair()
    worker.settimeout(2)
    with worker, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_receive, host)
        _send(worker, _local_grant())
        request = json.loads(worker.makefile("rb").readline())
        receipt = {**request, "version": "runtime.local-worker-activation-recorded/v1"}
        if failure in {"callback", "disconnect"}:
            pass  # Owner callback failed or did not confirm its receipt.
        elif failure == "malformed":
            worker.sendall(b"not-json\n")
        else:
            fields = {"activation": "activation_id", "incarnation": "executor_incarnation",
                      "digest": "evidence_digest", "credential": "credential_file"}
            if failure in fields:
                receipt["grant"][fields[failure]] = "different"
            elif failure in {"pid", "birth"}:
                receipt["host"]["pid" if failure == "pid" else "birth_id"] = "different"
            elif failure in {"operation", "channel"}:
                receipt[failure + "_id"] = "different"
            elif failure == "out_of_order":
                receipt["version"] = "astrid.local-worker-activation-accepted/v1"
            _send(worker, receipt)
            if failure == "duplicate":
                _send(worker, receipt)
        if failure != "duplicate":
            worker.shutdown(socket.SHUT_WR)
        with pytest.raises(generic_host.HostError):
            future.result(timeout=2)
        assert worker.recv(1) == b""


def test_machine_receiver_cannot_downgrade_to_legacy_ack(monkeypatch):
    monkeypatch.setattr(generic_host, "process_birth_identity", lambda pid=None: "birth-1")
    worker, host = socket.socketpair()
    with worker:
        _send(worker, _grant())
        with pytest.raises(generic_host.HostError, match="requires a Runtime acceptance receipt"):
            _receive(host, require_receipt=True)
        assert worker.recv(1) == b""


def test_local_receiver_continues_same_incarnation_when_final_ack_send_is_lost(monkeypatch):
    monkeypatch.setattr(generic_host, "process_birth_identity", lambda pid=None: "birth-1")
    send = generic_host._send_activation_frame

    def lose_ack(control, value):
        if value["version"] == "astrid.local-worker-activation-accepted/v1":
            raise BrokenPipeError("private ACK deliberately lost")
        send(control, value)

    monkeypatch.setattr(generic_host, "_send_activation_frame", lose_ack)
    worker, host = socket.socketpair()
    worker.settimeout(2)
    with worker, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_receive, host)
        _send(worker, _local_grant())
        request = json.loads(worker.makefile("rb").readline())
        _send(worker, {**request, "version": "runtime.local-worker-activation-recorded/v1"})
        worker.shutdown(socket.SHUT_WR)
        assert future.result(timeout=2) == _local_grant()
        assert worker.recv(1) == b""


def test_parked_host_accepts_one_same_process_grant_before_continuing(monkeypatch) -> None:
    monkeypatch.setattr(generic_host, "process_birth_identity", lambda pid=None: "birth-1")
    worker, host = socket.socketpair()
    completed = threading.Event()
    result: list[dict[str, object]] = []

    def wait() -> None:
        result.append(
            generic_host._await_worker_activation(
                host.detach(),
                operation_id="operation-1",
                channel_id="channel-1",
                credential_file="/private/worker.token",
                timeout_seconds=2,
            )
        )
        completed.set()

    thread = threading.Thread(target=wait)
    thread.start()
    time.sleep(0.02)
    assert not completed.is_set(), "parked host continued before an activation grant"
    worker.sendall(json.dumps(_grant()).encode() + b"\n")
    acknowledgement = json.loads(worker.makefile("rb").readline())
    thread.join(timeout=2)

    assert completed.is_set()
    assert result == [_grant()]
    assert acknowledgement["version"] == "astrid.local-worker-activation-accepted/v1"
    assert acknowledgement["host"] == {"pid": os.getpid(), "birth_id": "birth-1"}
    worker.close()


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda value: value.update(channel_id="stale"), "wrong private channel"),
        (lambda value: value.update(operation_id="stale"), "wrong private channel"),
        (lambda value: value.update(version="old"), "version"),
        (lambda value: value.update(executor_incarnation=""), "incarnation"),
        (lambda value: value.update(evidence_digest="sha256:bad"), "digest"),
        (lambda value: value["host"].update(birth_id="replacement"), "process identity"),
        (lambda value: value["host"].update(pid=os.getpid() + 1), "process identity"),
        (lambda value: value.update(credential_file="/private/other.token"), "credential"),
    ],
)
def test_parked_host_rejects_wrong_or_stale_grant(monkeypatch, mutation, message) -> None:
    monkeypatch.setattr(generic_host, "process_birth_identity", lambda pid=None: "birth-1")
    worker, host = socket.socketpair()
    grant = _grant()
    mutation(grant)
    worker.sendall(json.dumps(grant).encode() + b"\n")
    with pytest.raises(generic_host.HostError, match=message):
        descriptor = host.detach()
        generic_host._await_worker_activation(
            descriptor,
            operation_id="operation-1",
            channel_id="channel-1",
            credential_file="/private/worker.token",
            timeout_seconds=2,
        )
    with pytest.raises(OSError):
        os.fstat(descriptor)
    assert worker.recv(1) == b"", "rejected grant received an ACK"
    worker.close()


def test_targeted_direct_launch_refuses_before_bootstrap(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "astrid.core.execution.generic_host",
            "--pack-root",
            str(tmp_path),
            "--execution-target-json",
            '{"kind":"default"}',
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 2
    assert "targeted execution requires Worker-supervised activation" in completed.stderr


def test_parked_host_times_out_without_reading_a_credential(monkeypatch) -> None:
    monkeypatch.setattr(generic_host, "process_birth_identity", lambda pid=None: "birth-1")
    worker, host = socket.socketpair()
    with pytest.raises(generic_host.HostError, match="channel failed"):
        generic_host._await_worker_activation(
            host.detach(),
            operation_id="operation-1",
            channel_id="channel-1",
            credential_file="/path/that/must/not/be/read.token",
            timeout_seconds=0.02,
        )
    worker.close()


def test_activated_host_waits_for_runtime_to_enable_disabled_bearer(monkeypatch) -> None:
    calls = []

    class Client:
        def health(self):
            raise AssertionError("public health cannot prove bearer enablement")

        def _authenticate_worker(self, executor_id):
            assert executor_id == "astrid-pack-host"
            calls.append("handshake")
            if len(calls) < 3:
                raise RuntimeError("disabled bearer")

    monkeypatch.setattr(generic_host.time, "sleep", lambda _seconds: None)
    generic_host._await_enabled_runtime_credential(Client(), timeout_seconds=1)
    assert calls == ["handshake", "handshake", "handshake"]


def test_disabled_bearer_never_passes_public_health(monkeypatch) -> None:
    class Client:
        def health(self):
            return {"status": "ok"}

        def _authenticate_worker(self, executor_id):
            raise RuntimeError("disabled bearer")

    with pytest.raises(generic_host.HostError, match="was not enabled"):
        generic_host._await_enabled_runtime_credential(Client(), timeout_seconds=0.05)


@pytest.mark.parametrize("field, value", [
    ("actor_id", "foreign-host"),
    ("scopes", []),
    ("scopes", ["worker:execute", "admin"]),
    ("realm_id", ""),
    ("schema_digest", "sha256:foreign"),
    ("protocol", "foreign.v1"),
])
def test_authenticated_readiness_rejects_foreign_or_incomplete_handshake(field, value):
    from types import SimpleNamespace

    response = {
        "actor_id": "host", "scopes": ["worker:execute"], "realm_id": "realm",
        "schema_digest": "schema", "protocol": "workspace.v1",
    }
    response[field] = value
    client = generic_host.RuntimeProtocolClient.__new__(generic_host.RuntimeProtocolClient)
    client.schema_digest = "schema"
    client.generated = SimpleNamespace(handshake=lambda *args: response)
    with pytest.raises(generic_host.HostError, match="foreign or incomplete"):
        client._authenticate_worker("host")
