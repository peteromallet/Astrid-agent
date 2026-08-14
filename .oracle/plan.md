# TASKLIST

## Phase 0 — name and legalize the kernel

### 0.1 Lock the kernel and green import baseline — Phase 0 · S · Depends: none

- Update `docs/packs/contract.md` with the authoritative kernel table. For every retained area, record its responsibility, why it is kernel-resident now, and its future portability disposition:
  - CLI gateway
  - session/project management
  - task-run machinery
  - pack discovery/validation/install/store/aliases
  - capability registries
  - SDK and skills installer
  - structure/doctor
  - foundation/contracts
  - timeline/eventlog
  - rendering and generation protocols
  - Arnold lifecycle/orchestration
- Justify Arnold explicitly: `start`, `next`, `ack`, and `abort` default to the Arnold engine in `astrid/core/gateway/dispatch.py`; manifest/folder orchestrators compile through Arnold lowering in `astrid/core/execution/orchestrator/pipeline.py`; and the authoring DSL does the same in `astrid/core/orchestrate/compile.py`. Arnold is therefore the default lifecycle host and compiler target, not an optional service adapter like Reigh or RunPod.
- Keep Arnold loading lazy through `astrid/core/integrations/arnold/host/compat.py`; retain the legacy task engine as an explicit fallback.
- State the exclusion rule: concrete generation adapters, discoverable capabilities, product workflows, and optional service domains belong in manifest-backed packs.
- Retain `astrid/core/integrations/arnold/`, `astrid/core/orchestrate/`, `astrid/core/timeline/`, `astrid/core/timeline/eventlog/`, `astrid scratch`, and `astrid serve` in their host/protocol roles.
- Classify `remotion/` as Astrid product/rendering-pack runtime substrate and `themes/` as Astrid product rendering data. Neither is part of the reusable framework kernel.
- Classify `scripts/gen_capability_index.py`, `scripts/reshape/check_repo_hygiene.py`, changed-file CI selection, and other Git-aware checks as product-repository tooling that must not migrate into a future shared framework.
- Accept `astrid.packs.*` as the stable Astrid product namespace for this migration. Keep `python3 -m astrid` and `<pack>.<name>` IDs unchanged; a future framework distribution gets its own namespace rather than renaming Astrid’s packs during packification.
- Record two known pre-framework-extraction blockers without solving them here:
  - `astrid/core/runtime/in_process.py` assumes `astrid.packs.*`.
  - `astrid/core/integrations/arnold/host/shapes.py` contains a static product workflow table and concrete Astrid pack command strings.
- State that those two debts are not precedent for adding more product knowledge to core.
- Record the verified baseline: `validate_import_layering()` and `validate_repo_structure()` both report zero violations.
- Keep `astrid/core/runtime/in_process.py` as the sole static core-to-pack import exception and preserve the existing manifest-driven dynamic resolver allowlist in `astrid/core/structure.py`; add no exemptions.
- Generalize the hardcoded-import rail: detect literal `importlib.import_module()` targets under `astrid/core/` rather than testing only the current generation registry lines. Continue checking executable/module strings involved in each extraction.
- Document the extension-hook admission rule: add a hook only when core has a real consumer and interchangeable implementations justify registration. A single product implementation should use dependency injection, canonical capability dispatch, or a narrow existing host facade.
- Add contract tests covering Arnold’s stated residency reason, the product/framework classifications, the accepted product namespace, and the two documented extraction blockers.

### 0.2 `[XHARD]` Make `_core` a legal, manifest-backed system pack — Phase 0 · M · Depends: 0.1

- Change validation before or atomically with adding `astrid/packs/_core/pack.yaml`; a naïve manifest is runtime-fatal because:
  - `astrid/core/pack/_common.py:144-146` rejects `_core`.
  - `astrid/core/pack/loader.py:94-107` does not skip underscore directories.
  - `loader.py:117-118` requires manifest ID to match the folder.
  - The resulting `PackValidationError` escapes discovery and crashes every registry.
- Update `astrid/core/pack/schemas/v1/_defs.json` so the lexical `pack_id` definition accepts either a normal pack ID or the reserved literal `_core`; do not pretend raw JSON Schema can validate trust.
- Add one provenance seam in `astrid/core/pack/loader.py`, such as `is_trusted_system_pack_source(pack_id, manifest_path)`, and route every loader/validator decision for reserved system IDs through it.
- Implement that function initially with resolved filesystem provenance: `_core` is accepted only when its resolved manifest parent is the canonical shipped source root. Keep distribution-origin trust as a future replacement behind the same function.
- Continue rejecting user, local, extra, environment, and installed packs claiming `_core`; preserve folder/ID equality and reject `_core.<name>` capability IDs.
- Add symlink, relative-path, installed-store, extra-root, and alias-fed tests proving a noncanonical `_core` claim cannot pass the provenance function.
- Add `astrid/packs/_core/pack.yaml` with system metadata, the existing skill root, and no executors, orchestrators, elements, aliases, dependencies, or extension capabilities.
- Remove the manifest-less skill-shell rules from `astrid/core/pack/validate_first_party.py:136-155` and `astrid/core/pack/validate_layout.py:122-129`.
- Add one skills-layer branding seam in `astrid/skills/branding.py` for the `_core` pack ID, `astrid` harness-link name, display name, and managed markers.
- Consume that branding definition from:
  - `astrid/skills/harnesses/base.py`
  - `astrid/skills/harnesses/claude.py`
  - `astrid/skills/harnesses/codex.py`
  - `astrid/skills/harnesses/hermes.py`
  - `astrid/skills/{__init__,registry,cli}.py`
