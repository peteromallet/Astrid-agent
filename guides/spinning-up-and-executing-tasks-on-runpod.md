# Spinning up and executing tasks on RunPod

## Choose the canonical path

For the prepared H3 RTX 5090 target, the operator flow is:

1. claim capacity with `scripts/claim_runpod_5090_backup.py` and its durable
   canonical handle;
2. optionally watch that local evidence with `scripts/watch_h3_claim_flow.py`;
3. write an execution request bound to the returned pod and provider account;
4. submit that request through the `h3_av.transform` orchestrator for the
   intended Astrid project; and
5. retrieve and verify the managed result while honoring the declared
   `leave_running` lifecycle.

This is the canonical H3 path. Do not substitute generic `runpod.provision`,
`runpod.session`, or `runpod.exec`, and do not execute the H3 workload with a
direct `runpod-lifecycle run`. Those interfaces do not own the H3
prepare/compile/validate/run/compose/verify sequence or its managed result and
final receipt.

`h3_av.transform` prepares the request, submits the Astrid child tasks,
composes and verifies the candidate, and writes the final lifecycle receipt.
The standalone lifecycle material later in this guide is retained only for
bounded transport diagnosis; it is not an alternative H3 execution recipe.

### Transport diagnostic only

For “use this pod, upload a job, run it, collect results, keep the pod”, use
`runpod-lifecycle run POD_ID --keep-pod`. This path was live-tested on the
existing storage-backed RTX 5090 on 2026-09-21. It does not need the Astrid
workspace runtime or an Astrid generic worker installed on the pod.

Astrid's `runpod.exec` wraps the same library runner, adding Astrid admission,
project/task receipts, artifact handling and cost records when invoked through
a working Astrid runtime. Its local executor controls the remote workload over
SSH.

For a generation that must become an Astrid task and Generation, use the
canonical task path below. A pod being reachable or a VibeComfy file existing
on the pod is not success. The path requires task settlement, a managed output
association, a Generation record, and a verified local download, then records
technical verification, editorial approval, and exact cleanup separately in the
final receipt.

## 1. Resolve the exact pod and existing storage

Read-only inventory:

```bash
runpod-lifecycle volumes ls --json
runpod-lifecycle list --json
```

Use the shared credential setup in [RunPod credentials](../docs/reference/runpod-credentials.md).
If the credential wrapper is installed:

```bash
astrid-with-credential --provider runpod -- runpod-lifecycle list --json
```

From this Astrid checkout, the equivalent wrapper is:

```bash
.venv/bin/python -m astrid.core.util.credential_exec \
  --provider runpod -- runpod-lifecycle list --json
```

The child lifecycle executable must be installed and its RunPod SSH identity
configured. Astrid's virtualenv and the lifecycle CLI may use different Python
environments; a working CLI does not establish Astrid executor dependencies.

Compare the pod's provider-reported networkVolumeId with the resolved volume ID.
The current normalized status omits that field, and list can return null when
the upstream SDK omits it. Null is not proof of absent storage: use an
authenticated provider GET /v1/pods/<pod-id>, printing only the needed fields.
Never print credentials or raw status containing SSH passwords.

For the reviewed pod, direct provider observation confirmed:
pod `8f18jbuh81vko9`, volume `sfak8553dy` (`backup`, 250 GB),
mount path `/workspace`, container disk 200 GB. SSH independently confirmed
`/workspace` as a mounted filesystem. Recheck these facts before later jobs.

`get_pod(pod_id, config)` resolves an existing pod; it does not attach or replace
a volume. Passing storage_name while reusing a pod does not enforce an attachment.
A mismatch must fail before upload. Never “repair” it by silently creating a pod.

### Wait for the prepared 5090 capacity

Run the canonical claim from the repository root exactly as follows:

```bash
python3 scripts/claim_runpod_5090_backup.py --handle-path .otto/runs/h3-av-simplicity-20260924-T2/runpod/claim-handle.json --max-wait-seconds 43200 --poll-seconds 120
```

The 43,200-second bound is 12 hours. Capacity polling is 120 seconds by
default; the explicit flag keeps the operator command self-describing. A
5-second interval can be explicitly requested for a short, deliberate watch,
but it generates substantially more provider API traffic and is not the
normal setting.

