# Character Swap recommendations (advisory only)

Nothing in this file changes a workflow. Apply a recommendation only by making
an explicit request/settings change. The adapter itself keeps the workflow's
current/default settings, including Turbo enabled at 0.95 and 8 steps.

| Topic | Starting point to consider | Execution contract |
| --- | --- | --- |
| Shot | One continuous 4–5 second shot with one clearly named target | No automatic splitting, target tracking or multi-character guarantee |
| Task strength | 1.0 | Its own default; finite 0..2 overrides are allowed |
| Author's non-Turbo baseline | Explicitly turn Turbo off and consider 20 steps | Not an adapter preset; neither change happens automatically |
| Existing Turbo branch | Keep the native 8-step res_multistep/simple Turbo workflow when desired | Character Swap is applied before the already selected Turbo |
| Replacement | One clear image of the intended identity | Exactly one typed image reference, `<Picture 1>` |
| Performance | One video reference, `<Video 1>`; explicitly select the source window | Existing 24 fps loader normalization; native grid and delivery trim unchanged |
| Audio | Use `audio: false` for visual-only reference conditioning; keep a timeline baseline to preserve original audio | Reference audio defaults remain true; delivery audio is an independent composition decision |
| Preservation | Request the original action/camera/setting in words; use a real mask for exact protected pixels | Prompt wording is not segmentation or an exact preservation guarantee |

The author describes an experimental 1,000-update LoRA, promising short clips and
limitations on timing/longer clips. Do not describe these suggestions as measured
quality of this integration. Native attention and video/audio SigmaShift remain
the pack's existing choices; this change does not introduce Spectrum, Sol or an
alternate guidance pipeline.

## Sources and provenance

Primary repository model card:
https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA

Pinned model-card and checksum locations recorded by the handoff:
https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA/blob/62407e0cc8089c363abd9ce4b0b27662abb237af/README.md
https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA/blob/62407e0cc8089c363abd9ce4b0b27662abb237af/SHA256SUMS

The current model card was accessible during this implementation. The pinned raw
assets were not independently downloaded or re-hashed; immutable values are
carried from the supplied handoff. No model weights or inference outputs were
obtained in this CPU-only implementation.
