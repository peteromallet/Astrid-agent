# Pack authoring direction: actions, UI and rendering

**Status:** Design proposal captured from the user discussion, 2026-10-01. The role-based folder convention and general ownership model are the direction recorded here. Manifest syntax, UI type names and new host APIs below are illustrative, not an implemented schema or an amendment to the current pack contract.

## Current delivery boundary: live-scenes first

The user has selected the existing **Astrid Live Scenes** editor extension from the `live-code-scenes-20260930` donor worktrees as the first concrete UI integration. The pack-authoring migration includes that extension, its private support/assets and its existing editor-host/build dependencies. Keep it owned by the rendering pack, with its entry and support colocated under `ui/live-scenes/`, while final-output backends retain their rendering contracts. Reuse the existing editor SDK, generated catalog, lifecycle and project storage. No new generic component/extension platform is required.

The themes, full app tools and embedding examples below describe possible later extensions of the convention. Their hosts and app-wide UI migration are **deferred**, not completion requirements for this delivery. Admit and document only implemented receiving-host contracts; preserve current unrelated app behavior. A UI-only example can target the existing editor extension host. The authoritative execution scope is `.otto/runs/pack-authoring-convergence-20261001/plan.md` and its tasklist.

This is the durable account of the direction reached after reviewing existing packs and challenging several alternative layouts. In particular, it replaces the earlier discussion's `exports/` authoring convention with plainly named functionality folders. It does not initiate a repository migration or add work to Worker retirement.

## 1. The outcome

A person or agent opening a pack should immediately understand:

- What they can call.
- What UI the pack contributes, and where that UI belongs.
- Whether it plugs into the rendering process.
- Where to find each implementation and its supporting code.
- Which code or formats are shared, and where user-created data belongs.

The design should work for a two-file utility, a substantial integration such as VibeComfy, a complete app tool, a theme, an editor extension, and a pack combining several of these. Authors should have an obvious default location for new functionality without constructing empty folders or registering every helper.

## 2. The conceptual model

**A pack is an ownership, distribution and namespace boundary.** It can expose several related things. It need not be exclusively a frontend pack, backend pack, renderer or inference integration.

| Public functionality | Meaning | Example |
| --- | --- | --- |
| Action | Callable work with inputs and outputs | Extract audio, run a workflow, assemble a film |
| UI contribution | Something an app or interface presents or mounts | App tool, embeddable editor, viewport, panel, theme |
| Rendering contribution | Implementation consumed by the media rendering process | Three.js renderer, rendering finalizer |
| Document format | A public definition of project content the pack understands | A Three.js scene format |

**Usage documentation** is a separate concern: Markdown guides, agent skills and their examples explain how to use a pack. The `documents` category above describes formats for user-created project data; it is not the place to declare a README or `SKILL.md`. One skill can explain several actions, UI contributions and rendering operations.

An action may be small or complex and may call actions in other packs. There is no separate author-facing executor/orchestrator distinction in this proposal. The user's clarification is explicit: existing executor/orchestrator implementation details are background, not requirements for this authoring exercise. Retry, replay and parent-task lifecycle policies are separate implementation decisions, not prerequisites for selecting this structure.

An inference integration does not automatically need another public category. For example, a VibeComfy pack can expose import, edit, validate and run actions while keeping its native engine connection in supporting code. Register a selectable provider only when a real consuming host needs that public contract.

Frontend/backend describes where implementation runs, not the overall type of the pack. A pack can contain both, with public actions providing the backend operations its UI calls.

## 3. The folder convention

```text
pack/
  pack.yaml
  actions/                 # callable operations
  ui/                      # app tools, embedded interfaces, extensions, themes
  rendering/               # rendering-process integrations
  shared/                  # named implementation reused by multiple owners
  docs/                    # one authored SKILL.md and linked guides/support
```

Create only the folders actually needed. Normal language/build files, bundled assets and agent documentation can accompany them. A pack does not need an additional `src/` layer by default; necessary language packaging/build layout should retain the same visible roles rather than introduce a second organization scheme.

The folders answer ordinary navigation questions. The manifest identifies the precise public entrypoints and their contracts. Only those declarations are public; the presence of a helper in `actions/` or `ui/` does not register it.

### Where does new code go?

