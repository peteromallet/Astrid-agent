# When and how to create a pack

A pack is a reusable toolbox with a stable owner. Its V3 manifest declares the
callable work, host UI, visual elements, and less common rendering or document
contracts that the pack contributes. First check whether an existing pack
already covers the need. A one-off task belongs in a run, not in the pack
catalog.

## V3 contribution and support map

The V3 schema is the authority for manifest shape:
[pack.json](../../astrid/core/pack/schemas/v3/pack.json). A folder or filename
does not publish anything by itself.

| What you want to add | V3 declaration | Typical use |
|---|---|---|
| Callable work, direct or composed | `actions` | An agent or host invokes an operation; a composed action may invoke other actions. Use `kind="action"` for V3 callable work. |
| An interactive editor contribution | `ui` | A receiving application mounts the declared editor entry. The current V3 schema accepts `type: editor`. |
| A visual item placed on a timeline | `rendering` with `type: element` | Effects, animations, and transitions are reusable timeline elements. This is the ordinary authoring route for visual behavior. |
| Rendering host infrastructure | `rendering` with `type: renderer`, `planner`, or `finalizer` | Advanced backend integration; these are not ordinary timeline elements or callable actions. |
| A versioned file format and schema | `documents` | Use when a current consumer needs to recognize that format. The V3 schema supports it, but this checkout has no in-tree V3 `documents` example. |

These are different authoring choices mapped onto the existing V3 fields; they
are not new manifest families. There is no separate `visual_elements` key.
Declare an effect, animation, or transition through `rendering` with
`type: element`, then point to its element manifest and resources.

Contribution type and ownership answer different questions. For the existing
one-user collection, `id: local` remains its stable owner namespace and the
display name is **Personal**. Keep that ID and existing references; do not
rename the source pack to change its label. A contribution's family describes
what it does, not who owns it.

`resources` and `authoring_only` are supporting manifest sections, not public
contribution families. Resources declare files used by the pack; authoring-only
paths are deliberately excluded from runtime packaging and include a reason.

Pack-level `dependencies`, `permissions`, `secrets`, `documentation`,
`agent`, and discovery metadata provide installation/build inputs, trust
disclosures, author guidance, and discovery context. Declare only needs the
pack actually has. Permission declarations explain the access a pack needs;
they do not, by themselves, enforce a sandbox. Do not add V1 taxonomy fields
such as `origin`, `install_tier`, or `pack_type` to a V3 manifest. The
[taxonomy reference](../packs/pack-taxonomy.md) explains the V3 metadata and
legacy compatibility output.

## Keep pack source and project work in their owners

The pack owns reusable source: `pack.yaml`, action/UI/element implementation,
declared assets, and its authored `docs/SKILL.md`. The workspace Runtime owns a
project's media, timelines, clip placements and parameters, authored live-scene
objects, tasks, runs, and render outputs. A pack can supply the element code a
project uses; the saved timeline stores the selected owner, kind, ID, revision,
and parameters.

The existing one-user source pack keeps the stable ID `local` and displays the
name **Personal**. Keep `local` in element references; “Personal” is a label,
not a new pack type or project store. For live scenes, the scene HTML is
project-owned Runtime content, while the reusable import/editor host remains in
the rendering pack.

## Give it to your agent

    Help me turn this idea into an Astrid pack.
    Start with Astrid’s Pack Builder skill, check what already exists,
    then build and validate the smallest useful version.

[Open the Pack Builder skill →](../../astrid/packs/_core/docs/pack-builder/SKILL.md)

## Build it yourself

The current starter command creates a small V3 pack with only the selected
authoring roles:

    python3 -m astrid.core.pack.cli new my_pack --starter standalone --role action

The supported starter profiles are standalone, wrapper, and nested. Repeat
`--role` for action, ui, rendering, or shared support. These role switches
choose starter files; they are not manifest families. The `shared` folder is
support code, not a public contribution. The current CLI creates a renderer
example for `--role rendering`; it does not scaffold a visual element.

