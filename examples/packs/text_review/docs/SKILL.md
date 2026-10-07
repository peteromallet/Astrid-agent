---
name: text_review
description: Use this example pack to separate deterministic text analysis from caller-authored review judgment.
---

# Text Review Example Pack

Call `text_review.summarize_file` to read a file and compute its counts and
preview. Review that result, then call `text_review.file_audit` with the
summary and a one-line caller-authored verdict. The review boundary belongs to
the caller; this pack does not implement a paused or resumable session.

The root `file_audit.py` module is retained as a legacy `astrid.core.orchestrate`
teaching artifact outside the v3 action catalog. It demonstrates the old
code → code → attested arrangement and does not have lifecycle parity with the
separate v3 actions. Its original fixture and golden event trace remain
available as historical teaching data.
