# SPRINT 1

Frozen execution rule: execute batches in the order below. Preserve task scope exactly. This is a direct-cut migration: no deprecation windows, compatibility releases, temporary redirects, or fallback routes. Any execution-time revision must go through the oracle.

# Batch 1 — kernel and lifecycle lock · Phase 0 · Sol(XHARD)

Execution owner: GPT-5.6 Sol. Delegation mandate: delegate bounded implementation, investigation, and validation work; retain responsibility for integration, architectural decisions, and checkpoint proof.

Tasks: 0.1 `[XHARD]` Lock the kernel, remove lifecycle duality, and eliminate core-to-pack exceptions — L · Depends: none

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
- Port any still-required run inspection/admin operations to common Arnold-backed run state in this task. Delete task-only verbs with no Arnold meaning rather than retaining a fallback engine.
- Replace `astrid/core/integrations/arnold/host/compat.py` with one exact lazy Arnold contract loader. Remove optional-symbol/version accommodation and compatibility naming.
- Delete the static product workflow and alias table in `astrid/core/integrations/arnold/host/shapes.py`.
- Resolve qualified orchestrators through the existing discovered registry and compile them through the existing Arnold lowering path. Add no shape extension framework.
- Consolidate pack runtime loading in the existing `astrid/core/pack/resolver.py`.
- Move the fresh-module loading behavior needed by in-process execution behind that resolver, then remove:
  - `_IMPORT_LAYERING_EXEMPT_REL`
  - `_PACK_RUNTIME_BRIDGE_EXEMPT_REL`
  - the claim that `runtime/in_process.py` is a static core-to-pack bridge
- Make the structural rail reject literal `astrid.packs.*` imports or importlib targets anywhere in core and reject direct runtime-module resolution outside the single resolver.
- State the exclusion rule: concrete adapters, discoverable capabilities, product workflows, and optional service domains belong in packs.
- Retain `astrid scratch` as the sole host escape hatch for arbitrary project-scoped scripts.
- Do not classify `astrid serve` as a permanent host contract; Task 2.5 removes it when the canonical Reigh executor lands.
- Classify `remotion/`, `themes/`, and Git-aware scripts as product-owned rather than reusable framework kernel.
- Accept `astrid.packs.*`, `python3 -m astrid`, and `<pack>.<name>` as Astrid product contracts.
- Record the zero-violation import/structure baseline and add negative tests proving no exception or product workflow table remains.
- Keep the extension-admission rule: use an extension only for a real core consumer with interchangeable implementations.

CHECKPOINT:

- `pytest -q tests/test_gateway_lifecycle_engine_dispatch.py tests/test_gateway_status_routing.py tests/test_lifecycle_start.py tests/test_lifecycle_next.py tests/test_lifecycle_ack.py tests/test_lifecycle_status.py tests/test_lifecycle_abort.py tests/core/integrations tests/core/runtime/test_in_process.py tests/test_structure_contracts.py`
- `python3 -c 'from astrid.core.structure import validate_import_layering, validate_repo_structure; assert validate_import_layering() == []; report = validate_repo_structure(); assert report.ok, report.errors'`
- `! rg -n '_IMPORT_LAYERING_EXEMPT_REL|_PACK_RUNTIME_BRIDGE_EXEMPT_REL' astrid tests`
- `! rg -n '(from|import)[[:space:]]+astrid\\.packs\\.|import_module\\([\"'\"']astrid\\.packs\\.' astrid/core`
- `! rg -n -- '--engine[ =](task|arnold)|task\\|arnold|fallback engine|fallback.*Arnold|release warning|sunset version' astrid docs tests --glob '!**/fixtures/**'`
- `python3 -m astrid --help` exposes one Arnold-backed lifecycle surface; qualified discovered orchestrators compile without a static product workflow or alias table.
- `runtime/in_process.py` resolves manifest-owned runtime targets only through `astrid/core/pack/resolver.py`.
- `scripts/reshape/run_ci_checks.sh --changed` passes.

# Batch 2 — legal `_core` system pack · Phase 0 · Sol(XHARD)

Execution owner: GPT-5.6 Sol. Delegation mandate: delegate bounded schema, loader, skills, and test work; retain responsibility for the trusted-source boundary and final checkpoint proof.

Tasks: 0.2 `[XHARD]` Make `_core` a legal, manifest-backed system pack — M · Depends: 0.1

- Update `_defs.json` so `pack_id` accepts normal IDs or reserved literal `_core`; provenance remains a loader concern.
- Add one `is_trusted_system_pack_source(pack_id, manifest_path)` seam in the loader.
- Accept `_core` only from the canonical shipped source root.
- Reject user, local, extra, environment, installed, symlinked, and relative-path `_core` claims.
- Preserve folder/ID equality and reject `_core.<name>` capability IDs.
- Add `astrid/packs/_core/pack.yaml` with system metadata, its skill root, and no capabilities, dependencies, or extensions.
- Remove manifest-less skill-shell handling from first-party and layout validation.
- Add `astrid/skills/branding.py` as the one `_core` → `astrid` presentation seam.
- Consume it from all Claude, Codex, Hermes, registry, CLI, and installer code.
- Preserve literal `python3 -m astrid` commands and Python package paths.
- Extend schema, loader, discovery, validation, skills, and wheel tests for canonical trust, noncanonical rejection, empty capabilities, and branding invariants.

CHECKPOINT:

