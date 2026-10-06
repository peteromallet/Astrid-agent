"""Bounded composed-frame capture, cache, and worker lifecycle.

The visualizer plans cards before it asks the renderer for pixels.  This module
keeps that seam small: exact renders still use ffmpeg extraction, while a
capture authority asks the trusted Remotion backend for only the planned
frames.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from threading import Timer
from typing import Any

RENDERER_IDENTITY = "astrid.remotion.frame-capture.v1|backend=1.0.0|remotion=4.0.509|fonts=local-bundle-v1"
DEFAULT_IDLE_SECONDS = 45.0
DEFAULT_MAX_ENTRIES = 256
DEFAULT_MAX_BYTES = 512 * 1024 * 1024


def frame_cache_key(
    snapshot: Mapping[str, Any],
    frame: int,
    resolution: Sequence[int] | None,
    *,
    renderer_identity: str = RENDERER_IDENTITY,
) -> str:
    """Return a content key for one exact composition/frame request."""
    identity = {
        "composition": snapshot,
        "frame": int(frame),
        "resolution": list(resolution) if resolution is not None else None,
        "renderer": renderer_identity,
        "environment": {
            "project_dir": os.environ.get("ASTRID_REMOTION_PROJECT_DIR"),
            "node": os.environ.get("ASTRID_NODE_EXECUTABLE"),
            "theme": os.environ.get("ASTRID_ACTIVE_THEME"),
        },
    }
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def default_cache_root() -> Path:
    configured = os.environ.get("ASTRID_TIMELINE_FRAME_CACHE")
    return Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / "astrid-timeline-frame-cache"


class FrameCaptureCache:
    """Small, bounded, content-addressed PNG cache."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.root = (root or default_cache_root()).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_entries = max(1, int(max_entries))
        self.max_bytes = max(1, int(max_bytes))
        self._lock = threading.RLock()

    def path_for(self, key: str) -> Path:
        if not key or any(ch not in "0123456789abcdef" for ch in key):
            raise ValueError("invalid frame cache key")
        return self.root / f"{key}.png"

    def read(self, key: str) -> bytes | None:
        path = self.path_for(key)
        with self._lock:
            try:
                data = path.read_bytes()
            except OSError:
                return None
            os.utime(path, None)
            return data

    def write(self, key: str, data: bytes) -> Path:
        path = self.path_for(key)
        temporary = path.with_suffix(f".{threading.get_ident()}.tmp")
        with self._lock:
            temporary.write_bytes(data)
            os.replace(temporary, path)
            self.prune()
        return path

    def prune(self) -> None:
        with self._lock:
            files = [path for path in self.root.glob("*.png") if path.is_file()]
            files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            total = 0
            keep: list[Path] = []
            for path in files:
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if len(keep) >= self.max_entries or total + size > self.max_bytes:
                    path.unlink(missing_ok=True)
                    continue
                keep.append(path)
                total += size


