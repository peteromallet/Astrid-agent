# chiptune.sfx - stage notes

- Entrypoint: `astrid.packs.chiptune.executors.sfx.run` (`main`), argv built from executor.yaml.
- Synthesis: `executors/_synth.py` (`render_sfx`). Pure numpy; seeded from crc32(kind) and variant, so it is stable across processes.
- Single mode outputs `sfx.wav` (primary) and `sfx.json`. Batch mode (`kinds`) outputs `sfx-batch.zip` (primary) and `sfx-batch.json`.
- The zip uses fixed timestamps, so identical requests give identical archives.
