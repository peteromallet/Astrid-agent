---
name: media
description: >
  Media pack — lossless clip extraction, GIF and sticker search, and weak-mic
  speech repair for downstream timeline and media workflows.
---

# Media — Agent Guide

## When to Use This Pack

Use this pack for lossless clip extraction, GIF or sticker lookup, and repair
of weak-mic speech before it is used in downstream media workflows.

## Entrypoints

This pack has no orchestrators. Agents should invoke one of its actions
directly through the SDK:

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
        inputs={
            "input": imported.data["object_id"],
            "start": 10.0,
            "dur": 5.0,
        },
        wait=True,
    )
    if not result.ok:
        raise RuntimeError(result.error)
    if not result.kernel_task_id:
        raise RuntimeError("Runtime did not return a task id")

    outputs = client.tasks.list_managed_outputs(result.kernel_task_id)
    if not outputs.ok:
        raise RuntimeError(outputs.error)
    print(outputs.data)
```

File-valued SDK inputs must be Runtime-managed object references. Import local
files with `client.media.import_file(...)` and pass the returned `object_id` or
`digest`; a caller-local path is not available to the executor. The action
declares its output location, so do not pass an `output` path in `inputs`.
After a non-generation action completes, use
`client.tasks.list_managed_outputs(task_id)` to read its managed output records.
The same managed-object rule applies to optional file inputs such as
`env_file`; prefer the configured Runtime secret for `FAL_KEY` or
`GIPHY_API_KEY` where available.

## Executors

| Executor | What it does |
|---|---|
| `media.clip_extract` | Extract a video segment with `ffmpeg -ss/-t/-c copy` without re-encoding. Inputs: `input`, `start`, `dur`, and `output`. Requires `ffmpeg` on PATH. |
| `media.gif_search` | Search GIPHY for GIF or sticker assets and optionally download one rendition for timeline use. |
| `media.speech_repair_lavasr` | Extract a video section, repair weak speech with fal.ai LavaSR, then remux and loudness-master the result. |

## Supporting guides

- [Clip extraction](../actions/clip_extract/STAGE.md)
- [GIF and sticker search](../actions/gif_search/STAGE.md)
- [Weak-mic speech repair](../actions/speech_repair_lavasr/STAGE.md)
- [References usage](references.md) — References are a Runtime CLI mount under media; this guide covers their supported workflow and boundaries.
