---
name: discord_local
description: Run one bounded personal Discord browser generation attempt or recover one completed result into an experiment-ready Astrid manifest.
---

# Discord local adapter

Use action `discord_local.command`.

1. Choose `preview` to fill without submitting, `queue` to press Enter exactly
   once and release the single shared tab without waiting, `submit` to press
   Enter exactly once and watch for the new result, or `fetch` to recover an
   already completed result without typing. Pair queued jobs with bounded,
   correlated fetches.
2. Pass a command file whenever possible. Its `/gen prompt:...` text and ordered
   `input_media`, `input_media_2`, and later references become canonical
   experiment provenance.
3. For `fetch`, always provide an `after` timestamp and a narrow `match` and/or
   `link_match`.
4. Prefer `--project PROJECT_SLUG` for a new experiment case. The resulting
   managed run contains the universal `manifest.json` used directly by
   `iteration.experiment_prepare`.
5. Treat a `draft`, `timed_out`, `provider_rejected`, `interrupted`, `partial`,
   or `failed` manifest as truthful terminal evidence. Never silently retry a
   submitted command.
6. Prefer `--input variant=v1` or `--input variant=v2` over pasting a channel
   URL. The checkout-local `model_variants.json` maps those stable aliases to
   their Discord channels, so switching models changes one action input.

The watcher is part of the bounded action invocation, not a daemon. It uses a
pre-submit Discord message boundary (or the fetch `after` timestamp), optional
author/correlation filters, input-filename exclusion, and a finite timeout.
