"""Element motion from the element itself, with ``model.py`` as the fallback.

An element folder may ship ``motion.ts`` exporting ``motionAt(params, clipFrame,
fps) -> {prop: number}`` (am-presenter, am-snap-plate). The component draws with
the same functions, so these values are the element's motion, not a copy of it.
visualize evaluates them once per run, for every frame of every such clip, with
the renderer's own Node and esbuild (~0.1 s). Without Node the hand mirror in
``model.py`` answers instead and ``note`` says why.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

PACKS_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = Path(__file__).with_name("element_motion.mjs")
MOTION_FILE = "motion.ts"
TIMEOUT_S = 30.0
MAX_FRAMES = 20_000  # per clip; a longer clip falls back to the mirror


def modules(root: Path | None = None) -> dict[str, Path]:
    """``{element type: motion.ts}`` for every pack element that ships one."""
    found: dict[str, Path] = {}
    for path in sorted((root or PACKS_ROOT).glob(f"*/elements/*/*/{MOTION_FILE}")):
        if (path.parent / "element.yaml").is_file():
            found.setdefault(path.parent.name, path)
    return found


class Unavailable(RuntimeError):
    """The element maths could not be evaluated here; the caller uses the mirror."""


def _runtime() -> tuple[str, Path]:
    node = (os.environ.get("ASTRID_NODE_EXECUTABLE") or "").strip() or shutil.which("node")
    if not node or not Path(node).is_file():
        raise Unavailable("no Node (ASTRID_NODE_EXECUTABLE unset)")
    from astrid.core.rendering.remotion_runtime import resolve_remotion_project_dir

    project = resolve_remotion_project_dir()
    if not (project / "node_modules" / "esbuild").is_dir():
        raise Unavailable(f"no esbuild in {project}")
    return node, project


def evaluate(requests: list[Mapping[str, Any]], types: Mapping[str, Path], *, timeout: float = TIMEOUT_S) -> dict[str, list[dict]]:
    """Run ``motionAt`` for each request (``id, type, params, fps, frames``); raises ``Unavailable``."""
    if not requests:
        return {}
    node, project = _runtime()
    payload = json.dumps({"modules": {k: str(v) for k, v in types.items()}, "requests": list(requests)}, default=str)
    try:
        done = subprocess.run([node, str(SCRIPT), str(project)], input=payload, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unavailable(f"node failed: {exc}") from exc
    if done.returncode != 0:
        tail = (done.stderr.strip().splitlines() or ["no output"])[-1]
        raise Unavailable(f"node exited {done.returncode}: {tail[:160]}")
    try:
        result = json.loads(done.stdout)
    except ValueError as exc:
        raise Unavailable("node returned no JSON") from exc
    if result.get("errors") and not result.get("values"):
        raise Unavailable("; ".join(f"{k}: {v}" for k, v in result["errors"].items())[:200])
    return {str(k): [dict(row) for row in rows] for k, rows in (result.get("values") or {}).items()}


class ElementMaths:
    """One lazy batch for every clip of a run whose element ships ``motion.ts``."""

    def __init__(self, elements: Iterable[Any], types: Mapping[str, Path] | None = None) -> None:
        self.types = dict(modules() if types is None else types)
        self.elements = [e for e in elements if e.type in self.types and not getattr(e, "audio", False)]
        self.values: dict[str, list[dict]] | None = None
        self.fps: float | None = None
        self.note: str | None = None  # why the mirror answered, when it did

    def wants(self, element: Any) -> bool:
        return element.type in self.types

    def _load(self, fps: float) -> None:
        self.fps = fps
        requests = []
        for element in self.elements:
            frames = int(math.ceil((element.end - element.start) * fps - 1e-6)) + 1
            if 0 < frames <= MAX_FRAMES:
                requests.append({"id": element.key, "type": element.type, "params": dict(element.params),
                                 "fps": fps, "frames": frames})
        try:
            self.values = evaluate(requests, {e.type: self.types[e.type] for e in self.elements})
        except Unavailable as exc:
            self.values, self.note = {}, str(exc)

    def at(self, element: Any, frame: int, fps: float | None = None) -> dict[str, float] | None:
        """The element's own values on a clip frame, or ``None`` (use the mirror)."""
        if not self.wants(element):
            return None
        if self.values is None or (fps is not None and self.fps is not None and abs(fps - self.fps) > 1e-6):
            self._load(float(fps or self.fps or 30.0))
        rows = (self.values or {}).get(element.key)
        if rows is None or not 0 <= frame < len(rows):
            return None
        return {k: float(v) for k, v in rows[frame].items() if isinstance(v, (int, float))}
