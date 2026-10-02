from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrid.core.execution.runpod_deployment import (
    CANONICAL_RUN_ID,
    CANONICAL_TASK_ID,
    DeploymentOperationError,
    QualifiedRunPodDeploymentOwner,
)
from scripts import run_h3_canonical_on_runpod as canonical
from tests.test_runpod_deployment_operation import TASK


def _task() -> dict:
    task = copy.deepcopy(TASK)
    target = {"kind": "runpod", "pod_id": "pod-1"}
    task["execution_request"]["target"] = target
    task["target"] = target
    task["execution_binding"].update({
        "original_target": target,
        "effective_target": target,
        "resolved_target": target,
    })
    return task


class _Runtime:
    def __init__(self, task: dict, calls: list[str]):
        self.task = task
        self.calls = calls

    def get_task(self, task_id):
        assert task_id == CANONICAL_TASK_ID
        self.calls.append("runtime.get")
        return self.task

    def assert_claim_eligible(self, task):
        self.calls.append("runtime.eligible")

    def retry_existing_task(self, task_id, *, expected_version, idempotency_key, before_queue):
        assert (task_id, expected_version, idempotency_key) == (CANONICAL_TASK_ID, 7, "operation-1")
        self.calls.append("runtime.retry.enter")
        before_queue()
        self.calls.append("runtime.retry.queue")
        return {"task_id": task_id, "run_id": CANONICAL_RUN_ID}

    def execute_canonical_h3(self, task_id, run_id):
        assert (task_id, run_id) == (CANONICAL_TASK_ID, CANONICAL_RUN_ID)
        self.calls.append("runtime.h3")
        return {"task_id": task_id, "run_id": run_id, "state": "succeeded"}


class _Claim:
    def __init__(self, calls: list[str]):
        self.calls = calls
        self.called = False

    def claim(self, handle_path: Path):
        self.called = True
        self.calls.append("provider.claim")
        return {
            "schema_version": "astrid.runpod.claim.v1",
            "pod_id": "pod-1",
            "network_volume_id": "sfak8553dy",
        }


class _Qualification:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def prepare_and_qualify(self, task, claim_handle):
        assert task["id"] == CANONICAL_TASK_ID
        assert claim_handle["pod_id"] == "pod-1"
        self.calls.append("qualification.prepare")
        return {"task_id": CANONICAL_TASK_ID, "run_id": CANONICAL_RUN_ID}

    def assert_fresh(self, task, claim_handle, qualification, *, phase):
        assert qualification is not None
        assert task["id"] == qualification["task_id"]
        self.calls.append(f"qualification.{phase}")


class _Cleanup:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def cleanup(self, claim_handle):
        self.calls.append("cleanup")
        return {"status": "terminated", "pod_id": claim_handle["pod_id"]}


class _Settlement:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def settle(self, task_id, run_id):
        self.calls.append("settle")
        return {"task_id": task_id, "run_id": run_id}

    def pullback(self, settlement, output_path):
        self.calls.append("pullback")
        return {"path": str(output_path), "decoded": True}


def _patch_adapters(monkeypatch, calls: list[str], task: dict):
    runtime = _Runtime(task, calls)
    claim = _Claim(calls)
    qualification = _Qualification(calls)
    monkeypatch.setattr(canonical, "AstridRuntimeTaskAdapter", lambda client: runtime)
    monkeypatch.setattr(canonical, "RunPodClaimHelperAdapter", lambda **kwargs: claim)
    monkeypatch.setattr(canonical, "RunPodLifecycleCleanupAdapter", lambda: _Cleanup(calls))
    monkeypatch.setattr(canonical, "AstridManagedOutputSettlementAdapter", lambda client: _Settlement(calls))
    return runtime, claim, qualification


def test_callable_front_end_keeps_live_boundary_closed(tmp_path, monkeypatch):
    calls: list[str] = []
    _runtime, claim, _qualification = _patch_adapters(monkeypatch, calls, _task())
    operations = canonical.build_concrete_operations(
        object(), handle_path=tmp_path / "claim-handle.json", operation_id="operation-1",
    )

    with pytest.raises(DeploymentOperationError, match="task/run-scoped"):
        canonical.run(
            operation_id="operation-1",
            receipt_path=(tmp_path / "receipt.json").resolve(),
            handle_path=(tmp_path / "claim-handle.json").resolve(),
            output_path=(tmp_path / "output.mkv").resolve(),
            operations=operations,
            task_id=CANONICAL_TASK_ID, run_id=CANONICAL_RUN_ID,
        )
    assert claim.called is False
    assert "provider.claim" not in calls


