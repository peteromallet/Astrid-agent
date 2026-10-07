"""Caller-controlled review action for the Text Review teaching pack."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def run(summary: Mapping[str, Any], verdict: str) -> dict[str, Any]:
    if type(summary.get("line_count")) is not int:
        raise ValueError("summary.line_count must be an integer")
    if type(summary.get("word_count")) is not int:
        raise ValueError("summary.word_count must be an integer")
    if type(summary.get("char_count")) is not int:
        raise ValueError("summary.char_count must be an integer")
    normalized = verdict.strip()
    if not normalized:
        raise ValueError("verdict must be a non-empty caller-authored assessment")
    return {"summary": dict(summary), "verdict": normalized}