- Preserve literal `python3 -m astrid` commands and Python package paths; the branding seam is for harness/product presentation, not an attempted abstraction over the package namespace.
- Extend `tests/packs/test_pack_yaml_schema.py`, `test_pack_discovery.py`, `test_pack_layout_contract.py`, `test_packs_validate.py`, `test_packs_cli.py`, `tests/test_skills.py`, and wheel smoke coverage with lexical schema acceptance, canonical loader acceptance, noncanonical-source rejection, capability emptiness, discovery, and branding invariants.

### 0.3 Establish one deterministic product-owned first-party inventory — Phase 0 · M · Depends: 0.2

- Replace `_FIRST_PARTY_PACK_IDS` and `_FIRST_PARTY_INTERNAL_DIRS` in `astrid/core/pack/validate_first_party.py` with one packaged product-owned inventory at `astrid/packs/bundled.yaml`.
- Include the tracked, manifest-backed bundled set in that inventory, including `blender` and `_core`.
- Make generic first-party validation consume an explicit adjacent inventory instead of embedding Astrid pack IDs, repository-root assumptions, or Git commands in kernel Python.
- Recognize the Astrid product pack root through `bundled.yaml`; validate every listed pack and derive displayed counts, documentation, and tests from it.
- Remove hardcoded bundled-pack counts such as `19` from `astrid/core/pack/cli_basic.py`.
- Treat `discord_local` and `seedance_local` correctly: they are checkout-local personal packs excluded through `.git/info/exclude`, not stale or bundled content.
- Do not add those packs to the bundled inventory, delete them, restore them, or suppress their runtime discovery.
- Keep tracked-file filtering in product tooling: change `scripts/gen_capability_index.py` to generate the committed capability index from the IDs in `bundled.yaml` and verify that each selected source manifest is Git-tracked.
- Add a parity check proving the bundled inventory, tracked bundled pack directories, and packaged wheel inventory agree.
- Add a regression fixture containing an untracked personal pack and prove it remains runtime-discoverable but cannot enter the committed index.
- Add a check mode for `_core/skill/SKILL.md` and capability-index generation so CI fails when committed generated output differs from clean regeneration.
- Regenerate `astrid/packs/_core/skill/SKILL.md` from the product inventory in a clean checkout.
- Keep `astrid/packs/builtin/pack.yaml` visible and make its description truthful about the live `builtin.agent_probe` orchestrator; add a manifest/documentation consistency test.

## Phase 1 — one manifest-backed load graph

### 1.1 `[XHARD]` Route skills through the canonical discovered-pack stream — Phase 1 · M · Depends: 0.2–0.3

- Refactor `astrid/skills/discovery.py` to consume ordered `DiscoveredPack` records from `astrid/core/pack/discovery.py` across source, local, extra, environment, and installed roots.
- Delete the direct `PACKS_DIR.iterdir()` walk, manifest-less fallback, swallowed manifest errors, and duplicate `_scan_discovered_packs()` traversal.
- Fix the current `ASTRID_PACKS_PATH` omission caused by `astrid/skills/discovery.py:143-145`; environment-root skills must list.
- Make hidden-pack treatment consistent: hidden packs must not enter source or installed discovery.
- Document hidden and deprecated policies side by side in `docs/packs/contract.md`: hidden controls discoverability; deprecated remains discoverable with lifecycle/deprecation metadata.
- Reject aliases and capability references that resolve only into a hidden pack.
- Obtain skill roots only from `DiscoveredPack.skill_roots()` and apply pack-ID deduplication once at canonical source priority.
- Preserve explicit-root testability by parameterizing shared discovery rather than adding another filesystem scanner.
- Make invalid manifests fail explicitly at the pack boundary and never leak skills.
- Preserve top-level SDK laziness asserted by `tests/test_sdk_public_surface.py:3339-3386`.
- Add source/local/extra/environment/installed ordering, `_core`, duplicate, hidden-installed, deprecated, hidden-alias, invalid-manifest, and checkout-local pack cases in `tests/packs/test_pack_discovery_metadata.py` and `tests/test_skills.py`.

### 1.2 `[XHARD]` Remove theme and workspace element discovery — Phase 1 · L · Depends: 1.1

