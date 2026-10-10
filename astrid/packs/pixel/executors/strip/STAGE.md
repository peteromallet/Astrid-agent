# pixel.strip

## Purpose

Pack a set of same-grid pixel frames (for example two generated claw poses) into
one horizontal sprite strip, so `am-sprite` can cycle them as a loop. The frames
stay handles: change the order, the fps or a pose by re-running this executor, not
by regenerating art.

## Inputs

- `frame` (file, required, repeatable): a list of at least 2 media handles, one per frame, in playback order:
  `"run:<run_id>/<port>#n"`, `result.output("native")`, `"ref:<name>"` or `"sha256:<digest>"`. Repeats are fine (ping-pong).
- `grid_width` / `grid_height` (integer, default 0): the shared logical grid. Set both to snap every frame to that grid (as `pixel.snap`). At 0 the frames' own size is used, and they must all match.
- `fit` (`cover` | `contain` | `none`, default `cover`): how a frame fits an explicit grid.
- `palette` (string, optional): `astrid` or hex colours, applied to every frame.
- `max_colors` (integer, optional, 2-256): one quantisation across frames when no palette is given.
- `fps` (number, default 8): playback rate written to the metadata.
- `align` (`bottom` | `center` | `top`, default `bottom`): frames of different sizes (pixel.cutout trims each pose
  to its own box) are padded with transparency onto one common cell before snapping, centred horizontally and
  anchored at the bottom (one ground line), centre or top. Every frame keeps one scale; `strip.json` records each
  frame's `placed` size and offset.

## Outputs

- `strip`: `strip.png`, RGBA, frames left to right, hard alpha, on the shared grid.
- `metadata`: `strip.json` with `frameWidth`, `frameHeight`, `count`, `fps`, `grid`,
  per-frame `x` offsets and `am_sprite_frames`.

## Canonical command

```python
import astrid.sdk as sdk
strip = sdk.invoke("pixel.strip", kind="executor", project="almost-ready", wait=True,
                   inputs={"frame": [claw_a.output("native"), "run:<run_id>/native#0"], "fps": 6})
strip.raise_for_error()  # a FAILED task returns normally from sdk.invoke; this raises
print(strip)  # strip[0]  run:<run_id>/strip#0  <viewable local path>; pass strip.output("strip") on
```

## Using it in a shot

Put the strip on `am-sprite` as `clip.asset` and copy `am_sprite_frames` into the
clip's `params.frames`. Use `blinkIndex` for a frame during a blink. The cycle runs
at `fps` and is quantised to clip frames.
