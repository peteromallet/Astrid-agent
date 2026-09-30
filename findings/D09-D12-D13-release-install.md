# D09/D12/D13 release selection, affected checks, and clean install

Run: `h3-full-merge-megado-plan-20260930`  
Date: 2026-09-30  
Result: **PARTIAL — one exact offline candidate set was selected, affected offline checks passed, and an isolated Worker-free wheel/import/discovery path passed; owner-artifact compatibility and live qualification remain open.**

This receipt closes D09, D12, and D13 as one evidence unit. It does not claim
promotion, merge-to-main, publication, provider/GPU/credential/live success, or
the required pre-live integrated review.

## Authority and selection

The selection used `plan.md`, `tasklist.md`, `implementation-criteria.md`, the
Astra merge-in ruling in
`/Users/peteromalley/Documents/reigh-workspace/Astrid/.otto/runs/h3-full-merge-megado-plan-20260930/findings/oracle-merge-in-disposition-response.md`,
and the D02, D04, D05, D06, D07, D08, D10, and D11 receipts. Astra's ruling is
applied as follows:

- Astrid custody is the `f24c4a014ab45d5fc02d765b87b49f68daef52f5` base with
  coherent `896eb2a8b4949f469e17934a36819013524d6793` integration, followed by
  the already-validated D07/D10/D11 candidate changes.
- The sole Runtime lineage remains the 126-operation `workspace.v1` set with
  owner provenance `a278cd460018976940ae21fc2cad563a29a29649`; no second client
  or Worker authority is introduced.
- The RunPod floor remains `>=0.3.1.dev0,<0.4`; its candidate artifact is not
  treated as remotely fetchable or owner-certified.
- VibeComfy remains workflow/schema authority. The unverified
  `AstridAnon/VibeComfy@a6a0cdb4` switch is excluded.

The selected set below is therefore an **offline candidate set**, not a
portable released dependency set. The two unpushed candidate commits are local
identities only; no consumer may fetch them by assuming a remote ref exists.

## Frozen candidate identities

| Owner | Candidate and tree | Package/artifact identity | Dependency/schema/workflow identity |
| --- | --- | --- | --- |
| RunPod_Lifecycle | commit `15748580e4a5922eb4a856b36d88faa1f67004e1`; tree `ff6da3f8360e7544580ed7512117d3a8ef828680`; branch `otto/h3-full-merge-megado-plan-20260930`; clean | `runpod-lifecycle` `0.3.1.dev0`; wheel SHA-256 `c26ee1323b7be1c1b23d04a1a02a3af58b3429732347a166931cf68b1bb3bbd8` | `pyproject.toml` SHA-256 `6fa78936ba3d3dfc3b5faa3deb4556bfb13a324cd0c94c604d48e629df5ef939`; `uv.lock` SHA-256 `ab1a26f7a3e6f6b75ed58308ae665a7cf2c4f21e85980d069a4e7faa0ccb3f45` |
| VibeComfy | final product commit `46ed4f8aeea144b4bbf1d9293b0f2012f05dddc2`; tree `6ba22cf8a1fb5ec3707dcccf4100ea5f44f83674`; branch `otto/h3-full-merge-megado-plan-20260930`; clean | `vibecomfy` `2.8.0`; wheel SHA-256 `bdee7c0dff0785ba500e4a7a7d1b3450f4ca4511445eeb4d923a389794e9345e`; wheel contains the packaged ready-template corpus (193 files, no `__pycache__`) | `pyproject.toml` SHA-256 `3248b11a98a5f07316668c98302ec8e97bafc2c0b1e7bd4e70daa01763d7608d`; `uv.lock` SHA-256 `1f13c0cb756d819c5e63f3ef0ec7d6a956a78ffc825af7666d8e6a4079f2198c`; `template_index.json` SHA-256 `47c0569f0d65cde7dda439585df2458b99c4814bf391ec8f4a77929b2ac68e8e` |
| Astrid | product source tested at commit `e89886f881eb7e0894047f003d1bb301c158eb8f`; tree `2854fc75475f332a7441b36815c70828f3961802`; parent D10/D11 chain intact; clean before this receipt | `astrid` `0.1.0`; wheel SHA-256 `26fd0e33d5088f6a87358949fcf81afbb45620b91004d61a5fc891b6ca7c294e`; wheel contains both required H3 mask resources | `pyproject.toml` SHA-256 `be0f60c26482b5ce10b7b16768ac93fd9611976d13c17a3ba10e3222cf000d5e`; `requirements/runtime.lock` SHA-256 `979fe4af91b2bd01947f94050e44e7e5818efd707999d3bf71ac46819df4f057`; `requirements/proof.lock` SHA-256 `493fd708a567ff962633226b36709e4d48265230df34aaf8f1e1a1f84b0f06fc` |

