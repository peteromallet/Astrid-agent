from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from astrid.core.execution.runpod_deployment import (
    AstridRuntimeTaskAdapter,
    AstridManagedOutputSettlementAdapter,
    CANONICAL_RUN_ID,
    CANONICAL_TASK_ID,
    ConcreteDeploymentOperations,
    QualifiedRunPodDeploymentOwner,
    DeploymentOperationError,
    DeploymentRequest,
    RunPodClaimHelperAdapter,
    RunPodLifecycleCleanupAdapter,
    run_existing_h3_task,
    resume_h3_settlement,
)


TASK = {
    "id": CANONICAL_TASK_ID,
    "run_id": CANONICAL_RUN_ID,
    "project_id": "project-1",
    "idempotency_key": "admission-1",
    "version": 7,
    "status": "failed",
    "capability_id": "h3_av.transform",
    "capability_digest": "sha256:" + "1" * 64,
    "input_object_ids": ["sha256:" + "2" * 64, "sha256:" + "3" * 64],
    "execution_request": {
        "target": {"kind": "runpod", "pod_id": "km0stbzgmi3kdm"},
        "inputs": [
            {"name": "source_a", "object_id": "sha256:" + "2" * 64},
            {"name": "source_b", "object_id": "sha256:" + "3" * 64},
        ],
    },
    "spec": {
        "schema_version": "1",
        "capability_digest": "sha256:" + "1" * 64,
        "input_object_ids": ["sha256:" + "2" * 64, "sha256:" + "3" * 64],
        "spec": {"capability": "h3_av.transform"},
    },
    "target": {"kind": "runpod", "pod_id": "km0stbzgmi3kdm"},
    "execution_binding": {
        "original_target": {"kind": "runpod", "pod_id": "km0stbzgmi3kdm"},
        "effective_target": {"kind": "runpod", "pod_id": "km0stbzgmi3kdm"},
        "resolved_target": {"kind": "runpod", "pod_id": "km0stbzgmi3kdm"},
        "placement_version": 0,
    },
}


class FakeOperations:
    def __init__(
        self,
        *,
        fail_at: str | None = None,
        fail_settlement_once: bool = False,
        fail_pullback_once: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.fail_at = fail_at
        self.fail_settlement_once = fail_settlement_once
        self.fail_pullback_once = fail_pullback_once
        self.task = dict(TASK)
        self.settlement_attempts = 0
        self.pullback_attempts = 0

    def get_task(self, task_id: str) -> Mapping[str, Any]:
        assert task_id == CANONICAL_TASK_ID
        self.calls.append("get_task")
        return self.task

    def assert_claim_eligible(self, task: Mapping[str, Any]) -> None:
        assert task["id"] == CANONICAL_TASK_ID
        self.calls.append("claim_eligible")

    def claim_pod(self, handle_path: Path) -> Mapping[str, Any]:
        assert handle_path.is_absolute()
        self.calls.append("claim")
        handle = {
            "schema_version": "astrid.runpod.claim.v1",
            "pod_id": "pod-owned-by-this-operation",
            "network_volume_id": "sfak8553dy",
        }
        handle_path.write_text(json.dumps(handle), encoding="utf-8")
        return handle

    def prepare_and_qualify(self, task: Mapping[str, Any], claim_handle: Mapping[str, Any]) -> Mapping[str, Any]:
        assert claim_handle["pod_id"] == "pod-owned-by-this-operation"
        self.calls.append("prepare")
        if self.fail_at == "prepare":
            raise DeploymentOperationError("remote preparation failed before activation")
        return {
            "task_id": task["id"],
            "run_id": task["run_id"],
            "target": task["target"],
            "deployment_digest": "sha256:deployment",
            "proof_digest": "sha256:qualification",
        }

    def assert_fresh(self, task, claim_handle, qualification, *, phase: str) -> None:
        assert task["id"] == qualification["task_id"]
        assert claim_handle["pod_id"] == "pod-owned-by-this-operation"
        assert qualification["deployment_digest"] == "sha256:deployment"
        self.calls.append(phase)
        if self.fail_at == phase:
            raise DeploymentOperationError(f"stale qualification at {phase}")

    def retry_existing_task(self, task_id: str, *, expected_version: int, idempotency_key: str, before_queue):
        assert task_id == CANONICAL_TASK_ID
        assert expected_version == 7
        assert idempotency_key == "operation-1"
        before_queue()
        self.calls.append("retry")
        return {"task_id": task_id, "version": 8}

    def execute_canonical_h3(self, task_id: str, run_id: str):
        assert (task_id, run_id) == (CANONICAL_TASK_ID, CANONICAL_RUN_ID)
        self.calls.append("canonical_h3_handoff")
        self.calls.append("queue")
        return {"task_id": task_id, "run_id": run_id, "status": "settled"}

    def cleanup_owned_pod(self, claim_handle: Mapping[str, Any], *, task_id: str, run_id: str):
        assert (task_id, run_id) == (self.task["id"], self.task["run_id"])
        self.calls.append("cleanup")
        if self.fail_at == "cleanup":
            raise RuntimeError("provider termination unavailable")
        return {"status": "terminated", "pod_id": claim_handle["pod_id"]}

    def settle_managed_result(self, task_id: str, run_id: str):
        self.calls.append("settle")
        self.settlement_attempts += 1
        if self.fail_settlement_once and self.settlement_attempts == 1:
            raise OSError("simulated interrupted settlement read")
        return {
            "task_id": task_id,
            "run_id": run_id,
            "object_id": "sha256:" + hashlib.sha256(b"verified-video").hexdigest(),
            "size": len(b"verified-video"),
        }

    def pullback_managed_result(self, settlement: Mapping[str, Any], output_path: Path):
        self.calls.append("pullback")
        self.pullback_attempts += 1
        if self.fail_pullback_once and self.pullback_attempts == 1:
            raise OSError("simulated interrupted Runtime read")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"verified-video")
        return {"path": str(output_path), "sha256": settlement["object_id"], "decoded": True}


def _request(tmp_path: Path) -> DeploymentRequest:
    return DeploymentRequest(
        task_id=CANONICAL_TASK_ID,
        run_id=CANONICAL_RUN_ID,
        operation_id="operation-1",
        receipt_path=(tmp_path / "receipt.json").resolve(),
        handle_path=(tmp_path / "claim-handle.json").resolve(),
        output_path=(tmp_path / "pullback" / "verified-candidate.mkv").resolve(),
    )


