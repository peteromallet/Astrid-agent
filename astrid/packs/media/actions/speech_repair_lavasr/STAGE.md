# media.speech_repair_lavasr

## Purpose

Repair a weak-mic speech section using the ADOS Pom repair chain that worked
best in review:

1. Extract the requested video section.
2. Create a hotter 16 kHz mono speech pre-lift.
3. Run `fal-ai/lava-sr`.
4. Optionally run `fal-ai/deepfilternet3` as a denoise/48 kHz post-pass.
5. Remux the repaired audio onto the extracted video.
6. Apply the final loudness/compressor/limiter pass.

Use this for short dialogue sections where the source mic is too low or muffled
but the source video should be preserved.

## Inputs

- `input` (file, required): Runtime-managed source video object reference when
  invoked through the SDK; the local command runner receives its materialized
  file.
- `start` (number, required): Start time in seconds in the source video.
- `dur` (number, required): Duration in seconds.
- `env_file` (file, optional): Runtime-managed `.env` object containing
  `FAL_KEY` when invoked through the SDK.
- `deepfilternet3` (boolean, optional): Run `fal-ai/deepfilternet3` after LavaSR.

## Outputs

- `output` (file): Repaired MP4 at `{out}/speech-repair-lavasr.mp4`.
- `manifest.json`: Inputs, intermediate artifact names, FAL response file, and
  basic loudness metrics.

The MP4 is Runtime-managed. After a successful invocation, inspect its output
record with `client.tasks.list_managed_outputs(result.kernel_task_id)`; see the
[pack guide](../../docs/SKILL.md) for the complete SDK flow.

## Canonical Command

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
        "media.speech_repair_lavasr",
        kind="action",
        project="demo",
        client=client,
        inputs={
            "input": imported.data["object_id"],
            "start": 578.64,
            "dur": 151.0,
        },
        wait=True,
    )
    if not result.ok:
        raise RuntimeError(result.error)
```

To run the optional DeepFilterNet3 post-pass, add `"deepfilternet3": True` to
the invocation's `inputs` mapping above.

The SDK file-input, Runtime output-record, and optional `env_file` rules are
described in the [pack guide](../../docs/SKILL.md). Prefer the configured
Runtime `FAL_KEY` secret to passing a credential file.

## Dependencies

- `ffmpeg`, `ffprobe`
- Python `fal_client`
- `FAL_KEY` in environment or candidate `.env`