The claim helper uses the existing `backup` network volume and discovers its
current size. It requests `attach_only=True` with `disk_size_gb=0`, so it does
not create, replace, or resize that persistent storage. The 200 GB value is
the disposable container disk only. It pins the prepared CUDA-13 image, allows
CUDA 13.0, uses no generic provider template, waits for readiness and SSH, and
verifies the mounted H3 release venv with a real Torch/CUDA initialization.
It does not call generic `runpod.provision` and does not submit an H3 task.

Before the provider request, the helper exclusively creates a secret-free
allocation-attempt marker at the handle path. A successful claim atomically
replaces that marker with an `astrid.runpod.claim.v1` handle. Allocation alone
is not success: if readiness, SSH discovery, or release preflight fails after
a pod is allocated, the handle becomes `allocated_unverified`, the exact pod
is left running, and the command fails. It does not terminate that pod, retry
with a replacement, or silently relinquish custody.

The handle path is an exclusive custody boundary. Active claims and
pending/unknown allocation handles fail closed before a new provider launch.
A stale terminal handle can roll over only when a local JSON cleanup receipt
matches the complete prior handle digest, exact pod ID, exact backup volume
ID, `cleanup_status: "complete"`, a terminal status of `terminated`,
`already_gone`, or `absent`, and `backup_volume_preserved: true`. The old bytes
are archived before the new allocation marker is written. Missing, malformed,
mismatched, or incomplete evidence never authorizes rollover.

After a successful claim, use the returned pod identity for the canonical
execution request and H3 orchestrator setup below. Do not submit H3 work
directly with `runpod-lifecycle run`; that command remains a transport
diagnostic and does not create the managed task/Generation lifecycle.

## 2. Transport diagnostic: stage and execute a bounded job

Put only this job's script, workflow and required input files in a dedicated
local directory. The CLI uploads the script's parent directory recursively.
Do not put the script at a repository root or beside credentials, large caches,
or previous downloaded artifacts.

```bash
runpod-lifecycle run 8f18jbuh81vko9 \
  --script /absolute/path/job/run.sh \
  --remote-root /workspace/unique-job-id \
  --upload-mode sftp_walk \
  --timeout 900 \
  --keep-pod \
  --json
```

Use a fresh simple remote path with no spaces or shell metacharacters. For
sftp_walk, its parent must already exist; /workspace/<unique-job-id> works.
Do not use tarball upload against /workspace, a model directory, or any shared
release: the current tarball implementation removes and replaces remote_root.

The script is the workload entry point, not an attach/worker-registration wrapper.
For a prepared Comfy environment it can run the existing VibeComfy workflow.
Validate the workflow, inputs, models, nodes and environment before inference.

Equivalent existing library API:

```python
pod = await get_pod(pod_id, config)
await pod.wait_ready(timeout=60)
result = await ship_and_run_detached(
    pod=pod,
    remote_script=script_text,
    local_root=job_directory,
    remote_root="/workspace/unique-job-id",
    timeout=900,
    terminate_after_exec=False,
)
```

Current operational limits:

- The CLI defaults to termination unless --keep-pod is supplied. Astrid
  runpod.exec passes terminate_after_exec=False internally.
- timeout bounds the runner's polling phase, not all upload/download time.
  On timeout it returns 124 without stopping the remote job in keep-pod mode.
  Put a tested process-level deadline in the workload, e.g. GNU
  `timeout --kill-after=5s 840s <command>`. A client timeout does not cancel
  work queued into an independently running Comfy server; that requires
  cancellation of the owned prompt/session and verification.
- Only one detached lifecycle job may run on a pod at a time: script, log,
  exit-marker and artifact archive names under /tmp are currently shared.
- Detached inference can outlive the caller. There is no durable task-manager
  recovery or deadline guarantee in this standalone command.
- --json currently prints progress lines before its final JSON summary. Do not
  feed all stdout directly into json.loads. Preserve the log and parse the
  final object, or use the typed library return value.

## 3. Transport diagnostic: collect and verify results

The detached runner collects only remote_root/out and remote_root/output into
the local script directory's artifacts/ folder. Arrange for the workload to
put intended outputs there; for an external Comfy server, explicitly copy its
owned prompt outputs there. Use the existing fetch/pull tools for other paths.

