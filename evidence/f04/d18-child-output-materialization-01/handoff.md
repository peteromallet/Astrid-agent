# F04 D18 child-output materialization SDK handoff

Status: implementation and focused SDK proof complete; source reservation released to the coordinator. F05 may consume this exact protocol after coordinator audit. Worktree: `/Users/peteromalley/Documents/reigh-workspace/Astrid/.otto/worktrees/pack-authoring-convergence-20261001`. HEAD: `9be144c4f2f63216e693a1a13e42cd79d3845845`.

## Public API

`InvocationResult.materialize_output(association_id: str) -> astrid.sdk.MaterializedChildOutput` is available only on the original successful awaited bridge-child result. Private binding captures that result object, the original bridge, generated or explicit child key, run/task/attempt identity and a copied verified output page. Copying/reconstructing a public result does not acquire the binding. Failed, non-child and non-awaited results reject materialization. Missing, duplicate, mutated or foreign descriptors/associations reject before transmission. The caller supplies neither a task/attempt/key nor a destination.

The typed return has `output` (read-only mapping containing the original descriptor), `filename` (actual host-selected parent-relative filename), `to_dict()` and `producer_file()`. The latter returns exactly `filename`, `media_type`, `output_port` for the existing subsequent-child producer-file registration route. The child descriptor's original filename remains unchanged inside `output`. `InvocationResult.to_dict()` and repr omit all private state. No callback, socket or bridge key is serialized as an authority field.

```python
child = astrid.invoke("pack.action", kind="action", inputs={...}, wait=True)
local = child.materialize_output(child.outputs["managed_outputs"][0]["association_id"])
next_child = astrid.invoke("pack.consume", kind="action", inputs={"clip": local.producer_file()})
```

Each repeat performs a fresh host request; there is no SDK cache, retry or fallback. The socket fixtures prove repeated requests and equal return values for an idempotent host response. F05 must implement authoritative revalidation and stable destination selection for repeated requests.

## Exact private wire handoff to F05

Request envelope, with no additional fields:

```json
{"v":1,"request_id":4,"op":"materialize_output","child_key":"original-key","task_id":"T-original","association_id":"association-1"}
```

Success envelope, with no additional fields:

```json
{"v":1,"request_id":4,"ok":true,"data":{"output":{"association_id":"association-1","run_id":"R-original","task_id":"T-original","attempt_id":"A-original","object_id":"sha256:<64 lowercase hex>","digest":"sha256:<same 64 lowercase hex>","size":123,"filename":"child/audio.wav","media_type":"audio/wav","output_port":"audio","ordinal":0},"filename":"child-outputs/association-1/audio.wav"}}
```

`output` must equal the originally verified descriptor exactly, including run/task/attempt and association. Its exact 11 fields are `association_id`, `run_id`, `task_id`, `attempt_id`, `object_id`, `digest`, `size`, `filename`, `media_type`, `output_port`, `ordinal`. IDs and child keys use bounded ASCII `[A-Za-z0-9][A-Za-z0-9._~-]{0,127}`. Digests are exact lowercase SHA256; object_id equals digest; size is an integer in [0, 64 MiB]; ordinal is a nonnegative integer. Booleans do not count as integers. Filenames must be contained relative POSIX paths without dot-only, traversal, backslashes or control characters. Media type and output port are nonempty strings without control characters. Pages have at most 256 unique association IDs. The host-selected `data.filename` is validated by the same relative-filename rule.

SDK also validates the original `outputs` page against the task, run and completed attempt before binding. Invalid pages permanently close the bridge and become failed invocation results through existing readback normalization. Invalid materialization success shapes/descriptors/filenames, extra authority/media-byte fields, duplicate JSON fields and malformed/correlation/type/frame envelopes permanently close the channel and raise `CapabilityInvocationError`. The existing 1 MiB frame bound is unchanged. Explicit valid host rejection keeps the channel open and returns no materialized result; error wire shape remains `code`, `message`, `details`.

## Focused evidence

- `focused-tests-02.json`, `.stdout.txt`, `.stderr.txt`: exact reserved module command, **316 passed in 5.00s**. Socketpair fixtures only; no byte custody or local file creation claim.
- `focused-compile-02.*`: all five reserved sources compile without bytecode writes, exit 0.
- `focused-lint-02.*` and `baseline-lint*`: 27 existing Ruff findings before and after; no new finding. Full-file advisory exit 1 remains accurately recorded.
- `focused-type-01.*` and `baseline-type*`: 14 existing mypy errors before and after, all in invocation.py; outputs equal after only source line-number normalization. Full-file advisory exit 1 remains accurately recorded.
- `baseline-comparison.json`: exact bounded lint/type comparison.
- `scoped-checks.json`: scoped diff whitespace check and reverse incremental patch check pass (exit 0); each exact baseline/source no-index whitespace check has exit 1 for differences and empty stdout/stderr.
- `before/**`, `incremental-source.patch`, `worker-start.json`, `validation-manifest.json`, `reservation-release.json`: baseline bytes, exact patch, source/evidence hashes and release.

The preflight HEAD, candidate-manifest hash, candidate-verification hash and all five reserved source hashes matched before editing. The coordinator's 1,501-record candidate verification is inherited; this worker specifically checked the hash-bound manifest/verification files and the reserved sources. No assertion is made about other concurrent owners' source preservation.

Only the five reserved SDK/test files changed. F05 host paths and tests, tasklist/status/control artifacts, Runtime/provider/network/GPU/user projects were untouched. No install, staging, commit, merge, push, publication or deploy occurred. Authenticated readback, byte custody, confined atomic file creation, lifecycle/budget enforcement and production composition remain F05/coordinator proof.
