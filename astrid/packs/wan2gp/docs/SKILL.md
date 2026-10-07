---
name: wan2gp
description: >
  Native Wan2GP pack — generate video through the GenericPackHost-owned
  upstream Python session (shared.api.init / WanGPSession.submit_task). The
  selected upstream candidate is deepbeepmeep/Wan2GP@f3f204e5.
---

# Wan2GP

Native Wan2GP is the **local** video-generation engine for Astrid. Astrid owns
the typed capability, settings compiler, thin host adapter, and lifecycle
mapping; the upstream checkout and installation are explicit host admission
inputs, not Worker-relative discovery. This pack does not claim native model
residency, warm reuse, deployment readiness, or real engine/GPU qualification.

## Engine seam

```
shared.api.init(root=..., output_dir=...) -> WanGPSession
WanGPSession.submit_task(settings: dict) -> SessionJob
SessionJob.result(timeout=...) -> GenerationResult
```

- `root` is the Wan2GP checkout root (contains `wgp.py`, `shared/`, `wgp_config.json`).
- `output_dir` is the per-runner private spool (attempt-scoped, not shared).
- `settings` is the compiled Wan2GP settings dict (deterministic compiler in `astrid/packs/wan2gp/src/compiler.py`).
- Cancellation is cooperative via `SessionJob.cancel()` / `WanGPSession.cancel()`.

## Executors

| Executor | What it does |
|---|---|
| `wan2gp.generate_video` | Typed host-mediated generation: compiles inputs, submits one native job through the retained host child, and returns structured terminal evidence. |
| `wan2gp.validate_settings` | Validates and compiles inputs without executing the engine. |

## Session lifecycle

GenericPackHost owns native session initialization, job submission, output
custody, cooperative cancellation, settlement, and release. The direct-run
guard never performs per-attempt `init()`/`close()` ownership, and the pack does
not create a second session manager. F05's synthetic host fixture proves the
route and fail-closed boundaries only; it does not prove warm reuse or model
residency.

## Inputs (portable vs machine-local)

Portable (part of capability digest): `prompt`, `negative_prompt`, `model`, `resolution`, `num_frames`/`video_length`, `fps`/`force_fps`, `seed`, `guidance_scale`, `loras`, etc.

Machine-local (excluded from digest, part of reuse fingerprint): `wan2gp_path`, `attempt_root`, `device`, `output_dir`.

## Quick-start

```python
import astrid.sdk as sdk

# Validate without running
sdk.invoke("wan2gp.validate_settings", kind="executor", inputs={"prompt": "a cat", "model": "wan-2.2"})

# Host-mediated generation (requires explicit Wan source/config/interpreter admission)
sdk.invoke("wan2gp.generate_video", kind="executor", inputs={"prompt": "a cat", "model": "wan-2.2", "resolution": "1280x720"})
```
