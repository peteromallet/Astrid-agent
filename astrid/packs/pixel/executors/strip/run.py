#!/usr/bin/env python3
"""Pack same-grid pixel frames into one sprite strip (pixel.strip).

Invoked by the Astrid runtime per command.argv in executor.yaml. Each frame is
snapped to one shared grid with the pixel.snap rules, then the frames are
joined left to right. The metadata's am_sprite_frames block is the value for
am-sprite's frames param (frameWidth, frameHeight, count, fps).
"""

from __future__ import annotations

from astrid.core.pack.entrypoint import guard_canonical_entrypoint, run_pack_main

guard_canonical_entrypoint("pixel.strip")

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from astrid.core._shared.result_manifest import build_manifest, write_manifest
from astrid.core.contracts.errors import AstridError
from astrid.packs.pixel.executors import _common as px


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pixel.strip",
        description="Join same-grid pixel frames into one horizontal sprite strip.",
    )
    parser.add_argument("--frame", action="append", type=Path, required=True, help="Ordered frame image (repeatable).")
    parser.add_argument("--out", type=Path, required=True, help="Output directory.")
    parser.add_argument("--grid-width", type=int, default=0, help="Shared grid width (0 = frame size).")
    parser.add_argument("--grid-height", type=int, default=0, help="Shared grid height (0 = frame size).")
    parser.add_argument("--fit", choices=("cover", "contain", "none"), default="cover")
    parser.add_argument(
        "--palette",
        action="append",
        default=None,
        help="Preset (astrid), comma-separated hex colours, or one hex colour per repeated flag.",
    )
    parser.add_argument("--max-colors", type=int, default=0, help="Quantise all frames to N colours when no palette.")
    parser.add_argument("--fps", type=float, default=8.0, help="Playback rate for am-sprite.")
    parser.add_argument("--align", choices=ALIGNS, default="bottom",
                        help="Anchor for frames of different sizes inside the common cell (default: bottom).")
    return parser


ALIGNS = ("bottom", "center", "top")


def _palette_text(values: list[str] | None) -> str | None:
    """Join repeated ``--palette`` flags (one list of hex colours) into one comma string."""
    if not values:
        return None
    return ",".join(item for value in values for item in value.split(",") if item.strip())