def test_fake_factory_is_rejected_before_provider_claim(tmp_path, monkeypatch):
    calls: list[str] = []
    _runtime, _claim, qualification = _patch_adapters(monkeypatch, calls, _task())
    factory_calls: list[dict] = []

    def factory(**context):
        factory_calls.append(context)
        return qualification

    with pytest.raises(DeploymentOperationError, match="real Runtime-qualified"):
        canonical.build_concrete_operations(
            object(), handle_path=tmp_path / "claim-handle.json", operation_id="operation-1",
            qualification_factory=factory,
        )
    assert factory_calls[0]["operation_id"] == "operation-1"
    assert "provider.claim" not in calls


def test_canonical_front_end_does_not_pin_a_stale_provider_pod(tmp_path, monkeypatch):
    captured = {}

    class _ClaimAdapter:
        pass

    monkeypatch.setattr(canonical, "AstridRuntimeTaskAdapter", lambda client: object())
    def capture_claim(**kwargs):
        captured["claim"] = kwargs
        return _ClaimAdapter()

    monkeypatch.setattr(canonical, "RunPodClaimHelperAdapter", capture_claim)
    monkeypatch.setattr(canonical, "RunPodLifecycleCleanupAdapter", lambda: object())
    monkeypatch.setattr(canonical, "AstridManagedOutputSettlementAdapter", lambda client: object())

    canonical.build_concrete_operations(
        object(), handle_path=tmp_path / "claim-handle.json", operation_id="operation-1"
    )

    assert captured["claim"].get("expected_pod") is None


def test_build_qualified_owner_uses_existing_runtime_owner_and_t12_kernel():
    owner = canonical.build_qualified_owner(
        runtime=object(),
        launcher=object(),
        task_reader=lambda task_id: {},
        launch_factory=lambda task, handle: object(),
        reference_factory=lambda task, handle: object(),
        loss_observer=lambda target: {},
        owner_identity={"actor": "owner", "scopes": ["admin"]},
        operation_id="operation-1",
    )
    assert isinstance(owner, QualifiedRunPodDeploymentOwner)


def test_live_cli_quarantine_blocks_before_runtime_or_provider_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    from astrid.sdk import AstridClient

    monkeypatch.setattr(
        AstridClient,
        "open_from_launcher",
        lambda **_kwargs: calls.append("AstridClient.open_from_launcher"),
    )
    monkeypatch.setattr(
        canonical,
        "build_concrete_operations",
        lambda *_args, **_kwargs: calls.append("build_concrete_operations"),
    )
    monkeypatch.setattr(
        canonical,
        "RunPodClaimHelperAdapter",
        lambda **_kwargs: calls.append("claim_helper"),
    )
    monkeypatch.setattr(
        canonical,
        "run_existing_h3_task",
        lambda *_args, **_kwargs: calls.append("task_admission_or_retry"),
    )

    def qualification_factory(**_kwargs):
        calls.append("qualification_factory")
        return object()

    assert canonical.main(
        ["--operation-id", "operation-1", "--receipt", str(tmp_path / "receipt.json")],
        qualification_factory=qualification_factory,
    ) == 2
    assert calls == []


def test_live_quarantine_error_is_clear_and_machine_readable():
    error = canonical.H3LiveQuarantineError()
    payload = error.as_dict()

    assert payload == {
        "status": "blocked",
        "code": "h3_live_runpod_quarantined",
        "operation": "h3_live_runpod_submit",
        "offline_only": True,
        "provider_allocation": False,
        "runtime_task_admission": False,
        "runtime_task_retry": False,
        "credential_issuance": False,
        "activation": False,
        "message": str(error),
    }
    assert json.loads(json.dumps(payload)) == payload
    assert "quarantined" in str(error)


