"""C11: the public H3 transform through a real CPU Runtime and pack host."""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
import urllib.request
from urllib.parse import urlencode
from pathlib import Path
from typing import Any

import pytest

from astrid.packs.h3_av.src.input_bundle import build_input_bundle
from astrid.packs.h3_av.src.request import normalize_request
from astrid.packs.h3_av.src.timing import plan_continuation
from astrid.packs.vibecomfy.asset_manifest import read_archive, read_archive_bytes
from astrid.sdk.pagination import page_pair, paged_rows
from tests.helpers.h3_cpu_runtime import (
    APPROVED_CPU_MODEL_BOUNDARY,
    CPU_VIBECOMFY_CANDIDATE_REVISION,
    H3_CAPTURED_VIBECOMFY_REVISION,
    H3_GRAPH_CLOSURE_CLASSES,
    H3CpuRuntime,
    T9_PROFILE,
    _SERVER_CODE,
    _object_info_from_checkout,
    _prepare_clean_vibecomfy_checkout,
    _probe_vibecomfy_checkout,
)


def test_cpu_candidate_preserves_captured_fixture_provenance(tmp_path: Path) -> None:
    from astrid.core.generation.vibecomfy_dependency import (
        VIBECOMFY_ENGINE_REVISION,
        VibeComfyDependencyError,
        configured_vibecomfy_checkout,
    )

    assert VIBECOMFY_ENGINE_REVISION == "01f38461d633651c8857712b2331650aedaee461"
    assert H3_CAPTURED_VIBECOMFY_REVISION == VIBECOMFY_ENGINE_REVISION
    assert CPU_VIBECOMFY_CANDIDATE_REVISION == "b554ed14dbb481130b96dd0c927e1fde4f02e447"
    assert CPU_VIBECOMFY_CANDIDATE_REVISION != H3_CAPTURED_VIBECOMFY_REVISION
    checkout = _prepare_clean_vibecomfy_checkout(tmp_path)
    identity = _probe_vibecomfy_checkout(checkout)
    assert identity["revision"] == CPU_VIBECOMFY_CANDIDATE_REVISION
    # This checks all three captured fixture hashes and historical revisions.
    object_info = _object_info_from_checkout(checkout)
    cache = checkout / "vibecomfy/porting/cache/object_info"
    index = json.loads((cache / "index.json").read_text())
    captured = json.loads((cache / index["ModelAttentionBackend"]).read_text())["ModelAttentionBackend"]
    assert object_info["ModelAttentionBackend"]["input"] == captured["inputs"]
    assert object_info["ModelAttentionBackend"]["source_sha256"] == captured["source_sha256"]
    candidate_env = {
        "ASTRID_VIBECOMFY_CHECKOUT": str(checkout),
        "ASTRID_VIBECOMFY_CANDIDATE_KIND": "local_snapshot",
        "ASTRID_VIBECOMFY_CANDIDATE_REVISION": identity["revision"],
        "ASTRID_VIBECOMFY_CANDIDATE_CONTENT_DIGEST": identity["content_digest"],
    }
    assert configured_vibecomfy_checkout(candidate_env) == checkout
    with pytest.raises(VibeComfyDependencyError, match="must resolve to VibeComfy revision"):
        configured_vibecomfy_checkout({"ASTRID_VIBECOMFY_CHECKOUT": str(checkout)})


def _request(source_name: str = "source") -> dict[str, Any]:
    return {
        "version": 1,
        "operation": "continue",
        "source": {"asset": source_name, "range": [0, 2]},
        "output": {"duration": 15},
        "content": {"prompt": "Continue the source as a coherent audiovisual scene."},
        "changes": {
            "video": [{"during": [2, 15], "area": {"full_frame": True}, "action": "generate"}],
            "audio": [{"during": [2, 15], "action": "generate"}],
        },
        "references": [],
        "overrides": {"seed": 731},
    }