The Astrid receipt-only commit is created after this file is added and is
reported below; it is not part of the product wheel bytes. The Vibe repair is
the only D09/D12/D13 source change:

- `vibecomfy/registry/ready.py` uses the installed package's
  `vibecomfy/ready_templates` root when no checkout exists.
- `pyproject.toml` force-includes the existing `ready_templates` corpus in the
  wheel.
- `tests/test_ready_template_helpers.py` adds the focused packaged-install
  regression. The regression passed before the candidate commit.

The Vibe candidate's existing optional RunPod declarations and lock still point
to the older remote revision
`14d12f3c5e100247ffb1360c8fe6ba82aa5c7aa6`; they were not silently rewritten
to the unpushed RunPod candidate. This is a deliberate owner-artifact
limitation, not a claim that the two artifacts are compatible.

### Selected H3 inputs

The selected offline contract is the parent v1 source-free route plus retained
T2 v2 branches. The intended live defaults are recorded exactly as contract
inputs, but their immutable model/node bytes are **not** selected or live
qualified here:

- model default: `minimax_h3_ref2va_pruned_int8_convrot.safetensors`;
- graph: one `MiniMaxH3ReferenceToVideo` sampling graph with Qwen3VL text
  conditioning, H3 video/audio VAEs, turbo LoRA, one sampler, and one muxed MP4
  sink at 1024x576/24 fps;
- workflow identities: `lanpaint_h3_av_generalized/source.json`,
  `workflow.py`, and `workflow.vibe.json`; package masks are
  `mask_full_frame.png` SHA-256
  `bcbc6286dd1723da8b2575971fd80890306ad583a60413fa10112475d61d5c31` and
  `mask_preserve.png` SHA-256
  `051bfbda34c73ac4b83df67bb533090524e543e59b09cf65e65dccd54bb39e05`;
- Runtime schema digest:
  `sha256:62da8b1285ba3586b3b9707d4f7ea9b1bd4c31833b7fffc6cbacd2b2a8442f0f`;
  component manifest:
  `sha256:fcae767eaba85e406658ac3b14f3c3447e11073dffcb5e1256e223bdb84f51f4`.

The model name and node names are not evidence of model-weight, custom-node,
Runtime, or GPU qualification. No live provider/model input was invoked.

## D12 affected checks

All checks were offline/local or mocked. No provider, GPU, credential, Runtime
service, Worker, or external orchestrator was contacted.

| Candidate | Check scope | Result |
| --- | --- | --- |
| RunPod_Lifecycle | Existing allocation, termination, launch, discovery, config, CLI, events, guard, prebuilt, shipping, storage, probe, and live-pod fixture suites | **199 passed, 6 skipped**, exit 0. Prior D04 broader affected receipt remains applicable at 222 passed/6 skipped excluding the unrelated runner failure. |
| VibeComfy | Ready-template helper/template, strict-ready, workflow-bundle, traceability, release-contract, plugin-discovery, packaging, and alias suites | **311 passed, 6 skipped**, exit 0; 38 existing warnings. Focused packaged-install regression: **1 passed**. |
| Astrid | H3 request/generation/compile/continuation/operation/receipt/runtime, D10 invocation, D11 publication/retrieval, host activation/session/qualification/model-root, thumbnail, bridge, and RunPod cleanup fixture suites | **363 passed**, exit 0; 7 existing warnings. |

