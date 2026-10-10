# Generate Image

**Executor**: `generation.generate_image`  
**Modality**: image (`schema_version: 2`)  
**Status**: implemented (Sprint 02 — v2 model → mode → backend taxonomy)

Generates images from text prompts (or prompt files) using local (vibecomfy),
cloud (fal), or Codex `image_generation` backends.  A single executor
dispatches through `BackendAdapter` (SD-004) — callers pick a model, a mode,
and a backend; the executor does the rest.  **`--mode` is required** (SD-005).

## Starting models (Tier-1)

| Model               | Modes wired       | Local template(s)              | Cloud endpoint(s)                     | Codex |
|---------------------|-------------------|--------------------------------|---------------------------------------|-------|
| `z-image`           | t2i, i2i          | `image/z_image`, `image/z_image_img2img` | `fal-ai/z-image/turbo`, `fal-ai/z-image/turbo/image-to-image` | yes |
| `qwen-image-2512`   | t2i               | `image/qwen_image_2512`       | `fal-ai/qwen-image`                  | yes |
| `qwen-image-edit`   | edit              | `edit/qwen_image_edit`        | `fal-ai/qwen-image-edit`             | yes |
| `seedream-v5-pro`   | edit              | —                             | `bytedance/seedream/v5/pro/edit`      | no |
| `flux-dev`          | t2i, i2i          | — (no local template per SD-001) | `fal-ai/flux/dev`, `fal-ai/flux/dev/image-to-image` | yes |
| `flux-schnell`      | t2i               | — (no local template per SD-001) | `fal-ai/flux/schnell`                | yes |
| `seedvr2-upscaler`  | upscale           | —                             | `fal-ai/seedvr/upscale/image/seamless` | no |

`upscale` mode is prompt-less: it requires `image_ref` plus optional
`upscale_mode` (`factor`/`target`), `upscale_factor` (1-10), `target_resolution`
(`720p`/`1080p`/`1440p`/`2160p`), `noise_scale`, `seed`, and `output_format`.

The `codex` backend is also wired for image modes above. It runs
`codex exec` and forces Codex's built-in `image_generation` tool by prompt
contract. The PNG is expected under `~/.codex/generated_images/<session-id>/`
as `ig_*.png`; the adapter copies fresh files into `{out}/images` with
deterministic `codex_NNN.png` names. If Codex reports success but no fresh
`ig_*.png` appears, the executor hard-fails.

All entries are registered in `astrid/core/model_catalog/models.yaml` under
`schema_version: 2`.  Each entry declares per-mode `supports: [...]`,
`requires: [...]`, and per-backend `param_map` entries (SD-003).

## Execution modes

### Local (`--execution local`)

