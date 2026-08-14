# SPRINTS

## Sprint 1 / Sprint A — canonical graph and direct-cut foundation

Time box: approximately two delegated execution weeks.

Batches/tasks:

- Batch 1: Task 0.1
- Batch 2: Task 0.2
- Batch 3: Task 0.3
- Batch 4: Tasks 1.1, then 1.2
- Batch 5: Task 1.3
- Batch 10a, pulled forward: Task 3.1

Seven tasks across six batches.

Rationale:

- Phase 2 cannot begin until Phase 1 proves the real manifest-backed load graph in both source and wheel installations.
- Task 3.1 is independent of extraction and can therefore remove aliases before Phase 2 rewrites manifests and entrypoints.
- This is the smaller effort slice. Its remaining capacity is reserved for wheel/discovery hardening and the oracle gate; pulling extraction into it would violate the hard dependency.
- One larger combined sprint would hide the most important architectural checkpoint. A third sprint would add a boundary without exposing another dependency-safe release state.

Exit criteria:

- `_core` is a trusted, manifest-backed system pack.
- The bundled inventory is deterministic, packaged, and excludes `builtin` and checkout-local packs.
- Skills and elements use the single discovered-pack stream.
- Wheel smoke passes outside the checkout with an empty `ASTRID_HOME`.
- Arnold is the sole lifecycle engine; no task-engine fallback, selector, warnings, or product-workflow table remains.
- `runtime/in_process.py` is not an import-layer exception.
- `_IMPORT_LAYERING_EXEMPT_REL` and `_PACK_RUNTIME_BRIDGE_EXEMPT_REL` are gone.
- Pack manifests and schemas contain no capability `aliases:` field.
- `builtin`, `builtin.*`, capability aliases, `astrid author`, `astrid run`, implicit brief routing, and pure-executor shortcuts are absent.
- `validate_import_layering()` and `validate_repo_structure()` pass with zero exemptions.
- Pack validation, generated-artifact checks, targeted tests, wheel smoke, and `scripts/reshape/run_ci_checks.sh` pass.

Oracle checkpoint:

- Batch 5 / Task 1.3 is the hard Phase 1 → Phase 2 gate.
- It passes only when all discovery layers and wheel inventory resolve through the canonical graph.
- No 2.x task may begin before that recorded `PASS`.
- Task 3.1 runs after Batch 5 within Sprint A because it is independent of Phase 2 extraction.

Shippable means a releasable Astrid whose product domains remain in their existing locations, but whose loading, lifecycle, packaging, identity, and public capability names already match the end state. It is not a compatibility release.

## Sprint 2 / Sprint B — extraction, residual deletion, and closure

Time box: approximately two delegated execution weeks.

Batches/tasks:

- Batch 6: Tasks 2.1 and 2.3
- Batch 7: Task 2.2
- Batch 8: Tasks 2.4, then 2.5
- Batch 9: Task 2.6
- Batch 10b: Task 3.2
- Batch 11: Tasks 4.1, then 4.2
- Batch 12: Tasks 4.3, then 4.4, then 4.5

Twelve tasks across seven batches.

Rationale:

- This is the heavier slice because extraction, import-policy closure, private-entrypoint cleanup, layout enforcement, documentation, and final verification must converge.
- Tasks 2.1 and 2.3 can be delegated as independent streams.
- The Reigh inversion remains sequential: 2.4 must pass before 2.5.
- Phase 4 begins only after the Phase 3 deletion work is complete.
- Splitting closure into a third sprint would either strand transitional state or produce a boundary that is not independently shippable.

Inter-sprint dependency gate:

- Batch 5 / Task 1.3 must have a recorded `PASS`.
- Source, local, extra, environment, installed, and wheel-source layers must all use the canonical graph.
- The wheel must contain the complete bundled inventory.
- Sprint A’s full checkpoint, including Task 3.1, must be green before Sprint B begins.

Exit criteria:

