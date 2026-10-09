# Managed Codex image edits

Use the public `generation.generate_image` entrypoint with `execution="codex"`.
The SDK selects the managed Codex profile; do not invoke its executor module
or supply a worker staging directory yourself. `qwen-image-edit` is a catalog
routing label here: the actual provider is Codex's built-in image tool, recorded
as `codex/gpt-image`, not a claim that Qwen generated the image.

Open a connected client and pass it explicitly to `sdk.invoke`. Caller-local
paths are valid only for the import operation below. Worker reference inputs
must be managed image descriptors, including a safe basename such as
`source.png` (no directory components). A digest alone passes early admission
but fails host materialization if `filename` is missing.

This complete example imports a local source and character guide, then edits
one still. Replace the project slug and the two local paths before running.
It creates a generation; it does not replace media in a timeline.

```python
from pathlib import Path
import hashlib
import astrid.sdk as sdk

project = "demo"

def import_image(client, project_id, path, filename):
    path = Path(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    imported = client.media.import_file(
        project=project_id,
        path=path,
        idempotency_key=f"codex-reference-{content_hash}",
    )
    if not imported.ok:
        raise RuntimeError(str(imported.error))
    object_id = imported.data["object_id"]
    return {
        "object_id": object_id,
        "digest": object_id,
        "filename": filename,
        "media_type": "image/png",
        "size_bytes": path.stat().st_size,
    }

with sdk.AstridClient.open_from_launcher(start_pack_host=True) as client:
    selected = client.projects.show(project)
    if not selected.ok:
        raise RuntimeError(str(selected.error))
    project_id = selected.data["project_id"]
    source = import_image(client, project_id, "./source.png", "source.png")
    guide = import_image(client, project_id, "./character-guide.png", "character-guide.png")
    result = sdk.invoke(
        "generation.generate_image",
        kind="executor",
        project=project,
        client=client,
        inputs={
            "model": "qwen-image-edit",
            "mode": "edit",
            "execution": "codex",
            "prompt": (
                "Create one 16:9 still. Preserve the room in reference 1 and "
                "the character design in reference 2; have her present a book."
            ),
            "image_ref": source,
            "style_ref": guide,
            "count": 1,
            "size": "auto",
            "quality": "high",
            "background": "opaque",
            "timeout": 600,
        },
        wait=True,
        timeout_seconds=900,
    )
    if not result.ok:
        raise RuntimeError(str(result.error))
    image = next(
        artifact for artifact in result.outputs["artifacts"]
        if artifact["output_port"] == "generated_images"
    )
    # Optional delivery copy, read through the public managed media API.
    Path("./edited-image.png").write_bytes(client.media.read_bytes(image["digest"]))
    print(result.run_id, image["digest"])
```

If the references already belong to the Runtime, reuse their verified
`sha256:...` object IDs instead of importing them again. Build the same
`object_id`, `digest`, `filename`, and `media_type` descriptor from their
receipts; include the known byte size when available. Do not invent IDs or
use private CAS paths. PNG filenames/media types in this example must match
the actual files; use the corresponding filename and MIME type for JPEGs.

Reference order is `image_ref` (source), `style_ref` (character/style), then
`brand_ref` (optional symbols/brand). Each supplied role must use a **distinct
image digest**. If the source already contains the needed brand reference,
omit `brand_ref`; do not repeat the source descriptor. References are limited
to 16 MiB each. `edit` requires `image_ref`; `style_ref` and `brand_ref` are
optional. Explicit `mode` and `execution` keep routing unambiguous.

Successful results expose both `artifacts` and `managed_outputs`. Keep the
run ID and managed output receipt: its generation ID and project association
preserve provenance. A later timeline edit should select this managed object
and its corresponding generation variant through the timeline checkout route,
rather than reimporting the delivery PNG as an unrelated upload. Inspect the
actual pixels before selecting the result; size and aspect ratio are prompt
hints rather than guaranteed dimensions.

Common errors and their fixes:

- `explicit generated runtime client is required`: pass the connected client
  as `client=client` to `sdk.invoke`.
- `file input ... requires a managed Runtime object`: import the reference or
  use an existing managed image descriptor, not a caller-local path.
- `HC-04 CAS parameter ... requires a safe filename`: include a filename such
  as `source.png` in each descriptor.
- `Codex reference roles must use distinct images`: omit duplicate roles;
  source, style, and brand references cannot repeat the same digest.
