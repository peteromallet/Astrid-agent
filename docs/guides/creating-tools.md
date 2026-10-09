# Creating Tools

Use this guide when Astrid is missing a capability.

## Operating Level

Start with the highest-level command that fits the user request. For normal
video creation, run an orchestrator through the SDK instead of chaining
internal executors by hand:

```python
import astrid.sdk as sdk
result = sdk.invoke(
    "video_editing.hype",
    kind="orchestrator",
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

Do not chain pipeline internals by hand unless you are debugging one specific
stage. Source-analysis executors intentionally pass file artifacts such as
transcripts, scenes, quote candidates, pools, timelines, and assets. Those files
make runs resumable and auditable, but they are not the right interface for a
creative request like "make a video about AI". Use the hype orchestrator or add
a new orchestrator for that workflow.

Current start points:

```python
# Source-backed edit
sdk.invoke("video_editing.hype", kind="orchestrator", inputs={"video": "source.mp4", "brief": "brief.txt"}, project="demo")

# Audio-backed edit
sdk.invoke("video_editing.hype", kind="orchestrator", inputs={"audio": "voiceover.wav", "brief": "brief.txt"}, project="demo")

# Pure-generative edit from an existing brief
sdk.invoke("video_editing.hype", kind="orchestrator", inputs={"brief": "examples/briefs/cinematic.txt", "target_duration": 15}, project="demo")
```

If the user gives a topic instead of a brief, create or use a brief-generation
executor, then coordinate it from an orchestrator. Do not fake source media just
to satisfy a source-video path.

## Build Order

Before adding anything, follow this order. Move to the next step only when the
previous one cannot satisfy the request.

1. **Try to compose existing executors.** Run `astrid.sdk.discover()` /
   `astrid.sdk.get_capability(<id>)` to search the registry, then inspect the
   likely candidates. If a workflow can be built by wiring existing executors
   together, write *only* an orchestrator that calls them. Do not duplicate
   logic that already lives in an executor.
2. **Create the missing executors.** Each new executor must do exactly one
   concrete unit of work — independently runnable, inspectable, testable. Keep
   it narrow: one network call, one transformation, one artifact in / one
   artifact out. Workflow shape, retries-across-stages, and conditional
   branching belong in the orchestrator, not the executor.
3. **Write the orchestrator that composes them.** It calls the executors
   (existing + new) and may call other orchestrators. Executors must not call
   orchestrators.

When an orchestrator needs child work, call the capability through
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

Anti-pattern: a single orchestrator `run.py` that opens HTTP sockets, parses
model output, downloads files, and assembles grids — all inline. That is three
or four executors hiding in a trench coat. Split them out so each piece is
discoverable, reusable, and individually testable.

## Decision Rule

Create an **executor** when the missing capability performs one concrete unit of
work. It should be independently runnable, inspectable, and testable. Examples:
fetch source data, render a timeline, upload a video, inspect audio, build a
sprite sheet, generate a brief from a topic, or transform one artifact into
another.

Create an **orchestrator** when the missing capability coordinates a workflow.
It should call or plan child executors/orchestrators and keep business flow out
of individual tool implementations. Examples: hype pipeline, event-talk
workflow, thumbnail workflow, topic-to-video creation, or an understanding
dispatcher.

Create an **element** when the missing capability is a reusable render building
block consumed by timeline JSON. Effects, animations, and transitions are
elements. If the user needs an editable visual primitive, create or edit an
element in its owning source pack instead of hard-coding behavior in an executor.

Create a **shared library** only when the code has no public runtime of its own.
Hype/editing concepts belong with the owning editorial pack under
`astrid/packs/editorial/hype`. Generic
plumbing belongs under `astrid/core/util`. Executor-specific helpers belong
inside that executor's optional `src/` package.

Create a **renderer**, **planner**, or **finalizer** only when extending the
timeline render backend layer. These are protocol commands registered by a
pack through `extensions.rendering.renderers`, `.planners`, or `.finalizers`;
they are not public executor kinds. A renderer produces one validated primary
video, a planner assigns exact frame windows to renderers, and a finalizer
normalizes/assembles planned artifacts. Keep `rendering.render` as the public
facade and follow `docs/contracts/render-backend-v1.md`; do not import a
concrete backend or add engine branches to the facade. To start, scaffold the
canonical four-file renderer pack with
`python3 -m astrid.core.rendering.cli create <name> <dest>`, then walk the
golden path (implement `render.py` → generated test → `renderers validate` →
validated pack source → `renderers smoke` → provenance sidecar) described in
[render-backend-v1.md](../contracts/render-backend-v1.md#renderer-author-golden-path).
The `renderers`/`packs` verbs live on the internal module CLIs
(`python3 -m astrid.core.rendering.cli`, `python3 -m astrid.core.pack.cli`),
not on the eight-family gateway.

For a one-off experiment, keep outputs and scratch files under `runs/`. Do not
create a public executor, orchestrator, or element unless the behavior should be
discoverable and reusable.

## Common Friction Points

**Too many required file paths.** This is expected for low-level executors.
Those paths are the artifact contract. Solve it by using an orchestrator, adding
a small helper executor for the missing artifact, or adding an orchestrator that
owns the whole flow. Only add literal/stdin conveniences when direct executor
use is itself the product surface.

**Pool building rejects abstract or dialogue-light sources.** The source-video
hype path expects usable visual and dialogue candidates. If the goal is
abstract or purely generative, use the pure-generative path. If source-backed
abstract editing should be reusable, add an explicit orchestrator mode or a
focused executor change with tests rather than hand-editing triage and quote
JSON to force a pool.

**No brief file exists.** Briefs are first-class input artifacts today. Use
`examples/briefs/` as samples. If the user repeatedly asks from a topic, add a
`generation.generate_brief` executor and call it from a topic-to-video
orchestrator.

**Render is missing assets.** Rendering always needs a timeline. Pass the
registry created by cut when the timeline references media assets. An
asset-free timeline may omit it; the facade supplies an empty registry. Do not
skip cut in the normal media pipeline unless its required timeline/registry
artifacts already exist.

**No one-command topic creation.** The current one-command path starts from a
brief file. A topic-only command should be an orchestrator that provisions the
brief and then delegates to the existing hype or render flow.

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
