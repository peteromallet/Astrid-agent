# Batch 1 — Lock the kernel contract · Phase 0 · Flash
Tasks: **0.1 Lock the kernel and green import baseline**

- Update `docs/packs/contract.md` with the authoritative kernel table. For every retained area, record its responsibility, current kernel-residency reason, and future portability disposition:
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
- Justify Arnold explicitly: `start`, `next`, `ack`, and `abort` default to Arnold in `astrid/core/gateway/dispatch.py`; manifest/folder orchestrators compile through Arnold lowering in `astrid/core/execution/orchestrator/pipeline.py`; the authoring DSL does the same in `astrid/core/orchestrate/compile.py`. Arnold is the default lifecycle host and compiler target, not an optional service adapter.
- Keep Arnold loading lazy through `astrid/core/integrations/arnold/host/compat.py`; retain the legacy task engine as an explicit fallback.
- State that concrete generation adapters, discoverable capabilities, product workflows, and optional service domains belong in manifest-backed packs.
- Retain `astrid/core/integrations/arnold/`, `astrid/core/orchestrate/`, `astrid/core/timeline/`, `astrid/core/timeline/eventlog/`, `astrid scratch`, and `astrid serve` in host/protocol roles.
- Classify `remotion/` as Astrid product/rendering-pack runtime substrate and `themes/` as product rendering data, not reusable framework kernel.
- Classify `scripts/gen_capability_index.py`, `scripts/reshape/check_repo_hygiene.py`, changed-file CI selection, and other Git-aware checks as product-repository tooling.
- Accept `astrid.packs.*` as the stable Astrid product namespace. Keep `python3 -m astrid` and `<pack>.<name>` IDs unchanged.
- Record, without solving, the pre-framework-extraction blockers in `astrid/core/runtime/in_process.py` and `astrid/core/integrations/arnold/host/shapes.py`; state that they are not precedent for more product knowledge in core.
- Record the verified zero-violation baseline for `validate_import_layering()` and `validate_repo_structure()`.
- Preserve `astrid/core/runtime/in_process.py` as the sole static core-to-pack import exception and the existing manifest-driven dynamic resolver allowlist in `astrid/core/structure.py`; add no exemptions.
- Generalize the hardcoded-import rail to detect literal `importlib.import_module()` targets throughout `astrid/core/`, including executable/module strings involved in each extraction.
- Document the extension-hook admission rule: require a real core consumer and interchangeable implementations; otherwise use dependency injection, canonical capability dispatch, or a narrow existing host facade.
- Add contract tests for Arnold residency, product/framework classifications, the accepted product namespace, and both documented extraction blockers.

CHECKPOINT:

- `python3 -c 'from astrid.core.structure import validate_import_layering, validate_repo_structure; assert validate_import_layering() == []; assert validate_repo_structure().ok'` exits zero.
- `pytest -q tests/test_structure_contracts.py tests/test_gateway_lifecycle_engine_dispatch.py` passes.
- `docs/packs/contract.md` contains the kernel table, Arnold lifecycle/compiler rationale, `astrid.packs.*` decision, product classifications, extension-hook rule, and both named extraction blockers.
- Repository tests prove `astrid/core/runtime/in_process.py` remains the sole static core-to-pack exception and literal `importlib.import_module()` targets are checked generically.
- `python3 -m astrid packs validate astrid/packs` and `scripts/reshape/run_ci_checks.sh` pass with no new exemptions.

# Batch 2 — Legalize the `_core` system pack · Phase 0 · Sol(XHARD)
Tasks: **0.2 `[XHARD]` Make `_core` a legal, manifest-backed system pack**

- Change validation before or atomically with adding `astrid/packs/_core/pack.yaml`; avoid the runtime-fatal interaction among `_common.py`, `loader.py`, reserved IDs, and folder/manifest equality.
- Update `astrid/core/pack/schemas/v1/_defs.json` so `pack_id` accepts a normal ID or reserved literal `_core`; trust remains a loader concern.
- Add one provenance seam in `astrid/core/pack/loader.py`, such as `is_trusted_system_pack_source(pack_id, manifest_path)`, and route all reserved-system-ID loader/validator decisions through it.
- Initially trust `_core` only when its resolved manifest parent is the canonical shipped source root. Preserve the seam for a future distribution-origin implementation.
- Reject user, local, extra, environment, installed, symlinked, relative-path, and alias-fed `_core` claims. Preserve folder/ID equality and reject `_core.<name>` capability IDs.
- Add `astrid/packs/_core/pack.yaml` with system metadata, the existing skill root, and no executors, orchestrators, elements, aliases, dependencies, or extensions.
- Remove manifest-less skill-shell rules from `astrid/core/pack/validate_first_party.py` and `astrid/core/pack/validate_layout.py`.
- Add `astrid/skills/branding.py` as the single branding seam for `_core`, the `astrid` harness-link name, display name, and managed markers.
- Consume branding from:
  - `astrid/skills/harnesses/base.py`
  - `astrid/skills/harnesses/claude.py`
  - `astrid/skills/harnesses/codex.py`
  - `astrid/skills/harnesses/hermes.py`
  - `astrid/skills/__init__.py`
  - `astrid/skills/registry.py`
  - `astrid/skills/cli.py`
