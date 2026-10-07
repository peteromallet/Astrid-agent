---
name: upload
description: Publish a reachable video URL to YouTube through the shared banodoco-social publisher.
---

# Upload to YouTube Action

`youtube.upload` is the terminal publishing action. It sends a reachable
HTTP(S) video URL and metadata to the existing shared `banodoco-social`
publisher, which owns the Zapier/YouTube integration and OAuth behavior. The
action does not ingest a local video file and does not create a file artifact.

## Inputs

| name | type | required | description |
|---|---|---:|---|
| `video_url` | string | yes | Reachable HTTP(S) URL for the finished video. |
| `title` | string | yes | YouTube video title. |
| `description` | string | yes | YouTube video description. |
| `tag` | string | no | Individual tag; may be repeated or supplied as a list. |
| `tags` | string | no | Comma-separated tags; may be repeated or supplied as a list. |
| `privacy_status` | string | no | `private` (default), `unlisted`, or `public`. |
| `playlist_id` | string | no | Optional YouTube playlist ID. |
| `made_for_kids` | boolean | no | `false` by default. |

The return value is the publisher's JSON result in the public action result
payload. It is not a media output and must not be interpreted as a local file
path.

## Validation and errors

The action rejects local paths before calling the shared publisher. Publisher
errors retain the existing Astrid error mapping and recovery guidance. The
host owns admission, cancellation, and settlement; a cancelled or failed
attempt cannot be reported as a successful publish.
