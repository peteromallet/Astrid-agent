from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

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


def _runtime_birth_identity(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = raw.rsplit(")", 1)[-1].split()
        if len(fields) >= 20:
            return f"proc-start-ticks:{fields[19]}"
    except (OSError, ValueError):
        pass
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="],
        capture_output=True,
        text=True,
        check=False,
        timeout=1,
    )
    return f"ps-lstart:{result.stdout.strip()}" if result.returncode == 0 else ""


def test_process_birth_identity_matches_runtime_worker_contract_at_comparison_boundary() -> None:
    expected = _runtime_birth_identity(os.getpid())
    assert expected
    assert generic_host._same_process_birth_identity(
        generic_host.process_birth_identity(), expected
    )


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
    ("observed", "wire"),
    [
        (
            "ps-lstart:Thu Oct 1 03:08:41 2026",
            "ps-lstart:Thu Oct  1 03:08:41 2026",
        ),
        (
            "ps-lstart:Mon Oct 12 13:18:41 2026",
            "ps-lstart:Mon Oct 12 13:18:41 2026",
        ),
    ],
)
def test_parked_host_accepts_equivalent_ps_birth_tokens_without_rewriting_evidence(
    monkeypatch, observed, wire
) -> None:
    monkeypatch.setattr(generic_host, "process_birth_identity", lambda pid=None: observed)
    worker, host = socket.socketpair()
    grant = _grant(birth=wire)
    worker.sendall(json.dumps(grant).encode() + b"\n")

    accepted = generic_host._await_worker_activation(
        host.detach(),
        operation_id="operation-1",
        channel_id="channel-1",
        credential_file="/private/worker.token",
        timeout_seconds=2,
    )
    acknowledgement = json.loads(worker.makefile("rb").readline())

    assert accepted["host"]["birth_id"] == wire
    assert acknowledgement["host"]["birth_id"] == wire
    worker.close()


@pytest.mark.parametrize(
    ("left", "right", "matches"),
    [
        ("proc-start-ticks:42", "proc-start-ticks:42", True),
        ("proc-start-ticks:42", "proc-start-ticks:43", False),
        ("opaque-birth", "opaque-birth", True),
        ("ps-lstart:Thu Oct  1 03:08:41 2026", "ps-lstart:Thu Oct 1 03:08:41 2026", True),
        ("ps-lstart:Thu Oct 1 03:08:41 2026", "ps-lstart:Thu Oct 1 03:08:42 2026", False),
        ("ps-lstart:Thu Oct 1 03:08:41 2026", "ps-lstart:Fri Oct 1 03:08:41 2026", False),
        ("ps-lstart:", "ps-lstart:", False),
        ("ps-lstart:not-a-date", "ps-lstart:not-a-date", False),
        ("ps-lstart:Thu Oct 32 03:08:41 2026", "ps-lstart:Thu Oct 32 03:08:41 2026", False),
        ("ps-lstart:Thu Oct 1 25:08:41 2026", "ps-lstart:Thu Oct 1 25:08:41 2026", False),
        ("ps-lstart:Thu Oct 1 03:08:41 2026", "proc-start-ticks:42", False),
    ],
)
def test_process_birth_identity_comparison_is_narrow_and_fail_closed(
    left, right, matches
) -> None:
    assert generic_host._same_process_birth_identity(left, right) is matches


def test_host_control_owner_observation_accepts_only_equivalent_ps_birth_tokens(
    monkeypatch,
) -> None:
    observed = "ps-lstart:Thu Oct 1 03:08:41 2026"
    monkeypatch.setattr(
        generic_host,
        "process_birth_identity",
        lambda pid=None: observed if pid == 41_001 else None,
    )

    generic_host.LocalWorkerHostControl._observe_owner(
        {"pid": 41_001, "birth_id": "ps-lstart:Thu Oct  1 03:08:41 2026"},
        label="old owner",
    )
    with pytest.raises(generic_host.HostControlRejected, match="old owner"):
        generic_host.LocalWorkerHostControl._observe_owner(
            {"pid": 41_001, "birth_id": "ps-lstart:Thu Oct  1 03:08:42 2026"},
            label="old owner",
        )
    with pytest.raises(generic_host.HostControlRejected, match="old owner"):
        generic_host.LocalWorkerHostControl._observe_owner(
            {"pid": 41_002, "birth_id": observed},
            label="old owner",
        )


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda value: value.update(channel_id="stale"), "wrong private channel"),
        (lambda value: value.update(version="old"), "version"),
        (lambda value: value.update(executor_incarnation=""), "incarnation"),
        (lambda value: value.update(evidence_digest="sha256:bad"), "digest"),
        (lambda value: value["host"].update(birth_id="replacement"), "process identity"),
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
        generic_host._await_worker_activation(
            host.detach(),
            operation_id="operation-1",
            channel_id="channel-1",
            credential_file="/private/worker.token",
            timeout_seconds=2,
        )
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
            calls.append("health")
            if len(calls) < 3:
                raise RuntimeError("disabled bearer")
            return {"status": "ok"}

    monkeypatch.setattr(generic_host.time, "sleep", lambda _seconds: None)
    generic_host._await_enabled_runtime_credential(Client(), timeout_seconds=1)
    assert calls == ["health", "health", "health"]


def _runtime_identity(*, suffix: str, epoch: int) -> dict[str, object]:
    return {
        "endpoint": "http://127.0.0.1:8765",
        "protocol": "workspace.v1",
        "schema_digest": "sha256:" + "1" * 64,
        "runtime_epoch": epoch,
        "runtime_instance_id": f"runtime-{suffix}",
        "runtime_session_id": f"session-{suffix}",
    }


