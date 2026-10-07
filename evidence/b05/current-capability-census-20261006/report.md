# B05 current capability census audit — 2026-10-06

## Scope and method

Read-only inventory of the candidate Astrid checkout at
`/Users/peteromalley/Documents/reigh-workspace/Astrid/.otto/worktrees/pack-authoring-convergence-20261001`.
The target assertion is `tests/packs/test_b2_integrated_closure.py::test_catalog_preserves_stage1_capability_census`.
No tests, Python imports, build/install, Runtime, provider, GPU, or network operations were run.

Commands used included:

```sh
sed -n '1,250p' tests/packs/test_b2_integrated_closure.py
rg --files astrid | rg 'orchestrator\.yaml$|executor\.yaml$'
for f in astrid/packs/*/pack.yaml; do awk ... "$f"; done
find astrid/packs -mindepth 4 -type f \( -name element.yaml -o -name manifest.yaml \) -print | sort
sha256sum <registry sources> astrid/packs/*/pack.yaml <element manifests>
```

The shell `awk` inventory counted top-level `actions` mapping keys in each pack manifest. Counts below are rule-derived from current manifests and the inspected projection code, not a live call to the registries. The current shell environment has no `ASTRID_PACKS_PATH`; `discover_pack_metadata` can also include a configured managed source inventory, which was outside this bundled-candidate census.

## Finding

The test's expected tuple `(23, 77, 13, 10)` does not match the candidate's declared, statically projectable bundled inventory. The smallest expectation-only correction for the bundled candidate is **`(23, 83, 7, 23)`**:

| Registry value | Current qualified bundled membership | Count | Test expectation |
|---|---|---:|---:|
| Discovered packs | 22 v3 packs + retained v2 `video_editing` | 23 | 23 |
| Executors | 82 v3 actions projected to `<pack>.<action>` + retained v2 `video_editing.cut` | 83 | 77 |
| Orchestrators | Seven retained v2 `video_editing.*` declarations | 7 | 13 |
| Elements | 24 pack-qualified element declarations; registry identity key is `(kind,id)`, so the duplicated `effects/text-card` resolves to one winner | 23 winner keys | 10 |

The old executor/orchestrator/element counts appear to be stale census expectations. In particular, counting v3 `actions` as executor entries follows the actual loader: `load_default_registry` converts every action key to `<pack>.<key>`, then adds the retained v2 executor roots. V3 `rendering` entries of type `renderer` or `finalizer` are not elements; only entries of type `element` enter the element registry. The v3 `ui.live-scenes` editor declaration is not an executor, orchestrator, or element.

## Qualified membership

### v3 action-derived executor IDs — 82

```text
blender: render
comfy_wrap: run
discord_local: command
editorial: arrange, boundary_candidates, editor_review, human_notes, human_review, inspect_cut, quality_zones, quote_scout, refine, scenes, script_pipeline, shots, transcribe, triage, validate
fal: fal_foley, h3_video
foley: tile_video, foley_review, foley_map
generation: generate_audio, generate_image, generate_image_cloud_i2i, generate_image_codex, generate_image_edit, generate_image_openai, generate_image_upscale, generate_video
h3_av: prepare, compile, compose, verify, transform
iteration: assemble, experiment_import, experiment_prepare, experiment_review, experiment_review_session
media: clip_extract, gif_search, speech_repair_lavasr
moirae: moirae
rendering: assemble_timeline, timeline_visualize, html_canvas_effect, timeline_storyboard, render, sprite_sheet
runpod: provision, exec, pull, teardown, session
seedance_local: reference_video
stream_content: clip_candidates, segment_map, distill
training: pool_build, pool_merge, search_loras, dataset_build, training_run
typed_timeline: map
understanding: audio_understand, visual_understand, video_understand, scene_describe, understand
vibecomfy: import, inspect, edit, run, validate, video_enhance, character_animation
wan2gp: generate_video, validate_settings
youtube: youtube_audio, upload
```

These are action IDs only; support files under a pack's `resources` and UI/rendering declarations do not add action entries. None of these v3 IDs are replaced by a same-ID source declaration in the bundled manifests.

### Retained v2 Video Editing declarations — 8

Executor: `video_editing.cut`.

Orchestrators: `video_editing.animate_image`, `video_editing.event_talks`, `video_editing.hype`, `video_editing.iteration_video`, `video_editing.logo_ideas`, `video_editing.thumbnail_maker`, `video_editing.vary_grid`.

The v2 pack-level `capabilities` list is descriptive metadata and does not create executor or orchestrator registry entries. A filesystem inventory found seven `orchestrator.yaml` files and one `executor.yaml` under `astrid/packs`; no v3 pack currently contributes an orchestrator manifest.

### Elements — 24 declarations, 23 registry keys

`rendering` v3 pack declares 10 elements:

