# Media Production Pack

A v3 teaching pack showing declared media actions, action composition, a
JSON Schema, an example brief, and a Remotion rendering element.

## Public actions

`media.ingest_assets` lists files in a source directory. `media.make_trailer`
calls that public action and returns a trailer build manifest.

Invoke the ingestion action through the public SDK (from the repository root):

```python
from astrid import sdk

result = sdk.invoke(
    "media.ingest_assets",
    kind="action",
    extra_pack_roots=("examples/packs/media",),
    inputs={"source": "/path/to/assets"},
)
```

Invoke the composed trailer action the same way, with the optional brief:

```python
result = sdk.invoke(
    "media.make_trailer",
    kind="action",
    extra_pack_roots=("examples/packs/media",),
    inputs={"source": "/path/to/assets", "brief": "A warm, energetic launch."},
)
```

The action returns its result through `result.raw_result["payload"]["action_result"]`.
Validate the static pack contract with:

```bash
python3 -m astrid.core.pack.cli validate examples/packs/media
```

See `docs/SKILL.md` and `actions/make_trailer/STAGE.md` for the pack and
composition guidance. The example brief, schema, and title-card rendering
resource remain in the pack.
