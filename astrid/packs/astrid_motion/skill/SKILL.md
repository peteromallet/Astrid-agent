# Astrid Motion Pack

Use this pack when a timeline needs a pixel-motion building block for the
"Pixel Modernism" explainer look: pixel plates that pan and zoom on whole pixels,
transparent pixel cutouts that step, editorial type, floating callouts, and
hard pixel-block wipes.

Every element is a render-time overlay (`clipType` = the element id). It draws
inside the Remotion composition and does not generate pixels, import media, or
render the video. Use the rendering pack to render, and the pixel pack to
snap or cut out art.

Element ids are `am-*` so they never collide with the shadowed `text-card`
pair. Read STYLE.md in the production folder before choosing values. Its motion
grammar is law: pixels step on twos or threes, type may ease, and cuts are hard.

## Rules that apply to every element

1. **Managed media goes on `clip.asset`, not in `params`.** Import the PNG with
   `python3 -m astrid media import <file.png> --project <p> --json`, add a
   registry entry (`media_id` from the import, `content_sha256` = its
   `sha256:` digest, `type`, `resolution` as `WxH`), and set the clip's
   `asset` to that registry key. The composition hands the entry to the element
   as `assetEntry`, and the render backend rewrites its `file` to a loopback
   URL. `params.src` only takes a static http(s) URL or public path. A registry
   key placed in `params` is not resolved.
2. **Units.** Pixel elements use logical px: 320x180 space, one logical px is 6
   screen px at canvas 1920x1080. `am-snap-plate` focus and pan, and
   `am-sprite` x and y, are logical px. `am-type` and `am-callout` use canvas
   px. `am-sprite` scale is screen px per art px (6 = one art px per logical
   px).
3. **Timing.** Clip `hold` (seconds) sets length. Frames come from the
   composition fps. Stepped motion uses `stepFrames` (2 or 3). Type eases.
4. **Determinism.** No `Math.random`. Seeded orders use a hash of (seed, index).
5. **Overlays on top.** Visual tracks draw earlier array entries on top. Put
   overlay tracks (`fx`, `callouts`) before the picture or plate track.
6. Element `params` are not schema-checked at validation time. Keep to the
   documented shape. Out-of-range values are clamped by the component.

## am-snap-plate: pixel plate with stepped pan and zoom

Shows a pixel snapshot (1920x1080, or any 320x180 multiple) through an integer
zoom of 1 to 4.

| param | default | meaning |
|---|---|---|
| `zoom` | 1 | integer 1-4. At zoom 1 the plate fills the frame and pan has no effect. |
| `focus` | `{x:160,y:90}` | logical px placed at the frame centre. Clamped to the plate. |
| `pan` | `{dx:0,dy:0}` | logical px moved per step. Positive dx moves the view right. |
| `stepFrames` | 2 | 2 or 3. Pan changes only on step boundaries. |
| `tint` | none | `{color, opacity}` flat overlay, default opacity 0.25. |
| `enter` | `cut` | `cut` or `blockWipe` (reveal over `enterFrames`). |
| `exit` | `cut` | `cut` or `blockWipe` (cover over the last `exitFrames`). |
| `enterFrames` / `exitFrames` | 6 | block-wipe length. |
| `pattern` / `seed` | diagonal / 1 | block order for the wipes: diagonal, random, or scan. |
| `wipeColor` | `#1F1F1F` | block colour. |
| `background` | `#F7F4ED` | colour behind the plate. |
| `src` | none | static URL or public path (use `clip.asset` for managed media). |

```json
{"id":"plate-01","at":0,"track":"base","clipType":"am-snap-plate","asset":"plate-p01","hold":4,"params":{"zoom":3,"focus":{"x":120,"y":60},"pan":{"dx":2,"dy":0},"stepFrames":2,"enter":"blockWipe","enterFrames":6,"exit":"cut"}}
```

## am-sprite: transparent pixel cutout with stepped motion

A transparent cutout (managed PNG on `clip.asset`) placed on the logical grid.

| param | default | meaning |
|---|---|---|
| `scale` | 6 | integer screen px per art px. 6 gives one art px per logical px. |
| `x`, `y` | 0, 0 | logical px of the sprite's top-left corner. |
| `flipX` | false | mirror the sprite in place. |
| `keyframes` | none | `[{frame, x, y, flipX?}]` held poses. The last keyframe at or before each step is active. No interpolation. |
| `stepFrames` | 2 | 2 or 3. Keyframe changes land on step boundaries. |
| `enter` | `cut` | `stamp` (frame 0 on at 1 px high, frame 1 off, frame 2 on at rest), `slideIn` (closes `slideDistance` logical px in 4 steps from `slideFrom`), or `cut`. |
| `frames` | none | horizontal strip `{frameWidth, frameHeight, count, fps}`. Frame index is `floor(frame*fps/clipFps) % count`, so it changes on steps. |
| `blinkAt` | none | clip frame or frames where the sprite hides for two frames, or shows `blinkIndex` when `frames` is set. |
| `src` | none | static URL or public path. |

```json
{"id":"mink-run","at":1.0,"track":"fx","clipType":"am-sprite","asset":"mink-run-strip","hold":3,"params":{"scale":6,"x":40,"y":96,"enter":"stamp","frames":{"frameWidth":32,"frameHeight":32,"count":8,"fps":12},"keyframes":[{"frame":0,"x":40,"y":96},{"frame":45,"x":60,"y":96,"flipX":true}],"blinkAt":[60]}}
```

## am-type: editorial type

