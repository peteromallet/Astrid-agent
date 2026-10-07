# Dataset Build

`training.dataset_build` builds a reviewed video training dataset from configured
sources. It is file-backed by design: every expensive or human-facing boundary
writes a checkpoint in the run directory so interrupted runs can resume without
discarding completed work.

## Run

```python
import astrid.sdk as sdk
from astrid.packs.training.actions.dataset_build.config import load_dataset_config, preflight_delegation_budget

config_path = "<config.json-or-yaml>"
config = load_dataset_config(config_path).data
limits = preflight_delegation_budget(config)["limits"]  # all six finite limits
# Supply current registered definition digests for every enabled child action.
child_digests = {
    "youtube.youtube_audio": "<registered-definition-digest>",
    "editorial.scenes": "<registered-definition-digest>",
    # Include enabled understanding and Human Review actions when configured.
}
policy = {
    "capabilities": [{"capability_id": name, "capability_digest": digest}
                     for name, digest in child_digests.items()],
    "targets": [{"kind": "default"}],
    "input_object_ids": [],  # exact admitted original objects, if any
    "limits": limits,
}
result = sdk.invoke(
    "training.dataset_build", kind="action", project="demo",
    inputs={"config": config_path, "out": "<run-output-directory>"},
    child_delegation=policy, client=client,  # explicit connected public SDK client
    wait=False,
)
```

Optional inputs:

- `--review-decisions <path>` applies non-interactive review decisions and then
  finalizes manifests.
- `--dry-run` validates config, budgets, and required secrets, prints the
  planned filters/budgets/fixture mode, and exits without creating the run
  directory or any artifacts.
- `--skip-review` runs acquisition, deterministic/model-backed filters,
  captioning, and review-data checkpointing, then leaves state at `reviewing`.
  It does not launch `editorial.human_review`, does not write final manifests,
  and does not auto-accept pending items.
- `--review-only` requires matching `review_state.json` and existing
  `review_data.json`, then runs only review/finalization from those checkpoints.
  It does not reacquire sources, rerun filters, or caption.

Config-level `review.enabled: false` is separate compatibility behavior. It
bypasses the browser and accepts all captioned active items non-interactively.
It is not the same as `--skip-review`.

## Child-work preflight

Live child work requires an explicit positive `budgets.delegation.max_derived_bytes`
(at most 4 GiB). Optional finite limits are `max_children` (4,096),
`max_active_children` (64), `max_derived_objects` (1,024), `max_child_inputs`
(256), and `max_child_bytes` (256 MiB); the parentheses give hard ceilings.
Local defaults are one active child, 256 objects/inputs, 256 MiB per child,
and the calculated child count. An explicit child count must cover the plan.

The calculation includes every configured YouTube URL and search query
(including bucket queries), both acquisition and scene work for each possible
initial/refill round, the shared API-call allowance across all rounds, and each
possible interactive review. Rejected sources still count. The current callers
have no outer retry loop; replaying one logical child is one admission.
Dry-run reports this calculation and rejects known child-count excess before
acquisition. Fixture mode performs no child-budget admission.

The public Python SDK parent admission must carry the matching complete grant:
registered capability digests, allowed targets, admitted original object IDs,
and all six resolved limits. Config preflight is local checking; the inherited
bridge enforces the persisted Runtime grant. CLI/Reigh grant parity is separate.

YouTube acquisition and Scenes use public child actions. The run-scoped meter
survives provider recreation and refill rounds. It charges exact output
associations separately for host retention, and compatible object ID/digest
unions separately before derived-input registration. Zero-byte objects count;
completed and uncertain charges are retained. Unknown sizes are checked from
verified receipts before materialization, with a separate 64 MiB per-object cap.
The unchanged public `producer_file()` descriptor carries downloaded media to
Scenes. Failed, missing, or corrupt managed output fails closed; a successful
empty scene list retains the full-video fallback. Local ffmpeg clipping is
unchanged. Later callers must meter transformed clips before child submission.
This bounded slice does not close captioning, the other Training child callers,
or Human Review production composition.

## Runtime Phases

The runtime phases are ordered as follows:

1. Parse and normalize config, including legacy filter blocks and ordered
   `filters.stages`.
2. Load existing `review_state.json` if present, or create a new one.
3. Acquire candidates and write `candidates.json`.
4. Convert candidates into review items.
5. Run deterministic filters and write `work_preview.json` before model-backed
   filters or captioning.
6. Run model-backed filters, currently `bucket_judge_filter`, and write
   `filtered_items.json`.
7. Caption active items and write caption sidecars under the run directory.
8. Write `review_data.json` with active and rejected items. If
   `review.sampling` is configured, every item is retained and marked with
   `review_sampled`; unsampled items remain pending unless explicitly decided.
