# Check out a timeline, edit JSON, check it in

Use this for an ordinary user-authorized edit starting with a project and
timeline slug, without a coordinator-supplied target. A checkout is a detached
snapshot, not a lock. The runtime remains authoritative until publication.

The complete executable example is
[`../scripts/timeline_document.py`](../scripts/timeline_document.py). It defines
all connection, discovery, immutable-closure, validation, and writer variables;
read it when adapting the workflow. `AstridClient.open_from_launcher` resolves
project/timeline names and pins the head. The public launcher receipt and
`WorkspaceClient` supply the revision reads and publication port that the
high-level client does not yet expose as a general checkout method. It never
constructs a pretend coordinator capability or accesses private client fields.

Run with Python from the environment where Astrid is installed. Keep the same
launcher configuration as Reigh; for a checkout-backed installation this may
mean setting `BANODOCO_LOCAL_DATA_ROOT` to its already-configured data root.
Do not guess another root when the expected project is absent. Use project and
timeline **slugs or IDs**, not display titles.

```bash
# From this skill directory; replace the two names with the selected scope.
python3 scripts/timeline_document.py checkout \
  --project astrid-intro --timeline main-final-blackend2 \
  --file /tmp/timeline-edit.json

# Edit /tmp/timeline-edit.json with a text editor or a Python transformation.
python3 scripts/timeline_document.py check --file /tmp/timeline-edit.json

# After inspecting the resulting .check.json and completing the requested edit:
python3 scripts/timeline_document.py publish --file /tmp/timeline-edit.json \
  --idempotency-key intro-edit-pass-01
```

`checkout` reads the full pinned parent → shot revisions → internal timelines,
not the bounded `timelines show` projection. `check` validates and diffs locally
without connecting to or writing Runtime state. Its `.check.json` records
selected media identities and unresolved selectors. `publish` repeats that
preflight, rechecks the selected head, and sends one parent-CAS publication.
Keep the `.publication.json` receipt, including `new_head` and
`dependency_manifest`; use a new checkout for the next edit. Choose a distinct
idempotency key for each intended publication.

## The control map

The normal inspection loop is deliberately short:

```text
timelines show       authored structure and timing
        ↓
timelines visualize  composed pixels, with synchronized input lanes
        ↓
checkout             detached, editable JSON at the current head
        ↓
check                local validation and a readable diff
        ↓
publish              one parent compare-and-swap publication
```

Both inspection commands print the next useful command in human output and
return the complete set of copyable commands in `data.navigation` (or
`outputs.navigation` for a render-backed result). The visual route retains
`exact_frame`, sampling, range, resolution, lane, and inspection commands in
that structured object, so a compact terminal footer does not hide detail.
The edit guide and helper catalog below are the stable route for changes.

## Where to edit

In these paths, `work` is the JSON document, `row` is one member of
`work["placements"]`, and `shot = work["shots"][row["shot_id"]]`.

| Change | Authoritative field |
| --- | --- |
| Authored shot order | Order of `work["placements"]`; chronology also depends on starts |
| Shot start | Normally `row["placement"]["start_ms"]`; existing `row["at_ms"]` takes precedence, so preserve the opened convention |
| Shot window duration | `row["duration_ms"]` at the row level, **not** inside `placement` |
| Shot display name | `shot["payload"]["name"]`; the current review/name projection also reads `shot["payload"]["metadata"]["name"]`, so update that field when present and check the selected UI reader |
| Internal picture/audio/layers | `shot["internal_timeline"]["clips"]` and `["tracks"]` |
| Internal clip timing | Clip `at`, `from`, `to`, `hold` in seconds; `speed` changes visible duration |
| Selected media | Clip `asset` (or its existing selector) resolved by internal timeline `registry`; mirrored shot items' `media_id` must resolve to the same managed object |
| Parent overlays/effects | Existing `work["parent"]["clips"]` and parent config/registry fields |
| Spoken script | Registered shot's `voiceover_script` descriptor in `payload.text_bindings`, registered via `client.shots` / `timelines shots text`, then pinned by parent publication |

Script text and playable voiceover audio are separate: changing the binding does
not synthesize speech, and changing an audio clip does not update the text.
Read `timelines script` to inspect the selected composition's pinned narration;
project text lists include reusable unplaced shots and are not timeline script reads.
Associate bindings with the
registered shot, not a temporary authoring ID. See the skill's narration commands.

Preserve `base_parent`, `base_parent_payload`, `base_placements`, `source_mapping`,
and per-shot base/source fields. They are checkout provenance, not editable
content. Let compilation allocate revisions, digests, occurrence revision pins,
and the dependency manifest. `parent.occurrences` is derived from `placements`.
Keep unrelated and unknown payload fields for a lossless round trip. State the
ripple/quantization policy when retiming; changing a duration does not implicitly
move later placements or extend parent overlays.

## Add or replace media

Import new media before editing the checkout, using the public Astrid SDK:

