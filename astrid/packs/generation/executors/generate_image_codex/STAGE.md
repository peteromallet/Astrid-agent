# Managed Codex Image Generation

The public `generation.generate_image` SDK entrypoint selects this runtime
profile when `execution="codex"`. Cloud generation retains its own profile.
Use a model/mode with an existing Codex catalog cell, such as
`model="qwen-image-edit", mode="edit"`, or `flux-dev` / `t2i`; an unknown model
lists the cells this route accepts.
These catalog labels do not select the actual image model: Codex's current
built-in image tool selects it. The receipt records `codex/gpt-image`; it
does not claim a pinned Sunburst model.

`image_ref`, `style_ref`, `brand_ref` are the ordered source, character/style and brand slots (16 MiB each; `edit` needs a source).
Fill them with `references=[{"ref", "role"}]` ([generate_image](../generate_image/STAGE.md)); raw `{digest, filename, media_type, size_bytes}` descriptors still work.

Inputs and defaults: `model`, `mode`, `execution` (set to `codex`), `prompt`
(required), `count` (default 1, range 1–4; there is no `n` or `num_images`),
`size` (default `1536x1024`), `quality` (default `high`), `background`
(default `opaque`), `timeout` (default 600 s per image), `seed` (default 0).
An undeclared key such as `n=` is refused with the list of declared inputs.
Validate any request first with `sdk.invoke(..., dry_run=True)`; it admits nothing.

Roles on this route: `source` fills `image_ref`, `character` and `style` both
fill `style_ref` (one of them), `brand` fills `brand_ref`. Local file paths are
refused: import them with `python -m astrid media import FILE --project P` first.
For `mode="edit"` the output follows `size`, so pass the source's size.
`variant_key` `original` is the first image of the request, not the source. `size`, `quality` and
`background` are prompt hints, not guaranteed pixel or alpha controls.
The task uses the host broker with declared OpenAI/ChatGPT routes and an
enforced 512 MiB scratch / 257 MiB output envelope. Codex auth is copied with
private permissions into attempt scratch and removed at completion; generated
images and session files stay in scratch until the runtime publishes outputs.
Missing authentication fails without selecting a different provider.
