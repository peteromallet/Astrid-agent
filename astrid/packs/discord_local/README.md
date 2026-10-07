# Personal Discord browser adapter

`discord_local.command` turns the existing headed Chrome proof-of-concept into
a bounded Astrid action. It supports:

- `preview`: fill and validate one slash command, but do not press Enter.
- `queue`: press Enter once, record the validated submission, and immediately
  release the single shared tab so another command can be queued.
- `submit`: press Enter once, watch only messages newer than the submission
  boundary, download the matched result, and stop.
- `fetch`: do not type anything; find and download an already completed
  attachment after a supplied timestamp, then stop.

Configured model/channel profiles live in `model_variants.json`. Select one
with `variant="v1"` or `variant="v2"` in the invoke inputs; an explicit
`channel_url` remains available for one-off channels.


The action stages ordered input media, preserves the exact prompt, hashes
inputs and outputs, and always writes `result.json` and `manifest.json`.
Screenshots and sanitized runner logs live under `debug/`; generated media live
under `outputs/`. Signed Discord CDN URLs and absolute source paths are omitted
from the canonical artifacts.

The existing local runtime is expected at:

```text
tools/discord-command-poc/discord-command-poc.mjs
```

Install its pinned dependency once:

```bash
npm ci --prefix tools/discord-command-poc
```

First use opens a dedicated, visible Chrome profile. Sign in manually in that
window. The profile stays local under `.tmp/discord-command-poc-profile`.

## Preview

```python
import astrid.sdk as sdk
result = sdk.invoke("discord_local.command", out="runs/discord-local-preview", inputs={
    "mode": "preview",
    "channel_url": "https://discord.com/channels/GUILD/CHANNEL",
    "command_file": "runs/discord-command-poc/prompts/example.txt",
})
```

## Submit once

Change `mode=preview` to `mode=submit`. This is the only mode that presses
Enter, and it never retries the submission.

## Recover a prior result

```python
result = sdk.invoke("discord_local.command", out="runs/discord-local-fetch", inputs={
    "mode": "fetch",
    "channel_url": "https://discord.com/channels/GUILD/CHANNEL",
    "command_file": "runs/discord-command-poc/prompts/example.txt",
    "after": "2026-07-27T23:35:00Z",
    "response_message_id": "KNOWN_DISCORD_MESSAGE_ID",
    "match": "35635346",
    "link_match": ".mp4",
})
```

Supplying the historical command file in fetch mode is optional for retrieval,
but recommended: it lets the manifest preserve the exact prompt and ordered
input references so the recovered run can be used directly as an experiment
case. If the response is old enough that Discord no longer loads it in the
channel's current message slice, pass its `response_message_id` to deep-link to
that message before the bounded poll.

For managed runs, route the run to a project instead of an output directory.
Reference that managed run ID from `experiment.json`; no import bridge is
needed for new pack runs.
