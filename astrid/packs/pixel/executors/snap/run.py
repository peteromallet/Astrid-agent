#!/usr/bin/env python3
"""Snap a managed image onto a logical pixel grid (pixel.snap).

Invoked by the Astrid runtime per command.argv in executor.yaml. The heavy
lifting lives in ``astrid.packs.pixel.executors._common``.
"""

from __future__ import annotations

from astrid.core.pack.entrypoint import guard_canonical_entrypoint, run_pack_main

guard_canonical_entrypoint("pixel.snap")

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from astrid.core._shared.result_manifest import build_manifest, write_manifest
from astrid.core.contracts.errors import AstridError
from astrid.packs.pixel.executors import _common as px


def _boolean(value: str) -> bool:
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {value!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pixel.snap",
        description="Snap an image onto a logical pixel grid with mode downsampling.",
    )
    parser.add_argument("--image", type=Path, required=True, help="Source image (managed path).")
    parser.add_argument("--out", type=Path, required=True, help="Output directory.")
    parser.add_argument("--crop", default=None, help="Optional {x,y,width,height} in source pixels.")
    parser.add_argument("--auto-grid", type=_boolean, default=False, help="Detect the pixel lattice.")
    parser.add_argument("--grid-width", type=int, default=320, help="Explicit grid width in cells.")
    parser.add_argument("--grid-height", type=int, default=180, help="Explicit grid height in cells.")
    parser.add_argument("--fit", choices=("cover", "contain", "none"), default="cover")
    parser.add_argument("--palette", default=None, help="Preset (astrid) or comma-separated hex colours.")
    parser.add_argument("--max-colors", type=int, default=0, help="Quantise to N colours when no palette.")
    parser.add_argument("--scale", type=int, default=6, help="Integer preview scale factor.")
    parser.add_argument("--dither", type=_boolean, default=False, help="Floyd-Steinberg dithering.")
    return parser


def main(argv: list[str] | None = None) -> int:
    def _run() -> int:
        args = build_parser().parse_args(argv)
        source = args.image.expanduser().resolve()
        if not source.is_file():
            raise AstridError(
                f"image not found: {source}",
                recovery_command="import the image with python -m astrid media import and pass its managed digest",
            )
        if args.scale < 1:
            raise AstridError("scale must be an integer >= 1", recovery_command="use --scale 1 or larger")
        out_dir = args.out.expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        crop = px.parse_crop(args.crop)
        source_arr = px.load_rgba(str(source))
        arr = px.apply_crop(source_arr, crop)
        native, preview, report = px.snap_image(
            arr,
            grid_width=args.grid_width,
            grid_height=args.grid_height,
            auto_grid=bool(args.auto_grid),
            fit=args.fit,
            palette=args.palette,
            max_colors=int(args.max_colors or 0),
            scale=int(args.scale),
            dither=bool(args.dither),
        )
        report["crop"] = crop
        report["source_size"] = {"width": int(source_arr.shape[1]), "height": int(source_arr.shape[0])}

        px.save_rgba(native, str(out_dir / "native.png"))
        px.save_rgba(preview, str(out_dir / "preview.png"))
        (out_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        manifest = build_manifest(
            kind="pixel_snap",
            inputs={
                "image": source.name,
                "crop": crop,
                "auto_grid": bool(args.auto_grid),
                "grid": [args.grid_width, args.grid_height],
                "fit": args.fit,
                "palette": args.palette,
                "max_colors": int(args.max_colors or 0),
                "scale": int(args.scale),
                "dither": bool(args.dither),
            },
            outputs=[
                {"name": "native", "path": "native.png", "type": "file", "artifact_type": "image", "role": "result", "is_primary": True},
                {"name": "preview", "path": "preview.png", "type": "file", "artifact_type": "image", "role": "result"},
                {"name": "report", "path": "report.json", "type": "file", "role": "auxiliary"},
            ],
            created=datetime.now(timezone.utc).isoformat(),
        )
        write_manifest(out_dir / "manifest.json", manifest)
        return 0

    return run_pack_main("pixel.snap", _run, argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
