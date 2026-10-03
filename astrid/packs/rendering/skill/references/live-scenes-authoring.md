# Live-scene authoring and conversion

Live scenes are the rendering pack's route for a source-coded Three.js scene
that must be evaluated at exact timeline source times. Use it for authored
worlds, actions, camera behavior, and internal cuts. Use the normal
[timeline editing guide](../../../../../docs/timeline-editing-guide.md) for
ordinary clip placement, timing, and publication; a live scene does not add a
second timeline or retiming system.

The reusable loader and renderer belong to Astrid's existing `rendering` pack:
the declared editor entry is
[`editor/live-scenes/extension.tsx`](../../editor/live-scenes/extension.tsx),
and the pack's `pack.yaml` remains the discovery authority. A project scene's
HTML, source/data, immutable objects, and revisions belong to Runtime project
storage. Changing the pack implementation follows the normal extension
build/release path; editing a project scene does not rebuild the editor. Do not
edit a checkout database, generated catalog, private store, or mutable dev URL.

## Find the source and use the public route

Start from the active Reigh editor/runtime scope and the public timeline reader.
The live-scene reader projection identifies the target clip, its
`com.reigh.astrid.liveScene` clip type, `app.liveScene` cache, source/package
references, and ordinary timing (`at`, `duration`, `sourceOffset`,
`sourceEnd`, `rate`). The registered public handler is exposed through the
`reigh.live-scene-request/v1` ACP route, not through a private editor store or a
new Astrid CLI verb. The SDK types are exported from Reigh's public SDK; the
pack binds the handler through `ctx.liveSceneAuthoring`.

The agent receives an exact
`<reigh_live_scene_context>` scope. Read bounded source first, then publish
against the same session/turn capture:

```text
<reigh_live_scene_request>{
  "schema": "reigh.live-scene-request/v1",
  "requestId": "read-1",
  "scope": {"sessionId":"…","turnId":"…","projectId":"…","timelineId":"…","capturedTimelineVersion":7},
  "placementIds": ["<placement-id-from-active-timeline>"],
  "action": "read", "offset": 0, "length": 1200
}</reigh_live_scene_request>
```

The placeholder above is illustrative, not a live ID. Replace it with the
placement ID returned by the selected timeline's current public context; never
assume an example ID or acceptance fixture remains current.

The read result returns a bounded excerpt, total source length, placement
timing, and a `capture` containing project/timeline/version plus package and
entry object identities. A publish request copies that capture exactly and
contains one to sixteen `{before, after}` exact-text replacements. Each
`before` must occur exactly once in a previously read excerpt; missing,
ambiguous, overlapping, duplicate, stale, or out-of-scope edits fail closed.
The host accepts at most 32 KiB per request and 24 KiB per result; source reads
are at most 8,192 characters. Never send a multi-megabyte entry through ACP.

The current ACP editor changes bounded text spans in the canonical single HTML
entry only. It does not edit an arbitrary multi-file TypeScript project, bundle
new dependencies, or change manifest duration; publication preserves the
captured manifest. Bundle/retool on the authoring side before preparing the
replacement entry. If a scene duration changes, explicitly reconcile affected
ordinary clip ranges and any separately requested audio timing through the
existing timeline APIs; publication does not stretch or ripple them.

The rendering pack re-reads and digest-checks the canonical package and entry,
ingests a new immutable package revision, updates every selected placement with
one atomic patch, awaits `TimelineOps.flush()`, and returns the small durable
receipt. The capture's `projectId`, `timelineId`, and
`capturedTimelineVersion` are expected-head/CAS guards. On a stale or changed
scope, discard the candidate, reread the live source, and retry from a new
capture. `flushReceipt.version` is the acknowledged editor timeline version,
not the package revision. See the existing
[timeline publication guide](../../../../../docs/timeline-editing-guide.md)
for the surrounding timeline publication model.

## Package and entry contract

The current manifest is exactly these fields:

```json
{
  "formatVersion": 1,
  "entry": "two-shot-threejs.html",
  "duration": 4,
  "authoredFps": 30
}
```

