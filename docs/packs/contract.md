# Astrid Pack Contract

This is the current authoring contract for V3 packs. The V3 JSON Schema is
[pack.json](../../astrid/core/pack/schemas/v3/pack.json); the
[V3 pack guide](../guides/create-a-pack.md) explains the authoring route.
Sections in older V1/V2 reference pages describe compatibility formats, not
the recommended shape for new work.

## Pack identity and ownership

A pack is the namespace, distribution, and ownership boundary for a coherent
set of reusable work. Its stable `id` owns its declared contributions and
their references. A display `name` and `description` help a person recognize
the pack; they do not alter the pack ID, contribution IDs, or project state.

For the existing one-user collection, keep the source pack ID `local` and its
existing references. Its display name **Personal** and description communicate
that this is user-owned reusable content. Do not create a new taxonomy value
or migrate the identity to display that label.

Pack ownership and contribution type are independent. One owner can provide
actions, UI extensions, rendering behavior, document formats, and resources.
Do not create separate owner packs just because a contribution serves a
different host or has a different implementation language.

## V3 contribution families

The V3 schema has four public declaration fields in `pack.yaml`, but authors
do not need to treat them as four equally common product concepts. Start with
the user's need and map it to the existing field:

| Authoring need | V3 declaration | Contract |
|---|---|---|
| Callable operation, direct or composed | `actions` | Declared invocation, input/output, and supporting resource data. `kind="action"` is the normal V3 SDK route. |
| Interactive host UI | `ui` | Host-mounted entry and target. The V3 schema currently supports `type: editor`. |
| Reusable timeline visual | `rendering` with `type: element` | A manifested effect, animation, or transition selected and consumed by the rendering host. |
| Rendering infrastructure | `rendering` with `type: renderer`, `planner`, or `finalizer` | Advanced host/backend protocol contributions; not timeline elements or callable actions. |
| Versioned file format | `documents` | A versioned format declaration and schema. This is supported in the schema, but no current in-tree V3 pack/example declares it. |

These are author-facing choices mapped to the current manifest, not a proposal
for new sections. The `rendering` family deliberately holds both visual
content and backend infrastructure; see the separate contracts below.

The starter's `action`, `ui`, `rendering`, and `shared` role flags choose
scaffold files. They are not a second manifest taxonomy; `shared` is private
support layout rather than a public family. A folder or file alone does not
publish an entry.

`resources` and `authoring_only` are supporting manifest sections rather than
public contribution types. Resources declare pack-relative files needed by
the pack or contributions. Authoring-only entries identify source material
excluded from runtime packaging and give a reason.

For callable V3 work, SDK `kind="action"` is the unified selector across the
callable surface. Typed `kind="executor"` and `kind="orchestrator"`
selectors remain supported for specific typed routes and older registrations.
An element is selected with `kind="element"`; the SDK does not invoke visual
elements as actions.

Pack source and project state have different owners. The pack owns reusable
implementation, assets, documentation, and stable contribution identity. The
workspace Runtime owns project media, timelines, element placements and saved
parameters, authored live-scene objects, tasks, runs, and outputs. The
user-owned collection keeps pack ID `local` and display name **Personal**;
`Personal` changes the label, not owner IDs or Runtime storage.

## Visual elements and rendering

The `rendering` family includes two distinct roles, used by different authors:

- Renderer, planner, and finalizer entries provide rendering infrastructure
  and are advanced host/backend work.
- `type: element` entries point to reusable element manifests. Those manifests
  declare the visual `effect`, `animation`, or `transition` kinds that
  timeline authors select. This is the common reusable-visual path.

An element reference remains scoped by its owning pack and kind. Preserve
pack ID, element ID, and existing reference/revision data when changing
implementation or assets. There is no separate `visual_elements` manifest
family and no parallel element catalog.

Pack-owned TSX components and their declared resources are trusted inputs to
the checked-out Astrid/Reigh build and generated rendering catalog. They are
not downloaded or compiled by Runtime at project-edit time. Production and
other machines use the source/catalog included in their build; updating them
requires the normal source-delivery/build/reload path, not automatic pack
installation or live source download.

Changing an element's manifest, defaults, implementation, helper, or assets
changes its catalog revision. Regenerate the editor catalog from the source
registry; then re-apply the current owner-qualified element to update a saved
placement's revision. HMR or a file edit does not rewrite Runtime timeline
state. In checkout-backed development, the browser may resolve the current
component by owner/kind/ID and show HMR-ed code under an older saved revision,
while export validates the pinned revision. Treat preview as code feedback,
not evidence that the saved placement is current. The step-by-step source,
catalog, save/reopen, and render check belongs in the
[authoring guide](../guides/create-a-pack.md#updating-an-existing-element).

A user-authored live scene is a separate Runtime project object: self-contained
HTML admitted and persisted through Runtime with the current `assets: []`
restriction. It is not a V3 TSX element or a pack-owned dynamic loader.
Three.js is an existing host route for admitted scenes, not a new pack
contribution family. See
[Live scene authoring](../../astrid/packs/rendering/docs/references/live-scenes-authoring.md).

## Pack metadata, dependencies, and trust

Pack metadata answers questions about grouping, lifecycle, discovery, and
support; it does not classify the pack's contribution types:

- `domain` groups the pack by broad area.
- `status`, `visibility`, and `stability` describe lifecycle, normal
  discoverability, and API maturity.
- `support` records the maintenance boundary; `project` is appropriate for
  project-owned packs.
- `keywords`, `capabilities`, and `aliases` are discovery/compatibility
  metadata with the shapes and limits in the V3 schema.

Declare actual build dependencies under `dependencies` using supported groups.
Describe requested access in `permissions` with a reason and any useful
service/access detail. These are disclosures, not a sandbox implementation.
Declare secret names and requirements under `secrets`; never include secret
values. Use `documentation` for the single authored skill bundle and `agent`
for concise purpose and routing metadata.

`origin`, `install_tier`, and `pack_type` belong to older taxonomy contracts,
not the V3 source manifest. Some normalized CLI output keeps compatibility
projections of these names for existing consumers; do not add them to new V3
manifests.

## Discovery and validation

Use manifest-backed SDK/host discovery. For the current V3 pack CLI:

    python3 -m astrid.core.pack.cli validate <pack-path> --json
    python3 -m astrid.core.pack.cli inspect <pack-id> --pack-root <pack-root>

Validation checks the declared contract and paths. It does not execute a
contribution, prove browser/render behavior, or install arbitrary dependencies.
Use the relevant host/runtime for behavior checks.

V1/V2 component layouts and the M0 discovery narrative are historical
compatibility material. They do not define V3 contribution names, current
taxonomy fields, or the new-pack authoring flow. Start with the
[V3 pack guide](../guides/create-a-pack.md) and
[current taxonomy](pack-taxonomy.md) for new work.