- Generation, RunPod, Reigh, worker, and experiment implementations live only in their packs.
- Every static cross-pack support dependency is declared, necessary, and acyclic.
- No pack imports another pack’s executor/orchestrator `run.py`.
- Every pack-to-core import belongs to the machine-readable supported kernel API.
- `runpod`, `worker`, and `serve` have no top-level gateway routes.
- Reigh serving is invoked only through `executors run reigh.serve_local_bridge`.
- Legacy runtime shapes, parser fallbacks, bridge `assets.json` migration fallback, auto-bind shims, rendering compatibility selectors, and the old hybrid-planner ID are gone.
- Pack-private commands are guarded and pack-root layout is enforced.
- Documentation, generated artifacts, CI selection, wheel contents, and repository hygiene describe the actual tree.
- Full tests, wheel smoke, Remotion checks, pack validation, import rails, and clean-checkout hygiene pass.

Shippable means the complete end state: one discovery graph, one lifecycle engine, one canonical ID per capability, one public route per operation, and no retained migration scaffolding.

# TASKLIST

## Phase 0 — name and legalize the kernel

### 0.1 `[XHARD]` Lock the kernel, remove lifecycle duality, and eliminate core-to-pack exceptions — Phase 0 · L · Depends: none

- Update `docs/packs/contract.md` with the authoritative kernel table:
  - CLI gateway
  - session/project management
  - common run-state and event machinery
  - pack discovery/validation/install/store
  - capability registries
  - SDK and skills installer
  - structure/doctor
  - foundation/contracts
  - timeline/eventlog
  - rendering and generation protocols
  - Arnold lifecycle/orchestration
- Remove capability aliases from the kernel definition.
- Make Arnold the sole lifecycle engine for `start`, `next`, `ack`, `status`, and `abort`.
- Remove the `--engine task|arnold` selector, legacy task lifecycle branch, fallback logging, release-warning machinery, and dual-engine help.
- Port required run inspection/admin operations to common Arnold-backed state. Delete task-only verbs with no Arnold meaning.
- Delete `astrid/core/integrations/arnold/host/compat.py` in favor of one exact lazy Arnold contract loader with no compatibility naming or optional-version accommodation.
- Delete the static product-workflow and alias table in `astrid/core/integrations/arnold/host/shapes.py`.
- Resolve qualified orchestrators through the discovered registry and compile them through the existing Arnold lowering path. Add no shape-extension framework.
- Consolidate pack runtime loading in `astrid/core/pack/resolver.py`.
- Move fresh-module loading needed by in-process execution behind that resolver, then remove:
  - `_IMPORT_LAYERING_EXEMPT_REL`
  - `_PACK_RUNTIME_BRIDGE_EXEMPT_REL`
  - the classification of `runtime/in_process.py` as a static core-to-pack bridge
- Make the structural rail reject literal `astrid.packs.*` imports/importlib targets anywhere in core and reject direct runtime-module resolution outside the resolver.
- State that concrete adapters, discoverable capabilities, product workflows, and optional service domains belong in packs.
- Retain `astrid scratch` as the sole host escape hatch for arbitrary project-scoped scripts.
- Do not classify `astrid serve` as a permanent host contract; Task 2.5 deletes it.
- Classify `remotion/`, `themes/`, and Git-aware scripts as product-owned.
- Accept `astrid.packs.*`, `python3 -m astrid`, and `<pack>.<name>` as Astrid product contracts.
- Record the zero-violation import/structure baseline and add negative tests proving no exception or product-workflow table remains.
- Retain the extension-admission rule: use an extension only for a real core consumer with interchangeable implementations.

### 0.2 `[XHARD]` Make `_core` a legal, manifest-backed system pack — Phase 0 · M · Depends: 0.1

