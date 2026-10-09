# Published source references

These preservation refs make selected source recoverable; they are comparison inputs, not merge acceptance or validation. Candidates below are deliberately limited to source useful to the current integration. Older refs remain archival and are not recommended merge units.

## Astrid

Remote: https://github.com/peteromallet/Astrid-agent.git. The publisher verified the refs with `git ls-remote`. The active six-effort parent and Pack-coherence child are the current convergence records; the root source snapshot and stash preserve unique dirty source. The dirty root snapshot faithfully includes a Remotion deletion; triage explicitly says to restore/retain the tracked Remotion project for delivery.

### Current integration and useful donor branch tips

| Source branch | Published branch | Exact SHA |
| --- | --- | --- |
| `otto/astrid-preferences-and-documents-20261002` | [`preserve/20261009/branches/otto/astrid-preferences-and-documents-20261002`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/otto/astrid-preferences-and-documents-20261002) | `e8cba0a58f571dd8822e783c82c8ce57ca5f4aca` |
| `otto/h3-final-v2-repair-20261001` | [`preserve/20261009/branches/otto/h3-final-v2-repair-20261001`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/otto/h3-final-v2-repair-20261001) | `c4feae4b963a1fe0db96f885673e5c242535a51e` |
| `otto/hosted-browser-test-20261002` | [`preserve/20261009/branches/otto/hosted-browser-test-20261002`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/otto/hosted-browser-test-20261002) | `3d8e1a3301aa0049a4ecb8a2305714bd58cabf80` |
| `otto/pack-coherence-20261008` | [`preserve/20261009/branches/otto/pack-coherence-20261008`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/otto/pack-coherence-20261008) | `e58ff35727a5abbbd3c61882f28e26e675298427` |
| `otto/three-effort-post-completion-integration-20261001` | [`preserve/20261009/branches/otto/three-effort-post-completion-integration-20261001`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/otto/three-effort-post-completion-integration-20261001) | `32b044360fa853041ef9a34035283ae95b2642c6` |
| `otto/timeline-phase-media-contract-20261006` | [`preserve/20261009/branches/otto/timeline-phase-media-contract-20261006`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/otto/timeline-phase-media-contract-20261006) | `2f5b391b9457c1adee9086dafb29a9676d1ca2f8` |
| `otto/worker-retirement-20261002-astrid-main-candidate` | [`preserve/20261009/branches/otto/worker-retirement-20261002-astrid-main-candidate`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/otto/worker-retirement-20261002-astrid-main-candidate) | `1113123b743241d675a7a177561a42a6ad213c3b` |

### Dirty source snapshots

| Source label | Donor parent SHA | Published branch | Exact snapshot SHA |
| --- | --- | --- | --- |
| `root-current-source` (sanitized tip) | `0a6b3fefd80c39042f6c9afdbe8f995b1a39d0ea` | [`preserve/20261009/snapshots/root-current-source`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/snapshots/root-current-source) | `5539aa101294ce76dfbb441334a465f256422308` |
| `stash-source` | `3471019118f7ae04d74023e02478bfff5ab45b39` | [`preserve/20261009/snapshots/stash-source`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/snapshots/stash-source) | `be930446f24005c2d733c9e6e9c26584e3b43a46` |
| `three-effort-parent-dirty` | `32b044360fa853041ef9a34035283ae95b2642c6` | [`preserve/20261009/snapshots/three-effort-parent-dirty`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/snapshots/three-effort-parent-dirty) | `2109a003678e79b2c8cfdfbc5c1b025024f87be9` |
| `runpod-execution-dirty` | `9be144c4f2f63216e693a1a13e42cd79d3845845` | [`preserve/20261009/snapshots/runpod-execution-dirty`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/snapshots/runpod-execution-dirty) | `e35b333465c36079bb0a170f2792d96a8629eb6b` |
| `runpod-worker-preparation-dirty` | `9be144c4f2f63216e693a1a13e42cd79d3845845` | [`preserve/20261009/snapshots/runpod-worker-preparation-dirty`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/snapshots/runpod-worker-preparation-dirty) | `732c81280029091dbc8e151a7e6b5f235189a00e` |
| `timeline-media-dirty-checkout` | `3471019118f7ae04d74023e02478bfff5ab45b39` | [`preserve/20261009/snapshots/timeline-media-dirty-checkout`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/snapshots/timeline-media-dirty-checkout) | `4e83a968e3af32414154a47a9044c95032a4bca7` |