Dispatches through `VibeComfyBackend` adapter (SD-004).  Uses
[vibecomfy](https://github.com/nosresearch/vibecomfy) to drive a local
ComfyUI backend.  The `vibecomfy` package is **lazy-imported** inside the
adapter — only loaded when `execution=local`.  Cloud-only invocations never
touch the package (SD-009).

Requires a running ComfyUI instance reachable from the executor process
(configured via the vibecomfy package defaults or environment).

### Cloud (`--execution cloud`)

Dispatches through `FalBackend` adapter (SD-004).  Pure HTTP against
[fal.ai](https://fal.ai) using `astrid/core/util/http.py` `HttpClient`.
No fal SDK required (SD-009).

Requires `FAL_KEY` to be resolvable via the candidate-env-file walk
(see `astrid/core/util/secrets.py`).

### Codex (`--execution codex`)

Dispatches through `CodexBackend`.  This path needs **no `OPENAI_API_KEY`** and
does not read OpenAI API key files. It uses the Codex CLI's own ChatGPT auth at
`~/.codex/auth.json`, so the cheap readiness check is:

1. `codex` binary is on `PATH`
2. `~/.codex/auth.json` exists

If `--execution codex` is requested and either check fails, the executor falls
back to `--execution cloud` when the selected model/mode has a cloud backend,
and prints a warning naming the reason. This is a preflight fallback only; a
real Codex generation failure does not silently switch backends.

`--size`, `--quality`, and `--background` are hints for Codex, not structured
API parameters. The adapter folds them into natural language (for example,
wide 3:2, high fidelity, transparent background). Aspect ratio is often
honored, but exact pixels and true alpha are not guaranteed.

## Escape hatch

**For LoRAs, IP-adapter, controlnet, custom samplers, and exotic conditioning,
use `vibecomfy.run` directly.**  The `generation.generate_image` executor is
scoped to *basic generation only* (prompt, negative_prompt, seed, count, size,
single image_ref, strength, guidance_scale, steps).  Everything else belongs in
the escape hatch.

See:
- `astrid/packs/vibecomfy/executors/run/STAGE.md` — VibeComfy workflow runner
- `docs/generation/` — modality contracts, manifest schema, feature list

## SDK quick-start

Use the public SDK; every executor invocation is attached to a project and
project-scoped runs write their outputs under that project's run tree.

```python
import astrid.sdk as sdk
result = sdk.invoke(
    "generation.generate_image",
    kind="executor",
    project="demo",
    inputs={
        "model": "z-image",
        "mode": "t2i",
        "execution": "local",
        "prompt": "a serene mountain lake at dawn",
    },
    dry_run=True,
)
```

### Inputs and defaults (SDK, `sdk.invoke("generation.generate_image", ...)`)

Pass these in `inputs={...}`. Any other key is refused with
`undeclared parameter(s): ...` and the error lists the declared names.

| Input | Default | Notes |
|---|---|---|
| `prompt` | `""` | Required for t2i/i2i/edit. Mutually exclusive with `prompts_file`. |
| `model` | required | Catalog id (`flux-dev`, `z-image`, `qwen-image-edit`, ...). On the codex route see the labels below. |
| `mode` | required | `t2i`, `i2i`, `edit`, `upscale` (upscale needs no prompt). |
| `execution` | required | `local`, `cloud` or `codex`. |
| `references` | none | `[{"ref": ..., "role": ...}]`, see the roles table below. Codex route only for the reference roles. |
| `count` | `1` (codex: `1`, max `4`) | Number of images. There is no `n` or `num_images`; use `count`. |
| `quality` | codex `high` | Codex hint: `low`, `medium`, `high`, `auto`. |
| `size` | codex `1536x1024` | `WxH`. For `mode=edit` the output follows `size`, not the source (see below). |
| `background` | codex `opaque` | Codex hint: `transparent`, `opaque`, `auto`. |
| `seed` | none (codex `0`) | Deterministic seed. |
| `timeout` | codex `600` s, generate_image `300` s | Per-image timeout. |
| `negative_prompt`, `strength`, `guidance_scale`, `steps`, `loras` | none | Cloud/local only; dropped with a warning on codex. |
| `image_ref`, `mask_ref`, `upscale_mode`, `upscale_factor`, `target_resolution`, `noise_scale`, `prompts_file`, `shot_generation_recipe` | none | Advanced; on codex, `image_ref` is filled by `references` with role `source`. |

Codex `size`, `quality` and `background` are prompt hints, not guaranteed
pixel or alpha controls. The full input list is printed by
`sdk.get_capability("generation.generate_image_codex", kind="executor").inputs`.

### `references=[{"ref", "role"}]`

`ref` is a reference name (`"Astrid presenter"`), a prior output row
(`result.output("generated_images")`), `"run:<run_id>/<port>#n"`, or
`"sha256:<digest>"`. A **local file path is refused**: import it first with
`python -m astrid media import FILE --project P`, then pass the name or digest.

| role | slot the executor receives | route |
|---|---|---|
| `source` | `image_ref` | codex only |
| `character` | `style_ref` | codex only |
| `style` | `style_ref` (same slot as `character`; use one) | codex only |
| `brand` | `brand_ref` | codex only |

On cloud and local, pass `image_ref="<path or name>"` directly instead; those
routes do not take `references`.

Order is source, then character/style, then brand. `depicts: True` on an entry
lists the output under `media references show "<name>"`. Any role outside this
table is refused with the valid roles and slots listed.

### Codex model/mode labels

On `execution="codex"` the `model` and `mode` labels only pick a catalog cell
so the request has a valid shape; Codex's built-in image tool picks the actual
image model, and the receipt records `codex/gpt-image`. The labels the codex
route accepts are the catalog cells with a codex backend, for example
`flux-dev`/`t2i` or `flux-dev`/`i2i`, `flux-schnell`/`t2i`, `z-image`/`t2i`,
`qwen-image-2512`/`t2i`, `qwen-image-edit`/`edit`. An unknown model on the codex
route lists exactly these (`Unknown model 'x' for execution 'codex'. Models valid
on that route ...`).

### Edits: size and variant keys

- **Edit output size.** An edit's output follows the `size` input, not the source
  image. For `mode=edit`, pass the source's size. If `size` flips the source's
  orientation, the result carries a warning with code `edit_size_orientation`
  (PNG sources are checked; a 1536x1024 source with `size=1024x1536` returns a
  portrait image and warns).
- **`variant_key`.** `original` is the first image generated by this request
  (ordinal 0), **not the source image**; `variant-N` is the Nth additional image
  of the same request. The keys name generation slots, not lineage to the source.

### Validate without running: `dry_run=True`

Validate inputs with `dry_run=True`. It binds inputs, shows the argv and runs
the same admission checks (undeclared inputs, route, roles, local files), but
it never admits a task, never opens a client and never writes the ledger:

```python
import astrid.sdk as sdk
preview = sdk.invoke("generation.generate_image", kind="executor", project="demo",
    dry_run=True, inputs={"model": "flux-dev", "mode": "t2i", "execution": "codex",
                          "prompt": "a tiny blue teapot", "count": 2})
print(preview.raw_result["command"])
```

### Worked example

```python
import astrid.sdk as sdk
from astrid.sdk import AstridClient

with AstridClient.open_from_launcher() as client:
    result = sdk.invoke(
        "generation.generate_image", kind="executor", project="demo", client=client, wait=True,
        inputs={"model": "qwen-image-edit", "mode": "edit", "execution": "codex",
                "prompt": "same shot, night window", "size": "1536x1024",
                "references": [{"ref": "Astrid presenter", "role": "character"}]},
    )
    print(result)                 # the short summary: ok, run id, warnings
    result.raise_for_error()      # raises the typed error if the run failed
```

Use `print(result)`. `result.output(port)` and `result.outputs` return the full
~2 KB records, so use them only to pass a handle to the next capability.

## Internal runner command (not a public entrypoint)

The following module command is reserved for Astrid's internal runner and is
shown only for implementation debugging. Direct invocation is rejected by the
canonical-entrypoint guard; use the SDK example above.

```bash
# Cloud text-to-image
ASTRID_INTERNAL_INVOCATION=1 python -m astrid.packs.generation.executors.generate_image.run \
  --model flux-dev --mode t2i --execution cloud \
  --prompt "a serene mountain lake at dawn" --out ./out

# Local text-to-image (requires vibecomfy + ComfyUI)
ASTRID_INTERNAL_INVOCATION=1 python -m astrid.packs.generation.executors.generate_image.run \
  --model z-image --mode t2i --execution local \
  --prompt "a serene mountain lake at dawn" --out ./out

# Image-to-image (cloud)
ASTRID_INTERNAL_INVOCATION=1 python -m astrid.packs.generation.executors.generate_image.run \
  --model flux-dev --mode i2i --execution cloud \
  --prompt "turn this into a watercolor painting" \
  --image-ref ./input.png --out ./out

# Image-to-image (local)
ASTRID_INTERNAL_INVOCATION=1 python -m astrid.packs.generation.executors.generate_image.run \
  --model z-image --mode i2i --execution local \
  --prompt "turn this into a watercolor painting" \
  --image-ref ./input.png --out ./out

# Instruction-guided edit
ASTRID_INTERNAL_INVOCATION=1 python -m astrid.packs.generation.executors.generate_image.run \
  --model qwen-image-edit --mode edit --execution cloud \
  --prompt "replace the background with a forest" \
  --image-ref ./input.png --out ./out

# Multiple images with seed
ASTRID_INTERNAL_INVOCATION=1 python -m astrid.packs.generation.executors.generate_image.run \
  --model flux-schnell --mode t2i --execution cloud \
  --prompt "cyberpunk city" --count 3 --seed 42 --out ./out

# Codex text-to-image (no OpenAI API key)
ASTRID_INTERNAL_INVOCATION=1 python -m astrid.packs.generation.executors.generate_image.run \
  --model flux-dev --mode t2i --execution codex \
  --prompt "a tiny blue teapot" --size 1024x1024 --quality low --out ./out
```

## Prompts file (JSONL)

For batch generation, use `--prompts-file` with a JSONL file (one JSON object
per line).  Each line may override `model`, `seed`, `count`, `size`,
`negative_prompt`, `image_ref`, `strength`, `guidance_scale`, and `steps`.
**Per-entry `model` overrides must include an explicit `mode` field matching
CLI `--mode`** (SD-005, FLAG-004):

```jsonl
{"prompt": "a cat in a spacesuit", "seed": 42, "count": 2}
{"prompt": "a dog on a skateboard", "negative_prompt": "blurry", "size": "1024x1024"}
{"prompt": "replace the background with a forest", "model": "qwen-image-edit", "mode": "edit", "image_ref": "/path/to/source.png"}
```

`--prompt` and `--prompts-file` are mutually exclusive — providing both is
rejected at argparse.

## Validation rules

1. **Missing `--mode`** → rejected at argparse (required argument, SD-005).
2. **Missing `requires`** (e.g. `flux-dev --mode i2i` without `--image-ref`)
   → hard-fail BEFORE any HTTP call or vibecomfy import.
3. **`--execution` must name a registered backend** such as `local`, `cloud`,
   or `codex`.
4. **Unsupported features** (e.g. `--negative-prompt` on `flux-dev --mode t2i` cloud) →
   dropped with a `Warning` in the manifest; never hard-fail (SD-004).
5. **Per-entry mode mismatch**: If a prompts-file entry overrides `model`
   without a matching `mode` field, the entry is rejected (FLAG-004).

## Output

- `{out}/images/` — generated image files (e.g. `0-flux-dev.png`)
- `{out}/manifest.json` — canonical manifest conforming to `docs/generation/20-manifest-schema.md` (v2)

## Design docs

- `docs/generation/00-features.md` — canonical feature list
- `docs/generation/10-registry-schema.md` — model registry schema (v2)
- `docs/generation/20-manifest-schema.md` — manifest JSON shape (v2)
- `docs/generation/30-image-contract.md` — image modality contract with six canonical modes