- Preserve literal `python3 -m astrid` commands and Python package paths.
- Extend `tests/packs/test_pack_yaml_schema.py`, `test_pack_discovery.py`, `test_pack_layout_contract.py`, `test_packs_validate.py`, `test_packs_cli.py`, `tests/test_skills.py`, and wheel-smoke coverage for schema acceptance, canonical trust, noncanonical rejection, empty capabilities, discovery, and branding invariants.

CHECKPOINT:

- `astrid/packs/_core/pack.yaml` and `astrid/skills/branding.py` exist; `_core/pack.yaml` declares no capabilities, aliases, dependencies, or extensions.
- `pytest -q tests/packs/test_pack_yaml_schema.py tests/packs/test_pack_discovery.py tests/packs/test_pack_layout_contract.py tests/packs/test_packs_validate.py tests/packs/test_packs_cli.py tests/test_skills.py` passes.
- Tests cover canonical `_core` acceptance and rejection from symlink, relative, installed-store, extra-root, environment-root, local, and alias-fed sources.
- `rg -n 'manifest.?less|skill.?shell' astrid/core/pack/validate_first_party.py astrid/core/pack/validate_layout.py` finds no live `_core` exception.
- `python3 -m astrid packs validate astrid/packs` and `scripts/reshape/run_ci_checks.sh` pass.

# Batch 3 — Establish the bundled-product inventory · Phase 0 · Flash
Tasks: **0.3 Establish one deterministic product-owned first-party inventory**

- Replace `_FIRST_PARTY_PACK_IDS` and `_FIRST_PARTY_INTERNAL_DIRS` in `astrid/core/pack/validate_first_party.py` with `astrid/packs/bundled.yaml`.
- Include the complete tracked manifest-backed bundled set, including `blender` and `_core`.
- Make generic first-party validation consume an explicit adjacent inventory rather than embedding Astrid IDs, repository-root assumptions, or Git commands in kernel Python.
- Recognize the Astrid product pack root through `bundled.yaml`; validate every listed pack and derive displayed counts, documentation, and tests from it.
- Remove hardcoded bundled-pack counts such as `19` from `astrid/core/pack/cli_basic.py`.
- Keep `discord_local` and `seedance_local` as checkout-local personal packs excluded by `.git/info/exclude`; do not inventory, delete, restore, or suppress them.
- Make `scripts/gen_capability_index.py` select IDs from `bundled.yaml` and verify every selected manifest is Git-tracked.
- Add parity checks among `bundled.yaml`, tracked bundled pack directories, and packaged wheel inventory.
- Add an untracked personal-pack fixture proving runtime discovery works while committed-index generation excludes it.
- Add check modes for `_core/skill/SKILL.md` and capability-index generation.
- Regenerate `astrid/packs/_core/skill/SKILL.md` from the product inventory in a clean checkout.
- Keep `astrid/packs/builtin/pack.yaml` visible, accurately describe `builtin.agent_probe`, and add a consistency test.

CHECKPOINT:

- `astrid/packs/bundled.yaml` exists, includes `_core` and `blender`, and excludes `discord_local` and `seedance_local`.
- `rg -n '_FIRST_PARTY_PACK_IDS|_FIRST_PARTY_INTERNAL_DIRS' astrid/core/pack/validate_first_party.py` returns nothing.
- `rg -n '(^|[^0-9])19([^0-9]|$)' astrid/core/pack/cli_basic.py` returns nothing.
- `pytest -q tests/packs/test_packs_shipped_ids.py tests/packs/test_packs_gitignore_filter.py tests/packs/test_pack_discovery.py tests/packs/test_packs_validate.py tests/packs/test_packs_cli.py` passes, including inventory/tree/wheel parity and untracked-personal-pack behavior.
- The batch-defined generation check commands for the capability index and `_core/skill/SKILL.md` exit zero, and regeneration leaves both committed outputs unchanged.
- `python3 -m astrid packs validate astrid/packs` and `scripts/reshape/run_ci_checks.sh` pass.

# Batch 4 — Unify skills and elements on the canonical graph · Phase 1 · Sol(XHARD)
Tasks: Execute **1.1** before **1.2** within this batch.

**1.1 `[XHARD]` Route skills through the canonical discovered-pack stream**

- Refactor `astrid/skills/discovery.py` to consume ordered `DiscoveredPack` records from `astrid/core/pack/discovery.py` across source, local, extra, environment, and installed roots.
- Delete direct `PACKS_DIR.iterdir()` walking, manifest-less fallback, swallowed manifest errors, and duplicate `_scan_discovered_packs()` traversal.
- Fix `ASTRID_PACKS_PATH` skill discovery.
- Exclude hidden packs consistently from source and installed discovery.
- Document hidden versus deprecated policy in `docs/packs/contract.md`; reject aliases and capability references resolving only into hidden packs.
- Obtain skill roots only from `DiscoveredPack.skill_roots()` and deduplicate pack IDs once at canonical source priority.
- Preserve explicit-root testability by parameterizing shared discovery.
- Make invalid manifests fail explicitly at the pack boundary and never leak skills.
- Preserve top-level SDK laziness.
- Add source/local/extra/environment/installed ordering, `_core`, duplicate, hidden-installed, deprecated, hidden-alias, invalid-manifest, and checkout-local cases in `tests/packs/test_pack_discovery_metadata.py` and `tests/test_skills.py`.

**1.2 `[XHARD]` Remove theme and workspace element discovery**