- `test -f astrid/packs/_core/pack.yaml`
- `python3 -m astrid packs validate astrid/packs`
- `pytest -q tests/packs/test_pack_yaml_schema.py tests/packs/test_pack_discovery.py tests/packs/test_pack_discovery_canonical.py tests/packs/test_pack_layout_contract.py tests/packs/test_packs_validate.py tests/test_skills.py tests/test_skills_sync_registry.py tests/test_sdk_public_surface.py`
- Tests prove canonical shipped `_core` succeeds while user, local, extra, environment, installed, symlinked, and relative-path `_core` claims fail.
- Tests prove `_core.<name>` capability IDs fail and `_core` declares no capabilities, dependencies, or extensions.
- `test -f astrid/skills/branding.py`
- `! rg -n 'skill.only.shell|manifest.less.*skill|_core.*not a pack' astrid/core astrid/skills docs --glob '!**/fixtures/**'`
- All harness-facing `_core` → `astrid` presentation goes through `astrid/skills/branding.py`; literal `python3 -m astrid` and Python package paths remain unchanged.
- `scripts/reshape/run_ci_checks.sh --changed` passes.

# Batch 3 — deterministic bundled inventory and `builtin` deletion · Phase 0 · Flash

Execution owner: DeepSeek V4 Flash.

Tasks: 0.3 Establish one deterministic product-owned inventory and delete `builtin` — M · Depends: 0.2

- Replace hardcoded first-party pack sets with `astrid/packs/bundled.yaml`.
- Include the complete tracked manifest-backed set, including `_core` and Blender.
- Exclude `builtin`, `discord_local`, and `seedance_local`.
- Delete `astrid/packs/builtin/`, including `builtin.agent_probe`, its build output, fixtures, and golden data.
- Replace production `builtin.agent_probe` regression use with a temporary test pack fixture exercising the same generic orchestration behavior.
- Remove every `builtin.*` reference from manifests, tests, docs, skills, fixtures, and Arnold state.
- Make generic first-party validation consume an explicit adjacent inventory rather than embedded Astrid IDs or Git assumptions.
- Derive displayed counts, docs, tests, and wheel parity from the inventory.
- Keep checkout-local personal packs runtime-discoverable but exclude them from committed index generation.
- Make capability-index generation select only tracked bundled IDs.
- Add generated-output check modes for the capability index and `_core/skill/SKILL.md`.
- Regenerate both artifacts from a clean checkout.

CHECKPOINT:

- `test -f astrid/packs/bundled.yaml`
- `test ! -e astrid/packs/builtin`
- `python3 -m astrid packs validate astrid/packs`
- `pytest -q tests/packs/test_packs_shipped_ids.py tests/packs/test_pack_layout_contract.py tests/packs/test_pack_discovery.py tests/packs/test_packs_validate.py tests/test_sprint1_regression.py tests/test_skills_sync_registry.py`
- `python3 scripts/gen_capability_index.py --check` passes from a clean checkout and verifies both the capability index and `astrid/packs/_core/skill/SKILL.md` are current.
- The inventory contains `_core` and `blender`; it contains neither `builtin`, `discord_local`, nor `seedance_local`.
- `! rg -n '\\bbuiltin(?:\\.|\\b)' astrid docs scripts --glob '!**/fixtures/**' --glob '!**/golden/**'`
- Generated counts and wheel-parity expectations derive from `astrid/packs/bundled.yaml`; no embedded first-party ID set remains.
- A temporary test pack, not a shipped compatibility namespace, covers the former `builtin.agent_probe` orchestration behavior.
- `scripts/reshape/run_ci_checks.sh --changed` passes.

# Batch 4 — canonical reader graph and pack-only elements · Phase 1 · Sol(XHARD)

Execution owner: GPT-5.6 Sol. Delegation mandate: delegate the skills-reader and element-reader work as bounded streams; retain responsibility for their shared discovery ordering, invalid-manifest semantics, and integrated checkpoint.

Tasks: 1.1 `[XHARD]` Route every reader through the canonical discovered-pack stream — M · Depends: 0.2–0.3; then 1.2 `[XHARD]` Remove theme and workspace element discovery — L · Depends: 1.1

Task 1.1 detail:

- Refactor skills discovery and the agent index to consume ordered `DiscoveredPack` records.
- Cover source, local, extra, environment, and installed roots.
- Delete direct `PACKS_DIR.iterdir()` walks, manifest-less fallback, swallowed manifest errors, duplicate scanners, and the agent-index dual discovery path.
- Fix `ASTRID_PACKS_PATH` skill discovery.
- Exclude hidden packs consistently from every discovery layer.
- Treat `deprecated` only as lifecycle metadata: such a pack remains discoverable under its canonical ID, with no redirected name, warning window, or retained implementation path.
- Obtain skill roots only from `DiscoveredPack.skill_roots()`.
- Apply pack-ID deduplication once at canonical source priority.
- Preserve explicit-root testability by parameterizing shared discovery.
- Make invalid manifests fail at the pack boundary and leak no capabilities or skills.
- Preserve top-level SDK laziness.
- Add ordering, `_core`, duplicate, hidden-installed, deprecated-status, invalid-manifest, and checkout-local tests.

Task 1.2 detail:

- Remove `ElementSource`, `default_sources()`, `load_source_elements()`, active-theme element loading, `WORKSPACE_ROOT`, `legacy_workspace`, and conflict warnings.
- Build the element registry exclusively from discovered pack metadata and declared element roots.
- Add no pseudo-packs for theme or workspace directories.
- Remove discovery-only `active_theme`, `include_missing_roots`, and `elements --theme` inputs.
- Update timeline validators, training, rendering, SDK discovery, and effect-registry generation to consume pack metadata.
- Keep theme selection, state, pointers, and provenance as rendering data.
- Replace positive theme/workspace discovery tests with negative no-scan rails.
- Preserve local-pack precedence and rendering behavior.
- Require every loaded element to report `source == "pack:<id>"`.

