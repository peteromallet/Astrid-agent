# File Summarizer

This teaching pack has a v3 action catalog for reading text, accepting a
caller-authored summary, and recording a verdict. Review remains an explicit
caller step between those actions.

Validate the pack's declared v3 surface from the repository root:

```bash
python3 -m astrid.core.pack.cli validate examples/packs/file_summarizer
```

The authored guidance is [`docs/SKILL.md`](docs/SKILL.md). The root
`simple_text_pipeline.py`, `document_pipeline.py`, `e2e_text_pipeline.py`, and
`text_summarizer.py` files remain as legacy `astrid.core.orchestrate` teaching
artifacts. Their pause/ack, nested-plan, and event behavior is outside the v3
catalog; the new actions do not claim lifecycle parity. The existing
`fixtures/` and `golden/` files are retained for those examples.