| param | default | meaning |
|---|---|---|
| `text` | required | the copy. Empty text renders nothing. |
| `font` | `display` | `display` = Gelasio, `label` = Departure Mono (uppercase, +0.12em). |
| `size` | 96 / 28 | canvas px. |
| `color` | `#25241F` | ink. |
| `align` | `left` | `left`, `center`, or `right`. |
| `x`, `y`, `width` | 96, 96, 1200 | canvas px. |
| `reveal` | `slideUp` | `slideUp` (masked, 8 frames, ease-out), `type` (one char every 2 frames), or `none`. |
| `italicWord` | none | first matching word in Gelasio Italic, rust `#A94714`. |
| `underlineWord` / `underlineAt` | none / 0 | first matching word gets an orange underline. It draws in over 6 frames from `underlineAt`. |
| `tracking` / `lineHeight` | -0.01 / 0.95 display; 0.12 / 1.3 label | em. |

```json
{"id":"head-1","at":0.5,"track":"fx","clipType":"am-type","hold":3,"params":{"text":"Ship it tomorrow.","italicWord":"tomorrow","font":"display","size":120,"x":96,"y":300,"width":1400,"reveal":"slideUp","underlineWord":"tomorrow","underlineAt":30}}
```

## am-callout: floating card with an orange connector

The site's annotation card: panel white, 1px rule border, soft shadow, Gelasio
heading (weight 650), Inter body. The connector leaves the card's left or right
edge at the title line and draws in on steps to a dot at `anchor`.

| param | default | meaning |
|---|---|---|
| `title`, `body` | none | at least one is required. |
| `x`, `y`, `width` | 1200, 160, 520 | card top-left and width, canvas px. |
| `anchor` | `{x:960,y:540}` | canvas px where the connector ends. |
| `drawFrames` | 12 | frames until the connector finishes. |
| `stepFrames` | 2 | 2 or 3. |

Font note: only Gelasio 400 and 700 are vendored, so weight 650 renders at 700.

```json
{"id":"callout-1","at":2,"track":"callouts","clipType":"am-callout","hold":3,"params":{"title":"9,640 commits","body":"Thirteen repos, one year.","x":1180,"y":200,"width":560,"anchor":{"x":720,"y":430},"drawFrames":12}}
```

## am-pixel-wipe: pixel-block wipe

A full-frame overlay of 6 px blocks. `cover` fills the frame; `reveal` clears
it. It advances one block set per frame over `frames`, then holds.

| param | default | meaning |
|---|---|---|
| `mode` | `cover` | `cover` or `reveal`. |
| `frames` | 12 | frames to finish. |
| `color` | `#1F1F1F` | block colour. |
| `pattern` | `diagonal` | `diagonal` (corner to corner), `random` (seeded hash), or `scan` (left to right). |
| `seed` | 1 | seed for `random`. Same seed, same order. |

```json
{"id":"wipe-out","at":5.5,"track":"fx","clipType":"am-pixel-wipe","hold":0.6,"params":{"mode":"cover","frames":12,"color":"#1F1F1F","pattern":"random","seed":7}}
```

## Media registry entry (for `clip.asset`)

```json
"plate-p01": {"media_id": "<id returned by media import>", "content_sha256": "sha256:<digest>", "type": "image/png", "resolution": "1920x1080"}
```

## Checks

- `python3 -m astrid.core.element.cli list --kind effects | grep am-`
- `python3 -m astrid.core.element.cli inspect effects am-type --json`
- `python3 -m astrid.core.pack.cli validate astrid/packs/astrid_motion`

## Preview loop (stills and mp4, no runtime)

Use this while building or tuning an element. It mounts each element component
directly in a Remotion harness, so you do not need a timeline, a runtime or a
registry alias. Node must be 20.19.4 (Remotion pins it). Put Node on PATH with
`export PATH=~/Developer/astrid-video/tools/node-v20.19.4-darwin-arm64/bin:$PATH`.

```bash
OUT=<scratch dir>
python astrid/packs/astrid_motion/preview/make_art.py $OUT/.public/am   # test art, once per OUT (venv python, needs Pillow)
OUT=$OUT ONLY=type node astrid/packs/astrid_motion/preview/render.mjs  # scene names from scenes.json; mp4 when "mp4": true
```

Output is `$OUT/<scene>-f<frame>.png`. Read a PNG to look at it. The first run
bundles in about 10 s, and later runs bundle in about 3 s. Add a scene by
editing `preview/scenes.json`: `{"name", "frames": [..], "mp4": false,
"assets": {"key": {"file": "am/x.png", "type": "image/png", "resolution": "WxH"}},
"layers": [{"element": "am-type", "at": 0, "hold": 3, "params": {...}}]}`.
Asset `file` paths are relative to `$OUT/.public/`. The harness also renders a
`control-plain` layer, a plain `<Img>` with no pixelated style, for comparison.
The fonts come from `remotion/src/fonts.ts` (`FontProvider`), so previews
match the render.

## Known limits

- **Render alias (blocks timeline renders).** `scripts/gen_effect_registry.py`
  imports pack elements as `@pack-astrid_motion-elements-effects/<id>/component`.
  `remotion/remotion.config.ts` and `remotion/webpack-alias.mjs` alias only the
  `local` and `rendering` packs (`remotion.config.ts:67-68` for effects). Any other pack needs
  `ASTRID_PACKS_PATH` to add its alias. So a timeline that uses `am-*` clips will
  not bundle until the config aliases in-tree pack element roots. Use the preview
  loop for stills and mp4 until that change lands.
- `am-snap-plate` zoom is fixed per clip. For a stepped zoom punch, split the
  plate into two clips.
- `am-sprite` `slideIn` moves from the rest position by `slideDistance`, not from
  the canvas edge.
- Only Gelasio 400 and 700 are vendored, so `am-callout` weight 650 renders at 700.
