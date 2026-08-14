# Batch 1 — Legal system-pack kernel · Phase 0 · Sol(XHARD)
Tasks: 0.1, 0.2 `[XHARD]`, and 0.3, executed in that order. GPT-5.6 Sol owns this batch and must delegate implementation and validation work before synthesizing the batch result.

**0.1 Lock the kernel and green import baseline**

- Update `docs/packs/contract.md` with the authoritative kernel table: CLI gateway; session/project management; task-run machinery; pack discovery/validation/install/store/aliases; capability registries; SDK and skills installer; structure/doctor; foundation/contracts; timeline/eventlog; rendering and generation protocols; Arnold lifecycle/orchestration.
- State the exclusion rule: concrete generation adapters, discoverable capabilities, and optional service domains belong in manifest-backed packs.
- Retain `astrid/core/integrations/arnold/`, `astrid/core/orchestrate/`, `astrid/core/timeline/`, `astrid/core/timeline/eventlog/`, `astrid scratch`, `astrid serve`, `remotion/`, and `themes/` in their host/substrate/data roles.
- Record the verified baseline: `validate_import_layering()` and `validate_repo_structure()` both report zero violations.
- Keep `astrid/core/runtime/in_process.py` as the sole static core-to-pack import exception and preserve the existing manifest-driven dynamic resolver allowlist in `astrid/core/structure.py`; add no exemptions.
- Explicitly test hardcoded/importlib module strings such as those currently in `astrid/core/generation/backends/registry.py:185-205`, because the AST checker cannot see them.

**0.2 `[XHARD]` Make `_core` a legal, manifest-backed system pack**

- Change validation before or atomically with adding `astrid/packs/_core/pack.yaml`; a naïve manifest is runtime-fatal because:
  - `astrid/core/pack/_common.py:144-146` rejects `_core`.
  - `astrid/core/pack/loader.py:94-107` does not skip underscore directories.
  - `loader.py:117-118` requires manifest ID to match the folder.
  - The resulting `PackValidationError` escapes discovery and crashes every registry.
- Update `astrid/core/pack/schemas/v1/_defs.json` so the lexical `pack_id` definition accepts either a normal pack ID or the reserved literal `_core`; do not pretend raw JSON Schema can validate filesystem provenance.
- Add an explicit reserved-ID path in `astrid/core/pack/_common.py` and enforce provenance context in `astrid/core/pack/loader.py`: `_core` is accepted only at the canonical shipped source root.
- Continue rejecting user, local, extra, environment, and installed packs claiming `_core`; preserve folder/ID equality and reject `_core.<name>` capability IDs.
- Add `astrid/packs/_core/pack.yaml` with system metadata, the existing skill root, and no executors, orchestrators, elements, aliases, or extension capabilities.
- Remove the manifest-less skill-shell rules from `astrid/core/pack/validate_first_party.py:136-155` and `astrid/core/pack/validate_layout.py:122-129`.
- Preserve literal `_core → astrid` harness branding in:
  - `astrid/skills/harnesses/base.py`
  - `astrid/skills/harnesses/claude.py`
  - `astrid/skills/harnesses/codex.py`
  - `astrid/skills/harnesses/hermes.py`
  - `astrid/skills/{__init__,registry,cli}.py`
- Extend `tests/packs/test_pack_yaml_schema.py`, `test_pack_discovery.py`, `test_pack_layout_contract.py`, `test_packs_validate.py`, `test_packs_cli.py`, `tests/test_skills.py`, and wheel smoke coverage with lexical schema acceptance, canonical loader acceptance, noncanonical-source rejection, capability emptiness, discovery, and branding invariants.

**0.3 Establish one deterministic first-party inventory**

- Make `_FIRST_PARTY_PACK_IDS` in `astrid/core/pack/validate_first_party.py` describe the tracked, manifest-backed bundled set; add `blender` and `_core`, and remove `_FIRST_PARTY_INTERNAL_DIRS`.
- Derive tests and documentation from that inventory instead of maintaining duplicate shipped-ID lists such as `tests/packs/test_pack_layout_contract.py:49-69`.
- Treat `discord_local` and `seedance_local` correctly: they are checkout-local personal packs excluded through `.git/info/exclude`, not stale or bundled content.
- Do not add those packs to the first-party inventory, delete them, restore them, or suppress their runtime discovery.
- Change `scripts/gen_capability_index.py` to generate the committed capability index from tracked first-party source packs only, excluding untracked/ignored personal packs even when they exist in the live checkout.
- Add a regression fixture containing an untracked personal pack and prove it remains runtime-discoverable but cannot enter the committed index.
- Regenerate `astrid/packs/_core/skill/SKILL.md` from a clean checkout.
- Keep `astrid/packs/builtin/pack.yaml` visible and make its description truthful about the live `builtin.agent_probe` orchestrator; add a manifest/documentation consistency test.

