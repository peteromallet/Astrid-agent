# Pixel — Agent Guide

## When to Use This Pack

Use this pack when a generated image should become true pixel art (hard
edges, one colour per logical cell, optional brand palette) or needs a
transparent cutout. Typical source: Codex `gpt-image` output, which looks
pixelated but has soft edges, non-integer block sizes, and an opaque background.

Do not use it to generate images (use `generation`) or to smooth or upscale art
(`generation.generate_image_upscale` smooths edges, which is the wrong direction).

## Entrypoints

Both executors take a managed image. Import the file first, then pass its digest:

```bash
python3 -m astrid media import <file.png> --project almost-ready --json
```

```python
import astrid.sdk as sdk

snapped = sdk.invoke(
    "pixel.snap",
    kind="executor",
    project="almost-ready",
    inputs={"image": "<digest>", "grid_width": 48, "grid_height": 48, "fit": "none"},
)
cutout = sdk.invoke(
    "pixel.cutout",
    kind="executor",
    project="almost-ready",
    inputs={"image": "<digest>", "mode": "flat", "grid": "48x48", "fit": "none"},
)
```

Pass `crop` ({x, y, width, height} in source pixels) to either executor to cut a
region first, for example a character pose out of a reference sheet.

## Executors

| Executor | What it does |
|---|---|
| `pixel.snap` | Mode-downsamples onto an explicit grid (`grid_width`/`grid_height`, `fit`) or a detected lattice (`auto_grid`). Optional `palette` (`astrid` preset or hex list) or `max_colors`. Outputs `native` (grid size), `preview` (scaled), `report`. |
| `pixel.cutout` | Removes a background (`chroma`, `flat`, or `luma`) into hard alpha. Optional `grid` snaps first so alpha is per logical pixel. Trims, drops specks. Outputs `cutout` (RGBA) and `report`. |

## Choosing a grid

- Already-pixelated sprite on an exact lattice (for example a 4x upscale): `auto_grid: true`.
- gpt-image output: the blocks are non-integer, so auto detection is often
  unreliable. Pick the grid from the sprite's block size, for example 1254 px ÷ 26.1 px
  = 48 cells. The `report.grid.*.hint` field of an `auto_grid` run suggests a block size.
- Use `fit: none` when the grid already matches the aspect; `contain` for cutouts so
  the whole subject survives.