```python
from pathlib import Path

result = client.media.import_file(
    project=project_id,
    path=Path("media/new-picture.png"),
    idempotency_key="maple-picture-import-01",
)
if not result.ok:
    raise RuntimeError(str(result.error))
media_digest = result.data["digest"]
```

Use the returned `sha256:` digest in the selected asset's registry entry, remove
temporary path fields, and update matching shot-item/media mirrors when the same
selection is recorded there. Preserve baseline provenance and historical
generation inputs. The checkout helper rejects local filesystem paths: it must
never appear to accept an edit while silently keeping the old managed pin.
Repeated imports are idempotent when the same key is reused; choose a distinct
key for each intentional import. Media import and parent publication are
separate transactions, so an imported object may remain in the project if a
later parent CAS fails. Re-check out if the parent head advanced.

## Review and recover

- **No render needed for saving:** refresh/open the same project and timeline in
  Reigh after publication. `timelines visualize --mode inputs` gives declared
  placement evidence without rendering. The default/`--mode auto` route uses a
  matching render when available and otherwise captures bounded composed frames
  through the canonical Remotion route. Use `--mode inputs` for render-free
  evidence; a frozen candidate JSON is not a visual preview.
- **Empty or wrong project list:** check the launcher/runtime selection before
  creating a new project or importing data.
- **Stale head:** check out a new file and reapply the intended edits. Do not
  overwrite the base head in the old file to force publication.
- **Missing media selector:** import through `client.media.import_file`, then
  use its returned managed digest in the selected registry entry and update any
  matching shot-item mirrors. Do not put a local path or guessed ID into the
  checkout document.
- **No-op diff:** an unchanged checkout now produces an empty authored diff.
  Placement timing and order remain meaningful changes.
- **Unsupported edit or unavailable API:** retain the local file and report the
  actual missing capability. Do not fall back to legacy document saves or
  manufacture a target descriptor that claims an edit capability.

## Editing helper catalog

The public `astrid.sdk.timeline_editing` helpers operate on the detached
candidate only. They do not publish, rewrite Runtime history, or bypass the
checkout's provenance fields. Use them for ordinary transformations, then run
`check` and inspect the diff before `publish`. The catalog is grouped by the
kind of edit it expresses and mirrors the module's `__all__` export list.

### Components and media

- `clone_candidate(candidate)` — make a lossless editable copy of a checkout.
- `add_track(container, ...)` — add a visual or audio track.
- `add_shot(candidate, ...)` — add a shot to the separate list-shaped
  candidate helper format (`candidate["shots"]` is a list). A checked-out
  authoring bundle stores `shots` as a mapping; use `add_authoring_shot(...)`
  for that shape instead.
- `place_media(container, media, track=..., start=..., end=...)` — place a
  managed media selector on a track. Import the file first; never put a local
  filesystem path in the checkout.
- `replace_media(container, clip_id, media, preserve_interval=True)` — swap a
  selected asset while retaining its timing by default.
- `duplicate(value, ...)` and `remove(container, item_id, ...)` — copy or
  remove a clip/row while preserving the surrounding document shape.

### Timing and frame policy

- `move(container, clip_id, track=..., start=..., end=...)` — move one clip.
- `retime(clip, start=..., end=..., ripple="none")` — change one interval;
  the default leaves later placements where they are.
- `retime_with_ripple(...)` — opt into a narrow ripple on one visual track;
  later clips on that same track move with the changed duration. Audio,
  voice, music, other tracks, and parent occurrences are not moved.
- `sequence(clips, start=..., durations=..., gap=...)` — lay out clips in
  order; `fit_duration(container, mode="extend")` updates the container to
  the resulting end.
- `frame_time(frame, fps)`, `source_to_timeline_time(...)`, and
  `timeline_to_source_time(...)` — convert between the admitted clocks.
- `quantize_time(seconds, fps, policy=...)` and
  `quantize_interval(...)` — make frame rounding explicit at the boundary.

For example, to move a visual clip and intentionally ripple later clips on its
same track:

```python
from astrid.sdk.timeline_editing import retime_with_ripple

retime_with_ripple(
    work["shots"]["shot-terminal"]["internal_timeline"],
    "clip-terminal",
    start=20.5,
    end=24.0,
    parent_duration="extend",
)
```

State the policy in the edit note. A duration change never implicitly moves
later placements, overlays, or audio.

### Layout and layers

- `align(clips, anchors, edge="start")` — align a set of clips to explicit
  anchors.
- `grid(rows, columns, gap=...)` — calculate repeatable positions for a
  multi-item visual layout.
- `reorder_layers(container, track_ids)` — change authored layer order without
  changing clip timing.

### Parent and authoring-shot structure

- `add_authoring_shot(...)`, `duplicate_authoring_shot(...)`, and
  `remove_authoring_shot(...)` — change the complete authoring-shot bundle.
- `move_occurrence_group(...)` — move a parent occurrence together with its
  linked internal structure.

These structural helpers are useful when a visual change spans a parent shot
and its internal timeline. They still produce one detached candidate; the
publication boundary remains `check` → `publish`, with the Runtime head
rechecked before the compare-and-swap.
