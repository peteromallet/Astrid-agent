# B05 current local portfolio expectation refresh

Date: 2026-10-06

## Scope

Updated only `tests/packs/test_b2_integrated_closure.py` and this evidence artifact. The aggregate capability census, migrated teaching-example assertions, external fixture assertions, local pack order, retired-root checks, `_core` manifestless check, and Runtime CLI mount checks remain intact.

## Current manifest-backed portfolio

The test now expects these 22 schema-v3 migrated roots, preserving their order in `LOCAL_PACK_IDS`:

`blender`, `comfy_wrap`, `discord_local`, `editorial`, `fal`, `foley`, `generation`, `h3_av`, `iteration`, `local`, `media`, `moirae`, `rendering`, `runpod`, `seedance_local`, `stream_content`, `training`, `typed_timeline`, `understanding`, `vibecomfy`, `wan2gp`, `youtube`.

`video_editing` is the sole intentionally retained schema-v2 root. Its manifest points to `skill/SKILL.md`; that file exists and resolves as a bundled resource. Each migrated v3 manifest points to `docs/SKILL.md`; each file exists and resolves as a bundled resource. The portfolio test also validates canonical manifests. The separate bundled-resource assertion still checks every discovered resource remains inside its pack root and exists.

The source tree also currently has `astrid/packs/video_editing/docs/SKILL.md`, while the v2 manifest still declares `skill/SKILL.md`. This is a present extra file, not the v2 manifest's declared skill/resource; the test retains the manifest's actual v2 identity and does not silently reinterpret it as migrated.

## Verification

Only the requested portfolio assertion was run:

```text
/Users/peteromalley/Documents/reigh-workspace/Astrid/.venv/bin/python -m pytest -q tests/packs/test_b2_integrated_closure.py::test_current_catalog_matches_authoritative_local_portfolio
.                                                                        [100%]
1 passed in 2.82s
exit code: 0
```

`git diff --check -- tests/packs/test_b2_integrated_closure.py` passed (exit code 0).

## Source hash

SHA-256 before edit: `8aa81e4359dfb18b785fa4552af05931d56ba551294eb54959f23444798e19b2`

SHA-256 after edit: `5ce8e327613ad127a2e7914b1593f7e45b247417511c5fa9c4441248d1daf2ec`
