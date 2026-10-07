# B05 Editorial stale path handoff

The bounded test-path edit is complete in the three authorized test modules.

- `test_validate_clip_duration.py`: imported `clip_timeline_duration_sec`
  from `editorial.actions.validate`.
- `test_quality_zones_hash.py`: imported `compute` and patched both
  `_run_ffmpeg` targets under `editorial.actions.quality_zones`.
- `test_experiment_review_session.py`: imported Human Review from
  `editorial.actions.human_review`.
- The pre-existing M09 Iteration action imports in the same file are preserved
  exactly.

Before/after SHA-256 values and the complete incremental diff are recorded in
`after-01.json` and `incremental.diff`.

The two Editorial test modules pass (`4 passed in 0.27s`). The exact required
three-file command exits `2` during Iteration test collection because the
moved Human Review action still imports the separately migrated Training
orchestrator path. That product-source repair is outside B05's write set and
was left untouched. The scoped `git diff --check` passes.

The Runtime D18 reservation is released. No stage, commit, merge, push,
publish, deployment, provider, network, GPU, browser, profile, project-data,
or user-data operation was performed.
