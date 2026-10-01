from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest
import yaml
from PIL import Image
from vibecomfy.cli_loader import load_workflow_any

from astrid.packs.h3_av.orchestrators.transform.run import (
    _authoritative_source_asset_id,
    _write_generated_bundle,
)
from astrid.packs.h3_av.src.compile import CompilationError, compile_preparation
from astrid.packs.h3_av.src.compose import _sample_digest, compose_candidate
from astrid.packs.h3_av.src.generation import generation_timing
from astrid.packs.h3_av.src.prepare import prepare_request
from astrid.packs.h3_av.src.request import normalize_request
from astrid.packs.h3_av.src.request_v2 import branch_for
from astrid.packs.h3_av.src.verify import verify_candidate

PACK = Path(__file__).resolve().parents[3] / "astrid/packs/h3_av"


def _case(branch: str) -> dict[str, object]:
    if branch == "source_free":
        media = [{"id": "look", "asset": "look.png", "role": "reference", "modality": "image"}]
    elif branch == "audio_only":
        media = [{"id": "voice", "asset": "voice.wav", "role": "timeline", "modality": "audio", "at": {"frame": 0}, "edit": [{"stream": "audio", "during": [0, 1], "text": "A literal line."}]}]
    elif branch == "extension_context":
        media = [{"id": "source", "asset": "source.mp4", "role": "timeline", "modality": "video", "at": {"frame": 0}, "range": [0, 2]}]
    else:
        media = [
            {"id": "source", "asset": "source.mp4", "role": "timeline", "modality": "video", "at": {"frame": 0}, "range": [0, 5], "edit": [{"stream": "video", "during": [1, 2], "mask": {"full_frame": True}}]},
        ]
    return {"version": 2, "prompt": f"Exercise {branch}.", "duration": 5, "continuation": branch == "extension_context", "media": media, "settings": {}}


def _av(path: Path, *, frames: int, color: str, frequency: int) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=64x48:r=24",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000",
            "-frames:v", str(frames), "-t", str(frames / 24),
            "-c:v", "ffv1", "-c:a", "pcm_s16le", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_native_v2_schema_profile_and_supported_branch_bindings(tmp_path: Path) -> None:
    schema = json.loads((PACK / "schemas/request.v2.json").read_text(encoding="utf-8"))
    profile = yaml.safe_load((PACK / "profiles/native-v2.yaml").read_text(encoding="utf-8"))
    assert schema["properties"]["version"] == {"const": 2}
    assert profile["branches"] == ["source_free", "audio_only", "extension_context", "source_backed_v2v"]
    paths = {}
    for name in ("look.png", "voice.wav", "source.mp4"):
        path = tmp_path / name
        if name.endswith(".png"):
            Image.new("RGB", (16, 16), color=(32, 64, 128)).save(path)
        else:
            path.write_bytes(name.encode("utf-8"))
        paths[name] = str(path)
    for branch in profile["branches"]:
        raw = _case(branch)
        jsonschema.validate(raw, schema)
        request = normalize_request(raw)
        assert branch_for(request) == branch
        prepared = prepare_request(request, asset_map=paths)
        assert set(prepared["mask_schedule"]["video"]) >= {
            "generated_intervals", "protected_intervals"
        }
        assert set(prepared["mask_schedule"]["audio"]) >= {
            "generated_intervals", "protected_intervals"
        }
        if branch == "audio_only":
            with pytest.raises(CompilationError, match="VHS_LoadVideoFFmpeg.*no timeline-audio input"):
                compile_preparation(prepared, out_dir=tmp_path / branch)
            continue
        compiled = compile_preparation(prepared, out_dir=tmp_path / branch)
        assert compiled["profile"] == "h3_av.native.v2"
        assert compiled["branch"] == branch
        assert compiled["capabilities"]["output_contract"] == (
            "separate_av_full_timeline" if branch == "source_backed_v2v"
            else "muxed_av_full_timeline"
        )
        workflow_path = Path(compiled["workflow"]["workflow.py"]["path"])
        workflow = load_workflow_any(str(workflow_path))
        assert set(compiled["workflow_inputs"]).issubset(workflow.inputs)
        api = workflow.compile("api", run_inputs=compiled["workflow_inputs"])
        assert isinstance(api, dict) and api
        assert workflow.validate().ok


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)
def test_canonical_v2_source_free_reaches_real_compose_and_verify_boundary(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "look.png"
    Image.new("RGB", (16, 16), color=(32, 64, 128)).save(reference)
    request = normalize_request(
        {
            "version": 2,
            "prompt": "Animate <Picture 1> with synchronized sound.",
            "duration": 1,
            "media": [
                {
                    "id": "look",
                    "asset": "look.png",
                    "role": "reference",
                    "modality": "image",
                }
            ],
            "settings": {},
        }
    )
    assert _authoritative_source_asset_id(request) is None
    preparation = prepare_request(request, asset_map={"look.png": str(reference)})
    schedule = preparation["mask_schedule"]
    assert schedule["video"]["generated_intervals"] == [[0.0, 1.0]]
    assert schedule["audio"]["generated_intervals"] == [[0.0, 1.0]]
    assert schedule["video"]["protected_intervals"] == []
    assert schedule["audio"]["protected_intervals"] == []
    compilation = compile_preparation(preparation, out_dir=tmp_path / "compiled")
    assert compilation["branch"] == "source_free"

    generated = tmp_path / "generated.mkv"
    timing = generation_timing(1)
    _av(generated, frames=timing["raw_frames"], color="red", frequency=880)
    composition = compose_candidate(
        preparation=preparation,
        generated=generated,
        out_dir=tmp_path / "composition",
    )
    verification = verify_candidate(
        preparation=preparation,
        composition=composition,
    )

    assert composition["source"] is None
    assert composition["coverage"]["candidate"]["video"]["frames"] == 24
    assert verification["preservation"]["status"] == "no_protected_permissions"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)
