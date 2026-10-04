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
    "evidence_digest": "sha256:" + "e" * 64,
    "executor_incarnation": "incarnation-a",
}
PLACEMENT = {
    "actual": TARGET,
    "verification": {"method": "credential_claim", "verified": True,
                     "evidence_digest": QUALIFICATION["evidence_digest"]},
    "executor_incarnation": QUALIFICATION["executor_incarnation"],
}


class FakeRuntime:
    def __init__(self):
        self.epoch = 3
        self.session = "session-a"
        self.state = "queued"
        self.target = TARGET
        self.calls = []
        self.lose_once = None

    def health(self):
        return {"runtime_instance_id": "instance-a", "runtime_session_id": self.session,
                "runtime_epoch": self.epoch}

    def get_task(self, task_id):
        assert task_id == "task-a"
        return SimpleNamespace(task_id=task_id, run_id="run-a", state=self.state,
                               execution_request={"target": TARGET},
                               execution_binding={"effective_target": self.target})

    def _reply(self, action, value):
        self.calls.append(action)
        if self.lose_once == action:
            self.lose_once = None
            raise TimeoutError("response lost after commit")
        return value

    def control_remote_credential(self, task_id, control):
        assert task_id == "task-a"
        action = control["action"]
        if action == "revoke-uncommitted" and (
            control.get("qualification") != QUALIFICATION
            or control.get("placement") != PLACEMENT
        ):
            raise ValueError("foreign uncommitted generation")
        return self._reply(action, {
            "provision": {"credential_actor": "astrid-pack-host", "credential_file": "/runtime/token"},
            "enable": {"enabled": True}, "verify": {"fresh": True},
            "revoke": {"revoked": True},
            "revoke-uncommitted": {"revoked": True},
            "begin-drain": {"state": "pending", "activation_id": "activation-a"},
            "finish-drain": {"state": "drained", "activation_id": "activation-a"},
        }[action])

    def record_remote_activation(self, task_id, qualification):
        assert task_id == "task-a"
        if self.lose_once == "record":
            # Model the host claiming the newly enabled task before the commit
            # response reaches the coordinator.
            self.state = "running"
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


def test_exact_drain_retries_lost_responses_without_rotating_credentials():
    runtime = FakeRuntime()
    owner = _owner(runtime)
    runtime.state = "running"
    runtime.lose_once = "begin-drain"

    assert owner.begin_remote_drain("task-a", QUALIFICATION) == {
        "state": "pending", "activation_id": "activation-a",
    }
    assert owner.finish_remote_drain("task-a", QUALIFICATION) == {
        "state": "drained", "activation_id": "activation-a",
    }
    assert runtime.calls == ["begin-drain", "begin-drain", "finish-drain"]
    assert "provision" not in runtime.calls


def test_exact_uncommitted_revoke_is_idempotent_and_never_provisions():
    runtime = FakeRuntime()
    owner = _owner(runtime)
    runtime.lose_once = "revoke-uncommitted"
    control = {
        "action": "revoke-uncommitted", "qualification": QUALIFICATION,
        "placement": PLACEMENT,
    }
    assert owner.control_remote_credential("task-a", control) == {"revoked": True}
    assert runtime.calls == ["revoke-uncommitted", "revoke-uncommitted"]


@pytest.mark.parametrize("change", ["placement", "generation", "runtime"])
def test_uncommitted_revoke_rejects_foreign_or_nonqueued_generation(change):
    runtime = FakeRuntime()
    owner = _owner(runtime)
    qualification = dict(QUALIFICATION)
    placement = dict(PLACEMENT)
    if change == "placement":
        placement["actual"] = {**TARGET, "pod_id": "pod-b"}
    elif change == "generation":
        qualification["activation_id"] = "activation-b"
    else:
        runtime.epoch += 1
    with pytest.raises((ValueError, RemoteActivationUncertain)):
        owner.control_remote_credential("task-a", {
            "action": "revoke-uncommitted", "qualification": qualification,
            "placement": placement,
        })
    assert runtime.calls == []


def test_drain_rejects_a_foreign_task_or_runtime_generation_before_control():
    runtime = FakeRuntime()
    owner = _owner(runtime)
    foreign = {**QUALIFICATION, "runtime_epoch": 2}

    with pytest.raises(RemoteActivationUncertain):
        owner.begin_remote_drain("task-a", foreign)
    assert runtime.calls == []


@pytest.mark.parametrize("change", ["target", "cancel", "epoch", "session"])
def test_foreign_target_live_attempt_cancel_and_runtime_restart_block_authority(change):
    runtime = FakeRuntime()
    owner = _owner(runtime)
    if change == "target":
        runtime.target = {**TARGET, "pod_id": "pod-b"}
    elif change == "cancel":
        runtime.state = "cancelled"
    elif change == "epoch":
        runtime.epoch += 1
    else:
        runtime.session = "session-b"
    with pytest.raises(RemoteActivationUncertain):
        owner.record_remote_activation("task-a", QUALIFICATION, identity=OWNER)
    assert runtime.calls == []


def test_generated_workspace_client_task_mapping_is_supported():
    runtime = FakeRuntime()
    owner = _owner(runtime)
    runtime.get_task = lambda task_id: {
        "task_id": task_id,
        "run_id": "run-a",
        "state": "queued",
        "execution_request": {"target": TARGET},
        "execution_binding": {"effective_target": TARGET},
    }

    assert owner.control_remote_credential("task-a", {
        "action": "provision", "qualification": QUALIFICATION, "placement": {},
    }) == {
        "credential_actor": "astrid-pack-host", "credential_file": "/runtime/token",
    }
    assert runtime.calls == ["provision"]


def test_exact_commit_retry_is_allowed_after_worker_claim_but_provision_is_not():
    runtime = FakeRuntime()
    owner = _owner(runtime)
    runtime.state = "running"
    assert owner.record_remote_activation("task-a", QUALIFICATION, identity=OWNER) == QUALIFICATION
    assert runtime.calls == ["record"]
    with pytest.raises(RemoteActivationUncertain):
        owner.control_remote_credential("task-a", {
            "action": "provision", "qualification": QUALIFICATION, "placement": {},
        })
    assert runtime.calls == ["record"]


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