CHECKPOINT:

- Execute and validate Task 1.1 before beginning Task 1.2.
- `pytest -q tests/packs/test_pack_discovery.py tests/packs/test_pack_discovery_canonical.py tests/packs/test_pack_discovery_metadata.py tests/packs/test_pack_local_priority.py tests/test_skills.py tests/test_skills_sync_registry.py tests/test_sdk_public_surface.py`
- `pytest -q tests/core/test_elements_registry.py tests/core/test_elements_cli.py tests/core/test_elements_install.py tests/timeline/test_effects_catalog.py tests/timeline/test_timeline_elements_catalog.py tests/packs/test_composition_elements.py tests/packs/test_text_card_render.py`
- Tests cover source, local, extra, `ASTRID_PACKS_PATH`, environment, installed, `_core`, duplicate, hidden, deprecated, invalid-manifest, and checkout-local ordering.
- Invalid manifests expose no capabilities, skills, or elements.
- `! rg -n 'PACKS_DIR\\.iterdir|ElementSource|default_sources\\(|load_source_elements\\(|legacy_workspace|WORKSPACE_ROOT|include_missing_roots|elements --theme' astrid --glob '*.py'`
- Every loaded element reports `source == "pack:<id>"`; negative fixtures prove themes and workspace directories are not scanned.
- `python3 -c 'import astrid, sys; assert "astrid.core.pack.discovery" not in sys.modules'` passes.
- `python3 -m astrid packs validate astrid/packs`
- `scripts/reshape/run_ci_checks.sh --changed` passes.

# Batch 5 — wheel-packaged canonical graph · Phase 1 · Flash

Execution owner: DeepSeek V4 Flash.

Tasks: 1.3 Package and prove the canonical graph in wheels — L · Depends: 1.1–1.2

- Replace the rendering-only package-data declaration with explicit coverage for:
  - model-catalog YAML
  - rendering schemas and parity fixtures
  - `packs/bundled.yaml`
  - pack manifests
  - executor/orchestrator manifests
  - element manifests
  - rendering extension YAML
  - pack and nested executor skills
  - executor/orchestrator `STAGE.md`
- Use no blanket recursive pack-root include.
- Extend wheel smoke to run outside the checkout with empty `ASTRID_HOME`.
- Prove every bundled ID has its canonical manifest in the wheel.
- Prove source inventory and wheel inventory agree.
- Exercise representative capabilities, skills, STAGE files, extension files, model catalogs, and an empty installed store.
- Preserve `import astrid` laziness.
- Prove registry loading does not eagerly import concrete generation, Reigh, or RunPod implementations.

CHECKPOINT:

- `python3 -m astrid packs validate astrid/packs`
- `pytest -q tests/packs/test_pack_discovery.py tests/packs/test_packs_shipped_ids.py tests/core/rendering/test_package_data.py tests/test_skills.py tests/test_sdk_public_surface.py`
- `ASTRID_HOME="$(mktemp -d)" bash scripts/smoke_wheel_install.sh` passes outside the checkout.
- Wheel smoke proves every ID in `astrid/packs/bundled.yaml` has its canonical `pack.yaml` and that source and wheel inventories are identical.
- Wheel smoke exercises model-catalog YAML, pack manifests, executor/orchestrator/element manifests, rendering extension YAML, pack and nested executor skills, and `STAGE.md` files with an empty installed store.
- Wheel package-data configuration uses explicit patterns and no blanket recursive pack-root include.
- `import astrid` and registry discovery remain lazy; concrete generation, Reigh, and RunPod implementation modules are absent from `sys.modules` after registry loading.
- `python3 scripts/gen_capability_index.py --check`
- `bash scripts/reshape/run_ci_checks.sh`
- Phase-1 gate result is recorded as `PASS` only if every criterion above succeeds. Batch 5 / Task 1.3 is the hard inter-sprint gate; no 2.x task may start without this result.

# Batch 10a — pulled-forward alias eradication · Phase 3 · Flash

Execution owner: DeepSeek V4 Flash.

Tasks: 3.1 Remove all alias surfaces — M · Depends: 1.3

Placement: deliberately pulled forward into Sprint A after Batch 5. It is independent of Phase 2 extraction and runs only after the Batch 5 hard gate passes.

- Delete the pack-level `aliases:` schema field, definition field, parser, normalizer, resolver, validation, and registry wiring.
- Remove `AliasRecord`, `AliasResolver`, alias-cycle logic, deprecation messages, and alias tests.
- Delete every alias declaration from shipped manifests, including `builtin.*`, `external.*`, `upload.youtube`, and similar alternate IDs.
- Require all code, manifests, tests, skills, docs, fixtures, and stored examples to use canonical qualified IDs directly.
- Delete the `builtin` namespace rather than redirecting it.
- Remove top-level `publish`, `publish-youtube`, `upload-youtube`, and `reigh-data`.
- Remove `astrid author` and `astrid run`.
- Remove the implicit flag-first `astrid --brief/--video` route; use the canonical qualified orchestrator.
- Remove Arnold CLI aliases and element-kind aliases such as `crossfade`.
- Replace `tests/test_canonical_aliases.py` with canonical-ID rejection and uniqueness tests.
- Replace the aliases/forks/overrides guide with a forks-and-overrides guide.
- Keep forks and explicit user overrides: they are customization contracts, not migration redirects.
- Add negative root-help, unknown-command, schema, and manifest tests for every removed name.

