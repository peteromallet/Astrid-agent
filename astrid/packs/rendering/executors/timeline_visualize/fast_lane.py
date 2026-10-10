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
            found.append(Moment(float(before[0]), f"{name} removed (was from {before[0]:.2f} s)"))
            continue
        if after is None:
            continue
        start, end = float(after[0]), float(after[1])
        if before is None or "start" in fields:
            found += [Moment(start - step, f"{name}: the frame before it starts"), Moment(start, f"{name} starts")]
        if before is not None and "end" in fields:
            found += [Moment(end - step, f"{name}: its last frame"), Moment(end, f"{name}: the first frame without it")]
        if before is not None and not fields & {"start", "end"}:
            found.append(Moment((start + end) / 2, f"{name} ({', '.join(sorted(fields)) or 'changed'}) mid-clip"))
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
    return {"checkout": checkout, "python": python if Path(python).exists() else sys.executable}


def worker_environment(checkout: Path) -> tuple[dict[str, str], str]:
    """The pack host's renderer environment, so the worker reaches the same warm owner.

    Source: the newest frame-owner record for this checkout's Remotion project (written
    when the host launches an owner), else this shell. Returns (env, where it came from).
    """
    from astrid.packs.rendering.backends.remotion.run import OWNER_IDENTITY_ENV, owner_records

    records = owner_records(checkout / "remotion")
    env = {k: v for k, v in os.environ.items() if k not in OWNER_IDENTITY_ENV and k != "PYTHONPATH"}
    if records:
        env.update({k: str(v) for k, v in (records[0].get("env") or {}).items() if k in OWNER_IDENTITY_ENV})
        source = "the pack host's renderer (shared warm owner)"
    else:
        env.update({k: os.environ[k] for k in OWNER_IDENTITY_ENV if k in os.environ})
        source = "this shell's renderer settings (no host owner record yet)"
    if not env.get("ASTRID_NODE_EXECUTABLE"):
        raise FastLaneError(
            "the fast lane needs the renderer's Node: run one `timelines visualize` (the host records it), "
            "or export ASTRID_NODE_EXECUTABLE=<Node 20.19.4>")
    env["PYTHONPATH"] = str(checkout)
    env.setdefault("ASTRID_INTERNAL_INVOCATION", "1")
    return env, source


def run_worker(jobs: list[dict[str, Any]], run_dir: Path, *, served: Mapping[str, Any] | None = None,
               timeout: float = WORKER_TIMEOUT_S) -> dict[str, Any]:
    """Capture every job with the served checkout's code (one subprocess, one owner)."""
    served = served or served_renderer()
    checkout = Path(served["checkout"])
    env, source = worker_environment(checkout)
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


# ---------------------------------------------------------------- the page

def _difference(a: Path, b: Path) -> tuple[float, tuple[int, int, int, int] | None]:
    """Share of pixels that differ, and their bounding box (in the tile's own pixels)."""
    from PIL import Image, ImageChops

    with Image.open(a) as left, Image.open(b) as right:
        left, right = left.convert("RGB"), right.convert("RGB")
        if left.size != right.size:
            right = right.resize(left.size)
        delta = ImageChops.difference(left, right).convert("L").point(lambda v: 255 if v > 24 else 0)
        box = delta.getbbox()
        changed = sum(delta.histogram()[255:]) / float(left.size[0] * left.size[1])
    return changed, box