CHECKPOINT:

- `.oracle/tasklist.md` is frozen before execution starts. Executors must not edit it; proposed revisions fail the checkpoint and return to the oracle.
- `python3 -m astrid packs validate astrid/packs` exits 0.
- `python3 -c 'from astrid.core.structure import validate_import_layering, validate_repo_structure; assert not validate_import_layering(); assert not validate_repo_structure()'` exits 0.
- `pytest -q tests/packs/test_pack_yaml_schema.py tests/packs/test_pack_discovery.py tests/packs/test_pack_layout_contract.py tests/packs/test_packs_validate.py tests/packs/test_packs_cli.py tests/test_skills.py` passes.
- `astrid/packs/_core/pack.yaml` and `astrid/packs/_core/skill/SKILL.md` exist; `_core` exposes no capabilities, aliases, or extensions.
- Tests prove canonical shipped `_core` acceptance, rejection from every noncanonical source, `_core.<name>` rejection, and unchanged `_core → astrid` harness branding.
- `rg -n '_FIRST_PARTY_INTERNAL_DIRS|skill[_ -]only[_ -]shell' astrid/core/pack tests/packs` returns nothing.
- Inventory tests prove `_core` and `blender` are bundled while `discord_local` and `seedance_local` remain runtime-discoverable but absent from the committed index.
- `python3 scripts/gen_capability_index.py && git diff --exit-code -- astrid/packs/_core/skill/SKILL.md` exits 0 from a clean checkout.
- No import-layer exemption has been added, and the hardcoded/importlib module-string rail passes.
- `scripts/reshape/run_ci_checks.sh` exits 0.

# Batch 2 — Canonical skill discovery stream · Phase 1 · Sol(XHARD)
Tasks: 1.1 `[XHARD]`. GPT-5.6 Sol owns this batch and must delegate implementation and validation work before synthesizing the batch result.

**1.1 `[XHARD]` Route skills through the canonical discovered-pack stream**

- Refactor `astrid/skills/discovery.py` to consume ordered `DiscoveredPack` records from `astrid/core/pack/discovery.py` across source, local, extra, environment, and installed roots.
- Delete the direct `PACKS_DIR.iterdir()` walk, manifest-less fallback, swallowed manifest errors, and duplicate `_scan_discovered_packs()` traversal.
- Fix the current `ASTRID_PACKS_PATH` omission caused by `astrid/skills/discovery.py:143-145`; environment-root skills must list.
- Make hidden-pack treatment consistent: hidden packs must not enter source or installed discovery. Preserve the explicitly documented deprecated-pack policy instead of conflating it with hidden visibility.
- Obtain skill roots only from `DiscoveredPack.skill_roots()` and apply pack-ID deduplication once at canonical source priority.
- Preserve explicit-root testability by parameterizing shared discovery rather than adding another filesystem scanner.
- Make invalid manifests fail at the pack boundary and never leak skills.
- Preserve top-level SDK laziness asserted by `tests/test_sdk_public_surface.py:3339-3386`.
- Add source/local/extra/environment/installed ordering, `_core`, duplicate, hidden-installed, deprecated, invalid-manifest, and checkout-local pack cases in `tests/packs/test_pack_discovery_metadata.py` and `tests/test_skills.py`.

CHECKPOINT:

- Batch 1 has PASSED.
- `python3 -m astrid packs validate astrid/packs` exits 0.
- `pytest -q tests/packs/test_pack_discovery_metadata.py tests/test_skills.py tests/test_sdk_public_surface.py` passes.
- Tests prove source/local/extra/environment/installed ordering, canonical deduplication, `_core`, hidden-installed, deprecated, invalid-manifest, and checkout-local behavior.
- An `ASTRID_PACKS_PATH` fixture exposes its declared skill.
- Invalid manifests fail at the pack boundary and expose no skills.
- `rg -n 'PACKS_DIR\\.iterdir|_scan_discovered_packs' astrid/skills/discovery.py` returns nothing.
- No manifest-less fallback or swallowed manifest-validation error remains in `astrid/skills/discovery.py`.
- SDK laziness tests prove `import astrid` does not eagerly import pack discovery.
- `scripts/reshape/run_ci_checks.sh` exits 0.

