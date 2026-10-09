# Generate speech

**Executor**: `generation.generate_speech`
**Reached by**: `generation.generate_speech` directly, or `generation.generate_audio`
with `mode="tts"` (the SDK redirects it here; see `../generate_audio/STAGE.md`).

Invoke through the SDK with a connected Runtime client, a project, and exact
`text`. Optional Edge TTS settings: `provider` (only `edge-tts`), `voice`
(default `en-US-ChristopherNeural`), `rate` (`+0%`), `volume` (`+0%`), `pitch`
(`+0Hz`). Example:

```python
result = client.invoke_result(
    "generation.generate_speech", kind="executor", project="my-project",
    inputs={"text": "Exact words.", "voice": "en-US-AndrewMultilingualNeural", "rate": "+12%"},
    wait=True,
)
```

Requirements: the `edge-tts` Python package (`pip install 'astrid[speech]'`) and
`ffmpeg`/`ffprobe` on PATH. Edge TTS streams from Microsoft's online speech
service (`speech.platform.bing.com:443`); no API key is needed. Synthesis uses
the `edge_tts.Communicate` API with `boundary="WordBoundary"`, so the service
returns a timing event for each word. When the host sets `ASTRID_BROKER_PROXY`,
that proxy is passed to edge-tts.

Managed outputs:

- `speech` (`audio/speech.wav`, PCM 16-bit WAV, audio artifact).
- `speech_manifest` (`speech-provenance.json`): exact text and its SHA-256,
  voice settings, WAV SHA-256 and measured duration.
- `speech_words` (`speech-words.json`): `{"words": [{"word", "start_s", "end_s"}],
  "duration_seconds", "boundary": "WordBoundary", ...}`. Times are seconds from
  the start of the WAV. Edge TTS reports offsets in 100 ns ticks; they are
  converted and rounded to microseconds. Use these as word-accurate cut points.

`manifest.json` is the universal result harvest manifest; it is not a
self-hashed managed output. Synthesis, decoding, or missing word timing removes
staged audio and returns failure. A last word ending more than 50 ms after the
measured WAV duration is recorded as a manifest warning.

This executor does not edit timelines. To place the audio on a timeline, use the
rendering route in `packs/rendering/skill/references/placeholder-voiceover.md`
(media import, then a candidate edit on an audio track, then `bind-script`).
`video_editing.sync_draft_voiceover` is not shipped on this checkout.
