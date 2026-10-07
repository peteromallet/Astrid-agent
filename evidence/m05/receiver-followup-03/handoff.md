# M05 Fal actual-receipt follow-up 03

**Disposition: PASS for the scoped correction.**

## Correction

`fal.fal_foley` now writes `audio.wav.fal.json` before `manifest.json`. The authoritative receipt contains exactly two concrete named entries:

- primary `audio` at `audio.wav`, role `result`, ordinal `0`
- auxiliary `audio_provenance` at `audio.wav.fal.json`, role `auxiliary`, ordinal `1`

Both paths are spool-relative. `write_manifest` computes the receipt entries' exact `sha256:` hashes and byte counts. `manifest.json` remains the receipt and is not listed as a harvested result.

The existing receiver test now executes `run.main` through the canonical Fal action, normal host-owned output binding, and `harvest_staged_outputs` with a synthetic response and patched offline seams. It asserts receipt and harvested descriptors against the exact files, hashes, byte counts, names, and attempt-spool containment. The optional `env_file` omission/path-with-spaces and dry-run tests remain present.

## Verification

- Focused Fal receiver plus H3 tests: **9 passed**.
- Canonical Fal validate: **PASS**.
- Canonical Fal inspect/discovery: **PASS**; both Fal actions and resources resolve.
- Canonical package resource closure: **PASS** (`ok=true`).
- Scoped Ruff: receiver test **PASS**; runner **PASS** with the known pre-existing `E402` guard diagnostic excluded.
- Scoped `git diff --check`: **PASS**.

Exact commands/results and hashes are in `verification-01.json`.

## Limits

No live Fal provider, network, credential resolution, GPU, install/build, central B05 test, H3 behavior change, GenericPackHost credential-readiness change, F04/F05 edit, profile mutation, merge, push, publication, deployment, or user sync. This does not claim the full GenericPackHost provider path or `env_file`-only host readiness.

Final source hashes:

- `astrid/packs/fal/actions/fal_foley/run.py`: `7d3ed3c26b78a01113f19b40a0486144da367d28acf79cf91824e1d8d890c5ee`
- `astrid/packs/fal/tests/test_fal_foley_receiver.py`: `51ad6d7a72d3722fbc88a0f417ae29c8816796feba2dd8d375d210581e86bb1c`
