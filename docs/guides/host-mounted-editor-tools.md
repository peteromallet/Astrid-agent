# Host-mounted editor Tools

This guide records the V3 boundary used by the admitted Video Editor Tool. It
describes a real Astrid declaration and its existing Reigh host entry; it is
not a general plug-in or installer contract.

## Declare the Tool in Astrid

The existing declaration is `ui.video-editor` in
[video_editing/pack.yaml](../../astrid/packs/video_editing/pack.yaml). It has
`type: tool`, `target: reigh`, `compatibility.host: "1"`, and the JSON entry
[entry.json](../../astrid/packs/video_editing/ui/video-editor/entry.json):

```json
{
  "schema_version": 1,
  "tool_id": "video-editor",
  "host_entry": "video-editor"
}
```

The stable IDs remain `video_editing.video-editor` (canonical pack ID plus
Tool ID), `video-editor` (host ID), and `/tools/video-editor` (existing host
route). The declaration points to that existing host entry; it does not
bundle a React page or create a new route.

Use the V3 family that matches the contribution. `type: tool` is the whole
host-mounted Tool. `type: editor` is an editor-only contribution, such as the
existing [`live-scenes` entry](../../astrid/packs/rendering/ui/live-scenes/extension.tsx)
in the `rendering` pack. The Tool catalog and
editor-extension catalog intentionally select different declaration types.
Changing visibility of other Tools does not remove editor effects,
animations, transitions, overlays, panels, inspectors, or extension commands
from the Video Editor. Those surfaces keep their existing IDs and runtime
semantics. A declared `timelineOverlay` has an implemented host renderer, but
is still controlled by the provider's existing opt-in; the query switch used
by `npm run dev:editor` is development-only.

## Generate and bind source

From the Astrid checkout containing the source you intend Reigh to build:

```sh
python3 scripts/gen_tool_catalog.py
python3 scripts/gen_tool_catalog.py --check
```

When editing an editor-only contribution, also regenerate and check its
separate catalog:

```sh
python3 scripts/gen_editor_extension_catalog.py
python3 scripts/gen_editor_extension_catalog.py --check
```

The generated Tool catalog binds the manifest digest, entry bytes, declared
resource paths and digests, dependencies, compatibility, and release digest.
Reigh imports that catalog as `@astrid/tools/catalog`; Vite build/test
configuration checks the bound source bytes, and the launch boundary checks
the admitted catalog identity again. Build Reigh with the matching Astrid
source selected:

```sh
ASTRID_CHECKOUT=/path/to/matching/Astrid npm run build
```

The provenance-pinned `vendor/astrid-browser` fallback does not contain the
selected V3 Video Editing Tool declaration/catalog. Pointing a build at that
fallback is not a substitute for the matching Astrid checkout. There is no
dynamic package loader or arbitrary install path in this contract.

## Other Tool shapes are design-only

A future declaration such as `ui.<another-tool>: { type: tool, target: reigh,
entry: ... }` is only a sketch until Reigh adds a real public host entry,
catalog admission, launch mapping, and source-bound build. The current launch
boundary admits the catalog-bound Video Editor only. A declaration by itself
does not implement the host service contract or make another route launchable.

For editor contribution slots, scoped runtime services, and the real source
change/reopen procedure, see Reigh's `docs/video-editor/tool-authoring.md` in
the Reigh repository.
