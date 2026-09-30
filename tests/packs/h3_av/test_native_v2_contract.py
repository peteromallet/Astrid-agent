from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml
from PIL import Image

from astrid.packs.h3_av.src.compile import compile_preparation
from astrid.packs.h3_av.src.prepare import prepare_request
from astrid.packs.h3_av.src.request import normalize_request
from astrid.packs.h3_av.src.request_v2 import branch_for
from vibecomfy.cli_loader import load_workflow_any


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
            {"id": "source", "asset": "source.mp4", "role": "timeline", "modality": "video", "at": {"frame": 0}, "range": [0, 4], "edit": [{"stream": "video", "during": [1, 2], "mask": {"full_frame": True}, "guides": ["look"]}]},
            {"id": "look", "asset": "look.png", "role": "reference", "modality": "image"},
        ]
    return {"version": 2, "prompt": f"Exercise {branch}.", "duration": 5, "continuation": branch == "extension_context", "media": media, "settings": {}}


def test_native_v2_schema_profile_and_four_branch_bindings(tmp_path: Path) -> None:
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
        compiled = compile_preparation(prepared, out_dir=tmp_path / branch)
        assert compiled["profile"] == "h3_av.native.v2"
        assert compiled["branch"] == branch
        assert compiled["capabilities"]["output_contract"] == "muxed_av_full_timeline"
        workflow_path = Path(compiled["workflow"]["workflow.py"]["path"])
        workflow = load_workflow_any(str(workflow_path))
        assert set(compiled["workflow_inputs"]).issubset(workflow.inputs)
        api = workflow.compile("api", run_inputs=compiled["workflow_inputs"])
        assert isinstance(api, dict) and api
        assert workflow.validate().ok


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
        assert normalize_request(request).value["version"] == 1


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