- Update `_defs.json` so `pack_id` accepts normal IDs or reserved literal `_core`; provenance remains a loader concern.
- Add one `is_trusted_system_pack_source(pack_id, manifest_path)` seam.
- Accept `_core` only from the canonical shipped source root.
- Reject user, local, extra, environment, installed, symlinked, and relative-path `_core` claims.
- Preserve folder/ID equality and reject `_core.<name>` capability IDs.
- Add `astrid/packs/_core/pack.yaml` with system metadata, its skill root, and no capabilities, dependencies, or extensions.
- Remove manifest-less skill-shell handling from first-party and layout validation.
- Add `astrid/skills/branding.py` as the single `_core` → `astrid` presentation seam.
- Consume it from Claude, Codex, Hermes, registry, CLI, and installer code.
- Preserve literal `python3 -m astrid` commands and Python package paths.
- Extend schema, loader, discovery, validation, skills, and wheel tests for canonical trust, noncanonical rejection, empty capabilities, and branding invariants.

### 0.3 Establish one deterministic product-owned inventory and delete `builtin` — Phase 0 · M · Depends: 0.2

- Replace hardcoded first-party pack sets with `astrid/packs/bundled.yaml`.
- Include the complete tracked manifest-backed set, including `_core` and Blender.
- Exclude `builtin`, `discord_local`, and `seedance_local`.
- Delete `astrid/packs/builtin/`, including `builtin.agent_probe`, build output, fixtures, and golden data.
- Replace `builtin.agent_probe` regression use with a temporary test-pack fixture covering the same generic orchestration behavior.
- Remove every `builtin.*` reference from manifests, tests, docs, skills, fixtures, and Arnold state.
- Make generic first-party validation consume an adjacent inventory rather than embedded Astrid IDs or Git assumptions.
- Derive displayed counts, documentation, tests, and wheel parity from the inventory.
- Keep checkout-local packs runtime-discoverable but exclude them from committed index generation.
- Make capability-index generation select only tracked bundled IDs.
- Add generated-output check modes for the capability index and `_core/skill/SKILL.md`.
- Regenerate both artifacts from a clean checkout.

## Phase 1 — one manifest-backed load graph

### 1.1 `[XHARD]` Route every reader through the canonical discovered-pack stream — Phase 1 · M · Depends: 0.2–0.3

- Refactor skills discovery and the agent index to consume ordered `DiscoveredPack` records.
- Cover source, local, extra, environment, and installed roots.
- Delete direct `PACKS_DIR.iterdir()` walks, manifest-less fallback, swallowed manifest errors, duplicate scanners, and the agent-index dual path.
- Fix `ASTRID_PACKS_PATH` skill discovery.
- Exclude hidden packs consistently from every layer.
- Treat `deprecated` only as lifecycle metadata: the pack remains discoverable under its canonical ID, with no redirect, warning window, or retained old implementation.
- Obtain skill roots only from `DiscoveredPack.skill_roots()`.
- Apply pack-ID deduplication once at canonical source priority.
- Preserve explicit-root testability by parameterizing shared discovery.
- Make invalid manifests fail at the pack boundary and expose no capabilities or skills.
- Preserve top-level SDK laziness.
- Add ordering, `_core`, duplicate, hidden-installed, deprecated-status, invalid-manifest, and checkout-local tests.

### 1.2 `[XHARD]` Remove theme and workspace element discovery — Phase 1 · L · Depends: 1.1

- Remove `ElementSource`, `default_sources()`, `load_source_elements()`, active-theme element loading, `WORKSPACE_ROOT`, `legacy_workspace`, and conflict warnings.
- Build the element registry exclusively from discovered pack metadata and declared roots.
- Add no pseudo-packs for theme or workspace directories.
- Remove discovery-only `active_theme`, `include_missing_roots`, and `elements --theme` inputs.
- Update timeline validators, training, rendering, SDK discovery, and effect-registry generation to consume pack metadata.
- Keep theme selection, state, pointers, and provenance as rendering data.
- Replace positive theme/workspace discovery tests with negative no-scan rails.
- Preserve local-pack precedence and rendering behavior.
- Require every loaded element to report `source == "pack:<id>"`.