def _utf8_canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def test_unicode_registration_body_uses_canonical_utf8_bytes() -> None:
    body = generic_host.RuntimeProtocolClient.executor_registration_body(
        "astrid-éxecutor",
        capabilities=[{"capability_id": "生成.vidéo"}],
        max_concurrency=1,
        resource_keys=["cœur"],
        source_digest="sha256:" + "1" * 64,
        runtime_epoch=2,
    )
    observed = generic_host._registration_body_digest(body)
    ascii_escaped = "sha256:" + hashlib.sha256(
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    assert observed == _utf8_canonical_digest(body)
    assert observed != ascii_escaped


def test_unicode_host_ack_uses_same_canonical_utf8_bytes(tmp_path: Path) -> None:
    host = generic_host.GenericPackHost(pack_roots=[])
    activation = _grant(channel="canal-é")
    control = generic_host.LocalWorkerHostControl(
        host,
        control_fd=-1,
        activation=activation,
        credential_file=tmp_path / "worker.token",
    )
    frame = {
        "command": "pause_prepare",
        "handoff_id": "transfert-é",
        "nonce_digest": "sha256:" + "9" * 64,
    }
    acknowledgement = control._ack(
        frame,
        "paused",
        activation=activation,
    )
    digest = acknowledgement.pop("ack_sha256")

    assert digest == _utf8_canonical_digest(acknowledgement)


def _registered_state(runtime: dict[str, object]) -> dict[str, object]:
    executor_body = {
        "executor_id": "astrid-pack-host",
        "capabilities": [
            {
                "capability_id": "test.echo",
                "definition_digest": "sha256:" + "2" * 64,
                "status": "ready",
                "required_resource_keys": ["cpu"],
                "estimated_scratch_bytes": 1,
                "estimated_output_bytes": 0,
                "unavailable_reason": None,
            }
        ],
        "max_concurrency": 1,
        "resource_keys": ["cpu"],
        "protocol": runtime["protocol"],
        "source_digest": "source-registration-digest",
        "source_epoch": "source-epoch-1",
        "dependency_digest": "dependency-registration-digest",
        "runtime_epoch": runtime["runtime_epoch"],
        "verified_facts": {},
    }
    registration_bodies = {
        "/v1/capabilities": [],
        "/v1/executors": [executor_body],
    }
    return {
        "executor_id": "astrid-pack-host",
        "source_epoch": "source-epoch-1",
        "runtime": dict(runtime),
        "capabilities": [
            {
                "capability_id": "test.echo",
                "capability_digest": "sha256:" + "2" * 64,
                "source_digest": "sha256:" + "3" * 64,
                "dependency_digest": "sha256:" + "4" * 64,
                "ready": True,
                "preflight_digest": "sha256:" + "5" * 64,
            }
        ],
        "registration_actor": "astrid-pack-host",
        "registration_bodies": registration_bodies,
        "registration_allowlist": [
            {
                "method": "POST",
                "path": path,
                "actor": "astrid-pack-host",
                "body_sha256": sorted(
                    generic_host._registration_body_digest(body)
                    for body in registration_bodies[path]
                ),
            }
            for path in sorted(registration_bodies)
        ],
    }


def _state_for_runtime(
    state: dict[str, object], runtime: dict[str, object]
) -> dict[str, object]:
    value = json.loads(json.dumps(state))
    value["runtime"] = dict(runtime)
    bodies = value["registration_bodies"]
    assert isinstance(bodies, dict)
    executors = bodies["/v1/executors"]
    assert isinstance(executors, list)
    executors[0]["runtime_epoch"] = runtime["runtime_epoch"]
    value["registration_allowlist"] = [
        {
            "method": "POST",
            "path": path,
            "actor": value["registration_actor"],
            "body_sha256": sorted(
                generic_host._registration_body_digest(body) for body in bodies[path]
            ),
        }
        for path in sorted(bodies)
    ]
    return value


def _pause_frame(
    old_runtime: dict[str, object],
    *,
    handoff_id: str = "handoff-1",
    nonce_digest: str = "sha256:" + "6" * 64,
    old_owner: dict[str, object] | None = None,
    deadline_seconds: float = 30,
) -> dict[str, object]:
    return {
        "version": generic_host.HOST_CONTROL_VERSION,
        "command": "pause_prepare",
        "handoff_id": handoff_id,
        "nonce_digest": nonce_digest,
        "deadline_monotonic": time.monotonic() + deadline_seconds,
        "deadline_unix_ms": int(time.time() * 1000) + int(deadline_seconds * 1000),
        "old_runtime": dict(old_runtime),
        "old_owner": dict(old_owner or _owner_identity()),
    }


def _owner_identity() -> dict[str, object]:
    return {
        "pid": os.getpid(),
        "birth_id": generic_host.process_birth_identity(),
    }


def _host_control(
    host: generic_host.GenericPackHost,
    *,
    state: dict[str, object],
    credential_file: Path,
) -> generic_host.LocalWorkerHostControl:
    host.registered_state_snapshot = (  # type: ignore[method-assign]
        lambda *, revalidate=False, runtime_override=None: _state_for_runtime(
            state,
            dict(runtime_override or state["runtime"]),
        )
    )
    return generic_host.LocalWorkerHostControl(
        host,
        control_fd=-1,
        activation=_grant(),
        credential_file=credential_file,
    )


def _write_credential_generation(root: Path) -> tuple[Path, dict[str, str]]:
    token = root / "worker.token"
    metadata = token.with_suffix(".json")
    commit = token.with_suffix(".commit")
    token_bytes = b"worker-secret\n"
    metadata_bytes = b'{"actor":"astrid-pack-host","generation":"generation-1"}\n'
    token.write_bytes(token_bytes)
    metadata.write_bytes(metadata_bytes)
    commit.write_text(
        json.dumps(
            {
                "generation": "generation-1",
                "token_sha256": "sha256:" + hashlib.sha256(token_bytes).hexdigest(),
                "metadata_sha256": "sha256:"
                + hashlib.sha256(metadata_bytes).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    for path in (token, metadata, commit):
        path.chmod(0o600)
    return token, generic_host.credential_generation_snapshot(token)


def _rebind_frame(
    pause: dict[str, object],
    *,
    new_runtime: dict[str, object],
    credential: Path,
    generation: dict[str, str],
    registered_state: dict[str, object],
) -> dict[str, object]:
    return {
        "version": generic_host.HOST_CONTROL_VERSION,
        "command": "rebind_prepare",
        "handoff_id": pause["handoff_id"],
        "nonce_digest": pause["nonce_digest"],
        "deadline_monotonic": pause["deadline_monotonic"],
        "deadline_unix_ms": pause["deadline_unix_ms"],
        "new_runtime": new_runtime,
        "credential_file": str(credential),
        "credential_generation": generation,
        "registered_state": registered_state,
        "old_owner": pause["old_owner"],
        "new_owner": _owner_identity(),
    }


class _HostControlSocket:
    def __init__(
        self,
        frame: dict[str, object],
        *,
        send_error: OSError | None = None,
        after_send=None,
    ):
        self.encoded = json.dumps(frame).encode("utf-8") + b"\n"
        self.send_error = send_error
        self.after_send = after_send
        self.sent: list[bytes] = []
        self.closed = False

    def settimeout(self, _timeout: float) -> None:
        pass

    def recv(self, _size: int) -> bytes:
        encoded, self.encoded = self.encoded, b""
        return encoded

    def sendall(self, payload: bytes) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(payload)
        if self.after_send is not None:
            self.after_send()

    def close(self) -> None:
        self.closed = True


def test_host_control_persistent_claim_refuses_pause_without_mutation(tmp_path: Path) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    state = _registered_state(old_runtime)
    credential, _generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    control = _host_control(host, state=state, credential_file=credential)
    entered = threading.Event()
    release = threading.Event()

    def claim() -> None:
        entered.set()
        assert release.wait(5)

    host._claim_once_unlocked = claim  # type: ignore[method-assign]
    thread = threading.Thread(target=host.claim_once)
    thread.start()
    assert entered.wait(2)

    before = json.loads(json.dumps(state))
    try:
        acknowledgement = control.handle_frame(
            _pause_frame(old_runtime, deadline_seconds=0.2)
        )
    finally:
        release.set()

    assert acknowledgement["status"] == "active_work"
    assert acknowledgement["phase"] == "ACTIVE"
    assert acknowledgement["inflight_claim_iterations"] == 1
    assert acknowledgement["activation"] == _grant()
    assert acknowledgement["registered_state"] == before
    assert set(acknowledgement) == {
        "version", "command", "handoff_id", "nonce_digest", "status",
        "host", "phase", "activation", "registered_state",
        "inflight_claim_iterations", "ack_sha256",
    }
    assert host.claim_gate_state == {"paused": False, "in_flight": 1}
    assert state == before
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_host_control_transient_claim_drains_before_pause(tmp_path: Path) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    state = _registered_state(old_runtime)
    credential, _generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    control = _host_control(host, state=state, credential_file=credential)
    entered = threading.Event()
    release = threading.Event()

    def claim() -> None:
        entered.set()
        assert release.wait(5)

    host._claim_once_unlocked = claim  # type: ignore[method-assign]
    thread = threading.Thread(target=host.claim_once)
    thread.start()
    assert entered.wait(2)
    timer = threading.Timer(0.02, release.set)
    timer.start()
    try:
        acknowledgement = control.handle_frame(
            _pause_frame(old_runtime, deadline_seconds=1)
        )
    finally:
        timer.cancel()

    assert acknowledgement["status"] == "paused"
    assert acknowledgement["phase"] == "PAUSED"
    assert "inflight_claim_iterations" not in acknowledgement
    assert host.claim_gate_state == {"paused": True, "in_flight": 0}
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_host_control_pause_blocks_claims_until_pause_cancel(tmp_path: Path) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    state = _registered_state(old_runtime)
    credential, _generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    control = _host_control(host, state=state, credential_file=credential)
    frame = _pause_frame(old_runtime)

    acknowledgement = control.handle_frame(frame)
    assert acknowledgement["status"] == "paused"
    assert acknowledgement["phase"] == "PAUSED"
    assert host.claim_gate_state == {"paused": True, "in_flight": 0}

    claimed = threading.Event()
    host._claim_once_unlocked = claimed.set  # type: ignore[method-assign]
    thread = threading.Thread(target=host.claim_once)
    thread.start()
    time.sleep(0.03)
    assert not claimed.is_set()

    cancelled = control.handle_frame(
        {
            "version": generic_host.HOST_CONTROL_VERSION,
            "command": "pause_cancel",
            "handoff_id": frame["handoff_id"],
            "nonce_digest": frame["nonce_digest"],
            "old_owner": frame["old_owner"],
        }
    )
    assert cancelled["status"] == "pause_cancelled"
    assert cancelled["phase"] == "ACTIVE"
    thread.join(timeout=2)
    assert claimed.is_set()
    assert host.claim_gate_state == {"paused": False, "in_flight": 0}


def test_host_control_replay_and_wrong_identity_do_not_mutate_healthy_pause(
    tmp_path: Path,
) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    state = _registered_state(old_runtime)
    credential, _generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    control = _host_control(host, state=state, credential_file=credential)
    frame = _pause_frame(old_runtime)
    first = control.handle_frame(frame)

    assert control.handle_frame(dict(frame)) == first
    changed = dict(frame)
    changed["deadline_unix_ms"] = int(changed["deadline_unix_ms"]) + 1
    with pytest.raises(generic_host.HostControlRejected, match="replay"):
        control.handle_frame(changed)
    with pytest.raises(generic_host.HostControlRejected, match="identity"):
        control.handle_frame(
            {
                "version": generic_host.HOST_CONTROL_VERSION,
                "command": "resume_prepare",
                "handoff_id": "losing-contender",
                "nonce_digest": "sha256:" + "7" * 64,
                "new_owner": _owner_identity(),
            }
        )
    assert control.state == "PAUSED"
    assert host.claim_gate_state == {"paused": True, "in_flight": 0}
    assert not host._shutdown.is_set()


def test_host_control_owner_identity_is_bound_before_replay_lookup(
    tmp_path: Path,
) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    state = _registered_state(old_runtime)
    credential, _generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    control = _host_control(host, state=state, credential_file=credential)
    frame = _pause_frame(old_runtime)

    wrong_old_owners = (
        {"pid": os.getpid() + 100_000, "birth_id": frame["old_owner"]["birth_id"]},
        {"pid": os.getpid(), "birth_id": "ps-lstart:wrong-old-owner"},
    )
    for wrong_owner in wrong_old_owners:
        wrong_initial = json.loads(json.dumps(frame))
        wrong_initial["old_owner"] = wrong_owner
        with pytest.raises(generic_host.HostControlRejected, match="old owner"):
            control.handle_frame(wrong_initial)
    assert control.state == "ACTIVE"
    assert host.claim_gate_state == {"paused": False, "in_flight": 0}

    acknowledgement = control.handle_frame(frame)
    assert acknowledgement["status"] == "paused"
    wrong_replay = json.loads(json.dumps(frame))
    wrong_replay["old_owner"]["birth_id"] = "ps-lstart:wrong-old-owner"
    with pytest.raises(generic_host.HostControlRejected, match="old owner"):
        control.handle_frame(wrong_replay)
    assert control.state == "PAUSED"
    assert host.claim_gate_state == {"paused": True, "in_flight": 0}


def test_host_control_rebind_rejects_wrong_new_owner_without_mutation(
    tmp_path: Path,
) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    new_runtime = _runtime_identity(suffix="b", epoch=2)
    state = _registered_state(old_runtime)
    credential, generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    control = _host_control(host, state=state, credential_file=credential)
    pause = _pause_frame(old_runtime)
    control.handle_frame(pause)
    frame = {
        "version": generic_host.HOST_CONTROL_VERSION,
        "command": "rebind_prepare",
        "handoff_id": pause["handoff_id"],
        "nonce_digest": pause["nonce_digest"],
        "deadline_monotonic": pause["deadline_monotonic"],
        "deadline_unix_ms": pause["deadline_unix_ms"],
        "new_runtime": new_runtime,
        "credential_file": str(credential),
        "credential_generation": generation,
        "registered_state": state,
        "old_owner": pause["old_owner"],
        "new_owner": _owner_identity(),
    }

    before = (dict(host.runtime_state), dict(host.claim_gate_state), host.client)
    wrong_new_owners = (
        {"pid": os.getpid() + 100_000, "birth_id": frame["new_owner"]["birth_id"]},
        {"pid": os.getpid(), "birth_id": "ps-lstart:wrong-new-owner"},
    )
    for wrong_owner in wrong_new_owners:
        wrong_frame = json.loads(json.dumps(frame))
        wrong_frame["new_owner"] = wrong_owner
        with pytest.raises(generic_host.HostControlRejected, match="new owner"):
            control.handle_frame(wrong_frame)
    assert control.state == "PAUSED"
    assert control.new_owner is None
    assert (dict(host.runtime_state), dict(host.claim_gate_state), host.client) == before


@pytest.mark.parametrize(
    "mutation, message",
    [
        (
            lambda frame: frame["new_runtime"].update(endpoint="http://127.0.0.1:9999"),
            "endpoint",
        ),
        (
            lambda frame: frame["new_runtime"].update(runtime_epoch=1),
            "epoch",
        ),
        (
            lambda frame: frame["new_runtime"].update(
                runtime_instance_id="runtime-a"
            ),
            "runtime_instance_id",
        ),
        (
            lambda frame: frame["credential_generation"].update(generation="other"),
            "credential generation",
        ),
    ],
)
def test_host_control_rebind_prepare_rejects_wrong_owner_facts(
    tmp_path: Path, mutation, message: str
) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    new_runtime = _runtime_identity(suffix="b", epoch=2)
    state = _registered_state(old_runtime)
    credential, generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    control = _host_control(host, state=state, credential_file=credential)
    pause = _pause_frame(old_runtime)
    control.handle_frame(pause)
    frame = {
        "version": generic_host.HOST_CONTROL_VERSION,
        "command": "rebind_prepare",
        "handoff_id": pause["handoff_id"],
        "nonce_digest": pause["nonce_digest"],
        "deadline_monotonic": pause["deadline_monotonic"],
        "deadline_unix_ms": pause["deadline_unix_ms"],
        "new_runtime": new_runtime,
        "credential_file": str(credential),
        "credential_generation": generation,
        "registered_state": state,
        "old_owner": pause["old_owner"],
        "new_owner": _owner_identity(),
    }
    mutation(frame)

    with pytest.raises(generic_host.HostError, match=message):
        control.handle_frame(frame)
    assert control.state == "PAUSED"
    assert host.claim_gate_state["paused"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state: state.update(registration_actor="different-worker"),
        lambda state: state["registration_bodies"]["/v1/executors"][0].update(
            max_concurrency=2
        ),
        lambda state: state["registration_bodies"].update(
            {"/v1/alternate": state["registration_bodies"].pop("/v1/executors")}
        ),
    ],
    ids=["alternate-actor", "alternate-body", "alternate-path"],
)
def test_host_control_rebind_prepare_rejects_altered_registration_admission(
    tmp_path: Path, mutation
) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    new_runtime = _runtime_identity(suffix="b", epoch=2)
    state = _registered_state(old_runtime)
    credential, generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    control = _host_control(host, state=state, credential_file=credential)
    pause = _pause_frame(old_runtime)
    control.handle_frame(pause)
    altered_state = json.loads(json.dumps(state))
    mutation(altered_state)

    with pytest.raises(generic_host.HostError, match="registered state does not match"):
        control.handle_frame(
            {
                "version": generic_host.HOST_CONTROL_VERSION,
                "command": "rebind_prepare",
                "handoff_id": pause["handoff_id"],
                "nonce_digest": pause["nonce_digest"],
                "deadline_monotonic": pause["deadline_monotonic"],
                "deadline_unix_ms": pause["deadline_unix_ms"],
                "new_runtime": new_runtime,
                "credential_file": str(credential),
                "credential_generation": generation,
                "registered_state": altered_state,
                "old_owner": pause["old_owner"],
                "new_owner": _owner_identity(),
            }
        )
    assert control.state == "PAUSED"
    assert host.claim_gate_state["paused"] is True


def test_host_control_rebind_revalidation_diagnostic_preserves_category_and_sanitizes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    new_runtime = _runtime_identity(suffix="b", epoch=2)
    state = _registered_state(old_runtime)
    credential, generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    control = _host_control(host, state=state, credential_file=credential)
    pause = _pause_frame(
        old_runtime,
        handoff_id="handoff/unsafe\ncredential-marker",
    )
    control.handle_frame(pause)

    def fail_revalidation(*, revalidate=False, runtime_override=None):
        assert revalidate is True
        assert runtime_override is None
        raise OSError("credential-marker raw-frame-marker")

    host.registered_state_snapshot = fail_revalidation  # type: ignore[method-assign]
    frame = _rebind_frame(
        pause,
        new_runtime=new_runtime,
        credential=credential,
        generation=generation,
        registered_state=state,
    )
    fake_socket = _HostControlSocket(frame)
    monkeypatch.setattr(generic_host.socket, "socket", lambda **_kwargs: fake_socket)

    control.serve()

    diagnostic_text = capsys.readouterr().err.strip()
    diagnostic = json.loads(diagnostic_text)
    assert diagnostic["stage"] == "registered_state_revalidation"
    assert diagnostic["category"] == "os_error"
    assert diagnostic["command"] == "rebind_prepare"
    assert diagnostic["host"]["pid"] == os.getpid()
    assert diagnostic["handoff_id_sha256"] == "sha256:" + hashlib.sha256(
        str(pause["handoff_id"]).encode("utf-8")
    ).hexdigest()
    assert "handoff_id" not in diagnostic
    assert "credential-marker" not in diagnostic_text
    assert "raw-frame-marker" not in diagnostic_text
    assert pause["nonce_digest"] not in diagnostic_text
    assert str(credential) not in diagnostic_text
    assert len(diagnostic_text.encode("utf-8")) <= 2048
    assert fake_socket.closed is True
    assert host._shutdown.is_set()


def test_host_control_rebind_ack_send_diagnostic_preserves_category_without_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    new_runtime = _runtime_identity(suffix="b", epoch=2)
    state = _registered_state(old_runtime)
    credential, generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    control = _host_control(host, state=state, credential_file=credential)
    pause = _pause_frame(old_runtime, handoff_id="handoff-ack-1")
    control.handle_frame(pause)
    frame = _rebind_frame(
        pause,
        new_runtime=new_runtime,
        credential=credential,
        generation=generation,
        registered_state=state,
    )
    fake_socket = _HostControlSocket(
        frame,
        send_error=BrokenPipeError("credential-marker raw-ack-marker"),
    )
    monkeypatch.setattr(generic_host.socket, "socket", lambda **_kwargs: fake_socket)

    control.serve()

    diagnostic_text = capsys.readouterr().err.strip()
    diagnostic = json.loads(diagnostic_text)
    assert diagnostic["stage"] == "ack_send"
    assert diagnostic["category"] == "broken_pipe"
    assert diagnostic["command"] == "rebind_prepare"
    assert diagnostic["handoff_id"] == "handoff-ack-1"
    assert "credential-marker" not in diagnostic_text
    assert "raw-ack-marker" not in diagnostic_text
    assert pause["nonce_digest"] not in diagnostic_text
    assert str(credential) not in diagnostic_text
    assert fake_socket.closed is True
    assert host._shutdown.is_set()


def test_host_control_commit_deliberately_registers_then_two_stage_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_a = {"pid": 41_001, "birth_id": "owner-a-birth"}
    owner_b = {"pid": 41_002, "birth_id": "owner-b-birth"}
    alternate_owner = {"pid": 41_003, "birth_id": "alternate-owner-birth"}
    owner_births = {
        owner_a["pid"]: owner_a["birth_id"],
        owner_b["pid"]: owner_b["birth_id"],
        alternate_owner["pid"]: alternate_owner["birth_id"],
    }

    def observed_birth(pid=None):
        if pid is None:
            return "host-process-birth"
        return owner_births.get(pid, "")

    monkeypatch.setattr(generic_host, "process_birth_identity", observed_birth)
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    new_runtime = _runtime_identity(suffix="b", epoch=2)
    current_state = _registered_state(old_runtime)
    credential, generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])

    def state_snapshot(*, revalidate=False, runtime_override=None):
        return _state_for_runtime(
            current_state,
            dict(runtime_override or current_state["runtime"]),
        )

    host.registered_state_snapshot = state_snapshot  # type: ignore[method-assign]
    control = generic_host.LocalWorkerHostControl(
        host,
        control_fd=-1,
        activation=_grant(),
        credential_file=credential,
    )
    pause = _pause_frame(old_runtime, old_owner=owner_a)
    control.handle_frame(pause)
    prepare = {
        "version": generic_host.HOST_CONTROL_VERSION,
        "command": "rebind_prepare",
        "handoff_id": pause["handoff_id"],
        "nonce_digest": pause["nonce_digest"],
        "deadline_monotonic": pause["deadline_monotonic"],
        "deadline_unix_ms": pause["deadline_unix_ms"],
        "new_runtime": new_runtime,
        "credential_file": str(credential),
        "credential_generation": generation,
        "registered_state": json.loads(json.dumps(current_state)),
        "old_owner": pause["old_owner"],
        "new_owner": owner_b,
    }
    prepared = control.handle_frame(prepare)
    assert prepared["status"] == "rebind_prepared"
    assert prepared["credential_generation"] == generation
    assert prepared["registered_state"]["runtime"] == new_runtime
    assert prepared["registered_state"]["registration_bodies"][
        "/v1/executors"
    ][0]["runtime_epoch"] == 2
    assert set(prepared) == {
        "version", "command", "handoff_id", "nonce_digest", "status",
        "host", "phase", "activation", "credential_generation",
        "registered_state", "ack_sha256",
    }
    changed_owner_replay = json.loads(json.dumps(prepare))
    changed_owner_replay["new_owner"]["birth_id"] = "ps-lstart:wrong-new-owner"
    with pytest.raises(generic_host.HostControlRejected, match="new owner"):
        control.handle_frame(changed_owner_replay)
    assert control.state == "REBIND_PREPARED"
    assert host.claim_gate_state["paused"] is True

    created = []

    class FreshRuntime:
        def __init__(self, endpoint, token):
            self.endpoint = endpoint
            self.token = token
            self.renewed = 0
            created.append(self)

        def renew_registration_session(self):
            self.renewed += 1

    monkeypatch.setattr(generic_host, "RuntimeProtocolClient", FreshRuntime)
    host._runtime_compatibility = lambda: {  # type: ignore[method-assign]
        key: value for key, value in new_runtime.items() if key != "endpoint"
    }
    deliberate_calls = []

    def register(*, deliberate=False):
        deliberate_calls.append(deliberate)
        current_state["runtime"] = dict(new_runtime)
        current_state["registration_bodies"]["/v1/executors"][0][
            "runtime_epoch"
        ] = new_runtime["runtime_epoch"]
        return {"registration": {"state": "registered"}, "withdrawn_capabilities": []}

    host.register = register  # type: ignore[method-assign]
    commit = control.handle_frame(
        {
            "version": generic_host.HOST_CONTROL_VERSION,
            "command": "rebind_commit",
            "handoff_id": pause["handoff_id"],
            "nonce_digest": pause["nonce_digest"],
            "new_owner": prepare["new_owner"],
        }
    )
    assert deliberate_calls == [True]
    assert created[0].renewed == 1
    assert host.client is created[0]
    assert commit["status"] == "rebind_committed"
    assert commit["registered_state"]["runtime"] == new_runtime
    registration_payload = {"state": "registered"}
    registration_bytes = json.dumps(
        registration_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert commit["registration"] == {
        "runtime_registration": {
            "canonical_bytes": len(registration_bytes),
            "sha256": "sha256:" + hashlib.sha256(registration_bytes).hexdigest(),
        },
        "withdrawn_capabilities": [],
    }
    assert set(commit) == {
        "version", "command", "handoff_id", "nonce_digest", "status",
        "host", "phase", "activation", "credential_generation",
        "registered_state", "registration", "ack_sha256",
    }

    claimed = threading.Event()
    host._claim_once_unlocked = claimed.set  # type: ignore[method-assign]
    thread = threading.Thread(target=host.claim_once)
    thread.start()
    prepared_resume = control.handle_frame(
        {
            "version": generic_host.HOST_CONTROL_VERSION,
            "command": "resume_prepare",
            "handoff_id": pause["handoff_id"],
            "nonce_digest": pause["nonce_digest"],
            "new_owner": prepare["new_owner"],
        }
    )
    time.sleep(0.03)
    assert prepared_resume["phase"] == "RESUME_PREPARED"
    assert set(prepared_resume) == {
        "version", "command", "handoff_id", "nonce_digest", "status",
        "host", "phase", "ack_sha256",
    }
    assert host.claim_gate_state["paused"] is True
    assert not claimed.is_set()
    committed_resume = control.handle_frame(
        {
            "version": generic_host.HOST_CONTROL_VERSION,
            "command": "resume_commit",
            "handoff_id": pause["handoff_id"],
            "nonce_digest": pause["nonce_digest"],
            "new_owner": prepare["new_owner"],
        }
    )
    thread.join(timeout=2)
    assert committed_resume["status"] == "resumed"
    assert committed_resume["phase"] == "RESUMED"
    assert set(committed_resume) == {
        "version", "command", "handoff_id", "nonce_digest", "status",
        "host", "phase", "ack_sha256",
    }
    assert claimed.is_set()

    # A lost acknowledgement may be retried only while the adoption remains
    # unpublished and the host is still in the matching result phase.
    assert control.handle_frame(
        {
            "version": generic_host.HOST_CONTROL_VERSION,
            "command": "resume_commit",
            "handoff_id": pause["handoff_id"],
            "nonce_digest": pause["nonce_digest"],
            "new_owner": prepare["new_owner"],
        }
    ) == committed_resume
    finalize = {
        "version": generic_host.HOST_CONTROL_VERSION,
        "command": "handoff_finalize",
        "handoff_id": pause["handoff_id"],
        "nonce_digest": pause["nonce_digest"],
        "new_owner": prepare["new_owner"],
    }
    finalized = control.handle_frame(finalize)
    assert finalized["status"] == "adopted"
    assert finalized["phase"] == "ADOPTED"
    assert set(finalized) == {
        "version", "command", "handoff_id", "nonce_digest", "status",
        "host", "phase", "ack_sha256",
    }
    assert control.state == "ACTIVE"
    assert control.handoff_id is None
    assert control.nonce_digest is None
    assert control.old_owner is None
    assert control.new_owner is None
    assert control.current_runtime == new_runtime
    assert control.current_owner == owner_b
    assert control._terminal_tombstone == {
        "handoff_id": pause["handoff_id"],
        "nonce_digest": pause["nonce_digest"],
        "request_digest": control._request_digest(finalize),
        "new_owner": owner_b,
        "ack": finalized,
    }
    assert control._acks == {}

    def authority_snapshot():
        return (
            host.client,
            dict(host.runtime_state),
            dict(host.claim_gate_state),
            host._shutdown.is_set(),
            control.state,
            control.handoff_id,
            control.nonce_digest,
            dict(control.current_runtime or {}),
            dict(control.current_owner or {}),
            dict(control.old_owner or {}),
            dict(control.new_owner or {}),
            dict(control._acks),
            json.loads(json.dumps(control._terminal_tombstone)),
        )

    stale_a = {
        "version": generic_host.HOST_CONTROL_VERSION,
        "command": "pause_cancel",
        "handoff_id": pause["handoff_id"],
        "nonce_digest": pause["nonce_digest"],
        "old_owner": pause["old_owner"],
    }
    for stale in (prepare, stale_a):
        authority_before = authority_snapshot()
        with pytest.raises(generic_host.HostControlRejected, match="finalized handoff is stale"):
            control.handle_frame(dict(stale))
        assert authority_snapshot() == authority_before

    authority_before = authority_snapshot()
    assert control.handle_frame(dict(finalize)) == finalized
    assert authority_snapshot() == authority_before

    for wrong_final_owner in (owner_a, alternate_owner):
        authority_before = authority_snapshot()
        altered_finalize = dict(finalize)
        altered_finalize["new_owner"] = wrong_final_owner
        with pytest.raises(
            generic_host.HostControlRejected,
            match="finalized handoff is stale",
        ):
            control.handle_frame(altered_finalize)
        assert authority_snapshot() == authority_before

    for wrong_owner in (owner_a, alternate_owner):
        authority_before = authority_snapshot()
        contender = _pause_frame(
            new_runtime,
            handoff_id=f"handoff-wrong-{wrong_owner['pid']}",
            nonce_digest="sha256:" + str(wrong_owner["pid"])[-1] * 64,
            old_owner=wrong_owner,
        )
        with pytest.raises(
            generic_host.HostControlRejected,
            match="not the current runtime owner",
        ):
            control.handle_frame(contender)
        assert authority_snapshot() == authority_before

    next_pause = _pause_frame(
        new_runtime,
        handoff_id="handoff-2",
        nonce_digest="sha256:" + "8" * 64,
        old_owner=owner_b,
    )
    next_paused = control.handle_frame(next_pause)
    assert next_paused["status"] == "paused"
    assert next_paused["phase"] == "PAUSED"
    assert control.state == "PAUSED"
    assert control.old_runtime == new_runtime
    assert control.old_owner == owner_b
    assert control.current_runtime == new_runtime
    assert control.current_owner == owner_b

    next_cancelled = control.handle_frame(
        {
            "version": generic_host.HOST_CONTROL_VERSION,
            "command": "pause_cancel",
            "handoff_id": next_pause["handoff_id"],
            "nonce_digest": next_pause["nonce_digest"],
            "old_owner": owner_b,
        }
    )
    assert next_cancelled["status"] == "pause_cancelled"
    assert control.state == "ACTIVE"
    assert control.current_runtime == new_runtime
    assert control.current_owner == owner_b


def test_host_control_serve_serializes_observed_scale_rebind_commit_below_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_a = {"pid": 42_001, "birth_id": "owner-a-birth"}
    owner_b = {"pid": 42_002, "birth_id": "owner-b-birth"}
    owner_births = {
        owner_a["pid"]: owner_a["birth_id"],
        owner_b["pid"]: owner_b["birth_id"],
    }

    def observed_birth(pid=None):
        if pid is None:
            return "host-process-birth"
        return owner_births.get(pid, "")

    monkeypatch.setattr(generic_host, "process_birth_identity", observed_birth)
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    new_runtime = _runtime_identity(suffix="b", epoch=2)
    current_state = _registered_state(old_runtime)
    capabilities = []
    definitions = []
    for index in range(77):
        capability_id = f"test.observed-scale.{index:02d}"
        capabilities.append(
            {
                "capability_id": capability_id,
                "capability_digest": "sha256:" + f"{index % 16:x}" * 64,
                "source_digest": "sha256:" + f"{(index + 1) % 16:x}" * 64,
                "dependency_digest": "sha256:" + f"{(index + 2) % 16:x}" * 64,
                "ready": True,
                "preflight_digest": "sha256:" + f"{(index + 3) % 16:x}" * 64,
            }
        )
        definitions.append(
            {
                "capability_id": capability_id,
                "definition_digest": "sha256:" + f"{(index + 4) % 16:x}" * 64,
                "status": "ready",
                "required_resource_keys": ["cpu"],
                "estimated_scratch_bytes": 1,
                "estimated_output_bytes": 0,
                "unavailable_reason": None,
            }
        )
    current_state["capabilities"] = capabilities
    current_state["registration_bodies"]["/v1/executors"][0][
        "capabilities"
    ] = definitions
    current_state = _state_for_runtime(current_state, old_runtime)
    registered_state_bytes = json.dumps(
        current_state,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert 50_000 <= len(registered_state_bytes) < generic_host.HOST_CONTROL_FRAME_LIMIT

    runtime_registration = {
        "registrations": [
            {
                "capability_id": capability["capability_id"],
                "receipt": "r" * 220,
            }
            for capability in capabilities
        ]
    }
    runtime_registration_bytes = json.dumps(
        runtime_registration,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert len(runtime_registration_bytes) > 20_000

    credential, generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])

    def state_snapshot(*, revalidate=False, runtime_override=None):
        return _state_for_runtime(
            current_state,
            dict(runtime_override or current_state["runtime"]),
        )

    host.registered_state_snapshot = state_snapshot  # type: ignore[method-assign]
    control = generic_host.LocalWorkerHostControl(
        host,
        control_fd=-1,
        activation=_grant(),
        credential_file=credential,
    )
    pause = _pause_frame(old_runtime, old_owner=owner_a)
    control.handle_frame(pause)
    prepare = _rebind_frame(
        pause,
        new_runtime=new_runtime,
        credential=credential,
        generation=generation,
        registered_state=json.loads(json.dumps(current_state)),
    )
    prepare["new_owner"] = owner_b
    assert control.handle_frame(prepare)["status"] == "rebind_prepared"

    class FreshRuntime:
        def __init__(self, endpoint, token):
            self.endpoint = endpoint
            self.token = token

        def renew_registration_session(self):
            return None

    monkeypatch.setattr(generic_host, "RuntimeProtocolClient", FreshRuntime)
    host._runtime_compatibility = lambda: {  # type: ignore[method-assign]
        key: value for key, value in new_runtime.items() if key != "endpoint"
    }

    def register(*, deliberate=False):
        assert deliberate is True
        current_state["runtime"] = dict(new_runtime)
        current_state["registration_bodies"]["/v1/executors"][0][
            "runtime_epoch"
        ] = new_runtime["runtime_epoch"]
        return {
            "registration": runtime_registration,
            "withdrawn_capabilities": [],
        }

    host.register = register  # type: ignore[method-assign]
    commit_frame = {
        "version": generic_host.HOST_CONTROL_VERSION,
        "command": "rebind_commit",
        "handoff_id": pause["handoff_id"],
        "nonce_digest": pause["nonce_digest"],
        "new_owner": owner_b,
    }
    fake_socket = _HostControlSocket(
        commit_frame,
        after_send=host._shutdown.set,
    )
    monkeypatch.setattr(generic_host.socket, "socket", lambda **_kwargs: fake_socket)

    control.serve()

    assert fake_socket.closed is True
    assert len(fake_socket.sent) == 1
    assert len(fake_socket.sent[0]) <= generic_host.HOST_CONTROL_FRAME_LIMIT
    acknowledgement = json.loads(fake_socket.sent[0].decode("utf-8"))
    assert acknowledgement["status"] == "rebind_committed"
    assert acknowledgement["registered_state"] == _state_for_runtime(
        current_state, new_runtime
    )
    assert acknowledgement["registration"] == {
        "runtime_registration": {
            "canonical_bytes": len(runtime_registration_bytes),
            "sha256": "sha256:"
            + hashlib.sha256(runtime_registration_bytes).hexdigest(),
        },
        "withdrawn_capabilities": [],
    }
    claimed_digest = acknowledgement.pop("ack_sha256")
    assert claimed_digest == generic_host._host_control_ack_digest(acknowledgement)
    hypothetical = json.loads(json.dumps(acknowledgement))
    hypothetical["registration"]["runtime_registration"] = runtime_registration
    hypothetical_bytes = json.dumps(
        hypothetical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    assert len(hypothetical_bytes) > generic_host.HOST_CONTROL_FRAME_LIMIT


def test_host_control_abort_closes_engine_custody(tmp_path: Path) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    state = _registered_state(old_runtime)
    credential, _generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    engine_closed = threading.Event()
    host.managed_tool_session = SimpleNamespace(
        close=lambda **_kwargs: engine_closed.set()
    )
    control = _host_control(host, state=state, credential_file=credential)
    pause = _pause_frame(old_runtime)
    control.handle_frame(pause)

    acknowledgement = control.handle_frame(
        {
            "version": generic_host.HOST_CONTROL_VERSION,
            "command": "handoff_abort",
            "handoff_id": pause["handoff_id"],
            "nonce_digest": pause["nonce_digest"],
            "reason": "bound_adopter_failed",
        }
    )

    assert acknowledgement["status"] == "aborted"
    assert acknowledgement["phase"] == "ABORTED"
    assert set(acknowledgement) == {
        "version", "command", "handoff_id", "nonce_digest", "status",
        "host", "phase", "ack_sha256",
    }
    assert engine_closed.is_set()
    assert host._shutdown.is_set()


def test_host_control_abort_latches_process_cleanup_uncertainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    state = _registered_state(old_runtime)
    credential, _generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    class Process:
        pid = 123

    process = Process()
    host._active_processes.add(process)
    control = _host_control(host, state=state, credential_file=credential)
    pause = _pause_frame(old_runtime)
    control.handle_frame(pause)

    def uncertain(*_args, **_kwargs):
        raise generic_host.ProcessCleanupUncertain(
            "cleanup-uncertain: host child identity changed"
        )

    monkeypatch.setattr(generic_host, "_terminate_process_group", uncertain)
    with pytest.raises(generic_host.HostError, match="owned process cleanup uncertain"):
        control.handle_frame(
            {
                "version": generic_host.HOST_CONTROL_VERSION,
                "command": "handoff_abort",
                "handoff_id": pause["handoff_id"],
                "nonce_digest": pause["nonce_digest"],
                "reason": "bound_adopter_failed",
            }
        )

    assert control.state == "ABORTED"
    assert host._shutdown.is_set()
    assert host._cleanup_uncertain is True
    assert "cleanup-uncertain" in process._astrid_cleanup_uncertain


def test_host_control_eof_terminates_owned_child_and_engine_custody(
    tmp_path: Path,
) -> None:
    old_runtime = _runtime_identity(suffix="a", epoch=1)
    state = _registered_state(old_runtime)
    credential, _generation = _write_credential_generation(tmp_path)
    host = generic_host.GenericPackHost(pack_roots=[])
    engine_closed = threading.Event()
    host.managed_tool_session = SimpleNamespace(
        close=lambda **_kwargs: engine_closed.set()
    )
    child = generic_host.popen_owned_group(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    host._track_process(child)
    worker, host_socket = socket.socketpair()
    control = _host_control(host, state=state, credential_file=credential)
    control.control_fd = host_socket.detach()
    thread = threading.Thread(target=control.serve)
    thread.start()

    worker.close()
    thread.join(timeout=3)
    child.wait(timeout=3)

    assert not thread.is_alive()
    assert host._shutdown.is_set()
    assert engine_closed.is_set()
    assert child.returncode is not None