# Batch 3 — Pack-only element graph · Phase 1 · Sol(XHARD)
Tasks: 1.2 `[XHARD]`. GPT-5.6 Sol owns this batch and must delegate implementation and validation work before synthesizing the batch result.

**1.2 `[XHARD]` Remove theme and workspace element discovery**

- In `astrid/core/element/registry.py`, `catalog.py`, and `__init__.py`, remove `ElementSource`, `default_sources()`, `load_source_elements()`, active-theme element loading, `WORKSPACE_ROOT`, `legacy_workspace`, and source-conflict warnings.
- Build the element registry exclusively from `discover_pack_metadata()` and pack-declared element roots; do not create pseudo-packs for absent theme/workspace sources.
- Remove discovery-only `active_theme`, `include_missing_roots`, and `elements --theme` inputs from `astrid/core/element/cli.py` and `astrid/sdk/discovery.py`.
- Update `astrid/core/timeline/validators/`, `astrid/packs/training/executors/pool_merge/run.py`, `astrid/packs/rendering/backends/remotion/run.py`, and `scripts/gen_effect_registry.py` to use pack metadata.
- Keep theme selection, pointers, state, and provenance as rendering data.
- Replace positive theme/workspace discovery expectations with negative no-scan rails in `tests/core/test_elements_registry.py`, `tests/timeline/test_effects_catalog.py`, `tests/timeline/test_timeline_elements_catalog.py`, and `tests/test_sdk_public_surface.py`.
- Preserve local-pack precedence and rendering behavior through `tests/packs/test_pack_local_priority.py`, `tests/packs/test_text_card_override.py`, and Remotion registry/code-generation tests.
- Assert every loaded element has `source == "pack:<id>"` and matching pack metadata.

CHECKPOINT:

- Batch 2 has PASSED.
- `python3 -m astrid packs validate astrid/packs` exits 0.
- `pytest -q tests/core/test_elements_registry.py tests/timeline/test_effects_catalog.py tests/timeline/test_timeline_elements_catalog.py tests/packs/test_pack_local_priority.py tests/packs/test_text_card_override.py tests/test_sdk_public_surface.py tests/packs/rendering` passes.
- `rg -n 'ElementSource|default_sources|load_source_elements|legacy_workspace|WORKSPACE_ROOT' astrid/core/element` returns nothing.
- Discovery-only `active_theme`, `include_missing_roots`, and `elements --theme` surfaces are absent from `astrid/core/element/cli.py` and `astrid/sdk/discovery.py`.
- Negative fixtures prove theme and workspace directories are not scanned.
- Every loaded element reports `source == "pack:<id>"` with matching pack metadata.
- Local-pack precedence, text-card override, Remotion registry, and code-generation tests pass.
- `scripts/reshape/run_ci_checks.sh` exits 0.

# Batch 4 — Wheel-complete canonical graph · Phase 1 · Flash
Tasks: 1.3.

**1.3 Package and prove the canonical graph in wheels**

- Replace the rendering-only package-data declaration in `pyproject.toml` with explicit coverage for:
  - `core/model_catalog/*.yaml`
  - existing rendering schemas and parity fixtures
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
  - Every canonical bundled manifest is present.
  - Representative executor, orchestrator, element, nested skill, STAGE, and extension files ship.
  - `ModelRegistry.load_default()` and `LoraRegistry.load_default()` succeed.
  - Skills discover from the wheel’s source layer.
  - `include_installed=True` with an empty installed store is a no-op, not a loss of source packs.
- Prefer canonical-inventory assertions over brittle fixed capability counts.
- Preserve `import astrid` laziness and add a rail that loading registries does not eagerly import concrete generation, Reigh, or RunPod implementations.

CHECKPOINT:

- Batch 3 has PASSED.
- `python3 -m astrid packs validate astrid/packs` exits 0.
- `pytest -q tests/core/rendering/test_package_data.py tests/test_sdk_public_surface.py tests/packs/test_pack_discovery_metadata.py` passes.
- `scripts/smoke_wheel_install.sh` exits 0 outside the checkout with an empty `ASTRID_HOME`.
- Wheel smoke proves all canonical manifests and representative executor, orchestrator, element, nested-skill, STAGE, model-catalog, and rendering-extension files ship.
- `ModelRegistry.load_default()` and `LoraRegistry.load_default()` succeed from the installed wheel.
- `include_installed=True` with an empty installed store preserves source-pack discovery.
- Package-data declarations contain no blanket recursive pack-root include.
- Registry loading does not eagerly import concrete generation, Reigh, or RunPod implementations.
- `scripts/reshape/run_ci_checks.sh` exits 0.

