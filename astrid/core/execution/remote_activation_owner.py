"""Pinned owner-side HTTP bridge for Runtime's qualified remote launcher.

The Runtime launcher supplies independent observation and private grant/ack.
This bridge only forwards its authority calls to the resident Runtime. It never
creates a task or treats an uncertain credential provision as safe to replay.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.error import URLError


class RemoteActivationUncertain(RuntimeError):
    """An authority response was lost and the generation needs reconciliation."""


class RuntimeRemoteActivationOwner:
    """RuntimeService-shaped adapter for QualifiedRemoteWorkerLauncher.

    Construct from the owner-authenticated generated client and a health witness
    captured for the selected Runtime. The launcher's credential_control argument
    should be this object's ``control_remote_credential`` method.
    """

    def __init__(self, client: Any, *, runtime_instance_id: str,
                 runtime_session_id: str, runtime_epoch: int) -> None:
        if not runtime_instance_id or not runtime_session_id or runtime_epoch <= 0:
            raise ValueError("exact Runtime instance, session and epoch are required")
        self.client = client
        self.runtime_instance_id = runtime_instance_id
        self.runtime_session_id = runtime_session_id
        self.runtime_epoch = runtime_epoch
        self.store = self  # The Runtime launcher's narrow epoch-read interface.
        self._confirmed_revocations: set[tuple[str, str]] = set()
        self._assert_runtime()

    def _assert_runtime(self) -> None:
        health = self.client.health()
        if (health["runtime_instance_id"] != self.runtime_instance_id
                or health["runtime_session_id"] != self.runtime_session_id
                or health["runtime_epoch"] != self.runtime_epoch):
            raise RemoteActivationUncertain("Runtime instance, session or epoch changed")

    def _current_runtime_epoch(self) -> int:
        self._assert_runtime()
        return self.runtime_epoch

    def _assert_task(self, task_id: str, qualification: Mapping[str, Any]) -> None:
        self._assert_runtime()
        task = self.client.get_task(task_id)
        binding = task.execution_binding
        request = task.execution_request
        if not isinstance(binding, Mapping) or not isinstance(request, Mapping):
            raise RemoteActivationUncertain("Runtime task has no exact execution binding")
        target = binding.get("effective_target", binding.get("resolved_target"))
        original = request.get("target")
        if (not isinstance(target, Mapping) or target.get("kind") != "runpod"
                or not target.get("pod_id") or not target.get("provider_account_ref")
                or not isinstance(original, Mapping) or original.get("kind") != "runpod"):
            raise RemoteActivationUncertain("task has no exact RunPod placement")
        if (task.task_id != task_id or task.run_id != qualification.get("run_id")
                or task.state != "queued"
                or qualification.get("task_id") != task_id
                or qualification.get("effective_target") != target
                or qualification.get("runtime_session_id") != self.runtime_session_id
                or qualification.get("runtime_epoch") != self.runtime_epoch):
            raise RemoteActivationUncertain("activation is foreign to current task or placement")

    def control_remote_credential(self, task_id: str,
                                  control: Mapping[str, Any]) -> Mapping[str, Any]:
        action = control.get("action")
        if action == "provision":
            qualification = control.get("qualification")
            if not isinstance(qualification, Mapping):
                raise ValueError("qualification is required")
            self._assert_task(task_id, qualification)
            # Provision rotates the actor token. A lost response cannot be
            # retried: it may have committed and its disabled token may already
            # exist. The launcher fails closed and retains the generation ID.
            try:
                return self.client.control_remote_credential(task_id, control)
            except (OSError, TimeoutError, URLError) as exc:
                activation_id = qualification.get("activation_id")
                if isinstance(activation_id, str) and activation_id:
                    try:
                        # If provision committed, revoke its still-disabled
                        # generation by exact ID. A failed revoke remains
                        # unknown; it must not trigger a new provision.
                        self.control_remote_credential(task_id, {
                            "action": "revoke", "activation_id": activation_id,
                        })
                    except Exception:
                        pass
                raise RemoteActivationUncertain(
                    "credential provision response lost; reconcile activation generation"
                ) from exc
        if action not in {"enable", "verify", "revoke"}:
            raise ValueError("invalid remote credential action")
        activation_id = control.get("activation_id")
        if not isinstance(activation_id, str) or not activation_id:
            raise ValueError("activation_id is required")
        self._assert_runtime()
        if action == "revoke" and (task_id, activation_id) in self._confirmed_revocations:
            return {"revoked": True}
        try:
            result = self.client.control_remote_credential(task_id, control)
        except (OSError, TimeoutError, URLError):
            # Runtime enable/revoke for the same generation are idempotent.
            # Verify is a read and may be repeated too. Never retry provision.
            self._assert_runtime()
            result = self.client.control_remote_credential(task_id, control)
        if action == "revoke" and result == {"revoked": True}:
            self._confirmed_revocations.add((task_id, activation_id))
        return result

    def record_remote_activation(self, task_id: str,
                                 qualification: Mapping[str, Any], *,
                                 identity: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        if identity != {"actor": "owner", "scopes": ["admin"]}:
            raise ValueError("Runtime owner identity is required")
        self._assert_task(task_id, qualification)
        try:
            result = self.client.record_remote_activation(task_id, qualification)
        except (OSError, TimeoutError, URLError):
            # Runtime commits the exact qualification idempotently. A changed
            # session/epoch or task is never retried under this generation.
            self._assert_task(task_id, qualification)
            result = self.client.record_remote_activation(task_id, qualification)
        if result != qualification:
            raise RemoteActivationUncertain("Runtime returned a different activation generation")
        return result

    def revoke_remote_activation(self, task_id: str, activation_id: str, *,
                                 identity: Mapping[str, Any] | None = None) -> None:
        if identity != {"actor": "owner", "scopes": ["admin"]}:
            raise ValueError("Runtime owner identity is required")
        if (task_id, activation_id) in self._confirmed_revocations:
            return  # Credential control already revoked Runtime's activation.
        # The direct event-only revoke leaves a live bearer until a separate
        # credential call. Runtime's credential-control revoke fences both in
        # one resident operation and is idempotent after a lost response.
        result = self.control_remote_credential(task_id, {
            "action": "revoke", "activation_id": activation_id,
        })
        if result != {"revoked": True}:
            raise RemoteActivationUncertain("Runtime did not confirm activation revocation")
