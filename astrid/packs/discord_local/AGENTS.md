# Personal Discord Browser Adapter

## Overview

This is a gitignored personal adapter pack. It wraps the local headed-browser
Discord runner as one bounded Astrid action attempt.

## Quick start

Inspect `docs/SKILL.md`, then use `discord_local.command`. Preview is the safe
default; submit must be selected explicitly. Fetch recovers a completed result
without typing or submitting.

## Boundaries

- Keep every invocation bounded by `timeout_seconds`.
- Never add an indefinite watcher or an automatic retry-after-submit loop.
- Keep Chrome authentication in the dedicated local profile.
- Never persist Discord attachment URLs or absolute source paths in canonical
  `result.json` or `manifest.json`.
- Direct experiment cases at the managed run containing `manifest.json`; the
  legacy `iteration.experiment_import` executor is only for old POC runs.
