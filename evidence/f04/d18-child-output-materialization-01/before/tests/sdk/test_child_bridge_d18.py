from __future__ import annotations

import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import astrid
from astrid.sdk import invocation, _child_bridge as bridge_module
from test_public_actions_f04 import isolated, write_pack


@pytest.fixture
def channel(monkeypatch):
    local, host = socket.socketpair()
    bridge = bridge_module.ChildBridge(local, io_timeout_seconds=0.2)
    monkeypatch.setattr(bridge_module, "_bridge", bridge)
    requests, failures = [], []
    terminal = {"state": "completed", "result": {"payload": {"action_result": {"ok": False, "returncode": 42}}}}

    def serve():
        try:
            with host.makefile("rb") as stream:
                while frame := stream.readline():
                    request = json.loads(frame)
                    requests.append(request)
                    key = request["child_key"]
                    task = {"run_id": "R-" + key, "task_id": "T-" + key, "attempt_id": "A-" + key}
                    if request["op"] == "status":
                        task.update(terminal)
                    data = [] if request["op"] == "outputs" else task
                    host.sendall(json.dumps({"v": 1, "request_id": request["request_id"], "ok": True, "data": data}).encode() + b"\n")
        except (OSError, ValueError) as exc:
            failures.append(type(exc).__name__)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    yield bridge, requests, terminal
    bridge.close()
    host.close()
    worker.join(timeout=1)
    assert not worker.is_alive()


def call(root, **kwargs):
    return astrid.invoke("ordinary.echo", kind="action", extra_pack_roots=(str(root),), **kwargs)


def test_parent_policy_forwarded_outside_action_inputs(tmp_path, isolated, monkeypatch):
    root = write_pack(tmp_path)
    policy = {"capabilities": [{"capability_id": "ordinary.echo", "capability_digest": "sha256:" + "a" * 64}],
              "targets": [{"kind": "local", "id": "test"}], "input_object_ids": [], "limits": {"max_children": 2}}
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return {"run_id": "R", "task_id": "T"}
    client = SimpleNamespace(tasks=SimpleNamespace(create=create))
    assert call(root, inputs={"message": "hello"}, child_delegation=policy, client=client).ok
    assert calls[0]["child_delegation"] == policy
    assert calls[0]["spec"]["inputs"] == {"message": "hello"}
    assert "child_delegation" not in calls[0]["spec"]
    assert call(root, inputs={"message": "hello"}, client=client).ok
    assert "child_delegation" not in calls[1]


def recoverable_policy(**changes):
    pin = {"capability_id": "editorial.human_review", "capability_digest": "sha256:" + "a" * 64}
    return {"capabilities": [pin], "targets": [{"kind": "local", "id": "test"}],
            "input_object_ids": [], "limits": {"max_children": 2},
            "recoverable_outputs": [{**pin, "output_ports": ["state_result"]}], **changes}


@pytest.fixture
def generated_admission(monkeypatch):
    from banodoco_workspace_client.generated import WorkspaceClient as GeneratedWorkspaceClient
    from astrid.sdk.remote import RemoteAstridClient
    from astrid.sdk.workspace_client import WorkspaceClient

    requests = []

    def transport(method, path, headers, body):
        assert (method, path) == ("POST", "/v1/tasks")
        requests.append(json.loads(body))
        data = {"run_id": "R", "task_id": "T", "attempt_id": "A"}
        receipt = {"receipt_id": "receipt-1", "command_kind": "task.admit",
                   "idempotency_key": headers["Idempotency-Key"], "request_hash": "b" * 64,
                   "project_id": "test", "project_seq": [1, 1], "event_ids": [],
                   "result": data, "created_at": "2026-10-03T00:00:00Z"}
        return 201, {}, json.dumps({"data": data, "receipt": receipt}).encode()

    workspace = WorkspaceClient("http://127.0.0.1:1", "test-token")
    monkeypatch.setattr(workspace, "_generated", GeneratedWorkspaceClient(workspace.endpoint, transport=transport))
    # Control the catalog read only; retain RemoteTasks, the workspace facade,
    # and the actual generated admission serializer on the public invoke path.
    monkeypatch.setattr(workspace, "list_capabilities", lambda **kwargs: ([{
        "capability_id": "ordinary.echo", "definition_digest": "sha256:" + "b" * 64,
        "status": "ready"}], None))
    return RemoteAstridClient(workspace), requests


