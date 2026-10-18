# Editor panel authoring

This starter is reconciled against the current rendering pack and pinned Astrid App SDK. The independent author exercise on the Beat Cue example remains a separate final proof.

## Start from the V3 UI scaffold

Pack IDs accept lowercase letters, digits, and underscores; use `beat_cues` (not `beat-cues`). For an editor-catalog proof, put the pack under the checkout's `astrid/packs/`: the catalog generator scans that source tree and does not use `ASTRID_PACKS_PATH`. A pack outside the tree can pass direct `validate <path>`, but the editor generator will not discover it. Use a disposable Astrid checkout so the proof never rewrites the candidate checkout's rendering catalog:

```sh
ASTRID_CHECKOUT=/path/to/Astrid
tmp_astrid="$(mktemp -d "${TMPDIR:-/tmp}/astrid-panel-proof.XXXXXX")"
mkdir -p "$tmp_astrid/scripts"
cp -R "$ASTRID_CHECKOUT/astrid" "$tmp_astrid/astrid"
cp "$ASTRID_CHECKOUT/pyproject.toml" "$tmp_astrid/pyproject.toml"
cp "$ASTRID_CHECKOUT/scripts/gen_editor_extension_catalog.py" "$tmp_astrid/scripts/"
cd "$tmp_astrid"
python3 -m astrid.core.pack.cli new beat_cues --starter standalone --role ui --destination "$tmp_astrid/astrid/packs/beat_cues"
python3 -m astrid.core.pack.cli validate "$tmp_astrid/astrid/packs/beat_cues" --json
python3 scripts/gen_editor_extension_catalog.py
rg -n "packId: 'beat_cues'|@astrid/packs/beat_cues" astrid/packs/rendering/editor/catalog.ts
```

The source package fallback reads the checkout-root `pyproject.toml`, so include it in a minimal copy. The generator uses its own checkout root and writes only the copied `astrid/packs/rendering/editor/catalog.ts`. The `rg` check should show both a `packId: 'beat_cues'` row and its `@astrid/packs/beat_cues/...` entry import.

The scaffold starts with `ui.editor` and `ui/editor/extension.tsx`. Rename that key and entry path to match your panel. The V3 declaration follows the rendering pack's current `ui` convention:

```yaml
ui:
  beat-cues:
    type: editor
    entry: ui/beat-cues/extension.tsx
    target: video-editor
    compatibility:
      sdk: '1'
    resources:
    - kind: support
      path: ui/beat-cues/BeatCuePanel.tsx
```

The rendering pack's live transcript entry is [ui/transcript/extension.tsx](../../astrid/packs/rendering/ui/transcript/extension.tsx), with its React panel alongside it. The small [Beat Cue example](../../examples/editor-panels/beat-cues/extension.tsx) uses the same entry/component split and contributes one panel only. For a real pack, place those two files under the declared `ui/beat-cues/` paths above. The example source is kept under `examples/` for review; it is not another pack, loader, catalog, or persisted domain and is not loaded until a V3 UI declaration points to it.

## Declare and register a panel

Keep the pack UI key, extension ID, contribution ID, and render ID stable. The V3 pack key identifies `rendering.beat-cues`; Astrid App resolves the panel by the extension ID plus contribution ID. The host's selected-panel preference uses that qualified runtime identity. The current transcript binding is the rendering pack's `transcript` key → `haus.astrid.transcript-lane` → `transcript-lane-panel`.

In the extension entry, declare one `kind: 'panel'` contribution with a render ID, then bind that ID through `ctx.ui.registerRenderer`. Use the returned disposal handle as the activation result. Return a React element so hooks and panel-local drafts belong to the mounted component instance:

```tsx
return ctx.ui.registerRenderer<PanelRenderProps>(
  RENDER_ID,
  (props) => createElement(BeatCuePanel, props),
);
```

The public shared props are `PanelRenderProps`: `instanceId`, `visible`, optional `video`, and optional host-owned `settings: { open, onOpenChange }`. The retained Transcript panel uses this presentation seam for Back to transcript; other panels may ignore it. The video-free panel is valid. When present, `video.view` is the read-only timeline snapshot/observation surface and `video.seek(seconds)` is a host callback. Do not reach into app internals or keep another playhead store. See the complete entry and typed component in the example. The entry declares `apiVersion: 1`; its V3 UI declaration advertises `compatibility.sdk: '1'`. If the host rejects an unsupported API version, tell the host author to use a compatible SDK 1 host or update the entry's compatibility declaration before loading it again.

## Render states, observe, and clean up

Give unsupported context a clear recovery action, such as “Open this panel in the video editor with a timeline selected.” Show loading while the host reports that the timeline surface is not mounted, show a useful empty state when the panel has no rows to display, and put failures in an accessible `role="alert"` region. The example demonstrates these states around one seek interaction. For an API-version rejection, tell the host author to use a compatible SDK 1 host or update the entry's compatibility declaration; do not render a blank panel or retry with guessed props.

