"""Qualification composition for an explicit candidate and isolated H3 lane."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Mapping
from urllib.parse import urlsplit

from astrid.core.execution.generic_host import source_checkout_digest
from astrid.core._shared.boot_manifest import (
    BOOT_MANIFEST_FILENAME,
    load_boot_manifest_hash,
    normalize_sha256_digest,
)
from astrid.core.execution.h3_remote_host import (
    H3RemoteInspector, H3RemotePreparer, _exec, _identity,
    _provider, _ssh,
)
from astrid.core.execution.runpod_deployment import (
    AstridRuntimeTaskAdapter, DeploymentOperationError, QualifiedRunPodDeploymentOwner,
    _owner_result,
)
from runtime_protocol.remote_worker_activation import QualifiedRemoteWorkerLauncher, _digest
from runtime_protocol.remote_worker_deployment import (
    ArtifactReference, DeploymentReference, deployment_binding_from_task,
)
from astrid.sdk.workspace_client import WorkspaceClient


def _lane_path(value: str, root: str, label: str) -> str:
    """Remote paths are lexical POSIX paths; never resolve them on this Mac."""
    if (not isinstance(value, str) or not value.startswith("/")
            or str(PurePosixPath(value)) != value or ".." in PurePosixPath(value).parts
            or "\\" in value or any(ord(char) < 32 for char in value)
            or not PurePosixPath(value).is_relative_to(root)):
        raise DeploymentOperationError(f"{label} escapes the validated lane root")
    return value


@dataclass(frozen=True)
class H3LaneConfig:
    lane: str
    lane_root: str
    release_root: str
    executor_id: str
    session_ref: str
    local_data_root: Path
    expected_pod: str
    runtime_instance_id: str
    runtime_session_id: str
    runtime_epoch: int

    def __post_init__(self) -> None:
        for field in ("lane", "executor_id", "session_ref", "expected_pod"):
            value = getattr(self, field)
            if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) is None:
                raise DeploymentOperationError(f"explicit lane {field} is required")
        # Fixed namespaces prevent a caller from assigning another lane's root.
        if self.lane_root != f"/tmp/astrid-h3-lanes/{self.lane}":
            raise DeploymentOperationError("foreign lane root rejected")
        release_base = f"/workspace/h3-lanes/{self.lane}/releases"
        _lane_path(self.release_root, release_base, "release_root")
        if self.release_root == release_base:
            raise DeploymentOperationError("release_root must name a lane release")
        if (not self.local_data_root.is_absolute()
                or self.local_data_root != self.local_data_root.resolve()):
            raise DeploymentOperationError("explicit real local data root is required")
        if (not isinstance(self.runtime_instance_id, str) or not self.runtime_instance_id.strip()
                or not isinstance(self.runtime_session_id, str) or not self.runtime_session_id.strip()
                or type(self.runtime_epoch) is not int or self.runtime_epoch < 0):
            raise DeploymentOperationError("explicit Runtime instance/session/epoch identity is required")

    def paths(self) -> dict[str, str]:
        root = f"{self.lane_root}/executors/{self.executor_id}/sessions/{self.session_ref}"
        return {"source": root + "/source", "data": root + "/data",
                "support": root + "/data/runtime", "output": root + "/output",
                "credential": root + "/data/runtime/pack-host.token"}


def _release_profile(config: H3LaneConfig) -> dict[str, Any]:
    profile_path = os.environ.get("ASTRID_H3_REMOTE_PROFILE")
    if not profile_path:
        raise DeploymentOperationError("ASTRID_H3_REMOTE_PROFILE must name observed release paths")
    try:
        profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeploymentOperationError("remote release profile is unreadable") from exc
    required = {"readiness_profile_path", "boot_manifest_path", "boot_manifest_hash",
                "session_config_path", "session_ref", "model_root", "release_manifest_path"}
    if not isinstance(profile, dict) or not required.issubset(profile):
        raise DeploymentOperationError("remote release profile is incomplete")
    for key in ("lane", "lane_root", "release_root", "executor_id", "session_ref"):
        if profile.get(key) != getattr(config, key):
            raise DeploymentOperationError(f"remote release profile has foreign {key}")
    for key in required - {"boot_manifest_hash", "session_ref"}:
        _lane_path(profile[key], config.release_root, key)
    if profile["model_root"] != config.release_root + "/models":
        raise DeploymentOperationError("remote profile points outside the lane model release")
    normalize_sha256_digest(profile["boot_manifest_hash"], label="profile boot manifest logical hash")
    return profile


def _remote_file_hashes(claim: Mapping[str, Any], paths: list[str]) -> dict[str, str]:
    client = _ssh(claim)
    try:
        return {path: "sha256:" + _exec(client, ["sha256sum", path]).split()[0] for path in paths}
    finally:
        client.close()


def _remote_boot_manifest_hash(claim: Mapping[str, Any], remote_path: str) -> str:
    client = _ssh(claim)
    try:
        with tempfile.TemporaryDirectory() as directory:
            support_root = Path(directory).resolve(strict=True)
            local = support_root / BOOT_MANIFEST_FILENAME
            with client.open_sftp() as sftp:
                sftp.get(remote_path, str(local))
            digest = load_boot_manifest_hash(local, support_root=support_root)
            return "sha256:" + normalize_sha256_digest(
                digest, label="remote boot manifest logical hash"
            )
    finally:
        client.close()


def _loss_observer(target: Mapping[str, Any]) -> Mapping[str, Any]:
    pod_id = target.get("pod_id")
    if not isinstance(pod_id, str) or not pod_id:
        raise DeploymentOperationError("original target has no pod identity")
    try:
        result = subprocess.run(["runpod-lifecycle", "list", "--json"], capture_output=True,
                                text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentOperationError("provider inventory is unavailable for T12 recovery") from exc
    if result.returncode != 0:
        raise DeploymentOperationError("provider inventory is unavailable for T12 recovery")
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise DeploymentOperationError("provider inventory shape is unknown") from exc
    rows = value if isinstance(value, list) else value.get("pods") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise DeploymentOperationError("provider inventory shape is unknown")
    ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise DeploymentOperationError(f"provider inventory row {index} is invalid")
        aliases = [row.get(key) for key in ("id", "runpod_id") if key in row]
        if not aliases or any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
            raise DeploymentOperationError(f"provider inventory row {index} has no valid pod ID")
        if len(set(aliases)) != 1:
            raise DeploymentOperationError(f"provider inventory row {index} has conflicting pod IDs")
        ids.add(aliases[0])
    if pod_id in ids:
        raise DeploymentOperationError("original pod still exists; T12 replacement is forbidden")
    proof = {"target": dict(target), "provider_pod_ids": sorted(str(item) for item in ids)}
    return {"status": "absent", "no_active_work": True, "target": dict(target),
            "evidence_digest": _digest(proof)}


class _HttpRuntimeOwner:
    """Narrow resident owner facade; no RuntimeService or CredentialStore is made."""

    def __init__(self, transport: Any, health: Mapping[str, Any]):
        self.transport = transport
        self.runtime_session_id = health["runtime_session_id"]
        self.store = SimpleNamespace(_current_runtime_epoch=lambda: health["runtime_epoch"])

    def record_remote_activation(self, task_id: str, qualification: Mapping[str, Any], *, identity: Any):
        return self.transport.record_remote_activation(task_id, qualification)

    def revoke_remote_activation(self, task_id: str, activation_id: str, *, identity: Any):
        return self.transport.revoke_remote_activation(task_id, activation_id)

    def recover_task_placement(self, task_id: str, body: Mapping[str, Any], *,
                               idempotency_key: str, identity: Any):
        return self.transport.recover_task_placement(task_id, body, idempotency_key=idempotency_key)


def default_qualification_factory(*, client: Any, handle_path: Path,
                                  operation_id: str,
                                  lane_config: H3LaneConfig,
                                  source_identity: Mapping[str, Any],
                                  task_id: str, run_id: str,
                                  owner_credential: Path | None = None,
                                  preflight_only: bool = False) -> QualifiedRunPodDeploymentOwner | Mapping[str, Any]:
    """Build the owner path from resident HTTP and exact release evidence.

    The remote profile names existing boot/readiness/session files; absent or
    drifting files stop before the already-claimed pod is activated. It is
    supplied by ASTRID_H3_REMOTE_PROFILE, a local JSON file, and is not a
    claim helper or generated readiness assertion.
    """
    from scripts.run_h3_canonical_on_runpod import assert_product_source_identity

    # Re-measure immediately before the connected boundary; never trust a
    # previously returned dictionary as proof of unchanged executable bytes.
    if not isinstance(source_identity, Mapping) or not source_identity.get("source_root"):
        raise DeploymentOperationError("explicit candidate source identity is required")
    identity = assert_product_source_identity(
        expected_branch=source_identity.get("branch"), expected_head=source_identity.get("head"),
        content_digest=source_identity.get("content_digest"),
        source_root=Path(source_identity["source_root"]),
    )
    if not task_id or not run_id or not operation_id.strip():
        raise DeploymentOperationError("explicit task/run/operation identity is required")
    profile = _release_profile(lane_config)
    if not handle_path.is_file() or handle_path.is_symlink():
        raise DeploymentOperationError("exact existing claim handle is required; a new claim is forbidden")
    try:
        claim = json.loads(handle_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeploymentOperationError("existing claim handle is unreadable") from exc
    if not isinstance(claim, Mapping):
        raise DeploymentOperationError("existing claim handle must be an object")
    _identity(claim)
    if claim["pod_id"] != lane_config.expected_pod:
        raise DeploymentOperationError("existing claim handle names a foreign lane pod")
    transport = client._remote._transport
    endpoint = transport.endpoint
    parsed = urlsplit(endpoint)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
        raise DeploymentOperationError("resident Runtime endpoint must be local loopback")

    # Product credentials are intentionally not allowed to perform worker
    # lifecycle operations.  The old code declared owner/admin in metadata
    # while sending the product bearer token, so the first placement recovery
    # failed only after a real pod had already been claimed and prepared.
    # Authenticate the resident owner explicitly and prove admin access before
    # touching provider state or opening SSH.
    owner_path = owner_credential
    if owner_path is None:
        configured = os.environ.get("ASTRID_H3_OWNER_CREDENTIAL", "").strip()
        owner_path = Path(configured) if configured else None
    if owner_path is None:
        raise DeploymentOperationError(
            "explicit Runtime owner credential is required before RunPod claim"
        )
    owner_path = owner_path.expanduser()
    if not owner_path.is_absolute() or owner_path.is_symlink() or not owner_path.is_file():
        raise DeploymentOperationError("Runtime owner credential is not a regular local file")
    try:
        owner_transport = WorkspaceClient(endpoint, owner_path)
        owner_health = owner_transport.health()
        owner_handshake = owner_transport.handshake(
            "astrid-h3-runpod-owner", "stage1", []
        )
        if not isinstance(owner_health, Mapping) or owner_health.get("status") != "ok":
            raise DeploymentOperationError("resident Runtime owner health is unavailable")
        if not isinstance(owner_handshake, Mapping) or owner_handshake.get("actor_id") != "owner":
            raise DeploymentOperationError("Runtime owner credential did not authenticate as owner")
        product_handshake = getattr(transport, "_last_handshake", None)
        if (
            not isinstance(product_handshake, Mapping)
            or not owner_handshake.get("realm_id")
            or owner_handshake.get("realm_id") != product_handshake.get("realm_id")
        ):
            raise DeploymentOperationError("Runtime owner credential belongs to a different realm")
        # This is the read-only authority gate.  It requires the actual admin
        # scope; a product token fails here before provider inspection/claim.
        doctor = owner_transport.doctor()
        if isinstance(doctor, Mapping) and doctor.get("ok") is False:
            raise DeploymentOperationError("Runtime owner admin authorization failed")
    except DeploymentOperationError:
        raise
    except Exception as exc:
        raise DeploymentOperationError(
            "resident Runtime owner authorization is unavailable before RunPod claim"
        ) from exc

    health = client.health()
    if not isinstance(health, Mapping) or health.get("status") != "ok":
        raise DeploymentOperationError("resident Runtime health is unavailable")
    for key in ("runtime_instance_id", "runtime_session_id", "runtime_epoch"):
        if health.get(key) != getattr(lane_config, key) or owner_health.get(key) != health.get(key):
            raise DeploymentOperationError(f"foreign Runtime {key}")
    if not health.get("schema_digest") or owner_health.get("schema_digest") != health["schema_digest"]:
        raise DeploymentOperationError("foreign Runtime schema identity")
    task = AstridRuntimeTaskAdapter(client).get_task(task_id)
    binding = deployment_binding_from_task(task)
    if binding.admission_identity.task_id != task_id or binding.admission_identity.run_id != run_id:
        raise DeploymentOperationError("Runtime returned a foreign task/run")
    target = binding.placement.effective_target
    if target.get("kind") != "runpod" or target.get("pod_id") != lane_config.expected_pod or not target.get("provider_account_ref"):
        raise DeploymentOperationError("Runtime task has a foreign lane pod or missing provider account identity")
    AstridRuntimeTaskAdapter(client).assert_claim_eligible(task)
    paths = lane_config.paths()
    if preflight_only:
        return {"status": "preclaim_ready", "provider_allocation": False, "activation": False,
                "remote_hashes_verified": False, "task_id": task_id, "run_id": run_id,
                "operation_id": operation_id, "source_identity": identity,
                "lane": lane_config.lane, "lane_root": lane_config.lane_root,
                "release_root": lane_config.release_root, "executor_id": lane_config.executor_id,
                "session_ref": lane_config.session_ref, "paths": paths,
                "local_data_root": str(lane_config.local_data_root)}

    # Retain exact provider and remote hash checks beyond the CPU boundary.
    _provider(claim)
    release_paths = [profile[key] for key in (
        "readiness_profile_path", "boot_manifest_path", "session_config_path", "release_manifest_path",
    )]
    release_python = lane_config.release_root + "/runtime/venv/bin/python"
    release_paths.append(release_python)
    hashes = _remote_file_hashes(claim, release_paths)
    logical_manifest_hash = _remote_boot_manifest_hash(claim, profile["boot_manifest_path"])
    expected_manifest_hash = "sha256:" + normalize_sha256_digest(
        profile["boot_manifest_hash"], label="profile boot manifest logical hash"
    )
    if logical_manifest_hash != expected_manifest_hash:
        raise DeploymentOperationError("remote boot manifest logical hash drifted")
    local_source = Path(identity["source_root"])
    source_digest = "sha256:" + source_checkout_digest(local_source)
    if source_digest.removeprefix("sha256:") != identity["scoped_source_digest"]:
        raise DeploymentOperationError("candidate pack source digest drifted")
    remote_source, data_root, support_root = paths["source"], paths["data"], paths["support"]
    staged_manifest_path = support_root + "/" + BOOT_MANIFEST_FILENAME
    hashes[staged_manifest_path] = hashes[profile["boot_manifest_path"]]
    output_root, credential_file = paths["output"], paths["credential"]
    artifact = ArtifactReference("host", Path(release_python), hashes[release_python])
    manifest_artifact = ArtifactReference("release", Path(profile["release_manifest_path"]),
                                          hashes[profile["release_manifest_path"]])
    closure = (artifact, manifest_artifact)
    dependency_digest = _digest([
        {"name": item.name, "path": str(item.path), "digest": item.digest}
        for item in closure
    ])
    source_closure = _digest({"packs": source_digest, "release": hashes[profile["release_manifest_path"]]})
    owner = _HttpRuntimeOwner(owner_transport, owner_health)

    def control(task_id: str, body: Mapping[str, Any]):
        return owner_transport.control_remote_credential(task_id, body)

    def launch_factory(task: Mapping[str, Any], claim_handle: Mapping[str, Any]):
        _identity(claim_handle)
        if claim_handle != claim:
            raise DeploymentOperationError("qualification received a foreign claim handle")
        binding = deployment_binding_from_task(task)
        target = {**binding.placement.effective_target, "pod_id": claim_handle["pod_id"]}
        if target.get("provider_account_ref") is None:
            raise DeploymentOperationError("admitted target lacks provider account identity")
        channel_id = uuid.uuid4().hex
        env = {
            "PYTHONPATH": ":".join((remote_source, lane_config.release_root + "/runtime/vibecomfy", lane_config.release_root + "/runtime/ComfyUI")),
            "VIBECOMFY_HEADLESS": "1",
            "ASTRID_RUNTIME_INSTANCE_ID": health["runtime_instance_id"],
            "ASTRID_RUNTIME_EPOCH": str(health["runtime_epoch"]),
            "ASTRID_RUNTIME_SESSION_ID": health["runtime_session_id"],
            "ASTRID_SOURCE_CLOSURE_DIGEST": source_closure,
            "ASTRID_DEPENDENCY_CLOSURE_DIGEST": dependency_digest,
            "ASTRID_MODEL_ROOT": profile["model_root"],
            "ASTRID_SESSION_REF": profile["session_ref"],
            "ASTRID_DATA_ROOT": data_root,
            "ASTRID_SUPPORT_ROOT": support_root,
            "ASTRID_SESSION_CONFIG_DIGEST": hashes[profile["session_config_path"]],
            "ASTRID_TARGET_JSON": json.dumps(target, sort_keys=True, separators=(",", ":")),
            "ASTRID_VIBECOMFY_MODELS_ROOT": profile["model_root"],
            "ASTRID_OUTPUT_ROOT": output_root,
            "BANODOCO_LOCAL_DATA_ROOT": data_root,
            "ASTRID_EXECUTION_TARGET_JSON": json.dumps(target, sort_keys=True, separators=(",", ":")),
        }
        argv = [release_python, "-m", "astrid.core.execution.generic_host", "run",
                "--pack-root", remote_source + "/astrid/packs/h3_av",
                "--pack-root", remote_source + "/astrid/packs/vibecomfy",
                "--runtime-endpoint", "http://127.0.0.1:50604",
                "--credential-file", credential_file,
                "--executor-id", lane_config.executor_id, "--max-concurrency", "2",
                "--ready-file", support_root + "/generic-host.ready.json",
                "--support-root", support_root,
                "--source-checkout", remote_source,
                "--source-checkout-digest", source_digest.removeprefix("sha256:"),
                "--runtime-instance-id", health["runtime_instance_id"],
                "--source-inventory-identity", source_digest,
                "--boot-manifest-path", staged_manifest_path,
                "--boot-manifest-hash", logical_manifest_hash,
                "--readiness-profile-path", profile["readiness_profile_path"],
                "--readiness-profile-hash", hashes[profile["readiness_profile_path"]],
                "--register"]
        return {
            "claim": dict(claim_handle), "target": target,
            "local_source": str(local_source), "source_checkout": remote_source,
            "support_root": support_root, "output_root": output_root,
            "remote_tar": remote_source + "/source.tar", "credential_file": credential_file,
            "file_hashes": hashes, "release_manifest_path": profile["release_manifest_path"],
            "manifest_source": profile["boot_manifest_path"],
            "manifest_target": staged_manifest_path,
            "argv": argv, "env": env, "cwd": remote_source,
            "log": support_root + "/generic-host.log",
            "operation_id": operation_id, "channel_id": channel_id,
            "local_runtime_port": parsed.port,
        }

    def reference_factory(task: Mapping[str, Any], claim_handle: Mapping[str, Any]):
        _identity(claim_handle)
        if claim_handle != claim:
            raise DeploymentOperationError("qualification received a foreign claim handle")
        binding = deployment_binding_from_task(task)
        target = binding.placement.effective_target
        if target.get("pod_id") != claim_handle["pod_id"]:
            raise DeploymentOperationError("Runtime recovered to a foreign pod")
        return DeploymentReference(
            deployment_id="h3-" + lane_config.lane + "-" + operation_id,
            revision=PurePosixPath(lane_config.release_root).name,
            task_id=binding.admission_identity.task_id, run_id=binding.admission_identity.run_id,
            target_ref="runpod:" + binding.placement.original_target["pod_id"],
            effective_target_ref="runpod:" + claim_handle["pod_id"],
            executable=artifact, dependency_closure=closure,
            source_closure_digest=source_closure,
            data_root=Path(data_root), support_root=Path(support_root),
            runtime_endpoint="http://127.0.0.1:50604",
            runtime_instance_id=health["runtime_instance_id"], runtime_epoch=health["runtime_epoch"],
            runtime_schema_digest=health["schema_digest"],
            model_root=Path(profile["model_root"]), capacity=2,
            session_ref=profile["session_ref"],
            session_config_digest=hashes[profile["session_config_path"]],
            output_root=Path(output_root), credential_ref=credential_file,
            executor_id=lane_config.executor_id,
            boot_manifest_path=Path(staged_manifest_path),
            boot_manifest_hash=logical_manifest_hash,
            readiness_profile_path=Path(profile["readiness_profile_path"]),
            readiness_profile_hash=hashes[profile["readiness_profile_path"]],
            source_checkout=Path(remote_source), source_checkout_digest=source_digest,
            pack_roots=(Path(remote_source) / "astrid/packs/h3_av", Path(remote_source) / "astrid/packs/vibecomfy"),
            source_inventory_identity=source_digest, execution_target=target,
            admission_identity=binding.admission_identity,
            capability_identity=binding.capability_identity,
            input_bindings=binding.input_bindings,
            original_target=binding.placement.original_target, effective_target=target,
            placement_version=binding.placement.placement_version,
            recovery_decision_digest=binding.placement.recovery_decision_digest,
        )

    launcher = QualifiedRemoteWorkerLauncher(
        runtime=owner, credentials=None, credential_control=control,
        preparer=H3RemotePreparer(), inspector=H3RemoteInspector(),
    )
    return QualifiedRunPodDeploymentOwner(
        runtime=owner, launcher=launcher,
        task_reader=lambda task_id: _owner_result(client.tasks.show(task_id), "Runtime task lookup"),
        launch_factory=launch_factory, reference_factory=reference_factory,
        loss_observer=_loss_observer,
        owner_identity={"actor": "owner", "scopes": ["admin"]},
        operation_id=operation_id,
        task_id=task_id,
        run_id=run_id,
    )