- In `astrid/core/element/registry.py`, `catalog.py`, and `__init__.py`, remove `ElementSource`, `default_sources()`, `load_source_elements()`, active-theme element loading, `WORKSPACE_ROOT`, `legacy_workspace`, and source-conflict warnings.
- Build the element registry exclusively from `discover_pack_metadata()` and pack-declared element roots; do not create pseudo-packs for absent theme/workspace sources.
- Remove discovery-only `active_theme`, `include_missing_roots`, and `elements --theme` inputs from `astrid/core/element/cli.py` and `astrid/sdk/discovery.py`.
- Update `astrid/core/timeline/validators/`, `astrid/packs/training/executors/pool_merge/run.py`, `astrid/packs/rendering/backends/remotion/run.py`, and `scripts/gen_effect_registry.py` to use pack metadata.
- Keep theme selection, pointers, state, and provenance as rendering data.
- Replace positive theme/workspace discovery expectations with negative no-scan rails in `tests/core/test_elements_registry.py`, `tests/timeline/test_effects_catalog.py`, `tests/timeline/test_timeline_elements_catalog.py`, and `tests/test_sdk_public_surface.py`.
- Preserve local-pack precedence and rendering behavior through `tests/packs/test_pack_local_priority.py`, `tests/packs/test_text_card_override.py`, and Remotion registry/code-generation tests.
- Assert every loaded element has `source == "pack:<id>"` and matching pack metadata.

### 1.3 Package and prove the canonical graph in wheels — Phase 1 · L · Depends: 1.1–1.2

- Replace the rendering-only package-data declaration in `pyproject.toml` with explicit coverage for:
  - `core/model_catalog/*.yaml`
  - existing rendering schemas and parity fixtures
  - `packs/bundled.yaml`
  - `packs/*/pack.yaml`
  - `packs/*/executors/*/executor.yaml`
  - `packs/*/orchestrators/*/orchestrator.yaml`
  - element manifests
  - rendering extension YAML
  - `packs/*/skill/SKILL.md`
  - nested executor skills
  - executor and orchestrator `STAGE.md` files
- Do not use a blanket recursive pack-root include.
- Extend `scripts/smoke_wheel_install.sh` to run outside the checkout with an empty `ASTRID_HOME` and prove:
  - Every ID in `astrid/packs/bundled.yaml` has its canonical manifest in the wheel.
  - The bundled inventory agrees with the tracked source tree before wheel construction.
  - Representative executor, orchestrator, element, nested skill, STAGE, and extension files ship.
  - `ModelRegistry.load_default()` and `LoraRegistry.load_default()` succeed.
  - Skills discover from the wheel’s source layer.
  - `include_installed=True` with an empty installed store is a no-op, not a loss of source packs.
- Prefer inventory-derived assertions over brittle fixed capability or pack counts.
- Preserve `import astrid` laziness and add a rail that loading registries does not eagerly import concrete generation, Reigh, or RunPod implementations.

## Phase 2 — extract concrete domain implementations

### 2.1 `[XHARD]` Move concrete generation backends into the generation pack — Phase 2 · M · Depends: 1.1, 1.3

- Move:
  - `astrid/core/generation/backends/fal.py`
  - `astrid/core/generation/backends/codex.py`
  - `astrid/core/generation/backends/vibecomfy.py`
  into `astrid/packs/generation/backends/`.
- Declare `cloud → FalBackend`, `codex → CodexBackend`, and `local → VibeComfyBackend` under `extensions.generation.backends` in `astrid/packs/generation/pack.yaml`.
- Use the existing, tested hook; do not add another extension framework. It is already defined in `astrid/core/pack/schemas/v1/pack.json:114-128` and consumed by generation registry/features/verbs and `astrid/core/pack/permissions.py:141`.
- Delete builtin seeding and hardcoded module strings from `astrid/core/generation/backends/registry.py:78-79,185-205`.
- Keep provider-neutral protocols, registry, taxonomy IDs, verbs, and feature contracts in core. A bare registry must be empty; default loading must populate it only from discovered manifests.
- Remove concrete exports and lazy concrete imports from `astrid/core/generation/backends/__init__.py`.
- Update generation executor imports, `codex_unavailable_reason`, golden patch targets, gateway generation resolution, SDK discovery, and model-catalog validation.
- Delete the six tracked files under `fal-voice-upscale/` in this change instead of repairing their private FAL-helper imports; they are uncalled scratch experiments already designated for removal and recoverable from Git history.
- Move concrete adapter tests from `tests/core/generation/` to `tests/packs/generation/`; update:
  - `tests/test_generation_backend_registry.py`
  - `tests/packs/builtin/generate_image/test_codex_backend.py`
  - `tests/test_sdk_public_surface.py`
  - generation parameter-map tests
  - wheel smoke
