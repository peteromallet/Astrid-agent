# Chiptune - Agent Guide

## When to use this pack

Original retro music and sound effects, generated in code: no samples, network or models.

- A soundtrack bed for a video: one section per chapter, a beat grid, speech ducking, dead-air mutes.
- One-shot UI or cartoon sound effects (stamp, flip, blip, whoosh, chime, error, coin,
  typewriter_tick, wipe, thud), singly or as a zip batch.

Do not use it for speech (`generation.generate_speech`), AI audio (`generation.generate_audio`,
`fal.fal_foley`), or sampled or licensed music.

## Entrypoints

Both executors are deterministic: the same inputs give identical bytes. Re-running identical inputs
reuses the task. After a fix, retry with `python3 -m astrid tasks retry <task-id> --project almost-ready`.

```python
import astrid.sdk as sdk

result = sdk.invoke(  # opens the runtime client itself; pass client= to reuse one
    "chiptune.sfx",
    kind="executor",
    project="almost-ready",
    wait=True,
    inputs={"kind": "stamp", "variant": 2, "pitch": 0},
)
print(result)  # each output: its handle and a viewable local path
sfx = result.output("sfx")  # run:<id>/sfx, usable as a file input
```

## chiptune.compose

| Input | Default | Meaning |
|---|---|---|
| `duration_s` | required | Exact length. The WAV holds round(duration_s x 48000) frames. |
| `bpm` | 132 | Tempo. Every event sits on this grid. |
| `key` | "A minor" | Tonic and mode ("F# major", "Bb minor"). |
| `seed` | 1 | Drives the motif and the drum noise. |
| `sections` | required | JSON array of `{start_s, end_s, energy 0..1, mood, cadence_bars 0..8}`. Edges are **exact**, not snapped. Sections must not overlap; gaps are silent. |
| `hits` | none | JSON array of accents: a number (stab) or `{t, kind}`, kind `stab`, `thud` or `blip`. Times are exact. |
| `vo_mask` | none | JSON array of `[start_s, end_s]` speech spans. The bed dips by `duck_db` (120 ms in, 300 ms out). A gap of 0.6 s or more returns it to full. |
| `duck_db` | -10 | Dip under `vo_mask` and the default for `duck` items. |
| `duck` | none | JSON array of `{start_s, end_s, gain_db}` explicit dips. |
| `mutes` | none | JSON array of `[start_s, end_s]` hard mutes: exact silence for the bed (10 ms ramps at the edges). Hits still sound inside a mute. |
| `master_db` | -16 | RMS target. The -1.05 dBFS peak ceiling wins if they conflict. |

Section behaviour:

- Each section starts its own bar grid at `start_s`, so a chapter opens on a downbeat. The last
  bar may be partial. `mood: silent` (or `energy` 0) is silence.
- `cadence_bars` makes the last N bars resolve to the tonic (dominant, then tonic).
- A fill runs on the last full bar before a section boundary. Sections with `energy >= 0.5` open with a crash (not the first section).
- Hits: `stab` is voiced on the chord sounding at that moment. `thud` is a low dry hit. `blip` is a two-note blip.

Outputs:

- `music`: WAV, 48 kHz stereo 16-bit. The primary output.
- `beats`: JSON, all times in cue seconds:
  `{schema_version, bpm, key, seed, duration_s, sample_rate, bar_s, bars_per_phrase: 4,
  beats[], downbeats[] (every bar start), bars[] (4-bar phrase starts), sections[], hits[{t, kind}], mutes[[start, end]]}`.
  Each section has `index, mood, energy, start_s, end_s, grid_origin_s` (= `start_s`), `bars`,
  `cadence_bars`, `chords`, and measured `peak_dbfs` and `rms_dbfs`.
  There is no snapped or requested time: `start_s` and `end_s` are what you cut at.
  Older beats files that carry `requested_start_s` / `requested_end_s` predate the exact-edge change
  (commit cde9b25d, 9 Oct); regenerate them.

## Worked example: one cue for a timeline

Times come from the timeline and the VO take. `word_t` reads the take's words (`{start, end, text}`).

```python
import json
import astrid.sdk as sdk

VO_WORDS = json.load(open("/path/to/take.words.json"))  # the VO take's word list

def word_t(text, end=False, n=1):
    matches = [w for w in VO_WORDS if w["text"].strip(" ,.!?").lower() == text.lower()]
    word = matches[n - 1]
    return word["end"] if end else word["start"]

t_astrid, t_viral = word_t("Astrid"), word_t("viral")   # chapter moments, in seconds
END = 150.0

sections = [  # exact edges: each chapter starts on its moment
    {"start_s": 0.0, "end_s": t_astrid, "energy": 0.4, "mood": "wistful"},
    {"start_s": t_astrid, "end_s": t_viral, "energy": 0.7, "mood": "tense", "cadence_bars": 2},
    {"start_s": t_viral, "end_s": END, "energy": 0.8, "mood": "triumphant"},
]
vo_mask = [[word_t("Astrid"), word_t("Astrid", end=True)],          # bed dips under speech
           [word_t("viral"), word_t("viral", end=True)]]
hits = [{"t": t_astrid, "kind": "stab"},                              # accents on words
        {"t": t_viral, "kind": "thud"}]
mutes = [[word_t("viral", end=True) + 0.2, word_t("viral", end=True) + 1.2]]  # joke hold: silence

result = sdk.invoke(
    "chiptune.compose",
    kind="executor",
    project="almost-ready",
    wait=True,
    inputs={
        "duration_s": END, "bpm": 132, "key": "A minor", "seed": 7,
        "sections": json.dumps(sections),
        "hits": json.dumps(hits),
        "vo_mask": json.dumps(vo_mask),
        "mutes": json.dumps(mutes),
        "duck_db": -10,
        "master_db": -16,
    },
)
print(result)  # each output: its handle and a viewable local path
music = result.output("music")  # run:<id>/music: the bed to place on the timeline
beats = result.output("beats")  # run:<id>/beats: cut times and beat grid
```

The beats belong with the music clip. `timelines edit TL --project P --clip music --swap-asset run:<id>/music`
brings the run's beats along (and the new length); to attach a grid yourself: `--beats run:<id>/beats` (or a
beats.json), in Python `tl.clip("music").set_beats("run:<id>/beats")`. Every `beat N after …` moment re-resolves.

## chiptune.sfx

| Input | Default | Meaning |
|---|---|---|
| `kind` | blip | stamp, flip, blip, whoosh, chime, error, coin, typewriter_tick, wipe, thud |
| `kinds` | none | Batch: comma list. Returns one zip (`sfx_batch`) of NN-kind-vN.wav plus JSON sidecars. |
| `variant` | 1 | Seed for a different take. Same variant gives identical bytes. |
| `pitch` | 0 | Semitones relative to A4. |
| `duration_s` | per kind | Exact length, 0.01 to 10 s. |

Single mode outputs `sfx` (mono 16-bit WAV, peak -3.1 dBFS) and `sfx_meta` (JSON).
Batch mode outputs `sfx_batch` (zip) and `sfx_meta` (JSON sidecar).

## Verification

`python -m pytest tests/packs/chiptune -q` covers determinism, exact duration, the peak ceiling,
DC offset, edge clicks, exact section edges, cadence, typed hits, mutes, ducking, the batch zip and tempo.
