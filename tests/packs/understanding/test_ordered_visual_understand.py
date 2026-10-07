from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from astrid.core._shared.result_manifest import harvest_staged_outputs
from astrid.core.contracts.errors import AstridError
from astrid.core.execution.executor.actions import action_executor_definition
from astrid.core.pack.discovery import DiscoveredPack
from astrid.core.pack.loader import load_pack_manifest
from astrid.packs.understanding.actions.visual_understand import run as visual_understand
from astrid.packs.understanding.actions.video_understand import run as video_understand

PACK_ROOT = Path(__file__).resolve().parents[3] / "astrid/packs/understanding"


def _visual_definition():
    pack = load_pack_manifest(PACK_ROOT / "pack.yaml")
    discovered = DiscoveredPack(pack, "source", 0)
    return action_executor_definition(
        discovered,
        "visual_understand",
        pack.actions["visual_understand"],
    )

def _video_definition():
    pack = load_pack_manifest(PACK_ROOT / "pack.yaml")
    discovered = DiscoveredPack(pack, "source", 0)
    return action_executor_definition(
        discovered,
        "video_understand",
        pack.actions["video_understand"],
    )


def _run_visual(
    *,
    image: Path,
    out_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str] | None = None,
) -> Path:
    _fake_transport(monkeypatch)
    out_path = out_dir / "result.json"
    args = visual_understand.build_parser().parse_args(
        [
            "--query",
            "Describe the image.",
            "--image",
            str(image),
            "--out-dir",
            str(out_dir),
            "--out",
            str(out_path),
            *(extra_args or []),
        ]
    )
    assert visual_understand.run(args) == 0
    return out_dir / "manifest.json"



ANSWER_SCHEMA = {
    "additionalProperties": False,
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "type": "object",
}


def _write_images(tmp_path: Path, count: int) -> tuple[Path, ...]:
    paths: list[Path] = []
    for index in range(count):
        path = tmp_path / f"page-{index + 1}.png"
        Image.new("RGB", (8, 8), (index * 30, 20, 200 - index * 20)).save(path)
        paths.append(path)
    return tuple(paths)


