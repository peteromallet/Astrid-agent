# TESTS: tests that tell us the truth (Astrid · Runtime · App)

> The cross-repo testing plan for Astrid, the workspace Runtime and the App (astrid-app). It lives here because Astrid hosts the journeys (`astrid dev journey`, see `journeys.md`).

A standalone plan in Megado format (cards with route, size, dependencies and "Done when"). It is not run inside Otto.

**Revision 3, 15 Oct 2026.** It is built from:
- an Opus review of both Python suites, with a Haiku inventory;
- a Haiku map of how the repos connect;
- r4's `testing.md`;
- `testing-strategy.md` and `testing-gaps.md` (13 Oct). Their thesis is why the journey layer exists: in agent-written code a unit test mirrors its code, and the bugs live between slices, out in the world, and in ports.

It was then revised after four independent reviews, kept with the owner's working notes rather than in this repo. The claims that drove each change were checked against the code.

| Review | Question |
|---|---|
| Astra | Sense check of revision 1 |
| Opus | Does the plan reach its end state? |
| Opus | Cross-product interaction gaps |
| Astra | Cross-product interaction gaps |

**The owner's yardstick (15 Oct):**
- No arbitrary time constraints. Durations are recorded and never judged. Only a genuine hang fails.
- What counts: tests that are robust and thorough, assert meaningful behaviour, and do it as elegantly as possible.

## End state (what "done" means)

When this plan is finished, these statements are true. Each one is checkable.

1. **One honest answer per repo.**
   - A plain default run is green in CI.
   - Red means something is broken; there is no "normal red".
   - At most 10 quarantines per repo, each owned and expiring.
   - Every test runs somewhere with a recorded result. The union of the lanes equals the collected suite.
2. **No copy between the products can drift unnoticed.** Each copy is checked by **artifact digest** against a producer-owned manifest, so a one-byte change fails the consumer. There are three producers:
   - **Runtime**: clients, schemas, fixtures, and real-daemon response fixtures.
   - **Astrid**: its CLI command surface, its elements catalog, and the `astrid-browser` source the App vendors.
   - **The timeline packages**: schema and composition, including transitive copies.

   Every journey records the exact revision set it tested:
   - the Astrid, Runtime and App heads;
   - the manifest versions;
   - the vendored commits.
3. **The human-and-agent workflow is proven for real**, on a clone of the real film:
   - **`smoke`** (Astrid + Runtime) proves:
     - host start;
     - a render admitted the way the App admits it;
     - host replacement after a code change;
     - a crash mid-render, with orphans reaped and the killed task ending in a defined state;
     - a Runtime restart under a live host.
   - **`full`** (all three, through the topology beta users run: the App on its Workspace v1 provider → paired relay → connector → Runtime) proves:
     - an agent publish shows up in the open App;
     - a concurrent human/agent edit loses nothing;
     - the App's own render action renders exactly the accepted revision;
     - the film plays in the App, with seeking and sound;
     - the App survives a Runtime restart without losing its draft.
4. **Every red names its cause:**
   - the layer and the phase;
   - a hang is stopped and named as a hang;
   - a provider failure (Codex or the image model) is kept apart from a product failure.
5. **Each check runs where it belongs, once:**
   - hosted CI runs the local and boundary layers per push;
   - the journeys run locally via `otto-heavy` when the paths that need them change (R-11), and at each release;
   - nothing heavy runs per push.
