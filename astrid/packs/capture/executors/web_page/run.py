#!/usr/bin/env python3
"""Capture a real web page to a PNG plus a JSON receipt (capture.web_page).

Drives a headless Chromium over the DevTools protocol using the pipe transport
(``--remote-debugging-pipe``), so the only dependency is the standard library
plus the Chromium binary that Remotion already installs. When the host admits a
broker proxy for this task (``ASTRID_BROKER_PROXY``), Chromium is pointed at it,
so the upstream route set is enforced by the host, not by this process.
"""

from __future__ import annotations

from astrid.core.pack.entrypoint import guard_canonical_entrypoint, run_pack_main

guard_canonical_entrypoint("capture.web_page")

import argparse
import base64
import json
import os
import re
import select
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from astrid.core._shared.result_manifest import build_manifest, write_manifest
from astrid.core.contracts.errors import AstridError

DEFAULT_VIEWPORT = (1600, 1000)
DEFAULT_DEVICE_SCALE = 2
DEFAULT_WAIT_MS = 1500
NAV_TIMEOUT_S = 45.0
CHROME_ENV = "ASTRID_CAPTURE_CHROME"
PROXY_ENV = "ASTRID_BROKER_PROXY"
REPO_ROOT = Path(__file__).resolve().parents[5]
REMOTION_SHELL_GLOB = "remotion/node_modules/.remotion/chrome-headless-shell/*/*/chrome-headless-shell"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


# ---------------------------------------------------------------------------
# Input parsing and validation
# ---------------------------------------------------------------------------


def _boolean(value: str) -> bool:
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {value!r}")


def parse_viewport(value: str | None) -> tuple[int, int]:
    if value is None or str(value).strip() == "":
        return DEFAULT_VIEWPORT
    match = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", str(value))
    if not match:
        raise AstridError(f"viewport must be WIDTHxHEIGHT, got {value!r}")
    width, height = int(match.group(1)), int(match.group(2))
    if not (16 <= width <= 8000 and 16 <= height <= 8000):
        raise AstridError(f"viewport {width}x{height} is outside 16..8000 px")
    return width, height


