"""Product timelines family CLI (m4 plan step 26, task T28).

This module is the product parser for the runtime-owned ``timelines``
family. Every verb is **argument parsing plus exactly one SDK
call** on the composed :class:`~astrid.sdk.client.AstridClient` (stamped
onto every subparser by
:func:`astrid.core.cli.registration.register_product_commands`), and every
handler renders through the shared product output layer
(:mod:`astrid.core.cli.domain_output`) so the exact five-key JSON envelope,
concise human output, and stable exit codes stay aligned with the frozen SDK
contract.

The parser also mounts the runtime-owned nested ``shots`` family beneath
``timelines``: ``astrid timelines shots <verb>`` embeds the shots
product parser (``astrid/packs/shots/cli.py``) so project-level reusable shot
``list/create/show/add/remove/reorder`` commands are executable only beneath timelines
(plan step 26, task T29). There is **no top-level shots family**.

Verbs (the product routes plus the nested ``shots`` mount; reads resolve the
Runtime-owned canonical current head, while the only supported timeline
mutation is a parent-composition candidate publish):

- ``list`` — ``client.timelines.list`` (active timelines only), rendered as
  compact identity/count summaries; use ``show`` for the canonical current
  head inspection;
- ``show`` — the Runtime-owned bounded current-head inspection by UUID, ULID,
  or slug; ``--summary`` is retained as a presentation spelling and never
  exposes the legacy document;
- ``replace-parent-media`` — replace one selected clip in the canonical
  parent-composition closure through ``client.timelines.replace_parent_media``;
- ``archive`` — reversible event-backed ``client.timelines.archive``;
- ``recover`` — idempotent recovery through ``client.timelines.recover``;
- ``history`` — ordered lifecycle events (read);
- ``diff`` — deterministic adjacent-version diffs (read).
- ``visualize`` — the single public native timeline visualization operation
  (declared inputs immediately, or an exactly matched composed view when one
  already exists).
- ``render`` — version-pinned kernel timeline render through the explicit
  ``rendering.render`` `timeline_ref` mode.

**Negative routes (sense check SC28):** the legacy timeline verbs
``migration``, ``push``, ``pull``, ``sync``, ``audit``, ``erase``, and
``repair`` are **absent** from this product parser, as are all obsolete
aliases (``ls``, ``tl``, ...), and ``copy`` is **absent** — the reserved
save-as-copy route is contractually deferred to m6 (plan step 2 / watch
item) and must never be registered here.

This module contains **no SQL**, **no repository logic**, and **no
domain rules**: it parses argv, makes one SDK call, and renders the
returned envelope.
"""

from __future__ import annotations

import argparse
import json
import shlex
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path
from typing import Any

from astrid.core.cli.domain_output import DomainResult, envelope_dict, print_result, render_human
from astrid.core.cli.registration import CommandSpec, register_product_commands
from astrid.core.cli.task_progress import task_handoff

__all__ = ["COMMANDS", "build_parser"]

_FAMILY = "timelines"


def _parse_json_object(value: str) -> dict[str, Any]:
    """Parse a ``--config``/``--registry`` JSON object argument."""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON object: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return parsed


def _add_json_flag(subparser: argparse.ArgumentParser, *, default: bool = True) -> None:
    subparser.add_argument(
        "--json",
        action="store_true",
        default=default,
        help=(
            "Print the exact SDK envelope (ok/data/error/receipt/idempotency_key)."
            if not default
            else "Print the exact SDK envelope (ok/data/error/receipt/idempotency_key); default output."
        ),
    )


def _add_idempotency_key(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--idempotency-key",
        dest="idempotency_key",
        default=None,
        help="Caller idempotency key (a fresh key is generated when absent).",
    )


def _add_project_arg(
    subparser: argparse.ArgumentParser, *, required: bool = True
) -> None:
    subparser.add_argument(
        "--project",
        required=required,
        default=None,
        help=(
            "Owning project id or immutable slug. When omitted, use the "
            "workspace runtime's current project."
            if not required
            else "Owning project id or immutable slug."
        ),
    )


# -- handlers (one SDK call each, no domain rules) -------------------------


def _cmd_list(parsed: argparse.Namespace) -> int:
    result = parsed.client.timelines.list(
        parsed.project, include_archived=parsed.include_archived
    )
    # Listing is a discovery route. Keep the identity/count view compact and
    # leave current-head content to the canonical ``show`` inspection route.
    if result.ok and isinstance(result.data, (list, tuple)):
        from astrid.sdk.contracts import DomainResult

        data: Any = list(result.data)
        # Runtime list reads are cursor pages: [rows, next_cursor]. Keep the
        # cursor in the envelope while compacting only the document rows.
        if (
            len(data) == 2
            and isinstance(data[0], list)
            and (data[1] is None or isinstance(data[1], str))
        ):
            data = [[_timeline_summary(item) for item in data[0]], data[1]]
        else:
            data = [_timeline_summary(item) for item in data]
        result = DomainResult.success(
            data,
            receipt=result.receipt,
            idempotency_key=result.idempotency_key,
        )
    return print_result(result, as_json=parsed.json)


def _timeline_summary(item: Any) -> Any:
    """Return a compact identity/count view for one listed timeline."""
    if not isinstance(item, Mapping):
        return item

    summary: dict[str, Any] = {}
    # These are the stable identity/lifecycle fields emitted by the runtime.
    for key in (
        "timeline_id", "id", "slug", "name", "version", "config_version",
        "archived", "archived_at", "is_default", "default",
    ):
        if key in item:
            summary[key] = item[key]
    # Some runtime projections keep the display metadata nested.
    display = item.get("display")
    if "is_default" not in summary and isinstance(display, Mapping):
        if "is_default" in display:
            summary["is_default"] = display["is_default"]

    config = item.get("config")
    registry = item.get("registry")
    counts: dict[str, int] = {}
    if isinstance(config, Mapping):
        counts["config_keys"] = len(config)
        for key in ("clips", "tracks", "shots", "scenes"):
            value = config.get(key)
            if isinstance(value, (list, tuple, Mapping)):
                counts[key] = len(value)
    if isinstance(registry, Mapping):
        assets = registry.get("assets")
        if isinstance(assets, (list, tuple, Mapping)):
            counts["assets"] = len(assets)
    if counts:
        summary["counts"] = counts
    return summary


def _human_time(value: Any) -> str:
    """Keep authored times readable without rounding away exact boundaries."""
    if value is None:
        return "?"
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            value = Fraction(int(value[0]), int(value[1]))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if isinstance(value, Fraction):
        value = float(value)
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    text = str(value)
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _human_clip_interval(clip: Mapping[str, Any]) -> tuple[str, str]:
    timing = clip.get("timing_projection")
    mounted = timing.get("mounted") if isinstance(timing, Mapping) else None
    if isinstance(mounted, Mapping) and mounted.get("start") is not None and mounted.get("end") is not None:
        return _human_time(mounted["start"]), _human_time(mounted["end"])
    bounds = clip.get("time_bounds") if isinstance(clip.get("time_bounds"), Mapping) else {}
    start = clip.get("at", clip.get("start", clip.get("start_time", clip.get("time", bounds.get("timeline_start")))))
    end = clip.get("end", clip.get("end_time", bounds.get("timeline_end")))
    if end is None:
        duration = clip.get("duration", clip.get("hold", clip.get("duration_seconds")))
        try:
            if isinstance(start, (list, tuple)) and len(start) == 2:
                start_value = Fraction(int(start[0]), int(start[1]))
            else:
                start_value = Fraction(str(start))
            if isinstance(duration, (list, tuple)) and len(duration) == 2:
                duration_value = Fraction(int(duration[0]), int(duration[1]))
            else:
                duration_value = Fraction(str(duration))
            end = start_value + duration_value
        except (TypeError, ValueError):
            end = None
    return _human_time(start), _human_time(end)


def _fractional(value: Any) -> Fraction | None:
    try:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return Fraction(int(value[0]), int(value[1]))
        return Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _clip_bounds(clip: Mapping[str, Any]) -> tuple[Fraction | None, Fraction | None]:
    timing = clip.get("timing_projection")
    mounted = timing.get("mounted") if isinstance(timing, Mapping) else None
    if isinstance(mounted, Mapping) and mounted.get("start") is not None and mounted.get("end") is not None:
        return _fractional(mounted["start"]), _fractional(mounted["end"])
    bounds = clip.get("time_bounds") if isinstance(clip.get("time_bounds"), Mapping) else {}
    start = _fractional(clip.get("start", clip.get("at", bounds.get("timeline_start"))))
    end = _fractional(clip.get("end", bounds.get("timeline_end")))
    if end is None and start is not None:
        duration = _fractional(clip.get("duration", clip.get("hold")))
        if duration is not None:
            end = start + duration
    return start, end


def _human_clip_name(clip: Mapping[str, Any]) -> str:
    source = clip.get("source")
    candidates = [
        clip.get("media_name"), clip.get("asset_name"), clip.get("name"),
        clip.get("asset"), clip.get("asset_id"),
        source.get("name") if isinstance(source, Mapping) else None,
        source.get("key") if isinstance(source, Mapping) else source if isinstance(source, str) else None,
    ]
    return next((str(value) for value in candidates if value not in (None, "")), "(unnamed media)")


