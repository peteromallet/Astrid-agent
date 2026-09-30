"""CPU end-to-end C12 seam: Runtime -> Worker -> GenericPackHost -> child.

The Worker neutral preflight is exercised with a deterministic CPU fact probe;
the public launcher, Runtime HTTP authority, host registration, claim loop,
inline CAS settlement, cancellation, and Worker-owned process cleanup remain
real.  This test is deliberately not GPU acceptance.
"""

from __future__ import annotations

import hashlib
import base64
import copy
import json
import multiprocessing
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any, Mapping

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_ROOT = Path(
    os.environ.get("BANODOCO_RUNTIME_CHECKOUT")
    or WORKSPACE_ROOT
    / "banodoco-workspace-runtime"
    / ".otto"
    / "worktrees"
    / "h3-runtime-contract-20260927"
).resolve()
WORKER_ROOT = Path(
    os.environ.get("REIGH_WORKER_CHECKOUT")
    or WORKSPACE_ROOT / "reigh-worker"
).resolve()
sys.path.insert(0, str(RUNTIME_ROOT))
sys.path.insert(0, str(WORKER_ROOT))

from banodoco_workspace_client.generated import (  # noqa: E402
    ApiError,
    WorkspaceClient as GeneratedWorkspaceClient,
)
from runtime_protocol.remote_worker_activation import (  # noqa: E402
    QualifiedRemoteWorkerLauncher,
    _digest as _activation_digest,
)
from runtime_protocol.remote_worker_deployment import (  # noqa: E402
    ArtifactReference,
    DeploymentReference,
    deployment_binding_from_task,
)
from runtime_protocol.daemon import RuntimeDaemon  # noqa: E402
from tests.helpers.runtime import initialize_runtime_realm
from source.runtime import supervisor  # noqa: E402
from source.runtime.worker import preflight  # noqa: E402
from astrid.core.execution.generic_host import GenericPackHost, RuntimeProtocolClient  # noqa: E402
from astrid.core.execution.guards import ExecutionGuardPolicy  # noqa: E402
from astrid.core.gateway.dispatch import compose_profile_handoff  # noqa: E402
from astrid.sdk.workspace_client import WorkspaceClient  # noqa: E402


_RuntimeDaemon = RuntimeDaemon


def RuntimeDaemon(root, *args, **kwargs):
    initialize_runtime_realm(root)
    return _RuntimeDaemon(root, *args, **kwargs)


FIXTURE_PACK = Path(__file__).parents[1] / "fixtures" / "c12_cpu_pack"
# These are the post-T7 composition pins.  Keeping them explicit makes the
# CPU journey fail closed when a dependency checkout drifts from the reviewed
# composition instead of silently testing another tree.
PINNED_RUNTIME_COMMIT = "a278cd460018976940ae21fc2cad563a29a29649"
PINNED_WORKER_COMMIT = "efc2276d30c22ac0cbe54f735a1129cef79583ef"


def _assert_pinned_dependency_heads() -> None:
    for checkout, expected in (
        (RUNTIME_ROOT, PINNED_RUNTIME_COMMIT),
        (WORKER_ROOT, PINNED_WORKER_COMMIT),
    ):
        observed = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        assert observed == expected, f"dependency checkout drifted: {checkout} {observed} != {expected}"
        tracked = subprocess.run(
            ["git", "-C", str(checkout), "diff", "--quiet", "HEAD", "--"],
            check=False,
        )
        assert tracked.returncode == 0, f"dependency checkout has tracked changes: {checkout}"
        untracked_python = subprocess.check_output(
            [
                "git",
                "-C",
                str(checkout),
                "ls-files",
                "--others",
                "--exclude-standard",
                "--",
                "*.py",
            ],
            text=True,
        ).strip()
        assert not untracked_python, (
            f"dependency checkout has untracked executable Python: {checkout}: "
            f"{untracked_python}"
        )


def _worker_entry(config: supervisor.HostLaunchConfig, environ: Mapping[str, str]) -> None:
    """Run the public Worker launcher in a separate process.

    The only CPU seam is the neutral GPU fact probe. All discovery, Runtime
    identity, Worker readiness publication, host registration, lease loop,
    settlement and cleanup use the production code path.
    """

    preflight._probe_gpu = lambda: {
        "uuid": "cpu-fixture-gpu",
        "name": "CPU fixture probe",
        "driver": "fixture-driver",
        "cuda": "fixture-cuda",
        "vram_bytes": 1,
    }
    code = supervisor.launch_generic_pack_host(
        config,
        environ=dict(environ),
        enforce_readiness=True,
    )
    raise SystemExit(code)


def _manifest(root: Path, filename: str, content: bytes) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    payload = root / filename
    payload.write_bytes(content)
    manifest = root.parent / f"{root.name}.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": filename,
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return root, manifest


