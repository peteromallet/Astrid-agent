# M04 Editorial pack migration handoff

## Result

M04 is complete for the Editorial pack root. `astrid/packs/editorial/` is now a
v3 pack with stable pack ID `editorial`, one directly authored skill at
`docs/SKILL.md`, and 15 unified `actions/` declarations. No central consumer,
test, other pack, build file, generated catalog, or runtime file was edited.
No commit, push, merge, deploy, production workflow, media render, network
call, model/API call, or user project write was performed.

Source checkpoint and identity:

- Worktree: `/Users/peteromalley/Documents/reigh-workspace/Astrid/.otto/worktrees/pack-authoring-convergence-20261001`
- Starting candidate: `evidence/p05/post-m03-migration-progress-candidate-manifest.json`
  (SHA-256 `bff18dc5c27b047d2fc77fa9e9ab9734257115cbde2341d3590677692f72d990`)
- Starting source verification: `evidence/p05/post-m03-migration-progress-source-verification.json`
  (SHA-256 `cfc5a325b59f97a2bf21eed8f93e920518c36f897bdf8eda3a1ac11bbebed26d`)
- Astrid base HEAD: `9be144c4f2f63216e693a1a13e42cd79d3845845`
- Disk headroom sampled during the work: approximately 757 MiB; the brief's
  bounded-validation limit was respected.

## Public operation reconciliation

The v2 executor descriptors were reconciled into exactly these v3 action IDs;
the set, typed inputs/outputs, cache behavior, clip kinds, conditions, graph
edges, isolation, requirements, scoped configuration, keywords, pipeline
requirements, permissions, and result/resource contracts were checked against
the source descriptors:

`arrange`, `boundary_candidates`, `editor_review`, `human_notes`,
`human_review`, `inspect_cut`, `quality_zones`, `quote_scout`, `refine`,
`scenes`, `script_pipeline`, `shots`, `transcribe`, `triage`, `validate`.

The parity probe reported `public_action_count=15`, exact ID-set equality, and
`parity=OK` for every action (`all_typed_contracts_checked=true`). The two old
descriptors without an `outputs` key were represented as explicit v3
`outputs: []` for `human_notes` and `inspect_cut`; this preserves their empty
output contract. Missing legacy descriptions were supplied from the stage
guides/action purpose without changing operation behavior. Pack ID,
version/name metadata, and permission IDs remain stable. The manifest is
`schema_version: 3`.

## Path and ownership mapping

- `executors/<action>/{run.py,__init__.py,STAGE.md}` moved to
  `actions/<action>/`; the 15 executor descriptors were removed and their
  contracts are inline in `pack.yaml`.
- `executors/refine/src/reviewers/**` moved to
  `actions/refine/src/reviewers/**` and is declared in the refine resources.
- `executors/script_pipeline/presets/**` moved to
  `actions/script_pipeline/presets/**` and remains declared by that action.
- `executors/_common.py` moved to `shared/_common.py`. Static import evidence
  shows it is used by `editor_review`, `refine`, and `transcribe`.
- `hype/{__init__,arrangement_rules,enriched_arrangement,text_match}.py` moved
  to `shared/hype/`. Static import evidence shows use by arrange, inspect_cut,
  quality_zones, refine/reviewers, and validate.
- `skill/SKILL.md` moved to `docs/SKILL.md`; no `executors/` or `skill/`
  directory remains below the Editorial root.
- Added package markers for the root, `actions/`, and `shared/` namespaces.
- Local imports and action runtime module/file paths were rewritten to the v3
locations. The action/top-level `resources` lists have no duplicate paths (61
explicit implementation/documentation entries); the `documentation.path`
entry supplies the one skill resource. Canonical closure therefore sees the
full pack exactly: 62 declared non-manifest resources, 62 actual non-manifest
files, missing `[]`, extra `[]`.

Content hashes for the moved implementation and the new manifest are recorded
in [final-hashes.json](final-hashes.json). The recorded after-pair manifest
digest is `b08cfeb8bd4846a8e7a0facc41cd1a2fb5adb720f1546106bb195976143fdf44`.

## Skill and static checks

All checks were local and bounded:

