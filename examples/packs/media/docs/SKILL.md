---
name: media
description: Use this example pack to learn declared media actions, action composition, and pack-owned rendering resources.
---

# Media Production Example Pack

This teaching pack demonstrates how a pack declares public actions, a
composed action, a JSON Schema, a brief template, and a rendering element.

## Actions

- `media.ingest_assets` lists files in a source directory.
- `media.make_trailer` calls `media.ingest_assets` and returns a trailer build
  manifest.

The Remotion title-card element is declared under `rendering` in `pack.yaml`.
The brief schema and example input remain ordinary pack-owned resources.

See [the action guide](../actions/make_trailer/STAGE.md) for the composition
example.