The active parent dirty snapshot is `three-effort-parent-dirty`; the root dirty source and stash are separate snapshots. RunPod and timeline snapshots retain comparison sources. The root snapshot tip `5539aa1` removes the zero-byte local registry lock and local source-profile file. Its earlier ancestor `39b79a3` contained absolute local checkout/environment paths in `astrid-source-profile.json`; do not check out that ancestor. Sanitization updates to the root, stash, and timeline snapshot refs were normal fast-forwards and do not erase earlier objects from public history. No credentials were present in the local profile file; use the listed sanitized tips and avoid the earlier snapshot ancestors. Other excluded outputs, private agent environments, generated/runtime state, caches and credential-bearing files were not included. Eleven older published refs remain archival only; the preservation publisher did not merge them to `main`. The Pack branch ref is source preservation and does not imply its pending terminal evidence/review has passed.


### Archival Astrid refs (not integration candidates)

Eleven older refs were published before the scope narrowed. They remain available as provenance only; their publication did not select them for the active merge.

| Source branch | Published branch | Exact SHA |
| --- | --- | --- |
| `archive/stage1-premature-legacy-deletion` | [`preserve/20261009/branches/archive/stage1-premature-legacy-deletion`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/archive/stage1-premature-legacy-deletion) | `6f284e4428f01c7680abd70b56c78bfa0baff2e4` |
| `codex/project-authority-census-luna` | [`preserve/20261009/branches/codex/project-authority-census-luna`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/codex/project-authority-census-luna) | `6da20df0616ccfff2d1aea5ea3fc3396751e0372` |
| `codex/zero-shim-media-cutover-luna` | [`preserve/20261009/branches/codex/zero-shim-media-cutover-luna`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/codex/zero-shim-media-cutover-luna) | `e7ac1a7dc20aae1deb8d64425c804f14fdd9941c` |
| `codex/zero-shim-reigh-bridge-delete-luna` | [`preserve/20261009/branches/codex/zero-shim-reigh-bridge-delete-luna`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/codex/zero-shim-reigh-bridge-delete-luna) | `d4a0e9d01b9aef999d9d8234c6a0899c5dd007e7` |
| `codex/zero-shim-reigh-bridge-delete-luna-fix` | [`preserve/20261009/branches/codex/zero-shim-reigh-bridge-delete-luna-fix`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/codex/zero-shim-reigh-bridge-delete-luna-fix) | `13094d4becc4da0609776d4b160332173e98ed69` |
| `codex/zero-shim-timeline-cutover-luna` | [`preserve/20261009/branches/codex/zero-shim-timeline-cutover-luna`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/codex/zero-shim-timeline-cutover-luna) | `a076d59b28219f1d6fec531831c9c0f1f20592f8` |
| `integration/final-env-thread-docs-luna` | [`preserve/20261009/branches/integration/final-env-thread-docs-luna`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/integration/final-env-thread-docs-luna) | `d5d0983cda5991215f51763b7b8281f635257c0c` |
| `integration/final-pack-composition-luna` | [`preserve/20261009/branches/integration/final-pack-composition-luna`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/integration/final-pack-composition-luna) | `483ca559ee83a49059f1da3d2729e9c7448ac29a` |
| `integration/final-schema-flatten-docs-luna` | [`preserve/20261009/branches/integration/final-schema-flatten-docs-luna`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/integration/final-schema-flatten-docs-luna) | `f5d992a7a978e0883592605e9b51ebb1199481a1` |
| `integration/final-source-override-luna` | [`preserve/20261009/branches/integration/final-source-override-luna`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/integration/final-source-override-luna) | `a269e2fa4c385ea2e1b0cc7d194c71045752134e` |
| `oracle-packification` | [`preserve/20261009/branches/oracle-packification`](https://github.com/peteromallet/Astrid-agent/tree/preserve/20261009/branches/oracle-packification) | `0c93fd8a661efa3601471c10b1e3304b17dccf61` |

## Runtime

Remote: https://github.com/banodoco/banodoco-workspace-runtime.git. The publisher verified all 19 refs against remote SHAs. The active parent dirty-tree snapshot is `preserve/transfer/20261009/runtime/three-effort-post-completion-integration-20261001` at `62eaccfe04ef67efeeab31a983d4499803f087af`. The parent branch tip is `otto/three-effort-post-completion-integration-20261001` at `de953bcca942eb6a7cd4b87f2b7802c413ae9201`; these are distinct records. The preserved source branch is `preserve/live-code-scenes-20260930-dirty-root-20261003` (`07b6bd5567e98834ee426dda96a3b553bff9c693`), which contains parent-composition revision history/restore absent from audited main. The source stash snapshot is `preserve/transfer/20261009/runtime/stash-pre-imported-media` (`fe61a3a3205cf1e519ec544025c71c2617c303be`). Review rounds 1 and 2 share one identical snapshot (`preserve/transfer/20261009/runtime/final-round-1`, `715da9190a5225bf5361b4c07cbbebef66e7460d`).

### Active parent and distinct recovery source

| Source | Published branch | Exact SHA | Disposition |
| --- | --- | --- | --- |
| Parent integration branch tip | [`otto/three-effort-post-completion-integration-20261001`](https://github.com/banodoco/banodoco-workspace-runtime/tree/otto/three-effort-post-completion-integration-20261001) | `de953bcca942eb6a7cd4b87f2b7802c413ae9201` | Parent-owned candidate lineage; seven commits are not a separate delivery. |
| Parent dirty worktree snapshot | [`preserve/transfer/20261009/runtime/three-effort-post-completion-integration-20261001`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/transfer/20261009/runtime/three-effort-post-completion-integration-20261001) | `62eaccfe04ef67efeeab31a983d4499803f087af` | Captures selected dirty/untracked source and tests for the active candidate. |
| Distinct preserved root source | [`preserve/live-code-scenes-20260930-dirty-root-20261003`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/live-code-scenes-20260930-dirty-root-20261003) | `07b6bd5567e98834ee426dda96a3b553bff9c693` | Inspect/port only the parent-composition history/restore capability if accepted into current scope; drop superseded overlap guard/renderer. |
| Accepted RunPod preparation source | [`preserve/transfer/20261009/runtime/runpod-worker-preparation-20261002`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/transfer/20261009/runtime/runpod-worker-preparation-20261002) | `299b1afe8d2ad2d4ec2d7254acb8af1150e32bc1` | Compare/admit through parent after exact scope/dependency freeze; no provider operation implied. |

### Additional retained Runtime refs

| Published branch | Exact SHA |
| --- | --- |
| [`h3-runtime-promotion-20260929`](https://github.com/banodoco/banodoco-workspace-runtime/tree/h3-runtime-promotion-20260929) | `53bcd6144f71d093d5b407b1cbe26ea10173137d` |
| [`otto/astrid-preferences-and-documents-20261002`](https://github.com/banodoco/banodoco-workspace-runtime/tree/otto/astrid-preferences-and-documents-20261002) | `a36040796fc2decfdd57d50d94f68c73e04f31ad` |
| [`otto/h3-b3-local-witness-20260930`](https://github.com/banodoco/banodoco-workspace-runtime/tree/otto/h3-b3-local-witness-20260930) | `6de12bce5f403cc27006e26e299696357bb09cd1` |
| [`otto/hosted-browser-test-20261002`](https://github.com/banodoco/banodoco-workspace-runtime/tree/otto/hosted-browser-test-20261002) | `1bc21799c7a7b0c16bc044fa53a8dd0508be52e0` |
| [`otto/worker-retirement-20261002-runtime-main-candidate`](https://github.com/banodoco/banodoco-workspace-runtime/tree/otto/worker-retirement-20261002-runtime-main-candidate) | `0580609ea08dfa1f0c95dea352af7fa630b5923d` |
| [`preserve/transfer/20261009/runtime/banodoco-workspace-runtime`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/transfer/20261009/runtime/banodoco-workspace-runtime) | `111f96c06103a133b9f761e168138a302fa89b21` |
| [`preserve/transfer/20261009/runtime/final-round-1`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/transfer/20261009/runtime/final-round-1) | `715da9190a5225bf5361b4c07cbbebef66e7460d` |
| [`preserve/transfer/20261009/runtime/astrid-dirty-main-candidate-c-20260925`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/transfer/20261009/runtime/astrid-dirty-main-candidate-c-20260925) | `f4a65d87e8b00b0e4a5b331a8f54098cfc65b899` |
| [`preserve/transfer/20261009/runtime/astrid-dirty-main-integration-publish-20260924`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/transfer/20261009/runtime/astrid-dirty-main-integration-publish-20260924) | `05f06144e5bce1e1df82605a8796dbfd7c7f7d35` |
| [`preserve/transfer/20261009/runtime/astrid-plan-a-execution-worker-20260923`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/transfer/20261009/runtime/astrid-plan-a-execution-worker-20260923) | `ec0e81d13fb01f4b1c1cbba2a789eb327e589ea6` |
| [`preserve/transfer/20261009/runtime/astrid-plan-a-followup-20260925`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/transfer/20261009/runtime/astrid-plan-a-followup-20260925) | `2f90e8996519e009a07d1bbcb1f51322c4bf9ffd` |
| [`preserve/transfer/20261009/runtime/astrid-plan-a-setup-lifecycle-20260923`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/transfer/20261009/runtime/astrid-plan-a-setup-lifecycle-20260923) | `3b8f3b1e56753236f61b3e0e8abe0948d27e11d4` |
| [`preserve/transfer/20261009/runtime/imported-media-catalog-runtime-20260923`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/transfer/20261009/runtime/imported-media-catalog-runtime-20260923) | `2593df66cdd7503c5145771b13d39e73a1100289` |
| [`preserve/transfer/20261009/runtime/runpod-execution-simplification-20261002`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/transfer/20261009/runtime/runpod-execution-simplification-20261002) | `5165efbcd768d49ead90ec926698e2038ac78ab2` |
| [`preserve/transfer/20261009/runtime/stash-pre-imported-media`](https://github.com/banodoco/banodoco-workspace-runtime/tree/preserve/transfer/20261009/runtime/stash-pre-imported-media) | `fe61a3a3205cf1e519ec544025c71c2617c303be` |

These additional refs are preserved history/source comparisons, not merge recommendations. In particular, preferences/thumbnails and worker/hosted/H3 changes may be duplicates or superseded on the active candidate. Runtime source preservation excluded build/egg-info outputs, `.otto` run contents, logs, media, caches, OS files, and runtime state. Its secret scan found two generic-key pattern matches in test fixture calls and no credential assignment/file; no product tests or integration ran.

## Fetch commands

These commands use fresh destinations and do not overwrite an existing worktree:

```sh
git clone https://github.com/peteromallet/Astrid-agent.git Astrid
cd Astrid
git fetch origin refs/heads/preserve/20261009/snapshots/three-effort-parent-dirty:refs/remotes/origin/preserve/20261009/snapshots/three-effort-parent-dirty
git cat-file -e 2109a003678e79b2c8cfdfbc5c1b025024f87be9^{commit}
git show --stat --oneline 2109a003678e79b2c8cfdfbc5c1b025024f87be9
cd ..
git clone https://github.com/banodoco/banodoco-workspace-runtime.git Runtime
cd Runtime
git fetch origin refs/heads/preserve/transfer/20261009/runtime/three-effort-post-completion-integration-20261001:refs/remotes/origin/preserve/transfer/20261009/runtime/three-effort-post-completion-integration-20261001
git cat-file -e 62eaccfe04ef67efeeab31a983d4499803f087af^{commit}
git show --stat --oneline 62eaccfe04ef67efeeab31a983d4499803f087af
cd ..
git clone https://github.com/banodoco/reigh-app.git ReighApp
cd ReighApp
git fetch origin refs/heads/preserve/transfer/20261009/reigh/three-effort-post-completion-integration:refs/remotes/origin/preserve/transfer/20261009/reigh/three-effort-post-completion-integration
git cat-file -e d5d6ac31ae19170f7d1aa891b8934032f19d93c0^{commit}
git show --stat --oneline d5d6ac31ae19170f7d1aa891b8934032f19d93c0
```

For any other entry, substitute its branch and exact SHA. Use `git worktree add --detach <new-empty-path> <SHA>` to inspect separately. Do not reset or checkout over work in progress.

## Reigh app

Remote: https://github.com/banodoco/reigh-app.git. The publisher verified all three refs with `git ls-remote`; a build-context hook passed. No tests were run and the snapshots are not validated candidates.

| Source | Published branch | Exact SHA | Disposition |
| --- | --- | --- | --- |
| Active integration dirty snapshot | [`preserve/transfer/20261009/reigh/three-effort-post-completion-integration`](https://github.com/banodoco/reigh-app/tree/preserve/transfer/20261009/reigh/three-effort-post-completion-integration) | `d5d6ac31ae19170f7d1aa891b8934032f19d93c0` | Preserve and reconcile against current main; based on older main and 114 commits behind at audit. |
| Audio preview recovery | [`preserve/transfer/20261009/reigh/audio-preview-recovery`](https://github.com/banodoco/reigh-app/tree/preserve/transfer/20261009/reigh/audio-preview-recovery) | `141bad53989dc3ec363e288743f0112404c76888` | Focused candidate based on current main; source only until affected proof passes. |
| Timeline render contract | [`preserve/transfer/20261009/reigh/timeline-render-contract`](https://github.com/banodoco/reigh-app/tree/preserve/transfer/20261009/reigh/timeline-render-contract) | `3bd1d019715de79cd54aa208cf26de10427c29d5` | Existing clean 3-commit delivery based on current main; preserve source and validate on integration. |

The active Reigh snapshot includes ACP project chat/local gateway, Runtime asset registration/save certainty, scoped configs, and affected tests. Older Pack/V3, timeline-phase and adapter-successor branches were excluded as stale, duplicate, or superseded. The publisher excluded nested old worktree content, local evidence/junk, build dependencies, test results, and `.env` files.
