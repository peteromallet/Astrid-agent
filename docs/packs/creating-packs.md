# Creating Astrid Packs

This page preserves the v1 pack protocol and reference material for existing
packs, including executor, orchestrator, element, and rendering-extension
manifests.

> **New-pack route:** Start with [When and how to create a pack](../guides/create-a-pack.md).
> That page and the [Pack Builder skill](../../astrid/packs/_core/docs/pack-builder/SKILL.md)
> describe the implemented v3 convention: one pack, public declarations in
> `pack.yaml`, role folders only where needed, and one authored `docs/SKILL.md`.
> **Legacy warning:** the commands, taxonomy fields, schemas, permission
> examples, and `skill/`/component-root layouts below describe V1 migration
> material. They are not instructions for creating a V3 pack. V3 uses
> [pack.json](../../astrid/core/pack/schemas/v3/pack.json) and the guide above.

For current V3 pack ownership, contribution types, visual elements, and stable
references, use the [current contract](contract.md). Keep the V1 sections here
only when maintaining or migrating an existing V1 pack.

V3 `documents` declarations describe versioned document formats and schemas;
they do not create a database schema pack or a second product-state store. Do
not add `schema-pack.yaml`, SQL migrations, or a local schema registry.
Product state remains under the workspace Runtime contract.

## Legacy v1 quick start

```bash
# 1. Scaffold a new pack
python3 -m astrid.core.pack.cli new my_video_tools

# 2. Enter the pack directory
cd my_video_tools

# 3. Add an executor — create `executors/transcribe/` with `executor.yaml`
#    and `run.py` following the layout below (there is no scaffold verb for
#    individual components; `packs validate` checks the result).

# 4. Add an orchestrator — create `orchestrators/make_highlight_reel/` with
#    `orchestrator.yaml` and `run.py` the same way.

# 5. Validate everything
python3 -m astrid.core.pack.cli validate .
# valid: /path/to/my_video_tools
```

## Legacy v1 repository shape

A pack is a directory with a `pack.yaml` manifest at its root. The
example below shows the canonical layout:

```text
my_video_tools/
  pack.yaml            # Pack manifest (required)
  README.md            # Human-facing docs
  skill/
    SKILL.md           # Agent-facing skill guidance
  executors/
    transcribe/
      executor.yaml    # Executor manifest (required)
      run.py           # Runtime entrypoint (required)
      STAGE.md         # Component staging notes
  orchestrators/
    make_highlight_reel/
      orchestrator.yaml # Orchestrator manifest (required)
      run.py            # Runtime entrypoint (required)
      STAGE.md          # Component staging notes
  elements/
    ...                # Optional element components
  rendering/           # Optional protocol-v1 implementations
    renderer.yaml
    run.py
```

The exact layout is declared by `pack.yaml`, not guessed by scanning
the repository. Runtime discovery now reads the same `content` roots that
`packs validate` checks. Legacy flat packs without `schema_version` are still
supported, but new packs should use the canonical `executors/`,
`orchestrators/`, and `elements/` roots declared in `pack.yaml`.

Pack discovery commands:

```bash
# List all discovered packs (grouped by taxonomy domain in plain text)
python3 -m astrid.core.pack.cli list
python3 -m astrid.core.pack.cli list --json

# Filter by taxonomy fields
python3 -m astrid.core.pack.cli list --domain system
python3 -m astrid.core.pack.cli list --origin builtin
python3 -m astrid.core.pack.cli list --stability stable
python3 -m astrid.core.pack.cli list --install-tier core

# Filter by category (metadata.category only — not a taxonomy filter)
python3 -m astrid.core.pack.cli list --category media
python3 -m astrid.core.pack.cli list --status stub
python3 -m astrid.core.pack.cli list --visibility hidden

# Include packs marked visibility: hidden (excluded by default)
python3 -m astrid.core.pack.cli list --show-hidden

# Inspect a specific pack (always includes hidden packs)
python3 -m astrid.core.pack.cli inspect my_video_tools
python3 -m astrid.core.pack.cli inspect my_video_tools --json

# Validate every discovered pack and show effective status (grouped by domain)
python3 -m astrid.core.pack.cli status
python3 -m astrid.core.pack.cli status --json
python3 -m astrid.core.pack.cli status --show-hidden
```