def test_receipt_composes_prepare_retry_freshness_and_canonical_queue(tmp_path: Path) -> None:
    operations = FakeOperations()
    receipt = run_existing_h3_task(_request(tmp_path), operations)

    assert receipt["status"] == "completed"
    assert receipt["helper_task_admission"] is False
    assert receipt["pod_id"] == "pod-owned-by-this-operation"
    assert operations.calls.index("prepare") < operations.calls.index("before_retry")
    assert operations.calls.index("before_retry") < operations.calls.index("retry")
    assert operations.calls.index("before_queue") < operations.calls.index("retry")
    persisted = json.loads((tmp_path / "receipt.json").read_text())
    assert persisted == receipt
    assert persisted["phase"] == "complete"
    assert persisted["pullback"]["decoded"] is True
    assert persisted["cleanup_status"] == "complete"
    assert persisted["effective_target"] == TASK["execution_binding"]["effective_target"]
    assert "attempt_id" not in persisted


@pytest.mark.parametrize("attempt_source", ["task", "retry", "execution", "settlement"])
def test_receipt_preserves_optional_runtime_lineage(tmp_path: Path, monkeypatch, attempt_source: str) -> None:
    operations = FakeOperations()
    operations.task = copy.deepcopy(TASK)
    target = {
        "kind": "runpod", "pod_id": "pod-owned-by-this-operation",
        "provider_account_ref": "runpod",
        "storage": {"network_volume_id": "sfak8553dy"},
    }
    operations.task["execution_binding"].update({
        "original_target": target,
        "effective_target": target,
        "resolved_target": target,
        "placement_version": 0,
    })
    operations.task["execution_request"]["target"] = target
    operations.task["target"] = target
    if attempt_source == "task":
        operations.task["attempt_id"] = "attempt-1"
    else:
        method = {
            "retry": "retry_existing_task", "execution": "execute_canonical_h3",
            "settlement": "settle_managed_result",
        }[attempt_source]
        original = getattr(operations, method)
        monkeypatch.setattr(operations, method, lambda *args, **kwargs: {
            **original(*args, **kwargs), "attempt_id": "attempt-1",
        })

    request = _request(tmp_path)
    receipt = run_existing_h3_task(request, operations)

    normalized_json = json.dumps(
        operations.task["execution_request"], sort_keys=True, separators=(",", ":")
    )
    assert receipt["execution_request_digest"] == "sha256:" + hashlib.sha256(normalized_json.encode()).hexdigest()
    assert receipt["effective_target"] == target
    assert receipt["effective_target"] == target
    assert receipt["attempt_id"] == "attempt-1"
    assert json.loads(request.receipt_path.read_text()) == receipt


@pytest.mark.parametrize("phase", ["settlement", "pullback"])
def test_resume_preserves_settlement_lineage_without_regeneration(tmp_path: Path, monkeypatch, phase: str) -> None:
    operations = FakeOperations(
        fail_settlement_once=phase == "settlement", fail_pullback_once=phase == "pullback",
    )
    original = operations.settle_managed_result
    lineage = {
        "execution_request_digest": "sha256:" + "4" * 64,
        "effective_target": TASK["execution_binding"]["effective_target"],
        "attempt_id": "attempt-settled",
    }
    monkeypatch.setattr(operations, "settle_managed_result", lambda *args: {
        **original(*args), **lineage,
    })
    request = _request(tmp_path)
    with pytest.raises(OSError, match="simulated interrupted"):
        run_existing_h3_task(request, operations)
    if phase == "pullback":
        # An older receipt may have lineage only inside its saved settlement.
        pending = json.loads(request.receipt_path.read_text())
        for key in lineage:
            pending.pop(key, None)
        request.receipt_path.write_text(json.dumps(pending), encoding="utf-8")

    receipt = resume_h3_settlement(request, operations)

    assert receipt["status"] == "completed"
    assert all(receipt[key] == value for key, value in lineage.items())
    assert receipt["settlement"]["attempt_id"] == "attempt-settled"
    assert json.loads(request.receipt_path.read_text()) == receipt
    assert operations.calls.count("retry") == 1
    assert operations.calls.count("canonical_h3_handoff") == 1
    assert operations.settlement_attempts == (2 if phase == "settlement" else 1)


def test_allocated_failure_stops_and_cleans_exact_pod(tmp_path: Path) -> None:
    operations = FakeOperations(fail_at="before_queue")

    with pytest.raises(DeploymentOperationError, match="stale qualification"):
        run_existing_h3_task(_request(tmp_path), operations)

    assert operations.calls[-1] == "cleanup"
    assert "queue" not in operations.calls
    persisted = json.loads((tmp_path / "receipt.json").read_text())
    assert persisted["phase"] == "failed_cleaned"
    assert persisted["cleanup"]["pod_id"] == "pod-owned-by-this-operation"


def test_interrupted_pullback_resumes_without_reexecuting_h3(tmp_path: Path) -> None:
    request = _request(tmp_path)
    operations = FakeOperations(fail_pullback_once=True)
    with pytest.raises(OSError, match="interrupted Runtime read"):
        run_existing_h3_task(request, operations)

    pending = json.loads(request.receipt_path.read_text())
    assert pending["status"] == "pullback_pending"
    assert pending["settlement"]["object_id"].startswith("sha256:")
    assert operations.calls.count("canonical_h3_handoff") == 1

    resumed = resume_h3_settlement(request, operations)
    assert resumed["status"] == "completed"
    assert operations.calls.count("canonical_h3_handoff") == 1
    assert operations.calls.count("retry") == 1
    assert resumed["pullback"]["decoded"] is True


def test_interrupted_settlement_resumes_from_existing_task_result(tmp_path: Path) -> None:
    request = _request(tmp_path)
    operations = FakeOperations(fail_settlement_once=True)
    with pytest.raises(OSError, match="interrupted settlement"):
        run_existing_h3_task(request, operations)

    assert json.loads(request.receipt_path.read_text())["status"] == "settlement_pending"
    resumed = resume_h3_settlement(request, operations)
    assert resumed["status"] == "completed"
    assert operations.calls.count("canonical_h3_handoff") == 1
    assert operations.settlement_attempts == 2


def test_termination_failure_is_cleanup_pending_with_backup_volume_retained(tmp_path: Path) -> None:
    operations = FakeOperations(fail_at="cleanup")
    receipt = run_existing_h3_task(_request(tmp_path), operations)

    assert receipt["status"] == "cleanup_pending"
    assert receipt["cleanup_status"] == "pending"
    assert receipt["cleanup"]["backup_volume_id"] == "sfak8553dy"
    assert receipt["cleanup"]["backup_volume_preserved"] is True
    assert receipt["pullback"]["decoded"] is True