CHECKPOINT:

- `pytest -q tests/test_canonical_cli.py tests/test_cli_choices.py tests/test_qualified_id_enforcement.py tests/test_public_id_resolution.py tests/test_override.py tests/packs/test_pack_yaml_schema.py tests/packs/test_pack_parser_binding.py`
- `test ! -e tests/test_canonical_aliases.py`
- `! rg -n '^[[:space:]]*aliases:' astrid/packs --glob 'pack.yaml'`
- `! rg -n 'AliasRecord|AliasResolver|alias-cycle|capability_alias|builtin\\.|external\\.|upload\\.youtube' astrid docs scripts --glob '!**/fixtures/**' --glob '!**/golden/**'`
- Root help and unknown-command tests reject `publish`, `publish-youtube`, `upload-youtube`, `reigh-data`, `author`, `run`, implicit `--brief/--video`, Arnold aliases, and `crossfade`.
- Canonical qualified IDs succeed; each removed alias fails without warning, redirect, deprecation window, or retained implementation.
- Fork and explicit override tests remain green.
- `python3 -m astrid packs validate astrid/packs`
- `bash scripts/reshape/run_ci_checks.sh`

Inter-sprint dependency gate:

- Batch 5 / Task 1.3 must have a recorded `PASS`.
- No 2.x task may start before that result.
- Source, local, extra, environment, installed, and wheel-source layers must all resolve through the canonical graph.
- The wheel must contain the complete bundled inventory.
- Sprint A is shippable only with Arnold as the sole lifecycle engine, `_core` manifest-backed, `builtin` deleted, aliases deleted rather than redirected, canonical wheel discovery proven, and both `validate_import_layering()` and `validate_repo_structure()` green with zero exemptions.
- This is not a compatibility release.

# SPRINT 2

Frozen execution rule: execute batches in the order below after the inter-sprint dependency gate passes. Preserve task scope exactly. Continue direct cuts without deprecation windows, compatibility routes, or temporary shims. Any revision must go through the oracle.

# Batch 6 — generation and RunPod extraction · Phase 2 · Sol(XHARD)

Execution owner: GPT-5.6 Sol. Delegation mandate: delegate Tasks 2.1 and 2.3 as independent implementation streams where safe; retain responsibility for kernel-boundary consistency, integration, and the combined checkpoint.

Tasks: 2.1 `[XHARD]` Move concrete generation backends into the generation pack — M · Depends: 1.1, 1.3; and 2.3 `[XHARD]` Move RunPod maintenance into its pack and delete the host route — M · Depends: 0.1, 1.1

Task 2.1 detail:

- Move Fal, Codex, and VibeComfy backends into `astrid/packs/generation/backends/`.
- Declare all three through the existing generation backend extension.
- Delete builtin descriptor seeding and hardcoded module strings from core.
- Keep provider-neutral protocols, registry, taxonomy, verbs, and feature contracts in core.
- Require a bare registry to be empty and default loading to come only from manifests.
- Remove concrete core exports and lazy concrete imports.
- Update generation executors, unavailable-reason handling, tests, SDK discovery, gateway resolution, and model validation.
- Delete all tracked `fal-voice-upscale/` files.
- Move adapter tests under `tests/packs/generation/`.
- Add negative tests for old core paths and literal module strings.
- Prove wheel discovery gains and loses all three backends with the generation manifest.

Task 2.3 detail:

- Move RunPod storage and sweeper implementations into the RunPod pack.
- Add `runpod.sweep`, `runpod.list_volumes`, and `runpod.ensure_storage`.
- Add no core RunPod protocol, extension, or doctor hook.
- Preserve dry-run diagnostics and storage recovery behavior.
- Delete top-level `astrid runpod`, `astrid/core/gateway/runpod.py`, its dispatch functions, help, exports, and allowlist entries in this same task.
- Do not temporarily rewire the old route.
- Remove RunPod checks from core doctor and cover the executor instead.
- Keep `require_existing_storage` pack-local.
- Give training a local diagnostic pointing directly to `runpod.ensure_storage`.
- Remove `astrid/core/integrations/runpod/` after imports move.
- Relocate and retarget tests.
- Add no structure exemption.

CHECKPOINT:

- `test -d astrid/packs/generation/backends`
- `test ! -e astrid/core/generation/backends`
- `test ! -e fal-voice-upscale`
- `test ! -e astrid/core/gateway/runpod.py`
- `test ! -e astrid/core/integrations/runpod`
- `pytest -q tests/core/test_generation_backend_registry.py tests/core/test_generation_taxonomy_registry.py tests/packs/generation tests/packs/runpod tests/test_doctor_setup.py`
- A bare generation registry is empty; manifest loading supplies exactly the Fal, Codex, and VibeComfy backends.
- Wheel tests prove removing the generation manifest removes all three backends and restoring it restores all three.
- `python3 -m astrid executors inspect runpod.sweep --json`
- `python3 -m astrid executors inspect runpod.list_volumes --json`
- `python3 -m astrid executors inspect runpod.ensure_storage --json`
- Root help and unknown-command tests reject `astrid runpod`; no transitional dispatch remains.
- `! rg -n 'astrid\\.core\\.generation\\.backends|astrid\\.core\\.integrations\\.runpod|astrid runpod' astrid docs scripts --glob '!**/fixtures/**'`
- `python3 -c 'from astrid.core.structure import validate_import_layering, validate_repo_structure; assert validate_import_layering() == []; report = validate_repo_structure(); assert report.ok, report.errors'`
- `scripts/reshape/run_ci_checks.sh --changed` passes.

