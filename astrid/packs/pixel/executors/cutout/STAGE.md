# pixel.cutout

## Purpose

Remove a flat, chroma-key, or light background from a generated image and write
an RGBA PNG whose alpha is strictly 0 or 255. Only background that is connected to
the image border is removed. Pixels enclosed by the sprite, such as a magenta
interior, and the dark 1 px outline are kept.

## Inputs

Every input, with its default. Defaults apply when the input is omitted.

| Input | Type | Default | What it does |
|---|---|---|---|
| `image` | file | required | Media handle: output row, `"run:<run_id>/<port>#n"`, or `"sha256:<digest>"`. |
| `crop` | json | none | `{x, y, width, height}` in source pixels. Applied first, before grid and cutout. Report: `crop`, `source_size`. |
| `mode` | string | `chroma` | `chroma`: remove pixels within `tolerance` of `key`. `flat`: detect the dominant opaque border colour and remove it. `luma`: remove light backdrops (luminance at or above `255 - tolerance`). |
| `key` | string | `#FF00FF` | Chroma key colour (hex). Used only by `mode=chroma`. |
| `tolerance` | integer | `48` | RGB distance (0-255) from the key or border colour that counts as background. For `luma`, the light threshold is `255 - tolerance`. |
| `grid` | string | none | `auto` detects the pixel lattice. `WIDTHxHEIGHT` snaps first (with `fit`). Omitted: cut at full resolution. |
| `fit` | string | `contain` | Fit used with an explicit `grid`: `cover`, `contain` or `none`. Only read when `grid` is set. Note: this default is `contain` here, but `pixel.snap` defaults to `cover`. |
| `trim` | boolean | `true` | Crop the output to the alpha bounding box plus `trim_padding`. Each frame comes out at its own subject size. |
| `trim_padding` | integer | `2` | Padding in output pixels around the trimmed box. Only read when `trim` is true. |
| `keep_largest` | boolean | `true` | Drop detached specks smaller than 5% of the largest foreground component. |
| `holes` | boolean | `false` | Also remove key-coloured pixels enclosed by the sprite (gaps between legs, chain links). Only for keys that never occur in the art. |

Booleans take `true`/`false`. Unknown `mode` or `tolerance` outside 0-255 raise an error.

## Outputs

- `cutout`: RGBA PNG with hard alpha.
- `report`: JSON with the key used, background and foreground counts, bounding box,
  `trim` (padding and the crop it applied, so you can map back to the source), speck
  statistics, `grid` used, `source_size`, and `hard_alpha`.

## Trimmed size and frames of one strip

`trim` defaults to `true`, so every frame is cropped to its own subject. Two poses from
the same generation can come out at different sizes (for example 51x65 and 31x64). Those
sizes cannot be snapped to a shared grid: `pixel.snap` only downsamples, so a grid larger than
the trimmed source fails with `grid 51x65 is larger than the 31x64 source`. The error names the
trim when the source has transparent margins.

Two ways to build a strip from frames:

1. Cut the frames with `trim: false` so they share the source canvas, then pass them to `pixel.strip`:

   ```python
   a = sdk.invoke("pixel.cutout", kind="executor", project="almost-ready", wait=True,
                  inputs={"image": "run:<run_a>/generated_images#0", "mode": "flat", "trim": False})
   b = sdk.invoke("pixel.cutout", kind="executor", project="almost-ready", wait=True,
                  inputs={"image": "run:<run_b>/generated_images#0", "mode": "flat", "trim": False})
   strip = sdk.invoke("pixel.strip", kind="executor", project="almost-ready", wait=True,
                      inputs={"frame": [a.output("cutout"), b.output("cutout")], "fps": 6})
   ```

2. Keep the default trim and let `pixel.strip` align them. It pads trimmed frames onto one common
   cell, centred horizontally and anchored at the bottom (`align: bottom`), so one scale and one
   ground line are kept. Pass `a.output("cutout")` and `b.output("cutout")` directly.

## Canonical command

```python
import astrid.sdk as sdk
result = sdk.invoke(  # opens the runtime client itself; pass client= to reuse one
    "pixel.cutout",
    kind="executor",
    project="almost-ready",
    wait=True,
    inputs={
        "image": "run:<run_id>/generated_images#0",  # any media handle: output row, run:, sha256:
        "mode": "flat",
        "grid": "auto",
        "trim": True,
    },
)
print(result)  # each output: its handle and a viewable local path
```

For full-resolution soft-edged art, prefer `grid` so the alpha is per logical pixel. Without
a grid, colours that blend with the key but fall outside `tolerance` can leave a faint fringe.

## Dependencies

- Pillow and numpy. No network.
