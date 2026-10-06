from pathlib import Path

from PIL import Image

from astrid.packs.rendering.executors.timeline_visualize.composed_frame import (
    FrameCaptureCache,
    FrameCaptureWorker,
    RemotionFrameProvider,
    frame_cache_key,
)
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
    worker.close()


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
    assert provider.worker.started is True
    provider.close()
    assert provider.worker.started is False


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
