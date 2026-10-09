# Extend timeline visualize: layers, checks, project rules, data tracks

`timelines visualize` and `timelines lint` are built from four small, registered parts.
You can add each one without touching the rendering pack.

| You want to… | Add | Where | Python? |
|---|---|---|---|
| see a timeline a new way | a **layer** (one PNG panel + findings) | `astrid/packs/<pack>/visualize_layers/<name>.py` → `LAYER` | yes |
| check a new condition | a **check** (typed findings, optional fix) | same module → `CHECK` (or `Layer(..., checks=(...))`) | yes |
| change thresholds/severities | a **rules file** | `astrid-lint.toml` next to your work | no |
| attach new data to clips | a **data track** | `clip.app.data.<name>` in the timeline document | no (JSON) |

Discovery: every `astrid/packs/*/visualize_layers/*.py` (not starting with `_`) is imported, and its
`LAYER`/`LAYERS`/`CHECK`/`CHECKS` are registered. A module that raises is listed as broken and
the rest keep working. Verify with:

```bash
python3 -m astrid timelines visualize --list-layers      # layers (built-in and pack)
python3 -m astrid timelines lint --list-checks           # checks, their threshold keys and defaults
```

The host serves the packs of the promoted checkout. A new module runs in `timelines lint` (client-side)
at once, and in `visualize` sheets after it is committed and promoted.

## 1. A layer

```python
# astrid/packs/<pack>/visualize_layers/cut_length.py
from PIL import ImageDraw
from astrid.packs.rendering.executors.timeline_visualize.layers import Layer, LayerResult
from astrid.packs.rendering.executors.timeline_visualize.layers.base import PALETTE, draw_text

def render(ctx):
    image = ctx.panel(60, title="cut length")          # a panel on the shared time axis
    draw = ImageDraw.Draw(image)
    x0, x1 = ctx.x_of(float(ctx.cut["start"])), ctx.x_of(float(ctx.cut["end"]))
    draw.rectangle((x0, 30, x1, 50), fill=PALETTE["key"])
    seconds = float(ctx.cut["duration"])
    return LayerResult(image, [f"LENGTH cut {ctx.cut['index']} is {seconds:.2f} s"])

LAYER = Layer("cutlength", "the cut's span on the time axis", render, needs=("doc",))
```

`Layer(name, help, render, needs=("doc",), views=("motion",), experimental=False, default=True,
order=100, checks=())`:
- `name` is one short word (letters, digits, `-`, `_`). `--layer cutlength` selects it.
- `needs`: `"doc"` (timeline data), `"frames"` (captured frames; the planner captures them),
  `"words"`, `"beats"`, `"audio"`.
- `views`: `"motion"` (window/cut sheets) and/or `"contact"`.
- `default=False` makes it opt-in; `experimental=True` runs it only when named.
- `render(ctx) -> LayerResult(image | None, findings: list[str], title="", page="data"|"frames")`.

`ctx` (a `LayerContext`) holds everything in seconds on the timeline clock:

| field | what |
|---|---|
| `cut` | the cut or window: `index, start, end, duration, shot, clip_id, layers, deliberate_hold, sequence`; `window=True` for a range |
| `cuts` | every picture cut |
| `elements`, `all_elements` | clips in the window / in the timeline (`type, track, start, end, params, clip, asset, label`) |
| `words`, `beats`, `sfx`, `events` | VO words (`start, end, text`); `{"beats","downbeats","hits"}`; `(start, end, name)`; entrances/exits/keys |
| `tracks` | data tracks in the window (see §4) |
| `frames` | `{timeline frame: PNG path}` when the layer needs frames; `ctx.frame_image(n)` opens one |
| `fps`, `window`, `width` | frame rate, the panel time window `(start, end)`, the panel width |
| helpers | `ctx.panel(height, title=...)`, `ctx.x_of(t)`, `ctx.visible(t0, t1)`, `ctx.rel(t)` |
| `shared` | scratch dict shared by the layers of one sheet |

## 2. A check (a condition)

```python
# same module as the layer, or its own: astrid/packs/<pack>/visualize_layers/long_text.py
from astrid.packs.rendering.executors.timeline_visualize.layers import Check

def run(ctx):
    limit = ctx.param("max_words_on_screen")             # a threshold this check declares (below)
    found = []
    for element in ctx.elements:
        text = str(element.params.get("text") or "")
        if element.type == "am-type" and len(text.split()) > limit:
            found.append(ctx.finding("WORDY", f"{element.label} shows {len(text.split())} words (> {limit:g})",
                                     t=element.start, fix={"clip": element.short_id, "set": {"params.text": "…"}}))
    return found

CHECK = Check("wordy-type", "on-screen type longer than max_words_on_screen words", run,
              params={"max_words_on_screen": 8}, scope="cut", codes=("WORDY",))
```