# Batch 7 — experiment extraction and declared pack dependencies · Phase 2 · Sol(XHARD)

Execution owner: GPT-5.6 Sol. Delegation mandate: delegate dependency inventory, experiment relocation, and adversarial import-policy testing; retain responsibility for edge classification, API decisions, and zero-deferred-violation closure.

Tasks: 2.2 `[XHARD]` Move experiments, declare dependencies, and eliminate entrypoint coupling — L · Depends: 0.1, 1.1

- Add optional sorted, unique `depends` pack IDs for static Python support dependencies.
- Keep it distinct from external dependencies and capability composition.
- Reject malformed, duplicate, self, cyclic, undeclared, missing, and stale dependencies.
- Add `astrid/core/pack/import_policy.py` and its test suite.
- Inventory all static and literal-dynamic cross-pack imports.
- Classify every edge as capability invocation, genuine support dependency, or accidental/private coupling.
- Replace execution dependencies with qualified capability dispatch.
- Forbid importing another pack’s executor/orchestrator `run.py`, even with `depends`.
- Move only genuinely shared symbols into narrow owning-pack support modules.
- Declare surviving edges such as editorial → training, video_editing → editorial, and editorial → iteration.
- Move `astrid/core/experiments/` directly to `astrid/packs/iteration/experiments/`.
- Delete the old path and update all consumers in the same change.
- Move experiment tests under `tests/packs/iteration/experiments/`.
- Finish with no known violation deferred.

CHECKPOINT:

- `test -f astrid/core/pack/import_policy.py`
- `test -d astrid/packs/iteration/experiments`
- `test ! -e astrid/core/experiments`
- `test -d tests/packs/iteration/experiments`
- `test ! -e tests/core/experiments`
- `pytest -q tests/packs/test_pack_yaml_schema.py tests/packs/test_packs_validate.py tests/packs/iteration tests/packs/iteration/experiments`
- Import-policy tests reject malformed, duplicate, self, cyclic, undeclared, missing, and stale `depends` edges.
- The declared dependency graph is sorted, unique, complete, necessary, and acyclic.
- No pack imports another pack’s executor/orchestrator `run.py`, including packs with a declared `depends` edge.
- All execution dependencies use qualified capability dispatch.
- `! rg -n 'astrid\\.core\\.experiments' astrid tests docs --glob '!**/fixtures/**'`
- The import-policy checker reports zero violations and no deferred allowlist.
- `python3 -c 'from astrid.core.structure import validate_import_layering, validate_repo_structure; assert validate_import_layering() == []; report = validate_repo_structure(); assert report.ok, report.errors'`
- `scripts/reshape/run_ci_checks.sh --changed` passes.

# Batch 8 — Reigh state inversion and implementation extraction · Phase 2 · Sol(XHARD)

Execution owner: GPT-5.6 Sol. Delegation mandate: execute Task 2.4 before Task 2.5; delegate provider-neutral state tests and pack extraction work as bounded streams, while retaining responsibility for the inversion boundary and deletion of host routes.

Tasks: 2.4 `[XHARD]` Invert generic Reigh bridge state before extraction — M · Depends: 0.1; then 2.5 `[XHARD]` Move Reigh and worker implementations into the Reigh pack and delete host routes — L · Depends: 2.4

Task 2.4 detail:

- Create provider-neutral `astrid/core/timeline/asset_registry_state.py`.
- Move latest-event recovery, sidecar repair, record/source resolution, and no-pruning merge behavior into it.
- Make core edits and the eventual pack bridge consume this helper.
- Extend the existing remote-timeline Protocol only with required load/save/list shapes.
- Keep implementations caller-injected; add no Reigh registry or extension.
- Preserve generic timeline backend modules.
- Keep event recovery, CAS, reconciliation, sidecar, no-op, and no-pruning tests in the timeline suite.

Task 2.5 detail:

- Move Reigh environment, provider, transport, task client, remote timeline, JWT/JWKS, append service, errors, and worker implementations into the Reigh pack.
- Delete `astrid/core/integrations/reigh/` and `astrid/core/integrations/worker/`.
- Delete `event_construction.py`, integration-local `supabase_client.py`, and all other compatibility exports or copies.
- Add canonical `reigh.worker`.
- Add canonical `reigh.serve_local_bridge --projects-root`.
- Make `executors run reigh.serve_local_bridge` the only public server invocation.
- Delete top-level `astrid worker` and `astrid serve`, their dispatch/help/export code, and the sessionless `serve` allowlist entry in this same change.
- Use the normal executor contract. If it requires an attached/explicit project, accept that contract change; do not add a new sessionless host adapter.
- Add narrow `reigh.timeline_edit` for `add-clip`, `move-clip`, and `set-theme`.
- Preserve PAT defaults, optional service-role authentication, optimistic versioning, three retries, `force=False`, and event descriptors.
- Remove remote `projects list` and `projects edit`.
- Delete `scripts/node/ops_helper.mjs`.
- Keep local project storage and local timeline commands in core.
- Ensure core contains no Reigh implementation import or hardcoded Reigh module string.

CHECKPOINT:

- Task 2.4 passes its timeline checkpoint before Task 2.5 begins.
- `test -f astrid/core/timeline/asset_registry_state.py`
- `pytest -q tests/timeline/test_asset_registry_contract.py tests/timeline/test_asset_registry_replaced.py tests/timeline/test_asset_registry_sync.py tests/timeline/test_backend_contract.py tests/timeline/test_eventlog.py tests/timeline/test_sync_state.py`
- `test ! -e astrid/core/integrations/reigh`
- `test ! -e astrid/core/integrations/worker`
- `test ! -e scripts/node/ops_helper.mjs`
- `pytest -q tests/packs/reigh tests/timeline tests/core/test_project_cli.py tests/test_banodoco_worker.py tests/test_worker_jwt.py tests/test_task_client.py`
- `python3 -m astrid executors inspect reigh.worker --json`
- `python3 -m astrid executors inspect reigh.serve_local_bridge --json`
- `python3 -m astrid executors inspect reigh.timeline_edit --json`
- `executors run reigh.serve_local_bridge` is the only public server invocation; root help and unknown-command tests reject `worker`, `serve`, remote `projects list`, and remote `projects edit`.
- Reigh tests prove PAT defaults, optional service-role authentication, optimistic versioning, three retries, `force=False`, and event descriptors.
- `! rg -n 'astrid\\.core\\.integrations\\.(reigh|worker)|astrid worker|astrid serve|event_construction|integrations/.*/supabase_client' astrid docs scripts --glob '!**/fixtures/**'`
- `! rg -n 'astrid\\.packs\\.reigh|packs\\.reigh' astrid/core`
- `python3 -c 'from astrid.core.structure import validate_import_layering, validate_repo_structure; assert validate_import_layering() == []; report = validate_repo_structure(); assert report.ok, report.errors'`
- `scripts/reshape/run_ci_checks.sh --changed` passes.

# Batch 9 — extraction import closure and supported pack API · Phase 2 · Sol(XHARD)

Execution owner: GPT-5.6 Sol. Delegation mandate: delegate full-tree import inventory and CI-selection verification; retain responsibility for supported-kernel API decisions, exception rejection, and zero-violation closure.

Tasks: 2.6 `[XHARD]` Close extraction imports, define the pack-facing API, and remove CI path coupling — L · Depends: 2.1–2.5

- Move all Reigh implementation tests under `tests/packs/reigh/`.
- Keep provider-neutral timeline/eventlog tests under `tests/timeline/`.
- Document `tests/packs/<id>/` as the extraction rail.
- Replace positive implementation inventories with negative absence rails.
- Require no live `astrid.core.integrations.{reigh,runpod,worker}` imports.
- Inventory every remaining pack-to-core import.
- Enforce exact machine-readable supported kernel module prefixes.
- Support only provider-neutral contracts and explicitly public foundation, execution, discovery, runtime, session, generation, rendering, timeline, and lineage APIs.
- Reject `_shared`, private symbols, CLI handlers, concrete implementations, and blanket utility families.
- Promote genuinely shared helpers into existing public modules or make them pack-local.
- Define the supported surface as a current pack contract; removals update every first-party caller atomically, with no alias or deprecation window promised.
- Wire the checker into CI with zero exemptions.
- Make changed-file selection arbitrary-depth under `astrid/**`.
- Update the bridge workflow for `astrid/packs/reigh/**` and the actual PR ref.
- Keep all moved tests discoverable under `tests/`.

CHECKPOINT:

- `test -d tests/packs/reigh`
- `test ! -e tests/integrations/reigh`
- `pytest -q tests/packs/reigh tests/packs/runpod tests/packs/generation tests/packs/iteration/experiments tests/timeline`
- The machine-readable supported kernel-prefix file exists, is consumed by the checker, and contains only the approved provider-neutral/public API families.
- The checker rejects `_shared`, private symbols, CLI handlers, concrete implementations, blanket utility families, undeclared dependencies, and every cross-pack executor/orchestrator `run.py` import.
- `! rg -n 'astrid\\.core\\.integrations\\.(reigh|runpod|worker)' astrid tests docs --glob '!**/fixtures/**'`
- `python3 -c 'from astrid.core.structure import validate_import_layering, validate_repo_structure; assert validate_import_layering() == []; report = validate_repo_structure(); assert report.ok, report.errors'`
- CI import-policy execution reports zero exemptions.
- A changed file at arbitrary depth beneath `astrid/**` selects its tests in `scripts/reshape/run_ci_checks.sh --changed`.
- `.github/workflows/bridge-latency.yml` covers `astrid/packs/reigh/**` and references the actual PR checkout.
- `scripts/reshape/run_ci_checks.sh --changed` passes for representative deeply nested generation, iteration, Reigh, and RunPod files.

# Batch 10b — residual compatibility deletion · Phase 3 · Flash

Execution owner: DeepSeek V4 Flash.

Tasks: 3.2 Delete remaining compatibility parsers and dual-path runtime support — L · Depends: 2.1–2.5, 3.1

- Delete `runtime_command_legacy` and require one canonical runtime manifest shape.
- Delete fallback parsing of legacy agent entrypoints; require `agent.normal_entrypoints`.
- Delete the legacy flat manifest parser; use the canonical YAML/JSON loader only.
- Delete the disabled project auto-bind compatibility functions.
- Remove rendering’s `engine` selector, neutral alias-to-engine translation, `legacy_engine.py`, and legacy argument adaptation.
- Require qualified renderer/planner/finalizer IDs and namespaced backend configuration.
- Rename the load-bearing hybrid planner directly from `rendering.legacy_hybrid` to `rendering.hybrid`; update every caller, fixture, schema, and provenance expectation in the same change and add no alias.
- Delete obsolete sibling-output compatibility parameters and re-export shells.
- Verify the RunPod, worker, and serve host routes removed in Phase 2 have not survived through help, exports, tests, or docs.
- Remove warning-window and sunset-version machinery.
- Add a scoped repository rail rejecting compatibility shims, alias bridges, legacy runtime shapes, and dual public routes.