def test_v2_source_backed_extension_preserves_prefix_through_composition(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mkv"
    generated = tmp_path / "generated.mkv"
    _av(source, frames=24, color="blue", frequency=440)
    request = normalize_request(
        {
            "version": 2,
            "prompt": "Continue the source for one second.",
            "duration": 2,
            "continuation": True,
            "media": [
                {
                    "id": "source",
                    "asset": "source.mkv",
                    "role": "timeline",
                    "modality": "video",
                    "at": {"frame": 0},
                    "range": [0, 1],
                }
            ],
            "settings": {},
        }
    )
    assert _authoritative_source_asset_id(request) == "source.mkv"
    preparation = prepare_request(request, asset_map={"source.mkv": str(source)})
    schedule = preparation["mask_schedule"]
    assert schedule["video"]["protected_intervals"] == [[0.0, 1.0]]
    assert schedule["audio"]["protected_intervals"] == [[0.0, 1.0]]
    assert schedule["video"]["generated_intervals"] == [[1.0, 2.0]]
    assert schedule["audio"]["generated_intervals"] == [[1.0, 2.0]]
    compilation = compile_preparation(preparation, out_dir=tmp_path / "compiled")
    _av(
        generated,
        frames=compilation["continuation_timing"]["expected_graph_output_frames"],
        color="red",
        frequency=880,
    )

    composition = compose_candidate(
        preparation=preparation,
        generated=generated,
        source=source,
        out_dir=tmp_path / "composition",
    )
    verification = verify_candidate(
        preparation=preparation,
        composition=composition,
        source=source,
    )

    assert composition["coverage"]["candidate"]["video"]["frames"] == 48
    assert verification["preservation"]["status"] == "protected_sample_evidence"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)