Require all of: returncode=0, terminated=false, expected nonempty downloaded
files, matching hashes, and full decode/frame checks for media. Download failure
can currently leave the remote exit code at zero; no error code alone proves
delivery. The detached result's stdout/stderr are not a complete workload log;
write relevant workload logs into out/ as well.

Standalone downloaded files are not Astrid managed objects and create no Astrid
task history. If project custody is required, import selected results through
`python -m astrid media import <file> --project <project> --json` on a working
runtime. That records media ingestion; it does not retroactively establish a
managed generation task or a scheduler execution binding.

## 3a. Historical continuation transport test

For an H3 continuation test, use
[Anchor-to-anchor video generation](anchor-to-anchor-video-generation.md) for
the graph and context rules, and use this lifecycle path for transport and
pod custody. The continuation test is a workload script submitted to an
existing pod; it is not a new worker-registration path and it must not call
the old watcher that provisions or tears down pods.

Use the following bounded sequence:

1. Start with a one-segment, one-step capacity smoke using the production
   graph and the longest admitted shape (`1920x1088`, `260` native frames).
   This checks model loading, node resolution, backend selection and memory
   without spending the time required for a creative take.
2. If that passes, run the reviewed starter/continuation prefix: a `175`-frame
   starter followed by a `260`-frame continuation. Keep the same ComfyUI
   server and workflow graph, and pass the generated AV latent edge directly
   into the continuation with its `39` native context frames.
3. Download both raw outputs into the job's `artifacts/` directory. Verify
   dimensions, frame counts, decodability, hashes and the continuation seam.
   Do not decode an MP4 and feed it back as continuation context.

The existing storage-backed pod and release currently used for this test are:

```text
pod:     8f18jbuh81vko9
storage: backup / sfak8553dy mounted at /workspace
release: /workspace/h3-golden/releases/h3-cu130-v1-candidate
bundle:  /workspace/astrid-continuity-20260918
```

Historical scripts in the continuity bundle use `/opt/astrid-continuity-20260918`;
that path is not present on the current pod. Adapt them to the mounted
`/workspace/astrid-continuity-20260918` path inside the submitted job instead
of running them unchanged. In particular, do not treat an old result or PID
file on the persistent volume as evidence that the current server is alive.

When diagnosing transport rather than running a canonical H3 task, submit the
bounded workload through the standalone lifecycle runner and leave the existing
pod running:

```bash
runpod-lifecycle run 8f18jbuh81vko9 \
  --script /absolute/path/to/continuation-job/run.sh \
  --remote-root /workspace/jobs/h3-continuation-<utc-stamp> \
  --upload-mode sftp_walk \
  --timeout 900 \
  --keep-pod \
  --json
```

The job script should start or reuse only its owned loopback ComfyUI process,
wait for `/system_stats` or `/object_info`, run VibeComfy against that server,
write logs and a result manifest under `out/`, and copy intended media under
`output/`. Put a process-level deadline inside the script as well as the
runner timeout. A passing transport result is not an H3 result: the release
must still pass the CUDA/Torch, custom-node, model, backend and capacity gates
in [RunPod lifecycle](runpod-lifecycle.md). At the time of this review, the
candidate venv was missing Torch while the provider Python exposed
`torch==2.4.1+cu124`; that mismatch is a fail-closed preflight finding, not a
successful continuation run.

## 4. Canonical Astrid task path on a prepared RunPod pod

### Provider launch is not Astrid task admission

Use the claim command from section 1. The exact current operation is:

```bash
python3 scripts/claim_runpod_5090_backup.py --handle-path .otto/runs/h3-av-simplicity-20260924-T2/runpod/claim-handle.json --max-wait-seconds 43200 --poll-seconds 120
```

On success, read the exact `pod_id` from that handle. The claim is
`pod_verified`; it is not worker readiness, task admission, sampling, managed
delivery, or editorial approval. The helper's successful and
`allocated_unverified` outcomes both preserve the exact allocated pod under
`lifecycle.mode=leave_running`. It never turns this handle into a generic
`runpod.exec` provision handle.

### Deployment qualification owns the boundary

The supported target composition is now:

