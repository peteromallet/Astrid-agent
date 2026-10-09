from __future__ import annotations

import functools
import http.server
import json
import socket
import threading
from pathlib import Path

import pytest
import yaml

from astrid.core.contracts.errors import AstridError
from astrid.core.pack.validate import validate_pack
from astrid.packs.capture.executors.web_page import run as cap

PACK_ROOT = Path(__file__).resolve().parents[3] / "astrid" / "packs" / "capture"
RECEIPT_KEYS = {
    "url",
    "final_url",
    "title",
    "captured_at",
    "viewport",
    "http_status",
    "user_agent",
    "device_scale",
    "full_page",
    "clip",
    "dark_mode",
    "wait_ms",
    "image",
}


def _chrome_or_none() -> str | None:
    try:
        return cap.find_chrome()
    except AstridError:
        return None


needs_chrome = pytest.mark.skipif(_chrome_or_none() is None, reason="no headless Chromium on this machine")


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


@pytest.fixture()
def page(tmp_path: Path) -> Path:
    html = tmp_path / "site" / "index.html"
    html.parent.mkdir()
    html.write_text(
        "<!doctype html><html><head><title>Capture Fixture</title>"
        "<style>body{margin:0;background:#f4ead8;font:32px Georgia}"
        ".tall{height:2400px;background:linear-gradient(#fff,#c60)}</style></head>"
        "<body><h1>Fixture</h1><div class='tall'></div></body></html>",
        encoding="utf-8",
    )
    return html


@pytest.fixture()
def local_server(page: Path):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            return

    handler = functools.partial(Quiet, directory=str(page.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_pack_manifest_validates_statically() -> None:
    errors, _warnings = validate_pack(PACK_ROOT)
    assert errors == []


def test_executor_manifest_declares_https_host_only_network() -> None:
    spec = yaml.safe_load((PACK_ROOT / "executors" / "web_page" / "executor.yaml").read_text(encoding="utf-8"))
    assert spec["id"] == "capture.web_page"
    assert spec["isolation"]["network"] is True
    policy = spec["metadata"]["network_policy"]
    assert policy["dynamic_url_inputs"] == ["url"]
    assert policy["allowed_destinations"] == []
    assert policy["allow_redirects"] is False
    assert [i["name"] for i in spec["inputs"]] == ["url", "viewport", "device_scale", "full_page", "wait_ms", "clip", "dark_mode"]
    assert [o["name"] for o in spec["outputs"]] == ["screenshot", "receipt"]
    assert len(spec["description"]) <= 500 and len(spec["short_description"]) <= 120


def test_url_policy_refuses_anything_but_https() -> None:
    assert cap.validate_url("https://github.com/peteromallet/dataclaw") == "https://github.com/peteromallet/dataclaw"
    for bad in ("http://github.com/", "ftp://example.org/x", "file:///etc/passwd", "https://user:pw@example.org/", "not a url"):
        with pytest.raises(AstridError):
            cap.validate_url(bad)
    assert cap.validate_url("http://127.0.0.1:8000/", allow_local=True)
    with pytest.raises(AstridError):
        cap.validate_url("http://example.org/", allow_local=True)


def test_viewport_and_clip_parsing() -> None:
    assert cap.parse_viewport(None) == (1600, 1000)
    assert cap.parse_viewport("1280X800") == (1280, 800)
    assert cap.parse_clip('{"x": 1, "y": 2, "w": 300, "h": 200}') == {"x": 1, "y": 2, "w": 300, "h": 200}
    assert cap.parse_clip(None) is None
    for bad in ("12", "0x500", "9000x10"):
        with pytest.raises(AstridError):
            cap.parse_viewport(bad)
    with pytest.raises(AstridError):
        cap.parse_clip({"x": 0, "y": 0, "w": 0, "h": 5})


def test_chrome_args_route_through_admitted_broker_proxy(tmp_path: Path) -> None:
    with_proxy = cap._chrome_args("/bin/chrome", tmp_path, "http://127.0.0.1:9999")
    assert "--remote-debugging-pipe" in with_proxy
    assert "--proxy-server=http://127.0.0.1:9999" in with_proxy
    without = cap._chrome_args("/bin/chrome", tmp_path, None)
    assert not any(arg.startswith("--proxy-server") for arg in without)


def test_cli_refuses_plain_http_before_launching_a_browser(tmp_path: Path) -> None:
    code = cap.main(["--url", "http://example.org/", "--out", str(tmp_path / "out")])
    assert code != 0
    assert not (tmp_path / "out" / "screenshot.png").exists()


@needs_chrome
def test_file_page_writes_png_and_receipt(page: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    receipt = cap.capture(
        url=page.as_uri(), out_dir=out, viewport=(800, 500), device_scale=2, wait_ms=0, allow_local=True
    )
    assert set(receipt) == RECEIPT_KEYS
    assert receipt["title"] == "Capture Fixture"
    assert receipt["final_url"].endswith("/site/index.html")
    assert receipt["viewport"] == {"width": 800, "height": 500}
    assert receipt["captured_at"].endswith("+00:00")
    assert _png_size(out / "screenshot.png") == (1600, 1000)
    saved = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
    assert saved == receipt


@needs_chrome
def test_full_page_and_clip_sizes(page: Path, tmp_path: Path) -> None:
    full = cap.capture(
        url=page.as_uri(), out_dir=tmp_path / "full", viewport=(800, 500), device_scale=1, full_page=True,
        wait_ms=0, allow_local=True,
    )
    width, height = _png_size(tmp_path / "full" / "screenshot.png")
    assert width == 800 and height > 500
    assert full["full_page"] is True

    clipped = cap.capture(
        url=page.as_uri(), out_dir=tmp_path / "clip", viewport=(800, 500), device_scale=2,
        clip={"x": 0, "y": 0, "w": 300, "h": 150}, wait_ms=0, allow_local=True,
    )
    assert _png_size(tmp_path / "clip" / "screenshot.png") == (600, 300)
    assert clipped["clip"] == {"x": 0, "y": 0, "w": 300, "h": 150}


@needs_chrome
def test_localhost_records_http_status_and_final_url(local_server: str, tmp_path: Path) -> None:
    receipt = cap.capture(
        url=f"{local_server}/missing.html", out_dir=tmp_path / "404", viewport=(640, 400), device_scale=1,
        wait_ms=0, allow_local=True,
    )
    assert receipt["http_status"] == 404
    assert receipt["url"].endswith("/missing.html")
    ok = cap.capture(
        url=f"{local_server}/index.html", out_dir=tmp_path / "ok", viewport=(640, 400), device_scale=1,
        wait_ms=0, allow_local=True,
    )
    assert ok["http_status"] == 200
    assert ok["title"] == "Capture Fixture"


@needs_chrome
def test_refused_connection_fails_with_browser_error(tmp_path: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
    with pytest.raises(AstridError, match="navigation to"):
        cap.capture(
            url=f"http://127.0.0.1:{closed_port}/", out_dir=tmp_path / "dead", wait_ms=0, allow_local=True,
        )
