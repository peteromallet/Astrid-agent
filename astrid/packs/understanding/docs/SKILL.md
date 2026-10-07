---
name: understanding
description: >
  Understanding pack: modality-specific LLM inspection for audio, images,
  synchronized video, and scene descriptions, with a legacy dispatcher.
---

# Understanding

Use this pack to inspect existing media. The five stable public actions are
`understanding.audio_understand`, `understanding.visual_understand`,
`understanding.video_understand`, `understanding.scene_describe`, and the
legacy `understanding.understand` dispatcher.

## Choose an action

| Action | Provider | Use it for | Primary outputs |
|---|---|---|---|
| `understanding.audio_understand` | OpenAI | Audio clips, video-audio windows, audition reels, and editorial listening questions | `analysis.json`, `manifest.json` |
| `understanding.visual_understand` | OpenAI | One image, ordered images, or sampled video frames; free text or JSON-schema output | `result.json`, `manifest.json`, plus frame/contact-sheet sidecars |
| `understanding.video_understand` | Gemini | Synchronized audio/video windows with an optional response schema | `result.json`, `manifest.json`, and `video-windows/` |
| `understanding.scene_describe` | Gemini | Scene and triage inputs for the editorial scene-description step | `scene_descriptions.json`, `manifest.json` |
| `understanding.understand` | Routed | Legacy `--mode audio|image|visual|video` dispatch; forwards the remaining flags unchanged | The selected modality action's outputs |

The action declarations in `pack.yaml` are the binding authority. The focused
stage guides contain the modality-specific operating details:

- [Audio](../actions/audio_understand/STAGE.md)
- [Visual](../actions/visual_understand/STAGE.md)
- [Video](../actions/video_understand/STAGE.md)
- [Scene descriptions](../actions/scene_describe/STAGE.md)
- [Legacy dispatcher](../actions/understand/STAGE.md)

## Public contracts

All actions run in a subprocess, declare `requires_timeline: false`, and write
their result manifest beside their declared result. `--dry-run` plans local
window/frame work and prints JSON without resolving a credential or submitting
to a provider. Provider calls are blocking; a provider or validation failure is
recorded by the runner and the action returns a non-zero status. `--force`
rebuilds local extracted media, while the scene action retains its
`scene_descriptions.json` sentinel cache behavior.

`visual_understand` accepts `image` (repeatable), `video` with repeatable
`at` timestamps, required `query`, `mode`, explicit `model`, repeatable
`compare_model`, `detail`, contact-sheet and crop controls, `response_schema`,
`env_file`, token/timeout controls, `force`, and `dry_run`. Ordered image
requests preserve each source image separately; result evidence retains prompt
and image hashes, model/response metadata, structured answers, and the
`manifest.json` sidecar. Use an optional explicitly named env file only when
the Runtime-managed credential is not being used.

`audio_understand` accepts repeated `audio` or one `video`, optional `at`,
`start`, `end`, window/chunk controls, `query`, `mode`, explicit `model`,
repeatable `compare_model`, `env_file`, and local output/retry controls. It
preserves the numbered audition-reel behavior and writes `analysis.json` and
the universal manifest.

`video_understand` accepts required `video`, query/window planning controls,
`mode`, explicit Gemini `model`, repeatable `compare_model`, an optional JSON
`response_schema`, `env_file`, and local extraction controls. Extracted windows
remain under `video-windows/`; their source-relative start/end metadata is
retained in the result and manifest.

`scene_describe` accepts `video`, `scenes`, `triage`, `top_n`, `model`, and
optional `env_file`. It preserves pipeline step 5, its editorial dependencies,
the versioned `scene_descriptions.json` schema, and sentinel/cache behavior.

`understanding.understand` requires `mode` and forwards `--audio`, `--image`,
`--video`, `--at`, and `--query` exactly to the selected modality runner. The
aliases are `audio`, `image`, `visual`, and `video`; use a specific action when
you need its complete modality-specific flags.

## Credentials and provider boundary

| Actions | Credential | Declared destination |
|---|---|---|
| Audio, visual, and legacy OpenAI routes | `OPENAI_API_KEY` | `api.openai.com:443` |
| Video and scene descriptions | `GEMINI_API_KEY` | `generativelanguage.googleapis.com:443` |

Network is declared per provider action, and the pack also declares subprocess,
project-file, network, and environment permissions. The migration and its
focused tests are offline-only: no OpenAI, Google/Gemini, GPU, or external
network operation is implied by this guide.