```text
canonical claim helper and durable claim handle
  -> execution request binds exact pod/account and leave_running policy
  -> stage + verify release/models; prepare Comfy and parked GenericHost
  -> real child attachment + exact VibeComfy CPU validation
  -> activate qualified worker; canonical Astrid task admission
  -> h3_av.transform children -> managed settlement/pullback -> final receipt
```

Before claimable readiness, it must independently verify pod/account placement,
issue scoped placement authority, bind the exact claim selector, attest staged
source/dependencies, and derive one runtime instance/epoch/schema/realm and
canonical support root. Launch the host in its own process session, validate real
PID/birth/PGID/argv, and publish matching state/ready/handoff generations. H3's
canonical capacity is two lanes: `max_concurrency=2` and
`astrid-pack-host-orchestration-executor-v1` (one orchestration slot and one
executor slot), not two concurrent GPU samplers. A generic token, ready marker,
capability row or successful Torch probe alone is insufficient.

Generate the managed Comfy session, validator/compiler child configuration and
effective `extra_model_paths` from one release model inventory. For this release,
the canonical root is
`/workspace/h3-golden/releases/h3-cu130-v1-candidate/models`, not the empty
`runtime/ComfyUI/models` default. Pass the verified root explicitly to production
validation/compilation; merely setting an Astrid-prefixed variable is not proof
that the downstream validator consumes it. Verify the exact workflow's full
model closure, per-file hashes, aliases/search precedence, custom nodes and live
schemas in the real child before activation. No GPU prompt is submitted by
qualification.

Do not restore the earlier manual serial-host/token-swap launch instructions.
`watch_h3_5090_storage.py` is not an alternative launcher for this procedure;
the local-only watcher below is the supported observation tool.

### Read-only claim-to-H3 watch

Use `scripts/watch_h3_claim_flow.py` when an operator only needs progress from
the local canonical evidence. It reads the claim handle, optional execution
request, numbered H3 output stages, final receipt, and cleanup state. It does
not call RunPod, allocate or terminate a pod, submit an H3 task, or alter any
provider/runtime state. Missing local evidence is reported as
`allocation_pending/unknown` or `pending/unknown`; it is not upgraded to a
provider claim.

From the repository root, use the exact canonical run directory and normal
120-second interval:

```bash
.venv/bin/python scripts/watch_h3_claim_flow.py .otto/runs/h3-av-simplicity-20260924-T2 --poll-seconds 120
```

For one non-blocking snapshot, use:

```bash
.venv/bin/python scripts/watch_h3_claim_flow.py .otto/runs/h3-av-simplicity-20260924-T2 --poll-seconds 120 --once
```

The watcher prints only changed local observations, in this order:

1. `allocation`: missing local custody is `allocation_pending/unknown`; a
   marker can be `allocation_pending` or `allocation_unknown`; a completed
   handle is `claimed`.
2. `pod`: the exact pod ID and local readiness evidence. A valid final claim
   implies the claim helper's readiness/release preflight passed. Exact
   terminal cleanup evidence takes precedence over stale historical readiness.
3. `execution_request`: missing, invalid, present, or `target_mismatch`, plus
   the target pod ID, request status, and lifecycle mode.
4. `h3_receipt`: `prepare`, `compile`, `validate`, `run`, `compose`, `verify`,
   and `final`, derived only from matching local JSON artifacts.
5. `cleanup`: an exact cleanup receipt if one exists; otherwise
   `leave_running` when that is the request policy, or `pending/unknown`.

The watcher does not call RunPod, allocate, retry, terminate, submit a task, or
mutate Runtime state. Missing local evidence remains missing or unknown; the
watcher never upgrades it through an implicit provider lookup. Its own poll
interval is local filesystem polling and does not change the claim helper's
provider polling.

### Bind the claimed pod in the execution request

After the claim succeeds, write
`.otto/runs/h3-av-simplicity-20260924-T2/execution-request.json`. Replace
`<returned-pod-id>` with the exact `pod_id` in the successful claim handle and
use the provider account reference returned by the configured/qualified
target. The current canonical example uses `runpod`:

```json
{
  "target": {
    "kind": "runpod",
    "pod_id": "<returned-pod-id>",
    "provider_account_ref": "runpod"
  },
  "lifecycle": {
    "mode": "leave_running"
  },
  "limits": {
    "max_queue_seconds": 1800,
    "max_runtime_seconds": 3600
  }
}
```

