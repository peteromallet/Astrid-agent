# Pack and contribution taxonomy

This page describes the current V3 authoring model. The exact manifest
contract is [pack.json](../../astrid/core/pack/schemas/v3/pack.json); the
[pack guide](../guides/create-a-pack.md) is the authoring walkthrough.

## Separate the owner from its contributions

A pack is the stable namespace and ownership boundary. The family declared in
its manifest says what each contribution does. Do not use a pack's display
name, domain, folder layout, or support metadata as a substitute for the
contribution declaration.

| Public V3 family | Contribution |
|---|---|
| `actions` | Callable functions and operations. The SDK's `kind="action"` selector is the unified callable route; typed executor/orchestrator selectors remain available for their specific routes and older registrations. |
| `ui` | Host-mounted editor or other declared UI entry. |
| `rendering` | Renderer, planner, finalizer, or reusable visual element. |
| `documents` | Versioned document formats and schemas. |

`resources` and `authoring_only` are supporting manifest sections, not public
contribution types. `resources` lists files used by the pack or contributions;
`authoring_only` records material excluded from runtime packaging with a
reason. `shared/` is a useful private support layout, not a manifest family.
Dependencies, permissions, secrets, documentation, and discovery metadata
describe how a pack is built, trusted, discovered, or documented; they are
not additional contribution types.

The proposed future distinction between a complete **Tool**, reusable UI
**Widget**, and callable **Action** is a product-design direction, not a new
V3 taxonomy. See the [future model](../guides/frontend-pack-vision.md); use the
current declarations in this page when authoring a pack.

## Rendering and visual elements

The `rendering` family contains two different kinds of contribution:

- Renderer, planner, and finalizer entries provide rendering infrastructure.
- An entry with `type: element` declares a reusable visual element. Its
  element manifest uses a kind of `effect`, `animation`, or `transition`.

The element owner, kind, ID, and revision are part of the stable reference.
Keep the owning pack ID and existing references when updating a personal
element. Do not add a separate `visual_elements` key or a second catalog.

Pack-owned TSX components and their declared assets are trusted build-time
inputs to the Astrid/Reigh rendering build and catalog. A user-authored
Three.js live scene is a separate self-contained HTML object admitted and saved
through Runtime; the current scene contract uses `assets: []`. Runtime scenes
are not TSX elements or a pack-source code loader. See
[Live scene authoring](../../astrid/packs/rendering/docs/references/live-scenes-authoring.md).

## V3 pack metadata

These fields describe the pack, not what its contributions do:

| Field | Meaning | V3 values |
|---|---|---|
| `id`, `name`, `version`, `description` | Stable identity and human-facing label. | `id` is the namespace; change it only through an explicit identity migration. |
| `domain` | Broad area for grouping. | `general`, `development`, `editorial`, `generation`, `infrastructure`, `integration`, `media`, `system`. |
| `status` | Current pack state. | `active`, `experimental`, `deprecated`. |
| `visibility` | Whether normal discovery shows the pack. | `visible`, `hidden`. |
| `stability` | API maturity. | `stable`, `experimental`, `deprecated`. |
| `support` | Maintenance boundary. | `project`, `core`, `community`. |
| `keywords`, `capabilities`, `aliases` | Additional discovery and compatibility metadata. | Use the shapes in the V3 schema; they do not publish an undeclared family. |

For the existing one-user collection, keep `id: local` and its stable
references. The display name **Personal** and its description communicate
user-owned content; “Personal” is not a new pack type or a reason to migrate
the ID.

The older V1 taxonomy names `origin`, `install_tier`, and `pack_type` are not
fields to add to a V3 source manifest. Some normalized CLI inspect/list output
still projects compatibility values for older callers; treat those as output
compatibility, not authoring instructions.

## Build inputs and disclosures

- Declare pack-level `dependencies` for the Python, npm, system, or other
  supported dependency groups actually used. A wrapper's declaration does not
  vendor upstream code or promise an isolated install.
- Declare `permissions` with a reason and optional access/service details.
  They describe requested access and do not by themselves enforce a sandbox.
- Declare named `secrets` where a contribution needs credentials; keep secret
  values out of the manifest and source.
- Declare `resources` only for files the pack or its contributions need at
  runtime. If authoring/test material should not ship, list it in
  `authoring_only` with its reason.
- Use `documentation` to point to the pack's single authored skill bundle (or
  the supported agents/none forms), and use `agent`/discovery metadata to
  orient users and discovery surfaces.

## Discover and validate

Use the current pack CLI to validate and inspect the actual manifest:

    python3 -m astrid.core.pack.cli validate <pack-path> --json
    python3 -m astrid.core.pack.cli inspect <pack-id> --pack-root <pack-root>

SDK discovery supports `kind="action"` for the unified callable surface,
`kind="executor"` or `kind="orchestrator"` for typed callable lookup, and
`kind="element"` for visual contributions. Elements are selected and rendered
through the host; they are not invoked as SDK actions.

V1/V2 manifests and their old component folders remain compatibility and
migration material for existing packs. For new work, start with the
[V3 pack guide](../guides/create-a-pack.md), not those legacy examples.
