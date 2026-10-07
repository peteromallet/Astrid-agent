# discord_local.command

**Action:** `discord_local.command`

Runs one bounded headed-browser Discord generation attempt through the
`discord_local.command` action.

## Inputs

- `mode`: `preview`, `queue`, `submit`, or `fetch`.
- `channel_url`: the target Discord channel.
- `command_file`: required for preview/submit; recommended for fetch.
- Optional watcher correlation, known response message ID, timeout, author,
  profile, and CDP settings.

## Outputs

- `manifest.json`: universal `discord_browser.generate` result manifest.
- `result.json`: sanitized attempt summary.
- `prompt.txt` and `command.txt`: exact prompt and portable command capture.
- `inputs/`: staged, hashed references in attachment order.
- `outputs/`: generated attachments only.
- `debug/`: screenshots, sanitized runtime result, stdout, and stderr.

Every invocation reaches a terminal status. Only `submit` presses Enter, once.
`fetch` exercises the same watcher/download path without submitting.
