# Managed Codex Image Generation

The public `generation.generate_image` SDK entrypoint selects this runtime
profile when `execution="codex"`. Cloud generation retains its own profile.
Use a model/mode with an existing Codex catalog cell, such as
`model="qwen-image-edit", mode="edit"`, or `flux-dev` / `t2i`.
These catalog labels do not select the actual image model: Codex's current
built-in image tool selects it. The receipt records `codex/gpt-image`; it
does not claim a pinned Sunburst model.

`image_ref`, `style_ref`, `brand_ref` are the ordered source, character/style and brand slots (16 MiB each; `edit` needs a source).
Fill them with `references=[{"ref", "role"}]` ([generate_image](../generate_image/STAGE.md)); raw `{digest, filename, media_type, size_bytes}` descriptors still work.

Count is 1–4; default timeout is 600 seconds per image. `size`, `quality` and
`background` are prompt hints, not guaranteed pixel or alpha controls.
The task uses the host broker with declared OpenAI/ChatGPT routes and an
enforced 512 MiB scratch / 257 MiB output envelope. Codex auth is copied with
private permissions into attempt scratch and removed at completion; generated
images and session files stay in scratch until the runtime publishes outputs.
Missing authentication fails without selecting a different provider.