- Preserve the third-party descriptor rail in `tests/test_third_party_integration.py`.
- Add explicit negative tests for old core backend paths and hardcoded importlib strings.
- Extend wheel smoke to prove the generation manifest supplies all three descriptors and that removing that manifest removes them.

### 2.2 `[XHARD]` Move experiments, declare pack dependencies, and eliminate cross-pack entrypoint coupling — Phase 2 · L · Depends: 0.1, 1.1

- Add an optional `depends` field to `astrid/core/pack/schemas/v1/pack.json`, `astrid/core/pack/definition.py`, the loader, author validation, and scaffolding.
- Define `depends` as a sorted, unique list of pack IDs describing static Python support-module dependencies.
- Keep it distinct from the existing `dependencies.{python,npm,system}` block and from manifest-declared capability composition.
- Reject self-dependencies, dependency cycles, duplicates, malformed IDs, undeclared static cross-pack imports, and stale declarations in first-party root validation.
- Add `astrid/core/pack/import_policy.py` and `tests/packs/test_pack_import_policy.py` for the cross-pack AST rule.
- Do not use `install_tier` as an implicit dependency rule: all current bundled packs share `install_tier: core`, so it would hide rather than describe coupling.
- Do not add version solving, automatic installation, or a second registry. This task creates a truthful dependency contract and enforcement rail only.
- Inventory every current static and literal-dynamic cross-pack import before enabling the enforcement rail.
- Classify every cross-pack edge as exactly one of:
  - capability invocation, which must use canonical qualified capability dispatch
  - genuine reusable support code, which must live outside an executor/orchestrator `run.py` and have a matching `depends` declaration
  - accidental/private coupling, which must be removed or made pack-local
- Eliminate every import of another pack’s executor/orchestrator `run.py` before enabling the checker. Known hotspots include:
  - `astrid/packs/rendering/executors/sprite_sheet/` importing generation executor internals
  - understanding, editorial, and video-editing code importing `training.executors.asset_cache.run`
  - `video_editing.orchestrators.iteration_video` importing iteration executor `run.py`
  - `editorial.executors.refine` importing `video_editing.executors.cut.run`
  - video-editing orchestration importing editorial executor `run.py`
- Move only genuinely shared symbols into narrow owning-pack support modules; do not create a generic shared-helpers pack or a catch-all facade.
- Use canonical capability dispatch when the dependency is execution rather than library reuse.
- Declare the surviving support edges explicitly, including:
  - `editorial → training` for dataset-review state
  - `video_editing → editorial` for editorial arrangement support
  - `editorial → iteration` after the experiment move
- Move `astrid/core/experiments/` to `astrid/packs/iteration/experiments/` without a compatibility shim.
- Update iteration experiment import, prepare, review, and review-session entrypoints.
- Update `astrid/packs/editorial/executors/human_review/run.py`, the second current consumer, and add `iteration` to editorial’s declared `depends`.
- Move `tests/core/experiments/` under `tests/packs/iteration/experiments/` and update iteration/editorial tests and `STAGE.md` references.
- Add tests showing:
  - an undeclared cross-pack support import fails
  - declared support imports pass
  - executor/orchestrator `run.py` imports fail even when `depends` is declared
  - dependency cycles and stale declarations fail
  - removing a depended-on pack is reported clearly
- Finish the task with pack validation and the broad suite green; do not defer known violations to Phase 4.

### 2.3 `[XHARD]` Move RunPod maintenance into the RunPod pack without a new abstraction — Phase 2 · M · Depends: 0.1, 1.1

- Move `astrid/core/integrations/runpod/storage.py` and `sweeper.py` into support code under `astrid/packs/runpod/`.
- Add canonical executors and manifests for:
  - `runpod.sweep`
  - `runpod.list_volumes`
  - `runpod.ensure_storage`
- Do not add a core `RunPodMaintenance` Protocol or another extension hook: after the callers below are rewired, core has no remaining consumer that justifies one.
- Preserve sweep dry-run diagnostics and storage recovery messages.
- Rewire the transitional top-level `runpod` handler through canonical executor dispatch rather than statically importing pack code.
- Remove `_check_runpod_stale_handles()` from `astrid/core/doctor.py`; replace it with executor coverage and do not add a doctor extension hook.
- Keep `require_existing_storage` pack-local. Replace training’s imported `ENSURE_STORAGE_HINT` with a local message pointing to `runpod.ensure_storage`; do not create a core interface solely to share a diagnostic string.
- Add a source comment in both diagnostic locations cross-referencing the canonical `runpod.ensure_storage` recovery contract so their advice cannot silently diverge.
- Update `astrid/packs/runpod/pack.yaml`, `skill/SKILL.md`, executor `_common.py`, and `astrid/packs/training/orchestrators/training_run/{compute_backends,config}.py`.
- Remove `astrid/core/integrations/runpod/` only after all imports have moved.
- Relocate or retarget `tests/packs/runpod/test_sweeper.py`, `test_ensure_storage.py`, `tests/test_sweeper_async.py`, `tests/test_sweeper_edges.py`, `tests/test_doctor_setup.py`, and task-mutation inventories.
- Add no `astrid/core/structure.py` exemption.