| New code or resource | Default location |
| --- | --- |
| An independently callable operation | `actions/<operation>.py` or its language equivalent |
| Several supporting files for that operation | `actions/<operation>/`, beside its entrypoint |
| A tool, panel or viewport | `ui/<contribution>/`, with its components, hooks and styles |
| A theme | A data file under `ui/themes/` |
| A rendering-system adapter | `rendering/<name>/` or a single file if small |
| A helper used only by one owner | In that owner's file or directory |
| Implementation actually used by multiple owners | A named file or module under `shared/` |
| A private input schema | Beside the action or UI that owns it |
| A common document schema | With the shared document implementation; referenced by the manifest if public |
| Pack’s single agent skill | `docs/SKILL.md`, with its own YAML name/description; pack.yaml points to it |
| Documentation references, examples or assets | Beside SKILL.md under `docs/`, with authored relative links |
| Human usage guide | An ordinary file under `docs/`; no skill export required |

### A file grows into a folder

Start with:

```text
actions/
  create_scene.py
```

When supporting files warrant it:

```text
actions/
  create_scene/
    run.py
    validation.py
    prompt_builder.py
    input.schema.json
```

The public action ID need not change. There is no required wrapper around a wrapper, CLI shim, or additional manifest for every helper. Tiny helpers can stay in the entrypoint file.

### Shared implementation has to earn its place

Move code to `shared/` because multiple parts actually use the same implementation, not because reuse might happen later. Use a meaningful module name, such as `shared/workflow/`, `shared/engine/` or `shared/scene/`. Do not make a miscellaneous `utils` collection the default home for code whose owner is unclear.

For example, `shared/scene/` is justified when actions, the editor and the renderer need a common definition of scene objects and evaluation. It is not a top-level category that other packs must copy. If the common implementation is one file, use one file. If only the viewport needs it, keep it with the viewport.

## 4. The manifest mirrors the public roles

The following is **proposed syntax**, not a currently valid Astrid manifest. The abbreviated input/output types express intent rather than specifying a new complete schema language. Applicable permissions, compatibility and dependency details would be incorporated using the eventual contract.

```yaml
id: threejs
name: Three.js
version: 1.0.0
description: Create, edit and render 3D scenes.

actions:
  create_scene:
    description: Create a scene in the current project.
    entry: actions/create_scene.py:run
    inputs:
      name:
        type: string
    outputs:
      scene:
        type: document_ref
        format: threejs.scene

  inspect_scene:
    description: Describe the objects and settings in a scene.
    entry: actions/inspect_scene.py:run
    inputs:
      scene:
        type: document_ref
        format: threejs.scene
    outputs:
      description:
        type: string

ui:
  studio:
    type: app_tool
    title: Three.js Studio
    entry: ui/studio/Studio.tsx:Studio

  viewport:
    type: editor_viewport
    entry: ui/viewport/Viewport.tsx:Viewport
    accepts: threejs.scene

  graphite:
    type: theme
    title: Graphite
    file: ui/themes/graphite.json

rendering:
  world:
    type: renderer
    entry: rendering/world/renderer.ts:renderer
    accepts: threejs.scene

documents:
  scene:
    version: 1
    schema: shared/scene/schema.json
```

Local names produce qualified identities such as `threejs.create_scene` and `threejs.scene`; the final contract should give declarations unambiguous identities. Each UI or rendering type corresponds to a real receiving host contract. The manifest does not create a working host merely by naming a new type.

Keep small contracts inline. Reference larger schemas or declarations beside their implementation once, rather than maintaining duplicate inventories. Helper files are imported normally and need no manifest entries. Discovery, inspection and generated documentation should derive from authoritative declarations.

`shared/` has no public manifest section. A specific shared resource can become public through an explicit declaration, as the scene schema does above. A pack-level `documents` declaration defines a format; it does not prescribe a `documents/` source folder or contain user documents.

Normal language dependency files continue to describe library installation. Cross-pack references should identify supported public actions or UI contracts, rather than deep-importing another pack's private source. The exact dependency syntax is not settled here.

## 5. Different kinds of UI packs

Yes: a pack can provide only a theme, only an editor extension, an entire tool, an embeddable video editor, or a combination. Empty action or rendering sections are unnecessary.

| UI contribution | What its host does | Typical supporting material |
| --- | --- | --- |
| Theme | Applies selected design tokens | Colours, typography and other supported token data |
| App tool | Opens the tool in the app | Page components, local state, styles, calls to actions |
| Embeddable interface | Mounts an interface inside another tool | Public embedding entrypoint and its own components |
| Editor viewport | Displays/interacts with supported content | Camera controls, picking, selection overlays |
| Editor panel/inspector | Mounts UI at a published editor extension point | Controls bound to host selection and project data |

These are host-specific contracts, not one universal component API. A theme can be a data resource; it need not have an action, process or React entrypoint. A viewport and an inspector may share implementation while exposing different interactions to their host.

### Theme-only pack

