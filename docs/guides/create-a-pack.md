# When and how to create a pack

A pack is the distribution and ownership boundary for reusable work. Its V3
manifest declares the public contribution types; the implementation and
supporting files stay inside that pack. First check whether an existing pack
already covers the need. A one-off task belongs in a run, not in the pack
catalog.

## V3 contribution and support map

The V3 schema is the authority for manifest shape:
[pack.json](../../astrid/core/pack/schemas/v3/pack.json). A folder or filename
does not publish anything by itself.

| Public contribution family | Use it for | What it declares |
|---|---|---|
| `actions` | Callable work an agent or host can invoke. | The public action name, invocation, inputs, outputs, and related metadata. |
| `ui` | A contribution mounted by a receiving application, such as an editor extension. | The UI type, entry file, target host, and optional compatibility/resources. |
| `rendering` | Rendering infrastructure and reusable visual behavior. | Renderer, planner, finalizer, or element entries. Element manifests describe effects, animations, and transitions. |
| `documents` | A versioned document format understood by the pack. | A format version and its schema, with any supporting resources. |

The current manifest has no separate `visual_elements` family. Declare a
reusable effect, animation, or transition through `rendering` with
`type: element`, then point to its element manifest and resources. The
existing [Personal pack](../../astrid/packs/local/pack.yaml) is the concrete
V3 example.

Contribution type and ownership answer different questions. For the existing
one-user collection, `id: local` remains its stable owner namespace and the
display name is **Personal**. Keep that ID and existing references; do not
rename the source pack to change its label. A contribution's family describes
what it does, not who owns it.

`resources` and `authoring_only` are supporting manifest sections, not public
contribution families. Resources declare files used at runtime; authoring-only
paths are deliberately excluded from runtime packaging and include a reason.

Pack-level `dependencies`, `permissions`, `secrets`, `documentation`,
`agent`, and discovery metadata provide installation/build inputs, trust
disclosures, author guidance, and discovery context. Declare only needs the
pack actually has. Permission declarations explain the access a pack needs;
they do not, by themselves, enforce a sandbox. Do not add V1 taxonomy fields
such as `origin`, `install_tier`, or `pack_type` to a V3 manifest. The
[taxonomy reference](../packs/pack-taxonomy.md) explains the V3 metadata and
legacy compatibility output.

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