- Remove `ElementSource`, `default_sources()`, `load_source_elements()`, active-theme element loading, `WORKSPACE_ROOT`, `legacy_workspace`, and source-conflict warnings from `astrid/core/element/registry.py`, `catalog.py`, and `__init__.py`.
- Build the element registry exclusively from `discover_pack_metadata()` and pack-declared element roots; do not create pseudo-packs.
- Remove discovery-only `active_theme`, `include_missing_roots`, and `elements --theme` inputs from `astrid/core/element/cli.py` and `astrid/sdk/discovery.py`.
- Update `astrid/core/timeline/validators/`, `astrid/packs/training/executors/pool_merge/run.py`, `astrid/packs/rendering/backends/remotion/run.py`, and `scripts/gen_effect_registry.py` to use pack metadata.
- Keep theme selection, pointers, state, and provenance as rendering data.
- Replace positive theme/workspace discovery expectations with negative no-scan rails in `tests/core/test_elements_registry.py`, `tests/timeline/test_effects_catalog.py`, `tests/timeline/test_timeline_elements_catalog.py`, and `tests/test_sdk_public_surface.py`.
- Preserve local-pack precedence and rendering behavior through `tests/packs/test_pack_local_priority.py`, `tests/packs/test_text_card_override.py`, and Remotion registry/code-generation tests.
- Assert every loaded element has `source == "pack:<id>"` and matching pack metadata.

CHECKPOINT:

- `pytest -q tests/packs/test_pack_discovery_metadata.py tests/test_skills.py tests/core/test_elements_registry.py tests/core/test_elements_cli.py tests/timeline/test_effects_catalog.py tests/timeline/test_timeline_elements_catalog.py tests/test_sdk_public_surface.py tests/packs/test_pack_local_priority.py tests/packs/test_text_card_override.py tests/packs/rendering/test_remotion_element_generation.py tests/packs/rendering/test_render_remotion_registry.py` passes.
- `rg -n 'PACKS_DIR\\.iterdir|_scan_discovered_packs|ElementSource|default_sources|load_source_elements|legacy_workspace|WORKSPACE_ROOT' astrid/skills/discovery.py astrid/core/element astrid/sdk/discovery.py` returns nothing.
- `python3 -m astrid skills list --json` succeeds and includes the canonical `_core` skill; environment-root skill coverage passes in tests.
- `python3 -m astrid elements --help` contains no `--theme`, and no SDK discovery signature retains `active_theme` or `include_missing_roots`.
- Tests prove hidden packs and hidden-only aliases are undiscoverable, deprecated packs remain discoverable with metadata, invalid manifests leak no skills, and all elements report `source == "pack:<id>"`.
- `python3 -m astrid packs validate astrid/packs` and `scripts/reshape/run_ci_checks.sh` pass.

# Batch 5 — Package and prove the canonical graph · Phase 1 · Flash
Tasks: **1.3 Package and prove the canonical graph in wheels**

- Replace rendering-only package data in `pyproject.toml` with explicit coverage for:
  - `core/model_catalog/*.yaml`
  - rendering schemas and parity fixtures
  - `packs/bundled.yaml`
  - `packs/*/pack.yaml`
  - executor and orchestrator manifests
  - element manifests
  - rendering extension YAML
  - pack skills and nested executor skills
  - executor and orchestrator `STAGE.md` files
- Do not use a blanket recursive pack-root include.
- Extend `scripts/smoke_wheel_install.sh` to run outside the checkout with empty `ASTRID_HOME` and prove:
  - every bundled ID has its canonical manifest
  - source-tree and bundled inventory parity before wheel construction
  - representative executor, orchestrator, element, nested-skill, STAGE, and extension files ship
  - `ModelRegistry.load_default()` and `LoraRegistry.load_default()` succeed
  - skills discover from the wheel source layer
  - empty installed-store inclusion is a no-op
- Prefer inventory-derived assertions over fixed counts.
- Preserve `import astrid` laziness and ensure registry loading does not eagerly import concrete generation, Reigh, or RunPod implementations.

CHECKPOINT:

- `pyproject.toml` explicitly lists all required package-data families and contains no blanket recursive `astrid/packs/**` include.
- `pytest -q tests/core/rendering/test_package_data.py tests/packs/test_pack_discovery_metadata.py tests/test_skills.py tests/test_sdk_public_surface.py tests/core/test_generation_backend_registry.py` passes.
- `scripts/smoke_wheel_install.sh` passes from outside the checkout with an empty temporary `ASTRID_HOME`.
- Wheel-smoke output proves inventory parity, representative manifest/skill/STAGE/extension inclusion, model and LoRA default loading, source-layer skill discovery, and empty installed-store behavior.
- SDK/import-laziness tests prove registry loading does not import concrete generation, Reigh, or RunPod implementation modules.
- `python3 -m astrid packs validate astrid/packs` and `scripts/reshape/run_ci_checks.sh` pass.

# Batch 6 — Extract generation and RunPod implementations · Phase 2 · Sol(XHARD)
Tasks: **2.1** and **2.3**.

**2.1 `[XHARD]` Move concrete generation backends into the generation pack**