def invoke_parent_policy(root, policy, client, entry_point):
    if entry_point == "public":
        return call(root, inputs={"message": "hello"}, child_delegation=policy, client=client)
    capability = astrid.get_capability("ordinary.echo", kind="action", extra_pack_roots=(str(root),))
    return invocation._kernel_invoke(capability, kind="executor", project=None,
                                     inputs={"message": "hello"}, outputs=None,
                                     child_delegation=policy, _client=client)


def test_recoverable_policy_public_to_generated_admission_unchanged(tmp_path, isolated, monkeypatch,
                                                                   generated_admission):
    root = write_pack(tmp_path)
    client, requests = generated_admission
    policy = recoverable_policy()
    original = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    validations = []
    validate = bridge_module._validate_child_policy

    def checked(value):
        validations.append(json.loads(json.dumps(value)))
        return validate(value)

    monkeypatch.setattr(bridge_module, "_validate_child_policy", checked)
    result = invoke_parent_policy(root, policy, client, "public")
    assert result.ok and result.kernel_task_id == "T"
    assert validations == [policy, policy]  # Public invoke and _kernel_invoke both validate.
    assert len(requests) == 1
    assert json.dumps(requests[0]["child_delegation"], sort_keys=True, separators=(",", ":")) == original
    assert json.dumps(policy, sort_keys=True, separators=(",", ":")) == original
    assert requests[0]["spec"]["inputs"] == {"message": "hello"}
    assert "child_delegation" not in requests[0]["spec"]
    assert "recoverable_outputs" not in requests[0]["spec"]


@pytest.mark.parametrize("entry_point", ["public", "kernel"])
@pytest.mark.parametrize("grants", [None, False, {}, [], "state_result",
    [None], [False], [[]], [{}],
    *[[{key: value for key, value in recoverable_policy()["recoverable_outputs"][0].items()
        if key != missing}] for missing in ("capability_id", "capability_digest", "output_ports")],
    [{**recoverable_policy()["recoverable_outputs"][0], "authority": "forged"}],
    recoverable_policy()["recoverable_outputs"] * 2,
    [*recoverable_policy()["recoverable_outputs"],
     {**recoverable_policy()["recoverable_outputs"][0], "capability_digest": "sha256:" + "b" * 64}],
    *[[{**recoverable_policy()["recoverable_outputs"][0], field: value}]
      for field, values in (
          ("capability_id", (None, False, "", "ordinary.echo")),
          ("capability_digest", (None, False, "", "sha256:" + "b" * 64,
                                 "sha256:" + "A" * 64, "a" * 64)),
          ("output_ports", (None, False, "state_result", [], ["other"],
                            ["state_result", "other"], ["state_result", "state_result"], ("state_result",))),
      ) for value in values],
])
def test_recoverable_policy_invalid_grant_rejected_before_admission(tmp_path, isolated,
                                                                   generated_admission, entry_point, grants):
    root = write_pack(tmp_path)
    client, requests = generated_admission
    with pytest.raises(astrid.CapabilityValidationError, match="recoverable_outputs"):
        invoke_parent_policy(root, recoverable_policy(recoverable_outputs=grants), client, entry_point)
    assert requests == []


@pytest.mark.parametrize("entry_point", ["public", "kernel"])
@pytest.mark.parametrize("capabilities", [
    [{"capability_id": "ordinary.echo", "capability_digest": "sha256:" + "a" * 64}],
    [{"capability_id": "editorial.human_review", "capability_digest": "sha256:" + "b" * 64}],
    [{"capability_id": "editorial.human_review"}],
    [{**recoverable_policy()["capabilities"][0], "authority": "forged"}],
])
def test_recoverable_policy_requires_exact_capability_pin_before_admission(tmp_path, isolated,
                                                                         generated_admission, entry_point,
                                                                         capabilities):
    root = write_pack(tmp_path)
    client, requests = generated_admission
    with pytest.raises(astrid.CapabilityValidationError, match="recoverable_outputs"):
        invoke_parent_policy(root, recoverable_policy(capabilities=capabilities), client, entry_point)
    assert requests == []


@pytest.mark.parametrize("digest", [None, False, "", "sha256:" + "A" * 64, "a" * 64])
def test_recoverable_policy_malformed_digest_rejected_even_when_pin_matches(digest):
    policy = recoverable_policy()
    policy["capabilities"][0]["capability_digest"] = digest
    policy["recoverable_outputs"][0]["capability_digest"] = digest
    with pytest.raises(astrid.CapabilityValidationError, match="recoverable_outputs"):
        astrid.invoke("absent", kind="action", child_delegation=policy)