The pack CLI is an internal module surface; `packs` is intentionally not a
ninth top-level gateway family. `list`, `status`, and `inspect` include
additional pack collections from `ASTRID_PACKS_PATH` (colon-separated) or an
explicit repeatable `--pack-root PATH` flag. These scopes match SDK discovery
and render invocation scope; they do not modify the repository's local pack.

`packs status` annotates packs whose `agent.purpose` is
`"TODO: describe what this pack is for"` with an effective status of
`stub`. This annotation is runtime-only — no manifest files are
modified. The `--show-hidden` flag includes packs with
`visibility: hidden` (excluded by default from both `list` and
`status`).

## Legacy v1 manifests and protocol

Every pack component has a YAML manifest that declares its identity,
contract, and runtime requirements. The manifest schemas are published
as JSON Schema documents in the repository:

| Manifest | Schema |
|---|---|
| `pack.yaml` | `astrid/core/pack/schemas/v1/pack.json` |
| `executor.yaml` | `astrid/core/pack/schemas/v1/executor.json` |
| `orchestrator.yaml` | `astrid/core/pack/schemas/v1/orchestrator.json` |
| `element.yaml` | `astrid/core/pack/schemas/v1/element.json` |
| renderer manifest | `astrid/core/rendering/schemas/v1/renderer-manifest.json` |
| planner manifest | `astrid/core/rendering/schemas/v1/planner-manifest.json` |
| finalizer manifest | `astrid/core/rendering/schemas/v1/finalizer-manifest.json` |

Shared constraints (id patterns, version format, runtime shape, etc.)
are defined in `astrid/core/pack/schemas/v1/_defs.json`.

All v1 manifests require a `schema_version: 1` field. Validation
rejects unknown schema versions with a clear error message. This
contract allows the schemas to evolve without breaking existing packs.

### Pack Manifest (`pack.yaml`)

The pack manifest declares:

- **Identity**: `id`, `name`, `version`, `description`.
- **Taxonomy**: `origin`, `install_tier`, `pack_type`, `domain`, `stability`,
  `support` — six fields that classify the pack for discovery, filtering, and
  grouping. See [pack-taxonomy.md](pack-taxonomy.md) for the full vocabulary.
- **Content roots**: where to find executor, orchestrator, and element
  manifests (e.g., `executors: executors`).
- **Agent instructions**: `purpose`, `normal_entrypoints`,
  `do_not_use_for`, `required_context`.
- **Documentation references**: paths to `README.md`, `skill/SKILL.md`, etc.
- **Aliases**: alternate public ids that resolve to canonical executor,
  orchestrator, or rendering capabilities in this pack. They are declared in
  the pack manifest and never select a shadow source.
- **Extensions**: optional pack-owned registries, including timeline renderers,
  planners, and finalizers under `extensions.rendering`.

Refer to `pack.json` for the full field list and constraints.

### Permissions (`pack.yaml`)

The pack manifest may declare a `permissions` array. Each permission object
describes a capability domain the pack needs. Permissions are **disclosure
metadata only** in v1 — they are not enforced at runtime, do not configure
sandboxing, and do not restrict what the pack can do.

#### Permission Fields

Each permission object requires:

| Field | Required | Type | Description |
|---|---|---|---|
| `id` | Yes | string | One of six approved permission IDs (see below) |
| `reason` | Yes | string (non-blank) | Human explanation of why the pack needs this permission |
| `access` | No | string (non-blank) | Optional detail about what is accessed (e.g., "reads input videos") |
| `services` | No | array of non-blank strings | Optional list of external services contacted (e.g., `api.openai.com`) |

Unknown keys are rejected at validation time.

#### Approved Permission IDs

| ID | Meaning | Example reason |
|---|---|---|
| `project_files` | Reads or writes files in the project directory | "Reads input videos and writes rendered output" |
| `network` | Makes outbound network connections | "Calls OpenAI and fal.ai APIs for AI generation" |
| `subprocess` | Spawns child processes | "Runs ffmpeg for video encoding and audio extraction" |
| `environment` | Reads environment variables | "Reads OPENAI_API_KEY and FAL_KEY from environment" |
| `accelerator` | Uses GPU or specialized hardware | "Runs ComfyUI inference on local GPU" |
| `external_services` | Calls paid or third-party cloud services | "Provisions and manages RunPod GPU pods" |

#### Example

```yaml
permissions:
  - id: network
    reason: Calls OpenAI and fal.ai APIs for AI generation
    services:
      - api.openai.com
      - api.fal.ai
  - id: environment
    reason: Reads OPENAI_API_KEY from environment for API authentication
  - id: subprocess
    reason: Runs ffmpeg for video encoding
  - id: project_files
    reason: Reads input images and writes generated output
    access: Reads from project input directory, writes to output directory
```

