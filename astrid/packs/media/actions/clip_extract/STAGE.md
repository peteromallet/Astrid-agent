# media.clip_extract

## Purpose

Extract a clip segment from a source video using ffmpeg with stream copy (`-c copy`)
for fast, lossless extraction. Use when you need to trim a video to a specific
start time and duration without re-encoding.

This executor invokes ffmpeg for real via an injectable `runner` callable
(default: `subprocess.run`).  Return codes are propagated: 0 on success,
nonzero on ffmpeg failure or validation errors.

## Inputs

- `input` (file, required): Runtime-managed source video object reference when
  invoked through the SDK; the local command runner receives its materialized
  file.
- `start` (number, required): Start time in seconds.
- `dur` (number, required): Duration in seconds.

## Outputs

- `output` (file): The clipped video file, written to `{out}/clip.mp4`.

## Canonical Command

Via the Astrid SDK (recommended):

```python
from pathlib import Path

import astrid.sdk as sdk
from astrid.sdk import AstridClient

with AstridClient.open_from_launcher() as client:
    imported = client.media.import_file(
        project="demo", path=Path("source.mp4"), idempotency_key="source-import"
    )
    if not imported.ok:
        raise RuntimeError(imported.error)
    result = sdk.invoke(
        "media.clip_extract",
        kind="action",
        project="demo",
        client=client,
        inputs={"input": imported.data["object_id"], "start": 10.0, "dur": 5.0},
        wait=True,
    )
    if not result.ok:
        raise RuntimeError(result.error)
    if not result.kernel_task_id:
        raise RuntimeError("Runtime did not return a task id")
```

The host assigns the declared output path. Inspect completed output records
with `client.tasks.list_managed_outputs(result.kernel_task_id)`; the SDK does
not accept a caller-selected `output` path in `inputs`. See the
[pack guide](../../docs/SKILL.md) for the complete example.

For a local implementation test only, use `ASTRID_INTERNAL_INVOCATION`; this
path accepts local file paths and is not the public SDK input contract:

```bash
ASTRID_INTERNAL_INVOCATION=1 python3 -m astrid.packs.media.actions.clip_extract.run \
  --input source.mp4 --start 10 --dur 5 --output runs/my_clip/clip.mp4
```

## Dependencies

- ffmpeg (system binary)