Additional Vibe gates passed: strict-ready JSON `ok: true` with 64 templates;
template-index check exit 0; strict traceability exit 0 with existing
allowlisted model/source metadata findings; `uv lock --check` exit 0; and
`git diff --check` clean. The warnings are existing deprecation/schema-less
bridge warnings, not new failures.

The parent v1 1/4/9 reference cases, T2 v2 routes, Runtime callers, and
publication/retrieval/cleanup checks are covered by the current Astrid bundles
and the exact D06/D07/D08/D10/D11 receipts. No unchanged all-family row was
recertified.

## D13 isolated install/import/discovery proof

The proof used fresh temporary environment
`/tmp/h3-clean-install.7gEnxZ`, built local wheels only, and ran from `/tmp`
with `PYTHONNOUSERSITE=1` and `PYTHONPATH` unset. The three distributions were
installed into the temporary venv; no checkout was editable or on `sys.path`.

Observed installed origins were all under the temporary venv's
`site-packages`:

- `astrid` `0.1.0` imported successfully;
- `vibecomfy` `2.8.0` imported successfully;
- `runpod-lifecycle` `0.3.1.dev0` imported successfully;
- Astrid discovery returned 25 skills and included `h3_av`;
- Vibe discovery returned 64 packaged ready-template IDs;
- RunPod discovery imported `list_pods` as callable without a provider call;
- `astrid --help`, `vibecomfy --help`, and `runpod-lifecycle --help` each exited
  0;
- no `reigh_worker_orchestrator` module, `CURRENT` override, sibling path, or
  editable distribution was observed.

The first probe exposed the concrete Vibe checkout-only discovery defect. The
candidate-only repair above fixed it; the rebuilt Vibe wheel and the complete
probe then passed. This is a package/import/discovery proof, not an execution
or model/GPU proof. The local wheel install also does not certify the Vibe
optional `runpod-local` extra against the selected unpushed RunPod candidate.

## Gate disposition and limitations

| Criterion | D09/D12/D13 disposition |
| --- | --- |
| C1 | **Evidence reused/pass for this unit:** candidate custody and Astra disposition are exact; all three branches clean after commit; source mains and donors untouched. |
| C2 | **Offline evidence reused/pass for this unit:** D04 allocation/custody/cleanup checks plus the RunPod rerun above. Owner release identity remains open. |
| C3 | **Offline evidence reused/pass for this unit:** parent v1/T2 affected bundles passed; no T2 wholesale import. |
| C4 | **PARTIAL:** sole 126-operation `a278cd46` lineage and hashes are identified, but no immutable Runtime release or exact owner qualification receipt was supplied. |
| C5 | **Offline evidence reused/pass for this unit:** D07/D10/D11 publication/readback/retrieval/cleanup contracts and current rerun passed; no live publication is claimed. |
| C6 | **PARTIAL:** the selected local wheels install/import/discover without Worker/sibling/CURRENT/editable fallback, but compatible hash-bound RunPod/Vibe owner artifacts and Runtime/direct-host release evidence are unavailable. |

Remaining owner return conditions are the exact ones from D02/D04/D05/D08:

1. an immutable Runtime bundle/qualification receipt binding the selected
   generated/schema/private-interface bytes;
2. an immutable compatible RunPod artifact/API receipt for the `0.3.1.dev0`
   candidate behavior;
3. a hash-bound Vibe dependency artifact descended from clean local
   `5ebdc90e` and compatible with the selected RunPod artifact; and
4. the independent direct-host observer/topology and cleanup evidence.

No promotion, merge, push, shared-environment install, Worker retirement,
provider/GPU/live operation, D14 review, D15 witness, or D16 closeout follows
from this receipt.