def compose_pair_page(rows: Sequence[Mapping[str, Any]], out: Path, *, title: str, footer: Sequence[str] = ()) -> Path:
    """One row per moment: published | working copy, with what differs."""
    from PIL import Image, ImageDraw

    from .layers.base import PALETTE, draw_text

    tile_w, tile_h = TILE
    row_h = tile_h + 46
    width = 24 + 2 * (tile_w + 12)
    height = 64 + row_h * max(1, len(rows)) + 22 * len(footer) + 16
    page = Image.new("RGB", (width, height), PALETTE["bg"])
    draw = ImageDraw.Draw(page)
    draw_text(draw, (12, 10), title, 20, "white")
    draw_text(draw, (12, 38), "left: published · right: working copy · same frame of the timeline", 13, PALETTE["muted"])
    for index, row in enumerate(rows):
        top = 64 + index * row_h
        for column, key in enumerate(("published", "working")):
            x = 12 + column * (tile_w + 12)
            path = row.get(key)
            if path and Path(path).is_file():
                with Image.open(path) as source:
                    page.paste(source.convert("RGB").resize((tile_w, tile_h)), (x, top))
            else:
                draw.rectangle((x, top, x + tile_w, top + tile_h), outline=PALETTE["grid_strong"])
                draw_text(draw, (x + 8, top + 8), "no frame", 13, PALETTE["muted"])
        box = row.get("box")
        if box:
            sx, sy = tile_w / float(row["size"][0]), tile_h / float(row["size"][1])
            x = 12 + tile_w + 12
            draw.rectangle((x + box[0] * sx, top + box[1] * sy, x + box[2] * sx, top + box[3] * sy), outline="#f59e0b", width=2)
        draw_text(draw, (12, top + tile_h + 6), f"{row['label']}  ·  {row['t']:.2f} s, frame {row['frame']}", 13, PALETTE["ink"])
        draw_text(draw, (12, top + tile_h + 24), row["verdict"], 12, PALETTE["muted"])
    y = 64 + row_h * max(1, len(rows)) + 4
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
           served: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Published vs working copy at each moment, as one page. Reads only; no task, no queue.

    ``draft`` is the working copy (a ``Checkout``); without one the page shows the
    published head alone. Returns ``page``, ``rows`` (frame, label, verdict), ``timing``,
    ``notes`` and ``lines`` (the human summary).
    """
    from astrid.sdk.timeline_filmstrip import prepare_filmstrip

    if not moments:
        raise FastLaneError("no moment to verify")
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
    fps = float(Fraction(*sides[0][1]["capture_snapshot"]["fps_rational"]))
    moments = list(moments)[:MAX_MOMENTS]
    frames = sorted({frame_of(m.t, fps) for m in moments})
    run_dir = Path(tempfile.mkdtemp(prefix="verify-", dir=root))
    jobs, fetched, dropped = [], 0, 0
    for name, authority in sides:
        snapshot = authority["capture_snapshot"]
        config, registry, cut = window_snapshot(snapshot["config"], snapshot["registry"], frames, fps)
        registry, objects, got = materialize(remote, registry, root=root)
        fetched, dropped = fetched + got, max(dropped, cut)
        side = run_dir / name
        side.mkdir()
        for filename, value in (("timeline.json", config), ("registry.json", registry), ("objects.json", objects)):
            (side / filename).write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        jobs.append({"name": name, "timeline": str(side / "timeline.json"), "registry": str(side / "registry.json"),
                     "objects": str(side / "objects.json"), "materialized_root": str(root / "objects"),
                     "frames": frames, "resolution": list(resolution), "out": str(side)})
    media_s = time.time() - started - read_s
    captured = run_worker(jobs, run_dir, served=served)
    render_s = time.time() - started - read_s - media_s
    by_side = {job["name"]: job for job in captured["jobs"]}
    rows = []
    for moment in moments:
        frame = frame_of(moment.t, fps)
        published = (by_side.get("published") or {}).get("frames", {}).get(str(frame))
        working = (by_side.get("working") or {}).get("frames", {}).get(str(frame))
        row = {"t": moment.t, "frame": frame, "label": moment.label, "published": published, "working": working,
               "box": None, "size": None}
        if published and working:
            share, box = _difference(Path(published), Path(working))
            from PIL import Image

            with Image.open(published) as image:
                row["size"] = image.size
            row["box"] = box
            row["verdict"] = ("same picture as published" if not box else
                              f"differs from published: {share:.1%} of pixels, box {box[0]},{box[1]}–{box[2]},{box[3]} "
                              f"of {row['size'][0]}x{row['size'][1]}")
        else:
            row["verdict"] = "published only (no working copy)"
        rows.append(row)
    page = compose_pair_page(rows, run_dir / "verify.png", title=f"verify {timeline} · {len(rows)} moment(s)",
                             footer=list(footer))
    for job in jobs:  # keep the page; the frames are in the bounded frame cache already
        shutil.rmtree(Path(job["out"]) / "frames", ignore_errors=True)
    _prune_runs(root)
    total = time.time() - started
    owner = [job.get("owner") or {} for job in captured["jobs"]]
    bundled = any(o.get("bundled") for o in owner)
    timing = {"total_s": round(total, 1), "read_s": round(read_s, 1), "media_s": round(media_s, 1),
              "render_s": round(render_s, 1), "fetched_bytes": fetched, "owner_bundled": bundled,
              "cached_frames": sum(int(j.get("cached") or 0) for j in captured["jobs"]),
              "fresh_frames": sum(int(j.get("fresh") or 0) for j in captured["jobs"])}
    result = {"page": str(page), "rows": rows, "timing": timing, "notes": list(captured.get("notes") or []),
              "environment_source": captured.get("environment_source"), "dropped_clips": dropped}
    result["lines"] = verify_lines(result)
    return result


def verify_lines(result: Mapping[str, Any]) -> list[str]:
    """What the CLI prints: one line per moment, the page, and where the time went."""
    timing = result["timing"]
    lines = [f"verify  {row['label']} · {row['t']:.2f} s (f{row['frame']}): {row['verdict']}" for row in result["rows"]]
    lines.append(f"page    {result['page']}   (left published, right working copy; read it)")
    note = "cold renderer: bundled once, the next verify is warm" if timing.get("owner_bundled") else "warm renderer"
    lines.append(f"time    {timing['total_s']:.1f} s = read {timing['read_s']:.1f} + media {timing['media_s']:.1f} + "
                 f"render {timing['render_s']:.1f} ({timing['fresh_frames']} new frames, {timing['cached_frames']} cached; "
                 f"{note}; fast lane, no queue)")
    lines.extend(str(n) for n in result.get("notes") or ())
    return lines
