"""Thin coordinator composition for one explicitly identified existing H3 task.

Provider allocation, Runtime authority, task retry, H3 execution, and teardown
remain owned by their existing adapters. This module only orders those calls
and records a redacted operation receipt; it is not a scheduler or task API.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


CANONICAL_TASK_ID = "5b908bceb1564eef9b8197c9b8cfdbd9"
CANONICAL_RUN_ID = "76746d8e67904bdc8e679876b53b5428"


class DeploymentOperationError(RuntimeError):
    """The existing task cannot safely continue through this operation."""


class DeploymentOperations(Protocol):
    """Callbacks implemented by the existing provider/Runtime/H3 owners."""

    def get_task(self, task_id: str) -> Mapping[str, Any]: ...

    def assert_claim_eligible(self, task: Mapping[str, Any]) -> None: ...

    def claim_pod(self, handle_path: Path) -> Mapping[str, Any]: ...

    def prepare_and_qualify(
        self, task: Mapping[str, Any], claim_handle: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def assert_fresh(
        self,
        task: Mapping[str, Any],
        claim_handle: Mapping[str, Any],
        qualification: Mapping[str, Any],
        *,
        phase: str,
    ) -> None: ...

    def retry_existing_task(
        self,
        task_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        before_queue: Callable[[], None],
    ) -> Mapping[str, Any]: ...

    def execute_canonical_h3(
        self,
        task_id: str,
        run_id: str,
    ) -> Mapping[str, Any]: ...

    def cleanup_owned_pod(
        self,
        claim_handle: Mapping[str, Any],
        *,
        task_id: str,
        run_id: str,
        activation_id: str | None = None,
        phase_observer: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> Mapping[str, Any]: ...

    def settle_managed_result(
        self,
        task_id: str,
        run_id: str,
        *,
        phase_observer: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> Mapping[str, Any]: ...

    def pullback_managed_result(self, settlement: Mapping[str, Any], output_path: Path) -> Mapping[str, Any]: ...


class DeploymentQualificationOwner(Protocol):
    """Existing owner for RunPod release/host/Runtime qualification."""

    def prepare_and_qualify(
        self, task: Mapping[str, Any], claim_handle: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def assert_fresh(
        self,
        task: Mapping[str, Any],
        claim_handle: Mapping[str, Any],
        qualification: Mapping[str, Any],
        *,
        phase: str,
    ) -> None: ...


class QualifiedRunPodDeploymentOwner:
    """Bind the parked host to Runtime's exact task and T12 recovery decision.

    The supplied launcher owns preparation, independent inspection, credential
    issuance and private acknowledgement. The loss observer is a read-only
    provider inspector; only Runtime's existing owner transition can move the
    effective placement. No provider call occurs in this class.
    """

    def __init__(self, *, runtime: Any, launcher: Any,
                 task_reader: Callable[[str], Mapping[str, Any]],
                 launch_factory: Callable[[Mapping[str, Any], Mapping[str, Any]], Any],
                 reference_factory: Callable[[Mapping[str, Any], Mapping[str, Any]], Any],
                 loss_observer: Callable[[Mapping[str, Any]], Mapping[str, Any]],
                 owner_identity: Mapping[str, Any], operation_id: str,
                 task_id: str = CANONICAL_TASK_ID,
                 run_id: str = CANONICAL_RUN_ID) -> None:
        self.runtime = runtime
        self.launcher = launcher
        self.task_reader = task_reader
        self.launch_factory = launch_factory
        self.reference_factory = reference_factory
        self.loss_observer = loss_observer
        self.owner_identity = dict(owner_identity)
        self.operation_id = operation_id
        if not isinstance(task_id, str) or not task_id.strip():
            raise DeploymentOperationError("qualification owner task_id is required")
        self.task_id = task_id
        if not isinstance(run_id, str) or not run_id.strip():
            raise DeploymentOperationError("qualification owner run_id is required")
        self.run_id = run_id
        self._parked: Any = None
        self._reference: Any = None
        self.activation_state = "inactive"

    def assert_claim_eligible(self, task: Mapping[str, Any]) -> None:
        _task_binding(task, task_id=self.task_id, run_id=self.run_id)
        binding = _runtime_deployment_binding(task)
        if binding.admission_identity.task_id != self.task_id:
            raise DeploymentOperationError("qualification owner received a foreign task")
        if self.owner_identity.get("actor") != "owner" or "admin" not in self.owner_identity.get("scopes", ()):
            raise DeploymentOperationError("Runtime owner authority is required before allocation")
        original = binding.placement.original_target
        if binding.placement.effective_target == original:
            loss = self.loss_observer(original)
            if (loss.get("status") != "absent" or loss.get("no_active_work") is not True
                    or loss.get("target") != original or not loss.get("evidence_digest")):
                raise DeploymentOperationError("authoritative loss and quiescence are required before replacement allocation")

    def prepare_and_qualify(self, task: Mapping[str, Any], claim_handle: Mapping[str, Any]) -> Mapping[str, Any]:
        _task_binding(task, task_id=self.task_id, run_id=self.run_id)
        if self.owner_identity.get("actor") != "owner" or "admin" not in self.owner_identity.get("scopes", ()):
            raise DeploymentOperationError("Runtime owner authority is required before preparation")
        self.activation_state = "inactive"
        binding = _runtime_deployment_binding(task)
        target = binding.placement.effective_target
        pod_id = _claim_pod_id(claim_handle)
        replacement = target.get("pod_id") != pod_id
        if replacement:
            if binding.placement.placement_version != 0:
                raise DeploymentOperationError("claim handle differs from the authorized recovered placement")
            target = {**target, "pod_id": pod_id}
        parked = self.launcher.park(self.launch_factory(task, claim_handle), target=target)
        try:
            if replacement:
                loss = self.loss_observer(binding.placement.effective_target)
                old = binding.placement.original_target
                recovery = self.runtime.recover_task_placement(
                    self.task_id,
                    {
                        "schema_version": 1,
                        "expected_task_version": _task_version(task),
                        "expected_placement_version": binding.placement.placement_version,
                        "expected_original_target": old,
                        "expected_current_target": binding.placement.effective_target,
                        "replacement_target": target,
                        "reason": "original exact pod is authoritatively absent",
                        "loss_evidence": dict(loss),
                        "qualification": parked.recovery_qualification(),
                    },
                    idempotency_key=f"{self.operation_id}-placement-recovery",
                    identity=self.owner_identity,
                )
                if not isinstance(recovery, Mapping):
                    raise DeploymentOperationError("Runtime did not commit placement recovery")
            fresh = self.task_reader(self.task_id)
            fresh_binding = _runtime_deployment_binding(fresh)
            if (fresh_binding.admission_identity != binding.admission_identity
                    or fresh_binding.capability_identity != binding.capability_identity
                    or fresh_binding.input_bindings != binding.input_bindings
                    or fresh_binding.placement.original_target != binding.placement.original_target
                    or fresh_binding.placement.effective_target != target):
                raise DeploymentOperationError("Runtime recovery changed canonical admission or inputs")
            reference = self.reference_factory(fresh, claim_handle)
            self.activation_state = "unknown"
            qualification = self.launcher.activate(fresh, reference, parked)
            self.activation_state = "active"
            self._parked = parked
            self._reference = reference
            return qualification
        except Exception:
            if self.activation_state == "unknown":
                self.activation_state = getattr(self.launcher, "activation_state", "unknown")
            try:
                self.launcher.preparer.abort(parked.handle)
            except Exception:
                pass
            raise

    def assert_fresh(self, task: Mapping[str, Any], claim_handle: Mapping[str, Any],
                     qualification: Mapping[str, Any], *, phase: str) -> None:
        _task_binding(task, task_id=self.task_id, run_id=self.run_id)
        if self._parked is None or self._reference is None:
            raise DeploymentOperationError("remote host has not been activated")
        if _runtime_deployment_binding(task).placement.effective_target.get("pod_id") != _claim_pod_id(claim_handle):
            raise DeploymentOperationError("effective placement no longer matches the owned pod")
        self.launcher.assert_fresh(task, self._reference, self._parked, qualification)


def _owner_result(value: Any, label: str) -> Mapping[str, Any]:
    """Unwrap an Astrid DomainResult while accepting test doubles."""
    if isinstance(value, Mapping):
        if value.get("ok") is False:
            raise DeploymentOperationError(f"{label} failed: {value.get('error') or value}")
        data = value.get("data", value)
    else:
        if getattr(value, "ok", True) is False:
            raise DeploymentOperationError(f"{label} failed: {getattr(value, 'error', value)}")
        data = getattr(value, "data", None)
    if not isinstance(data, Mapping):
        raise DeploymentOperationError(f"{label} returned no object")
    return data


class AstridRuntimeTaskAdapter:
    """Concrete adapter over the existing Astrid Runtime task family."""

    def __init__(self, client: Any, *, wait_timeout_seconds: float = 3600.0, poll_seconds: float = 1.0) -> None:
        self.client = client
        self.wait_timeout_seconds = wait_timeout_seconds
        self.poll_seconds = poll_seconds

    def get_task(self, task_id: str) -> Mapping[str, Any]:
        task = dict(_owner_result(self.client.tasks.show(task_id), "Runtime task lookup"))
        if "id" not in task and task.get("task_id") is not None:
            task["id"] = task["task_id"]
        _runtime_deployment_binding(task)
        return task

    def assert_claim_eligible(self, task: Mapping[str, Any]) -> None:
        state = str(task.get("state") or task.get("status") or "").lower()
        if state not in {"failed", "expired"}:
            raise DeploymentOperationError(
                f"canonical task is not eligible for retry: state={state or 'unknown'}"
            )
        target = _runtime_deployment_binding(task).placement.effective_target
        if target.get("kind") != "runpod" or not target.get("pod_id"):
            raise DeploymentOperationError("canonical task has no exact RunPod target")

    def retry_existing_task(
        self,
        task_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        before_queue: Callable[[], None],
    ) -> Mapping[str, Any]:
        # The Runtime retry is the existing canonical queue admission boundary.
        before_queue()
        return _owner_result(
            self.client.tasks.retry(
                task_id,
                idempotency_key=idempotency_key,
                expected_version=expected_version,
            ),
            "canonical task retry",
        )

    def execute_canonical_h3(self, task_id: str, run_id: str) -> Mapping[str, Any]:
        """Follow the retried task; its admitted orchestrator owns H3 execution."""
        deadline = time.monotonic() + self.wait_timeout_seconds
        while True:
            task = dict(self.get_task(task_id))
            observed_run = task.get("run_id")
            if observed_run != run_id:
                raise DeploymentOperationError("canonical task changed run identity while executing")
            state = str(task.get("state") or task.get("status") or "").lower()
            if state in {"succeeded", "completed"}:
                return task
            if state in {"failed", "cancelled"}:
                raise DeploymentOperationError(f"canonical H3 task ended in state={state}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeploymentOperationError("canonical H3 task wait deadline expired")
            time.sleep(min(self.poll_seconds, remaining))


class RunPodClaimHelperAdapter:
    """Invoke the repository's existing claim helper without a second provider client."""

    def __init__(self, *, python_executable: Path, helper_path: Path,
                 existing_only: bool = False, expected_pod: str | None = None) -> None:
        self.python_executable = python_executable
        self.helper_path = helper_path
        self.existing_only = existing_only
        self.expected_pod = expected_pod

    @staticmethod
    def _read_existing_handle(handle_path: Path) -> Mapping[str, Any]:
        try:
            value = json.loads(handle_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeploymentOperationError("existing claim handle is unreadable or malformed") from exc
        if not isinstance(value, Mapping):
            raise DeploymentOperationError("existing claim handle must be a JSON object")
        if value.get("schema_version") != "astrid.runpod.claim.v1":
            raise DeploymentOperationError("existing claim handle has a foreign schema")
        pod_id = value.get("pod_id")
        if not isinstance(pod_id, str) or not pod_id.strip():
            raise DeploymentOperationError("existing claim handle has no exact pod_id")
        if value.get("operation_status") in {
            "allocated_failure_cleaned", "failed", "terminated", "stale"
        }:
            raise DeploymentOperationError("existing claim handle records a failed or stale allocation")
        # This handle is locally adopted only. This adapter has no independent
        # provider/SSH probe; downstream qualification must establish liveness
        # before Runtime retry. Do not interpret the schema as live-pod proof.
        return dict(value)

    def claim(self, handle_path: Path) -> Mapping[str, Any]:
        if handle_path.exists():
            value = self._read_existing_handle(handle_path)
            if self.expected_pod is not None and value.get("pod_id") != self.expected_pod:
                raise DeploymentOperationError("existing claim belongs to a different pod")
            return value
        if self.existing_only:
            raise DeploymentOperationError("exact existing claim handle is required; a new claim is forbidden")
        if not self.python_executable.is_file() or not self.helper_path.is_file():
            raise DeploymentOperationError("Astrid interpreter or canonical claim helper is unavailable")
        argv = (
            str(self.python_executable),
            str(self.helper_path),
            "--max-wait-seconds", "0",
            "--handle-path", str(handle_path),
            "--stop-after-allocated-failure",
        )
        completed = subprocess.run(argv, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "claim helper failed").strip()[-4000:]
            raise DeploymentOperationError(f"claim helper failed: {detail}")
        try:
            value = json.loads(handle_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeploymentOperationError("claim helper did not leave a readable claim handle") from exc
        if not isinstance(value, Mapping) or value.get("schema_version") != "astrid.runpod.claim.v1":
            raise DeploymentOperationError("claim helper did not return a verified claim-v1 handle")
        if not isinstance(value.get("pod_id"), str) or not value["pod_id"].strip():
            raise DeploymentOperationError("claim helper returned no exact pod_id")
        return value


class RunPodLifecycleCleanupAdapter:
    """Terminate only the exact owned pod through runpod-lifecycle."""

    def cleanup(self, claim_handle: Mapping[str, Any]) -> Mapping[str, Any]:
        pod_id = claim_handle.get("pod_id")
        if not isinstance(pod_id, str) or not pod_id.strip():
            raise DeploymentOperationError("cannot clean up a claim without an exact pod_id")
        from runpod_lifecycle import RunPodConfig, terminate as terminate_pod

        config = RunPodConfig.from_env(
            gpu_type=claim_handle.get("gpu_type"),
            storage_name=claim_handle.get("storage_name"),
            storage_volumes=(claim_handle.get("storage_name"),)
            if claim_handle.get("storage_name")
            else (),
            container_disk_gb=claim_handle.get("container_disk_gb"),
            worker_image=claim_handle.get("worker_image"),
            template_id=claim_handle.get("template_id"),
        )

        async def terminate() -> None:
            # The lifecycle boundary verifies provider absence and treats a 404
            # as idempotent success. Replaying this exact-pod operation once is
            # therefore the recovery path for a lost mutation response.
            failure: BaseException | None = None
            for _attempt in range(2):
                try:
                    await terminate_pod(pod_id, config.api_key)
                    return
                except BaseException as exc:  # noqa: BLE001 - preserve unknown response
                    failure = exc
            raise DeploymentOperationError(
                f"exact-pod termination remains unknown for {pod_id}"
            ) from failure

        asyncio.run(terminate())
        return {
            "status": "terminated",
            "pod_id": pod_id,
            "absence_confirmed": True,
            "backup_volume_id": claim_handle.get("network_volume_id"),
            "backup_volume_preserved": True,
        }


class AstridManagedOutputSettlementAdapter:
    """Read the existing task settlement and materialize its managed result."""

    def __init__(self, client: Any, *, decode: Callable[[Path], Mapping[str, Any]] | None = None) -> None:
        self.client = client
        self.decode = decode or _decode_managed_video

    def settle(
        self,
        task_id: str,
        run_id: str,
        *,
        phase_observer: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> Mapping[str, Any]:
        observe = phase_observer or (lambda _phase, _evidence: None)
        parent = _owner_result(self.client.tasks.show(task_id), "Runtime parent settlement read")
        parent_id = parent.get("id", parent.get("task_id"))
        if parent_id != task_id or parent.get("run_id") != run_id:
            raise DeploymentOperationError("managed settlement belongs to a different parent task or run")
        if _task_state(parent) not in {"succeeded", "completed"}:
            raise DeploymentOperationError("parent H3 task is not authoritatively settled")
        project_id = _required_string(parent.get("project_id"), "parent project_id")
        parent_attempt_id = _required_string(parent.get("attempt_id"), "parent attempt_id")
        parent_spec = _task_spec(parent)
        final_publication = parent_spec.get("child_delegation", {}).get("final_publication")
        if not _is_h3_final_publication(final_publication, project_id):
            raise DeploymentOperationError("parent does not carry the fixed H3 final-only publication contract")

        children = self._project_tasks(project_id)
        finalizers = [
            row for row in children
            if isinstance(row, Mapping)
            and row.get("capability_id") == "h3_av.publication_finalizer"
            and _task_spec(row).get("delegated_stage") == "finalize"
            and _delegated_parent_matches(
                _task_spec(row).get("delegated_parent"),
                parent_task_id=task_id,
                parent_attempt_id=parent_attempt_id,
                project_id=project_id,
            )
        ]
        if len(finalizers) != 1:
            raise DeploymentOperationError(f"expected one exact H3 finalizer child, found {len(finalizers)}")
        finalizer_id = _required_string(
            finalizers[0].get("task_id", finalizers[0].get("id")), "finalizer task_id"
        )
        finalizer = _owner_result(
            self.client.tasks.show(finalizer_id), "Runtime finalizer task read"
        )
        finalizer_attempt_id = _required_string(finalizer.get("attempt_id"), "finalizer attempt_id")
        finalizer_run_id = _required_string(finalizer.get("run_id"), "finalizer run_id")
        if (
            _task_state(finalizer) not in {"succeeded", "completed"}
            or finalizer.get("project_id") != project_id
            or finalizer.get("capability_id") != "h3_av.publication_finalizer"
        ):
            raise DeploymentOperationError("H3 finalizer child is not exactly settled")
        finalizer_spec = _task_spec(finalizer)
        if not _delegated_parent_matches(
            finalizer_spec.get("delegated_parent"),
            parent_task_id=task_id,
            parent_attempt_id=parent_attempt_id,
            project_id=project_id,
        ):
            raise DeploymentOperationError("H3 finalizer is foreign to the parent attempt")

        source = finalizer_spec.get("verified_publication_source")
        if not isinstance(source, Mapping):
            raise DeploymentOperationError("H3 finalizer has no Runtime-verified publication source")
        verify_task_id = _required_string(source.get("producer_task_id"), "verify task_id")
        verify_attempt_id = _required_string(source.get("producer_attempt_id"), "verify attempt_id")
        verify_association_id = _required_string(source.get("association_id"), "verify association_id")
        source_object_id = _canonical_object_id(source.get("object_id"), "verified source object_id")
        if source.get("producer_stage") != "verify" or source.get("output_port") != "verified_candidate":
            raise DeploymentOperationError("H3 finalizer source is not the declared verify output")
        verify_task = _owner_result(self.client.tasks.show(verify_task_id), "Runtime verify task read")
        if (
            _task_state(verify_task) not in {"succeeded", "completed"}
            or verify_task.get("attempt_id") != verify_attempt_id
            or verify_task.get("project_id") != project_id
            or verify_task.get("capability_id") != "h3_av.verify"
            or _task_spec(verify_task).get("delegated_stage") != "verify"
            or not _delegated_parent_matches(
                _task_spec(verify_task).get("delegated_parent"),
                parent_task_id=task_id,
                parent_attempt_id=parent_attempt_id,
                project_id=project_id,
            )
        ):
            raise DeploymentOperationError("verified source task is foreign to the parent attempt")
        verify_row = _owner_result(
            self.client.tasks.get_managed_output(verify_association_id),
            "Runtime verified-source association read",
        )
        _require_managed_row(
            verify_row,
            task_id=verify_task_id,
            attempt_id=verify_attempt_id,
            project_id=project_id,
            association_id=verify_association_id,
            object_id=source_object_id,
            output_port="verified_candidate",
            capability_id="h3_av.verify",
            require_generation=False,
        )
        observe("verified", {
            "verify_task_id": verify_task_id,
            "verify_attempt_id": verify_attempt_id,
            "verify_association_id": verify_association_id,
            "object_id": source_object_id,
        })

        outputs = _task_outputs(finalizer)
        if len(outputs) != 1:
            raise DeploymentOperationError("finalizer result must contain exactly one output")
        observe("finalizer_succeeded", {
            "finalizer_task_id": finalizer_id,
            "finalizer_attempt_id": finalizer_attempt_id,
            "finalizer_run_id": finalizer_run_id,
        })
        observe("publication_unknown", {
            "finalizer_task_id": finalizer_id,
            "reason": "publication readback pending",
        })

        managed_rows = _owner_list(
            self.client.tasks.list_managed_outputs(finalizer_id),
            "Runtime finalizer managed-output read",
        )
        matches = [
            row for row in managed_rows
            if isinstance(row, Mapping)
            and row.get("output_port") == "verified_candidate"
            and row.get("group_key") == "main"
            and row.get("variant_key") == "original"
            and row.get("ordinal") == 0
            and row.get("role") == "result"
        ]
        if len(matches) != 1:
            raise DeploymentOperationError(
                f"expected one exact published H3 finalizer association, found {len(matches)}"
            )
        row = dict(matches[0])
        association_id = _required_string(row.get("association_id"), "publication association_id")
        object_id = _canonical_object_id(row.get("object_id", row.get("digest")), "publication object_id")
        if object_id != source_object_id:
            raise DeploymentOperationError("finalizer publication object differs from verified source")
        size = _positive_size(row.get("size"), "publication size")
        generation_id = _required_string(row.get("generation_id"), "publication generation_id")
        direct = _owner_result(
            self.client.tasks.get_managed_output(association_id),
            "Runtime finalizer association read",
        )
        _require_managed_row(
            direct,
            task_id=finalizer_id,
            attempt_id=finalizer_attempt_id,
            project_id=project_id,
            association_id=association_id,
            object_id=object_id,
            output_port="verified_candidate",
            capability_id="h3_av.publication_finalizer",
            generation_id=generation_id,
        )
        if any(
            direct.get(field) != row.get(field)
            for field in (
                "task_id", "attempt_id", "project_id", "run_id", "association_id",
                "generation_id", "variant_key", "output_port", "object_id", "size",
            )
        ):
            raise DeploymentOperationError("listed and direct publication associations disagree")
        if not _output_matches(outputs[0], object_id=object_id, size=size, output_port="verified_candidate"):
            raise DeploymentOperationError("managed association does not match finalizer task output")

        generation = _owner_result(
            self.client.generations.show(project_id, generation_id),
            "Runtime generation read",
        )
        if (
            generation.get("generation_id") != generation_id
            or generation.get("project_id") != project_id
            or generation.get("source_task_id") != finalizer_id
            or generation.get("type") != "h3_av.publication_finalizer"
            or generation.get("status") != "completed"
        ):
            raise DeploymentOperationError("published generation is foreign to the H3 finalizer")
        variants = _owner_list(
            self.client.generations.variants(project_id, generation_id, limit=200),
            "Runtime generation variant read",
        )
        variant_matches = [
            variant for variant in variants
            if isinstance(variant, Mapping)
            and variant.get("generation_id") == generation_id
            and variant.get("object_id") == object_id
            and variant.get("variant_type") == "original"
            and isinstance(variant.get("metadata"), Mapping)
            and variant["metadata"].get("output_port") == "verified_candidate"
            and variant["metadata"].get("group_key") == "main"
            and variant["metadata"].get("variant_key") == "original"
            and variant["metadata"].get("ordinal") == 0
            and variant["metadata"].get("size") == size
        ]
        if len(variants) != 1 or len(variant_matches) != 1:
            raise DeploymentOperationError("published generation does not have one exact H3 variant")
        variant_id = _required_string(variant_matches[0].get("variant_id"), "publication variant_id")
        settlement = {
            "parent_task_id": task_id,
            "parent_run_id": run_id,
            "parent_attempt_id": parent_attempt_id,
            "verify_task_id": verify_task_id,
            "verify_attempt_id": verify_attempt_id,
            "verify_association_id": verify_association_id,
            "finalizer_task_id": finalizer_id,
            "finalizer_run_id": finalizer_run_id,
            "finalizer_attempt_id": finalizer_attempt_id,
            "generation_id": generation_id,
            "variant_id": variant_id,
            "variant_key": "original",
            "association_id": association_id,
            "output_port": "verified_candidate",
            "object_id": object_id,
            "size": size,
            "lifecycle": dict(direct["lifecycle"]),
            "media_type": direct.get("media_type"),
            "filename": direct.get("filename"),
            **_receipt_lineage(parent),
        }
        observe("publication_committed", settlement)
        return settlement

    def _project_tasks(self, project_id: str) -> list[Any]:
        rows: list[Any] = []
        cursor: str | None = None
        for _page in range(50):
            page_rows, cursor = _owner_page(
                self.client.tasks.list(project_id, cursor=cursor, limit=200),
                "Runtime project task read",
            )
            rows.extend(page_rows)
            if cursor is None:
                return rows
        raise DeploymentOperationError("Runtime project task read exceeded the bounded page limit")

    def pullback(self, settlement: Mapping[str, Any], output_path: Path) -> Mapping[str, Any]:
        object_id = str(settlement.get("object_id") or "")
        expected_size = settlement.get("size")
        expected_digest = object_id.removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
            raise DeploymentOperationError("pullback settlement has no canonical object digest")
        if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size <= 0:
            raise DeploymentOperationError("pullback settlement has no valid byte size")
        destination = output_path.expanduser().resolve()
        if not destination.is_absolute():
            raise DeploymentOperationError("pullback output path must be absolute")
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.is_file() and _verified_local_file(
            destination, expected_digest, expected_size, self.decode
        ):
            return {
                "path": str(destination),
                "object_id": object_id,
                "size": expected_size,
                "sha256": expected_digest,
                "decoded": True,
                "reused": True,
            }

        # Runtime read_bytes is the existing whole-object transport. A failed
        # read can be retried against this same immutable object without
        # re-entering H3 or submitting another sampler job.
        payload = self.client.media.read_bytes(object_id)
        if not isinstance(payload, bytes):
            raise DeploymentOperationError("Runtime media read returned no bytes")
        actual_digest = hashlib.sha256(payload).hexdigest()
        if len(payload) != expected_size or actual_digest != expected_digest:
            raise DeploymentOperationError("managed pullback failed size or SHA-256 verification")
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".partial", dir=str(destination.parent)
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self.decode(temporary)
            temporary.replace(destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        if not _verified_local_file(destination, expected_digest, expected_size, self.decode):
            destination.unlink(missing_ok=True)
            raise DeploymentOperationError("local H3 pullback failed post-write verification")
        return {
            "path": str(destination),
            "object_id": object_id,
            "size": expected_size,
            "sha256": expected_digest,
            "decoded": True,
            "reused": False,
        }


def _owner_page(value: Any, label: str) -> tuple[list[Any], str | None]:
    if hasattr(value, "ok") and hasattr(value, "data"):
        if not bool(value.ok):
            raise DeploymentOperationError(f"{label} failed: {getattr(value, 'error', value)}")
        value = value.data
    elif isinstance(value, Mapping) and "ok" in value:
        if value.get("ok") is False:
            raise DeploymentOperationError(f"{label} failed: {value.get('error') or value}")
        value = value.get("data")
    if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], list):
        cursor = value[1]
        if cursor is not None and not isinstance(cursor, str):
            raise DeploymentOperationError(f"{label} returned an invalid cursor")
        return list(value[0]), cursor
    if isinstance(value, list):
        return value, None
    raise DeploymentOperationError(f"{label} returned an invalid page")


def _owner_list(value: Any, label: str) -> list[Any]:
    rows, cursor = _owner_page(value, label)
    if cursor is not None:
        raise DeploymentOperationError(f"{label} unexpectedly requires pagination")
    return rows


def _task_state(task: Mapping[str, Any]) -> str:
    return str(task.get("state") or task.get("status") or "").lower()


def _task_spec(task: Mapping[str, Any]) -> Mapping[str, Any]:
    value = task.get("spec")
    return value if isinstance(value, Mapping) else {}


def _task_outputs(task: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result = task.get("result")
    outputs = result.get("outputs") if isinstance(result, Mapping) else None
    if not isinstance(outputs, list) or any(not isinstance(output, Mapping) for output in outputs):
        raise DeploymentOperationError("settled task result has no valid output manifest")
    return list(outputs)


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeploymentOperationError(f"{label} is missing")
    return value


def _canonical_object_id(value: Any, label: str) -> str:
    result = str(value or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", result):
        raise DeploymentOperationError(f"{label} is not canonical")
    return result


def _positive_size(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DeploymentOperationError(f"{label} is invalid")
    return value


def _delegated_parent_matches(
    value: Any,
    *,
    parent_task_id: str,
    parent_attempt_id: str,
    project_id: str,
) -> bool:
    return isinstance(value, Mapping) and all(
        value.get(key) == expected
        for key, expected in (
            ("parent_task_id", parent_task_id),
            ("parent_attempt_id", parent_attempt_id),
            ("project_id", project_id),
        )
    )


def _is_h3_final_publication(value: Any, project_id: str) -> bool:
    if not isinstance(value, Mapping):
        return False
    effect = value.get("effect")
    payload = effect.get("payload") if isinstance(effect, Mapping) else None
    groups = payload.get("groups") if isinstance(payload, Mapping) else None
    selectors = groups[0].get("selectors") if isinstance(groups, list) and len(groups) == 1 and isinstance(groups[0], Mapping) else None
    return (
        value.get("stage") == "finalize"
        and value.get("verify_stage") == "verify"
        and value.get("verify_output_port") == "verified_candidate"
        and isinstance(effect, Mapping)
        and effect.get("effect_type") == "generation.publish_v1"
        and effect.get("target_id") == project_id
        and isinstance(payload, Mapping)
        and payload.get("generation_type") == "h3_av.publication_finalizer"
        and payload.get("partial_success_policy") == "reject"
        and isinstance(selectors, list)
        and len(selectors) == 1
        and selectors[0].get("output_port") == "verified_candidate"
        and selectors[0].get("variant_key") == "original"
        and selectors[0].get("ordinal") == 0
        and selectors[0].get("required") is True
    )


def _output_matches(
    output: Mapping[str, Any], *, object_id: str, size: int, output_port: str
) -> bool:
    return (
        str(output.get("digest") or output.get("object_id") or "") == object_id
        and output.get("size") == size
        and output.get("output_port", output.get("name")) == output_port
    )


def _require_managed_row(
    row: Mapping[str, Any],
    *,
    task_id: str,
    attempt_id: str,
    project_id: str,
    association_id: str,
    object_id: str,
    output_port: str,
    capability_id: str,
    generation_id: str | None = None,
    require_generation: bool = True,
) -> None:
    lifecycle = row.get("lifecycle")
    producer = row.get("producer")
    provenance = row.get("provenance")
    if any(
        row.get(field) != expected
        for field, expected in (
            ("task_id", task_id),
            ("attempt_id", attempt_id),
            ("project_id", project_id),
            ("association_id", association_id),
            ("object_id", object_id),
            ("output_port", output_port),
        )
    ):
        raise DeploymentOperationError("managed output association has foreign identity")
    if row.get("durability") != "durable" or not isinstance(lifecycle, Mapping):
        raise DeploymentOperationError("managed output association is not durable")
    if lifecycle.get("state") not in {"available", "promoted"}:
        raise DeploymentOperationError("managed output lifecycle is not available")
    if not isinstance(lifecycle.get("version"), int) or lifecycle["version"] < 1:
        raise DeploymentOperationError("managed output lifecycle has no version fence")
    if not isinstance(producer, Mapping) or producer.get("capability_id") != capability_id:
        raise DeploymentOperationError("managed output producer capability is foreign")
    if (
        not isinstance(provenance, Mapping)
        or provenance.get("task_id") != task_id
        or provenance.get("attempt_id") != attempt_id
        or provenance.get("capability_id") != capability_id
    ):
        raise DeploymentOperationError("managed output provenance is foreign")
    if require_generation:
        if row.get("generation_id") != generation_id:
            raise DeploymentOperationError("managed output generation is foreign")
        if (
            row.get("group_key") != "main"
            or row.get("variant_key") != "original"
            or row.get("ordinal") != 0
            or row.get("role") != "result"
            or row.get("selector") != {"group_key": "main", "variant_key": "original"}
        ):
            raise DeploymentOperationError("managed publication selector is foreign")


def _decode_managed_video(path: Path) -> Mapping[str, Any]:
    from astrid.core.media import ffprobe_metadata_strict

    probe = ffprobe_metadata_strict(path)
    if not probe.has_video_stream or not probe.has_audio_stream:
        raise DeploymentOperationError("managed H3 output must decode with video and audio streams")
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise DeploymentOperationError("managed H3 output failed full media decode")
    return {"video": True, "audio": True}


def _verified_local_file(
    path: Path,
    expected_digest: str,
    expected_size: int,
    decode: Callable[[Path], Mapping[str, Any]],
) -> bool:
    try:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != expected_size or digest != expected_digest:
            return False
        decode(path)
        return True
    except (OSError, DeploymentOperationError, ValueError):
        return False


class ConcreteDeploymentOperations:
    """Compose the existing provider, Runtime, and H3 owners.

    The qualification owner is deliberately required. This checkout has no
    safe public RunPod prepare/activate API, so omission fails before claim
    instead of pretending that a provider handle is a qualified worker.
    """

    def __init__(
        self,
        *,
        runtime: AstridRuntimeTaskAdapter,
        claim: RunPodClaimHelperAdapter,
        cleanup: RunPodLifecycleCleanupAdapter,
        qualification: DeploymentQualificationOwner | None,
        settlement: AstridManagedOutputSettlementAdapter,
        activation_control: Callable[[str, Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self.runtime = runtime
        self.claim = claim
        self.cleanup = cleanup
        self.qualification = qualification
        self.settlement = settlement
        self.activation_control = activation_control
        self._activation_id: str | None = None
        self._task_identity: tuple[str, str] | None = None
        self._claim_handle_digest: str | None = None
        self.activation_state = (
            getattr(qualification, "activation_state", "unknown")
            if isinstance(qualification, QualifiedRunPodDeploymentOwner) else "inactive"
        )

    def restore_activation_state(self, state: str) -> None:
        if state not in {"inactive", "unknown"}:
            raise DeploymentOperationError("cleanup receipt has invalid activation state")
        self.activation_state = state

    def get_task(self, task_id: str) -> Mapping[str, Any]:
        return self.runtime.get_task(task_id)

    def assert_claim_eligible(self, task: Mapping[str, Any]) -> None:
        self.runtime.assert_claim_eligible(task)
        if self.qualification is None:
            raise DeploymentOperationError(
                "no concrete RunPod deployment qualification owner is available; "
                "prepare/activate the existing worker through its Runtime owner before claiming"
            )
        preflight = getattr(self.qualification, "assert_claim_eligible", None)
        if callable(preflight):
            preflight(task)
        admission = _runtime_deployment_binding(task).admission_identity
        self._task_identity = (admission.task_id, admission.run_id)

    def claim_pod(self, handle_path: Path) -> Mapping[str, Any]:
        handle = self.claim.claim(handle_path)
        self._claim_handle_digest = _digest(handle)
        return handle

    def prepare_and_qualify(self, task: Mapping[str, Any], claim_handle: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.qualification is None:
            raise DeploymentOperationError("RunPod deployment qualification owner is required")
        try:
            result = _owner_result(
                self.qualification.prepare_and_qualify(task, claim_handle),
                "deployment qualification",
            )
        except BaseException:
            self.activation_state = getattr(self.qualification, "activation_state", "inactive")
            raise
        if isinstance(self.qualification, QualifiedRunPodDeploymentOwner):
            activation_id = result.get("activation_id")
            if not isinstance(activation_id, str) or not activation_id:
                raise DeploymentOperationError("Runtime-qualified deployment returned no activation_id")
            if self.activation_control is None:
                raise DeploymentOperationError("Runtime activation cleanup control is unavailable")
            self._activation_id = activation_id
            self.activation_state = "active"
        return result

    def assert_fresh(self, task, claim_handle, qualification, *, phase: str) -> None:
        target = _runtime_deployment_binding(task).placement.effective_target
        if target.get("pod_id") != claim_handle.get("pod_id"):
            raise DeploymentOperationError("RunPod target changed or does not match the claimed pod")
        for field in ("task_id", "run_id"):
            if field in qualification and qualification[field] != task.get(field if field != "task_id" else "id"):
                raise DeploymentOperationError(f"deployment qualification {field} is stale")
        if self.qualification is None:
            raise DeploymentOperationError("RunPod deployment qualification owner is required")
        self.qualification.assert_fresh(task, claim_handle, qualification, phase=phase)

    def retry_existing_task(self, task_id, *, expected_version, idempotency_key, before_queue):
        return self.runtime.retry_existing_task(
            task_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            before_queue=before_queue,
        )

    def execute_canonical_h3(self, task_id: str, run_id: str) -> Mapping[str, Any]:
        return self.runtime.execute_canonical_h3(task_id, run_id)

    def cleanup_owned_pod(self, claim_handle: Mapping[str, Any], *, task_id: str,
                          run_id: str, activation_id: str | None = None) -> Mapping[str, Any]:
        if any(not isinstance(value, str) or not value.strip() for value in (task_id, run_id)):
            raise DeploymentOperationError("cleanup task/run identity is required")
        if self._task_identity is not None and self._task_identity != (task_id, run_id):
            raise DeploymentOperationError("cleanup belongs to a foreign task/run")
        if isinstance(self.qualification, QualifiedRunPodDeploymentOwner) and (
            self.qualification.task_id, self.qualification.run_id
        ) != (task_id, run_id):
            raise DeploymentOperationError("cleanup belongs to a foreign qualification task/run")
        if self._claim_handle_digest is not None and _digest(claim_handle) != self._claim_handle_digest:
            raise DeploymentOperationError("cleanup received a foreign claim handle")
        if activation_id is not None and self._activation_id is not None and activation_id != self._activation_id:
            raise DeploymentOperationError("cleanup received a foreign activation")
        activation_id = activation_id or self._activation_id
        if activation_id is None and self.activation_state != "inactive":
            raise DeploymentOperationError("Runtime activation state is unknown; pod termination is withheld")
        if activation_id is not None:
            if self.activation_control is None:
                raise DeploymentOperationError("Runtime activation cleanup control is unavailable")
            revoked = _owner_result(
                self.activation_control(task_id, {"action": "revoke", "activation_id": activation_id}),
                "Runtime activation and credential revocation",
            )
            if revoked.get("revoked") is not True:
                raise DeploymentOperationError("Runtime did not revoke remote activation and credential")
        return self.cleanup.cleanup(claim_handle)

    def settle_managed_result(self, task_id: str, run_id: str) -> Mapping[str, Any]:
        return self.settlement.settle(task_id, run_id)

    def pullback_managed_result(self, settlement: Mapping[str, Any], output_path: Path) -> Mapping[str, Any]:
        return self.settlement.pullback(settlement, output_path)


@dataclass(frozen=True)
class DeploymentRequest:
    task_id: str
    run_id: str
    operation_id: str
    receipt_path: Path
    handle_path: Path
    output_path: Path

    def __post_init__(self) -> None:
        for field in ("task_id", "run_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise DeploymentOperationError(f"explicit {field} is required")
        if not self.operation_id.strip():
            raise DeploymentOperationError("operation_id is required")
        for path, label in (
            (self.receipt_path, "receipt_path"),
            (self.handle_path, "handle_path"),
            (self.output_path, "output_path"),
        ):
            if not path.is_absolute():
                raise DeploymentOperationError(f"{label} must be absolute")
        if self.receipt_path == self.handle_path:
            raise DeploymentOperationError("operation receipt and claim handle need separate paths")
        if self.output_path in {self.receipt_path, self.handle_path}:
            raise DeploymentOperationError("managed output needs a distinct local path")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _receipt_lineage(value: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve optional Runtime lineage without inventing missing identity.

    The admitted execution request is already Runtime-normalized. Canonical
    JSON hashing records those bytes without adding admission defaults here.
    """
    lineage: dict[str, Any] = {}
    execution_request = value.get("execution_request")
    if isinstance(execution_request, Mapping):
        lineage["execution_request_digest"] = _digest(execution_request)
    elif isinstance(value.get("execution_request_digest"), str):
        lineage["execution_request_digest"] = value["execution_request_digest"]
    binding = value.get("execution_binding")
    target = binding.get("effective_target") if isinstance(binding, Mapping) else None
    if target is None:
        target = value.get("effective_target")
    if isinstance(target, Mapping):
        lineage["effective_target"] = dict(target)
    attempt_id = value.get("attempt_id")
    if isinstance(attempt_id, str) and attempt_id:
        lineage["attempt_id"] = attempt_id
    return lineage


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DeploymentOperationError(f"{label} owner returned no object")
    return value


def _task_version(task: Mapping[str, Any]) -> int:
    version = task.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise DeploymentOperationError("canonical task has no valid version fence")
    return version


def _task_binding(task: Mapping[str, Any], *, task_id: str, run_id: str) -> dict[str, Any]:
    """Fields whose change means the admitted task is no longer the same task."""
    binding = _runtime_deployment_binding(task).as_dict()
    if binding["admission"]["task_id"] != task_id or binding["admission"]["run_id"] != run_id:
        raise DeploymentOperationError("Runtime returned a different requested task or run")
    return binding


def _runtime_deployment_binding(task: Mapping[str, Any]):
    """Read the Runtime-owned binding without adding an Astrid identity."""
    try:
        from runtime_protocol.remote_worker_deployment import deployment_binding_from_task
        return deployment_binding_from_task(task)
    except (ImportError, TypeError, ValueError) as exc:
        raise DeploymentOperationError(f"Runtime deployment binding rejected: {exc}") from exc


def _claim_pod_id(handle: Mapping[str, Any]) -> str:
    if handle.get("schema_version") != "astrid.runpod.claim.v1":
        raise DeploymentOperationError("claim helper did not return a verified claim-v1 handle")
    pod_id = handle.get("pod_id")
    if not isinstance(pod_id, str) or not pod_id.strip():
        raise DeploymentOperationError("verified claim handle has no exact pod_id")
    return pod_id


def run_existing_h3_task(
    request: DeploymentRequest,
    operations: DeploymentOperations,
) -> Mapping[str, Any]:
    """Qualify, retry, and execute the same task; clean only the owned pod on failure."""
    receipt: dict[str, Any] = {
        "schema_version": "astrid.runpod.deployment-operation.v1",
        "operation_id": request.operation_id,
        "task_id": request.task_id,
        "run_id": request.run_id,
        "phase": "preflight",
        "started_at": _now(),
        "status": "running",
        "helper_task_admission": False,
    }
    claim_handle: Mapping[str, Any] | None = None

    def save() -> None:
        _atomic_json(request.receipt_path, receipt)

    save()
    try:
        task = _mapping(operations.get_task(request.task_id), "Runtime task lookup")
        binding = _task_binding(task, task_id=request.task_id, run_id=request.run_id)
        version = _task_version(task)
        operations.assert_claim_eligible(task)
        receipt.update({
            "task_binding_digest": _digest(binding),
            "task_version_before_claim": version,
            "phase": "claiming",
            **_receipt_lineage(task),
            "effective_target": dict(binding["placement"]["effective_target"]),
        })
        save()

        claim_handle = _mapping(operations.claim_pod(request.handle_path), "claim helper")
        pod_id = _claim_pod_id(claim_handle)
        receipt.update({
            "pod_id": pod_id,
            "claim_handle_digest": _digest(claim_handle),
            "phase": "preparing",
            "activation_state": getattr(operations, "activation_state", "unknown"),
        })
        save()

        # A crash inside qualification may occur after credential issuance.
        # Persist uncertainty before entering that call; the in-process owner
        # narrows it back to inactive only when no activation was possible.
        receipt["activation_state"] = "unknown"
        save()
        qualification = _mapping(
            operations.prepare_and_qualify(task, claim_handle), "deployment qualification"
        )
        receipt["activation_state"] = getattr(operations, "activation_state", "unknown")
        activation_id = qualification.get("activation_id")
        if activation_id is not None:
            if not isinstance(activation_id, str) or not activation_id:
                raise DeploymentOperationError("qualified activation_id is invalid")
            receipt["activation_id"] = activation_id
            save()
        current = _mapping(operations.get_task(request.task_id), "post-qualification Runtime task check")
        current_binding = _task_binding(current, task_id=request.task_id, run_id=request.run_id)
        if (any(current_binding[key] != binding[key] for key in ("admission", "capability", "inputs"))
                or current_binding["placement"]["original_target"] != binding["placement"]["original_target"]
                or _task_version(current) != version):
            raise DeploymentOperationError("canonical admission or placement changed during qualification")
        binding = current_binding
        receipt.update(_receipt_lineage(current))
        receipt["effective_target"] = dict(binding["placement"]["effective_target"])
        receipt["qualification_digest"] = _digest(qualification)
        receipt["phase"] = "checking_before_retry"
        save()
        operations.assert_fresh(current, claim_handle, qualification, phase="before_retry")

        current = _mapping(operations.get_task(request.task_id), "Runtime task recheck")
        if _task_binding(current, task_id=request.task_id, run_id=request.run_id) != binding or _task_version(current) != version:
            raise DeploymentOperationError("canonical task changed during deployment preparation")

        def before_queue() -> None:
            fresh_task = _mapping(operations.get_task(request.task_id), "pre-queue Runtime task check")
            if _task_binding(fresh_task, task_id=request.task_id, run_id=request.run_id) != binding:
                raise DeploymentOperationError("canonical task identity changed before H3 queue")
            operations.assert_fresh(
                fresh_task, claim_handle, qualification, phase="before_queue"
            )

        receipt["phase"] = "canonical_retry"
        save()
        retry_result = _mapping(
            operations.retry_existing_task(
                request.task_id,
                expected_version=version,
                idempotency_key=request.operation_id,
                before_queue=before_queue,
            ),
            "canonical task retry",
        )
        receipt["retry_result_digest"] = _digest(retry_result)
        receipt.update(_receipt_lineage(retry_result))
        receipt["phase"] = "canonical_h3"
        save()

        result = _mapping(
            operations.execute_canonical_h3(
                request.task_id,
                request.run_id,
            ),
            "canonical H3 path",
        )
        receipt["canonical_result_digest"] = _digest(result)
        receipt.update(_receipt_lineage(result))
        receipt["phase"] = "settlement_pending"
        receipt["status"] = "settlement_pending"
        save()
        try:
            settlement = _mapping(
                operations.settle_managed_result(request.task_id, request.run_id),
                "managed result settlement",
            )
            receipt["settlement"] = dict(settlement)
            receipt.update(_receipt_lineage(settlement))
            receipt["phase"] = "pullback_pending"
            receipt["status"] = "pullback_pending"
            save()
            pullback = _mapping(
                operations.pullback_managed_result(settlement, request.output_path),
                "managed result pullback",
            )
            receipt["pullback"] = dict(pullback)
            receipt["phase"] = "cleanup"
            save()
        except BaseException as phase_exc:
            receipt["status"] = (
                "settlement_pending" if receipt["phase"] == "settlement_pending" else "pullback_pending"
            )
            receipt["failure"] = {"type": type(phase_exc).__name__, "message": str(phase_exc)}
            save()
            # Keep the exact pod and attached backup volume until managed bytes
            # have been verified locally; this phase can resume without H3.
            raise

        try:
            cleanup = _mapping(
                operations.cleanup_owned_pod(claim_handle, task_id=request.task_id,
                                             run_id=request.run_id, activation_id=activation_id)
                if activation_id else operations.cleanup_owned_pod(
                    claim_handle, task_id=request.task_id, run_id=request.run_id),
                "owned pod cleanup",
            )
        except BaseException as cleanup_exc:
            receipt["status"] = "cleanup_pending"
            receipt["phase"] = "cleanup_pending"
            receipt["cleanup_status"] = "pending"
            receipt["cleanup"] = {
                "status": "cleanup_pending",
                "pod_id": claim_handle.get("pod_id"),
                "backup_volume_id": claim_handle.get("network_volume_id"),
                "backup_volume_preserved": True,
                "type": type(cleanup_exc).__name__,
                "message": str(cleanup_exc),
            }
            save()
            return dict(receipt)
        receipt["cleanup"] = dict(cleanup)
        receipt["cleanup_status"] = "complete"
        receipt["phase"] = "complete"
        receipt["status"] = "completed"
        receipt["completed_at"] = _now()
        save()
        return dict(receipt)
    except BaseException as exc:
        if receipt.get("phase") in {"settlement_pending", "pullback_pending"}:
            # The inner phase handler persisted resumable state and intentionally
            # retained the exact owned pod and backup volume.
            raise
        receipt["status"] = "failed"
        receipt["activation_state"] = getattr(operations, "activation_state", receipt.get("activation_state", "unknown"))
        receipt["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        receipt["phase"] = "cleanup" if claim_handle is not None else "failed"
        try:
            save()
        except OSError:
            # Receipt storage must not prevent exact-pod cleanup.
            pass
        if claim_handle is not None:
            try:
                cleanup = _mapping(
                    operations.cleanup_owned_pod(claim_handle, task_id=request.task_id,
                                                 run_id=request.run_id, activation_id=receipt["activation_id"])
                    if receipt.get("activation_id") else operations.cleanup_owned_pod(
                        claim_handle, task_id=request.task_id, run_id=request.run_id),
                    "owned pod cleanup",
                )
                receipt["cleanup"] = dict(cleanup)
                receipt["phase"] = "failed_cleaned"
                receipt["cleanup_status"] = "complete"
            except BaseException as cleanup_exc:
                receipt["cleanup"] = {
                    "status": "cleanup_pending",
                    "type": type(cleanup_exc).__name__,
                    "message": str(cleanup_exc),
                }
                receipt["phase"] = "cleanup_pending"
                receipt["cleanup_status"] = "pending"
            try:
                save()
            except OSError:
                # The raised operation error remains authoritative; cleanup was
                # still attempted and the provider handle remains in its own file.
                pass
        raise


def resume_h3_settlement(
    request: DeploymentRequest,
    operations: DeploymentOperations,
) -> Mapping[str, Any]:
    """Resume settlement, pullback, or cleanup without retrying the task."""
    try:
        receipt = json.loads(request.receipt_path.read_text(encoding="utf-8"))
        claim_handle = json.loads(request.handle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentOperationError("T11 resume requires the existing receipt and claim handle") from exc
    if not isinstance(receipt, dict) or not isinstance(claim_handle, dict):
        raise DeploymentOperationError("T11 resume evidence is malformed")
    if (receipt.get("task_id"), receipt.get("run_id"), receipt.get("operation_id")) != (
        request.task_id, request.run_id, request.operation_id
    ):
        raise DeploymentOperationError("T11 resume evidence does not match the pinned operation")
    if (receipt.get("status") not in {"settlement_pending", "pullback_pending", "cleanup_pending"}
            and not (receipt.get("status") == "failed" and receipt.get("phase") == "cleanup_pending")):
        raise DeploymentOperationError("existing operation has no resumable T11 phase")
    if _claim_pod_id(claim_handle) != receipt.get("pod_id"):
        raise DeploymentOperationError("T11 resume claim handle does not match receipt ownership")
    if receipt.get("claim_handle_digest") != _digest(claim_handle):
        raise DeploymentOperationError("T11 resume claim handle digest does not match receipt ownership")

    def save() -> None:
        _atomic_json(request.receipt_path, receipt)

    restore = getattr(operations, "restore_activation_state", None)
    if callable(restore) and not receipt.get("activation_id"):
        restore(receipt.get("activation_state", "unknown"))

    needs_pullback = receipt.get("phase") != "cleanup_pending"
    if needs_pullback:
        try:
            settlement = receipt.get("settlement")
            if not isinstance(settlement, Mapping):
                settlement = _mapping(
                    operations.settle_managed_result(request.task_id, request.run_id),
                    "managed result settlement",
                )
                receipt["settlement"] = dict(settlement)
            receipt.update(_receipt_lineage(settlement))
            receipt["phase"] = "pullback_pending"
            receipt["status"] = "pullback_pending"
            save()
            pullback = _mapping(
                operations.pullback_managed_result(settlement, request.output_path),
                "managed result pullback",
            )
            receipt["pullback"] = dict(pullback)
        except BaseException as exc:
            receipt["phase"] = "pullback_pending"
            receipt["status"] = "pullback_pending"
            receipt["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            save()
            raise

    try:
        cleanup = _mapping(
            operations.cleanup_owned_pod(claim_handle, task_id=request.task_id,
                                         run_id=request.run_id, activation_id=receipt["activation_id"])
            if receipt.get("activation_id") else operations.cleanup_owned_pod(
                claim_handle, task_id=request.task_id, run_id=request.run_id),
            "owned pod cleanup",
        )
    except BaseException as exc:
        receipt["phase"] = "cleanup_pending"
        receipt["status"] = "cleanup_pending"
        receipt["cleanup_status"] = "pending"
        receipt["cleanup"] = {
            "status": "cleanup_pending",
            "pod_id": claim_handle.get("pod_id"),
            "backup_volume_id": claim_handle.get("network_volume_id"),
            "backup_volume_preserved": True,
            "type": type(exc).__name__,
            "message": str(exc),
        }
        save()
        return dict(receipt)
    receipt["cleanup"] = dict(cleanup)
    receipt["cleanup_status"] = "complete"
    failed_generation = "canonical_result_digest" not in receipt and "failure" in receipt
    receipt["phase"] = "failed_cleaned" if failed_generation else "complete"
    receipt["status"] = "failed" if failed_generation else "completed"
    receipt["completed_at"] = _now()
    if not failed_generation:
        receipt.pop("failure", None)
    save()
    return dict(receipt)


__all__ = [
    "CANONICAL_RUN_ID",
    "CANONICAL_TASK_ID",
    "DeploymentOperationError",
    "DeploymentQualificationOwner",
    "DeploymentOperations",
    "DeploymentRequest",
    "AstridRuntimeTaskAdapter",
    "RunPodClaimHelperAdapter",
    "RunPodLifecycleCleanupAdapter",
    "AstridManagedOutputSettlementAdapter",
    "ConcreteDeploymentOperations",
    "run_existing_h3_task",
    "resume_h3_settlement",
]
