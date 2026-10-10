"""pixel.snap palette: a hex list and a comma string both reach the CLI as one palette."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from astrid.core.contracts.binding import expand_command
from astrid.packs.pixel.executors.snap import run as snap_run

SNAP_YAML = Path(__file__).resolve().parents[3] / "astrid" / "packs" / "pixel" / "executors" / "snap" / "executor.yaml"
PALETTE = ["#25241F", "#ED6B23", "#FFFFFF"]


def _argv(palette):
    spec = yaml.safe_load(SNAP_YAML.read_text(encoding="utf-8"))
    values = {"image": "/tmp/in.png", "out": "/tmp/out", "python_exec": "python", "palette": palette}
    return expand_command(spec["command"], spec["inputs"], values, spec.get("metadata")).argv


def _palette_values(argv) -> list[str]:
    return [argv[i + 1] for i, part in enumerate(argv) if part == "--palette"]


def test_palette_list_expands_to_repeated_flags_and_string_stays_one_flag() -> None:
    assert _palette_values(_argv(PALETTE)) == PALETTE
    assert _palette_values(_argv(",".join(PALETTE))) == [",".join(PALETTE)]


def test_repeated_palette_flags_are_joined_into_one_palette_for_the_cli() -> None:
    args = snap_run.build_parser().parse_args(
        ["--image", "x.png", "--out", "o", "--palette", "#25241F", "--palette", "#ED6B23"]
    )
    assert snap_run._palette_text(args.palette) == "#25241F,#ED6B23"
    comma = snap_run.build_parser().parse_args(["--image", "x.png", "--out", "o", "--palette", "#25241F,#ED6B23"])
    assert snap_run._palette_text(comma.palette) == "#25241F,#ED6B23"
    assert snap_run._palette_text(None) is None


def test_palette_list_snaps_with_the_palette_and_comma_string_gives_same_report(tmp_path: Path, monkeypatch) -> None:
    import numpy as np
    from PIL import Image

    monkeypatch.setenv("ASTRID_INTERNAL_INVOCATION", "1")
    source = tmp_path / "source.png"
    Image.fromarray(np.full((32, 32, 4), 255, dtype=np.uint8)).save(source)

    def run(out: str, palette_args: list[str]) -> dict:
        target = tmp_path / out
        assert snap_run.main(["--image", str(source), "--out", str(target), "--grid-width", "8",
                              "--grid-height", "8", "--fit", "none", "--scale", "1", *palette_args]) == 0
        return json.loads((target / "report.json").read_text(encoding="utf-8"))

    from_list = run("list", [item for colour in PALETTE for item in ("--palette", colour)])
    from_string = run("string", ["--palette", ",".join(PALETTE)])
    assert from_list["palette"] == from_string["palette"]
    assert from_list["palette"]["source"] == "custom"
