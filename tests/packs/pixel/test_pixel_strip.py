from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from astrid.core.contracts.errors import AstridError
from astrid.packs.pixel.executors.strip import run as strip_run

PACK_ROOT = Path(__file__).resolve().parents[3] / "astrid" / "packs" / "pixel"


def _frame(width: int, height: int, colour: tuple[int, int, int]) -> np.ndarray:
    arr = np.zeros((height, width, 4), dtype=np.uint8)
    arr[..., :3] = colour
    arr[..., 3] = 255
    return arr


def _save(path: Path, arr: np.ndarray) -> Path:
    Image.fromarray(arr, "RGBA").save(path)
    return path


def test_strip_joins_frames_left_to_right_on_their_own_grid() -> None:
    red = _frame(4, 3, (255, 0, 0))
    blue = _frame(4, 3, (0, 0, 255))
    strip = strip_run.build_strip([red, blue])
    assert strip.shape == (3, 8, 4)
    assert tuple(strip[0, 0, :3]) == (255, 0, 0)
    assert tuple(strip[0, 4, :3]) == (0, 0, 255)


def test_strip_pads_trimmed_frames_onto_one_cell_on_a_shared_ground_line() -> None:
    # pixel.cutout's default trim made a claw loop 31x64 and 51x65 (S24)
    narrow, wide = _frame(31, 64, (255, 0, 0)), _frame(51, 65, (0, 0, 255))
    strip = strip_run.build_strip([narrow, wide])
    assert strip.shape == (65, 102, 4)  # one 51x65 cell per frame
    # the narrow frame sits centred, its feet on the cell's bottom row, transparent around it
    assert strip[64, 10, 3] == 255 and strip[0, 10, 3] == 0 and strip[64, 0, 3] == 0
    aligned, placed = strip_run.align_frames([narrow, wide], align="bottom")
    assert placed[0] == {"width": 31, "height": 64, "x": 10, "y": 1}
    _, top = strip_run.align_frames([narrow, wide], align="top")
    assert top[0]["y"] == 0


def test_strip_keeps_one_scale_when_mismatched_frames_snap_to_a_grid() -> None:
    small, big = _frame(4, 6, (10, 20, 30)), _frame(8, 6, (10, 20, 30))
    strip = strip_run.build_strip([small, big], grid_width=4, grid_height=3, fit="contain")
    assert strip.shape == (3, 8, 4)
    # the small frame was padded to 8x6 first, so it covers half the cell width, not all of it
    assert strip[1, 0, 3] == 0 and strip[1, 1, 3] == 255


def test_strip_snaps_mismatched_frames_onto_one_explicit_grid() -> None:
    small = _frame(8, 6, (10, 20, 30))
    big = _frame(16, 12, (10, 20, 30))
    strip = strip_run.build_strip([small, big], grid_width=4, grid_height=3)
    assert strip.shape == (3, 8, 4)


def test_strip_requires_grid_width_and_height_together() -> None:
    with pytest.raises(AstridError):
        strip_run.build_strip([_frame(4, 3, (0, 0, 0)), _frame(4, 3, (0, 0, 0))], grid_width=4)


def test_strip_needs_at_least_two_frames() -> None:
    for frames in ([], [_frame(4, 3, (0, 0, 0))]):
        with pytest.raises(AstridError) as caught:
            strip_run.build_strip(frames)
        assert "at least 2 frames" in str(caught.value)


def test_metadata_carries_am_sprite_frames_block() -> None:
    meta = strip_run.build_metadata(
        frame_w=4, frame_h=3, count=2, fps=6.0, sources=["a.png", "b.png"], grid=(4, 3), fit="cover"
    )
    assert meta["am_sprite_frames"] == {"frameWidth": 4, "frameHeight": 3, "count": 2, "fps": 6.0}
    assert [entry["x"] for entry in meta["frames"]] == [0, 4]


def test_strip_cli_writes_strip_metadata_and_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASTRID_INTERNAL_INVOCATION", "1")
    first = _save(tmp_path / "claw-a.png", _frame(4, 3, (255, 0, 0)))
    second = _save(tmp_path / "claw-b.png", _frame(4, 3, (0, 255, 0)))
    out = tmp_path / "out"
    code = strip_run.main(["--frame", str(first), "--frame", str(second), "--out", str(out), "--fps", "4"])
    assert code == 0
    strip = np.asarray(Image.open(out / "strip.png"))
    assert strip.shape == (3, 8, 4)
    meta = json.loads((out / "strip.json").read_text(encoding="utf-8"))
    assert meta["am_sprite_frames"]["count"] == 2
    assert meta["am_sprite_frames"]["fps"] == 4.0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "pixel_strip"
    assert [entry["path"] for entry in manifest["outputs"]] == ["strip.png", "strip.json"]


def test_strip_cli_records_each_frames_placement(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASTRID_INTERNAL_INVOCATION", "1")
    first = _save(tmp_path / "a.png", _frame(31, 64, (255, 0, 0)))
    second = _save(tmp_path / "b.png", _frame(51, 65, (0, 255, 0)))
    out = tmp_path / "out"
    assert strip_run.main(["--frame", str(first), "--frame", str(second), "--out", str(out), "--align", "center"]) == 0
    meta = json.loads((out / "strip.json").read_text(encoding="utf-8"))
    assert meta["align"] == "center" and meta["am_sprite_frames"]["frameWidth"] == 51
    assert meta["frames"][0]["placed"] == {"width": 31, "height": 64, "x": 10, "y": 0}


def test_strip_executor_declares_repeatable_frames_and_outputs() -> None:
    import yaml

    manifest = yaml.safe_load((PACK_ROOT / "executors" / "strip" / "executor.yaml").read_text(encoding="utf-8"))
    assert manifest["id"] == "pixel.strip"
    frame = next(port for port in manifest["inputs"] if port["name"] == "frame")
    assert frame["type"] == "file" and frame["required"] is True and frame["repeatable"] is True
    repeat = next(arg for arg in manifest["command"]["input_args"] if arg["input"] == "frame")
    assert repeat["flag"] == "--frame" and repeat["repeatable"] is True
    assert {port["name"] for port in manifest["outputs"]} == {"strip", "metadata"}
    assert manifest["metadata"]["output_result_manifest"] is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
