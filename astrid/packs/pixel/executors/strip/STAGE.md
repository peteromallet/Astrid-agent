# pixel.strip

## Purpose

Pack a set of same-grid pixel frames (for example two generated claw poses) into
one horizontal sprite strip, so `am-sprite` can cycle them as a loop. The frames
stay handles: change the order, the fps or a pose by re-running this executor, not
by regenerating art.

## Inputs

- `frame` (file, required, repeatable): ordered managed image handles, one per frame. Each is a media handle: `"sha256:<digest>"`, `"ref:<name>"`, `"run:<run_id>/<port>#n"`, or a managed descriptor. Pass a list. Order is playback order.
- `grid_width` / `grid_height` (integer, default 0): the shared logical grid. Set both to snap every frame to that grid (as `pixel.snap`). At 0 the frames' own size is used, and they must all match.
- `fit` (`cover` | `contain` | `none`, default `cover`): how a frame fits an explicit grid.
- `palette` (string, optional): `astrid` or hex colours, applied to every frame.
- `max_colors` (integer, optional, 2-256): one quantisation across frames when no palette is given.
- `fps` (number, default 8): playback rate written to the metadata.

## Outputs

- `strip`: `strip.png`, RGBA, frames left to right, hard alpha, on the shared grid.
- `metadata`: `strip.json` with `frameWidth`, `frameHeight`, `count`, `fps`, `grid`,
  per-frame `x` offsets and `am_sprite_frames`.

## Using it in a shot

Put the strip on `am-sprite` as `clip.asset` and copy `am_sprite_frames` into the
clip's `params.frames`. Use `blinkIndex` for a frame during a blink. The cycle runs
at `fps` and is quantised to clip frames.
