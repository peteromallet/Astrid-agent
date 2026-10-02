"""CPU checks for the remote scripts without contacting a provider or pod."""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrid.core.execution import h3_remote_host as remote
from astrid.core.execution import generic_host
from astrid.core.execution.generic_host import HostError, _await_enabled_runtime_credential


def _claim() -> dict[str, str]:
    return {
        "schema_version": "astrid.runpod.claim.v1",
        "pod_id": "pod-123",
        "network_volume_id": "volume-456",
        "ssh": "root@198.51.100.7 -p 53603",
        "volume_mount_path": "/workspace",
    }


def _provider_status(**overrides):
    value = {
        "runpod_id": "pod-123",
        "desired_status": "RUNNING",
        "actual_status": None,
        "ip": "203.0.113.9",
        "ports": [{
            "ip": "198.51.100.7", "privatePort": 22,
            "publicPort": 53603, "type": "tcp",
        }],
    }
    value.update(overrides)
    return value


def _provider_result(stdout: str, *, returncode: int = 0):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="provider error")


def test_provider_accepts_exact_claimed_ssh_mapping_even_if_top_level_ip_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = _provider_status()
    monkeypatch.setattr(remote.subprocess, "run", lambda *_args, **_kwargs:
                        _provider_result(json.dumps(status)))

    assert remote._provider(_claim()) == status


@pytest.mark.parametrize("overrides", [
    {"runpod_id": "other-pod"},
    {"desired_status": "EXITED"},
    {"ports": [{"ip": "198.51.100.8", "privatePort": 22,
                "publicPort": 53603, "type": "tcp"}]},
    {"ports": [{"ip": "198.51.100.7", "privatePort": 22,
                "publicPort": 53604, "type": "tcp"}]},
    {"ports": [{"ip": "198.51.100.7", "privatePort": 2222,
                "publicPort": 53603, "type": "tcp"}]},
    {"ports": [{"ip": "198.51.100.7", "privatePort": 22,
                "publicPort": 53603, "type": "udp"}]},
    {"ports": [
        {"ip": "198.51.100.7", "privatePort": 22,
         "publicPort": 9999, "type": "tcp"},
        {"ip": "198.51.100.8", "privatePort": 2222,
         "publicPort": 53603, "type": "tcp"},
    ]},
])
def test_provider_rejects_identity_status_or_non_exact_ssh_mapping(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object],
) -> None:
    status = _provider_status(**overrides)
    monkeypatch.setattr(remote.subprocess, "run", lambda *_args, **_kwargs:
                        _provider_result(json.dumps(status)))

    with pytest.raises(remote.DeploymentOperationError):
        remote._provider(_claim())


@pytest.mark.parametrize("stdout", [
    "not-json",
    "[]",
    json.dumps(_provider_status(ports=None)),
    json.dumps(_provider_status(ports=[None])),
    json.dumps(_provider_status(ports=[])),
])
def test_provider_rejects_malformed_or_missing_status_ports(
    monkeypatch: pytest.MonkeyPatch, stdout: str,
) -> None:
    monkeypatch.setattr(remote.subprocess, "run", lambda *_args, **_kwargs:
                        _provider_result(stdout))

    with pytest.raises(remote.DeploymentOperationError):
        remote._provider(_claim())


def test_provider_rejects_status_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote.subprocess, "run", lambda *_args, **_kwargs:
                        _provider_result("", returncode=1))

    with pytest.raises(remote.DeploymentOperationError, match="status is unavailable"):
        remote._provider(_claim())


def test_observe_hashes_path_keys_not_expected_digest_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "release-manifest.json"
    artifact.write_bytes(b"pinned release")
    expected = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    birth = "Mon Sep 29 00:00:00 2026"
    monkeypatch.setattr(sys, "argv", ["python", "7", birth, json.dumps({str(artifact): expected})])
    monkeypatch.setattr(subprocess, "check_output", lambda *_args, **_kwargs: f"7 1 7 7 {birth}\n")
    real_open = builtins.open

    def opened(path, mode="r", *args, **kwargs):
        if path == "/proc/7/cmdline":
            return io.BytesIO(b"python\0-m\0astrid.core.execution.generic_host\0run\0--pack-root\0/tmp/pack\0--max-concurrency\0" + b"2\0")
        if path == "/proc/7/environ":
            return io.BytesIO(b"ASTRID_RUNTIME_INSTANCE_ID=runtime-1\0")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", opened)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(remote._OBSERVE, {})
    assert json.loads(output.getvalue())["checks"] == {str(artifact): expected}