`entry` is a safe `.html` path without a `..` path segment; `duration` and
`authoredFps` are finite positive numbers. Authoring-side package input is
`{manifest, entry: {bytes, mediaType: "text/html", filename}, assets}`. The
authoring validator requires `entry.filename === manifest.entry`; assets are
immutable byte inputs with media type and filename. It ingests the entry and
assets through the public project-object capability, then serializes one
package object (`application/json`, filename `scene.package.json`) as:

```json
{
  "manifest": {"formatVersion": 1, "entry": "two-shot-threejs.html", "duration": 4, "authoredFps": 30},
  "entry": {"object_id": "…", "digest": "sha256:…", "media_type": "text/html", "size": 1234, "filename": "two-shot-threejs.html"},
  "assets": []
}
```

The exact UTF-8 bytes of that serialized body determine the package revision.
The revision is kept outside the body because including it would be circular.
The only inline clip cache envelope is:

```json
{
  "revision": "sha256:<packageBody-digest>",
  "source": {"objectId": "<package-object-id>", "revision": "sha256:<packageBody-digest>"},
  "packageBody": "<exact UTF-8 JSON text>",
  "html": "<entry object's UTF-8 text>"
}
```

`html` is a derived mirror, not a second source of truth. Its UTF-8 digest and
byte size must match the persisted `entry` metadata in `packageBody`; the
package revision and `source.revision` must match the digest of `packageBody`.
Manifest-only changes therefore publish a new package revision. Do not spread
another mutable `manifest`, `entry`, or `assets` object beside this envelope.
The current authoring limits are an entry of at most 8,388,608 UTF-8 bytes, a
package body of at most 65,536 characters, at most 16 assets, and at most
16,777,216 total asset bytes. The ACP limits above are separate: each exact
replacement's `before` and `after` is at most 8,192 characters, and a publish
must contain one to sixteen replacements.
The implementation is in
[`editor/live-scenes/authoring.ts`](../../editor/live-scenes/authoring.ts),
[`editor/live-scenes/runtime.ts`](../../editor/live-scenes/runtime.ts), and
[`live_scenes/package.py`](../../live_scenes/package.py).

The underlying package types can carry immutable asset metadata, but the
current prepared browser command supports only a self-contained HTML entry and
the currently supported empty asset list. `Import prepared scene` opens a
native file picker for JSON with `manifest`, prepared entry metadata, `html`,
and exactly `"assets": []`. The public parser is
`parsePreparedScenePackage(bytes)` and the pack-owned admission operation is
`importPreparedScene(ctx, prepared, placements)`. It verifies UTF-8, JSON,
supported manifest keys, entry filename/media type, digest, and byte size; the
normal command supplies one ordinary `V1` placement at `0` with source range
`0..manifest.duration` and rate `1`, through ordered `clip.add` followed by
`clip.update` operations. Callers that need different timing may instead pass
their own explicitly validated placements; read active clip IDs and timing
from the selected public timeline rather than relying on canned Maple fixture
ranges. Placement values are caller data, outside the generic
parser/admission logic. This is not generic ZIP import,
arbitrary-project automatic conversion, dependency installation, or a general
asset-serving layer. The prepared file must not claim a persisted object ID or
revision. Bundle dependencies, fonts, and assets into the HTML on the authoring
side before admission. The compatibility aliases `parsePreparedMaplePackage`
and `importPreparedMapleScene` are retained only for existing Maple acceptance
callers; the public command itself is format-generic.

Bundle dependencies, fonts, and scene assets on the authoring side. Do not run
arbitrary ZIP build scripts during import, load remote modules, assume a runtime
dependency installer, or expect served project-object URLs. For the current
self-contained HTML consumer, code and anything it needs to render must be in
the entry or otherwise already supported by the package/runtime path; listing
an asset object does not create a browser URL for the entry.

## The hosted lifecycle and one clock

The entry implements exactly one host-facing interface:

```ts
interface AstridScene {
  initialize(): Promise<void>;
  render(input: {sourceTime: number; width: number; height: number}): Promise<void>;
  dispose(): void;
}
```