The host keeps a hidden panel mounted when the user switches panels. Keep local selection, scroll, and drafts in that component; pause subscriptions while `visible` is false, refresh from the host when it becomes visible, and dispose every subscription/listener on hide or unmount. Use an active flag or `AbortController` for asynchronous reads so late results cannot update a replaced panel. Runtime and the host remain the authority for timeline, transcript, media, persistence, history, and durable revisions. A panel's React state is presentation state only.

## Transcript source, playback, and correction recipe

Use the optional `props.video?.transcript` adapter exposed by the host; it can be absent on unsupported/read-only hosts. `getSnapshot()` returns readonly `sources` and projected `occurrences`. Source rows carry canonical source times and supplied text/speaker/word timing. Occurrences carry host-projected timeline intervals for a clip use. Do not infer repeated-clip timing, rewrite source times from the playhead, or persist projected occurrences. Subscribe with `subscribe(listener)` for canonical document changes and observe `video.view` for playback updates; dispose both handles while hidden and refresh immediately on reveal.

The current transcript contract is exported from `@astrid/editor-sdk` in `video/transcript`. `TranscriptSelection` is the `{ assetId, sourceId, occurrenceId }` tuple; `TranscriptSourceIdentity` is `{ assetId, sourceId }`. Keep the media/source pair together because the same source ID can occur on different assets. `getPlayback(selection)` returns focused and all-playing occurrences/word IDs; `selectSource(selection, sourceIdentity)` returns the next local selection. The host adapter owns trim/speed/repeat projection. `seek({ occurrenceId, wordId? })` returns a `TranscriptSeekResult`: success identifies the resolved seconds and whether timing was word- or segment-level; failure reports unavailable, stale occurrence/word, or out-of-trim targets. Unusable word timing falls back to the segment start. For a generic time jump, use the shared `video.seek(seconds)` callback shown in the Beat Cue example.

For text correction, submit a typed word/segment edit to optional `editText(edit)` and render its result. `{ ok: true, changed }` means the host accepted the canonical text-only edit; `{ ok: false, code, message, fallback? }` gives an actionable error and, when alignment is ambiguous, a suggested whole-segment edit that the user must submit separately. If `editText` is absent, keep the panel read-only. Include the displayed row’s `assetId`, `documentId` and `documentVersion` as `baseVersion` in an edit, and retain that base while typing. Call `stageTextDraft(edit)` for valid field changes to retain unsent text. Await `editText`: project-source edits write the shared project document with version checking, independently of timeline timing/history. Use `getProjectState()` for Saving, Unsaved and Conflict status; `undoSource`, `redoSource` and explicit `resolveSourceConflict(documentId, "discard" | "reapply")` operate on that source. Preserve local drafts after a conflict; never silently overwrite a newer document. Do not write a transcript file, call private `commitData`, or mutate a projection.

Transcript import is also optional. When present, `getMediaChoices()` supplies eligible audio/video assets; `importTranscript({ document, associationAssetId? })` returns asset/source identity plus imported/replaced counts or throws a typed actionable error. If the file has no asset identity, ask the user to choose from those host-provided choices. Await the import receipt: the project document saves first, followed by its timeline association. `associationSaved: false` means the project transcript exists but timeline association still needs retry. Keep the selected bytes for retry. `listProjectSources` and `selectProjectSource` reuse existing project transcripts; `promoteSource` explicitly saves a Timeline only transcript to the project and rejects divergent canonical content. Edits apply everywhere that project transcript is used. The normal reading source picker is separate from the host’s tool picker. The example intentionally uses none of these transcript-specific callbacks.

## Optional atomic timeline edits

This is an optional trusted UI-extension recipe, not a first-panel prerequisite and not an agent-tool registration. When an extension genuinely needs timeline edits, use `ctx.creative.timeline`: call `validate(patch)`, then `preview(patch)` and show its diagnostics/diff before applying a confirmed batch once. `apply(patch)` validates and routes the atomic mutation through the host's existing timeline history path; the editor's normal undo/redo remain the user's history controls. `checkpoint(label?)` and `rollback(checkpointId)` are available for an extension-owned explicit rollback (`rollback` returns the diff or `null`). Await `flush()` only when the UI needs durable-save acknowledgement; it returns an editor timeline version, not a Runtime revision. Let the host own conflict/retry UX: a rejected transport call does not prove that the server did not commit. Transcript corrections should use the transcript adapter above so shared word/source identity and its fallback rules stay intact. See the SDK `TimelineOps` and `TimelinePatch` contracts for exact shapes.

## Transcript settings and reading

