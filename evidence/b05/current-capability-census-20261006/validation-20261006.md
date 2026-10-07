# B05 bundled capability census correction — validation

Date: 2026-10-06  
Candidate: `/Users/peteromalley/Documents/reigh-workspace/Astrid/.otto/worktrees/pack-authoring-convergence-20261001`  
Reservation: `tests/packs/test_b2_integrated_closure.py::test_catalog_preserves_stage1_capability_census` and evidence in this directory.

## Result

The central assertion now pins the current bundled mixed-v2/v3 registry
projection: **(23 packs, 83 executors, 7 orchestrators, 23 element registry
keys)**. This changes the stale tuple and adds a short test docstring only.
No production registry, manifest, collision policy, or global v2 support was
changed. All other test assertions were preserved.

The membership basis is the accompanying
[read-only census report](report.md):

- 23 bundled packs: 22 v3 packs plus retained v2 `video_editing`.
- 83 executors: 82 v3 action-derived qualified IDs plus retained v2
  `video_editing.cut`.
- 7 orchestrators: the seven retained v2 `video_editing.*` declarations.
- 23 element registry keys from 24 declarations. The registry keys by
  `(kind,id)`; the duplicate `effects/text-card` declaration resolves to the
  existing `rendering` winner over `local`.

The existing report lists every qualified action and orchestrator membership,
both element declaration groups, and the projection rules in the loader and
registries. Its source-derived counts are now corroborated by the isolated
dynamic test below.

## Pre/post identity

| State | SHA-256 of `tests/packs/test_b2_integrated_closure.py` |
|---|---|
| Before this assertion-only correction | `5ce8e327613ad127a2e7914b1593f7e45b247417511c5fa9c4441248d1daf2ec` |
| After correction | `c1c90a7366abc010a9737a8c12306ba35d8cea98481e6794a9ae612cc4b2a7e4` |

## Exact validation

The existing B05 harness was reused to isolate the bundled inventory from
configured/managed pack sources. The source-state sentinel was confirmed absent;
`ASTRID_SOURCE_STATE` pointed to it, `ASTRID_PACKS_PATH` was unset, Python
bytecode and pytest cache writes were disabled, and only the reserved test was
selected.

```sh
test ! -e /private/tmp/astrid-b05-absent-source-state-20261002.json
ASTRID_SOURCE_STATE=/private/tmp/astrid-b05-absent-source-state-20261002.json \
env -u ASTRID_PACKS_PATH PYTHONDONTWRITEBYTECODE=1 \
python3 -m pytest -q -p no:cacheprovider --tb=short \
  tests/packs/test_b2_integrated_closure.py::test_catalog_preserves_stage1_capability_census
```

Result: **1 passed in 13.39s**, exit code 0.

## Limits

Only the exact named test ran. No broad suite, build/install/import sweep,
Runtime/provider/GPU/network operation, or source/manifest/registry edit was
performed. The count is the isolated bundled candidate census; configured or
managed external pack sources are deliberately excluded.
