---
name: timeline_editing
description: >
  Author, inspect, edit, preview, render, and open runtime-owned Astrid
  timelines, including Remotion output and render-time animation, transition,
  and effect elements.
---

# Timeline editing

This is the canonical skill for runtime-owned timeline work: inspect → edit →
validate → save, with Remotion rendering and effect/evidence workflows when
needed. The broader [video editing skill](../../video_editing/skill/SKILL.md)
owns production orchestrators such as hype edits, talks, thumbnails, and logo
grids; it is not a second timeline-authoring route. In a checkout the source is
`astrid/packs/rendering/skill/SKILL.md`; in an installed skill view it is
`packs/rendering/SKILL.md`.

Use this skill to render, inspect render/input evidence, and open timeline
output for a candidate or published state. Rendering is an optional downstream
evidence action within the same timeline route, not a second timeline authority.
When the request also needs
new generated media (for example Foley audio), use [creative work](../../_core/skill/creative-work/SKILL.md)
to find its generation capability, then return here to assemble the result.

## See the whole video before rendering

One command per question, one page each, no render. `--plan` says what it will capture first.

```bash
python3 -m astrid timelines visualize <timeline> --project <project>                         # overview: a tile per cut
python3 -m astrid timelines visualize <timeline> --project <project> --range 5..10            # scan: 2 fps, 8 per row
python3 -m astrid timelines visualize <timeline> --project <project> --preset motion --at 5.3  # big frames, 1 s, every 2nd frame
python3 -m astrid timelines visualize <timeline> --project <project> --preset beat --range 5..10  # a frame per word/hit/sfx
python3 -m astrid timelines visualize <timeline> --project <project> --at 7.2                 # one frame, 1280x720
python3 -m astrid timelines lint <timeline> --project <project>                               # checks, ~1 s
```

`--at` also takes a spoken word or on-screen text (`--at viral`). Each run prints the page, counts,
`wall = queued + capture + compose` and `next:` (earlier, later, zoom in on the busiest moment, zoom
out, another preset, the cut). Override with `--every`, `--columns` (≤ 16), `--size WxH`; one page
always, so for the biggest frames show fewer (`--window 0.5`); over budget names the preset that fits.

## How an agent edits motion (you cannot watch video; read these instead)

```bash
python3 -m astrid timelines visualize <timeline> --project <project> --cut 17
```

1. Read `findings` (also `findings.txt`): FACE/FRAME/SAFE/SMALL/SYNC lines name the clip and the
   change (`set params.x ≤ 1104`, `move +0.12 s`); STILL/STRIP give holds; TIME gives each entrance's
   distance to its word, music hit and sfx; CURVE says stepped vs eased.
2. Open `motion-cut-17.png` (sync, curves, stillness, pixel change, lip-sync on one axis) and
   `motion-cut-17-frames.png` (strip with HOLD tiles; onion skins t-2…t+6 per entrance).
3. Edit (checkout → edit → check → publish), re-run (cached frames: seconds), then
   `timelines visualize --view diff --from <old head> --edited 17` (before/after + SCOPE).
4. Render only when the sheets read right. Beats come from the music clip's `app.beats`;
   `--preview` adds a GIF for people; mark intended freezes `app.deliberate_hold: true`.

## Extend visualize: layers, checks, rules, data tracks

Read [visualize-extend.md](references/visualize-extend.md). In short: a pack module
`astrid/packs/<pack>/visualize_layers/<name>.py` defines `LAYER` (a panel + findings) and/or
`CHECK` (a condition `timelines lint` runs); an `astrid-lint.toml` next to your work sets thresholds
(`max_cut_s = 4`) and severities without Python; data tracks (`app.data.<name>` on a clip:
points | intervals | series | boxes) are read by every layer and check
(`timeline_data.py add|loudness|list`). Check with `--list-layers` and `timelines lint --list-checks`.

## Start a new timeline

Use this when the project has no timeline yet. A timeline has no head until its
first parent composition is published, and checkout, `show`, `visualize`, and
`render` all refuse a headless timeline. `timelines create` makes the identity
and that empty first head in one call.

```bash
python3 -m astrid projects create <project-slug> --name "<Display name>" --json
python3 -m astrid timelines create --project <project-slug> --json
# data.timeline_id is the new id; --canvas WIDTHxHEIGHT and --fps set the canvas
python3 -m astrid projects update <project-slug> \
  --settings '{"default_timeline_id": "<timeline_id>"}' --json
python3 -m astrid timelines show --project <project-slug>
```

- `timelines create` takes an optional ULID (generated when omitted), `--canvas`
  (default `1920x1080`) and `--fps` (default `30`). The canvas is stored as
  `theme_overrides.visual.canvas` in the parent config. Runtime keeps no display
  name or slug for a timeline, so keep the returned `timeline_id`.
- The default timeline is optional. Without it, commands that omit `--timeline`
  stop with a recovery message; with it, `show`, `visualize`, `render`, and
  `runs open --default-timeline` resolve here.
- The new timeline has no tracks and no shots. Add them through the checkout
  route below: `checkout` (current head), edit, `check`, then `publish`. Use
  `timeline_document.py` from this skill directory, for example
  `python3 scripts/timeline_document.py checkout --project <slug> --timeline <timeline_id> --file <scratch>/edit.json`.
