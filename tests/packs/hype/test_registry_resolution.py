"""Real canonical discovery happens once per Hype invocation, not per step."""

import pytest
from types import SimpleNamespace

from astrid.core.execution.executor import registry as registry_module
from astrid.packs.video_editing.actions.hype import entrypoint as pipeline


@pytest.mark.parametrize("dry_run", [False, True])
def test_hype_discovers_once_per_invocation(tmp_path, monkeypatch, dry_run):
    monkeypatch.setenv("ASTRID_SOURCE_STATE", str(tmp_path / "absent-source-state.json"))
    monkeypatch.delenv("ASTRID_PACKS_PATH", raising=False)
    original_load = registry_module.load_default_registry
    loads = []
    commands = []

    def counted_load():
        result = original_load()
        loads.append(result)
        return result

    def run_step(step, cmd, args):
        commands.append(cmd)
        for path in pipeline.sentinel_paths(step, args):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"verdict": "ship"}')
        return 0

    monkeypatch.setattr(registry_module, "load_default_registry", counted_load)
    monkeypatch.setattr(pipeline, "run_step", run_step)
    video, audio, brief = [tmp_path / name for name in ("source.mp4", "source.wav", "brief.txt")]
    for path in (video, audio, brief):
        path.write_text("fixture")
    for invocation in range(2):
        argv = ["--video", str(video), "--audio", str(audio), "--brief", str(brief),
                "--out", str(tmp_path / f"run-{invocation}"), "--no-audit", "--render"]
        if dry_run:
            argv.append("--dry-run")
        assert pipeline.main(argv) == 0
        assert len(loads) == invocation + 1
    assert loads[0] is not loads[1]
    if not dry_run:
        assert len(commands) >= 20
        selected_module = loads[0].get("video_editing.cut").metadata["runtime_module"]
        assert any(cmd[2] == selected_module for cmd in commands)


def test_hype_selects_v3_cut_and_preserves_downstream_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRID_SOURCE_STATE", str(tmp_path / "absent-source-state.json"))
    monkeypatch.delenv("ASTRID_PACKS_PATH", raising=False)
    registry = registry_module.load_default_registry()

    cut = registry.get("video_editing.cut")
    assert cut.metadata["runtime_module"] == "astrid.packs.video_editing.actions.cut.run"
    assert cut.metadata["command_builder"] == "astrid.packs.video_editing.actions.hype.run.build_pool_steps"
    assert cut.metadata["pipeline_step"] == "cut"
    assert cut.metadata["pipeline_step_order"] == 10
    assert cut.pipeline_requirements == ("arrangement", "pool")
    assert cut.cache.sentinels == ("hype.timeline.json", "hype.assets.json", "hype.metadata.json")
    assert cut.graph.provides == ("timeline", "assets", "metadata")

    definitions = [definition for definition in registry._iter_all() if definition.id == "video_editing.cut"]
    assert len(definitions) == 1


@pytest.mark.parametrize(
    ("mode", "args", "expected_steps"),
    [
        (
            "source-video",
            {"video": "source.mp4", "audio": None, "target_duration": None},
            {"scenes", "quality_zones", "shots", "triage", "scene_describe", "pool_merge", "arrange", "cut", "render"},
        ),
        (
            "audio-only",
            {"video": None, "audio": "source.wav", "target_duration": None},
            {"transcribe", "pool_merge", "arrange", "cut", "refine", "render", "editor_review", "validate"},
        ),
        (
            "generative",
            {"video": None, "audio": None, "target_duration": 30},
            {"pool_merge", "arrange", "cut", "render"},
        ),
    ],
)
def test_cut_metadata_preserves_source_audio_and_generative_step_selection(mode, args, expected_steps):
    selected = {step.name for step in pipeline.select_steps(SimpleNamespace(
        **args,
        allow_generative_effects=mode == "generative",
        brief_allow_generative_visuals=False,
    ))}

    assert selected == expected_steps