def parse_clip(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"none", "off", "null"}:
            return None
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AstridError(f"clip must be JSON {{x,y,w,h}}, got {text!r}") from exc
    if not isinstance(value, dict):
        raise AstridError("clip must be an object with x, y, w, h (CSS px of the viewport)")
    try:
        x, y, w, h = (int(value[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError) as exc:
        raise AstridError("clip needs integer x, y, w and h") from exc
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        raise AstridError("clip needs x,y >= 0 and w,h > 0")
    return {"x": x, "y": y, "w": w, "h": h}


def validate_url(url: str, *, allow_local: bool = False) -> str:
    """Accept https URLs only. ``allow_local`` admits loopback http, for tests."""
    text = (url or "").strip()
    parts = urlsplit(text)
    if parts.scheme == "file" and allow_local:
        return text
    host = (parts.hostname or "").lower()
    if not host:
        raise AstridError(f"url has no host: {url!r}")
    if parts.username or parts.password:
        raise AstridError("url must not carry credentials")
    if parts.scheme == "https":
        return text
    if allow_local and parts.scheme == "http" and host in _LOOPBACK:
        return text
    raise AstridError(f"url must be https, got {parts.scheme}://{host}")


def find_chrome() -> str:
    """Locate the headless Chromium: env override first, then Remotion's copy."""
    override = os.environ.get(CHROME_ENV, "").strip()
    if override:
        if not os.access(override, os.X_OK):
            raise AstridError(f"{CHROME_ENV}={override} is not executable")
        return override
    matches = sorted(REPO_ROOT.glob(REMOTION_SHELL_GLOB))
    if matches:
        return str(matches[-1])
    raise AstridError(
        "no headless Chromium found",
        recovery_command=f"set {CHROME_ENV} to a Chromium binary, or install Remotion's browser (npx remotion browser ensure in remotion/)",
    )


# ---------------------------------------------------------------------------
# Minimal DevTools protocol client over the pipe transport
# ---------------------------------------------------------------------------


class _Cdp:
    """JSON messages, each terminated by a NUL byte, on fds 3 (in) and 4 (out)."""

    def __init__(self, write_fd: int, read_fd: int) -> None:
        self._w = write_fd
        self._r = read_fd
        self._buf = b""
        self._next_id = 0
        self.events: list[dict[str, Any]] = []

    def send(self, method: str, params: dict[str, Any] | None = None, session_id: str | None = None) -> int:
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        data = json.dumps(message).encode("utf-8") + b"\0"
        view = memoryview(data)
        while view:
            written = os.write(self._w, view)
            view = view[written:]
        return self._next_id

    def _read_one(self, deadline: float) -> dict[str, Any]:
        while b"\0" not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AstridError("timed out waiting for the browser")
            ready, _, _ = select.select([self._r], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(self._r, 1 << 16)
            if not chunk:
                raise AstridError("the browser closed its debugging pipe unexpectedly")
            self._buf += chunk
        raw, _, self._buf = self._buf.partition(b"\0")
        return json.loads(raw.decode("utf-8"))

    def call(self, method: str, params: dict[str, Any] | None = None, session_id: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        msg_id = self.send(method, params, session_id)
        while True:
            message = self._read_one(deadline)
            if message.get("id") == msg_id:
                if "error" in message:
                    raise AstridError(f"browser refused {method}: {message['error'].get('message', message['error'])}")
                return message.get("result", {})
            self._keep(message)

    def pump(self, seconds: float) -> None:
        """Read (and keep) events for a while, so the browser never blocks on a full pipe."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                self._keep(self._read_one(deadline))
            except AstridError as exc:
                if "timed out" in str(exc):
                    return
                raise

    def wait_for(self, predicate, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while True:
            for event in self.events:
                if predicate(event):
                    return event
            if time.monotonic() >= deadline:
                return None
            try:
                self._keep(self._read_one(deadline))
            except AstridError as exc:
                if "timed out" in str(exc):
                    return None
                raise

    def _keep(self, message: dict[str, Any]) -> None:
        if "method" in message:
            self.events.append(message)
            if len(self.events) > 5000:
                del self.events[:1000]


def _chrome_args(chrome: str, profile: Path, proxy: str | None) -> list[str]:
    args = [
        chrome,
        "--remote-debugging-pipe",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-extensions",
        "--disable-gpu",
        "--hide-scrollbars",
        "--mute-audio",
        "--font-render-hinting=none",
    ]
    if proxy:
        args.append(f"--proxy-server={proxy}")
    args.append("about:blank")
    return args


def _launch(chrome: str, profile: Path, proxy: str | None, log_path: Path) -> tuple[subprocess.Popen, _Cdp]:
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()

    def _wire_fds() -> None:
        # Pipe ends are non-inheritable. Duplicate first, so a pipe end that
        # already sits on 3 or 4 is not clobbered, then mark 3 and 4 inheritable
        # (dup2 onto the same number would keep CLOEXEC). close_fds stays False.
        a = os.dup(in_r)
        b = os.dup(out_w)
        os.dup2(a, 3)
        os.dup2(b, 4)
        os.set_inheritable(3, True)
        os.set_inheritable(4, True)

    try:
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(
                _chrome_args(chrome, profile, proxy),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                close_fds=False,
                preexec_fn=_wire_fds,
            )
    except OSError as exc:
        os.close(in_r); os.close(in_w); os.close(out_r); os.close(out_w)
        raise AstridError(f"could not start Chromium: {exc}") from exc
    os.close(in_r)
    os.close(out_w)
    return proc, _Cdp(in_w, out_r)


def _shutdown(proc: subprocess.Popen, cdp: _Cdp) -> None:
    try:
        cdp.call("Browser.close", timeout=5)
    except (AstridError, OSError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    for fd in (cdp._r, cdp._w):
        try:
            os.close(fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def capture(
    *,
    url: str,
    out_dir: Path,
    viewport: tuple[int, int] = DEFAULT_VIEWPORT,
    device_scale: int = DEFAULT_DEVICE_SCALE,
    full_page: bool = False,
    wait_ms: int = DEFAULT_WAIT_MS,
    clip: dict[str, int] | None = None,
    dark_mode: bool = False,
    allow_local: bool = False,
    chrome: str | None = None,
) -> dict[str, Any]:
    """Capture one page. Writes screenshot.png and receipt.json into ``out_dir``."""
    url = validate_url(url, allow_local=allow_local)
    if not (1 <= int(device_scale) <= 3):
        raise AstridError("device_scale must be 1, 2 or 3")
    if not (0 <= int(wait_ms) <= 30000):
        raise AstridError("wait_ms must be between 0 and 30000")
    width, height = viewport
    binary = chrome or find_chrome()
    proxy = os.environ.get(PROXY_ENV) or None
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="capture-web-page-"))
    proc: subprocess.Popen | None = None
    try:
        proc, cdp = _launch(binary, profile, proxy, profile / "browser.log")
        browser = cdp.call("Browser.getVersion", timeout=30)
        user_agent = str(browser.get("userAgent", ""))
        target_id = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = cdp.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})["sessionId"]
        cdp.call("Page.enable", session_id=session)
        cdp.call("Network.enable", session_id=session)
        cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": int(device_scale), "mobile": False},
            session_id=session,
        )
        if dark_mode:
            cdp.call(
                "Emulation.setEmulatedMedia",
                {"features": [{"name": "prefers-color-scheme", "value": "dark"}]},
                session_id=session,
            )

        nav = cdp.call("Page.navigate", {"url": url}, session_id=session, timeout=NAV_TIMEOUT_S)
        if nav.get("errorText"):
            raise AstridError(
                f"navigation to {url} failed: {nav['errorText']}",
                recovery_command="check the URL is reachable from the host; a blocked host or redirect shows here as a tunnel error",
            )
        loader_id = nav.get("loaderId")
        loaded = cdp.wait_for(
            lambda e: e.get("method") == "Page.loadEventFired" and e.get("sessionId") == session,
            timeout=NAV_TIMEOUT_S,
        )
        if loaded is None:
            raise AstridError(f"page {url} did not finish loading within {int(NAV_TIMEOUT_S)} s")
        cdp.pump(int(wait_ms) / 1000.0)

        http_status = None
        for event in cdp.events:
            if event.get("method") != "Network.responseReceived" or event.get("sessionId") != session:
                continue
            params = event.get("params", {})
            if params.get("type") != "Document":
                continue
            if loader_id and params.get("loaderId") != loader_id:
                continue
            http_status = params.get("response", {}).get("status")

        probe = cdp.call(
            "Runtime.evaluate",
            {"expression": "({title: document.title, href: location.href})", "returnByValue": True},
            session_id=session,
        )
        page = probe.get("result", {}).get("value") or {}

        if clip is not None:
            shot_clip: dict[str, Any] | None = {"x": clip["x"], "y": clip["y"], "width": clip["w"], "height": clip["h"], "scale": 1}
            beyond = True
        elif full_page:
            metrics = cdp.call("Page.getLayoutMetrics", session_id=session)
            size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
            shot_clip = {"x": 0, "y": 0, "width": float(size.get("width", width)), "height": float(size.get("height", height)), "scale": 1}
            beyond = True
        else:
            shot_clip = None
            beyond = False
        params: dict[str, Any] = {"format": "png", "captureBeyondViewport": beyond}
        if shot_clip is not None:
            params["clip"] = shot_clip
        shot = cdp.call("Page.captureScreenshot", params, session_id=session, timeout=60)
        png = base64.b64decode(shot["data"])
        if not png.startswith(b"\x89PNG"):
            raise AstridError("browser returned a non-PNG screenshot")
        (out_dir / "screenshot.png").write_bytes(png)

        receipt = {
            "url": url,
            "final_url": str(page.get("href") or ""),
            "title": str(page.get("title") or ""),
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "viewport": {"width": width, "height": height},
            "http_status": http_status,
            "user_agent": user_agent,
            "device_scale": int(device_scale),
            "full_page": bool(full_page),
            "clip": clip,
            "dark_mode": bool(dark_mode),
            "wait_ms": int(wait_ms),
            "image": {"width_px": _png_size(png)[0], "height_px": _png_size(png)[1]},
        }
        (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return receipt
    finally:
        if proc is not None:
            _shutdown(proc, cdp)
        shutil.rmtree(profile, ignore_errors=True)


def _png_size(data: bytes) -> tuple[int, int]:
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


# ---------------------------------------------------------------------------
# Executor entrypoint
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="capture.web_page", description="Capture a web page to PNG plus receipt.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", type=Path, required=True, help="Output directory.")
    parser.add_argument("--viewport", default="1600x1000")
    parser.add_argument("--device-scale", type=int, default=DEFAULT_DEVICE_SCALE)
    parser.add_argument("--full-page", type=_boolean, default=False)
    parser.add_argument("--wait-ms", type=int, default=DEFAULT_WAIT_MS)
    parser.add_argument("--clip", default=None, help="Optional {x,y,w,h} in CSS px of the viewport.")
    parser.add_argument("--dark-mode", type=_boolean, default=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    def _run() -> int:
        args = build_parser().parse_args(argv)
        out_dir = args.out.expanduser().resolve()
        receipt = capture(
            url=args.url,
            out_dir=out_dir,
            viewport=parse_viewport(args.viewport),
            device_scale=args.device_scale,
            full_page=bool(args.full_page),
            wait_ms=args.wait_ms,
            clip=parse_clip(args.clip),
            dark_mode=bool(args.dark_mode),
        )
        manifest = build_manifest(
            kind="web_page_capture",
            inputs={
                "url": args.url,
                "viewport": args.viewport,
                "device_scale": args.device_scale,
                "full_page": bool(args.full_page),
                "wait_ms": args.wait_ms,
                "clip": parse_clip(args.clip),
                "dark_mode": bool(args.dark_mode),
            },
            outputs=[
                {"name": "screenshot", "path": "screenshot.png", "type": "file", "artifact_type": "image", "role": "result", "is_primary": True},
                {"name": "receipt", "path": "receipt.json", "type": "file", "role": "auxiliary"},
            ],
            created=datetime.now(timezone.utc).isoformat(),
        )
        write_manifest(out_dir / "manifest.json", manifest)
        print(json.dumps(receipt, sort_keys=True))
        return 0

    return run_pack_main("capture.web_page", _run, argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