### 1.3 Package and prove the canonical graph in wheels — Phase 1 · L · Depends: 1.1–1.2

- Replace rendering-only package data with explicit coverage for:
  - model-catalog YAML
  - rendering schemas and parity fixtures
  - `packs/bundled.yaml`
  - pack manifests
  - executor/orchestrator manifests
  - element manifests
  - rendering extension YAML
  - pack and nested-executor skills
  - executor/orchestrator `STAGE.md`
- Use no blanket recursive pack-root include.
- Extend wheel smoke to run outside the checkout with empty `ASTRID_HOME`.
- Prove every bundled ID has its canonical manifest in the wheel.
- Prove source and wheel inventories agree.
- Exercise representative capabilities, skills, STAGE files, extensions, model catalogs, and an empty installed store.
- Preserve `import astrid` laziness.
- Prove registry loading does not eagerly import concrete generation, Reigh, or RunPod implementations.

## Phase 2 — extract concrete domain implementations

### 2.1 `[XHARD]` Move concrete generation backends into the generation pack — Phase 2 · M · Depends: 1.1, 1.3

- Move Fal, Codex, and VibeComfy backends into `astrid/packs/generation/backends/`.
- Declare all three through the existing generation-backend extension.
- Delete builtin descriptor seeding and hardcoded module strings from core.
- Keep provider-neutral protocols, registry, taxonomy, verbs, and feature contracts in core.
- Require a bare registry to be empty and default loading to come only from manifests.
- Remove concrete core exports and lazy concrete imports.
- Update generation executors, unavailable-reason handling, tests, SDK discovery, gateway resolution, and model validation.
- Delete all tracked `fal-voice-upscale/` files.
- Move adapter tests under `tests/packs/generation/`.
- Add negative tests for old core paths and literal module strings.
- Prove wheel discovery gains and loses all three backends with the generation manifest.

### 2.2 `[XHARD]` Move experiments, declare dependencies, and eliminate entrypoint coupling — Phase 2 · L · Depends: 0.1, 1.1

- Add optional sorted, unique `depends` pack IDs for static Python support dependencies.
- Keep `depends` distinct from external dependencies and capability composition.
- Reject malformed, duplicate, self, cyclic, undeclared, missing, and stale dependencies.
- Add `astrid/core/pack/import_policy.py` and its test suite.
- Inventory all static and literal-dynamic cross-pack imports.
- Classify each edge as capability invocation, genuine support dependency, or accidental/private coupling.
- Replace execution dependencies with qualified capability dispatch.
- Forbid importing another pack’s executor/orchestrator `run.py`, even with `depends`.
- Move genuinely shared symbols into narrow owning-pack support modules.
- Declare surviving edges such as editorial → training, video_editing → editorial, and editorial → iteration.
- Move `astrid/core/experiments/` directly to `astrid/packs/iteration/experiments/`.
- Delete the old path and update all consumers atomically.
- Move experiment tests under `tests/packs/iteration/experiments/`.
- Finish with no known violation deferred.

### 2.3 `[XHARD]` Move RunPod maintenance into its pack and delete the host route — Phase 2 · M · Depends: 0.1, 1.1

- Move RunPod storage and sweeper implementations into the RunPod pack.
- Add `runpod.sweep`, `runpod.list_volumes`, and `runpod.ensure_storage`.
- Add no core RunPod protocol, extension, or doctor hook.
- Preserve dry-run diagnostics and storage-recovery behavior.
- Delete top-level `astrid runpod`, `astrid/core/gateway/runpod.py`, dispatch functions, help, exports, and allowlist entries in this task.
- Do not temporarily rewire the old route.
- Remove RunPod checks from core doctor and cover the executor instead.
- Keep `require_existing_storage` pack-local.
- Give training a local diagnostic pointing directly to `runpod.ensure_storage`.
- Remove `astrid/core/integrations/runpod/` after imports move.
- Relocate and retarget tests.
- Add no structure exemption.