def _facts(tmp_path: Path, source_checkout: Path, python: Path) -> dict[str, str]:
    engine_lock = tmp_path / "engine.lock"
    engine_lock.write_text("c12-engine-lock\n", encoding="utf-8")
    model_root, model_manifest = _manifest(tmp_path / "models", "model.bin", b"c12-model")
    node_root, node_manifest = _manifest(tmp_path / "nodes", "node.py", b"c12-node")
    scratch = tmp_path / "scratch"
    cas = tmp_path / "cas"
    output = tmp_path / "output"
    scratch.mkdir()
    cas.mkdir()
    output.mkdir()
    return {
        "REIGH_INTERPRETER": str(python),
        "REIGH_ENGINE_INTERPRETER": str(python),
        "REIGH_RUNTIME_LOCK_PATH": str(WORKER_ROOT / "uv.lock"),
        "REIGH_ENGINE_LOCK_PATH": str(engine_lock),
        "REIGH_MODEL_ROOT": str(model_root),
        "REIGH_MODEL_MANIFEST_PATH": str(model_manifest),
        "REIGH_CUSTOM_NODE_ROOT": str(node_root),
        "REIGH_CUSTOM_NODE_MANIFEST_PATH": str(node_manifest),
        "REIGH_SCRATCH_ROOT": str(scratch),
        "REIGH_CAS_ROOT": str(cas),
        "REIGH_OUTPUT_ROOT": str(output),
    }


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _inventory_sources() -> dict[str, list[str]]:
    payload = json.loads(
        (Path(__file__).with_name("c12_cpu_case_inventory.json")).read_text(
            encoding="utf-8"
        )
    )
    return {
        str(case): list(sources)
        for case, sources in payload["evidence_sources"].items()
    }


def _mutation_data(value: Any) -> Any:
    if isinstance(value, Mapping) and "data" in value and "receipt" in value:
        return value["data"]
    return value


def _task_state(client: WorkspaceClient, task_id: str) -> tuple[str, Any]:
    task = client.get_task(task_id)
    return str(_value(task, "state", "")), task


def _wait_state(client: WorkspaceClient, task_id: str, expected: set[str], timeout: float = 30.0) -> Any:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        state, task = _task_state(client, task_id)
        last = state
        if state in expected:
            return task
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} did not reach {sorted(expected)}; last={last}")