```text
graphite/
  pack.yaml
  ui/themes/graphite.json
```

```yaml
id: graphite
version: 1.0.0
ui:
  theme:
    type: theme
    title: Graphite
    file: ui/themes/graphite.json
```

### A UI extension rather than a whole tool

```text
shot_notes/
  pack.yaml
  ui/notes/
    NotesPanel.tsx
    NoteRow.tsx
    styles.css
```

Its proposed declaration might be:

```yaml
ui:
  notes:
    type: editor_panel
    target: video_editor.inspector
    entry: ui/notes/NotesPanel.tsx:NotesPanel
```

`target` illustrates a published extension point belonging to a host, not a selector into private DOM. The actual extension-point naming and contract remain design work. Notes can use shared project storage; they do not inherently require a pack-specific server or action.

### A whole video editor can itself be a pack

A video-editor pack could provide:

- An app-tool entrypoint that opens the complete editor.
- An embeddable editor entrypoint used inside another app tool.
- Public actions where independent invocation is useful.
- Published extension points for panels, viewports, controls or other concrete editor features.

It can therefore both contribute UI to the app and host UI contributions from other packs. The standalone shell and embedded editor can reuse the same implementation. No requirement follows that every tool must be extensible, or that each screen must be a separate pack.

The existing React editor's final ownership relative to the current `video_editing` pack should follow actual ownership and release needs. This document does not establish a migration or claim the general pack UI loader exists today.

## 6. Representative layouts and edge cases

### Small media operation

```text
audio_extract/
  pack.yaml
  actions/extract.py
```

```yaml
id: audio_extract
version: 1.0.0
actions:
  extract:
    description: Extract audio from a media file.
    entry: actions/extract.py:run
    inputs:
      source: {type: media_ref}
    outputs:
      audio: {type: media_ref}
```

Two files. No provider descriptor, helper module or empty category directories.

### A composed action

```text
footage_summary/
  pack.yaml
  actions/make_summary/
    run.py
    select_moments.py
    build_timeline.py
    input.schema.json
```

`make_summary` can call `understanding.transcribe`, use its private selection and timeline helpers, and call `rendering.render`. The private intermediate steps do not become actions merely because the pipeline has steps. Expose one only when independent invocation is useful. This uses the same action category as a simple file transformation.

### VibeComfy and supporting inference code

```text
vibecomfy/
  pack.yaml
  actions/
    import_workflow.py
    inspect_workflow.py
    validate_workflow.py
    run_workflow/
      run.py
      stage_inputs.py
      collect_outputs.py
    enhance_video/
      run.py
      build_workflow.py
  shared/
    workflow/
      document.py
      validation.py
    engine/
      connection.py
      driver.py
```

Workflow inspection and validation share workflow handling. Run and enhancement share engine integration. A public validation action and a run action can both use the same private validation function without creating another child task for that helper call.

The native VibeComfy/ComfyUI implementation remains its dependency. A completed action does not by definition close its engine session. The host supplies shared session ownership; the pack connects to the native engine, which owns its model loading, cache and native workflow behavior. Warm-session implementation is separate from folder naming and is not proven by this illustration.

This example reorganizes the responsibilities conceptually; it does not authorize porting retired Worker generation features or copying upstream engine internals into Astrid.

### A UI tool with backend operations

```text
transcript_review/
  pack.yaml
  actions/
    transcribe/
      run.py
      prepare_audio.py
    apply_corrections.py
  ui/reviewer/
    Reviewer.tsx
    TranscriptRow.tsx
    AudioPlayer.tsx
    useSelection.ts
    styles.css
  shared/transcript/
    schema.json
```

The selection hook belongs with the reviewer. Audio preparation belongs with transcription. Independently callable transcription and correction work belongs in `actions/`, even though the UI invokes it. The transcript schema is shared because the UI and backend exchange the same document. They need not share all Python/TypeScript implementation code.

One user-facing feature may therefore span `ui/` and `actions/`. That is a deliberate separation of two usable surfaces, with the manifest exposing their relationship; UI-only helpers should not be scattered across those surfaces.

### Expanded Three.js pack

This is an illustration of a future broader pack. The current Three.js implementation is a limited text/background renderer inside the rendering pack, not the general world editor shown below.

```text
threejs/
  pack.yaml
  actions/
    create_scene.py
    inspect_scene.py
  ui/
    studio/
      Studio.tsx
      SceneBrowser.tsx
    viewport/
      Viewport.tsx
      CameraControls.ts
      SelectionOverlay.tsx
    themes/
      graphite.json
  rendering/world/
    renderer.ts
    capture.ts
  shared/scene/
    schema.json
    evaluate.ts
    objects.ts
```