def test_cleanup_only_cli_skips_live_qualification_factory_and_uses_explicit_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astrid.sdk import AstridClient

    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"phase": "cleanup_pending", "task_id": CANONICAL_TASK_ID,
                                   "run_id": CANONICAL_RUN_ID}), encoding="utf-8")
    data_root = tmp_path / "task-realm"
    monkeypatch.setenv("ASTRID_H3_DATA_ROOT", str(data_root))
    calls = []

    class Opened:
        def __enter__(self):
            return SimpleNamespace(tasks=SimpleNamespace(control_remote_credential=None))

        def __exit__(self, *_args):
            return False

    def open_client(**kwargs):
        calls.append(("open", kwargs))
        return Opened()

    def build(_client, **kwargs):
        calls.append(("build", kwargs))
        return object()

    monkeypatch.setattr(AstridClient, "open_from_launcher", open_client)
    monkeypatch.setattr(canonical, "build_concrete_operations", build)
    monkeypatch.setattr(canonical, "resume_h3_settlement", lambda *_args: {"status": "completed"})
    assert canonical.main([
        "--operation-id", "operation-1", "--receipt", str(receipt),
        "--resume-settlement",
    ], qualification_factory=lambda **_kwargs: pytest.fail("live factory must not run")) == 0
    assert calls[0] == ("open", {"start_pack_host": False, "data_root": data_root})
    assert calls[1][1]["qualification_factory"] is None


def test_cleanup_resume_reports_failed_generation_as_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astrid.sdk import AstridClient

    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"phase": "cleanup_pending", "task_id": CANONICAL_TASK_ID,
                                   "run_id": CANONICAL_RUN_ID}), encoding="utf-8")

    class Opened:
        def __enter__(self):
            return SimpleNamespace(tasks=SimpleNamespace(control_remote_credential=None))

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(AstridClient, "open_from_launcher", lambda **_kwargs: Opened())
    monkeypatch.setattr(canonical, "build_concrete_operations", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(canonical, "resume_h3_settlement", lambda *_args: {"status": "failed"})

    assert canonical.main([
        "--operation-id", "operation-1", "--receipt", str(receipt),
        "--resume-settlement",
        "--data-root", str(tmp_path / "task-realm"),
    ]) == 1


def _candidate(monkeypatch):
    monkeypatch.setattr(canonical, "_git_value", lambda *args: {
        "branch": "candidate-branch", "rev-parse": "a" * 40, "status": " M scripts/launcher.py",
    }[args[0]])
    monkeypatch.setattr(canonical, "candidate_content_digest", lambda _root: "sha256:" + "b" * 64)
    return {"expected_branch": "candidate-branch", "expected_head": "a" * 40,
            "content_digest": "sha256:" + "b" * 64}


@pytest.mark.parametrize("change, message", [
    ({"expected_branch": "foreign"}, "branch mismatch"),
    ({"expected_head": "c" * 40}, "HEAD mismatch"),
    ({"content_digest": "sha256:" + "c" * 64}, "digest mismatch"),
    ({"content_digest": None}, "requires an explicit content digest"),
    ({"expected_branch": None}, "explicit candidate"),
])
def test_candidate_rejects_wrong_or_missing_snapshot(monkeypatch, change, message):
    inputs = _candidate(monkeypatch)
    with pytest.raises(canonical.SourceCustodyError, match=message):
        canonical.assert_product_source_identity(**{**inputs, **change})


def test_capsule_pins_dirty_source_and_rejects_conflicts(tmp_path, monkeypatch):
    inputs = _candidate(monkeypatch)
    capsule = tmp_path / "capsule.json"
    capsule.write_text(json.dumps({"schema_version": "astrid.h3.source-capsule.v1", **inputs,
                                   "source_root": str(canonical.PRODUCT_SOURCE_ROOT)}))
    identity = canonical.assert_product_source_identity(capsule=capsule)
    assert identity["content_digest"] == inputs["content_digest"]
    assert identity["dirty"] is True
    with pytest.raises(canonical.SourceCustodyError, match="conflicts"):
        canonical.assert_product_source_identity(capsule=capsule, expected_head="c" * 40)
    capsule.write_text(json.dumps({"schema_version": "astrid.h3.source-capsule.v1",
                                   "expected_branch": inputs["expected_branch"],
                                   "expected_head": inputs["expected_head"]}))
    with pytest.raises(canonical.SourceCustodyError, match="requires content_digest"):
        canonical.assert_product_source_identity(capsule=capsule)


def test_authorized_live_still_rejects_before_runtime_open(tmp_path, monkeypatch, capsys):
    from astrid.sdk import AstridClient
    monkeypatch.setattr(AstridClient, "open", lambda **_kw: pytest.fail("Runtime must not open"))
    assert canonical.main(["--operation-id", "op", "--receipt", str(tmp_path / "receipt"),
                           "--lane", "lane-a", "--authorize-lane", "lane-a"]) == 2
    assert "task/run-scoped" in capsys.readouterr().err
