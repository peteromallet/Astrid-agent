# Skills install layer

Astrid installs its prompt content as "skills" into three agent harnesses:
**Claude Code**, **Codex**, and **Hermes**. Each manifest-backed pack exports
one authored skill bundle through the existing sync layer. One canonical
command is:

```bash
python3 -m astrid.skills install --all
```

`--all` only writes to harnesses whose home directory exists; missing harnesses are skipped silently.

The gateway also recognizes external packs selected by managed setup. Run
`python3 -m astrid.setup` to provision pinned defaults and compose their
skills; discovery and `skills sync` do not download code. If a user removes a
managed default skill with `skills uninstall`, Astrid records that choice and
leaves it removed. A missing link that was still installed is drift and is
restored by the normal auto-heal path or `skills sync`.

The installed-wheel recovery entrypoint is `python -m astrid.skills sync` (or
`python -m astrid.skills doctor --heal`).

## SkillDescriptor contract

For a v3 pack, `pack.yaml` declares the single authored source:

```yaml
documentation:
  kind: skill
  path: docs/SKILL.md
```

That file is `astrid/packs/<pack>/docs/SKILL.md` in the checkout. It has normal
YAML frontmatter with at least `name` and `description`, followed by Markdown
body content. Discovery requires the declared file and reads the authored
bundle as-is. It does not scan executor, action, UI, rendering, or nested
component directories for additional skills.

`astrid/packs/_core/docs/SKILL.md` is the manifestless Astrid gateway and is
composed by the same existing sync path; it is not a second manifest-backed
pack export.

```markdown
---
name: "my-pack"
description: "Short blurb that fits on one line."
---

# My pack

Body content visible to the agent.
```

When Astrid discovers a skill it builds a `SkillDescriptor`:

| Field | Source |
| --- | --- |
| `pack_id` | directory name under `astrid/packs/` |
| `name` | frontmatter `name` |
| `description` | frontmatter `description` |
| `short_description` | reused from the discovery search index — `short_description_or_truncated(...)` |
| `skill_dir` | the declared authored bundle directory, normally `astrid/packs/<pack>/docs/` |
| `skill_md` | the declared `documentation.path`, normally `astrid/packs/<pack>/docs/SKILL.md` |
| `hermes_metadata` | optional `metadata.hermes.*` block (see below) |

## Per-pack `docs/SKILL.md` convention

- Source of truth: exactly one authored `docs/SKILL.md` per pack. Claude, Codex,
  and Hermes receive that bundle through the existing composed view.
- Additional Markdown files under the pack are linked documentation, not extra
  skills. Keep references, templates, and assets beside the authored guide and
  use normal relative links.
- The shared file MUST NOT contain Hermes-specific dynamic tokens. The two patterns flagged by the linter are:
  - `${HERMES_*}` — environment-variable interpolation
  - `` !`shell` `` — Hermes inline-shell substitution
- If a pack genuinely needs Hermes-only dynamic content, put it in
  `astrid/packs/<pack>/docs/references/hermes-only.md` and reference it via
  `metadata.hermes.references`.

Do not author in an installed or composed view. Edit the pack bundle, then
refresh the view with the existing sync command. For a source-only check, use
the read-only form:

```bash
python3 -m astrid.skills sync --check --deep
```

When a disposable harness is appropriate, the write form is:

```bash
python3 -m astrid.skills sync --deep --mechanism external-dir --json
```

`--deep` (also `--all`) links each pack skill; `--check` reports drift without
writing. Do not use a personal/global sync to publish an in-progress pack.

Run the lint check via `python3 -m astrid.skills doctor`. Findings are non-zero exit code.

## `metadata.hermes.*` block

Optional block in the frontmatter that Claude and Codex ignore (they don't recognise the key) but Hermes can read:

```markdown
---
name: "my-pack"
description: "Short blurb."
metadata:
  hermes:
    references:
    - "references/hermes-only.md"
    enable_when: "${HERMES_FEATURE_X}"
---
```

Astrid stores the `metadata.hermes.*` mapping verbatim on the descriptor; how a Hermes runtime consumes it is up to that runtime.

## Codex AGENTS.md fenced-block format

Codex installs maintain an idempotent fenced block at `~/.codex/AGENTS.md`. The block is rewritten on every `install`, `uninstall`, and `sync`. Surrounding user content is preserved.

```markdown
<!-- astrid:begin -->
# Astrid skills

- `_core` (/Users/you/.codex/skills/astrid): Use for the Astrid repo: ...
- `examplepack` (/Users/you/.codex/skills/astrid-examplepack): Short blurb here.
<!-- astrid:end -->
```

When zero packs are installed the inner content reads `_no Astrid skills installed_`. Re-running install with the same input set produces a byte-identical file.

## Hermes mechanisms

Default (`--mechanism symlink`): per-pack symlinks at `${HERMES_HOME:-~/.hermes}/skills/astrid-<pack>` (and `astrid/` for the `_core` pack). Same shape as Claude and Codex.

Opt-in (`--mechanism external-dir`): no per-pack symlinks. Instead the install adds the absolute path of `astrid/packs/` to `~/.hermes/config.yaml` `skills.external_dirs`. Other keys in the file are preserved. The list is deduplicated, so re-running install is a no-op.

```yaml
skills:
  external_dirs:
    - /Users/you/work/Astrid/astrid/packs
```

## State file

Install state lives at `$XDG_STATE_HOME/astrid/skills.json` (default `~/.local/state/astrid/skills.json`). Tests and CI can override with `ASTRID_STATE_HOME=...`.

## Nudge

If at least one harness is detected on disk and is missing one of the expected packs, Astrid prints a single-line nudge to stderr at most once every 7 days when you run any non-`skills` subcommand. The nudge is suppressed by `ASTRID_NO_NUDGE=1` or `--quiet`. The nudge is best-effort — it cannot break a real command.

## Related Guides

- [discovery-for-agents.md](discovery-for-agents.md) — The agent-facing
  contract for discovering capabilities: `skills list`, search, and
  `inspect --json`. Skills install is the delivery mechanism; discovery
  describes what agents do with the installed skills.
- [create-a-pack.md](create-a-pack.md) — Current v3 pack authoring and source
  discovery route. The [legacy pack protocol reference](../packs/creating-packs.md)
  covers older manifests and migration details.
