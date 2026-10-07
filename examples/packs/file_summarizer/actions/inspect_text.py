"""Public action for reading a selected UTF-8 text file."""

from __future__ import annotations

from pathlib import Path


def run(source: str) -> dict[str, str]:
    return {"text": Path(source).read_text(encoding="utf-8")}
