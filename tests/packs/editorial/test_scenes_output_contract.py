from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from astrid.core._shared.result_manifest import harvest_staged_outputs
from astrid.core.execution.generic_host import GenericPackHost
from astrid.packs.editorial.actions.scenes import run as scenes_run


PACK_ROOT = Path(__file__).resolve().parents[3] / "astrid" / "packs"
EXPECTED_OUTPUTS = {
    "scenes": ("scenes.json", True),
    "scene_items": ("scene_items.json", False),
}


def test_scenes_emits_harvestable_typed_result_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"tiny video placeholder")
    out = tmp_path / "attempt" / "outputs"
    expected_scenes = [
        {"index": 1, "start": 0.0, "end": 1.25, "duration": 1.25},
        {"index": 2, "start": 1.25, "end": 2.5, "duration": 1.25},
    ]

    def fake_detect_scenes(
        video_path: Path, threshold: float
    ) -> list[dict[str, float | int]]:
        assert video_path == video.resolve()
        assert threshold == 27.0
        return expected_scenes

    monkeypatch.setattr(scenes_run, "detect_scenes", fake_detect_scenes)

    assert (
        scenes_run.main(
            [
                "--video",
                str(video),
                "--out",
                str(out),
            ]
        )
        == 0
    )

    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [
        (entry["name"], entry["path"], entry["ordinal"], entry["role"], entry["is_primary"])
        for entry in manifest["outputs"]
    ] == [
        ("scenes", "scenes.json", 0, "result", True),
        ("scene_items", "scene_items.json", 1, "result", False),
    ]
    assert all(not Path(entry["path"]).is_absolute() for entry in manifest["outputs"])
    assert "scenes.csv" not in [entry["path"] for entry in manifest["outputs"]]

    for ordinal, (name, (relative_path, is_primary)) in enumerate(EXPECTED_OUTPUTS.items()):
        entry = manifest["outputs"][ordinal]
        concrete = out / relative_path
        assert entry["name"] == name
        assert entry["path"] == relative_path
        assert entry["ordinal"] == ordinal
        assert entry["is_primary"] is is_primary
        assert entry["content_hash"] == f"sha256:{hashlib.sha256(concrete.read_bytes()).hexdigest()}"
        assert entry["bytes"] == concrete.stat().st_size
        assert entry["bytes"] >= 0

    assert (out / "scenes.csv").is_file()
    assert (out / "scenes.json").read_bytes() == json.dumps(expected_scenes, indent=2).encode()
    assert (out / "scene_items.json").read_bytes() == json.dumps(
        ["scene-0001", "scene-0002"], indent=2
    ).encode()

    capability_matrix = tmp_path / "empty-capability-matrix.json"
    capability_matrix.write_text(
        json.dumps({"schema_version": 1, "capabilities": []}),
        encoding="utf-8",
    )
    host = GenericPackHost(pack_roots=[PACK_ROOT], capability_matrix=capability_matrix)
    host.discover()
    record = host.capabilities["editorial.scenes"]
    harvested = harvest_staged_outputs(
        out,
        definition=record.definition,
        declared_outputs=record.definition.outputs,
        require=True,
    )

    assert [
        (item["name"], Path(item["path"]).relative_to(out).as_posix(), item["ordinal"])
        for item in harvested
    ] == [
        ("scenes", "scenes.json", 0),
        ("scene_items", "scene_items.json", 1),
    ]
    assert all(item["path"] != str(out / "scenes.csv") for item in harvested)

    typed = host._typed_outputs(record, harvested, out.parent)
    typed_by_name = {item["name"]: item for item in typed}
    declared_by_name = {output.name: output for output in record.definition.outputs}
    assert set(typed_by_name) == set(EXPECTED_OUTPUTS)
    for name, (relative_path, _is_primary) in EXPECTED_OUTPUTS.items():
        bound = typed_by_name[name]
        entry = manifest["outputs"][bound["ordinal"]]
        concrete = out / relative_path
        assert bound["artifact_type"] == declared_by_name[name].artifact_type
        assert bound["filename"] == relative_path
        assert bound["ordinal"] == list(EXPECTED_OUTPUTS).index(name)
        assert bound["digest"] == entry["content_hash"]
        assert bound["size"] == entry["bytes"]
        assert bound["path"] == str(concrete.resolve())
        assert concrete.read_bytes() == Path(bound["path"]).read_bytes()
