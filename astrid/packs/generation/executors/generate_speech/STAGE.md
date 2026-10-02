# Generate speech

Invoke `generation.generate_speech` through the SDK with a connected Runtime
client, project, exact `text`, and configurable Edge TTS `provider`, `voice`,
`rate`, `volume`, and `pitch`. Defaults are `edge-tts`,
`en-US-ChristopherNeural`, `+0%`, `+0%`, and `+0Hz`. The local executables
`edge-tts`, `ffmpeg`, and `ffprobe` are required. Edge TTS uses an online speech
service via the host's declared WebSocket broker route.

The managed result ports are `speech` (PCM WAV) and `speech_manifest`
(`speech-provenance.json` containing exact words, voice settings, text/audio
hashes and measured duration). `manifest.json` is the universal result harvest
manifest; it is not a self-hashed managed output. Synthesis and decoding errors
remove staged audio and return failure. This executor does not edit timelines;
use `video_editing.sync_draft_voiceover` to reconcile temporary narration.
