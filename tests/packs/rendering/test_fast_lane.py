"""One-shot verify: the fast lane's moments, window, media cache, renderer environment and page."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from astrid.packs.rendering.backends.remotion import run as remotion_run
from astrid.packs.rendering.executors.timeline_visualize import composed_frame, fast_lane
from astrid.packs.rendering.executors.timeline_visualize.fast_lane import (
    FastLaneError,
    Moment,
    frame_of,
    materialize,
    moments_from_changes,
    window_snapshot,
)

FPS = 30.0


def test_an_until_edit_is_shown_on_its_last_frame_and_the_first_without_it():
    changes = [{"kind": "changed", "clip_id": "occ:c30.cover", "before": [91.5, 93.4], "after": [91.5, 93.5],
                "fields": ["end"]}]
    moments = moments_from_changes(changes, FPS)
    assert [frame_of(m.t, FPS) for m in moments] == [2804, 2805]
    assert moments[0].label == "c30.cover: its last frame" and moments[1].label == "c30.cover: the first frame without it"


def test_moved_starts_added_removed_and_param_changes_each_get_their_frames():
    changes = [
        {"kind": "changed", "clip_id": "c41.sparks", "before": [121.0, 122.0], "after": [121.867, 122.867], "fields": ["start", "end"]},
        {"kind": "changed", "clip_id": "c21.claw", "before": [80.0, 82.0], "after": [80.0, 82.0], "fields": ["params"]},
        {"kind": "added", "clip_id": "c33a.label", "before": None, "after": [110.0, 111.0], "fields": []},
        {"kind": "removed", "clip_id": "c12.old", "before": [40.0, 41.0], "after": None, "fields": []},
    ]
    moments = moments_from_changes(changes, FPS, limit=20)
    labels = {m.label for m in moments}
    assert "c41.sparks starts" in labels and "c41.sparks: the frame before it starts" in labels
    assert "c21.claw (params) mid-clip" in labels and "c33a.label starts" in labels
    assert any(label.startswith("c12.old removed") for label in labels)
    assert len(moments_from_changes(changes, FPS)) == fast_lane.MAX_MOMENTS  # bounded
    frames = [frame_of(m.t, FPS) for m in moments]
    assert frames == sorted(set(frames))


def _config():
    clips = [
        {"id": "plate", "track": "plate", "clipType": "am-snap-plate", "at": 0.0, "hold": 60.0, "asset": "P-01"},
        {"id": "early", "track": "sprite", "clipType": "am-sprite", "at": 2.0, "hold": 1.0, "asset": "OLD"},
        {"id": "near", "track": "sprite", "clipType": "am-sprite", "at": 29.0, "hold": 2.0, "asset": "NEAR",
         "params": {"frames": {"asset": "STRIP"}}},
        {"id": "music", "track": "music", "clipType": "media", "at": 0.0, "hold": 60.0, "asset": "MUSIC"},
    ]
    tracks = [{"id": "plate", "kind": "visual"}, {"id": "sprite", "kind": "visual"}, {"id": "music", "kind": "audio"}]
    assets = {k: {"content_sha256": hashlib.sha256(k.encode()).hexdigest()} for k in ("P-01", "OLD", "NEAR", "STRIP", "MUSIC")}
    return {"tracks": tracks, "clips": clips, "app": {}}, {"assets": assets}


def test_the_window_keeps_visual_clips_near_the_frames_and_only_their_media():
    config, registry = _config()
    windowed, assets, dropped = window_snapshot(config, registry, [frame_of(30.0, FPS)], FPS)
    assert [c["id"] for c in windowed["clips"]] == ["plate", "near"]
    assert set(assets["assets"]) == {"P-01", "NEAR", "STRIP"}  # params reach assets too; no music
    assert dropped == 2 and windowed["tracks"] == config["tracks"]


def test_the_window_never_ends_before_the_frame_asked_for():
    config, registry = _config()
    config["clips"][0]["hold"] = 10.0  # the plate ends at 10 s; nothing covers 59 s
    config["clips"][2]["at"] = 50.0
    windowed, _assets, _dropped = window_snapshot(config, registry, [frame_of(59.5, FPS)], FPS)
    assert [c["id"] for c in windowed["clips"]] == ["plate", "early", "near"]  # every visual clip


class _Media:
    def __init__(self):
        self.reads = []

    def read_bytes(self, ref):
        self.reads.append(ref)
        name = {hashlib.sha256(k.encode()).hexdigest(): k for k in ("P-01", "NEAR", "STRIP", "BAD")}[ref.removeprefix("sha256:")]
        return b"tampered" if name == "BAD" else name.encode()


class _Remote:
    def __init__(self):
        self.media = _Media()


def test_media_come_through_the_sdk_once_into_a_bounded_cache(tmp_path):
    _config_, registry = _config()
    wanted = {"assets": {k: registry["assets"][k] for k in ("P-01", "NEAR")}}
    remote = _Remote()
    derived, objects, fetched = materialize(remote, wanted, root=tmp_path)
    assert fetched == len(b"P-01") + len(b"NEAR") and len(remote.media.reads) == 2
    assert Path(derived["assets"]["P-01"]["file"]).read_bytes() == b"P-01"
    assert objects[hashlib.sha256(b"P-01").hexdigest()] == derived["assets"]["P-01"]["file"]
    _again, _objects, fetched_again = materialize(remote, wanted, root=tmp_path)
    assert fetched_again == 0 and len(remote.media.reads) == 2
    materialize(remote, wanted, root=tmp_path, cap_bytes=4)  # newest first, under the cap
    assert len(list((tmp_path / "objects").iterdir())) == 1
    bad = {"assets": {"BAD": {"content_sha256": hashlib.sha256(b"BAD").hexdigest()}}}
    with pytest.raises(FastLaneError, match="did not match"):
        materialize(remote, bad, root=tmp_path)


def test_the_worker_reproduces_the_hosts_renderer_from_its_owner_record(tmp_path, monkeypatch):
    checkout = tmp_path / "serve"
    (checkout / "remotion").mkdir(parents=True)
    owners = tmp_path / "rfo"
    owners.mkdir()
    monkeypatch.setattr(remotion_run, "REPO_ROOT", checkout)
    monkeypatch.setenv("ASTRID_NODE_EXECUTABLE", "/host/node")
    monkeypatch.setenv("ASTRID_TIMELINE_SCHEMA_PYTHONPATH", "/host/schema")
    remotion_run.write_owner_record(owners / ("owner-" + "a" * 32 + ".sock"), project_dir=checkout / "remotion")
    records = remotion_run.owner_records(checkout / "remotion", root=owners)
    assert records[0]["env"] == {"ASTRID_NODE_EXECUTABLE": "/host/node", "ASTRID_TIMELINE_SCHEMA_PYTHONPATH": "/host/schema"}

    # the client shell differs: the record wins, and stray identity settings are dropped
    monkeypatch.setenv("ASTRID_NODE_EXECUTABLE", "/client/node")
    monkeypatch.setenv("ASTRID_REMOTION_PROJECT_DIR", "/client/remotion")
    monkeypatch.setattr(remotion_run, "owner_records", lambda project_dir: records)
    env, source = fast_lane.worker_environment(checkout)
    assert env["ASTRID_NODE_EXECUTABLE"] == "/host/node" and "ASTRID_REMOTION_PROJECT_DIR" not in env
    assert env["PYTHONPATH"] == str(checkout) and "shared warm owner" in source

    monkeypatch.setattr(remotion_run, "owner_records", lambda project_dir: [])
    monkeypatch.delenv("ASTRID_NODE_EXECUTABLE")
    (checkout / "remotion" / "package.json").write_text(json.dumps({"engines": {"node": "=20.19.4"}}))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    with pytest.raises(FastLaneError, match="ASTRID_NODE_EXECUTABLE"):
        fast_lane.worker_environment(checkout)


def test_the_served_renderer_is_the_one_the_pack_host_runs(tmp_path):
    serve = tmp_path / "Astrid-serve"
    (serve / "astrid").mkdir(parents=True)
    (tmp_path / "data" / "runtime").mkdir(parents=True)
    (tmp_path / "data" / "runtime" / "generic-host.json").write_text(json.dumps(
        {"source_checkout": str(serve), "python_executable": "/missing/python"}))
    served = fast_lane.served_renderer(tmp_path / "data")
    assert served["checkout"] == serve and Path(served["python"]).exists()  # a missing python falls back


def _tile(path: Path, *, box: bool) -> str:
    image = Image.new("RGB", (640, 360), "#204050")
    if box:
        ImageDraw.Draw(image).rectangle((100, 50, 160, 90), fill="#ffcc00")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return str(path)


def test_verify_makes_one_page_from_two_snapshots_without_the_host(tmp_path, monkeypatch):
    config, registry = _config()
    snapshot = {"fps_rational": [30, 1], "config": config, "registry": registry}
    asked = []

    def prepare(inputs, *, project, client):
        asked.append(("working" if "authoring_preview" in inputs else "published", project, inputs["timeline_slug"]))
        return {"capture_snapshot": snapshot}

    def worker(jobs, run_dir, *, served=None, timeout=None):
        out = {"jobs": [], "notes": [], "environment_source": "test"}
        for job in jobs:
            frames = {str(f): _tile(Path(job["out"]) / "frames" / f"f{f:06d}.png", box=job["name"] == "working" and f == 900)
                      for f in job["frames"]}
            out["jobs"].append({"name": job["name"], "frames": frames, "cached": 0, "fresh": len(frames),
                                "owner": {"bundled": False}})
        return out

    class Draft:
        def document(self):
            return {"doc": True}

    monkeypatch.setattr("astrid.sdk.timeline_filmstrip.prepare_filmstrip", prepare)
    monkeypatch.setattr("astrid.core.timeline.authoring_bundle.preview_authoring_candidate", lambda doc: {"preview": doc})
    monkeypatch.setattr(fast_lane, "run_worker", worker)
    monkeypatch.setattr(fast_lane, "materialize", lambda remote, reg, root=None: (reg, {}, 0))
    result = fast_lane.verify(object(), "demo", "tl", [Moment(29.0, "a: last frame"), Moment(30.0, "a: first without")],
                              draft=Draft(), footer=["lint on c30: clean"], root=tmp_path)
    assert [a[0] for a in asked] == ["published", "working"]
    same, changed = result["rows"]
    assert same["pixels"] == "pixels: identical to published"
    assert (same["published_shows"], same["working_shows"]) == ("the same layers", "the same layers, unchanged")
    assert changed["pixels"] == "pixels: 1.09% differ, box 300,150–483,273 (183x123 canvas px)"
    with Image.open(result["page"]) as page:
        assert page.size[0] == 24 + 2 * (fast_lane.TILE[0] + 12)
    assert not list(Path(result["page"]).parent.glob("*/frames"))  # only the page is kept
    lines = result["lines"]
    assert lines[:3] == ["verify  a: last frame · 29.00 s (f870)",
                         "        published: the same layers · working copy: the same layers, unchanged",
                         "        pixels: identical to published"]
    assert any(line.startswith("page    ") for line in lines)
    assert any("fast lane, no queue" in line and "warm renderer" in line for line in lines)


def test_verify_without_a_moment_says_so():
    with pytest.raises(FastLaneError, match="no moment"):
        fast_lane.verify(object(), "demo", "tl", [])


def test_the_cli_falls_back_to_visualize_when_the_fast_lane_cannot_capture(monkeypatch, capsys):
    from argparse import Namespace

    from astrid.packs.timeline import cli

    def broken(*_a, **_k):
        raise FastLaneError("no Node")

    monkeypatch.setattr(fast_lane, "verify", broken)
    code = cli._print_verify(Namespace(client=None, project="p", timeline="tl"), None, [Moment(93.5, "x")])
    out = capsys.readouterr().out
    assert code == 1 and "verify  not captured: no Node" in out and "--preset frame --at 93.50" in out


# --- the renderer: warm captures write nothing shared and never take the render lock ----------

def test_a_warm_owner_capture_does_not_take_the_render_lock(tmp_path, monkeypatch):
    calls = {}

    class Locked:
        def __enter__(self):
            raise AssertionError("the frame capture took the global Remotion lock")

        def __exit__(self, *exc):
            return False

    def execute(*args, frame_numbers, frame_output_dir, **kwargs):
        calls.update(kwargs)
        for frame in frame_numbers:
            (frame_output_dir / f"frame-{frame}.png").write_bytes(b"png")

    monkeypatch.setattr(remotion_run.remotion_lock, "remotion_render_lock", lambda: Locked())
    monkeypatch.setattr(remotion_run, "_execute_remotion_locked", execute)
    session = remotion_run.PersistentRemotionFrameSession()
    result = remotion_run.capture_remotion_frames(tmp_path / "t.json", tmp_path / "a.json", tmp_path / "out", [3, 1],
                                                  project_dir=tmp_path, frame_session=session)
    assert sorted(result) == [1, 3] and calls["frame_session"] is session


def test_current_registries_are_read_without_the_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(remotion_run, "_element_registries_current", lambda project_dir, theme: True)
    monkeypatch.setattr(remotion_run.remotion_lock, "remotion_render_lock",
                        lambda: (_ for _ in ()).throw(AssertionError("locked to read")))
    remotion_run._regenerate_element_registries(tmp_path, None)  # no lock, no write


def test_the_session_sends_the_overlay_and_keeps_the_owners_timing(tmp_path, monkeypatch):
    session = remotion_run.PersistentRemotionFrameSession()
    sent = {}
    monkeypatch.setattr(session, "_ensure_owner", lambda **_kwargs: None)

    def request(payload):
        sent.update(payload)
        return {"ok": True, "timing": {"bundled": False, "renderMs": 900}}

    monkeypatch.setattr(session, "_request", request)
    session.render(project_dir=tmp_path, composition_id="c", node_executable=tmp_path / "node", remotion_cli=tmp_path / "cli",
                   props_path=tmp_path / "p.json", output_dir=tmp_path, frames=[1], resolution=None, port=1,
                   environment={}, identity="i", public_overlay=tmp_path / "overlay")
    assert sent["publicOverlay"] == str(tmp_path / "overlay") and session.last_timing["renderMs"] == 900


# --- the frame cache keys on every pack's element code ---------------------------------------

def test_the_frame_cache_follows_any_packs_element_code(tmp_path):
    for pack in ("astrid_motion", "rendering", "local"):
        (tmp_path / "astrid" / "packs" / pack / "elements").mkdir(parents=True)
    roots = composed_frame.element_source_roots(tmp_path)
    assert [label for label, _root in roots] == ["elements:astrid_motion", "elements:local", "elements:rendering"]
    component = tmp_path / "astrid/packs/astrid_motion/elements/effects/am-sprite/component.tsx"
    component.parent.mkdir(parents=True)
    component.write_text("export const a = 1;\n")
    first = composed_frame._digest_paths(roots)
    assert composed_frame._digest_paths(roots) == first  # memoized while nothing changed
    component.write_text("export const a = 2;\n")  # same size: the mtime tells
    os.utime(component, ns=(component.stat().st_atime_ns, component.stat().st_mtime_ns + 1_000_000))
    assert composed_frame._digest_paths(roots) != first


def test_the_live_identity_covers_the_motion_pack():
    labels = json.loads(composed_frame.renderer_environment_identity())
    assert "sources" in labels
    checkout = Path(composed_frame.__file__).resolve().parents[5]
    assert any(label == "elements:astrid_motion" for label, _root in composed_frame.element_source_roots(checkout))


def test_moments_use_the_addresses_the_edit_printed():
    changes = [{"kind": "changed", "clip_id": "occ-film:c30b-02-am-type", "before": [107.4, 108.7],
                "after": [107.4, 108.7], "fields": ["params"]}]
    (moment,) = moments_from_changes(changes, FPS, names={"c30b-02-am-type": "c30b.astrid"})
    assert moment.label == "c30b.astrid (params) mid-clip"


def test_edit_and_check_offer_the_one_shot_verify():
    from astrid.packs.timeline import cli

    verbs = cli.build_parser(object())._subparsers._group_actions[0].choices
    assert "--verify" in verbs["edit"].format_help() and "Add --verify" in verbs["edit"].format_help()
    assert "--at MOMENT" in verbs["check"].format_help()
    parsed = verbs["check"].parse_args(["tl", "--project", "p", "--at", '"Astrid"', "--at", "c30.cover"])
    assert parsed.at == ['"Astrid"', "c30.cover"]


# --- 1. what each side shows, in plain words ------------------------------------------------

def _sparks(at, hold, **params):
    return {"id": "occ:c41-03-am-sprite", "track": "sprite", "clipType": "am-sprite", "at": at, "hold": hold,
            "asset": "SPARKS", "params": {"x": 230, **params}, "app": {"cut": "c41", "layer": "sparks"}}


def _cfg(*clips):
    return {"tracks": [{"id": "fx", "kind": "visual"}, {"id": "sprite", "kind": "visual"},
                       {"id": "plate", "kind": "visual"}], "clips": list(clips)}


def test_a_moved_start_says_the_old_layer_is_still_there_and_the_new_one_is_not_yet():
    published, working = _cfg(_sparks(121.0, 1.0)), _cfg(_sparks(121.867, 1.0))
    before = Moment(121.833, "c41.sparks: the frame before it starts", "occ:c41-03-am-sprite")
    shows = fast_lane.describe_sides(before, frame_of(before.t, FPS), FPS, published, working)
    assert shows == ("c41.sparks on screen", "no c41.sparks yet (it starts at 121.87 s)")
    after_end = Moment(122.5, "c41.sparks: the first frame without it", "occ:c41-03-am-sprite")
    shows = fast_lane.describe_sides(after_end, frame_of(after_end.t, FPS), FPS, published, working)
    assert shows == ("no c41.sparks any more (it ended at 122.00 s)", "c41.sparks on screen")
    moved = _cfg(_sparks(121.0, 1.0, x=260))
    shows = fast_lane.describe_sides(Moment(121.5, "mid", "c41-03-am-sprite"), frame_of(121.5, FPS), FPS, published, moved)
    assert shows == ("c41.sparks on screen, before your change", "c41.sparks on screen with your change (params)")


def test_a_bare_moment_names_the_layers_only_one_side_has_and_the_changed_ones():
    cover = {"id": "occ:c30-01-am-sprite", "track": "sprite", "clipType": "am-sprite", "at": 91.5, "hold": 2.0,
             "asset": "MYSTERY", "app": {"cut": "c30", "layer": "cover"}}
    icon = {"id": "occ:c30-02-am-sprite", "track": "sprite", "clipType": "am-sprite", "at": 93.4, "hold": 1.0,
            "asset": "ICON", "app": {"cut": "c30", "layer": "icon"}}
    shows = fast_lane.describe_sides(Moment(93.45, "93.45"), frame_of(93.45, FPS), FPS, _cfg(cover),
                                     _cfg({**cover, "hold": 1.8}, icon))
    assert shows == ("c30.cover (not in the working copy here)", "c30.icon (new here)")


# --- 2. small changes are never "0.0%" -------------------------------------------------------

def test_a_few_changed_pixels_read_as_less_than_a_tenth_of_a_percent_with_their_box(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (640, 360), "#204050").save(a)
    image = Image.new("RGB", (640, 360), "#204050")
    for x in (300, 301, 302):
        image.putpixel((x, 100), (40, 74, 90))  # a faint, 1-px connector end: delta 10 per channel
    image.save(b)
    share, count, box, size = fast_lane._difference(a, b)
    assert count == 3 and box == (300, 100, 303, 101)
    line = fast_lane.pixel_line(share, count, box, size, (1920, 1080))
    assert line == "pixels: <0.1% (3 px) differ, box 900,300–909,303 (9x3 canvas px)"
    assert fast_lane.pixel_line(0.03041, 7000, (10, 10, 20, 20), (640, 360), (1920, 1080)).startswith("pixels: 3.04% differ")


# --- 3. the first use primes the renderer (no visualize needed first) ------------------------

def test_after_a_host_restart_the_renderer_settings_come_from_the_live_host(tmp_path, monkeypatch):
    ps = ("/usr/bin/python -m astrid.core.execution.generic_host run PATH=/usr/bin "
          "ASTRID_NODE_EXECUTABLE=/host/node20 ASTRID_REMOTION_PROJECT_DIR=/serve/remotion HOME=/Users/x")

    class Done:
        stdout = ps

    monkeypatch.setattr(fast_lane.subprocess, "run", lambda *a, **k: Done())
    assert fast_lane.host_renderer_env(123) == {"ASTRID_NODE_EXECUTABLE": "/host/node20",
                                                "ASTRID_REMOTION_PROJECT_DIR": "/serve/remotion"}
    monkeypatch.setattr(remotion_run, "owner_records", lambda project_dir: [{"env": {"ASTRID_NODE_EXECUTABLE": "/old"}}])
    monkeypatch.delenv("ASTRID_NODE_EXECUTABLE", raising=False)
    env, source = fast_lane.worker_environment(tmp_path, host_pid=123)
    assert env["ASTRID_NODE_EXECUTABLE"] == "/host/node20" and source.startswith("the pack host's renderer")


def test_with_no_host_and_no_record_the_projects_pinned_node_is_found(tmp_path, monkeypatch):
    (tmp_path / "remotion").mkdir()
    (tmp_path / "remotion" / "package.json").write_text(json.dumps({"engines": {"node": "=20.19.4"}}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, version in (("old", "v18.0.0"), ("node", "v20.19.4")):
        target = bin_dir / name / "node" if name == "old" else bin_dir / "node"
        target.parent.mkdir(exist_ok=True)
        target.write_text(f"#!/bin/sh\necho {version}\n")
        target.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir / 'old'}{os.pathsep}{bin_dir}")
    monkeypatch.delenv("ASTRID_NODE_EXECUTABLE", raising=False)
    monkeypatch.setattr(remotion_run, "owner_records", lambda project_dir: [])
    env, source = fast_lane.worker_environment(tmp_path, host_pid=None)
    assert env["ASTRID_NODE_EXECUTABLE"] == str(bin_dir / "node") and "version pin" in source


# --- 4. zoom: a layer at full resolution ------------------------------------------------------

def _layered():
    from tests.packs.rendering.test_timeline_visualize_motion import layered_snapshot

    snapshot = layered_snapshot()
    snapshot["config"] = {"tracks": snapshot["tracks"], "clips": [dict(c) for c in snapshot["clips"]]}
    return snapshot


def test_zoom_finds_the_layers_box_and_regions_parse():
    snapshot = _layered()
    region = fast_lane.zoom_region([snapshot], "card", 3.8, FPS, (1920, 1080))
    x0, y0, x1, y1 = region
    assert x0 < 1260 < 1980 and y0 < 120 and x1 == 1920 and y1 > 200  # the callout box, padded, inside the canvas
    assert fast_lane.zoom_region([snapshot], "card", 1.0, FPS, (1920, 1080)) is None  # not on screen yet
    for clip in snapshot["clips"]:
        if clip["id"].endswith("card"):
            clip["params"] = {**clip["params"], "anchor": {"x": 900, "y": 700}}
    x0, y0, x1, y1 = fast_lane.zoom_region([snapshot], "card", 3.8, FPS, (1920, 1080))
    assert x0 < 900 and y1 > 700  # the connector's end is in the crop
    assert fast_lane.parse_region("100,50,300,200") == (100, 50, 400, 250)
    assert fast_lane.zoom_scale((747, 377)) == 1.0  # full resolution when it fits the page
    assert fast_lane.zoom_scale((100, 50)) == 6.0 and fast_lane.zoom_scale((1968, 400)) == 0.5
    with pytest.raises(FastLaneError, match="x,y,w,h"):
        fast_lane.parse_region("100,50")


def test_verify_with_zoom_crops_both_sides_at_full_resolution(tmp_path, monkeypatch):
    snapshot = _layered()

    def prepare(inputs, *, project, client):
        return {"capture_snapshot": snapshot}

    def worker(jobs, run_dir, *, served=None, timeout=None):
        assert all(job["resolution"] is None for job in jobs)  # full resolution when zooming
        out = {"jobs": [], "notes": []}
        for job in jobs:
            frames = {}
            for f in job["frames"]:
                image = Image.new("RGB", (1920, 1080), "#204050")
                if job["name"] == "working":
                    ImageDraw.Draw(image).rectangle((1500, 150, 1503, 152), fill="#ffffff")  # a connector end moved
                path = Path(job["out"]) / "frames" / f"f{f:06d}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                image.save(path)
                frames[str(f)] = str(path)
            out["jobs"].append({"name": job["name"], "frames": frames, "owner": {}})
        return out

    class Draft:
        def document(self):
            return {}

    monkeypatch.setattr("astrid.sdk.timeline_filmstrip.prepare_filmstrip", prepare)
    monkeypatch.setattr("astrid.core.timeline.authoring_bundle.preview_authoring_candidate", lambda doc: {})
    monkeypatch.setattr(fast_lane, "run_worker", worker)
    monkeypatch.setattr(fast_lane, "materialize", lambda remote, reg, root=None: (reg, {}, 0))
    result = fast_lane.verify(object(), "demo", "tl", [Moment(3.8, "card")], draft=Draft(), zoom="card", root=tmp_path)
    (row,) = result["rows"]
    zoom = row["zoom"]
    x0, y0, x1, y1 = zoom["region"]
    with Image.open(zoom["published"]) as crop:
        assert crop.size == (x1 - x0, y1 - y0)  # canvas px, not a thumbnail
    assert "full resolution" in zoom["label"] and "box 1500,150–1504,153" in zoom["label"]
    assert any(line.strip().startswith("zoom card:") for line in result["lines"])


def test_python_and_the_cli_take_a_zoom(monkeypatch):
    from tests.sdk.test_timeline_checkout import bundle

    from astrid.packs.timeline import cli
    from astrid.sdk.timeline_checkout import Checkout

    seen = {}

    def fake(client, project, timeline, moments, **kwargs):
        seen.update(kwargs, moments=[round(m.t, 2) for m in moments])
        return {"lines": [], "rows": [], "page": "p.png"}

    monkeypatch.setattr(fast_lane, "verify", fake)
    tl = Checkout(bundle())
    tl.verify(at=6.0, zoom="a-rocket", client=object())
    assert seen["zoom"] == "a-rocket" and seen["moments"] == [6.0]
    rocket = tl.clip("a-rocket")
    tl.verify(zoom="a-rocket", client=object())  # nothing changed: a zoom alone looks at the layer's middle
    assert seen["moments"] == [round((rocket.start + rocket.end) / 2, 2)]
    before = tl.document()
    tl.clip("a-rocket").nudge(0.5)
    tl.verify(since=before, zoom="a-rocket", client=object())  # an edit: its changed moments, zoomed
    assert seen["moments"] != [round((rocket.start + rocket.end) / 2, 2)] and seen["zoom"] == "a-rocket"
    help_text = cli.build_parser(object())._subparsers._group_actions[0].choices["check"].format_help()
    assert "--zoom ADDRESS" in help_text and "--region X,Y,W,H" in help_text


# --- 5. a change hidden under a higher layer is not "the same picture" -------------------------

def test_a_changed_layer_under_an_opaque_higher_layer_is_named_as_hidden(tmp_path, monkeypatch):
    from tests.packs.rendering.test_timeline_visualize_motion import _clip

    occ = "occ-ch01"
    clips = [
        _clip("p1", "plate", "visual", 0.0, 2.0, occ, clipType="am-snap-plate", params={"zoom": 1}),
        _clip("churn", "sprite", "visual", 0.0, 2.0, occ, clipType="am-churn", params={},
              app={"cut": "c18", "layer": "churn"}),
        _clip("board", "fx", "visual", 0.0, 2.0, occ, clipType="am-ui-sketch", params={},
              app={"cut": "c18", "layer": "board"}),
    ]
    snapshot = _layered()
    snapshot.update(clips=clips, config={"tracks": snapshot["tracks"], "clips": [dict(c) for c in clips]},
                    occurrences=[{"occurrence_id": occ, "shot_id": "ch01", "shot_name": "01", "start": 0.0, "end": 2.0,
                                  "start_frame": 0, "end_frame": 60}])
    assert fast_lane.hidden_under(snapshot, "churn", 1.0, FPS) == "c18.board"
    assert fast_lane.hidden_under(snapshot, "board", 1.0, FPS) is None  # nothing above it

    moved = {**snapshot, "config": {**snapshot["config"],
                                    "clips": [dict(c, params={"speed": 2}) if c["id"].endswith("churn") else dict(c)
                                              for c in snapshot["config"]["clips"]]}}

    def prepare(inputs, *, project, client):
        return {"capture_snapshot": moved if "authoring_preview" in inputs else snapshot}

    def worker(jobs, run_dir, *, served=None, timeout=None):
        return {"jobs": [{"name": job["name"], "owner": {},
                          "frames": {str(f): _tile(Path(job["out"]) / "frames" / f"f{f}.png", box=False) for f in job["frames"]}}
                         for job in jobs], "notes": []}

    class Draft:
        def document(self):
            return {}

    monkeypatch.setattr("astrid.sdk.timeline_filmstrip.prepare_filmstrip", prepare)
    monkeypatch.setattr("astrid.core.timeline.authoring_bundle.preview_authoring_candidate", lambda doc: {})
    monkeypatch.setattr(fast_lane, "run_worker", worker)
    monkeypatch.setattr(fast_lane, "materialize", lambda remote, reg, root=None: (reg, {}, 0))
    result = fast_lane.verify(object(), "demo", "tl", [Moment(1.0, "c18.churn (params) mid-clip", f"{occ}:churn")],
                              draft=Draft(), root=tmp_path)
    assert result["rows"][0]["pixels"] == ("pixels: identical to published, but c18.churn changed and is hidden on "
                                           "this frame (under c18.board)")
