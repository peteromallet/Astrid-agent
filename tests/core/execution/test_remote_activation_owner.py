from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrid.core.execution.remote_activation_owner import (
    RemoteActivationUncertain,
    RuntimeRemoteActivationOwner,
)


TARGET = {"kind": "runpod", "pod_id": "pod-a", "provider_account_ref": "account-a"}
OWNER = {"actor": "owner", "scopes": ["admin"]}
QUALIFICATION = {
    "activation_id": "activation-a", "task_id": "task-a", "run_id": "run-a",
    "effective_target": TARGET, "runtime_session_id": "session-a", "runtime_epoch": 3,
}


class FakeRuntime:
    def __init__(self):
        self.epoch = 3
        self.session = "session-a"
        self.state = "queued"
        self.target = TARGET
        self.original_target = TARGET
        self.binding_extra = {}
        self.version = 1
        self.calls = []
        self.lose_once = None

    def health(self):
        return {"runtime_instance_id": "instance-a", "runtime_session_id": self.session,
                "runtime_epoch": self.epoch}

    def get_task(self, task_id):
        assert task_id == "task-a"
        return SimpleNamespace(task_id=task_id, run_id="run-a", state=self.state,
                               version=self.version,
                               execution_request={"target": self.original_target},
                               execution_binding={"effective_target": self.target, **self.binding_extra})

    def _reply(self, action, value):
        self.calls.append(action)
        if self.lose_once == action:
            self.lose_once = None
            raise TimeoutError("response lost after commit")
        return value

    def control_remote_credential(self, task_id, control):
        assert task_id == "task-a"
        action = control["action"]
        return self._reply(action, {
            "provision": {"credential_actor": "astrid-pack-host", "credential_file": "/runtime/token"},
            "enable": {"enabled": True}, "verify": {"fresh": True},
            "revoke": {"revoked": True},
        }[action])

    def record_remote_activation(self, task_id, qualification):
        assert task_id == "task-a"
        return self._reply("record", dict(qualification))

    def revoke_remote_activation(self, task_id, activation_id):
        self.calls.append("direct-revoke")


def _owner(runtime):
    return RuntimeRemoteActivationOwner(
        runtime, runtime_instance_id="instance-a",
        runtime_session_id="session-a", runtime_epoch=3,
    )


def test_lost_record_response_replays_exact_generation_only():
    runtime = FakeRuntime()
    owner = _owner(runtime)
    runtime.lose_once = "record"
    assert owner.record_remote_activation("task-a", QUALIFICATION, identity=OWNER) == QUALIFICATION
    assert runtime.calls == ["record", "record"]


def test_lost_provision_response_revokes_exact_generation_without_rotating_twice():
    runtime = FakeRuntime()
    owner = _owner(runtime)
    runtime.lose_once = "provision"
    with pytest.raises(RemoteActivationUncertain, match="provision response lost"):
        owner.control_remote_credential("task-a", {
            "action": "provision", "qualification": QUALIFICATION, "placement": {},
        })
    assert runtime.calls == ["provision", "revoke"]
    owner.revoke_remote_activation("task-a", "activation-a", identity=OWNER)
    assert runtime.calls == ["provision", "revoke"]


def test_lost_enable_response_retries_same_generation_and_confirmed_revoke_fences():
    runtime = FakeRuntime()
    owner = _owner(runtime)
    runtime.lose_once = "enable"
    assert owner.control_remote_credential("task-a", {
        "action": "enable", "activation_id": "activation-a",
    }) == {"enabled": True}
    assert owner.control_remote_credential("task-a", {
        "action": "revoke", "activation_id": "activation-a",
    }) == {"revoked": True}
    owner.revoke_remote_activation("task-a", "activation-a", identity=OWNER)
    assert runtime.calls == ["enable", "enable", "revoke"]


def test_direct_fence_uses_resident_credential_and_activation_revoke():
    runtime = FakeRuntime()
    owner = _owner(runtime)
    runtime.lose_once = "revoke"
    owner.revoke_remote_activation("task-a", "activation-a", identity=OWNER)
    assert runtime.calls == ["revoke", "revoke"]


@pytest.mark.parametrize("change", ["target", "cancel", "running", "epoch", "session"])
def test_foreign_target_live_attempt_cancel_and_runtime_restart_block_authority(change):
    runtime = FakeRuntime()
    owner = _owner(runtime)
    if change == "target":
        runtime.target = {**TARGET, "pod_id": "pod-b"}
    elif change == "cancel":
        runtime.state = "cancelled"
    elif change == "running":
        runtime.state = "running"
    elif change == "epoch":
        runtime.epoch += 1
    else:
        runtime.session = "session-b"
    with pytest.raises(RemoteActivationUncertain):
        owner.record_remote_activation("task-a", QUALIFICATION, identity=OWNER)
    assert runtime.calls == []


