"""Public ingestion action for the Minimal teaching pack."""

from __future__ import annotations

from pathlib import Path


def run(source: str = ".") -> dict[str, object]:
    """Return a sorted inventory of the entries directly inside ``source``."""
    root = Path(source)
    entries = sorted(path.name for path in root.iterdir()) if root.is_dir() else []
    return {"item_count": len(entries), "assets": entries}
