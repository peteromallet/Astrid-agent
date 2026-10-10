"""One-shot verify: published and working-copy frames at the edited moments, in seconds.

``timelines edit … --verify`` and ``timelines check TL --at MOMENT`` call :func:`verify`.
It is the FAST LANE for short, read-only captures:

- It runs in the client, not on the pack host. The host runs one task at a time, so a
  three-second capture used to wait behind renders and generations (22–91 s a view).
- It reads the published head and the working copy through the SDK (reads only) and keeps
  only the visual clips around the moments (frames carry no sound). So it fetches only a
  handful of media files, into a small content-addressed cache.
- It renders with the served checkout's code and the pack host's own warm Remotion frame
  owner: the same renderer identity, so the same bundle and the same Chrome. A warm
  capture costs the render alone, because the owner no longer rebundles per request. The
  global Remotion lock is taken only when the generated element registries must be
  rewritten, never for a render.

CPU and Chrome: at most one extra Chrome exists, the owner's, which visualize already keeps
warm for 5 minutes. The owner runs one request at a time. A verify renders 2–8 frames,
about 2–4 s of one core. A full render on the host may run at the same time: the two share
the CPU for those seconds, and neither waits for the other. Nothing is written to the
runtime; the caches are bounded (media 256 MiB, the last few verify pages).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

FAST_ROOT = Path(tempfile.gettempdir()) / "astrid-fast-lane"
OBJECT_CAP_BYTES = 256 * 1024 * 1024
KEEP_RUNS = 6
MAX_MOMENTS = 4
MARGIN_S = 3.0
RESOLUTION = (640, 360)
TILE = (480, 270)
WORKER_TIMEOUT_S = 240.0


class FastLaneError(RuntimeError):
    """The fast lane cannot capture here; the message says what to do instead."""


@dataclass(frozen=True)
class Moment:
    t: float  # timeline seconds
    label: str
    clip: str | None = None  # the clip id the moment is about (an edit's change), if any


# ---------------------------------------------------------------- which frames show an edit

def frame_of(t: float, fps: float) -> int:
    """The frame a moment lands on: floor, with the 0.02-frame epsilon the moment grammar uses."""
    return max(0, int(math.floor(float(t) * float(fps) + 0.02)))


def moments_from_changes(changes: Iterable[Mapping[str, Any]], fps: float, *, limit: int = MAX_MOMENTS,
                         names: Mapping[str, str] | None = None) -> list[Moment]:
    """The frames that show an edit (``timeline_cuts.diff_bundles(before, after)["changes"]``).

    A moved boundary is shown as it is now: its first frame and the frame before it (a
    moved end: the clip's last frame and the first frame without it). An added clip shows
    its first frame and the one before; a removed one, where it used to start; a change of
    params, asset or app only, the middle of the clip. ``names`` maps clip ids to the
    addresses the edit printed (``c30.cover``).
    """
    found: list[Moment] = []
    step = 1.0 / float(fps)
    for change in changes:
        raw = str(change.get("clip_id") or "?")
        name = (names or {}).get(raw) or (names or {}).get(raw.rsplit(":", 1)[-1]) or raw.rsplit(":", 1)[-1]
        before, after = change.get("before"), change.get("after")
        fields = set(change.get("fields") or ())
        if after is None and before is not None:
            found.append(Moment(float(before[0]), f"{name} removed (was from {before[0]:.2f} s)", raw))
            continue
        if after is None:
            continue
        start, end = float(after[0]), float(after[1])
        if before is None or "start" in fields:
            found += [Moment(start - step, f"{name}: the frame before it starts", raw), Moment(start, f"{name} starts", raw)]
        if before is not None and "end" in fields:
            found += [Moment(end - step, f"{name}: its last frame", raw),
                      Moment(end, f"{name}: the first frame without it", raw)]
        if before is not None and not fields & {"start", "end"}:
            found.append(Moment((start + end) / 2, f"{name} ({', '.join(sorted(fields)) or 'changed'}) mid-clip", raw))
    unique: dict[int, Moment] = {}
    for moment in found:
        if moment.t >= 0:
            unique.setdefault(frame_of(moment.t, fps), moment)
    return [unique[f] for f in sorted(unique)][:limit]


# ---------------------------------------------------------------- a small, renderable window

def _clip_span_frames(clip: Mapping[str, Any], fps: float) -> tuple[int, int] | None:
    from astrid.core.timeline.duration import clip_end_frame, clip_start_frame

    try:
        return clip_start_frame(clip, fps), clip_end_frame(clip, fps)
    except (TypeError, ValueError):
        return None


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def window_snapshot(config: Mapping[str, Any], registry: Mapping[str, Any], frames: Sequence[int], fps: float,
                    *, margin_s: float = MARGIN_S) -> tuple[dict[str, Any], dict[str, Any], int]:
    """The visual clips within ``margin_s`` of the frames, and only the assets they use.

    Audio is dropped (frames carry no sound; no element reads audio data). If the kept clips
    would end before the last frame (a moment in a gap), every visual clip is kept, so the
    composition is never shorter than the frame asked for. Returns (config, registry, dropped).
    """
    audio_tracks = {str(t.get("id")) for t in config.get("tracks") or () if isinstance(t, Mapping) and t.get("kind") == "audio"}
    clips = [c for c in config.get("clips") or () if isinstance(c, Mapping)]
    visual = [c for c in clips if str(c.get("track")) not in audio_tracks]
    lo, hi = min(frames) - margin_s * fps, max(frames) + margin_s * fps
    kept = []
    last_end = -1
    for clip in visual:
        span = _clip_span_frames(clip, fps)
        if span is None or (span[1] > lo and span[0] < hi):
            kept.append(clip)
            last_end = max(last_end, span[1] if span else 10**9)
    if last_end <= max(frames):
        kept = visual
    kept_ids = {id(c) for c in kept}
    out = {**dict(config), "clips": [dict(c) for c in clips if id(c) in kept_ids]}
    assets = dict((registry or {}).get("assets") or {})
    used = {s for clip in out["clips"] for s in _strings(clip) if s in assets}
    return out, {**dict(registry or {}), "assets": {k: v for k, v in assets.items() if k in used}}, len(clips) - len(kept)


# ---------------------------------------------------------------- media, through the SDK

def _digest_of(entry: Mapping[str, Any]) -> str:
    raw = str(entry.get("digest") or entry.get("content_sha256") or entry.get("media_id") or "")
    digest = raw.removeprefix("sha256:")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise FastLaneError(f"asset without a content digest: {dict(entry)}")
    return digest


def materialize(remote: Any, registry: Mapping[str, Any], *, root: Path | None = None,
                cap_bytes: int = OBJECT_CAP_BYTES) -> tuple[dict[str, Any], dict[str, str], int]:
    """Each asset's bytes in a content-addressed cache (``media.read_bytes``; reads only).

    Returns (registry with ``file`` paths, object map, bytes fetched now). The cache keeps
    the most recently used files under ``cap_bytes``.
    """
    objects_dir = (root or FAST_ROOT) / "objects"
    objects_dir.mkdir(parents=True, exist_ok=True)
    derived: dict[str, Any] = {}
    objects: dict[str, str] = {}
    fetched = 0
    for key, entry in ((registry or {}).get("assets") or {}).items():
        digest = _digest_of(entry)
        path = objects_dir / digest
        if not path.is_file():
            data = remote.media.read_bytes("sha256:" + digest)
            if hashlib.sha256(data).hexdigest() != digest:
                raise FastLaneError(f"media {key} did not match its digest")
            temporary = path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, path)
            fetched += len(data)
        else:
            os.utime(path, None)
        for name in (entry.get("object_id"), entry.get("media_id"), digest):
            if name:
                objects[str(name)] = str(path)
        derived[str(key)] = {**dict(entry), "file": str(path)}
    _prune(objects_dir, cap_bytes)
    return {**dict(registry or {}), "assets": derived}, objects, fetched


def _prune(directory: Path, cap_bytes: int) -> None:
    files = sorted((p for p in directory.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    total = 0
    for path in files:
        total += path.stat().st_size
        if total > cap_bytes:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------- the served renderer

def served_renderer(data_root: Path | None = None) -> dict[str, Any]:
    """The checkout and Python the pack host runs (``runtime/generic-host.json``)."""
    root = data_root or (Path(os.environ["BANODOCO_LOCAL_DATA_ROOT"]) if os.environ.get("BANODOCO_LOCAL_DATA_ROOT") else None)
    record: Mapping[str, Any] = {}
    if root is not None:
        try:
            record = json.loads((root / "runtime" / "generic-host.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = {}
    checkout = Path(str(record.get("source_checkout") or "")) if record.get("source_checkout") else None
    if checkout is None or not (checkout / "astrid").is_dir():
        from astrid.core.foundation.paths import REPO_ROOT

        checkout = Path(REPO_ROOT)
    python = str(record.get("python_executable") or sys.executable)
    pid = record.get("pid") if isinstance(record.get("pid"), int) else None
    return {"checkout": checkout, "python": python if Path(python).exists() else sys.executable, "pid": pid}


def host_renderer_env(pid: int | None) -> dict[str, str]:
    """The live pack host's renderer settings, read from its process environment (same user only).

    After a host restart there may be no frame-owner record yet; the host process itself
    always has the settings its captures use, so the fast lane reads them there.
    """
    from astrid.core.rendering.node_pin import process_env
    from astrid.packs.rendering.backends.remotion.run import OWNER_IDENTITY_ENV

    return process_env(pid, OWNER_IDENTITY_ENV)


def pinned_node(checkout: Path) -> str | None:
    """The Node the Remotion project pins (core/rendering/node_pin: the one resolver)."""
    from astrid.core.rendering.node_pin import resolve_pinned_node

    found = resolve_pinned_node(checkout / "remotion")
    return str(found.path) if found.ok else None


def worker_environment(checkout: Path, *, host_pid: int | None = None) -> tuple[dict[str, str], str]:
    """The pack host's renderer environment, so the worker reaches the same warm owner.

    In order: the live host process's settings (right after a restart too), the newest
    frame-owner record, this shell; a missing Node is then found by the Remotion
    project's version pin. The first capture starts the owner if none is running
    (~14 s once). Returns (env, where it came from).
    """
    from astrid.packs.rendering.backends.remotion.run import OWNER_IDENTITY_ENV, owner_records

    env = {k: v for k, v in os.environ.items() if k not in OWNER_IDENTITY_ENV and k != "PYTHONPATH"}
    live = host_renderer_env(host_pid)
    records = [] if live.get("ASTRID_NODE_EXECUTABLE") else owner_records(checkout / "remotion")
    if live.get("ASTRID_NODE_EXECUTABLE"):
        env.update(live)
        source = "the pack host's renderer (shared warm owner)"
    elif records:
        env.update({k: str(v) for k, v in (records[0].get("env") or {}).items() if k in OWNER_IDENTITY_ENV})
        source = "the pack host's last renderer record (shared warm owner)"
    else:
        env.update({k: os.environ[k] for k in OWNER_IDENTITY_ENV if k in os.environ})
        source = "this shell's renderer settings"
    if not env.get("ASTRID_NODE_EXECUTABLE"):
        node = pinned_node(checkout)
        if node is None:
            raise FastLaneError(
                "no Node for the renderer: the pack host is not running and no Node matches remotion/package.json's "
                "engines pin; export ASTRID_NODE_EXECUTABLE=<that Node>")
        env["ASTRID_NODE_EXECUTABLE"] = node
        source += f"; Node {node} by the project's version pin"
    env["PYTHONPATH"] = str(checkout)
    env.setdefault("ASTRID_INTERNAL_INVOCATION", "1")
    if host_pid and live:  # an owner this starts belongs to the running host and stops with it
        from astrid.packs.rendering.backends.remotion.run import OWNER_HOST_ENV

        env[OWNER_HOST_ENV] = str(host_pid)
    return env, source


def run_worker(jobs: list[dict[str, Any]], run_dir: Path, *, served: Mapping[str, Any] | None = None,
               timeout: float = WORKER_TIMEOUT_S) -> dict[str, Any]:
    """Capture every job with the served checkout's code (one subprocess, one owner)."""
    served = served or served_renderer()
    checkout = Path(served["checkout"])
    env, source = worker_environment(checkout, host_pid=served.get("pid"))
    request = run_dir / "request.json"
    request.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
    code = ("import sys; from astrid.packs.rendering.executors.timeline_visualize.fast_lane import _worker_main; "
            "sys.exit(_worker_main(sys.argv[1:]))")
    try:
        done = subprocess.run([str(served["python"]), "-c", code, str(request)], cwd=str(checkout), env=env,
                              capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise FastLaneError(f"capture took over {timeout:.0f} s") from exc
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("{")]
    if done.returncode != 0 or not lines:
        tail = (done.stderr.strip().splitlines() or ["no output"])[-1]
        raise FastLaneError(f"capture failed: {tail[:300]}")
    result = json.loads(lines[-1])
    result["environment_source"] = source
    return result


def _worker_main(argv: Sequence[str]) -> int:
    """In the served checkout: capture each job through the warm frame owner."""
    from astrid.core.pack.entrypoint import canonical_runtime_entrypoint

    request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    out: dict[str, Any] = {"jobs": [], "notes": []}
    with canonical_runtime_entrypoint("rendering.timeline_visualize"):
        from astrid.packs.rendering.executors.timeline_visualize.composed_frame import RemotionFrameProvider

        project_dir = Path(os.environ.get("ASTRID_REMOTION_PROJECT_DIR") or (Path.cwd() / "remotion"))
        for job in request["jobs"]:
            started = time.time()
            objects = json.loads(Path(job["objects"]).read_text(encoding="utf-8"))
            config = json.loads(Path(job["timeline"]).read_text(encoding="utf-8"))
            registry = json.loads(Path(job["registry"]).read_text(encoding="utf-8"))
            snapshot = {"fast_lane": {"config": config, "registry": registry}}
            provider = RemotionFrameProvider(
                snapshot, timeline_path=Path(job["timeline"]), assets_path=Path(job["registry"]),
                project_dir=project_dir, materialized_root=Path(job["materialized_root"]),
                materialized_objects=objects,
            )
            try:
                cards = [{"frame": int(f), "image": f"frames/f{int(f):06d}.png"} for f in job["frames"]]
                info = provider.capture(cards, Path(job["out"]), job.get("resolution"))
                session = getattr(getattr(provider.worker.renderer, "owner", None), "session", None)
                out["jobs"].append({
                    "name": job["name"],
                    "frames": {str(c["frame"]): str(Path(job["out"]) / c["image"]) for c in cards},
                    "cached": info.get("cached_frames"), "fresh": info.get("fresh_frames"),
                    "owner": dict(getattr(session, "last_timing", {}) or {}),
                    "seconds": round(time.time() - started, 2),
                })
                out["notes"].extend(info.get("notes") or [])
            finally:
                provider.close()
    print(json.dumps(out))
    return 0


# ---------------------------------------------------------------- what each side shows, in words

def _short(clip_id: Any) -> str:
    return str(clip_id or "?").rsplit(":", 1)[-1]


def clip_address(clip: Mapping[str, Any]) -> str:
    """``c41.sparks`` (cut.layer, the address edits print), else the clip's short id."""
    app = clip.get("app") if isinstance(clip.get("app"), Mapping) else {}
    cut, layer = app.get("cut"), app.get("layer")
    return f"{cut}.{layer}" if cut and layer else _short(clip.get("id"))


def _visual_clips(config: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    audio = {str(t.get("id")) for t in config.get("tracks") or () if isinstance(t, Mapping) and t.get("kind") == "audio"}
    return {_short(c.get("id")): c for c in config.get("clips") or ()
            if isinstance(c, Mapping) and str(c.get("track")) not in audio}


def _look(clip: Mapping[str, Any]) -> str:
    """What decides how a clip looks, besides time: its asset, params and track."""
    return json.dumps([clip.get("asset"), clip.get("params"), clip.get("track")], sort_keys=True, default=str)


def _on(clip: Mapping[str, Any] | None, frame: int, fps: float) -> bool:
    span = _clip_span_frames(clip, fps) if clip is not None else None
    return bool(span) and span[0] <= frame < span[1]


def describe_sides(moment: Moment, frame: int, fps: float, published: Mapping[str, Any],
                   working: Mapping[str, Any] | None) -> tuple[str, str]:
    """Plain words for what each side shows on this frame (``published: …``, ``working copy: …``).

    For an edited clip: on screen, not yet (with its start) or no longer (with its end), on
    each side. For a bare moment: the layers only one side has, and the ones that changed.
    """
    pub, work = _visual_clips(published), _visual_clips(working or {})
    if moment.clip:
        key = _short(moment.clip)
        clip = work.get(key) or pub.get(key)
        name = clip_address(clip) if clip else key

        def side(mine: Mapping[str, Any] | None, other: Mapping[str, Any] | None, *, yours: bool) -> str:
            if mine is None:
                return f"no {name} (not in this version)"
            span = _clip_span_frames(mine, fps)
            if span and frame < span[0]:
                return f"no {name} yet (it starts at {span[0] / fps:.2f} s)"
            if span and frame >= span[1]:
                return f"no {name} any more (it ended at {span[1] / fps:.2f} s)"
            fields = [f for f in ("asset", "params", "track") if other is not None and mine.get(f) != other.get(f)]
            if not fields:
                return f"{name} on screen"
            return f"{name} on screen with your change ({', '.join(fields)})" if yours else f"{name} on screen, before your change"

        return (side(pub.get(key), work.get(key), yours=False),
                side(work.get(key), pub.get(key), yours=True) if working is not None else "—")
    if working is None:
        return "what is published", "—"
    on_pub = {k for k, c in pub.items() if _on(c, frame, fps)}
    on_work = {k for k, c in work.items() if _on(c, frame, fps)}
    gone = sorted(clip_address(pub[k]) for k in on_pub - on_work)
    new = sorted(clip_address(work[k]) for k in on_work - on_pub)
    changed = sorted(clip_address(work[k]) for k in on_pub & on_work if _look(pub[k]) != _look(work[k])
                     or _clip_span_frames(pub[k], fps) != _clip_span_frames(work[k], fps))
    left = f"{', '.join(gone)} (not in the working copy here)" if gone else "the same layers"
    parts = ([f"{', '.join(new)} (new here)"] if new else []) + ([f"{', '.join(changed)} changed"] if changed else [])
    return left, "; ".join(parts) if parts else "the same layers, unchanged"


def _changed_on_screen(configs: Mapping[str, Mapping[str, Any]], frame: int, fps: float) -> list[str]:
    """Short ids of the clips on screen on both sides whose look or timing differs."""
    pub, work = _visual_clips(configs.get("published") or {}), _visual_clips(configs.get("working") or {})
    return [k for k in sorted(set(pub) & set(work)) if _on(pub[k], frame, fps) and _on(work[k], frame, fps)
            and (_look(pub[k]) != _look(work[k]) or _clip_span_frames(pub[k], fps) != _clip_span_frames(work[k], fps))]


def _canvas(config: Mapping[str, Any]) -> tuple[int, int]:
    canvas = ((config.get("theme_overrides") or {}).get("visual") or {}).get("canvas") or {}
    try:
        return int(canvas.get("width") or 1920), int(canvas.get("height") or 1080)
    except (TypeError, ValueError):
        return 1920, 1080


def _difference(a: Path, b: Path) -> tuple[float, int, tuple[int, int, int, int] | None, tuple[int, int]]:
    """(share of pixels that differ, how many, their bounding box, the image size), in the image's pixels."""
    from PIL import Image, ImageChops

    with Image.open(a) as left, Image.open(b) as right:
        left, right = left.convert("RGB"), right.convert("RGB")
        if left.size != right.size:
            right = right.resize(left.size)
        delta = ImageChops.difference(left, right).convert("L").point(lambda v: 255 if v > 8 else 0)
        box = delta.getbbox()
        count = delta.histogram()[255]
        size = left.size
    return count / float(size[0] * size[1]), count, box, size


def pixel_line(share: float, count: int, box: tuple[int, int, int, int] | None, size: tuple[int, int],
               canvas: tuple[int, int], *, offset: tuple[int, int] = (0, 0)) -> str:
    """``pixels: 3.04% differ, changed area (old ∪ new) 558,522–1527,723 (…)``; tiny changes say ``<0.1% (12 px)``."""
    if not box:
        return "pixels: identical to published"
    amount = f"{share * 100:.2f}%" if share >= 0.001 else f"<0.1% ({count} px)"
    sx, sy = canvas[0] / float(size[0]), canvas[1] / float(size[1])
    if offset != (0, 0):  # a zoom crop is already in canvas px
        sx = sy = 1.0
    x0, y0 = round(offset[0] + box[0] * sx), round(offset[1] + box[1] * sy)
    x1, y1 = round(offset[0] + box[2] * sx), round(offset[1] + box[3] * sy)
    return (f"pixels: {amount} differ, changed area (old ∪ new) {x0},{y0}–{x1},{y1} "
            f"({x1 - x0}x{y1 - y0} canvas px; where either side differs, not a layer's box)")


OPAQUE_KINDS = ("plate", "panel", "card")  # boxes that hide what is under them (sprites and type have holes)


def hidden_under(snapshot: Mapping[str, Any], key: str, t: float, fps: float, *, cover: float = 0.9) -> str | None:
    """The address of a higher, opaque layer covering ``cover`` of this clip's box at ``t``, if any.

    Stacking is by track, top first in the timeline's track list (chrome > type > fx > sprite >
    plate). Plates, panels and cards count as opaque; sprites and type do not.
    """
    from .motion import model
    from .motion.sheet import snapshot_elements

    order = [str(tr.get("id")) for tr in (snapshot.get("config") or {}).get("tracks") or snapshot.get("tracks") or ()
             if isinstance(tr, Mapping)]
    rank = {track: index for index, track in enumerate(order)}
    _occurrences, elements = snapshot_elements(snapshot)
    moment = t + 1e-6
    target = next((e for e in elements if _short(e.clip.get("id") or e.id) == _short(key) and not e.audio
                   and e.start - 1e-6 <= moment < e.end - 1e-6), None)
    if target is None or target.track not in rank:
        return None
    rects = [box.rect for box in model.boxes_at(target, moment, fps)]
    if not rects:
        return None
    mine = (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))
    if model.area(mine) <= 0:
        return None
    for element in sorted(elements, key=lambda e: rank.get(e.track, 99)):
        if element is target or element.audio or rank.get(element.track, 99) >= rank[target.track]:
            continue
        for box in model.boxes_at(element, moment, fps):
            if box.kind in OPAQUE_KINDS and model.intersection(box.rect, mine) >= cover * model.area(mine):
                return clip_address(element.clip) if element.clip else element.short_id
    return None


# ---------------------------------------------------------------- zoom: a layer at full resolution

ZOOM_WIDTH = 984  # the page's inner width: a zoom crop shows at 1× (or a whole-number enlargement) up to this


def parse_region(text: str) -> tuple[int, int, int, int]:
    """``x,y,w,h`` in canvas px → (x0, y0, x1, y1)."""
    try:
        x, y, w, h = (int(round(float(v))) for v in str(text).split(","))
    except ValueError as exc:
        raise FastLaneError(f"--region takes x,y,w,h in canvas px, got {text!r}") from exc
    if w <= 0 or h <= 0:
        raise FastLaneError(f"--region needs a positive width and height, got {text!r}")
    return x, y, x + w, y + h


def _matches(element: Any, address: str) -> bool:
    app = element.clip.get("app") if isinstance(element.clip.get("app"), Mapping) else {}
    wanted = address.strip()
    return (f"{app.get('cut')}.{app.get('layer')}" == wanted or element.short_id == wanted
            or str(element.id).endswith(wanted))


def zoom_region(snapshots: Sequence[Mapping[str, Any]], address: str, t: float, fps: float, canvas: tuple[int, int],
                *, pad: float = 0.25, minimum: int = 160) -> tuple[int, int, int, int] | None:
    """The layer's on-screen box on either side at ``t`` (their union), padded, in canvas px."""
    from .motion import model
    from .motion.sheet import snapshot_elements

    rects = []
    for snapshot in snapshots:
        _occurrences, elements = snapshot_elements(snapshot)
        for element in elements:
            if _matches(element, address):
                found = [box.rect for box in model.boxes_at(element, t + 1e-6, fps)]
                anchor = element.params.get("anchor") if isinstance(element.params.get("anchor"), Mapping) else None
                if found and anchor and all(isinstance(anchor.get(k), (int, float)) for k in ("x", "y")):
                    # a callout's connector ends at its anchor: keep the end in the crop
                    found.append((anchor["x"], anchor["y"], anchor["x"] + 1, anchor["y"] + 1))
                rects += found
    if not rects:
        return None
    x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
    x1, y1 = max(r[2] for r in rects), max(r[3] for r in rects)
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    grow_x, grow_y = max(w * pad, (minimum - w) / 2), max(h * pad, (minimum - h) / 2)
    return (max(0, int(x0 - grow_x)), max(0, int(y0 - grow_y)),
            min(canvas[0], int(math.ceil(x1 + grow_x))), min(canvas[1], int(math.ceil(y1 + grow_y))))


def _crop(path: Path, region: tuple[int, int, int, int], out: Path) -> Path:
    from PIL import Image

    with Image.open(path) as image:
        image.convert("RGB").crop(region).save(out)
    return out


# ---------------------------------------------------------------- the page

def zoom_scale(size: tuple[int, int], *, width: int = ZOOM_WIDTH, height: int = 420) -> float:
    """1× when it fits (full resolution), a whole-number enlargement when small, a reduction only when wider."""
    if size[0] > width:
        return width / float(size[0])
    return float(max(1, min(6, int(width // size[0]), int(height // max(1, size[1])))))


def _zoom_tile(path: str | Path, scale: float) -> Any:
    from PIL import Image

    with Image.open(path) as source:
        image = source.convert("RGB")
        size = (max(1, round(image.size[0] * scale)), max(1, round(image.size[1] * scale)))
        return image.resize(size, Image.NEAREST if scale >= 1 else Image.LANCZOS)


def compose_pair_page(rows: Sequence[Mapping[str, Any]], out: Path, *, title: str, footer: Sequence[str] = ()) -> Path:
    """One block per moment: published | working copy, what each shows, what differs; a zoom pair when asked."""
    from PIL import Image, ImageDraw

    from .layers.base import PALETTE, draw_text

    tile_w, tile_h = TILE
    width = 24 + 2 * (tile_w + 12)
    def zoom_height(row: Mapping[str, Any]) -> int:
        zoom = row.get("zoom") or {}
        if not zoom.get("published"):
            return 26 if zoom else 0
        region = zoom["region"]
        scale = zoom_scale((region[2] - region[0], region[3] - region[1]))
        return 2 * (round((region[3] - region[1]) * scale) + 24) + 26

    heights = [tile_h + 70 + zoom_height(row) for row in rows]
    height = 64 + sum(heights) + 22 * len(footer) + 16
    page = Image.new("RGB", (width, height), PALETTE["bg"])
    draw = ImageDraw.Draw(page)
    draw_text(draw, (12, 10), title, 20, "white")
    draw_text(draw, (12, 38), "left: published · right: working copy · the same frame of the timeline", 13, PALETTE["muted"])
    top = 64
    for row, block in zip(rows, heights):
        for column, key in enumerate(("published", "working")):
            x = 12 + column * (tile_w + 12)
            path = row.get(key)
            if path and Path(path).is_file():
                with Image.open(path) as source:
                    page.paste(source.convert("RGB").resize((tile_w, tile_h), Image.LANCZOS), (x, top))
            else:
                draw.rectangle((x, top, x + tile_w, top + tile_h), outline=PALETTE["grid_strong"])
                draw_text(draw, (x + 8, top + 8), "no frame", 13, PALETTE["muted"])
        box = row.get("box")
        if box:
            sx, sy = tile_w / float(row["size"][0]), tile_h / float(row["size"][1])
            x = 12 + tile_w + 12
            draw.rectangle((x + box[0] * sx - 2, top + box[1] * sy - 2, x + box[2] * sx + 2, top + box[3] * sy + 2),
                           outline="#f59e0b", width=2)
        y = top + tile_h + 6
        draw_text(draw, (12, y), f"{row['label']}  ·  {row['t']:.2f} s, frame {row['frame']}", 13, PALETTE["ink"])
        draw_text(draw, (12, y + 20), f"published: {row['published_shows']}   ·   working copy: {row['working_shows']}", 12,
                  PALETTE["ink"])
        draw_text(draw, (12, y + 38), row["pixels"], 12, PALETTE["muted"])
        zoom = row.get("zoom")
        if zoom:
            ztop = top + tile_h + 64
            draw_text(draw, (12, ztop), zoom["label"][:160], 12, PALETTE["muted"])
            ztop += 22
            if zoom.get("published"):
                region = zoom["region"]
                scale = zoom_scale((region[2] - region[0], region[3] - region[1]))
                for key, name in (("published", "published"), ("working", "working copy")):
                    if not zoom.get(key):
                        continue
                    tile = _zoom_tile(zoom[key], scale)
                    draw_text(draw, (12, ztop), f"{name} · shown at {scale:g}x (1x = full resolution)", 12, PALETTE["ink"])
                    page.paste(tile, (12, ztop + 18))
                    change = zoom.get("box") if key == "working" else None
                    if change:
                        draw.rectangle((12 + change[0] * scale - 2, ztop + 18 + change[1] * scale - 2,
                                        12 + change[2] * scale + 2, ztop + 18 + change[3] * scale + 2),
                                       outline="#f59e0b", width=2)
                    ztop += tile.size[1] + 24
        top += block
    y = top + 4
    for line in footer:
        draw_text(draw, (12, y), line[:150], 13, PALETTE["ink"])
        y += 22
    page.save(out)
    return out


# ---------------------------------------------------------------- one call

def _prune_runs(root: Path, keep: int = KEEP_RUNS) -> None:
    runs = sorted((p for p in root.glob("verify-*") if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in runs[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def verify(client: Any, project: str, timeline: str, moments: Sequence[Moment], *, draft: Any = None,
           footer: Sequence[str] = (), resolution: Sequence[int] = RESOLUTION, root: Path | None = None,
           served: Mapping[str, Any] | None = None, zoom: str | None = None,
           region: Sequence[int] | str | None = None) -> dict[str, Any]:
    """Published vs working copy at each moment, as one page. Reads only; no task, no queue.

    ``draft`` is the working copy (a ``Checkout``); without one the page shows the published
    head alone. ``zoom`` (an address, ``c08.app``) or ``region`` (``x,y,w,h`` canvas px) adds
    a full-resolution crop of both sides under each moment. Returns ``page``, ``rows``
    (frame, label, what each side shows, pixels), ``timing``, ``notes`` and ``lines``.
    """
    from astrid.sdk.timeline_filmstrip import prepare_filmstrip

    if not moments:
        raise FastLaneError("no moment to verify")
    fixed_region = parse_region(region) if isinstance(region, str) else (
        (int(region[0]), int(region[1]), int(region[0]) + int(region[2]), int(region[1]) + int(region[3]))
        if region is not None else None)
    root = root or FAST_ROOT
    root.mkdir(parents=True, exist_ok=True)
    started = time.time()
    remote = getattr(client, "_remote", client)
    base = {"timeline_slug": timeline, "composed_capture": True, "view": "filmstrip"}
    sides = [("published", prepare_filmstrip(base, project=project, client=remote))]
    if draft is not None:
        from astrid.core.timeline.authoring_bundle import preview_authoring_candidate

        preview = preview_authoring_candidate(draft.document())
        sides.append(("working", prepare_filmstrip({**base, "authoring_preview": preview}, project=project, client=remote)))
    read_s = time.time() - started
    snapshots = {name: authority["capture_snapshot"] for name, authority in sides}
    fps = float(Fraction(*snapshots["published"]["fps_rational"]))
    canvas = _canvas(snapshots["published"].get("config") or {})
    moments = list(moments)[:MAX_MOMENTS]
    frames = sorted({frame_of(m.t, fps) for m in moments})
    full = bool(zoom or fixed_region)  # a zoom crops the full-resolution frame
    run_dir = Path(tempfile.mkdtemp(prefix="verify-", dir=root))
    jobs, fetched, dropped, windowed = [], 0, 0, {}
    for name, snapshot in snapshots.items():
        config, registry, cut = window_snapshot(snapshot["config"], snapshot["registry"], frames, fps)
        windowed[name] = config
        registry, objects, got = materialize(remote, registry, root=root)
        fetched, dropped = fetched + got, max(dropped, cut)
        side = run_dir / name
        side.mkdir()
        for filename, value in (("timeline.json", config), ("registry.json", registry), ("objects.json", objects)):
            (side / filename).write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        jobs.append({"name": name, "timeline": str(side / "timeline.json"), "registry": str(side / "registry.json"),
                     "objects": str(side / "objects.json"), "materialized_root": str(root / "objects"),
                     "frames": frames, "resolution": None if full else list(resolution), "out": str(side)})
    media_s = time.time() - started - read_s
    captured = run_worker(jobs, run_dir, served=served)
    render_s = time.time() - started - read_s - media_s
    by_side = {job["name"]: job for job in captured["jobs"]}
    rows = []
    for index, moment in enumerate(moments):
        frame = frame_of(moment.t, fps)
        published = (by_side.get("published") or {}).get("frames", {}).get(str(frame))
        working = (by_side.get("working") or {}).get("frames", {}).get(str(frame))
        shows = describe_sides(moment, frame, fps, windowed["published"], windowed.get("working"))
        row = {"t": moment.t, "frame": frame, "label": moment.label, "published": published, "working": working,
               "published_shows": shows[0], "working_shows": shows[1], "box": None, "size": None, "zoom": None}
        if published and working:
            share, count, box, size = _difference(Path(published), Path(working))
            row.update(box=box, size=size, pixels=pixel_line(share, count, box, size, canvas))
            if box is None:  # the same picture: say so only if the change could have been seen
                keys = [moment.clip] if moment.clip else _changed_on_screen(windowed, frame, fps)
                for key in keys:
                    cover = hidden_under(snapshots["working"], key, moment.t, fps)
                    if cover:
                        name = clip_address(_visual_clips(windowed["working"]).get(_short(key)) or {"id": key})
                        row["pixels"] = (f"pixels: identical to published, but {name} changed and is hidden on this "
                                         f"frame (under {cover})")
                        break
        else:
            row["pixels"] = "pixels: published only (no working copy)"
        if full and published:
            area = fixed_region or zoom_region(list(snapshots.values()), str(zoom), moment.t, fps, canvas)
            if area is None:
                row["zoom"] = {"label": f"zoom {zoom}: not on screen at this moment (try --region x,y,w,h)"}
            else:
                crops = {key: _crop(Path(path), area, run_dir / f"zoom-{index}-{key}.png")
                         for key, path in (("published", published), ("working", working)) if path}
                what = zoom or "region"
                label = f"zoom {what}: {area[0]},{area[1]}–{area[2]},{area[3]} canvas px at full resolution"
                change = None
                if len(crops) == 2:
                    share, count, change, size = _difference(crops["published"], crops["working"])
                    label += " · " + pixel_line(share, count, change, size, canvas, offset=(area[0], area[1]))
                row["zoom"] = {**{k: str(v) for k, v in crops.items()}, "region": area, "label": label, "box": change}
        row["verdict"] = f"published: {shows[0]} · working copy: {shows[1]} · {row['pixels']}"
        rows.append(row)
    page = compose_pair_page(rows, run_dir / "verify.png", title=f"verify {timeline} · {len(rows)} moment(s)",
                             footer=list(footer))
    for job in jobs:  # keep the page (and any zoom crops); the frames are in the bounded frame cache already
        shutil.rmtree(Path(job["out"]) / "frames", ignore_errors=True)
    _prune_runs(root)
    total = time.time() - started
    owner = [job.get("owner") or {} for job in captured["jobs"]]
    timing = {"total_s": round(total, 1), "read_s": round(read_s, 1), "media_s": round(media_s, 1),
              "render_s": round(render_s, 1), "fetched_bytes": fetched, "owner_bundled": any(o.get("bundled") for o in owner),
              "cached_frames": sum(int(j.get("cached") or 0) for j in captured["jobs"]),
              "fresh_frames": sum(int(j.get("fresh") or 0) for j in captured["jobs"])}
    result = {"page": str(page), "rows": rows, "timing": timing, "notes": list(captured.get("notes") or []),
              "environment_source": captured.get("environment_source"), "dropped_clips": dropped}
    result["lines"] = verify_lines(result)
    return result


def verify_lines(result: Mapping[str, Any]) -> list[str]:
    """What the CLI prints: per moment what each side shows and what differs; the page; where the time went."""
    timing = result["timing"]
    lines = []
    for row in result["rows"]:
        lines.append(f"verify  {row['label']} · {row['t']:.2f} s (f{row['frame']})")
        lines.append(f"        published: {row['published_shows']} · working copy: {row['working_shows']}")
        lines.append(f"        {row['pixels']}")
        if row.get("zoom"):
            lines.append(f"        {row['zoom']['label']}")
    lines.append(f"page    {result['page']}   (left published, right working copy; read it)")
    note = "cold renderer: started once, the next verify is warm" if timing.get("owner_bundled") else "warm renderer"
    lines.append(f"time    {timing['total_s']:.1f} s = read {timing['read_s']:.1f} + media {timing['media_s']:.1f} + "
                 f"render {timing['render_s']:.1f} ({timing['fresh_frames']} new frames, {timing['cached_frames']} cached; "
                 f"{note}; fast lane, no queue)")
    lines.extend(str(n) for n in result.get("notes") or ())
    return lines
