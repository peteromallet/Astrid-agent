"""Real parked receiver against the locally selected Runtime owner contract."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("runtime_protocol")
from banodoco_workspace_client import ApiError, WorkspaceClient
from runtime_protocol.daemon import RuntimeDaemon
from runtime_protocol.errors import ConflictError
from runtime_protocol.remote_worker_activation import QualifiedRemoteWorkerLauncher
from runtime_protocol.remote_worker_deployment import (
    ArtifactReference, DeploymentReference, deployment_binding_from_task, project_launch,
)

from astrid.core.execution.generic_host import (
    RuntimeProtocolClient,
    _await_enabled_runtime_credential,
    GenericPackHost, source_checkout_digest, source_checkout_closure_digest,
)
from astrid.core.execution import generic_host
from astrid.core.execution.remote_activation_owner import RuntimeRemoteActivationOwner
from astrid.core.gateway.dispatch import compose_profile_handoff
from astrid.core.generation.model_root import canonical_model_inventory_digest
from astrid.sdk import host_bootstrap
from tests.helpers.runtime import initialize_runtime_realm


OWNER = {"actor": "owner", "scopes": ["admin"]}


@pytest.fixture
def resident_host(tmp_path, request):
    """CPU fixture: actual host/OS/artifacts and one resident authority store."""
    source = Path(__file__).resolve().parents[2]
    data = tmp_path / "data"
    support = data / "runtime"
    support.mkdir(parents=True)
    models = tmp_path / "models"
    models.mkdir()
    imports = tmp_path / "site-packages"
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
    delegated = getattr(request, "param", False)
    parent_gate, child_gate = tmp_path / "parent-release", tmp_path / "child-release"
    parent_started, child_started = tmp_path / "parent-started", tmp_path / "child-started"

    def command(gate, started):
        return ("from pathlib import Path\nimport time\n"
                f"Path({str(started)!r}).write_text('running')\n"
                f"while not Path({str(gate)!r}).exists(): time.sleep(0.02)\n"
                "Path('{out}/answer.txt').write_text('accepted')\n")

    (pack / "executor.yaml").write_text(json.dumps({
        "schema_version": 1, "id": "cpu.activation", "name": "CPU activation",
        "kind": "external", "version": "1.0",
        "command": {"argv": ["{python_exec}", "-c",
                              command(parent_gate, parent_started) if delegated else
                              "from pathlib import Path; Path('{out}/answer.txt').write_text('accepted')"]},
        "outputs": [{"name": "answer", "type": "file", "path_template": "{out}/answer.txt",
                     "artifact_type": "text/plain"}],
        "metadata": {"adapter_family": "cpu", "resource_keys": ["cpu-parent"]},
    }))
    pack_roots = [pack]
    matrix = None
    if delegated:
        child_pack = tmp_path / "cpu-child-pack"
        child_pack.mkdir()
        (child_pack / "executor.yaml").write_text(json.dumps({
            "schema_version": 1, "id": "cpu.activation.child", "name": "CPU child",
            "kind": "external", "version": "1.0",
            "command": {"argv": ["{python_exec}", "-c", command(child_gate, child_started)]},
            "outputs": [{"name": "answer", "type": "file", "path_template": "{out}/answer.txt",
                         "artifact_type": "text/plain"}],
            "metadata": {"adapter_family": "cpu", "resource_keys": ["cpu-child"]},
        }))
        pack_roots.append(child_pack)
        matrix = support / "cpu-capability-matrix.json"
        matrix.write_text(json.dumps({
            "schema_version": 1,
            "capabilities": [
                {"id": capability, "disposition": "required", "adapter_family": "cpu",
                 "evidence_reason": "Independent CPU fixture lanes at measured capacity two",
                 "resource_keys": [resource]}
                for capability, resource in (("cpu.activation", "cpu-parent"),
                                             ("cpu.activation.child", "cpu-child"))
            ],
        }, sort_keys=True))
    profile = support / "profile.json"
    profile.write_text(json.dumps({
        "launch": {"model_root": {"schema_version": 1, "path": str(models), "inventory": [],
                                  "inventory_digest": canonical_model_inventory_digest([])}},
        "verified_facts": {"exact": {}, "minimum": {}},
    }))
    session = support / "session.json"
    session.write_text(json.dumps({"session_ref": "cpu-local-session", "capacity": 2}))
    boot = support / "boot-manifest.json"
    handoff = compose_profile_handoff(boot, support_root=support)
    records = GenericPackHost(pack_roots=pack_roots, capability_matrix=matrix).discover()
    record = next(item for item in records if item.id == "cpu.activation")
    child_record = next((item for item in records if item.id == "cpu.activation.child"), None)
    if child_record is not None:
        assert set(record.resource_keys) == {"cpu-parent"}
        assert set(child_record.resource_keys) == {"cpu-child"}
        assert set(record.resource_keys).isdisjoint(child_record.resource_keys)
    realm = initialize_runtime_realm(tmp_path / "realm")
    daemon = RuntimeDaemon(realm, support_root=support, production_worker_credentials=True).start()
    preparer = host_bootstrap._ParkedPackHostPreparer(project_launch, timeout_seconds=10)
    original_abort = preparer.abort
    try:
        service = daemon.service
        for item in records:
            service.register_capability({"capability_id": item.id, "definition_digest": item.capability_digest})
        target = {"kind": "machine", "id": socket.gethostname()}
        admission = {
            "capability_id": record.id, "capability_digest": record.capability_digest,
            "input_object_ids": [], "spec": {"inputs": {}}, "idempotency_key": "cpu-parked-receiver",
            "execution_request": {"schema_version": 1, "target": target},
        }
        if child_record is not None:
            admission["child_delegation"] = {
                "capabilities": [{"capability_id": child_record.id, "capability_digest": child_record.capability_digest}],
                "targets": [target], "input_object_ids": [],
            }
        task_id = service.create_task(admission, enforce_readiness=True)["task"]["id"]
        task = service._task_resource(service.store.get_task(task_id))
        binding = deployment_binding_from_task(task)
        executable = Path(sys.executable).resolve()
        artifact_paths = [("python", executable), ("imports", imports)] + [(path.name, path) for path in pack_roots]
        if matrix is not None:
            artifact_paths.append(("capability-matrix", matrix))
        artifacts = tuple(sorted((ArtifactReference(name, path, host_bootstrap._host_artifact_digest(path))
                          for name, path in artifact_paths),
                          key=lambda item: item.name))
        health_client = RuntimeProtocolClient(daemon.endpoint, daemon.token)
        health = health_client.health()
        reference = DeploymentReference(
            deployment_id="cpu-receiver", revision="cpu-receiver-1", task_id=task_id,
            run_id=binding.admission_identity.run_id,
            target_ref="machine:" + target["id"], effective_target_ref="machine:" + target["id"],
            executable=next(item for item in artifacts if item.name == "python"), dependency_closure=artifacts,
            source_closure_digest="sha256:" + source_checkout_closure_digest(source),
            data_root=data, support_root=support, runtime_endpoint=daemon.endpoint,
            runtime_instance_id=daemon.instance_id, runtime_epoch=health.runtime_epoch,
            runtime_schema_digest=health.schema_digest, model_root=models, capacity=2,
            session_ref="cpu-local-session", session_config_digest="sha256:" + hashlib.sha256(session.read_bytes()).hexdigest(),
            output_root=tmp_path / "outputs", credential_ref=str(daemon.worker_credential_path),
            executor_id=host_bootstrap.PACK_HOST_ACTOR, boot_manifest_path=boot,
            boot_manifest_hash="sha256:" + handoff["sha256"], readiness_profile_path=profile,
            readiness_profile_hash="sha256:" + hashlib.sha256(profile.read_bytes()).hexdigest(),
            source_checkout=source, source_checkout_digest=source_checkout_digest(source),
            pack_roots=tuple(pack_roots), capability_matrix=matrix, ready_file=support / "receiver.ready.json",
            admission_identity=binding.admission_identity, capability_identity=binding.capability_identity,
            input_bindings=binding.input_bindings, original_target=target, effective_target=target,
            execution_target=target,
        )
        inspector = host_bootstrap._ParkedPackHostInspector(health_client.health, session_config_path=session)
        launcher = QualifiedRemoteWorkerLauncher(
            runtime=service, credentials=daemon.credentials, preparer=preparer, inspector=inspector,
            scopes=host_bootstrap.PACK_HOST_SCOPES,
        )
        yield SimpleNamespace(daemon=daemon, service=service, task_id=task_id, task=task,
                              reference=reference, preparer=preparer, inspector=inspector, launcher=launcher,
                              child_record=child_record, parent_gate=parent_gate, child_gate=child_gate,
                              parent_started=parent_started, child_started=child_started)
    finally:
        try:
            if preparer._handle is not None:
                handle = preparer._handle
                handle.control.close()
                try:
                    handle.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    original_abort(handle)  # Still live: retain the strict identity check.
                original_abort(handle)
                assert handle.process.poll() is not None
        finally:
            daemon.stop()


@pytest.mark.parametrize("lost_reply", [None, "ack", "reply"])
def test_real_receiver_commits_before_ack_and_recovers_same_incarnation(resident_host, monkeypatch, lost_reply):
    witness = resident_host
    parked = witness.launcher.park(witness.reference, target=witness.reference.effective_target)
    original_acknowledge = witness.preparer.acknowledge
    callbacks = []
    deliveries = []
    parent_pid = os.getpid()

    def acknowledge(handle, grant, *, accept):
        deliveries.append(handle)

        def committed(received, process):
            assert os.getpid() == parent_pid
            assert process == {"pid": handle.process.pid, "birth_id": handle.state["process_birth_id"]}
            assert process["pid"] != parent_pid
            token = Path(grant["credential_file"]).read_text().strip()
            disabled = RuntimeProtocolClient(witness.daemon.endpoint, token)
            assert disabled.health().status == "ok"
            with pytest.raises(ApiError) as rejected:
                disabled._authenticate_worker(witness.reference.executor_id)
            assert rejected.value.status == 401
            assert witness.service._latest_remote_activation(witness.task_id) is None
            accept(received, process)
            # A second connection proves that Runtime committed before the
            # parent emits a receipt or the child can emit a final ACK.
            with sqlite3.connect(witness.service.store.db_path) as reader:
                payload = reader.execute(
                    "SELECT payload_json FROM events WHERE task_id=? AND kind=?",
                    (witness.task_id, "task.remote_activation_accepted"),
                ).fetchall()
            assert [json.loads(row[0]) for row in payload] == [{"activation_id": grant["activation_id"]}]
            callbacks.append(dict(received))

        result = original_acknowledge(handle, grant, accept=committed)
        assert set(result) == {"activation_id", "executor_incarnation", "evidence_digest"}
        if lost_reply == "reply":
            raise EOFError("private reply deliberately lost")
        return result

    witness.preparer.acknowledge = acknowledge
    if lost_reply == "ack":
        send = generic_host._send_activation_frame

        def lose_ack(control, value):
            send(control, value)
            if value["version"] == "runtime.local-worker-activation-recorded/v1":
                control.shutdown(socket.SHUT_WR)
                control.close()  # The real receiver's final ACK has no reader.

        monkeypatch.setattr(generic_host, "_send_activation_frame", lose_ack)
    qualification = witness.launcher.activate(witness.task, witness.reference, parked)
    assert witness.launcher.activation_state == "active"
    assert deliveries == [parked.handle]
    assert len(callbacks) == 1
    assert callbacks[0]["activation_id"] == qualification["activation_id"]
    assert host_bootstrap._host_identity_matches(parked.handle.state)
    token = witness.daemon.worker_credential_path.read_text().strip()
    RuntimeProtocolClient(witness.daemon.endpoint, token)._authenticate_worker(witness.reference.executor_id)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = witness.service.task(witness.task_id)["task"]["status"]
        if state == "completed":
            break
        time.sleep(0.02)
    assert state == "completed"
    events = witness.service._remote_activation_history(witness.task_id)
    assert [kind for kind, _ in events].count("task.remote_activation_accepted") == 1
    with pytest.raises(ConflictError):
        witness.launcher.activate(witness.task, witness.reference, parked)
    assert witness.daemon.worker_credential_path.read_text().strip() == token
    assert host_bootstrap._host_identity_matches(parked.handle.state)
    witness.preparer.abort(parked.handle)
    assert parked.handle.process.poll() is not None
    assert parked.handle.control.fileno() == -1
    assert not witness.reference.ready_file.exists()


@pytest.mark.parametrize("resident_host", [True], indirect=True)
def test_machine_host_and_resident_owner_allow_real_parent_child_progress_with_capacity_two(resident_host):
    witness = resident_host
    owner_client = WorkspaceClient(witness.daemon.endpoint, witness.daemon.token)
    health = owner_client.health()
    owner = RuntimeRemoteActivationOwner(
        owner_client, runtime_instance_id=health["runtime_instance_id"],
        runtime_session_id=health["runtime_session_id"], runtime_epoch=health["runtime_epoch"],
    )
    launcher = QualifiedRemoteWorkerLauncher(
        runtime=owner, credentials=None, credential_control=owner.control_remote_credential,
        preparer=witness.preparer, inspector=witness.inspector, scopes=host_bootstrap.PACK_HOST_SCOPES,
    )
    parked = launcher.park(witness.reference, target=witness.reference.effective_target)
    assert "machine_identity" in parked.observation
    assert "provider_identity" not in parked.observation and "child" not in parked.observation
    launcher.activate(witness.task, witness.reference, parked)

    def wait_for(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError("real CPU parent/child did not progress")

    wait_for(witness.parent_started.exists)
    parent = owner_client.get_task(witness.task_id)
    assert parent.state == "running"
    token = witness.daemon.worker_credential_path.read_text().strip()
    worker = WorkspaceClient(witness.daemon.endpoint, token)
    authority = worker.issue_child_authority(
        parent.attempt_id, lease_id=witness.service.store.get_task(witness.task_id)["task"]["lease_token"],
        fence=parent.lease_fence, runtime_epoch=parent.runtime_epoch,
    )["authority"]
    worker.admit_delegated_task(
        authority=authority,
        task={"capability_id": witness.child_record.id, "capability_digest": witness.child_record.capability_digest,
              "input_object_ids": [], "spec": {"inputs": {}},
              "execution_request": {"schema_version": 1, "target": witness.reference.effective_target}},
        idempotency_key="real-machine-child",
    )
    wait_for(witness.child_started.exists)
    with sqlite3.connect(witness.service.store.db_path) as reader:
        rows = reader.execute(
            "SELECT id, capability, status, attempt_id, executor_id FROM tasks "
            "WHERE status='running' ORDER BY capability"
        ).fetchall()
        reservations = reader.execute(
            "SELECT task_id, resource_key FROM reservations WHERE released_at IS NULL"
        ).fetchall()
    assert [(row[1], row[2]) for row in rows] == [
        ("cpu.activation", "running"), ("cpu.activation.child", "running"),
    ]
    assert len({row[3] for row in rows}) == 2 and all(row[3] for row in rows)
    assert {row[4] for row in rows} == {host_bootstrap.PACK_HOST_ACTOR}
    assert parked.handle.process.poll() is None
    child_id = next(row[0] for row in rows if row[1] == "cpu.activation.child")
    assert {resource for task_id, resource in reservations if task_id == witness.task_id} == {"cpu-parent"}
    assert {resource for task_id, resource in reservations if task_id == child_id} == {"cpu-child"}
    witness.child_gate.write_text("release child")
    wait_for(lambda: owner_client.get_task(child_id).state == "succeeded")
    assert owner_client.get_task(witness.task_id).state == "running"
    witness.parent_gate.write_text("release parent")
    wait_for(lambda: owner_client.get_task(witness.task_id).state == "succeeded")
    with sqlite3.connect(witness.service.store.db_path) as reader:
        assert reader.execute(
            "SELECT COUNT(*) FROM reservations WHERE task_id IN (?, ?) AND released_at IS NULL",
            (witness.task_id, child_id),
        ).fetchone()[0] == 0
    assert launcher.activation_state == "active"


@pytest.mark.parametrize("failure", ["callback", "grant", "session", "cleanup"])
def test_resident_failure_fences_and_cleans_only_exact_host(resident_host, monkeypatch, failure):
    witness = resident_host
    parked = witness.launcher.park(witness.reference, target=witness.reference.effective_target)
    original_acknowledge = witness.preparer.acknowledge
    original_abort = witness.preparer.abort
    aborts = []
    abort_failures = []
    callbacks = []
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        def acknowledge(handle, grant, *, accept):
            def callback(received, process):
                callbacks.append(received)
                if failure in {"callback", "cleanup"}:
                    raise OSError("acceptance deliberately rejected")
                accept(received, process)
                if failure == "session":
                    monkeypatch.setattr(witness.service, "runtime_session_id", "stale-runtime-session")
            return original_acknowledge(handle, grant, accept=callback)

        witness.preparer.acknowledge = acknowledge
        if failure == "grant":
            read = generic_host._read_activation_frame

            def mismatch(control):
                value = read(control)
                if value["version"] == "astrid.local-worker-activation-request/v1":
                    value["grant"]["executor_incarnation"] = "foreign"
                return value

            monkeypatch.setattr(generic_host, "_read_activation_frame", mismatch)

        def abort(handle):
            aborts.append(handle)
            try:
                if failure == "cleanup":
                    raise OSError("exact process cleanup could not be confirmed")
                original_abort(handle)
            except Exception as exc:
                abort_failures.append({"type": type(exc).__name__, "message": str(exc)})
                # This fixture owns the actual Popen object. Channel closure
                # causes rejection to exit; wait/reap it without weakening
                # identity checks or signalling a different process.
                try:
                    handle.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    raise exc  # Still live and unverified: keep cleanup unknown.
                if failure == "cleanup":
                    raise  # Preserve the deliberately recorded uncertainty.
                original_abort(handle)  # Reaped child; remove only its exact marker.

        witness.preparer.abort = abort
        with pytest.raises((ConflictError, OSError, host_bootstrap.PackHostBootstrapError)):
            witness.launcher.activate(witness.task, witness.reference, parked)
        assert aborts == [parked.handle]
        assert len(callbacks) == (0 if failure == "grant" else 1)
        assert witness.daemon.credentials.actor_metadata(witness.reference.executor_id) is None
        assert witness.service._latest_remote_activation(witness.task_id) is None
        assert witness.launcher.activation_state == ("unknown" if failure == "cleanup" else "inactive")
        if witness.launcher.activation_state == "unknown":
            assert abort_failures
        assert unrelated.poll() is None
        parked.handle.process.wait(timeout=2)
        assert parked.handle.process.poll() is not None
        assert parked.handle.control.fileno() == -1
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


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
