# Legacy executor and orchestrator templates

The `executor/` and `orchestrator/` folders contain legacy descriptor examples
retained as reference material and inputs to schema-v2 compatibility
validation. New packs use the v3 authoring CLI.

Create a v3 action starter with:

```bash
python3 -m astrid.core.pack.cli new my_pack --starter standalone --role action --destination ./my_pack
```

The authoring CLI creates the v3 manifest, selected role files, and
`docs/SKILL.md` directly. These legacy folders do not supply its scaffolds.

See the [authoring guide](../guides/creating-tools.md#templates) and the
[legacy reference explanation](../packs/creating-packs.md#legacy-templates).