Do not copy a pod ID from an older example or provider inventory. The execution
request must match the newly returned claim and qualified account exactly. The
watcher reports a differing target as `target_mismatch`; it does not repair it.
The queue and runtime limits bound canonical task execution. They are not
authorization to terminate the pod and are not a provider capacity-wait limit.

### Submit through the H3 orchestrator

The canonical SDK dispatch launches the H3 orchestrator with the request,
asset map, output root, execution request, and project. For the current Matrix
Minkhole example, its runner-owned command resolves to:

```bash
.venv/bin/python -m astrid.packs.h3_av.orchestrators.transform.run \
  --request runs/matrix-minkhole/h3-canonical-submit-20260924/request.json \
  --asset-map runs/matrix-minkhole/h3-canonical-submit-20260924/asset-map.json \
  --out .otto/runs/h3-av-simplicity-20260924-T2/receipts/h3-transform \
  --execution-request .otto/runs/h3-av-simplicity-20260924-T2/execution-request.json \
  --project matrix-minkhole
```

That module command is runner-owned and guarded against ad-hoc direct use. The
operator submits the same values through the public SDK so Astrid creates that
command in the canonical invocation context:

```python
from pathlib import Path

import astrid.sdk as sdk

result = sdk.invoke_result(
    "h3_av.transform",
    kind="orchestrator",
    project="matrix-minkhole",
    inputs={
        "request": Path("runs/matrix-minkhole/h3-canonical-submit-20260924/request.json"),
        "asset_map": Path("runs/matrix-minkhole/h3-canonical-submit-20260924/asset-map.json"),
        "execution_request": Path(
            ".otto/runs/h3-av-simplicity-20260924-T2/execution-request.json"
        ),
    },
    out=Path(
        ".otto/runs/h3-av-simplicity-20260924-T2/receipts/h3-transform"
    ),
)
if not result.ok:
    raise RuntimeError(result.error)
```

The orchestrator owns `request -> prepare -> compile -> validate -> run ->
compose -> verify -> final receipt`. Only its canonical `vibecomfy.run` child
receives the execution target. Do not hand-submit a second VibeComfy task for
the same request.

### Leave-running and later cleanup

`leave_running` is exact: neither the claim helper nor successful H3 settlement
terminates the pod. An allocated pod whose readiness/SSH/release preflight
fails is also left running and recorded as `allocated_unverified`; reconcile
that exact pod before any retry. A queue/runtime timeout does not change this
postcondition.

If a separately authorized owner later terminates the pod, cleanup must target
the exact claimed `pod_id`, never a GPU type or volume. Preserve the `backup`
network volume. The cleanup receipt must bind the complete claim-handle digest,
repeat the exact pod ID at top level and in `cleanup`, record
`cleanup_status: "complete"`, record `terminated`, `already_gone`, or `absent`,
repeat the exact backup volume ID, and set
`backup_volume_preserved: true`. Until that evidence exists, the watcher
reports `leave_running` and the claim handle cannot roll over.

### Existing-task recovery is not a fresh invocation

For H3 task `5b908bceb1564eef9b8197c9b8cfdbd9`, preserve task/run/inputs and use
canonical eligible retry only after qualification. The original exact pod target
cannot silently change: if that pod is destroyed, first implement and authorize
the plan's explicit same-task placement-recovery operation. Current retry alone
does not relocate it. Until that prerequisite exists, stop before paid allocation.
Do not create a replacement task.

The `h3_av.transform` example above describes an ordinary new invocation,
**not recovery of this existing H3 task**. A runbook cannot substitute for
Runtime enforcing the preclaim qualification barrier.

### Mandatory invocation preflight

Code deployment and task asset transfer are separate. Managed executor file
ports must carry Runtime object descriptors, not paths from the submitting
machine. Import files with `client.media.import_file(...)` before invoking an
executor. The SDK rejects unresolved paths before task creation, and the host
rechecks before launching a child. JSON dependencies must be explicitly packaged;
the host does not search arbitrary JSON strings for paths.