```text
animations/fade, animations/fade-up, animations/scale-in,
animations/slide-left, animations/slide-up, animations/type-on,
effects/audio-reactive-colour, effects/text-card,
transitions/cross-fade, transitions/fade
```

`local` v3 pack declares 14 elements:

```text
effects/end-codex-transform, effects/end-mid-combined,
effects/end-minimax-animate, effects/end-spanning-layer,
effects/ending-carousel, effects/event-card, effects/frame-overlay,
effects/model-trends, effects/neon-orbit-card, effects/scrolling-guide,
effects/sliding-media, effects/text-card,
effects/vibe-comfy-asset-overlay, effects/vibe-comfy-bumper
```

The registry key is `(kind,id)`, not pack-qualified identity. Both packs declare `effects/text-card`, so the 24 source declarations yield 23 winner keys; source-pack discovery order registers `rendering` before project-local `local`, so `rendering.effects/text-card` wins the collision and the local declaration is shadowed. If the intent is to assert source membership instead of resolved registry cardinality, assert the 24 pack-qualified declarations and the collision explicitly instead of using `elements.list()` length.

## Projection rule evidence

- `astrid/core/execution/executor/registry.py`: discovery loads legacy executor roots, then loops each discovered pack's `actions.items()` and registers `action_executor_definition(pack, local_id, action)`.
- `astrid/core/execution/executor/actions.py`: action ID is constructed as `f"{pack.id}.{local_id}"`.
- `astrid/core/pack/walkers.py`: legacy executor/orchestrator roots are found by manifest walk; v3 elements are projected only from declared `rendering` items with `type: element`, while v2 retains filesystem element discovery.
- `astrid/core/execution/orchestrator/registry.py`: orchestrator registry is populated from discovered orchestrator roots; the bundled tree currently has only the seven retained Video Editing orchestrator manifests.
- `astrid/core/element/registry.py`: element registry uses `(kind,id)` keys and `list()` returns winners by default. Its pack element loader consumes the walker projection.
- `astrid/core/pack/discovery.py`: bundled source packs and the project-local pack are separate ordered layers; `ASTRID_PACKS_PATH` and configured managed source inventory can add external packs outside this report's scope.

## Smallest correction recommendation

Change only the expected count tuple in `test_catalog_preserves_stage1_capability_census` from `(23, 77, 13, 10)` to `(23, 83, 7, 23)`, if that assertion is intended to describe the current isolated bundled registry. No production source correction is indicated by this census. A follow-up hardening could assert qualified IDs (or per-layer sets) rather than counts, but that is broader than the smallest expectation-only correction.

Because the requested audit prohibited running tests or importing registry code, this recommendation is statically verified against declaration and projection rules but is not a claim that the dynamic registry tuple was executed in this audit.

## Hashes

Relevant source SHA-256:

| File | SHA-256 |
|---|---|
| `tests/packs/test_b2_integrated_closure.py` | `5ce8e327613ad127a2e7914b1593f7e45b247417511c5fa9c4441248d1daf2ec` |
| `astrid/core/execution/executor/registry.py` | `d1d76409d1b95cb4d9f049b0c6c8128503a1e2a4c0577f6107829d36a82250f2` |
| `astrid/core/execution/executor/actions.py` | `140d04e9db8c40c656e08b5d651dedb996dc3045841073ee153b4995cc0f73c9` |
| `astrid/core/execution/orchestrator/registry.py` | `701ac6af634806ea3628d5f0675f17995d643252211c080c0a91539719bdb9a3` |
| `astrid/core/element/registry.py` | `0fd28daf71fbb6190794835b7f606d36d25bc4700bc8fb9f8e464a07c5ac3ada` |
| `astrid/core/pack/walkers.py` | `06bc67a225f8d573c87e09f024d5afe1d39f58e521eee9c61ffd075edc0907dc` |
| `astrid/core/pack/discovery.py` | `076bd6ba127b9c49c0c4432fef806d20f7509e6ef2200e4612c38e3c816ab52f` |
| `astrid/core/pack/loader.py` | `6bdf29df4dbc21932c479b582cc443430753875133c3ed704a39556cec679619` |

Reproduce the aggregate pack-manifest identity with `sha256sum astrid/packs/*/pack.yaml | sort | shasum -a 256`; observed digest: `c82eb0a2d46c6eeab6ca848e2097af24648a091f0bdf90effc3d89e0af54bd38` (23 pack manifests). Reproduce the aggregate element-manifest identity with `sha256sum astrid/packs/rendering/rendering/elements/*/*/element.yaml astrid/packs/local/rendering/elements/effects/*/element.yaml | sort | shasum -a 256`; observed digest: `e1337b2a1ec77ce9753594303fdbd81cc3350cee63d8f299e6b57a7f8c7cfd84` (24 element manifests).