# Batch 5 — Generation and experiment extraction · Phase 2 · Sol(XHARD)
Tasks: 2.1 `[XHARD]` and 2.2. GPT-5.6 Sol owns this batch and must delegate implementation and validation work before synthesizing the batch result.

**2.1 `[XHARD]` Move concrete generation backends into the generation pack**

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

**2.2 Move experiments into the iteration pack**

- Move `astrid/core/experiments/` to `astrid/packs/iteration/experiments/` without a compatibility shim.
- Update iteration experiment import, prepare, review, and review-session entrypoints.
- Update `astrid/packs/editorial/executors/human_review/run.py`, the second current consumer.
- Document pack-to-pack support-module imports as legal while retaining the prohibition on core-to-pack imports.
- Move `tests/core/experiments/` under `tests/packs/iteration/experiments/` and update iteration/editorial tests and `STAGE.md` references.

CHECKPOINT:

- Batch 4 has PASSED.
- `python3 -m astrid packs validate astrid/packs` exits 0.
- `pytest -q tests/core/test_generation_backend_registry.py tests/packs/generation tests/packs/iteration tests/packs/editorial tests/packs/builtin/generate_image/test_codex_backend.py tests/test_sdk_public_surface.py tests/test_third_party_integration.py` passes.
- `scripts/smoke_wheel_install.sh` exits 0 and proves the generation manifest supplies exactly `cloud`, `codex`, and `local`; removing that manifest removes all three.
- `test ! -e astrid/core/generation/backends/fal.py && test ! -e astrid/core/generation/backends/codex.py && test ! -e astrid/core/generation/backends/vibecomfy.py` exits 0.
- `test ! -d astrid/core/experiments && test ! -e fal-voice-upscale` exits 0.
- `astrid/packs/generation/backends/{fal,codex,vibecomfy}.py` and `astrid/packs/iteration/experiments/` exist.
- `rg -n 'astrid\\.core\\.generation\\.backends\\.(fal|codex|vibecomfy)|astrid\\.core\\.experiments' astrid scripts tests --glob '*.py'` returns nothing except explicit negative-test literals.
- A bare generation registry is empty and default loading obtains descriptors only from discovered manifests.
- No compatibility shim, new extension framework, import-layer exemption, or concrete backend export remains in core.
- `scripts/reshape/run_ci_checks.sh` exits 0.

# Batch 6 — RunPod extraction and Reigh state inversion · Phase 2 · Sol(XHARD)
Tasks: 2.3 `[XHARD]` and 2.4 `[XHARD]`. GPT-5.6 Sol owns this batch and must delegate implementation and validation work before synthesizing the batch result.

**2.3 `[XHARD]` Move RunPod maintenance into the RunPod pack without a new abstraction**

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
- Update `astrid/packs/runpod/pack.yaml`, `skill/SKILL.md`, executor `_common.py`, and `astrid/packs/training/orchestrators/training_run/{compute_backends,config}.py`.
- Remove `astrid/core/integrations/runpod/` only after all imports have moved.
- Relocate or retarget `tests/packs/runpod/test_sweeper.py`, `test_ensure_storage.py`, `tests/test_sweeper_async.py`, `tests/test_sweeper_edges.py`, `tests/test_doctor_setup.py`, and task-mutation inventories.
- Add no `astrid/core/structure.py` exemption.

**2.4 `[XHARD]` Invert the generic Reigh bridge state before extraction**

- Create `astrid/core/timeline/asset_registry_state.py` for provider-neutral:
  - latest registry-event recovery
  - sidecar repair
  - record/source resolution
  - no-pruning merge semantics
- Move the generic logic currently buried in `astrid/core/integrations/reigh/local_bridge.py:485-549` into that host module.
- Make `astrid/core/timeline/asset_registry_edits.py` and the eventual pack bridge consume the new host helper.
- Extend the existing Protocol precedent in `astrid/core/contracts/remote_timeline.py` with only the remote load/save/list shapes needed by migration, editing, and worker callers.
- Preserve `astrid/core/timeline/{local_fs,supabase,selector,reigh_events,transfer}.py`.
- Keep event recovery, CAS, crash reconciliation, sidecar repair, no-op, and no-pruning behavior in `tests/timeline/test_asset_registry_sync.py`; move generic recovery tests out of `tests/integrations/reigh/test_local_bridge_helpers.py`.

