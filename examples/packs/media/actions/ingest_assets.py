"""Public action implementation for the Media teaching pack."""

from __future__ import annotations

from pathlib import Path


def run(source: str) -> dict[str, object]:
    """Return the sorted names of files directly inside ``source``."""
    root = Path(source)
    files = sorted(path.name for path in root.iterdir() if path.is_file()) if root.is_dir() else []
    return {"file_count": len(files), "assets": files}
