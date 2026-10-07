# When and how to create a pack

Turn something useful into something reusable. A pack is the unit of
distribution and namespace: its `pack.yaml` declares the public functionality,
while implementation and support stay inside that pack.

First check whether an existing pack already does the job; a one-off task does
not need a new pack.

## Give it to your agent

```text
Help me turn this idea into an Astrid pack.
Start with Astrid’s Pack Builder skill, check what already exists,
then build and validate the smallest useful version.
```

[Open the Pack Builder skill →](../../astrid/packs/_core/docs/pack-builder/SKILL.md)

## Build it yourself

Choose the contribution roles you need: `action`, `ui`, `rendering`, and
`shared`. A pack may omit unused roles. A path or folder is not an export by
itself: declare every public function or contribution in `pack.yaml`, then put
its implementation and private support under the applicable role folder.

From an empty working folder, with Astrid’s environment active:

```bash
python3 -m astrid.core.pack.cli new my_pack --starter standalone --role action
```

This is the implemented pack CLI entrypoint for the F08 `packs new` route in
this checkout; `python3 -m astrid packs ...` is not a supported gateway command.

The starter writes v3 `pack.yaml`, an authored `docs/SKILL.md`, and only the
selected role folders. Keep the ordinary YAML `name` and `description`
frontmatter in that one skill. The manifest points to it with:

```yaml
documentation:
  kind: skill
  path: docs/SKILL.md
```

Link adjacent guides, references, and templates with normal relative Markdown
links. They are supporting documentation, not additional skills.

### Choose a starter journey

The existing F08 starter implementation is the source of truth; do not copy a
second template or schema into this guide. It lives in
[`astrid/core/pack/cli_basic.py`](../../astrid/core/pack/cli_basic.py), with
focused coverage in
[`tests/core/pack/test_cli_scaffold_f08.py`](../../tests/core/pack/test_cli_scaffold_f08.py).

- **Standalone:** the new pack owns its declaration and implementation. Add
  only the roles you use, for example `--role action --role shared`.
- **Wrapper:** leave the external repository unchanged. Add a thin adapter in
  the pack, name its normal dependency and importable public module, and use
  that repository’s normal install/package route. This does not vendor
  arbitrary upstream source.
- **Nested:** keep the upstream repository layout and put only Astrid
  integration under `integrations/<pack-id>/` (the `astrid` starter defaults to
  `integrations/astrid/`). Keep the parent repository’s normal build/install
  route and record a pinned source declaration with `pack_subpath`.
  `pack_subpath` identifies the integration root; it does not install
  dependencies.

For example:

```bash
python3 -m astrid.core.pack.cli new adapter_pack --starter wrapper \
  --role action --dependency clean-client --external-module clean_client
python3 -m astrid.core.pack.cli new astrid --starter nested \
  --role action --role ui
```

In all three journeys, declare public actions/UI/rendering entries in
`pack.yaml`; do not treat `actions/`, `ui/`, `rendering/`, or `shared/` as
implicit exports.

This page and [Pack Builder](../../astrid/packs/_core/docs/pack-builder/SKILL.md)
are the current v3 authoring route. The older
[pack protocol/reference page](../packs/creating-packs.md) is retained for
existing v1 packs and migration details; do not use its legacy layout as the
starting point for a new pack.

Validate the pack, then inspect the discovered declaration:

```bash
python3 -m astrid.core.pack.cli validate my_pack --json
python3 -m astrid.core.pack.cli inspect my_pack --pack-root .
```

`inspect` shows the declared documentation pointer and populated public role
sections. Validation checks structure; it does not install dependencies or
prove runtime behavior. Discover and invoke through the supported SDK/host.

After source edits, use Astrid’s existing sync/check against a disposable
harness state when needed. The composed view links the authored bundle; do not
author files in an installed view or perform a personal/global sync as part of
pack authoring.

[Choosing what to build](creating-tools.md) · [Pack contract](../packs/contract.md)

Have a useful finding rather than a reusable tool? [Contribute knowledge](contributing-knowledge.md).

[Back to Astrid](../../README.md)
