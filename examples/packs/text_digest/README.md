# Text Digest

This schema-v2 example is retained as legacy compatibility material for
agent-in-the-loop text pipelines. Its Python modules illustrate the earlier
code, attested/ack, and nested-plan orchestration API; they are preserved with
their fixtures and golden files.

## Reading the example

See [the agent guide](AGENTS.md) and [file_summary.py](file_summary.py) for the
legacy read → attested summary → nested verdict sequence. This root lives
under `examples/packs/` and is excluded from ordinary runtime pack discovery.

For current v3 pack authoring, use the [File Summarizer](../file_summarizer/README.md)
or [Text Review](../text_review/README.md) examples and the
[pack authoring guide](../../../docs/guides/create-a-pack.md).

To validate this retained manifest, run from the repository root:

```bash
python3 -m astrid.core.pack.cli validate examples/packs/text_digest
```
