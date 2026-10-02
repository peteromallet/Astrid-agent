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
not the bounded `timelines show` projection. `check` probes every referenced
local file, prepares a detached copy with calculated media digests, validates,
and diffs locally. It does not connect to or write runtime state. Its `.check.json`
lists the files and import keys that publication will use. `publish` repeats
that preflight, rechecks the selected head, imports through the existing project
catalog, validates the durable result, and sends one parent-CAS publication.
Keep its `.prepared.json` durable rewrite, `.imports.json` catalog receipts,
and `.publication.json` receipt,
including `new_head` and `dependency_manifest`; use a new checkout for the next
edit. Choose a distinct idempotency key for each intended publication.

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

## Add or replace local media

Local paths are temporary inputs in the checkout document. To replace a selected
image, video, or audio asset, add `local_path` to its existing registry entry:

```python
shot = work["shots"][row["shot_id"]]
shot["internal_timeline"]["registry"]["assets"]["picture"]["local_path"] = "media/new-picture.png"
shot["internal_timeline"]["registry"]["assets"]["voiceover"]["local_path"] = "media/new-voiceover.wav"
```

For a new clip, create a registry entry with `local_path` and select that asset
key, or use a local file path directly as the clip's `asset`/`asset_id` selector.
New or changed `file`, `path`, and `src` fields in registry entries are also
accepted as local inputs. Relative paths resolve from the checkout JSON's
directory; absolute paths and local `file://` URLs are accepted. HTTP URLs are
not downloaded. Unchanged managed registry filenames remain presentation data.

The public SDK operations `plan_authoring_media(candidate, base_directory=...)`
and `import_authoring_media(plan, transport)` implement this preparation. Import
uses `WorkspaceClient.import_project_media`; it does not create a second media
store or claim a provider generated an upload. The existing Runtime import
publishes managed project bytes and an ordinary catalog Generation/original
Variant with `external_upload`/`imported` provenance. Audio imports keep audio
MIME/type and duration, omit visual dimensions, and have native audio controls
in Reigh's gallery and lightbox.

The prepared document uses durable managed media IDs. Local registry locators
and stale selected-object hashes are removed; matching shot items, audio
bindings, and asset descriptors follow the selected replacement. Baseline
provenance and historical generation inputs remain pinned. If one old object
has multiple different new selections, update each mirror explicitly rather
than relying on an ambiguous replacement.

Preflight rejects missing/non-media/undecodable files and files over the existing
64 MiB Runtime limit before importing anything. Files are checked again before
the first import. Repeated references share an import; retries have stable
import keys and reuse matching imported catalog entries. A different imported
filename retains its distinct import provenance even when bytes match.

Imports and parent publication are separate existing transactions. If a later
import or parent CAS fails, the timeline is unchanged; completed imports remain
ordinary project catalog media. Keep the source JSON and retry after resolving
the failure. If the parent head advanced, check out a fresh document and reapply
the edit. The helper preserves the editable source JSON and writes the durable
rewrite separately.

## Review and recover

- **No render needed for saving:** refresh/open the same project and timeline in
  Reigh after publication. `timelines visualize --mode inputs` gives declared
  placement evidence without rendering. `--mode auto` only adds composed output
  if a matching render already exists. Render only when the requested review
  needs it; a frozen candidate JSON is not a visual preview.
- **Empty or wrong project list:** check the launcher/runtime selection before
  creating a new project or importing data.
- **Stale head:** check out a new file and reapply the intended edits. Do not
  overwrite the base head in the old file to force publication.
- **Missing media selector:** use the temporary local input fields above or
  import explicitly through `WorkspaceClient.import_project_media`, then use
  its returned managed identity. `media.import_file` alone manages project bytes
  and does not promise gallery visibility. Raw paths must pass through
  preparation before durable publication; do not guess managed IDs.
- **No-op diff:** an unchanged checkout now produces an empty authored diff.
  Placement timing and order remain meaningful changes.
- **Unsupported edit or unavailable API:** retain the local file and report the
  actual missing capability. Do not fall back to legacy document saves or
  manufacture a target descriptor that claims an edit capability.