CHECKPOINT:

- `pytest -q tests/test_schema_contract.py tests/test_component_manifest_parser_parity.py tests/test_runtime_correctness_inventory.py tests/core/rendering tests/packs/rendering tests/test_canonical_cli.py tests/test_cli_choices.py`
- `test ! -e astrid/core/rendering/legacy_engine.py`
- `! rg -n 'runtime_command_legacy|legacy_engine|rendering\\.legacy_hybrid|auto.bind|sunset.version|warning.window' astrid docs scripts --glob '!**/fixtures/**' --glob '!**/golden/**'`
- `! rg -n -- '(--engine|engine selector|alias.to.engine)' astrid/core/rendering astrid/packs/rendering docs`
- Canonical manifest tests accept only `agent.normal_entrypoints` and the canonical YAML/JSON manifest shape.
- Renderer, planner, and finalizer tests require qualified IDs and namespaced configuration.
- `rendering.hybrid` succeeds; `rendering.legacy_hybrid` fails with no alias or warning.
- Root help and unknown-command tests continue to reject `runpod`, `worker`, and `serve`.
- The scoped compatibility rail reports no shims, alias bridges, legacy runtime shapes, dual routes, warning windows, or sunset machinery outside explicit negative fixtures.
- `python3 -m astrid packs validate astrid/packs`
- `scripts/reshape/run_ci_checks.sh --changed` passes.

# Batch 11 — canonical private entrypoints and enforced pack layout · Phase 4 · Sol(XHARD)

Execution owner: GPT-5.6 Sol. Delegation mandate: execute Task 4.1 before Task 4.2; delegate Blender migration, transport guarding, and layout adversarial tests as bounded work while retaining responsibility for the final pack-root contract.

Tasks: 4.1 `[XHARD]` Canonicalize pack-private entrypoints — L · Depends: Phase 3; then 4.2 `[XHARD]` Enforce actual pack-root layout — L · Depends: 4.1

Task 4.1 detail:

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

Task 4.2 detail:

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

CHECKPOINT:

- Task 4.1 passes its entrypoint checkpoint before Task 4.2 begins.
- `python3 -m astrid executors inspect blender.deploy --json`
- Canonical `blender.deploy` execution succeeds through the executor gateway.
- Direct Blender helper/module CLI subprocesses fail; mesh fetching remains private support for `blender.render`.
- Rendering transport tests prove manifest-private renderer/planner/finalizer commands succeed only with the internal-invocation marker and fail when invoked directly.
- `pytest -q tests/packs/test_pack_layout_contract.py tests/packs/test_packs_validate.py tests/packs/rendering tests/core/rendering`
- `test -d tests/packs/fal`
- Layout validation walks actual filesystem entries and reports actionable paths for undeclared files/directories.
- Positive layout tests cover editorial support, rendering extension roots, fixtures, golden data, and capability-local golden fixtures.
- `editorial/hype/` has no discoverable capability or CLI surface.
- `! rg -n 'python(3)? -m astrid\\.sdk\\.rendering' astrid docs tests --glob '!**/fixtures/**'`
- Any surviving `python -m astrid.packs.*` instruction is an exact manifest-private transport command; all others are absent.
- `python3 -m astrid packs validate astrid/packs`
- `python3 -c 'from astrid.core.structure import validate_repo_structure; report = validate_repo_structure(); assert report.ok, report.errors'`
- `scripts/reshape/run_ci_checks.sh --changed` passes.

# Batch 12 — repository truth and full closure · Phase 4 · Flash

Execution owner: DeepSeek V4 Flash.

Tasks: 4.3 Close root-hygiene gaps and root-writing tests — M · Depends: 2.1, 4.2; then 4.4 Complete the documentation and CI truth pass — M · Depends: 4.1–4.3; then 4.5 Run the full closure gate — M · Depends: 4.1–4.4

Task 4.3 detail:

- Verify `fal-voice-upscale/` is absent and remove its root allowlist entry.
- Add `*.mp3` to Git ignore and tracked-runtime-media rules.
- Inspect actual root filesystem entries as well as tracked Git paths.
- Keep the hygiene checker product-repository-owned.
- Replace root-directed test output with `tmp_path`, `TemporaryDirectory()`, or system temp paths.
- Add no speculative deletion rules for absent unrelated directories.
- Do not touch `.oracle-threejs-archive/`.
- Run final hygiene from a clean checkout.

Task 4.4 detail:

- Document the exact kernel, Arnold-only lifecycle, canonical product namespace, supported pack API, `depends`, and extension-admission rule.
- Document no core-to-pack exceptions and no static Arnold product shape table.
- Document `_core` as the system pack and the absence of `builtin`.
- Remove alias-carrier, compatibility-window, fallback-engine, `astrid serve`, and extraction-debt language.
- Document hidden and deprecated status without implying redirected names or retained old implementations.
- Correct repository shape, execution paths, gateway modules, and SDK export count.
- Document pack-only discovery and qualified capability routes.
- Document `rendering.hybrid` and remove the old planner ID.
- Update Generation, Iteration, Reigh, RunPod, YouTube, Blender, rendering, and `_core` skills/manifests/STAGE files.
- Update integration contracts to point to `executors run reigh.serve_local_bridge`.
- Update CI-lane documentation and command verification.
- Regenerate `_core/skill/SKILL.md`.
- Search for stale domain paths, aliases, `builtin`, removed host verbs, legacy runtime shapes, old planner IDs, direct pack commands, and old exception language.

