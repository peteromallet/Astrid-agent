# Astrid packification — megado status

**Status: EXECUTION-READY** — megado Phases 0-4 complete (plan STABLE v7, sprint split, tasklist frozen v3); Phase 5 (execution) not started.

## Aspiration (accepted)

Every discoverable capability and every optional domain is a pack, behind a
closed, named kernel — with portability seams for a future framework
extraction, and **no transitional machinery**: shims, migration patterns,
deprecation windows, fallback engines, and compatibility routes are cut
outright, not preserved.

## Artifacts

- `.oracle/plan.md` — STABLE plan v7: SPRINTS section + 19 tasks (phases 0-4,
  [XHARD] tags) + SHIM-SWEEP verdict table (24 candidates: 19 CUT, 5 KEEP).
  History: v1 → v2 (findings) → v3 STABLE → v4 (sense-check integration) →
  v5 STABLE → v7 (user-directed sprint split + shim sweep) → v8 STABLE.
- `.oracle/tasklist.md` — FROZEN v3, sprint-structured: 13 batches, 13
  checkpoints.
- `.oracle/inputs/openrouter-sensecheck.md` — external Claude Fable 5
  sense-check that drove v4.
- `.oracle/findings/01-16*.txt` — 16 DeepSeek V4 Flash exploration findings.
- `.oracle-threejs-archive/` — previous megado run (three.js), preserved.

## The two sprints (~1 delegated-execution week each)

- **Sprint A**: Batch 1 (0.1 kernel+lifecycle lock, Sol), 2 (0.2 `_core`
  legalization, Sol), 3 (0.3 bundled inventory + `builtin` deletion, Flash),
  4 (1.1 skills stream + 1.2 pack-only elements, Sol), 5 (1.3 wheel graph,
  Flash), 10a (3.1 alias eradication, pulled forward, Flash). Exit: releasable
  Astrid with end-state loading, lifecycle, identity, packaging, and public
  names; zero import exemptions.
- **Sprint B**: 6 (2.1 generation + 2.3 RunPod, Sol), 7 (2.2 experiments +
  `depends:`, Sol), 8 (2.4 + 2.5 Reigh, Sol), 9 (2.6 API freeze + CI, Sol),
  10b (3.2 residual compat deletion, Flash), 11 (4.1 + 4.2 layout, Sol),
  12 (4.3-4.5 truth + closure, Flash).
- **Hard gate between sprints:** Batch 5 / task 1.3 — no 2.x task before the
  canonical graph is wheel-proven.

## Key shim cuts (19 of 24, per SHIM-SWEEP)

Arnold sole lifecycle engine (legacy task fallback, `--engine` selector gone);
`core/runtime/in_process.py` exception + both allowlists deleted (loading via
resolver); `astrid serve` → `executors run reigh.serve_local_bridge`;
`astrid worker`/`runpod`/`publish*`/`reigh-data`/`author`/`run`/`--brief`
routes deleted; `builtin` pack + `builtin.agent_probe` deleted (test fixture);
pack `aliases:` field + resolver deleted; Arnold `compat.py` + `shapes.py`
table deleted; legacy runtime manifest shapes, auto-bind shim, rendering
`engine` selector + `legacy_engine.py` deleted; `rendering.legacy_hybrid` →
`rendering.hybrid` direct rename; no deprecation windows anywhere.
KEPT: `astrid scratch`, `_core → astrid` branding, `deprecated`-as-metadata,
rendering support fallback, forks/overrides, editorial/golden/fixtures.

## Functionality preservation

All cuts are route/name/machinery removals with canonical replacements
(`executors run <id>`). Two items interpreted narrowly — review before
execution: (1) `builtin.agent_probe` survives only as a test fixture;
(2) task-only lifecycle verbs with no Arnold meaning are deleted (common ops
are ported to Arnold-backed run state).

## Next action

Say "execute" (or "get it megado") to start Phase 5: run Sprint A Batch 1
(Sol XHARD, delegation mandate), oracle-gate it, proceed batch by batch.