- Units: placement `start_ms` and row `duration_ms` are milliseconds; clip `at`,
  `from`, `to`, and `hold` are seconds. Add a visual track with
  `add_track(shot, kind="visual", track_id=...)`, and an audio track with
  `kind="audio"` (`add_track` defaults to `"video"`, which is not a track kind).
  Put a new shot in the timeline with `add_authoring_shot(work, shot_id=..., occurrence_id=..., start_ms=0)`,
  then set the row's `duration_ms`; the template row defaults to `duration_ms: 1`.
- Media is referenced by its managed digest after `python3 -m astrid media import <file> --project <slug> --json`.
  `place_media(shot, "sha256:...", ...)` writes `media_id`; a registry key writes `asset`.
- After `publish`, `show` reports the new current head.

## Live Scenes extension route

When the task concerns native Three.js scene code or a
`com.reigh.astrid.liveScene` clip, first confirm that the corresponding
registered extension/operation is active; installed documentation alone does
not make the capability available. Then read [Live-scene authoring and
conversion](references/live-scenes-authoring.md) for the supported public
Reigh read/exact-edit/publish loop, Runtime-owned package revisions, hosted
lifecycle, source-time mapping, and bounded prepared-scene admission/export
boundaries. Use the [two-shot Three.js template](templates/two-shot-threejs.ts)
for the minimal reusable-set, animated-subject example with named camera cuts.

Ordinary clip placement, trim, repetition, playback rate, and audio remain on
this general timeline route; use scene source/data for internal action or
camera timing. Maple remains a continuous take. This extension route does not
add a camera UI, retiming engine, scene-audio subsystem, generic ZIP importer,
or second timeline authority.

Treat the connected workspace runtime as the sole authority for projects,
timeline documents, versions, media objects, tasks, runs, and render outputs.
Use the public CLI or SDK; do not edit a checkout database, event log, CAS tree,
or generated run directory. Read current help before using a new option:

```bash
python3 -m astrid --help
python3 -m astrid timelines --help
```

## Edit a timeline (start here)

**Read [the editing front door](references/editing.md) first.** It is the whole loop:
`timelines checkout` → `timelines show --as sheet` → edit a line → `timelines apply` (or
`timelines edit … --clip c30.cover --until Astrid`, or three lines of Python on
`Checkout.draft`) → `timelines visualize` (reads the working copy) → `timelines status` →
`timelines publish`. When things happen is written as moments (`on "viral"`, `until
"Astrid"`, `beat 2 after "Astrid"`), and a new narration take re-flows the film.

The low-level document reference (bundle fields, provenance, recovery) is
[document-checkout](references/document-checkout.md); you should rarely need it.

Narration: make a take with `generation.generate_speech` (WAV plus word timing), then put it in with `timelines edit TL --line ID --take WAV --words WORDS.json --text "…"` (or `--insert-line`, `--remove-line`, `--from-script vo.json`). The film re-flows and the shot's `voiceover_script` binding is updated at publish. The [placeholder voiceover recipe](references/placeholder-voiceover.md) covers generating the take. No video render is needed.

## Agent operating contract

The timeline is one canonical authoring composition. `timelines show` (and the
typed authoring-bundle opener) is the bounded structural/text view;
`timelines visualize` is the declared-input/composed-output visual view; and offline
`timelines inspect --manifest` is a bounded read-only view of an already
published evidence pack. These are sister commands over the same identity and
time model in the single `timelines` product family. The default `auto` mode
reuses a fresh matching render or captures bounded composed frames from the
pinned composition when no render exists. Explicit `--mode inputs` never
looks up renders and remains renderer-free. `--mode composed` uses the same
capture route; `--frame N` requests one exact rendered frame. Source decoding, waveforms, and rendered-output
filmstrips are private implementation details of the same operation. Never invent a
second text-only timeline, treat a filmstrip as a new source of truth, or
switch to a mutable child document because it is easier to read.

The default composed surface keeps the rendered output and synchronized input
lanes together, so a sampled frame can be compared with the source placement
that produced it. Use `--hide inputs` for output-only review, or
`--mode inputs` for a renderer-free placement view. `--every SECONDS` and
`--every-frames N` control temporal sample spacing; `--resolution WIDTHxHEIGHT`
controls the pixels used for each sampled output image. The returned receipt
records the resolved components, cadence, frame step, and resolution so the
input/output relationship is inspectable after the PNG is opened.

Use this loop for every inspection or edit:

1. **Resolve and pin scope.** Explicit project and timeline arguments always win.
   For the timeline discovery/read/visualize/render commands, omitted scope is
   resolved by the connected Runtime: the workspace's current project, then
   that project's `metadata.default_timeline_id`. If either selection is
   missing, stop with the recovery command instead of guessing from names or
   ordering. Capture one current authoring head, candidate digest, or explicit
   historical render identity and label the lifecycle (`current`, `candidate
   preview`, `historical`, or `unverified legacy`) in the answer.
2. **Start broad, then narrow.** Get the compact structural overview first.
   Expand an occurrence, then its clip/track/property, then exact text or
   source media. Use the visual view to confirm what is on screen at the same
   target/time; use the text view to explain ownership, roles, bytes, and
   diagnostics. Do not scan the entire media library when a target can be
   addressed directly.
3. **Carry one target between views.** Every result is navigated by the same
   `occurrence_id` plus optional clip, track, media, text/property path and
   half-open time window. Shot names and ordinals are display conveniences, not
   identity. Switching text ↔ visual preserves scope, target, time window,
   expansion, and filters. A missing frame or source is reported explicitly;
   never substitute a nearby frame or a different occurrence.