@pytest.mark.parametrize("explicit_key", [False, True])
def test_recoverable_policy_valid_grant_still_rejected_in_child_mode(tmp_path, isolated, monkeypatch,
                                                                  channel, explicit_key):
    root = write_pack(tmp_path)
    _, messages, _ = channel
    monkeypatch.setattr(invocation, "_kernel_invoke", lambda *a, **k: pytest.fail("normal admission"))
    policy = recoverable_policy()
    assert bridge_module._validate_child_policy(policy) == policy
    with pytest.raises(astrid.CapabilityValidationError, match="child invocation cannot set child_delegation"):
        call(root, inputs={"message": "hello"}, child_delegation=policy,
             **({"child_key": "explicit"} if explicit_key else {}))
    assert messages == []


def test_recoverable_policy_valid_grant_rejected_with_child_key_without_bridge(monkeypatch):
    monkeypatch.setattr(bridge_module, "_bridge", None)
    with pytest.raises(astrid.CapabilityValidationError, match="child invocation cannot set child_delegation"):
        astrid.invoke("absent", kind="action", child_key="explicit", child_delegation=recoverable_policy())


@pytest.mark.parametrize("policy", [False, {}, {"capabilities": [], "targets": [], "input_object_ids": []},
    {"capabilities": [{}], "targets": [{}], "input_object_ids": [], "limits": {"max_children": float("inf")}}])
def test_policy_shape_rejected_before_admission(policy):
    with pytest.raises(astrid.CapabilityValidationError):
        astrid.invoke("absent", kind="action", child_delegation=policy)


def test_bridge_exact_identity_key_result_and_no_normal_admission(tmp_path, isolated, monkeypatch, channel):
    root = write_pack(tmp_path)
    _, messages, _ = channel
    monkeypatch.setattr(invocation, "_kernel_invoke", lambda *a, **k: pytest.fail("normal admission"))
    client = SimpleNamespace(tasks=SimpleNamespace(create=lambda **k: pytest.fail("ambient credentials")))
    result = call(root, inputs={"message": "hello"}, child_key="echo-1", wait=True,
                  timeout_seconds=0.5, poll_seconds=0.01, client=client)
    assert result.ok and result.kernel_task_id == "T-echo-1" and result.kernel_attempt_id == "A-echo-1"
    assert result.raw_result["result"]["payload"]["action_result"] == {"ok": False, "returncode": 42}
    assert result.outputs == {"artifacts": [], "managed_outputs": []} and result.run_root is None
    assert [m["op"] for m in messages] == ["submit", "status", "outputs"]
    submit = messages[0]
    assert set(submit) == {"v", "request_id", "op", "child", "inputs", "input_descriptors", "child_key", "wait", "timeout_seconds", "poll_seconds"}
    assert submit["child"] == {"capability_id": "ordinary.echo", "capability_digest": "sha256:" + result.executor_version}
    assert submit["child_key"] == "echo-1" and submit["inputs"] == {"message": "hello"}
    assert submit["wait"] is True and submit["timeout_seconds"] == 0.5 and submit["poll_seconds"] == 0.01
    assert all(m["child_key"] == "echo-1" for m in messages)


def test_auto_key_is_stable_for_exact_request(tmp_path, isolated, channel):
    root = write_pack(tmp_path)
    _, messages, _ = channel
    call(root, inputs={"message": "hello"})
    call(root, inputs={"message": "hello"})
    call(root, inputs={"message": "different"})
    assert messages[0]["child_key"] == messages[1]["child_key"] != messages[2]["child_key"]


def test_validation_and_recursion_precede_bridge(tmp_path, isolated, channel):
    root = write_pack(tmp_path, actions={"echo": {"description": "Validate.",
        "invocation": {"kind": "python", "path": "actions/echo.py", "function": "echo"},
        "inputs": {"type": "object", "properties": {"message": {"type": "string"}},
                   "required": ["message"], "additionalProperties": False}}})
    _, messages, _ = channel
    for kwargs in ({"inputs": {"message": 1}}, {"inputs": {"message": "hello"}, "child_delegation": {}},
                   {"inputs": {"message": "hello"}, "idempotency_context": {"parent_attempt_id": "forged"}},
                   {"inputs": {"message": "hello"}, "child_key": "bad key"},
                   {"inputs": {"message": "hello"}, "timeout_seconds": float("nan")}):
        with pytest.raises(astrid.CapabilityValidationError):
            call(root, **kwargs)
    assert messages == []