| Check | Result |
| --- | --- |
| `PYTHONDONTWRITEBYTECODE=1 .../.venv/bin/python -m astrid.core.pack.cli validate astrid/packs/editorial --json` | PASS; v3, `valid: true`, zero errors and warnings |
| `.../.venv/bin/python -m astrid.core.pack.cli inspect editorial --pack-root astrid/packs --json` | PASS; discovers the v3 pack, 15 actions, and `docs/SKILL.md` |
| Canonical resource closure probe | PASS; 62/62, no missing or extra resources |
| `scripts/reshape/package_closure.py .` | PASS; `{"ok": true}` |
| Python AST parse of all Editorial Python files | PASS; 44 files |
| Skill frontmatter/link probe | PASS; exactly `docs/SKILL.md`, valid `name`/`description`, zero relative links outside the root |
| Action `python -m ... --help` binding probe | PASS for 14/15 actions; see dependency limit below |
| `git diff --check -- astrid/packs/editorial` | PASS |

The six remaining help bindings (`scenes`, `script_pipeline`, `shots`,
`transcribe`, `triage`, `validate`) also returned zero; together with the first
eight passes this covers 14 actions. No tests were added or run, per the
pack-migration brief and the low-disk constraint.

## Unexercised dependency and central old-path evidence

`refine` was AST-checked and its v3 declaration/resource closure passed, but its
help binding cannot import because the action reaches a central/M21-owned
Video Editing module that still imports the old Editorial shared path:

```
astrid/packs/editorial/actions/refine/run.py:52
  -> astrid.packs.video_editing.executors.cut.run
astrid/packs/video_editing/executors/cut/timeline_build.py:40
  -> astrid.packs.editorial.hype.arrangement_rules
ModuleNotFoundError: No module named 'astrid.packs.editorial.hype'
```

This was left unchanged because M04 must not migrate `video_editing` or central
consumers. Other exact old-path evidence remains for the B-lane owners and is
intentionally reported rather than edited:

- `astrid/skills/view.py:31` and
  `astrid/packs/_core/docs/creative-work/{SKILL.md:42,references/packs.md:12}`
  still point at the old skill path (catalog/core documentation owners).
- `astrid/packs/video_editing/executors/cut/{run.py:40,timeline_build.py:28,40}`
  and `astrid/packs/video_editing/orchestrators/hype/runner.py:522,527` retain
  old Editorial imports (M21/video-editing owner).
- `astrid/packs/stream_content/orchestrators/distill/{plan_template.py:146,153,run.py:320,336}`
  retain old transcribe/scenes imports (M16 owner).
- `astrid/packs/iteration/orchestrators/experiment_review_session/run.py:237`
  retains the old human-review import (M09 owner).
- `astrid/packs/training/orchestrators/dataset_build/{source_providers/youtube.py:167,phases.py:480,filter_stages/transcript_keyword.py:171}`
  retain old Editorial imports (M17 owner).
- Core guides/docs retain old references at `docs/reference/architecture.md:94,115,120`,
  `docs/architecture/repo-shape.md:112,143,158`,
  `docs/guides/creating-tools.md:109`,
  `docs/examples/training-workflow.md:203`, and
  `guides/replacing-speech-in-existing-video.md:81` (B01/B04/B05/B06/B07
  documentation owners).
- Central Editorial tests and fixtures still import or assert old paths,
  including `tests/test_refine.py`, `tests/test_validate.py`,
  `tests/test_quality_zones.py`, `tests/test_human_notes.py`,
  `tests/test_boundary_candidates.py`, `tests/test_triage.py`,
  `tests/test_pipeline_caching.py`, `tests/packs/test_pack_layout_contract.py`,
  `tests/v10/test_pack_write_path_lint.py`, and
  `tests/fixtures/recoverability_conformance_worklist.json` (central test
  owners; not edited).

No live provider, ffmpeg/scenedetect, human-review server, media input, or
external package dependency was exercised. The only remaining entrypoint
uncertainty is the cross-pack old-path dependency above; the migrated pack's
v3 schema, declarations, resources, imports local to the pack, and static
operation contracts are otherwise validated.