def _wait_file(path: Path, process: multiprocessing.Process, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        if not process.is_alive():
            raise AssertionError(f"Worker exited before readiness: exitcode={process.exitcode}")
        time.sleep(0.1)
    raise AssertionError(f"Worker readiness file was not published: {path}")


def _stop_worker(process: multiprocessing.Process, state_file: Path) -> dict[str, Any]:
    if process.is_alive():
        process.terminate()
    process.join(timeout=30)
    if process.is_alive():
        process.kill()
        process.join(timeout=10)
    assert not process.is_alive()
    assert state_file.is_file()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["status"] == "exited"
    return state


def _wait_pid_absent(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        state = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode == 0 and state.startswith("Z"):
            return
        if result.returncode == 1 and not state and not stderr:
            return
        if result.returncode != 0 or not state:
            raise AssertionError(
                f"process observation failed for {pid}: returncode={result.returncode} stderr={stderr!r}"
            )
        time.sleep(0.1)
    raise AssertionError(f"owned CPU child {pid} remained live: {state!r}")


def _wait_port_available(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = socket.socket()
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
            probe.listen(1)
            return
        except OSError as exc:
            if exc.errno not in {48, 98, 10048}:  # EADDRINUSE on macOS/Linux/Windows
                raise AssertionError(f"port observation failed for {port}: {exc}") from exc
        finally:
            probe.close()
        time.sleep(0.1)
    raise AssertionError(f"owned CPU child port {port} remained bound")


_AUTHORITY_EXECUTOR = "h3-cpu-authority-worker"
_AUTHORITY_TARGET = {
    "kind": "runpod",
    "pod_id": "cpu-fixture-pod",
    "provider_account_ref": "cpu-fixture-account",
}
_PARENT_CAPABILITY = "h3_av.transform"
_VERIFY_CAPABILITY = "h3_av.verify"
_PUBLICATION_CAPABILITY = "h3_av.publication_finalizer"


def _capability_digest(capability_id: str) -> str:
    return "sha256:" + hashlib.sha256(capability_id.encode("utf-8")).hexdigest()


def _mutation_payload(value: Any) -> Mapping[str, Any]:
    payload = _value(value, "data", value)
    assert isinstance(payload, Mapping), payload
    return payload


def _task_id(value: Any) -> str:
    payload = _mutation_payload(value)
    nested = payload.get("task")
    candidate = payload.get("task_id") or (
        nested.get("id") if isinstance(nested, Mapping) else None
    )
    assert isinstance(candidate, str) and candidate
    return candidate


def _attempt_settlement(claim: Any, outputs: list[Mapping[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "lease_id": str(_value(claim, "lease_id")),
        "fence": int(_value(claim, "fence")),
        "runtime_epoch": int(_value(claim, "runtime_epoch")),
        "outputs": [dict(row) for row in outputs],
        **extra,
    }


def _authority_reference(tmp_path: Path, daemon: RuntimeDaemon, task_id: str) -> DeploymentReference:
    service = daemon.service
    task = service._task_resource(service.store.get_task(task_id))
    binding = deployment_binding_from_task(task)
    executable = ArtifactReference(
        "python",
        (tmp_path / "python").resolve(),
        "sha256:" + "2" * 64,
    )
    target = binding.placement.effective_target
    return DeploymentReference(
        deployment_id="cpu-authority-deployment",
        revision="cpu-authority-revision",
        task_id=task_id,
        run_id=binding.admission_identity.run_id,
        target_ref="runpod:cpu-fixture-pod",
        effective_target_ref="runpod:cpu-fixture-pod",
        executable=executable,
        dependency_closure=(executable,),
        source_closure_digest="sha256:" + "3" * 64,
        data_root=(tmp_path / "data").resolve(),
        support_root=(tmp_path / "data" / "runtime").resolve(),
        runtime_endpoint=str(daemon.endpoint),
        runtime_instance_id=daemon.instance_id,
        runtime_epoch=int(service.health()["runtime_epoch"]),
        runtime_schema_digest="sha256:" + "4" * 64,
        model_root=(tmp_path / "models").resolve(),
        capacity=2,
        session_ref="cpu-authority-session",
        session_config_digest="sha256:" + "5" * 64,
        output_root=(tmp_path / "outputs").resolve(),
        credential_ref=str(daemon.credentials.path_for(_AUTHORITY_EXECUTOR)),
        executor_id=_AUTHORITY_EXECUTOR,
        boot_manifest_path=(tmp_path / "data" / "runtime" / "boot.json").resolve(),
        boot_manifest_hash="sha256:" + "6" * 64,
        readiness_profile_path=(tmp_path / "data" / "runtime" / "ready.json").resolve(),
        readiness_profile_hash="sha256:" + "7" * 64,
        admission_identity=binding.admission_identity,
        capability_identity=binding.capability_identity,
        input_bindings=binding.input_bindings,
        original_target=binding.placement.original_target,
        effective_target=target,
        placement_version=binding.placement.placement_version,
        recovery_decision_digest=binding.placement.recovery_decision_digest,
        execution_target=target,
    )


def _authority_observation(reference: DeploymentReference, daemon: RuntimeDaemon) -> dict[str, Any]:
    service = daemon.service
    return {
        "target": dict(reference.effective_target),
        "provider_identity": {
            "account_ref": reference.effective_target["provider_account_ref"],
            "pod_id": reference.effective_target["pod_id"],
            "fixture": True,
        },
        "process": {
            "pid": 12345,
            "birth_id": "cpu-fixture-host-birth",
            "pgid": 12345,
            "sid": 12345,
        },
        "child": {
            "attached": True,
            "birth_id": "cpu-fixture-child-birth",
            "lanes": ["orchestration", "executor"],
        },
        "runtime_instance_id": reference.runtime_instance_id,
        "runtime_epoch": reference.runtime_epoch,
        "runtime_session_id": service.runtime_session_id,
        "source_closure_digest": reference.source_closure_digest,
        "dependency_closure_digest": _activation_digest(
            [
                {"name": item.name, "path": str(item.path), "digest": item.digest}
                for item in reference.dependency_closure
            ]
        ),
        "model_root": str(reference.model_root),
        "session_ref": reference.session_ref,
        "data_root": str(reference.data_root),
        "support_root": str(reference.support_root),
        "capacity": reference.capacity,
        "model_inventory_digest": "sha256:" + "8" * 64,
        "session_config_digest": reference.session_config_digest,
    }


class _CpuAuthorityPreparer:
    """Fake only provider/process I/O; Runtime credential state remains real."""

    def __init__(self, daemon: RuntimeDaemon) -> None:
        self.daemon = daemon
        self.disabled_claim_rejected = False
        self.calls: list[str] = []

    def prepare(self, launch: Any) -> object:
        self.calls.append("prepare")
        return {"launch": launch}

    def acknowledge(self, _handle: object, grant: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append("private_ack")
        token = Path(str(grant["credential_file"])).read_text(encoding="utf-8").strip()
        disabled = GeneratedWorkspaceClient(str(self.daemon.endpoint), token)
        try:
            disabled.claim_task(
                executor_id=_AUTHORITY_EXECUTOR,
                capability_ids=[_PARENT_CAPABILITY],
                idempotency_key="disabled-before-private-ack",
                runtime_epoch=int(self.daemon.service.health()["runtime_epoch"]),
                target=_AUTHORITY_TARGET,
            )
        except ApiError as exc:
            assert exc.status in {401, 403}
            self.disabled_claim_rejected = True
        else:
            raise AssertionError("disabled remote credential claimed before activation")
        return {
            "activation_id": grant["activation_id"],
            "executor_incarnation": grant["executor_incarnation"],
            "evidence_digest": grant["evidence_digest"],
        }

    def abort(self, _handle: object) -> None:
        self.calls.append("abort")


class _CpuAuthorityInspector:
    def __init__(self, observation: Mapping[str, Any]) -> None:
        self.observation = dict(observation)
        self.calls = 0

    def observe(self, _handle: object) -> Mapping[str, Any]:
        self.calls += 1
        return copy.deepcopy(self.observation)


@pytest.mark.timeout(60)
def test_cpu_remote_authority_claim_and_verified_publication(tmp_path: Path) -> None:
    """C3/C10 plus the canonical authority/publication subset of C11.

    Provider/process observation is an explicit CPU fake. Task admission,
    qualified credential state, claim/fence authority, delegated stage
    lineage, CAS settlement, generation publication, and public readback are
    the pinned Runtime implementation and Astrid's generated client.
    """

    _assert_pinned_dependency_heads()
    support_root = (tmp_path / "support").resolve()
    daemon = RuntimeDaemon(
        tmp_path / "realm",
        support_root=support_root,
        production_worker_credentials=True,
    ).start()
    try:
        owner = GeneratedWorkspaceClient(str(daemon.endpoint), daemon.token)
        project_result = owner.create_project(
            "CPU authority publication",
            slug="cpu-authority-publication",
            idempotency_key="cpu-authority-project",
        )
        project = _mutation_payload(project_result)
        project_id = str(project.get("project_id") or project.get("id"))
        assert project_id

        capability_ids = (
            _PARENT_CAPABILITY,
            _VERIFY_CAPABILITY,
            _PUBLICATION_CAPABILITY,
        )
        digests = {name: _capability_digest(name) for name in capability_ids}
        for capability_id in capability_ids:
            owner.register_capability(
                capability_id,
                digests[capability_id],
                idempotency_key=f"register-{capability_id}",
            )
        owner.register_executor(
            {
                "executor_id": _AUTHORITY_EXECUTOR,
                "capabilities": list(capability_ids),
                "max_concurrency": 2,
            },
            idempotency_key="register-cpu-authority-worker",
        )

        effect = {
            "effect_type": "generation.publish_v1",
            "target_id": project_id,
            "payload": {
                "version": 1,
                "modality": "video",
                "generation_type": "h3_av_verified_cpu_fixture",
                "metadata": {"boundary": "cpu_fixture", "provider_execution": False},
                "partial_success_policy": "reject",
                "groups": [
                    {
                        "group_key": "main",
                        "selectors": [
                            {
                                "selector": "main-0",
                                "ordinal": 0,
                                "variant_key": "original",
                                "output_port": "verified_candidate",
                            }
                        ],
                    }
                ],
            },
        }
        stages = [
            {
                "name": "verify",
                "capability_id": _VERIFY_CAPABILITY,
                "capability_digest": digests[_VERIFY_CAPABILITY],
                "target": _AUTHORITY_TARGET,
                "inputs": [],
            },
            {
                "name": "publish",
                "capability_id": _PUBLICATION_CAPABILITY,
                "capability_digest": digests[_PUBLICATION_CAPABILITY],
                "target": _AUTHORITY_TARGET,
                "inputs": [
                    {
                        "name": "verified_candidate",
                        "producer_stage": "verify",
                        "output_port": "verified_candidate",
                    }
                ],
            },
        ]
        admitted = owner.admit_task(
            capability_id=_PARENT_CAPABILITY,
            capability_digest=digests[_PARENT_CAPABILITY],
            input_object_ids=[],
            project_id=project_id,
            spec={"inputs": {}},
            execution_request={
                "schema_version": 1,
                "target": _AUTHORITY_TARGET,
                "inputs": [],
            },
            child_delegation={
                "capabilities": [
                    {
                        "capability_id": capability_id,
                        "capability_digest": digests[capability_id],
                    }
                    for capability_id in (_VERIFY_CAPABILITY, _PUBLICATION_CAPABILITY)
                ],
                "targets": [_AUTHORITY_TARGET],
                "input_object_ids": [],
                "stages": stages,
                "final_publication": {
                    "stage": "publish",
                    "verify_stage": "verify",
                    "verify_output_port": "verified_candidate",
                    "effect": effect,
                },
            },
            idempotency_key="cpu-authority-parent",
        )
        parent_task_id = _task_id(admitted)
        parent_task = daemon.service._task_resource(
            daemon.service.store.get_task(parent_task_id)
        )
        reference = _authority_reference(tmp_path, daemon, parent_task_id)
        preparer = _CpuAuthorityPreparer(daemon)
        inspector = _CpuAuthorityInspector(
            _authority_observation(reference, daemon)
        )
        launcher = QualifiedRemoteWorkerLauncher(
            runtime=daemon.service,
            credentials=daemon.credentials,
            preparer=preparer,
            inspector=inspector,
        )

        waiting = daemon.service.claim_next(
            {
                "executor_id": _AUTHORITY_EXECUTOR,
                "capability_ids": [_PARENT_CAPABILITY],
                "runtime_epoch": int(daemon.service.health()["runtime_epoch"]),
                "target": _AUTHORITY_TARGET,
            },
            idempotency_key="claim-before-activation",
            identity={"actor": _AUTHORITY_EXECUTOR, "scopes": ["worker:execute"]},
        )
        assert waiting["waiting_reason"] in {
            "execution_binding_missing",
            "remote_activation_missing",
        }
        assert daemon.service.store.conn.execute(
            "SELECT COUNT(*) FROM attempts WHERE task_id=?", (parent_task_id,)
        ).fetchone()[0] == 0

        parked = launcher.park(reference, target=_AUTHORITY_TARGET)
        qualification = launcher.activate(parent_task, reference, parked)
        launcher.assert_fresh(parent_task, reference, parked, qualification)
        assert preparer.calls == ["prepare", "private_ack"]
        assert preparer.disabled_claim_rejected is True
        assert inspector.calls == 5

        worker_token = daemon.credentials.path_for(_AUTHORITY_EXECUTOR).read_text(
            encoding="utf-8"
        ).strip()
        worker = GeneratedWorkspaceClient(str(daemon.endpoint), worker_token)
        foreign = worker.claim_task(
            executor_id=_AUTHORITY_EXECUTOR,
            capability_ids=[_PARENT_CAPABILITY],
            idempotency_key="foreign-target-claim",
            runtime_epoch=int(daemon.service.health()["runtime_epoch"]),
            target={**_AUTHORITY_TARGET, "pod_id": "foreign-pod"},
        )
        assert foreign is None or _value(foreign, "waiting_reason") in {
            "execution_binding_mismatch",
            "remote_activation_missing",
            "target_mismatch",
        }
        parent_claim = worker.claim_task(
            executor_id=_AUTHORITY_EXECUTOR,
            capability_ids=[_PARENT_CAPABILITY],
            idempotency_key="exact-parent-claim",
            runtime_epoch=int(daemon.service.health()["runtime_epoch"]),
            target=_AUTHORITY_TARGET,
        )
        assert _value(parent_claim, "task_id") == parent_task_id
        assert _value(parent_claim, "execution_binding")["actual_target"] == _AUTHORITY_TARGET

        authority = worker.issue_child_authority(
            str(_value(parent_claim, "attempt_id")),
            lease_id=str(_value(parent_claim, "lease_id")),
            fence=int(_value(parent_claim, "fence")),
            runtime_epoch=int(_value(parent_claim, "runtime_epoch")),
        )["authority"]
        verify_admitted = worker.admit_delegated_task(
            authority=authority,
            task={
                "capability_id": _VERIFY_CAPABILITY,
                "capability_digest": digests[_VERIFY_CAPABILITY],
                "stage": "verify",
                "input_refs": [],
                "spec": {"inputs": {}},
                "execution_request": {
                    "schema_version": 1,
                    "target": _AUTHORITY_TARGET,
                },
            },
            idempotency_key="cpu-authority-verify",
        )
        verify_task_id = _task_id(verify_admitted)
        verify_claim = worker.claim_task(
            executor_id=_AUTHORITY_EXECUTOR,
            capability_ids=[_VERIFY_CAPABILITY],
            idempotency_key="cpu-authority-verify-claim",
            runtime_epoch=int(daemon.service.health()["runtime_epoch"]),
            target=_AUTHORITY_TARGET,
        )
        assert _value(verify_claim, "task_id") == verify_task_id

        payload = b"verified CPU H3 candidate"
        object_id = "sha256:" + hashlib.sha256(payload).hexdigest()
        verified_output = {
            "name": "verified_candidate",
            "filename": "verified-candidate.mp4",
            "output_port": "verified_candidate",
            "group_key": "main",
            "variant_key": "original",
            "ordinal": 0,
            "role": "result",
            "is_primary": True,
            "kind": "object",
            "digest": object_id,
            "media_type": "video/mp4",
            "size": len(payload),
            "data_base64": base64.b64encode(payload).decode("ascii"),
            "durability": "durable",
        }
        worker.settle_attempt(
            str(_value(verify_claim, "attempt_id")),
            _attempt_settlement(verify_claim, [verified_output]),
            idempotency_key="cpu-authority-verify-settle",
        )
        verify_outputs, verify_cursor = owner.list_managed_outputs(verify_task_id)
        assert verify_cursor is None and len(verify_outputs) == 1
        verified = verify_outputs[0]
        assert verified.object_id == object_id
        assert verified.generation_id is None

        publish_admitted = worker.admit_delegated_task(
            authority=authority,
            task={
                "capability_id": _PUBLICATION_CAPABILITY,
                "capability_digest": digests[_PUBLICATION_CAPABILITY],
                "stage": "publish",
                "input_refs": [
                    {
                        "name": "verified_candidate",
                        "producer_task_id": verify_task_id,
                        "association_id": verified.association_id,
                        "output_port": "verified_candidate",
                    }
                ],
                "spec": {
                    "inputs": {
                        "verified_candidate": {
                            "object_id": object_id,
                            "digest": object_id,
                            "filename": "verified-candidate.mp4",
                            "required": True,
                        }
                    }
                },
                "execution_request": {
                    "schema_version": 1,
                    "target": _AUTHORITY_TARGET,
                },
            },
            idempotency_key="cpu-authority-publish",
        )
        publish_task_id = _task_id(publish_admitted)
        publish_claim = worker.claim_task(
            executor_id=_AUTHORITY_EXECUTOR,
            capability_ids=[_PUBLICATION_CAPABILITY],
            idempotency_key="cpu-authority-publish-claim",
            runtime_epoch=int(daemon.service.health()["runtime_epoch"]),
            target=_AUTHORITY_TARGET,
        )
        assert _value(publish_claim, "task_id") == publish_task_id
        publication_settlement = _attempt_settlement(
            publish_claim,
            [verified_output],
            effect=effect,
            result={"boundary": "cpu_fixture", "provider_execution": False},
        )
        first_settlement = worker.settle_attempt(
            str(_value(publish_claim, "attempt_id")),
            publication_settlement,
            idempotency_key="cpu-authority-publish-settle",
        )
        replayed_settlement = worker.settle_attempt(
            str(_value(publish_claim, "attempt_id")),
            publication_settlement,
            idempotency_key="cpu-authority-publish-settle",
        )
        assert _mutation_payload(first_settlement) == _mutation_payload(replayed_settlement)

        worker.settle_attempt(
            str(_value(parent_claim, "attempt_id")),
            _attempt_settlement(parent_claim, []),
            idempotency_key="cpu-authority-parent-settle",
        )

        parent_readback = owner.get_task(parent_task_id)
        publication_readback = owner.get_task(publish_task_id)
        assert parent_readback.state == "succeeded"
        assert publication_readback.state == "succeeded"
        assert publication_readback.attempt_id == str(_value(publish_claim, "attempt_id"))
        publish_outputs, publish_cursor = owner.list_managed_outputs(publish_task_id)
        assert publish_cursor is None and len(publish_outputs) == 1
        published = publish_outputs[0]
        assert published.task_id == publish_task_id
        assert published.attempt_id == publication_readback.attempt_id
        assert published.output_port == "verified_candidate"
        assert published.object_id == verified.object_id == object_id
        assert published.generation_id is not None
        assert bytes(owner.get_object(object_id).data) == payload

        generations, generation_cursor = owner.list_generations(project_id)
        assert generation_cursor is None and len(generations) == 1
        generation = generations[0]
        assert generation.generation_id == published.generation_id
        assert generation.source_task_id == publish_task_id
        variants, variant_cursor = owner.list_variants(generation.generation_id)
        assert variant_cursor is None and len(variants) == 1
        variant = variants[0]
        assert variant.object_id == object_id
        assert variant.metadata["provenance"]["task_id"] == publish_task_id
        assert variant.metadata["provenance"]["attempt_id"] == publication_readback.attempt_id
        assert published.provenance["selector"] == "main-0"
    finally:
        daemon.stop()


def test_c12_observation_helpers_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: type(
            "Result", (), {"returncode": 2, "stdout": "", "stderr": "permission denied"}
        )(),
    )
    with pytest.raises(AssertionError, match="process observation failed"):
        _wait_pid_absent(12345, timeout=0.1)

    occupied = socket.socket()
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    port = occupied.getsockname()[1]
    try:
        with pytest.raises(AssertionError, match="remained bound"):
            _wait_port_available(port, timeout=0.1)
    finally:
        occupied.close()


@pytest.mark.timeout(60)
def test_c12_deadline_contains_child_and_terminalizes_runtime(tmp_path: Path) -> None:
    support_root = (tmp_path / "support").resolve()
    daemon = RuntimeDaemon(
        tmp_path / "realm",
        support_root=support_root,
        production_worker_credentials=True,
    ).start()
    host: GenericPackHost | None = None
    try:
        owner = WorkspaceClient(daemon.endpoint, daemon.token)
        worker_client = RuntimeProtocolClient(
            daemon.endpoint,
            Path(daemon.worker_credential_path).read_text(encoding="utf-8").strip(),
        )
        host = GenericPackHost(
            pack_roots=[FIXTURE_PACK],
            client=worker_client,
            execution_policy=ExecutionGuardPolicy(
                evidence_cap_bytes=1024,
                deadline_seconds=1.0,
            ),
        )
        records = {record.id: record for record in host.discover()}
        host.preflight()
        host.register()
        slow_pid_file = tmp_path / "deadline-child.pid"
        slow_port_file = tmp_path / "deadline-child.port"
        mutation = owner.admit_task(
            capability_id="c12_cpu.slow",
            capability_digest=records["c12_cpu.slow"].capability_digest,
            input_object_ids=[],
            idempotency_key="c12-runtime-deadline",
            spec={
                "inputs": {
                    "marker": str(slow_pid_file),
                    "port_marker": str(slow_port_file),
                }
            },
        )
        task_id = str(_value(_mutation_data(mutation), "task_id"))
        outcome = host.claim_once()
        assert outcome is not None
        assert _value(outcome, "status") == "failed"
        failed = _wait_state(owner, task_id, {"failed"})
        assert _value(failed, "state") == "failed"
        assert slow_pid_file.is_file()
        assert slow_port_file.is_file()
        _wait_pid_absent(int(slow_pid_file.read_text(encoding="utf-8")))
        _wait_port_available(int(slow_port_file.read_text(encoding="utf-8")))
    finally:
        if host is not None:
            host.shutdown()
        daemon.stop()


def _start_worker(
    *,
    daemon: RuntimeDaemon,
    support_root: Path,
    source_checkout: Path,
    facts: Mapping[str, str],
    suffix: str,
) -> tuple[multiprocessing.Process, Path, Path, dict[str, Any]]:
    host_root = support_root / f"astrid-host-{suffix}"
    host_root.mkdir(parents=True, exist_ok=True)
    boot_manifest = host_root / "boot-manifest.json"
    handoff = compose_profile_handoff(boot_manifest, support_root=support_root)
    ready_file = support_root / f"host-ready-{suffix}.json"
    state_file = support_root / f"worker-state-{suffix}.json"
    config = supervisor.HostLaunchConfig(
        # Preserve the venv entrypoint lexically.  ``Path.resolve()`` follows
        # the macOS venv symlink back to the system interpreter and would drop
        # the prepared YAML/runtime dependencies from the host child.
        host_python=Path(sys.executable),
        source_checkout=source_checkout.resolve(),
        pack_root=FIXTURE_PACK.resolve(),
        runtime_endpoint=str(daemon.endpoint),
        credential_file=Path(daemon.worker_credential_path).resolve(),
        support_root=support_root.resolve(),
        runtime_instance_id=daemon.instance_id,
        ready_file=ready_file.resolve(),
        state_file=state_file.resolve(),
        boot_manifest_path=boot_manifest.resolve(),
        boot_manifest_hash=str(handoff["sha256"]),
    )
    environ = dict(os.environ)
    environ.update(facts)
    environ["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    environ["PYTHONPATH"] = os.pathsep.join((str(WORKER_ROOT), str(RUNTIME_ROOT), str(source_checkout)))
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_worker_entry, args=(config, environ), name=f"c12-worker-{suffix}")
    process.start()
    readiness = _wait_file(ready_file, process)
    assert readiness["status"] == "ready"
    assert readiness["executor_id"] == "astrid-pack-host"
    assert "c12_cpu.echo" in readiness["ready_capabilities"]
    assert "c12_cpu.slow" in readiness["ready_capabilities"]
    return process, ready_file, state_file, readiness


@pytest.mark.timeout(120)
def test_c12_cpu_runtime_worker_host_harness(tmp_path: Path) -> None:
    source_checkout = Path(__file__).parents[2].resolve()
    support_root = (tmp_path / "support").resolve()
    daemon = RuntimeDaemon(
        tmp_path / "realm",
        support_root=support_root,
        production_worker_credentials=True,
    ).start()
    worker: multiprocessing.Process | None = None
    try:
        owner = WorkspaceClient(daemon.endpoint, daemon.token)
        _assert_pinned_dependency_heads()
        inventory_sources = _inventory_sources()
        runtime_cases = {
            "cold_success",
            "cancel_confirmed",
            "deadline_contained",
            "restart_replaces_incarnation",
            "stale_fence_rejected",
            "cas_settlement_exactly_once",
            "cleanup_releases_owned_session",
        }
        assert runtime_cases <= set(inventory_sources)
        assert all("runtime_worker" in inventory_sources[case] for case in runtime_cases)
        records = {
            record.id: record
            for record in GenericPackHost(pack_roots=[FIXTURE_PACK]).discover()
        }
        assert set(records) == {"c12_cpu.echo", "c12_cpu.slow"}
        facts = _facts(tmp_path, source_checkout, Path(sys.executable).resolve())

        worker, _ready, state_file, readiness = _start_worker(
            daemon=daemon,
            support_root=support_root,
            source_checkout=source_checkout,
            facts=facts,
            suffix="first",
        )

        success = owner.admit_task(
            capability_id="c12_cpu.echo",
            capability_digest=records["c12_cpu.echo"].capability_digest,
            input_object_ids=[],
            idempotency_key="c12-runtime-success",
            spec={"inputs": {}},
        )
        success_id = str(_value(_mutation_data(success), "task_id"))
        completed = _wait_state(owner, success_id, {"succeeded"})
        result = _value(completed, "result", {})
        guard_receipt = _value(result, "execution_guards", {})
        assert "scratch" not in guard_receipt
        assert guard_receipt["evidence"]["cap_bytes"] == 2 * 1024**3
        assert guard_receipt["deadline_seconds"] == 3600.0
        assert guard_receipt["warm_expectation"] == {
            "warm_reuse_expected": False,
            "listener_port": None,
            "port_independent": True,
        }
        outputs = _value(result, "outputs", [])
        assert isinstance(outputs, list) and len(outputs) == 1
        output = outputs[0]
        digest = str(_value(output, "digest"))
        assert digest == "sha256:" + hashlib.sha256(b"c12-runtime-output").hexdigest()
        stored = owner.get_object(digest)
        assert bytes(_value(stored, "data")) == b"c12-runtime-output"
        success_attempt_id = str(_value(completed, "attempt_id"))
        success_attempt = daemon.service.store.conn.execute(
            "SELECT lease_id, fence, runtime_epoch FROM attempts WHERE id=?",
            (success_attempt_id,),
        ).fetchone()
        assert success_attempt is not None
        duplicate_output = b"duplicate-must-not-publish"
        duplicate_digest = "sha256:" + hashlib.sha256(duplicate_output).hexdigest()
        duplicate_client = RuntimeProtocolClient(
            daemon.endpoint,
            Path(daemon.worker_credential_path).read_text(encoding="utf-8").strip(),
        )
        with pytest.raises(Exception, match="stale|settled|cancelled"):
            duplicate_client.generated.settle_attempt(
                success_attempt_id,
                {
                    "attempt_id": success_attempt_id,
                    "lease_id": success_attempt["lease_id"],
                    "fence": int(success_attempt["fence"]),
                    "runtime_epoch": int(success_attempt["runtime_epoch"]),
                    "outputs": [{
                        "name": "answer",
                        "kind": "object",
                        "media_type": "text/plain",
                        "digest": duplicate_digest,
                        "size": len(duplicate_output),
                        "data_base64": base64.b64encode(duplicate_output).decode("ascii"),
                    }],
                    "result": {"duplicate_replay": True},
                },
                idempotency_key="c12-success-duplicate-replay",
            )
        events = daemon.service.events(str(_value(completed, "run_id")))
        assert [event.get("event_type", event.get("kind")) for event in events].count("task.completed") == 1
        with pytest.raises(Exception):
            owner.get_object(duplicate_digest)

        slow_pid_file = tmp_path / "slow-child.pid"
        slow_port_file = tmp_path / "slow-child.port"
        slow = owner.admit_task(
            capability_id="c12_cpu.slow",
            capability_digest=records["c12_cpu.slow"].capability_digest,
            input_object_ids=[],
            idempotency_key="c12-runtime-cancel",
            spec={
                "inputs": {
                    "marker": str(slow_pid_file),
                    "port_marker": str(slow_port_file),
                }
            },
        )
        slow_id = str(_value(_mutation_data(slow), "task_id"))
        _wait_state(owner, slow_id, {"running"})
        running_slow = owner.get_task(slow_id)
        slow_attempt_id = str(_value(running_slow, "attempt_id"))
        slow_attempt = daemon.service.store.conn.execute(
            "SELECT lease_id, fence, runtime_epoch FROM attempts WHERE id=?",
            (slow_attempt_id,),
        ).fetchone()
        assert slow_attempt is not None
        _wait_file(slow_pid_file, worker)
        slow_pid = int(slow_pid_file.read_text(encoding="utf-8"))
        _wait_file(slow_port_file, worker)
        slow_port = int(slow_port_file.read_text(encoding="utf-8"))
        port_probe = socket.socket()
        try:
            assert port_probe.connect_ex(("127.0.0.1", slow_port)) == 0
        finally:
            port_probe.close()
        owner.cancel_task(slow_id, idempotency_key="c12-runtime-cancel-request")
        cancelled = _wait_state(owner, slow_id, {"cancelled"})
        assert _value(cancelled, "result") in (None, {})
        _wait_pid_absent(slow_pid)
        _wait_port_available(slow_port)

        first_state = _stop_worker(worker, state_file)
        assert first_state["signals"]
        first_host_pid = int(readiness["pid"])
        with pytest.raises(OSError):
            os.kill(first_host_pid, 0)
        worker = None

        worker, _ready2, state_file2, readiness2 = _start_worker(
            daemon=daemon,
            support_root=support_root,
            source_checkout=source_checkout,
            facts=facts,
            suffix="restart",
        )
        stale_client = RuntimeProtocolClient(
            daemon.endpoint,
            Path(daemon.worker_credential_path).read_text(encoding="utf-8").strip(),
        )
        stale_output = b"stale-must-not-publish"
        stale_digest = "sha256:" + hashlib.sha256(stale_output).hexdigest()
        with pytest.raises(Exception, match="stale|settled|cancelled"):
            stale_client.generated.settle_attempt(
                slow_attempt_id,
                {
                    "attempt_id": slow_attempt_id,
                    "lease_id": slow_attempt["lease_id"],
                    "fence": int(slow_attempt["fence"]),
                    "runtime_epoch": int(slow_attempt["runtime_epoch"]),
                    "outputs": [
                        {
                            "name": "answer",
                            "kind": "object",
                            "media_type": "text/plain",
                            "digest": stale_digest,
                            "size": len(stale_output),
                            "data_base64": base64.b64encode(stale_output).decode("ascii"),
                        }
                    ],
                    "result": {"stale_replay": True},
                },
                idempotency_key="c12-stale-replay",
            )
        cancelled_events = daemon.service.events(str(_value(cancelled, "run_id")))
        assert [event.get("event_type", event.get("kind")) for event in cancelled_events].count("task.completed") == 0
        with pytest.raises(Exception):
            owner.get_object(stale_digest)
        restarted = owner.admit_task(
            capability_id="c12_cpu.echo",
            capability_digest=records["c12_cpu.echo"].capability_digest,
            input_object_ids=[],
            idempotency_key="c12-runtime-restart",
            spec={"inputs": {}},
        )
        restarted_id = str(_value(_mutation_data(restarted), "task_id"))
        restarted_task = _wait_state(owner, restarted_id, {"succeeded"})
        restarted_output = _value(_value(restarted_task, "result", {}), "outputs", [])[0]
        assert _value(restarted_output, "digest") == digest
        second_host_pid = int(readiness2["pid"])
        assert second_host_pid != first_host_pid
        _stop_worker(worker, state_file2)
        with pytest.raises(OSError):
            os.kill(second_host_pid, 0)
        worker = None
    finally:
        if worker is not None:
            _stop_worker(worker, state_file)
        daemon.stop()