CHECKPOINT:

- Batch 5 has PASSED.
- `python3 -m astrid packs validate astrid/packs` exits 0.
- `pytest -q tests/packs/runpod tests/test_sweeper_async.py tests/test_sweeper_edges.py tests/test_doctor_setup.py tests/timeline/test_asset_registry_sync.py` passes.
- `astrid/core/timeline/asset_registry_state.py` exists.
- Manifests exist for `astrid/packs/runpod/executors/{sweep,list_volumes,ensure_storage}/executor.yaml`.
- `test ! -d astrid/core/integrations/runpod` exits 0.
- `rg -n 'astrid\\.core\\.integrations\\.runpod|_check_runpod_stale_handles' astrid scripts tests --glob '*.py'` returns nothing except explicit negative-test literals.
- Tests preserve RunPod sweep dry-run diagnostics, storage recovery messages, and training’s qualified `runpod.ensure_storage` hint.
- Asset-registry recovery, CAS, crash reconciliation, sidecar repair, no-op, and no-pruning tests pass from core timeline tests.
- `rg -n 'RunPodMaintenance' astrid` returns nothing.
- No doctor extension hook, additional extension framework, or structure exemption has been added.
- `scripts/reshape/run_ci_checks.sh` exits 0.

# Batch 7 — Reigh service and worker extraction · Phase 2 · Sol(XHARD)
Tasks: 2.5 `[XHARD]`. GPT-5.6 Sol owns this batch and must delegate the sessionless-serve boundary exploration, implementation, and validation before synthesizing the batch result.

**2.5 `[XHARD]` Move the Reigh service domain and worker into the Reigh pack**

- Move Reigh environment, provider, bridge transport, task client, remote timeline I/O, JWT/JWKS, append service, error, and worker implementations from:
  - `astrid/core/integrations/reigh/`
  - `astrid/core/integrations/worker/`
  into `astrid/packs/reigh/integration/` and pack executor support.
- Keep the host timeline/eventlog primitives in core and delete compatibility copies such as `event_construction.py` and the integration-local `supabase_client.py`.
- Add `reigh.worker`, preserving the long-running claim loop, signal handling, authentication, and qualified provenance.
- Add `reigh.serve_local_bridge` for the pack-owned HTTP/CORS/media transport, including `--projects-root`.
- Preserve top-level `astrid serve` as the documented, unbound host facade. Resolve its pack implementation through the canonical registry without weakening normal executor project/session requirements or adding a general service framework.
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
- Ensure core gateway, project handlers, and timeline code never statically import pack implementations.

CHECKPOINT:

- Batch 6 has PASSED.
- `python3 -m astrid packs validate astrid/packs` exits 0.
- `pytest -q tests/packs/reigh tests/timeline tests/core/test_project_cli.py tests/session/test_cli_gate.py` passes.
- `test ! -d astrid/core/integrations/reigh && test ! -d astrid/core/integrations/worker` exits 0.
- Manifests exist for `astrid/packs/reigh/executors/{worker,serve_local_bridge,timeline_edit}/executor.yaml`.
- `test ! -e scripts/node/ops_helper.mjs` exits 0.
- `rg -n 'astrid\\.core\\.integrations\\.(reigh|worker)' astrid scripts tests --glob '*.py'` returns nothing except explicit negative-test literals.
- Static-import rails prove core gateway, project handlers, and timeline modules do not import `astrid.packs.reigh`.
- Gateway tests prove `astrid serve --projects-root ...` remains sessionless without granting ordinary executors a sessionless path.
- `reigh.worker` tests cover claim-loop behavior, signals, authentication, and qualified provenance.
- `reigh.timeline_edit` tests cover only `add-clip`, `move-clip`, and `set-theme`, including PAT default, service-role option, expected-version handling, three retries, `force=False`, and event descriptors.
- Remote `projects list` and `projects edit` are absent; generic local project-store and `timelines` commands remain.
- No general service framework or new import-layer exemption has been added.
- `scripts/reshape/run_ci_checks.sh` exits 0.

# Batch 8 — Extraction closure and CI path rails · Phase 2 · Flash
Tasks: 2.6.

**2.6 Close extraction imports and CI path coupling**

- Move Reigh-domain, worker, claim-loop, task-client, JWT, provider, and baseline tests under `tests/packs/reigh/`; retain host timeline/eventlog Protocol tests under `tests/timeline/`.
- Replace positive inventories in `tests/test_structure_contracts.py` and `tests/test_m2_public_surface.py` with negative rails for:
  - `core/experiments`
  - concrete generation backends
  - Reigh implementations
  - RunPod implementations
  - worker implementations
