# Review round-1 corrections — normal Luna

Run: `h3-full-merge-megado-plan-20260930`  
Date: 2026-09-30  
Source evidence: `findings/pre-live-integrated-review-r1.md`  
Status: candidate corrections implemented; this is not a pre-live review pass.

## Exact custody and comparison identities

The correction work stayed in the three isolated candidate worktrees. No
source main, donor, external orchestrator, provider/GPU/live state, shared
environment, promotion, merge, or push was touched.

- Reviewed RunPod candidate: HEAD `15748580e4a5922eb4a856b36d88faa1f67004e1`,
  tree `ff6da3f8360e7544580ed7512117d3a8ef828680`, version `0.3.1.dev0`.
- Reviewed VibeComfy candidate: HEAD
  `46ed4f8aeea144b4bbf1d9293b0f2012f05dddc2`, tree
  `6ba22cf8a1fb5ec3707dcccf4100ea5f44f83674`.
- Astrid pre-correction candidate: HEAD
  `0537c3727a9b0109b88cd2892db19952b439b30d`, tree
  `dd9fc3333d18aec56162139116eef242062c6673`.
- Accepted baseline: `896eb2a8b4949f469e17934a36819013524d6793`.
- Exact T2 comparison ref used for the narrow B1 reconciliation:
  `a92acb3beb27a61bab90d7a3f5991875699ef3ba`, tree
  `ee8080e9d9dd52e65ba72cf59f81383e888cef34`.

Final candidate/artifact identities are recorded below and in
`candidate-dependencies.json`.

## B1 — native-v2 preservation

Implemented the smallest separate v2 adapter, without transplanting T2 or
prohibiting parent-v1:

- `astrid/packs/h3_av/src/request.py` retains `_normalize_v1` and dispatches
  version 2 lazily to `src/request_v2.py`; v1 source-free generation remains
  accepted.
- `astrid/packs/h3_av/src/request_v2.py` normalizes the media-list contract,
  rational frame/sample timing, references, masks, dialogue/guides, and the
  four required branches: `source_free`, `audio_only`,
  `extension_context`, and `source_backed_v2v`.
- `src/prepare.py` preserves v2 asset and mask bindings and branch metadata.
- `src/compile.py` adds the deterministic `_compile_native_v2` binding,
  `h3_av.native.v2` profile, managed asset bundle, main-0 selector, and the
  explicit CPU/GPU limitation. Parent-v1 compile paths are unchanged.
- Added `profiles/native-v2.yaml`, `schemas/request.v2.json`, and
  `tests/packs/h3_av/test_native_v2_contract.py`.

The v2 contract test exercises all four branches and the profile/schema asset
path. The same test retains parent-v1 source-free generation with 1/4/9
ordered still references and v1 edit/continue semantics. Existing affected
tests cover bounds/native timing, trim/separate-AV behavior, and the main-0
selector; no GPU qualification is claimed.

## B2 — final publication boundary

`astrid/packs/h3_av/src/receipt.py` now has a distinct
`final_composition_publication` state and
`attest_runtime_managed_composition`. The attestation accepts only a Runtime
`InvocationResult` from the `h3_av.publication_finalizer` capability and
binds:

- the finalizer run/task/attempt and project identity;
- `generation.publish_v1`, the H3 request digest, and the
  `verified_candidate` main selector;
- the final candidate SHA-256, generation/variant identity, managed-output
  association, and locally retrieved verified size/digest.

It rejects raw-vs-composed hash reuse, foreign/tampered associations, and
partial retrieval. `complete` is now impossible unless that final attestation
and cleanup both pass. Raw `vibecomfy.run` remains `raw_internal_lineage`;
the normal transform path passes no final attestation and therefore ends at
`candidate_verified` until the existing Runtime finalizer/readback provides
the sealed final evidence. No second publication or recovery authority was
added.

Focused regressions include raw/composed hash separation, same-identity retry
readback after a lost publication reply, foreign and tampered association,
partial retrieval, and cleanup-only recovery. The obsolete raw-success
assertion in `test_runtime_contract.py` now requires
`published_scope=raw_internal_lineage` and
`final_composition_publication=required`.

## B4 — actual dependency declarations

The Astrid candidate now points its VibeComfy engine constant and all six
VibeComfy executor requirement files at the isolated Vibe candidate. The
VibeComfy candidate now declares `runpod-lifecycle>=0.3.1.dev0,<0.4`, stages
the isolated RunPod candidate through `[tool.uv.sources]`, and has a refreshed
`uv.lock` directory source. Broad stale-revision search found no remaining
`b554ed14…`, `14d12f3c…`, or `0.3.0` declaration in the selected Astrid/Vibe
consumer path.

The hash-bound staging assertions are in both candidates' root
`candidate-dependencies.json` files. The selected artifacts are:

- VibeComfy corrected wheel:
  `/tmp/h3-vibe-correction-wheel/vibecomfy-2.8.0-py3-none-any.whl`, SHA-256
  `e589917a94ef2c988d63556c39e3542eee89f9389c980493fea6eb605973b352`.
- RunPod wheel:
  `/tmp/h3-clean-install.7gEnxZ/wheels/runpod_lifecycle-0.3.1.dev0-py3-none-any.whl`,
  SHA-256
  `c26ee1323b7be1c1b23d04a1a02a3af58b3429732347a166931cf68b1bb3bbd8`.
- The corrected Astrid wheel was built at
  `/tmp/h3-astrid-correction-wheel/astrid-0.1.0-py3-none-any.whl`, SHA-256
  `f97899573373ab59ce1c77cd20747ac5f861b2efdd1e96ac4c2746c6becf5a9d`.

`uv lock --check` passed in the Vibe candidate. A clean temporary install
containing the corrected Astrid/Vibe wheels and RunPod `0.3.1.dev0` imported
from `/private/tmp/h3-correction-install.y945gz/lib/python3.11/site-packages`;
the smoke check resolved the v2 source-free branch and imported
`runpod_lifecycle.config.RunPodConfig`. It used no sibling checkout or
editable install.

## Checks run

- `python3 -m pytest -q tests/packs/h3_av` — **130 passed, 1 warning**.
- Focused B1/parent/compile/timing/generation/operation/receipt/backend set —
  **122 passed, 1 warning**.
- `python3 -m pytest -q tests/packs/h3_av/test_runtime_contract.py` —
  **26 passed, 1 warning**.
- `python3 -m pytest -q tests/packs/h3_av/test_receipt.py` — **29 passed**.
- `python3 -m compileall -q astrid/packs/h3_av/src
  astrid/packs/h3_av/orchestrators/transform` — passed.
- `uv lock --check` in the VibeComfy candidate — passed.
- `uv build --wheel` for corrected VibeComfy and Astrid candidates — passed;
  wheel metadata reports RunPod floor `>=0.3.1.dev0,<0.4`.
- `git diff --check` and stale-revision search — passed.

## Honest B3 limitation

B3 remains open. This offline correction did not prove the consumed Runtime
owner source-release/distribution, actual direct-host composition topology,
private ACK/reply-loss witness, independent PID/birth/group/listener
re-observation, stale-fence denial, parent-plus-child capacity, device
exclusion, or provider/GPU qualification. The exact missing evidence remains
the D02/D08 bounded owner/direct-host composition witness described by Astra.
No B3 qualification, pre-live review pass, D14 promotion, or D15 paid/live
authorization is claimed.

## Final candidate commits

The final Astrid commit/tree and corrected Vibe commit/tree are recorded after
the findings file is committed; RunPod is unchanged at the reviewed candidate
identity above.
