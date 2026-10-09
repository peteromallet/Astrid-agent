---
name: pack-builder
description: "Design and implement reusable Astrid packs and contributions. Use when deciding whether work belongs in an action, editor UI extension, rendering contribution, document format, or a personal pack, and when authoring its V3 manifest and entrypoints."
metadata:
  short-description: "Build reusable Astrid packs and contributions"
---

# Pack Builder

Use this skill when a maker wants to turn repeatable work into a reusable
Astrid pack. First inspect and compose the existing catalog. A pack is the
distribution and ownership boundary; its V3 manifest declares the public
contribution families. A folder is not an export, and a pack is not a second
project store, schema registry, or gateway command.

## Authoritative references

- [When and how to create a pack](../../../../../docs/guides/create-a-pack.md)
  is the current V3 authoring route, contribution map, visual contracts,
  scaffold command, and validation path.
- [Pack Contract](../../../../../docs/packs/contract.md) and
  [Pack Taxonomy](../../../../../docs/packs/pack-taxonomy.md) distinguish
  owner identity, contribution type, discovery metadata, and legacy output.
- [Discovery for Agents](../../../../../docs/guides/discovery-for-agents.md)
  and the [SDK reference](../../../../../docs/reference/sdk.md) document
  runtime lookup and invocation.
- [Creating Tools](../../../../../docs/guides/creating-tools.md) helps decide
  whether to compose existing work or author one focused contribution.
- The [rendering skill](../../../rendering/docs/SKILL.md) documents timeline
  use and render compatibility.
- [Live scene authoring](../../../rendering/docs/references/live-scenes-authoring.md)
  documents self-contained Runtime scene objects.

Read only the reference needed for the current choice. Use
[`pack.json`](../../../../../astrid/core/pack/schemas/v3/pack.json) and the
existing [starter source](../../../../../astrid/core/pack/cli_basic.py) instead
of copying schemas or maintaining another template here.

## Choose the contribution

1. **Existing work:** discover and inspect first. If existing contributions
   compose to satisfy the request, add no new code.
2. **Action:** put callable work in the V3 `actions` family. Declare its
   invocation, inputs, outputs, and supporting resources. The SDK selector
   `kind="action"` is the unified callable route; typed
   `executor`/`orchestrator` selectors remain available for those specific
   routes and older registrations.
3. **UI:** use `ui` for an application-hosted contribution such as an editor
   extension. Declare its entry and receiving target; keep host-specific code
   at the declared path.
4. **Rendering:** use `rendering` for renderer, planner, finalizer, or
   reusable element entries. Visual element manifests describe effects,
   animations, and transitions. This is not a separate top-level
   `visual_elements` family; do not add one.
5. **Document format:** use `documents` for a versioned document schema.
   Declare the format version and schema; keep examples and supporting data as
   declared resources.
6. **Support files:** use `resources` for runtime/package files and
   `authoring_only` for source material deliberately excluded from the
   runtime package, with a reason. A `shared` folder is a starter layout
   convention for private helpers, not a public contribution type.
7. **No new pack:** keep an experiment in its run directory until the behavior
   is useful and reusable.

Ownership and contribution type are independent. For the existing personal
collection, retain pack ID `local` and its stable references while displaying
the owner-facing name **Personal**. Do not migrate the ID just to change its
label. See the [current local manifest](../../../local/pack.yaml).

## Build a pack

Use the current V3 starter:

    python3 -m astrid.core.pack.cli new <pack_id> --starter standalone

Repeat `--role` for supported starter layouts: `action`, `ui`,
`rendering`, and `shared`. The role flags choose starter files; they do not
add a top-level manifest field. The renderer starter is not a visual-element
scaffold; for an element declaration, follow the V3 manifest shape in the
[Personal pack](../../../local/pack.yaml).

1. Choose standalone, wrapper, or nested ownership based on the source layout.
   For wrappers keep upstream unchanged and declare its dependency; for nested
   packs preserve the parent repository's install/build route.
2. Declare each public contribution in the matching `pack.yaml` family.
   Put implementation and private support under only the declared paths.
3. Author one `docs/SKILL.md` with ordinary `name` and `description`
   frontmatter; point `documentation` to that bundle. Link its references,
   templates, and assets normally rather than creating more skill exports.
4. Declare the pack's actual Python/npm/system dependencies, access needs in
   `permissions`, and named secrets as required by their V3 contracts. These
   disclosures do not by themselves enforce a sandbox or install arbitrary
   upstream code.
5. Validate and inspect the pack before using it:

       python3 -m astrid.core.pack.cli validate <path> --json
       python3 -m astrid.core.pack.cli inspect <pack_id> --pack-root <root>

Static validation checks the manifest, declared paths, skill, and entrypoints;
it does not execute code or prove runtime behavior. Use the current SDK/host
route for discovery and invocation. The starter, rather than this skill, owns
the scaffold files; focused coverage lives in
[`test_cli_scaffold_f08.py`](../../../../../tests/core/pack/test_cli_scaffold_f08.py).

## Visual source and runtime scenes

A pack-owned visual element is a trusted source/build contribution. Its
descriptor and resources are declared under `rendering` and included in the
Astrid/Reigh build and generated catalog. The element kinds are effects,
animations, and transitions; retain their owner identity and resource paths.
Do not compile TSX at runtime or invent a second catalog.

A user-authored live scene is a different object: self-contained HTML saved
and loaded through the Runtime project-object route. Its current contract
requires `assets: []`. It is not a TSX element, pack resource, or code-loading
exception. Three.js is an existing host rendering path for admitted scenes,
not a new V3 manifest family or editor pack loader.

## Implementation invariants

- Discover public entries from manifests and use the SDK; do not guess an
  export from a source-tree folder.
- Keep the pack ID as the owner namespace and preserve stable contribution
  references. A display label does not change identity.
- Keep timeline, project, media, task, and scene state under the workspace
  Runtime's authority.
- Keep dependencies, permissions, secrets, and documentation aligned with
  what the declared contributions actually use.
- Keep TSX rendering elements on the checked-out build/catalog path; keep
  self-contained HTML scenes on the Runtime object path.
- Do not add a database schema pack, SQL migration, local state authority,
  generic loader, runtime TSX compiler, or new top-level gateway family.

## Verify the result

Confirm the manifest and its declared paths pass the V3 validator, then
inspect the discovered entries and documentation pointer. For runtime changes,
use the supported host and only the checks needed to prove the behavior.
Rendering work follows the rendering-specific validation and smoke path from
the [current pack guide](../../../../../docs/guides/create-a-pack.md).

The manifestless `_core` gateway remains an existing exception. Its authored
source is `astrid/packs/_core/docs/`; it is composed by the existing skill
sync and is not another manifest-backed pack export.
