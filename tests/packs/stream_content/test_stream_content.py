from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _tiny_video(tmp_path: Path, duration: float = 2.0) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for the synthetic stream_content video fixture")
    video = tmp_path / "testsrc.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size=160x90:rate=2",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    return video


def test_segment_map_fuses_ocr_and_transcript_density(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from astrid.packs.stream_content.actions.segment_map import core

    video = _tiny_video(tmp_path)
    transcript = _write_json(
        tmp_path / "transcript.json",
        {
            "segments": [
                {
                    "start": 1.1,
                    "end": 1.8,
                    "text": "Now the panel starts with the real content.",
                    "words": [
                        {"start": 1.1, "end": 1.2, "word": "Now"},
                        {"start": 1.25, "end": 1.38, "word": "the"},
                        {"start": 1.4, "end": 1.55, "word": "panel"},
                        {"start": 1.58, "end": 1.8, "word": "starts"},
                    ],
                }
            ]
        },
    )

    def fake_ocr(video_path: Path, work_dir: Path, *, sample_sec: float = 10.0) -> dict[str, object]:
        return {
            "video": str(video_path),
            "sample_sec": 1.0,
            "hits": [{"time": 0.0, "matched": ["STARTING SOON"], "text": "Starting Soon"}],
            "intervals": [{"start": 0.0, "end": 0.0, "matched": ["STARTING SOON"], "text": "Starting Soon"}],
        }

    monkeypatch.setattr(core, "sample_holding_screens", fake_ocr)
    payload = core.build_segment_map(video=video, transcript_path=transcript, ocr_work_dir=tmp_path / "ocr")

    assert payload["version"] == 1
    assert payload["duration"] == pytest.approx(2.0, abs=0.08)
    segments = payload["segments"]
    assert [segment["kind"] for segment in segments] == ["holding", "content"]
    assert segments[0]["start"] == 0.0
    assert segments[-1]["end"] == pytest.approx(payload["duration"], abs=0.08)
    for left, right in zip(segments, segments[1:]):
        assert left["end"] == right["start"]
    assert "Starting Soon" in segments[0]["label"]
    assert "panel starts" in segments[1]["label"]


def test_clip_candidates_scores_brief_matches_higher(tmp_path: Path) -> None:
    from astrid.packs.stream_content.actions.clip_candidates.scoring import build_candidates

    transcript = _write_json(
        tmp_path / "transcript.json",
        {
            "segments": [
                {
                    "start": 0.0,
                    "end": 24.0,
                    "speaker": "A",
                    "text": "The important thing is to remember why this format changed. It is a useful lesson.",
                },
                {
                    "start": 30.0,
                    "end": 58.0,
                    "speaker": "B",
                    "text": "The surprising truth is that latency costs shape every live demo decision. [applause]",
                },
            ]
        },
    )
    segment_map = _write_json(
        tmp_path / "segment_map.json",
        {
            "version": 1,
            "duration": 60.0,
            "segments": [
                {"start": 0.0, "end": 60.0, "kind": "content", "label": "Main panel", "confidence": 0.9, "signals": {}}
            ],
        },
    )
    brief = tmp_path / "brief.md"
    brief.write_text("Prioritize latency and live demo costs.", encoding="utf-8")

    payload = build_candidates(transcript=transcript, segment_map=segment_map, brief=brief)

    assert payload["version"] == 1
    assert len(payload["candidates"]) >= 2
    top = payload["candidates"][0]
    assert "latency costs" in top["text"]
    assert any(reason.startswith("brief_match:") for reason in top["reasons"])
    assert top["segment_label"] == "Main panel"


def test_distill_dry_run_emits_plan_shape(tmp_path: Path) -> None:
    video = _tiny_video(tmp_path)
    transcript = _write_json(
        tmp_path / "transcript.json",
        {"segments": [{"start": 0.1, "end": 1.5, "text": "A short synthetic transcript."}]},
    )
    out = tmp_path / "run"
    env = os.environ.copy()
    env["ASTRID_INTERNAL_INVOCATION"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "astrid.packs.stream_content.actions.distill.run",
            "--video",
            str(video),
            "--transcript",
            str(transcript),
            "--out",
            str(out),
            "--no-scenes",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert plan["version"] == 2
    assert [step["id"] for step in plan["steps"]] == [
        "segment-map",
        "extract-segments",
        "clip-candidates",
        "review",
    ]
    assert all(step["adapter"] == "local" for step in plan["steps"])
    # Runtime admission owns the run ledger; the orchestrator emits only its
    # plan and derived artifacts, never a competing local run record.
    assert not (out / "run.json").exists()


def _projected_distill_action():
    from astrid.core.execution.executor.actions import action_executor_definition
    from astrid.core.pack.discovery import DiscoveredPack
    from astrid.core.pack.loader import load_pack_manifest

    pack_root = Path(__file__).resolve().parents[3] / "astrid/packs/stream_content"
    pack = load_pack_manifest(pack_root / "pack.yaml")
    discovered = DiscoveredPack(pack, "source", 0)
    return action_executor_definition(discovered, "distill", pack.actions["distill"])


def _distill_fixed_command(tmp_path: Path) -> tuple[str, ...]:
    return (
        str(tmp_path / "Python Runtime" / "python"),
        "-m",
        "astrid.packs.stream_content.actions.distill.run",
        "--video",
        str(tmp_path / "source videos" / "event.mp4"),
        "--out",
        str(tmp_path / "distill output"),
    )


def _distill_command(tmp_path: Path, **extra_inputs: object) -> tuple[str, ...]:
    from astrid.core.execution.executor.registry import ExecutorRegistry
    from astrid.core.execution.executor.runner import ExecutorRunRequest, build_executor_command

    definition = _projected_distill_action()
    inputs = {"video": tmp_path / "source videos" / "event.mp4"}
    inputs.update(extra_inputs)
    return build_executor_command(
        ExecutorRunRequest(
            executor_id="stream_content.distill",
            out=tmp_path / "distill output",
            python_exec=str(tmp_path / "Python Runtime" / "python"),
            inputs=inputs,
        ),
        ExecutorRegistry((definition,)),
    )


def test_distill_v3_command_binding_projects_exact_public_contract() -> None:
    definition = _projected_distill_action()
    ports = {port.name: port for port in definition.inputs}

    assert definition.id == "stream_content.distill"
    assert set(ports) == {"video", "transcript", "brief", "dry_run", "no_scenes"}
    assert (ports["video"].type, ports["video"].required) == ("file", True)
    assert (ports["transcript"].type, ports["transcript"].required) == ("file", False)
    assert (ports["brief"].type, ports["brief"].required) == ("file", False)
    assert (ports["dry_run"].type, ports["dry_run"].default) == ("boolean", False)
    assert (ports["no_scenes"].type, ports["no_scenes"].default) == ("boolean", False)

    assert definition.command is not None
    assert definition.command.argv == (
        "{python_exec}",
        "-m",
        "astrid.packs.stream_content.actions.distill.run",
        "--video",
        "{video}",
        "--out",
        "{out}",
    )
    assert tuple((item.input, item.flag, item.optional) for item in definition.command.input_args) == (
        ("transcript", "--transcript", True),
        ("brief", "--brief", True),
        ("dry_run", "--dry-run", True),
        ("no_scenes", "--no-scenes", True),
    )
    assert "{orchestrator_args}" not in definition.command.argv

    declaration = definition.metadata["action_declaration"]
    assert declaration["outputs"] == [
        {"name": "segment_map", "type": "file", "path_template": "{out}/segment_map.json"},
        {"name": "segments_manifest", "type": "file", "path_template": "{out}/segments/segments.json"},
        {"name": "candidates", "type": "file", "path_template": "{out}/candidates.json"},
        {"name": "review", "type": "file", "path_template": "{out}/review.html"},
    ]
    assert declaration["graph"] == {
        "child_actions": [
            "editorial.transcribe",
            "editorial.scenes",
            "stream_content.segment_map",
            "media.clip_extract",
            "stream_content.clip_candidates",
        ],
        "child_orchestrators": [],
    }
    assert declaration["resources"] == [
        {"kind": "implementation", "path": "actions/distill/__init__.py"},
        {"kind": "implementation", "path": "actions/distill/plan_template.py"},
        {"kind": "implementation", "path": "actions/distill/run.py"},
        {"kind": "documentation", "path": "actions/distill/STAGE.md"},
    ]


def test_distill_v3_command_binding_omits_absent_optional_inputs(tmp_path: Path) -> None:
    assert _distill_command(tmp_path) == _distill_fixed_command(tmp_path)


def test_distill_v3_command_binding_preserves_optional_paths_as_atomic_argv(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript files" / "transcript.json"
    brief = tmp_path / "editorial briefs" / "brief.md"
    assert _distill_command(tmp_path, transcript=transcript, brief=brief) == (
        _distill_fixed_command(tmp_path)
        + ("--transcript", str(transcript), "--brief", str(brief))
    )


@pytest.mark.parametrize(
    ("input_name", "flag"),
    (("dry_run", "--dry-run"), ("no_scenes", "--no-scenes")),
)
def test_distill_v3_command_binding_preserves_false_and_true_boolean_flags(
    tmp_path: Path,
    input_name: str,
    flag: str,
) -> None:
    assert _distill_command(tmp_path, **{input_name: False}) == _distill_fixed_command(tmp_path)
    assert _distill_command(tmp_path, **{input_name: True}) == _distill_fixed_command(tmp_path) + (flag,)


def test_distill_uses_public_children_and_materializes_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from astrid.packs.stream_content.actions.distill import run as distill

    video = tmp_path / "event.mp4"
    video.write_bytes(b"admitted-video")
    out = tmp_path / "distill"
    calls: list[dict[str, object]] = []

    class FakeChild:
        def __init__(self, capability_id: str, row: dict[str, object], payload: bytes) -> None:
            self.ok = True
            self.error = None
            self.kernel_run_id = "run-1"
            self.kernel_task_id = f"task-{len(calls)}"
            self.kernel_attempt_id = "attempt-1"
            self.raw_result = {"state": "completed"}
            self.outputs = {"managed_outputs": [row]}
            self._row = row
            self._payload = payload

        def materialize_output(self, association_id: str) -> SimpleNamespace:
            assert association_id == self._row["association_id"]
            filename = str(self._row["filename"])
            relative = f"child-outputs/{association_id}/{filename}"
            destination = out / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(self._payload)
            return SimpleNamespace(output=dict(self._row), filename=relative)

    def fake_invoke(
        capability_id: str,
        *,
        kind: str,
        inputs: dict[str, object],
        child_key: str,
        wait: bool,
        timeout_seconds: float,
        poll_seconds: float,
    ) -> FakeChild:
        assert kind == "action"
        assert wait is True
        assert timeout_seconds == 3600
        assert poll_seconds == 0.1
        calls.append({"capability_id": capability_id, "inputs": inputs, "child_key": child_key})
        port, filename, payload = {
            "editorial.transcribe": ("transcript", "transcript.json", b'{"segments": []}\n'),
            "editorial.scenes": ("scenes", "scenes.json", b'{"scenes": []}\n'),
            "media.clip_extract": ("output", "clip.mp4", b"clip-bytes"),
        }[capability_id]
        import hashlib

        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        row = {
            "association_id": f"association-{len(calls)}",
            "run_id": "run-1",
            "task_id": f"task-{len(calls)}",
            "attempt_id": "attempt-1",
            "object_id": digest,
            "digest": digest,
            "size": len(payload),
            "filename": filename,
            "media_type": "video/mp4" if port == "output" else "application/json",
            "output_port": port,
            "ordinal": 0,
        }
        return FakeChild(capability_id, row, payload)

    def fake_subprocess(cmd: list[str], *, label: str) -> str:
        output = Path(cmd[cmd.index("--out") + 1])
        if label == "stream_content.segment_map":
            _write_json(
                output,
                {
                    "version": 1,
                    "source": str(video),
                    "duration": 2.0,
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 1.0,
                            "kind": "content",
                            "label": "Main panel",
                            "confidence": 1.0,
                            "signals": {},
                        }
                    ],
                },
            )
        elif label == "stream_content.clip_candidates":
            _write_json(output, {"version": 1, "candidates": []})
        else:
            raise AssertionError(label)
        return ""

    monkeypatch.setattr(distill.sdk, "invoke", fake_invoke)
    monkeypatch.setattr(distill, "_run_subprocess", fake_subprocess)

    args = distill.build_parser().parse_args(
        ["--video", str(video), "--out", str(out)]
    )
    assert distill.run_full(args) == 0

    assert [call["capability_id"] for call in calls] == [
        "editorial.transcribe",
        "editorial.scenes",
        "media.clip_extract",
    ]
    for index, call in enumerate(calls):
        input_name = "audio" if index == 0 else "video" if index == 1 else "input"
        original_input = call["inputs"][input_name]
        assert isinstance(original_input, dict)
        assert set(original_input) == {"object_id", "filename"}
    assert (out / "segments/001-main-panel.mp4").read_bytes() == b"clip-bytes"
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert "editorial.transcribe" in plan["steps"][0]["command"]
    assert "editorial.scenes" in plan["steps"][1]["command"]


@pytest.mark.parametrize(
    ("supplied_transcript", "no_scenes", "expected_children"),
    (
        (True, False, ["editorial.scenes", "media.clip_extract"]),
        (False, True, ["editorial.transcribe", "media.clip_extract"]),
        (True, True, ["media.clip_extract"]),
    ),
    ids=("transcript-skips-transcribe", "no-scenes-skips-scenes", "both-optional-branches"),
)
def test_distill_optional_branches_preserve_media_parent_outputs_and_brief(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supplied_transcript: bool,
    no_scenes: bool,
    expected_children: list[str],
) -> None:
    from types import SimpleNamespace

    from astrid.packs.stream_content.actions.distill import run as distill

    video = tmp_path / "event.mp4"
    video.write_bytes(b"admitted-video")
    transcript = tmp_path / "supplied transcript.json"
    _write_json(transcript, {"segments": [{"start": 0.0, "end": 2.0, "text": "supplied"}]})
    brief = tmp_path / "optional brief.md"
    brief.write_text("Prioritize the main panel.", encoding="utf-8")
    out = tmp_path / "distill"
    calls: list[dict[str, object]] = []
    subprocess_calls: list[dict[str, object]] = []

    class FakeChild:
        def __init__(self, row: dict[str, object], payload: bytes) -> None:
            self.ok = True
            self.error = None
            self.kernel_run_id = "run-optional"
            self.kernel_task_id = f"task-{len(calls)}"
            self.kernel_attempt_id = "attempt-optional"
            self.raw_result = {"state": "completed"}
            self.outputs = {"managed_outputs": [row]}
            self._row = row
            self._payload = payload

        def materialize_output(self, association_id: str) -> SimpleNamespace:
            assert association_id == self._row["association_id"]
            relative = f"child-outputs/{association_id}/{self._row['filename']}"
            destination = out / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(self._payload)
            return SimpleNamespace(output=dict(self._row), filename=relative)

    def fake_invoke(
        capability_id: str,
        *,
        kind: str,
        inputs: dict[str, object],
        child_key: str,
        wait: bool,
        timeout_seconds: float,
        poll_seconds: float,
    ) -> FakeChild:
        assert kind == "action"
        assert wait is True
        assert timeout_seconds == 3600
        assert poll_seconds == 0.1
        calls.append({"capability_id": capability_id, "inputs": inputs, "child_key": child_key})
        port, filename, payload = {
            "editorial.transcribe": ("transcript", "transcript.json", b'{"segments": []}\n'),
            "editorial.scenes": ("scenes", "scenes.json", b'{"scenes": []}\n'),
            "media.clip_extract": ("output", "clip.mp4", b"clip-bytes"),
        }[capability_id]
        import hashlib

        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        row = {
            "association_id": f"association-{len(calls)}",
            "run_id": "run-optional",
            "task_id": f"task-{len(calls)}",
            "attempt_id": "attempt-optional",
            "object_id": digest,
            "digest": digest,
            "size": len(payload),
            "filename": filename,
            "media_type": "video/mp4" if port == "output" else "application/json",
            "output_port": port,
            "ordinal": 0,
        }
        return FakeChild(row, payload)

    def fake_subprocess(cmd: list[str], *, label: str) -> str:
        subprocess_calls.append({"cmd": cmd, "label": label})
        output = Path(cmd[cmd.index("--out") + 1])
        if label == "stream_content.segment_map":
            _write_json(
                output,
                {
                    "version": 1,
                    "source": str(video),
                    "duration": 2.0,
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 1.0,
                            "kind": "content",
                            "label": "Main panel",
                            "confidence": 1.0,
                            "signals": {},
                        }
                    ],
                },
            )
        elif label == "stream_content.clip_candidates":
            _write_json(output, {"version": 1, "candidates": []})
        else:
            raise AssertionError(label)
        return ""

    monkeypatch.setattr(distill.sdk, "invoke", fake_invoke)
    monkeypatch.setattr(distill, "_run_subprocess", fake_subprocess)

    argv = ["--video", str(video), "--brief", str(brief), "--out", str(out)]
    if supplied_transcript:
        argv.extend(["--transcript", str(transcript)])
    if no_scenes:
        argv.append("--no-scenes")
    args = distill.build_parser().parse_args(argv)

    assert distill.run_full(args) == 0
    assert [call["capability_id"] for call in calls] == expected_children
    assert [call["label"] for call in subprocess_calls] == [
        "stream_content.segment_map",
        "stream_content.clip_candidates",
    ]
    candidate_cmd = subprocess_calls[-1]["cmd"]
    assert isinstance(candidate_cmd, list)
    assert candidate_cmd[candidate_cmd.index("--brief") + 1] == str(brief)
    if supplied_transcript:
        segment_cmd = subprocess_calls[0]["cmd"]
        assert isinstance(segment_cmd, list)
        assert segment_cmd[segment_cmd.index("--transcript") + 1] == str(transcript)
        assert candidate_cmd[candidate_cmd.index("--transcript") + 1] == str(transcript)

    assert (out / "segments/001-main-panel.mp4").read_bytes() == b"clip-bytes"
    for output in (
        out / "plan.json",
        out / "segment_map.json",
        out / "segments/segments.json",
        out / "candidates.json",
        out / "review.html",
    ):
        assert output.is_file(), output
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    expected_steps = []
    if not supplied_transcript:
        expected_steps.append("transcribe")
    if not no_scenes:
        expected_steps.append("scenes")
    expected_steps.extend(["segment-map", "extract-segments", "clip-candidates", "review"])
    assert [step["id"] for step in plan["steps"]] == expected_steps