def _make_source(path: Path, *, valid: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not valid:
        path.write_bytes(b"this is not a media container")
        return
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=24:d=2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )


def _write_request_and_bundle(root: Path, *, valid_source: bool) -> tuple[Path, Path]:
    source = root / "source.mp4"
    _make_source(source, valid=valid_source)
    request_value = _request()
    request_path = root / "request.json"
    request_path.write_text(json.dumps(request_value, sort_keys=True), encoding="utf-8")
    model = normalize_request(request_value)
    bundle_path = root / "h3-input-bundle.zip"
    build_input_bundle(model, {"source": str(source)}, bundle_path)
    return request_path, bundle_path


def _tasks(runtime: H3CpuRuntime) -> list[Any]:
    rows = paged_rows(runtime.owner.tasks.list, runtime.project_id, limit=100)
    assert rows is not None
    return list(rows)


def _generations(runtime: H3CpuRuntime) -> list[Any]:
    result = runtime.owner.generations.list(runtime.project_id, limit=100)
    assert result.ok, result.error
    page = page_pair(result.data)
    assert page is not None, result.data
    rows, _ = page
    return list(rows)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _task_output_bytes(runtime: H3CpuRuntime, task: Any, name: str) -> bytes:
    result = _field(task, "result", {})
    outputs = _field(result, "outputs", [])
    row = next((item for item in outputs if _field(item, "name") == name), None)
    assert row is not None, (name, outputs)
    object_id = _field(row, "digest") or _field(row, "object_id")
    assert isinstance(object_id, str) and object_id
    payload = runtime.owner.media.read_bytes(object_id)
    assert isinstance(payload, bytes)
    return payload