- Move `fal.py`, `codex.py`, and `vibecomfy.py` from `astrid/core/generation/backends/` to `astrid/packs/generation/backends/`.
- Declare `cloud → FalBackend`, `codex → CodexBackend`, and `local → VibeComfyBackend` under `extensions.generation.backends` in `astrid/packs/generation/pack.yaml`.
- Use the existing generation extension hook; add no extension framework.
- Remove builtin seeding and hardcoded module strings from `astrid/core/generation/backends/registry.py`.
- Keep provider-neutral protocols, registry, taxonomy IDs, verbs, and feature contracts in core. A bare registry is empty; defaults load only from discovered manifests.
- Remove concrete exports and lazy imports from `astrid/core/generation/backends/__init__.py`.
- Update generation executors, `codex_unavailable_reason`, golden patch targets, gateway resolution, SDK discovery, and model-catalog validation.
- Delete all six tracked files under `fal-voice-upscale/`.
- Move concrete adapter tests from `tests/core/generation/` to `tests/packs/generation/`; update registry, Codex backend, SDK-surface, parameter-map, and wheel-smoke tests.
- Preserve the third-party descriptor rail.
- Add negative tests for old core-backend paths and hardcoded importlib strings.
- Extend wheel smoke to prove all three descriptors come from the generation manifest and disappear when that manifest is removed.

**2.3 `[XHARD]` Move RunPod maintenance into the RunPod pack without a new abstraction**

- Move `astrid/core/integrations/runpod/storage.py` and `sweeper.py` under `astrid/packs/runpod/`.
- Add canonical executors and manifests for `runpod.sweep`, `runpod.list_volumes`, and `runpod.ensure_storage`.
- Add no core RunPod protocol or extension hook.
- Preserve sweep dry-run diagnostics and storage recovery messages.
- Rewire the transitional top-level `runpod` handler through canonical executor dispatch.
- Remove `_check_runpod_stale_handles()` from `astrid/core/doctor.py` and replace it with executor coverage.
- Keep `require_existing_storage` pack-local. Replace training’s imported `ENSURE_STORAGE_HINT` with a local message pointing to `runpod.ensure_storage`; cross-reference the recovery contract in both diagnostic locations.
- Update the RunPod manifest, skill, executor `_common.py`, and training-run `compute_backends` and `config`.
- Remove `astrid/core/integrations/runpod/` after all imports move.
- Relocate or retarget sweeper, storage, async, edge, doctor, and task-mutation tests.
- Add no `astrid/core/structure.py` exemption.

CHECKPOINT:

- `astrid/packs/generation/backends/{fal,codex,vibecomfy}.py` exist; the corresponding `astrid/core/generation/backends/` files do not.
- `astrid/packs/runpod/` owns storage and sweeper support plus manifests for `runpod.sweep`, `runpod.list_volumes`, and `runpod.ensure_storage`; `astrid/core/integrations/runpod/` does not exist.
- `fal-voice-upscale/` does not exist.
- `rg -n 'astrid\\.core\\.generation\\.backends\\.(fal|codex|vibecomfy)|astrid\\.core\\.integrations\\.runpod|import_module\\([^)]*core\\.generation\\.backends' astrid tests scripts` returns no live implementation import or hardcoded module string outside explicit negative-test fixtures.
- `pytest -q tests/core/test_generation_backend_registry.py tests/packs/generation tests/packs/builtin/generate_image/test_codex_backend.py tests/test_sdk_public_surface.py tests/packs/runpod tests/test_doctor_setup.py tests/test_third_party_integration.py` passes.
- Tests prove a bare generation registry is empty, the generation manifest supplies exactly the three descriptors, removal of that manifest removes them, and RunPod diagnostics retain dry-run/recovery behavior.
- `python3 -m astrid packs validate astrid/packs` and `scripts/reshape/run_ci_checks.sh` pass with no new structure exemption.

# Batch 7 — Declare pack dependencies and move experiments · Phase 2 · Flash
Tasks: **2.2 Move experiments, declare pack dependencies, and eliminate cross-pack entrypoint coupling**

- Add optional `depends` to `astrid/core/pack/schemas/v1/pack.json`, `astrid/core/pack/definition.py`, loader, author validation, and scaffolding.
- Define it as a sorted, unique list of pack IDs for static Python support-module dependencies, distinct from external dependencies and capability composition.
- Reject self-dependencies, cycles, duplicates, malformed IDs, undeclared imports, and stale declarations.
- Add `astrid/core/pack/import_policy.py` and `tests/packs/test_pack_import_policy.py`.
- Do not use `install_tier` as a dependency rule and do not add version solving, automatic installation, or another registry.
- Inventory static and literal-dynamic cross-pack imports and classify every edge as capability invocation, reusable support code, or accidental/private coupling.
- Eliminate every import of another pack’s executor/orchestrator `run.py`, including rendering→generation internals, understanding/editorial/video-editing→training cache code, video-editing→iteration executor entrypoints, editorial→video-editing cut internals, and video-editing→editorial executor entrypoints.
- Move only genuinely shared symbols into narrow owning-pack support modules; use qualified capability dispatch for execution dependencies.
- Declare surviving support edges, including `editorial → training`, `video_editing → editorial`, and `editorial → iteration`.
- Move `astrid/core/experiments/` to `astrid/packs/iteration/experiments/` without a compatibility shim.
- Update iteration experiment entrypoints and `astrid/packs/editorial/executors/human_review/run.py`.
- Move `tests/core/experiments/` to `tests/packs/iteration/experiments/` and update tests and `STAGE.md` references.
- Add tests for undeclared imports, valid declarations, forbidden entrypoint imports even when declared, cycles, stale declarations, and missing depended-on packs.
- Finish with pack validation and the broad suite green; defer no known violation.

CHECKPOINT:

- `astrid/core/pack/import_policy.py` and `tests/packs/test_pack_import_policy.py` exist.
- `astrid/core/experiments/` and `tests/core/experiments/` do not exist; `astrid/packs/iteration/experiments/` and `tests/packs/iteration/experiments/` exist.
- `pytest -q tests/packs/test_pack_import_policy.py tests/packs/iteration tests/packs/iteration/experiments tests/packs/editorial tests/packs/video_editing` passes.
- Import-policy tests prove sorted/unique declarations, malformed/self/cyclic/stale rejection, missing-dependency diagnostics, undeclared-edge rejection, and unconditional rejection of cross-pack executor/orchestrator `run.py` imports.
- The machine-generated cross-pack graph is acyclic and every surviving static support edge has exactly one matching `depends` declaration.
- Repository AST/search rails find no pack importing another pack’s executor or orchestrator `run.py`.
- `python3 -m astrid packs validate astrid/packs` and `scripts/reshape/run_ci_checks.sh` pass.

# Batch 8 — Invert and extract the Reigh service domain · Phase 2 · Sol(XHARD)
Tasks: Execute **2.4** before **2.5** within this batch.

**2.4 `[XHARD]` Invert the generic Reigh bridge state before extraction**

- Create `astrid/core/timeline/asset_registry_state.py` for provider-neutral latest-event recovery, sidecar repair, record/source resolution, and no-pruning merge semantics.
- Move generic logic from `astrid/core/integrations/reigh/local_bridge.py` into that host module.
- Make `astrid/core/timeline/asset_registry_edits.py` and the eventual pack bridge consume the helper.
- Extend `astrid/core/contracts/remote_timeline.py` only with load/save/list shapes needed by migration, editing, and worker callers.
- Keep remote implementations caller-injected; add no Reigh discovery or registration.
- Preserve `astrid/core/timeline/{local_fs,supabase,selector,reigh_events,transfer}.py`.
- Keep event recovery, CAS, crash reconciliation, sidecar repair, no-op, and no-pruning tests in `tests/timeline/test_asset_registry_sync.py`; move generic recovery tests out of Reigh helper tests.

**2.5 `[XHARD]` Move the Reigh service domain and worker into the Reigh pack**

- Move Reigh environment, provider, bridge transport, task client, remote timeline I/O, JWT/JWKS, append service, error, and worker implementations from `astrid/core/integrations/reigh/` and `astrid/core/integrations/worker/` into `astrid/packs/reigh/integration/` and executor support.
- Keep timeline/eventlog host primitives in core; delete compatibility copies such as `event_construction.py` and integration-local `supabase_client.py`.
- Add `reigh.worker`, preserving the claim loop, signals, authentication, and qualified provenance.
- Add `reigh.serve_local_bridge`, including `--projects-root`.
- Preserve top-level unbound `astrid serve`, implemented by resolving `reigh.serve_local_bridge` through the canonical executor registry and a narrow host adapter.
- Add no hardcoded Reigh module path, `extensions.reigh`, service registry, or general service framework.
- Prove removal of the Reigh manifest yields an actionable missing-capability error.
- Preserve the `serve` session/output/shutdown contract without making all executors sessionless.
- Add narrow `reigh.timeline_edit` operations for `add-clip`, `move-clip`, and `set-theme`, preserving PAT defaults, optional service-role auth, `expected_version`, three retries, `force=False`, and event descriptors.
- Remove remote `projects list`; remove remote `projects edit` from `astrid/core/cli/project.py` and `project_handlers.py` after executor coverage.
- Delete `scripts/node/ops_helper.mjs` after its sole mutation caller is gone.
- Keep generic local project storage and local `timelines` commands in core.
- Update Reigh manifests, skill, permissions, STAGE files, seed script, project/gateway/provider tests, and the Supabase contract document.
- Ensure gateway, project handlers, and timeline code contain no static import or hardcoded module string for Reigh implementations.

CHECKPOINT:

- `astrid/core/timeline/asset_registry_state.py` exists and `pytest -q tests/timeline/test_asset_registry_sync.py` passes all recovery, CAS, reconciliation, sidecar, no-op, and no-pruning cases.
- `astrid/core/integrations/reigh/` and `astrid/core/integrations/worker/` do not exist; Reigh implementation and worker code exists under `astrid/packs/reigh/`.
- Reigh manifests expose `reigh.worker`, `reigh.serve_local_bridge`, and `reigh.timeline_edit`; tests cover only `add-clip`, `move-clip`, and `set-theme` remote edits.
- `scripts/node/ops_helper.mjs` is absent, and remote `projects list/edit` code is absent from `astrid/core/cli/project.py` and `astrid/core/cli/project_handlers.py`.
- `rg -n 'astrid\\.core\\.integrations\\.(reigh|worker)|import_module\\([^)]*(reigh|worker)' astrid scripts` returns no live implementation import or hardcoded module string.
- `pytest -q tests/timeline tests/integrations/reigh tests/packs/reigh tests/core/test_project_cli.py tests/session/test_cli_gate.py tests/test_cli_gate.py` passes, including sessionless `serve`, shutdown/output behavior, missing-manifest diagnostics, worker claim-loop behavior, and caller-injected remote timeline implementations.
- `python3 -m astrid packs validate astrid/packs` and `scripts/reshape/run_ci_checks.sh` pass.

# Batch 9 — Close extraction imports and freeze the pack-facing API · Phase 2 · Flash
Tasks: **2.6 Close extraction imports, define the pack-facing kernel API, and remove CI path coupling**