`Check(name, help, run, params={key: default}, scope="cut"|"timeline", needs=("doc",), codes=())`:
- `run(ctx)` returns `Finding`s. Build them with `ctx.finding(code, message, t=..., severity="warn",
  fix=...)`. `ctx` is a `CheckContext` with `cut` (`None` for timeline scope), `cuts`, `elements`
  (in the cut), `all_elements`, `words`, `beats`, `sfx`, `tracks`, `params`, `fps`, `frames`.
- `params` declares your threshold keys and defaults. `None` means off until a rules file sets it.
  Keys are shared, so another check reading `min_text_px` gets the same value.
- `severity` is `error | warn | info`. `fix` is machine-applicable: `{"clip": id, "set": {"params.x": 1104}}`
  or `{"clip": id, "move_s": 0.12}`.
- `needs=("frames",)` runs only inside `visualize --view motion`, where frames exist. Doc checks also
  run in `timelines lint`. A check can ship with a layer: `Layer(..., checks=(CHECK,))`.

## 3. Project rules: `astrid-lint.toml` (no Python)

Put it in your working directory (or any parent), or pass `--rules FILE`:

```toml
# thresholds: any key from `timelines lint --list-checks`
max_cut_s = 4                 # off by default; cuts marked deliberate_hold are exempt
stamp_to_word_max_s = 0.1
min_text_px = 32
max_presenter_share = 0.3

[severity]                    # per finding code: off | info | warn | error
EDGE = "off"
LONG = "error"

[checks]
disable = ["vo-level"]        # skip whole checks by name
```

`timelines lint` prints `rules: <path>`. An unknown key is an error that lists the known keys.
`--strict` exits 1 on any `error` finding. `visualize --view motion` uses the same file.

## 4. Data tracks

A data track is typed time data on a clip: `clip["app"]["data"][<name>]`.

```json
{
  "kind": "points",
  "units": "s",
  "time": "clip",
  "source": {"handle": "sha256:…", "producer": "my-tool@1", "params": {}},
  "items": [[0.4, "claw closes"], [1.2, "claw opens"]]
}
```

| kind | items | example |
|---|---|---|
| `points` | `[[t, "label"?], …]` | beats, cues, claw contact |
| `intervals` | `[[t0, t1, "label"?], …]` | words, holds, gestures |
| `series` | `{"t0": 0, "step": 0.05, "values": [...]}` or `[[t, v], …]` | loudness, motion energy |
| `boxes` | `[[t \| null, x, y, w, h, "label"?], …]` (canvas px; `null` = static) | a tracked face |

- `time: "clip"` means seconds from the clip's start (like `app.words`). `"source"` means media seconds,
  mapped through the clip's `from` (like `app.beats`).
- `source.handle` uses the media-handle grammar: `sha256:<hex>`, `run:<run_id>/<port>#n` or `ref:<name>`.
- Reading: every layer and check gets them in timeline seconds through `ctx.tracks`
  (`motion.data.tracks(...)`). The sync layer draws any points/intervals/series track as a lane.
  The `composition` check uses a `face` boxes track, when present, as the presenter's face.
- Derived tracks (read-only views of older data): `words`, `beats`, `music_hits`, `face_zone`, `presenter_words`.

Attach one to a checked-out document, then check and publish as usual:

```bash
python3 -m astrid.packs.rendering.skill.scripts.timeline_document checkout --project P --timeline T --file /tmp/edit.json
python3 -m astrid.packs.rendering.skill.scripts.timeline_data add --file /tmp/edit.json --clip v17-04-am-sprite --name claw_contact --track track.json
python3 -m astrid.packs.rendering.skill.scripts.timeline_data loudness --file /tmp/edit.json   # producer: RMS dBFS per VO/music clip
python3 -m astrid.packs.rendering.skill.scripts.timeline_data list --file /tmp/edit.json
python3 -m astrid.packs.rendering.skill.scripts.timeline_document check --file /tmp/edit.json
python3 -m astrid.packs.rendering.skill.scripts.timeline_document publish --file /tmp/edit.json --idempotency-key <key>
```

A check reads a track by name:

```python
from astrid.packs.rendering.executors.timeline_visualize.motion import data
for track in data.by_name(ctx.tracks, "claw_contact"):
    for t, label in track.points: ...
```
