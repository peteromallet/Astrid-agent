from __future__ import annotations

import json
import copy
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.h3_runpod_qualification import _loss_observer
from scripts import h3_runpod_qualification as qualification
from scripts import run_h3_canonical_on_runpod as canonical
from astrid.core.execution.runpod_deployment import DeploymentOperationError
from tests.test_runpod_deployment_operation import TASK


TARGET = {"kind": "runpod", "pod_id": "original-pod"}


def _result(value, *, returncode: int = 0):
    return SimpleNamespace(returncode=returncode, stdout=json.dumps(value), stderr="")


def test_loss_observer_requests_json_and_accepts_replacement_only_inventory(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: (calls.append((argv, kwargs)) or _result([{"id": "replacement-pod"}])),
    )

    observed = _loss_observer(TARGET)

    assert calls[0][0] == ["runpod-lifecycle", "list", "--json"]
    assert observed["status"] == "absent"


@pytest.mark.parametrize("inventory", [
    [{"id": "original-pod", "desired_status": "EXITED"}],
    [{"runpod_id": "original-pod"}],
])
def test_loss_observer_rejects_original_pod_in_any_inventory_row(monkeypatch, inventory):
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: _result(inventory))
    with pytest.raises(DeploymentOperationError, match="original pod still exists"):
        _loss_observer(TARGET)


@pytest.mark.parametrize("inventory", [
    "not-json",
    {"pods": [{"id": "replacement-pod", "runpod_id": "other-id"}]},
    {"pods": [{}]},
    {"pods": [None]},
    {"pods": "invalid"},
])
def test_loss_observer_rejects_unknown_or_conflicting_inventory(monkeypatch, inventory):
    stdout = inventory if isinstance(inventory, str) else json.dumps(inventory)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )
    with pytest.raises(DeploymentOperationError):
        _loss_observer(TARGET)


def test_loss_observer_rejects_provider_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: _result([], returncode=1))
    with pytest.raises(DeploymentOperationError, match="inventory is unavailable"):
        _loss_observer(TARGET)


@pytest.fixture
def lane_boundary(tmp_path, monkeypatch):
    config = qualification.H3LaneConfig(
        lane="lane-a", lane_root="/tmp/astrid-h3-lanes/lane-a",
        release_root="/workspace/h3-lanes/lane-a/releases/candidate",
        executor_id="host-a", session_ref="session-a", local_data_root=tmp_path,
        expected_pod="pod-a", runtime_instance_id="runtime-a", runtime_session_id="runtime-session-a",
        runtime_epoch=1,
    )
    claim = {"schema_version": "astrid.runpod.claim.v1", "pod_id": "pod-a",
             "network_volume_id": "volume-a", "ssh": "root@127.0.0.1 -p 2222",
             "volume_mount_path": "/workspace"}
    handle = tmp_path / "claim.json"
    handle.write_text(json.dumps(claim))
    credential = tmp_path / "owner.token"
    credential.write_text("fake-owner")
    profile = {"lane": config.lane, "lane_root": config.lane_root, "release_root": config.release_root,
               "executor_id": config.executor_id, "session_ref": config.session_ref,
               "boot_manifest_hash": "sha256:" + "b" * 64,
               "model_root": config.release_root + "/models"}
    for key in ("readiness_profile_path", "boot_manifest_path", "session_config_path", "release_manifest_path"):
        profile[key] = config.release_root + "/" + key + ".json"
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile))
    monkeypatch.setenv("ASTRID_H3_REMOTE_PROFILE", str(profile_path))
    health = {"status": "ok", "runtime_instance_id": config.runtime_instance_id,
              "runtime_session_id": config.runtime_session_id, "runtime_epoch": 1,
              "schema_digest": "sha256:" + "d" * 64}
    task = copy.deepcopy(TASK)
    task.update(id="lane-a-task", run_id="lane-a-run")
    target = {"kind": "runpod", "pod_id": "pod-a", "provider_account_ref": "account-a"}
    task["execution_request"]["target"] = target
    task["target"] = target
    task["execution_binding"].update(original_target=target, effective_target=target, resolved_target=target)
    calls = []
    handshake = {"actor_id": "owner", "realm_id": "realm-a"}
    owner = SimpleNamespace(
        health=lambda: (calls.append("owner.health") or health),
        handshake=lambda *_a: (calls.append("owner.handshake") or handshake),
        doctor=lambda: (calls.append("owner.doctor") or {"status": "ok"}),
    )
    monkeypatch.setattr(qualification, "WorkspaceClient", lambda *_a: owner)
    client = SimpleNamespace(
        health=lambda: health,
        tasks=SimpleNamespace(show=lambda _id: (calls.append("task.read") or task)),
        _remote=SimpleNamespace(_transport=SimpleNamespace(
            endpoint="http://127.0.0.1:6543", _last_handshake={"realm_id": "realm-a"},
        )),
    )
    identity = {"source_root": str(canonical.PRODUCT_SOURCE_ROOT), "branch": "candidate",
                "head": "a" * 40, "content_digest": "sha256:" + "c" * 64,
                "scoped_source_digest": "c" * 64}
    monkeypatch.setattr(canonical, "assert_product_source_identity", lambda **_kw: identity)
    for name in ("_provider", "_remote_file_hashes", "_remote_boot_manifest_hash", "_ssh"):
        monkeypatch.setattr(qualification, name, lambda *_a, **_kw: pytest.fail("provider/SSH must not run"))
    kwargs = {"client": client, "handle_path": handle, "operation_id": "op-a",
              "lane_config": config, "source_identity": identity, "owner_credential": credential,
              "task_id": task["id"], "run_id": task["run_id"], "preflight_only": True}
    return SimpleNamespace(kwargs=kwargs, calls=calls, task=task, handshake=handshake,
                           health=health, config=config, profile=profile, profile_path=profile_path)