def test_concrete_cleanup_revokes_runtime_activation_before_exact_pod_termination() -> None:
    calls: list[Any] = []

    class Cleanup:
        def cleanup(self, handle):
            calls.append(("terminate", handle["pod_id"]))
            return {"status": "terminated", "pod_id": handle["pod_id"]}

    def control(task_id, body):
        calls.append(("revoke", task_id, dict(body)))
        return {"revoked": True}

    operations = ConcreteDeploymentOperations(
        runtime=object(), claim=object(), cleanup=Cleanup(), qualification=None,
        settlement=object(), activation_control=control,
    )
    result = operations.cleanup_owned_pod(
        {"pod_id": "exact-owned-pod"}, task_id=CANONICAL_TASK_ID,
        run_id=CANONICAL_RUN_ID, activation_id="activation-1"
    )
    assert result["pod_id"] == "exact-owned-pod"
    assert calls == [
        ("revoke", CANONICAL_TASK_ID, {"action": "revoke", "activation_id": "activation-1"}),
        ("terminate", "exact-owned-pod"),
    ]


def test_concrete_cleanup_keeps_pod_when_revocation_is_unproven() -> None:
    calls: list[str] = []

    class Cleanup:
        def cleanup(self, handle):
            calls.append("terminate")
            return {"status": "terminated"}

    operations = ConcreteDeploymentOperations(
        runtime=object(), claim=object(), cleanup=Cleanup(), qualification=None,
        settlement=object(), activation_control=lambda *_args: {"revoked": False},
    )
    with pytest.raises(DeploymentOperationError, match="did not revoke"):
        operations.cleanup_owned_pod({"pod_id": "exact-owned-pod"}, task_id=CANONICAL_TASK_ID,
                                     run_id=CANONICAL_RUN_ID, activation_id="activation-1")
    assert calls == []


def test_real_owner_without_durable_activation_id_cannot_terminate_pod() -> None:
    calls: list[str] = []
    owner = QualifiedRunPodDeploymentOwner(
        runtime=object(), launcher=object(), task_reader=lambda _task_id: {},
        launch_factory=lambda _task, _handle: object(),
        reference_factory=lambda _task, _handle: object(),
        loss_observer=lambda _target: {},
        owner_identity={"actor": "owner", "scopes": ["admin"]}, operation_id="operation-1",
    )
    owner.activation_state = "unknown"
    operations = ConcreteDeploymentOperations(
        runtime=object(), claim=object(),
        cleanup=SimpleNamespace(cleanup=lambda _handle: calls.append("terminate")),
        qualification=owner, settlement=object(), activation_control=lambda *_args: {"revoked": True},
    )
    with pytest.raises(DeploymentOperationError, match="activation state is unknown"):
        operations.cleanup_owned_pod({"pod_id": "exact-owned-pod"}, task_id=CANONICAL_TASK_ID,
                                     run_id=CANONICAL_RUN_ID)
    assert calls == []


def test_confirmed_pre_activation_failure_cleans_exact_pod() -> None:
    calls: list[str] = []
    owner = QualifiedRunPodDeploymentOwner(
        runtime=object(), launcher=object(), task_reader=lambda _task_id: {},
        launch_factory=lambda _task, _handle: object(),
        reference_factory=lambda _task, _handle: object(),
        loss_observer=lambda _target: {},
        owner_identity={"actor": "owner", "scopes": ["admin"]}, operation_id="operation-1",
    )
    operations = ConcreteDeploymentOperations(
        runtime=object(), claim=object(),
        cleanup=SimpleNamespace(cleanup=lambda handle: calls.append(handle["pod_id"]) or {"status": "terminated"}),
        qualification=owner, settlement=object(), activation_control=None,
    )
    assert operations.cleanup_owned_pod({"pod_id": "exact-owned-pod"}, task_id=CANONICAL_TASK_ID,
                                       run_id=CANONICAL_RUN_ID) == {"status": "terminated"}
    assert calls == ["exact-owned-pod"]


def test_failed_preparation_cleanup_resume_skips_settlement(tmp_path: Path) -> None:
    request = _request(tmp_path)

    class InactiveOperations(FakeOperations):
        activation_state = "inactive"

    operations = InactiveOperations(fail_at="prepare")
    operations.fail_at = "prepare"
    original_cleanup = operations.cleanup_owned_pod
    cleanup_calls = 0

    def cleanup_once(handle, **identity):
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 1:
            raise DeploymentOperationError("temporary provider failure")
        return original_cleanup(handle, **identity)

    operations.cleanup_owned_pod = cleanup_once
    with pytest.raises(DeploymentOperationError, match="remote preparation failed"):
        run_existing_h3_task(request, operations)
    pending = json.loads(request.receipt_path.read_text())
    assert pending["status"] == "failed"
    assert pending["phase"] == "cleanup_pending"
    assert pending["activation_state"] == "inactive"
    completed = resume_h3_settlement(request, operations)
    assert completed["phase"] == "failed_cleaned"
    assert completed["status"] == "failed"
    assert "settle" not in operations.calls
    assert "pullback" not in operations.calls
    assert cleanup_calls == 2