### 2.4 `[XHARD]` Invert the generic Reigh bridge state before extraction — Phase 2 · M · Depends: 0.1

- Create `astrid/core/timeline/asset_registry_state.py` for provider-neutral:
  - latest registry-event recovery
  - sidecar repair
  - record/source resolution
  - no-pruning merge semantics
- Move the generic logic currently buried in `astrid/core/integrations/reigh/local_bridge.py:485-549` into that host module.
- Make `astrid/core/timeline/asset_registry_edits.py` and the eventual pack bridge consume the new host helper.
- Extend the existing Protocol precedent in `astrid/core/contracts/remote_timeline.py` with only the remote load/save/list shapes needed by migration, editing, and worker callers.
- Keep remote implementations caller-injected: Reigh pack code constructs its implementation and passes it to the provider-neutral core Protocol consumer. Core does not discover or register a Reigh implementation.
- Preserve `astrid/core/timeline/{local_fs,supabase,selector,reigh_events,transfer}.py`.
- Keep event recovery, CAS, crash reconciliation, sidecar repair, no-op, and no-pruning behavior in `tests/timeline/test_asset_registry_sync.py`; move generic recovery tests out of `tests/integrations/reigh/test_local_bridge_helpers.py`.

### 2.5 `[XHARD]` Move the Reigh service domain and worker into the Reigh pack — Phase 2 · L · Depends: 2.4

- Move Reigh environment, provider, bridge transport, task client, remote timeline I/O, JWT/JWKS, append service, error, and worker implementations from:
  - `astrid/core/integrations/reigh/`
  - `astrid/core/integrations/worker/`
  into `astrid/packs/reigh/integration/` and pack executor support.
- Keep the host timeline/eventlog primitives in core and delete compatibility copies such as `event_construction.py` and the integration-local `supabase_client.py`.
- Add `reigh.worker`, preserving the long-running claim loop, signal handling, authentication, and qualified provenance.
- Add `reigh.serve_local_bridge` for the pack-owned HTTP/CORS/media transport, including `--projects-root`.
- Preserve top-level `astrid serve` as the documented, unbound host facade.
- Implement that facade by resolving the qualified `reigh.serve_local_bridge` definition through the existing canonical executor registry and a narrow host adapter; do not import a Reigh module path and do not add `extensions.reigh`, a service registry, or a general service framework.
- Prove removal of the Reigh manifest makes `astrid serve` fail with an actionable missing-capability error rather than falling back to core code.
- Preserve the `serve` session/output/shutdown contract without weakening normal executor project/session requirements.
- Add a deliberately narrow `reigh.timeline_edit` executor replacing only the existing remote operations:
  - `add-clip`
  - `move-clip`
  - `set-theme`
- Preserve PAT-by-default authentication, optional service-role auth, optimistic `expected_version`, three retries, `force=False`, and event descriptors. Do not expand it to the full local timeline CLI.
- Remove remote `projects list` because `reigh.reigh_data` already returns timelines.
- Remove remote `projects edit` from `astrid/core/cli/project.py` and `project_handlers.py` after `reigh.timeline_edit` is covered.
- Delete `scripts/node/ops_helper.mjs` only after proving its sole mutation caller is gone.
- Keep generic local project-store and local `timelines` commands in core.
- Update Reigh pack manifests, skill, permissions, STAGE files, `scripts/reigh_seed_timeline_events.py`, `tests/core/test_project_cli.py`, `tests/test_cli_gate.py`, provider tests, and `docs/architecture/timeline-event-sourcing/m6a-astrid-supabase-contract.md`.
- Ensure core gateway, project handlers, and timeline code contain neither static imports nor hardcoded module strings for Reigh pack implementations.

### 2.6 `[XHARD]` Close extraction imports, define the pack-facing kernel API, and remove CI path coupling — Phase 2 · L · Depends: 2.1–2.5

- Move Reigh-domain, worker, claim-loop, task-client, JWT, provider, and baseline tests under `tests/packs/reigh/`; retain host timeline/eventlog Protocol tests under `tests/timeline/`.
- State that `tests/packs/<id>/` is the extraction rail: tests remain centrally discoverable today, while a future pack split can move one complete subtree with `git mv`.
- Replace positive inventories in `tests/test_structure_contracts.py` and `tests/test_m2_public_surface.py` with negative rails for:
  - `core/experiments`
  - concrete generation backends
  - Reigh implementations
  - RunPod implementations
  - worker implementations