def test_explicit_connected_cpu_boundary_authenticates_before_task_read(lane_boundary):
    result = qualification.default_qualification_factory(**lane_boundary.kwargs)
    assert result["status"] == "preclaim_ready"
    assert result["provider_allocation"] is False
    assert result["remote_hashes_verified"] is False
    assert lane_boundary.calls.index("owner.doctor") < lane_boundary.calls.index("task.read")
    assert all(Path(path).is_relative_to(lane_boundary.config.lane_root) for path in result["paths"].values())


@pytest.mark.parametrize("change", [
    {"lane_root": "/tmp/astrid-h3-lanes/lane-b"},
    {"release_root": "/workspace/h3-lanes/lane-b/releases/candidate"},
    {"release_root": "/workspace/h3-lanes/lane-a/releases/../foreign"},
    {"executor_id": "../host"}, {"session_ref": "../session"},
])
def test_foreign_or_escaping_lane_roots_rejected(lane_boundary, change):
    with pytest.raises(DeploymentOperationError):
        replace(lane_boundary.config, **change)
    assert lane_boundary.calls == []


@pytest.mark.parametrize("field, value, message", [
    ("actor_id", "product", "authenticate as owner"),
    ("realm_id", "foreign", "different realm"),
])
def test_foreign_owner_rejected_without_provider(lane_boundary, field, value, message):
    lane_boundary.handshake[field] = value
    with pytest.raises(DeploymentOperationError, match=message):
        qualification.default_qualification_factory(**lane_boundary.kwargs)
    assert "task.read" not in lane_boundary.calls


def test_missing_owner_credential_rejected_without_provider(lane_boundary, monkeypatch):
    monkeypatch.delenv("ASTRID_H3_OWNER_CREDENTIAL", raising=False)
    with pytest.raises(DeploymentOperationError, match="owner credential is required"):
        qualification.default_qualification_factory(**{**lane_boundary.kwargs, "owner_credential": None})
    assert lane_boundary.calls == []


@pytest.mark.parametrize("field", ["runtime_instance_id", "runtime_session_id", "runtime_epoch"])
def test_foreign_runtime_rejected_without_provider(lane_boundary, field):
    lane_boundary.health[field] = "foreign"
    with pytest.raises(DeploymentOperationError, match="foreign Runtime"):
        qualification.default_qualification_factory(**lane_boundary.kwargs)


def test_foreign_pod_rejected_without_provider(lane_boundary):
    with pytest.raises(DeploymentOperationError, match="foreign lane pod"):
        qualification.default_qualification_factory(**{
            **lane_boundary.kwargs, "lane_config": replace(lane_boundary.config, expected_pod="foreign-pod"),
        })
    assert lane_boundary.calls == []


def test_foreign_task_run_and_profile_rejected_without_provider(lane_boundary):
    with pytest.raises(DeploymentOperationError, match="foreign task/run"):
        qualification.default_qualification_factory(**{**lane_boundary.kwargs, "run_id": "foreign-run"})
    lane_boundary.profile["session_config_path"] = "/tmp/foreign/config.json"
    lane_boundary.profile_path.write_text(json.dumps(lane_boundary.profile))
    with pytest.raises(DeploymentOperationError, match="escapes"):
        qualification.default_qualification_factory(**lane_boundary.kwargs)


def test_three_lanes_have_disjoint_source_data_output_and_credentials(lane_boundary):
    path_sets = []
    for lane in ("lane-a", "lane-b", "lane-c"):
        config = replace(lane_boundary.config, lane=lane,
                         lane_root=f"/tmp/astrid-h3-lanes/{lane}",
                         release_root=f"/workspace/h3-lanes/{lane}/releases/candidate")
        path_sets.append(set(config.paths().values()))
    assert len(set.union(*path_sets)) == sum(map(len, path_sets))


def test_explicit_candidate_cli_reaches_cpu_boundary_without_launcher(lane_boundary, monkeypatch, capsys):
    from astrid.sdk import AstridClient

    class Opened:
        def __enter__(self):
            return lane_boundary.kwargs["client"]
        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(AstridClient, "open", lambda **_kw: Opened())
    monkeypatch.setattr(AstridClient, "open_from_launcher", lambda **_kw: pytest.fail("must not bootstrap"))
    config = lane_boundary.config
    argv = ["--preflight-only", "--operation-id", "op-a", "--receipt", str(config.local_data_root / "receipt.json"),
            "--existing-task", lane_boundary.task["id"], "--expected-run", lane_boundary.task["run_id"],
            "--expected-branch", "candidate", "--expected-head", "a" * 40, "--content-digest", "sha256:" + "c" * 64,
            "--lane", config.lane, "--authorize-lane", config.lane, "--lane-root", config.lane_root,
            "--release-root", config.release_root, "--executor-id", config.executor_id, "--session-ref", config.session_ref,
            "--data-root", str(config.local_data_root), "--handle-path", str(lane_boundary.kwargs["handle_path"]),
            "--owner-credential", str(lane_boundary.kwargs["owner_credential"]), "--expected-pod", config.expected_pod,
            "--runtime-endpoint", "http://127.0.0.1:6543", "--runtime-realm", "realm-a",
            "--runtime-instance-id", config.runtime_instance_id, "--runtime-session-id", config.runtime_session_id,
            "--runtime-epoch", "1"]
    assert canonical.main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "preclaim_ready"
    assert result["live_blocker"] == canonical.LIVE_BOUNDARY_BLOCKER
    assert not (config.local_data_root / "receipt.json").exists()
