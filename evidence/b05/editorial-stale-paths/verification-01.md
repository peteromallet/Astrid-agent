# B05 Editorial stale path verification

Worktree: `/Users/peteromalley/Documents/reigh-workspace/Astrid/.otto/worktrees/pack-authoring-convergence-20261001`

The three requested Editorial test imports were moved to their v3 `actions/`
modules. The two pre-existing M09 Iteration import changes remain in place.

## Required scoped command

```text
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q --tb=short -p no:cacheprovider tests/packs/editorial/test_validate_clip_duration.py tests/packs/editorial/test_quality_zones_hash.py tests/packs/iteration/test_experiment_review_session.py
```

Result: exit code `2` during collection. The Editorial duration and
quality-zones modules are reached, but the Iteration module fails while loading
the moved `editorial.actions.human_review` action because that action still
imports the separately migrated, out-of-scope path
`astrid.packs.training.orchestrators.dataset_build.state`.
The candidate contains `astrid.packs.training.actions.dataset_build.state`;
repairing that Training import would edit product source outside the B05 write
set and was not performed.

## Isolated Editorial test outcome

```text
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q --tb=short -p no:cacheprovider tests/packs/editorial/test_validate_clip_duration.py tests/packs/editorial/test_quality_zones_hash.py
```

Result: exit code `0`; `4 passed in 0.27s`.

## Required scoped whitespace check

```text
git diff --check -- tests/packs/editorial/test_validate_clip_duration.py tests/packs/editorial/test_quality_zones_hash.py tests/packs/iteration/test_experiment_review_session.py
```

Result: exit code `0`; no diagnostics.

No product source, unrelated tests, runtime source, user data, provider,
network, GPU, browser, profile, project-data, packaging, publish, deployment,
stage, commit, merge, or push operation was performed. The Runtime D18
reservation is released.
