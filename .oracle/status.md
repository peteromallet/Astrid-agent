# Astrid packification — megado status

**Status: EXECUTION-READY** — megado Phases 0-4 complete (plan STABLE v5, tasklist frozen v2); Phase 5 (execution) not started.

## Aspiration (accepted)

Every discoverable capability and every optional domain is a pack, behind a
closed, named kernel — with cheap portability seams so the kernel can later be
extracted into a shared framework repo ("Arnold", to be renamed) that other
agent tools consume. "As much as possible is part of the plugin system; the
kernel surface stays as narrow as possible; everything else plugs in on top."

## Artifacts

- `.oracle/plan.md` — STABLE plan (Sol v5, 19 tasks, phases 0-4, [XHARD] tags).
  Revision history: v1 (plan) → v2 (findings) → v3 (STABLE) → v4 (external
  sense-check integration) → v5 (minor refinements) → STABLE again.
- `.oracle/inputs/openrouter-sensecheck.md` — the external Claude Fable 5
  sense-check conversation that drove v4.
- `.oracle/findings/01-16*.txt` — 16 DeepSeek V4 Flash exploration findings.
- `.oracle/tasklist.md` — FROZEN tasklist (regenerated from stable v5): 12
  batches, 12 checkpoints.
- `.oracle-threejs-archive/` — previous megado run (three.js), preserved.

## v4/v5 changes (external sense-check integration — Sol's adjudication)

INTEGRATED (15): `depends:` manifest field + static cross-pack import
validation (2.2, with sequencing: eliminate cross-pack `run.py` imports
before enabling the checker); defined pack-facing kernel API replacing
"stable kernel APIs" (2.6/4.4); `astrid/packs/bundled.yaml` product-owned
inventory replacing the kernel constant (0.3); `_core` provenance seam behind
one function (0.2); skills branding seam `astrid/skills/branding.py` (0.2);
Arnold residency justified with code evidence (0.1); `remotion/`/`themes/`
classified product assets, git-aware tooling classified product-repo tooling
(0.1); `astrid.packs.*` accepted as the product namespace, documented (0.1);
two pre-framework-extraction blockers recorded as debts (0.1); importlib-
string rail generalized (0.1/4.5); extension-hook admission rule (0.1);
per-pack test subtree rail (2.6/4.5); generated-output drift checks (0.3/4.5).

REJECTED (11, reasons in plan.md): `install_tier` dependency rule; version
solver / auto-installer; broad `astrid.kernel` facade; renaming `astrid.packs.*`
now; global CLI-literal abstraction; distribution-origin `_core` trust now;
moving Arnold host shapes now; replacing `in_process.py` now; Reigh extension
hook; quarantining invalid user packs; deprecation window for retired aliases;
mesh_fetch promoted to a standalone executor (cut in v5 — stays private
`blender.render` support).

## Batch map (frozen v2 — owners rebalanced from v1)

| Batch | Name | Phase | Owner |
|---|---|---|---|
| 1 | Lock the kernel contract | 0 | Flash |
| 2 | Legalize the `_core` system pack | 0 | Sol (XHARD) |
| 3 | Establish the bundled-product inventory | 0 | Flash |
| 4 | Unify skills and elements on the canonical graph | 1 | Sol (XHARD) |
| 5 | Package and prove the canonical graph | 1 | Flash |
| 6 | Extract generation and RunPod implementations | 2 | Sol (XHARD) |
| 7 | Declare pack dependencies and move experiments | 2 | Flash |
| 8 | Invert and extract the Reigh service domain | 2 | Sol (XHARD) |
| 9 | Close extraction imports and freeze the pack-facing API | 2 | Flash |
| 10 | Retire capability-shaped host aliases | 3 | Flash |
| 11 | Canonicalize and enforce pack layout | 4 | Sol (XHARD) |
| 12 | Repository truth, documentation, and closure | 4 | Flash |

## Next action

Say "execute" (or "get it megado") to start Phase 5: run batch 1 (Flash),
oracle-gate it, then proceed batch by batch (Sol batches carry the delegation
mandate). Until then the plan is deliberately not executed.
