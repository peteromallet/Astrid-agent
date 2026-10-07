---
name: youtube_audio
description: Download a YouTube video's audio (MP3) or video (MP4) — by search query or direct URL via yt-dlp.
---

# YouTube Audio/Video Action

Use `youtube.youtube_audio` to acquire one YouTube result as an MP3 or MP4.
The public input is always `query`: free text selects the current top search
hit, while a direct HTTP(S) URL in `query` is downloaded directly. There is no
separate `url` input.

## Inputs

| name | type | required | description |
|---|---|---:|---|
| `query` | string | yes | Search text or a direct HTTP(S) YouTube URL. |
| `mode` | string | no | `audio` (default, MP3 extraction) or `video` (MP4 download). |

`mode="video"` is the MP4 acquisition route used by Training. Audio mode
requires both `yt-dlp` and `ffmpeg`; video mode requires `yt-dlp` and does not
perform audio extraction.

## Managed outputs

The action writes into the host-assigned output spool. Its strict
`manifest.json` receipt names the contained `media` output and records the
actual `.mp3` or `.mp4` path, media type, hash, byte count, selected mode,
target, yt-dlp provenance, and output details. The manifest itself is retained
as the declared `media_manifest` sidecar. Callers should read the Runtime's
managed output records; they should not pass a local `out` path or infer a
filename from the search text.

## Boundary and failures

yt-dlp runs only through the existing host-managed YouTube media broker. The
cache policy is `none`. Non-zero yt-dlp exit, a missing/empty output, missing
binaries, malformed output receipt, cancellation, or a lost host lease is a
failed or cancelled action and must not be treated as a successful download.

Respect YouTube's terms and only acquire material you are permitted to use.
