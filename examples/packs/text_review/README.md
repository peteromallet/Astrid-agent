# Text Review

This v3 teaching pack separates deterministic file analysis from review
judgment. `text_review.summarize_file` returns the machine-generated summary;
after reviewing it, the caller supplies a verdict to `text_review.file_audit`.

Validate the static pack contract from the repository root:

```bash
python3 -m astrid.core.pack.cli validate examples/packs/text_review
```

See [`docs/SKILL.md`](docs/SKILL.md) for the intended two-call review boundary.
The root `file_audit.py` is retained as a legacy orchestration teaching
artifact outside the v3 action catalog. Its fixture and event golden remain
unchanged; the v3 actions do not claim pause/ack lifecycle parity.