@pytest.mark.parametrize("child_state", ("failed", "cancelled"), ids=("failed-child", "cancelled-child"))
def test_distill_failed_or_cancelled_child_stops_parent_and_preserves_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    child_state: str,
) -> None:
    from astrid.core.contracts.errors import AstridError
    from astrid.packs.stream_content.actions.distill import run as distill

    video = tmp_path / "event.mp4"
    video.write_bytes(b"admitted-video")
    out = tmp_path / "distill"
    calls: list[dict[str, object]] = []

    class FailedChild:
        ok = False
        error = {"code": "child-stop", "message": f"synthetic {child_state}"}
        kernel_run_id = "run-diagnostic"
        kernel_task_id = "task-diagnostic"
        kernel_attempt_id = "attempt-diagnostic"
        raw_result = {"state": child_state}
        outputs = {"managed_outputs": []}

    def fake_invoke(
        capability_id: str,
        *,
        kind: str,
        inputs: dict[str, object],
        child_key: str,
        wait: bool,
        timeout_seconds: float,
        poll_seconds: float,
    ) -> FailedChild:
        calls.append({"capability_id": capability_id, "child_key": child_key})
        return FailedChild()

    def unexpected_subprocess(cmd: list[str], *, label: str) -> str:
        raise AssertionError(f"subsequent work ran: {label}: {cmd}")

    monkeypatch.setattr(distill.sdk, "invoke", fake_invoke)
    monkeypatch.setattr(distill, "_run_subprocess", unexpected_subprocess)

    args = distill.build_parser().parse_args(["--video", str(video), "--out", str(out)])
    with pytest.raises(AstridError) as raised:
        distill.run_full(args)

    error = raised.value
    assert calls and [call["capability_id"] for call in calls] == ["editorial.transcribe"]
    snapshot = error.state_snapshot
    assert snapshot == {
        "capability_id": "editorial.transcribe",
        "child_key": calls[0]["child_key"],
        "run_id": "run-diagnostic",
        "task_id": "task-diagnostic",
        "attempt_id": "attempt-diagnostic",
        "state": child_state,
        "error": {"code": "child-stop", "message": f"synthetic {child_state}"},
    }
    assert error.cause == "stream_content.distill child editorial.transcribe did not complete"
    assert (out / "plan.json").is_file()
    assert not any(
        (out / output).exists()
        for output in ("segment_map.json", "segments", "candidates.json", "review.html")
    )