- Move Reigh-domain, worker, claim-loop, task-client, JWT, provider, and baseline tests under `tests/packs/reigh/`; retain timeline/eventlog Protocol tests under `tests/timeline/`.
- Document `tests/packs/<id>/` as the extraction rail.
- Replace positive inventories in `tests/test_structure_contracts.py` and `tests/test_m2_public_surface.py` with negative rails for:
  - `core/experiments`
  - concrete generation backends
  - Reigh implementations
  - RunPod implementations
  - worker implementations
- Require no live imports of `astrid.core.integrations.{reigh,runpod,worker}`.
- Inventory every remaining static and literal-dynamic `astrid.core` import from packs.
- Extend `astrid/core/pack/import_policy.py` with exact machine-readable pack-facing kernel module prefixes and enforce them through `validate_import_layering()`.
- Support only:
  - provider-neutral `astrid.core.contracts.*`
  - explicitly public foundation I/O, hashing, path, and project-path helpers
  - public executor/orchestrator execution APIs
  - public pack discovery, entrypoint, resolver, and metadata APIs
  - public project/runtime/session/task launch APIs
  - provider-neutral generation/model-catalog contracts
  - public rendering contracts, registries, assets, transport, service, profile, publication, and artifact APIs
  - public timeline/event-schema and thread-lineage APIs
  - individually admitted shared utilities
- Exclude `_shared`, private modules/symbols, CLI handlers and presentation helpers, `task.plan.verbs`, `session.current_run_state`, `command_render`, concrete integrations/backends, and broad utility-family exemptions.
- Promote genuinely shared private helpers into existing public modules or make them pack-local; create no catch-all facade.
- Define supported-path stability and migration expectations in `docs/packs/contract.md`.
- Add negative fixtures for private core modules/symbols, concrete integrations, unlisted utilities, and CLI handlers.
- Wire the expanded checker into `scripts/reshape/run_ci_checks.sh`; add no exemptions.
- Replace depth-limited CI matching with arbitrary-depth `astrid/**` selection and test moved Reigh/RunPod paths.
- Admit `astrid.core.session.identity` explicitly or route it through the public session surface.
- Update `.github/workflows/bridge-latency.yml` for `astrid/packs/reigh/**`, retain `astrid/core/timeline/**`, and test the actual PR ref.
- Keep all moved tests under `tests/`.

CHECKPOINT:

- All Reigh implementation tests reside under `tests/packs/reigh/`, iteration experiment tests under `tests/packs/iteration/`, and corresponding old core/integration test locations are absent.
- `pytest -q tests/test_structure_contracts.py tests/test_m2_public_surface.py tests/packs/test_pack_import_policy.py tests/reshape/test_ci_changed_selection.py tests/packs/reigh tests/packs/iteration/experiments tests/timeline` passes.
- `python3 -c 'from astrid.core.structure import validate_import_layering, validate_repo_structure; assert validate_import_layering() == []; assert validate_repo_structure().ok'` exits zero.
- `rg -n 'astrid\\.core\\.integrations\\.(reigh|runpod|worker)' astrid scripts tests --glob '*.py'` finds only explicit negative-test data, never live imports.
- Import-policy fixtures reject private modules/symbols, CLI handlers, concrete integrations, unlisted utilities, and pack-to-pack entrypoint imports; every accepted pack-to-core import matches the machine-readable API.
- `tests/reshape/test_ci_changed_selection.py` proves arbitrary-depth selection for moved Reigh and RunPod paths.
- `.github/workflows/bridge-latency.yml` includes `astrid/packs/reigh/**` and `astrid/core/timeline/**` and checks out the PR ref.
- `python3 -m astrid packs validate astrid/packs` and `scripts/reshape/run_ci_checks.sh` pass with zero exemptions added.

# Batch 10 — Retire capability-shaped host aliases · Phase 3 · Flash
Tasks: Execute **3.1** before **3.2** within this batch.

**3.1 Remove pure executor aliases**

- Remove top-level `publish`, `publish-youtube`, `upload-youtube`, and `reigh-data`.
- Direct users to `executors run reigh.publish`, `executors run youtube.upload`, and `executors run reigh.reigh_data`.
- Update gateway dispatch/help/exports, pipeline-alias tests, and social-publish tests.
- Add negative root-help and unknown-command assertions.
- Preserve existing session-gating behavior until removal.

**3.2 Remove RunPod and worker host routes after executor parity**

- Remove top-level `worker` after `reigh.worker` parity.
- Remove top-level `runpod` after the three maintenance executors cover its full surface.
- Delete `astrid/core/gateway/runpod.py`, obsolete dispatch functions, help entries, and exports.
- Retain `_dispatch_executor_main` if used by permanent `serve` or another canonical host bridge.
- Keep `scratch`, `astrid/core/gateway/scratch.py`, `serve`, and the unbound `serve` allowlist.
- Update frozen allowlist assertions in `tests/test_cli_gate.py`.
- Add negative coverage for all six retired tokens and positive coverage for `scratch` and `serve`.
- Replace shortcut commands in RunPod, Reigh, and YouTube skills, STAGE files, recovery messages, and manifests with qualified executor invocations.

CHECKPOINT:

- `astrid/core/gateway/runpod.py` is absent.
- `python3 -m astrid --help` contains none of `publish`, `publish-youtube`, `upload-youtube`, `reigh-data`, `worker`, or `runpod` as root commands; `scratch` and `serve` remain documented.
- `pytest -q tests/test_pipeline_dispatch_aliases.py tests/test_cli_gate.py tests/session/test_cli_gate.py tests/packs/runpod tests/packs/reigh` passes, including unknown-command assertions for all six retired tokens and positive `scratch`/`serve` cases.
- Scoped searches of gateway/help exports and RunPod/Reigh/YouTube manifests, skills, STAGE files, and recovery messages find no invocation of a retired shortcut; qualified executor commands are present.
- Canonical `reigh.worker`, `runpod.sweep`, `runpod.list_volumes`, `runpod.ensure_storage`, `reigh.publish`, `youtube.upload`, and `reigh.reigh_data` execution coverage passes.
- `python3 -m astrid packs validate astrid/packs` and `scripts/reshape/run_ci_checks.sh` pass.

# Batch 11 — Canonicalize and enforce pack layout · Phase 4 · Sol(XHARD)
Tasks: Execute **4.1** before **4.2** within this batch.

**4.1 `[XHARD]` Canonicalize pack-private entrypoints**

- Convert `astrid/packs/blender/deploy.py` into canonical `blender.deploy`.
- Keep mesh fetching as private `blender.render` support; move `mesh_fetch.py` beneath its support tree and remove independent `__main__`.
- Move `render_core.py`, `renders/`, and `server/blender_render_server.py` beneath appropriate executor support trees; retain library imports but remove alternate user-facing module surfaces.
- Update Blender imports, presets, README, manifest, skill, STAGE files, and tests.
- Preserve the no-cross-pack-entrypoint-import rail.
- Classify rendering backend/planner/finalizer runners as manifest-private transport commands:
  - `astrid/packs/rendering/run.py`
  - `backends/{ffmpeg,remotion,threejs}/run.py`
  - `planners/{legacy_hybrid,threejs_hybrid}/run.py`
  - `finalizers/ffmpeg/run.py`
- Make `astrid/core/rendering/transport.py` set an internal-invocation marker; direct subprocess invocation must fail while manifest transport succeeds.
- Remove the unsupported `python -m astrid.sdk.rendering` claim from code and documentation.
- Remove executable `__main__` behavior from unledgered generation golden demos; retain only needed non-runnable fixtures.
- Add subprocess rails for canonical capability/transport success and direct pack-module failure.
- Add a scoped stale-`python -m astrid.packs.*` rail while allowing exact manifest-private commands.

**4.2 `[XHARD]` Enforce actual pack-root layout**

- Extend `astrid/core/pack/validate_layout.py` to walk real pack-root entries.
- Permit only `pack.yaml`, declared roots, `skill/`, `docs/`, `examples/`, `schemas/`, `fixtures/`, `golden/`, capability-local golden fixtures, package markers, manifest-declared extension roots, and narrowly documented manifest-declared support-library roots.
- Declare `astrid/packs/editorial/hype/` as library-only support and prove it has no discovery or CLI surface.
- Preserve rendering’s declared `backends/`, `planners/`, and `finalizers/`.
- Reject undeclared loose files and directories with actionable paths.
- Move `astrid/packs/fal/tests/test_h3_video.py` to `tests/packs/fal/`.
- Add positive rendering/editorial/golden/fixture tests and negative Blender-style junk cases.

CHECKPOINT:

- The Blender manifest exposes `blender.deploy`; mesh-fetch and render/server support live under canonical executor support trees with no independent `__main__` surface.
- Direct invocation tests fail for guarded rendering-private commands while canonical manifest transport succeeds.
- `rg -n 'python(3)? -m astrid\\.sdk\\.rendering' astrid docs` returns nothing.
- The scoped stale-command test finds no unsupported `python -m astrid.packs.*` instruction outside the exact manifest-private allowlist.
- `astrid/packs/fal/tests/test_h3_video.py` is absent and `tests/packs/fal/test_h3_video.py` exists.
- `pytest -q tests/packs/test_pack_import_policy.py tests/packs/test_pack_layout_contract.py tests/packs/test_packs_validate.py tests/core/rendering tests/packs/rendering tests/packs/fal tests --ignore=tests/agentic` passes for Blender, transport guarding, rendering extension layout, editorial support, fixtures/golden roots, and negative loose-entry cases.
- `python3 -m astrid packs validate astrid/packs` rejects undeclared pack-root junk with actionable paths and accepts all shipped packs.
- `scripts/reshape/run_ci_checks.sh` passes with no new exemption.

# Batch 12 — Repository truth, documentation, and closure · Phase 4 · Flash
Tasks: Execute **4.3**, then **4.4**, then **4.5** within this batch.

**4.3 Close root-hygiene gaps and root-writing tests**

- Verify `fal-voice-upscale/` is absent and remove it from `ROOT_DIR_ALLOWLIST`.
- Add `*.mp3` to `.gitignore` and tracked-runtime-media hygiene rules.
- Make `find_unknown_root_entries()` and hygiene tests inspect actual root entries as well as tracked Git paths.
- Keep the checker product-repository-owned.
- Replace root-directed temporary directories with `tmp_path`, `TemporaryDirectory()`, or system temp paths in:
  - `tests/test_pipeline_caching.py`
  - `tests/core/test_project_cli.py`
  - `tests/test_managed_write_paths.py`
  - worker/claim-loop tests
  - `tests/core/test_executor_cli.py`
  - `tests/packs/reigh/test_open_in_reigh.py`
  - `tests/timeline/test_edit_helpers.py`
