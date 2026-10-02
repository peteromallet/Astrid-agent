"""CPU boundaries for delegated orchestration alongside the retained engine lane."""
from __future__ import annotations

import json
import hashlib
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrid.core.execution import generic_host as gh
from astrid.core.execution.host_lane_policy import canonical_pack_host_capacity
from tests.test_generic_host import FakeRuntime, _write_manifest


def _host(tmp_path, *, require_capacity_ack=False):
    packs = tmp_path / "packs"
    _write_manifest(packs / "echo")
    parent = packs / "parent"
    parent.mkdir()
    (parent / "orchestrator.yaml").write_text(json.dumps({
        "schema_version": 1, "id": "test.parent", "name": "Parent",
        "kind": "external", "version": "1.0",
        "runtime": {"kind": "command", "command": {"argv": ["{python_exec}", "-c", "pass"]}},
        "child_executors": ["test.echo"],
        "isolation": {"network": True},
    }))
    runtime = FakeRuntime()
    runtime.authorities = []

    def issue(attempt_id, **kwargs):
        runtime.authorities.append((attempt_id, kwargs))
        return {"authority": "runtime-issued-authority", "expires_at": "2099-01-01T00:00:00Z"}

    runtime.issue_child_authority = issue
    host = gh.GenericPackHost(pack_roots=[packs], client=runtime, max_concurrency=2,
                              attempt_base=tmp_path / "attempts", require_capacity_ack=require_capacity_ack)
    host.discover()
    host.nested_handoff_template = {"version": 1}
    host.runtime_state = {"runtime_epoch": 7}
    return host, runtime


def _task(runtime, capability="test.parent", *, delegation=True, task_id="parent"):
    spec = {"spec": {"inputs": {}}}
    if delegation:
        spec["child_delegation"] = {"capabilities": [{"capability_id": "test.echo", "capability_digest": "sha256:" + "a" * 64}]}
    task = {"id": task_id, "capability": capability, "attempt_id": task_id + "-attempt",
            "fence": 3, "runtime_epoch": 7, "status": "running", "spec": spec}
    runtime.tasks[task_id] = task
    return task


def test_discovery_and_readiness_keep_orchestration_off_executor_reservation(tmp_path):
    host, _ = _host(tmp_path)
    records = {record.id: record for record in host.preflight()}
    assert records["test.parent"].ready
    assert records["test.parent"].capability_kind == "orchestrator"
    assert records["test.parent"].adapter.family == "cpu"
    assert records["test.parent"].resource_keys == ("astrid-orchestration",)
    assert records["test.echo"].resource_keys == ("cpu",)
    assert gh._record_command(records["test.parent"]).argv[-1] == "pass"
    host.claim_once(lane="orchestration")
    assert host.client.claim_payload["capability_ids"] == ["test.parent"]
    host.claim_once(lane="executor")
    assert host.client.claim_payload["capability_ids"] == ["test.echo"]


def test_h3_transform_is_a_discovered_cpu_orchestrator():
    root = Path(__file__).resolve().parents[1] / "astrid" / "packs" / "h3_av"
    host = gh.GenericPackHost(pack_roots=[root], max_concurrency=2)
    host.discover()
    record = host.capabilities["h3_av.transform"]
    assert record.capability_kind == "orchestrator"
    assert record.resource_keys == ("astrid-orchestration",)
    assert "astrid.packs.h3_av.orchestrators.transform.run" in gh._record_command(record).argv


@pytest.mark.parametrize("failure", ["delegation", "attachment", "epoch", "authority"])
def test_parent_admission_fails_before_dispatch_or_spool(tmp_path, monkeypatch, failure):
    host, runtime = _host(tmp_path)
    task = _task(runtime, delegation=failure != "delegation")
    if failure == "attachment":
        host.nested_handoff_template = None
    elif failure == "epoch":
        task["runtime_epoch"] = True
    elif failure == "authority":
        runtime.issue_child_authority = lambda *args, **kwargs: {"authority": ""}
    monkeypatch.setattr(host, "invoke_capability", lambda **kwargs: pytest.fail("must not dispatch"))
    with pytest.raises(gh.HostError):
        host.run_task(task, lease_token="parent-lease")
    assert len(runtime.failures) == 1
    assert runtime.failures[0][3]["retryable"] is False
    assert not (tmp_path / "attempts").exists()
    assert not host.managed_tool_session.active


