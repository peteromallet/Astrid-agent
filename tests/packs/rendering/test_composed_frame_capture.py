from pathlib import Path

from PIL import Image

from astrid.packs.rendering.executors.timeline_visualize.composed_frame import (
    FrameCaptureCache,
    FrameCaptureWorker,
    RemotionFrameProvider,
    frame_cache_key,
)
from astrid.packs.rendering.backends.remotion.run import _resize_frame_outputs
from astrid.packs.rendering.executors.timeline_visualize.filmstrip_cards import (
    build_filmstrip_pack,
    plan_filmstrip,
)
from astrid.packs.rendering.executors.timeline_visualize.filmstrip_options import filmstrip_options


def _snapshot():
    return {
        "project_slug": "demo",
        "timeline_id": "timeline-1",
        "render_run_id": "capture-1",
        "video_digest": "sha256:" + "a" * 64,
        "fps_rational": [24, 1],
        "duration_frames": 48,
        "clips": [],
        "scripts": [],
        "metadata": {"capture_identity": "sha256:revision-a"},
    }


def test_exact_frame_planning_reports_one_frame_and_rejects_out_of_range():
    options = filmstrip_options({"frame": 7, "resolution": "320x180"})
    index = plan_filmstrip(_snapshot(), options)
    assert [card["frame"] for card in index["cards"]] == [7]
    assert index["cards"][0]["image"].endswith(".jpg")
    assert index["sampling"]["requested_frame"] == 7
    assert options["request"]["frame"] == 7

    try:
        plan_filmstrip(_snapshot(), {"frame": 48})
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("out-of-range frame was accepted")


def test_worker_is_serialized_and_expires(tmp_path):
    calls = []

    def render(frames, output_dir, resolution):
        calls.append((tuple(frames), tuple(resolution or ())))
        output = Path(output_dir)
        result = {}
        for frame in frames:
            path = output / f"frame-{frame:06d}.png"
            Image.new("RGB", (32, 18), (frame, 0, 0)).save(path)
            result[frame] = path
        return result

    worker = FrameCaptureWorker(render, idle_seconds=0)
    output = tmp_path / "frames"
    assert worker.capture([2, 1], output, [32, 18])
    assert calls == [((1, 2), (32, 18))]
    assert worker.started is False
    worker.close()


def test_worker_closes_after_renderer_failure(tmp_path):
    def render(_frames, _output_dir, _resolution):
        raise RuntimeError("renderer failed")

    worker = FrameCaptureWorker(render, idle_seconds=45)
    try:
        worker.capture([1], tmp_path / "frames", [32, 18])
    except RuntimeError as exc:
        assert str(exc) == "renderer failed"
    else:
        raise AssertionError("renderer failure was swallowed")
    assert worker.started is False
    assert worker._closed is True


def test_frame_outputs_are_resized_after_native_render(tmp_path):
    output = tmp_path / "frame-000000.png"
    Image.new("RGB", (1920, 1080), (12, 34, 56)).save(output)

    _resize_frame_outputs(tmp_path, (320, 180))

    with Image.open(output) as image:
        assert image.size == (320, 180)


def test_provider_uses_cache_on_second_request(tmp_path):
    calls = []

    def render(frames, output_dir, resolution):
        calls.append(tuple(frames))
        result = {}
        for frame in frames:
            path = Path(output_dir) / f"frame-{frame:06d}.png"
            Image.new("RGB", (32, 18), (frame, 0, 0)).save(path)
            result[frame] = path
        return result

    snapshot = _snapshot()
    cache = FrameCaptureCache(tmp_path / "cache")
    provider = RemotionFrameProvider(
        snapshot,
        timeline_path=tmp_path / "timeline.json",
        assets_path=tmp_path / "assets.json",
        project_dir=tmp_path,
        cache=cache,
        renderer=render,
        idle_seconds=0,
    )
    cards = [{"frame": 1, "image": "frames/frame-000000001.png"}]
    first = provider.capture(cards, tmp_path / "first", [32, 18])
    assert first["evidence_source"] == "fresh_capture"
    second_provider = RemotionFrameProvider(
        snapshot,
        timeline_path=tmp_path / "timeline.json",
        assets_path=tmp_path / "assets.json",
        project_dir=tmp_path,
        cache=cache,
        renderer=render,
        idle_seconds=0,
    )
    second = second_provider.capture(cards, tmp_path / "second", [32, 18])
    assert second["evidence_source"] == "cache"
    assert calls == [(1,)]
    assert (tmp_path / "second" / cards[0]["image"]).is_file()
    assert frame_cache_key(snapshot, 1, [32, 18]) != frame_cache_key(snapshot, 2, [32, 18])
    assert provider.worker.started is False
    provider.close()
    assert provider.worker.started is False


def test_cache_identity_changes_when_renderer_source_changes(tmp_path, monkeypatch):
    source_root = tmp_path / "renderer-source"
    source_root.mkdir()
    source_file = source_root / "effect.tsx"
    source_file.write_text("export const value = 1;", encoding="utf-8")
    monkeypatch.setenv("ASTRID_RENDERER_SOURCE_ROOT", str(source_root))
    first = frame_cache_key(_snapshot(), 1, [32, 18])
    source_file.write_text("export const value = 2;", encoding="utf-8")
    second = frame_cache_key(_snapshot(), 1, [32, 18])
    assert first != second


