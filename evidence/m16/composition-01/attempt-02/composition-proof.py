#!/usr/bin/env python3
"""Evidence-only M16 Stream Content composition through GenericPackHost and Runtime."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import inspect
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import unquote, urlsplit
from unittest.mock import patch

WORK = Path(__file__).resolve().parents[4]
RUNTIME = Path("/Users/peteromalley/Documents/reigh-workspace/banodoco-workspace-runtime/.otto/worktrees/pack-authoring-convergence-20261001").resolve()
EVIDENCE = Path(__file__).resolve().parent
FIXTURE = EVIDENCE / f"fixture-{os.getpid()}"
SUPPORT = FIXTURE / "support"
REALM = FIXTURE / "realm"
ATTEMPTS = FIXTURE / "attempts"
TOKEN = "m16-offline-worker-token"
EXECUTOR_ID = "m16-composition-attempt-02"
PARENT = "stream_content.distill"
CHILDREN = ("editorial.transcribe", "editorial.scenes", "media.clip_extract")

sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(WORK))
os.environ["ASTRID_SOURCE_STATE"] = str(FIXTURE / "absent-source-state.json")
os.environ.pop("ASTRID_PACKS_PATH", None)

from banodoco_workspace_client import WorkspaceClient  # noqa: E402
from runtime_protocol.service import RuntimeService  # noqa: E402
from runtime_protocol.store import RealmStore  # noqa: E402
from astrid.core.execution import generic_host as gh  # noqa: E402
from astrid.core.execution.generic_host import GenericPackHost, RuntimeProtocolClient  # noqa: E402

PACK_ROOTS = [WORK / "astrid/packs/stream_content", WORK / "astrid/packs/editorial", WORK / "astrid/packs/media"]


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def jsonable(value):
    if isinstance(value, MappingProxyType):
        return dict(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


class MappingProxyType(dict):
    pass


FIXTURE.mkdir(parents=True, exist_ok=False)
SUPPORT.mkdir(parents=True)
ATTEMPTS.mkdir(parents=True)
REALM.mkdir(parents=True)
RealmStore.initialize(REALM).close()
service = RuntimeService(REALM)
lock = threading.RLock()
transport_calls: list[dict[str, object]] = []
transport_errors: list[dict[str, object]] = []

identity = {
    "actor": EXECUTOR_ID,
    "scopes": ["worker:execute", "projects:read", "projects:write", "objects:read", "objects:write"],
    "execution_binding": {
        "actual": {"kind": "machine", "id": "m16-fixture-machine"},
        "executor_incarnation": "worker/m16-composition-attempt-02",
        "verification": {"method": "credential_claim", "verified": True, "evidence_digest": digest(b"m16-placement")},
    },
}


def transport(method: str, path: str, headers: dict[str, str], body: bytes | None):
    clean_path = urlsplit(path).path
    payload = json.loads(body) if body and headers.get("Content-Type") == "application/json" else None
    key = headers.get("Idempotency-Key")
    parts = [unquote(item) for item in clean_path.split("/")]
    transport_calls.append({"method": method, "path": clean_path, "has_json_body": payload is not None})
    try:
        mutation = False
        if clean_path == "/v1/handshake" and method == "POST":
            handshake_payload = dict(payload or {})
            handshake_payload["authenticated_actor"] = identity["actor"]
            handshake_payload["authenticated_scopes"] = identity["scopes"]
            data = service.handshake(handshake_payload)
        elif clean_path == "/v1/health":
            data = service.health()
            data["runtime_instance_id"] = "m16-offline-fixture"
        elif clean_path == "/v1/projects" and method == "POST":
            data = service.create_project(payload or {}, idempotency_key=key)
            mutation = True
        elif len(parts) == 5 and parts[2] == "projects" and parts[4] == "objects" and method == "POST":
            data = service.ingest(parts[3], body or b"", media_type=headers["Content-Type"], original_name=headers.get("X-Original-Name"), idempotency_key=key)
            mutation = True
        elif clean_path == "/v1/objects" and method == "POST":
            binding = json.loads(headers["X-Output-Binding"]) if headers.get("X-Output-Binding") else None
            data = service.ingest_object(body or b"", media_type=headers["Content-Type"], original_name=headers.get("X-Filename"), idempotency_key=key, identity=identity, upload_binding=binding)
            mutation = True
        elif len(parts) == 4 and parts[2] == "objects" and method in {"GET", "HEAD"}:
            data = service.cas.path_for(parts[3].removeprefix("sha256:")).read_bytes()
            return 200, {}, data if method == "GET" else b""
        elif clean_path == "/v1/tasks" and method == "POST":
            task = service.create_task({**(payload or {}), "idempotency_key": key}, enforce_readiness=True)
            data = {"task": service._task_resource(task)}
            mutation = True
        elif len(parts) == 4 and parts[2] == "tasks" and method == "GET":
            data = service._task_resource(service.task(parts[3]))
        elif len(parts) == 5 and parts[2] == "tasks" and parts[4] == "managed-outputs" and method == "GET":
            data = service.managed_output_page(parts[3])
        elif len(parts) == 4 and parts[2] == "managed-outputs" and method == "GET":
            data = service.managed_output(parts[3])
        elif clean_path == "/v1/delegated-tasks" and method == "POST":
            child = service.admit_delegated_child(payload or {}, idempotency_key=key, identity=identity)
            data = {"task": service._task_resource(child)}
            mutation = True
        elif len(parts) == 5 and parts[2] == "attempts":
            attempt_id = parts[3]
            action = parts[4]
            if action == "child-authority":
                data = service.issue_child_authority(attempt_id, payload or {}, identity=identity)
            elif action == "heartbeat":
                data = service.heartbeat_attempt(attempt_id, payload or {}, idempotency_key=key, identity=identity)
                mutation = True
            elif action == "settle":
                data = service.settle_attempt(attempt_id, payload or {}, idempotency_key=key, identity=identity)
                mutation = True
            elif action == "fail":
                data = service.fail_attempt(attempt_id, payload or {}, idempotency_key=key, identity=identity)
                mutation = True
            else:
                raise AssertionError(f"unsupported attempt endpoint: {clean_path}")
        else:
            raise AssertionError(f"unsupported Runtime endpoint: {method} {clean_path}")
        if mutation and set(data) != {"data", "receipt"}:
            data = {"data": data, "receipt": {"fixture": "committed"}}
        return 200, {}, json.dumps(jsonable(data)).encode()
    except Exception as exc:
        transport_errors.append({"method": method, "path": clean_path, "type": type(exc).__name__, "message": str(exc)[:2000]})
        raise


def make_client() -> tuple[WorkspaceClient, RuntimeProtocolClient]:
    owner = WorkspaceClient("http://127.0.0.1:1", TOKEN)
    owner._transport = transport
    runtime_client = RuntimeProtocolClient("http://127.0.0.1:1", TOKEN)
    runtime_client.generated._transport = transport
    runtime_client.executor_id = EXECUTOR_ID
    return owner, runtime_client


def host_for(config_path: Path, *, capabilities=None) -> GenericPackHost:
    host = GenericPackHost(
        pack_roots=PACK_ROOTS,
        client=runtime_client,
        executor_id=EXECUTOR_ID,
        max_concurrency=1,
        attempt_base=ATTEMPTS,
        credential_source={"OPENAI_API_KEY": "inert-offline-marker"},
    )
    if capabilities is None:
        host.discover()
    else:
        host.capabilities = capabilities.copy()
        host.source_epoch = base_host.source_epoch
    host._m16_config = config_path
    return host


def child_environment(self, *args, **kwargs):
    env, secrets = original_child_environment(self, *args, **kwargs)
    env["PYTHONPATH"] = str(SUPPORT) + os.pathsep + env.get("PYTHONPATH", "")
    env["M16_OFFLINE_CONFIG"] = str(offline_config)
    env["ASTRID_STREAM_CONTENT_SKIP_OCR"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if "OPENAI_API_KEY" not in env:
        env["OPENAI_API_KEY"] = "inert-offline-marker"
    return env, secrets


def make_video() -> bytes:
    video = FIXTURE / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=16x16:r=4:d=0.8", "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(video)],
        check=True,
        capture_output=True,
    )
    return video.read_bytes()


owner, runtime_client = make_client()
owner.handshake("m16-composition-attempt-02", "0.1.0", ["projects:read", "projects:write", "objects:read", "objects:write", "worker:execute"])

offline_config = FIXTURE / "offline-config.json"
offline_config.write_text(json.dumps({"worktree": str(WORK), "scope": "m16-stream-content"}), encoding="utf-8")
bootstrap = EVIDENCE / "offline-bootstrap.py"
(SUPPORT / "sitecustomize.py").write_text(
    "import runpy\nrunpy.run_path(" + repr(str(bootstrap)) + ")\n",
    encoding="utf-8",
)

original_child_environment = GenericPackHost._child_environment
patches = [
    patch.object(GenericPackHost, "_child_environment", child_environment),
    patch.object(GenericPackHost, "_start_network_broker", lambda *args, **kwargs: None),
    patch.object(GenericPackHost, "_network_evidence", lambda *args, **kwargs: None),
    patch.object(gh, "_network_startup_argv", lambda argv, *args, **kwargs: argv),
    patch.object(gh, "_network_sandbox_argv", lambda argv, *args, **kwargs: argv),
]
for item in patches:
    item.start()

result: dict[str, object] = {
    "schema": "m16-composition-attempt-02/v1",
    "status": "FAIL",
    "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    "sources": {
        "astrid_generic_host": str(Path(inspect.getfile(GenericPackHost)).resolve()),
        "runtime_service": str(Path(inspect.getfile(RuntimeService)).resolve()),
        "vendored_workspace_client": str(Path(inspect.getfile(WorkspaceClient)).resolve()),
    },
    "fixture": str(FIXTURE),
    "parent_capability": PARENT,
    "required_child_capabilities": list(CHILDREN),
}

try:
    base_host = host_for(offline_config)
    base_host.preflight()
    readiness = {
        capability_id: {
            "ready": base_host.capabilities[capability_id].ready,
            "adapter": base_host.capabilities[capability_id].adapter.family,
            "digest": base_host.capabilities[capability_id].capability_digest,
        }
        for capability_id in (PARENT, *CHILDREN)
    }
    result["readiness"] = readiness
    if not all(item["ready"] for item in readiness.values()):
        raise RuntimeError(f"capability readiness failed: {readiness}")
    for capability_id in (PARENT, *CHILDREN):
        record = base_host.capabilities[capability_id]
        service.register_capability({"capability_id": capability_id, "definition_digest": record.capability_digest})
    epoch = service.health()["runtime_epoch"]
    service.register_executor({"executor_id": EXECUTOR_ID, "capabilities": [PARENT, *CHILDREN], "max_concurrency": 1, "runtime_epoch": epoch}, idempotency_key=f"register-{EXECUTOR_ID}")

    project_result = owner.create_project("M16 composition attempt 02", slug=f"m16-composition-attempt-02-{os.getpid()}", idempotency_key=f"project-{os.getpid()}")
    project_id = str(project_result.get("id") or project_result.get("project_id"))
    source_bytes = make_video()
    source_result = owner.ingest_project_object(project_id, source_bytes, media_type="video/mp4", filename="source.mp4", idempotency_key=f"source-{os.getpid()}")
    source_id = str(source_result.get("object_id") or source_result.get("id"))
    source_digest = digest(source_bytes)
    if source_id != source_digest:
        raise AssertionError(f"source digest mismatch: {source_id} != {source_digest}")

    child_policy = {
        "capabilities": [{"capability_id": capability_id, "capability_digest": base_host.capabilities[capability_id].capability_digest} for capability_id in CHILDREN],
        "targets": [{"kind": "default"}],
        "input_object_ids": [source_id],
        "limits": {"max_children": 4, "max_active_children": 1, "max_derived_objects": 8, "max_derived_bytes": 8 * 1024 * 1024},
    }
    task_result = owner.admit_task(
        capability_id=PARENT,
        capability_digest=base_host.capabilities[PARENT].capability_digest,
        input_object_ids=[source_id],
        project_id=project_id,
        idempotency_key=f"parent-{os.getpid()}",
        spec={"inputs": {"video": {"object_id": source_id, "filename": "source.mp4"}}},
        child_delegation=child_policy,
    )
    parent_resource = task_result.get("task", task_result)
    parent_task_id = str(parent_resource.get("id") or parent_resource.get("task_id"))
    claim = service.claim_next({"executor_id": EXECUTOR_ID, "capability_ids": [PARENT], "runtime_epoch": epoch, "target": {"kind": "default"}}, idempotency_key=f"claim-parent-{os.getpid()}", identity=identity)
    if not claim:
        raise RuntimeError("parent task was not claimable")
    parent_attempt_id = str(claim["attempt_id"])
    runtime_client._attempt_runtime_epochs[parent_attempt_id] = int(claim["runtime_epoch"])
    parent_task = service.task(parent_task_id)["task"]
    parent_task.update(
        runtime_epoch=claim["runtime_epoch"],
        fence=claim["fence"],
        project_id=project_id,
        spec=claim["spec"],
        input_object_ids=claim["input_object_ids"],
        execution_binding=claim.get("execution_binding"),
        lease_id=claim["lease_id"],
        executor_id=EXECUTOR_ID,
    )

    stop_children = threading.Event()
    child_errors: list[dict[str, str]] = []
    served_children: list[str] = []

    def serve_children() -> None:
        while not stop_children.is_set():
            with lock:
                claim_child = service.claim_next(
                    {"executor_id": EXECUTOR_ID, "capability_ids": list(CHILDREN), "runtime_epoch": epoch, "target": {"kind": "default"}},
                    idempotency_key=f"claim-child-{os.getpid()}-{time.monotonic_ns()}",
                    identity=identity,
                )
            if not claim_child:
                time.sleep(0.01)
                continue
            child_task_id = claim_child.get("task_id")
            if not child_task_id:
                time.sleep(0.01)
                continue
            child_task_id = str(child_task_id)
            child_attempt_id = str(claim_child["attempt_id"])
            runtime_client._attempt_runtime_epochs[child_attempt_id] = int(claim_child["runtime_epoch"])
            child_task = service.task(child_task_id)["task"]
            child_task.update(
                runtime_epoch=claim_child["runtime_epoch"],
                fence=claim_child["fence"],
                project_id=project_id,
                spec=claim_child["spec"],
                input_object_ids=claim_child["input_object_ids"],
                execution_binding=claim_child.get("execution_binding"),
                lease_id=claim_child["lease_id"],
                executor_id=EXECUTOR_ID,
            )
            child_host = host_for(offline_config, capabilities=base_host.capabilities)
            try:
                record = child_host.capabilities[str(child_task["capability"])]
                grant = child_host.request_provider_route_grant({"task": child_task}) if record.adapter.family == "provider" else None
                child_host.run_task({"task": child_task}, lease_token=claim_child["lease_id"], attempt_id=child_attempt_id, fence=claim_child["fence"], provider_route_grant=grant)
            except Exception as exc:
                child_errors.append({"task_id": child_task_id, "type": type(exc).__name__, "message": str(exc)[:4000]})
            finally:
                child_host.shutdown()
                served_children.append(child_task_id)

    worker = threading.Thread(target=serve_children, name="m16-child-worker", daemon=True)
    worker.start()
    parent_error = None
    parent_return = None
    try:
        parent_host = host_for(offline_config, capabilities=base_host.capabilities)
        parent_return = parent_host.run_task({"task": parent_task}, lease_token=claim["lease_id"], attempt_id=parent_attempt_id, fence=claim["fence"])
    except Exception as exc:
        parent_error = {"type": type(exc).__name__, "message": str(exc)[:12000]}
    finally:
        stop_children.set()
        worker.join(timeout=20)
        parent_host.shutdown()

    parent_raw = service.task(parent_task_id)["task"]
    child_rows = service.store.delegated_children(parent_task_id, parent_attempt_id)
    child_resources = [service.task(row["id"])["task"] for row in child_rows]
    parent_outputs_raw = service.managed_outputs(parent_task_id)
    parent_outputs = []
    for row in parent_outputs_raw:
        association_id = str(row["association_id"])
        managed = jsonable(owner.get_managed_output(association_id).__dict__)
        object_id = str(row.get("object_id") or row.get("digest"))
        readback = owner.get_object(object_id.removeprefix("sha256:")) if object_id.startswith("sha256:") else b""
        parent_outputs.append({"descriptor": jsonable(row), "managed_readback": managed, "readback_sha256": hashlib.sha256(readback).hexdigest(), "readback_size": len(readback)})

    expected_ports = {"segment_map", "segments_manifest", "candidates", "review"}
    settled_by_port = {str(row.get("output_port")): row for row in parent_outputs_raw}
    readback_ports = sorted(expected_ports & set(settled_by_port))
    parent_spec_row = service.store.conn.execute("SELECT spec_json FROM tasks WHERE id=?", (parent_task_id,)).fetchone()
    parent_spec = json.loads(parent_spec_row["spec_json"]) if parent_spec_row else {}
    derived_registry = parent_spec.get("derived_input_registry", {})
    derived_rows = [row for row in parent_outputs_raw if row.get("role") == "derived_input"]
    uploaded_source_derived = [call for call in transport_calls if call["path"] == "/v1/objects" and call["has_json_body"]]
    result.update(
        {
            "status": "PASS" if parent_raw.get("status") == "completed" and len(readback_ports) == 4 else "BOUNDARY_BLOCKED" if parent_error and ("segments" in parent_error["message"] or "segment" in parent_error["message"]) else "FAIL",
            "project_id": project_id,
            "source": {"object_id": source_id, "sha256": source_digest, "size": len(source_bytes), "filename": "source.mp4"},
            "parent": {"task_id": parent_task_id, "attempt_id": parent_attempt_id, "state": parent_raw.get("status"), "run_state": service.store.conn.execute("SELECT status FROM runs WHERE id=?", (parent_raw.get("run_id"),)).fetchone()["status"], "return": jsonable(parent_return), "error": parent_error},
            "children": [{"task_id": task.get("id"), "capability_id": task.get("capability"), "attempt_id": task.get("attempt_id"), "state": task.get("status"), "input_object_ids": task.get("input_object_ids")} for task in child_resources],
            "exact_child_identities": [task.get("capability") for task in child_resources] == list(CHILDREN),
            "served_children": served_children,
            "child_errors": child_errors,
            "custody": {"parent_input_object_ids": parent_raw.get("input_object_ids"), "parent_derived_input_registry": derived_registry, "parent_derived_output_rows": derived_rows, "source_digest_uploaded_as_child_derived": any(source_id in json.dumps(call) for call in uploaded_source_derived), "zero_original_video_derived_registration": not derived_registry and not derived_rows and not any(source_id in json.dumps(call) for call in uploaded_source_derived)},
            "parent_outputs": parent_outputs,
            "required_output_ports": sorted(expected_ports),
            "verified_materialized_output_ports": readback_ports,
            "transport_call_count": len(transport_calls),
            "transport_calls": transport_calls,
            "transport_errors": transport_errors,
            "output_namespace_boundary_observed": bool(parent_error and "segments" in parent_error["message"]),
            "fixture_bytes": sum(path.stat().st_size for path in FIXTURE.rglob("*") if path.is_file()),
        }
    )
except Exception as exc:
    result.update({"status": "FAIL", "error": {"type": type(exc).__name__, "message": str(exc)[:12000], "traceback": traceback.format_exc()}})
finally:
    result["ended_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    (EVIDENCE / "composition-result.json").write_text(json.dumps(jsonable(result), indent=2, default=str) + "\n", encoding="utf-8")
    (EVIDENCE / "transport-errors.json").write_text(json.dumps(jsonable(transport_errors), indent=2) + "\n", encoding="utf-8")
    for item in reversed(patches):
        item.stop()
    service.close()

print(json.dumps(jsonable(result), indent=2, default=str), flush=True)
raise SystemExit(0 if result.get("status") in {"PASS", "BOUNDARY_BLOCKED"} else 1)
