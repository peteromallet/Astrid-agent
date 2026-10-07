"""Return the example's deterministic verdict or caller-authored judgment."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def run(summary: Mapping[str, Any], judgment: str | None = None) -> dict[str, str]:
    word_count = summary.get("word_count")
    if type(word_count) is not int or word_count < 0:
        raise ValueError("summary.word_count must be a non-negative integer")
    if judgment is not None:
        verdict = judgment.strip()
        if not verdict:
            raise ValueError("judgment must be non-empty when supplied")
    else:
        verdict = "PASS" if word_count > 3 else "NEEDS_MORE_WORDS"
    return {"verdict": verdict}
