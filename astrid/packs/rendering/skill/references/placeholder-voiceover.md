# Placeholder voiceover for a timeline draft

Use this when the user wants a basic spoken draft for timing or editorial
feedback. Script text and playable speech are separate assets: generate the audio,
place it on the shot's audio track, and pin the same words as the shot's
registered `voiceover_script` binding. Updating a text binding alone does not
generate speech, and placing audio does not update the text.

This is the route that works on the current checkout. Two older routes are not
available here: `video_editing.sync_draft_voiceover` (not shipped; it needs a
`timelines script` reader that does not exist) and the manual `edge-tts` CLI
recipe (superseded by the executor below).

## 1. Generate the speech

Use `generation.generate_speech` through a connected runtime client. Exact words
go in `text`; the default voice is `en-US-ChristopherNeural` (Astrid Intro
precedent). Choose the voice and rate for the project; Edge TTS calls an online
service, so report failures instead of silently changing the voice.

```python
result = client.invoke_result(
    "generation.generate_speech",
    kind="executor",
    project="my-project",
    inputs={
        "text": "Exact approved spoken words go here.",
        "voice": "en-US-ChristopherNeural",
        "rate": "+0%",
    },
    wait=True,
)
if not result.ok:
    raise RuntimeError(result.error)
```

The run yields three managed outputs, declared by the executor manifest:

- `speech`: WAV (`audio/speech.wav`). Its `sha256:` digest is the managed media
  identity to place on the timeline.
- `speech_manifest`: provenance (exact text, text hash, voice settings, audio
  hash, measured duration). Keep it with the shot's production notes.
- `speech_words`: `{"words": [{"word", "start_s", "end_s"}], "duration_seconds"}`
  in seconds from the start of the WAV. Use it for word-accurate cut points.

Read the outputs from `result.outputs` by their declared names; read a managed
file's bytes with `client.media.read_bytes(digest)`. Check that the WAV decodes
and its duration is plausible before placing it:

```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 speech.wav
```

For a voice audition or a one-off file outside a project, the executor is the
same route; only the project context changes.

## 2. Place the audio on the shot

Follow the [document checkout recipe](document-checkout.md) to get a fresh
editable candidate. Work only on the selected shot's internal timeline (the
placement's `shot_id` points at `work["shots"][shot_id]`):

```python
from astrid.sdk.timeline_editing import place_media

shot = work["shots"][placement["shot_id"]]
internal = shot["internal_timeline"]
duration = float(speech_words["duration_seconds"])  # parsed from the speech_words output
place_media(
    internal,
    speech_digest,          # "sha256:..." of the speech WAV
    track="vo",             # an existing audio track id, or add one with add_track
    start=0.0,
    end=duration,
)
```

Replace an earlier draft VO clip rather than layering a second one. Preserve
recorded or final audio. Set the row-level `duration_ms` to cover the audio and
any intended tail pause; a duration change does not move later shots, so ripple
them by hand if the new narration runs past its window. The placement helper
only edits the detached candidate; nothing changes until `publish`.

If the checkout helper rejects the speech digest as not in the project media
catalog, import the WAV through the public client (`client.media.import_file`
with a distinct idempotency key) and use the digest it returns. Never put a local
file path in the checkout. This import fallback is not yet exercised on this
checkout; record it if you use it.

## 3. Pin the words, check, publish

For a registered shot, pin the script with the checkout helper, using the same
words that were synthesized and the observed binding head (`0` only for a new
binding):

```bash
python3 scripts/timeline_document.py bind-script --file /tmp/edit.json --shot <registered-shot-id> \
  --text-file script.txt --expected-head 0 --idempotency-key narration-01
python3 scripts/timeline_document.py check --file /tmp/edit.json
python3 scripts/timeline_document.py publish --file /tmp/edit.json --idempotency-key pin-narration-01
```

Review the `check` diff before `publish`. For a new shot, follow the main skill's
registration-before-binding sequence, and do not substitute unregistered inline
text for a registered descriptor. Verify the pinned words afterward with
`python3 -m astrid timelines shots text list --project <project> --kind voiceover_script`.

## 4. Review

- Decode checks (ffprobe, ffmpeg `-f null`) establish file integrity only. An
  auditory review establishes pronunciation and pacing. Report which was checked.
- `speech_words` gives word start and end times as the service reports them.
  Cut on sentence or beat boundaries first; use word times only where a cut needs
  them. Do not estimate timing from character counts.
- Keep the voice, rate, and placeholder status in shot metadata or a production
  note when they need to be reproducible; the speech manifest already records
  the text and settings.
- No video render is needed unless the user requests one. Confirm the audio
  appears under Reigh's **Audio** gallery filter after publication.
