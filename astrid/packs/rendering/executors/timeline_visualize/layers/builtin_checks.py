"""Built-in checks (conditions). Packs add more the same way (``CHECK = Check(...)``)."""
from __future__ import annotations

from ..motion import lint as rules
from ..motion import model
from .base import Check, CheckContext, Finding, register_check


def _composition(ctx: CheckContext) -> list[Finding]:
    return rules.composition(ctx.cut, ctx.elements, ctx.fps, track_order=ctx.track_order,
                             params=ctx.params, tracks=ctx.tracks)


def _timing(ctx: CheckContext) -> list[Finding]:
    return rules.timing(ctx.cut, ctx.elements, ctx.words, ctx.fps, beats=ctx.beats, sfx=ctx.sfx, params=ctx.params)


def _max_cut(ctx: CheckContext) -> list[Finding]:
    limit = ctx.param("max_cut_s")
    if limit is None or ctx.cut.get("deliberate_hold"):
        return []
    duration = float(ctx.cut["duration"])
    if duration <= float(limit) + 1e-6:
        return []
    return [ctx.finding("LONG", f"{duration:.2f} s is over max_cut_s = {float(limit):g} "
                        "(split it, or mark the picture clip app.deliberate_hold: true)")]


def _presenter_share(ctx: CheckContext) -> list[Finding]:
    limit = ctx.param("max_presenter_share")
    if limit is None or not ctx.cuts:
        return []
    total = sum(float(c["duration"]) for c in ctx.cuts) or 1.0
    presenter_cuts = []
    for cut in ctx.cuts:
        on = [e for e in model.in_window(ctx.all_elements, float(cut["start"]), float(cut["end"]))
              if e.type == "am-presenter"]
        if on:
            presenter_cuts.append(cut)
    share = sum(float(c["duration"]) for c in presenter_cuts) / total
    if share <= float(limit) + 1e-9:
        return []
    numbers = ", ".join(str(c["index"]) for c in presenter_cuts[:12])
    return [Finding("SHARE", None, None, f"presenter on screen {share:.0%} of the runtime (max_presenter_share = "
                    f"{float(limit):.0%}); presenter cuts: {numbers}", "warn", None, "presenter-share")]


def _vo_level(ctx: CheckContext) -> list[Finding]:
    """Loudness jumps between consecutive VO clips, from ``loudness`` data tracks."""
    from ..motion import data

    limit = ctx.param("max_vo_jump_db")
    if limit is None:
        return []
    vo_ids = {e.id: e for e in ctx.all_elements if e.audio and e.track not in ("music", "sfx")}
    levels = []
    for track in data.by_name(ctx.tracks, "loudness"):
        element = vo_ids.get(track.clip_id)
        if element is None or track.kind != "series":
            continue
        voiced = [v for _t, v in track.series if v > -60]
        if voiced:
            levels.append((element.start, element, sum(voiced) / len(voiced)))
    levels.sort(key=lambda row: row[0])
    found = []
    for (_t0, a, la), (t1, b, lb) in zip(levels, levels[1:]):
        jump = lb - la
        if abs(jump) > float(limit):
            volume = float(b.clip.get("volume") if b.clip.get("volume") is not None else 1.0)
            target = round(volume * 10 ** (-jump / 20), 3)
            found.append(Finding("LEVEL", None, t1, f"VO {b.short_id} is {jump:+.1f} dB vs {a.short_id} "
                                 f"(max_vo_jump_db = {float(limit):g}); set volume {volume:g} → {target:g}",
                                 "warn", {"clip": b.short_id, "set": {"volume": target}}, "vo-level"))
    return found


register_check(Check(
    "composition", "a layer over a face (presenter anchors or a 'face' data track), text off-frame or outside "
    "title-safe, text below min_text_px, layers covering each other, sprites cropped by the frame",
    _composition, params={"min_text_px": 32.0, "face_fraction": 0.05},
    codes=("FACE", "FRAME", "SAFE", "SMALL", "COVER", "EDGE")))
register_check(Check(
    "timing", "accents off their word onset, sfx off their visual, accents just off a music hit, short cuts, "
    "stretches with no event", _timing,
    params={"stamp_to_word_max_s": 0.10, "sync_search_s": 0.35, "short_cut_s": 0.5, "hold_s": 2.5,
            "beat_near_miss_s": 0.2},
    codes=("SYNC", "SFX", "BEAT", "SHORT", "HOLD")))
register_check(Check(
    "max-cut", "cuts longer than max_cut_s, unless the picture clip is marked app.deliberate_hold", _max_cut,
    params={"max_cut_s": None}, codes=("LONG",)))
register_check(Check(
    "presenter-share", "share of the runtime with the presenter on screen", _presenter_share,
    params={"max_presenter_share": None}, scope="timeline", codes=("SHARE",)))
register_check(Check(
    "vo-level", "loudness jumps between consecutive VO clips (reads 'loudness' series data tracks)", _vo_level,
    params={"max_vo_jump_db": 4.0}, scope="timeline", needs=("doc", "audio"), codes=("LEVEL",)))
