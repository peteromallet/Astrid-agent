"""Offline v3 contracts; dependency services and the native engine are mocked."""
from __future__ import annotations

from contextvars import ContextVar
import hashlib
import importlib
import json
from pathlib import Path
import socket
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from astrid.core.pack.entrypoint import canonical_runtime_entrypoint
from astrid.core.pack.loader import load_pack_manifest
from astrid.sdk.actions import action_executor_definition
from astrid.packs.vibecomfy.shared.bundle_inputs import (
    CanonicalBundleInputError, staged_workflow_path,
)
from astrid.packs.vibecomfy.media import runtime
from astrid.packs.vibecomfy.media.compiler import (
    CharacterAnimationRequest, MediaCompileError, VideoEnhanceRequest,
)

PACK = Path(__file__).resolve().parents[3] / "astrid/packs/vibecomfy"
OPS = ("import", "inspect", "edit", "run", "validate", "video_enhance", "character_animation")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("offline contract check attempted network")
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect_ex", reject)
    monkeypatch.setattr(socket, "create_connection", reject)


def action(op):
    with canonical_runtime_entrypoint(f"vibecomfy.{op}"):
        return importlib.import_module(f"astrid.packs.vibecomfy.actions.{op}.run")


def module(monkeypatch, name, **attrs):
    value = ModuleType(name)
    value.__dict__.update(attrs)
    monkeypatch.setitem(sys.modules, name, value)
    return value


def gate_modules(monkeypatch):
    active = ContextVar("offline_vibe_gate", default=SimpleNamespace(audit=[]))
    module(monkeypatch, "vibecomfy.security",
           GateContext=lambda **kw: SimpleNamespace(audit=[], **kw),
           current_gate_context=active.get, set_gate_context=active.set)


def sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def bundle(tmp_path):
    values = {"workflow.py": b"fixture Python never evaluated\n",
              "workflow.vibe.json": b'{"revision_id":"parent"}\n',
              "source.json": b'{ "nodes": [], "links": [] }\r\n'}
    for name, data in values.items():
        (tmp_path / name).write_bytes(data)
    return values


def test_seven_declarations_binding_and_exact_resources():
    pack = load_pack_manifest(PACK / "pack.yaml")
    data = yaml.safe_load((PACK / "pack.yaml").read_text())
    assert pack.id == "vibecomfy" and data["schema_version"] == 3
    assert set(data["actions"]) == set(OPS)
    assert data["documentation"]["path"] == "docs/SKILL.md"
    resources = list(data["resources"])
    for op, declaration in data["actions"].items():
        definition = action_executor_definition(pack, op, declaration)
        assert definition.id == f"vibecomfy.{op}"
        assert definition.command.argv[2] == f"astrid.packs.vibecomfy.actions.{op}.run"
        assert definition.metadata["runtime_file"] == f"actions/{op}/run.py"
        assert callable(action(op).main)
        resources.extend(declaration["resources"])
    paths = [item["path"] for item in resources]
    assert len(paths) == len(set(paths))
    actual = {file.relative_to(PACK).as_posix() for file in PACK.rglob("*")
              if file.is_file() and "__pycache__" not in file.parts and file.name != "pack.yaml"}
    assert set(paths) == actual
    assert not (PACK / "executors").exists()


@pytest.mark.parametrize("op", OPS)
def test_action_import_guard_owns_exact_id(op, monkeypatch):
    name = f"astrid.packs.vibecomfy.actions.{op}.run"
    previous = sys.modules.pop(name, None)
    monkeypatch.delenv("ASTRID_INTERNAL_INVOCATION", raising=False)
    try:
        with canonical_runtime_entrypoint("vibecomfy.wrong"):
            with pytest.raises(SystemExit) as exc:
                importlib.import_module(name)
            assert exc.value.code == 2
        with canonical_runtime_entrypoint(f"vibecomfy.{op}"):
            assert callable(importlib.import_module(name).main)
    finally:
        sys.modules.pop(name, None)
        if previous is not None:
            sys.modules[name] = previous


