"""Validate a caller-authored summary without doing the caller's interpretation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def run(text: str, summary: Mapping[str, Any]) -> dict[str, Any]:
    lines = text.splitlines()
    words = text.split()
    expected = {
        "line_count": len(lines),
        "word_count": len(words),
        "char_count": len(text),
    }
    for key, value in expected.items():
        if type(summary.get(key)) is not int or summary[key] != value:
            raise ValueError(f"summary.{key} must equal {value}")
    notes = summary.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        raise ValueError("summary.notes must be a non-empty caller-authored sentence")
    return {**expected, "notes": notes.strip()}