- Require repository searches for `astrid.core.integrations.{reigh,runpod,worker}` to return no live imports.
- Wire the existing import-layer checker into `scripts/reshape/run_ci_checks.sh`; add no exemptions.
- Replace depth-limited changed-file matching in `scripts/reshape/run_ci_checks.sh:139-163` with arbitrary-depth `astrid/**` selection and cover moved Reigh/RunPod paths in `tests/reshape/test_ci_changed_selection.py`.
- Keep the `astrid.core.session.identity` seed import because session identity remains kernel-owned.
- Update `.github/workflows/bridge-latency.yml` to trigger on `astrid/packs/reigh/**` while retaining `astrid/core/timeline/**`, and make checkout test the actual PR ref rather than a hardcoded external repository state.
- Keep all moved tests under `tests/` so broad discovery cannot silently lose them.

CHECKPOINT:

- Batches 5–7 have PASSED.
- `python3 -m astrid packs validate astrid/packs` exits 0.
- `pytest -q tests/test_structure_contracts.py tests/test_m2_public_surface.py tests/reshape/test_ci_changed_selection.py tests/packs/reigh tests/packs/runpod tests/timeline` passes.
- `rg -n 'astrid\\.core\\.integrations\\.(reigh|runpod|worker)' astrid scripts tests --glob '*.py'` finds no live imports; only explicit negative-test literals are permitted.
- Negative structure rails reject `core/experiments`, concrete core generation backends, Reigh, RunPod, and worker implementations.
- `python3 -c 'from astrid.core.structure import validate_import_layering; assert not validate_import_layering()'` exits 0 with no new exemptions.
- `scripts/reshape/run_ci_checks.sh` invokes the import-layer checker and exits 0.
- `pytest -q tests/reshape/test_ci_changed_selection.py` proves arbitrary-depth `astrid/**` changes select moved Reigh and RunPod tests.
- `astrid.core.session.identity` remains the CI seed import.
- `.github/workflows/bridge-latency.yml` contains both `astrid/packs/reigh/**` and `astrid/core/timeline/**` and checks out the actual PR ref.
- All relocated tests remain under `tests/`.

# Batch 9 — Retire capability-shaped host routes · Phase 3 · Flash
Tasks: 3.1 and 3.2, executed in that order.

**3.1 Remove pure executor aliases**

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

**3.2 Remove RunPod and worker host routes after executor parity**

- Remove top-level `worker` only after `reigh.worker` covers the claim loop.
- Remove top-level `runpod` only after `runpod.sweep`, `runpod.list_volumes`, and `runpod.ensure_storage` cover its entire maintenance surface.
- Delete `astrid/core/gateway/runpod.py`, obsolete dispatch functions, help entries, and unused exports.
- Retain `_dispatch_executor_main` if still used by the permanent `serve` facade or other canonical host bridges.
- Keep `scratch`, `astrid/core/gateway/scratch.py`, `serve`, and the current unbound `serve` allowlist entry.
- Update the frozen allowlist assertions in `tests/test_cli_gate.py` in the same change.
- Add gateway-level negative coverage for all six retired tokens, including the previously untested `worker` route, plus positive `scratch` and `serve` coverage.
- Replace shortcut commands in RunPod, Reigh, and YouTube skills, STAGE files, recovery messages, and manifests with qualified executor invocations.

CHECKPOINT:

- Batch 8 has PASSED.
- `python3 -m astrid packs validate astrid/packs` exits 0.
- `pytest -q tests/test_pipeline_dispatch_aliases.py tests/session/test_cli_gate.py tests/packs/runpod tests/packs/reigh` passes.
- Root help omits `publish`, `publish-youtube`, `upload-youtube`, `reigh-data`, `worker`, and `runpod`; gateway tests prove each is an unknown root command.
- Positive gateway tests for `scratch` and sessionless `serve` pass.
- `test ! -e astrid/core/gateway/runpod.py` exits 0.
- `rg -n '\\b(publish-youtube|upload-youtube|reigh-data|worker|runpod)\\b|[\"'\"']publish[\"'\"']' astrid/core/gateway` returns nothing except qualified permanent-bridge references explicitly allowed by tests.
- `rg -n 'astrid (publish|publish-youtube|upload-youtube|reigh-data|worker|runpod)(\\s|$)' astrid/packs docs` returns nothing.
- RunPod, Reigh, and YouTube skills, STAGE files, manifests, and diagnostics use qualified executor invocations.
- The frozen unbound allowlist retains `serve` and contains no retired route.
- `scripts/reshape/run_ci_checks.sh` exits 0.