### 2.4 `[XHARD]` Invert generic Reigh bridge state before extraction — Phase 2 · M · Depends: 0.1

- Create provider-neutral `astrid/core/timeline/asset_registry_state.py`.
- Move latest-event recovery, sidecar repair, record/source resolution, and no-pruning merge behavior into it.
- Delete the legacy `assets.json` recovery branch from `_ensure_bridge_registry`; canonical recovery may use current sidecar state, current event-stream state, or normal derivation, but must not import the legacy representation.
- Update recovery tests so legacy-assets-only state is not silently migrated.
- Make core edits and the eventual pack bridge consume the helper.
- Extend the existing remote-timeline Protocol only with required load/save/list shapes.
- Keep implementations caller-injected; add no Reigh registry or extension.
- Preserve generic timeline backend modules.
- Keep event recovery, CAS, reconciliation, sidecar, no-op, and no-pruning tests in the timeline suite.

### 2.5 `[XHARD]` Move Reigh and worker implementations into the Reigh pack and delete host routes — Phase 2 · L · Depends: 2.4

- Move Reigh environment, provider, transport, task client, remote timeline, JWT/JWKS, append service, errors, and worker implementations into the Reigh pack.
- Delete `astrid/core/integrations/reigh/` and `astrid/core/integrations/worker/`.
- Delete `event_construction.py`, integration-local `supabase_client.py`, and every other compatibility export or copy.
- Add canonical `reigh.worker`.
- Add canonical `reigh.serve_local_bridge --projects-root`.
- Make `executors run reigh.serve_local_bridge` the only public server invocation.
- Delete top-level `astrid worker` and `astrid serve`, their dispatch/help/export code, and the sessionless `serve` allowlist entry.
- Use the normal executor contract. Accept attached/explicit-project requirements; add no sessionless host adapter.
- Add narrow `reigh.timeline_edit` for `add-clip`, `move-clip`, and `set-theme`.
- Preserve PAT defaults, optional service-role authentication, optimistic versioning, three retries, `force=False`, and event descriptors.
- Remove remote `projects list` and `projects edit`.
- Delete `scripts/node/ops_helper.mjs`.
- Keep local project storage and local timeline commands in core.
- Ensure core contains no Reigh implementation import or hardcoded Reigh module string.

### 2.6 `[XHARD]` Close extraction imports, define the pack-facing API, and remove CI path coupling — Phase 2 · L · Depends: 2.1–2.5

- Move all Reigh implementation tests under `tests/packs/reigh/`.
- Keep provider-neutral timeline/eventlog tests under `tests/timeline/`.
- Document `tests/packs/<id>/` as the extraction rail.
- Replace positive implementation inventories with negative absence rails.
- Require no live `astrid.core.integrations.{reigh,runpod,worker}` imports.
- Inventory every remaining pack-to-core import.
- Enforce exact machine-readable supported kernel-module prefixes.
- Support only provider-neutral contracts and explicitly public foundation, execution, discovery, runtime, session, generation, rendering, timeline, and lineage APIs.
- Reject `_shared`, private symbols, CLI handlers, concrete implementations, and blanket utility families.
- Promote genuinely shared helpers into existing public modules or make them pack-local.
- Define the supported surface as a current contract; update every first-party caller atomically when it changes, with no alias or deprecation window.
- Wire the checker into CI with zero exemptions.
- Make changed-file selection arbitrary-depth under `astrid/**`.
- Update the bridge workflow for `astrid/packs/reigh/**` and the actual PR ref.
- Keep all moved tests discoverable under `tests/`.

## Phase 3 — eliminate public aliases and residual migration machinery

### 3.1 Remove all alias surfaces — Phase 3 · M · Depends: 1.3

Scheduled in Sprint A after Batch 5.