def align_frames(frames: list[np.ndarray], *, align: str = "bottom") -> tuple[list[np.ndarray], list[dict]]:
    """Pad frames of different sizes onto one common cell, transparent outside.

    Trimmed cutouts (pixel.cutout's default) come out at different sizes; a
    strip must keep one scale and one ground line, so every frame is placed in
    the largest width x height, centred horizontally and anchored at the
    bottom (feet), centre or top. Returns the frames and each one's placement.
    """
    if align not in ALIGNS:
        raise AstridError(f"align must be one of {', '.join(ALIGNS)}", recovery_command="use --align bottom")
    cell_h = max(frame.shape[0] for frame in frames)
    cell_w = max(frame.shape[1] for frame in frames)
    placed, placements = [], []
    for frame in frames:
        height, width = frame.shape[:2]
        dx = (cell_w - width) // 2
        dy = {"bottom": cell_h - height, "center": (cell_h - height) // 2, "top": 0}[align]
        placements.append({"width": width, "height": height, "x": dx, "y": dy})
        if (height, width) == (cell_h, cell_w):
            placed.append(frame)
            continue
        canvas = np.zeros((cell_h, cell_w, 4), dtype=np.uint8)
        canvas[dy:dy + height, dx:dx + width] = frame
        placed.append(canvas)
    return placed, placements


def build_strip(
    frames: list[np.ndarray],
    *,
    grid_width: int = 0,
    grid_height: int = 0,
    fit: str = "cover",
    palette: str | None = None,
    max_colors: int = 0,
    align: str = "bottom",
) -> np.ndarray:
    """Align frames on one cell, snap them to one grid and join them left to right (RGBA)."""
    if len(frames) < 2:
        raise AstridError(
            f"pixel.strip needs at least 2 frames (got {len(frames)})",
            recovery_command='pass a list of handles: inputs={"frame": [handle_a, handle_b]}',
        )
    if (grid_width > 0) != (grid_height > 0):
        raise AstridError("grid_width and grid_height must be set together", recovery_command="set both, or neither")
    frames, _placements = align_frames(frames, align=align)
    if grid_width <= 0:
        grid_height, grid_width = frames[0].shape[:2]
    natives = []
    for frame in frames:
        native, _preview, _report = px.snap_image(
            frame,
            grid_width=int(grid_width),
            grid_height=int(grid_height),
            fit=fit,
            palette=palette,
            max_colors=int(max_colors or 0),
            scale=1,
            dither=False,
        )
        natives.append(native)
    return np.concatenate(natives, axis=1)


def build_metadata(*, frame_w: int, frame_h: int, count: int, fps: float, sources: list[str], grid: tuple[int, int], fit: str,
                   placements: list[dict] | None = None, align: str = "bottom") -> dict:
    sprite = {"frameWidth": frame_w, "frameHeight": frame_h, "count": count, "fps": fps}
    frames = [{"index": i, "source": name, "x": i * frame_w} for i, name in enumerate(sources)]
    for entry, placement in zip(frames, placements or []):
        entry["placed"] = placement  # source size and offset inside the common cell, before snapping
    return {
        "frameWidth": frame_w,
        "frameHeight": frame_h,
        "count": count,
        "fps": fps,
        "grid": {"width": grid[0], "height": grid[1]},
        "fit": fit,
        "align": align,
        "frames": frames,
        "am_sprite_frames": sprite,
    }


def main(argv: list[str] | None = None) -> int:
    def _run() -> int:
        args = build_parser().parse_args(argv)
        if not args.frame:
            raise AstridError("no frames given", recovery_command="pass one --frame per pose")
        if args.fps <= 0:
            raise AstridError("fps must be greater than 0", recovery_command="use --fps 8")
        sources = [path.expanduser().resolve() for path in args.frame]
        for source in sources:
            if not source.is_file():
                raise AstridError(
                    f"frame not found: {source}",
                    recovery_command='pass frames as media handles: inputs={"frame": ["run:<run_id>/<port>#0", "sha256:<digest>"]}',
                )
        out_dir = args.out.expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        palette = _palette_text(args.palette)
        frames = [px.load_rgba(str(source)) for source in sources]
        _aligned, placements = align_frames(frames, align=args.align) if len(frames) > 1 else (frames, [])
        strip = build_strip(
            frames,
            grid_width=int(args.grid_width),
            grid_height=int(args.grid_height),
            fit=args.fit,
            palette=palette,
            max_colors=int(args.max_colors or 0),
            align=args.align,
        )
        frame_h, total_w = strip.shape[:2]
        frame_w = total_w // len(sources)
        metadata = build_metadata(
            frame_w=frame_w,
            frame_h=frame_h,
            count=len(sources),
            fps=float(args.fps),
            sources=[source.name for source in sources],
            grid=(frame_w, frame_h),
            fit=args.fit,
            placements=placements,
            align=args.align,
        )
        px.save_rgba(strip, str(out_dir / "strip.png"))
        (out_dir / "strip.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        manifest = build_manifest(
            kind="pixel_strip",
            inputs={
                "frames": [source.name for source in sources],
                "grid": [args.grid_width, args.grid_height],
                "fit": args.fit,
                "palette": palette,
                "max_colors": int(args.max_colors or 0),
                "fps": float(args.fps),
                "align": args.align,
            },
            outputs=[
                {"name": "strip", "path": "strip.png", "type": "file", "artifact_type": "image", "role": "result", "is_primary": True},
                {"name": "metadata", "path": "strip.json", "type": "file", "role": "auxiliary"},
            ],
            created=datetime.now(timezone.utc).isoformat(),
        )
        write_manifest(out_dir / "manifest.json", manifest)
        return 0

    return run_pack_main("pixel.strip", _run, argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