def _fake_transport(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def send(*, api_key: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
        calls.append({"api_key": api_key, "payload": payload, "timeout": timeout})
        return response or {
            "id": "resp_ordered_123",
            "model": "gpt-5.6-sol-2026-08-01",
            "output_text": json.dumps({"answer": "ok"}),
            "usage": {"input_tokens": 31, "output_tokens": 4, "total_tokens": 35},
        }

    monkeypatch.setattr(visual_understand, "load_api_key", lambda **_kwargs: "test-key")
    monkeypatch.setattr(visual_understand, "_send_responses_request", send)
    return calls


def _image_blocks(call: dict[str, Any]) -> list[dict[str, Any]]:
    content = call["payload"]["input"][0]["content"]
    return [block for block in content if block["type"] == "input_image"]


def _data_url_bytes(data_url: str) -> bytes:
    _, encoded = data_url.split(",", 1)
    return base64.b64decode(encoded)


def test_image_hash_order_is_preserved(monkeypatch, tmp_path):
    images = _write_images(tmp_path, 3)
    ordered = (images[2], images[0], images[1])
    _fake_transport(monkeypatch)

    evidence = visual_understand.understand_ordered(
        ordered,
        prompt="Read every page in order.",
        model="gpt-5.6-sol",
    )

    assert evidence.image_paths == tuple(str(path) for path in ordered)
    assert evidence.image_hashes == tuple(
        hashlib.sha256(path.read_bytes()).hexdigest() for path in ordered
    )


def test_request_uses_separate_original_blocks_not_a_contact_sheet(monkeypatch, tmp_path):
    images = _write_images(tmp_path, 3)
    calls = _fake_transport(monkeypatch)

    visual_understand.understand_ordered(
        images,
        prompt="Read every page in order.",
        model="gpt-5.6-sol",
    )

    assert len(calls) == 1
    blocks = _image_blocks(calls[0])
    assert len(blocks) == 3
    assert [_data_url_bytes(block["image_url"]) for block in blocks] == [
        path.read_bytes() for path in images
    ]


def test_explicit_model_is_required_and_aliases_are_rejected(monkeypatch, tmp_path):
    images = _write_images(tmp_path, 1)
    calls = _fake_transport(monkeypatch)

    with pytest.raises(AstridError, match="rejects model alias"):
        visual_understand.understand_ordered(images, prompt="Read.", model="best")
    assert calls == []

    evidence = visual_understand.understand_ordered(
        images,
        prompt="Read.",
        model="gpt-5.6-sol",
    )
    assert evidence.model == "gpt-5.6-sol"
    assert calls[0]["payload"]["model"] == "gpt-5.6-sol"


def test_cost_ceiling_is_hard_and_boundary_is_allowed(monkeypatch, tmp_path):
    images = _write_images(tmp_path, 5)
    calls = _fake_transport(monkeypatch)

    with pytest.raises(AstridError, match="cost ceiling exceeded"):
        visual_understand.understand_ordered(
            images,
            prompt="Read.",
            model="gpt-5.6-sol",
            settings={"cost_ceiling": 4},
        )
    assert calls == []

    evidence = visual_understand.understand_ordered(
        images[:4],
        prompt="Read.",
        model="gpt-5.6-sol",
        settings={"cost_ceiling": 4},
    )
    assert evidence.cost_ceiling == 4
    assert evidence.settings["cost_ceiling"] == 4
    assert len(_image_blocks(calls[0])) == 4


def test_full_request_and_response_provenance_is_recorded(monkeypatch, tmp_path):
    images = _write_images(tmp_path, 2)
    calls = _fake_transport(monkeypatch)
    prompt = "Answer the fixture questions."

    evidence = visual_understand.understand_ordered(
        images,
        prompt=prompt,
        model="gpt-5.6-sol",
        settings={"cost_ceiling": 2, "detail": "low", "max_output_tokens": 321},
        structured=ANSWER_SCHEMA,
    )

    assert evidence.prompt_sha256 == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert evidence.image_hashes == tuple(
        hashlib.sha256(path.read_bytes()).hexdigest() for path in images
    )
    assert evidence.response_id == "resp_ordered_123"
    assert evidence.returned_model == "gpt-5.6-sol-2026-08-01"
    assert evidence.usage == {"input_tokens": 31, "output_tokens": 4, "total_tokens": 35}
    assert evidence.answers == {"answer": "ok"}
    assert evidence.settings["detail"] == "low"
    assert evidence.settings["max_output_tokens"] == 321
    assert evidence.settings["structured"]["schema"] == ANSWER_SCHEMA
    assert calls[0]["payload"]["text"]["format"]["type"] == "json_schema"


def test_structured_answers_are_validated_client_side(monkeypatch, tmp_path):
    images = _write_images(tmp_path, 1)
    calls = _fake_transport(
        monkeypatch,
        response={
            "id": "resp_bad",
            "model": "gpt-5.6-sol-2026-08-01",
            "output_text": json.dumps({"wrong": 42}),
            "usage": {"total_tokens": 10},
        },
    )

    with pytest.raises(AstridError, match="client-side schema validation"):
        visual_understand.understand_ordered(
            images,
            prompt="Read.",
            model="gpt-5.6-sol",
            structured=ANSWER_SCHEMA,
        )
    assert len(calls) == 1


def test_evidence_serialization_is_byte_stable(monkeypatch, tmp_path):
    images = _write_images(tmp_path, 2)
    _fake_transport(monkeypatch)

    evidence = visual_understand.understand_ordered(
        images,
        prompt="Read deterministically.",
        model="gpt-5.6-sol",
        settings={"cost_ceiling": 2, "detail": "high"},
        structured=ANSWER_SCHEMA,
    )

    first_dict = evidence.to_dict()
    second_dict = evidence.to_dict()
    assert first_dict == second_dict
    assert evidence.to_json_bytes() == evidence.to_json_bytes()
    assert b"created_at" not in evidence.to_json_bytes()
    assert json.loads(evidence.to_json()) == first_dict


def test_single_image_receipt_harvests_result_only(monkeypatch, tmp_path):
    image = _write_images(tmp_path, 1)[0]
    out_dir = tmp_path / "one-image"
    manifest_path = _run_visual(image=image, out_dir=out_dir, monkeypatch=monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["kind"] == "understanding.visual_understand"
    assert manifest["schema_version"] == 1
    assert manifest["inputs"]["images"] == [str(image)]
    assert manifest["inputs"]["image_hashes"] == [hashlib.sha256(image.read_bytes()).hexdigest()]
    assert [entry["name"] for entry in manifest["outputs"]] == ["result"]
    assert manifest["outputs"][0]["output_port"] == "result"
    assert manifest["outputs"][0]["path"] == "result.json"
    assert manifest["outputs"][0]["role"] == "result"
    assert manifest["outputs"][0]["is_primary"] is True
    assert not any(entry["path"] == str(image) for entry in manifest["outputs"])

    definition = _visual_definition()
    assert [output.name for output in definition.outputs] == [
        "result",
        "manifest",
        "contact_sheet",
        "crop_contact_sheet",
        "crops",
        "frames",
    ]
    harvested = harvest_staged_outputs(
        out_dir,
        definition=definition,
        declared_outputs=definition.outputs,
    )
    assert [item["name"] for item in harvested] == ["result"]
    result = harvested[0]
    assert result["output_port"] == "result"
    assert result["role"] == "result"
    assert result["is_primary"] is True
    assert Path(result["path"]).resolve().relative_to(out_dir.resolve()).as_posix() == "result.json"
    assert result["bytes"] == (out_dir / "result.json").stat().st_size
    assert result["content_hash"] == "sha256" + ":" + hashlib.sha256(
        (out_dir / "result.json").read_bytes()
    ).hexdigest()
    assert Path(image).resolve() not in {
        Path(item["path"]).resolve() for item in harvested
    }


def test_crop_receipt_harvests_contact_sheet_and_crop_files(monkeypatch, tmp_path):
    image = _write_images(tmp_path, 1)[0]
    out_dir = tmp_path / "crop-output"
    manifest_path = _run_visual(
        image=image,
        out_dir=out_dir,
        monkeypatch=monkeypatch,
        extra_args=["--crop-aspect", "1:1", "--crop-position", "center"],
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert [entry["name"] for entry in manifest["outputs"]] == [
        "result",
        "crop_contact_sheet",
        "crops",
    ]
    assert [entry["output_port"] for entry in manifest["outputs"]] == [
        "result",
        "crop_contact_sheet",
        "crops",
    ]
    assert [entry["path"] for entry in manifest["outputs"]] == [
        "result.json",
        "crop-contact-sheet.jpg",
        "crops",
    ]
    crop_inventory = manifest["outputs"][2]
    assert crop_inventory["type"] == "directory"
    assert len(crop_inventory["entries"]) == 1
    crop_entry = crop_inventory["entries"][0]
    crop_relative = Path("crops") / crop_entry["path"]
    crop_path = out_dir / crop_relative
    assert crop_path.is_file()
    assert crop_entry["bytes"] == crop_path.stat().st_size
    assert crop_entry["content_hash"] == "sha256:" + hashlib.sha256(
        crop_path.read_bytes()
    ).hexdigest()
    assert not any(entry["path"] == str(image) for entry in manifest["outputs"])

    definition = _visual_definition()
    harvested = harvest_staged_outputs(
        out_dir,
        definition=definition,
        declared_outputs=definition.outputs,
    )
    assert [item["name"] for item in harvested] == [
        "result",
        "crop_contact_sheet",
        "crops",
    ]
    assert [
        Path(item["path"]).resolve().relative_to(out_dir.resolve()).as_posix()
        for item in harvested
    ] == ["result.json", "crop-contact-sheet.jpg", crop_relative.as_posix()]
    for item in harvested:
        path = Path(item["path"])
        assert path.resolve().is_relative_to(out_dir.resolve())
        assert item["bytes"] == path.stat().st_size
        assert item["content_hash"] == "sha256:" + hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        assert item["output_port"] == item["name"]


def test_video_receipt_harvests_contact_sheet_and_extracted_frames(
    monkeypatch, tmp_path
):
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"synthetic video input")

    def extract_frames(_video, times, out_dir, _force):
        frame_dir = out_dir / "frames"
        frame_dir.mkdir(parents=True)
        frames = []
        for index, seconds in enumerate(times, start=1):
            path = frame_dir / f"frame-{index}.png"
            Image.new("RGB", (8, 8), (index * 20, 40, 160)).save(path)
            frames.append((path, f"{seconds:.2f}"))
        return frames

    monkeypatch.setattr(visual_understand, "_extract_video_frames", extract_frames)
    _fake_transport(monkeypatch)
    out_dir = tmp_path / "video-output"
    out_path = out_dir / "result.json"
    args = visual_understand.build_parser().parse_args(
        [
            "--query",
            "Describe the frames.",
            "--video",
            str(video),
            "--at",
            "0,1",
            "--out-dir",
            str(out_dir),
            "--out",
            str(out_path),
        ]
    )
    assert visual_understand.run(args) == 0
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))

    assert [entry["name"] for entry in manifest["outputs"]] == [
        "result",
        "contact_sheet",
        "frames",
    ]
    frame_inventory = manifest["outputs"][2]
    assert frame_inventory["type"] == "directory"
    assert [entry["path"] for entry in frame_inventory["entries"]] == [
        "frame-1.png",
        "frame-2.png",
    ]

    definition = _visual_definition()
    harvested = harvest_staged_outputs(
        out_dir,
        definition=definition,
        declared_outputs=definition.outputs,
    )
    assert [item["name"] for item in harvested] == [
        "result",
        "contact_sheet",
        "frames",
        "frames",
    ]
    assert [
        Path(item["path"]).resolve().relative_to(out_dir.resolve()).as_posix()
        for item in harvested
    ] == [
        "result.json",
        "contact-sheet.jpg",
        "frames/frame-1.png",
        "frames/frame-2.png",
    ]
    for item in harvested:
        path = Path(item["path"])
        assert path.resolve().is_relative_to(out_dir.resolve())
        assert item["bytes"] == path.stat().st_size
        assert item["content_hash"] == "sha256:" + hashlib.sha256(
            path.read_bytes()
        ).hexdigest()


def test_video_receipt_harvests_selected_windows_only(monkeypatch, tmp_path):
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"synthetic video input")
    out_dir = tmp_path / "video-output"
    out_path = out_dir / "result.json"
    stale = out_dir / "video-windows/window_999_000000000_000001000.mp4"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale window")
    reused = out_dir / "video-windows/window_002_000003000_000005000.mp4"
    reused.write_bytes(b"reused window")

    extract_calls: list[dict[str, Any]] = []

    def extract_window(
        source: Path,
        window: dict[str, Any],
        destination: Path,
        *,
        force: bool,
        max_width: int,
    ) -> Path:
        assert source == video
        assert max_width == 960
        path = destination / "video-windows" / (
            f"window_{int(window['index']):03d}_"
            f"{int(float(window['start']) * 1000):09d}_"
            f"{int(float(window['end']) * 1000):09d}.mp4"
        )
        extract_calls.append({"window": window, "path": path, "force": force})
        if force or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"window-{window['index']}".encode("ascii"))
        return path

    describe_calls: list[dict[str, Any]] = []

    class FakeGeminiClient:
        def describe_video(self, **kwargs: Any) -> dict[str, Any]:
            describe_calls.append(kwargs)
            return {"answer": "ok"}

    monkeypatch.setattr(
        video_understand,
        "ffprobe_duration_seconds",
        lambda *_args, **_kwargs: 8.0,
    )
    monkeypatch.setattr(video_understand, "_extract_window", extract_window)
    monkeypatch.setattr(
        video_understand,
        "build_gemini_client",
        lambda _env_file: FakeGeminiClient(),
    )

    query = "Describe the synchronized evidence."
    args = video_understand.build_parser().parse_args(
        [
            "--query",
            query,
            "--video",
            str(video),
            "--at",
            "1,4",
            "--window-sec",
            "2",
            "--mode",
            "best",
            "--model",
            "gemini-custom",
            "--compare-model",
            "gemini-compare",
            "--out-dir",
            str(out_dir),
            "--out",
            str(out_path),
        ]
    )

    assert video_understand.run(args) == 0
    selected = (
        out_dir / "video-windows/window_001_000000000_000002000.mp4",
        reused,
    )
    assert [call["path"] for call in extract_calls] == list(selected)
    assert [call["video_path"] for call in describe_calls] == list(selected) * 2

    result_payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert result_payload["provider"] == "gemini"
    assert result_payload["source"] == str(video)
    assert result_payload["source_kind"] == "video"
    assert result_payload["duration_sec"] == 8.0
    assert result_payload["query"] == query
    assert [(window["start"], window["end"]) for window in result_payload["windows"]] == [
        (0.0, 2.0),
        (3.0, 5.0),
    ]
    assert [Path(window["path"]) for window in result_payload["windows"]] == list(selected)
    assert [result["status"] for result in result_payload["results"]] == ["ok"] * 4

    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["kind"] == "understanding.video_understand"
    assert manifest["inputs"]["video"] == str(video)
    assert manifest["inputs"]["query"] == query
    assert manifest["inputs"]["mode"] == "best"
    assert manifest["inputs"]["model"] == "gemini-custom"
    assert manifest["inputs"]["compare_model"] == ["gemini-compare"]
    assert manifest["inputs"]["at"] == ["1,4"]
    assert manifest["inputs"]["start"] is None
    assert manifest["inputs"]["end"] is None
    assert manifest["inputs"]["window_sec"] == 2.0
    assert manifest["inputs"]["chunk_sec"] == 30.0
    assert manifest["inputs"]["max_chunks"] == 8
    assert manifest["inputs"]["max_width"] == 960
    assert manifest["inputs"]["out_dir"] == str(out_dir)

    expected_relative = [
        "result.json",
        "video-windows/window_001_000000000_000002000.mp4",
        "video-windows/window_002_000003000_000005000.mp4",
    ]
    assert [entry["name"] for entry in manifest["outputs"]] == [
        "result",
        "windows",
        "windows",
    ]
    assert [entry["output_port"] for entry in manifest["outputs"]] == [
        "result",
        "windows",
        "windows",
    ]
    assert [entry["path"] for entry in manifest["outputs"]] == expected_relative
    assert [entry["ordinal"] for entry in manifest["outputs"]] == [0, 1, 2]
    assert [entry["role"] for entry in manifest["outputs"]] == [
        "result",
        "result",
        "result",
    ]
    assert [entry["is_primary"] for entry in manifest["outputs"]] == [True, False, False]
    assert not any(entry["path"] == "manifest.json" for entry in manifest["outputs"])
    assert not any("window_999" in entry["path"] for entry in manifest["outputs"])
    assert not any(str(video) == entry["path"] for entry in manifest["outputs"])
    for entry, path in zip(manifest["outputs"], (out_path, *selected)):
        assert entry["bytes"] == path.stat().st_size
        assert entry["content_hash"] == "sha256:" + hashlib.sha256(
            path.read_bytes()
        ).hexdigest()

    definition = _video_definition()
    assert [output.name for output in definition.outputs] == ["windows", "result", "manifest"]
    harvested = harvest_staged_outputs(
        out_dir,
        definition=definition,
        declared_outputs=definition.outputs,
    )
    assert [
        (
            item["name"],
            Path(item["path"]).resolve().relative_to(out_dir.resolve()).as_posix(),
            item["role"],
            item["is_primary"],
        )
        for item in harvested
    ] == [
        ("result", "result.json", "result", True),
        (
            "windows",
            "video-windows/window_001_000000000_000002000.mp4",
            "result",
            False,
        ),
        (
            "windows",
            "video-windows/window_002_000003000_000005000.mp4",
            "result",
            False,
        ),
    ]
    assert {
        Path(item["path"]).resolve() for item in harvested
    } == {out_path.resolve(), *(path.resolve() for path in selected)}
    assert all(
        item["bytes"] == Path(item["path"]).stat().st_size
        and item["content_hash"] == "sha256:" + hashlib.sha256(
            Path(item["path"]).read_bytes()
        ).hexdigest()
        for item in harvested
    )
    assert all(Path(item["path"]).resolve().is_relative_to(out_dir.resolve()) for item in harvested)
    assert stale.resolve() not in {Path(item["path"]).resolve() for item in harvested}
    assert video.resolve() not in {Path(item["path"]).resolve() for item in harvested}


def test_custom_result_path_stays_standalone_and_outside_spool(
    monkeypatch, tmp_path
):
    image = _write_images(tmp_path, 1)[0]
    _fake_transport(monkeypatch)
    out_dir = tmp_path / "host-output"
    custom_result = tmp_path / "standalone" / "answer.json"
    args = visual_understand.build_parser().parse_args(
        [
            "--query",
            "Describe the image.",
            "--image",
            str(image),
            "--out-dir",
            str(out_dir),
            "--out",
            str(custom_result),
        ]
    )

    assert visual_understand.run(args) == 0
    assert custom_result.is_file()
    standalone_manifest = custom_result.parent / "manifest.json"
    manifest = json.loads(standalone_manifest.read_text(encoding="utf-8"))
    assert manifest["outputs"] == []
    assert not out_dir.exists()
