# chiptune.compose - stage notes

- Entrypoint: `astrid.packs.chiptune.executors.compose.run` (`main`), argv built from executor.yaml.
- Synthesis: `executors/_synth.py` (`compose_music`). Pure numpy; no network; no subprocess.
- Outputs: `music.wav` (primary, artifact_type audio) and `beats.json`, both listed in `manifest.json`
  (the exclusive receipt). `beats.json` fields are listed in `skill/SKILL.md`.
- Section edges are exact (`start_s`, `end_s`). Each section gets its own bar grid from `start_s`. Nothing is snapped.
- Structured inputs (`sections`, `hits`, `duck`, `vo_mask`, `mutes`) arrive as strings. Keep them as JSON.
  `hits` items are numbers (stab) or `{t, kind}` with kind `stab`, `thud` or `blip`.
- `mutes` are exact silence for the bed. Hits are mixed after the mutes, so they sound inside a mute.
- Determinism: all randomness comes from `seed` through one generator, in a fixed order.