The scaffold writes V3 `pack.yaml`, one authored `docs/SKILL.md`, and the
selected starter files. Keep ordinary YAML `name` and `description`
frontmatter in that one skill. The manifest points to it with:

    documentation:
      kind: skill
      path: docs/SKILL.md

Link adjacent references, templates, and assets with normal relative Markdown
links. They are supporting files, not additional skills.

### Starter journeys

The existing CLI implementation is the source of truth:
[`cli_basic.py`](../../astrid/core/pack/cli_basic.py), with focused coverage
in [`test_cli_scaffold_f08.py`](../../tests/core/pack/test_cli_scaffold_f08.py).
Do not maintain a second scaffold or schema here.

- **Standalone:** the pack owns its declarations and implementation.
- **Wrapper:** keep the external repository unchanged; add a thin adapter and
  declare its normal dependency and public module. Do not vendor upstream code.
- **Nested:** preserve the parent repository layout and place only Astrid
  integration under `integrations/<pack-id>/`. A pinned `pack_subpath`
  identifies the integration root; it does not install dependencies.

Declare each public action, UI entry, rendering contribution, or document
format in the matching `pack.yaml` family. Put its implementation and private
support under the declared paths; folders are not exports.

### Read an existing example

Start with the smallest example that matches what you are adding:

| Goal | Source to read | Check and use |
|---|---|---|
| One action plus a composed action | [`examples/packs/minimal/pack.yaml`](../../examples/packs/minimal/pack.yaml) and [`actions/make_trailer.py`](../../examples/packs/minimal/actions/make_trailer.py) | `python3 -m astrid.core.pack.cli validate examples/packs/minimal --json`; the second action calls the first through `kind="action"`. It is a teaching example under `examples/`, not a runtime-discovered product pack. |
| An element with a media asset | [`local` pack declaration](../../astrid/packs/local/pack.yaml), [`frame-overlay/element.yaml`](../../astrid/packs/local/rendering/elements/effects/frame-overlay/element.yaml), its [`component.tsx`](../../astrid/packs/local/rendering/elements/effects/frame-overlay/component.tsx), and [`frame.png`](../../astrid/packs/local/rendering/elements/effects/frame-overlay/assets/frame.png) | Validate `astrid/packs/local`, regenerate/check the editor catalog as described below, then select **Frame Overlay** from the element picker. Save, reopen, and render the timeline to check the packaged asset in output. |
| A user-authored Three.js scene | [`rendering` UI declaration](../../astrid/packs/rendering/pack.yaml), [`ui/live-scenes/extension.tsx`](../../astrid/packs/rendering/ui/live-scenes/extension.tsx), the [`scene template`](../../astrid/packs/rendering/docs/templates/two-shot-threejs.ts), and the [live-scene authoring guide](../../astrid/packs/rendering/docs/references/live-scenes-authoring.md) | Validate the rendering pack; bundle a self-contained HTML entry and prepare the documented JSON package with `assets: []`; use **Import prepared scene**. The result is a Runtime-backed visual clip that can be selected and scrubbed in preview. Follow the live-scene guide for the supported Astrid Three.js/Remotion export path; do not infer that every Reigh or renderer route exports live scenes. |

`examples/packs/media` is another V3 teaching pack: its `project-title-card`
element is declared beside the action examples, while its `kind: schema` and
`kind: template` entries are supporting resources. They are not a
`documents` contribution. This pack is not runtime-discovered; its element
illustrates declaration shape, not a live editor catalog entry. See
[`examples/README.md`](../../examples/README.md) for that distinction.

## Visual contributions and live scenes

A pack-owned rendering element is a trusted build-time contribution. Its
element manifest and any TSX component/assets are declared in the pack and
included in the Astrid/Reigh rendering build and generated catalog. For
example, an entry has this shape:

    rendering:
      effects/text-card:
        type: element
        path: rendering/elements/effects/text-card/element.yaml
        resources:
          - kind: implementation
            path: rendering/elements/effects/text-card/component.tsx

The manifest path and resources belong to the source pack; do not introduce a
second catalog declaration or a `visual_elements` key. Three.js is an existing
host rendering route and does not change the pack's element contract.