def test_missing_and_closed_bridge_fail_closed(tmp_path, isolated, monkeypatch):
    root = write_pack(tmp_path)
    monkeypatch.setattr(bridge_module, "_bridge", None)
    monkeypatch.setattr(invocation, "_kernel_invoke", lambda *a, **k: pytest.fail("fallback"))
    with pytest.raises(astrid.CapabilityInvocationError, match="live host bridge"):
        call(root, inputs={"message": "hello"}, child_key="explicit")
    local, host = socket.socketpair()
    bridge = bridge_module._install_child_bridge(local)
    bridge.close()
    host.close()
    with pytest.raises(astrid.CapabilityInvocationError, match="closed"):
        call(root, inputs={"message": "hello"})


def test_ordered_file_bindings_have_only_relative_producer_identity(tmp_path, isolated, channel):
    root = write_pack(tmp_path, actions={"echo": {"description": "Files.",
        "invocation": {"kind": "python", "path": "actions/echo.py", "function": "echo"},
        "inputs": [{"name": "z", "type": "file"}, {"name": "a", "type": "file"}]}})
    _, messages, _ = channel
    value = {"filename": "scratch/child.txt", "media_type": "text/plain", "output_port": "text"}
    call(root, inputs={"a": "sha256:" + "b" * 64, "z": value})
    assert messages[0]["input_descriptors"] == [
        {"name": "z", "kind": "producer_file", **value},
        {"name": "a", "kind": "object", "object_id": "sha256:" + "b" * 64}]
    for filename in ("/private/foreign", "../foreign", "scratch/../../foreign", "scratch\\foreign"):
        with pytest.raises(astrid.CapabilityValidationError):
            call(root, inputs={"z": dict(value, filename=filename)})
    with pytest.raises(astrid.CapabilityValidationError):
        call(root, inputs={"z": dict(value, size=42)})
    assert len(messages) == 1


def test_concurrent_submission_during_child_wait(tmp_path, isolated, channel):
    root = write_pack(tmp_path)
    _, messages, terminal = channel
    terminal.update(state="running", result={})
    with ThreadPoolExecutor(max_workers=2) as pool:
        waiting = pool.submit(call, root, inputs={"message": "hello"}, child_key="waiting", wait=True,
                              timeout_seconds=0.2, poll_seconds=0.01)
        admitted = pool.submit(call, root, inputs={"message": "hello"}, child_key="other")
        assert admitted.result(timeout=1).ok
        timed = waiting.result(timeout=1)
    assert not timed.ok and timed.error["code"] == "task_wait_timeout"
    assert timed.error["sdk_category"] == "runtime"
    assert any(m["op"] == "submit" and m["child_key"] == "other" for m in messages)


def test_cancelled_and_strict_output_semantics(tmp_path, isolated, channel):
    root = write_pack(tmp_path, actions={"echo": {"description": "Strict output.",
        "invocation": {"kind": "python", "path": "actions/echo.py", "function": "echo"},
        "outputs": {"type": "integer"}}})
    _, _, terminal = channel
    terminal.update(state="cancelled", result={"error": {"message": "parent revoked"}})
    result = call(root, wait=True)
    assert not result.ok and result.error["code"] == "task_cancelled"
    terminal.update(state="completed", result={"payload": {"action_result": "invalid"}})
    with pytest.raises(astrid.CapabilityValidationError):
        call(root, wait=True)


@pytest.mark.parametrize("reply", [b"invalid\n", b'{"v":1,"request_id":999,"ok":true,"data":{}}\n', b"x" * (1024 * 1024 + 1)])
def test_invalid_channel_reply_is_bounded_and_revokes(reply):
    local, host = socket.socketpair()
    bridge = bridge_module.ChildBridge(local, io_timeout_seconds=0.2)
    def send():
        try:
            host.recv(65536)
            host.sendall(reply)
        except OSError:
            pass
    worker = threading.Thread(target=send, daemon=True)
    worker.start()
    with pytest.raises(astrid.CapabilityInvocationError):
        bridge.submit({"child_key": "x"})
    assert bridge._closed
    host.close()
    worker.join(timeout=1)


def test_invalid_inputs_and_oversized_request_never_reach_host(tmp_path, isolated, channel):
    root = write_pack(tmp_path)
    _, messages, _ = channel
    for inputs in ({"message": object()}, {"message": float("nan")}, {"message": 42}, {"message": "x" * 1024 * 1024},
                   {"message": "hello", "authority": "forged"}, {}):
        with pytest.raises((astrid.CapabilityValidationError, astrid.CapabilityMissingInputError)):
            call(root, inputs=inputs)
    assert not messages