def test_cache_identity_ignores_ephemeral_attempt_directory_paths():
    first = _snapshot()
    second = _snapshot()
    first["metadata"]["asset_integrity"] = {
        "clip": {
            "observed_sha256": "abc",
            "path": "/tmp/astrid-attempt-first/managed-objects/0000-content",
        }
    }
    second["metadata"]["asset_integrity"] = {
        "clip": {
            "observed_sha256": "abc",
            "path": "/tmp/astrid-attempt-second/managed-objects/0000-content",
        }
    }
    assert frame_cache_key(first, 1, [32, 18]) == frame_cache_key(second, 1, [32, 18])


def test_cache_identity_changes_when_remotion_source_changes(tmp_path, monkeypatch):
    project = tmp_path / "remotion"
    source = project / "src"
    source.mkdir(parents=True)
    worker = source / "astrid-frame-worker.mjs"
    worker.write_text("const version = 1;", encoding="utf-8")
    monkeypatch.setenv("ASTRID_REMOTION_PROJECT_DIR", str(project))
    first = frame_cache_key(_snapshot(), 1, [32, 18])
    worker.write_text("const version = 2;", encoding="utf-8")
    second = frame_cache_key(_snapshot(), 1, [32, 18])
    assert first != second


def test_cache_identity_changes_when_remotion_config_changes(tmp_path, monkeypatch):
    project = tmp_path / "remotion"
    (project / "src").mkdir(parents=True)
    config = project / "remotion.config.ts"
    config.write_text("export default {};\n", encoding="utf-8")
    monkeypatch.setenv("ASTRID_REMOTION_PROJECT_DIR", str(project))
    first = frame_cache_key(_snapshot(), 1, [32, 18])
    config.write_text("export default { enableTailwind: true };\n", encoding="utf-8")
    second = frame_cache_key(_snapshot(), 1, [32, 18])
    assert first != second


def test_shared_owner_force_release_forwards_force_to_session(monkeypatch):
    from astrid.packs.rendering.executors.timeline_visualize import composed_frame

    class Session:
        def __init__(self):
            self.calls = []

        def close(self, *, force=False):
            self.calls.append(force)

    session = Session()
    owner = object.__new__(composed_frame._SharedRemotionOwner)
    owner.session = session
    owner.lock = __import__("threading").RLock()
    owner.references = 1
    owner.timer = None
    owner.timer_generation = 0
    owner.closed = False
    owner.idle_seconds = 45.0
    owner.release(force=True)
    assert session.calls == [True]


def test_force_close_removes_submitted_launchd_owner(monkeypatch, tmp_path):
    from astrid.packs.rendering.backends.remotion import run as remotion_run

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)

    monkeypatch.setattr(remotion_run.sys, "platform", "darwin")
    monkeypatch.setattr(remotion_run.subprocess, "run", fake_run)
    session = remotion_run.PersistentRemotionFrameSession()
    socket_path = tmp_path / ("owner-" + "a" * 32 + ".sock")
    session._socket_path = socket_path
    session.close(force=True)

    assert calls == [[
        "/bin/launchctl",
        "remove",
        "astrid-rfo-" + "a" * 32,
    ]]


def test_filmstrip_pack_accepts_capture_provider_without_video(tmp_path):
    snapshot = _snapshot()
    snapshot["duration_frames"] = 3
    options = filmstrip_options({"frame": 1, "resolution": "32x18"})
    options["frame_extension"] = "png"

    class Provider:
        def capture(self, cards, out_root, resolution):
            for card in cards:
                path = Path(out_root) / card["image"]
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (32, 18), "#204050").save(path)
            return {"evidence_source": "fresh_capture", "fresh_frames": len(cards), "cached_frames": 0}

    result = build_filmstrip_pack(
        out_root=tmp_path / "filmstrip-view",
        snapshot=snapshot,
        options=options,
        frame_provider=Provider(),
    )
    assert result["frame_index"]["frame_capture"]["evidence_source"] == "fresh_capture"
    assert result["cards"][0]["image"].endswith(".png")
    assert Path(result["paths"]["png"][0]).is_file()


# --- cold browser: one retry, and the findings say so --------------------------------------
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

WORKER = Path(__file__).resolve().parents[3] / "remotion" / "src" / "astrid-frame-worker.mjs"
STUB_RENDERER = """
export const ensureBrowser = async () => ({ path: null });
export const openBrowser = async () => { throw new Error('stub'); };
export const renderFrames = async () => {};
export const selectComposition = async () => ({});
"""
DRIVER = """
import { _test } from './worker.mjs';
const timeout = new Error('Timed out after 25000 ms while trying to connect to the browser! Chrome logged the following:');
const run = async (failures, error = timeout) => {
  let calls = 0, warmed = 0;
  const open = async () => { calls += 1; if (calls <= failures) throw error; return { browser: true }; };
  try {
    await _test.connectBrowser({}, open, async () => { warmed += 1; });
    return { ok: true, calls, warmed, notes: _test.takeNotes() };
  } catch (e) {
    return { ok: false, calls, warmed, error: e.message, notes: _test.takeNotes() };
  }
};
console.log(JSON.stringify([await run(0), await run(1), await run(2), await run(1, new Error('no chrome'))]));
"""