@pytest.mark.parametrize("op,request_type,flags", [
    ("video_enhance", VideoEnhanceRequest, ["--video-ref", "source.mp4", "--enable-upscale", "true"]),
    ("character_animation", CharacterAnimationRequest,
     ["--reference-image-ref", "reference.png", "--driving-video-ref", "drive.mp4", "--mode", "animate", "--resolution", "720p"]),
])
def test_media_action_typed_adapter_and_selector_rejection(op, request_type, flags, tmp_path, monkeypatch):
    entry = action(op)
    called = Mock()
    monkeypatch.setattr(runtime, "_run", called)
    args = ["--capability", f"vibecomfy.{op}", "--profile", "pip_embedded",
            "--task-identity", "task-1", "--out", str(tmp_path / "output"), *flags]
    assert entry.main(args) == 0
    kwargs = called.call_args.kwargs
    assert kwargs["capability"] == f"vibecomfy.{op}"
    assert isinstance(kwargs["request"], request_type)
    if op == "character_animation":
        assert kwargs["request"].reference_image_ref == "reference.png"
        assert kwargs["request"].driving_video_ref == "drive.mp4"
    called.reset_mock()
    args[1] = "vibecomfy.character_animation" if op == "video_enhance" else "vibecomfy.video_enhance"
    with pytest.raises(SystemExit):
        entry.main(args)
    called.assert_not_called()


@pytest.mark.parametrize("op,media_request,name", [
    ("video_enhance", VideoEnhanceRequest(video_ref="source.mp4"), "enhanced_video"),
    ("character_animation", CharacterAnimationRequest(reference_image_ref="reference.png", driving_video_ref="drive.mp4", mode="animate", resolution="720p", prompt="walk"), "animated_video"),
])
def test_media_mock_engine_receipt_and_hash(op, media_request, name, tmp_path, monkeypatch):
    from astrid.packs.vibecomfy import production_engine
    out = tmp_path / "output"
    profile = tmp_path / "synthetic-readiness.json"
    profile.write_text(json.dumps({"verified_facts": {"exact": {"model_digest": "sha256:" + "b" * 64}}}))
    loader = Mock(return_value="resolved-offline-workflow")
    def run(resolved, profile_id, profile_document, **kwargs):
        assert resolved == "resolved-offline-workflow"
        assert profile_id == "pip_embedded" and kwargs["task_identity"] == "task-1"
        source = out / "native-result.mp4"
        source.write_bytes(b"synthetic boundary output")
        return [source]
    monkeypatch.setattr(production_engine, "_load_workflow", loader)
    monkeypatch.setattr(production_engine, "_run_profile", run)
    runtime._run(capability=f"vibecomfy.{op}", request=media_request, task_identity="task-1",
                 profile_id="pip_embedded", readiness_profile_path=str(profile),
                 readiness_profile_hash=sha(profile.read_bytes()), out=out)
    payload = json.loads((out / "manifest.json").read_text())
    assert payload["capability_id"] == f"vibecomfy.{op}"
    assert payload["output"] == "output.mp4"
    assert payload["outputs"] == [{"path": "output.mp4", "name": name, "ordinal": 0,
                                  "role": "result", "is_primary": True,
                                  "content_hash": sha((out / "output.mp4").read_bytes()),
                                  "bytes": len(b"synthetic boundary output")}]
    bindings = loader.call_args.args[0]["bindings"]
    if op == "character_animation":
        assert bindings["input_image"] == "reference.png"
        assert bindings["driving_video"] == "drive.mp4"
    else:
        assert bindings["video_ref"] == "source.mp4"


@pytest.mark.parametrize("media_request", [
    VideoEnhanceRequest(video_ref="source.mp4", enable_interpolation=True),
    VideoEnhanceRequest(video_ref="source.mp4", color_fix=True),
    VideoEnhanceRequest(video_ref="source.mp4", output_quality="medium"),
    CharacterAnimationRequest(reference_image_ref="reference.png", driving_video_ref="drive.mp4", mode="replace", resolution="720p"),
])
def test_rejected_controls_precede_profile_and_scratch(media_request, tmp_path, monkeypatch):
    reader = Mock(side_effect=AssertionError("profile read before rejection"))
    monkeypatch.setattr(runtime, "_read_profile", reader)
    op = "video_enhance" if isinstance(media_request, VideoEnhanceRequest) else "character_animation"
    output = tmp_path / "never-created"
    with pytest.raises(MediaCompileError):
        runtime._run(capability=f"vibecomfy.{op}", request=media_request, task_identity="task-1",
                     profile_id="pip_embedded", readiness_profile_path="missing", readiness_profile_hash="-", out=output)
    reader.assert_not_called()
    assert not output.exists()