def test_host_admission_rejection_preserves_runtime_result_taxonomy(tmp_path, isolated, monkeypatch):
    root = write_pack(tmp_path)
    local, host = socket.socketpair()
    bridge = bridge_module.ChildBridge(local, io_timeout_seconds=0.2)
    monkeypatch.setattr(bridge_module, "_bridge", bridge)
    error = {"code": "authorization_error", "message": "child outside policy", "details": {}}
    def reject():
        request = json.loads(host.makefile("rb").readline())
        host.sendall(json.dumps({"v": 1, "request_id": request["request_id"], "ok": False, "error": error}).encode() + b"\n")
    worker = threading.Thread(target=reject, daemon=True)
    worker.start()
    try:
        result = call(root, inputs={"message": "hello"})
        assert not result.ok and result.error == {**error, "sdk_error": "CapabilityRuntimeError", "sdk_category": "runtime"}
        assert result.kernel_task_id is None
    finally:
        bridge.close()
        host.close()
        worker.join(timeout=1)


def test_unresponsive_channel_times_out_and_revokes():
    local, host = socket.socketpair()
    bridge = bridge_module.ChildBridge(local, io_timeout_seconds=0.03)
    try:
        with pytest.raises(astrid.CapabilityInvocationError):
            bridge.status("child", "T-child")
        assert bridge._closed
    finally:
        bridge.close()
        host.close()


def test_foreign_status_fails_closed(tmp_path, isolated, channel):
    root = write_pack(tmp_path)
    _, messages, terminal = channel
    terminal["task_id"] = "T-foreign"
    with pytest.raises(astrid.CapabilityInvocationError, match="foreign task"):
        call(root, inputs={"message": "hello"}, wait=True)
    assert [item["op"] for item in messages] == ["submit", "status"]


def snapshot_receipt(**changes):
    return {"association_id": "association-1", "receipt_id": "receipt-1", "revision": 1,
            "output_port": "state_result", "digest": "sha256:" + "a" * 64,
            "size": 123, "durability": "durable", **changes}


@pytest.fixture
def snapshot_channel():
    local, host = socket.socketpair()
    bridge = bridge_module.ChildBridge(local, io_timeout_seconds=0.2)
    requests, failures = [], []
    response = {"ok": True, "data": snapshot_receipt()}

    def serve():
        try:
            with host.makefile("rb") as stream:
                while frame := stream.readline():
                    request = json.loads(frame)
                    requests.append(request)
                    host.sendall(json.dumps({"v": 1, "request_id": request["request_id"],
                                             **response}).encode() + b"\n")
        except (OSError, ValueError) as exc:
            failures.append(type(exc).__name__)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield bridge, requests, response
    finally:
        bridge.close()
        host.close()
        worker.join(timeout=1)
        assert not worker.is_alive()
        assert not failures


@pytest.mark.parametrize("filename", ["state.json", "snapshots/revision-1.json", "./snapshots/state.json"])
@pytest.mark.parametrize("size", [0, 123])
def test_snapshot_exact_request_and_durable_receipt(snapshot_channel, filename, size):
    bridge, requests, response = snapshot_channel
    response["data"] = snapshot_receipt(size=size)
    result = bridge.publish_snapshot(filename=filename, output_port="state_result", revision=1)
    assert result == response["data"]
    assert set(result) == {"association_id", "receipt_id", "revision", "output_port", "digest", "size", "durability"}
    assert requests == [{"v": 1, "request_id": 1, "op": "publish_snapshot", "filename": filename,
                         "output_port": "state_result", "revision": 1}]
    response["data"] = snapshot_receipt(revision=2, output_port="other_state")
    assert bridge.publish_snapshot(filename="snapshots/revision-2.json", output_port="other_state",
                                   revision=2) == response["data"]
    assert requests[1] == {"v": 1, "request_id": 2, "op": "publish_snapshot",
                           "filename": "snapshots/revision-2.json", "output_port": "other_state", "revision": 2}


@pytest.mark.parametrize("field,value", [
    ("filename", value) for value in (None, False, 1, "", ".", "./", "././", "/state.json",
                                      "//state.json", "../state.json", "snapshots/../state.json",
                                      "snapshots/..", "snapshots\\state.json", "state\x00.json")
] + [("output_port", value) for value in (None, False, 1, "")]
  + [("revision", value) for value in (None, True, False, 0, -1, 1.0, "1")])
