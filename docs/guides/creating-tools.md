# Creating Tools

Use this guide when Astrid is missing reusable behavior. Start with the V3
[pack guide](create-a-pack.md) for manifest shape and the source paths of
existing examples.

## Operating Level

Start with the highest-level existing action that fits the request. The V3
manifest has one `actions` family for callable work, and the SDK's normal
selector is `kind="action"`. For example, use the existing composed hype
action instead of wiring pipeline stages by hand:

```python
import astrid.sdk as sdk
result = sdk.invoke(
    "video_editing.hype",
    kind="action",
    inputs={"video": "source.mp4", "brief": "brief.txt"},
    project="demo",
)
```

Astrid is not session-gated and has no `setup` command: product commands run
through the workspace runtime (`python3 -m astrid doctor --json` first, then
the `projects`/`timelines`/`media`/`tasks`/`runs` families), and pack
capabilities run through the SDK (`astrid.sdk.discover` / `get_capability` /
`invoke`). The runtime owns durable state; tool authors must use these public
surfaces rather than opening a database or writing a parallel state store.

Do not chain pipeline internals by hand unless debugging one specific stage.
Source-analysis actions intentionally pass file artifacts such as
transcripts, scenes, quote candidates, pools, timelines, and assets. Those files
make runs resumable and auditable, but they are not the right interface for a
creative request like "make a video about AI". Use the hype action or compose
existing actions into a new callable workflow when needed.

Current start points:

```python
# Source-backed edit
sdk.invoke("video_editing.hype", kind="action", inputs={"video": "source.mp4", "brief": "brief.txt"}, project="demo")

# Audio-backed edit
sdk.invoke("video_editing.hype", kind="action", inputs={"audio": "voiceover.wav", "brief": "brief.txt"}, project="demo")

# Pure-generative edit from an existing brief
sdk.invoke("video_editing.hype", kind="action", inputs={"brief": "examples/briefs/cinematic.txt", "target_duration": 15}, project="demo")
```

If the user gives a topic instead of a brief, create or use a brief-generation
action, then compose it with the existing workflow action. Do not fake source
media just to satisfy a source-video path.

## Build Order

Before adding anything, follow this order. Move to the next step only when the
previous one cannot satisfy the request.

1. **Try existing actions first.** Run `astrid.sdk.discover()` /
   `astrid.sdk.get_capability(<id>)` to search the registry, then inspect the
   likely candidates. The [minimal teaching pack](../../examples/packs/minimal)
   shows a direct action and a second action that composes it. Add no new code
   when those actions already satisfy the request.
2. **Add one focused action if needed.** Give it a clear unit of work, declared
   inputs and outputs, and only the support it needs. Keep workflow decisions
   in a composed action rather than duplicating behavior from an existing one.
3. **Compose through declared actions.** The manifest graph declares child
   actions/orchestrators; call admitted child work through the SDK. Existing
   `executor` and `orchestrator` capability types and selector values remain
   supported for typed routes, but V3 authors declare callable entries under
   `actions`.

When a composed action needs child work, call the capability through
`astrid.sdk.invoke`: the invocation is admitted into the kernel as a run +
task and executed through the kernel lifecycle (admit → claim → start →
execute → complete|fail), and the finalize-time `run.json` projection
lands under the project's `runs/<run-id>/` tree. Code that needs to drive
an admitted task with its own loop implements the
`astrid.core.task_executor` `TaskHandler` protocol instead of hand-rolling
state tracking. The legacy task-mode plan schema (`plan.json`,
`plan_initialized`, `plan_mutated`, step adapters, `repeat` loops,
`remote-artifact` leaves) was retired with the task-mode runtime and must
not be authored.

Anti-pattern: a single action `run.py` that opens HTTP sockets, parses model
output, downloads files, and assembles grids — all inline. That hides several
reusable operations in one entry. Split work only when the pieces have a useful
independent contract; keep workflow composition in an action.

## Decision Rule

Create an **action** for callable work. It may do one focused operation or
compose existing actions into a workflow. Keep child operations independently
useful and declare the graph; do not add an extra wrapper when an existing
action already fits. The `minimal` teaching pack demonstrates both shapes.

An SDK **executor** or **orchestrator** is a supported typed callable route in
existing registrations and some focused backend contracts. It is not a
separate V3 manifest family. Prefer `kind="action"` for new V3 callable use;
use typed selectors when a documented route requires them.

Create an **editor UI contribution** when the missing behavior is an
interactive host surface. A working UI example is the
[live-scene editor entry](../../astrid/packs/rendering/ui/live-scenes/extension.tsx);
the prepared scene itself is project state, not installed pack code.

Create an **element** when the missing capability is a reusable render building
block consumed by a timeline. Effects, animations, and transitions are
elements. If the user needs an editable visual primitive, create or edit an
element in its owning source pack instead of hard-coding behavior in an action.

Put private helpers with the owning action or contribution; use a shared
library only when multiple owners need a stable common API. Hype/editing
concepts belong with their owner under `astrid/packs/editorial/hype`; generic
plumbing belongs under `astrid/core/util`.