def test_media_profile_hash_rejection_precedes_scratch(tmp_path):
    profile = tmp_path / "synthetic-readiness.json"
    profile.write_text("{}")
    output = tmp_path / "never-created"
    with pytest.raises(RuntimeError, match="profile hash"):
        runtime._run(capability="vibecomfy.video_enhance", request=VideoEnhanceRequest(video_ref="source.mp4"),
                     task_identity="task", profile_id="pip_embedded", readiness_profile_path=str(profile),
                     readiness_profile_hash="sha256:" + "0" * 64, out=output)
    assert not output.exists()


@pytest.mark.parametrize("op", ["video_enhance", "character_animation"])
def test_media_canonical_loader_mock_boundary(op, tmp_path, monkeypatch):
    """Prove pack bindings without building the ambient dependency's templates."""
    from astrid.packs.vibecomfy import production_engine
    from astrid.packs.vibecomfy.media.compiler import compile_character_animation, compile_video_enhance
    compiled = (compile_video_enhance(VideoEnhanceRequest(video_ref="source.mp4")) if op == "video_enhance"
                else compile_character_animation(CharacterAnimationRequest(reference_image_ref="reference.png",
                     driving_video_ref="drive.mp4", mode="animate", resolution="720p", prompt="walk")))
    expected_bindings = ({"video_ref": "source.mp4", "scale": 2.0, "upscale_method": "lanczos"}
                         if op == "video_enhance" else
                         {"input_image": "reference.png", "driving_video": "drive.mp4", "prompt": "walk",
                          "negative_prompt": "", "seed": 42, "width": 1280, "height": 720,
                          "frames": 81, "fps": 16, "steps": 6})
    def set_input(name, value):
        if name not in expected_bindings:
            raise ValueError("unsupported template input")
        assert value == expected_bindings[name]
    resolved = SimpleNamespace(set_input=Mock(side_effect=set_input))
    discovery = Mock(return_value="synthetic-discovery")
    loader = Mock(return_value=resolved)
    module(monkeypatch, "vibecomfy", __file__=str(tmp_path / "dependency/vibecomfy/__init__.py"))
    module(monkeypatch, "vibecomfy.registry.ready", repo_ready_template_discovery=discovery, workflow_from_ready=loader)
    result = production_engine._load_workflow({"template_id": compiled.template_id, "bindings": dict(compiled.bindings)}, {}, tmp_path)
    assert result is resolved
    assert compiled.template_id == ("video/basic_video_enhance" if op == "video_enhance" else "video/wan22_animate_native_first_stage")
    assert dict(compiled.bindings) == expected_bindings
    loader.assert_called_once_with(compiled.template_id, _discovery="synthetic-discovery")
    assert [(call.args[0], call.args[1]) for call in resolved.set_input.call_args_list] == list(compiled.bindings.items())
    with pytest.raises(production_engine.ProductionEngineError, match="does not expose typed input 'unknown_control'"):
        production_engine._load_workflow({"template_id": compiled.template_id,
                                        "bindings": dict(compiled.bindings) | {"unknown_control": True}}, {}, tmp_path)


def test_bundle_staging_is_complete_private_and_cleaned(tmp_path):
    values = bundle(tmp_path)
    with staged_workflow_path(workflow=None, python=tmp_path / "workflow.py",
                              companion=tmp_path / "workflow.vibe.json", source=tmp_path / "source.json",
                              scratch=tmp_path / "scratch") as (staged, authority):
        assert authority == "canonical_bundle" and staged.name == "workflow.py"
        assert staged.parent != tmp_path
        assert {name: (staged.parent / name).read_bytes() for name in values} == values
    assert not staged.parent.exists()
    for args in [dict(workflow=tmp_path / "source.json", python=tmp_path / "workflow.py", companion=None, source=None),
                 dict(workflow=None, python=tmp_path / "workflow.py", companion=None, source=None)]:
        with pytest.raises(CanonicalBundleInputError):
            with staged_workflow_path(**args):
                pytest.fail("invalid bundle admitted")