Camera controls belong to the viewport; frame capture belongs to the renderer. Scene schema/evaluation/object construction are shared where both preview and export need them. The common implementation has one owner without implying that browser and render process share a live WebGL instance.

Another location-planning tool can depend on the Three.js pack's published actions and embedding surface, and pass a scene reference to it. It should not copy the scene implementation or import private files such as `shared/scene/objects.ts`.

## 7. Rendering, interactive preview and project data

### Rendering is a host integration

“Render this timeline” is an action. A renderer is an implementation selected by the rendering system to produce output according to its supported contract. Three.js is one example; an Unreal Engine integration could be another if it implemented the relevant host contract.

```text
Public rendering action
  → rendering host selects a compatible renderer
  → renderer produces output
```

An interactive viewport handles navigation, selection and feedback. Media rendering evaluates content at specified times and produces frames/files. They should share scene meaning where appropriate, with their respective controls and capture code kept local.

An action that creates a thumbnail is still an action; producing pixels alone does not make code a registered rendering backend. Existing rendering-specific roles such as finalizers, planners and elements retain their actual host contracts when needed. This proposal does not force them into UI or invent a complete replacement rendering protocol.

### Pack files are not user documents

| Responsibility | Owner |
| --- | --- |
| Scene format, validation and visual meaning | Pack |
| UI and renderer implementations | Pack |
| A particular user's scene | User's project |
| Storage of documents, media and generated outputs | Astrid runtime/project services |
| Bundled sample scene | Installed pack resource |

Shared project storage should allow a standalone tool and an embedded editor to open the same document reference. A document identifies its format and format version; a pack release version is a separate concept. Saving a scene does not modify its installed pack. Removing or updating a pack should not delete the user's project content.

The exact project document API is future implementation detail. The current direction is one shared storage authority, not a database/schema registry invented independently by each pack.

## 8. Guardrails against ceremony and confusion

- No mandatory empty folders or sections.
- No `exports/` catch-all as the author-facing layout: use descriptive role folders.
- No separate public executor/orchestrator choice for callable work.
- No automatic inference-provider category for every engine or API client.
- No public registration of ordinary supporting functions.
- No movement into `shared/` based only on hypothetical reuse.
- No duplicate contract inventories across manifest, helper manifests and generated docs.
- No assumption that a UI theme, renderer and callable action share one execution API.
- No inference that proposed YAML establishes a working loader or service.
- No requirement to build a universal workflow/scene language, scheduler or dynamic plugin marketplace.

## 9. What is decided here, and what remains

The intended direction is: plainly named functionality folders; supporting code beside its owner; named shared modules only for actual reuse; a manifest mirroring public roles; UI-only and mixed packs; callable composition; rendering host integration; and project-owned user data.

Details still to resolve against concrete implementations include the exact manifest schema and host type names, public UI embedding and extension contracts, build/package projection, document schema/resource references through existing project APIs, and appropriate compatibility/resource declarations. These should refine the convention without turning private helpers into new public categories.

A small implementation sequence, when implementation is requested, would be:

1. Settle the declaration shapes for a minimal action and one real UI host.
2. Provide authoring examples/scaffolding that produce the convention directly.
3. Connect discovery to those declarations and prove a small action plus a UI-only contribution.
4. Exercise cross-pack UI reuse and a real rendering contribution against their concrete hosts.
5. Apply the convention to existing packs in scoped changes, without importing retired Worker functionality.

This is not a new execution plan or authorization to perform those steps now. Existing executor/orchestrator runtime behavior does not constrain this design discussion; any eventual migration semantics should be scoped separately when needed.

## 10. Relationship to earlier documents

- [Pack-hosted tools and interface contributions](pack-hosted-interfaces.md) describes the broader app/interface extension direction. This document supplies the later authoring/layout convention and concrete examples. That proposal's runtime, build and host questions remain relevant; its old capability labels are not a commitment to preserve them in this new direction.
- [Current pack contract](../packs/contract.md) remains the documentation for implemented behavior. The illustrative manifests above do not replace it.
- The earlier ten-pack review and independent Astra opinion are background evidence under `.otto/runs/reigh-worker-retirement-20260930/findings/pack-structure-20261001/`. The later user discussion rejected the opinion's `exports/` folder convention. This document records the subsequent direction, rather than copying that earlier recommendation unchanged.

Read this document as the design discussion's current durable summary; use the current contract and source when authoring executable packs today.

## 11. External repositories and skill-led authoring

The role-folder convention governs the **Astrid integration's pack root**. It should not require an existing library or application to reorganize its entire source tree.