def test_qualification_stages_manifest_under_launch_support_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import h3_runpod_qualification as qualification

    claim = {
        "schema_version": "astrid.runpod.claim.v1", "pod_id": "pod-1",
        "network_volume_id": "volume-1", "ssh": "root@127.0.0.1 -p 2222",
        "volume_mount_path": "/workspace",
    }
    handle = tmp_path / "claim.json"
    handle.write_text(json.dumps(claim), encoding="utf-8")
    release = "/workspace/h3-lanes/lane-a/releases/candidate"
    source_manifest = release + "/staging/boot-manifest.json"
    profile = {
        "readiness_profile_path": release + "/staging/readiness.json",
        "boot_manifest_path": source_manifest,
        "boot_manifest_hash": "sha256:" + "b" * 64,
        "session_config_path": release + "/session/config.json",
        "session_ref": "session-1", "model_root": release + "/models",
        "release_manifest_path": release + "/receipts/release-manifest.json",
        "lane": "lane-a", "lane_root": "/tmp/astrid-h3-lanes/lane-a",
        "release_root": release, "executor_id": "executor-a",
    }
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setenv("ASTRID_H3_REMOTE_PROFILE", str(profile_path))
    monkeypatch.setattr(qualification, "_provider", lambda _claim: {})
    monkeypatch.setattr(qualification, "_remote_file_hashes",
                        lambda _claim, paths: {path: "sha256:" + "a" * 64 for path in paths})
    monkeypatch.setattr(qualification, "_remote_boot_manifest_hash",
                        lambda _claim, _path: "sha256:" + "b" * 64)
    monkeypatch.setattr(qualification, "source_checkout_digest", lambda _path: "c" * 64)
    health = {"status": "ok", "runtime_instance_id": "runtime-1", "runtime_epoch": 1,
              "runtime_session_id": "session-1", "schema_digest": "sha256:" + "d" * 64}
    owner_credential = tmp_path / "owner.token"
    owner_credential.write_text("owner-token", encoding="utf-8")

    class OwnerTransport:
        endpoint = "http://127.0.0.1:6543"

        def health(self):
            return health

        def handshake(self, *_args):
            return {"actor_id": "owner", "realm_id": "realm-1"}

        def doctor(self):
            return {"status": "ok"}

    monkeypatch.setattr(qualification, "WorkspaceClient", lambda *_args: OwnerTransport())
    client = SimpleNamespace(
        health=lambda: health,
        tasks=SimpleNamespace(show=lambda _task_id: task),
        _remote=SimpleNamespace(_transport=SimpleNamespace(
            endpoint="http://127.0.0.1:6543",
            _last_handshake={"realm_id": "realm-1"},
        )),
    )
    task = copy.deepcopy(TASK)
    target = {"kind": "runpod", "pod_id": "pod-1", "provider_account_ref": "account-1"}
    task["execution_binding"]["effective_target"] = target
    task["execution_binding"]["original_target"] = target
    task["execution_binding"]["resolved_target"] = target
    task["execution_request"]["target"] = target
    task["target"] = target
    from scripts import run_h3_canonical_on_runpod as canonical
    identity = {"source_root": str(Path(qualification.__file__).resolve().parents[1]),
                "branch": "candidate", "head": "a" * 40, "content_digest": "sha256:" + "c" * 64,
                "scoped_source_digest": "c" * 64}
    monkeypatch.setattr(canonical, "assert_product_source_identity", lambda **_kw: identity)
    owner = qualification.default_qualification_factory(
        client=client, handle_path=handle, operation_id="operation-1",
        owner_credential=owner_credential, source_identity=identity,
        task_id=CANONICAL_TASK_ID, run_id=CANONICAL_RUN_ID,
        lane_config=qualification.H3LaneConfig(
            lane="lane-a", lane_root=profile["lane_root"], release_root=release,
            executor_id="executor-a", session_ref="session-1", local_data_root=tmp_path,
            expected_pod="pod-1", runtime_instance_id="runtime-1", runtime_session_id="session-1",
            runtime_epoch=1,
        ),
    )
    launch = owner.launch_factory(task, claim)
    staged = launch["support_root"] + "/boot-manifest.json"
    assert launch["manifest_source"] == source_manifest
    assert launch["manifest_target"] == staged
    assert launch["file_hashes"][staged] == launch["file_hashes"][source_manifest]
    assert launch["argv"][launch["argv"].index("--boot-manifest-path") + 1] == staged
    assert launch["argv"][launch["argv"].index("--boot-manifest-hash") + 1] == profile["boot_manifest_hash"]
    reference = owner.reference_factory(task, claim)
    assert str(reference.boot_manifest_path) == staged
    assert reference.boot_manifest_hash == profile["boot_manifest_hash"]


def test_cleanup_resume_reuses_durable_activation_id_without_retry(tmp_path: Path) -> None:
    request = _request(tmp_path)

    class ActivatedOperations(FakeOperations):
        def __init__(self):
            super().__init__()
            self.cleanup_attempts: list[str | None] = []

        def prepare_and_qualify(self, task, claim_handle):
            return {**super().prepare_and_qualify(task, claim_handle), "activation_id": "activation-1"}

        def cleanup_owned_pod(self, claim_handle, *, task_id, run_id, activation_id=None):
            self.cleanup_attempts.append(activation_id)
            if len(self.cleanup_attempts) == 1:
                raise DeploymentOperationError("resident revocation unavailable")
            return super().cleanup_owned_pod(claim_handle, task_id=task_id, run_id=run_id)

    operations = ActivatedOperations()
    pending = run_existing_h3_task(request, operations)
    assert pending["status"] == "cleanup_pending"
    assert pending["activation_id"] == "activation-1"
    assert operations.cleanup_attempts == ["activation-1"]

    completed = resume_h3_settlement(request, operations)
    assert completed["status"] == "completed"
    assert operations.cleanup_attempts == ["activation-1", "activation-1"]
    assert operations.calls.count("retry") == 1


