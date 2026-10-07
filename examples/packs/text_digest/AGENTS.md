# Text Digest — Agent Guide

## When to Use This Pack

Use this guide when inspecting the retained schema-v2 text-pipeline examples.
For current v3 actions, start with [File Summarizer](../file_summarizer/README.md)
or [Text Review](../text_review/README.md).

## Legacy plans

[file_summary.py](file_summary.py) and [text_pipeline.py](text_pipeline.py)
demonstrate the earlier orchestration API: a code step stages a text file, an
attested step asks an agent to write and acknowledge a structured summary, and
a nested plan emits a verdict. Their source instructions describe that legacy
API rather than the current v3 action interface.

## Retained material

The Python plans, `fixtures/`, and `golden/` files remain legacy examples. The
v2 manifest continues to declare this `AGENTS.md` as its documentation; this
documentation correction does not migrate or register the plans as v3 actions.
See [README.md](README.md) for manifest validation and current authoring links.
