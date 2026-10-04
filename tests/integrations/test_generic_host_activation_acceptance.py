"""Real parked receiver against the locally selected Runtime owner contract."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("runtime_protocol")
from runtime_protocol.daemon import RuntimeDaemon
from runtime_protocol.remote_worker_activation import QualifiedRemoteWorkerLauncher
from runtime_protocol.remote_worker_deployment import (
    ArtifactReference,
    DeploymentReference,
    deployment_binding_from_task,
    project_launch,
)

from astrid.core.execution.generic_host import (
    GenericPackHost,
    RuntimeProtocolClient,
    _await_enabled_runtime_credential,
    source_checkout_closure_digest,
    source_checkout_digest,
)
from astrid.core.gateway.dispatch import compose_profile_handoff
from astrid.core.generation.model_root import canonical_model_inventory_digest
from astrid.sdk import host_bootstrap
from banodoco_workspace_client import ApiError
from tests.helpers.runtime import initialize_runtime_realm

OWNER = {"actor": "owner", "scopes": ["admin"]}


@pytest.fixture
def resident_host(tmp_path):
    """CPU fixture: actual host/OS/artifacts and one resident authority store."""
    source = Path(__file__).resolve().parents[2]
    data = tmp_path / "data"
    support = data / "runtime"
    support.mkdir(parents=True)
    models = tmp_path / "models"
    models.mkdir()
    imports = tmp_path / "python-libs"
    imports.mkdir()
    # The canonical, non-symlink interpreter consumes these measured packages.
    # Avoid changing/installing anything in the shared venv or Runtime checkout.
    for name in ("PIL", "yaml", "dotenv", "jsonschema", "attrs", "attr", "referencing", "rpds",
                 "jsonschema_specifications", "typing_extensions"):
        package = importlib.import_module(name)
        if hasattr(package, "__path__"):
            shutil.copytree(Path(package.__file__).parent, imports / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(package.__file__, imports / Path(package.__file__).name)
    pack = tmp_path / "cpu-pack"
    pack.mkdir()
    (pack / "executor.yaml").write_text(json.dumps({
        "schema_version": 1, "id": "cpu.activation", "name": "CPU activation",
        "kind": "external", "version": "1.0",
        "command": {"argv": ["{python_exec}", "-c",
                              "from pathlib import Path; "
                              f"assert Path('{{out}}').is_relative_to({str(tmp_path / 'outputs')!r}); "
                              "Path('{out}/answer.txt').write_text('accepted')"]},
        "outputs": [{"name": "answer", "type": "file", "path_template": "{out}/answer.txt",
                     "artifact_type": "text/plain"}],
        "metadata": {"adapter_family": "cpu", "resource_keys": ["cpu"]},
    }))
    profile = support / "profile.json"
    profile.write_text(json.dumps({
        "launch": {"model_root": {"schema_version": 1, "path": str(models), "inventory": [],
                                  "inventory_digest": canonical_model_inventory_digest([])}},
        "verified_facts": {"exact": {}, "minimum": {}},
    }))
    session = support / "session.json"
    session.write_text(json.dumps({"session_ref": "cpu-local-session", "capacity": 1}))
    boot = support / "boot-manifest.json"
    handoff = compose_profile_handoff(boot, support_root=support)
    record = GenericPackHost(pack_roots=[pack]).discover()[0]
    realm = initialize_runtime_realm(tmp_path / "realm")
    daemon = RuntimeDaemon(realm, support_root=support, production_worker_credentials=True).start()
    target = {"kind": "runpod", "pod_id": "explicit-fake-local-pod", "provider_account_ref": "test-account"}
    def verify_fake_target(actual):
        assert actual == target  # Only this CPU fixture maps the fake pod to our local OS.
    preparer = host_bootstrap._ParkedPackHostPreparer(
        project_launch, timeout_seconds=10, verify_target=verify_fake_target,
    )
    original_abort = preparer.abort
    try:
        service = daemon.service
        service.register_capability({"capability_id": record.id, "definition_digest": record.capability_digest})
        project = service.create_project({"slug": "activation", "name": "Activation"},
                                         idempotency_key="activation-project")
        task_id = service.create_task({
            "project": project["id"],
            "capability_id": record.id, "capability_digest": record.capability_digest,
            "input_object_ids": [], "spec": {"inputs": {}}, "idempotency_key": "cpu-parked-receiver",
            "execution_request": {"schema_version": 1, "target": target},
        }, enforce_readiness=True)["task"]["id"]
        task = service._task_resource(service.store.get_task(task_id))
        binding = deployment_binding_from_task(task)
        executable = Path(sys.executable).resolve()
        artifacts = tuple(sorted((ArtifactReference(name, path, host_bootstrap._host_artifact_digest(path))
                          for name, path in (("python", executable), ("imports", imports), ("cpu-pack", pack))),
                          key=lambda item: item.name))
        health_client = RuntimeProtocolClient(daemon.endpoint, daemon.token)
        health = health_client.health()
        reference = DeploymentReference(
            deployment_id="cpu-receiver", revision="cpu-receiver-1", task_id=task_id,
            run_id=binding.admission_identity.run_id,
            target_ref="runpod:" + target["pod_id"], effective_target_ref="runpod:" + target["pod_id"],
            executable=next(item for item in artifacts if item.name == "python"), dependency_closure=artifacts,
            source_closure_digest="sha256:" + source_checkout_closure_digest(source),
            data_root=data, support_root=support, runtime_endpoint=daemon.endpoint,
            runtime_instance_id=daemon.instance_id, runtime_epoch=health.runtime_epoch,
            runtime_schema_digest=health.schema_digest, model_root=models, capacity=1,
            session_ref="cpu-local-session", session_config_digest="sha256:" + hashlib.sha256(session.read_bytes()).hexdigest(),
            output_root=tmp_path / "outputs", credential_ref=str(daemon.worker_credential_path),
            executor_id=host_bootstrap.PACK_HOST_ACTOR, boot_manifest_path=boot,
            boot_manifest_hash="sha256:" + handoff["sha256"], readiness_profile_path=profile,
            readiness_profile_hash="sha256:" + hashlib.sha256(profile.read_bytes()).hexdigest(),
            source_checkout=source, source_checkout_digest=source_checkout_digest(source),
            pack_roots=(pack,), ready_file=support / "receiver.ready.json",
            admission_identity=binding.admission_identity, capability_identity=binding.capability_identity,
            input_bindings=binding.input_bindings, original_target=target, effective_target=target,
            execution_target=target,
        )
        reference.output_root.mkdir()
        def runtime_health():
            value = vars(health_client.health())
            return {**value, "runtime_session_id": service.runtime_session_id}
        def executor_observer(executor_id):
            with service.store._mutex:
                row = service.store.conn.execute("SELECT * FROM executors WHERE id=?", (executor_id,)).fetchone()
                return service.store._executor_result(row) if row is not None else None
        inspector = host_bootstrap._ParkedPackHostInspector(
            runtime_health, session_config_path=session,
            provider_observer=lambda handle: {"account_ref": target["provider_account_ref"], "pod_id": target["pod_id"]},
            executor_observer=executor_observer,
        )
        launcher = QualifiedRemoteWorkerLauncher(
            runtime=service, credentials=None, preparer=preparer, inspector=inspector,
            credential_control=lambda task_id, body: daemon.remote_credential_control(task_id, body, identity=OWNER),
            scopes=host_bootstrap.PACK_HOST_SCOPES,
        )
        yield SimpleNamespace(daemon=daemon, service=service, task_id=task_id, task=task,
                              reference=reference, preparer=preparer, inspector=inspector, launcher=launcher)
    finally:
        if preparer._handle is not None:
            original_abort(preparer._handle)
        daemon.stop()


def test_real_parked_process_inspection_preserves_identity_guards(resident_host, monkeypatch):
    witness = resident_host
    handle = witness.preparer.prepare(witness.reference)
    observed = witness.inspector.observe(handle)
    assert observed["process"]["pid"] == handle.process.pid
    assert observed["process"]["birth_id"] == handle.state["process_birth_id"]
    assert observed["process"]["uid"] == os.getuid()

    original_command = host_bootstrap._host_command
    command = original_command(handle.process.pid)
    import shlex
    argv = shlex.split(command)
    monkeypatch.setattr(host_bootstrap, "_host_command", lambda pid: shlex.join([
        "/unrelated/Python", *argv[1:],
    ]))
    with pytest.raises(host_bootstrap.PackHostBootstrapError, match="executable, uid or birth"):
        witness.inspector.observe(handle)
    monkeypatch.setattr(host_bootstrap, "_host_command", original_command)

    original_run = host_bootstrap.subprocess.run
    def foreign_uid(argv, **kwargs):
        result = original_run(argv, **kwargs)
        if argv[-1] == "uid=":
            result.stdout = str(os.getuid() + 1)
        return result
    monkeypatch.setattr(host_bootstrap, "subprocess", SimpleNamespace(
        **{**vars(subprocess), "run": foreign_uid},
    ))
    with pytest.raises(host_bootstrap.PackHostBootstrapError, match="executable, uid or birth"):
        witness.inspector.observe(handle)
    monkeypatch.setattr(host_bootstrap, "subprocess", subprocess)

    birth = handle.state["process_birth_id"]
    try:
        handle.state["process_birth_id"] = "a different process incarnation"
        with pytest.raises(host_bootstrap.PackHostBootstrapError, match="OS identity changed"):
            witness.inspector.observe(handle)
    finally:
        handle.state["process_birth_id"] = birth


@pytest.mark.parametrize("lost_reply", [False, True])
def test_bootstrap_register_observe_commit_and_same_incarnation_recovery(resident_host, monkeypatch, lost_reply):
    witness = resident_host
    parked = witness.launcher.park(witness.reference, target=witness.reference.effective_target)
    events = []
    acknowledge = witness.preparer.acknowledge
    observe_ready = witness.inspector.observe_ready
    record = witness.service.record_remote_activation

    def private_ack(handle, grant):
        token = Path(grant["credential_file"]).read_text().strip()
        with pytest.raises(ApiError) as denied:
            RuntimeProtocolClient(witness.daemon.endpoint, token)._authenticate_worker(witness.reference.executor_id)
        assert denied.value.status == 401
        assert witness.service._latest_remote_activation(witness.task_id) is None
        result = acknowledge(handle, grant)
        assert witness.service._latest_remote_activation(witness.task_id) is None
        events.append("private_ack_disabled")
        return result

    def ready(handle):
        observation = observe_ready(handle)
        if witness.service._latest_remote_activation(witness.task_id) is None:
            client = RuntimeProtocolClient(witness.daemon.endpoint, witness.daemon.worker_credential_path.read_text().strip())
            client._authenticate_worker(witness.reference.executor_id)
            denied = client.claim_next(executor_id=witness.reference.executor_id,
                                       capability_ids=[witness.reference.capability_identity.capability_id],
                                       idempotency_key="precommit-claim", target=witness.reference.effective_target)
            assert denied.waiting_reason == "remote_activation_missing"
            assert getattr(denied, "attempt_id", None) is None
            assert witness.service.task(witness.task_id)["task"]["status"] == "queued"
            events.append("enabled_registered_observed_claim_denied")
        return observation

    def commit(task_id, qualification, **kwargs):
        first = "commit" not in events
        if first:
            assert events == ["private_ack_disabled", "enabled_registered_observed_claim_denied"]
        result = record(task_id, qualification, **kwargs)
        events.append("commit" if first else "replayed_commit")
        if lost_reply and first:
            deadline = time.monotonic() + 5
            while witness.service.task(task_id)["task"]["status"] == "queued" and time.monotonic() < deadline:
                time.sleep(0.02)
            assert witness.service.task(task_id)["task"]["status"] != "queued"
            raise EOFError("committed response lost after host claim")
        return result

    monkeypatch.setattr(witness.preparer, "acknowledge", private_ack)
    monkeypatch.setattr(witness.inspector, "observe_ready", ready)
    monkeypatch.setattr(witness.service, "record_remote_activation", commit)
    qualification = witness.launcher.activate(witness.task, witness.reference, parked)
    assert witness.launcher.activation_state == "active"
    assert witness.service._latest_remote_activation(witness.task_id) == qualification
    assert host_bootstrap._host_identity_matches(parked.handle.state)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        task = witness.service.task(witness.task_id)["task"]
        if task["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)
    assert task["status"] == "completed", task
    with witness.service.store._mutex:
        assert witness.service.store.conn.execute("SELECT COUNT(*) FROM attempts WHERE task_id=?", (witness.task_id,)).fetchone()[0] == 1
    outputs = witness.service.managed_outputs(witness.task_id)
    assert len(outputs) == 1
    assert witness.service.object(outputs[0]["object_id"])[1] == b"accepted"
    if lost_reply:
        assert events[-1] == "replayed_commit"
    assert witness.service.store.conn.execute(
        "SELECT COUNT(*) FROM events WHERE task_id=? AND kind='task.remote_activation_qualified'",
        (witness.task_id,),
    ).fetchone()[0] == 1


@pytest.mark.parametrize("failure", ["ack", "ready", "cleanup"])
def test_precommit_failure_revokes_before_stopping_exact_host(resident_host, monkeypatch, failure):
    witness = resident_host
    parked = witness.launcher.park(witness.reference, target=witness.reference.effective_target)
    abort = witness.preparer.abort
    stopped = []
    def fail(*args, **kwargs):
        raise OSError("deliberate precommit failure")
    monkeypatch.setattr(witness.preparer if failure == "ack" else witness.inspector,
                        "acknowledge" if failure == "ack" else "observe_ready", fail)
    def stop(handle):
        assert witness.service._latest_remote_activation(witness.task_id) is None
        assert witness.daemon.credentials.actor_metadata(witness.reference.executor_id) is None
        stopped.append(handle)
        if failure == "cleanup":
            raise OSError("cannot confirm exact cleanup")
        abort(handle)
    monkeypatch.setattr(witness.preparer, "abort", stop)
    with pytest.raises(OSError):
        witness.launcher.activate(witness.task, witness.reference, parked)
    assert stopped == [parked.handle]
    assert witness.launcher.activation_state == ("unknown" if failure == "cleanup" else "inactive")
    assert witness.service.task(witness.task_id)["task"]["status"] == "queued"


@pytest.mark.parametrize("changed", ["ready_marker", "registration", "provider"])
def test_independent_readiness_rejects_changed_evidence(resident_host, monkeypatch, changed):
    witness = resident_host
    parked = witness.launcher.park(witness.reference, target=witness.reference.effective_target)
    await_ready = witness.preparer.await_ready
    def changed_after_start(handle):
        await_ready(handle)
        if changed == "ready_marker":
            marker = json.loads(witness.reference.ready_file.read_text())
            marker["output_root"] = "/foreign/output"
            witness.reference.ready_file.write_text(json.dumps(marker))
        elif changed == "registration":
            monkeypatch.setattr(witness.inspector, "executor_observer", lambda executor_id: None)
        else:
            monkeypatch.setattr(witness.inspector, "provider_observer", lambda handle: {
                "account_ref": "different-account", "pod_id": "different-pod",
            })
    monkeypatch.setattr(witness.preparer, "await_ready", changed_after_start)
    with pytest.raises(host_bootstrap.PackHostBootstrapError):
        witness.launcher.activate(witness.task, witness.reference, parked)
    assert witness.service._latest_remote_activation(witness.task_id) is None
    assert witness.daemon.credentials.actor_metadata(witness.reference.executor_id) is None
    assert parked.handle.process.poll() is not None


def test_uncertain_commit_preserves_exact_host_and_credential(resident_host, monkeypatch):
    witness = resident_host
    parked = witness.launcher.park(witness.reference, target=witness.reference.effective_target)
    def unknown(*args, **kwargs):
        raise OSError("commit transport unresolved")
    monkeypatch.setattr(witness.service, "record_remote_activation", unknown)
    monkeypatch.setattr(witness.preparer, "abort", lambda handle: pytest.fail("uncertain commit must not stop host"))
    with pytest.raises(OSError):
        witness.launcher.activate(witness.task, witness.reference, parked)
    assert witness.launcher.activation_state == "unknown"
    assert witness.launcher.activation_host == parked
    assert witness.daemon.credentials.actor_metadata(witness.reference.executor_id) is not None
    assert host_bootstrap._host_identity_matches(parked.handle.state)


def test_real_disabled_bearer_fails_handshake_and_enabled_bearer_becomes_ready(tmp_path):
    realm = initialize_runtime_realm(tmp_path / "realm")
    daemon = RuntimeDaemon(
        realm, support_root=tmp_path / "support", production_worker_credentials=True,
    ).start()
    try:
        token, _path = daemon.credentials.provision(
            "activation-host", ["handshake", "worker:execute"], enabled=False,
        )
        client = RuntimeProtocolClient(daemon.endpoint, token)
        assert client.health().status == "ok"
        with pytest.raises(ApiError) as rejected:
            client._authenticate_worker("activation-host")
        assert rejected.value.status == 401
        daemon.credentials.enable_actor("activation-host")
        _await_enabled_runtime_credential(client, executor_id="activation-host", timeout_seconds=1)
        daemon.credentials.disable_actor("activation-host")
        with pytest.raises(ApiError) as rejected:
            client._authenticate_worker("activation-host")
        assert rejected.value.status == 401
    finally:
        daemon.stop()