Create a **renderer**, **planner**, or **finalizer** only when extending the
render backend. In a V3 pack, declare the entry in `rendering` with its
`type` and manifest `path`; renderer/planner/finalizer manifests and commands
follow the separate [render backend protocol](../contracts/render-backend-v1.md).
This is advanced host work. For a reusable timeline visual, declare
`type: element` instead. The pack guide describes the V3 rendering declaration
and starter; do not copy the older `extensions.rendering.*` shape into a V3
`pack.yaml`.

For a one-off experiment, keep outputs and scratch files under `runs/`. Do not
create a public action, UI contribution, or element unless the behavior should
be discoverable and reusable.

## Common Friction Points

**Too many required file paths.** Those paths may be the direct action's
artifact contract. Solve a repeated workflow need by composing actions or adding
a focused helper action for a missing artifact. Only add literal/stdin
conveniences when direct action use is itself the product surface.

**Pool building rejects abstract or dialogue-light sources.** The source-video
hype path expects usable visual and dialogue candidates. If the goal is
abstract or purely generative, use the pure-generative path. If source-backed
abstract editing should be reusable, add an explicit composed-action path or a
focused action change with tests rather than hand-editing triage and quote
JSON to force a pool.

**No brief file exists.** Briefs are first-class input artifacts today. Use
`examples/briefs/` as samples. If users repeatedly start from a topic, add a
brief-generation action and compose it with the existing workflow action.

**Render is missing assets.** Rendering always needs a timeline. Pass the
registry created by cut when the timeline references media assets. An
asset-free timeline may omit it; the facade supplies an empty registry. Do not
skip cut in the normal media pipeline unless its required timeline/registry
artifacts already exist.

**No one-command topic creation.** The current one-command path starts from a
brief file. A reusable topic-first path can be a composed action that creates
the brief and delegates to the existing video workflow.

## Required Formats

New packs use the V3 manifest at `astrid/packs/<pack>/pack.yaml`. Its
`actions`, `ui`, `rendering`, `documents`, `resources`, and
`authoring_only` fields are the public contribution/source declarations; a
directory is not an implicit export. The V3 schema and authoring flow are in
[When and how to create a pack](create-a-pack.md) and the
[current contract](../packs/contract.md).

The contribution family, not the folder name, determines the role:

- `actions` declares callable work. The SDK's unified selector is
  `kind="action"`; `executor` and `orchestrator` remain supported typed
  selectors for those routes and existing registrations.
- `ui` declares a host-mounted UI contribution and its entry/target.
- `rendering` declares renderer, planner, finalizer, or element entries.
  Element manifests use `effect`, `animation`, or `transition` and retain
  their owning pack identity.
- `documents` declares versioned document formats. Use `resources` for
  packaged support files and `authoring_only` for excluded authoring material.

For the existing one-user collection, keep the stable pack ID `local` and its
references. The display name **Personal** communicates ownership; it does not
change contribution IDs. The source is
[astrid/packs/local/pack.yaml](../../astrid/packs/local/pack.yaml).

## Templates

Use the supported V3 starter and edit the generated declaration and files:

    python3 -m astrid.core.pack.cli new my_pack --starter standalone --role action
    python3 -m astrid.core.pack.cli validate my_pack --json
    python3 -m astrid.core.pack.cli inspect my_pack --pack-root .

The role options scaffold action, UI, renderer, and shared-support examples.
They do not create manifest families, and the renderer starter does not create
an element declaration. For an effect, animation, or transition, follow the
current `rendering` entry shape in
[the Personal pack](../../astrid/packs/local/pack.yaml) and its element
manifest. There is no element role flag or separate `visual_elements` family.

The older `docs/templates/executor/`, `docs/templates/orchestrator/`, and
`docs/templates/element/` examples remain for legacy compatibility and
migration. Do not copy them as the V3 starter source.

Then run:

```bash
python3 -m astrid doctor
python3 -m astrid projects list --json
```

Use the SDK (`astrid.sdk.discover()` / `get_capability`) to inspect the thing
you created instead of guessing from ids alone.

## Review Checklist

- The V3 declaration and paths pass
  `python3 -m astrid.core.pack.cli validate <path> --json`.
- The matching manifest section, entry paths, resources, dependencies,
  permissions, and authored documentation describe the behavior it provides.
- The SDK/host discovers callable work through `kind="action"`, or exposes a
  rendering, UI, or document contribution through its declared host surface.
  Visual elements are inspected with `get_capability(..., kind="element")`
  and selected by timelines; they are not invoked as actions.
- Behavior changes are checked through their supported host/runtime route.
  Use focused tests for the identity or behavior that could break; manifest
  validation alone is not a runtime proof.

## Related Guides

- [discovery-for-agents.md](discovery-for-agents.md) — How agents discover
  capabilities via the SDK.
- [debugging.md](debugging.md) — Debugging renderers: static validation, smoke
  tests, the failure replay bundle, and SDK-level moves.

## Future Work

- **Remote registry** — Publishing and discovering packs from outside the
  repository.
- **Dependency isolation** — Per-pack isolated dependency resolution.