4. **Choose the smallest supported edit.** Use a public command for a direct,
   exact Runtime operation such as replacing admitted media or rendering. Use
   the [helper catalog](references/document-checkout.md#editing-helper-catalog)
   for sequence, reorder, quantize, ripple, layout, or repeated transforms;
   those helpers operate on the same detached authoring bundle. That code is a
   candidate transform, not a new format or hidden command language. Do not
   edit Runtime files directly, call undocumented endpoints, or silently rebase
   a stale candidate.
5. **Validate and save.** Validate the complete candidate, inspect its diff,
   and save through the existing compare-and-swap/idempotent publication boundary.
   Preview, rendering, and focused readback are optional unless the request
   needs them. For an explicit preview-before-save request, inspect/deliver a
   successful unpublished candidate render before saving that same candidate.
   Keep the receipt; further corrective edits and saves are allowed.
6. **Explain the evidence.** Report what was observed, what was changed, and
   what remains unavailable separately. A successful command, a semantic
   readback, a decoded media check, and a browser/render proof are different
   claims; do not collapse them into one “done”.

The short agent preamble is:

> Open one pinned composition. Find the target in the structural view, carry its
> exact identity to the visual view, and expand only as needed. Use the smallest
> supported command; otherwise edit a detached same-schema candidate with
> ordinary code. Validate, diff, and save. Choose source inspection, rendered
> preview, or readback when the request needs it. Keep current, candidate, and
> historical evidence distinct.

For discovery, `timelines show` answers structural/text questions, and
`timelines visualize --mode inputs` answers declared-placement questions. They
share the explicit project/timeline, pinned head, and exact
`occurrence_id`; carry that identity between calls rather than using a shot
name, ordinal, or fixture alias. Input mode is not a render or source-pixel
proof. Composed mode reports whether pixels came from an exact render, a fresh
bounded capture, or the frame cache; it never publishes a full render as a
side effect.

Workers must treat live Runtime responses and the artifacts returned by those
calls as authoritative. Fixture/evaluator JSON, seed maps, baseline exports,
and prior result files are coordinator inputs or test evidence, never the
source of truth for an agent's conclusion. If a live operation or artifact is
unavailable, say so and stop the unsupported claim.

This contract deliberately favors simple primitives for common work and code
for complex batch work. It does not add a DSL, persistent checkout, new media
store, or automatic merge service.

## Extension data and programmatic passes

Use the shared schema's open maps when a timeline needs application data. Keep
the scope explicit:

| Scope | Extension point | Good use |
| --- | --- | --- |
| Whole timeline | `candidate["parent"]["config"]["app"]["structure"]` | delivery-pass version or project-wide structure |
| One shot/revision | `candidate["shots"][shot_id]["payload"]["metadata"]` | delivery instructions, review status, or generation notes |
| One occurrence | `candidate["placements"][i]["provenance"]` | occurrence-specific processing state or receipt |
| One internal clip/effect | clip `app` or `params` | labels, effect controls, or local analysis |

`config.app.structure` is the intentional timeline extension point; an
unregistered top-level `config.structure` is invalid. These maps are retained
by the detached authoring bundle and published through the same validation,
diff, preview, and compare-and-swap boundary. They are storage extensions, so
the browser/editor needs an explicit projection before it can display or edit
an arbitrary key. Use a namespaced object such as `metadata["delivery"]` for
agent-owned state. If a shot revision is placed more than once, keep different
per-occurrence state in that placement's `provenance` rather than overwriting
shared shot metadata.

For a repeatable pass, use canonical `timelines show`/`open_composition` to pin
the current head and enumerate occurrence identities, then open the same
coordinator-issued target as a detached bundle. The target's
`head_revision_id` must equal the inspected `summary.head_revision_id`:

```python
shown = client.timelines.open_composition(project_id, timeline_ref, detail=True)
assert shown.ok
inspection = shown.data
assert target["head_revision_id"] == inspection["summary"]["head_revision_id"]

bound = client.open_authoring_target(target)  # same project/timeline/head
work = bound.open()                          # exact immutable closure
work["parent"].setdefault("config", {}).setdefault("app", {}).setdefault(
    "structure", {}
)["delivery_pass"] = {"version": 1, "status": "queued"}

for index, placement in enumerate(work["placements"]):
    shot = work["shots"][placement["shot_id"]]
    clips = shot["internal_timeline"].get("clips", [])
    shot["payload"].setdefault("metadata", {})["delivery"] = {
        "instructions": "Match the approved reference and preserve the cut.",
        "status": "queued",
        "source_head": inspection["summary"]["head_revision_id"],
        "clip_count": len(clips),
    }
    placement.setdefault("provenance", {})["delivery_index"] = index

bound.validate(work)
diff = bound.diff(work)       # inspect requested paths before saving
frozen = bound.preview(work)  # candidate JSON; no pixels are rendered
receipt = bound.publish(work, idempotency_key=frozen["candidate_digest"])
```

This is one candidate and one publication, so the status ledger and timeline
change share one head. On a stale-head/CAS error, discard the detached
candidate, run `timelines show` again, and rerun the deterministic pass. Do not
merge stale JSON or publish one shot at a time. For a report-only pass, walk
the ordered occurrences in `inspection`; for an item-level report or edit, use
the same `work["shots"][...]["internal_timeline"]` closure:

```python
report = []
for placement in work["placements"]:
    shot = work["shots"][placement["shot_id"]]
    for clip in shot["internal_timeline"].get("clips", []):
        report.append({
            "occurrence_id": placement["occurrence_id"],
            "clip_id": clip["id"],
            "track": clip.get("track"),
            "at": clip.get("at"),
            "from": clip.get("from"),
            "to": clip.get("to"),
        })
        clip.setdefault("app", {})["report_label"] = placement["occurrence_id"]

# For duration changes, use retime/quantize helpers with an explicit policy;
# then repeat validate -> diff -> preview -> publish on this same candidate.
bound.validate(work)
```

Stable Runtime identities remain immutable; compilation and CAS publication
allocate or reuse revisions as appropriate. The full extension-field example
and report/edit guide are in
[`docs/timeline-editing-guide.md`](../../../../docs/timeline-editing-guide.md).

### Renderer and skill boundary

Remotion and timeline effects stay on this route: edit the admitted internal
timeline `effects`/clip `app` or `params`, freeze the candidate, and use
`render_authoring_candidate_preview` when composed pixels are required. Do not
create a renderer-specific timeline format or send effect edits through the
production orchestrators in `video_editing`. The source pack directory and
capability IDs intentionally remain `rendering` for runtime compatibility;
the agent-facing skill name is `timeline_editing`. After changing this skill,
refresh the writable views with `python3 -m astrid.skills sync --all` and verify
with `python3 -m astrid.skills sync --all --check --json` so the root gateway and
installed pack view do not route existing-timeline work back to video editing.

## Discover and inspect

Resolve the project explicitly whenever more than one project is visible. When
the workspace already has the intended project selected, the `--project` flag
and timeline ref can be omitted; the Runtime uses its current project and the
project's `metadata.default_timeline_id`. List timelines for a compact
inventory, then show the selected timeline for its identity, current head, and
version. Canonical head-only reads may omit the
legacy `config` and `registry` fields; use the SDK composition opener below
for the pinned structural view:

```bash
python3 -m astrid projects list --json
python3 -m astrid projects current --json
python3 -m astrid timelines list [--project <project>] --json
python3 -m astrid timelines show [--project <project>] [<slug-or-id>] --json
python3 -m astrid timelines history --project <project> <slug-or-id> --json
python3 -m astrid timelines diff --project <project> <slug-or-id> --json
```

For SDK callers that need the same bounded structural result as the CLI,
`client.timelines.open_composition(project, ref, ...)` is the shared read-only
adapter. It returns the pinned query, summary, targets, rows, pagination,
media classifications, diagnostics, and scope-preserving actions used by
`timelines show`; it does not create a second timeline document. The adapter's
scope marks source-media actions as metadata-only until Runtime exposes a
digest-bound source handle, so agents must not claim source playback or exact
frame access from the media show action alone:

```python
opened = client.timelines.open_composition(
    None, None, occurrence="<occurrence-id>",
    range_value="10..15", limit=20, detail=True,
)
```

Passing `None` for either scope uses the same Runtime selection rules as the
CLI; pass an explicit project or timeline ref whenever you need a different
scope.

Keep the returned `head`, parent/revision identity, candidate digest, and
render identity together. A cursor is valid only for that complete scope;
reopen the composition after a head change instead of continuing an old page.

For visual continuity review, use the rendered filmstrip. It samples an exact
successful render when available, otherwise captures only the requested
chronological frames through the server-owned Remotion composition. The result
is a PNG contact sheet, Markdown, and machine-readable frame index. Omit `--out`; Astrid
owns the run and returns local delivery paths for verified copies of the
published evidence objects. The durable authority is the managed run's
digest-verified bundle/manifest, not those disposable local paths.

Input placement inspection is also available without a render. Run
`timelines visualize --mode inputs --format md --format png` to
project the current canonical input snapshot into declared visual lanes; the
Runtime publishes the Markdown/PNG view as derived project objects. This
reads no rendered pixels, decodes no audio, and makes no provider call.
Placement metadata establishes
which sources and intervals are declared, not what those sources look like in
the rendered composition. The native input PNG is a metadata diagram, not a
thumbnail sheet: it shows separate numbered clip spans, boundaries, a time
ruler, and an occurrence/track/clip legend. It displays at most 40 clips;
narrow the selectors or inspect the paired Markdown/JSON for the rest.

To inspect an image source itself, take `source_object_id` from the exact
selected clip in the returned inspection and retrieve it through the SDK:

```python
source_bytes = client.media.read_bytes(clip["source_object_id"])
```

Open those bytes in an image viewer (or save a temporary inspection copy and
use the agent's image-viewing tool). This verifies the source image, not its
crop, effects, or appearance in the composition. Use the installed Astrid
Python environment for these commands; in a checkout, activate `.venv` or use
`.venv/bin/python -m astrid` so declared dependencies are available.

To compare against composed pixels, pin the exact successful
render run with `--render-run <exact-run-id>` when one exists, or pin
`--revision-id <revision-id>` for a bounded current/historical capture. A
current-input view, a fresh composition capture, and an exact-run view answer
different questions, so retain their scope with any conclusion.

When `output` and `inputs` are both shown, the primary page uses a paired-row
layout: five (or the requested `--columns`) output samples per row with the
input lanes relevant to that row immediately underneath. The row's linear
half-open time axis is shared by cards, placements, audio rails, and the PNG
and `static_surface.rows` JSON contract.
Paired pages show one row by default (five columns unless changed with
`--columns`; `--columns 6` gives six across), while standalone output/input
pages keep their normal page sizing. `--page-size` changes the paired layout
only and does not make
standalone pages use the same card count. Pass `--page-size N` explicitly to
opt into a denser paired page, capped at two rows / 10 cards. Multi-page paired
results are intentional: open the numbered pages in order, then rerun with
`--range START..END --every 0.25` or `--shot first` / `--shot N` to drill down.
The frame index records the effective page size, page count, row ranges, and
copyable navigation guidance.

For a complete review pass, use `--show output,inputs,text,audio --every 5`.
The command reports the primary PNG, all numbered pages, and a bounded inspect
command. Open the pages in order; then use `--range 0..25 --every 0.25 --detail`
to zoom into one window, `--shot first` (or `--shot N`) to focus an authored
shot, and `--track vo` to isolate the voice lane. Use `--every` for seconds,
`--every-frames` for exact frame steps, `--columns`/`--page-size` for layout,
and `--resolution` for thumbnail size. The generated navigation metadata
contains copyable versions of these commands, so the full-timeline result is
the starting point rather than a dead-end overview.

```bash
python3 -m astrid timelines visualize <slug-or-id> --project <project> \
  --view filmstrip --render-run <exact-render-run-id> --every 0.5 --columns 5 --page-size 10 --include-media --json
python3 -m astrid timelines visualize <slug-or-id> --project <project> \
  --view filmstrip --render-run <exact-render-run-id> --at 12 --context 3 --every-frames 6
```

Use `--sample interval` (default) for regular time samples, `--sample clips`
for picture clips, `--sample cuts` for cut boundaries, and `--sample shots`
for authored story beat midpoints. Shots require authored shot metadata.
An explicit `--every` or `--every-frames` interval is a strict periodic grid;
pass `--include-cuts` when cut-neighbor frames should be added explicitly.
Shot-script context is retained in JSON/details and is shown once per shot
occurrence on review cards when no word/phrase timing exists; later samples
stay visually quiet while their machine-readable status is “same shot; no new
timed text.” Frozen timed speech captions
still take precedence, and “No timed text available” is used only when there
is no applicable timed phrase or shot script.
Use `--range 10..20`, `--shot`, `--clip`, or `--asset` to restrict the view.
The unified inspector can search dialogue, filter shots and time ranges, reduce
density, expand declared visual/audio tracks on one shared absolute-time ruler,
and enlarge a frame or copy its time, render-scoped target, and pinned focus
command. When the admitted render has audio, the disclosure adds bounded
multi-resolution waveform bins, measured “Quiet gap” intervals, and only
explicitly admitted immutable speech annotations. Audio rows are frozen to the
render; they never turn missing script into silence or present the rendered mix
as an isolated stem. Selecting an audio interval seeks the optional bundled
media and keeps the same frame/clip/track target model. If a clip has no
captured frame in its interval, the inspector says so and exposes the exact
focus command.
Density controls only select already captured frames; rerun with a finer
interval for additional detail. Extraction is bounded at 2,000 frames, so use
a coarser interval or a narrower range for long renders.

The PNG waveform uses a bounded display-only gain/contrast curve so quiet voice
remains visible; the JSON retains the measured amplitudes. Source audio-track
rails are timing signifiers (not per-source loudness measurements), and use the
same absolute time axis as the cards and input placements. In input lanes, each
rail is centered vertically inside its source clip and its label gets a dark
backing chip for legibility.

The default filmstrip route uses a matching successful render when one is
available, otherwise it captures the bounded requested frames through the
canonical Remotion compositor against the pinned timeline snapshot. The
explicit render-free `--mode inputs` route is the only route that does not
produce composed pixels. Filmstrips do not substitute source asset thumbnails.
PNG cards show
spoken text in quotes and a prominent local waveform/cursor when the admitted
render has audio. Script captions
remain authored segment text, not word-aligned transcription. Missing or
uncertain speech timing is reported as unavailable/partial, while waveform
inspection remains usable; opening the inspector never starts a provider call.
`--include-media` explicitly adds a relative, hash-verified video member for
offline playback. Analysis sidecars are bounded, keyed by immutable render and
settings identity, and verified in the bundle manifest. A pinned render keeps
old visual evidence associated with its own timeline state even after later
edits.

For rendered-output inspection, the rendered paired filmstrip/storyboard is
the canonical visual surface. It is not the only way to inspect a timeline:
the render-free `--mode inputs` route above exposes declared placements and
source media without pretending they are rendered pixels. There is no separate
structural diagram or frozen-object navigation route. Use `--render-run` when
the review must reuse a particular managed render; otherwise the default route
may perform a bounded composed-frame capture. Never supply a caller-owned
`--rendered-video` path. The frame index's render-scoped
range/shot/clip/track targets are the canonical navigation surface.

Use a coarse `--every 5` pass to locate a transition, then rerun the exact
time window with a finer `--every 0.25` (or `--every-frames 1` for frame-level
inspection) and add `--detail`. `--range START..END` is half-open, so the end
sample is excluded. `--shot first` and numeric `--shot N` use authored shot
order; use the exact shot name or a time range when chronological order is
what matters.

For the rhythm of a whole cut (how it moves and breathes, not what each frame shows), run the `editorial.pacing` executor rather than a visualize mode: `sdk.invoke("editorial.pacing", kind="executor", project="<slug>", inputs={"timeline_ref": "<ref>", "window": [start, end]}, client=client)` (`window` optional). It reads the current head read-only and writes a rhythm sheet PNG, Markdown and JSON with chapters, one log-scaled bar per visual cut, words per second with silences, cuts per 10 s against speech, and stalls. The requested `--view pacing` flag is not wired yet: `timelines visualize --view` accepts only `filmstrip`.

### Structural-to-visual navigation recipe

For an agent-facing investigation, keep the structural and visual calls next
to one another and reuse the returned target:

```bash
# 1. structural overview/current head
python3 -m astrid timelines show --project <project> <timeline> --json

# 2. visual overview on the exact successful render
python3 -m astrid timelines visualize <timeline> --project <project> \
  --view filmstrip --render-run <exact-render-run-id> \
  --show output,inputs,text,audio --every 5 --json

# 3. narrow both views to the same shot/time/clip/asset/track
python3 -m astrid timelines visualize <timeline> --project <project> \
  --render-run <exact-render-run-id> --shot <shot-id> \
  --range <start>..<end> --clip <clip-id> --detail --json
python3 -m astrid timelines inspect --manifest <manifest-path> \
  --section cards --limit 20 --json
```

The exact structural expansion is supplied by the authoring-bundle SDK when
the CLI `show` payload is not enough; it must return the same occurrence IDs,
media digests, and intervals that the visualizer uses. A result that cannot be
mapped back to the pinned scope is incomplete, not a new identity.

## Create and edit

The timeline-evaluation worker reads this checked-in skill at
`astrid/packs/rendering/skill/SKILL.md` when its case brief supplies that path.
The skill file is guidance, not an installed Python package: reading it does
not install Astrid. When the runtime package is installed, the public SDK
import below is the supported API; a checkout-only environment must report
that import/runtime capability as unavailable rather than inventing it.

### Canonical edit bundle

Use the same pinned parent composition for direct edits and code-driven batch
edits. `timelines show` opens a read-only inspection projection; it is not the
editable bundle. For an exact parent-media replacement, use the supported
`timelines replace-parent-media` command only when the case-specific target
receipt declares that route and supplies its required locator. For other
supported edits, use ordinary Python against the detached same-schema bundle:

```python
from astrid.sdk.authoring_bundle import (
    open_authoring_bundle, validate_authoring_candidate,
    diff_authoring_candidate, preview_authoring_candidate,
    publish_authoring_candidate,
)

candidate = open_authoring_bundle(
    pinned_parent, shot_revisions=pinned_shots,
    internal_timeline_revisions=pinned_internal_timelines,
)
# Make the requested small edit or run a deterministic batch transform here.
validate_authoring_candidate(candidate)
diff = diff_authoring_candidate(candidate)
frozen = preview_authoring_candidate(candidate)  # freezes candidate JSON; no pixels are rendered
publication = publish_authoring_candidate(candidate, writer, idempotency_key=run_id)
# Retain the receipt; reopen its exact closure only if a later check needs it.
```

Keep the exact publication response, including its `new_head` and complete
`dependency_manifest`. If readback is needed, reopen that returned closure
rather than a seed map or an agent-authored snapshot. These operations are
public where documented; an unsupported case-specific route must be reported
as unavailable, not inferred from the composition's shape.

`preview_authoring_candidate` and `render_authoring_candidate_preview` are
deliberately different operations. The former freezes a deterministic JSON
candidate for validation/diff/publication; it does not render pixels or prove
that a backend can play the result. The latter consumes that frozen candidate,
renders pixels through the runtime, and returns a render receipt/evidence
identity. Use the public `astrid.sdk.authoring_bundle` import shown above; do
not reach through `astrid.core` as a substitute public API.

For an explicitly requested composed preview, import
`render_authoring_candidate_preview` from that public module and render the
frozen candidate. Check its success and inspect/deliver the output before
publication when the user asks for that order (as in A01). If the required
preview fails, do not save. See the
[target-bound editing guide](../../../../docs/timeline-editing-guide.md) for
the optional preview branch and current source/artifact access limitations.

Keep authoring-bundle `placements` distinct from parent `occurrences`.
Placements are candidate rows describing where an item is placed in the
detached edit; parent occurrences are committed closure identities used for
readback and navigation. A placement may carry an occurrence reference, but
it is not a new parent occurrence and must not be substituted for one.
Start timing normally uses `row["placement"]["start_ms"]`; an existing
`row["at_ms"]` takes precedence, so preserve the opened convention. Duration
uses **`row["duration_ms"]` at the row level**, not inside `placement`.

Do not reconstruct or save a legacy whole-document `config`/`registry` payload
from `timelines show`. Use the detached checkout recipe above so the complete
parent/shot/internal closure, managed media identities, validation diff, and
compare-and-swap publication stay together. For reusable project shots, use
the nested `timelines shots` product only where its command is explicitly
available.

To group existing timeline clips into a named shot, use the canonical grouping
command rather than constructing shot resources and child documents by hand:

```bash
python3 -m astrid timelines shots group <timeline> --project <project> \
  --clip <picture-clip-id> --clip <voiceover-clip-id> --name "Opening" \
  --expected-version <version> --json
```

The command creates a registered shot and child timeline, associates its managed
media, and replaces the selected clips with one shot clip. It preserves their
timing and authored order. Selected clips must be adjacent in the document's
clip order; nested shots are unsupported. Use `--hold <seconds>` to extend the
shot window through an intentional pause. Read `show` again before grouping
the next shot, since each successful group advances the timeline version.
For a transient failure partway through, retain the returned idempotency key
and retry the same arguments with `--idempotency-key <key>`; the parent timeline
is saved last. A parent version conflict needs a fresh inspection, updated
version, and new key; the error identifies any resources left unattached by the
earlier attempt. Keep effects spanning multiple shots on the parent timeline.

Keep narration in the shot's canonical `voiceover_script` text binding alongside
its voiceover media. Do not leave the only copy in a generation script:

```bash
python3 -m astrid timelines shots text set <shot-id> --project <project> \
  --kind voiceover_script --text-file <script.txt> --expected-head 0
python3 -m astrid timelines shots text list --project <project> \
  --kind voiceover_script
```

Timeline-wide draft VO regeneration is not automated on this checkout:
`video_editing.sync_draft_voiceover` is not shipped. A manual edit that changes
narration length must keep the shot window in mind. Narration longer than its
shot window needs either a longer `duration_ms` (ripple later shots by hand, with
the detached diff as evidence) or a shorter line. Do not stretch another audio
clip or a parameterized layer to fit.

Publication accepts registered text descriptors in `shot["payload"]["text_bindings"]`.
The descriptor's `binding_id`, `head`, `media_id`, `content_hash`, and byte size pin
one verified revision; publication checks the registered head for a changed shot
revision. Arbitrary embedded-only narration and conflicting metadata copies are
rejected. Keep the registration receipt and publish that exact descriptor. For a
new reusable shot, publish its media/structure first to register it, register its
narration with head `0`, then publish the descriptor through the parent CAS. These
are explicit supported steps; registration alone does not change an existing
composition's pinned narration. A stale head requires a fresh checkout and explicit
reconciliation, never substitution of the latest text.

The runnable checkout example includes this complete path for a registered shot:

```bash
python3 scripts/timeline_document.py checkout --project <project> --timeline <timeline> --file /tmp/edit.json
python3 scripts/timeline_document.py bind-script --file /tmp/edit.json --shot <registered-shot-id> \
  --text-file <script.txt> --expected-head 0 --idempotency-key narration-01
python3 scripts/timeline_document.py check --file /tmp/edit.json
python3 scripts/timeline_document.py publish --file /tmp/edit.json --idempotency-key pin-narration-01
python3 -m astrid timelines shots text list --project <project> --kind voiceover_script
```

Reading back a placed occurrence's pinned narration (`timelines script` and
`client.timelines.script`) is not implemented on this checkout. Verify with the
binding list above, then re-check the published revision in Reigh. Placed
occurrence identity and timing still come from `timelines show` and the
checkout's `placements`.

`list` and `show <binding-id>` include the verified text. Use head `0` to create
a binding; read its current head before updating it. The binding belongs to
the registered shot referenced by the timeline; it does not add visible text
or regenerate audio. Managed renders pin its immutable text identity and head
in provenance. When importing an existing script, verify that it corresponds
to the shot's current voiceover audio.

The config must be renderable before spending a render attempt. Keep clip types
explicit, use registered element IDs for custom visual elements, and keep the
output/profile compatible with the authoritative theme canvas. Read
[references/timeline-cookbook.md](references/timeline-cookbook.md) when
constructing or checking the JSON shape.

For the constrained transparent PNG layer supported by the FFmpeg backend, use
one ordinary managed `clipType: "media"` clip with a positive `hold` on a
visual track placed first, followed by exactly one ordinary visual picture
track:

```json
{
  "tracks": [
    {"id": "overlay", "kind": "visual"},
    {"id": "picture", "kind": "visual"}
  ],
  "clips": [
    {"id": "overlay", "at": 0, "track": "overlay", "clipType": "media", "asset": "managed-alpha.png", "hold": 1.0},
    {"id": "picture", "at": 0, "track": "picture", "clipType": "media", "asset": "managed-video.mp4", "from": 0, "to": 1.0, "volume": 0}
  ]
}
```

The registry entry for `managed-alpha.png` must resolve to a local, probed PNG
whose decoded dimensions exactly match the visual canvas and whose PNG bytes
declare transparency. This subset supports one full-duration static layer;
arbitrary transforms, crop, per-layer blending, and multiple held overlays are
unsupported. The overlay is looped, bounded to its hold interval, normalized to
the canvas, and composited after the base visual concat; existing text overlays
still use their current path. Stream copy is disabled for this overlay path.

## Render and open

Render whenever you need to: renders are an ordinary part of the editing loop,
and you don't need to ask first. But look before you spend: run
`timelines visualize --view contact` on the timeline you're about to render (one
page, one frame per cut, shot names and words) and fix anything that's clearly
wrong. A render should confirm motion, timing and sound, not reveal a missing
plate. Render `--review` while iterating, and a clean render for the final
export. Admit long renders with `--detach` and follow them with the printed
`tasks follow <task-id>`. If a render fails, fix the cause and use
`tasks retry <task-id>`, because re-running the identical command replays the
failed result.