- Delete the pack-level `aliases:` schema field, definition field, parser, normalizer, resolver, validation, and registry wiring.
- Remove `AliasRecord`, `AliasResolver`, alias-cycle logic, deprecation messages, and alias tests.
- Delete every alias declaration from shipped manifests, including `builtin.*`, `external.*`, `upload.youtube`, and similar alternate IDs.
- Require code, manifests, tests, skills, docs, fixtures, and stored examples to use canonical qualified IDs directly.
- Delete the `builtin` namespace rather than redirecting it.
- Remove top-level `publish`, `publish-youtube`, `upload-youtube`, and `reigh-data`.
- Remove `astrid author` and `astrid run`.
- Remove implicit flag-first `astrid --brief/--video` routing.
- Remove Arnold CLI aliases and element-kind aliases such as `crossfade`.
- Replace `tests/test_canonical_aliases.py` with canonical-ID rejection and uniqueness tests.
- Replace the aliases/forks/overrides guide with a forks-and-overrides guide.
- Keep forks and explicit user overrides as customization contracts.
- Add negative root-help, unknown-command, schema, and manifest tests for every removed name.

### 3.2 Delete remaining compatibility parsers and dual-path runtime support — Phase 3 · L · Depends: 2.1–2.5, 3.1

- Delete `runtime_command_legacy` and require one canonical runtime-manifest shape.
- Delete fallback parsing of legacy agent entrypoints; require `agent.normal_entrypoints`.
- Delete the legacy flat manifest parser; use the canonical YAML/JSON loader only.
- Delete disabled project auto-bind compatibility functions.
- Remove rendering’s `engine` selector, neutral alias-to-engine translation, `legacy_engine.py`, and legacy argument adaptation.
- Require qualified renderer/planner/finalizer IDs and namespaced backend configuration.
- Rename the load-bearing hybrid planner directly from `rendering.legacy_hybrid` to `rendering.hybrid`; update callers, fixtures, schemas, and provenance atomically, with no alias.
- Delete obsolete sibling-output compatibility parameters and re-export shells.
- Verify RunPod, worker, and serve host routes have not survived through help, exports, tests, or docs.
- Remove warning-window and sunset-version machinery.
- Add a scoped repository rail rejecting compatibility shims, alias bridges, legacy runtime shapes, legacy bridge-state fallbacks, and dual public routes.

## Phase 4 — enforce pack layout and make the repository truthful

### 4.1 `[XHARD]` Canonicalize pack-private entrypoints — Phase 4 · L · Depends: Phase 3

- Convert `blender/deploy.py` into canonical `blender.deploy`.
- Keep mesh fetching as private `blender.render` support; remove its independent CLI.
- Move Blender render/server support beneath owning executor trees.
- Remove alternate `__main__` surfaces.
- Update Blender manifests, skills, STAGE files, docs, presets, imports, and tests.
- Keep rendering backend/planner/finalizer runners as manifest-private transport commands, including `rendering.hybrid`.
- Have core rendering transport set the internal-invocation marker.
- Reject direct subprocess invocation while allowing manifest transport.
- Remove the unsupported `python -m astrid.sdk.rendering` claim.
- Remove executable behavior from unledgered generation golden demos.
- Add canonical-success/direct-module-failure subprocess rails.
- Search for stale `python -m astrid.packs.*` instructions, permitting only exact manifest-private commands.

### 4.2 `[XHARD]` Enforce actual pack-root layout — Phase 4 · L · Depends: 4.1

- Make layout validation walk actual pack-root entries.
- Permit only:
  - `pack.yaml`
  - declared content roots
  - `skill/`, `docs/`, `examples/`, `schemas/`, `fixtures/`, `golden/`
  - capability-local golden fixtures
  - Python package markers
  - declared extension roots
  - narrowly documented declared support-library roots
