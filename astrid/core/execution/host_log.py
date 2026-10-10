"""Size-capped rotation for the pack host's stdout/stderr log.

The host log is not written through ``logging``: ``host_bootstrap`` opens
``generic-host.log`` and hands the file to the child as stdout and stderr.
Rotation therefore works on the file itself:

* the parent rotates the log before it spawns a host (fresh log per start);
* the running host watches its own fd 1/2 and, once the file passes the cap,
  renames it aside and ``dup2``s a new file onto fds 1 and 2 (no lost writes,
  no copy-truncate race).

Backups are ``generic-host.log.1`` (newest) .. ``.N`` (oldest). The cap comes
from ``ASTRID_HOST_LOG_MB`` (default 50, ``0`` disables rotation).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

HOST_LOG_NAME = "generic-host.log"
HOST_LOG_MB_ENV = "ASTRID_HOST_LOG_MB"
DEFAULT_HOST_LOG_MB = 50
DEFAULT_HOST_LOG_BACKUPS = 3
_WATCH_INTERVAL_SECONDS = 30.0


def host_log_limit_bytes(environ: Mapping[str, str] | None = None) -> int:
    """Return the rotation cap in bytes; ``0`` means rotation is disabled."""
    raw = (environ if environ is not None else os.environ).get(HOST_LOG_MB_ENV, "").strip()
    if not raw:
        megabytes = DEFAULT_HOST_LOG_MB
    else:
        try:
            megabytes = float(raw)
        except ValueError:
            megabytes = DEFAULT_HOST_LOG_MB
    if megabytes <= 0:
        return 0
    return int(megabytes * 1024 * 1024)


def _backup_path(path: Path, index: int) -> Path:
    return path.with_name(f"{path.name}.{index}")


def rotate_log(path: Path, *, backups: int = DEFAULT_HOST_LOG_BACKUPS) -> None:
    """Shift ``path`` to ``path.1`` and older backups up, dropping beyond ``backups``."""
    if backups < 1:
        path.unlink(missing_ok=True)
        return
    _backup_path(path, backups).unlink(missing_ok=True)
    for index in range(backups - 1, 0, -1):
        source = _backup_path(path, index)
        if source.exists():
            os.replace(source, _backup_path(path, index + 1))
    if path.exists():
        os.replace(path, _backup_path(path, 1))


def rotate_if_needed(path: Path, *, limit: int | None = None, backups: int = DEFAULT_HOST_LOG_BACKUPS) -> bool:
    """Rotate ``path`` when it is at or over the cap. Returns True when rotated."""
    cap = host_log_limit_bytes() if limit is None else limit
    if cap <= 0:
        return False
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return False
    if size < cap:
        return False
    rotate_log(path, backups=backups)
    return True


class StdioLogRotator:
    """Caps the log file that this process's fd 1 and fd 2 write to."""

    def __init__(self, path: Path, *, limit: int, backups: int = DEFAULT_HOST_LOG_BACKUPS) -> None:
        self.path = path
        self.limit = limit
        self.backups = backups

    def check(self) -> bool:
        """Rotate and redirect fds 1/2 if they still point at ``path`` and it is over the cap."""
        if self.limit <= 0:
            return False
        try:
            target = os.stat(self.path)
            current = os.fstat(1)
        except OSError:
            return False
        # Only rotate when fd 1 really is the log file; a terminal or pipe is left alone.
        if (current.st_dev, current.st_ino) != (target.st_dev, target.st_ino):
            return False
        if current.st_size < self.limit:
            return False
        rotate_log(self.path, backups=self.backups)
        fresh = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.dup2(fresh, 1)
            os.dup2(fresh, 2)
        finally:
            os.close(fresh)
        return True

    def start(self, interval: float = _WATCH_INTERVAL_SECONDS) -> threading.Thread:
        def _loop() -> None:
            while True:
                time.sleep(interval)
                try:
                    self.check()
                except OSError:
                    # Logging must never take the host down.
                    continue

        thread = threading.Thread(target=_loop, name="host-log-rotator", daemon=True)
        thread.start()
        return thread


def start_stdio_rotator(support_root: Path, *, environ: Mapping[str, str] | None = None) -> StdioLogRotator | None:
    limit = host_log_limit_bytes(environ)
    if limit <= 0:
        return None
    rotator = StdioLogRotator(Path(support_root) / HOST_LOG_NAME, limit=limit)
    rotator.start()
    return rotator


def host_log_section(support_root: Path | str | None) -> dict[str, Any]:
    """Read-only size report for ``astrid doctor``."""
    limit = host_log_limit_bytes()
    section: dict[str, Any] = {
        "path": None,
        "bytes": None,
        "limit_bytes": limit or None,
        "backups": DEFAULT_HOST_LOG_BACKUPS,
        "backup_bytes": 0,
        "over_limit": False,
    }
    if support_root is None:
        section["error"] = "support root not resolved"
        return section
    path = Path(support_root) / HOST_LOG_NAME
    section["path"] = str(path)
    try:
        section["bytes"] = path.stat().st_size if path.exists() else 0
    except OSError as exc:
        section["error"] = str(exc)
        return section
    section["backup_bytes"] = sum(
        _backup_path(path, index).stat().st_size
        for index in range(1, DEFAULT_HOST_LOG_BACKUPS + 1)
        if _backup_path(path, index).exists()
    )
    section["over_limit"] = bool(limit) and section["bytes"] >= limit
    return section