### Updating an existing element

Edit the files owned by the source pack. For the asset-backed `local` example,
the implementation is `astrid/packs/local/rendering/elements/effects/frame-overlay/component.tsx`,
its schema and defaults are in `element.yaml`, and `frame.png` is declared as
an `asset` in both the element manifest and the `rendering.effects/frame-overlay`
entry in `astrid/packs/local/pack.yaml`.
Keep the pack owner, element ID, and kind stable; update the declared resource
paths when the source files change. Do not edit the generated catalog by hand.

Validate the pack, regenerate the editor-facing catalog, then check it is
current:

```bash
python3 -m astrid.core.pack.cli validate astrid/packs/local --json
python3 scripts/gen_element_catalog.py
python3 scripts/gen_element_catalog.py --check
```

The generated descriptor includes a content-based element revision. In a
checkout-backed editor, an imported TSX edit may appear through Vite HMR; that
only refreshes the browser module. The element bridge resolves the current
component by owner/kind/ID, while render workers validate the saved revision,
so an old pin can preview newer code and still fail export as stale. Do not
treat HMR as proof that preview and saved bytes agree.

When the manifest, defaults, component, helpers, or declared assets change,
regenerate the descriptor and refresh the receiving browser build/source.
Refresh the source-bound render worker through the supported launcher
bootstrap described in [How Astrid executes work](how-it-works.md):
`astrid.sdk.autobootstrap.ensure_runtime` invokes
`astrid.sdk.host_bootstrap.ensure_pack_host` with the Runtime-issued source and
worker identity ([bootstrap API](../../astrid/sdk/autobootstrap.py),
[`ensure_pack_host`](../../astrid/sdk/host_bootstrap.py)). It refreshes an idle
worker against the selected source/runtime identity without cancelling work.
If an active child task makes it busy, reconfiguration is deferred; wait for
that work to finish, then retry the same supported bootstrap. Do not invent a
manual worker-refresh CLI.

Re-apply the current owner-qualified element from its catalog and save the
timeline through the normal Runtime editor route. Existing placements stay
pinned to their saved revision; source edits never rewrite them. Reopen and
verify owner, kind, ID, revision, and authored parameters before rendering the
saved state with `rendering.render`. For a new manifest-declared editor UI
entry, regenerate the static projection with
`python3 scripts/gen_editor_extension_catalog.py`, then let the host consume it
and activate the extension. Existing imported UI code may HMR in local
development; the browser activates the TS-exported extension manifest, not a
continuously reread YAML file. If an edit retains the same manifest reference,
reload or reactivate when needed. This is a checked-out build, not dynamic pack
installation. The
`rendering.html_canvas_effect` action is a scaffold for a new HTML-canvas
effect, not an updater for an existing element.

A user-authored live scene follows a separate Runtime project-object route.
The scene is self-contained HTML, admitted and saved through Runtime with the
current `assets: []` restriction; it is not a TSX component installed or
compiled at runtime. Follow
[Live scene authoring](../../astrid/packs/rendering/docs/references/live-scenes-authoring.md)
for its manifest and host contract.

## Validate and inspect

    python3 -m astrid.core.pack.cli validate my_pack --json
    python3 -m astrid.core.pack.cli inspect my_pack --pack-root .

`inspect` reads back the declared documentation pointer and public sections.
Static validation checks structure and paths; it does not install dependencies
or prove runtime behavior. Discover and invoke through the supported SDK/host.
For callable V3 work, the SDK's unified selector is `kind="action"`; the
typed `executor` and `orchestrator` selectors remain available for their
specific routes. See the [SDK reference](../reference/sdk.md).

After source edits, use Astrid's existing sync/check against a disposable
harness state when needed. Edit the authored pack, never an installed view or
personal/global sync target.

[Choosing what to build](creating-tools.md) · [Pack contract](../packs/contract.md)

Have a useful finding rather than a reusable tool?
[Contribute knowledge](contributing-knowledge.md).

[Back to Astrid](../../README.md)