- Require repository searches for `astrid.core.integrations.{reigh,runpod,worker}` to return no live imports.
- Inventory every remaining static and literal-dynamic `astrid.core` import from `astrid/packs/` after Tasks 2.1–2.5; do not grandfather the current surface merely because packs use it.
- Extend `astrid/core/pack/import_policy.py` with the exact machine-readable pack-facing kernel module prefixes and enforce them through `validate_import_layering()`.
- Define the supported pack API around:
  - provider-neutral `astrid.core.contracts.*`
  - explicitly public foundation I/O, hashing, path, and project-path helpers
  - public executor/orchestrator execution APIs
  - public pack discovery, entrypoint, resolver, and metadata APIs
  - public project/runtime/session/task APIs required to launch capabilities
  - provider-neutral generation/model-catalog contracts
  - public rendering contracts, registries, assets, transport, service, profile, publication, and artifact APIs
  - public timeline/event-schema and thread-lineage APIs
  - individually admitted shared utilities, never a blanket `astrid.core.util.*` prefix
- Exclude from the supported set:
  - `astrid.core._shared.*`
  - modules or imported symbols beginning with `_`
  - CLI handlers and presentation helpers
  - `task.plan.verbs`, `session.current_run_state`, and `command_render`
  - concrete integration and generation-backend implementations
  - broad utility families admitted only because they happen to be used today
- Promote genuinely shared private helpers to an existing public foundation/contract module or make them pack-local before allowing the checker to pass; do not create a catch-all facade.
- Define “supported” in `docs/packs/contract.md`: pack authors may rely on the listed module paths, and removals require a documented migration/deprecation path. This is the Astrid pack contract, not yet a framework semver promise.
- Add negative fixtures proving packs cannot import private core modules, concrete integrations, unlisted utilities, CLI handlers, or private symbols.
- Wire the expanded import-layer checker into `scripts/reshape/run_ci_checks.sh`; add no exemptions.
- Replace depth-limited changed-file matching in `scripts/reshape/run_ci_checks.sh:139-163` with arbitrary-depth `astrid/**` selection and cover moved Reigh/RunPod paths in `tests/reshape/test_ci_changed_selection.py`.
- Keep the `astrid.core.session.identity` seed import only if it is admitted explicitly as a supported session API; otherwise route it through the public session surface.
- Update `.github/workflows/bridge-latency.yml` to trigger on `astrid/packs/reigh/**` while retaining `astrid/core/timeline/**`, and make checkout test the actual PR ref rather than a hardcoded external repository state.
- Keep all moved tests under `tests/` so broad discovery cannot silently lose them.

## Phase 3 — retire capability-shaped host aliases

### 3.1 Remove pure executor aliases — Phase 3 · S · Depends: 2.5

- Remove top-level:
  - `publish`
  - `publish-youtube`
  - `upload-youtube`
  - `reigh-data`
- Point users to:
  - `executors run reigh.publish`
  - `executors run youtube.upload`
  - `executors run reigh.reigh_data`
- Update `astrid/core/gateway/dispatch.py`, `help.py`, related gateway exports, `tests/test_pipeline_dispatch_aliases.py`, and social-publish tests.
- Add negative root-help and unknown-command assertions.
- Preserve session gating semantics while the aliases exist; none of these routes currently bypasses the bound-session gate.

### 3.2 Remove RunPod and worker host routes after executor parity — Phase 3 · M · Depends: 2.3, 2.5, 3.1

- Remove top-level `worker` only after `reigh.worker` covers the claim loop.
- Remove top-level `runpod` only after `runpod.sweep`, `runpod.list_volumes`, and `runpod.ensure_storage` cover its entire maintenance surface.
- Delete `astrid/core/gateway/runpod.py`, obsolete dispatch functions, help entries, and unused exports.
- Retain `_dispatch_executor_main` if still used by the permanent `serve` facade or other canonical host bridges.
- Keep `scratch`, `astrid/core/gateway/scratch.py`, `serve`, and the current unbound `serve` allowlist entry.
- Update the frozen allowlist assertions in `tests/test_cli_gate.py` in the same change.
- Add gateway-level negative coverage for all six retired tokens, including the previously untested `worker` route, plus positive `scratch` and `serve` coverage.
- Replace shortcut commands in RunPod, Reigh, and YouTube skills, STAGE files, recovery messages, and manifests with qualified executor invocations.

## Phase 4 — enforce pack layout and make the repository truthful

### 4.1 `[XHARD]` Canonicalize pack-private entrypoints — Phase 4 · L · Depends: Phase 3

- Convert `astrid/packs/blender/deploy.py` into the canonical `blender.deploy` executor.
- Do not create `blender.mesh_fetch`: there is no demonstrated standalone capability boundary, and it is already library support for `blender.render`.
- Move `astrid/packs/blender/mesh_fetch.py` beneath the `blender.render` support tree, preserve its library use, and remove its independent `__main__` surface.
- Move `render_core.py`, `renders/`, and `server/blender_render_server.py` beneath the appropriate executor support trees; retain necessary library imports but remove alternate user-facing `__main__` surfaces.
- Update Blender imports, presets, README, pack manifest, skill, STAGE files, and tests.
- Preserve the Task 2.2 rail proving no pack imports another pack’s executor/orchestrator `run.py`; this task performs only a residual search, not deferred cleanup.
- Classify rendering backend/planner/finalizer runners as manifest-private transport commands, not new executors:
  - `astrid/packs/rendering/run.py`
  - `backends/{ffmpeg,remotion,threejs}/run.py`
  - `planners/{legacy_hybrid,threejs_hybrid}/run.py`
  - `finalizers/ffmpeg/run.py`