9. Run human review, apply `--review-decisions`, honor `--skip-review`, or
   accept-all for `review.enabled: false`.
10. Apply review decisions and write `final.manifest.json` plus the configured
    adapter manifest, currently `ai-toolkit-ltx.manifest.json`.

`work_preview.json` is intentionally written after acquisition plus cheap
deterministic filters and before bucket judge, captioning, or other expensive
work. It records active/rejected counts, deterministic filter rejects/warnings,
planned caption calls, enabled model-backed stages, budget limits, fixture mode,
and whether expensive spend is disabled.

## Checkpoints

Run artifacts are stored under `--out`:

- `review_state.json`: authoritative run state, state version, config hash,
  processed source IDs, filter stats, review decisions, and status.
- `candidates.json`: acquired candidates after media is copied into the run.
- `work_preview.json`: cheap-filter preview before model-backed/caption work.
- `filtered_items.json`: active and rejected item groups after filters.
- `review_data.json`: data served to the review UI.
- `review_server/human_review.final.json`: final submit payload from
  `editorial.human_review`, when the browser review server is used or
  compatibility accept-all writes an equivalent payload.
- `final.manifest.json`: canonical accepted-item manifest.
- `<adapter>.manifest.json`: adapter-specific export.

Writes use the shared atomic JSON helpers where run state or checkpoint
authority matters.

## Resume Semantics

Existing `review_state.json` is authoritative. On resume, the runtime validates
it and compares its `config_hash` with the normalized current config. A mismatch
raises `ResumeConfigMismatchError` before mutating state.

Supported resume statuses:

- `initializing`, `acquiring`, `failed`, `filtering`: load usable checkpoints if
  present, otherwise reacquire and continue.
- `preview_ready`: load `filtered_items.json` from the deterministic-filter
  boundary and continue with model-backed filters/captioning.
- `captioning`: load `filtered_items.json` and continue caption/review work.
- `reviewing`: load `review_data.json` and continue review/finalization.
- `finalized`: return a summary of existing manifests without rewriting them.

Resume does not delete prior completed artifacts unless the current phase
intentionally rewrites the next checkpoint.

## Source Identity

Canonical source identity is shared by source-cap filtering and resume
processed-source tracking:

1. Use `derived_from.source_id` when present.
2. Otherwise use `source_id`.

This handles YouTube-derived clips whose per-clip `source_id` differs from the
original video ID. The YouTube source provider also derives a stable URL/query
source key before download and skips provider-expensive work when that key is in
`processed_source_ids`.

## Filters

Legacy filter blocks normalize to ordered internal stages:

1. `duration_filter`
2. `resolution_filter`
3. `rights_filter`
4. `black_frame_filter`
5. `content_hash_filter`
6. `source_cap_filter`

Explicit `filters.stages` preserves list order. `bucket_judge_filter` is
model-backed and expensive, whether declared directly in `filters.stages` or
adapted from `extensions.bucket_judge`.

Cheap filter defaults are conservative:

- `black_frame_filter` is metadata-only by default with threshold `0.98`.
  Missing metadata warns and passes. Media probing happens only with
  `probe_media: true`.
- `content_hash_filter` rejects later duplicates and keeps the first item in
  order.
- `source_cap_filter` uses canonical source identity and preserves input order.
- `rights_filter` rejects restricted rights statuses, warns/passes unknown
  rights, and can reject configured restricted licenses.

## Fixture And No-Network Expectations

`extensions.fixture_mode: true` keeps fixture tests local and deterministic.
Fixture mode bypasses API secret and budget preflight, loads caption and judge
sidecars when configured, and must not require network access or API keys.

No-spend configs should set budgets to zero and use fixture sidecars:

```json
{
  "budgets": {
    "max_api_calls": 0,
    "max_estimated_cost_usd": 0,
    "providers": {
      "caption.visual_understand": {"max_calls": 0},
      "bucket_judge.visual_understand": {"max_calls": 0}
    }
  },
  "extensions": {
    "fixture_mode": true,
    "fixture_caption_dir": "captions",
    "fixture_judge_dir": "judges"
  }
}
```

Use `--dry-run` for preflight-only checks. Use fixture configs plus
`--review-decisions`, `--skip-review`, or `--review-only` for offline runtime
tests.

## Package Layout

- `source_providers/`: source acquisition and provider-level resume skips.
- `caption_providers/`: fixture or model-backed caption generation.
- `filter_stages/`: deterministic and model-backed candidate filters.
- `manifest_adapters/`: downstream manifest exports.
- `review_ui/`: generic dataset review static assets.
- `schemas/`: packaged runtime JSON schemas.

## Child Executors

The action declares these child actions:

- `youtube.youtube_audio`
- `editorial.scenes`
- `understanding.visual_understand`
- `understanding.video_understand`
- `editorial.human_review`
