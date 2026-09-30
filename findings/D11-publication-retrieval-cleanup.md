# D11 publication, retrieval and cleanup

Run: `h3-full-merge-megado-plan-20260930`  
Date: 2026-09-30  
Result: **PARTIAL — candidate contract checks pass; no concrete D11 candidate defect was found.**

## Scope and exact candidate identity

All D11 inspection and testing was restricted to:

`/Users/peteromalley/Documents/reigh-workspace/Astrid/.otto/worktrees/h3-full-merge-megado-plan-20260930/`

The candidate was clean before the receipt-only change.

| Item | Exact identity |
| --- | --- |
| Candidate branch | `otto/h3-full-merge-megado-plan-20260930` |
| Candidate commit tested | `963e9c60aadbababbbe0998859636e5b89c06078` |
| Candidate tree tested | `3952b229eba187636a61063b09f694e4212f9a5f` |
| D10 source parent | `12ae817ba135d0823a687a843386c950070bb497` |
| Selected Runtime source lineage | commit `a278cd460018976940ae21fc2cad563a29a29649`, tree `41817e38beb837a81ffc3f76bac7c0a756793bd0` |
| Runtime protocol/client | `workspace.v1`, one 126-operation client |
| Consumed immutable Runtime release | **Unknown; no artifact-bound owner release receipt was supplied** |

D02, D04, D06, D07, D08 and D10 receipts plus the Astra ruling were read. D04's
RunPod owner contract remains the source for exact-ID teardown and
`cleanup_pending`; no source main, donor, other candidate, external
orchestrator, provider or live state was changed during D11.

## Contract trace and disposition

No source or regression-test edit was necessary.

- `attest_runtime_managed_publication()` accepts only a successful
  `vibecomfy.run` `InvocationResult` whose Runtime settlement agrees on
  run/task/attempt identity, is terminal, admits `generation.publish_v1`, binds
  the H3 request digest and generation intent, and contains exactly one matching
  generation/variant/object plus a unique task-bound managed-output association.
  Raw publication is recorded as `raw_generation`; final composition
  publication remains deferred.
- H3 retrieval uses Runtime managed media only for the affected path. The
  muxed path requires one output; the separate-AV path requires exactly one
  video and one audio role. Materialization reads the managed object bytes and
  verifies the canonical SHA-256 digest and declared size. The generated bundle
  records role, digest and size, and the compose/verify path exercises decoded
  audiovisual coverage.
- The final receipt cannot become `complete` without both the Runtime raw
  publication attestation and exact cleanup. Missing/partial retrieval does
  not produce a success receipt. Cleanup requires equality with the admitted
  target, exact owned pod identity and matching expected/observed postconditions;
  preserved volumes are bound by exact identity. Duplicate, foreign or
  contradictory resources are rejected. Failed/uncertain resource verification
  remains `cleanup_pending`, and cleanup-only evidence cannot upgrade the
  operation to success.

The reviewed paths therefore preserve the requested partial/unknown truthfulness
and do not revoke a different pod or provider resource. No concrete candidate
defect was demonstrated, so there is no minimal source fix or focused
regression-test change in D11.

## Focused offline checks

Command, run from the candidate, with the existing Astrid virtualenv and no
external services:

```text
PYTHONDONTWRITEBYTECODE=1 /Users/peteromalley/Documents/reigh-workspace/Astrid/.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/packs/h3_av/test_receipt.py \
  tests/packs/h3_av/test_astra_runtime_fixes.py::test_lanpaint_retrieval_requires_and_preserves_video_audio_roles \
  tests/packs/h3_av/test_astra_runtime_fixes.py::test_paired_lanpaint_bundle_reaches_composition_with_both_streams \
  tests/packs/runpod/test_pack_executors.py::test_provision_persists_allocation_before_readiness_and_marks_cleanup_pending \
  tests/packs/runpod/test_pack_executors.py::test_provisional_handle_retains_cleanup_pending_until_reconciliation \
  tests/packs/runpod/test_pack_executors.py::test_teardown_terminates_and_writes_receipt
```

Result: **29 passed**, exit 0, 1.72 seconds. The run covered publication
identity/conflict and retrieval gates, missing/partial separate-AV roles,
local hash/size and decoded paired composition, exact cleanup target/resource
binding, owner cleanup-pending recovery, and teardown receipt behavior.

Additional checks: `git diff --check` passed; the candidate remained source-clean
before this receipt was added. No dependency installation, cache/provider/GPU
execution, credential use, live service, promotion, merge or push occurred.

## Limitations and non-claims

- D02 remains partial: the exact immutable Runtime release containing the
  selected source, generated client, schema, private interfaces and the tested
  bytes is not identified.
- D04/D05/D08 remain owner/topology gates. This D11 run does not prove a
  compatible immutable RunPod/Vibe artifact, direct-host independent
  observation, provider absence, physical device exclusion, GPU behavior or
  live cleanup.
- The tests are offline local/mocked evidence. They do not constitute a real
  Runtime publication, remote managed download, RunPod teardown, release or
  final-composition publication witness.
- No D12+, release/install, promotion, merge-to-main, live or final closeout
  success is claimed.

This receipt is the only D11 candidate change and is committed on the candidate
branch. The source candidate remains at the exact tested identity above.