# Batch 10 — Canonical private entrypoints · Phase 4 · Sol(XHARD)
Tasks: 4.1 `[XHARD]`. GPT-5.6 Sol owns this batch and must delegate the manifest-private caller inventory, implementation, and subprocess validation before synthesizing the batch result.

**4.1 `[XHARD]` Canonicalize pack-private entrypoints**

- Convert:
  - `astrid/packs/blender/deploy.py` into `blender.deploy`
  - `astrid/packs/blender/mesh_fetch.py` into `blender.mesh_fetch`
- Move `render_core.py`, `renders/`, and `server/blender_render_server.py` beneath the appropriate executor support trees; retain library imports but remove alternate user-facing `__main__` surfaces.
- Update Blender imports, presets, README, pack manifest, skill, STAGE files, and tests.
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

CHECKPOINT:

- Batch 9 has PASSED.
- `python3 -m astrid packs validate astrid/packs` exits 0.
- Manifests exist for `astrid/packs/blender/executors/{deploy,mesh_fetch}/executor.yaml`.
- Old loose `astrid/packs/blender/{deploy.py,mesh_fetch.py,render_core.py,renders,server}` paths are absent.
- Targeted Blender and rendering subprocess tests pass.
- Canonical `blender.deploy` and `blender.mesh_fetch` invocation succeeds through the executor registry.
- Direct Blender and generation pack-module execution fails; direct rendering backend/planner/finalizer execution fails without the internal marker, while manifest transport succeeds with it.
- `rg -n 'python(3)? -m astrid\\.sdk\\.rendering' astrid/sdk/rendering.py docs/reference/sdk.md` returns nothing.
- No unledgered generation golden demo retains executable `__main__` behavior.
- The scoped stale-command test rejects unsupported `python -m astrid.packs.*` instructions while allowing only exact manifest-private transport commands.
- No new public CLI or general transport abstraction has been added.
- `scripts/reshape/run_ci_checks.sh` exits 0.

# Batch 11 — Enforced physical pack layout · Phase 4 · Sol(XHARD)
Tasks: 4.2 `[XHARD]`. GPT-5.6 Sol owns this batch and must delegate implementation and positive/negative layout validation before synthesizing the batch result.

**4.2 `[XHARD]` Enforce actual pack-root layout**

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

CHECKPOINT:

- Batch 10 has PASSED.
- `python3 -m astrid packs validate astrid/packs` exits 0.
- `pytest -q tests/packs/test_pack_layout_contract.py tests/packs/test_packs_validate.py tests/packs/test_pack_rendering_extensions.py tests/packs/fal` passes.
- Tests prove validation walks actual pack-root entries rather than exception declarations alone.
- Rendering’s declared backend/planner/finalizer roots, editorial’s declared `hype/` support library, pack/capability golden fixtures, fixtures, schemas, examples, skills, docs, and Python package markers validate.
- Editorial `hype/` exposes no independent capability, discovery, or CLI surface.
- Negative fixtures reject undeclared loose files and directories and report their actionable paths.
- `test ! -e astrid/packs/fal/tests/test_h3_video.py && test -e tests/packs/fal/test_h3_video.py` exits 0.
- No broad catch-all pack-root exception has been added.
- `scripts/reshape/run_ci_checks.sh` exits 0.

# Batch 12 — Repository truth and full closure · Phase 4 · Flash
Tasks: 4.3, 4.4, and 4.5, executed in that order.

**4.3 Close root-hygiene gaps and root-writing tests**

- Verify `fal-voice-upscale/` is absent after Task 2.1 and remove it from `ROOT_DIR_ALLOWLIST` in `scripts/reshape/check_repo_hygiene.py`.
- Add `*.mp3` to `.gitignore` media rules and the hygiene checker’s tracked-runtime-media rules.
- Extend `find_unknown_root_entries()` and `tests/reshape/test_repo_hygiene.py` to inspect actual root filesystem entries as well as tracked Git paths.
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

**4.4 Complete the documentation and CI truth pass**