def test_lost_record_response_does_not_replay_after_runtime_restart():
    runtime = FakeRuntime()
    owner = _owner(runtime)

    def lost_then_restart(task_id, qualification):
        runtime.calls.append("record")
        runtime.epoch += 1
        raise TimeoutError("response lost")

    runtime.record_remote_activation = lost_then_restart
    with pytest.raises(RemoteActivationUncertain, match="Runtime instance, session or epoch changed"):
        owner.record_remote_activation("task-a", QUALIFICATION, identity=OWNER)
    assert runtime.calls == ["record"]


def test_non_owner_cannot_record_or_revoke():
    runtime = FakeRuntime()
    owner = _owner(runtime)
    with pytest.raises(ValueError, match="owner identity"):
        owner.record_remote_activation("task-a", QUALIFICATION, identity={"actor": "worker"})
    with pytest.raises(ValueError, match="owner identity"):
        owner.revoke_remote_activation("task-a", "activation-a", identity={"actor": "worker"})
    assert runtime.calls == []


def test_machine_acceptance_uses_existing_resident_rpc_and_exact_retry():
    runtime = FakeRuntime()
    runtime.target = runtime.original_target = {"kind": "machine", "id": "machine-a"}
    qualification = {**QUALIFICATION, "effective_target": runtime.target}
    owner = _owner(runtime)
    runtime.lose_once = "record"
    assert owner.record_remote_activation("task-a", qualification, identity=OWNER) == qualification
    assert runtime.calls == ["record", "record"]


@pytest.mark.parametrize("target,original", [
    ({"kind": "machine", "id": "foreign"}, {"kind": "machine", "id": "machine-a"}),
    ({"kind": "machine", "id": ""}, {"kind": "machine", "id": ""}),
    ({"kind": "machine", "id": "machine-a"}, TARGET),
    ({"kind": "runpod", "pod_id": "pod-a"}, TARGET),
])
def test_inexact_or_mixed_machine_placement_never_reaches_resident(target, original):
    runtime = FakeRuntime()
    runtime.target, runtime.original_target = target, original
    owner = _owner(runtime)
    with pytest.raises(RemoteActivationUncertain):
        owner.record_remote_activation("task-a", {**QUALIFICATION, "effective_target": target}, identity=OWNER)
    assert runtime.calls == []


@pytest.mark.parametrize("state", ["failed", "cancelled"])
@pytest.mark.parametrize("change", [None, "decision", "version", "task_version", "evidence", "incarnation", "binding"])
def test_only_current_recovered_terminal_receipt_reaches_resident(state, change):
    runtime = FakeRuntime()
    runtime.state, runtime.version = state, 2
    runtime.target = {**TARGET, "pod_id": "replacement"}
    replacement = {"target": runtime.target, "verified": True,
                   "evidence_digest": "sha256:" + "a" * 64, "executor_incarnation": "replacement-host"}
    runtime.binding_extra = {
        "status": "prepared", "placement_version": 1, "recovery_decision_digest": "sha256:" + "b" * 64,
        "placement_recovery": {"task_id": "task-a", "run_id": "run-a", "task_version": 2,
                               "placement_version": 1, "decision_digest": "sha256:" + "b" * 64,
                               "original_target": TARGET, "replacement_target": runtime.target,
                               "qualification": replacement},
    }
    qualification = {**QUALIFICATION, "effective_target": runtime.target,
                     "evidence_digest": replacement["evidence_digest"],
                     "executor_incarnation": replacement["executor_incarnation"],
                     "authorized_child_lineage": {"placement_version": 1}}
    if change == "decision":
        runtime.binding_extra["placement_recovery"]["decision_digest"] = "sha256:" + "c" * 64
    elif change == "version":
        qualification["authorized_child_lineage"]["placement_version"] = 0
    elif change == "task_version":
        runtime.version = 3
    elif change == "evidence":
        qualification["evidence_digest"] = "sha256:" + "c" * 64
    elif change == "incarnation":
        qualification["executor_incarnation"] = "foreign"
    elif change == "binding":
        runtime.binding_extra["status"] = "claimed"
    owner = _owner(runtime)
    if change is None:
        assert owner.record_remote_activation("task-a", qualification, identity=OWNER) == qualification
        assert runtime.calls == ["record"]
    else:
        with pytest.raises(RemoteActivationUncertain):
            owner.record_remote_activation("task-a", qualification, identity=OWNER)
        assert runtime.calls == []