- Have `astrid/core/rendering/transport.py` set the internal-invocation marker and guard those commands so direct subprocess invocation fails while manifest transport succeeds.
- Remove the unsupported `python -m astrid.sdk.rendering` claim from `astrid/sdk/rendering.py` and `docs/reference/sdk.md`; do not create another public CLI.
- Remove executable `__main__` behavior from the three unledgered generation golden demos; retain them only as non-runnable fixtures if tests still consume them, otherwise delete them.
- Add subprocess rails proving canonical capability/transport invocation works and direct pack-module invocation fails.
- Add a scoped search rail for stale `python -m astrid.packs.*` instructions while permitting exact manifest-private commands.

### 4.2 `[XHARD]` Enforce actual pack-root layout — Phase 4 · L · Depends: 4.1

- Extend `astrid/core/pack/validate_layout.py` to walk real pack-root entries rather than validating exception declarations alone.
- Permit:
  - `pack.yaml`
  - declared capability/content roots
  - `skill/`, `docs/`, `examples/`, `schemas/`, `fixtures/`, `golden/`
  - capability-local golden fixtures
  - Python package markers
  - manifest-declared extension roots
  - narrowly documented support-library roots declared by the owning manifest
- Declare `astrid/packs/editorial/hype/` as library-only support in `astrid/packs/editorial/pack.yaml`; prove it exposes no independent discovery or CLI surface.
- Preserve rendering’s declared `backends/`, `planners/`, and `finalizers/` extension layout.
- Reject undeclared loose files and directories with actionable paths.
- Move `astrid/packs/fal/tests/test_h3_video.py` to `tests/packs/fal/`.
- Add positive rendering/editorial/golden/fixture tests and negative Blender-style junk cases in `tests/packs/test_pack_layout_contract.py` and `tests/packs/test_packs_validate.py`.

### 4.3 Close root-hygiene gaps and root-writing tests — Phase 4 · M · Depends: 2.1, 4.2

- Verify `fal-voice-upscale/` is absent after Task 2.1 and remove it from `ROOT_DIR_ALLOWLIST` in `scripts/reshape/check_repo_hygiene.py`.
- Add `*.mp3` to `.gitignore` media rules and the hygiene checker’s tracked-runtime-media rules.
- Extend `find_unknown_root_entries()` and `tests/reshape/test_repo_hygiene.py` to inspect actual root filesystem entries as well as tracked Git paths.
- Keep the hygiene checker explicitly product-repository-owned; do not expose it as part of the pack/kernel conformance API.
- Replace root-directed temporary directories with `tmp_path`, `TemporaryDirectory()`, or the system temp directory in:
  - `tests/test_pipeline_caching.py`
  - `tests/core/test_project_cli.py`
  - `tests/test_managed_write_paths.py`
  - worker/claim-loop tests
  - `tests/core/test_executor_cli.py`
  - `tests/packs/reigh/test_open_in_reigh.py`
  - `tests/timeline/test_edit_helpers.py`
- Do not add speculative deletion rules for absent `external/`, competition, `mgt-*`, or pipeline-test directories.
- Do not touch `.oracle-threejs-archive/`; it is user-owned staged work outside HEAD. Run final hygiene validation from a clean checkout.

### 4.4 Complete the documentation and CI truth pass — Phase 4 · M · Depends: 4.1–4.3

- `docs/packs/contract.md`:
  - locked kernel with residency reasons and portability dispositions
  - Arnold’s lifecycle/compiler justification
  - accepted `astrid.packs.*` product namespace
  - supported pack-facing kernel import set
  - `depends` semantics and acyclic support-dependency rule
  - prohibition on cross-pack executor/orchestrator entrypoint imports
  - hidden versus deprecated policy
  - extension-hook admission rule
  - product/framework classification for `remotion/`, `themes/`, and Git-aware tooling
  - documented `in_process.py` and Arnold host-shape extraction blockers