The host presents one Inspector / Extensions navigation row, with Panel display on its right. Inspector contains only inspector content. The Extensions child header contains the tool picker and the enabled-extension settings menu. The outer Extension settings menu manages enabled extensions. Open the dedicated Transcript settings icon at the top right inside the Transcript box to import, promote Timeline only text, or toggle Show transcript lane. The lane toggle uses `getTimelineLaneVisible` / `setTimelineLaneVisible` and affects only the transcript lane. Compact, Full-height and Closed preserve the retained panel’s source, draft and scroll. Render canonical prose once, matching words against exact segment text; unreliable alignment stays intact and seeks the segment. Arrow keys move through timed targets, Enter/Space seeks, and the contextual Edit action opens Save/Cancel.

## Optional transcript lane

Keep a lane as a separate `dataKind` contribution; a panel does not draw into `TimelineCanvas`. The transcript entry declares `kind: 'dataKind'`, `kindId: 'astrid.transcript'`, `schemaRef: 'astrid.transcript_segment/v1'`, `shape: 'interval'`, and `domain: 'source_seconds'`. It binds the renderer with the existing `ctx.dataKinds.register(kindId, renderer, inspector?, options?)` API and disposes the returned handle alongside the panel renderer. The current transcript entry uses:

```tsx
const laneRegistration = ctx.dataKinds.register(
  TRANSCRIPT_KIND_ID,
  TranscriptLane,
  undefined,
  { supportsSparseItemWindows: true },
);
```

Omit `laneHeight` for the host's media-row-height default. Set it only when a lane intentionally needs a fixed custom height.

Implement the lane as `(props: DataLaneRendererProps)`. Paint only the bounded `props.items`; the host supplies occurrence IDs, mapped `timelineStart`/`timelineEnd`, and the shared `pixelsPerSecond` scale. `itemWindow` carries absolute indices for sparse/virtualized windows; do not call `getAllItems()` while rendering. `activeItemId` remains the keyboard roving tab stop. Paint the lane-local host selection from `selectedItemIds`: an explicit empty array selects nothing; when absent, fall back to `selectedItemId` (`null` selects nothing), then the legacy active-item behavior. Keep `focusItemId` and `playingItemIds` separate. Ordinary controls call `onSelectItem(itemId)` to replace selection; Shift/Cmd/Ctrl clicks call `onSelectItem(itemId, { additive: true })` to toggle that occurrence. Forward these options unchanged through wrappers. `onNavigateItem` retains single-item keyboard navigation. The host owns mixed media/data lasso and its read-only multi-item Inspector summary; selection does not mutate source content. The host owns source-to-timeline projection, clipping, speed, repetition, focus and geometry. See [ui/transcript-lane/TranscriptLane.tsx](../../astrid/packs/rendering/ui/transcript-lane/TranscriptLane.tsx) and the registered entry in [ui/transcript/extension.tsx](../../astrid/packs/rendering/ui/transcript/extension.tsx).

The panel selector and lane visibility are independent; the lane starts hidden under the host's saved `showDataLanes` preference. Do not create a lane-specific source or persist occurrence geometry.

## Validate, build the catalog, and preview

From an Astrid checkout root, validate the rendering V3 pack and check its existing generated editor catalog without rewriting it:

```sh
python3 -m astrid.core.pack.cli validate astrid/packs/rendering --json
python3 scripts/gen_editor_extension_catalog.py --check
```

When a declared editor entry changes, regenerate the projection from the same checkout root with `python3 scripts/gen_editor_extension_catalog.py`; it writes `astrid/packs/rendering/editor/catalog.ts`. The V3 validator checks declarations and declared paths. The generator calls `discover_packs()` rooted at that checkout's `astrid/packs`; setting `ASTRID_PACKS_PATH` does not add packs to this editor projection. Do not edit `editor/catalog.ts` by hand.

From the Astrid App root, set `ASTRID_CHECKOUT` to the disposable checkout to select it for local preview:

```sh
cd /path/to/astrid-app
ASTRID_CHECKOUT="$tmp_astrid" npm run dev:editor
```

This starts the committed local bridge stub and launches Vite. Vite preflight requires that checkout's `astrid/tools/catalog.json` to be valid and admit `video-editor`; copying the full `astrid/` tree supplies the existing catalog. If that whole-Tool catalog is missing or stale, copy `scripts/gen_tool_catalog.py` into the temp checkout and run `python3 scripts/gen_tool_catalog.py` there; its optional positional argument is an output path, and its default writes `astrid/tools/catalog.json`. `dev:editor` does not run this generator itself. This whole-Tool preflight is separate from the editor-extension catalog above. `npm run build` runs the app's Vite production build. The local dev route is not production admission evidence: use the reviewed owner/release controls for non-DEV loading, and never add a panel to the app shell by hand.
