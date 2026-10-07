"""Actual mixed-source core defaults at production consumer boundaries.

All inputs/state are disposable. Renderer/provider subprocess work is replaced
at dispatch; source discovery and graph validation stay real.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import astrid
from astrid.core.execution.executor import runner as executor_runner
from astrid.core.execution.executor.argv import executor_argv
from astrid.core.execution.executor.registry import load_default_registry
from astrid.core.execution.executor.runner import ExecutorRunRequest, run_executor
from astrid.core.execution.orchestrator import registry as orchestrator_registry
from astrid.core.execution.orchestrator.runner import (
    OrchestratorCapabilityRunner,
    OrchestratorRunRequest,
    build_orchestrator_command,
)
from astrid.core.foundation import project_paths
from astrid.sdk.discovery import _load_executor_registry


@pytest.fixture(autouse=True)
def actual_selected_sources(tmp_path, monkeypatch):
    state = tmp_path / "absent-source-state.json"
    monkeypatch.setenv("ASTRID_SOURCE_STATE", str(state))
    monkeypatch.delenv("ASTRID_PACKS_PATH", raising=False)
    yield
    assert not state.exists()


def test_raw_executor_orchestrator_and_sdk_resolve_identical_selected_winners():
    core = load_default_registry()
    sdk = _load_executor_registry(include_internal=True)
    orchestrators = orchestrator_registry.load_default_registry()
    assert core.to_dict() == sdk.to_dict() == orchestrators._executor_registry.to_dict()
    assert len(tuple(core._iter_all())) == len(core.list())
    assert not core.conflicts()
    render = core.get("rendering.render")
    assert "rendering.render" in core.get("editorial.editor_review").graph.depends_on
    assert "rendering.render" in orchestrators.get("video_editing.hype").child_executors
    public = astrid.discover()
    by_id = {item.id: item for item in public.executors}
    assert by_id["rendering.render"].definition == render.to_dict()
    assert render.metadata["pipeline_step"] == "render"
    assert render.metadata["runtime_module"] == "astrid.packs.rendering.actions.render.run"
    assert render.metadata["action_invocation"]["command"]["argv"][2] == render.metadata["runtime_module"]
    assert "rendering.timeline_visualize" in core.as_mapping()
    assert "rendering.timeline_visualize" not in by_id


def test_default_run_executor_reaches_selected_render_command_dispatch(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    project = projects / "demo"
    inputs_dir = project / "inputs"
    inputs_dir.mkdir(parents=True)
    monkeypatch.setenv(project_paths.PROJECTS_ROOT_ENV, str(projects))
    timeline = inputs_dir / "timeline.json"
    assets = inputs_dir / "assets.json"
    timeline.write_text(json.dumps({"theme": "banodoco-default", "tracks": [], "clips": []}))
    assets.write_text(json.dumps({"assets": {}}))
    staging = tmp_path / "kernel-staging"
    calls = []

    def render_subprocess(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[1:3] == ["-m", "astrid.packs.rendering.actions.render.run"]
        output = Path(argv[argv.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00\x00\x00\x18ftypmock-render")
        (output.parent / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "kind": "rendering.render", "inputs": {},
            "outputs": [{"name": "video", "path": output.name,
                         "content_hash": "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest(),
                         "bytes": output.stat().st_size, "ordinal": 0, "role": "result", "is_primary": True}],
            "created": "fixture", "warnings": [],
        }))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(executor_runner.subprocess, "run", render_subprocess)
    result = run_executor(ExecutorRunRequest(
        executor_id="rendering.render", out=staging, project="demo", projects_root=projects,
        inputs={"timeline": str(timeline), "timeline_ref": "main", "assets_registry": str(assets),
                "selector": "rendering.ffmpeg"},
        project_was_auto_resolved=True, python_exec=sys.executable,
    ))  # Deliberately no registry: exercises ExecutorCapabilityRunner's default.
    assert result.ok and result.returncode == 0
    assert result.executor_id == "rendering.render"
    assert len(calls) == 1
    assert result.command == tuple(calls[0][0])
    assert (staging / "hype.mp4").read_bytes().endswith(b"mock-render")
    assert result.run_root is None and not (staging / "run.json").exists()


def test_render_task_adapter_supplies_actual_composed_registry_at_dispatch(tmp_path, monkeypatch):
    from astrid.packs.rendering.actions.render import task_adapter

    staging = tmp_path / "staging"
    materialized = staging / "managed-objects/source.mp4"
    materialized.parent.mkdir(parents=True)
    materialized.write_bytes(b"fixture-managed-video")
    digest = hashlib.sha256(materialized.read_bytes()).hexdigest()
    task = SimpleNamespace(id="task-render", created_at="fixture", spec={
        "family": "render_export", "project_slug": "demo",
        "params": {"timeline_ref": "main", "expected_version": 1,
                   "materialized_objects": {"media-source": str(materialized)},
                   "output_filename": "render.mp4"},
        "timeline_snapshot": {
            "config": {"theme": "banodoco-default", "tracks": [{"id": "v", "kind": "visual"}],
                       "clips": [{"id": "clip", "at": 0, "track": "v", "clipType": "media",
                                  "asset": "source", "from": 0, "to": 1}]},
            "registry": {"assets": {"source": {"media_id": "media-source",
                          "content_sha256": f"sha256:{digest}", "type": "video/mp4"}}},
        },
    })
    selected = _load_executor_registry(include_internal=True).get("rendering.render")
    dispatches = []

    def render_dispatch(request, registry):
        definition = registry.get(request.executor_id)
        assert definition.to_dict() == selected.to_dict()
        assert "rendering.render" in registry.get("editorial.editor_review").graph.depends_on
        assert request.executor_id == "rendering.render"
        assert request.inputs["selector"] == "rendering.ffmpeg"
        assert request.inputs["timeline_ref"] == "main"
        assert request.inputs["expected_version"] == 1
        dispatches.append(request)
        output = Path(request.out) / request.inputs["output_name"]
        output.write_bytes(b"\x00\x00\x00\x18ftypmock-render")
        return SimpleNamespace(ok=True, outputs={"video": output})

    monkeypatch.setattr(task_adapter, "run_executor", render_dispatch)
    # Keep task_adapter.load_default_registry real.
    manifest = task_adapter.execute_render_export_task(task=task, staging_dir=staging)
    assert len(dispatches) == 1
    assert manifest["kind"] == "rendering.render"
    assert manifest["outputs"][0]["path"] == "render.mp4"
    assert manifest["outputs"][0]["content_hash"] == "sha256:" + hashlib.sha256(
        (staging / "render.mp4").read_bytes()).hexdigest()
    assert not list(staging.glob(".render-inputs-*"))


def test_hype_selection_and_argv_consume_raw_default_render_metadata():
    from astrid.packs.video_editing.orchestrators.hype import steps

    args = argparse.Namespace(video="source.mp4", audio="source.wav", target_duration=30)
    selected = {step.name for step in steps.select_steps(args)}
    assert {"render", "editor_review"} <= selected
    expected = [sys.executable, "-m", "astrid.packs.rendering.actions.render.run"]
    assert executor_argv("rendering.render", sys.executable) == expected
    assert steps.step_argv("rendering.render", sys.executable) == expected


def test_orchestrator_runner_default_and_command_builder_use_composed_registry(tmp_path):
    registry = OrchestratorCapabilityRunner().load_default_registry()
    assert registry._executor_registry.get("rendering.render")
    video = tmp_path / "source.mp4"
    brief = tmp_path / "brief.md"
    video.write_bytes(b"fixture-video")
    brief.write_text("Fixture brief")
    command = build_orchestrator_command(OrchestratorRunRequest(
        orchestrator_id="video_editing.hype", out=tmp_path / "out",
        inputs={"video": str(video), "brief": str(brief)}, python_exec=sys.executable,
    ))  # Deliberately no registry: exercises the independent raw builder path.
    assert command[:3] == (sys.executable, "-m", "astrid.packs.video_editing.orchestrators.hype.run")