- `docs/packs/pack-taxonomy.md`: `_core` as a manifest-backed system pack; visible `builtin`; Blender included; all current shipped manifests accurately described as `install_tier: core`; correct alias-carrier claims; bundled inventory derived from `astrid/packs/bundled.yaml`.
- `docs/architecture/repo-shape.md`: actual `astrid/core/execution/{executor,orchestrator}` paths, current gateway modules, complete kernel directories, and no legacy workspace source.
- `docs/architecture/import-tiers.md`: packs may consume only the machine-readable supported kernel API and declared pack support dependencies; core may use only the fixed runtime bridge and provider-neutral Protocols.
- `docs/contracts/platform-contract.md` and `docs/architecture/repo-shape.md`: derive the SDK public surface accurately—currently 32 exports, not 28.
- `docs/packs/adapter-packs.md`: include `fal.h3_video`.
- `docs/reference/{architecture,sdk}.md`: qualified capability routes, pack-only discovery, and no silent module CLI.
- Generation, iteration, Reigh, RunPod, YouTube, Blender, rendering, builtin, and `_core` skills/manifests/STAGE files: current paths, dependencies, and commands.
- `docs/contracts/integration_contracts.md` and `docs/contracts/asset-resolution-generation-bridge-contract.md`: pack ownership while retaining documented `astrid serve`.
- `docs/guides/ci-lanes.md` and documentation-command verification: arbitrary-depth changed-file selection and current workflow paths.
- Regenerate `astrid/packs/_core/skill/SKILL.md` from the deterministic bundled inventory.
- Search for stale core-domain paths, removed aliases, obsolete pack counts, undeclared pack imports, unsupported kernel imports, direct pack-module commands, and old `_core` exception language.

### 4.5 Run the full closure gate — Phase 4 · M · Depends: 4.1–4.4

- Run `python3 -m astrid packs validate astrid/packs`.
- Run schema, pack dependency/import-policy, pack discovery, skill, element, structure, gateway, doctor, generation, iteration, Reigh, RunPod, rendering, layout, CI-selection, and hygiene test groups.
- Run `scripts/smoke_wheel_install.sh` outside the source checkout with an empty `ASTRID_HOME`.
- Run `scripts/reshape/run_ci_checks.sh` and the full broad test suite; do not rely on the changed-file fast lane alone.
- Run Remotion typechecking and renderer-parity tests.
- Verify the import-layer checker remains green with zero new exemptions.
- Verify every static cross-pack support import has a matching `depends` declaration, no declaration is stale, and the dependency graph is acyclic.
- Verify no pack imports another pack’s executor/orchestrator `run.py`.
- Verify every pack-to-core import belongs to the supported machine-readable kernel API.
- Run generated-artifact check mode and prove the capability index and `_core/skill/SKILL.md` match clean regeneration.
- Search for retired gateway tokens, deleted core-domain imports, hardcoded old backend module strings, obsolete paths, unsupported direct module commands, and undeclared pack dependencies.
- Verify all executors, orchestrators, elements, skills, and concrete generation backends originate from manifest-backed packs.
- Verify `astrid/core/integrations/` contains only the retained Arnold implementation domain.
- Verify no new product-specific pack bindings have joined the documented `in_process.py` and Arnold host-shape extraction debts.
- Verify the deterministic capability index and repository hygiene from a clean checkout.
- Verify Reigh tests are under `tests/packs/reigh/`, iteration experiment tests are under `tests/packs/iteration/`, and every other moved pack domain follows `tests/packs/<id>/`.

# EXPLORE

- **Cross-pack invocation versus support classification:** before Task 2.2, generate the complete cross-pack import graph, including literal-dynamic imports. For each edge, identify whether it invokes a capability, consumes genuine support code, or reaches into a private entrypoint. Confirm the resulting declared support graph is acyclic before enabling enforcement.
- **Sessionless `serve` delegation:** before Task 2.5, trace the gateway, executor runner, project/session gate, output-root handling, provenance behavior, and shutdown path. Confirm `astrid serve --projects-root ...` can resolve and launch `reigh.serve_local_bridge` without granting all executors a sessionless mode. Confirm remote-timeline implementations remain caller-injected rather than registered through a new extension. If the standard executor runner cannot preserve this contract, use the smallest dedicated host adapter rather than introducing a general service framework.
- **Manifest-private command boundary:** before Task 4.1, inventory the exact rendering transport and deployed Blender-service subprocess callers so the shared internal-invocation guard covers every legitimate caller without creating another public CLI surface.
- **Pack-facing kernel import classification:** before closing Task 2.6, generate the complete post-extraction pack-to-core import inventory and classify each path against the stated supported families. Promote only genuinely shared contracts; keep private helpers pack-local instead of widening the allowed prefixes.

# OPEN QUESTIONS

- None.

# INTEGRATED

- **Task 2.2 sequencing fix:** eliminate all cross-pack executor/orchestrator `run.py` imports before enabling the dependency checker, keeping every phase green.
- **Blender KISS cut:** keep mesh fetching as private `blender.render` support instead of inventing an unsupported `blender.mesh_fetch` capability.

# REJECTED

- **Deferring known cross-pack entrypoint violations to Task 4.1:** this would make Task 2.2’s new enforcement rail fail immediately.
- **Promoting `mesh_fetch.py` to a standalone executor:** current evidence supports library reuse only; a public capability would be speculative.
