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
from banodoco_workspace_client import ApiError
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
from astrid.core.gateway.dispatch import compose_profile_handoff
from astrid.core.generation.model_root import canonical_model_inventory_digest
from astrid.sdk import host_bootstrap
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
                              "from pathlib import Path; Path('{out}/answer.txt').write_text('accepted')"]},
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
    session.write_text(json.dumps({"session_ref": "cpu-local-session", "capacity": 2}))
    boot = support / "boot-manifest.json"
    handoff = compose_profile_handoff(boot, support_root=support)
    record = GenericPackHost(pack_roots=[pack]).discover()[0]
    realm = initialize_runtime_realm(tmp_path / "realm")
    daemon = RuntimeDaemon(realm, support_root=support, production_worker_credentials=True).start()
    preparer = host_bootstrap._ParkedPackHostPreparer(project_launch, timeout_seconds=10)
    original_abort = preparer.abort
    try:
        service = daemon.service
        service.register_capability({"capability_id": record.id, "definition_digest": record.capability_digest})
        target = {"kind": "machine", "id": socket.gethostname()}
        task_id = service.create_task({
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
            pack_roots=(pack,), ready_file=support / "receiver.ready.json",
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
                              reference=reference, preparer=preparer, inspector=inspector, launcher=launcher)
    finally:
        if preparer._handle is not None:
            original_abort(preparer._handle)
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
    events = witness.service._remote_grant_events(witness.task_id)
    assert [kind for kind, _ in events].count("task.remote_activation_accepted") == 1
    with pytest.raises(ConflictError):
        witness.launcher.activate(witness.task, witness.reference, parked)
    assert witness.daemon.worker_credential_path.read_text().strip() == token
    assert host_bootstrap._host_identity_matches(parked.handle.state)
    witness.preparer.abort(parked.handle)
    assert parked.handle.process.poll() is not None
    assert parked.handle.control.fileno() == -1
    assert not witness.reference.ready_file.exists()


@pytest.mark.parametrize("failure", ["callback", "grant", "session", "cleanup"])
def test_resident_failure_fences_and_cleans_only_exact_host(resident_host, monkeypatch, failure):
    witness = resident_host
    parked = witness.launcher.park(witness.reference, target=witness.reference.effective_target)
    original_acknowledge = witness.preparer.acknowledge
    original_abort = witness.preparer.abort
    aborts = []
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
            if failure == "cleanup":
                raise OSError("exact process cleanup could not be confirmed")
            original_abort(handle)

        witness.preparer.abort = abort
        with pytest.raises((ConflictError, OSError, host_bootstrap.PackHostBootstrapError)):
            witness.launcher.activate(witness.task, witness.reference, parked)
        assert aborts == [parked.handle]
        assert len(callbacks) == (0 if failure == "grant" else 1)
        assert witness.daemon.credentials.actor_metadata(witness.reference.executor_id) is None
        assert witness.service._latest_remote_activation(witness.task_id) is None
        assert witness.launcher.activation_state == ("unknown" if failure == "cleanup" else "inactive")
        assert unrelated.poll() is None
        if failure != "cleanup":
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
