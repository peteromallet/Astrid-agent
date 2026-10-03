"""Bounded test writer tree with backend and browser session escapes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    role, output_raw, registry_raw = sys.argv[1:4]
    output = Path(output_raw)
    registry = Path(registry_raw)
    registry.mkdir(parents=True, exist_ok=True)
    (registry / f"{role}.pid").write_text(str(os.getpid()))
    deadline = time.monotonic() + 15
    if role == "worker":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while time.monotonic() < deadline:
            (output / "writer.txt").write_text("owned fixture writer")
            (registry / "last-write.txt").write_text(str(time.monotonic_ns()))
            time.sleep(0.01)
        return

    child_role = {"pack": "backend", "backend": "node", "node": "browser", "browser": "worker"}[role]
    child = subprocess.Popen(
        [sys.executable, __file__, child_role, str(output), str(registry)],
        start_new_session=child_role in {"backend", "browser"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Keep ancestors available while the stubborn leaf requires escalation.
    # Reap ordinary TERM exits; the 15 s bound also protects failed test runs.
    signal.signal(signal.SIGTERM, lambda *_: None)
    try:
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=2)


if __name__ == "__main__":
    main()
