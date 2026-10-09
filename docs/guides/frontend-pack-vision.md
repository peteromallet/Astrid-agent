# Tools, Widgets, and Actions: future product model

**Status: intended product direction, not current V3 schema or runtime
functionality.** Keep using the current V3 manifest and SDK until a future
contract is designed and implemented. Do not write `type: tool` or
`type: widget` into `pack.yaml`, or invent new SDK selectors.

The names describe different roles. They do not prescribe size, placement, or
whether state is temporary or saved:

| Concept | Role | Possible placement |
|---|---|---|
| **Tool** | A complete workspace for doing a kind of work. Preserve Reigh's existing “Tools” terminology for these user-facing workspaces. | A top-level experience a person opens, such as a video editor. It can compose reusable interface features and callable behavior. |
| **Widget** | A focused, reusable interface feature with an API suited to its host. A Widget may use host services or state and may request callable Actions. Each Widget remains owned by its pack. | A Tool or another supported host surface can embed it; Widgets can be shared across Tools. A host may also allow one to open on its own. Panels, dialogs, sidebars, and toolbar slots are placements, not required Widget types. |
| **Action** | A callable backend operation, independent of how a person interacts with it. | Invoked through an existing host capability route; a Widget may request an Action when the user asks it to do work. An Action does not own a screen. |

This separates a contribution's role from where it appears and how long its
state lasts. A button, form field, or layout primitive remains a private UI
component unless it has a useful reusable feature contract. The broader UI
umbrella can include other host integrations too; not every visible UI surface
has to become a Widget. There is no required Tool → Widget → view registration
chain. Existing editor extensions remain a host integration mechanism: a
future host adapter may expose reusable Widgets, or may register nonvisual
commands and shortcuts.

A temporary or saved view is a composition instance: a selection and
configuration of reusable interface features with the data they display. It
can be useful as project state without becoming a new pack. Packs own reusable
definitions and assets; the workspace Runtime owns project data and any
persisted composition state. A novel, user-authored interactive page would
need an explicit page host; the current pack UI contract does not establish a
general host for authored pages. Saving a view does not itself define that
capability.

## How this relates to current V3

Today, V3 declares callable work under `actions`, host UI under `ui` (the
current schema supports editor entries), and timeline visuals or render
infrastructure under `rendering`. A UI entry is not yet a pack-level Widget
contract, and an editor extension need not be modeled as one. Timeline effects,
animations, and transitions remain rendering elements, not interface Widgets.
The current frontend uses Reigh Tools and editor slots, panels, and dialogs;
the proposed Tool/Widget contract and host adapter are not implemented.

Use [the current pack guide](create-a-pack.md) and
[V3 taxonomy](../packs/pack-taxonomy.md) for authoring now. This page records
the future naming direction without changing those contracts.