def test_v2_full_timeline_audio_composes_with_independent_video_permissions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mkv"
    generated = tmp_path / "generated.mkv"
    _av(source, frames=24, color="blue", frequency=440)
    request = normalize_request(
        {
            "version": 2,
            "prompt": "Regenerate the soundtrack while extending the picture.",
            "duration": 2,
            "continuation": True,
            "media": [
                {
                    "id": "source",
                    "asset": "source.mkv",
                    "role": "timeline",
                    "modality": "video",
                    "at": {"frame": 0},
                    "range": [0, 1],
                    "edit": [
                        {"stream": "audio", "during": [0, 2], "text": "New soundtrack."}
                    ],
                }
            ],
            "settings": {},
        }
    )
    preparation = prepare_request(request, asset_map={"source.mkv": str(source)})
    schedule = preparation["mask_schedule"]
    assert schedule["video"]["protected_intervals"] == [[0.0, 1.0]]
    assert schedule["video"]["generated_intervals"] == [[1.0, 2.0]]
    assert schedule["audio"]["protected_intervals"] == []
    assert schedule["audio"]["generated_intervals"] == [[0.0, 2.0]]

    compilation = compile_preparation(preparation, out_dir=tmp_path / "compiled")
    _av(
        generated,
        frames=compilation["continuation_timing"]["expected_graph_output_frames"],
        color="red",
        frequency=880,
    )
    composition = compose_candidate(
        preparation=preparation,
        generated=generated,
        source=source,
        out_dir=tmp_path / "composition",
    )
    verification = verify_candidate(
        preparation=preparation,
        composition=composition,
        source=source,
    )

    assert composition["coverage"]["candidate"]["video"]["frames"] == 48
    assert composition["changed_permissions"] == {
        "video": [[1.0, 2.0]],
        "audio": [[0.0, 2.0]],
    }
    assert verification["preservation"]["status"] == "protected_sample_evidence"
    candidate = Path(composition["candidate"]["path"])
    source_prefix, prefix_frames = _sample_digest(source, "video", 0, 1)
    candidate_prefix, candidate_frames = _sample_digest(candidate, "video", 0, 1)
    assert prefix_frames == candidate_frames == 24
    assert candidate_prefix == source_prefix


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)
def test_v2_same_length_edit_binds_intervals_and_reaches_compose_verify(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mkv"
    video = tmp_path / "generated.mkv"
    audio = tmp_path / "generated.flac"
    _av(source, frames=24, color="blue", frequency=440)
    # The video container deliberately has the source tone; edited audio must
    # come from the separate LanPaint output, not that container's soundtrack.
    _av(video, frames=24, color="red", frequency=440)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=880:sample_rate=48000", "-t", "1", str(audio)],
        check=True, capture_output=True,
    )
    request = normalize_request({
        "version": 2,
        "prompt": "Edit independent portions of the source picture and soundtrack.",
        "duration": 1,
        "continuation": False,
        "media": [{
            "id": "source", "asset": "source.mkv", "role": "timeline",
            "modality": "video", "at": {"frame": 0}, "range": [0, 1],
            "edit": [
                {"stream": "video", "during": [0.25, 0.5], "mask": {"full_frame": True}},
                {"stream": "audio", "during": [0.5, 0.75], "text": "Hello."},
            ],
        }],
        "settings": {},
    })
    preparation = prepare_request(request, asset_map={"source.mkv": str(source)})
    schedule = preparation["mask_schedule"]
    assert schedule["branch"] == "source_backed_v2v"
    assert schedule["video"]["generated_intervals"] == [[0.25, 0.5]]
    assert schedule["audio"]["generated_intervals"] == [[0.5, 0.75]]
    assert schedule["video"]["protected_intervals"] == [[0.0, 0.25], [0.5, 1.0]]
    assert schedule["audio"]["protected_intervals"] == [[0.0, 0.5], [0.75, 1.0]]
    compilation = compile_preparation(preparation, out_dir=tmp_path / "compiled")
    assert compilation["profile"] == "h3_av.native.v2"
    assert compilation["branch"] == "source_backed_v2v"
    assert compilation["request_digest"] == request.digest
    assert "continuation_timing" not in compilation
    assert compilation["capabilities"]["output_contract"] == "separate_av_full_timeline"
    workflow = load_workflow_any(compilation["workflow"]["workflow.py"]["path"])
    assert workflow.validate().ok
    api = workflow.compile("api", run_inputs=compilation["workflow_inputs"])
    editor = api["164"]
    assert editor["class_type"] == "LanPaint_VideoMaskEditor"
    keyframes = json.loads(editor["inputs"]["keyframes"])
    assert set(keyframes) == {"0", "6", "12"}
    assert keyframes["0"].endswith("mask_preserve.png")
    assert keyframes["6"].endswith("mask_full_frame.png")
    assert keyframes["12"] == keyframes["0"]
    assert json.loads(editor["inputs"]["audio_mask"]) == [{"start": 0.5, "end": 0.75}]
    assert editor["inputs"]["video"] == compilation["workflow_inputs"]["source_video"]
    for node_id in ("105::166", "105::168"):
        assert api[node_id]["inputs"]["mask"] == ["164", 1]
        assert api[node_id]["inputs"]["audio_mask"] == ["164", 2]

    bundle = _write_generated_bundle(
        {role: (path, {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
         for role, path in (("video", video), ("audio", audio))},
        tmp_path / "generated.zip",
    )
    composition = compose_candidate(
        preparation=preparation, generated=bundle, source=source,
        out_dir=tmp_path / "composition",
    )
    verification = verify_candidate(
        preparation=preparation, composition=composition, source=source,
    )
    assert composition["coverage"]["candidate"]["video"]["frames"] == 24
    assert composition["composition"]["output_roles"] == ["video", "audio"]
    assert composition["changed_permissions"] == {"video": [[0.25, 0.5]], "audio": [[0.5, 0.75]]}
    assert verification["preservation"]["status"] == "protected_sample_evidence"
    candidate = Path(composition["candidate"]["path"])
    for stream, generated_path, start in (("video", video, 0.25), ("audio", audio, 0.5)):
        candidate_digest, count = _sample_digest(candidate, stream, start, 0.25)
        assert count > 0
        assert candidate_digest == _sample_digest(generated_path, stream, start, 0.25)[0]
        assert candidate_digest != _sample_digest(source, stream, start, 0.25)[0]


def test_v2_audio_only_fails_before_graph_composition_on_actual_video_port(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"bounded-audio-fixture")
    request = normalize_request(_case("audio_only"))
    preparation = prepare_request(request, asset_map={"voice.wav": str(audio)})
    assert _authoritative_source_asset_id(request) is None
    assert preparation["mask_schedule"]["video"]["generated_intervals"] == [[0.0, 5.0]]
    assert preparation["mask_schedule"]["video"]["protected_intervals"] == []
    assert preparation["mask_schedule"]["audio"]["protected_intervals"] == [[1.0, 5.0]]

    workflow = load_workflow_any(
        str(PACK / "workflows/native_h3_continuation/workflow.py")
    )
    assert workflow.inputs["source_video"].media_semantics == "video"
    api = workflow.compile("api", run_inputs={"source_video": "source.mp4"})
    loader = next(node for node in api.values() if node["class_type"] == "VHS_LoadVideoFFmpeg")
    assert loader["inputs"]["video"] == "source.mp4"
    with pytest.raises(
        CompilationError,
        match="source_video port is video-semantic.*VHS_LoadVideoFFmpeg.*no timeline-audio input",
    ):
        compile_preparation(preparation, out_dir=tmp_path / "compiled")


def test_parent_v1_source_free_generation_and_reference_capacity_are_retained() -> None:
    for count in (1, 4, 9):
        request = {
            "version": 1,
            "operation": "generate",
            "source": None,
            "output": {"duration": 5},
            "content": {"prompt": "Parent v1 generation."},
            "changes": {"video": [], "audio": []},
            "references": [{"asset": f"ref-{index}.png", "purpose": "appearance"} for index in range(count)],
            "overrides": {},
        }
        normalized = normalize_request(request)
        assert normalized.value["version"] == 1
        assert _authoritative_source_asset_id(normalized) is None


def test_native_v2_source_free_reference_capacity_uses_declared_ports(tmp_path: Path) -> None:
    paths = {}
    for index in range(9):
        path = tmp_path / f"ref-{index}.png"
        Image.new("RGB", (16, 16), color=(index, 32, 64)).save(path)
        paths[f"ref-{index}.png"] = str(path)
    for count in (1, 4, 9):
        raw = {
            "version": 2,
            "prompt": f"Source-free v2 with {count} references.",
            "duration": 5,
            "media": [
                {"id": f"ref-{index}", "asset": f"ref-{index}.png", "role": "reference", "modality": "image"}
                for index in range(count)
            ],
            "settings": {},
        }
        request = normalize_request(raw)
        compiled = compile_preparation(
            prepare_request(request, asset_map=paths),
            out_dir=tmp_path / f"source-free-{count}",
        )
        workflow = load_workflow_any(compiled["workflow"]["workflow.py"]["path"])
        assert compiled["capabilities"]["references"] == count
        assert set(compiled["workflow_inputs"]).issubset(workflow.inputs)
        assert workflow.validate().ok
        workflow.compile("api", run_inputs=compiled["workflow_inputs"])


def test_v1_edit_continue_and_separate_av_semantics_remain_in_the_parent_contract() -> None:
    edit = normalize_request({
        "version": 1,
        "operation": "edit",
        "source": {"asset": "source.mp4", "range": [0, 4]},
        "output": {"duration": 4},
        "content": {"prompt": "Edit the source."},
        "changes": {"video": [{"during": [1, 2], "area": {"full_frame": True}, "action": "generate"}], "audio": [{"during": [1, 2], "action": "generate"}]},
        "references": [],
        "overrides": {},
    })
    continuation = normalize_request({
        "version": 1,
        "operation": "continue",
        "source": {"asset": "source.mp4", "range": [0, 4]},
        "output": {"duration": 8},
        "content": {"prompt": "Continue the source."},
        "changes": {"video": [{"during": [4, 8], "area": {"full_frame": True}, "action": "generate"}], "audio": [{"during": [4, 8], "action": "generate"}]},
        "references": [],
        "overrides": {},
    })
    assert edit.value["operation"] == "edit"
    assert continuation.value["operation"] == "continue"
    assert _authoritative_source_asset_id(edit) == "source.mp4"
    assert _authoritative_source_asset_id(continuation) == "source.mp4"