def test_parent_dispatch_carries_runtime_authority_without_touching_engine(tmp_path, monkeypatch):
    host, runtime = _host(tmp_path)
    manager = host.managed_tool_session
    host._vibecomfy_current_warmth_hint = "retained-model"
    task = _task(runtime)
    seen = {}

    def invoke(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(payload={"returncode": 0, "process_id": 123})

    monkeypatch.setattr(host, "invoke_capability", invoke)
    for operation in ("open", "admit", "observe", "settle", "release"):
        monkeypatch.setattr(manager, operation, lambda *args, **kwargs: pytest.fail("parent touched native manager"))
    result = host.run_task(task, lease_token="parent-lease")
    assert result["task"]["status"] == "completed"
    assert host.managed_tool_session is manager
    assert host._vibecomfy_current_warmth_hint == "retained-model"
    assert runtime.authorities == [("parent-attempt", {"lease_id": "parent-lease", "fence": 3, "runtime_epoch": 7})]
    assert seen["capability_kind"] == "orchestrator"
    assert seen["admission"]["child_authority"] == "runtime-issued-authority"
    assert seen["admission"]["child_delegation"] == task["spec"]["child_delegation"]
    assert "managed_tool_session" not in runtime.settlements[0][2]["result"]


def test_parent_waits_while_executor_uses_the_single_engine_manager(tmp_path, monkeypatch):
    host, runtime = _host(tmp_path)
    parent = _task(runtime)
    child = _task(runtime, "test.echo", delegation=False, task_id="child")
    entered = threading.Event()
    child_finished = threading.Event()
    errors = []

    def invoke(**kwargs):
        entered.set()
        assert child_finished.wait(10), "executor lane deadlocked behind orchestration"
        return SimpleNamespace(payload={"returncode": 0, "process_id": 123})

    monkeypatch.setattr(host, "invoke_capability", invoke)

    def run_parent():
        try:
            host.run_task(parent, lease_token="parent-lease")
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run_parent)
    thread.start()
    try:
        assert entered.wait(5)
        assert host.run_task(child, lease_token="child-lease")["task"]["status"] == "completed"
    finally:
        child_finished.set()
        thread.join(10)
    assert not thread.is_alive()
    assert not errors
    settlements = {row[0]: row[2] for row in runtime.settlements}
    assert "managed_tool_session" in settlements["child"]["result"]
    assert "managed_tool_session" not in settlements["parent"]["result"]
    assert len(runtime.authorities) == 1
    assert not host._active_processes and not host._orchestration_processes


def test_registration_requires_exact_two_lane_capacity_ack(tmp_path):
    host, runtime = _host(tmp_path, require_capacity_ack=True)
    with pytest.raises(gh.HostRegistrationError, match="capacity/resources"):
        host.register()

    def register(executor_id, **payload):
        return {"max_concurrency": payload["max_concurrency"], "resource_keys": payload["resource_keys"]}

    runtime.register_executor = register
    capacity = host.register()["effective_capacity"]
    assert capacity == {**canonical_pack_host_capacity(), "registered_resource_keys": ["astrid-orchestration", "cpu"]}


def test_parallel_limit_drains_children_of_an_admitted_parent(tmp_path, monkeypatch):
    host, _ = _host(tmp_path)
    parent_started = threading.Event()
    children_finished = threading.Event()
    child_count = 0

    def claim(*, lane):
        nonlocal child_count
        if lane == "orchestration":
            parent_started.set()
            assert children_finished.wait(5)
            return {"task": "parent"}
        assert parent_started.wait(5)
        child_count += 1
        if child_count == 2:
            children_finished.set()
        if child_count <= 2:
            return {"task": f"child-{child_count}"}
        return None

    monkeypatch.setattr(host, "claim_once", claim)
    results = host.run(max_tasks=1, poll_seconds=0.01)
    assert {row["task"] for row in results} == {"parent", "child-1", "child-2"}


def test_cli_capacity_is_bounded_and_one_off_modes_remain_serial():
    assert gh._effective_cli_max_concurrency("run", None) == 2
    assert gh._effective_cli_max_concurrency("supervise", None) == 1
    assert gh._effective_cli_max_concurrency("discover", None) == 1
    assert gh._effective_cli_max_concurrency("run", 1) == 1
    assert gh._effective_cli_max_concurrency("run", 99) == 2