@pytest.mark.parametrize("failure", ["eof", "malformed_grant", "bad_ack"])
def test_bridge_reaps_parked_child_on_activation_failure(tmp_path: Path, failure: str) -> None:
    child_code = (
        "import socket,sys,time; "
        "fd=int(sys.argv[sys.argv.index('--activation-fd')+1]); "
        "sock=socket.socket(fileno=fd); sock.recv(8192); sock.sendall(b'{}\\n'); time.sleep(30)"
        if failure == "bad_ack" else "import time; time.sleep(30)"
    )
    spec = {
        "argv": [sys.executable, "-c", child_code], "cwd": str(tmp_path), "env": {},
        "log": str(tmp_path / "child.log"), "operation_id": "op", "channel_id": "channel",
    }
    bridge = subprocess.Popen(
        [sys.executable, "-u", "-c", remote._BRIDGE, json.dumps(spec)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        identity = json.loads(bridge.stdout.readline())
        assert identity["pgid"] == identity["sid"] == identity["pid"]
        if failure == "eof":
            bridge.stdin.close()
        elif failure == "malformed_grant":
            bridge.stdin.write("not-json\n")
            bridge.stdin.flush()
        else:
            bridge.stdin.write(json.dumps({
                "version": "runtime.local-worker-activation/v1", "operation_id": "op",
                "channel_id": "channel", "credential_file": "/tmp/token",
                "executor_incarnation": "incarnation", "evidence_digest": "sha256:" + "a" * 64,
                "host": {"pid": identity["pid"], "birth_id": identity["birth_id"]},
            }) + "\n")
            bridge.stdin.flush()
        assert bridge.wait(timeout=10) != 0
        with pytest.raises(ProcessLookupError):
            os.kill(identity["pid"], 0)
    finally:
        if bridge.poll() is None:
            bridge.kill()
            bridge.wait(timeout=5)
        bridge.stdout.close()
        bridge.stderr.close()


def test_abort_verifies_exact_process_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    class SSH:
        closes = 0

        def close(self):
            self.closes += 1

    ssh = SSH()
    stdin = io.StringIO()
    handle = remote.RemoteHostHandle({}, {}, 17, "Mon Sep 29 00:00:00 2026",
                                     ssh, stdin, io.StringIO(), pgid=17, sid=17)
    monkeypatch.setattr(remote, "_exec", lambda *_args, **_kwargs:
                        "17 17 17 Mon Sep 29 00:00:00 2026")
    remote.H3RemotePreparer().abort(handle)
    remote.H3RemotePreparer().abort(handle)
    assert ssh.closes == 1
    assert handle.stopped is True


def test_public_health_cannot_prove_enabled_worker_credential() -> None:
    def denied(*_args):
        raise PermissionError("credential disabled")

    client = SimpleNamespace(
        health=lambda: {"status": "ok"},
        _authenticate_worker=denied,
    )
    with pytest.raises(HostError, match="credential was not enabled"):
        _await_enabled_runtime_credential(client, timeout_seconds=0.05,
                                          executor_id="astrid-pack-host")


def test_ready_marker_requires_task_capabilities_and_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    launch = {"support_root": "/tmp/support", "operation_id": "op", "channel_id": "channel"}
    handle = remote.RemoteHostHandle({}, launch, 17, "birth", object(), io.StringIO(), io.StringIO())
    marker = {
        "status": "ready", "pid": 17, "process_birth_id": "birth",
        "ready_capabilities": ["h3_av.transform", "vibecomfy.run"],
        "registration": {"executor_id": "astrid-pack-host"},
        "effective_capacity": {"max_concurrency": 2},
        "activation": {"operation_id": "op", "channel_id": "channel"},
    }
    monkeypatch.setattr(remote, "_exec", lambda *_args, **_kwargs: json.dumps(marker))
    remote.H3RemotePreparer().await_ready(handle, timeout_seconds=0.1)
    marker["ready_capabilities"].remove("vibecomfy.run")
    with pytest.raises(remote.DeploymentOperationError, match="did not become ready"):
        remote.H3RemotePreparer().await_ready(handle, timeout_seconds=0.1)


def test_parked_cli_authenticates_before_preflight_and_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    support = tmp_path / "data" / "runtime"
    support.mkdir(parents=True)
    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", str(support.parent))
    manifest = support / "boot-manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    credential = tmp_path / "worker.token"

    class Host:
        def __init__(self, **_kwargs):
            self.capabilities = {
                name: SimpleNamespace(ready=True) for name in ("h3_av.transform", "vibecomfy.run")
            }

        def discover(self):
            calls.append("discover")

        def preflight(self):
            calls.append("preflight")

        def run_task(self, *_args, **_kwargs):
            calls.append("run_task")
            return {}

    def acknowledge(*_args, **_kwargs):
        calls.append("acknowledge")
        credential.write_text("token", encoding="utf-8")
        credential.chmod(0o600)
        return {"version": "runtime.local-worker-activation/v1"}

    monkeypatch.setattr(generic_host, "GenericPackHost", Host)
    monkeypatch.setattr(generic_host, "_compose_cli_boot_manifest", lambda *_args: (manifest, "sha256:" + "a" * 64))
    monkeypatch.setattr(generic_host, "_await_worker_activation", acknowledge)
    monkeypatch.setattr(generic_host, "RuntimeProtocolClient", lambda *_args: SimpleNamespace(task=lambda _id: {}))
    monkeypatch.setattr(generic_host, "_await_enabled_runtime_credential",
                        lambda *_args, **_kwargs: calls.append("authenticated"))
    monkeypatch.setattr(sys, "argv", [
        "generic-host", "--pack-root", str(tmp_path),
        "--boot-manifest-path", str(manifest), "--support-root", str(support),
        "--runtime-endpoint", "http://127.0.0.1:1234", "--credential-file", str(credential),
        "--activation-fd", "3", "--activation-operation-id", "op",
        "--activation-channel-id", "channel", "--run-task", "task", "--lease-token", "lease",
    ])
    assert generic_host._cli() == 0
    assert calls == ["acknowledge", "authenticated", "discover", "preflight", "run_task"]
