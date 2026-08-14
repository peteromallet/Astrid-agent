# Astrid packification — megado status

**Status: EXECUTION-READY** — megado Phases 0-4 complete; Phase 5 (execution) not started.

## Aspiration (accepted)

Every discoverable capability and every optional domain is a pack, behind a
closed, named kernel. Only the irreducible host (gateway, session/project
store, task engine, pack system, registries, SDK, skills installer,
structure/doctor, foundation/contracts, timeline store, render/generation
protocols) stays in core.

## Artifacts

- `.oracle/plan.md` — STABLE plan (Sol v3, 19 tasks, phases 0-4, 9 [XHARD]).
  Stability: round 1 revised, round 2 refined task 2.3 (dropped the new
  Protocol abstraction), round 3 declared STABLE.
- `.oracle/findings/01-16*.txt` — 16 DeepSeek V4 Flash exploration findings,
  all with file:line evidence.
- `.oracle/tasklist.md` — FROZEN execution tasklist: 12 batches, 12
  checkpoints, [XHARD] batches marked for GPT-5.6 Sol.
- `.oracle/briefs/` — the 16 exploration briefs.
- `.oracle-threejs-archive/` — previous megado run's artifacts (three.js
  renderer), preserved.

## Key verified facts driving the plan

- Import layering baseline is green (0 violations); moves (generation
  backends, experiments, skills flat-walk, legacy_workspace) are legal with
  zero exemptions; runpod/reigh need no new abstractions but careful
  sequencing (task 2.3 cut the Protocol idea).
- `_core` pack.yaml is runtime-illegal today (id regex + loader id==folder);
  task 0.2 makes it legal with a reserved-id rule (shipped-source only).
- `extensions.generation.backends` hook exists and is wired; no shipped pack
  uses it yet (task 2.1).
- `discord_local`/`seedance_local` are checkout-local personal packs
  (`.git/info/exclude`) — fix is a deterministic capability index
  (gen_capability_index.py), not repo changes (task 0.3).
- ASTRID_PACKS_PATH skills never list today (skills discovery filters
  source_kind) — task 1.1 fixes this.
- Capability-shaped host verbs: publish/publish-youtube/upload-youtube/
  reigh-data (pure aliases), worker + runpod (need executor parity first).

## Batch map (frozen)

| Batch | Name | Phase | Owner |
|---|---|---|---|
| 1 | Legal system-pack kernel | 0 | Sol (XHARD) |
| 2 | Canonical skill discovery stream | 1 | Sol (XHARD) |
| 3 | Pack-only element graph | 1 | Sol (XHARD) |
| 4 | Wheel-complete canonical graph | 1 | Flash |
| 5 | Generation and experiment extraction | 2 | Sol (XHARD) |
| 6 | RunPod extraction and Reigh state inversion | 2 | Sol (XHARD) |
| 7 | Reigh service and worker extraction | 2 | Sol (XHARD) |
| 8 | Extraction closure and CI path rails | 2 | Flash |
| 9 | Retire capability-shaped host routes | 3 | Flash |
| 10 | Canonical private entrypoints | 4 | Sol (XHARD) |
| 11 | Enforced physical pack layout | 4 | Sol (XHARD) |
| 12 | Repository truth and full closure | 4 | Flash |

## Next action

Say "execute" (or "get it megado") to start Phase 5: run batch 1 (Sol XHARD,
with the delegation mandate), oracle-gate it, then proceed batch by batch.
Until then the plan is deliberately not executed.