def test_managed_settlement_and_pullback_use_runtime_reads_and_verify_bytes(tmp_path: Path) -> None:
    payload = b"runtime-owned-h3-result"
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    calls: list[Any] = []

    parent_attempt_id = "attempt-parent"
    verify_task_id = "verify-task"
    verify_attempt_id = "attempt-verify"
    finalizer_task_id = "finalizer-task"
    finalizer_attempt_id = "attempt-finalizer"
    generation_id = "generation-1"
    verify_association_id = "verify-association-1"
    finalizer_association_id = "finalizer-association-1"
    delegated_parent = {
        "parent_task_id": CANONICAL_TASK_ID,
        "parent_attempt_id": parent_attempt_id,
        "project_id": "project-1",
    }
    final_publication = {
        "stage": "finalize",
        "verify_stage": "verify",
        "verify_output_port": "verified_candidate",
        "effect": {
            "effect_type": "generation.publish_v1",
            "target_id": "project-1",
            "payload": {
                "version": 1,
                "modality": "video",
                "generation_type": "h3_av.publication_finalizer",
                "metadata": {"source_capability": "h3_av.transform"},
                "partial_success_policy": "reject",
                "groups": [{"group_key": "main", "selectors": [{
                    "selector": "main-0",
                    "ordinal": 0,
                    "variant_key": "original",
                    "output_port": "verified_candidate",
                    "required": True,
                }]}],
            },
        },
    }
    parent = {
        **copy.deepcopy(TASK),
        "attempt_id": parent_attempt_id,
        "state": "completed",
        "spec": {
            **copy.deepcopy(TASK["spec"]),
            "child_delegation": {"final_publication": final_publication},
        },
    }
    verify_task = {
        "id": verify_task_id,
        "task_id": verify_task_id,
        "run_id": "run-verify",
        "project_id": "project-1",
        "attempt_id": verify_attempt_id,
        "state": "completed",
        "capability_id": "h3_av.verify",
        "spec": {"delegated_stage": "verify", "delegated_parent": delegated_parent},
    }
    finalizer_task = {
        "id": finalizer_task_id,
        "task_id": finalizer_task_id,
        "run_id": "run-finalizer",
        "project_id": "project-1",
        "attempt_id": finalizer_attempt_id,
        "state": "completed",
        "capability_id": "h3_av.publication_finalizer",
        "spec": {
            "delegated_stage": "finalize",
            "delegated_parent": delegated_parent,
            "verified_publication_source": {
                "producer_task_id": verify_task_id,
                "producer_attempt_id": verify_attempt_id,
                "association_id": verify_association_id,
                "object_id": digest,
                "producer_stage": "verify",
                "output_port": "verified_candidate",
            },
        },
        "result": {"outputs": [{
            "digest": digest,
            "size": len(payload),
            "output_port": "verified_candidate",
        }]},
    }
    verify_row = {
        "task_id": verify_task_id,
        "attempt_id": verify_attempt_id,
        "project_id": "project-1",
        "association_id": verify_association_id,
        "object_id": digest,
        "output_port": "verified_candidate",
        "durability": "durable",
        "lifecycle": {"state": "available", "version": 1},
        "producer": {"capability_id": "h3_av.verify"},
        "provenance": {
            "task_id": verify_task_id,
            "attempt_id": verify_attempt_id,
            "capability_id": "h3_av.verify",
        },
    }
    finalizer_row = {
        "task_id": finalizer_task_id,
        "attempt_id": finalizer_attempt_id,
        "project_id": "project-1",
        "run_id": "run-finalizer",
        "association_id": finalizer_association_id,
        "generation_id": generation_id,
        "object_id": digest,
        "size": len(payload),
        "media_type": "video/x-matroska",
        "filename": "verified-candidate.mkv",
        "output_port": "verified_candidate",
        "group_key": "main",
        "variant_key": "original",
        "ordinal": 0,
        "role": "result",
        "selector": {"group_key": "main", "variant_key": "original"},
        "durability": "durable",
        "lifecycle": {"state": "available", "version": 1},
        "producer": {"capability_id": "h3_av.publication_finalizer"},
        "provenance": {
            "task_id": finalizer_task_id,
            "attempt_id": finalizer_attempt_id,
            "capability_id": "h3_av.publication_finalizer",
        },
    }

    class Tasks:
        def show(self, task_id):
            calls.append(("show", task_id))
            return {
                CANONICAL_TASK_ID: parent,
                verify_task_id: verify_task,
                finalizer_task_id: finalizer_task,
            }[task_id]

        def list(self, project_id, *, cursor=None, limit=200):
            assert project_id == "project-1"
            assert cursor is None and limit == 200
            calls.append(("list", project_id))
            return [verify_task, finalizer_task]

        def list_managed_outputs(self, task_id):
            calls.append(("managed_outputs", task_id))
            assert task_id == finalizer_task_id
            return [finalizer_row]

        def get_managed_output(self, association_id):
            calls.append(("managed_output", association_id))
            return {verify_association_id: verify_row, finalizer_association_id: finalizer_row}[association_id]

    class Generations:
        def show(self, project_id, requested_generation_id):
            calls.append(("generation", project_id, requested_generation_id))
            assert project_id == "project-1" and requested_generation_id == generation_id
            return {
                "generation_id": generation_id,
                "project_id": "project-1",
                "source_task_id": finalizer_task_id,
                "type": "h3_av.publication_finalizer",
                "status": "completed",
            }

        def variants(self, project_id, requested_generation_id, *, limit=200):
            calls.append(("variants", project_id, requested_generation_id))
            assert project_id == "project-1" and requested_generation_id == generation_id
            assert limit == 200
            return [{
                "variant_id": "variant-1",
                "generation_id": generation_id,
                "object_id": digest,
                "variant_type": "original",
                "metadata": {
                    "output_port": "verified_candidate",
                    "group_key": "main",
                    "variant_key": "original",
                    "ordinal": 0,
                    "size": len(payload),
                },
            }]

    class Media:
        def read_bytes(self, object_id):
            calls.append(("read_bytes", object_id))
            return payload

    def decode(path):
        assert path.read_bytes() == payload
        calls.append(("decode", path.name))
        return {"video": True, "audio": True}

    adapter = AstridManagedOutputSettlementAdapter(
        SimpleNamespace(tasks=Tasks(), generations=Generations(), media=Media()), decode=decode
    )
    settlement = adapter.settle(CANONICAL_TASK_ID, CANONICAL_RUN_ID)
    destination = (tmp_path / "local" / "verified-candidate.mkv").resolve()
    pulled = adapter.pullback(settlement, destination)
    repeated = adapter.pullback(settlement, destination)

    assert settlement["association_id"] == finalizer_association_id
    assert settlement["attempt_id"] == parent_attempt_id
    assert settlement["effective_target"] == TASK["execution_binding"]["effective_target"]
    assert settlement["execution_request_digest"].startswith("sha256:")
    assert pulled["sha256"] == digest.removeprefix("sha256:")
    assert pulled["size"] == len(payload) and pulled["decoded"] is True
    assert repeated["reused"] is True
    assert sum(call[0] == "read_bytes" for call in calls) == 1


def test_wrong_task_or_run_is_rejected_before_any_owner_call(tmp_path: Path) -> None:
    operations = FakeOperations()
    request = DeploymentRequest(
            task_id=CANONICAL_TASK_ID,
            run_id="different-run",
            operation_id="operation-1",
            receipt_path=(tmp_path / "receipt.json").resolve(),
            handle_path=(tmp_path / "handle.json").resolve(),
            output_path=(tmp_path / "output.mkv").resolve(),
    )
    with pytest.raises(DeploymentOperationError, match="different requested task or run"):
        run_existing_h3_task(request, operations)
    assert operations.calls == ["get_task"]


def test_canonical_front_end_live_gate_retains_existing_claim_helper(tmp_path: Path) -> None:
    from scripts.run_h3_canonical_on_runpod import claim_helper_argv, run

    operations = FakeOperations()
    with pytest.raises(DeploymentOperationError, match="task/run-scoped"):
        run(
            operation_id="operation-1",
            receipt_path=(tmp_path / "receipt.json").resolve(),
            handle_path=(tmp_path / "handle.json").resolve(),
            output_path=(tmp_path / "output.mkv").resolve(),
            operations=operations, task_id=CANONICAL_TASK_ID, run_id=CANONICAL_RUN_ID,
        )
    argv = claim_helper_argv((tmp_path / "handle.json").resolve())

    assert operations.calls == []
    assert "scripts/claim_runpod_5090_backup.py" in argv[1]
    assert "--stop-after-allocated-failure" in argv
    assert "--handle-path" in argv
    assert not any("tasks create" in part or "admit_task" in part for part in argv)