def _human_clip_controls(clip: Mapping[str, Any]) -> list[str]:
    controls: list[str] = []
    authored = clip.get("authored_fields") if isinstance(clip.get("authored_fields"), Mapping) else {}
    presentation = clip.get("presentation_fields") if isinstance(clip.get("presentation_fields"), Mapping) else {}
    opacity = clip.get("opacity", presentation.get("opacity", authored.get("opacity")))
    if isinstance(opacity, (int, float)) and opacity != 1 and opacity != 100:
        controls.append(f"opacity {opacity * 100:g}%" if 0 <= opacity <= 1 else f"opacity {opacity:g}%")
    speed = clip.get("speed", clip.get("playback_rate"))
    if isinstance(speed, (list, tuple)) and len(speed) == 2:
        try:
            speed = float(Fraction(int(speed[0]), int(speed[1])))
        except (TypeError, ValueError, ZeroDivisionError):
            speed = None
    if isinstance(speed, (int, float)) and speed != 1:
        controls.append(f"speed {speed:g}×")
    blend = clip.get("blend", clip.get("blend_mode"))
    if blend not in (None, "", "normal"):
        controls.append(f"blend {blend}")
    if clip.get("muted") is True or clip.get("mute") is True:
        controls.append("muted")
    gain = clip.get("gain", clip.get("volume"))
    if isinstance(gain, (int, float)) and gain != 1:
        controls.append(f"gain {gain:g}")
    if clip.get("timing_unknown") is True:
        controls.append("motion timing unknown")
    return controls


def _human_track_controls(track: Mapping[str, Any]) -> list[str]:
    controls: list[str] = []
    opacity = track.get("opacity")
    if isinstance(opacity, (int, float)) and opacity not in (1, 100):
        controls.append(f"opacity {opacity * 100:g}%" if 0 <= opacity <= 1 else f"opacity {opacity:g}%")
    blend = track.get("blend", track.get("blend_mode", track.get("blendMode")))
    if blend not in (None, "", "normal"):
        controls.append(f"blend {blend}")
    if track.get("muted") is True or track.get("mute") is True:
        controls.append("muted")
    volume = track.get("volume", track.get("gain"))
    if isinstance(volume, (int, float)) and volume != 1:
        controls.append(f"gain {volume:g}")
    return controls


def _human_is_audio_clip(clip: Mapping[str, Any]) -> bool:
    """Classify audio from any non-empty clip, media, or scoped-track kind."""
    track = clip.get("track")
    candidates = (
        clip.get("kind"), clip.get("media_type"), clip.get("clip_type"),
        track.get("kind") if isinstance(track, Mapping) else None,
        clip.get("track_kind"),
    )
    for value in candidates:
        if not isinstance(value, str) or not value.strip():
            continue
        kind = value.lower()
        if kind in {"audio", "sound", "music"} or kind.startswith("audio/"):
            return True
    return False