6. **Less than today:**
   - the ~10 proof and gate mechanisms (including the App's retired `astrid serve` harness) reduce to the journeys;
   - each of their useful assertions has a named new home;
   - fakes are trusted only for what real-daemon fixtures verify;
   - no new framework.
7. **Test runs don't harm the machine.**
   - Every suite reaps the daemons and hosts it started, and fails if it leaves any.
   - `otto-heavy` flags a job that leaves processes or file descriptors behind.

   (The 15 Oct ENFILE crash came from Otto's own lab suites, about 23k leaked file descriptors.)

## Layers

1. **Local behaviour** (per repo, every push). Everything deterministic that needs no second repo, real host, renderer or network, including filesystem, database and component tests.
2. **Boundary compatibility** (every push to a consumer, and whenever a producer's manifest changes):
   - digests against producer manifests;
   - consumers' parsers and fakes run against **real-daemon response fixtures**;
   - the paired relay's route table is checked against every call the App actually makes.

   This proves fidelity to a pinned producer version, not compatibility with every future push.
3. **Real journeys** (`astrid dev journey --tier smoke|full`). `full` includes `smoke`.

When all three layers are green, the main things work together. When one is red, it says which thing is broken.

## Where we are (evidence, 15 Oct)

| | Astrid | Runtime | App |
|---|---|---|---|
| Tests | ~11.9k (240k lines, 788 files) | ~1.9k (37k lines) | 1,355 test files; never measured |
| Collection errors | 25 files (9,171 lines, 241 tests): 3 on a missing pinned checkout, 2 vibecomfy, 1 env value, the rest removed designs | 1 (i06) | — |
| Red on main | ~200 in the subset we run; never run whole | **60 failed / 1,852 passed** (measured 15 Oct, excluding i06 and the activation join) | `bridge-latency.yml` is red by design |
| CI | `ci.yml` cannot pass: about 40 deleted test files are named in m4/m7/m8; Runtime is pinned at `a278cd46`; the suite runs twice | none | No per-push vitest. The release gate isn't in any CI and drives the retired `astrid serve` at an Astrid pin 1,184 commits old. |
| Tests the real flow? | No; today's render failures were invisible | No | No: `REAL_BRIDGE` uses the retired transport |

**Copies between the products (checked):**
- Astrid's Python client exactly matches Runtime's, but by hand-copied hashes.
- **The App's `generated.ts` has drifted.**
  - `publishParentComposition` accepts `[200, 201]`; the server always returns 200.
  - The App's guard checks only `SCHEMA_DIGEST` and runs in no CI.
- Runtime's `contract/manifest.json`:
  - lists paths, with no digests and no version;
  - omits the visual fixture and the conformance YAML.
- `component-manifest.json`'s fixture digests are placeholders (`aaaa…`).
- Runtime has two differing TS clients: `packages/typescript` and `clients/typescript`.
- The TS "generator" patches its own committed output.
- The conformance YAML says PATCH 1→2, while the actor publishes a composition revision.
- Timeline-schema exists in three versions:
  - Astrid's Python pin (ArtAgents `242f9c43`);
  - **0.0.1**, transitively, in Astrid's Remotion lock;
  - the App's vendored 0.0.2.

  Composition is 0.0.6 versus 0.0.2, and Remotion 4.0.509 versus 4.0.503.
- The App vendors `astrid-browser` from Astrid `06dee36` (25 Sep), and nothing checks it.
- **The App's agent prompt tells the agent to use `timelines retime-clip`, which Astrid removed** (0bd37792).
- Five Astrid test files pin Runtime `afccb430` in a sibling checkout that doesn't exist.

**Live interaction bugs found by the reviews.** These are fixed *before* this plan starts, each with a red→green test, and merged to main on 15 Oct 2026 (Astrid 42eca1d7, Runtime ec18217, App 295670ca4). This plan is testing only. Its cards then keep those behaviours covered: B1 by C2.3 and F2, B2 by C1.2, B3 by C2 fixtures, B4 by U4.6 and F1.2, B5 by F1.3.

| ID | Bug | Where |
|---|---|---|
| B1 | The paired relay's allowlist rejects `…/timelines/{id}/composition-revisions` and `…/inspect` (and `/v1/capabilities` on the Runtime side). Paired-mode saves likely fail. | `astrid-app/config/vite/pairedRoutePolicy.ts` |
| B2 | The ACP prompt names the removed `retime-clip` command. | `astridAcpLauncher.ts` |
| B3 | The App classifies conflicts by regex on the message. The App's fakes send codes the Runtime never sends. | `client.ts:146`, `fakeBridgeRouter.ts:684` |
| B4 | An expired lease is reaped only inside a claim. A crashed render stays `running`, and the App's render poll spins forever. | Runtime `store.py:3368`; App `useRenderState.ts:776` |
| B5 | After a Runtime restart, the pack host keeps a sticky fence and refuses to re-register until an Astrid client opens. App-started renders then never run. | `generic_host.py:6015` |

**Duplication:**
- Fakes: ~20 Runtime fakes in Astrid, and ~9 in the App. The App's main fake imitates the retired Astrid bridge.
- ≥6 host-launch fixture families.
- ≥5 ways to locate the other repo.
- Proof and gate mechanisms:
  - in Astrid: the dev journey; s1/m4/m7/m8; stage1 acceptance; iteration e2e; tests/proofs; evals/timeline; the VLM gate; smoke_*.sh;
  - in Runtime: i06 and the activation join;
  - in the App: `verify-paired-release-e2e` and `REAL_BRIDGE`.

## Rulings

| # | Ruling |
|---|---|
| **R-1** | Three layers only. Nothing else gates a merge or a release. Other harnesses are folded in or deleted, and only after each useful assertion has a named new home. |
| **R-2** | The default run leaves out tests that need a pack host or other long-lived subprocess, Node, ffmpeg or Remotion, native macOS, the network, or a second checkout. They are marked `integration`, `darwin_native` or `live`. They run in a recorded nightly lane on this Mac via `otto-heavy`, so their reds are seen. |
| **R-3** | Each producer (Runtime, Astrid, the timeline packages) owns a versioned manifest of artifact **digests**. Consumers vendor by script and check digests, never hand-copied hashes or version strings. |
| **R-4** | A fake is trusted only for operations whose real-daemon responses (including errors) it reproduces from recorded fixtures. Anything else fails loudly. No universal fake. |
| **R-5** | Runtime never imports Astrid or the App. Cross-product tests live in the consumer and record the producer versions. |
| **R-6** | Red is never normal: `known_failures.txt` (id, owner, expiry) is applied as strict xfail and may only shrink. An import or collection error fails the run. `importorskip` is allowed locally but fails in a required lane. |
| **R-7** | Delete before adding. A test pinned to a removed design is deleted, with a `removed.yaml` line, not fixed. No new framework. |
| **R-8** | Tests assert behaviour and identity, not private attributes or proxies. Examples: "the output is revision R", not "a render finished"; "the killed task ended in state X", not "the next render works". |
| **R-9** | Heavy runs go through `otto-heavy`. Required lanes fail when prerequisites are missing. Every suite reaps what it starts and fails on leftovers. |
| **R-12** | **One pre-push command per repo** (`scripts/check-before-push`, a small script, not a framework). It runs the default run and, when R-11's paths changed, the smoke. A push to main without it is not done. (On 15 Oct a push after a hand-picked test subset broke two claim-loop tests that the default run would have caught.) |
| **R-10** | Every journey phase reports success, failure or hung, with its duration and the machine load. Durations never fail a run. Hang ceilings are many times the usual duration. Provider failures are reported separately and never replaced by a fixture. |
| **R-11** | **When journeys run.** Every merge runs the affected local and boundary checks. The smoke also runs before a merge that touches any of:<ul><li>Astrid `astrid/core/execution/{process_group,custody_broker,task_scope,generic_host*}.py`, `astrid/sdk/{autobootstrap,host_bootstrap,workspace_client}.py`, `astrid/packs/rendering/**` or `remotion/**`;</li><li>Runtime `runtime_protocol/**` or `astrid_runtime/**`.</li></ul>The thin `full` runs before a merge that touches the App's `src/integrations/runtime/**`, `renderRouter.ts`, `useTimelineSave.ts`, `usePollSync.ts`, `config/vite/paired*` or the connector. Both run at each integration point and release, and `full` includes `smoke`. This changes r4's "no journey per merge" rule, using its escape valve: on 15 Oct the render failures on main were exactly what a smoke catches. |

## Cards

Sizes are rough focused days, for ordering only, not deadlines (the reviews expect 18–22 d in total). Routes are normal or xhard. Each card wires its own CI.

### LEAK: test runs clean up after themselves
`U · 1 d · normal · produces U#no-leaks` · **partly done 15 Oct**
**Done when:**
1. ✅ Astrid and Runtime suites own and reap every daemon and host process group, and fail on leftovers. The guard signals a group only while it is still ours: Astrid 390e6679 + a6e7bd40 on the candidate; Runtime 66751d7 + dd57fe2.
2. A session-end check compares open file descriptors and rooted processes with the start, in both suites.
(Flagging leftovers in `otto-heavy` itself is Otto tooling, outside this plan.)

### CI0: a CI that can pass (Astrid), plus the pre-push command
`CI · 0.5 d · normal · produces CI#unfrozen` · first
**Done when:**
- The frozen m4/m7/m8 jobs and the Runtime pin `a278cd46` are gone, along with the duplicate run of the suite.
- One unit job runs the default run.
- GREEN's documentation and manifest checks (`make doc-refs`) are a step in it.
- R-12's `scripts/check-before-push` exists in Astrid. Runtime and the App get theirs with U4 and U5.

### U1: honest default run (Astrid)
`U · 0.5 d · normal · produces U#honest-astrid`
**Done when:**
1. Collection-time side effects move into fixtures, or out with `collect_ignore`. Markers handle the rest.
2. One `runtime_checkout` fixture replaces the five locators and the `afccb430` / `70e269bc` pins.
3. The union of the default, nightly and journey lanes equals the collected suite, checked by a test.
4. The nightly lane runs via `otto-heavy` and its result is recorded.
5. One full run records the true red list.

### U2: delete what tests deleted designs (Astrid)
`U · 1 d · normal · produces U#stale-astrid`
**Done when:**
- Each of the 25 collection-error files is dispositioned: deleted (with a `removed.yaml` line), or repaired. The candidate repairs are the 12 stale `packs.*.executors` imports; checkpoint commit a6f08be4 has repairs.
- The ~24 "retired" skips and the exact duplicates are gone.
- Collection has zero errors.
- No test listed by r4 GREEN item 6 as "never obsolete" is deleted.

### U3: green with an owned quarantine (Astrid)
`U · 3 d · normal · produces U#green-astrid`
**Done when:** every failure is handled in one of these ways:
- its environment is fixed;
- it is moved out of the default run;
- it is genuinely fixed;
- it is quarantined (at most 10).

Also: `xfail_strict` is on, no test is weakened without a reason in its PR, and CI0's job is green.

### U4: Runtime honest and green
`U · 2 d · normal · produces U#green-runtime` · parallel lane
**Done when:**
1. i06 and the Astrid activation join are deleted; F1 covers what they checked.
2. The **60 current reds** are each dispositioned.
3. Tests stop rewriting committed files: `test_generated_client.py:707`, and whatever regenerates `astrid_runtime.egg-info/SOURCES.txt`.
4. A generous per-test hang guard is set (hangs only).
5. Runtime's first workflow runs the default run, plus `test_second_client_e2e.py` as a boundary job with an npm cache.
6. A test keeps B4 fixed: an expired lease with no new claim ends in its defined state.

### U5: App honest and green
`U · 1.5 d · normal · produces U#green-app` · parallel lane
**Done when:**
- vitest is measured and gets a per-push workflow.
- The default run needs no Astrid checkout. Today the vitest config fails to load without `ASTRID_CHECKOUT`, because the vendored `astrid-browser` lacks `tools/catalog.json` and the `video_editing` pack.
- The load-dependent flakes are fixed or quarantined: the terminal-output test in `astridAcpBridge.test.ts`; `PublicAstridSite.lifecycle` "retains Home state and scroll across repeated round trips" (red in the full suite, 13/13 alone); and `AgentChat.projectContract` `beforeEach` under full-suite load.
- `shotCompositionRuntime.test.ts` (17 red on main: its handshake fixture no longer matches the product connector role) is fixed.
- `astridSceneAuthoring.test.ts` re-runs Astrid's own `tests/packs/rendering/test_live_scene_authoring.test` through a cross-repo import. It moves to the checkout lane, or Astrid runs it itself (R-5). The same applies to `localExtensions.catalog.test.ts`, and to `portableVisualSeamIntent.test.ts`, which reads a fixture from a sibling Runtime path.
- Stub-bridge and real-bridge tests are separated.
- `bridge-latency.yml`'s expected red is quarantined or deleted.
- The `known_failures` rule applies.

### C1: three producers, checked by digest
`C · 2.5 d · normal · produces C#manifest`
**Done when:**
1. **Runtime:**
   - `contract/manifest.json` has a version and digests for the openapi, the schemas, **the one named canonical TS client**, the Python client, the visual fixture and the conformance YAML.
   - The placeholder digests in `component-manifest.json` are replaced.
   - The conformance YAML's PATCH step matches what the actor does.
   - The committed TS client is digest-pinned. A generator rewrite is out of scope.
   - Runtime CI runs `generate.py --check`.
   - Shared with r4 CONTRACT-COMMIT item 5, and owned here.
2. **Astrid** publishes a manifest by script (with `--check`) of:
   - its CLI command and flag surface;
   - the editor/elements catalog;
   - the `astrid-browser` sources the App vendors.

   An App test asserts every `astrid-tools …` command in the ACP prompt exists in it. That catches B2.
3. **Timeline packages:** Astrid and the App check the effective schema and composition artifacts by digest, including the transitive 0.0.1 in Astrid's Remotion lock. Coordinated with r4 PINS: PINS aligns the versions, and C1 proves the artifacts are identical.
4. Astrid and the App vendor by one script each and record the manifest versions. The hand-copied hashes are deleted.
5. A planted one-byte change fails each consumer. (The App's existing `publishParentComposition` drift is fixed beforehand as bug B6.)

### F1: smoke, the real Astrid + Runtime contract
`F · 2 d · normal · produces F#smoke`
**Already in `astrid dev journey`:** host start, renders, outdated-host replacement, survives a heavy job, PATH reuse, `kill -9` idle and mid-render with orphan reap. Done 15 Oct:
- a Node preflight;
- durations recorded, never judged;
- generous hang ceilings;
- failures show stdout and stderr;
- walks tolerate scratch removal.

**Done when:**
1. It runs from a disposable worktree of HEAD (with `remotion/node_modules` linked), never editing the shared checkout.
2. **The killed task is read back from the Runtime:**
   - it carries `task.lease_expired`;
   - it ends in one defined state;
   - a stale attempt's publication is rejected.
3. **New phase `runtime_restart`:**
   - restart the Runtime under an idle host;
   - then admit an excerpt render **through `POST /v1/tasks` in the App's request shape**, with no Astrid client open;
   - it completes. That catches B5.
4. The summary records the revision set:
   - the Astrid HEAD;
   - the imported `runtime_protocol` checkout and its HEAD;
   - the App HEAD;
   - the manifest versions.
5. Preflight checks the reference clone and otto-heavy. It also checks free disk against the Runtime's free-space floor (4 GiB) plus the expected render scratch, and refuses to start without that headroom. On 15 Oct three gate runs went red on disk alone.
5b. Journeys run alone: `otto-heavy` gives the journey the pool, and nothing else heavy runs beside it. The renders that test crash custody are review size.
6. Unit fixtures prove each outcome kind (success, failure, hung), as in r4 GATE item 3.
7. The journey takes GATE item 5's scenario registry, so r4 cards can add scenarios.

### F2: full, all three products through the shipping topology (thin first)
`F · 4 d · xhard: the only test across all three, and it needs product surfaces confirmed first · produces F#full`
**Start from:**
- the I09 cross-client handoff test (`paired-repository.spec.ts:1277-1364`). Commit its `cli_helper` and context producer into Astrid, and move the shared helpers into a support module.
- **not** the paired-release phases or `REAL_BRIDGE`: they drive the retired `astrid serve`.

**Topology:** the App on its Workspace v1 RuntimeDataProvider, through the paired relay and connector, attached to the journey's Runtime and realm. The test asserts it reached that realm.

**Done when**, in order:
1. **Agent → open App.** Astrid publishes a revision while the App is open, and the App shows it without a reload. One real task-activity event for that work is visible in the App.
2. **Concurrent edit.**
   - Start an App edit and hold its save acknowledgement.
   - Publish an agent edit from the CLI.
   - Make another App edit, then release and retry.
   - Assertions:
     - both edits survive, or a named conflict keeps the draft intact;
     - the retried save produces exactly one publication;
     - opaque fields and explicit clears are preserved;
     - both clients agree after a reload.

   This carries r4 IP3/IP4.
3. **Render that revision, adversarially.**
   - Render accepted revision R through the App's render action (`renderRouter.enqueueAstridRuntimeRenderTimeline`; required).
   - Publish R+1 before it executes.
   - Assert the task's `timeline_authority.parent_revision_id` equals R, and that a frame or audio marker distinguishing R from R+1 is R's.
4. **Plays in the App:**
   - a seek through the relay returns 206 with `Content-Range`;
   - frames decode near the start, middle and end;
   - the decoded audio isn't silent.

   *First confirm where the App plays a timeline render. Today rendered output shows only in agent chat and as a download. If there is no such surface, that is a product card before this step.* Also confirm that Playwright's Chromium decodes H.264/AAC.
5. **Runtime restart with the App open:**
   - the App shows its relaunch notice;
   - the draft survives;
   - it saves after the relaunch.
6. **Full tier only:**
   - the Codex image → sprite step comes before step 1, as a separately reported product check;
   - the thin slice uses existing film media.

Phase outcomes follow R-10.
**Depends on:** F#smoke, C#manifest, C#fake, and B1 fixed.

### C2: fakes verified by real-daemon fixtures, and the relay route table
`C · 2 d · xhard: every unit test that uses a fake trusts it · produces C#fake`
**Done when:**
1. The existing real-daemon actor records response fixtures, digested in Runtime's manifest:
   - the publish 409 body;
   - task admission;
   - task read;
   - managed outputs;
   - an events page that crosses a page boundary.
2. Each of these accepts exactly those fixtures:
   - the App's zod parsers (`src/integrations/astrid/*Routes.ts`);
   - the App's `isRuntimeConflict`;
   - the Python fake.

   Unsupported operations fail loudly.
3. **The paired relay's route table** is checked table-by-table against every method, path and header the App's providers call (positive), and the forbidden routes are kept (negative). That catches B1.
4. Other fakes move over when their file is next touched.

### U6: custody behaviour tests, with PROCLIFE
`U · 1 d · xhard · produces U#custody`
**Done when:**
- The behaviour tests become PROCLIFE's acceptance tests:
  - a reused pid is never signalled;
  - no process is signalled twice;
  - busy means a task in flight;
  - the crash journal is reaped on start.
- When PROCLIFE retires the custody broker, its ~23k lines of tests go with it. That's the plan's one large reduction.

### TIDY: fold or delete the other mechanisms, assertion by assertion
`T · 1 d · normal · produces T#one-gate`
**Done when:** each item below has every useful assertion mapped to its new home (a line per assertion), and is then deleted:
- Astrid: stage1 cross-repo acceptance and `stage1_acceptance.py`; `iteration_video_runtime_e2e`; `evals/timeline`; `smoke_*.sh`; `tests/proofs`; the VLM gate; the s1/m4/m7/m8 scripts, Makefile targets and `artifacts/`; `run_ci_checks.sh`; the cold-launch matrix (decided);
- the App's `real-bridge-serve.mjs`, the `astrid serve` path in `verify-paired-release-e2e.mjs`, and the unpinned `bridge-latency.yml`.

## Relation to r4

- **Replaced:** GREEN's suite-greening (by U1–U5 and CI0) and GATE's single-film gate (by F1 and F2).
- **Kept:**
  - IP1 (preservation);
  - IP3 (crash between the head move and the response), carried into F2.2;
  - IP4 (App-save rebase), carried into F2.2;
  - IP5 (preview = render parity, so "plays in the App" is also "matches the preview");
  - IP6 (SEC-1…5, reconnect, Range seek);
  - REPLAYS.
- **Owned here:** the journey takes GATE item 5's scenario registry. CI0 owns GREEN's doc and manifest checks.
- **Pins:** PINS-ASTRID and PINS-APP still align the renderer closure; C1.3 proves it by digest.
- **To amend in r4's text:** `testing.md:25,29,32` and GATE items 1–3 (no journey per merge, "≤5 min", "not observed (deadline)"), so the two plans don't contradict each other.

## Order and checkpoints

**Week 1**
1. LEAK (finish), CI0, then U1 → U2 → U3.
2. In parallel: U4 and U5.
3. Then C1 and F1.

**Checkpoint A:**
- the three default runs are green in CI;
- a planted one-byte drift fails in each consumer;
- the smoke, with `runtime_restart` and the read-back of the killed task, is the R-11 merge check.

**Week 2–3**
1. C2.
2. Then F2's thin slice (steps 1–5 on existing media).
3. Then F2 in full.
4. Then U6 (with PROCLIFE) and TIDY.

**Checkpoint B:** `journey --tier full` passes on main, and **the owner watches the rendered film play in the App** after a concurrent edit.

## What not to do

- Don't build another gate, proof framework or universal fake.
- Don't fix all ~200 failures before marking and quarantining them.
- Don't run renders or journeys per push.
- Don't let Runtime import Astrid.
- Don't paper over flakes with longer sleeps.
- Don't treat a duration as a failure.
- Don't delete custody coverage before PROCLIFE replaces what it guards.
- Don't claim a digest proves behaviour: fixtures and journeys do that.
- Don't repeat full renders for each interaction case. One short render plus controlled interruptions is enough.