- `docs/packs/contract.md`: locked kernel, one manifest-backed graph, no concrete capability exceptions.
- `docs/packs/pack-taxonomy.md`: `_core` as a manifest-backed system pack; visible `builtin`; Blender included; all current shipped manifests accurately described as `install_tier: core`; correct alias-carrier claims.
- `docs/architecture/repo-shape.md`: actual `astrid/core/execution/{executor,orchestrator}` paths, current gateway modules, complete kernel directories, and no legacy workspace source.
- `docs/architecture/import-tiers.md`: packs may consume stable kernel APIs and pack support modules; core may use only the fixed runtime bridges and provider-neutral Protocols.
- `docs/contracts/platform-contract.md` and `docs/architecture/repo-shape.md`: derive the SDK public surface accurately—currently 32 exports, not 28.
- `docs/packs/adapter-packs.md`: include `fal.h3_video`.
- `docs/reference/{architecture,sdk}.md`: qualified capability routes, pack-only discovery, and no silent module CLI.
- Generation, iteration, Reigh, RunPod, YouTube, Blender, rendering, builtin, and `_core` skills/manifests/STAGE files: current paths and commands.
- `docs/contracts/integration_contracts.md` and `docs/contracts/asset-resolution-generation-bridge-contract.md`: pack ownership while retaining documented `astrid serve`.
- `docs/guides/ci-lanes.md` and documentation-command verification: arbitrary-depth changed-file selection and current workflow paths.
- Regenerate `astrid/packs/_core/skill/SKILL.md` from the deterministic tracked inventory.
- Search for stale core-domain paths, removed aliases, obsolete pack counts, direct pack module commands, and old `_core` exception language.

**4.5 Run the full closure gate**

- Run `python3 -m astrid packs validate astrid/packs`.
- Run schema, pack discovery, skill, element, structure, gateway, doctor, generation, iteration, Reigh, RunPod, rendering, layout, CI-selection, and hygiene test groups.
- Run `scripts/smoke_wheel_install.sh` outside the source checkout with an empty `ASTRID_HOME`.
- Run `scripts/reshape/run_ci_checks.sh` and the full broad test suite; do not rely on the changed-file fast lane alone.
- Run Remotion typechecking and renderer-parity tests.
- Verify the import-layer checker remains green with zero new exemptions.
- Search for retired gateway tokens, deleted core-domain imports, hardcoded old backend module strings, obsolete paths, and unsupported direct module commands.
- Verify all executors, orchestrators, elements, skills, and concrete generation backends originate from manifest-backed packs.
- Verify `astrid/core/integrations/` contains only the retained Arnold implementation domain.
- Verify the deterministic capability index and repository hygiene from a clean checkout.

CHECKPOINT:

- Batch 11 has PASSED.
- `test ! -e fal-voice-upscale` exits 0.
- `rg -n 'fal-voice-upscale' scripts/reshape/check_repo_hygiene.py` returns nothing, and `.gitignore` plus the hygiene checker cover `*.mp3`.
- `pytest -q tests/reshape/test_repo_hygiene.py` passes, including real-filesystem root entries and tracked-path cases.
- Root-writing tests use `tmp_path`, `TemporaryDirectory()`, or system temporary paths; the batch diff contains no path beneath `.oracle-threejs-archive/`.
- `python3 scripts/reshape/check_repo_hygiene.py` exits 0 from a clean checkout.
- `python3 -m astrid packs validate astrid/packs` exits 0.
- Targeted schema, discovery, skill, element, structure, gateway, doctor, generation, iteration, Reigh, RunPod, rendering, layout, CI-selection, and hygiene groups pass.
- `scripts/smoke_wheel_install.sh` exits 0 outside the checkout with an empty `ASTRID_HOME`.
- `scripts/reshape/run_ci_checks.sh` exits 0.
- `pytest --tb=no -q --no-header` passes.
- `npm --prefix remotion run typecheck` exits 0.
- `pytest -q -m renderer_parity tests/packs/test_renderer_parity.py` and the targeted rendering tests pass.
- `python3 -c 'from astrid.core.structure import validate_import_layering, validate_repo_structure; assert not validate_import_layering(); assert not validate_repo_structure()'` exits 0 with no new exemptions.
- Repository searches find no retired gateway routes, live imports of `astrid.core.integrations.{reigh,runpod,worker}`, concrete old backend paths, `astrid.core.experiments`, obsolete pack counts, unsupported direct module commands, or old manifest-less `_core` exception language.
- Every executor, orchestrator, element, skill, and concrete generation backend resolves from a manifest-backed pack.
- The only implementation-domain directory beneath `astrid/core/integrations/` is `arnold`.
- Regenerating the capability index and `_core` skill from a clean checkout produces no diff.
- `.oracle/tasklist.md` remains byte-identical to the frozen tasklist; any execution-time plan revision has been handled through the oracle.