def test_nested_handoff_is_private_and_overrides_untrusted_child_environment(tmp_path, monkeypatch):
    from astrid.sdk.host_bootstrap import NESTED_HANDOFF_HASH_ENV, NESTED_HANDOFF_PATH_ENV

    host, _ = _host(tmp_path)
    definition, admission = host.admit("orchestrator", "test.parent")
    admission = {**admission, "attempt_id": "parent-attempt", "lease_id": "parent-lease",
                 "fence": 3, "runtime_epoch": 7, "child_authority": "runtime-issued",
                 "child_delegation": {"capabilities": []}}
    # This unit exercises creation/transport; SDK separately authenticates the
    # ready-marker attachment against Runtime and the live process identity.
    monkeypatch.setattr(gh, "_refresh_owned_readiness_ack", lambda template: None)
    observed = {}

    def inspect_launch(*args, **kwargs):
        env = kwargs["env"]
        path = Path(env[NESTED_HANDOFF_PATH_ENV])
        data = path.read_bytes()
        observed.update(path=path, payload=json.loads(data))
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert env[NESTED_HANDOFF_HASH_ENV] == "sha256:" + hashlib.sha256(data).hexdigest()
        raise OSError("fixture stops before child launch")

    monkeypatch.setattr(gh, "popen_owned_group", inspect_launch)
    with pytest.raises(OSError, match="fixture stops"):
        host.invoke_capability(
            capability_kind="orchestrator", capability_id="test.parent",
            request={"inputs": {}, "out": str(tmp_path / "attempt" / "outputs")},
            definition=definition, admission=admission, attempt=tmp_path / "attempt",
            child_env={NESTED_HANDOFF_PATH_ENV: "/untrusted", NESTED_HANDOFF_HASH_ENV: "untrusted"},
        )
    assert observed["payload"]["child_authority"] == "runtime-issued"
    assert observed["payload"]["parent_attempt_id"] == "parent-attempt"
    assert observed["payload"]["parent_lease_id"] == "parent-lease"
    assert observed["payload"]["parent_fence"] == 3
    assert observed["payload"]["parent_runtime_epoch"] == 7
    assert not observed["path"].exists()


def test_claim_rejects_a_capability_from_the_other_lane(tmp_path, monkeypatch):
    host, runtime = _host(tmp_path)
    _task(runtime)
    monkeypatch.setattr(runtime, "claim_next", lambda **kwargs: {
        "task_id": "parent", "attempt_id": "parent-attempt", "lease_id": "parent-lease",
        "fence": 3, "runtime_epoch": 7,
    })
    with pytest.raises(gh.HostError, match="outside the requested host lane"):
        host.claim_once(lane="executor")
    assert len(runtime.failures) == 1
    assert not runtime.authorities


def test_generated_health_identity_survives_host_readiness(tmp_path):
    host, runtime = _host(tmp_path)
    runtime.health = lambda: SimpleNamespace(
        status="ok", protocol="workspace.v1", schema_digest=runtime.schema_digest,
        runtime_epoch=7, runtime_instance_id="runtime-instance", coordinator_epoch=9,
    )
    state = host._runtime_compatibility()
    assert state["runtime_instance_id"] == "runtime-instance"
    assert state["coordinator_epoch"] == 9


def test_main_generation_annotations_remain_discoverable():
    packs = Path(__file__).resolve().parents[1] / "astrid" / "packs"
    host = gh.GenericPackHost(pack_roots=[packs / "generation"])
    host.discover()
    for modality in ("image", "video", "audio"):
        record = host.capabilities[f"generation.generate_{modality}"]
        expected = {"image": "generated_images", "video": "generated_videos", "audio": "generated_audio"}[modality]
        assert gh._generation_output_port(record, {"modality": modality}) == expected


def test_explicit_publication_still_rejects_a_missing_version():
    definition = SimpleNamespace(metadata={
        "output_result_manifest": True,
        "generation_publication": {"modality": "video", "output_port": "verified_candidate"},
    })
    with pytest.raises(gh.GenerationPublicationError, match="exactly version"):
        gh._host_generation_publication(definition)