def test_snapshot_invalid_request_never_reaches_host(snapshot_channel, field, value):
    bridge, requests, _ = snapshot_channel
    request = {"filename": "state.json", "output_port": "state_result", "revision": 1, field: value}
    with pytest.raises(astrid.CapabilityValidationError):
        bridge.publish_snapshot(**request)
    assert not requests and not bridge._closed
    # A successful exchange orders the host reader after any possible invalid transmission.
    assert bridge.publish_snapshot(filename="state.json", output_port="state_result", revision=1) == snapshot_receipt()
    assert len(requests) == 1 and requests[0]["request_id"] == 1


@pytest.mark.parametrize("field,value", [("filename", "x" * 1024 * 1024),
                                         ("output_port", "x" * 1024 * 1024),
                                         ("filename", "state\ud800.json")])
def test_snapshot_strict_request_bounds_precede_transmission(snapshot_channel, field, value):
    bridge, requests, _ = snapshot_channel
    with pytest.raises(astrid.CapabilityValidationError):
        bridge.publish_snapshot(**{"filename": "state.json", "output_port": "state_result",
                                   "revision": 1, field: value})
    assert not requests and not bridge._closed
    bridge.publish_snapshot(filename="state.json", output_port="state_result", revision=1)
    assert len(requests) == 1


@pytest.mark.parametrize("receipt", [None, [], "durable", {},
    *[{key: value for key, value in snapshot_receipt().items() if key != missing}
      for missing in snapshot_receipt()],
    *[snapshot_receipt(**{extra: "forged"}) for extra in
      ("authority", "credentials", "task_id", "attempt_id", "lease", "fence", "epoch", "provenance")],
    *[snapshot_receipt(**{field: value}) for field, values in (
        ("association_id", ("", None, False, 123)),
        ("receipt_id", ("", None, False, 123)),
        ("revision", (0, -1, 2, True, 1.0, "1")),
        ("output_port", ("", None, False, "foreign_port")),
        ("digest", (None, "", "sha256:" + "A" * 64, "sha256:" + "g" * 64,
                    "sha256:" + "a" * 63, "sha256:" + "a" * 65, "a" * 64,
                    "sha256:" + "a" * 64 + "\n")),
        ("size", (-1, True, False, 1.0, "123", None)),
        ("durability", (None, False, "pending", "volatile", "DURABLE")),
    ) for value in values],
])
def test_snapshot_invalid_receipt_revokes_without_acknowledgment(snapshot_channel, receipt):
    bridge, requests, response = snapshot_channel
    response["data"] = receipt
    with pytest.raises(astrid.CapabilityInvocationError, match="invalid reply"):
        bridge.publish_snapshot(filename="state.json", output_port="state_result", revision=1)
    assert bridge._closed and len(requests) == 1
    with pytest.raises(astrid.CapabilityInvocationError, match="closed"):
        bridge.publish_snapshot(filename="state.json", output_port="state_result", revision=1)
    with pytest.raises(astrid.CapabilityInvocationError, match="closed"):
        bridge.submit({"child_key": "after-invalid-receipt"})
    assert len(requests) == 1


def test_snapshot_explicit_host_rejection_is_not_a_saved_result(snapshot_channel):
    bridge, requests, response = snapshot_channel
    error = {"code": "authorization_error", "message": "snapshot outside grant", "details": {}}
    response.clear()
    response.update(ok=False, error=error)
    with pytest.raises(astrid.CapabilityInvocationError) as raised:
        bridge.publish_snapshot(filename="state.json", output_port="state_result", revision=1)
    assert raised.value.details == error and len(requests) == 1


def test_snapshot_unresponsive_channel_revokes_without_retry():
    local, host = socket.socketpair()
    bridge = bridge_module.ChildBridge(local, io_timeout_seconds=0.03)
    try:
        with pytest.raises(astrid.CapabilityInvocationError):
            bridge.publish_snapshot(filename="state.json", output_port="state_result", revision=1)
        assert bridge._closed
        with pytest.raises(astrid.CapabilityInvocationError, match="closed"):
            bridge.publish_snapshot(filename="state.json", output_port="state_result", revision=1)
        with host.makefile("rb") as stream:
            assert json.loads(stream.readline())["op"] == "publish_snapshot"
            assert stream.readline() == b""
    finally:
        bridge.close()
        host.close()