Task 4.5 detail:

- Run pack validation.
- Run schema, dependency/import-policy, discovery, skills, elements, structure, gateway, doctor, generation, iteration, Reigh, RunPod, rendering, layout, CI-selection, and hygiene tests.
- Run wheel smoke outside the checkout with empty `ASTRID_HOME`.
- Run `scripts/reshape/run_ci_checks.sh` and the full broad suite.
- Run Remotion typechecking and renderer-parity tests.
- Require zero import-layer exemptions.
- Require no runtime-resolver file allowlist.
- Require a complete, non-stale, acyclic `depends` graph.
- Require no cross-pack executor/orchestrator entrypoint imports.
- Require every pack-to-core import to belong to the supported API.
- Run generated-artifact check modes.
- Prove no shipped manifest contains `aliases:`.
- Prove `builtin`, `builtin.*`, removed gateway verbs, task-engine selection, old runtime shapes, `rendering.legacy_hybrid`, and old core-domain paths are absent outside explicit negative fixtures.
- Prove `runtime/in_process.py` and the Arnold host contain no product-specific exception or workflow table.
- Prove every capability, skill, and concrete generation backend originates from a manifest-backed pack.
- Prove `astrid/core/integrations/` contains only the exact Arnold host contract.
- Verify moved tests follow `tests/packs/<id>/`.
- Verify clean-checkout indexing and hygiene without touching `.oracle-threejs-archive/`.

CHECKPOINT:

- Execute Task 4.3, then Task 4.4, then Task 4.5.
- `test ! -e fal-voice-upscale`
- `rg -n '^\\*\\.mp3$' .gitignore`
- `! rg -n 'fal-voice-upscale' scripts/reshape/check_repo_hygiene.py`
- Hygiene tests inspect tracked paths and actual root filesystem entries; root-writing tests use `tmp_path`, `TemporaryDirectory()`, or system temp paths.
- `.oracle-threejs-archive/` is unchanged.
- `python3 scripts/reshape/check_repo_hygiene.py` passes from a clean checkout.
- `python3 scripts/gen_capability_index.py --check`
- `python3 -m astrid packs validate astrid/packs`
- `python3 -c 'from astrid.core.structure import validate_import_layering, validate_repo_structure; assert validate_import_layering() == []; report = validate_repo_structure(); assert report.ok, report.errors'`
- `! rg -n '_IMPORT_LAYERING_EXEMPT_REL|_PACK_RUNTIME_BRIDGE_EXEMPT_REL' astrid tests`
- `! rg -n '^[[:space:]]*aliases:' astrid/packs --glob 'pack.yaml'`
- `test ! -e astrid/packs/builtin`
- `! rg -n '(from|import)[[:space:]]+astrid\\.packs\\.|import_module\\([\"'\"']astrid\\.packs\\.' astrid/core`
- `! rg -n 'astrid\\.core\\.integrations\\.(reigh|runpod|worker)|rendering\\.legacy_hybrid|runtime_command_legacy' astrid docs scripts --glob '!**/fixtures/**' --glob '!**/golden/**'`
- Root help and unknown-command tests reject `builtin`, `author`, `run`, implicit `--brief/--video`, `publish`, `publish-youtube`, `upload-youtube`, `reigh-data`, `runpod`, `worker`, and `serve`.
- `astrid/core/integrations/` contains only the exact Arnold host contract.
- The `depends` graph is complete, necessary, sorted, unique, non-stale, and acyclic.
- No pack imports another pack’s executor/orchestrator `run.py`.
- Every pack-to-core import belongs to the machine-readable supported kernel API.
- Every capability, skill, element, and concrete generation backend originates from a manifest-backed discovered pack.
- `pytest -q tests/test_schema_contract.py tests/packs/test_pack_yaml_schema.py tests/packs/test_pack_discovery.py tests/test_skills.py tests/core/test_elements_registry.py tests/test_structure_contracts.py tests/test_canonical_cli.py tests/test_doctor_setup.py tests/core/test_generation_backend_registry.py tests/packs/generation tests/packs/iteration tests/packs/reigh tests/packs/runpod tests/core/rendering tests/packs/rendering tests/packs/test_pack_layout_contract.py tests/reshape 2>/dev/null || pytest -q tests/test_schema_contract.py tests/packs tests/test_skills.py tests/test_structure_contracts.py tests/test_canonical_cli.py tests/test_doctor_setup.py tests/core/rendering`
- `pytest -q tests/packs/test_renderer_parity.py`
- `cd remotion && npm run typecheck`
- `ASTRID_HOME="$(mktemp -d)" bash scripts/smoke_wheel_install.sh`
- `ASTRID_CI_SKIP_COVERAGE=1 bash scripts/reshape/run_ci_checks.sh`
- `pytest --tb=no -q --no-header -m "not integration and not opt_in"`
- Final oracle verdict is `PASS` only when every command and absence rail above succeeds from a clean checkout. The resulting release has one discovery graph, one lifecycle engine, one canonical ID per capability, one public route per operation, and no retained migration scaffolding.