def _fake_claim_module(monkeypatch, tmp_path: Path, *, pod: Any, launches: list[Any]):
    from scripts import claim_runpod_5090_backup as claim

    config = SimpleNamespace(
        api_key="test-only",
        gpu_type="NVIDIA GeForce RTX 5090",
        storage_name="backup",
        container_disk_gb=200,
        volume_mount_path="/workspace",
        allowed_cuda_versions=("13.0",),
        worker_image="image",
        template_id=None,
        name_prefix="astrid-claim",
        merge=lambda **_: config,
    )
    monkeypatch.setattr(claim.RunPodConfig, "from_env", lambda **_: config)

    async def volume(*_args, **_kwargs):
        return {"id": "sfak8553dy", "name": "backup", "size": 400}

    monkeypatch.setattr(claim, "_require_existing_volume", volume)

    async def launch(*_args, **_kwargs):
        item = launches.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(claim, "launch_when_available", launch)
    monkeypatch.setattr(claim, "_preflight_release", lambda *_args: asyncio.sleep(0, result={"probe": "ok"}))
    return claim


def test_capacity_only_window_still_waits_until_claim(monkeypatch, tmp_path: Path) -> None:
    from scripts import claim_runpod_5090_backup as claim

    class Pod:
        id = "pod-1"
        name = "claim-pod"
        _gpu_type = "NVIDIA GeForce RTX 5090"
        _storage_name = "backup"
        _storage_volume = "sfak8553dy"

        async def wait_ready(self, timeout):
            return None

        async def _ensure_ssh_details(self):
            return {"ip": "127.0.0.1", "port": 22}

    pod = Pod()
    launches = [claim.LaunchFailure("capacity full"), pod]
    _fake_claim_module(monkeypatch, tmp_path, pod=pod, launches=launches)
    monkeypatch.setattr(claim, "_require_existing_volume", lambda *_a, **_k: asyncio.sleep(0, result={"id": "sfak8553dy", "name": "backup", "size": 400}))
    monkeypatch.setattr(claim, "asyncio", SimpleNamespace(to_thread=asyncio.to_thread, sleep=lambda *_: asyncio.sleep(0)))
    args = claim._parser().parse_args([
        "--stop-after-allocated-failure",
        "--handle-path", str((tmp_path / "claim.json").resolve()),
        "--capacity-window-seconds", "1",
    ])

    result = asyncio.run(claim._claim(args))

    assert result["pod_id"] == "pod-1"
    assert not launches
    assert pod.id == result["pod_id"]




def test_allocated_readiness_failure_cleans_then_stops(monkeypatch, tmp_path: Path) -> None:
    from scripts import claim_runpod_5090_backup as claim

    class Pod:
        id = "pod-failed"
        name = "claim-pod"
        _gpu_type = "NVIDIA GeForce RTX 5090"
        _storage_name = "backup"
        _storage_volume = "sfak8553dy"
        terminated = False

        async def wait_ready(self, timeout):
            raise TimeoutError("readiness deadline")

        async def terminate(self):
            self.terminated = True

    pod = Pod()
    _fake_claim_module(monkeypatch, tmp_path, pod=pod, launches=[pod])
    handle_path = (tmp_path / "claim.json").resolve()
    args = claim._parser().parse_args([
        "--stop-after-allocated-failure",
        "--handle-path", str(handle_path),
        "--capacity-window-seconds", "1",
    ])

    with pytest.raises(RuntimeError, match="exact pod cleanup completed"):
        asyncio.run(claim._claim(args))

    assert pod.terminated is True
    recorded = json.loads(handle_path.read_text())
    assert recorded["operation_status"] == "allocated_failure_cleaned"
    assert recorded["pod_id"] == "pod-failed"


class _Result:
    def __init__(self, data: Mapping[str, Any], *, ok: bool = True) -> None:
        self.data = dict(data)
        self.ok = ok
        self.error = None


def test_runtime_adapter_delegates_retry_and_preserves_run_identity() -> None:
    class Tasks:
        def __init__(self) -> None:
            self.shown = [dict(TASK), {**TASK, "state": "succeeded", "status": "succeeded"}]
            self.calls: list[tuple[str, Any]] = []

        def show(self, task_id: str) -> _Result:
            self.calls.append(("show", task_id))
            return _Result(self.shown.pop(0))

        def retry(self, task_id: str, **kwargs: Any) -> _Result:
            self.calls.append(("retry", task_id, kwargs))
            return _Result({"task_id": task_id, "run_id": CANONICAL_RUN_ID, "version": 8})

    tasks = Tasks()
    runtime = AstridRuntimeTaskAdapter(
        SimpleNamespace(tasks=tasks), wait_timeout_seconds=1, poll_seconds=0.01
    )
    task = runtime.get_task(CANONICAL_TASK_ID)
    runtime.assert_claim_eligible(task)
    freshness: list[str] = []
    retry = runtime.retry_existing_task(
        CANONICAL_TASK_ID,
        expected_version=7,
        idempotency_key="operation-1",
        before_queue=lambda: freshness.append("before_queue"),
    )
    completed = runtime.execute_canonical_h3(CANONICAL_TASK_ID, CANONICAL_RUN_ID)

    assert retry["task_id"] == CANONICAL_TASK_ID
    assert completed["run_id"] == CANONICAL_RUN_ID
    assert freshness == ["before_queue"]
    assert tasks.calls[1][0] == "retry"
    assert tasks.calls[1][2] == {
        "idempotency_key": "operation-1",
        "expected_version": 7,
    }