When a preview render is needed (including reference-frame and storyboard
previews), use `--review` by default. Deliver and open that review render so the user can identify frames
by shot name and timecode. Use meaningful registered shot names, and check that
the labels are visible in the exported video. A filmstrip viewer is useful
alongside the video, but does not replace its review overlay. Omit `--review`
when the user requests a clean or final export.

Render through the product command. The positional reference is a runtime slug,
UUID, or ULID; it is never a file path. Rendering pins the current kernel
snapshot. Add `--expected-version` when the observed version must remain
unchanged, select a qualified backend only when needed, and use `--detach` only
when admission without terminal completion is intended:

```bash
python3 -m astrid timelines render <slug-or-id> --project <project> \
  --expected-version <version> --review --output-name <name>-review.mp4 --json
```

Delivery audio (opt-in, off by default): `--audio-target -14 --true-peak -1` on `rendering.render` (or SDK input `delivery_audio`) masters the final mix to that LUFS with a true-peak ceiling (ffmpeg loudnorm, alimiter fallback; video stream-copied; AAC 320k). The render receipt (provenance `backend_fragments['rendering.delivery-audio']`) records before/after I, LRA and TP. A render whose ceiling cannot be met is refused, not published.

Review mode shows the registered shot name and running timeline time in the top-right corner in Remotion and Three.js, plus a readable bottom caption showing one phrase at a time, never the whole shot script. When the shot's VO clips carry word timings (`app.words`), phrases are timed to those words (breaks at sentence punctuation or silences of 0.25 s or more, at most two lines of 42 characters); without word timing, the script's sentences are distributed over the shot and marked non-word-aligned. No ASR timing is invented. Names and captions are pinned from canonical shot references and text bindings before expansion. Gaps show `No shot`; overlapping shots show all active names. The overlay exists only in this render; saved timeline documents are unchanged. FFmpeg rejects review mode explicitly; choose a review-capable backend for editorial previews rather than silently dropping the flag. SDK inputs use `"review": true`.

