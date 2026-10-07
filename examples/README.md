# Examples

The `examples/` directory contains committed schema fixtures, small sample
briefs, teaching packs, and the **Agentic UX** example application.
Generated media does not belong here.

## Agentic UX Example

A complete external-application walkthrough of the public Astrid SDK's
no-side-effect preview surface: **discover → inspect → dry-run invoke**.

- **Source**: [`agentic_ux/agentic_ux.py`](agentic_ux/agentic_ux.py)
- **Tutorial**: [`docs/guides/build-your-first-agentic-ux.md`](../docs/guides/build-your-first-agentic-ux.md)

```bash
python examples/agentic_ux/agentic_ux.py \
    --capability-id editorial.arrange
```

## Briefs

`examples/briefs/` contains human-readable pure-generative briefs that are safe
to commit and useful for manual smoke runs:

```bash
python3 -m astrid --brief examples/briefs/cinematic.txt --out runs/cinematic --render --target-duration 15
python3 -m astrid --brief examples/briefs/surreal.txt --out runs/surreal --render --target-duration 15
```

## Media Fixtures

Generate local sample media on demand with:

```bash
ffmpeg -f lavfi -i testsrc=duration=42:size=1920x1080:rate=30 -c:v libx264 examples/main.mp4
ffmpeg -f lavfi -i testsrc=duration=18:size=1280x720:rate=24 -c:v libx264 examples/broll.mp4
```

Notes:

- `main.mp4`, `broll.mp4`, and other generated media are not committed.
  Generate them locally when you need a real fixture render.
- `hype.timeline.json` is the small design fixture. It intentionally uses
  separate visual tracks for branding, captions, b-roll, and source footage;
  visual tracks listed earlier render above tracks listed later.
- `hype.timeline.full.json` and `hype.assets.full.json` are schema-only fixtures consumed by the smoke test and `tools/tests/test_schema_contract.py`.
- The full fixture `file` paths point at the on-demand media names, but those files do not need to exist for bundle-only smoke checks.

## Example Packs (`examples/packs/`)

`examples/packs/` contains teaching packs that demonstrate pack authoring
patterns. These packs are **not** runtime-discovered — you will not see them in
`packs list` or `packs status` output. The current authoring examples use v3;
`text_digest` is retained separately as a v2 compatibility example. Validate
the examples with the pack author check:

```bash
python3 -m astrid.core.pack.cli validate examples/packs/minimal
python3 -m astrid.core.pack.cli validate examples/packs/file_summarizer
python3 -m astrid.core.pack.cli validate examples/packs/text_digest
python3 -m astrid.core.pack.cli validate examples/packs/text_review
python3 -m astrid.core.pack.cli validate examples/packs/media
```

| Pack | Purpose |
|---|---|
| `minimal` | Canonical small v3 action pack: a directory inventory and a composed trailer action. |
| `media` | V3 ingestion and composed trailer actions, plus a declared schema, brief, and Remotion title-card element. |
| `file_summarizer` | V3 text inspection, caller-authored summary validation, and verdict actions; legacy pause/ack plans remain outside the action catalog. |
| `text_digest` | Retained v2 example of agent-in-the-loop text pipelines; not the current authoring template. |
| `text_review` | V3 deterministic summary action followed by a separate caller-authored review verdict. |

### What these packs are NOT

- **Not runtime-discovered.** They live in `examples/packs/`, not
  `astrid/packs/`. The runtime only discovers packs under `astrid/packs/`.
- **Not clip extraction packs.** The canonical product clip extraction action
  is `media.clip_extract` in `astrid/packs/media/`. The teaching `media` pack
  demonstrates a separate directory-ingestion/trailer example; it does not
  contain product clip-extraction capabilities.
- **Not hidden runtime packs.** The `visibility: hidden` field in their
  `pack.yaml` manifests is historical. These packs are structurally excluded
  from discovery by their location under `examples/packs/`.
