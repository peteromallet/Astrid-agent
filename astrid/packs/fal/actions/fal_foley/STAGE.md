# fal Hunyuan-Video Foley Action

Use `fal.fal_foley` to score one short video clip with a Foley track via
fal.ai's `fal-ai/hunyuan-video-foley` model. Network-bound. One clip in, one
audio file out, prompt-conditioned.

Run through the SDK:

```python
import astrid.sdk as sdk
result = sdk.invoke(
    "fal.fal_foley",
    kind="action",
    project="demo",
    inputs={
        "clip": "runs/tile_video/example/tiles/0_0.mp4",
        "prompt": "underwater turbulence, dense bubbles, organic motion",
    },
)
```

Internal runner invocation (not a public entrypoint):

```bash
ASTRID_INTERNAL_INVOCATION=1 python3 -m astrid.packs.fal.actions.fal_foley.run \
  --clip runs/tile_video/example/tiles/0_0.mp4 \
  --prompt "underwater turbulence, dense bubbles, organic motion" \
  --out runs/foley/0_0.wav \
  --env-file .env
```

Inputs:

- `--clip` mp4/mov/webm/m4v/gif, ≤15s recommended.
- `--prompt` short natural-language description of what should sound.
- `--env-file` optional file containing `FAL_KEY`; omission uses the declared
  `credentials.fal` scope.

Outputs:

- `audio`: one audio file at `--out` (typically wav).
- `audio_manifest`: `{out}/manifest.json`, the result manifest.
- `audio_provenance`: `{out}/audio.wav.fal.json`, a provenance sidecar
  recording the request id, model id, prompt, and source URL.

Cost: ~$0.10 per 10s of input video. Requires `FAL_KEY` (env var or `.env`).