There are three authoring routes:

1. **Create a new pack:** start with `pack.yaml` and a small action or UI entrypoint using the convention above.
2. **Wrap an unchanged external repo:** a separate pack declares its dependency and supplies action/UI adapters calling that project's supported API, executable or embedding surface. Upstream source remains unchanged.
3. **Let an existing repo also publish a pack:** add a nested integration directory, for example:

   ```text
   existing_repo/
     src/                         # existing implementation
     tests/
     pyproject.toml               # or its normal language build metadata
     integrations/astrid/
       pack.yaml
       actions/
         clean_image.py           # adapter using the repo's public library
   ```

   Source setup selects that subdirectory as the pack root. The existing source declaration already has repository, revision and `pack_subpath` fields. The repo's ordinary build/install supplies its library or executable; copying the nested pack directory alone does not magically install the parent project.

Declared local entry/resource paths stay within the pack root. Use normal package dependencies and supported build boundaries to reach upstream code, not arbitrary `../../src/` manifest references. A clean API/CLI or embeddable component may need only a small adapter; tightly coupled standalone apps or engine lifecycles can require substantive integration. There is no promise to expose an arbitrary repository automatically.

### Documentation and examples are the onboarding process

Update the repository's [Pack Builder skill](../../astrid/packs/_core/skill/pack-builder/SKILL.md) and [Creating Packs guide](../packs/creating-packs.md) when the new format is implemented. The installed personal skill copy should receive that source update through the normal distribution path.

The skill should explain: choose the route; choose the public surface; place local support; declare inputs/outputs and dependencies; validate; discover; then invoke or mount it. Link to one maintained runnable starter for each of the three routes, plus the planned UI-only and mixed examples. Keep large schemas in their authoritative files rather than copying divergent versions into the skill.

A new onboarding wizard or generic repository-conversion service is unnecessary. Extend the existing scaffold/validator, provide concrete examples, and exercise the documented authoring path. The execution project's F08 (templates/scaffold), B07 (source skill/guides) and I03 (authoring proof) own these outcomes.

## 12. Core and packs: plugin-first ownership

The shared runtime owns the rules and services every pack must use: manifest admission and discovery, resource confinement, invocation and task/session custody, permissions/provenance, project document storage, and the receiving host contracts. Packs own concrete creative behavior and integrations: provider-specific request/model translation, workflow processing, tools and views, renderer implementations, and the meaning/schemas of their documents. The editor owns its editor-specific runtime and extension host; those are not automatically generic app-core services.

A new ordinary action or supported UI contribution should work through declarations and existing public hosts without adding its pack name to central dispatch or hand-maintained tool tables. Prefer existing registries and protocols; introduce a new hook only when an actual supported integration needs it. A role-folder rename is not an ownership repair if core still reaches into the same private implementation under a new path.

This principle does not require moving all runtime code into packs. Security/custody checks remain runtime-owned even when a pack supplies an adapter. It also does not authorize a whole-core rewrite within the authoring migration: identify current couplings, fix the ones the new contract/migration touches through the smallest supported boundary, and give larger engine/session/provider extractions an explicit owner and scope. Preserve live callers and behavior before retiring wrappers or duplicated registries. There is no new document store or general plugin framework implied by the convention.

## 13. Documentation: one directly authored skill per pack

Write a normal skill document. One skill per pack; additional guides are linked supporting documents.

```text
media/
  pack.yaml
  actions/...
  docs/
    SKILL.md
    references/...
    templates/...
```

SKILL.md owns the skill metadata and instructions:

```markdown
---
name: media
description: Extract clips and process media with the media pack.
---

# Media

Use this pack to extract clips and process media.
```

The planned manifest keeps the existing declaration shape, with the new docs path:

```yaml
documentation:
  kind: skill
  path: docs/SKILL.md
```

Sync discovers the declared file, reads its existing frontmatter and uses the existing skill-directory linking/view mechanism. References, templates and other required support remain beside the skill and ship with it. No generated skill body or duplicated metadata in pack.yaml; no Markdown rebasing or new documentation snapshot system. The migration updates discovery, package-data and affected links together.

Existing _core gateway/internal-guide composition remains a bounded existing mechanism, with its manifestless shell retained. Former independently synced component skills become ordinary linked guidance under the owning pack; retire their separate managed installs explicitly while preserving content and pack opt-outs. VibeComfy SkillSinker and poms_skills remain separate.

Runtime documents/schema declarations are separate from usage documentation. This direct-skill design supersedes the prior D03 generated-document and multi-export proposals; it is planned migration work, not a claim that paths have already moved.