- Keep `editorial/hype/` as declared library-only support and prove it has no discovery or CLI surface.
- Keep rendering’s declared backend/planner/finalizer extension roots.
- Reject undeclared loose files and directories with actionable paths.
- Move Fal tests under `tests/packs/fal/`.
- Add positive editorial/rendering/fixture/golden tests and negative junk-layout cases.

### 4.3 Close root-hygiene gaps and root-writing tests — Phase 4 · M · Depends: 2.1, 4.2

- Verify `fal-voice-upscale/` is absent and remove its root allowlist entry.
- Add `*.mp3` to Git ignore and tracked-runtime-media rules.
- Inspect actual root filesystem entries as well as tracked Git paths.
- Keep the hygiene checker product-repository-owned.
- Replace root-directed test output with `tmp_path`, `TemporaryDirectory()`, or system temp paths.
- Add no speculative deletion rules for absent unrelated directories.
- Do not touch `.oracle-threejs-archive/`.
- Run final hygiene from a clean checkout.

### 4.4 Complete the documentation and CI truth pass — Phase 4 · M · Depends: 4.1–4.3

- Document the exact kernel, Arnold-only lifecycle, canonical product namespace, supported pack API, `depends`, and extension-admission rule.
- Document no core-to-pack exceptions and no static Arnold product-shape table.
- Document `_core` as the system pack and the absence of `builtin`.
- Remove alias-carrier, compatibility-window, fallback-engine, `astrid serve`, and extraction-debt language.
- Document hidden and deprecated status without implying redirects or retained old implementations.
- Correct repository shape, execution paths, gateway modules, and SDK export count.
- Document pack-only discovery and qualified capability routes.
- Document `rendering.hybrid` and remove the old planner ID.
- Update Generation, Iteration, Reigh, RunPod, YouTube, Blender, rendering, and `_core` skills/manifests/STAGE files.
- Update integration contracts to use `executors run reigh.serve_local_bridge`.
- Update CI-lane documentation and command verification.
- Regenerate `_core/skill/SKILL.md`.
- Search for stale domain paths, aliases, `builtin`, removed host verbs, legacy runtime/bridge-state shapes, old planner IDs, direct pack commands, and old exception language.

### 4.5 Run the full closure gate — Phase 4 · M · Depends: 4.1–4.4

- Run pack validation.
- Run schema, dependency/import-policy, discovery, skills, elements, structure, gateway, doctor, generation, iteration, Reigh, RunPod, rendering, layout, CI-selection, and hygiene tests.
- Run wheel smoke outside the checkout with empty `ASTRID_HOME`.
- Run `scripts/reshape/run_ci_checks.sh` and the broad suite.
- Run Remotion typechecking and renderer-parity tests.
- Require zero import-layer exemptions.
- Require no runtime-resolver file allowlist.
- Require a complete, non-stale, acyclic `depends` graph.
- Require no cross-pack executor/orchestrator entrypoint imports.
- Require every pack-to-core import to belong to the supported API.
- Run generated-artifact check modes.
- Prove no shipped manifest contains `aliases:`.
- Prove `builtin`, `builtin.*`, removed gateway verbs, task-engine selection, old runtime shapes, `rendering.legacy_hybrid`, old core-domain paths, and the legacy bridge `assets.json` fallback are absent outside explicit negative fixtures.
- Prove `runtime/in_process.py` and the Arnold host contain no product-specific exception or workflow table.
- Prove every capability, skill, and concrete generation backend originates from a manifest-backed pack.
- Prove `astrid/core/integrations/` contains only the exact Arnold host contract.
- Verify moved tests follow `tests/packs/<id>/`.
- Verify clean-checkout indexing and hygiene without touching `.oracle-threejs-archive/`.

# SHIM-SWEEP