def test_managed_schema_session_queue_protocol(tmp_path: Path) -> None:
    """Exercise the fixture's Comfy HTTP boundary independently of Runtime."""
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "output"
    input_directory.mkdir()
    output_directory.mkdir()
    basename = "fixture-source.mp4"
    source_bytes = b"managed C11 fixture source"
    (input_directory / basename).write_bytes(source_bytes)
    schema_path = tmp_path / "object-info.json"
    schema_path.write_text("{}", encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "input_directory": str(input_directory),
        "output_directory": str(output_directory),
    }), encoding="utf-8")
    expected_path = tmp_path / "expected-source.json"
    timing = plan_continuation(source_end=2, output_duration=15)
    expected_path.write_text(json.dumps({
        "basename": basename,
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "size": len(source_bytes),
        "generated_seconds": timing.expected_graph_output_duration,
    }), encoding="utf-8")
    evidence_path = tmp_path / "queue-evidence.json"
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    server = subprocess.Popen(
        [sys.executable, "-c", _SERVER_CODE, str(port), str(schema_path),
         str(config_path), str(expected_path), str(evidence_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            if server.poll() is not None:
                raise AssertionError(f"fixture server exited: {server.stderr.read()}")
            try:
                with urllib.request.urlopen(base + "/system_stats", timeout=0.2) as response:
                    if json.load(response)["system"]["argv"] == ["h3-c11-fixture"]:
                        break
            except OSError:
                time.sleep(0.02)
        else:
            raise AssertionError("fixture server did not become ready")
        prompt = {
            "99": {"class_type": "VHS_LoadVideoFFmpeg", "inputs": {"video": basename}},
            "946": {"class_type": "MiniMaxH3StreamLiveExtensionAVToVHS", "inputs": {}},
        }
        request = urllib.request.Request(
            base + "/prompt",
            data=json.dumps({"prompt": prompt}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            queued = json.load(response)
        prompt_id = queued["prompt_id"]
        assert queued["node_errors"] == {}
        with urllib.request.urlopen(base + "/history/" + prompt_id, timeout=5) as response:
            history = json.load(response)[prompt_id]
        assert history["status"]["status_str"] == "success"
        assert history["status"]["completed"] is True
        filename = history["outputs"]["946"]["gifs"][0]["filename"]
        descriptor = history["outputs"]["946"]["gifs"][0]
        output = output_directory / filename
        assert output.is_file()
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
            check=True, capture_output=True, text=True,
        )
        video = next(row for row in json.loads(probe.stdout)["streams"] if row["codec_type"] == "video")
        assert int(video["nb_frames"]) == timing.expected_graph_output_frames == 371
        assert video["avg_frame_rate"] == "24/1"
        view_url = base + "/view?" + urlencode({
            "filename": descriptor["filename"],
            "subfolder": descriptor["subfolder"],
            "type": descriptor["type"],
        })
        with urllib.request.urlopen(view_url, timeout=5) as response:
            downloaded = response.read()
            assert response.status == 200
            assert response.headers.get_content_type() == "video/mp4"
            assert int(response.headers["Content-Length"]) == len(downloaded)
        expected_bytes = output.read_bytes()
        expected_digest = hashlib.sha256(expected_bytes).hexdigest()
        assert downloaded == expected_bytes
        assert hashlib.sha256(downloaded).hexdigest() == expected_digest

        invalid_view_urls = (
            base + "/view?" + urlencode({
                "filename": "not-published.mp4", "subfolder": "", "type": "output",
            }),
            base + "/view?" + urlencode({
                "filename": "../outside.mp4", "subfolder": "", "type": "output",
            }),
            base + "/view?filename=" + filename,
        )
        for invalid_url in invalid_view_urls:
            with pytest.raises(HTTPError) as rejected:
                urllib.request.urlopen(invalid_url, timeout=5)
            assert rejected.value.code in {400, 403, 404}

        outside = tmp_path / "outside.mp4"
        outside.write_bytes(b"outside fixture root")
        backup = output_directory / (filename + ".backup")
        output.rename(backup)
        output.symlink_to(outside)
        try:
            with pytest.raises(HTTPError) as rejected:
                urllib.request.urlopen(view_url, timeout=5)
            assert rejected.value.code == 403
        finally:
            output.unlink()
            backup.rename(output)

        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert evidence["video"] == basename
        assert evidence["sha256"] == hashlib.sha256(source_bytes).hexdigest()
        for endpoint in ("/queue", "/api/free", "/interrupt"):
            release = urllib.request.Request(base + endpoint, data=b"{}", method="POST")
            with urllib.request.urlopen(release, timeout=5) as response:
                assert json.load(response) == {"ok": True, "completed": True, "status": "completed"}
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_cpu_model_boundary_rejects_unknown_opt_in_before_runtime_setup(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported CPU model boundary"):
        H3CpuRuntime(tmp_path / "runtime", cpu_model_boundary="unapproved")


def test_cpu_model_boundary_rejects_hook_bytes_changed_after_readiness(tmp_path: Path) -> None:
    from astrid.core.execution.t9_model_substitute import T9SubstituteError, approved_source

    profile = json.loads(T9_PROFILE.read_text(encoding="utf-8"))
    source_identity = profile["t9_model_substitute"]
    fixture_copy = tmp_path / "sitecustomize.py"
    fixture_copy.write_bytes(Path(source_identity["source_path"]).read_bytes())
    source_identity["source_path"] = str(fixture_copy)
    assert approved_source(profile)[0] == fixture_copy.resolve()
    fixture_copy.write_bytes(fixture_copy.read_bytes() + b"\n# altered after readiness\n")
    with pytest.raises(T9SubstituteError, match="source hash does not match readiness"):
        approved_source(profile)


def _assert_attempt_fences(runtime: H3CpuRuntime, tasks: list[Any], parent_task_id: str) -> None:
    binding = runtime.daemon.service.store.execution_binding(parent_task_id)
    assert binding is not None, parent_task_id
    assert binding["actual_target"] == runtime.execution_target
    assert binding["verification"]["verified"] is True
    assert binding["executor_incarnation"] == runtime.executor_incarnation
    child_tasks = [task for task in tasks if _field(task, "task_id") != parent_task_id]
    expected_caps = {
        "h3_av.prepare", "h3_av.compile", "vibecomfy.validate", "vibecomfy.run",
        "h3_av.compose", "h3_av.verify", "h3_av.publication_finalizer",
    }
    assert {_field(task, "capability_id") for task in child_tasks} == expected_caps
    for task in child_tasks:
        task_id = str(_field(task, "task_id"))
        attempt = runtime.daemon.service.store.conn.execute(
            "SELECT id AS attempt_id, task_id, lease_id, fence, runtime_epoch FROM attempts WHERE task_id=?",
            (task_id,),
        ).fetchone()
        assert attempt is not None, task_id
        assert attempt["task_id"] == task_id
        assert attempt["attempt_id"] == _field(task, "attempt_id")
        assert attempt["lease_id"]
        assert int(attempt["fence"]) > 0
        assert int(attempt["runtime_epoch"]) == int(
            runtime.daemon.service.health()["runtime_epoch"]
        )
        spec = _field(task, "spec", {})
        delegated = spec.get("delegated_parent") if isinstance(spec, dict) else None
        assert isinstance(delegated, dict), (task_id, spec)
        assert delegated.get("parent_task_id") == parent_task_id


def _assert_generation_readback(runtime: H3CpuRuntime, root: Path) -> tuple[str, bytes]:
    rows = _generations(runtime)
    assert len(rows) == 1, rows
    generation = rows[0]
    generation_id = str(_field(generation, "generation_id"))
    assert generation_id
    assert _field(generation, "source_task_id")
    variants_result = runtime.owner.generations.variants(runtime.project_id, generation_id)
    assert variants_result.ok, variants_result.error
    variant_page = page_pair(variants_result.data)
    assert variant_page is not None, variants_result.data
    variants, _ = variant_page
    assert len(variants) == 1, variants
    variant = variants[0]
    object_id = str(_field(variant, "object_id"))
    assert object_id.startswith("sha256:")
    managed_outputs = _field(variant, "managed_outputs", [])
    assert managed_outputs, variant
    selected = next((row for row in managed_outputs if row.get("object_id") == object_id), None)
    assert selected is not None
    assert selected.get("generation_id") == generation_id
    metadata = _field(variant, "metadata", {})
    assert isinstance(metadata, dict)
    assert selected.get("variant_key") == metadata["variant_key"]
    payload = runtime.owner.media.read_bytes(object_id)
    assert isinstance(payload, bytes)
    assert "sha256:" + hashlib.sha256(payload).hexdigest() == object_id
    assert len(payload) == int(selected["size"])
    video = root / "managed-generation.mp4"
    video.write_bytes(payload)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(probe.stdout)
    streams = metadata["streams"]
    assert any(row.get("codec_type") == "video" for row in streams)
    assert any(row.get("codec_type") == "audio" for row in streams)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"],
        check=True,
        capture_output=True,
    )
    return generation_id, payload


@pytest.mark.timeout(360)
def test_public_transform_nested_runtime_settlement_and_preflight_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bootstrap must use the structured fixture target even with a foreign
    # ambient target present in the launching process.
    monkeypatch.setenv("ASTRID_EXECUTION_TARGET_JSON", json.dumps({
        "kind": "machine", "id": "foreign-ambient-machine",
    }))
    runtime = H3CpuRuntime(tmp_path / "runtime")
    assert runtime.schema_session is not None
    initial_session = dict(runtime.schema_session.session_binding)
    session_dir = runtime.schema_session.session_dir
    try:
        from astrid.sdk.host_bootstrap import PACK_HOST_ACTOR, PACK_HOST_SCOPES

        assert runtime.execution_target == {
            "kind": "machine", "id": f"h3-cpu-{runtime.daemon.instance_id}",
        }
        credential = runtime.daemon.credentials.actor_metadata(PACK_HOST_ACTOR)
        assert credential["scopes"] == sorted(PACK_HOST_SCOPES)
        assert credential["execution_binding"] == {
            "actual": runtime.execution_target,
            "verification": {
                "method": "credential_claim",
                "evidence_digest": "sha256:" + hashlib.sha256(json.dumps(
                    runtime.execution_target, sort_keys=True, separators=(",", ":")
                ).encode()).hexdigest(),
                "verified": True,
            },
            "executor_incarnation": runtime.executor_incarnation,
        }
        from astrid.packs.h3_av.workflows.native_h3_continuation_refs.workflow import READY_METADATA

        compiled_graph_classes = set(
            READY_METADATA["custom_node_packs"]["ComfyUI-H3-Motion-Context-MultiRef"]["classes_used"]
        )
        available_object_info = _object_info_from_checkout(runtime.vibecomfy_root)
        assert compiled_graph_classes == H3_GRAPH_CLOSURE_CLASSES
        assert compiled_graph_classes <= set(available_object_info)
        captured_clip_loader = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "fixtures"
                / "h3_cpu"
                / "object_info_h3_runpod_overlay.json"
            ).read_text(encoding="utf-8")
        )["CLIPLoader"]
        assert available_object_info["CLIPLoader"] == captured_clip_loader
        assert "minimax" in available_object_info["CLIPLoader"]["input"]["required"]["type"][0]

        request_path, bundle_path = _write_request_and_bundle(tmp_path / "valid", valid_source=True)
        invoke_result = runtime.owner.invoke_result
        forwarded_requests = []

        def capture_invoke_result(*args, **kwargs):
            forwarded_requests.append(kwargs["execution_request"])
            return invoke_result(*args, **kwargs)

        monkeypatch.setattr(runtime.owner, "invoke_result", capture_invoke_result)
        first = runtime.run_public_transform(
            request_path,
            bundle_path,
            out=tmp_path / "public-transform-out",
            key="h3-c11-public-transform",
        )
        assert forwarded_requests == [{
            "schema_version": 1,
            "target": runtime.execution_target,
            "limits": {"max_queue_seconds": 120, "max_runtime_seconds": 1200},
        }]
        assert first.ok, first.error
        assert first.kernel_task_id
        assert first.kernel_attempt_id
        replaced_session = runtime.schema_session.registry_binding()
        for field in ("pid", "comfy_pid", "launch_token", "process_birth_id", "comfy_process_birth_id"):
            assert replaced_session[field] != initial_session[field], field
        readiness_session = json.loads(runtime.readiness_profile_path.read_text(encoding="utf-8"))["vibecomfy_session"]
        for field, value in replaced_session.items():
            assert readiness_session[field] == value, field
        assert readiness_session["config_digest"] == initial_session["config_digest"]

        task_rows = _tasks(runtime)
        bundled_source = next(
            row for row in read_archive(bundle_path).manifest["assets"]
            if row["binding"] == "source"
        )
        compiled_task = next(
            task for task in task_rows if _field(task, "capability_id") == "h3_av.compile"
        )
        compiled_outputs = _field(compiled_task, "result")["outputs"]
        archive_object_id = next(
            row["digest"] for row in compiled_outputs if row["name"] == "managed_assets"
        )
        archive_bytes = runtime.owner.media.read_bytes(archive_object_id)
        compiled_archive = read_archive_bytes(archive_bytes)
        compiled_source = next(
            row for row in compiled_archive.manifest["assets"]
            if row["binding"] == "source_video"
        )
        expected_basename = Path(compiled_source["member"]).name
        assert compiled_source["sha256"] == bundled_source["sha256"]
        assert compiled_source["size"] == bundled_source["size"]
        assert compiled_archive.bindings["source_video"] == expected_basename
        assert compiled_archive.manifest["workflow_inputs"]["source_video"] == expected_basename
        compilation_object_id = next(
            row["digest"] for row in compiled_outputs if row["name"] == "compilation"
        )
        compilation = json.loads(runtime.owner.media.read_bytes(compilation_object_id))
        assert compilation["managed_assets"]["sha256"] == hashlib.sha256(archive_bytes).hexdigest()
        assert compilation["managed_assets"]["manifest"] == compiled_archive.manifest
        assert compilation["workflow_inputs"]["source_video"] == expected_basename
        assert compilation["continuation_timing"]["expected_graph_output_frames"] == 371
        assert compilation["continuation_timing"]["requested_output_frames"] == 360
        run_task = next(
            task for task in task_rows if _field(task, "capability_id") == "vibecomfy.run"
        )
        task_envelope = _field(run_task, "spec")
        capability_spec = task_envelope["spec"]
        assert capability_spec["invocation_preflight"]["managed_asset_manifest"] == compiled_archive.manifest
        queued = json.loads(runtime.schema_session.queue_evidence_path.read_text(encoding="utf-8"))
        assert queued["node_id"] == "99"
        assert queued["video"] == expected_basename
        assert queued["sha256"] == compiled_source["sha256"]
        assert queued["size"] == compiled_source["size"]
        generated_probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json",
             str(runtime.schema_session.output_root / queued["output_filename"])],
            check=True, capture_output=True, text=True,
        )
        generated_video = next(
            row for row in json.loads(generated_probe.stdout)["streams"] if row["codec_type"] == "video"
        )
        assert int(generated_video["nb_frames"]) == 371
        compose_task = next(task for task in task_rows if _field(task, "capability_id") == "h3_av.compose")
        composition = json.loads(_task_output_bytes(runtime, compose_task, "composition"))
        assert composition["coverage"]["generated"]["video"]["frames"] == 371
        assert composition["coverage"]["candidate"]["video"]["frames"] == 360
        parent_task_id = str(first.kernel_task_id)
        parent = next(task for task in task_rows if _field(task, "task_id") == parent_task_id)
        assert _field(parent, "capability_id") == "h3_av.transform"
        assert _field(parent, "state") == "succeeded"
        _assert_attempt_fences(runtime, task_rows, parent_task_id)
        assert "orchestration" in runtime.claimed_lanes
        assert "executor" in runtime.claimed_lanes

        generation_id, payload = _assert_generation_readback(runtime, tmp_path)
        repeated = runtime.run_public_transform(
            request_path,
            bundle_path,
            out=tmp_path / "public-transform-out",
            key="h3-c11-public-transform",
        )
        assert repeated.ok, repeated.error
        assert repeated.kernel_task_id == first.kernel_task_id
        assert len(_generations(runtime)) == 1
        assert runtime.owner.media.read_bytes(
            str(_field(_generations(runtime)[0], "managed_outputs")[0]["object_id"])
        ) == payload
        assert str(_field(_generations(runtime)[0], "generation_id")) == generation_id

        before_tasks = len(_tasks(runtime))
        before_generations = len(_generations(runtime))
        invalid_request, invalid_bundle = _write_request_and_bundle(
            tmp_path / "invalid", valid_source=False
        )
        invalid = runtime.run_public_transform(
            invalid_request,
            invalid_bundle,
            out=tmp_path / "malformed-source-out",
            key="h3-c11-malformed-source",
        )
        assert not invalid.ok
        after_tasks = _tasks(runtime)
        new_tasks = after_tasks[before_tasks:]
        assert new_tasks
        assert all(_field(task, "capability_id") != "vibecomfy.run" for task in new_tasks)
        assert len(_generations(runtime)) == before_generations
        assert not any("t9_model_substitute" in str(_field(task, "result", {})) for task in new_tasks)
        assert not runtime.host_errors, runtime.host_errors
    finally:
        runtime.close()
        assert not (session_dir / "pid").exists()
        assert not (session_dir / "comfy_pid").exists()
