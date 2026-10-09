# Receiving-agent message

You are receiving the unpublished merge-work handoff for Astrid, Runtime, and Reigh app. Start at [START-HERE](../START-HERE.md) on branch `handover/unpublished-merge-work-20261009`; after fetching, record the exact commit with `git rev-parse HEAD`.

## Authority and current state

The authoritative project is the existing active Megado run `three-effort-post-completion-integration-20261001`; its source is the Astrid run captured in `run.yaml` and `merge-status.md`. The run remains active, and this package is a point-in-time transfer snapshot. Current phase is continued admitted non-Pack work before candidate freeze. Sequence: freeze the candidate → terminal Pack V3 handoff last → accumulated migration → I08/I09 → I10 → I11. At snapshot time, Pack-coherence still had D10/browser retry evidence and final review/merge pending. Prerequisites for each slice remain exact accepted source, stable identity, dependency and custody checks, and affected proof; Pack requires its terminal handoff before the final wave. Re-read current status before dispatch. Do not reset budgets, role bindings, counters, or acceptance states. The single `integrated_final` review allows at most 7 rounds and stops on an uncontested pass; later rounds verify actionable corrections only. No extra review stage is authorized.

Receiving mode: delivery through the existing integration run, continuing only after its prerequisites pass. The Megado-capable receiving runtime must support the declared role routing; runtime capability and model availability have not been verified on the receiving machine. Required models are the exact bindings below; report any unavailable model before dispatch and do not substitute silently.

Objective: preserve and integrate useful unpublished work from the six-effort merge and the source refs mapped in `assets/source-refs.md`. Boundaries: no deployment, production/provider operation, unapproved render/retry, live-data migration, deletion, cleanup, or premature main push. The user already authorized a main push after existing technical gates pass; this handoff does not alter those gates. This package covers selected Astrid/Runtime/Reigh app source preservation and merge context only; Reigh Worker/orchestrator, OMP, and other donor dependencies remain in the active merge.

## Exact source and skills identities

- Astrid package source: branch `handover/unpublished-merge-work-20261009`, based on fetched `origin/main` SHA `219850f96f32ac899482dbd3a1a99d06367aa7e1`.
- Astrid, Runtime, and Reigh app source refs and exact SHAs: see `assets/source-refs.md`; verify each before inspection. These are source-preservation refs, not accepted merge candidates. Reigh app’s active integration snapshot is 114 commits behind main; port/reconcile its selected behavior against current main, never merge the snapshot wholesale.
- Megado skill source inspected: `poms-skills` commit `f1296034386486d65cf19876d5e5ebfb0547e7cf`, file `megado-handover/SKILL.md`. Canonical Megado skill was also read from that same checkout; verify its commit through the referenced skills source if needed.
- Model bindings remain the ones in `run.yaml`: coordinator/reviewer normal GPT-6 Luna, normal worker GPT-6 Luna xhigh, XHard GPT-6.1 Sol, Astra oracle GPT-6 Astra, final reviewer GPT-6 Astra. Confirm these models are available in the receiving runtime before dispatch; do not silently substitute.

## Start commands

```sh
git clone https://github.com/peteromallet/Astrid-agent.git Astrid
cd Astrid
git fetch origin handover/unpublished-merge-work-20261009
git checkout --detach FETCH_HEAD
git rev-parse HEAD
```

The package contains a concise current queue, acceptance criteria summary and evidence-backed source dispositions; detailed task status/history and command receipts are not included. Use the active run’s exact refs from the source handoffs when accessible. The portable criteria in `merge-goal.md` are the minimum basis for admitting and validating the selected source slices. All source snapshots are unvalidated as a merged candidate. No tests ran in the 2026-10-09 source audit; the earlier Runtime 173-test result applies only to preserved pre-integration source.

Finish instruction: resume the existing integration authority at its next currently admissible task, refresh mutable facts, and preserve Pack-last and all existing gates. Do not create a new implementation branch or PR from this handoff; any later publication follows the active run and user's existing authority.