class FrameCaptureWorker:
    """Serialize capture batches and retire the worker after idle time.

    The callable is intentionally injected. Production uses one Remotion CLI
    image-sequence invocation per batch; tests can supply a deterministic
    renderer without starting Chromium.
    """

    def __init__(
        self,
        renderer: Callable[[Sequence[int], Path, Sequence[int] | None], Mapping[int, Path]],
        *,
        idle_seconds: float | None = None,
    ) -> None:
        self.renderer = renderer
        raw_idle = os.environ.get("ASTRID_TIMELINE_FRAME_IDLE_SECONDS") if idle_seconds is None else idle_seconds
        self.idle_seconds = max(0.0, float(DEFAULT_IDLE_SECONDS if raw_idle is None else raw_idle))
        self._lock = threading.RLock()
        self._timer: Timer | None = None
        self._closed = False
        self.started = False
        self.capture_batches = 0

    def _expire(self) -> None:
        with self._lock:
            self.started = False
            self._timer = None

    def _arm(self) -> None:
        if self.idle_seconds <= 0:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer = Timer(self.idle_seconds, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def capture(
        self,
        frames: Sequence[int],
        output_dir: Path,
        resolution: Sequence[int] | None,
    ) -> Mapping[int, Path]:
        requested = tuple(sorted(set(int(frame) for frame in frames)))
        if not requested:
            return {}
        with self._lock:
            if self._closed:
                raise RuntimeError("frame capture worker is closed")
            self.started = True
            self.capture_batches += 1
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            result = self.renderer(requested, output_dir, resolution)
            self._arm()
            return result

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self.started = False


class RemotionFrameProvider:
    """Provide PNG frames from an admitted, immutable capture snapshot."""

    def __init__(
        self,
        snapshot: Mapping[str, Any],
        *,
        timeline_path: Path,
        assets_path: Path,
        project_dir: Path,
        materialized_root: Path | None = None,
        materialized_objects: Mapping[str, str] | None = None,
        cache: FrameCaptureCache | None = None,
        renderer: Callable[[Sequence[int], Path, Sequence[int] | None], Mapping[int, Path]] | None = None,
        idle_seconds: float | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.timeline_path = Path(timeline_path)
        self.assets_path = Path(assets_path)
        self.project_dir = Path(project_dir)
        self.materialized_root = Path(materialized_root) if materialized_root else None
        self.materialized_objects = materialized_objects
        self.cache = cache or FrameCaptureCache()
        if renderer is None:
            from astrid.packs.rendering.backends.remotion.run import capture_remotion_frames

            def renderer(frames, output_dir, resolution):
                return capture_remotion_frames(
                    self.timeline_path,
                    self.assets_path,
                    output_dir,
                    frames,
                    project_dir=self.project_dir,
                    materialized_root=self.materialized_root,
                    materialized_objects=self.materialized_objects,
                    frame_resolution=tuple(resolution) if resolution is not None else None,
                )

        self.worker = FrameCaptureWorker(renderer, idle_seconds=idle_seconds)

    def close(self) -> None:
        self.worker.close()

    def capture(
        self,
        cards: Sequence[Mapping[str, Any]],
        out_root: Path,
        resolution: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        out_root = Path(out_root)
        frames_dir = out_root / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        misses: list[tuple[int, str, Path]] = []
        cached = 0
        for card in cards:
            frame = int(card["frame"])
            key = frame_cache_key(self.snapshot, frame, resolution)
            destination = out_root / str(card["image"])
            data = self.cache.read(key)
            if data is not None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                cached += 1
            else:
                misses.append((frame, key, destination))
        fresh = 0
        temporary = None
        succeeded = False
        try:
            if misses:
                temporary = Path(tempfile.mkdtemp(prefix="astrid-frame-capture-", dir=out_root.parent))
                produced = self.worker.capture(
                    [frame for frame, _key, _destination in misses], temporary, resolution
                )
                for frame, key, destination in misses:
                    source = Path(produced[frame])
                    data = source.read_bytes()
                    self.cache.write(key, data)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                    fresh += 1
            succeeded = True
        finally:
            if temporary is not None:
                shutil.rmtree(temporary, ignore_errors=True)
            if not succeeded:
                # Failed/cancelled batches must not leave a live worker behind.
                # Successful batches remain paused behind their idle timer so a
                # follow-up request can reuse the worker.
                self.close()
        return {
            "evidence_source": "cache" if fresh == 0 else "fresh_capture" if cached == 0 else "mixed",
            "renderer": RENDERER_IDENTITY,
            "cached_frames": cached,
            "fresh_frames": fresh,
            "requested_frames": [int(card["frame"]) for card in cards],
            "resolution": list(resolution) if resolution is not None else None,
            "cache_root": str(self.cache.root),
            "worker": {
                "batches": self.worker.capture_batches,
                "idle_seconds": self.worker.idle_seconds,
                "serialized": True,
            },
        }
