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


def _field(value: Any, name: str) -> Any:
    """Read the generated SDK's JSON mappings and narrow test/service objects."""
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


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

    def _assert_task(self, task_id: str, qualification: Mapping[str, Any], *,
                     allow_claimed_for_exact_commit: bool = False,
                     allowed_states: set[str] | None = None) -> None:
        self._assert_runtime()
        task = self.client.get_task(task_id)
        binding = _field(task, "execution_binding")
        request = _field(task, "execution_request")
        if not isinstance(binding, Mapping) or not isinstance(request, Mapping):
            raise RemoteActivationUncertain("Runtime task has no exact execution binding")
        target = binding.get("effective_target", binding.get("resolved_target"))
        original = request.get("target")
        if (not isinstance(target, Mapping) or target.get("kind") != "runpod"
                or not target.get("pod_id") or not target.get("provider_account_ref")
                or not isinstance(original, Mapping) or original.get("kind") != "runpod"):
            raise RemoteActivationUncertain("task has no exact RunPod placement")
        permitted_states = allowed_states or {"queued"}
        if allow_claimed_for_exact_commit:
            # A response-lost exact commit may be retried after the worker has
            # claimed or even settled this task. Runtime decides whether the
            # exact generation already exists; cancelled tasks remain fenced.
            permitted_states.update({"running", "succeeded", "failed"})
        if (_field(task, "task_id") != task_id or _field(task, "run_id") != qualification.get("run_id")
                or _field(task, "state") not in permitted_states
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
                    except Exception:  # noqa: BLE001 - cleanup must fail closed without retrying provision
                        pass
                raise RemoteActivationUncertain(
                    "credential provision response lost; reconcile activation generation"
                ) from exc
        if action in {"begin-drain", "finish-drain"}:
            qualification = control.get("qualification")
            if not isinstance(qualification, Mapping):
                raise ValueError("exact activation qualification is required for drain")
            # Drain is task-scoped, like activation. Unlike provision, it may
            # legitimately start after the task has claimed or settled; Runtime
            # itself decides whether all attempts, reservations and bindings are
            # quiescent before it revokes the exact generation.
            self._assert_task(
                task_id,
                qualification,
                allowed_states={
                    "queued", "running", "cancel_requested", "succeeded",
                    "completed", "failed", "cancelled",
                },
            )
            if set(control) != {"action", "qualification"}:
                raise ValueError("remote drain requires only its exact qualification")
            try:
                return self.client.control_remote_credential(task_id, control)
            except (OSError, TimeoutError, URLError) as exc:
                # Both Runtime drain commands are exact-generation idempotent.
                # A retry can only observe or advance this same drain marker;
                # it cannot provision or rotate a bearer.
                self._assert_task(
                    task_id,
                    qualification,
                    allowed_states={
                        "queued", "running", "cancel_requested", "succeeded",
                        "completed", "failed", "cancelled",
                    },
                )
                try:
                    return self.client.control_remote_credential(task_id, control)
                except Exception as retry_error:  # noqa: BLE001 - preserve uncertain authority boundary
                    raise RemoteActivationUncertain(
                        f"{action} response is unresolved for the exact activation generation"
                    ) from retry_error
        if action == "revoke-uncommitted":
            qualification = control.get("qualification")
            placement = control.get("placement")
            if (not isinstance(qualification, Mapping)
                    or not isinstance(placement, Mapping)
                    or set(control) != {"action", "qualification", "placement"}):
                raise ValueError("uncommitted revoke requires exact qualification and placement")
            expected_placement = {
                "actual": qualification.get("effective_target"),
                "verification": {
                    "method": "credential_claim", "verified": True,
                    "evidence_digest": qualification.get("evidence_digest"),
                },
                "executor_incarnation": qualification.get("executor_incarnation"),
            }
            if (not isinstance(qualification.get("activation_id"), str)
                    or not qualification["activation_id"]
                    or placement != expected_placement):
                raise ValueError("uncommitted revoke differs from its exact activation generation")
            self._assert_task(
                task_id, qualification,
                allowed_states={"queued", "running", "cancel_requested", "succeeded",
                                "completed", "failed", "cancelled", "canceled"},
            )
            try:
                result = self.client.control_remote_credential(task_id, control)
            except (OSError, TimeoutError, URLError):
                # Runtime cleanup is idempotent for this exact generation.
                self._assert_task(
                    task_id, qualification,
                    allowed_states={"queued", "running", "cancel_requested", "succeeded",
                                    "completed", "failed", "cancelled", "canceled"},
                )
                try:
                    result = self.client.control_remote_credential(task_id, control)
                except Exception as retry_error:  # noqa: BLE001 - no replacement generation is safe
                    raise RemoteActivationUncertain(
                        "uncommitted credential revocation is unresolved for this exact generation"
                    ) from retry_error
            if result != {"revoked": True}:
                raise RemoteActivationUncertain("Runtime did not confirm exact uncommitted credential revocation")
            return result
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

    def begin_remote_drain(self, task_id: str,
                           qualification: Mapping[str, Any]) -> Mapping[str, Any]:
        """Fence new claims for this exact task activation generation."""
        return self.control_remote_credential(task_id, {
            "action": "begin-drain", "qualification": dict(qualification),
        })

    def finish_remote_drain(self, task_id: str,
                            qualification: Mapping[str, Any]) -> Mapping[str, Any]:
        """Revoke only after Runtime proves this generation has settled."""
        return self.control_remote_credential(task_id, {
            "action": "finish-drain", "qualification": dict(qualification),
        })

    def record_remote_activation(self, task_id: str,
                                 qualification: Mapping[str, Any], *,
                                 identity: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        if identity != {"actor": "owner", "scopes": ["admin"]}:
            raise ValueError("Runtime owner identity is required")
        # Runtime's exact qualification write is idempotent. Its response may
        # be lost after the host becomes claimable and starts this same task;
        # permit that exact commit replay while retaining all task, placement,
        # and Runtime identity checks above. Credential provisioning remains
        # queued-only.
        self._assert_task(task_id, qualification, allow_claimed_for_exact_commit=True)
        try:
            result = self.client.record_remote_activation(task_id, qualification)
        except (OSError, TimeoutError, URLError):
            # Runtime commits the exact qualification idempotently. A changed
            # session/epoch or task is never retried under this generation.
            self._assert_task(task_id, qualification, allow_claimed_for_exact_commit=True)
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
