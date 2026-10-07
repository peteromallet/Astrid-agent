---
name: seedance-local
description: Run one temporary personal Seedance 2.0 reference-media generation through fal.ai as an experiment-ready Astrid run.
---

# Seedance local

Use `seedance_local.reference_video` for a bounded Seedance 2.0
reference-to-video request using ordered images, a reference video, or both.

Before running:

1. Supply up to nine JPEG, PNG, or WebP image references under 30 MB each.
   A video reference, when used, must be 2–15 seconds, under 50 MB, and MP4/MOV.
2. Repeat `image_ref=...` in the invoke inputs in storyboard order and refer to every image
   explicitly as `@Image1`, `@Image2`, and so on. Refer to a clip as `@Video1`.
3. State the desired motion, camera, composition, reference order, and cuts.
4. Inspect the executor, install it once, and invoke it through the SDK
   (`astrid.sdk.invoke(...)`).

Do not retry a paid request automatically. Inspect the terminal manifest and
debug record first.