def _human_track_identity(clip: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return the exact scoped track key, with a stable unassigned fallback."""
    reference = clip.get("track_ref") if isinstance(clip.get("track_ref"), Mapping) else {}
    track = clip.get("track")
    track_id = reference.get("track_id") or clip.get("track_id")
    if not track_id and isinstance(track, Mapping):
        track_id = track.get("id")
    if not track_id and isinstance(track, str):
        track_id = track
    return (
        str(reference.get("scope") or ""),
        str(reference.get("scope_id") or ""),
        str(track_id or "unassigned"),
    )


def _human_track_heading(identity: tuple[str, str, str]) -> str:
    scope, scope_id, track_id = identity
    heading = f"Track {track_id}"
    if scope or scope_id:
        heading += f" ({scope or 'scope unknown'}/{scope_id or 'owner unknown'})"
    return heading


def _human_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _human_clip_detail_lines(
    clip: Mapping[str, Any], *, summary: Mapping[str, Any], scope: Mapping[str, Any]
) -> list[str]:
    """Show bounded authored values plus the exact saved target in detail mode."""
    lines: list[str] = []
    authored = clip.get("authored_fields") if isinstance(clip.get("authored_fields"), Mapping) else {}
    label = clip.get("label", authored.get("label"))
    labels = clip.get("labels", authored.get("labels"))
    if label not in (None, ""):
        lines.append(f"       authored label: {_human_json(label)}")
    if labels not in (None, "", [], {}):
        lines.append(f"       authored labels: {_human_json(labels)}")
    params = clip.get("parameters")
    if params is None:
        params = next((authored.get(key) for key in ("parameters", "params", "props") if key in authored), None)
    if params not in (None, {}, []):
        source = clip.get("parameters_source") or next((key for key in ("parameters", "params", "props") if key in authored), "parameters")
        lines.append(f"       {source}: {_human_json(params)}")
    details = {
        key: value for key, value in (
            ("element_ref", clip.get("element_ref", authored.get("element_ref", authored.get("elementRef")))),
            ("extensions", clip.get("extensions", authored.get("extensions"))),
            ("presentation", clip.get("presentation", authored.get("presentation"))),
            ("presentation_fields", clip.get("presentation_fields", authored.get("presentation_fields"))),
            ("authored_timing", clip.get("authored_timing", authored.get("authored_timing"))),
        ) if value not in (None, {}, [])
    }
    if details:
        lines.append("       authored values: " + _human_json(details))
    # Keep future or opaque authored keys visible without repeating the fields
    # already presented above. Runtime has already bounded this mapping.
    if authored:
        known = {"label", "labels", "parameters", "params", "props", "element_ref", "elementRef",
                 "extensions", "presentation", "presentation_fields", "authored_timing",
                 "id", "clip_id", "clipType", "clip_type", "type", "track", "at", "at_ms",
                 "duration", "duration_ms", "hold", "from", "from_ms", "to", "to_ms",
                 "speed", "asset", "asset_id", "assetId", "source_object_id", "media_id",
                 "object_id", "content_sha256", "content_digest", "digest"}
        remaining = {key: value for key, value in authored.items() if key not in known}
        if remaining:
            lines.append("       authored fields: " + _human_json(remaining))
    elif any(item.get("path") == "authored_fields" for item in clip.get("omitted_fields", []) if isinstance(item, Mapping)):
        lines.append("       authored values: omitted by Runtime's authored-field byte limit")

    exact = {
        key: clip.get(key) for key in (
            "media_name", "asset_id", "source_object_id", "content_digest", "media_type", "element_ref"
        ) if clip.get(key) not in (None, "")
    }
    exact.update({key: clip.get(key) for key in ("shot_id", "occurrence_id", "clip_id") if clip.get(key) not in (None, "")})
    exact["timeline_id"] = scope.get("timeline") or scope.get("timeline_id")
    exact["revision_id"] = summary.get("revision_id")
    exact["is_current_head"] = summary.get("is_current_head")
    exact = {key: value for key, value in exact.items() if value not in (None, "")}
    lines.append("       identity: " + _human_json(exact))
    return lines


def _human_omission_lines(
    omissions: object, *, summary: Mapping[str, Any], scope: Mapping[str, Any],
    occurrence: Mapping[str, Any] | None = None, clip: Mapping[str, Any] | None = None,
    track_ref: Mapping[str, Any] | None = None,
) -> list[str]:
    if not isinstance(omissions, list):
        return []
    lines: list[str] = []
    seen: set[str] = set()
    for omission in omissions:
        if not isinstance(omission, Mapping):
            continue
        identity = _human_json(dict(omission))
        if identity in seen:
            continue
        seen.add(identity)
        path = omission.get("path") or "authored value"
        reason = omission.get("reason") or "omitted"
        size = omission.get("byte_length")
        limit = omission.get("limit_bytes")
        size_text = f" ({size} B; limit {limit} B)" if size is not None and limit is not None else ""
        target = {
            "timeline_id": scope.get("timeline") or scope.get("timeline_id"),
            "revision_id": summary.get("revision_id"),
        }
        if occurrence:
            target.update({key: occurrence.get(key) for key in ("occurrence_id", "shot_id") if occurrence.get(key) is not None})
        if clip:
            target.update({key: clip.get(key) for key in ("occurrence_id", "shot_id", "clip_id") if clip.get(key) is not None})
        if track_ref:
            target["track_ref"] = dict(track_ref)
        elif clip and isinstance(clip.get("track_ref"), Mapping):
            target["track_ref"] = dict(clip["track_ref"])
        elif occurrence and isinstance(occurrence.get("track_ref"), Mapping):
            target["track_ref"] = dict(occurrence["track_ref"])
        target = {key: value for key, value in target.items() if value not in (None, "")}
        digest = omission.get("sha256")
        digest_text = f"; sha256 {digest}" if digest else ""
        lines.append(
            f"       omitted {path}: {reason}{size_text}{digest_text}; pinned target {_human_json(target)}; "
            "full omitted values are unavailable through a bounded retrieval route"
        )
    return lines


def _human_occurrence_lookup(data: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    native = data.get("native_inspection") if isinstance(data.get("native_inspection"), Mapping) else {}
    selected = native.get("selected") if isinstance(native.get("selected"), list) else []
    result: dict[str, Mapping[str, Any]] = {}
    for row in selected:
        if not isinstance(row, Mapping):
            continue
        occurrence = row.get("occurrence")
        if isinstance(occurrence, Mapping) and occurrence.get("occurrence_id") is not None:
            result[str(occurrence["occurrence_id"])] = occurrence
    return result


def _human_motion_lines(clip: Mapping[str, Any], *, detail: bool = False) -> list[str]:
    """Present only producer-supported intervals; custom clocks stay declarations."""
    from astrid.packs.rendering.executors.timeline_visualize.readable_timing import project_readable_timing
    if "timed_changes" not in clip:
        clip = project_readable_timing([clip])[0]
    grouped = {}
    for change in clip.get("timed_changes", []):
        if change.get("hold") and not detail:
            continue
        key = (tuple(change["start"]), tuple(change["end"]), change["kind"], change.get("hold", False))
        grouped.setdefault(key, []).append(change)
    changes = []
    for (start, end, kind, hold), rows in sorted(
        grouped.items(), key=lambda item: (Fraction(*item[0][0]), Fraction(*item[0][1]), item[0][2], item[0][3])
    ):
        values = []
        for row in rows:
            before, after = row["before"], row["after"]
            if row["property"].endswith("multiplier"):
                before, after = f"{before*100:g}%", f"{after*100:g}%"
            else:
                before, after = f"{before:g}", f"{after:g}"
            values.append(f"{row['property']} holds {before}" if hold else f"{row['property']} {before} → {after}")
        label = "transform " if kind == "transform" else ""
        suffix = f" ({kind.replace('_', ' ')})" if kind != "transform" else ""
        if any(row.get("overlapping_fades") for row in rows):
            suffix += " [fade multipliers overlap]"
        changes.append(f"{_human_time(start)}–{_human_time(end)} {label}{'; '.join(values)}{suffix}")
    lines = changes if detail else changes[:3]
    if len(changes) > len(lines):
        lines.append(f"[{len(changes)-len(lines)} more change intervals; expand with --detail]")
    lines.extend(f"[motion timing unknown: {reason}]" for reason in clip.get("timing_unknowns", []))
    transition = clip.get("transition_projection")
    if isinstance(transition, Mapping):
        declaration = transition.get("transition")
        identifier = declaration.get("id", declaration.get("type", "transition")) if isinstance(declaration, Mapping) else declaration
        if transition.get("status") == "resolved":
            source = transition.get("from_target", {})
            destination = transition.get("to_target", {})
            target = lambda value: "/".join(str(value[key]) for key in ("occurrence_id", "clip_id") if value.get(key)) or str(value.get("render_clip_id"))
            lines.append(f"{_human_time(transition['start'])}–{_human_time(transition['end'])} transition {identifier}: {target(source)} → {target(destination)}")
        else:
            lines.append(f"[transition {identifier}; timing unresolved: {transition.get('reason', 'placement unavailable')}]")
    elif clip.get("transition"):
        declaration = clip["transition"]
        identifier = declaration.get("id", declaration.get("type", "transition")) if isinstance(declaration, Mapping) else declaration
        lines.append(f"[transition {identifier}; timing unresolved: producer unavailable]")
    if clip.get("source_clock"):
        lines.append(f"[{clip['source_clock']}]")
    return lines


def _render_timeline_human(result: object) -> str:
    """Render a compact, addressable authored timeline for human inspection."""
    envelope = envelope_dict(result)
    if not envelope["ok"]:
        return render_human(result)
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        return render_human(result)
    summary = data.get("summary") if isinstance(data.get("summary"), Mapping) else {}
    query = data.get("query") if isinstance(data.get("query"), Mapping) else {}
    scope = data.get("scope") if isinstance(data.get("scope"), Mapping) else {}
    currentness = (
        "current head" if summary.get("is_current_head") is True
        else "saved revision (not current head)" if summary.get("is_current_head") is False
        else "currentness unknown"
    )
    lines = [
        f"Timeline {scope.get('timeline') or scope.get('timeline_id') or 'current'}",
        f"  revision: {summary.get('revision_id') or 'unknown'} · {currentness} · Authored timeline",
    ]
    filters = [f"{key}={query[key]}" for key in ("range", "occurrence", "shot", "clip", "track", "asset") if query.get(key) not in (None, "", [])]
    if filters:
        lines.append("  scope: " + ", ".join(filters))
    lines.append("Visual layers (order unavailable; grouped by exact scoped track)")
    clips = [item for item in data.get("clips", []) if isinstance(item, Mapping)]
    audio = [clip for clip in clips if _human_is_audio_clip(clip)]
    visual_clips = [clip for clip in clips if not _human_is_audio_clip(clip)]
    occurrence_lookup = _human_occurrence_lookup(data)
    detail = query.get("detail") is True

    def render_rows(rows: list[Mapping[str, Any]], *, section: str) -> None:
        groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
        tracks: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        for clip in rows:
            identity = _human_track_identity(clip)
            groups.setdefault(identity, []).append(clip)
            track = clip.get("track")
            if isinstance(track, Mapping):
                tracks.setdefault(identity, track)
        if not rows:
            lines.append(f"  (no {section} clip occurrences in this page)")
            return
        display_index = 0
        for identity, group in groups.items():
            track = tracks.get(identity, {})
            heading = _human_track_heading(identity)
            track_label = track.get("label") if isinstance(track, Mapping) else None
            if isinstance(track_label, str) and track_label.strip():
                heading += f" · {track_label}"
            controls = _human_track_controls(track) if isinstance(track, Mapping) else []
            suffix = f" [{', '.join(controls)}]" if controls else ""
            lines.append(f"  {heading}{suffix}")
            if isinstance(track, Mapping):
                lines.extend(_human_omission_lines(
                    track.get("omitted_fields"), summary=summary, scope=scope,
                ))
            indexed = list(enumerate(group))
            indexed.sort(key=lambda pair: (
                _clip_bounds(pair[1])[0] is None,
                _clip_bounds(pair[1])[0] or Fraction(0),
                pair[0],
            ))
            for _source_index, clip in indexed:
                display_index += 1
                start, end = _human_clip_interval(clip)
                identity_parts = [clip.get("occurrence_id"), clip.get("clip_id")]
                row_identity = "/".join(str(value) for value in identity_parts if value not in (None, ""))
                label = f" · {row_identity}" if row_identity else ""
                controls = _human_clip_controls(clip)
                suffix = f" [{', '.join(controls)}]" if controls else ""
                lines.append(f"    {display_index}. {start}–{end}  {_human_clip_name(clip)}{label}{suffix}")
                for change in _human_motion_lines(clip, detail=query.get("detail") is True):
                    lines.append(f"         {change}")
                occurrence = occurrence_lookup.get(str(clip.get("occurrence_id"))) if clip.get("occurrence_id") is not None else None
                if detail:
                    lines.extend(_human_clip_detail_lines(clip, summary=summary, scope=scope))
                # Authored value omissions remain visible in compact mode too.
                lines.extend(_human_omission_lines(
                    [
                        *(clip.get("omitted_fields", []) if isinstance(clip.get("omitted_fields"), list) else []),
                        *(clip.get("authored_omitted_fields", []) if isinstance(clip.get("authored_omitted_fields"), list) else []),
                        *(clip.get("asset_omitted_fields", []) if isinstance(clip.get("asset_omitted_fields"), list) else []),
                        *(clip.get("track_omitted_fields", []) if isinstance(clip.get("track_omitted_fields"), list) else []),
                    ], summary=summary, scope=scope,
                    occurrence=occurrence, clip=clip,
                ))

    # Parent-level occurrence tracks place child timelines on a layer. Keep
    # that owner separate from each child's internal track and associate it
    # with the exact occurrence IDs that appear in the clip rows below.
    placements: dict[tuple[str, str, str, tuple[str, ...]], list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
    for occurrence_id, occurrence in occurrence_lookup.items():
        reference = occurrence.get("track_ref") if isinstance(occurrence.get("track_ref"), Mapping) else {}
        track = occurrence.get("track") if isinstance(occurrence.get("track"), Mapping) else {}
        track_id = reference.get("track_id") or (track.get("id") if isinstance(track, Mapping) else None) or "unassigned"
        occurrence_controls: list[str] = _human_track_controls(track)
        if occurrence.get("mute") is True:
            occurrence_controls.append("muted")
        gain = occurrence.get("gain", occurrence.get("volume"))
        if isinstance(gain, (int, float)) and gain != 1:
            occurrence_controls.append(f"gain {gain:g}")
        key = (
            str(reference.get("scope") or ""), str(reference.get("scope_id") or ""),
            str(track_id), tuple(dict.fromkeys(occurrence_controls)),
        )
        placements.setdefault(key, []).append((occurrence, track))
    if placements:
        lines.append("  Occurrence placement tracks")
        for identity_with_controls, rows in placements.items():
            identity = identity_with_controls[:3]
            controls = list(identity_with_controls[3])
            track = rows[0][1]
            heading = _human_track_heading(identity)
            occurrence_ids = ", ".join(str(row[0].get("occurrence_id")) for row in rows)
            suffix = f" [{', '.join(controls)}]" if controls else ""
            lines.append(f"    {heading} · occurrences {occurrence_ids}{suffix}")
            for occurrence, _track in rows:
                lines.extend(_human_omission_lines(
                    [
                        *(occurrence.get("omitted_fields", []) if isinstance(occurrence.get("omitted_fields"), list) else []),
                        *(occurrence.get("track_omitted_fields", []) if isinstance(occurrence.get("track_omitted_fields"), list) else []),
                    ], summary=summary, scope=scope,
                    occurrence=occurrence,
                ))

    render_rows(visual_clips, section="visual")
    if audio:
        lines.append("Audio (separate tracks; order unavailable unless projected)")
        render_rows(audio, section="audio")
    native = data.get("native_inspection") if isinstance(data.get("native_inspection"), Mapping) else {}
    pagination = data.get("pagination") if isinstance(data.get("pagination"), Mapping) else {}
    if not pagination and isinstance(native.get("next_cursor"), str):
        pagination = {"next_cursor": native.get("next_cursor")}
    page = data.get("page") if isinstance(data.get("page"), Mapping) else native.get("page") if isinstance(native.get("page"), Mapping) else {}
    if page.get("returned_clips") is not None:
        page_line = f"page: returned {page['returned_clips']} of {page.get('total_selected_clips', page['returned_clips'])} selected clips"
        if page.get("remaining_clips") not in (None, 0):
            page_line += f"; {page['remaining_clips']} remain after this page"
        lines.append(page_line)
    if pagination.get("next_cursor"):
        lines.append(f"  page: more results available (cursor {pagination['next_cursor']})")
    omissions = data.get("omission_metadata") if isinstance(data.get("omission_metadata"), Mapping) else native.get("omission_metadata") if isinstance(native.get("omission_metadata"), Mapping) else {}
    omitted_count = omissions.get("authored_values_omitted")
    if isinstance(omitted_count, int) and omitted_count > 0:
        lines.append(f"page: {omitted_count} bounded values omitted; full values are unavailable through a bounded retrieval route")
    navigation = data.get("navigation") if isinstance(data.get("navigation"), Mapping) else {}
    commands = navigation.get("commands") if isinstance(navigation.get("commands"), Mapping) else {}
    if commands.get("visualize"):
        lines.append(f"visualize: {commands['visualize']}")
    return "\n".join(lines)


def _cmd_show(parsed: argparse.Namespace) -> int:
    """Print the Runtime-owned canonical current-head inspection.

    ``show`` deliberately has no whole-document fallback.  A legacy timeline
    document is a mutable storage projection and cannot be presented as the
    current editor state when the native Runtime inspection route is absent.
    """
    from astrid.packs.rendering.executors.timeline_visualize.inspection_contract import inspection_options
    values = {
        name: getattr(parsed, name, None)
        for name in ("clip", "occurrence", "shot", "track", "asset", "range", "detail", "limit", "cursor", "revision_id")
    }
    normalized = inspection_options(values)
    opener = getattr(parsed.client.timelines, "open_composition", None)
    if not callable(opener):
        from astrid.sdk.contracts import ErrorObject

        result = DomainResult.failure(
            ErrorObject(
                "unavailable",
                "canonical timeline inspection is unavailable",
                {"project": str(parsed.project), "timeline": str(parsed.ref)},
            )
        )
    else:
        # ``summary`` remains accepted as a presentation flag for CLI
        # compatibility, but both forms read the same bounded canonical
        # projection and never expose the legacy document.
        result = opener(
            parsed.project,
            parsed.ref,
            limit=values.get("limit") or 50,
            cursor=values.get("cursor"),
            clip=normalized["clip"],
            occurrence=normalized["occurrence"],
            shot=normalized["shot"],
            track=normalized["tracks"],
            asset=normalized["asset"],
            range_value=values.get("range"),
            detail=normalized["detail"],
            revision_id=values.get("revision_id"),
        )
    if result.ok and isinstance(result.data, Mapping):
        data = dict(result.data)
        data["navigation"] = _show_navigation_help(
            project=parsed.project,
            ref=parsed.ref,
            parsed=parsed,
            outputs=data,
        )
        result = DomainResult.success(
            data,
            receipt=result.receipt,
            idempotency_key=result.idempotency_key,
        )
    return print_result(result, as_json=parsed.json, human_renderer=_render_timeline_human)


def _revision_from_outputs(outputs: Mapping[str, Any]) -> str | None:
    """Read an already-admitted revision pin from bounded result metadata."""
    for container in (outputs, outputs.get("summary"), outputs.get("inspection"), outputs.get("provenance")):
        if isinstance(container, Mapping):
            for key in ("revision_id", "head_revision_id", "parent_revision_id"):
                value = container.get(key)
                if value not in (None, ""):
                    return str(value)
    return None


def _show_navigation_help(
    *, project: str | None, ref: str | None, parsed: argparse.Namespace, outputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Offer a revision-pinned visual continuation from structural show output."""
    argv = ["python3", "-m", "astrid", "timelines", "visualize", "--project", str(project or "<project>")]
    if ref not in (None, ""):
        argv += ["--timeline-slug", str(ref)]
    revision = getattr(parsed, "revision_id", None) or _revision_from_outputs(outputs)
    if revision:
        argv += ["--revision-id", str(revision)]
    for name, flag in (("occurrence", "--occurrence"), ("shot", "--shot"), ("clip", "--clip"), ("asset", "--asset"), ("range", "--range")):
        value = getattr(parsed, name, None)
        if value not in (None, ""):
            argv += [flag, str(value)]
    for track in getattr(parsed, "track", None) or []:
        argv += ["--track", str(track)]
    return {
        "commands": {"visualize": shlex.join(argv)},
        "authority": revision or "resolved by Runtime current head",
        "note": "The visual continuation preserves the saved revision and structural filters; it does not follow a newer head implicitly.",
    }


def _cmd_replace_parent_media(parsed: argparse.Namespace) -> int:
    result = parsed.client.timelines.replace_parent_media(
        parsed.project,
        parsed.ref,
        occurrence_id=parsed.occurrence_id,
        clip_id=parsed.clip_id,
        source_object_id=parsed.source_object_id,
        expected_head=parsed.expected_head,
        idempotency_key=parsed.idempotency_key,
    )
    # The authoring route necessarily compiles the complete immutable
    # parent/shot/internal-timeline closure, but that closure is an internal
    # publication payload.  Never put it on the public CLI wire: a single
    # replacement can otherwise exceed the 4 MiB JSON pipe limit even though
    # the CAS publication succeeded.  Keep the SDK result lossless for
    # programmatic callers and project only a bounded public receipt here.
    if isinstance(result, DomainResult) and result.ok and isinstance(result.data, Mapping):
        source = result.data
        compact: dict[str, Any] = {}
        for key in (
            "representation",
            "project_id",
            "timeline_id",
            "occurrence_id",
            "clip_id",
            "expected_head",
            "candidate_digest",
        ):
            if key in source:
                compact[key] = source[key]
        validation = source.get("validation")
        if isinstance(validation, Mapping):
            compact["validation"] = {
                key: validation[key]
                for key in (
                    "valid",
                    "candidate_digest",
                    "publication_digest",
                    "changed_identities",
                    "reused_identities",
                )
                if key in validation
            }
        diff = source.get("diff")
        if isinstance(diff, Mapping):
            compact["diff"] = {
                key: diff[key]
                for key in ("changed", "changed_count", "summary")
                if key in diff and not isinstance(diff[key], (list, dict))
            }
            if not compact["diff"]:
                compact.pop("diff")
        publication = source.get("publication")
        if isinstance(publication, Mapping):
            published_data = publication.get("data")
            if not isinstance(published_data, Mapping):
                published_data = publication
            publication_summary = {
                key: published_data[key]
                for key in (
                    "new_head",
                    "old_head",
                    "revision_id",
                    "parent_revision_id",
                    "replayed",
                    "status",
                )
                if key in published_data and not isinstance(published_data[key], (dict, list))
            }
            dependency_manifest = published_data.get("dependency_manifest")
            if isinstance(dependency_manifest, Mapping):
                publication_summary["dependency_counts"] = {
                    key: len(value)
                    for key, value in dependency_manifest.items()
                    if key in {"shots", "internal_timelines", "media"}
                    and isinstance(value, list)
                }
            compact["publication"] = publication_summary
        result = DomainResult.success(
            compact,
            receipt=result.receipt,
            idempotency_key=result.idempotency_key,
        )
    return print_result(result, as_json=parsed.json)


def _cmd_archive(parsed: argparse.Namespace) -> int:
    result = parsed.client.timelines.archive(
        parsed.project,
        parsed.ref,
        idempotency_key=parsed.idempotency_key,
    )
    return print_result(result, as_json=parsed.json)


def _cmd_recover(parsed: argparse.Namespace) -> int:
    result = parsed.client.timelines.recover(
        parsed.project,
        parsed.ref,
        idempotency_key=parsed.idempotency_key,
    )
    return print_result(result, as_json=parsed.json)


def _cmd_history(parsed: argparse.Namespace) -> int:
    result = parsed.client.timelines.history(parsed.project, parsed.ref)
    return print_result(result, as_json=parsed.json)


def _cmd_diff(parsed: argparse.Namespace) -> int:
    result = parsed.client.timelines.diff(parsed.project, parsed.ref)
    return print_result(result, as_json=parsed.json)


def _visualize_format_argument(value: str) -> str:
    """Validate one bounded visualization format argument."""
    values = [part.strip().lower() for part in value.split(",") if part.strip()]
    if not values:
        raise argparse.ArgumentTypeError("format must name png or md")
    invalid = sorted(set(values) - {"png", "md"})
    if invalid:
        raise argparse.ArgumentTypeError(
            f"invalid visualization format(s): {', '.join(invalid)}; choose png or md"
        )
    return ",".join(values)


def _visualization_artifact_summary(outputs: Mapping[str, Any]) -> dict[str, Any] | None:
    """Summarize repeated visualization artifacts without dropping evidence."""
    raw_artifacts = outputs.get("artifacts")
    if not isinstance(raw_artifacts, list):
        return None

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    media_ids: set[str] = set()
    hashes: set[str] = set()
    for artifact in raw_artifacts:
        if not isinstance(artifact, Mapping):
            continue
        media_id = artifact.get("media_id")
        content_hash = artifact.get("content_hash")
        if isinstance(media_id, str) and media_id:
            media_ids.add(media_id)
        if isinstance(content_hash, str) and content_hash:
            hashes.add(content_hash)
        if not (
            isinstance(media_id, str)
            and media_id
            and isinstance(content_hash, str)
            and content_hash
        ):
            continue
        key = (media_id, content_hash)
        group = groups.setdefault(
            key,
            {"media_id": media_id, "content_hash": content_hash, "count": 0, "labels": []},
        )
        group["count"] += 1
        label = artifact.get("label")
        if isinstance(label, str) and label:
            group["labels"].append(label)

    duplicate_groups = [group for group in groups.values() if group["count"] > 1]
    duplicate_groups.sort(key=lambda group: (-group["count"], group["media_id"]))
    return {
        "artifact_count": len(raw_artifacts),
        "unique_media_count": len(media_ids),
        "unique_content_hash_count": len(hashes),
        "duplicate_reference_count": sum(group["count"] - 1 for group in duplicate_groups),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_groups": duplicate_groups,
    }


def _cmd_visualize(parsed: argparse.Namespace) -> int:
    """Run visualization through the public SDK and product output layer."""
    from astrid.sdk.contracts import DomainResult, ErrorObject
    from astrid.packs.rendering.executors.timeline_visualize.inspection_contract import inspection_options
    human_outputs: Mapping[str, Any] | None = None

    # Normalize repeatable and comma-separated spellings before the one
    # canonical SDK call. Inputs remain render-free; auto resolves to one
    # exact current composed output or falls back to declared inputs.
    formats = [
        item.strip().lower()
        for value in (parsed.formats or ["png", "md"])
        for item in str(value).split(",")
        if item.strip()
    ]
    inputs: dict[str, Any] = {"formats": formats}
    timeline_slug = parsed.timeline_slug or parsed.timeline_ref
    for name in (
        "shot", "view", "sample", "every", "every_frames", "include_cuts",
        "render_run", "columns", "page_size", "resolution", "include_media",
        "range", "at", "frame", "clip", "asset", "context", "neighbors", "show", "hide",
        "track", "detail", "occurrence", "revision_id",
    ):
        value = getattr(parsed, name, None)
        if value not in (None, "", []):
            inputs[name] = value
    if timeline_slug not in (None, ""):
        inputs["timeline_slug"] = timeline_slug
    # The public text and visual routes use the same bounded selector
    # normalizer. Preserve all existing input spellings, but canonicalize the
    # occurrence identity before crossing the SDK boundary.
    inputs["occurrence"] = inspection_options({"occurrence": getattr(parsed, "occurrence", None)})["occurrence"]
    if inputs["occurrence"] is None:
        inputs.pop("occurrence")
    shown_components = inputs.get("show") or []
    hidden_components = inputs.get("hide") or []
    if "output" in hidden_components or (shown_components and "output" not in shown_components):
        mode = "inputs"
    else:
        mode = getattr(parsed, "mode", "auto")
    result = parsed.client.timelines.visualize(
        parsed.project,
        timeline_slug,
        mode=mode,
        options=inputs,
        revision_id=getattr(parsed, "revision_id", None),
        out=getattr(parsed, "out", None),
    )
    if isinstance(result, DomainResult):
        # Native Runtime views are already typed DomainResults and deliberately
        # have no task/run identity. Keep the CLI envelope compatible while
        # preserving Runtime-owned artifact/inspection data verbatim.
        if result.ok:
            outputs = dict(result.data) if isinstance(result.data, Mapping) else {"data": result.data}
            summary = _visualization_artifact_summary(outputs)
            if summary is not None:
                outputs["artifact_summary"] = summary
            outputs["navigation"] = _visualization_navigation_help(
                project=parsed.project,
                inputs=inputs,
                outputs=outputs,
            )
            human_outputs = outputs
            envelope = DomainResult.success(
                {
                    "capability_id": "timelines.visualize",
                    "run_id": None,
                    "kernel_run_id": None,
                    "kernel_task_id": None,
                    "kernel_attempt_id": None,
                    "manifest_path": None,
                    "outputs": outputs,
                },
                receipt=result.receipt,
                idempotency_key=result.idempotency_key,
            )
        else:
            envelope = result
    elif result.ok:
        outputs = result.outputs
        if isinstance(outputs, Mapping):
            outputs = dict(outputs)
            summary = _visualization_artifact_summary(outputs)
            if summary is not None:
                outputs["artifact_summary"] = summary
            outputs["navigation"] = _visualization_navigation_help(
                project=parsed.project,
                inputs=inputs,
                outputs=outputs,
            )
            human_outputs = outputs
        envelope = DomainResult.success(
            {
                "capability_id": result.capability_id,
                "run_id": result.run_id,
                "kernel_run_id": result.kernel_run_id,
                "kernel_task_id": result.kernel_task_id,
                "kernel_attempt_id": result.kernel_attempt_id,
                "manifest_path": result.manifest_path,
                "outputs": outputs,
            }
        )
    else:
        detail = dict(result.error or {})
        category = str(detail.get("sdk_category") or "invocation")
        envelope = DomainResult.failure(
            ErrorObject(
                code="validation_error" if category == "validation" else "invocation_error",
                message=str(detail.get("message") or "timeline visualization failed"),
                details={
                    "sdk_error": detail.get("sdk_error"),
                    "sdk_category": category,
                    "validation": detail.get("validation"),
                    "run_id": result.run_id,
                    "kernel_run_id": result.kernel_run_id,
                    "kernel_task_id": result.kernel_task_id,
                    "kernel_attempt_id": result.kernel_attempt_id,
                },
            )
        )
    exit_status = print_result(envelope, as_json=parsed.json)
    if result.ok and not parsed.json:
        if human_outputs is not None:
            _print_visualization_navigation(human_outputs)
    return exit_status


def _visualization_navigation_help(
    *, project: str | None, inputs: Mapping[str, Any], outputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose copyable zoom/sampling/navigation guidance in CLI results."""
    identity = [
        "python3", "-m", "astrid", "timelines", "visualize",
        "--project", str(project or "<project>"),
    ]
    timeline = inputs.get("timeline_slug")
    if timeline not in (None, ""):
        identity += ["--timeline-slug", str(timeline)]
    render_run = inputs.get("render_run")
    if render_run not in (None, ""):
        identity += ["--render-run", str(render_run)]
    revision_id = inputs.get("revision_id") or _revision_from_outputs(outputs)
    if revision_id not in (None, ""):
        identity += ["--revision-id", str(revision_id)]

    def tokens(value: Any) -> list[str]:
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if str(item)]
        return [str(value)] if value not in (None, "") else []

    def base(*, components: bool = True, sampling: bool = True, resolution: bool = True) -> list[str]:
        argv = identity + ["--view", str(inputs.get("view") or "filmstrip")]
        if components:
            shown = tokens(inputs.get("show"))
            hidden = tokens(inputs.get("hide"))
            if shown:
                argv += ["--show", ",".join(shown)]
            if hidden:
                argv += ["--hide", ",".join(hidden)]
            for track in tokens(inputs.get("track")):
                argv += ["--track", track]
            for key, flag in (("shot", "--shot"), ("clip", "--clip"), ("occurrence", "--occurrence"), ("asset", "--asset")):
                value = inputs.get(key)
                if value not in (None, ""):
                    argv += [flag, str(value)]
        if inputs.get("detail"):
            argv += ["--detail"]
        if inputs.get("sample") not in (None, "", "interval"):
            argv += ["--sample", str(inputs["sample"])]
        if inputs.get("include_cuts"):
            argv += ["--include-cuts"]
        if inputs.get("columns") not in (None, ""):
            argv += ["--columns", str(inputs["columns"])]
        if inputs.get("page_size") not in (None, ""):
            argv += ["--page-size", str(inputs["page_size"])]
        if sampling:
            range_value = inputs.get("range")
            if isinstance(range_value, (list, tuple)) and len(range_value) == 2:
                range_value = f"{range_value[0]}..{range_value[1]}"
            if range_value not in (None, ""):
                argv += ["--range", str(range_value)]
            if inputs.get("at") not in (None, ""):
                argv += ["--at", str(inputs["at"])]
            if inputs.get("frame") not in (None, ""):
                argv += ["--frame", str(inputs["frame"])]
            if inputs.get("every") not in (None, ""):
                argv += ["--every", str(inputs["every"])]
            if inputs.get("every_frames") not in (None, ""):
                argv += ["--every-frames", str(inputs["every_frames"])]
        if resolution and inputs.get("resolution") not in (None, ""):
            value = inputs["resolution"]
            if isinstance(value, (list, tuple)) and len(value) == 2:
                value = f"{value[0]}x{value[1]}"
            argv += ["--resolution", str(value)]
        return argv

    # Build replacement commands from a clean base so mutually-exclusive
    # selectors (show/hide, every/every-frames, range/at) never duplicate.
    input_only = base(components=False, sampling=True, resolution=False) + [
        "--show", "inputs", "--hide", "output"
    ]
    clean = base(sampling=False, resolution=False)
    zoom_detail = [] if inputs.get("detail") else ["--detail"]
    pages = outputs.get("pages")
    primary_page = pages[0] if isinstance(pages, list) and pages else outputs.get("png")
    manifest_ref = outputs.get("manifest_path") or "MANIFEST"
    inspect_base = ["python3", "-m", "astrid", "timelines", "inspect", "--manifest", str(manifest_ref)]
    paired = "output" in tokens(inputs.get("show")) and "inputs" in tokens(inputs.get("show"))
    page_status = None
    if paired and isinstance(pages, list) and len(pages) > 1:
        page_status = f"Paired view generated {len(pages)} bite-sized pages; open them in numbered order."
    show_range = _visualization_show_range(inputs=inputs, outputs=outputs)
    show_selectors = [
        [flag, str(inputs[name])]
        for name, flag in (
            ("occurrence", "--occurrence"),
            ("shot", "--shot"),
            ("clip", "--clip"),
            ("asset", "--asset"),
        )
        if inputs.get(name) not in (None, "")
    ]
    if show_range is not None:
        show_selectors.append(["--range", show_range])
    return {
        "primary_page": primary_page,
        "pages": pages,
        "markdown": outputs.get("markdown"),
        "inspection": shlex.join(inspect_base + ["--section", "summary"]),
        "status": page_status,
        "keyboard": [
            "Open the primary PNG page for visual inspection; use the bounded inspect command for exact card, placement, lane, or timing lookup.",
            "Use the rerun commands below to zoom, change sampling intervals, or narrow to input lanes.",
        ],
        "filters": [
            "Use Shot, Track, From/To, Samples, and Density by rerunning with the matching flags.",
            "Density only reduces captured frames; rerun for finer samples.",
        ],
        "commands": {
            "rerun": shlex.join(base()),
            "zoom": shlex.join(clean + ["--range", "START..END", "--every", "0.25", *zoom_detail]),
            "interval_seconds": shlex.join(clean + ["--every", "1"]),
            "interval_frames": shlex.join(clean + ["--every-frames", "12"]),
            "exact_frame": shlex.join(clean + ["--frame", "FRAME"]),
            "resolution": shlex.join(base(resolution=False) + ["--resolution", "960x540"]),
            "inputs_only": shlex.join(input_only),
            "show": shlex.join(
                [
                    "python3", "-m", "astrid", "timelines", "show",
                    "--project", str(project or "<project>"),
                    *([str(timeline)] if timeline not in (None, "") else []),
                    *( ["--revision-id", str(revision_id)] if revision_id not in (None, "") else []),
                    *sum(show_selectors, []),
                    *sum((["--track", track] for track in tokens(inputs.get("track"))), []),
                ]
            ),
            "pages": shlex.join(clean + ["--columns", "5", "--page-size", "10"]),
            "inspect_summary": shlex.join(inspect_base + ["--section", "summary"]),
            "inspect_cards": shlex.join(inspect_base + ["--section", "cards"]),
            "inspect_placements": shlex.join(inspect_base + ["--section", "placements"]),
            "inspect_audio": shlex.join(inspect_base + ["--section", "audio"]),
            "inspect_boundaries": shlex.join(inspect_base + ["--section", "boundaries"]),
        },
        "notes": [
            "--range uses a half-open START..END seconds window.",
            "--every and --every-frames are mutually exclusive.",
            "--columns/--page-size change static layout; --track narrows input lanes.",
            "paired output+inputs pages show one row by default (five cards across); use --columns 6 for six across, or pass --page-size N explicitly for a denser two-row page.",
        ],
    }


def _fractional_seconds(value: Any) -> Fraction:
    """Parse the public seconds spelling without introducing float drift."""
    parts = str(value).split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError("invalid time")
    result = Fraction(0)
    for part in parts:
        result = result * 60 + Fraction(str(part))
    if result < 0:
        raise ValueError("invalid time")
    return result


def _visualization_fps(outputs: Mapping[str, Any]) -> Fraction:
    """Find the admitted frame clock, falling back to the public default."""
    containers: list[Mapping[str, Any]] = [outputs]
    for key in ("provenance", "inspection", "frame_index"):
        value = outputs.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
            nested = value.get("provenance")
            if isinstance(nested, Mapping):
                containers.append(nested)
    for container in containers:
        raw = container.get("fps_rational")
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            try:
                fps = Fraction(int(raw[0]), int(raw[1]))
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            if fps > 0:
                return fps
    frame_index = outputs.get("frame_index")
    frame_index_paths: list[Path] = []
    if isinstance(frame_index, str) and frame_index:
        frame_index_paths.append(Path(frame_index).expanduser())
        pack_root = outputs.get("pack_root")
        if isinstance(pack_root, str) and pack_root:
            candidate = Path(pack_root).expanduser() / frame_index
            if candidate not in frame_index_paths:
                frame_index_paths.append(candidate)
        manifest_path = outputs.get("manifest_path")
        if isinstance(manifest_path, str) and manifest_path:
            candidate = Path(manifest_path).expanduser().parent / frame_index
            if candidate not in frame_index_paths:
                frame_index_paths.append(candidate)
    for path in frame_index_paths:
        try:
            frame_index_data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if isinstance(frame_index_data, Mapping):
            provenance = frame_index_data.get("provenance")
            if isinstance(provenance, Mapping):
                raw = provenance.get("fps_rational")
                if isinstance(raw, (list, tuple)) and len(raw) == 2:
                    try:
                        fps = Fraction(int(raw[0]), int(raw[1]))
                    except (TypeError, ValueError, ZeroDivisionError):
                        continue
                    if fps > 0:
                        return fps
    return Fraction(30, 1)


def _fraction_text(value: Fraction) -> str:
    """Serialize a time rational without moving a half-open boundary."""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _visualization_show_range(
    inputs: Mapping[str, Any], *, outputs: Mapping[str, Any],
) -> str | None:
    """Project visualize's time selectors onto show's half-open range grammar."""
    range_value = inputs.get("range")
    if isinstance(range_value, (list, tuple)) and len(range_value) == 2:
        return f"{range_value[0]}..{range_value[1]}"
    if range_value not in (None, ""):
        return str(range_value)

    fps = _visualization_fps(outputs)
    frame = inputs.get("frame")
    if frame not in (None, ""):
        try:
            start = Fraction(int(frame), 1) / fps
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        end = Fraction(int(frame) + 1, 1) / fps
    elif inputs.get("at") not in (None, ""):
        try:
            center = _fractional_seconds(inputs["at"])
            # A timestamp without explicit context is still an exact frame
            # request for visualization; an explicit context is preserved as
            # the broader structural show window.
            radius = (
                _fractional_seconds(inputs["context"])
                if inputs.get("context") not in (None, "")
                else Fraction(1, 1) / fps
            )
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        start, end = max(Fraction(0), center - radius), center + radius
    else:
        return None

    return f"{_fraction_text(start)}..{_fraction_text(end)}"


def _print_visualization_navigation(outputs: Mapping[str, Any]) -> None:
    """Print short, copyable navigation hints in human CLI mode.

    ``--json`` already carries the structured ``outputs.navigation`` object;
    human mode should still be actionable without requiring the operator to
    rerun the command with another flag.
    """
    navigation = outputs.get("navigation")
    if not isinstance(navigation, Mapping):
        return
    print("navigation:")
    primary_page = navigation.get("primary_page")
    if primary_page:
        print(f"  open PNG: {primary_page}")
    inspection = navigation.get("inspection")
    if inspection:
        print(f"  inspect (bounded): {inspection}")
    status = navigation.get("status")
    if status:
        print(f"  status: {status}")
    commands = navigation.get("commands")
    if not isinstance(commands, Mapping):
        return
    for label, key in (
        ("zoom", "zoom"),
        ("interval (seconds)", "interval_seconds"),
        ("interval (frames)", "interval_frames"),
        ("resolution", "resolution"),
        ("inputs only", "inputs_only"),
        ("pages", "pages"),
    ):
        command = commands.get(key)
        if command:
            print(f"  {label}: {command}")


def _cmd_inspect(parsed: argparse.Namespace) -> int:
    from astrid.packs.rendering.executors.timeline_visualize.inspection_contract import inspect_filmstrip

    result = inspect_filmstrip(parsed.manifest, section=parsed.section, limit=parsed.limit, cursor=parsed.cursor,
                              frame=parsed.frame, card=parsed.card, shot=parsed.shot, occurrence=parsed.occurrence,
                              clip=parsed.clip, track=parsed.track, asset=parsed.asset, range_value=parsed.range_value)
    # This exact compact serialization is included in the inspector's byte cap.
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if result["ok"] else 1


def _configure_inspect(subparser: argparse.ArgumentParser) -> None:
    from astrid.packs.rendering.executors.timeline_visualize.inspection_contract import INSPECTION_SECTIONS

    subparser.description = "Inspect a verified materialized filmstrip offline; no runtime or render admission. Responses are capped at 8 KiB."
    subparser.add_argument("--manifest", required=True, help="Materialized filmstrip manifest.json; bundle members are verified before reading.")
    subparser.add_argument("--section", default="summary", help="Named section: " + ", ".join(INSPECTION_SECTIONS))
    subparser.add_argument("--limit", type=int, default=10, help="Records per response: 1–50, default 10; byte cap may reduce the page.")
    subparser.add_argument("--cursor", default=None, help="Opaque cursor from this bundle and identical query.")
    subparser.add_argument("--frame", type=int, default=None, help="Exact decoded frame number.")
    for selector in ("card", "shot", "occurrence", "clip", "track", "asset"):
        subparser.add_argument("--" + selector, default=None, help="Exact " + selector + " identity (shot also accepts an exact name).")
    subparser.add_argument("--range", dest="range_value", default=None, help="Half-open START..END seconds window.")
    subparser.add_argument("--json", action="store_true", help="Compact JSON envelope is always used.")
    subparser.set_defaults(handler=_cmd_inspect)


def offline_inspect_main(args: list[str]) -> int:
    """Separate parser keeps argument errors bounded and skips runtime startup."""
    class OfflineParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("invalid_arguments")

    parser = OfflineParser(prog="astrid timelines inspect")
    _configure_inspect(parser)
    try:
        return _cmd_inspect(parser.parse_args(args))
    except ValueError:
        from astrid.packs.rendering.executors.timeline_visualize.inspection_contract import _inspection_error
        print(json.dumps(_inspection_error("invalid_arguments"), separators=(",", ":")))
        return 2


def _cmd_render(parsed: argparse.Namespace) -> int:
    """Render one kernel timeline through the shared runtime scope resolver."""
    from astrid.sdk.contracts import ErrorObject

    inputs: dict[str, Any] = {}
    if parsed.ref not in (None, ""):
        inputs["timeline_ref"] = parsed.ref
    for name in ("expected_version", "output_name", "profile", "review"):
        value = getattr(parsed, name, None)
        if value not in (None, ""):
            inputs[name] = value
    if parsed.backend not in (None, ""):
        # ``--backend`` is the product-language spelling.  The rendering
        # executor's stable input/CLI contract calls this value ``selector``;
        # forwarding it as ``backend`` made the public option silently inert
        # because the manifest has no such input port.
        inputs["selector"] = parsed.backend
    result = parsed.client.invoke_result(
        "rendering.render",
        kind="executor",
        project=parsed.project,
        inputs=inputs,
        wait=parsed.wait,
        timeout_seconds=parsed.timeout_seconds,
    )
    if result.ok:
        run_id = result.kernel_run_id or result.run_id
        task_id = result.kernel_task_id
        handoff = (
            task_handoff(project=parsed.project, task_id=task_id, run_id=run_id)
            if task_id
            else {}
        )
        envelope = DomainResult.success(
            {
                "capability_id": result.capability_id,
                "run_id": result.run_id,
                "kernel_run_id": result.kernel_run_id,
                "kernel_task_id": result.kernel_task_id,
                "kernel_attempt_id": result.kernel_attempt_id,
                "state": str((result.raw_result or {}).get("state") or ("completed" if parsed.wait else "admitted")),
                "handoff": handoff,
                "outputs": result.outputs,
            }
        )
    else:
        detail = dict(result.error or {})
        category = str(detail.get("sdk_category") or "invocation")
        task_id = result.kernel_task_id
        run_id = result.kernel_run_id or result.run_id
        handoff = task_handoff(project=parsed.project, task_id=task_id, run_id=run_id) if task_id else {}
        envelope = DomainResult.failure(
            ErrorObject(
                code="validation_error" if category == "validation" else "invocation_error",
                message=str(detail.get("message") or "timeline render failed"),
                details={
                    "sdk_error": detail.get("sdk_error"),
                    "sdk_category": category,
                    "validation": detail.get("validation"),
                    "run_id": result.run_id,
                    "kernel_run_id": result.kernel_run_id,
                    "kernel_task_id": result.kernel_task_id,
                    "kernel_attempt_id": result.kernel_attempt_id,
                    "state": (result.raw_result or {}).get("state"),
                    "handoff": handoff,
                },
            )
        )
    if parsed.json or not envelope.ok:
        return print_result(envelope, as_json=parsed.json)
    data = envelope.data
    assert isinstance(data, Mapping)
    print(f"render {data['state']}")
    durable_run_id = data.get("kernel_run_id") or data.get("run_id")
    if durable_run_id:
        print(f"run: {durable_run_id}")
    if data.get("kernel_task_id"):
        print(f"task: {data['kernel_task_id']}")
    handoff = data.get("handoff")
    if isinstance(handoff, Mapping):
        if handoff.get("follow"):
            print(f"follow: {handoff['follow']}")
        if handoff.get("inspect"):
            print(f"inspect: {handoff['inspect']}")
        if handoff.get("events"):
            print(f"events: {handoff['events']}")
        if handoff.get("open"):
            print(f"open: {handoff['open']}")
        if handoff.get("recent"):
            print(f"recent: {handoff['recent']}")
    outputs = data.get("outputs")
    if data.get("state") == "completed" and isinstance(outputs, Mapping):
        artifacts = outputs.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if isinstance(artifact, str):
                    print(f"output: {artifact}")
                elif isinstance(artifact, Mapping):
                    location = next(
                        (artifact.get(key) for key in ("url", "path", "locator", "object_id") if artifact.get(key)),
                        None,
                    )
                    if location:
                        print(f"output: {location}")
    return 0


# -- parser ----------------------------------------------------------------


def _configure_list(subparser: argparse.ArgumentParser) -> None:
    _add_project_arg(subparser, required=False)
    subparser.add_argument(
        "--include-archived",
        dest="include_archived",
        action="store_true",
        help="Include archived timelines and their archived_at state.",
    )
    _add_json_flag(subparser)
    subparser.set_defaults(handler=_cmd_list)


def _configure_show(subparser: argparse.ArgumentParser) -> None:
    _add_project_arg(subparser, required=False)
    subparser.add_argument(
        "ref", nargs="?", default=None,
        help="Optional timeline UUID, ULID, or slug; omit to use the project's default timeline.",
    )
    subparser.add_argument(
        "--summary",
        action="store_true",
        help="Presentation flag for the canonical bounded inspection (no legacy document).",
    )
    subparser.add_argument(
        "--occurrence",
        default=None,
        help="Restrict the bounded inspection projection to one exact authored occurrence id.",
    )
    subparser.add_argument("--clip", default=None, help="Restrict the inspection projection to one authored clip id.")
    subparser.add_argument("--shot", default=None, help="Restrict the inspection projection to one authored shot id.")
    subparser.add_argument("--track", action="append", default=None, help="Restrict the inspection projection to one or more tracks.")
    subparser.add_argument("--asset", default=None, help="Restrict the inspection projection to one canonical asset key.")
    subparser.add_argument("--range", dest="range", default=None, help="Half-open START..END seconds window.")
    subparser.add_argument("--limit", type=int, default=50, help="Maximum bounded inspection rows (1–100).")
    subparser.add_argument("--cursor", default=None, help="Continue a bounded inspection page from its cursor.")
    subparser.add_argument("--revision-id", default=None, help="Inspect an exact immutable timeline revision.")
    subparser.add_argument("--detail", action="store_true", default=False, help="Include full bounded text for selected clips.")
    # ``show`` is the human inspection route by default. Machine callers use
    # the explicit stable envelope switch and keep the SDK shape unchanged.
    _add_json_flag(subparser, default=False)
    subparser.set_defaults(handler=_cmd_show)


def _configure_replace_parent_media(subparser: argparse.ArgumentParser) -> None:
    _add_project_arg(subparser)
    subparser.add_argument("ref", help="Timeline UUID, ULID, or slug.")
    subparser.add_argument("--occurrence-id", required=True, help="Exact parent-composition occurrence to edit.")
    subparser.add_argument("--clip-id", required=True, help="Exact internal-timeline clip id to replace.")
    subparser.add_argument("--source-object-id", required=True, help="Admitted project-owned media digest/object id.")
    subparser.add_argument(
        "--expected-head",
        required=True,
        help="Exact parent-composition revision id read from the target; stale heads fail closed.",
    )
    _add_idempotency_key(subparser)
    _add_json_flag(subparser)
    subparser.set_defaults(handler=_cmd_replace_parent_media)


def _configure_archive(subparser: argparse.ArgumentParser) -> None:
    _add_project_arg(subparser)
    subparser.add_argument("ref", help="Timeline UUID, ULID, or slug.")
    _add_idempotency_key(subparser)
    _add_json_flag(subparser)
    subparser.set_defaults(handler=_cmd_archive)


def _configure_recover(subparser: argparse.ArgumentParser) -> None:
    _add_project_arg(subparser)
    subparser.add_argument(
        "ref",
        help="Timeline UUID, ULID, or slug from list --include-archived.",
    )
    _add_idempotency_key(subparser)
    _add_json_flag(subparser)
    subparser.set_defaults(handler=_cmd_recover)


def _configure_history(subparser: argparse.ArgumentParser) -> None:
    _add_project_arg(subparser)
    subparser.add_argument("ref", help="Timeline UUID, ULID, or slug.")
    _add_json_flag(subparser)
    subparser.set_defaults(handler=_cmd_history)


def _configure_diff(subparser: argparse.ArgumentParser) -> None:
    _add_project_arg(subparser)
    subparser.add_argument("ref", help="Timeline UUID, ULID, or slug.")
    _add_json_flag(subparser)
    subparser.set_defaults(handler=_cmd_diff)


def _configure_visualize(subparser: argparse.ArgumentParser) -> None:
    _add_project_arg(subparser, required=False)
    subparser.add_argument(
        "timeline_ref", nargs="?", default=None,
        help="Optional positional timeline slug, UUID, or ULID (prefer --timeline-slug).",
    )
    subparser.add_argument(
        "--timeline-slug",
        default=None,
        help="Timeline slug, UUID, or ULID; omit to use the project default.",
    )
    subparser.add_argument(
        "--shot", default=None,
        help=(
            "Focus an authored shot id or exact name; use 'first' or a positive "
            "one-based authored-order ordinal (for example 1) for friendly shot selection."
        ),
    )
    subparser.add_argument("--range", dest="range", default=None, help="Zoom to a closed-open START..END seconds window.")
    subparser.add_argument("--at", default=None, help="Focus a timestamp.")
    subparser.add_argument("--frame", type=int, default=None, help="Capture one exact rendered frame number.")
    subparser.add_argument("--revision-id", default=None, help="Capture/inspect an exact immutable timeline revision.")
    subparser.add_argument("--clip", default=None, help="Focus an authored clip id.")
    subparser.add_argument("--occurrence", default=None, help="Focus an exact authored shot occurrence id.")
    subparser.add_argument("--asset", default=None, help="Focus a canonical asset key.")
    subparser.add_argument(
        "--show", action="append", default=None, metavar="COMPONENT[,COMPONENT...]",
        help=(
            "Add synchronized components: inputs, output, text, or audio "
            "(default: output,text,audio; add inputs for the paired view)."
        ),
    )
    subparser.add_argument(
        "--hide", action="append", default=None, metavar="COMPONENT[,COMPONENT...]",
        help="Hide components from the resolved surface.",
    )
    subparser.add_argument(
        "--track", action="append", default=None, metavar="TRACK_ID",
        help="Restrict input lanes; repeat for multiple tracks.",
    )
    subparser.add_argument(
        "--detail", action="store_true", default=None,
        help="Use enlarged frame, waveform, and text panels; combine with --range/--shot for a focused inspection.",
    )
    subparser.add_argument("--context", type=float, default=None, help="Context seconds around a focus.")
    subparser.add_argument("--neighbors", type=int, default=None, help="Neighbor clips retained around a focus.")
    subparser.add_argument(
        "--format", dest="formats", action="append", default=None,
        metavar="FORMAT[,FORMAT...]",
        type=_visualize_format_argument,
        help="Repeatable/comma-separated png or md (default: png,md).",
    )
    subparser.add_argument(
        "--mode",
        choices=("auto", "inputs", "composed"),
        default="auto",
        help=(
            "auto reuses an exact composed output or captures the pinned composition; "
            "inputs is render-free; composed always requests composed frames."
        ),
    )
    subparser.add_argument(
        "--view",
        choices=("filmstrip",),
        default="filmstrip",
        help="Rendered paired filmstrip (only view for composed output).",
    )
    subparser.add_argument("--sample", choices=("interval", "clips", "cuts", "shots"), default=None,
                           help="Filmstrip sampling: interval (default), picture clips, cut boundaries, or authored story beats.")
    sampling = subparser.add_mutually_exclusive_group()
    sampling.add_argument("--every", type=float, default=None,
                          help="Filmstrip interval in seconds (default: 0.5); rerun with --range START..END to zoom.")
    sampling.add_argument("--every-frames", type=int, default=None,
                          help="Filmstrip interval in exact rendered frames; replaces --every (rerun for finer samples).")
    subparser.add_argument(
        "--include-cuts", action="store_true", default=None,
        help="With interval sampling, also capture visual cut-neighbor frames.",
    )
    subparser.add_argument("--render-run", default=None,
                           help="Exact successful render run, or latest (filmstrip default).")
    subparser.add_argument("--columns", type=int, default=None,
                           help="Filmstrip contact sheet columns (default: 5; paired pages use one row by default).")
    subparser.add_argument("--page-size", type=int, default=None,
                           help="Filmstrip cards per static page (default: 50 standalone; paired pages use one row, or explicitly opt into up to two rows / 10 cards).")
    subparser.add_argument("--resolution", default=None, metavar="WIDTHxHEIGHT",
                           help="Filmstrip frame resolution, e.g. 960x540; recorded and applied exactly by the executor.")
    subparser.add_argument(
        "--include-media", action="store_true", default=None,
        help="Include a relative, digest-verified rendered video for offline filmstrip playback.",
    )
    subparser.add_argument(
        "--out", default=None,
        help=(
            "Unsupported compatibility option; project visualization owns output. "
            "Omit it and use the returned durable manifest_path."
        ),
    )
    _add_json_flag(subparser)
    subparser.set_defaults(handler=_cmd_visualize)


def _configure_render(subparser: argparse.ArgumentParser) -> None:
    _add_project_arg(subparser, required=False)
    subparser.add_argument("--review", action="store_true", default=None, help="Burn in shot names/time plus pinned authored speech captions at the bottom (Remotion/Three.js).")
    subparser.add_argument(
        "ref",
        nargs="?",
        default=None,
        help=(
            "Canonical timeline UUID, ULID, or slug. "
            "When omitted, the project's default timeline is rendered."
        ),
    )
    subparser.add_argument(
        "--expected-version",
        dest="expected_version",
        type=int,
        default=None,
        help="Optional exact kernel config version; stale pins fail before admission.",
    )
    subparser.add_argument(
        "--backend",
        default=None,
        help="Qualified renderer id or supported compatibility selector.",
    )
    subparser.add_argument(
        "--profile",
        type=_parse_json_object,
        default=None,
        metavar="JSON",
        help=(
            "Flat RenderProfile v1 JSON object (no video/audio nesting). "
            "Complete Remotion MP4 example: "
            "{\"width\": 1920, \"height\": 1080, \"fps_rational\": [30, 1], "
            "\"time_base\": [1, 90000], \"container\": \"mp4\", "
            "\"video_codec\": \"h264\", \"video_profile\": null, "
            "\"video_level\": null, \"pixel_format\": \"yuv420p\", "
            "\"audio_codec\": \"aac\", \"audio_sample_rate\": 48000, "
            "\"audio_channel_layout\": \"stereo\", \"duration_tolerance\": 1}. "
            "The audio trio must be supplied together or all omitted. When omitted, the "
            "resolved theme canvas is used (default 1920x1080 at 30 fps), "
            "not legacy config.output resolution/fps hints. Explicit profiles must "
            "match the authoritative theme canvas; set theme_overrides.visual.canvas "
            "for a different size."
        ),
    )
    subparser.add_argument(
        "--output-name",
        default=None,
        help=(
            "Plain output filename (default hype.mp4). A canonical timeline stamped "
            "metadata.astrid_layer.alpha=true may request .mov for ProRes 4444/PCM output."
        ),
    )
    completion = subparser.add_mutually_exclusive_group()
    completion.add_argument(
        "--wait",
        dest="wait",
        action="store_true",
        default=True,
        help="Follow the render to completion and propagate terminal failure (default).",
    )
    completion.add_argument(
        "--detach",
        dest="wait",
        action="store_false",
        help="Return after admission with state=admitted; inspect the returned task/run later.",
    )
    subparser.add_argument(
        "--timeout-seconds",
        type=float,
        default=3600.0,
        help="Maximum wait for --wait before returning a non-success result (default: 3600).",
    )
    _add_json_flag(subparser)
    subparser.set_defaults(handler=_cmd_render)


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        "list",
        help="List active timelines in a project (slug ascending).",
        configure=_configure_list,
    ),
    CommandSpec(
        "show",
        help="Show one timeline by UUID, ULID, or slug (use --summary for editor context).",
        configure=_configure_show,
    ),
    CommandSpec(
        "replace-parent-media",
        help="Atomically replace one clip in an exact canonical parent-composition closure.",
        configure=_configure_replace_parent_media,
    ),
    CommandSpec(
        "archive",
        help="Archive a timeline (reversible with recover).",
        configure=_configure_archive,
    ),
    CommandSpec(
        "recover",
        help="Restore archived work; safe to repeat (changed=false when active).",
        configure=_configure_recover,
    ),
    CommandSpec(
        "history",
        help="Ordered lifecycle event history for one timeline.",
        configure=_configure_history,
    ),
    CommandSpec(
        "diff",
        help="Deterministic adjacent-version diffs for one timeline.",
        configure=_configure_diff,
    ),
    CommandSpec(
        "visualize",
        help="Build a timeline evidence pack synchronously through the public SDK.",
        configure=_configure_visualize,
        requires_pack_host=True,
    ),
    CommandSpec(
        "inspect",
        help="Read bounded named sections of a verified filmstrip bundle offline.",
        configure=_configure_inspect,
    ),
    CommandSpec(
        "render",
        help="Render a canonical kernel timeline with optional version pinning.",
        configure=_configure_render,
        requires_pack_host=True,
    ),
)


def build_parser(client: Any) -> argparse.ArgumentParser:
    """Build the ``timelines`` product-family parser stamped with *client*.

    Exactly the supported verbs above are registered — no aliases, no legacy
    migration/push/pull/sync/audit/erase/repair verbs, and no ``copy``
    (reserved for m6) — plus the manifest-declared nested ``shots`` mount
    (``astrid timelines shots <verb>``) embedded from the shots product
    parser.
    """
    from astrid.packs.shots import cli as shots_cli

    def _configure_shots(subparser: argparse.ArgumentParser) -> None:
        nested = subparser.add_subparsers(dest="shot_command", required=True)
        register_product_commands(nested, shots_cli.COMMANDS, family="shots", client=client)

    parser = argparse.ArgumentParser(
        prog="astrid timelines",
        description=(
            "Timeline list/show/replace-parent-media/archive/recover/history/diff/visualize/render "
            "(product family); nested shots beneath 'timelines shots'."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_product_commands(
        subparsers,
        (
            *COMMANDS,
            CommandSpec(
                "shots",
                help="Nested project-level shot list/create/show/add/remove/reorder "
                "(manifest-owned mount).",
                configure=_configure_shots,
            ),
        ),
        family=_FAMILY,
        client=client,
    )
    return parser
