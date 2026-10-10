"""Size cap and reclaim for the durable ``timelines visualize`` evidence cache.

Every visualize capture is materialized host-side into
``<data root>/timeline-visualize/<project>/<bundle digest>/``. Nothing removed
those directories, so the cache grew without bound. This module owns the
directory layout's retention: an automatic least-recently-used cap that runs
after each materialization, and the explicit prune used by the operator verb
and by ``doctor``'s size report.

Eviction only ever touches complete digest directories (64 hex characters)
directly under ``<base>/<project>/``. Staging directories, other files and
anything outside that shape are never removed.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

CACHE_MB_ENV = "ASTRID_VISUALIZE_CACHE_MB"
DEFAULT_CACHE_MB = 500
DEFAULT_MAX_DIRS = 200
# A capture touched within this window may still be referenced by an in-flight
# task, so it is never evicted, whatever the limits say.
GUARD_SECONDS = 900

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CaptureDir:
    path: Path
    bytes: int
    mtime: float


@dataclass
class PruneReport:
    base: Path
    max_bytes: int
    max_dirs: int
    before_bytes: int
    before_dirs: int
    removed: list[CaptureDir] = field(default_factory=list)
    guarded: list[CaptureDir] = field(default_factory=list)
    protected: list[Path] = field(default_factory=list)

    @property
    def freed_bytes(self) -> int:
        return sum(item.bytes for item in self.removed)

    @property
    def after_bytes(self) -> int:
        return self.before_bytes - self.freed_bytes

    @property
    def after_dirs(self) -> int:
        return self.before_dirs - len(self.removed)

    def as_dict(self) -> dict:
        return {
            "base": str(self.base),
            "limits": {"max_bytes": self.max_bytes, "max_dirs": self.max_dirs, "guard_seconds": GUARD_SECONDS},
            "before": {"bytes": self.before_bytes, "dirs": self.before_dirs},
            "after": {"bytes": self.after_bytes, "dirs": self.after_dirs},
            "freed_bytes": self.freed_bytes,
            "removed": [{"path": str(i.path), "bytes": i.bytes} for i in self.removed],
            "guarded": [{"path": str(i.path), "bytes": i.bytes} for i in self.guarded],
            "protected": [str(p) for p in self.protected],
        }


def max_cache_bytes(env: Mapping[str, str] | None = None, *, override_mb: float | None = None) -> int:
    """Return the byte cap: an explicit MB override, else the env var, else the default."""
    if override_mb is not None:
        if override_mb < 0:
            raise ValueError("cache limit must be zero or more megabytes")
        return int(override_mb * 1024 * 1024)
    raw = (os.environ if env is None else env).get(CACHE_MB_ENV, "").strip()
    if not raw:
        return DEFAULT_CACHE_MB * 1024 * 1024
    try:
        mb = float(raw)
    except ValueError as exc:
        raise ValueError(f"{CACHE_MB_ENV} must be a number of megabytes") from exc
    if mb < 0:
        raise ValueError(f"{CACHE_MB_ENV} must be zero or more")
    return int(mb * 1024 * 1024)


def _tree_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def list_capture_dirs(base: Path) -> list[CaptureDir]:
    """Every ``<base>/<project>/<digest>`` capture directory, least recent first."""
    found: list[CaptureDir] = []
    if not base.is_dir():
        return found
    for project in base.iterdir():
        if project.is_symlink() or not project.is_dir():
            continue
        for capture in project.iterdir():
            if capture.is_symlink() or not capture.is_dir() or not _DIGEST_RE.match(capture.name):
                continue
            try:
                mtime = capture.stat().st_mtime
            except OSError:
                continue
            found.append(CaptureDir(path=capture, bytes=_tree_bytes(capture), mtime=mtime))
    found.sort(key=lambda item: (item.mtime, str(item.path)))
    return found


def cache_size(base: Path, *, max_bytes: int | None = None, max_dirs: int = DEFAULT_MAX_DIRS) -> dict:
    """Read-only size summary for ``doctor``."""
    captures = list_capture_dirs(base)
    total = sum(item.bytes for item in captures)
    return {
        "path": str(base),
        "bytes": total,
        "dirs": len(captures),
        "limit_bytes": max_bytes if max_bytes is not None else max_cache_bytes(),
        "limit_dirs": max_dirs,
        "over_limit": total > (max_bytes if max_bytes is not None else max_cache_bytes()) or len(captures) > max_dirs,
    }


def prune_visualize_cache(
    base: Path,
    *,
    max_bytes: int | None = None,
    max_dirs: int = DEFAULT_MAX_DIRS,
    protect: Iterable[Path] = (),
    now: float | None = None,
    guard_seconds: int = GUARD_SECONDS,
) -> PruneReport:
    """Evict least-recently-used capture directories until within both limits.

    ``protect`` names directories that must survive (the capture just produced).
    Any capture modified within ``guard_seconds`` is also kept, since it may
    back an in-flight task. The newest capture is therefore always kept.
    """
    limit_bytes = max_cache_bytes() if max_bytes is None else max_bytes
    clock = time.time() if now is None else now
    protected = {Path(p).resolve() for p in protect}
    captures = list_capture_dirs(base)
    report = PruneReport(
        base=base,
        max_bytes=limit_bytes,
        max_dirs=max_dirs,
        before_bytes=sum(item.bytes for item in captures),
        before_dirs=len(captures),
        protected=sorted(protected),
    )
    total_bytes = report.before_bytes
    total_dirs = report.before_dirs
    # Oldest first; stop as soon as both limits hold.
    for item in captures:
        if total_bytes <= limit_bytes and total_dirs <= max_dirs:
            break
        if item.path.resolve() in protected or clock - item.mtime < guard_seconds:
            report.guarded.append(item)
            continue
        shutil.rmtree(item.path, ignore_errors=True)
        if item.path.exists():
            report.guarded.append(item)
            continue
        report.removed.append(item)
        total_bytes -= item.bytes
        total_dirs -= 1
    return report


def touch_capture(path: Path) -> None:
    """Record a capture as most recently used so LRU ordering reflects production."""
    try:
        os.utime(path, None)
    except OSError:
        pass


def format_report(report: PruneReport) -> list[str]:
    def mb(value: int) -> str:
        return f"{value / (1024 * 1024):.1f} MB"

    lines = [
        f"visualize cache: {report.base}",
        f"limits: {mb(report.max_bytes)} / {report.max_dirs} dirs (guard {GUARD_SECONDS // 60} min)",
        f"before: {mb(report.before_bytes)} in {report.before_dirs} dirs",
    ]
    for item in report.removed:
        lines.append(f"removed: {item.path} ({mb(item.bytes)})")
    if not report.removed:
        lines.append("removed: nothing (within limits or every capture is protected or recent)")
    if report.guarded:
        lines.append(f"kept by guard or protection: {len(report.guarded)} dirs")
    lines.append(f"freed: {mb(report.freed_bytes)} ({report.freed_bytes} bytes)")
    lines.append(f"after: {mb(report.after_bytes)} in {report.after_dirs} dirs")
    return lines
