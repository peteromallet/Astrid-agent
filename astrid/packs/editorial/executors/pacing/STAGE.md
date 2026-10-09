# editorial.pacing

**Executor**: `editorial.pacing`
**Status**: implemented
**Role**: Review (auxiliary, outside the numbered pipeline)

Renders one rhythm sheet for a timeline, so a reviewer can see how the video
flows: where it races, where it breathes, and where it stalls. It reads the
timeline through the runtime at its current head and never writes to it.

## Inputs

- `project_slug` (string, optional): bound by the invocation (`project=`).
- `timeline_ref` (string, required): timeline slug, UUID or ULID.
- `window` (json, optional): `[start, end]` seconds; the sheet and its statistics
  are zoomed to that span. Density keeps context outside the window.

## Outputs

- `pacing_png`: 2400 x 1350 rhythm sheet (chapter band, cut-length bars, speech
  density with silence gaps, cuts per 10 s against speech, beats lane, summary).
- `pacing_md`: summary table, cut table, stalls, silences, method notes.
- `pacing_json`: the full model (`schema: editorial.pacing/v1`).

## Method

- **Cuts**: each clip on a shot's `plate` track is one visual cut. A shot with no
  plate clip counts as one cut. Bar height is log2 of duration, 0.5 to 16 s.
- **Kind**: `app.kind` when set; else `presenter` when an `am-presenter` clip
  overlaps the cut; else `silent` when no speech overlaps it; else `illustrative`.
  `silent` and `joke` share one colour.
- **Word timings**, in order of preference: `app.words` on a VO clip (relative to
  the clip start); `params.words` on a shot element (the lip-sync list that AM
  clips already carry); then the shot's text-binding word count, spread over the
  VO clips with no timed words, in proportion to their duration.
- **Speech density**: words whose midpoint lies within 1 s of each 0.1 s grid
  point, divided by 2 s.
- **Silence**: a gap of 0.4 s or more with no speech. Drawn as a grey strip.
- **Energy**: cuts starting within a 10 s window centred on each grid point,
  against normalised speech density.
- **Stall**: a cut longer than 6 s with no deliberate flag, or a stretch longer
  than 6 s under 1 word/s outside deliberate holds. A deliberate hold is
  `app.deliberate_hold` or `params.deliberate_hold` set to true.
- **Beats**: the optional music lane reads `app.beats` from the parent timeline
  config. It accepts a list of seconds, a `{"beats": [...]}` object, or a JSON
  path. The `chiptune.compose` beats.json has a `beats` list of seconds, so it
  loads as is. No linked beats file has been run through this executor yet.

## Known limits

- Speed other than 1x on a placement is noted, not applied to clip times.
- Clip kind relies on the plate track; a shot that hides its cut inside another
  track is counted as one cut.

## Dependencies

- Pillow (already in the Astrid environment). No network.
