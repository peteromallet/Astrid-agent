"""pixel.strip palette: a hex list and a comma string both reach the CLI as one palette."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from astrid.core.contracts.binding import expand_command
from astrid.packs.pixel.executors.strip import run as strip_run

STRIP_YAML = Path(__file__).resolve().parents[3] / "astrid" / "packs" / "pixel" / "executors" / "strip" / "executor.yaml"
PALETTE = ["#25241F", "#ED6B23", "#FFFFFF"]


def _argv(palette):
    spec = yaml.safe_load(STRIP_YAML.read_text(encoding="utf-8"))
    values = {"frame": ["/tmp/a.png", "/tmp/b.png"], "out": "/tmp/out", "python_exec": "python", "palette": palette}
    return expand_command(spec["command"], spec["inputs"], values, spec.get("metadata")).argv


def _palette_values(argv) -> list[str]:
    return [argv[i + 1] for i, part in enumerate(argv) if part == "--palette"]


def test_palette_list_expands_to_repeated_flags_and_string_stays_one_flag() -> None:
    assert _palette_values(_argv(PALETTE)) == PALETTE
    assert _palette_values(_argv(",".join(PALETTE))) == [",".join(PALETTE)]


def test_repeated_palette_flags_are_joined_into_one_palette_for_the_cli() -> None:
    args = strip_run.build_parser().parse_args(
        ["--frame", "a.png", "--frame", "b.png", "--out", "o", "--palette", "#25241F", "--palette", "#ED6B23"]
    )
    assert strip_run._palette_text(args.palette) == "#25241F,#ED6B23"
    comma = strip_run.build_parser().parse_args(
        ["--frame", "a.png", "--frame", "b.png", "--out", "o", "--palette", "#25241F,#ED6B23"]
    )
    assert strip_run._palette_text(comma.palette) == "#25241F,#ED6B23"
    assert strip_run._palette_text(None) is None


def test_palette_list_and_comma_string_give_same_strip_metadata(tmp_path: Path, monkeypatch) -> None:
    import numpy as np
    from PIL import Image

    monkeypatch.setenv("ASTRID_INTERNAL_INVOCATION", "1")
    frames = []
    for name in ("a.png", "b.png"):
        path = tmp_path / name
        Image.fromarray(np.full((8, 8, 4), 255, dtype=np.uint8)).save(path)
        frames.append(path)

    def run(out: str, palette_args: list[str]) -> dict:
        target = tmp_path / out
        argv = ["--out", str(target), "--grid-width", "4", "--grid-height", "4", "--fit", "none", "--fps", "8"]
        for frame in frames:
            argv += ["--frame", str(frame)]
        assert strip_run.main([*argv, *palette_args]) == 0
        return {
            "metadata": json.loads((target / "strip.json").read_text(encoding="utf-8")),
            "palette": json.loads((target / "manifest.json").read_text(encoding="utf-8"))["inputs"]["palette"],
            "pixels": (target / "strip.png").read_bytes(),
        }

    from_list = run("list", [item for colour in PALETTE for item in ("--palette", colour)])
    from_string = run("string", ["--palette", ",".join(PALETTE)])
    assert from_list["palette"] == from_string["palette"] == ",".join(PALETTE)
    assert from_list["metadata"] == from_string["metadata"]
    assert from_list["pixels"] == from_string["pixels"]