`h3_av.transform` performs this packaging automatically: it uploads the normalized
request, verified dependency bundle (source, references and masks), and frozen
source. Prepare and compile materialize the bundle independently. Compose settles
candidate media as its own output and verify consumes it as a managed input.
An orchestrator dry-run is only a dispatch preview. Before a GPU retry after a
transport change, run `pytest tests/packs/h3_av/test_runtime_contract.py` from
the Astrid environment; its CPU regression removes earlier attempt directories
and tests native continuation and separate video/audio composition handoffs.

There is one more gate between live capability readiness and orchestrator
admission. For the canonical VibeComfy child, the task must carry the complete
sibling bundle and filename-bearing managed asset descriptors. Astrid's SDK
runs the shared CPU-only invocation preflight at this boundary and fails closed
before task admission when the configured VibeComfy environment is unavailable
or the invocation is not executable.

Set the admission environment so the Astrid virtualenv runs with the VibeComfy
checkout explicitly importable. VibeComfy is a sibling checkout in the normal
development layout, so relying on whichever `python3` happens to be first on
`PATH` can produce a false preflight failure (`No module named vibecomfy`) before
the task is even admitted:

```bash
export ASTRID_PYTHON=/absolute/path/to/Astrid/.venv/bin/python
export VIBECOMFY_CHECKOUT=/absolute/path/to/vibecomfy
export PYTHONPATH="$VIBECOMFY_CHECKOUT${PYTHONPATH:+:$PYTHONPATH}"
export VIBECOMFY_HEADLESS=1
```

Run the public `h3_av.transform` SDK invocation from the preceding section in
this shell. It must fail before admission if that import check cannot pass; do not
work around it by submitting directly to ComfyUI or by omitting the canonical
task route. Treat the import environment as part of the local admission
preflight and record it with the task receipt.

The preflight validates the exact Python/companion/source digests, loads the
pair, compiles the task-bound public inputs, checks the active prompt, ignores
disconnected helper branches, rejects reachable blank media fields, and checks
the H3 source audiovisual timing contract. The `source_video` descriptor must
retain its real safe filename (including `.mp4` or `.mov`); the logical port
name is not a media filename. Do not submit only a digest list and expect the
worker to infer these names.

The worker repeats the same checks after materialization, using the actual
staged basename and bytes, before model/session warm-up or Comfy queue
submission. This second check is required because collision-safe staging can
change a basename and because raw Runtime callers can bypass the SDK helper.
The receipt is bound to the bundle and asset digests, filenames, run inputs,
prompt, and measured source media; it is evidence of preparation, not proof of
successful generation.

There is a separate delivery contract after the GPU run. A qualified
checkout-server profile must include the manager-owned Comfy `output_directory`
and the worker must bind that exact directory into the external runtime before
warm-up. Conflicting workflow or `VIBECOMFY_COMFY_CONFIGURATION` output roots
are rejected. After Comfy reports success, the worker resolves the declared
descriptor under that root, verifies the actual media and its decode, downloads
it into private Astrid custody, checks the private copy against the producer
bytes, and only then settles success. A Comfy prompt id, `/view` URL, or
decodable file on the pod is not by itself a managed output.

The standalone `vibecomfy.validate` task is an offline structural check. Canonical
bundles require `python_execution_consent="confirmed"` and use the audited
`vibecomfy validate --json --no-schema` path. Its report labels the scope
`canonical_bundle_structural` and explicitly defers runtime/session and schema
validation to `vibecomfy.run`. Success does not establish runtime readiness.
It must not launch or stop Comfy, nor infer ownership from an occupied port.
The run adapter owns session attestation and compilation against fresh schemas
from the attested target before queueing.

These are three distinct validation boundaries:

1. **Admission:** validate the canonical bundle, staged asset descriptors,
   source audiovisual timing, and delivery-readiness contract before accepting
   the task.
2. **Worker pre-GPU:** repeat the checks against the bytes staged on the pod,
   revalidate the owned session and effective output root, then warm the model
   and queue Comfy.
3. **Post-generation:** resolve, decode, custody, hash-check, and settle the
   final media. If delivery fails after generation, keep the attempt failed or
   incomplete; never retroactively mark it successful from a Comfy log alone.

Create the work through `h3_av.transform`, rather than calling ComfyUI,
`vibecomfy.run`, or raw `tasks create` directly. The orchestrator derives the
generation intent from the sealed compilation's public-generation contract and
publishes the managed candidate through the registered capability. A second
hand-written task would split authority and can duplicate generation or
publication.

