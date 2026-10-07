---
name: file_summarizer
description: Use this example pack to inspect text, validate a caller-authored summary, and record a verdict after review.
---

# File Summarizer Example Pack

The current v3 catalog declares three ordinary actions:

1. `file_summarizer.inspect_text` reads a caller-selected UTF-8 file.
2. `file_summarizer.accept_summary` checks the supplied line, word, and
   character counts and keeps the caller-authored notes.
3. `file_summarizer.write_verdict` returns the example's deterministic
   word-count verdict or a judgment supplied by the caller.

The caller controls the review boundary: inspect the text, author and submit a
summary, review the accepted summary, then invoke the verdict action. These
actions do not implement a paused or resumable session.

The root Python modules `simple_text_pipeline.py`, `document_pipeline.py`,
`e2e_text_pipeline.py`, and `text_summarizer.py` are retained as legacy
`astrid.core.orchestrate` teaching artifacts. Their historical pause/ack,
nested-plan and event identifiers remain outside the v3 action catalog. They
are not v3 entrypoints and the v3 actions do not claim lifecycle parity with
those plans. The `fixtures/` and `golden/` files are retained as historical
teaching data.

See [the verdict action guide](../actions/write_verdict/STAGE.md) for the
caller-controlled review step.
