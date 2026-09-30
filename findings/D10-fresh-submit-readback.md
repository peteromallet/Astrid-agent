# D10 fresh submit and authoritative readback

Run: `h3-full-merge-megado-plan-20260930`  
Date: 2026-09-30  
Route: normal → GPT-5.6 Luna/high  
Result: **PARTIAL — offline candidate path validated after a focused repair; immutable Runtime/host and live evidence remain unavailable.**

## Candidate identity and custody

All source and test work was restricted to:

`/Users/peteromalley/Documents/reigh-workspace/Astrid/.otto/worktrees/h3-full-merge-megado-plan-20260930/`

| Item | Exact identity |
| --- | --- |
| Candidate branch | `otto/h3-full-merge-megado-plan-20260930` |
| D07 parent | `08040853b0e7ed808d0f1bd1bad3aef74180134c`, tree `28ec84ce08f35f391304c7931a388241ae42386e` |
| D10 candidate commit | `12ae817ba135d0823a687a843386c950070bb497` |
| D10 candidate tree | `37679eef3846dbaca28c857403326d6c3f3b5b7e` |
| Final status before this finding | clean; the finding is the only post-commit candidate file |
| Selected Runtime lineage | `workspace.v1`, one 126-operation client; owner source `a278cd460018976940ae21fc2cad563a29a29649`, tree `41817e38beb837a81ffc3f76bac7c0a756793bd0` |
| Immutable consumed Runtime release | **not identified**; source commit/tree is not treated as a release artifact |

Changed source/test file SHA-256 values at the D10 commit:

| File | SHA-256 |
| --- | --- |
| `astrid/packs/h3_av/src/operation.py` | `61ca074d1276938a82aba6f700fec8d1092de4d06f0732c862ce322c0ff23e2d` |
| `astrid/packs/h3_av/orchestrators/transform/run.py` | `0475d012f893da08b7e58b25b3aabdc929b4dbec2b3012289d7374a073a26d12` |
| `astrid/sdk/invocation.py` | `60875349b7c8302463593c0aa797b4b860fd970da7f0c33bec07ef9e28045e48` |
| `tests/packs/h3_av/test_operation.py` | `bbde632e7d77f6812bdc0db90115e75e7caeda38abe65a3526a2f6d2b3f42c90` |
| `tests/stage1/test_invocation_runtime_cutover.py` | `6624e0ccff611615ab73dc0f4791415bc4792a39f4037c4ba524b8f342ebdd30` |

No source main, donor/owner worktree, external Runtime/Worker/orchestrator
repository, provider, GPU, credential, shared installation, promotion, merge or
push was performed.

## Admission and readback trace

The H3 parent opens the existing Runtime client with `start_pack_host=False` and
invokes the child sequence:

`h3_av.prepare` → `h3_av.compile` → `vibecomfy.validate` →
`vibecomfy.run` → `h3_av.compose` → `h3_av.verify`.

Each actual child admission reaches `sdk.invoke` → `_kernel_invoke` →
`RemoteTasks.create`. Runtime derives the idempotency key from the completed
normalized admission payload, including the H3 authority context, and returns
the task/run/attempt identity. The sole Runtime client remains the task,
attempt, settlement and managed-output authority; no local task ledger was
added.

The existing D07 `_reobserve_saved_result()` helper remains the only resume
mechanism. It uses the saved task/run/attempt tuple only as a locator, then:

1. reads the same task through Runtime;
2. requires the returned task, run and attempt identities to match;
3. requires terminal `succeeded`/`completed` state and a typed settlement;
4. reads that same task's typed managed-output page; and
5. reconstructs the invocation result from those Runtime values.

Unsettled, missing, mismatched or unreadable settlement remains an explicit
failure/unknown and does not submit a replacement or resample.

## Demonstrated D10 defect and focused repair

Before the repair, two new H3 admissions with identical inputs produced the
same deterministic idempotency key and the same Runtime task/run/attempt
identity. That made a fresh submit indistinguishable from replay.

The candidate repair:

- gives each new `OperationJournal` a fresh persisted submission nonce;
- carries that nonce as H3 `idempotency_context`, which is part of the admitted
  Runtime authority context and therefore the stable idempotency-key material;
- reuses the same nonce for all stages and for same-operation resume;
- records stage task/run/attempt identity in the existing journal; and
- rejects a saved result that has no matching journal admission or whose
  identity disagrees with that admission, before Runtime readback or replay.

This preserves D07's same-identity re-observation and does not create a second
recovery framework or publication mapping. A same-operation retry keeps the
same key and identity; a fresh operation gets a distinct key and fresh Runtime
lineage.

## Checks

All commands used the existing Astrid environment at
`/Users/peteromalley/Documents/reigh-workspace/Astrid/.venv/bin/python`, with
`PYTHONDONTWRITEBYTECODE=1` and pytest cache disabled. No dependency was
installed or resolved.

| Command / scope | Result |
| --- | --- |
| `tests/packs/h3_av/test_operation.py tests/stage1/test_invocation_runtime_cutover.py` | **31 passed** |
| `tests/packs/h3_av/test_receipt.py tests/packs/h3_av/test_runtime_contract.py tests/packs/h3_av/test_astra_runtime_fixes.py tests/sdk/test_execution_request_binding.py` | **93 passed, 1 existing PendingDeprecationWarning** |
| Host/activation/managed-runtime boundary bundle: `tests/core/execution/test_generic_host_activation.py`, `test_generic_host_contract.py`, `test_managed_tool_session.py`, `test_remote_activation_owner.py`, `tests/core/test_generic_host_attempt_isolation.py`, `test_managed_runtime_boundaries.py`, `tests/test_generic_host_vibecomfy_lifecycle.py` | **67 passed** |
| `py_compile` on all 5 changed source/test files | **PASS** |
| `git diff --check` | **PASS** |
| Offline Runtime admission mock | Same operation: same key and identity; fresh submission context: different key and `run-2/task-2/attempt-2` |

An adjacent SDK invocation bundle was also attempted; its D10-relevant
execution-request tests passed, while two pre-existing timeline-filmstrip
fixture cases failed before admission because their `SimpleNamespace` fixture
omits `capability_type`. That unchanged fixture failure was not repaired or
counted as a D10 result.

Ruff was also run on the changed files. It reported six existing findings in
`astrid/sdk/invocation.py` (import ordering, an unused import, and a broad
exception catch); no unrelated lint cleanup was made in this D10 change.

## Limitations and ownership result

D10 is **PARTIAL**, not a live or release pass. D02 remains PARTIAL because an
immutable Runtime release bundle or exact artifact-bound owner qualification
receipt was not located. D08 remains PARTIAL because actual direct-host
topology, independent process observation, parent/child capacity and remote
cleanup evidence are unavailable. The selected `a278cd46` Runtime lineage is
retained as the sole owner lineage by the candidate and prior receipts; no
second client, task ledger, publication authority or historical-ID replay path
was introduced.

The offline checks do not prove an installed Runtime release, actual provider
or GPU execution, credentials, live settlement, final publication/retrieval/
cleanup, D11+, release, promotion, merge-to-main or push success. Those remain
downstream and explicitly pending.