def test_frame_worker_retries_a_cold_chrome_connect_once(tmp_path):
    node = os.environ.get("ASTRID_NODE_EXECUTABLE") or shutil.which("node")
    if not node:
        pytest.skip("no Node")
    source = WORKER.read_text(encoding="utf-8")
    assert "from '@remotion/renderer';" in source
    (tmp_path / "worker.mjs").write_text(source.replace("from '@remotion/renderer';", "from './renderer.mjs';"))
    (tmp_path / "renderer.mjs").write_text(STUB_RENDERER)
    (tmp_path / "driver.mjs").write_text(DRIVER)
    done = subprocess.run([node, str(tmp_path / "driver.mjs")], stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=60, check=True)
    warm, cold, dead, other = json.loads(done.stdout.strip().splitlines()[-1])
    assert warm == {"ok": True, "calls": 1, "warmed": 0, "notes": []}
    assert cold["ok"] and cold["calls"] == 2 and cold["warmed"] == 1
    assert cold["notes"][0].startswith("CAPTURE  cold browser: Chrome missed the ") and "retried once" in cold["notes"][0]
    assert not dead["ok"] and dead["calls"] == 2 and "retried once" in dead["error"]
    assert not other["ok"] and other["calls"] == 1 and other["error"] == "no chrome"


def test_owner_notes_reach_the_findings(tmp_path, monkeypatch):
    from astrid.packs.rendering.backends.remotion import run as remotion_run
    from astrid.packs.rendering.executors.timeline_visualize import composed_frame

    session = remotion_run.PersistentRemotionFrameSession()
    monkeypatch.setattr(session, "_ensure_owner", lambda **_kwargs: None)
    monkeypatch.setattr(session, "_request", lambda _request: {"ok": True, "notes": ["CAPTURE  cold browser: retried"]})
    session.render(project_dir=tmp_path, composition_id="c", node_executable=tmp_path / "node", remotion_cli=tmp_path / "cli",
                   props_path=tmp_path / "p.json", output_dir=tmp_path, frames=[1], resolution=None, port=1,
                   environment={}, identity="i")
    renderer = type("Renderer", (), {"session": session})()
    assert composed_frame._renderer_notes(renderer) == ["CAPTURE  cold browser: retried"]
    assert session.notes == []

    snapshot = _snapshot()
    snapshot["duration_frames"] = 3
    options = filmstrip_options({"frame": 1, "resolution": "32x18"})
    options["frame_extension"] = "png"

    class Provider:
        def capture(self, cards, out_root, resolution):
            for card in cards:
                path = Path(out_root) / card["image"]
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (32, 18), "#204050").save(path)
            return {"evidence_source": "fresh_capture", "notes": ["CAPTURE  cold browser: retried"]}

    result = build_filmstrip_pack(out_root=tmp_path / "view", snapshot=snapshot, options=options, frame_provider=Provider())
    assert result["findings"] == ["CAPTURE  cold browser: retried"]


OVERLAY_DRIVER = """
import { mkdirSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { _test } from './worker.mjs';
const root = process.argv[2];
const overlay = join(root, 'overlay', 'astrid-effects', 'h1', 'am-x');
mkdirSync(overlay, { recursive: true });
writeFileSync(join(overlay, 'sheet.png'), 'png');
const pub = join(root, 'bundle', 'public');
mkdirSync(join(pub, 'fonts'), { recursive: true });
writeFileSync(join(pub, 'fonts', 'a.woff2'), 'font');
const created = _test.linkOverlay(join(root, 'overlay'), pub);
const during = readFileSync(join(pub, 'astrid-effects', 'h1', 'am-x', 'sheet.png'), 'utf8');
_test.unlinkOverlay(created);
console.log(JSON.stringify({ during, after: existsSync(join(pub, 'astrid-effects')), font: existsSync(join(pub, 'fonts', 'a.woff2')) }));
"""


def test_the_owner_serves_a_requests_assets_from_a_private_overlay(tmp_path):
    node = os.environ.get("ASTRID_NODE_EXECUTABLE") or shutil.which("node")
    if not node:
        pytest.skip("no Node")
    source = WORKER.read_text(encoding="utf-8")
    (tmp_path / "worker.mjs").write_text(source.replace("from '@remotion/renderer';", "from './renderer.mjs';"))
    (tmp_path / "renderer.mjs").write_text(STUB_RENDERER)
    (tmp_path / "driver.mjs").write_text(OVERLAY_DRIVER)
    done = subprocess.run([node, str(tmp_path / "driver.mjs"), str(tmp_path)], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60, check=True)
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result == {"during": "png", "after": False, "font": True}