An empty `permissions` array is valid and means the pack declares no
capability-domain needs:

```yaml
permissions: []
```

#### Secrets vs Permissions

Permissions and secrets are different things:

- **Permissions** are pack-level capability-domain declarations. They say
  *what kind of thing* the pack does (network access, file access,
  subprocess spawning). Every capability in the pack shares the same
  permission set.
- **Secrets** are executor-level environment-variable declarations. They
  say *which specific environment variables* an executor reads. Secrets
  are declared on individual executor/orchestrator manifests, not on the
  pack manifest.

A pack that calls an external API typically needs both:

1. `permissions: [{id: network, ...}, {id: environment, ...}]` in `pack.yaml`
2. `secrets: [{name: API_KEY, required: true}]` in `executor.yaml`

The `environment` permission tells users "this pack reads environment
variables." The `secrets` block tells users "specifically, it reads this
variable." Neither is enforced — Astrid v1 does not intercept, filter, or
audit environment reads at runtime.

#### Permissions in the Trust Summary

When a user discovers or inspects a pack, the declared permissions appear in
the trust summary alongside the v1 trust block:

```
━━━ Trust Summary ━━━
  Pack ID:       generation
  ...
  Permissions:
    - network: Calls OpenAI and fal.ai APIs for AI generation; services=api.openai.com, api.fal.ai
    - environment: Reads OPENAI_API_KEY from environment for API authentication
    - subprocess: Runs ffmpeg for video encoding
    - project_files: Reads input images and writes generated output; access=Reads from project input directory, writes to output directory
  Trust (v1):
    - sandbox=none
    - runs_with_user_process_permissions=true
    - permission_enforcement=disclosure_only
  Disclosure:
    - Astrid v1 does not sandbox source-discovered packs.
    - Permission declarations are disclosure-only and not enforced.
    - Source-discovered pack code runs with your user's process permissions.
```

This is the information users see when inspecting a source checkout before
invoking one of its execution-eligible capabilities.

### Pack-Level Aliases

A pack can declare aliases for its capabilities — old or alternate ids that
resolve to the canonical capability id. Aliases keep backward compatibility when
a capability is renamed or moved to a different pack namespace.

```yaml
# In pack.yaml
aliases:
  - kind: executor
    alias: legacy.render
    canonical_id: rendering.render
    deprecated: true
    deprecation_message: "Moved to rendering.render — update your references"
```

Key rules:

- Aliases are declared on the pack that owns the **canonical** capability, not on
  the alias source pack.
- `kind` may be `executor`, `orchestrator`, `renderer`, `planner`, or
  `finalizer` — element aliases are deferred.
- Both `alias` and `canonical_id` must be qualified ids (`pack.slug`).
- `deprecated` and `deprecation_message` are optional; they surface
  informational metadata in `inspect` and `search` without blocking resolution.
- Component-level `metadata.aliases` on individual executor/orchestrator
  manifests is legacy validation-only — new aliases must use the pack-level
  `aliases` field.

### Legacy v1 rendering extensions

A pack can add timeline rendering implementations without adding an executor
and without editing the built-in rendering pack. Declare pack-relative
manifest paths under the strict rendering extension:

```yaml
schema_version: 1
id: video_tool
name: Video Tool Pack
version: 1.0.0
permissions:
  - id: project_files
    reason: Reads localized timeline assets and writes invocation artifacts
  - id: subprocess
    reason: Runs the video-tool renderer
extensions:
  rendering:
    renderers:
      - rendering/renderer.yaml
    planners:
      - rendering/planner.yaml
    finalizers:
      - rendering/finalizer.yaml
```

All three arrays are optional. Unknown keys under `extensions.rendering` are
rejected. Each path is resolved relative to the pack root and must remain
inside it after symlink resolution. Static pack validation parses these
manifests against the rendering v1 schemas but never imports or executes their
commands.

Rendering implementation ids are qualified and pack-owned: their first
segment must equal the pack `id` (`video_tool.renderer`, not
`rendering.video-tool`). A renderer manifest must declare `render`, a planner
must declare `plan`, and a finalizer must declare `finalize`; any of them may
also declare `support`. A manifest's `required_permissions` must be a subset
of the permissions disclosed by its pack. The command is an argv prefix,
never a shell string, and runs with the pack root as its working directory.

