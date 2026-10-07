---
name: minimal
description: Use this example pack as the smallest reference for authoring and composing v3 actions.
---

# Minimal Example Pack

This pack demonstrates the smallest useful v3 composition:

- `minimal.ingest_assets` returns a sorted directory inventory.
- `minimal.make_trailer` calls `minimal.ingest_assets` and returns a trailer
  manifest.

Both are ordinary declared actions in `pack.yaml`. The composition is in
[`actions/make_trailer.py`](../actions/make_trailer.py); there is no separate
orchestrator role. See the [ingestion action](../actions/ingest_assets/STAGE.md)
and [composed action](../actions/make_trailer/STAGE.md) for their contracts.