`initialize` creates the final composition and awaits its own asset/font/frame
readiness. The host bridge then waits for `document.fonts.ready` and decodes
document images before acknowledging `initialized`. `render` must evaluate and
draw the requested source time, then return only after the final visible
surface (including authored overlays/text/postprocessing) is complete.
`dispose` must release GPU resources and be idempotent. The bridge serializes
render requests, uses revision/instance/request identity to reject stale
messages, retries initialization for a bounded period, and reports an error on
timeout; do not use a sleep as a readiness proof.

The host owns time. For a clip at composition time `at`, with source trim
`from` and positive playback `speed`, the current source time is:

```text
sourceTime = (from ?? 0) + max(0, compositionTime - at) * (speed ?? 1)
```

The active interval is half-open. A clip with `from`/`to` has duration
`(to - from) / speed`; `hold` uses `hold / speed`. The same mapping is used
when Astrid materializes a nonzero render window. Two clips can reference the
same package revision while keeping independent `at`, source range, and rate.
Use ordinary timeline operations for placement, trim, repetition, and rate;
use scene source/data for an internal action or camera-cut timing. Changing a
scene duration never stretches or ripples existing clips or audio clips.

Evaluate from source time, not from a delta: reset or derive every animated
property from the requested time so a direct seek and a backward seek produce
the intended frame. Do not start `requestAnimationFrame`, native playback,
input-driven mutation, or a competing audio clock. The template's
[`two-shot-threejs.ts`](../templates/two-shot-threejs.ts) shows this pattern.

## Sets, actions, cuts, and timing edits

It is useful, but optional, to organize source as one `world`/set constructor,
an action evaluator, and named camera cuts. These are ordinary code/data
conventions, not a required world registry, shot schema, host shot ID, camera
UI, or internal-track conversion. Source time selects the authored cut; shot
local time may drive camera motion, while world/action time defaults to source
time. An alternate angle of the same action requires explicit author-side world
time mapping or a separately authored interval; another trim alone does not
create an alternate angle. Keep independently editable events (for example,
camera cuts and background transitions) on separate named source-time controls,
even when their initial values are equal. This lets an agent retime one without
silently moving the other. If a cut boundary moves, reconcile dependent clip
trims, package duration, and any separately requested audio timing.

Instances share immutable source/assets, not mutable running simulation state.
Maple remains its native continuous take; do not force it into the optional
two-cut organization. The Maple camera-edit helper is an acceptance fixture,
not a normal discovered command or general camera editor. For ordinary code
edits, use a bounded source excerpt and exact replacements, preserve bytes
outside changed spans, reopen the returned revision, and check the displayed revision/loading/error
state before rendering its pinned revision.

For a shared-world edit, change one set or subject statement and verify that
both named cuts show the same published change. For a camera-only timing edit,
change a named cut's `start`, camera path, or look-at data while leaving world
construction and action evaluation unchanged; verify source times on both
sides of the boundary. The template uses no randomness; if a scene adds it,
seed it from author-controlled inputs so a direct seek reproduces the frame.

## Audio, security, and export boundaries

The hosted entry is silent. It must not start `Audio`, WebAudio, or use audio as
its clock. Retained audio source/code may stay in project source, but audible
content uses ordinary timeline audio clips, which own placement, trim, rate,
mute, and gain. There is no automatic audio extraction, track creation,
procedural capture, or live scene-audio synchronization. An offline score can
be an ordinary managed audio asset. See the existing
[render capability contract](../../executors/render/STAGE.md) for the normal
render facade and audio ownership; do not invent a scene-audio subsystem.

The preview document uses an iframe with `sandbox="allow-scripts"` and an
opaque origin plus a restrictive CSP: no default sources, inline scripts and
styles only, data/blob images and data fonts, `connect-src 'none'`, and
`media-src 'none'`. This limits the implemented transport and network surface,
but it is not an arbitrary-code security sandbox or a blanket guarantee
against JavaScript behavior. The trusted editor extension remains trusted;
muting an HTML audio element would not contain arbitrary WebAudio. Do not
describe this route as a security boundary or load credentials, remote
dependencies, project URLs, or unverified code.

