# pixel.snap

## Purpose

Turn a generated image (for example Codex `gpt-image` output at 1536x1024 or
1254x1254, with soft edges and anti-aliased blocks) into true pixel art: one
colour per logical cell, hard edges, and 1 px outlines preserved. Each cell takes
the modal colour of its source region, not a bilinear average.

## Inputs

- `image` (file, required): a media handle: an output row (`result.output("generated_images")`), `"run:<run_id>/<port>#n"`, `"sha256:<digest>"` or a managed descriptor.
- `crop` (json, optional): `{x, y, width, height}` in source pixels. Applied before everything else.
- `auto_grid` (boolean, default `false`): detect the block lattice from edge periodicity.
  Use it for images that already sit on a pixel lattice. If detection fails, the
  report carries a `hint` block size you can use with an explicit grid.
- `grid_width` / `grid_height` (integer, default 320 / 180): explicit logical grid. Ignored when `auto_grid` is true.
- `fit` (`cover` | `contain` | `none`, default `cover`): `cover` crops to the grid aspect,
  `contain` pads to it, `none` stretches the source as-is.
- `palette` (string, optional): `astrid` or comma-separated hex colours. Cells map to the nearest colour.
- `max_colors` (integer, optional, 2-256): deterministic k-means quantisation when no palette is given.
- `scale` (integer, default 6): nearest-neighbour factor for the preview.
- `dither` (boolean, default `false`): Floyd-Steinberg, only with a palette or `max_colors`.

## Outputs

- `native`: grid-size RGBA PNG with hard alpha (0 or 255). This is the true pixel art.
- `preview`: `native` scaled by `scale`, nearest-neighbour.
- `report`: JSON with source size, crop, detected or explicit grid, colours used,
  palette distance statistics (mean, p95, max), and alpha values.

## Canonical command

```python
import astrid.sdk as sdk
result = sdk.invoke(
    "pixel.snap",
    kind="executor",
    project="almost-ready",
    inputs={
        "image": {"digest": "sha256:<digest>", "filename": "<file.png>", "media_type": "image/png", "size_bytes": 199012},
        "crop": {"x": 60, "y": 190, "width": 460, "height": 210},
        "grid_width": 48,
        "grid_height": 48,
        "fit": "none",
    },
)
```

Use `auto_grid: true` for an already-pixelated sprite such as the mink site
sprite. Use an explicit grid for gpt-image output, whose blocks are non-integer
(for example 1254 px / 48 cells = 26.1 px).

## Dependencies

- Pillow and numpy (both already in the Astrid environment). No network.
