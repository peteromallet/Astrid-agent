"""Deterministic summary action for the Text Review teaching pack."""

from __future__ import annotations

from pathlib import Path


def run(source: str) -> dict[str, object]:
    text = Path(source).read_text(encoding="utf-8")
    lines = text.splitlines()
    words = text.split()
    return {
        "line_count": len(lines),
        "word_count": len(words),
        "char_count": len(text),
        "avg_word_len": round(sum(len(word) for word in words) / max(len(words), 1), 2),
        "first_line": lines[0] if lines else "",
        "preview": text[:200],
    }