Remotion review renders default to a backend-native low resolution that fits the
authored canvas inside 640x360 while preserving its aspect ratio. The canonical
canvas remains in the timeline props and all positions are evaluated there;
Remotion's `--scale` performs the output scaling. The resulting artifact
profile records the dimensions Remotion actually emits. Such renders carry the
exact `Low Res Render` label at top left, while shot name and timecode remain at
top right. A clean render, or a render with an explicit profile, keeps the
existing full-resolution behavior.

Review labels and subtitles are inspection overlays. Keep the authored
creative composition centered in the full canvas; do not shift primary visual
content or reserve a caption-safe band for review text unless the user
explicitly requests a caption-safe design. Review typography scales with the
authored canvas under `--scale`, while badge readability is handled separately.

The default waits for completion and propagates terminal failure. A successful
render records its run and provenance in the runtime. Review the newest
successful render, or an exact run, through the runs surface. Opening video is
currently supported by the native macOS opener; Linux/AgentBox playback does
not imply that `open` or `afplay` exists. On Linux, use the returned verified
local media path with an installed player or decode/inspect it headlessly, and
report interactive playback as unavailable when no player/display is present:

```bash
python3 -m astrid runs open --project <project> --timeline <slug-or-id>
python3 -m astrid runs open <run-id> --project <project>
```

