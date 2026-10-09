---
name: local
description: >
  Personal pack of user-owned, reusable visual effects and their declared
  assets for Astrid timelines.
---

# Personal Pack

This one-user pack keeps the stable source ID `local`; **Personal** is its
display label and ownership description. Existing element references remain
owned by the same pack.

Use its declared elements when a timeline needs one of the reusable visual
effects in `rendering/elements/effects`. Element manifests and their TSX
components/assets are trusted source contributions included in the Astrid/Reigh
build and rendering catalog.

This pack supplies rendering elements and resources. Use the rendering pack
for timeline authoring, preview, and export, and use the Runtime live-scene
route for self-contained user-authored HTML scenes. Those scenes are separate
project objects with the current `assets: []` contract; they are not installed
from this pack or compiled into TSX at runtime.