| # | Candidate | Verdict | Reason / end state |
|---|---|---|---|
| 1 | `core/runtime/in_process.py` exception | CUT | Keep in-process execution, but resolve manifest-owned targets through the existing resolver and delete the static bridge exception. |
| 2 | Five-file runtime/resolver allowlist | CUT | One resolver owns manifest-target validation; no file-by-file permission list remains. |
| 3 | Legacy task-engine fallback | CUT | Arnold is the sole lifecycle engine; operations are ported or removed. |
| 4 | `astrid serve` | CUT | `executors run reigh.serve_local_bridge` is the single public route. |
| 5 | `astrid scratch` | KEEP | It is a distinct host operation for arbitrary project-scoped scripts, not a duplicate capability route. |
| 6 | `builtin` and `builtin.agent_probe` | CUT | They are a compatibility namespace and regression fixture; generic behavior moves to a temporary test pack. |
| 7 | Pack `aliases:` and alias machinery | CUT | Alternate IDs exist to smooth migration; every caller moves directly to its canonical ID. |
| 8 | `_core` → `astrid` branding seam | KEEP | Harness installation is intentionally product-branded; one centralized seam is the final contract. |
| 9a | `in_process.py` extraction blocker | CUT | Solved in Task 0.1 by centralizing runtime resolution. |
| 9b | Arnold `host/shapes.py` blocker | CUT | Delete the product table and compile discovered qualified orchestrators through existing lowering. |
| 10 | Reigh compatibility exports | CUT | Delete `event_construction.py`, integration-local `supabase_client.py`, and every residual re-export shell. |
| 11a | Deprecation windows and one-release warnings | CUT | No warning period or scheduled-removal machinery remains. |
| 11b | Pack `status: deprecated` | KEEP | It is lifecycle metadata on the one canonical ID, not a redirect or second implementation. |
| 11c | `legacy_workspace` discovery | CUT | Elements originate only from manifests. |
| 11d | Transitional RunPod dispatch | CUT | Delete the host route when executor parity lands; never rewire it temporarily. |
| 11e | Recorded extraction debts | CUT | Both named blockers are solved now and removed from closure language. |
| 11f | Rendering support-based fallback policy | KEEP | Selecting a renderer that supports current input is runtime capability negotiation, not legacy migration. |
| 12 | `editorial/hype/`, `golden/`, and `fixtures/` classifications | KEEP | These are truthful content/layout classes with no alternate capability route. |
| 13 | `astrid author` and `astrid run` | CUT | They duplicate canonical orchestration/run surfaces. |
| 14 | Implicit `astrid --brief/--video` dispatch | CUT | It silently aliases the Hype orchestrator; require the qualified invocation. |
| 15 | `astrid publish*` and `reigh-data` shortcuts | CUT | Canonical executor IDs already provide each operation. |
| 16 | `astrid worker` and `astrid runpod` | CUT | Remove them with the tasks that land complete executor parity. |
| 17 | Arnold `compat.py` optional surface | CUT | Use one exact lazy contract loader with no multi-version accommodation. |
| 18 | Arnold shape CLI aliases | CUT | Qualified orchestrator IDs are the only accepted workflow names. |
| 19 | Legacy runtime-manifest shapes and entrypoint fallback | CUT | Schemas and readers accept one canonical representation. |
| 20 | Disabled project auto-bind functions | CUT | Dead compatibility functions have no end-state role. |
| 21 | Rendering `engine` selector and `legacy_engine.py` | CUT | Require qualified backend IDs and namespaced configuration. |
| 22 | `rendering.legacy_hybrid` name | CUT | Rename the implementation directly to `rendering.hybrid`, updating all callers without an alias. |
| 23 | Forks and explicit user overrides | KEEP | They are intentional customization mechanisms, not migration redirects. |
| 24 | Video transitions and transition layout/schema terms | KEEP | “Transition” describes a media capability, not migration machinery. |
| 25 | Reigh bridge’s legacy `assets.json` recovery fallback | CUT | It silently migrates an obsolete representation; retain integrity recovery from canonical sidecar/event state and normal derivation only. |

The legacy `assets.json` bridge fallback is the one additional shim-sweep candidate not adjudicated in the existing v7 table.