Pack-level aliases use the rendering kinds directly:

```yaml
aliases:
  - kind: renderer
    alias: video_tool.legacy
    canonical_id: video_tool.renderer
  - kind: planner
    alias: video_tool.old-policy
    canonical_id: video_tool.planner
  - kind: finalizer
    alias: video_tool.old-finalizer
    canonical_id: video_tool.finalizer
```

The public executor remains `rendering.render`. A backend pack contributes an
implementation behind that facade; it must not register a renderer with the
facade id or ask callers to import its Python module. See the complete wire,
artifact, profile, audio, finalization, and provenance contract in
[render-backend-v1.md](../contracts/render-backend-v1.md).

To author a renderer without hand-writing the pack layout, scaffold the
canonical four-file pack (`pack.yaml`, `renderer.yaml`, `render.py`,
`test_renderer.py`) with `python3 -m astrid.core.rendering.cli create <name> <dest>`,
then follow the golden path — generated test → `renderers validate` → source
checkout discovery with `renderers list`/`inspect --pack-root <parent>` →
`renderers smoke --pack-root <parent>` → provenance
sidecar — in
[render-backend-v1.md](../contracts/render-backend-v1.md#renderer-author-golden-path).
The scaffold destination directory name becomes the pack id (and must match
it for the read-only pack validator), and the renderer id becomes `<dest>.<name>`.

### Executor Manifest (`executor.yaml`)

An executor is a concrete unit of work an agent can run. Each
executor manifest declares:

- **Identity**: `id` (qualified as `<pack>.<slug>`), `name`, `version`.
- **Runtime**: `type` (currently `python-cli`), `entrypoint` (path to
  `run.py`), `callable` (function name, defaults to `main`).
- **Inputs and outputs**: typed ports with required/optional flags.
- **Dependencies**: Python, npm, and system requirements.
- **Secrets**: environment variables the executor needs at runtime.

Refer to `executor.json` for the full field list.

### Orchestrator Manifest (`orchestrator.yaml`)

An orchestrator is a workflow that coordinates executors and other
orchestrators. The manifest shape mirrors the executor with
additional fields for:

- **child_executors**: qualified ids this orchestrator coordinates.
- **child_orchestrators**: sub-orchestrator ids.

Refer to `orchestrator.json` for the full field list.

## Legacy v1 scaffold flow

The recommended workflow for creating a pack:

1. **`python3 -m astrid.core.pack.cli new <id>`** — Creates the pack
   skeleton: `pack.yaml`, `README.md`, `skill/SKILL.md`, and empty
   `executors/`, `orchestrators/`, `elements/` directories. The scaffolded
   pack passes `python3 -m astrid.core.pack.cli validate` immediately.

2. **Author executor components** — Create
   `executors/<slug>/executor.yaml`, `run.py`, and `STAGE.md` inside the
   executor content root (there is no scaffold verb for individual
   components). Each scaffolded component is validated against the v1 schema
   by `packs validate`.

3. **Author orchestrator components** — Same as above under
   `orchestrators/<slug>/`.

4. **`python3 -m astrid.core.pack.cli validate <path>`** — Validates the
   entire pack statically: checks that all manifests parse, conform to their
   JSON Schemas, have known `schema_version` values, and that declared
   content roots, docs, runtime entrypoint files, and rendering extension
   manifests exist on disk.

For a pack that contributes a timeline renderer (rather than an executor),
use `python3 -m astrid.core.rendering.cli create <name> <dest>` instead of
authoring an executor component; the scaffold writes the four-file renderer
pack and is discoverable from its source checkout as-is. Validate with
`python3 -m astrid.core.rendering.cli validate <path>`, discover with
`python3 -m astrid.core.rendering.cli list --pack-root <parent>` /
`inspect <id> --pack-root <parent>`, and smoke with
`python3 -m astrid.core.rendering.cli smoke <id> --pack-root <parent>`.
See the golden path in
[render-backend-v1.md](../contracts/render-backend-v1.md#renderer-author-golden-path).

All scaffold commands validate their output. A round-trip of
`python3 -m astrid.core.pack.cli new` → authored executor → authored
orchestrator → `python3 -m astrid.core.pack.cli validate` should succeed with
zero errors.

## Validation

Validation is **static**. It checks:

- `pack.yaml` exists and is valid YAML.
- `schema_version` is a known integer (currently only `1`).
- Each manifest conforms to its JSON Schema.
- Declared content roots and doc references point to existing paths.
- Runtime entrypoint files (`run.py`) exist on disk.
- Component `STAGE.md` files exist (warning, not error).
- Rendering extension paths stay inside the pack and each renderer, planner,
  or finalizer manifest satisfies its protocol-v1 schema.

Validation does **not**:

- Import or execute `run.py` (no sandboxing is needed — the file is
  never loaded).
- Run any code from the pack.
- Require a bound Astrid session.
- Install dependencies.

Errors include the specific file path and field, e.g.:

```
executors/my_exec/executor.yaml: missing required field runtime
pack.yaml: missing required field id
executors/my_exec/run.py: runtime entrypoint file not found
```

## Reference Examples

The `examples/packs/` directory contains teaching packs that demonstrate
pack authoring patterns. These packs are **not** runtime-discovered (they
live under `examples/packs/`, not `astrid/packs/`). See
[`examples/README.md`](../../examples/README.md) for the full listing.

The canonical minimal v3 example is `examples/packs/minimal/`. It declares
`minimal.ingest_assets` as a simple action and `minimal.make_trailer` as an
ordinary action that calls it through the public action API. There is no
separate executor/orchestrator role in this current example.

Other current v3 examples demonstrate different boundaries: `media` declares
ingestion and composed trailer actions alongside schema/template/rendering
resources; `text_review` separates deterministic summary work from caller
judgment; and `file_summarizer` makes summary validation and caller review
explicit. The legacy `astrid.core.orchestrate` Python modules and golden events
in `file_summarizer` and `text_review` remain teaching artifacts outside the
v3 action catalog; they do not imply v3 pause/ack lifecycle parity.
`text_digest` remains a v2 compatibility example owned separately.

Validate any example pack with:

```bash
python3 -m astrid.core.pack.cli validate examples/packs/minimal
python3 -m astrid.core.pack.cli validate examples/packs/file_summarizer
```

## Legacy Templates

The `docs/templates/executor/` and `docs/templates/orchestrator/` directories
contain legacy descriptor examples retained as reference material and inputs
to schema-v2 compatibility validation. They are not the current authoring
authority: the v3 authoring CLI constructs current pack scaffolds. Preserve
these bundles because existing compatibility fixtures and historical examples
use them; do not copy them when creating a new v3 pack. See the
[current authoring guide](../guides/creating-tools.md#templates) for the
authoring route. The element directory remains a separate legacy reference
and is outside this executor/orchestrator retention decision.

The v1 external pack contract described in this legacy reference is a separate
compatibility path. The current canonical external example is the v3
`examples/packs/minimal/` pack.

## Related Guides

The V3 contract and new-pack route are documented separately. Refer to these
when working on current packs:

- [create-a-pack.md](../guides/create-a-pack.md) — V3 contribution families,
  personal ownership, visual source/runtime boundaries, and scaffold usage.
- [pack-taxonomy.md](pack-taxonomy.md) — Current V3 pack metadata and the
  distinction between contribution family and owner identity.
- [contract.md](contract.md) — Current V3 identity, contribution, discovery,
  and trust boundaries.
- [discovery-for-agents.md](../guides/discovery-for-agents.md) — How a cold agent
  discovers capabilities (e.g., via `astrid.sdk.discover()` and
  `astrid.sdk.get_capability()`).

## Future Work

Several pack-system capabilities are deferred to future milestones:

- **Remote registry** — A shared catalog for publishing, discovering, and
  installing packs from outside the repository.
- **Dependency isolation** — Per-pack virtual environments and isolated
  dependency resolution to prevent conflicts between packs.

The generic app-tool proposal at
[Pack-Hosted Tools and Interface Contributions](../architecture/pack-hosted-interfaces.md)
is separate from the V3 `ui` family. Use `ui` only for its declared,
supported host contribution; the proposal does not add a general-purpose code
loader or extend V3's manifest contract.

## Next Steps

For the current v3 route, return to [When and how to create a pack](../guides/create-a-pack.md).
The historical v1 flow below is retained for existing packs and migration work:

1. Implement the `run.py` entrypoints for your executors and
   orchestrators.
2. Add tests in a `tests/` directory beside each component.
3. Document your legacy pack's capabilities in `skill/SKILL.md`.
4. Share your pack as a Git repository for others to install (Git
   install is planned for Sprint 2).

---

*Last updated: Sprint 1 (Pack Contract and Validation)*