def test_import_origin_bytes_digests_and_atomic_failure(tmp_path, monkeypatch):
    entry = action("import")
    source = tmp_path / "source.json"
    source.write_bytes(b'{ "nodes": [], "links": [] }\r\n')
    members = {"workflow.py": b"fixture source\n", "workflow.vibe.json": b'{}\n', "source.json": source.read_bytes()}
    digests = {name: sha(data) for name, data in members.items()}
    report = dict(schema_version=1, transition_kind="origin", workflow_id="w", workflow_identity="w",
                  revision_id="origin", parent_revision=None, parent_task_id=None, origin_task_id=None,
                  readiness={}, validation={}, members=digests, after=dict(revision_id="origin", members=digests))
    artifacts = SimpleNamespace(python_bytes=members["workflow.py"], companion_bytes=members["workflow.vibe.json"],
                                source_bytes=members["source.json"], report=report)
    service = Mock(return_value=artifacts)
    module(monkeypatch, "vibecomfy.porting.import_service", import_workflow_bytes=service)
    outputs = entry.import_workflow(source, "w", tmp_path / "origin")
    service.assert_called_once_with(source.read_bytes(), workflow_id="w")
    assert outputs["source"].read_bytes() == source.read_bytes()
    assert json.loads(outputs["report"].read_text())["members"] == digests
    artifacts.source_bytes = b"changed"
    with pytest.raises(entry.WorkflowImportError, match="changed"):
        entry.import_workflow(source, "w", tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_inspect_canonical_consent_and_read_only_members(tmp_path, monkeypatch):
    values = bundle(tmp_path)
    gate_modules(monkeypatch)
    render = Mock(return_value={"census": {}, "surface": "read only", "topology": {}})
    module(monkeypatch, "vibecomfy.porting.render", render=render)
    module(monkeypatch, "vibecomfy.workflow_bundle", load_bundle=Mock(return_value=SimpleNamespace(
        workflow={}, workflow_identity="w", revision_id="parent", parent_revision=None,
        semantic_digest="semantic", ui_digest="ui")))
    entry = action("inspect")
    args = ["--python", str(tmp_path / "workflow.py"), "--companion", str(tmp_path / "workflow.vibe.json"),
            "--source", str(tmp_path / "source.json"), "--out", str(tmp_path / "inspection")]
    assert entry.main(args) == 1
    render.assert_not_called()
    assert entry.main([*args, "--python-execution-consent", "confirmed"]) == 0
    payload = json.loads((tmp_path / "inspection/inspection.json").read_text())
    assert payload["authority"] == "canonical_workflow_bundle"
    assert payload["members"] == {name: sha(data) for name, data in values.items()}
    assert {name: (tmp_path / name).read_bytes() for name in values} == values


@pytest.mark.parametrize("transition_kind", ["typed_edit", "manual_capture"])
def test_edit_successor_preserves_lineage_and_source(transition_kind, tmp_path, monkeypatch):
    values = bundle(tmp_path)
    gate_modules(monkeypatch)
    parent = SimpleNamespace(workflow_identity="w", revision_id="parent", parent_revision=None,
                             semantic_digest="semantic-parent", ui_digest="ui-parent")
    child = SimpleNamespace(workflow_identity="w", revision_id="child", parent_revision="parent",
                            semantic_digest="semantic-child", ui_digest="ui-child")
    loader = Mock(side_effect=[parent, child])
    module(monkeypatch, "vibecomfy.workflow_bundle", load_bundle=loader)
    calls = []
    def transition(path, *, output, **kwargs):
        calls.append(kwargs)
        output.parent.mkdir(parents=True)
        for name, data in values.items():
            (output.parent / name).write_bytes(b"successor Python\n" if name == "workflow.py" else data)
        return SimpleNamespace(status="saved", revision_id="child", to_dict=lambda: {"operations": []})
    module(monkeypatch, "vibecomfy.porting.edit.bundle_service", transition_bundle=transition)
    operations = tmp_path / "operations.json"
    operations.write_text(json.dumps({"schema_version": 1, "ops": [{"op": "remove_node", "uid": "node"}]}))
    entry = action("edit")
    outputs = entry.edit_workflow(python_path=tmp_path / "workflow.py", companion_path=tmp_path / "workflow.vibe.json",
                                 source_path=tmp_path / "source.json", workflow_id="w", parent_revision="parent",
                                 parent_task_id="parent-task", origin_task_id="origin-task", transition_kind=transition_kind,
                                 operations_path=operations if transition_kind == "typed_edit" else None,
                                 capture_python_path=None, capture_graph_path=tmp_path / "source.json" if transition_kind == "manual_capture" else None,
                                 out_dir=tmp_path / "successor", python_execution_consent="confirmed")
    report = json.loads(outputs["report"].read_text())
    assert (report["parent_revision"], report["revision_id"], report["parent_task_id"], report["origin_task_id"]) == ("parent", "child", "parent-task", "origin-task")
    assert report["transition_kind"] == transition_kind
    assert outputs["source"].read_bytes() == values["source.json"]
    assert report["members"] == {name: sha(outputs[key].read_bytes()) for name, key in [("workflow.py", "python"), ("workflow.vibe.json", "companion"), ("source.json", "source")]}
    assert calls[0]["expected_parent_revision"] == "parent"
    assert {name: (tmp_path / name).read_bytes() for name in values} == values


def test_offline_validation_ui_and_canonical_consent(tmp_path, monkeypatch):
    bundle(tmp_path)
    gate_modules(monkeypatch)
    def validate(argv):
        assert argv[:2] == ["--yes", "--quiet"] and argv[-2:] == ["--json", "--no-schema"]
        print(json.dumps({"ok": True, "status": "ok"}))
        return 0
    cli = Mock(side_effect=validate)
    module(monkeypatch, "vibecomfy.cli", main=cli)
    loader = Mock(return_value={"nodes": [], "links": []})
    normalize = Mock(return_value=SimpleNamespace(id="fixture", validate=lambda: SimpleNamespace(ok=True, issues=[])))
    module(monkeypatch, "vibecomfy.ingest.loader", load_workflow_json=loader)
    module(monkeypatch, "vibecomfy.ingest.normalize", from_ui=normalize)
    entry = action("validate")
    out = tmp_path / "validation"
    assert entry.main(["validate", str(tmp_path / "source.json"), "--out", str(out)]) == 0
    assert normalize.call_args.kwargs["use_comfy_converter"] is False
    assert json.loads((out / "validation-report.json").read_text())["runtime_validation"]["status"] == "deferred"
    args = ["validate", "", "--python", str(tmp_path / "workflow.py"), "--companion", str(tmp_path / "workflow.vibe.json"),
            "--source", str(tmp_path / "source.json"), "--out", str(out)]
    assert entry.main(args) == 1
    cli.assert_not_called()
    assert entry.main([*args, "--python-execution-consent", "confirmed"]) == 0
    assert json.loads((out / "validation-report.json").read_text())["validation_mode"] == "canonical_bundle_structural"


@pytest.mark.parametrize("canonical", [False, True])
def test_run_mock_native_boundary_and_output_custody(canonical, tmp_path, monkeypatch):
    from astrid.packs.vibecomfy import production_engine
    values = bundle(tmp_path)
    out = tmp_path / "output"
    out.mkdir()
    source = out / "native.mp4"
    source.write_bytes(b"offline output")
    engine = Mock(return_value=SimpleNamespace(outputs=(source,), managed_generation_result_path=None))
    monkeypatch.setattr(production_engine, "run_workflow_result_path", engine)
    args = ["run", "" if canonical else str(tmp_path / "source.json"), "--out", str(out), "--task-identity", "task-1", "--execution-identity", "execution-1"]
    if canonical:
        args += ["--python", str(tmp_path / "workflow.py"), "--companion", str(tmp_path / "workflow.vibe.json"), "--source", str(tmp_path / "source.json")]
    assert action("run").main(args) == 0
    kwargs = engine.call_args.kwargs
    assert kwargs["task_identity"] == "task-1" and kwargs["expected_execution_identity"] == "execution-1"
    assert kwargs["profile_id"] == "pip_embedded"
    payload = json.loads((out / "manifest.json").read_text())
    assert payload["outputs"][0]["name"] == "vibecomfy_run"
    assert payload["outputs"][0]["is_primary"] is True
    assert (out / payload["outputs"][0]["path"]).read_bytes() == b"offline output"
    assert {name: (tmp_path / name).read_bytes() for name in values} == values
    if canonical:
        assert not engine.call_args.args[0].parent.exists()
