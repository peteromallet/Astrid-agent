# M16 composition closure — Stream Content public action calls

Status: complete within the M16 composition reservation. Source writes stop after this handoff. No commit, merge, push, deploy, publish, sync, package install, provider/network request, browser, GPU, or real-media operation was performed.

## Authority and preflight

- Integration worktree: `/Users/peteromalley/Documents/reigh-workspace/Astrid/.otto/worktrees/pack-authoring-convergence-20261001`
- Candidate manifest: `evidence/wave3/post-f04-cancel-requested-candidate-manifest.json`
- Candidate SHA-256: `75295b192dbc2b1ec345fd241764a7c69f4f0fc8a99b5778f5bd3d341feb0367`
- M16 preflight SHA-256: `7ef0e7b1f1131ea21bdf3ceef14669be956cee1772f3bd771438cd6606145841`
- Astrid/Reigh/Runtime heads matched the preflight: `9be144c4f2f63216e693a1a13e42cd79d3845845`, `34a8a2ff2cbeb8132346cbf43e33955be6063776`, `a278cd460018976940ae21fc2cad563a29a29649`.
- All nine preflight source/dependency hashes matched, including the three mutable files and the accepted Editorial/Media receiver files.
- Reservation `M16-composition-01` was active at source-write start and covered exactly the three source/test files. The M16/M09 reservations were recorded disjoint.
- Capacity gate `evidence/wave3/capacity-two-writer-02.json` passed its 360.038-second window: minimum observed free space `15,812,232 KiB`, net decline `33,268 KiB` under the `65,536 KiB` limit. Immediate recheck `evidence/wave3/capacity-two-writer-02-recheck.json` reported `15,792,024 KiB` against the `3,849,952 KiB` floor. An immediate local `df -k .` recheck reported `15,926,376 KiB` available.

Machine-readable preflight, capacity and verification details are in `verification.json`; before/after source hashes are in `source-hashes.json`.

## Implementation

- `distill/run.py` now invokes `editorial.transcribe`, `editorial.scenes`, and `media.clip_extract` through the existing `astrid.sdk.invoke(..., kind="action", wait=True)` child boundary with deterministic child keys and bounded polling.
- The already-admitted original video is passed as an object binding derived from its verified bytes (`object_id` plus safe filename). It is not registered as a producer/derived input.
- Settled child output rows are checked by exact output port and association, materialized through `InvocationResult.materialize_output`, confined beneath the parent output root, and re-hashed/size-checked before consumption. Clip bytes are copied to the existing `segments/<index>-<slug>.mp4` names after custody verification.
- Existing same-pack subprocess steps remain same-pack subprocesses: segment mapping and candidate scoring. Existing plan, review, manifest, optional transcript/brief/no-scenes behavior and output names remain intact.
- `plan_template.py` describes the two Editorial steps with the same public SDK action IDs and typed input names used by execution. The plan remains version 2 and does not introduce an invocation framework or session path.
- The focused test adds offline fakes around the actual SDK child boundary. It asserts all three public IDs, waited calls, original-video object bindings, materialized child output custody, preserved clip naming, and public plan IDs.

## Verification

Exact commands and results are recorded in `verification.json`.

- `PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/packs/stream_content/test_stream_content.py -q --tb=short -p no:cacheprovider`: **9 passed in 1.11s**.
- Scoped AST parsing and guarded imports of `run.py`, `plan_template.py`, and the focused test: **exit 0**.
- Tracked test `git diff --check`: **exit 0**.
- Untracked-source `git diff --no-index --check` checks: expected comparison exit `1`, no whitespace diagnostics.
- The reserved Stream Content distill files contain the public IDs and no direct `astrid.packs.editorial.*` or `astrid.packs.media.*` private/module call.

## Hash transition

| Path | Before SHA-256 | After SHA-256 |
| --- | --- | --- |
| `astrid/packs/stream_content/actions/distill/run.py` | `47a93a033639cae65279eeb25b9f2baacfd85be6c603a5bbefb01a97754829c6` | `c27ee284dcaeff92ab4b49b0a70a8ae46433e3b8ad004f719858ac74b157297d` |
| `astrid/packs/stream_content/actions/distill/plan_template.py` | `eef0937c0b0972a32f8ebcea05b1a4ecd358551cf4f7ce15d53ba6172a6a7bd3` | `09ad8ebc15084123aac22b69e1fac50ee318bcc15f7893ee8658755cc9000c2f` |
| `tests/packs/stream_content/test_stream_content.py` | `3639c2bd02b33c6d5f72646b22c98ff245a5a86de089a33ea27e1952b00fffc4` | `cbe450d695477a5a6b9edcaf4d66c9d1f0809954f8ecf2d8169e79a988e6aac3` |

## Residual boundaries

- The accepted M04/M11 receiver contracts and unchanged D18 F04/F05 bridge/materialization evidence still suffice for this caller adoption. This handoff does not claim live Editorial transcription, scene detection, Media extraction, provider credentials, normal-vendor Runtime execution, or real user-media behavior.
- M04's combined central test collection failure remains open for B05; no central tests were edited or retried.
- `editorial.refine`'s Video Editing old-path failure remains with M21.
- No Astra High consultation was needed: the public input/custody choice is settled by the accepted D18 bridge contract and the existing Foley public-child precedent.