Follow the orchestrator's returned task/run evidence until it is succeeded or
settled. Then read the managed output association and Generation created by
the canonical settlement, download the selected object through the Runtime
object API, and verify its SHA-256 and media decode locally:

```python
from astrid.sdk import AstridClient

with AstridClient.open_from_launcher() as client:
    task = client.tasks.show("<task-id>")
    outputs = client.tasks.list_managed_outputs("<task-id>")
    generation_rows = client.generations.list("<project-id>")
    data = client.media.read_bytes(outputs.data[0]["object_id"])
```

The task ID, attempt ID, output association, Generation ID, local path, byte
count, and digest belong in the receipt. A direct Comfy/VibeComfy smoke is
useful diagnosis, but it does not replace this task-to-Generation check.

Managed retrieval does not override `leave_running`. Close the H3 operation
with the pod still running unless a separately authorized cleanup owner
terminates that exact pod and emits the identity-bound cleanup receipt
described above. The final receipt deliberately keeps these facts separate:

```text
task_succeeded -> candidate_verified -> editorially_approved -> cleanup_verified
```

Successful generation does not imply editorial approval or cleanup. Under
`leave_running`, `cleanup_verified` remains distinct and the watcher reports
the explicit leave-running postcondition.

## 5. Generic RunPod pack boundary

`runpod.provision`, `runpod.exec`, and `runpod.session` remain useful generic
RunPod capabilities, but they are not the canonical H3 route documented here.
Their handles, lifecycle ownership, task shape, and settlement receipts differ
from the dedicated `astrid.runpod.claim.v1` plus `h3_av.transform` flow. Do not
normalize the canonical claim handle into a generic provision handle, invent
missing account/cost fields, or insert `runpod.exec` between claim and H3
orchestration.

The same boundary applies to direct `runpod-lifecycle run`: it can diagnose
upload/SSH/remote-exec/download behavior, but it does not produce the H3 child
lineage, managed Generation, composition/verification evidence, or final
receipt. Sections 2 and 3 retain that diagnostic material for incidents where
transport itself is under test.

## 6. New pods, only when requested

For the prepared H3 release, use the repository claim waiter. It is a thin
operator wrapper around the existing provider substrate: it attaches the
existing `backup` volume without resize, requests the validated CUDA-13
image/host profile, waits for SSH readiness, verifies the mounted release venv
with a real CUDA initialization, and leaves the exact allocated pod running.
It prints a secret-free lifecycle handle only after successful verification.

```bash
python3 scripts/claim_runpod_5090_backup.py --handle-path .otto/runs/h3-av-simplicity-20260924-T2/runpod/claim-handle.json --max-wait-seconds 43200 --poll-seconds 120
```

The script defaults pin the prepared image and CUDA 13.0 constraint; do not
replace them with the generic Torch 2.4/CUDA 12.4 template. After launch, the
release path is
`/workspace/h3-golden/releases/h3-cu130-v1-candidate/runtime/venv/bin/python`
and the Comfy entrypoint is
`/workspace/h3-golden/releases/h3-cu130-v1-candidate/runtime/launch-comfy.sh`.
There is no generic-provision equivalent in this canonical procedure. Continue
with the execution request and `h3_av.transform`, and never delete the backup
volume during any later cleanup. Keeping a pod running continues provider
billing; claim, queue, and runtime timeouts are not pod-lifetime limits.

## Live review and H3 boundary

The 2026-09-21 bounded transport test passed: upload -> execute -> fetch ->
SHA-256 verification, returncode 0, terminated false, and provider-side
confirmation that the same pod remained RUNNING on backup. It ran no inference
and admitted no Astrid task. Evidence:
[practical-path review](../docs/projects/astrid-unified-execution/runpod-practical-path-review-20260921.md).

That historical transport result does not establish H3 readiness and predates
the prepared CUDA-13 claim flow. Follow the claim helper's release preflight
and the H3 orchestrator's target-schema, node/model, backend, input, managed
delivery, and verification gates. SSH/GPU presence remains insufficient.

The [remote task-manager build brief](../docs/projects/astrid-unified-execution/runpod-task-execution-build-brief.md)
is an optional future integration for scheduler-enforced placement and remote
worker settlement. It is not a prerequisite for the practical path above.