Astrid's existing Three.js/Remotion composition consumes the pinned live-scene
revision and waits for the requested frame; the backend remains visual-only
for the hosted entry while ordinary timeline audio is handled by the existing
capture route. When calling the managed `rendering.render` executor directly,
pass `selector: "rendering.threejs"` for a timeline containing
`com.reigh.astrid.liveScene`; an omitted selector defaults to ordinary
`rendering.remotion`, which does not implement this scene composition. The
Reigh editor router already selects the Three.js backend for its supported
live-scene subset; direct SDK callers must do the same. Do not add this clip
type to ordinary Remotion's registry. The relevant composition mapping is in
[`ThreeTimelineComposition.tsx`](../../../../../remotion/src/ThreeTimelineComposition.tsx).
The template's `render` resizes the renderer to the host viewport, updates the
camera aspect, evaluates the final DOM label and WebGL surface, and calls the
WebGL context's finish hook before returning. Export therefore captures the
requested pinned frame, not a last-good preview frame.
The Reigh live-scene clip contribution itself is not a browser/worker export
capability; do not claim a generic Reigh export path. A true non-Maple live
acceptance through the current public interfaces starts with the bounded
prepared-HTML admission above and continues with bundling, publication, and
runtime/render checks; it does not add a generic ZIP importer or export path.

## Bundle and smoke the template

The template is TypeScript source, not an external dependency URL. Bundle it
with the repository-pinned `three` from the Remotion workspace, then place the
result in a self-contained HTML entry before creating the immutable package:

```bash
remotion/node_modules/.bin/tsc --noEmit --strict --skipLibCheck \
  --target ES2022 --module ESNext --moduleResolution Bundler \
  --baseUrl remotion --typeRoots remotion/node_modules/@types \
  astrid/packs/rendering/skill/templates/two-shot-threejs.ts
remotion/node_modules/.bin/esbuild \
  astrid/packs/rendering/skill/templates/two-shot-threejs.ts \
  --bundle --format=iife --platform=browser --target=es2022 \
  --alias:three=./remotion/node_modules/three \
  --outfile=/private/tmp/two-shot-threejs.bundle.js
```

For the supported public admission check, place that bundled code and its
inline styles in one self-contained HTML entry, then construct the prepared
JSON handoff with the exact four-second manifest, entry filename/media type,
UTF-8 byte `size`, SHA-256 `digest`, `assets: []`, and `html` text. Do not add
an object ID or revision: Runtime assigns both during admission. With the
normally activated rendering-pack extension, run the public `Import prepared
scene` command and select that JSON. The command calls
`parsePreparedScenePackage` and `importPreparedScene`; it creates one ordinary
visual clip on `V1` at `0`, source `0..4`, rate `1`, and awaits the public
timeline flush. Repeating the command chooses the next available
`live-scene-import-N` clip ID. Verify the resulting entry/package bytes and
timeline row through the public reader/project-object APIs. Use ordinary public
timeline operations for a second source range, then use the existing source
read/exact-replacement publication route for the template's shared-world and
camera-only edits; changing duration remains an explicit manifest-plus-clip
edit. This recipe is bounded prepared-HTML admission, not ZIP import, a
runtime bundler, dependency installation, or private-store injection.

The following is a developer-only host verification recipe, not a supported
end-user authoring route. Wrap the bundle in the HTML entry, construct the exact
immutable envelope, and exercise the internal `sceneDocument`/`SceneFrameHost`
path at `0.5s`, `3s`, then backward to `1s`. Check the named
`WIDE` → `CLOSE` → `WIDE` surface, ready acknowledgement for each requested
source time, and idempotent disposal. This internal smoke cannot substitute for
visible browser acceptance through the normally activated extension and public
admission path. If the actual browser host is unavailable, report that
separately from successful typecheck/bundle results. Do not invent a
private-store or test-only command and call it user-facing acceptance.
