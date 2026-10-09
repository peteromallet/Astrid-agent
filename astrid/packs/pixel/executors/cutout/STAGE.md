# pixel.cutout

## Purpose

Remove a flat, chroma-key, or light background from a generated image and write
an RGBA PNG whose alpha is strictly 0 or 255. Only background that is connected to
the image border is removed. Pixels enclosed by the sprite, such as a magenta
interior, and the dark 1 px outline are kept.

## Inputs

- `image` (file, required): a media handle (output row, `"run:<run_id>/<port>#n"` or `"sha256:<digest>"`).
- `crop` (json, optional): `{x, y, width, height}` in source pixels. Applied first.
- `mode` (default `chroma`):
  - `chroma`: remove pixels within `tolerance` of `key`.
  - `flat`: detect the dominant opaque border colour and remove that colour.
  - `luma`: remove light backdrops (luminance at or above `255 - tolerance`).
- `key` (default `#FF00FF`): chroma key colour.
- `tolerance` (default 48): RGB distance threshold.
- `grid` (optional): `auto` or `WIDTHxHEIGHT`. When set, the cutout runs on the snapped native
  image, so each logical pixel is fully in or out, with no soft edges.
- `fit` (default `contain`): fit used with an explicit grid. `contain` keeps the whole subject.
- `trim` (default `true`) and `trim_padding` (default 2): crop to the alpha bounding box.
- `keep_largest` (default `true`): drop detached specks smaller than 5% of the largest component.
- `holes` (default `false`): also remove key-coloured pixels enclosed by the sprite (magenta showing between a tower's legs or inside a chain link). Without it they stay and a later palette snap maps them to the nearest art colour.

## Outputs

- `cutout`: RGBA PNG with hard alpha.
- `report`: JSON with the key used, background and foreground counts, bounding box,
  speck statistics, grid used, and `hard_alpha`.

## Canonical command

```python
import astrid.sdk as sdk
result = sdk.invoke(
    "pixel.cutout",
    kind="executor",
    project="almost-ready",
    inputs={
        "image": "run:<run_id>/generated_images#0",  # any media handle: output row, run:, sha256:
        "mode": "flat",
        "grid": "auto",
        "trim": True,
    },
)
```

For full-resolution soft-edged art, prefer `grid` so the alpha is per logical pixel. Without
a grid, colours that blend with the key but fall outside `tolerance` can leave a faint fringe.

## Dependencies

- Pillow and numpy. No network.
