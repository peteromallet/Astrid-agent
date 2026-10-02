"""CPU identity seams, including a measured producer -> local Runtime/host.

Provider and inference are simulated. The measured-capsule case uses an actual
isolated local Runtime and managed Vibe session; no provider or SSH is opened.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from astrid.core.execution.generic_host import GenericPackHost, project_deployment_launch
from astrid.core.execution.runpod_deployment import (
    AstridRuntimeTaskAdapter, ConcreteDeploymentOperations, DeploymentOperationError,
    DeploymentRequest, RunPodClaimHelperAdapter, run_existing_h3_task, resume_h3_settlement,
)
from scripts import h3_runpod_qualification as qualification
from scripts import run_h3_canonical_on_runpod as candidate
from tests.test_h3_runpod_qualification import lane_boundary
from tests.test_vibecomfy_production_engine import _managed_result_fixture


@pytest.fixture
def measured_capsule(tmp_path):
    root = candidate.PRODUCT_SOURCE_ROOT
    capsule = tmp_path / "current-t2-capsule.json"
    capsule.write_text(json.dumps({
        "schema_version": "astrid.h3.source-capsule.v1",
        "source_root": str(root),
        "expected_branch": subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=root, text=True).strip(),
        "expected_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "content_digest": candidate.candidate_content_digest(root),
    }, sort_keys=True))
    return capsule


@pytest.mark.parametrize("field,value", [
    ("content_digest", "sha256:" + "0" * 64),
    ("expected_head", "0" * 40),
    ("expected_branch", "foreign-branch"),
    ("source_root", "/foreign/source"),
])
def test_measured_capsule_rejects_before_any_runtime_or_session(
    measured_capsule, tmp_path, field, value,
):
    from tests.helpers.h3_cpu_runtime import H3CpuRuntime

    capsule = json.loads(measured_capsule.read_text())
    capsule[field] = value
    measured_capsule.write_text(json.dumps(capsule))
    root = tmp_path / "must-not-start"
    with pytest.raises(candidate.SourceCustodyError, match="mismatch"):
        H3CpuRuntime(root, source_capsule=measured_capsule)
    assert not root.exists()


@pytest.mark.parametrize("damage", ["bytes", "attestation", "foreign-ack"])
def test_owned_readiness_renewal_rejects_tamper_without_publishing(tmp_path, monkeypatch, damage):
    import os
    from astrid.core.execution import generic_host

    profile = {"vibecomfy_session": {"source_content_digest": "sha256:" + "a" * 64,
                                     "pid": 123}}
    path = tmp_path / "readiness.json"
    path.write_text(json.dumps(profile))
    handoff = {"readiness_profile_path": str(path), "readiness_profile_hash": "old-owner-hash",
               "vibecomfy_execution_attestation": generic_host._vibecomfy_execution_attestation(profile),
               "ready_file": str(tmp_path / "ready.json"), "support_root": str(tmp_path)}
    ack = {**handoff, "pid": os.getpid(), "process_birth_id": generic_host.process_birth_identity()}
    ready = Path(handoff["ready_file"])
    state = tmp_path / "generic-host.json"
    if damage == "foreign-ack":
        ack["pid"] = -1
    ready.write_text(json.dumps(ack))
    state.write_text(json.dumps(ack))
    before = (ready.read_bytes(), state.read_bytes(), dict(handoff))
    if damage == "attestation":
        profile["vibecomfy_session"]["source_content_digest"] = "sha256:" + "b" * 64
        path.write_text(json.dumps(profile))
    monkeypatch.setenv("ASTRID_HOST_READINESS_PROFILE_PATH", str(path))
    monkeypatch.setenv("ASTRID_HOST_READINESS_PROFILE_HASH",
                       "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest())
    if damage == "bytes":
        path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(generic_host.HostError):
        generic_host._refresh_owned_readiness_ack(handoff)
    assert (ready.read_bytes(), state.read_bytes(), handoff) == before


@pytest.mark.timeout(360)
def test_measured_capsule_actual_runtime_host_l1_inputs_and_readback(
    measured_capsule, tmp_path, monkeypatch,
):
    from tests.helpers.h3_cpu_runtime import H3CpuRuntime, WORKSPACE_ROOT
    from tests.packs.h3_av.test_runtime_cpu_e2e import (
        _assert_attempt_fences, _assert_generation_readback, _field, _tasks, _generations,
    )
    from astrid.sdk import host_bootstrap
    from astrid.packs.vibecomfy.asset_manifest import read_archive

    # These are the exact L1 shared offline inputs, not generated lookalikes.
    request = WORKSPACE_ROOT / "runs/matrix-minkhole/h3-canonical-submit-20260924/request.json"
    bundle = WORKSPACE_ROOT / (
        ".otto/runs/h3-av-simplicity-20260924-T2/outputs/"
        "h3-canonical-submit-20260929/staged-inputs/h3-inputs.zip"
    )
    assert hashlib.sha256(request.read_bytes()).hexdigest() == (
        "9a2c11c11a3ee81244821fd8420e3d8c78e5ce22332f6570c2419aca2f80d7c8")
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == (
        "c27ff8ebf6ce15821573920837e908fec75735b38711b390a88be29716d32d2b")
    identity = candidate.assert_product_source_identity(capsule=measured_capsule)
    runtime = H3CpuRuntime(tmp_path / "actual-runtime", source_capsule=measured_capsule)
    session_dir = runtime.schema_session.session_dir
    try:
        assert runtime.source_identity == identity
        assert runtime.launcher_value["source_checkout_digest"] == identity["scoped_source_digest"]
        state = json.loads(runtime.host_state_path.read_text())
        assert state["source_checkout_digest"] == identity["scoped_source_digest"]
        assert state["source_checkout"] == identity["source_root"]
        ready = json.loads(Path(runtime.host["host_ready_file"]).read_text())
        assert ready["identity_attestation"]["target"] == runtime.execution_target

        # Exercise rejection against the actual Runtime credential and source.
        # A rejected handoff must not start a replacement host process.
        with monkeypatch.context() as guarded:
            popen = host_bootstrap.subprocess.Popen

            def reject_host_start(argv, *args, **kwargs):
                if "--source-checkout" in argv:
                    pytest.fail("rejected identity started a host")
                return popen(argv, *args, **kwargs)

            guarded.setattr(host_bootstrap.subprocess, "Popen", reject_host_start)
            for field, value in (
                ("source_checkout_digest", "0" * 64),
                ("source_closure_digest", "0" * 64),
                ("source_inventory_identity", "foreign-inventory"),
                ("runtime_instance_id", "foreign-runtime"),
                ("runtime_session_id", "foreign-session"),
                ("runtime_epoch", 999999),
                ("readiness_profile_hash", "sha256:" + "0" * 64),
                ("worker_actor", "unauthorized-actor"),
            ):
                with pytest.raises(host_bootstrap.PackHostBootstrapError):
                    host_bootstrap.ensure_pack_host(
                        {**runtime.launcher_value, field: value}, reconfigure_action="CPU rejection")

        first = runtime.run_public_transform(
            request, bundle, out=tmp_path / "first", key="w45-l1-first")
        assert first.ok, first.error
        tasks = _tasks(runtime)
        _assert_attempt_fences(runtime, tasks, str(first.kernel_task_id))
        for row in tasks:
            if _field(row, "task_id") != first.kernel_task_id:
                assert _field(row, "spec")["delegated_parent"]["parent_attempt_id"] == first.kernel_attempt_id
        vibe = next(row for row in tasks if _field(row, "capability_id") == "vibecomfy.run")
        managed = _field(vibe, "result")["managed_tool_session"]
        assert managed["manager"] == "astrid-pack-host"
        assert managed["capability"]["ownership_mode"] == "manager_owned"
        assert managed["capability"]["terminate_authority"] == "generic_pack_host"
        assert managed["state"] == "settled"
        first_session = runtime.schema_session.registry_binding()
        first_config = (session_dir / "config.json").read_bytes()
        queued = json.loads(runtime.schema_session.queue_evidence_path.read_text())
        source = read_archive(bundle).manifest["assets"][0]
        assert queued["sha256"] == source["sha256"]
        assert queued["size"] == source["size"]
        generation_id, payload = _assert_generation_readback(runtime, tmp_path)

        second = runtime.run_public_transform(
            request, bundle, out=tmp_path / "second", key="w45-l1-second")
        assert second.ok, second.error
        assert second.kernel_task_id != first.kernel_task_id
        assert second.kernel_attempt_id != first.kernel_attempt_id
        second_tasks = [row for row in _tasks(runtime)
                        if _field(row, "task_id") not in {_field(t, "task_id") for t in tasks}]
        _assert_attempt_fences(runtime, second_tasks, str(second.kernel_task_id))
        for row in second_tasks:
            if _field(row, "task_id") != second.kernel_task_id:
                assert _field(row, "spec")["delegated_parent"]["parent_attempt_id"] == second.kernel_attempt_id
        second_session = runtime.schema_session.registry_binding()
        # H3's intervening CPU children release the serial Vibe capacity. The
        # owner renews the session, retaining source/config and host identity.
        assert (session_dir / "config.json").read_bytes() == first_config
        assert second_session["launch_token"] != first_session["launch_token"]
        # Publication belongs to each delegated finalizer, whose parent task
        # and attempt were checked above, rather than to the orchestrator row.
        finalizer_ids = {_field(row, "task_id") for row in tasks + second_tasks
                         if _field(row, "capability_id") == "h3_av.publication_finalizer"}
        assert len(finalizer_ids) == 2
        assert {_field(row, "source_task_id") for row in _generations(runtime)} == finalizer_ids
        renewed_state = json.loads(runtime.host_state_path.read_text())
        assert renewed_state["pid"] == runtime.host_pid
        assert host_bootstrap._host_identity_matches(renewed_state)
        assert not runtime.host_errors
        assert generation_id and payload
    finally:
        runtime.close()
        assert not host_bootstrap._host_identity_matches(
            json.loads(runtime.host_state_path.read_text()))
        assert not (session_dir / "pid").exists()
        assert not (session_dir / "comfy_pid").exists()


@pytest.fixture
def connected(lane_boundary, tmp_path, monkeypatch):
    boundary = lane_boundary
    task = boundary.task
    task.update(attempt_id="lane-a-attempt", status="failed")
    claim = json.loads(boundary.kwargs["handle_path"].read_text())
    events = []
    controls = []
    activations = []
    fail = SimpleNamespace(ready=False, revoke=False, terminate=False)
    identity = boundary.kwargs["source_identity"]
    monkeypatch.setattr(qualification, "source_checkout_digest", lambda _p: identity["scoped_source_digest"])
    monkeypatch.setattr(qualification, "_provider", lambda _h: events.append("fake-provider-observe"))
    monkeypatch.setattr(qualification, "_remote_file_hashes", lambda _h, paths: {
        p: "sha256:" + "a" * 64 for p in paths})
    monkeypatch.setattr(qualification, "_remote_boot_manifest_hash", lambda *_a: "sha256:" + "b" * 64)
    monkeypatch.setattr(qualification, "_loss_observer", lambda target: {
        "status": "absent", "no_active_work": True, "target": target,
        "evidence_digest": "sha256:" + "e" * 64})

    def control(task_id, body):
        assert task_id == task["id"]
        controls.append((task_id, copy.deepcopy(body)))
        action = body["action"]
        events.append(action)
        if action == "provision":
            assert body["qualification"]["run_id"] == task["run_id"]
            return {"credential_actor": boundary.config.executor_id,
                    "credential_file": boundary.config.paths()["credential"]}
        if action == "revoke" and fail.revoke:
            raise OSError("fake revoke unavailable")
        return {"enable": {"enabled": True}, "verify": {"fresh": True},
                "revoke": {"revoked": True}}[action]

    owner_transport = qualification.WorkspaceClient(None, None)
    owner_transport.control_remote_credential = control
    def record(task_id, projection):
        assert (task_id, projection["run_id"]) == (task["id"], task["run_id"])
        activations.append(copy.deepcopy(projection))
        events.append("record-activation")
        return projection
    owner_transport.record_remote_activation = record
    owner_transport.revoke_remote_activation = lambda *_a: events.append("revoke-activation")

    class Preparer:
        def prepare(self, launch):
            events.append("park")
            assert launch["local_source"] == str(candidate.PRODUCT_SOURCE_ROOT)
            # Actual candidate construction/discovery is separately checked below.
            return launch
        def acknowledge(self, _handle, grant):
            events.append("ack")
            return {key: grant[key] for key in ("activation_id", "executor_incarnation", "evidence_digest")}
        def await_ready(self, _handle):
            events.append("ready")
            if fail.ready:
                raise OSError("fake readiness lost after activation")
        def abort(self, _handle):
            events.append("abort")

    def observation(_handle):
        ref = owner.reference_factory(task, claim)
        from runtime_protocol.remote_worker_activation import _digest
        return {
            "target": ref.effective_target, "provider_identity": {"account_ref": "account-a"},
            "process": {"pid": 123, "pgid": 123, "sid": 123, "birth_id": "fake-birth"},
            "child": {"attached": True, "lanes": ["orchestration", "executor"], "birth_id": "fake-child"},
            "runtime_instance_id": ref.runtime_instance_id, "runtime_epoch": ref.runtime_epoch,
            "runtime_session_id": boundary.config.runtime_session_id,
            "source_closure_digest": ref.source_closure_digest,
            "dependency_closure_digest": _digest([
                {"name": a.name, "path": str(a.path), "digest": a.digest} for a in ref.dependency_closure]),
            "model_root": str(ref.model_root), "session_ref": ref.session_ref,
            "data_root": str(ref.data_root), "support_root": str(ref.support_root),
            "capacity": ref.capacity, "model_inventory_digest": "sha256:" + "f" * 64,
            "session_config_digest": ref.session_config_digest,
        }
    monkeypatch.setattr(qualification, "H3RemotePreparer", Preparer)
    monkeypatch.setattr(qualification, "H3RemoteInspector", lambda: SimpleNamespace(observe=observation))
    owner = qualification.default_qualification_factory(**{**boundary.kwargs, "preflight_only": False})
    ref = owner.reference_factory(task, claim)
    argv, env = project_deployment_launch(ref)
    assert env["ASTRID_TASK_ID"] == task["id"]
    assert env["ASTRID_RUN_ID"] == task["run_id"]
    assert str(ref.source_checkout) in argv

    client = boundary.kwargs["client"]
    def retry(task_id, **kwargs):
        assert task_id == task["id"]
        assert kwargs == {"idempotency_key": "op-a", "expected_version": 7}
        events.append("retry")
        task["status"] = "running"
        task["version"] += 1
        return {"task_id": task_id, "run_id": task["run_id"], "attempt_id": task["attempt_id"]}
    client.tasks.retry = retry
    client.tasks.control_remote_credential = control

    def execute(task_id, run_id):
        assert (task_id, run_id) == (task["id"], task["run_id"])
        from astrid.packs.vibecomfy import production_engine
        run = importlib.import_module("astrid.packs.vibecomfy.executors.run.run")
        envelope, payload = _managed_result_fixture(tmp_path, task_id=task_id)
        payload["attempt_id"] = task["attempt_id"]
        envelope.write_text(json.dumps(payload))
        def inference(_workflow, _out, **kwargs):
            events.append("fake-inference")
            assert kwargs["task_identity"] == task_id
            assert kwargs["attempt_identity"] == task["attempt_id"]
            return production_engine.ProductionRunResult(
                outputs=(envelope.parent / "outputs/final.mp4",),
                managed_generation_result_path=envelope, managed_generation_result=payload)
        monkeypatch.setattr(production_engine, "run_workflow_result_path", inference)
        readiness = tmp_path / "readiness.json"
        readiness.write_text(json.dumps({"vibecomfy_session": {}}))
        manifest = run._run_and_settle(
            tmp_path / "workflow.py", tmp_path / "vibe-spool", task_identity=task_id,
            attempt_identity=task["attempt_id"], readiness_profile_path=str(readiness),
            readiness_profile_hash="sha256:" + hashlib.sha256(readiness.read_bytes()).hexdigest())
        assert (manifest["managed_generation_result"]["task_id"],
                manifest["managed_generation_result"]["attempt_id"]) == (task_id, task["attempt_id"])
        row = manifest["outputs"][0]
        data = (tmp_path / "vibe-spool" / row["path"]).read_bytes()
        managed = {**row, "object_id": row["content_hash"], "size": len(data),
                   "task_id": task_id, "attempt_id": task["attempt_id"], "association_id": "lane-a-output"}
        from astrid.packs.h3_av.orchestrators.transform.run import _materialize_output
        result = SimpleNamespace(outputs={"managed_outputs": [managed]}, raw_result={},
                                 kernel_task_id=task_id, kernel_attempt_id=task["attempt_id"], kernel_run_id=run_id)
        path, readback = _materialize_output(
            SimpleNamespace(media=SimpleNamespace(read_bytes=lambda _id: data)), result,
            "vibecomfy_run", tmp_path / "h3-readback")
        receipt = json.loads(Path(readback["receipt_path"]).read_text())
        assert (receipt["task_id"], receipt["run_id"], receipt["attempt_id"]) == (
            task_id, run_id, task["attempt_id"])
        task["status"] = "completed"
        events.append("h3-readback")
        return {**task, "readback_path": str(path)}

    def cleanup(handle):
        assert handle == claim
        events.append("terminate")
        if fail.terminate:
            raise OSError("fake termination unavailable")
        return {"status": "terminated", "pod_id": handle["pod_id"]}
    runtime = AstridRuntimeTaskAdapter(client)
    runtime.execute_canonical_h3 = execute
    operations = ConcreteDeploymentOperations(
        runtime=runtime,
        claim=RunPodClaimHelperAdapter(python_executable=Path(__import__("sys").executable),
                                      helper_path=candidate.REPOSITORY_ROOT / "scripts/claim_runpod_5090_backup.py",
                                      existing_only=True),
        cleanup=SimpleNamespace(cleanup=cleanup), qualification=owner,
        settlement=SimpleNamespace(
            settle=lambda task_id, run_id: {"task_id": task_id, "run_id": run_id, "attempt_id": task["attempt_id"]},
            pullback=lambda _s, _p: {"decoded": False, "fake_transport": True}),
        activation_control=control)
    request = DeploymentRequest(task_id=task["id"], run_id=task["run_id"], operation_id="op-a",
                                receipt_path=tmp_path / "receipt.json", handle_path=boundary.kwargs["handle_path"],
                                output_path=tmp_path / "output.mkv")
    return SimpleNamespace(request=request, operations=operations, events=events,
                           controls=controls, activations=activations, fail=fail, task=task, claim=claim)


def test_noncanonical_activation_vibe_h3_readback_and_revocation(connected):
    result = run_existing_h3_task(connected.request, connected.operations)
    assert result["status"] == "completed"
    assert (result["task_id"], result["run_id"], result["attempt_id"]) == (
        "lane-a-task", "lane-a-run", "lane-a-attempt")
    activation = connected.activations[0]
    assert activation["authorized_child_lineage"]["parent_task_id"] == "lane-a-task"
    assert activation["authorized_child_lineage"]["parent_run_id"] == "lane-a-run"
    assert connected.controls[-1] == ("lane-a-task", {
        "action": "revoke", "activation_id": activation["activation_id"]})
    order = connected.events
    assert order.index("ack") < order.index("record-activation") < order.index("enable")
    assert order.index("enable") < order.index("retry") < order.index("fake-inference")
    assert order.index("h3-readback") < order.index("revoke") < order.index("terminate")


def test_candidate_host_constructs_and_discovers_h3_transform():
    host = GenericPackHost(pack_roots=[candidate.PRODUCT_SOURCE_ROOT / "astrid/packs"])
    host.discover()
    assert "h3_av.transform" in host.capabilities


def test_activation_failure_retains_owned_cleanup_pending(connected):
    connected.fail.ready = connected.fail.revoke = True
    with pytest.raises(OSError, match="readiness lost"):
        run_existing_h3_task(connected.request, connected.operations)
    receipt = json.loads(connected.request.receipt_path.read_text())
    assert receipt["phase"] == "cleanup_pending"
    assert receipt["activation_state"] == "unknown"
    assert receipt["pod_id"] == "pod-a"
    assert "terminate" not in connected.events
    assert "retry" not in connected.events


def test_lane_cleanup_resume_uses_durable_activation_without_resubmission(connected):
    connected.fail.terminate = True
    pending = run_existing_h3_task(connected.request, connected.operations)
    assert pending["status"] == "cleanup_pending"
    connected.fail.terminate = False
    # A restarted composition has no in-memory activation or task identity.
    connected.operations._activation_id = None
    connected.operations._task_identity = None
    connected.operations._claim_handle_digest = None
    result = resume_h3_settlement(connected.request, connected.operations)
    assert result["status"] == "completed"
    assert connected.events.count("fake-inference") == 1
    assert connected.controls[-1][1]["activation_id"] == pending["activation_id"]
    assert connected.controls[-1][0] == "lane-a-task"


@pytest.mark.parametrize("foreign", ["task", "run", "handle", "activation"])
def test_cross_lane_cleanup_rejected_before_revoke_or_provider(connected, foreign):
    connected.fail.terminate = True
    pending = run_existing_h3_task(connected.request, connected.operations)
    before = list(connected.events)
    kwargs = {"task_id": "lane-a-task", "run_id": "lane-a-run", "activation_id": pending["activation_id"]}
    handle = connected.claim
    if foreign in {"task", "run"}:
        kwargs[foreign + "_id"] = "lane-b-" + foreign
    elif foreign == "handle":
        handle = {**handle, "pod_id": "pod-b"}
    else:
        kwargs["activation_id"] = "lane-b-activation"
    with pytest.raises(DeploymentOperationError, match="foreign"):
        connected.operations.cleanup_owned_pod(handle, **kwargs)
    assert connected.events == before


@pytest.mark.parametrize("foreign", ["task", "run", "pod", "handle-bytes"])
def test_cross_lane_resume_rejected_without_external_calls(connected, foreign):
    connected.fail.terminate = True
    run_existing_h3_task(connected.request, connected.operations)
    before = list(connected.events)
    request = connected.request
    if foreign in {"task", "run"}:
        request = replace(request, **{foreign + "_id": "lane-b-" + foreign})
    else:
        claim = {**connected.claim, ("pod_id" if foreign == "pod" else "network_volume_id"): "foreign"}
        request.handle_path.write_text(json.dumps(claim))
    with pytest.raises(DeploymentOperationError, match="match"):
        resume_h3_settlement(request, connected.operations)
    assert connected.events == before
