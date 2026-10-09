# chiptune.compose - stage notes

- Entrypoint: `astrid.packs.chiptune.executors.compose.run` (`main`), argv built from executor.yaml.
- Synthesis: `executors/_synth.py` (`compose_music`). Pure numpy; no network; no subprocess.
- Outputs: `music.wav` (primary, artifact_type audio) and `beats.json`. Both are listed in `manifest.json`,
  which is the exclusive receipt.
- Structured inputs (`sections`, `hits`, `duck`, `vo_mask`) arrive as strings. Keep them as JSON.
- Determinism: all randomness comes from `seed` through one generator, in a fixed order.