- Add no speculative deletion rules for absent unrelated directories.
- Do not touch `.oracle-threejs-archive/`; run final hygiene from a clean checkout.

**4.4 Complete the documentation and CI truth pass**

- Complete `docs/packs/contract.md` with the kernel, Arnold rationale, namespace decision, supported kernel API, `depends`, entrypoint-import prohibition, hidden/deprecated policy, hook-admission rule, product/framework classifications, and both extraction blockers.
- Update `docs/packs/pack-taxonomy.md` for `_core`, visible `builtin`, Blender, current `install_tier: core`, alias-carrier truth, and inventory derivation.
- Update `docs/architecture/repo-shape.md` for actual execution paths, gateways, kernel directories, and no legacy workspace source.
- Update `docs/architecture/import-tiers.md` for supported kernel APIs, declared support dependencies, the fixed runtime bridge, and provider-neutral Protocols.
- Correct SDK public-surface documentation to 32 exports in `docs/contracts/platform-contract.md` and `docs/architecture/repo-shape.md`.
- Add `fal.h3_video` to `docs/packs/adapter-packs.md`.
- Update architecture/SDK references for qualified capability routes, pack-only discovery, and no silent module CLI.
- Update Generation, Iteration, Reigh, RunPod, YouTube, Blender, rendering, builtin, and `_core` skills/manifests/STAGE files.
- Update integration and asset-resolution contracts for pack ownership while retaining `astrid serve`.
- Update CI-lane documentation and documentation-command verification for arbitrary-depth selection.
- Regenerate `_core/skill/SKILL.md`.
- Search for stale domain paths, aliases, pack counts, undeclared imports, unsupported kernel imports, direct pack commands, and old `_core` exception language.

**4.5 Run the full closure gate**

- Run pack validation; schema, dependency/import-policy, discovery, skills, elements, structure, gateway, doctor, generation, iteration, Reigh, RunPod, rendering, layout, CI-selection, and hygiene tests.
- Run wheel smoke outside the checkout with empty `ASTRID_HOME`.
- Run `scripts/reshape/run_ci_checks.sh` and the broad suite.
- Run Remotion typechecking and renderer-parity tests.
- Verify zero import-layer exemptions.
- Verify cross-pack support imports match acyclic, non-stale `depends`.
- Verify no pack imports another pack’s executor/orchestrator `run.py`.
- Verify all pack-to-core imports belong to the supported machine-readable API.
- Run generated-artifact check modes.
- Search for retired gateway tokens, deleted core-domain imports, old backend module strings, obsolete paths, unsupported module commands, and undeclared dependencies.
- Verify all capabilities, skills, and concrete generation backends originate from manifests.
- Verify `astrid/core/integrations/` contains only Arnold.
- Verify no new product-specific binding joins the two documented extraction debts.
- Verify deterministic capability indexing and repository hygiene from a clean checkout.
- Verify moved tests follow `tests/packs/<id>/`.

CHECKPOINT:

- `fal-voice-upscale/` is absent; `ROOT_DIR_ALLOWLIST` does not mention it; `.gitignore` includes `*.mp3`.
- `pytest -q tests/reshape/test_repo_hygiene.py tests/test_pipeline_caching.py tests/core/test_project_cli.py tests/test_managed_write_paths.py tests/core/test_executor_cli.py tests/packs/reigh/test_open_in_reigh.py tests/timeline/test_edit_helpers.py` passes without creating root artifacts.
- Documentation-command verification passes, and scoped greps find no stale core-domain paths, retired aliases, fixed pack counts, old `_core` exception language, or unsupported direct module commands.
- `python3 -m astrid packs validate astrid/packs` passes.
- Pack schema, dependency/import-policy, discovery, skill, element, structure, gateway, doctor, generation, iteration, Reigh, RunPod, rendering, layout, CI-selection, and hygiene test groups pass.
- `scripts/smoke_wheel_install.sh` passes outside the source checkout with an empty temporary `ASTRID_HOME`.
- `scripts/reshape/run_ci_checks.sh` and `pytest --tb=no -q --no-header` pass.
- Remotion typechecking and `pytest -q tests/packs/test_renderer_parity.py tests/packs/rendering tests/core/rendering` pass.
- `python3 -c 'from astrid.core.structure import validate_import_layering, validate_repo_structure; assert validate_import_layering() == []; assert validate_repo_structure().ok'` exits zero.
- Automated rails prove the `depends` graph is complete, non-stale, and acyclic; no pack imports another pack’s executor/orchestrator `run.py`; every pack-to-core import belongs to the supported API.
- Generated-artifact check modes prove the capability index and `astrid/packs/_core/skill/SKILL.md` match clean regeneration.
- `find astrid/core/integrations -mindepth 1 -maxdepth 1 -type d ! -name arnold -print` produces no output.
- Searches find no live retired gateway tokens, deleted core Reigh/RunPod/worker imports, old concrete-generation backend strings, obsolete paths, or unsupported direct pack-module commands outside explicit negative fixtures.
- Tests prove every executor, orchestrator, element, skill, and concrete generation backend originates from a manifest-backed pack.
- Reigh tests are under `tests/packs/reigh/`, iteration experiment tests under `tests/packs/iteration/`, and every moved pack domain follows `tests/packs/<id>/`.
- Final generated-index and repository-hygiene checks pass from a clean checkout without touching `.oracle-threejs-archive/`.