Use `--default-timeline` for the project's default. If no matching successful
render exists, render first or inspect `runs list/show`.

The [render capability contract](../executors/render/STAGE.md) covers detailed
SDK inputs and outputs; the [visualization contract](../executors/timeline_visualize/STAGE.md)
covers evidence navigation.

## SDK equivalent

Use the typed client or `astrid.sdk` when embedded in a program. Canonical
render uses `timeline_ref`, not a path-backed `timeline`:

```python
import astrid.sdk as sdk
result = sdk.invoke(
    "rendering.render", project="<project>",
    kind="executor",
    inputs={"timeline_ref": "<slug-or-id>", "expected_version": 4},
)
```

Use `client.timelines.visualize` for declared-input or composed-output evidence
and `client.timelines.show` / `client.timelines.open_composition` for bounded
text inspection. Use `rendering.render` to create an output before requesting
composed visualization. Programmatic edits use the detached authoring-bundle
validate/publish path above; mutable whole-document timeline saves are retired.
Keep `project` explicit and
use returned runtime IDs, manifests, and receipts for durable navigation.

## Renderer authoring

When building or extending a renderer, read the
[pack-builder skill](../../_core/skill/pack-builder/SKILL.md) and the protocol
contract at [docs/contracts/render-backend-v1.md](../../../../docs/contracts/render-backend-v1.md).
Renderer packs advertise qualified protocol capabilities; timeline editing and
the public `rendering.render` facade remain runtime-owned. Do not add a new
facade, direct module invocation, or backend-specific shape to the timeline.

## Runtime-owned multi-step stitching

For a two-child generation flow that must wait durably and preserve declared
output order, use the typed handoff in
`astrid.packs.video_editing.orchestrators.runtime_orchestration` and the stitch
admission in `astrid.packs.rendering.finalizers.runtime_stitch`. These modules
describe the graph and publication settings; the Runtime owns lifecycle,
continuation admission, canonical timeline CAS, and render-task creation. The
registered `rendering.assemble_timeline` executor consumes the claimed
`resolved_children` envelope and emits a deterministic authoring proposal. The
generic pack host sends that proposal through the fenced
`publish_timeline_render` checkpoint, which creates the ordinary
`rendering.render` task. Claim that task through the same host to run the
existing renderer and retrieve its output objects and receipt. Do not add a
pack-local timeline save, scheduler, polling loop, or second renderer.
