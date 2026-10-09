# Unpublished merge work: portable starting point

This is a point-in-time transfer package for unpublished Astrid, Runtime, and Reigh app work and the active integration context needed to resume it. It does not contain the repositories' product changes. The active integration remains locally authoritative; this package does not reset counters, change task states, or claim acceptance.

## Get the exact package

```sh
git clone https://github.com/peteromallet/Astrid-agent.git Astrid
cd Astrid
git fetch origin handover/unpublished-merge-work-20261009
git checkout --detach FETCH_HEAD
git rev-parse HEAD # record the exact package commit received
```

The handoff commit is recorded by the published branch and its URL. Start with the active Astrid and Runtime parent snapshots and the Reigh app references in [published source refs](assets/source-refs.md). Reigh app’s active snapshot is 114 commits behind current main, so reconcile and port selected behavior rather than merging it wholesale. Fetch each source repository/branch, verify the listed SHA, and inspect only the mapped scope. The refs preserve source; they do not establish acceptance.

## Resume the merge

The controlling local project is Megado run `three-effort-post-completion-integration-20261001`. Its execution directory was `.otto/runs/three-effort-post-completion-integration-20261001/` in Astrid; ignored run state was intentionally not copied wholesale. This tracked snapshot preserves the essential authority, roles, task sequence, and acceptance summary: [run.yaml](run.yaml), [merge status](merge-status.md), and [merge goal and criteria](merge-goal.md). These records are the portable merge owner context for bounded source intake; the original live run remains the detailed command/evidence authority when available. The 2026-10-09 recommendation and feature dispositions are in [source triage](source-triage.md).

Receiving mode is delivery through the existing integration run, under its existing user authority and gates. Continue admitted non-Pack work under exact-source, custody, and affected-proof conditions; freeze the candidate; consume the Pack V3 terminal handoff last; extend migrations across the accumulated union; then complete I08/I09, I10, and I11. Pack-coherence still had D10/browser retry evidence and final review/merge outstanding at the audit snapshot. Refresh mutable state from the active run before dispatch. Existing user authority covers pushing when the candidate clears its gates; this handoff itself does not authorize deploying, merging to main, provider operations, or reopening budgets.

## Scope and limits

Included: portable merge authority and current phase, run configuration snapshot, evidence-backed Astrid/Runtime/Reigh app source dispositions, exact source refs, receiving instructions, and this package's publication identity.

Not included: product source changes, `.otto` history, raw logs/receipts, credentials, environment files, caches, outputs, runtime state, pod files, private thread IDs, or local absolute paths. This package has selected Astrid, Runtime, and Reigh app source refs only; it is not a complete multi-repository merge transfer. Reigh Worker/orchestrator, OMP, and other cross-repository acceptance remain governed by the active merge's existing records.

All source snapshots are unvalidated as a merged candidate. The earlier Runtime 173-test pass was against preserved pre-integration source only. No tests ran in the 2026-10-09 source audit. Main was not changed by this handoff publication.
