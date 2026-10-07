# Text Review — Agent Guide

Use `text_review.summarize_file` for deterministic counts, then review its
result before calling `text_review.file_audit` with a one-line judgment. The
caller owns the review boundary; the v3 actions do not hold a session open.

The root `file_audit.py`, fixture, and golden trace are retained as explicitly
legacy orchestration teaching material outside the v3 action catalog. The
authored pack guide is `docs/SKILL.md`.
