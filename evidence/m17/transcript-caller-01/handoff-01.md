# M17 Transcript Caller Handoff

Reservation: `M17-TRANSCRIPT-KEYWORD-CHILD-01`

Base: Astrid worktree HEAD `9be144c4f2f63216e693a1a13e42cd79d3845845`. The candidate manifest was resolved in the Megado run directory at `/Users/peteromalley/Documents/reigh-workspace/Astrid/.otto/runs/pack-authoring-convergence-20261001/evidence/wave3/post-m17-youtube-scenes-accounting-candidate-manifest.json`; SHA-256 `42387ce22a64b01b73d9bd837484cf431e5fc97719e57bb438c897a08812fd74`. All five source pins and the dependency pins matched before editing. `budget.py` is both a source pin and one listed dependency; its post-edit hash is therefore expected to differ, while all other read-only dependency hashes still match.

## Implemented

- `TranscriptKeywordFilter` uses public `astrid.sdk.invoke("editorial.transcribe", kind="action", wait=True, timeout_seconds=600.0, poll_seconds=0.1)` after a cache miss. It sends the existing exact selected clip as the plain attempt-relative producer mapping `{filename, media_type, output_port}`. The path is resolved beneath the trusted parent attempt output root and must be a regular file. No Runtime/API primitive or local association is introduced.
- The run-scoped `ChildWorkMeter` hashes the clip and charges its size/object identity through the existing registration method before admission. The child key includes the selected clip identity and measured digest; exact key and immutable invocation inputs are admitted before `sdk.invoke`. The run meter and attempt root are excluded from transcript cache configuration hashing. A cache hit does no registration or child admission.
- On completion, the caller selects exactly one `transcript` output with matching task/attempt/run identity, retains only that real managed output association, materializes it beneath the attempt output root, verifies regular-file path, size, and SHA-256, then normalizes JSON and writes the existing hashed sidecar. Existing stale-sidecar invalidation and strict/lenient filter behavior remain in place.
- `DatasetRunServices` and `phases.py` carry the trusted attempt output root into both deterministic and model-backed filter phases. `ChildWorkMeter.register_local_file` is limited to a bounded regular local file and delegates byte/object ceilings to the existing registration path.
- The reserved Training D18 caller test now covers the local clip producer mapping through public SDK invocation, actual Editorial declaration/action execution, `HostChildBridge`, `GenericPackHost`, and Runtime custody/settlement. It stubs the transcription provider and clip extraction only. Focused sidecar/cache/option-hold tests were added.

## M04 receiver dependency

The pinned Editorial declaration accepts `audio` and `env_file`; it does not declare the receiver CLI options `model`, `language`, `max_chunk_sec`, or `no_vad_gate`. The caller preserves configured non-default values by explicitly holding only that invocation with an M04-specific error before local registration/admission. Receiver defaults (`whisper-1`, `en`, `600`, VAD enabled) remain equivalent when the corresponding options are unset/default. `env_file` continues through the declared input. M04 should add the four scalar/boolean inputs to `editorial.transcribe` with types matching the receiver CLI, then allow those values in the caller input mapping. No Editorial file was edited under this reservation.

## Files and hashes

Exact current sizes and SHA-256 values for the five authorized source files are recorded in `after-source-hashes-01.json`. Read-only dependency rechecks are in `dependency-verification-01.json`. Changes were confined to those five files plus this evidence directory.

## Validation

No tests, builds, installations, provider, network, or GPU calls were run, per reservation. The coordinator owns validation.