def test_runtime_task_family_forwards_expected_version() -> None:
    from astrid.sdk.remote import RemoteTasks

    class Workspace:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def retry_task(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {"data": {"task_id": CANONICAL_TASK_ID, "run_id": CANONICAL_RUN_ID}, "receipt": None}

    workspace = Workspace()
    result = RemoteTasks(workspace).retry(
        CANONICAL_TASK_ID,
        idempotency_key="operation-1",
        expected_version=7,
    )

    assert result.ok is True
    assert workspace.calls == [(
        (CANONICAL_TASK_ID,),
        {"idempotency_key": "operation-1", "expected_version": 7},
    )]


def test_claim_adapter_adopts_existing_claim_handle_without_helper(monkeypatch, tmp_path: Path) -> None:
    helper = tmp_path / "claim_runpod_5090_backup.py"
    python = tmp_path / "python"
    handle = tmp_path / "claim.json"
    helper.write_text("# existing helper", encoding="utf-8")
    python.write_text("", encoding="utf-8")
    handle.write_text(
        json.dumps({
            "schema_version": "astrid.runpod.claim.v1",
            "pod_id": "pod-1",
            "storage_name": "backup",
        }),
        encoding="utf-8",
    )
    seen: list[tuple[str, ...]] = []

    def unexpected_run(*args, **kwargs):
        raise AssertionError("existing claim handle must not invoke the helper")

    monkeypatch.setattr("astrid.core.execution.runpod_deployment.subprocess.run", unexpected_run)
    result = RunPodClaimHelperAdapter(
        python_executable=python,
        helper_path=helper,
    ).claim(handle)

    assert result["pod_id"] == "pod-1"
    assert seen == []


@pytest.mark.parametrize("payload, message", [
    ("{", "malformed"),
    (json.dumps({"schema_version": "foreign.v1", "pod_id": "pod-1"}), "foreign schema"),
    (json.dumps({"schema_version": "astrid.runpod.claim.v1"}), "no exact pod_id"),
    (json.dumps({
        "schema_version": "astrid.runpod.claim.v1",
        "pod_id": "pod-1",
        "operation_status": "allocated_failure_cleaned",
    }), "failed or stale"),
])
def test_claim_adapter_rejects_existing_invalid_handle_without_fallback(
    monkeypatch, tmp_path: Path, payload: str, message: str
) -> None:
    helper = tmp_path / "claim.py"
    python = tmp_path / "python"
    handle = tmp_path / "claim.json"
    helper.write_text("# helper", encoding="utf-8")
    python.write_text("", encoding="utf-8")
    handle.write_text(payload, encoding="utf-8")

    def unexpected_run(*args, **kwargs):
        raise AssertionError("invalid existing handle must not fall through to a new claim")

    monkeypatch.setattr("astrid.core.execution.runpod_deployment.subprocess.run", unexpected_run)
    adapter = RunPodClaimHelperAdapter(python_executable=python, helper_path=helper)
    with pytest.raises(DeploymentOperationError, match=message):
        adapter.claim(handle)


def test_claim_adapter_preserves_helper_claim_when_no_handle_exists(monkeypatch, tmp_path: Path) -> None:
    helper = tmp_path / "claim.py"
    python = tmp_path / "python"
    handle = tmp_path / "claim.json"
    helper.write_text("# helper", encoding="utf-8")
    python.write_text("", encoding="utf-8")
    seen: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        seen.append(tuple(argv))
        handle.write_text(json.dumps({
            "schema_version": "astrid.runpod.claim.v1", "pod_id": "pod-new"
        }), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("astrid.core.execution.runpod_deployment.subprocess.run", fake_run)
    result = RunPodClaimHelperAdapter(python_executable=python, helper_path=helper).claim(handle)
    assert result["pod_id"] == "pod-new"
    assert seen == [(
        str(python), str(helper), "--max-wait-seconds", "0",
        "--handle-path", str(handle), "--stop-after-allocated-failure",
    )]


def test_lifecycle_cleanup_delegates_exact_pod(monkeypatch) -> None:
    import runpod_lifecycle

    seen: list[Any] = []

    class Config:
        @classmethod
        def from_env(cls, **kwargs):
            seen.append(("config", kwargs))
            return SimpleNamespace(api_key="test-only")

    async def terminate(pod_id, api_key):
        seen.append(("terminate", pod_id, api_key))

    monkeypatch.setattr(runpod_lifecycle, "RunPodConfig", Config)
    monkeypatch.setattr(runpod_lifecycle, "terminate", terminate)
    result = RunPodLifecycleCleanupAdapter().cleanup({
        "pod_id": "pod-owned",
        "name": "claim-pod",
        "gpu_type": "NVIDIA GeForce RTX 5090",
        "storage_name": "backup",
        "network_volume_id": "sfak8553dy",
        "container_disk_gb": 200,
        "worker_image": "image",
        "template_id": None,
    })

    assert result == {
        "status": "terminated",
        "pod_id": "pod-owned",
        "absence_confirmed": True,
        "backup_volume_id": "sfak8553dy",
        "backup_volume_preserved": True,
    }
    assert seen[0][0] == "config"
    assert seen[1] == ("terminate", "pod-owned", "test-only")


def test_concrete_operations_fail_before_claim_without_qualification_owner(tmp_path: Path) -> None:
    class Runtime:
        def get_task(self, task_id):
            return TASK

        def assert_claim_eligible(self, task):
            return None

    class Claim:
        called = False

        def claim(self, handle_path):
            self.called = True
            raise AssertionError("provider claim must not run without qualification")

    claim = Claim()
    operations = ConcreteDeploymentOperations(
        runtime=Runtime(),
        claim=claim,
        cleanup=SimpleNamespace(cleanup=lambda handle: {"status": "terminated"}),
        qualification=None,
        settlement=SimpleNamespace(),
    )

    with pytest.raises(DeploymentOperationError, match="qualification owner"):
        run_existing_h3_task(_request(tmp_path), operations)
    assert claim.called is False


def test_concrete_operations_delegate_order_and_owned_cleanup(tmp_path: Path) -> None:
    calls: list[str] = []
    runtime_task = {
        **TASK,
        "execution_request": {
            **TASK["execution_request"],
            "target": {"kind": "runpod", "pod_id": "pod-1"},
        },
        "target": {"kind": "runpod", "pod_id": "pod-1"},
        "execution_binding": {
            **TASK["execution_binding"],
            "original_target": {"kind": "runpod", "pod_id": "pod-1"},
            "effective_target": {"kind": "runpod", "pod_id": "pod-1"},
            "resolved_target": {"kind": "runpod", "pod_id": "pod-1"},
        },
    }

    class Runtime:
        def get_task(self, task_id):
            calls.append("runtime.get")
            return runtime_task

        def assert_claim_eligible(self, task):
            calls.append("runtime.eligible")

        def retry_existing_task(self, task_id, *, expected_version, idempotency_key, before_queue):
            calls.append("runtime.retry.enter")
            before_queue()
            calls.append("runtime.retry.queue")
            return {"task_id": task_id, "run_id": CANONICAL_RUN_ID}

        def execute_canonical_h3(self, task_id, run_id):
            calls.append("runtime.h3")
            return {"task_id": task_id, "run_id": run_id, "state": "succeeded"}

    class Claim:
        def claim(self, handle_path):
            calls.append("provider.claim")
            return {
                "schema_version": "astrid.runpod.claim.v1",
                "pod_id": "pod-1",
                "network_volume_id": "sfak8553dy",
            }

    class Qualification:
        def prepare_and_qualify(self, task, claim_handle):
            calls.append("qualification.prepare")
            return {"task_id": CANONICAL_TASK_ID, "run_id": CANONICAL_RUN_ID, "pod_id": "pod-1"}

        def assert_fresh(self, task, claim_handle, qualification, *, phase):
            calls.append(f"qualification.{phase}")

    class Cleanup:
        def cleanup(self, claim_handle):
            calls.append("provider.cleanup")
            assert claim_handle["pod_id"] == "pod-1"
            assert claim_handle["network_volume_id"] == "sfak8553dy"
            return {
                "status": "terminated",
                "pod_id": claim_handle["pod_id"],
                "backup_volume_id": claim_handle["network_volume_id"],
                "backup_volume_preserved": True,
            }

    operations = ConcreteDeploymentOperations(
        runtime=Runtime(),
        claim=Claim(),
        cleanup=Cleanup(),
        qualification=Qualification(),
        settlement=SimpleNamespace(
            settle=lambda task_id, run_id: (
                calls.append("settlement")
                or {"task_id": task_id, "run_id": run_id}
            ),
            pullback=lambda settlement, path: (
                calls.append("pullback")
                or {"path": str(path), "decoded": True}
            ),
        ),
    )
    result = run_existing_h3_task(_request(tmp_path), operations)

    assert result["status"] == "completed"
    assert calls.index("qualification.prepare") < calls.index("runtime.retry.enter")
    assert calls.index("qualification.before_queue") < calls.index("runtime.retry.queue")
    assert calls.index("runtime.retry.queue") < calls.index("runtime.h3")
    assert calls.index("runtime.h3") < calls.index("settlement")
    assert calls.index("settlement") < calls.index("pullback")
    assert calls.index("pullback") < calls.index("provider.cleanup")
    assert calls.count("provider.cleanup") == 1
    assert calls[-1] == "provider.cleanup"
    assert result["cleanup_status"] == "complete"
    assert result["cleanup"] == {
        "status": "terminated",
        "pod_id": "pod-1",
        "backup_volume_id": "sfak8553dy",
        "backup_volume_preserved": True,
    }

    class FailingQualification(Qualification):
        def assert_fresh(self, task, claim_handle, qualification, *, phase):
            super().assert_fresh(task, claim_handle, qualification, phase=phase)
            if phase == "before_queue":
                raise DeploymentOperationError("proof changed")

    calls.clear()
    failed = ConcreteDeploymentOperations(
        runtime=Runtime(),
        claim=Claim(),
        cleanup=Cleanup(),
        qualification=FailingQualification(),
        settlement=SimpleNamespace(
            settle=lambda task_id, run_id: {"task_id": task_id, "run_id": run_id},
            pullback=lambda settlement, path: {"path": str(path), "decoded": True},
        ),
    )
    with pytest.raises(DeploymentOperationError, match="proof changed"):
        run_existing_h3_task(_request(tmp_path / "failed"), failed)
    assert calls[-1] == "provider.cleanup"


def test_qualified_owner_parks_replacement_then_commits_runtime_recovery(tmp_path: Path) -> None:
    task = copy.deepcopy(TASK)
    original = task["execution_request"]["target"]
    replacement = {**original, "pod_id": "pod-new"}
    calls: list[str] = []
    loss = {
        "source": "independent-provider", "status": "absent", "target": original,
        "observed_at": "2026-09-28T15:00:00+00:00",
        "evidence_digest": "sha256:" + "a" * 64, "no_active_work": True,
    }

    class Parked:
        handle = object()

        def recovery_qualification(self):
            return {"target": replacement, "verified": True,
                    "evidence_digest": "sha256:" + "b" * 64,
                    "executor_incarnation": "new-incarnation"}

    class Launcher:
        preparer = SimpleNamespace(abort=lambda handle: calls.append("abort"))

        def park(self, launch, *, target):
            calls.append("park")
            assert target == replacement
            return Parked()

        def activate(self, fresh, reference, parked):
            calls.append("activate")
            assert fresh["execution_binding"]["effective_target"] == replacement
            return {"task_id": CANONICAL_TASK_ID, "run_id": CANONICAL_RUN_ID,
                    "binding_digest": "sha256:" + "c" * 64}

        def assert_fresh(self, fresh, reference, parked, qualification):
            calls.append("fresh")
            assert fresh["execution_binding"]["effective_target"] == replacement

    class Runtime:
        def recover_task_placement(self, task_id, body, *, idempotency_key, identity):
            calls.append("recover")
            assert task_id == CANONICAL_TASK_ID
            assert identity == {"actor": "owner", "scopes": ["admin"]}
            assert body["qualification"]["target"] == replacement
            assert body["expected_original_target"] == original
            assert body["expected_task_version"] == TASK["version"]
            decision = {
                "task_id": task_id, "run_id": CANONICAL_RUN_ID,
                "original_target": original, "replacement_target": replacement,
                "placement_version": 1, "qualification": body["qualification"],
            }
            encoded = json.dumps(decision, sort_keys=True, separators=(",", ":"))
            decision["decision_digest"] = "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()
            task["execution_binding"].update({
                "effective_target": replacement, "resolved_target": replacement,
                "placement_version": 1, "recovery_decision_digest": decision["decision_digest"],
                "placement_recovery": decision,
            })
            return {"data": {"placement_recovery": decision}}

    owner = QualifiedRunPodDeploymentOwner(
        runtime=Runtime(), launcher=Launcher(), task_reader=lambda task_id: task,
        launch_factory=lambda task, handle: {"launch": "parked"},
        reference_factory=lambda task, handle: {"reference": "qualified"},
        loss_observer=lambda target: loss,
        owner_identity={"actor": "owner", "scopes": ["admin"]},
        operation_id="same-task-replacement",
    )
    owner.assert_claim_eligible(task)
    qualification = owner.prepare_and_qualify(task, {
        "schema_version": "astrid.runpod.claim.v1", "pod_id": "pod-new",
    })
    assert qualification["task_id"] == CANONICAL_TASK_ID
    owner.assert_fresh(task, {"schema_version": "astrid.runpod.claim.v1", "pod_id": "pod-new"},
                       qualification, phase="before_queue")
    assert calls == ["park", "recover", "activate", "fresh"]
    assert task["execution_request"]["target"] == original
    assert task["input_object_ids"] == TASK["input_object_ids"]
