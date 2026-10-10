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

- ``create`` — ``client.timelines.create_empty``: the runtime identity shell plus
  the first empty parent-composition head, so the timeline can be checked out;
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
- ``visualize`` — the single public native timeline visualization operation;
  the default is a composed output with synchronized input lanes when the
  canonical route can capture it, while ``--mode inputs`` is the explicit
  render-free declared-input view.
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
import copy
import json
import re
import shlex
import sys
import time
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


def _parse_canvas(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*([1-9]\d*)[xX]([1-9]\d*)\s*", value)
    if match is None:
        raise argparse.ArgumentTypeError("canvas must be WIDTHxHEIGHT, e.g. 1920x1080")
    return int(match.group(1)), int(match.group(2))


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _cmd_create(parsed: argparse.Namespace) -> int:
    width, height = parsed.canvas
    # The canvas lives in the parent composition config, where the renderer
    # reads it (timeline-cookbook: theme_overrides.visual.canvas).
    config = {
        "tracks": [],
        "theme_overrides": {
            "visual": {"canvas": {"width": width, "height": height, "fps": parsed.fps}}
        },
    }
    if parsed.slug and parsed.timeline_id and parsed.slug != parsed.timeline_id:
        print("give the id once: a positional timeline_id or --slug, not both", file=sys.stderr)
        return 2
    result = parsed.client.timelines.create_empty(
        project=parsed.project,
        timeline_id=parsed.slug or parsed.timeline_id,
        config=config,
        idempotency_key=parsed.idempotency_key,
    )
    return print_result(result, as_json=parsed.json)


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
    dispatch = clip.get("compositor_dispatch")
    authored_prefix = "authored " if isinstance(dispatch, Mapping) and dispatch.get("status") == "resolved" else ""
    authored = clip.get("authored_fields") if isinstance(clip.get("authored_fields"), Mapping) else {}
    presentation = clip.get("presentation_fields") if isinstance(clip.get("presentation_fields"), Mapping) else {}
    opacity = clip.get("opacity", presentation.get("opacity", authored.get("opacity")))
    if isinstance(opacity, (int, float)) and opacity != 1 and opacity != 100:
        controls.append(authored_prefix + (f"opacity {opacity * 100:g}%" if 0 <= opacity <= 1 else f"opacity {opacity:g}%"))
    speed = clip.get("speed", clip.get("playback_rate"))
    if isinstance(speed, (list, tuple)) and len(speed) == 2:
        try:
            speed = float(Fraction(int(speed[0]), int(speed[1])))
        except (TypeError, ValueError, ZeroDivisionError):
            speed = None
    if isinstance(speed, (int, float)) and speed != 1:
        controls.append(f"{authored_prefix}speed {speed:g}×")
    blend = clip.get("blend", clip.get("blend_mode"))
    if blend not in (None, "", "normal"):
        controls.append(f"{authored_prefix}blend {blend}")
    if clip.get("muted") is True or clip.get("mute") is True:
        controls.append(f"{authored_prefix}muted")
    gain = clip.get("gain", clip.get("volume"))
    if isinstance(gain, (int, float)) and gain != 1:
        controls.append(f"{authored_prefix}gain {gain:g}")
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
    """Prefer the resolved compositor target, falling back to authored facts."""
    dispatch = clip.get("compositor_dispatch")
    if isinstance(dispatch, Mapping) and dispatch.get("status") == "resolved":
        dispatched_track = dispatch.get("track")
        dispatched_kind = dispatched_track.get("kind") if isinstance(dispatched_track, Mapping) else None
        if dispatched_kind in {"audio", "visual"}:
            return dispatched_kind == "audio"
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


def _human_dispatch_lines(clip: Mapping[str, Any]) -> list[str]:
    """Describe effective compositor controls without replacing authored tracks."""
    dispatch = clip.get("compositor_dispatch")
    if not isinstance(dispatch, Mapping):
        return []
    if dispatch.get("status") != "resolved":
        reason = dispatch.get("reason") or "compositor dispatch could not be resolved"
        return [f"         [compositor dispatch unknown: {reason}]"]

    track = dispatch.get("track") if isinstance(dispatch.get("track"), Mapping) else {}
    controls = dispatch.get("controls") if isinstance(dispatch.get("controls"), Mapping) else {}
    kind = track.get("kind")
    track_id = track.get("id") or "unknown"
    effective: list[str] = []
    if kind == "audio":
        if track.get("muted") is True or track.get("mute") is True:
            effective.append("muted")
        parent_volume = track.get("volume", track.get("gain"))
        if isinstance(parent_volume, (int, float)) and parent_volume != 1:
            effective.append(f"parent volume {parent_volume:g}")
        base_gain = controls.get("base_gain")
        if isinstance(base_gain, (int, float)):
            effective.append(f"effective base gain {base_gain:g}")
    elif kind == "visual":
        track_opacity = controls.get("track_opacity_multiplier")
        if isinstance(track_opacity, (int, float)) and track_opacity != 1:
            effective.append(
                f"effective track opacity {track_opacity * 100:g}%"
                if 0 <= track_opacity <= 1 else f"effective track opacity {track_opacity:g}%"
            )
        clip_opacity = controls.get("clip_opacity_multiplier")
        if isinstance(clip_opacity, (int, float)) and clip_opacity != 1:
            effective.append(
                f"effective clip opacity {clip_opacity * 100:g}%"
                if 0 <= clip_opacity <= 1 else f"effective clip opacity {clip_opacity:g}%"
            )
        blend = track.get("blend", track.get("blend_mode", track.get("blendMode")))
        if blend not in (None, "", "normal"):
            effective.append(f"blend {blend}")
    if effective:
        return [f"         compositor dispatch: {kind or 'unknown'} track {track_id} [{', '.join(effective)}]"]
    return [f"         compositor dispatch: {kind or 'unknown'} track {track_id}"]


def _human_dispatch_group_presentation(
    clips: list[Mapping[str, Any]],
) -> tuple[str | None, dict[int, list[str]]]:
    """Hoist controls shared by one resolved dispatch track to its exact heading."""
    resolved: list[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], str, str]] = []
    for clip in clips:
        dispatch = clip.get("compositor_dispatch")
        if not isinstance(dispatch, Mapping) or dispatch.get("status") != "resolved":
            return None, {}
        track = dispatch.get("track") if isinstance(dispatch.get("track"), Mapping) else {}
        controls = dispatch.get("controls") if isinstance(dispatch.get("controls"), Mapping) else {}
        kind = str(track.get("kind") or "unknown")
        track_id = str(track.get("id") or "unknown")
        resolved.append((clip, dispatch, track, kind, track_id))

    if not resolved:
        return None, {}

    _first_clip, first_dispatch, first_track, kind, track_id = resolved[0]
    first_controls = first_dispatch.get("controls", {})
    first_controls = first_controls if isinstance(first_controls, Mapping) else {}

    def shared_track_controls(
        track: Mapping[str, Any], controls: Mapping[str, Any], dispatch_kind: str
    ) -> list[str]:
        values: list[str] = []
        if dispatch_kind == "audio":
            if track.get("muted") is True or track.get("mute") is True:
                values.append("muted")
            parent_volume = track.get("volume", track.get("gain"))
            if isinstance(parent_volume, (int, float)) and parent_volume != 1:
                values.append(f"parent volume {parent_volume:g}")
        elif dispatch_kind == "visual":
            track_opacity = controls.get("track_opacity_multiplier")
            if isinstance(track_opacity, (int, float)) and track_opacity != 1:
                values.append(
                    f"effective track opacity {track_opacity * 100:g}%"
                    if 0 <= track_opacity <= 1 else f"effective track opacity {track_opacity:g}%"
                )
            blend = track.get("blend", track.get("blend_mode", track.get("blendMode")))
            if blend not in (None, "", "normal"):
                values.append(f"blend {blend}")
        return values

    shared = shared_track_controls(first_track, first_controls, kind)
    for _clip, dispatch, track, candidate_kind, candidate_id in resolved[1:]:
        controls = dispatch.get("controls", {})
        controls = controls if isinstance(controls, Mapping) else {}
        if (
            candidate_kind != kind
            or candidate_id != track_id
            or shared_track_controls(track, controls, candidate_kind) != shared
        ):
            return None, {}

    per_clip: dict[int, list[str]] = {}
    if kind == "audio":
        base_gains = []
        for _clip, dispatch, _track, _kind, _track_id in resolved:
            controls = dispatch.get("controls") if isinstance(dispatch.get("controls"), Mapping) else {}
            base_gains.append(controls.get("base_gain"))
        numeric_gains = [value for value in base_gains if isinstance(value, (int, float))]
        if (
            len(numeric_gains) == len(base_gains)
            and numeric_gains
            and all(value == numeric_gains[0] for value in numeric_gains)
        ):
            shared.append(f"effective base gain {numeric_gains[0]:g}")
        elif numeric_gains:
            for (clip, *_rest), base_gain in zip(resolved, base_gains):
                if isinstance(base_gain, (int, float)):
                    per_clip[id(clip)] = [f"         compositor dispatch clip controls: effective base gain {base_gain:g}"]
    elif kind == "visual":
        for clip, dispatch, _track, _kind, _track_id in resolved:
            controls = dispatch.get("controls") if isinstance(dispatch.get("controls"), Mapping) else {}
            clip_opacity = controls.get("clip_opacity_multiplier")
            if isinstance(clip_opacity, (int, float)) and clip_opacity != 1:
                label = (
                    f"effective clip opacity {clip_opacity * 100:g}%"
                    if 0 <= clip_opacity <= 1 else f"effective clip opacity {clip_opacity:g}%"
                )
                per_clip[id(clip)] = [f"         compositor dispatch clip controls: {label}"]

    dispatch_label = f"compositor dispatch: {kind} track {track_id}"
    if shared:
        dispatch_label += f" [{', '.join(shared)}]"
    return dispatch_label, per_clip


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


def _append_navigation_footer(
    lines: list[str], navigation: Mapping[str, Any], *, include_visualize: bool = True,
) -> None:
    """Keep sibling views, editing, and control help visible but compact."""
    commands = navigation.get("commands") if isinstance(navigation.get("commands"), Mapping) else {}
    if include_visualize and commands.get("visualize"):
        lines.append(f"visualize: {commands['visualize']}")
    if commands.get("show"):
        lines.append(f"show: {commands['show']}")
    if commands.get("controls"):
        lines.append(f"controls: {commands['controls']}")
    editing = navigation.get("editing") if isinstance(navigation.get("editing"), Mapping) else {}
    if editing.get("guide"):
        lines.append(f"edit guide: {editing['guide']}")
    edit_commands = editing.get("commands") if isinstance(editing.get("commands"), Mapping) else {}
    if edit_commands.get("checkout"):
        lines.append(f"edit JSON (current head): {edit_commands['checkout']}")


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
            suffix = f" [authored track controls: {', '.join(controls)}]" if controls else ""
            dispatch_heading, dispatch_clip_lines = _human_dispatch_group_presentation(group)
            dispatch_suffix = f" [{dispatch_heading}]" if dispatch_heading else ""
            lines.append(f"  {heading}{suffix}{dispatch_suffix}")
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
                if dispatch_heading:
                    lines.extend(dispatch_clip_lines.get(id(clip), []))
                else:
                    lines.extend(_human_dispatch_lines(clip))
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
    if data.get("page_hint"):
        lines.append(f"  {data['page_hint']}")
    elif pagination.get("next_cursor"):
        lines.append(f"  page: more results available (cursor {pagination['next_cursor']})")
    omissions = data.get("omission_metadata") if isinstance(data.get("omission_metadata"), Mapping) else native.get("omission_metadata") if isinstance(native.get("omission_metadata"), Mapping) else {}
    omitted_count = omissions.get("authored_values_omitted")
    if isinstance(omitted_count, int) and omitted_count > 0:
        lines.append(f"page: {omitted_count} bounded values omitted; full values are unavailable through a bounded retrieval route")
    navigation = data.get("navigation") if isinstance(data.get("navigation"), Mapping) else {}
    if navigation:
        _append_navigation_footer(lines, navigation)
    return "\n".join(lines)


def _decimal_projection(data: dict[str, Any]) -> dict[str, Any]:
    """Presentation: rational time pairs become decimal seconds; the exact value stays under ``*_exact``."""
    from astrid.sdk.timeline_cuts import decimal_seconds

    return decimal_seconds(data)


def _projection_cursor(data: Mapping[str, Any]) -> str | None:
    """The next cursor of a paged inspection projection, if there is one."""
    pagination = data.get("pagination") if isinstance(data.get("pagination"), Mapping) else {}
    native = data.get("native_inspection") if isinstance(data.get("native_inspection"), Mapping) else {}
    cursor = pagination.get("next_cursor") or native.get("next_cursor")
    return cursor if isinstance(cursor, str) and cursor else None


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
    # Default view: the editor's complete cut table (every cut, layer and word; --json is the same
    # table, complete). The per-track inspection projection (paged, --limit/--cursor) stays behind
    # --layers and the clip/occurrence/asset/track/cursor/detail selectors it alone supports.
    layered = bool(
        getattr(parsed, "layers", False)
        or any(values.get(name) for name in ("clip", "occurrence", "asset", "track", "cursor"))
        # --detail with --at or a text view (--as code/script) means "untruncated", not the layer rows
        or (values.get("detail") and not getattr(parsed, "at", None) and (getattr(parsed, "as_view", None) or "cuts") == "cuts")
    )
    bundle_opener = getattr(parsed.client.timelines, "open_bundle", None)
    if getattr(parsed, "address", None):
        return _print_address(parsed, bundle_opener)
    if getattr(parsed, "as_view", None) == "sheet":
        return _print_sheet(parsed, bundle_opener)
    if not layered and callable(bundle_opener):
        return _print_cut_table(parsed, bundle_opener)
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
        if parsed.json:
            # Machine output reads decimal seconds; the human renderer keeps the exact rationals it computes with.
            data = _decimal_projection(data)
        data["working_copy"] = None
        cursor = _projection_cursor(data)
        if cursor:
            data["next_cursor"] = cursor
            shown = [clip for clip in data.get("clips") or [] if isinstance(clip, Mapping)]
            bundle_opener = getattr(parsed.client.timelines, "open_bundle", None)
            total = None
            if callable(bundle_opener):
                from astrid.sdk.timeline_cuts import count_clips

                opened = bundle_opener(parsed.project, parsed.ref, revision_id=values.get("revision_id"))
                if opened.ok and isinstance(opened.data, Mapping):
                    total = count_clips(opened.data["bundle"])
            last_shot = shown[-1].get("shot_id") if shown else None
            count = f"{len(shown)} of {total}" if total is not None else f"{len(shown)} (more)"
            data["page_hint"] = (
                f"showing {count} clips · next: --cursor {cursor}"
                + (f" · or --shot {last_shot}" if last_shot else "")
            )
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


def _navigation_scope(
    *, project: str | None, timeline: str | None, outputs: Mapping[str, Any],
) -> tuple[str, str]:
    """Resolve copyable navigation scope without guessing a new default."""
    containers = [outputs]
    for key in ("summary", "scope", "native_inspection", "inspection", "provenance"):
        value = outputs.get(key)
        if isinstance(value, Mapping):
            containers.append(value)

    def first(keys: tuple[str, ...], fallback: str) -> str:
        if fallback not in (None, ""):
            return str(fallback)
        for container in containers:
            for key in keys:
                value = container.get(key)
                if value not in (None, ""):
                    return str(value)
        return fallback or "<unresolved>"

    return (
        first(("project_slug", "project_id", "project"), project),
        first(("timeline_slug", "timeline_id", "timeline", "ref"), timeline),
    )


def _timeline_editing_resources() -> dict[str, Any]:
    """Locate the installed checkout recipe without depending on cwd."""
    skill_root = Path(__file__).resolve().parents[1] / "rendering" / "skill"
    guide = skill_root / "references" / "document-checkout.md"
    script = skill_root / "scripts" / "timeline_document.py"
    return {
        "guide": str(guide),
        "script": str(script),
        "workflow": ["checkout", "edit", "check", "publish"],
        "scope": "complete parent/shot/internal-timeline composition",
        "checkout_revision": "current Runtime head at checkout; historical revisions remain read-only",
    }


def _editing_navigation(
    *, project: str | None, timeline: str | None,
) -> dict[str, Any]:
    """Return one small, copyable entry point for detached JSON editing."""
    resources = _timeline_editing_resources()
    file_path = "/tmp/timeline-edit.json"
    checkout = shlex.join([
        "python3", "-m", "astrid.packs.rendering.skill.scripts.timeline_document",
        "checkout", "--project", project or "<project>", "--timeline", timeline or "<timeline>",
        "--file", file_path,
    ])
    check = shlex.join([
        "python3", "-m", "astrid.packs.rendering.skill.scripts.timeline_document",
        "check", "--file", file_path,
    ])
    publish = shlex.join([
        "python3", "-m", "astrid.packs.rendering.skill.scripts.timeline_document",
        "publish", "--file", file_path, "--idempotency-key", "<edit-key>",
    ])
    resources["commands"] = {"checkout": checkout, "check": check, "publish": publish}
    return resources


def _show_navigation_help(
    *, project: str | None, ref: str | None, parsed: argparse.Namespace, outputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Offer a revision-pinned visual continuation from structural show output."""
    resolved_project, resolved_timeline = _navigation_scope(
        project=project, timeline=ref, outputs=outputs,
    )
    argv = ["python3", "-m", "astrid", "timelines", "visualize", "--project", resolved_project]
    if resolved_timeline not in (None, ""):
        argv += ["--timeline-slug", resolved_timeline]
    revision = getattr(parsed, "revision_id", None) or _revision_from_outputs(outputs)
    if revision:
        argv += ["--revision-id", str(revision)]
    for name, flag in (("occurrence", "--occurrence"), ("shot", "--shot"), ("clip", "--clip"), ("asset", "--asset"), ("range", "--range")):
        value = getattr(parsed, name, None)
        if value not in (None, ""):
            argv += [flag, str(value)]
    for track in getattr(parsed, "track", None) or []:
        argv += ["--track", str(track)]
    edit = _editing_navigation(project=resolved_project, timeline=resolved_timeline)
    return {
        "commands": {
            "visualize": shlex.join(argv),
            "controls": "python3 -m astrid timelines visualize --help",
        },
        "editing": edit,
        "authority": revision or "resolved by Runtime current head",
        "note": "The visual continuation preserves the saved revision and structural filters; checkout starts a fresh current-head editable file.",
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


def _working_copy_view(parsed: argparse.Namespace) -> dict[str, Any] | None:
    """The unpublished working copy of this timeline (its document and edits), or None."""
    from astrid.sdk.timeline_checkout import Checkout, find_draft

    try:
        path = find_draft(parsed.project, parsed.ref)
    except Exception as exc:  # the runtime is not reachable: say so, show the published head
        print(f"working copy: not checked ({exc}); showing the published head", file=sys.stderr)
        return None
    if path is None:
        return None
    checkout = Checkout.load(path)
    return {
        "bundle": checkout.document(),
        "path": str(path),
        "base_revision": checkout.base_revision,
        "changes": list(checkout.edits().get("changes") or []),
    }


def _short_rev(revision: Any) -> str:
    text = str(revision or "")
    return text.removeprefix("authoring-parent-revision-")[:8] or text


def _show_checkout(parsed: argparse.Namespace, bundle_opener: Any) -> tuple[Any, str] | int:
    """The timeline ``show`` reads, as a Checkout: the working copy (unless --published/--revision-id)
    or the published head; with the banner line that says which. An int is an exit code."""
    from astrid.sdk.timeline_checkout import Checkout, find_draft

    if not getattr(parsed, "published", False) and not getattr(parsed, "revision_id", None):
        try:
            path = find_draft(parsed.project, parsed.ref, client=parsed.client)
        except Exception as exc:  # noqa: BLE001 - say so and show the head
            print(f"working copy: not checked ({exc}); showing the published head", file=sys.stderr)
            path = None
        if path is not None:
            tl = Checkout.load(path)
            n = len(tl.changes())
            return tl, (f"WORKING COPY · {n} unpublished change{'s' if n != 1 else ''} vs published "
                        f"{_short_rev(tl.base_revision)} · --published for the live version")
    opened = bundle_opener(parsed.project, parsed.ref, revision_id=getattr(parsed, "revision_id", None))
    if not opened.ok or not isinstance(opened.data, Mapping):
        return print_result(opened, as_json=False)
    tl = Checkout(dict(opened.data["bundle"]))
    return tl, f"PUBLISHED {_short_rev(opened.data.get('revision_id') or (tl.bundle.get('base_parent') or {}).get('revision_id'))}"


def _print_sheet(parsed: argparse.Namespace, bundle_opener: Any) -> int:
    """``show --as sheet``: the cut sheet of the working copy (or the published head)."""
    from astrid.sdk.timeline_address import AddressError, resolve
    from astrid.sdk.timeline_sheet import SheetError, moment_range, render_sheet

    got = _show_checkout(parsed, bundle_opener)
    if isinstance(got, int):
        return got
    tl, banner = got
    start = end = None
    try:
        if parsed.range:
            start, end = moment_range(tl, parsed.range)
        elif getattr(parsed, "at", None):
            # --at scopes the sheet to the cut on screen then
            target = resolve(tl, parsed.at, prefer="time")
            spans = tl._cut_spans()
            t = target.start
            cut_id = next((cid for cid, (lo, hi) in spans.items() if lo - 1e-6 <= t < (hi if hi is not None else tl.duration + 1)), None)
            if cut_id is None:
                raise AddressError(f"no cut is on screen at {t:.2f} s")
            start, end = spans[cut_id][0], spans[cut_id][1] or tl.duration
            print(f"--at {parsed.at!r}: {target} · in {cut_id}", file=sys.stderr)
    except (SheetError, AddressError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(render_sheet(tl, start=start, end=end, banner=banner, film=str(parsed.project)), end="")
    where = f"{parsed.ref} --project {parsed.project}"
    # a hint only on a terminal (to stderr): a redirected sheet stays exactly the sheet, even with 2>&1
    if sys.stdout.isatty():
        print(f"next: save it (> FILE), change a line, then  timelines apply {where} FILE", file=sys.stderr)
    return 0


def _print_address(parsed: argparse.Namespace, bundle_opener: Any) -> int:
    """``show TL ADDRESS``: the complete record of one thing (a layer, a word, a cut, a time)."""
    from astrid.sdk.timeline_address import AddressError, describe_target, resolve

    got = _show_checkout(parsed, bundle_opener)
    if isinstance(got, int):
        return got
    tl, banner = got
    try:
        target = resolve(tl, parsed.address, prefer="thing")
    except AddressError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(banner)
    print(describe_target(tl, target, timeline=str(parsed.ref), project=str(parsed.project)))
    return 0


def _at_detail(bundle: Mapping[str, Any], seconds: float) -> str:
    """``show --at T --detail``: each clip on screen at T once, with its moments and every param untruncated."""
    from astrid.sdk import timeline_intent as intent
    from astrid.sdk.timeline_checkout import Checkout

    tl = Checkout(copy.deepcopy(dict(bundle)))
    lines = [f"on screen at {seconds:.3f} s (every param, untruncated):"]
    for clip in tl.clips():
        if clip.is_audio or not (clip.start - 1e-6 <= seconds < clip.end - 1e-6):
            continue
        when = " ".join(x for x in (f"on {intent.on(clip.data)}" if intent.on(clip.data) else "",
                                     f"until {intent.until(clip.data)}" if intent.until(clip.data) else "") if x)
        params = json.dumps(clip.data.get("params") or {}, ensure_ascii=False, separators=(", ", ": "))
        lines.append(f"  {clip.address:<18} {clip.element:<14} {clip.asset or '':<14} {clip.start:7.3f}–{clip.end:.3f} s  {when}")
        lines.append(f"      {params}")
    return "\n".join(lines)


def _working_banner(working: Mapping[str, Any]) -> str:
    """The one-line banner show, lint and diff print when they read the working copy."""
    return (f"WORKING COPY · {len(working['changes'])} unpublished edits vs published {working['base_revision']} · "
            "--published for the live version")


def _print_cut_table(parsed: argparse.Namespace, bundle_opener: Any) -> int:
    """Print the cut table for ``timelines show`` (human default, or the complete --json)."""
    from astrid.sdk.timeline_cuts import (
        build_cut_table,
        cut_table_payload,
        filter_rows,
        page_rows,
        paging_hint,
        parse_seconds,
        render_cut_table,
        render_summary,
        resolve_shot,
    )
    from astrid.sdk.timeline_views import render_at, render_clips

    opened = bundle_opener(parsed.project, parsed.ref, revision_id=getattr(parsed, "revision_id", None))
    if not opened.ok or not isinstance(opened.data, Mapping):
        return print_result(opened, as_json=bool(parsed.json))
    data = opened.data
    # The working copy (unpublished draft) is what show reads unless --published or an exact revision is asked for.
    working = None
    if not getattr(parsed, "published", False) and not getattr(parsed, "revision_id", None):
        working = _working_copy_view(parsed)
    bundle = working["bundle"] if working else data["bundle"]
    table = build_cut_table(bundle)
    changes = working["changes"] if working else []
    changed = {str(change.get("clip_id")) for change in changes if change.get("clip_id")}
    working_info = None if working is None else {
        "draft": working["path"], "base_revision": working["base_revision"], "edits": len(changes),
    }
    banner: list[str] = []
    if working_info is not None:
        banner = [
            f"WORKING COPY · {len(changes)} unpublished edits vs published {working['base_revision']} · "
            "--published for the live version"
        ]
    if parsed.json:
        payload = cut_table_payload(bundle, table)
        payload.update({
            "project_id": data.get("project_id"),
            "timeline_id": data.get("timeline_id"),
            "revision_id": data.get("revision_id"),
            "is_current_head": data.get("is_current_head"),
            "head_revision_id": data.get("head_revision_id"),
            "working_copy": working_info,
        })
        return print_result(
            DomainResult.success(payload, receipt=opened.receipt, idempotency_key=opened.idempotency_key),
            as_json=True,
        )
    revision = str(data.get("revision_id") or "")
    state = "current head" if data.get("is_current_head") else f"saved revision (current head is {data.get('head_revision_id')})"
    title = f"Timeline {data.get('timeline_id')} · project {parsed.project or data.get('project_id')} · {state}\n  revision {revision}"
    project = str(parsed.project or data.get("project_id"))
    timeline = str(parsed.ref or data.get("timeline_id"))
    show_base = ["python3", "-m", "astrid", "timelines", "show", timeline, "--project", project]

    if getattr(parsed, "at", None):
        from astrid.sdk.timeline_address import AddressError, resolve
        from astrid.sdk.timeline_checkout import Checkout

        try:
            seconds = parse_seconds(parsed.at)
            at_note = None
        except ValueError:
            try:
                target = resolve(Checkout(copy.deepcopy(dict(bundle))), parsed.at, prefer="time")
            except AddressError as exc:
                print(f"error: {exc}")
                return 2
            seconds, at_note = target.start, f"--at {parsed.at!r}: {target}"
        text = render_at(bundle, table, seconds, changed=changed, show_command=shlex.join(show_base))
        if text is not None and at_note:
            text = at_note + "\n" + text
        if text is not None and getattr(parsed, "detail", False):
            text += "\n" + _at_detail(bundle, seconds)
        if text is None:
            print(f"error validation_error: no cut at {seconds:.2f} s (the timeline runs {table['duration']:.2f} s)")
            return 2
        print("\n".join(banner + [title, text]))
        return 0
    if getattr(parsed, "clips", False):
        if not getattr(parsed, "shot", None):
            print("error validation_error: --clips needs --shot N (one chapter)")
            return 2
        try:
            shot_ids = resolve_shot(table, parsed.shot)
        except ValueError as exc:
            print(f"error validation_error: {exc}")
            return 2
        print("\n".join(banner + [title, render_clips(bundle, shot_ids, changed=changed)]))
        return 0
    if getattr(parsed, "summary", False):
        lines = banner + [title, "", render_summary(table)]
        if changes:
            lines += ["", f"unpublished edits ({len(changes)}):"]
            for change in changes:
                lines.append(f"  ✎ {change.get('kind')} {change.get('clip_id')} "
                             f"({', '.join(change.get('fields') or []) or '-'}) at {change.get('after') or change.get('before')}")
        lines += ["", f"full table: {shlex.join(show_base)}   (no flag)  ·  one chapter: --shot N   ·  clips in a chapter: --shot N --clips"]
        print("\n".join(lines))
        return 0
    range_value = getattr(parsed, "range", None)
    if range_value and not re.fullmatch(r"\s*[\d.:]+\s*\.\.\s*[\d.:]+\s*", str(range_value)):
        # a cut (c30), cut range (c30..c31, inclusive), words ("a".."b") or a layer: the same addresses as everywhere
        from astrid.sdk.timeline_address import AddressError, resolve
        from astrid.sdk.timeline_checkout import Checkout

        try:
            target = resolve(Checkout(copy.deepcopy(dict(bundle))), range_value, prefer="time")
        except AddressError as exc:
            print(f"error: {exc}")
            return 2
        range_value = f"{target.start:.3f}..{max(target.end, target.start + 1e-3):.3f}"
    try:
        rows = filter_rows(table, range_value=range_value, shot=getattr(parsed, "shot", None))
    except ValueError as exc:
        print(f"error validation_error: {exc}")
        return 2
    view = getattr(parsed, "as_view", None) or "cuts"
    if view != "cuts":
        print("\n".join(banner + [_render_view(view, bundle, parsed, rows, title=title, changed=changed)]))
        return 0
    try:
        shown, info = page_rows(rows, page=getattr(parsed, "page", None), page_size=getattr(parsed, "page_size", None))
    except ValueError as exc:
        print(f"error validation_error: {exc}")
        return 2
    lines = banner + [render_cut_table(table, shown, title=title, changed=changed), ""]
    if info is not None:
        lines.insert(len(lines) - 1, paging_hint(table, rows, shown, info))
    visual = ["python3", "-m", "astrid", "timelines", "visualize", timeline, "--project", project]
    if not data.get("is_current_head"):
        visual += ["--revision-id", revision]
    if shown and (getattr(parsed, "range", None) or getattr(parsed, "shot", None) or info is not None):
        low, high = shown[0]["start"], shown[-1]["end"]
        lines.append("see these cuts: " + shlex.join(visual + ["--view", "contact", "--range", f"{low:.2f}..{high:.2f}"]))
    else:
        lines.append("see the whole video: " + shlex.join(visual + ["--view", "contact"]))
    lines.append("one moment: " + shlex.join(show_base) + " --at 1:02   ·   one chapter: --shot N --clips   ·   overview: --summary")
    lines.append("one moment on screen: " + shlex.join(visual) + " --at SECONDS   ·   every N frames: --range A..B --every-frames N")
    lines.append("how one cut moves: " + shlex.join(visual + ["--view", "motion", "--cut", "N"])
                 + "   ·   lint: " + shlex.join(["python3", "-m", "astrid", "timelines", "lint", timeline, "--project", project]))
    edit = _editing_navigation(project=project, timeline=timeline)
    lines.append(f"edit: {edit['commands']['checkout']}   (guide: {edit['guide']})")
    lines.append("per-track layer rows: --layers · SDK envelope (complete): --json")
    print("\n".join(lines))
    return 0


def _render_view(
    view: str, bundle: Mapping[str, Any], parsed: argparse.Namespace, rows: list, *, title: str,
    changed: set[str] | None = None,
) -> str:
    """The script or code representation, scoped like the cut table (--range, --shot)."""
    from astrid.sdk.timeline_cuts import _parse_range
    from astrid.sdk.timeline_views import render_code, render_script

    window = _parse_range(parsed.range) if getattr(parsed, "range", None) else None
    shot_ids = {row["shot_id"] for row in rows} if getattr(parsed, "shot", None) else None
    if view == "script":
        return title + "\n" + render_script(bundle, window=window, shot_ids=shot_ids, changed=changed)
    return render_code(bundle, window=window, shot_ids=shot_ids, header="# " + title.replace("\n  ", "\n# "),
                       full=bool(getattr(parsed, "detail", False)))


def _cmd_lint(parsed: argparse.Namespace) -> int:
    """Every registered check (built-in and pack) over every picture cut, from timeline data (no capture)."""
    from astrid.core.timeline.cuts import (
        bundle_fps,
        find_cut,
        occurrences_from_bundle,
        picture_cuts,
    )
    from astrid.packs.rendering.executors.timeline_visualize.layers.base import check_help
    from astrid.packs.rendering.executors.timeline_visualize.motion import model
    from astrid.packs.rendering.executors.timeline_visualize.motion.conditions import run_checks
    from astrid.packs.rendering.executors.timeline_visualize.motion.rules import resolve_rules

    try:
        rules = resolve_rules(getattr(parsed, "rules", None), search=not getattr(parsed, "no_rules", False))
    except (OSError, ValueError) as exc:
        print(f"error validation_error: {exc}", file=sys.stderr)
        return 2
    if getattr(parsed, "list_checks", False):
        print(check_help(rules))
        return 0
    opener = getattr(parsed.client.timelines, "open_bundle", None)
    if not callable(opener):
        print("error unavailable: this client cannot open a timeline bundle", file=sys.stderr)
        return 1
    opened = opener(parsed.project, parsed.ref, revision_id=getattr(parsed, "revision_id", None))
    if not opened.ok or not isinstance(opened.data, Mapping):
        return print_result(opened, as_json=parsed.json)
    bundle = opened.data["bundle"]
    working = None
    if parsed.ref and not getattr(parsed, "published", False) and not getattr(parsed, "revision_id", None):
        working = _working_copy_view(parsed)
    if working is not None:
        bundle = working["bundle"]
    fps = bundle_fps(bundle)
    occurrences = occurrences_from_bundle(bundle)
    all_cuts = picture_cuts(occurrences, fps=fps)
    cuts = all_cuts
    try:
        if getattr(parsed, "cut", None):
            cuts = [find_cut(all_cuts, parsed.cut)]
        elif getattr(parsed, "range", None):
            from astrid.sdk.timeline_cuts import _parse_range

            low, high = _parse_range(parsed.range)
            cuts = [cut for cut in all_cuts if cut["end"] > low and cut["start"] < high]
        beats = json.loads(_read_beats(parsed.beats)) if getattr(parsed, "beats", None) else None
    except (OSError, ValueError) as exc:
        print(f"error validation_error: {exc}", file=sys.stderr)
        return 2
    elements = model.elements_from_occurrences(occurrences)
    track_order = next((o.get("track_order") for o in occurrences if o.get("track_order")), [])
    params = dict((rules or {}).get("params") or {})
    if getattr(parsed, "min_text_px", None) is not None:
        params["min_text_px"] = float(parsed.min_text_px)
    results, timeline_findings = run_checks(
        cuts, elements, fps, track_order=track_order, beats=beats, params=params,
        severity=(rules or {}).get("severity"), disable=(rules or {}).get("disable") or (), all_cuts=all_cuts,
    )
    if parsed.json:
        rows = [{**f.as_dict(), "cut_start": cut["start"], "cut_end": cut["end"]}
                for cut, findings in results for f in findings]
        rows += [f.as_dict() for f in timeline_findings]
        print(json.dumps({"ok": True, "data": {"timeline_id": opened.data.get("timeline_id"),
                                                "revision_id": opened.data.get("revision_id"),
                                                "working_copy": working is not None,
                                                "rules": (rules or {}).get("path"), "findings": rows},
                          "error": None}, indent=2))
        return 0
    counts: dict[str, int] = {}
    info: dict[str, int] = {}
    if rules:
        effective = ", ".join(f"{k}={v:g}" for k, v in sorted(rules["params"].items()))
        severities = ", ".join(f"{k}={v}" for k, v in sorted(rules["severity"].items()))
        rules_line = (f"rules: {rules['path']} ({effective or 'no thresholds'}"
                      + (f"; severity {severities}" if severities else "") + ")")
    else:
        rules_line = ("rules: built-in defaults (no astrid-lint.toml in this folder or its parents; "
                      "--rules FILE to name one; --list-checks for the keys)")
    lines = [
        f"Timeline {opened.data.get('timeline_id')} · {len(results)} cut(s) · revision {opened.data.get('revision_id')}",
        rules_line,
    ]
    if working is not None:
        lines.insert(0, _working_banner(working))
    rank = {"error": 0, "warn": 1, "info": 2}
    from astrid.sdk.timeline_address import cut_ids_by_ordinal, name_things, ordinals_to_ids
    from astrid.sdk.timeline_checkout import Checkout

    named = Checkout(copy.deepcopy(dict(bundle)))
    ordinals = cut_ids_by_ordinal(named)
    # With a working copy, lint shows what is NEW since your checkout; --all shows everything.
    known: set[str] = set()
    if working is not None and not parsed.all:
        base_bundle = opened.data["bundle"]
        base_occurrences = occurrences_from_bundle(base_bundle)
        base_cuts = picture_cuts(base_occurrences, fps=fps)
        base_results, base_timeline = run_checks(
            base_cuts, model.elements_from_occurrences(base_occurrences), fps, track_order=track_order, beats=beats,
            params=params, severity=(rules or {}).get("severity"), disable=(rules or {}).get("disable") or (),
            all_cuts=base_cuts,
        )
        known = {f.line(c["start"]) for c, fs in base_results for f in fs} | {f.line(None) for f in base_timeline}
    already = 0

    def emit(finding, start=None, end=None) -> None:
        nonlocal already
        text = finding.line(start)
        if text in known:
            already += 1
            return
        bucket = info if finding.severity == "info" else counts
        bucket[finding.code] = bucket.get(finding.code, 0) + 1
        if finding.severity != "info" or parsed.all:
            prefix = "ERROR " if finding.severity == "error" else ""
            shown = name_things(text, named, start, end) if start is not None and end is not None else text
            lines.append(prefix + ordinals_to_ids(shown, named, ordinals))

    for finding in sorted(timeline_findings, key=lambda f: rank.get(f.severity, 3)):
        emit(finding)
    from astrid.packs.rendering.executors.timeline_visualize.layers.base import checks as registered_checks

    ran = sorted(name for name in registered_checks() if name not in set((rules or {}).get("disable") or ())
                 and "frames" not in registered_checks()[name].needs)
    lines.append(f"checks run: {', '.join(ran)} (a check with no line passed or is off: see --list-checks)")
    for cut, findings in results:
        for finding in findings:
            emit(finding, cut["start"], cut["end"])
    summary = ", ".join(f"{code} {n}" for code, n in sorted(counts.items(), key=lambda item: -item[1])) or "none"
    if working is not None and not parsed.all:
        lines.append(f"(new since your checkout only; {already} finding(s) were already in the published version: --all shows everything)")
    lines.append(f"findings: {summary}" + (
        f"; info hidden ({', '.join(f'{c} {n}' for c, n in sorted(info.items()))}; --all shows them)" if info and not parsed.all else ""))
    project = str(parsed.project or "<project>")
    lines.append("see one: " + shlex.join(["python3", "-m", "astrid", "timelines", "visualize", str(parsed.ref), "--project", project,
                                           "--view", "motion", "--cut", "N"])
                 + "   ·   machine-applicable fixes: --json")
    print("\n".join(lines))
    errors = sum(1 for _cut, fs in results for f in fs if f.severity == "error") + sum(
        1 for f in timeline_findings if f.severity == "error")
    return 1 if errors and getattr(parsed, "strict", False) else 0


def _cmd_history(parsed: argparse.Namespace) -> int:
    result = parsed.client.timelines.history(parsed.project, parsed.ref)
    return print_result(result, as_json=parsed.json)


def _cmd_diff(parsed: argparse.Namespace) -> int:
    from_revision = getattr(parsed, "from_revision", None)
    plain = from_revision is None and getattr(parsed, "to_revision", None) is None
    plain = plain and getattr(parsed, "from_version", None) is None and getattr(parsed, "to_version", None) is None
    if plain and parsed.ref and not getattr(parsed, "published", False):
        working = _working_copy_view(parsed)
        if working is not None:
            return _diff_working_copy(parsed, working)
    if from_revision is None:
        from_version = getattr(parsed, "from_version", None)
        to_version = getattr(parsed, "to_version", None)
        if from_version is None and to_version is None:
            print(
                "error validation_error: say what to compare: --from <revision-id> [--to <revision-id>] "
                "compares two saved revisions (default --to: current head). The previous head of an edit "
                "is old_head in its .publication.json; `timelines show` prints the current revision."
            )
            return 2
        result = parsed.client.timelines.diff(
            parsed.project, parsed.ref, from_version=from_version, to_version=to_version,
        )
        return print_result(result, as_json=parsed.json)
    opener = getattr(parsed.client.timelines, "open_bundle", None)
    if not callable(opener):
        print("error unavailable: this client cannot open timeline revisions")
        return 2
    from astrid.sdk.timeline_cuts import diff_bundles, render_diff

    before = opener(parsed.project, parsed.ref, revision_id=from_revision)
    if not before.ok or not isinstance(before.data, Mapping):
        return print_result(before, as_json=parsed.json)
    after = opener(parsed.project, parsed.ref, revision_id=getattr(parsed, "to_revision", None))
    if not after.ok or not isinstance(after.data, Mapping):
        return print_result(after, as_json=parsed.json)
    old, new = str(before.data["revision_id"]), str(after.data["revision_id"])
    view = getattr(parsed, "as_view", None) or "cuts"
    if view != "cuts":
        import difflib

        from astrid.sdk.timeline_views import render_code, render_script

        render = render_script if view == "script" else render_code
        text = difflib.unified_diff(
            render(before.data["bundle"]).splitlines(), render(after.data["bundle"]).splitlines(),
            fromfile=old, tofile=new, lineterm="", n=2,
        )
        print("\n".join(text) or f"No differences in the {view} view.")
        return 0
    diff = diff_bundles(before.data["bundle"], after.data["bundle"])
    diff.update({"from_revision": old, "to_revision": new, "timeline_id": after.data.get("timeline_id")})
    commands: dict[str, str] = {}
    for low, high in diff.get("windows") or []:
        for label, revision in (("before", old), ("after", new)):
            commands[f"{label} {low:g}..{high:g}"] = shlex.join([
                "python3", "-m", "astrid", "timelines", "visualize", str(parsed.ref), "--project", str(parsed.project),
                "--revision-id", revision,
                "--range", f"{low:g}..{high:g}", "--every-frames", "5",
            ])
    diff["commands"] = commands
    if parsed.json:
        print(json.dumps({"ok": True, "data": diff, "error": None}, indent=2, sort_keys=True))
        return 0
    lines = [render_diff(diff, title=f"Timeline {after.data.get('timeline_id')}: {old} → {new}")]
    if commands:
        lines.append("see it: frames of only the changed moments, before and after (no full render):")
        lines.extend(f"  {label}: {command}" for label, command in commands.items())
    print("\n".join(lines))
    return 0


def _diff_working_copy(parsed: argparse.Namespace, working: Mapping[str, Any]) -> int:
    """The published head against the working copy, with the banner."""
    from astrid.sdk.timeline_cuts import diff_bundles, render_diff

    opener = getattr(parsed.client.timelines, "open_bundle", None)
    if not callable(opener):
        print("error unavailable: this client cannot open timeline revisions")
        return 2
    head = opener(parsed.project, parsed.ref, revision_id=None)
    if not head.ok or not isinstance(head.data, Mapping):
        return print_result(head, as_json=parsed.json)
    old = str(head.data["revision_id"])
    diff = diff_bundles(head.data["bundle"], working["bundle"])
    diff.update({"from_revision": old, "to_revision": "working copy", "timeline_id": head.data.get("timeline_id")})
    if parsed.json:
        print(json.dumps({"ok": True, "data": {**diff, "working_copy": {"base_revision": working["base_revision"],
                          "edits": len(working["changes"])}}, "error": None}, indent=2, sort_keys=True))
        return 0
    print(_working_banner(working))
    print(render_diff(diff, title=f"Timeline {head.data.get('timeline_id')}: {old} → working copy"))
    return 0


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


def _visualize_working_copy(parsed: argparse.Namespace) -> dict[str, Any] | None:
    """The working copy visualize reads (path, base, edits, changed cuts), or None when it reads the published head."""
    from astrid.sdk.timeline_checkout import Checkout, find_draft

    if getattr(parsed, "published", False) or getattr(parsed, "revision_id", None) or getattr(parsed, "from_revision", None):
        return None
    ref = getattr(parsed, "timeline_slug", None) or getattr(parsed, "timeline_ref", None)
    try:
        path = find_draft(parsed.project, ref)
    except Exception as exc:  # the runtime is not reachable: say so, visualize the published head
        print(f"working copy: not checked ({exc}); visualizing the published head", file=sys.stderr)
        return None
    if path is None:
        return None
    checkout = Checkout.load(path)
    changes = list(checkout.edits().get("changes") or [])
    return {
        "draft": str(path),
        "base_revision": checkout.base_revision,
        "edits": len(changes),
        "changed_cuts": [int(n) for n in checkout.check().changed_cuts],
    }


def _changed_cuts_line(cuts: list[int], *, limit: int = 12) -> str:
    """One line: which cuts the default selection shows, paged explicitly (never silently truncated)."""
    if not cuts:
        return "the working copy changes no cut · --every-cut for all cuts"
    if len(cuts) <= limit:
        noun = "cut" if len(cuts) == 1 else "cuts"
        return f"showing the {len(cuts)} {noun} you changed ({', '.join(map(str, cuts))}) · --every-cut for all cuts"
    return (
        f"showing 1–{limit} of {len(cuts)} cuts you changed ({', '.join(map(str, cuts[:limit]))}) · "
        f"next: --cut {cuts[limit]} · --every-cut for all cuts"
    )


def _cmd_visualize(parsed: argparse.Namespace) -> int:
    """Run visualization through the public SDK and product output layer."""
    from astrid.sdk.contracts import DomainResult, ErrorObject
    from astrid.packs.rendering.executors.timeline_visualize.inspection_contract import inspection_options
    if getattr(parsed, "list_layers", False):
        from astrid.packs.rendering.executors.timeline_visualize.layers import layer_help

        print(layer_help())
        return 0
    if getattr(parsed, "preset", None) == "compare" or getattr(parsed, "view", None) == "diff":
        return _cmd_visualize_diff(parsed)
    try:
        at_note = _resolve_visualize_addresses(parsed)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if at_note:
        print(at_note)
    _resolve_view(parsed)
    human_outputs: Mapping[str, Any] | None = None

    # Normalize repeatable and comma-separated spellings before the one
    # canonical SDK call. The default auto route resolves to one exact
    # current composed output or captures the pinned composition; declared
    # inputs are an explicit input-only view.
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
    if getattr(parsed, "size", None) and not inputs.get("resolution"):
        inputs["resolution"] = parsed.size
    if inputs.get("view") == "contact" and not inputs.get("resolution"):
        # Review scale for the overview (the executor defaults the same;
        # sending it keeps an older host from capturing at full canvas).
        inputs["resolution"] = "480x270"
    if inputs.get("view") == "motion":
        inputs["preset"] = parsed.preset
        if getattr(parsed, "window_s", None) is not None:
            inputs["window_s"] = parsed.window_s
        # a short window shows EVERY frame (the motion preset samples every 2nd); say so in the skill
        if inputs.get("every") is None and inputs.get("every_frames") is None and parsed.preset in (None, "motion"):
            span = float(getattr(parsed, "window_s", None) or 1.0)
            if inputs.get("range"):
                lo, _, hi = str(inputs["range"]).partition("..")
                try:
                    span = float(hi) - float(lo)
                except ValueError:
                    pass
            if span <= 1.0 + 1e-6:
                inputs["every_frames"] = 1
    layers = [
        part.strip() for value in (getattr(parsed, "layers", None) or []) + (getattr(parsed, "overlay", None) or [])
        for part in str(value).split(",") if part.strip()
    ]
    if layers:
        inputs["layers"] = ",".join(dict.fromkeys(layers))
    if getattr(parsed, "cut", None) not in (None, ""):
        inputs["cut"] = str(parsed.cut)
    if getattr(parsed, "frame_budget", None) is not None:
        inputs["frame_budget"] = parsed.frame_budget
    if getattr(parsed, "highlight", None):
        inputs["highlight"] = parsed.highlight
    if getattr(parsed, "preview", False):
        inputs["preview"] = True
    if inputs.get("view") == "motion":
        from astrid.packs.rendering.executors.timeline_visualize.motion.rules import resolve_rules

        try:
            rules = resolve_rules(getattr(parsed, "rules", None), search=not getattr(parsed, "no_rules", False))
        except (OSError, ValueError) as exc:
            print(f"error validation_error: {exc}", file=sys.stderr)
            return 2
        if rules:
            inputs["rules"] = json.dumps(rules, separators=(",", ":"))
    if getattr(parsed, "beats", None):
        try:
            inputs["beats"] = _read_beats(parsed.beats)
        except (OSError, ValueError) as exc:
            print(f"error validation_error: --beats {parsed.beats}: {exc}", file=sys.stderr)
            return 2
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
    working = _visualize_working_copy(parsed)
    if working is not None:
        from astrid.core.timeline.authoring_bundle import AuthoringBundleError, preview_authoring_candidate
        from astrid.sdk.timeline_checkout import Checkout

        draft = Checkout.load(working["draft"])
        if getattr(parsed, "compare_with", None) == "published":
            # Before, then after: the published version and the working copy, same frames.
            parsed.compare_with = None
            parsed.published = True
            print("── PUBLISHED " + "─" * 60)
            code = _cmd_visualize(parsed)
            parsed.published = False
            print("── WORKING COPY " + "─" * 57)
            if code not in (0, None):
                return code
        changed_ids = draft.changed_cut_ids()
        banner = (
            f"WORKING COPY · {len(draft.changes())} unpublished change(s) vs published {_short_rev(working['base_revision'])} · "
            "--published for the live version"
        )
        selected = any(
            inputs.get(name) not in (None, "", [])
            for name in ("cut", "range", "at", "frame", "clip", "asset", "occurrence", "shot")
        )
        cut_line = None
        if not selected and not getattr(parsed, "all_cuts", False):
            if working["changed_cuts"]:
                inputs["cuts"] = ",".join(map(str, working["changed_cuts"][:12]))
                inputs["view"] = "contact"
                inputs.setdefault("resolution", "480x270")
            cut_line = _changed_cuts_line(working["changed_cuts"])
            if changed_ids:
                cut_line += f"  [✎ {', '.join(changed_ids)}]"
        elif getattr(parsed, "all_cuts", False):
            cut_line = "showing every cut of the working copy"
        else:
            window = inputs.get("at") or inputs.get("frame") or (str(inputs.get("range") or "").split("..")[0] or None)
            here = None
            try:
                if window not in (None, ""):
                    t = float(window) if not isinstance(window, (int, float)) else float(window)
                    here = next((g["id"] for g in draft._cut_groups()
                                 if g["start"] - 1e-6 <= t < (draft._cut_spans()[g["id"]][1] or draft.duration)), None)
            except (TypeError, ValueError):
                here = None
            if here and here in changed_ids:
                cut_line = f"✎ {here} is changed in the working copy (before/after: --compare published)"
            elif here:
                cut_line = f"{here} is unchanged in the working copy"
        if not parsed.json:
            print(banner)
            if cut_line:
                print(cut_line)
        try:
            inputs["authoring_preview"] = preview_authoring_candidate(draft.document())
        except AuthoringBundleError as exc:
            print(f"error validation_error: the working copy does not compile ({exc}); run timelines check", file=sys.stderr)
            return 2
        mode = "composed" if mode == "auto" else mode
    if getattr(parsed, "compare_with", None) and not parsed.json:
        print("no working copy: nothing to compare (showing the published head)")
    if getattr(parsed, "plan", False):
        return _visualize_plan(parsed, inputs)
    requested_at = time.time()
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
            outputs["working_copy"] = None  # the published head was read (see the working-copy gate above)
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
            outputs.setdefault("working_copy", None)
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
    if human_outputs is not None:
        timing = _visualization_timing(human_outputs, requested_at)
        if timing is not None:
            human_outputs["timing"] = timing
    if parsed.json or human_outputs is None or not result.ok:
        return print_result(envelope, as_json=parsed.json)
    print(_visualization_summary(human_outputs, parsed=parsed, inputs=inputs))
    return 0


# Flags that only mean something to the paired filmstrip (input lanes, pages).
_FILMSTRIP_ONLY = ("sample", "show", "hide", "track", "page_size", "include_media", "detail", "render_run",
                   "occurrence", "clip", "asset", "shot", "context", "neighbors", "include_cuts")


def _resolve_visualize_addresses(parsed: argparse.Namespace) -> str | None:
    """``--at``, ``--range`` and ``--highlight`` take the same addresses as every other verb
    (``'"Building" in v27'``, ``c30``, ``c41.mink``, ``93.5``), read from the working copy unless
    ``--published``. Ambiguity lists the choices; the line printed says which one was used."""
    from astrid.sdk.timeline_address import AddressError, resolve
    from astrid.sdk.timeline_checkout import Checkout, find_draft

    def needs(name: str, plain: str) -> bool:
        value = getattr(parsed, name, None)
        return isinstance(value, str) and bool(value.strip()) and not re.fullmatch(plain, value)

    if not (needs("at", r"\s*@?[\d.:]+s?\s*") or needs("range", r"\s*[\d.:]+\s*\.\.\s*[\d.:]+\s*")
            or needs("highlight", r"") or (needs("cut", r"\s*\d+\s*") and re.fullmatch(r"\s*c\d+[a-z]?\s*", str(parsed.cut).lower()))):
        return None  # plain seconds and cut numbers need no lookup
    ref = getattr(parsed, "timeline_slug", None) or getattr(parsed, "timeline_ref", None)
    tl = None
    if not getattr(parsed, "published", False) and not getattr(parsed, "revision_id", None):
        try:
            path = find_draft(parsed.project, ref, client=parsed.client)
        except Exception:  # noqa: BLE001 - fall back to the published head
            path = None
        tl = Checkout.load(path) if path is not None else None
    if tl is None:
        opener = getattr(parsed.client.timelines, "open_bundle", None)
        if not callable(opener):
            raise ValueError("an address needs a client that can open the timeline")
        opened = opener(parsed.project, ref, revision_id=getattr(parsed, "revision_id", None))
        if not opened.ok or not isinstance(opened.data, Mapping):
            raise ValueError(f"cannot open the timeline to resolve the address: {opened.error}")
        tl = Checkout(dict(opened.data["bundle"]))
    parsed._named = tl  # findings name layers by address (c30b.icon, not "ICON")
    notes = []
    try:
        if getattr(parsed, "highlight", None):
            target = resolve(tl, parsed.highlight, prefer="thing")
            if target.kind != "clip":
                raise ValueError(f"--highlight names a layer (c41.tool-16); {parsed.highlight!r} is a {target.kind}")
            parsed.highlight = target.clip.id
            notes.append(f"--highlight {target.address}")
            if not any(getattr(parsed, n, None) not in (None, "") for n in ("at", "range", "cut", "frame")):
                parsed.at = f"{min(target.end - 1 / tl.fps, target.start + 0.25):.3f}"
                notes.append(f"at {float(parsed.at):.2f} s (inside it)")
        cut = getattr(parsed, "cut", None)
        if isinstance(cut, str) and re.fullmatch(r"c\d+[a-z]?", cut.strip().lower()):
            from astrid.sdk.timeline_address import cut_ids_by_ordinal

            by_id = {cid: n for n, cid in cut_ids_by_ordinal(tl).items()}
            if cut.strip().lower() not in by_id:
                raise ValueError(f"no cut {cut}; cuts are {_id_span(list(tl._cut_spans()))}")
            parsed.cut = str(by_id[cut.strip().lower()])
            notes.append(f"--cut {cut.strip().lower()}")
        at = getattr(parsed, "at", None)
        if isinstance(at, str) and at.strip() and not re.fullmatch(r"\s*@?[\d.:]+s?\s*", at):
            target = resolve(tl, at, prefer="time")
            parsed.at = f"{target.start:.3f}"
            notes.append(f"--at {at!r}: {target.address} = {target.start:.2f} s")
        rng = getattr(parsed, "range", None)
        if isinstance(rng, str) and rng.strip() and not re.fullmatch(r"\s*[\d.:]+\s*\.\.\s*[\d.:]+\s*", rng):
            target = resolve(tl, rng, prefer="time")
            end = target.end if target.end > target.start else target.start + 1.0 / tl.fps
            parsed.range = f"{target.start:.3f}..{end:.3f}"
            notes.append(f"--range {rng!r}: {target.address} = {target.start:.2f}–{end:.2f} s")
    except AddressError as exc:
        raise ValueError(str(exc)) from None
    return " · ".join(notes) or None


def _id_span(ids: list[str]) -> str:
    """``c01…c42 (+ c30b)``: the valid cut ids in one short phrase."""
    plain = [c for c in ids if re.fullmatch(r"c\d+", c)]
    extra = [c for c in ids if c not in plain]
    head = f"{plain[0]}…{plain[-1]}" if len(plain) > 1 else ", ".join(plain)
    return head + (f" (+ {', '.join(extra)})" if extra else "")


def _resolve_word_at(parsed: argparse.Namespace) -> str | None:
    """``--at viral``: the first onset of that spoken word (or on-screen text) in seconds; returns a note."""
    value = getattr(parsed, "at", None)
    if value in (None, ""):
        return None
    try:
        float(str(value).replace(":", ""))
        return None
    except ValueError:
        pass
    opener = getattr(parsed.client.timelines, "open_bundle", None)
    if not callable(opener):
        raise ValueError("--at with a word needs a client that can open the timeline")
    opened = opener(parsed.project, parsed.timeline_slug or parsed.timeline_ref, revision_id=getattr(parsed, "revision_id", None))
    if not opened.ok or not isinstance(opened.data, Mapping):
        raise ValueError(f"cannot open the timeline to find {value!r}")
    from astrid.core.timeline.cuts import occurrences_from_bundle
    from astrid.packs.rendering.executors.timeline_visualize.motion import model

    wanted = str(value).strip().strip("\"'").lower()
    elements = model.elements_from_occurrences(occurrences_from_bundle(opened.data["bundle"]))
    for word in model.words(elements):
        if word.text.strip(".,!?;:\"'").lower() == wanted:
            parsed.at = f"{word.start:.3f}"
            return f'--at {value!r}: the word "{word.text}" at {word.start:.2f} s'
    import re as _re

    pattern = _re.compile(rf"\b{_re.escape(wanted)}\b", _re.IGNORECASE)
    fps = 30.0

    def keyed(value, start):
        """(time, text) of frame-keyed items (at/appearAt/atFrame/startAt) inside params."""
        if isinstance(value, Mapping):
            frame = next((value[k] for k in ("at", "appearAt", "atFrame", "startAt")
                          if isinstance(value.get(k), (int, float)) and not isinstance(value.get(k), bool)), None)
            texts = [v for v in value.values() if isinstance(v, str)]
            texts += [x for v in value.values() if isinstance(v, list) for x in v if isinstance(x, str)]
            if frame is not None:
                yield start + float(frame) / fps, " ".join(texts)
            for child in value.values():
                yield from keyed(child, start)
        elif isinstance(value, list):
            for child in value:
                yield from keyed(child, start)

    for element in sorted(elements, key=lambda e: e.start):
        texts = [str(element.params.get(key) or "") for key in ("text", "title", "body", "label", "name")]
        texts.append((element.asset or "").rsplit(":", 1)[-1])
        if any(pattern.search(text) for text in texts if text):
            parsed.at = f"{element.start:.3f}"
            return f"--at {value!r}: {element.label} enters at {element.start:.2f} s"
        for t, text in keyed(element.params, element.start):
            if pattern.search(text):
                parsed.at = f"{t:.3f}"
                return f"--at {value!r}: {element.label} shows {text[:40]!r} at {t:.2f} s"
    raise ValueError(f"no spoken word or on-screen element matches {value!r}; pass seconds instead")


def _resolve_view(parsed: argparse.Namespace) -> None:
    """Fill ``parsed.view``/``parsed.preset`` from what was asked: presets first, then the window given.

    No window → overview; ``--range`` → scan; ``--at``/``--frame`` → frame; ``--cut`` → cut;
    filmstrip-only flags (``--show``, ``--sample`` …) keep the paired filmstrip.
    """
    preset = getattr(parsed, "preset", None)
    if preset == "overview":
        parsed.view = "contact"
        return
    if preset:
        parsed.view = "motion"
        return
    if parsed.view == "motion" and not getattr(parsed, "cut", None):
        parsed.preset = "frame" if (parsed.at is not None or parsed.frame is not None) and not parsed.range else "scan"
        return
    if parsed.view == "motion":
        parsed.preset = "cut"
        return
    if parsed.view is not None:
        return
    if any(getattr(parsed, name, None) not in (None, "", [], False) for name in _FILMSTRIP_ONLY):
        parsed.view = "filmstrip"
    elif getattr(parsed, "cut", None):
        parsed.view, parsed.preset = "motion", "cut"
    elif parsed.range:
        parsed.view, parsed.preset = "motion", "scan"
    elif parsed.at is not None or parsed.frame is not None:
        parsed.view, parsed.preset = "motion", ("motion" if getattr(parsed, "window_s", None) else "frame")
    elif parsed.every is not None or parsed.every_frames is not None:
        parsed.view = "filmstrip"
    else:
        parsed.view = "contact"


def _cmd_visualize_diff(parsed: argparse.Namespace) -> int:
    """Before/after tiles for the cuts an edit changed, plus the SCOPE condition (client-side)."""
    from astrid.packs.rendering.executors.timeline_visualize.motion.diffview import (
        compare,
        compose_page,
        frames_by_cut,
    )

    old = getattr(parsed, "from_revision", None)
    if not old:
        print("error validation_error: --view diff needs --from <revision id> (timelines history / an edit's "
              ".publication.json old_head)", file=sys.stderr)
        return 2
    ref = parsed.timeline_slug or parsed.timeline_ref
    opener = getattr(parsed.client.timelines, "open_bundle", None)
    if not callable(opener):
        print("error unavailable: this client cannot open a timeline bundle", file=sys.stderr)
        return 1
    before = opener(parsed.project, ref, revision_id=old)
    after = opener(parsed.project, ref, revision_id=getattr(parsed, "to_revision", None))
    for opened in (before, after):
        if not opened.ok or not isinstance(opened.data, Mapping):
            return print_result(opened, as_json=parsed.json)
    new = str(after.data.get("revision_id"))
    try:
        edited = [int(n) for n in str(getattr(parsed, "edited", None) or "").split(",") if n.strip()]
    except ValueError:
        print("error validation_error: --edited takes cut numbers, e.g. 4,5", file=sys.stderr)
        return 2
    report = compare(before.data["bundle"], after.data["bundle"], edited=edited)
    page = None
    notes = []
    window_given = any(getattr(parsed, name, None) not in (None, "") for name in ("range", "at", "cut"))
    if getattr(parsed, "preset", None) == "compare" and window_given and not getattr(parsed, "no_capture", False):
        from astrid.packs.rendering.executors.timeline_visualize.motion.diffview import compose_compare, window_frames_of

        frames, roots = {}, {}
        options = {"view": "motion", "preset": "scan", "formats": ["png"]}
        for name in ("range", "at", "cut", "every", "every_frames"):
            if getattr(parsed, name, None) not in (None, ""):
                options[name] = getattr(parsed, name)
        for side, revision in (("before", old), ("after", new)):
            try:
                result = parsed.client.timelines.visualize(parsed.project, ref, mode="auto", revision_id=revision,
                                                           options={**options, "revision_id": revision})
                outputs = result.outputs if hasattr(result, "outputs") else (result.data or {})
                if not getattr(result, "ok", False) or not outputs.get("pack_root"):
                    raise RuntimeError(str(getattr(result, "error", None) or "no pack_root in the result"))
                roots[side] = Path(outputs["pack_root"])
                frames[side] = window_frames_of(roots[side])
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{side} frames not captured: {exc}")
        if len(frames) == 2:
            out = roots["after"].parent / f"compare-{old[-8:]}-{new[-8:]}.png"
            page = compose_compare(frames["before"], frames["after"], out,
                                   title=f"{ref} · {old[-12:]} (top) vs {new[-12:]} (bottom)")
    if page is None and (report["changed_after"] or report["changed_before"]) and not getattr(parsed, "no_capture", False):
        captures, roots = {}, {}
        for side, revision, numbers, opened in (("before", old, report["changed_before"], before),
                                                ("after", new, report["changed_after"], after)):
            if not numbers:
                continue
            try:
                result = parsed.client.timelines.visualize(
                    parsed.project, ref, mode="auto", revision_id=revision,
                    options={"view": "contact", "cuts": ",".join(map(str, numbers[:12])), "resolution": "480x270",
                             "formats": ["png"], "revision_id": revision},
                )
                outputs = result.outputs if hasattr(result, "outputs") else (result.data or {})
                if not getattr(result, "ok", False) or not isinstance(outputs, Mapping) or not outputs.get("pack_root"):
                    raise RuntimeError(str(getattr(result, "error", None) or "no pack_root in the result"))
                roots[side] = Path(outputs["pack_root"])
                captures[side] = frames_by_cut(roots[side], opened.data["bundle"])
            except Exception as exc:  # noqa: BLE001 - the data diff stands without pictures
                notes.append(f"{side} tiles not captured: {exc}")
        if captures:
            out = (roots.get("after") or roots["before"]).parent / f"diff-{old[-8:]}-{new[-8:]}.png"
            page = compose_page(report, captures.get("before", {}), captures.get("after", {}), out,
                                title=f"{ref} · {old[-12:]} → {new[-12:]} · changed cuts")
    if parsed.json:
        print(json.dumps({"ok": True, "data": {"from": old, "to": new, "page": str(page) if page else None,
                                                "findings": [f.as_dict() for f in report["findings"]],
                                                "notes": notes}, "error": None}, indent=2))
        return 0
    lines = [f"diff page: {page}" if page else "diff page: (none: no changed cuts, --no-capture, or capture failed)",
             f"  {report['before_cuts']} → {report['after_cuts']} cuts · changed {len(report['changed_after'])}, "
             f"removed {sum(1 for r in report['rows'] if r['status'] == 'removed')}"]
    lines += [f"  {note}" for note in notes]
    lines += [f.line() for f in report["findings"]]
    scope = [f for f in report["findings"] if f.code == "SCOPE"]
    lines.append("SCOPE ok: nothing outside --edited changed" if edited and not scope else
                 ("" if edited else "tip: --edited 4,5 turns any change outside those cuts into a SCOPE warning"))
    print("\n".join(line for line in lines if line))
    return 0


def _read_beats(path: str) -> str:
    """A cue's beats.json reduced to what the sync layer and lint read (cue seconds)."""
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {"beats": data}
    if not isinstance(data, Mapping):
        raise ValueError("expected a beats.json object with beats/downbeats/hits")
    compact = {
        "beats": [round(float(t), 4) for t in data.get("beats") or [] if isinstance(t, (int, float))],
        "downbeats": [round(float(t), 4) for t in data.get("downbeats") or [] if isinstance(t, (int, float))],
        "hits": [
            {"t": round(float(hit["t"]), 4), "kind": str(hit.get("kind") or "hit")}
            for hit in data.get("hits") or [] if isinstance(hit, Mapping) and isinstance(hit.get("t"), (int, float))
        ],
    }
    return json.dumps(compact, separators=(",", ":"))


def _seconds(value: Any) -> str:
    return f"{float(value):.0f} s" if isinstance(value, (int, float)) and not isinstance(value, bool) else "?"


def _visualization_summary(outputs: Mapping[str, Any], *, parsed: argparse.Namespace, inputs: Mapping[str, Any]) -> str:
    """The human result: where the picture is, what it holds, how long it took, what to run next."""
    view = str(inputs.get("view") or "filmstrip")
    pages = [str(page) for page in outputs.get("pages") or [] if page]
    primary = outputs.get("primary_page") or (pages[0] if pages else None) or outputs.get("png")
    timing = outputs.get("timing") if isinstance(outputs.get("timing"), Mapping) else {}
    label = {"contact": "contact sheet", "motion": "motion sheet" if inputs.get("preset") == "cut" else
             f"{inputs.get('preset')} view", "filmstrip": "filmstrip"}.get(view, view)
    lines = [f"{label}: {primary or '(no page produced; see --json)'}"]
    for page in pages:
        if page != primary:
            lines.append(f"  also: {page}")
    if outputs.get("preview"):
        lines.append(f"  preview for humans (animated, sampled frames at real timing): {outputs['preview']}")
    frames = timing.get("frames")
    resolution = timing.get("resolution") or inputs.get("resolution")
    if isinstance(resolution, (list, tuple)) and len(resolution) == 2:
        resolution = f"{resolution[0]}x{resolution[1]}"
    tiles = timing.get("tiles") or frames
    window_info = outputs.get("window") if isinstance(outputs.get("window"), Mapping) else {}
    preset = inputs.get("preset")
    what = {
        "contact": f"{tiles} tiles, one per picture cut (#N = cut number in timelines show)",
        "motion": (f"cut {inputs.get('cut')}: {frames} frames, dense after each entrance" if preset == "cut" else
                   f"{preset} {float(window_info.get('start', 0)):.2f}–{float(window_info.get('end', 0)):.2f} s: "
                   f"{frames} frames on one page"),
    }.get(view, f"{frames} frames")
    lines.append(f"  {what}" + (f" · frames {resolution}" if resolution else ""))
    if timing.get("capture_s") is not None:
        lines.append(
            f"  wall {_seconds(timing.get('wall_s'))} = queued {_seconds(timing.get('queued_s'))}"
            f" + capture {_seconds(timing.get('capture_s'))} + compose {_seconds(timing.get('compose_s'))}"
        )
    else:
        lines.append(f"  wall {_seconds(timing.get('wall_s'))} (executor timing unavailable: the host predates timing.json)")
    findings = [str(line) for line in outputs.get("findings") or []]
    named = getattr(parsed, "_named", None)
    if findings and named is not None:
        from astrid.sdk.timeline_address import name_things

        lo = float(window_info.get("start", 0) or 0)
        hi = float(window_info.get("end", 0) or named.duration)
        from astrid.sdk.timeline_address import ordinals_to_ids

        findings = [ordinals_to_ids(name_things(line, named, lo, hi), named) for line in findings]
    if findings:
        lines.append("findings:")
        lines.extend(f"  {line}" for line in findings[:14])
        if len(findings) > 14:
            lines.append(f"  … {len(findings) - 14} more in findings.txt next to the page")
    project = str(parsed.project or "<project>")
    timeline = str(inputs.get("timeline_slug") or "<timeline>")
    base = ["python3", "-m", "astrid", "timelines", "visualize", timeline, "--project", project]
    if inputs.get("revision_id"):
        base += ["--revision-id", str(inputs["revision_id"])]
    lint = ["python3", "-m", "astrid", "timelines", "lint", timeline, "--project", project]
    show = ["python3", "-m", "astrid", "timelines", "show", timeline, "--project", project]
    beats = ["--beats", str(parsed.beats)] if getattr(parsed, "beats", None) else []
    window = outputs.get("window") if isinstance(outputs.get("window"), Mapping) else None
    if view == "motion" and window and inputs.get("preset") != "cut":
        nexts = _window_navigation(window, base)
    elif view == "motion":
        cut = str(inputs.get("cut") or "N")
        following = str(int(cut) + 1) if cut.isdigit() else "N"
        nexts = [
            ("next cut", base + ["--cut", following] + beats),
            ("lint the whole edit", lint + beats),
            ("whole film again", base + ["--preset", "overview"]),
        ]
        if window and window.get("busiest") is not None:
            t = float(window["busiest"])
            nexts.insert(1, (f"biggest frames at the busiest moment ({t:.2f} s)",
                             base + ["--preset", "motion", "--range", f"{max(0.0, t - 1.5):.2f}..{t + 1.5:.2f}"]))
    else:
        nexts = [
            ("motion of one cut", base + ["--view", "motion", "--cut", "N"] + beats),
            ("lint composition + timing", lint + beats),
            ("cut table (numbers, words)", show),
        ]
    lines.append("next:")
    width = max(len(name) for name, _argv in nexts)
    lines.extend(f"  {name:<{width}}  {shlex.join(argv)}" for name, argv in nexts)
    lines.append("(--json prints the full SDK envelope; --list-layers lists the views you can add with --layer)")
    return "\n".join(lines)


def _visualize_plan(parsed: argparse.Namespace, inputs: Mapping[str, Any]) -> int:
    """``--plan``: resolve the view, window and frames without capturing (reads the timeline only)."""
    import math

    from astrid.packs.rendering.executors.timeline_visualize.filmstrip_cards import plan_filmstrip
    from astrid.packs.rendering.executors.timeline_visualize.filmstrip_options import filmstrip_options
    from astrid.packs.rendering.executors.timeline_visualize.motion.window import fit_columns, preset_settings
    from astrid.sdk.timeline_filmstrip import prepare_filmstrip

    values = dict(inputs)
    values["composed_capture"] = True
    try:
        authority = prepare_filmstrip({**values, "view": "filmstrip"}, project=parsed.project, client=parsed.client)
        options = filmstrip_options(values)
        index = plan_filmstrip(authority["capture_snapshot"], options)
    except Exception as exc:  # noqa: BLE001 - a plan reports what would fail
        print(f"plan: would fail: {exc}")
        return 2
    frames = len(index["cards"])
    view = options["view"]
    lines = [f"plan: view {view}" + (f", preset {options.get('preset')}" if view == "motion" else "")]
    if view == "motion":
        motion = index.get("motion") or {}
        window = motion.get("window") or [0, 0]
        settings = preset_settings(options.get("preset"))
        size = options.get("resolution") or list(settings["size"])
        columns, shown = fit_columns(frames, int(options.get("columns") or settings["columns"]),
                                     int(size[0]) if options.get("size_explicit") else int(settings["tile_w"]),
                                     int(size[1]))
        lines.append(f"  window {float(window[0]):.2f}–{float(window[1]):.2f} s · cuts {motion.get('cuts_in')}")
        lines.append(f"  {frames} frames captured at {size[0]}x{size[1]}, shown {shown} px wide, {columns} per row, "
                     f"{math.ceil(frames / columns)} rows on ONE page")
    elif view == "contact":
        lines.append(f"  {frames} tiles at {options['resolution'][0]}x{options['resolution'][1]} on one page")
    else:
        lines.append(f"  {frames} frames on {math.ceil(frames / max(1, int(options.get('page_size') or 50)))} page(s)")
    lines.append(f"  expect ~{max(8, round(5 + frames * 0.3))} s of capture when the host is free (frames are cached "
                 "after the first run)")
    lines.append("  run it: the same command without --plan (the run prints next: earlier/later/zoom/other preset)")
    print("\n".join(lines))
    return 0


def _window_navigation(window: Mapping[str, Any], base: list[str]) -> list[tuple[str, list[str]]]:
    """Neighbour windows as copyable commands: earlier, later, zoom in/out, another preset, the cut."""
    preset = str(window.get("preset") or "scan")

    def view(name: str, low: float, high: float) -> list[str]:
        if name == "motion" and high - low > 3.0:
            name = "scan"
        return base + ["--preset", name, "--range", f"{low:.2f}..{high:.2f}"]

    rows: list[tuple[str, list[str]]] = []
    start, end = float(window["start"]), float(window["end"])
    if window.get("earlier"):
        a, b = window["earlier"]
        rows.append((f"earlier {a:.2f}–{b:.2f} s", view(preset, a, b)))
    if window.get("later"):
        a, b = window["later"]
        rows.append((f"later {a:.2f}–{b:.2f} s", view(preset, a, b)))
    if window.get("zoom_in"):
        a, b = window["zoom_in"]
        where = f"the busiest moment {window['busiest']:.2f} s" if window.get("busiest") is not None else "the middle"
        rows.append((f"zoom in on {where}", view("motion" if b - a <= 3.0 else preset, a, b)))
    if window.get("zoom_out") and preset != "frame":
        a, b = window["zoom_out"]
        rows.append(("zoom out", view("scan", a, b)))
    other = {"scan": "beat", "beat": "scan", "motion": "beat", "frame": "motion"}.get(preset, "scan")
    if other == "motion" and preset == "frame":
        rows.append(("this moment moving (motion, 3 s)", base + ["--preset", "motion", "--at", f"{start:.2f}"]))
    else:
        rows.append((f"same window as {other}", view(other, start, end)))
    if window.get("cut") is not None:
        rows.append((f"the cut it is in (#{window['cut']})", base + ["--cut", str(window["cut"])]))
    return rows


def _visualization_timing(outputs: Mapping[str, Any], requested_at: float) -> dict[str, Any] | None:
    """Add queue time to the executor's timing: queued = executor start - request time.

    The executor cannot see when its request was admitted, so the client, which
    timed the request itself, computes the queue.  ``wall_s`` is the full
    client-observed duration.
    """
    raw = outputs.get("timing")
    timing = dict(raw) if isinstance(raw, Mapping) else {}
    started = timing.get("started_at")
    timing["queued_s"] = (
        round(max(0.0, float(started) - requested_at), 3)
        if isinstance(started, (int, float)) and not isinstance(started, bool) else None
    )
    timing["wall_s"] = round(time.time() - requested_at, 3)
    return timing


def _print_visualization_timing(timing: Any, requested_at: float) -> None:
    """One human line: how many frames were captured, how long, and how long it queued."""
    if not isinstance(timing, Mapping) or timing.get("capture_s") is None:
        print(f"visualized in {time.time() - requested_at:.0f} s (executor timing unavailable)")
        return
    queued = timing.get("queued_s")
    queue_text = f" (queued {float(queued):.0f} s)" if queued is not None else ""
    print(f"captured {int(timing.get('frames') or 0)} frames in {float(timing['capture_s']):.0f} s{queue_text}")


def _visualization_navigation_help(
    *, project: str | None, inputs: Mapping[str, Any], outputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose copyable zoom/sampling/navigation guidance in CLI results."""
    resolved_project, resolved_timeline = _navigation_scope(
        project=project, timeline=inputs.get("timeline_slug"), outputs=outputs,
    )
    identity = [
        "python3", "-m", "astrid", "timelines", "visualize",
        "--project", resolved_project,
    ]
    timeline = resolved_timeline
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
    output_components = outputs.get("components")
    if not isinstance(output_components, (list, tuple)):
        surface = outputs.get("static_surface")
        output_components = surface.get("components") if isinstance(surface, Mapping) else None
    effective_components = tokens(output_components)
    paired = (
        "output" in effective_components and "inputs" in effective_components
    ) or (
        not inputs.get("show") and not inputs.get("hide")
    )
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
    show_command = shlex.join(
        [
            "python3", "-m", "astrid", "timelines", "show",
            "--project", resolved_project,
            *([str(timeline)] if timeline not in (None, "") else []),
            *(["--revision-id", str(revision_id)] if revision_id not in (None, "") else []),
            *sum(show_selectors, []),
            *sum((["--track", track] for track in tokens(inputs.get("track"))), []),
        ]
    )
    return {
        "primary_page": primary_page,
        "pages": pages,
        "markdown": outputs.get("markdown"),
        "inspection": shlex.join(inspect_base + ["--section", "summary"]),
        "editing": _editing_navigation(project=resolved_project, timeline=timeline),
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
            "visualize": shlex.join(base()),
            "overview": shlex.join(identity + ["--view", "contact", "--sample", "cuts"]),
            "show": show_command,
            "controls": "python3 -m astrid timelines visualize --help",
            "zoom": shlex.join(clean + ["--range", "START..END", "--every", "0.25", *zoom_detail]),
            "interval_seconds": shlex.join(clean + ["--every", "1"]),
            "interval_frames": shlex.join(clean + ["--every-frames", "12"]),
            "exact_frame": shlex.join(clean + ["--frame", "FRAME"]),
            "resolution": shlex.join(base(resolution=False) + ["--resolution", "960x540"]),
            "inputs_only": shlex.join(input_only),
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
    footer: list[str] = []
    # The current command is already visible above. Keep the human footer to
    # the sibling view, controls, and editing entry points; JSON retains the
    # full rerun/exact-frame command catalog.
    _append_navigation_footer(footer, navigation, include_visualize=False)
    for line in footer:
        print(f"  {line}")


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
    working = None
    draft = getattr(parsed, "draft", None)
    if draft is not None:
        # --draft: the working copy is the candidate. Admitted through the managed renderer as an
        # unpublished authoring preview (nothing is published, the published head is untouched).
        from astrid.sdk.authoring_render_preview import render_authoring_candidate_preview
        from astrid.sdk.timeline_checkout import Checkout, find_draft

        if not parsed.ref:
            return print_result(
                DomainResult.failure(ErrorObject("validation_error", "render --draft needs the timeline ref", {})),
                as_json=parsed.json,
            )
        try:
            path = find_draft(parsed.project, parsed.ref, draft or "main", client=parsed.client)
        except Exception as exc:  # the runtime is not reachable
            return print_result(
                DomainResult.failure(ErrorObject("unavailable", f"working copy not checked: {exc}", {})),
                as_json=parsed.json,
            )
        if path is None:
            return print_result(
                DomainResult.failure(ErrorObject(
                    "not_found", f"no working copy for {parsed.ref}: run `timelines checkout {parsed.ref}` first", {},
                )),
                as_json=parsed.json,
            )
        checkout = Checkout.load(path)
        working = {
            "draft": str(path),
            "base_revision": checkout.base_revision,
            "edits": len(list(checkout.edits().get("changes") or [])),
        }
        result = render_authoring_candidate_preview(
            checkout.document(), parsed.client, project=parsed.project, timeline_ref=parsed.ref,
            wait=parsed.wait, **{key: value for key, value in inputs.items() if key != "timeline_ref"},
        )
    else:
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
                "working_copy": working,
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
    if working is not None:
        print(
            f"WORKING COPY · {working['edits']} unpublished edits vs published {working['base_revision']} · "
            "rendering the draft, not the published head"
        )
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


def _configure_create(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "timeline_id",
        nargs="?",
        default=None,
        help="New timeline id (ULID). Omit to generate one; the id is printed "
        "in the result and is what show/checkout/render accept.",
    )
    _add_project_arg(subparser)
    subparser.add_argument(
        "--slug",
        default=None,
        help="Slug for the new timeline (passed as timeline_id); an alternative to the positional id.",
    )
    subparser.add_argument(
        "--canvas",
        type=_parse_canvas,
        default=(1920, 1080),
        help="Canvas size WIDTHxHEIGHT (default 1920x1080).",
    )
    subparser.add_argument(
        "--fps",
        type=_positive_int,
        default=30,
        help="Canvas frame rate (default 30).",
    )
    _add_idempotency_key(subparser)
    _add_json_flag(subparser)
    subparser.set_defaults(handler=_cmd_create)


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
    subparser.description = (
        "Default: a cut table, one row per picture cut: timeline seconds and timecode, the shot "
        "(chapter) name, the picture clip, the layers over it and the words spoken under it. "
        "Filter with --range START..END (seconds) or --shot <id|name|ordinal>; --revision-id shows a saved revision."
    )
    subparser.epilog = (
        "Next: use `timelines visualize` for composed pixels. For edits, use the "
        "timeline_editing document-checkout recipe (checkout → edit → check → publish). "
        "Use `timelines visualize --help` for the visual controls."
    )
    _add_project_arg(subparser, required=False)
    subparser.add_argument(
        "ref", nargs="?", default=None,
        help="Optional timeline UUID, ULID, or slug; omit to use the project's default timeline.",
    )
    subparser.add_argument(
        "address", nargs="?", default=None,
        help='One thing\'s complete record: a layer (c41.mink), a cut (c30), a word ("Building" in v27), a time, '
        "an asset key or a layer name. Every param untruncated, the element's allowed keys, moments in seconds.",
    )
    subparser.add_argument(
        "--summary",
        action="store_true",
        help="One line per chapter (name, span, cut count, first words); with a working copy, its edits too.",
    )
    subparser.add_argument("--page", type=int, default=None, help="Cut table page N (1-based); with no --page-size, 20 cuts a page.")
    subparser.add_argument("--page-size", type=int, default=None, help="Cuts per page (default: all cuts).")
    subparser.add_argument("--at", default=None, help="The one cut on screen at SECONDS or m:ss: its clips, the words around it, next cuts.")
    subparser.add_argument("--clips", action="store_true", default=False, help="With --shot N: every clip in that chapter, one line each.")
    subparser.add_argument(
        "--published", action="store_true", default=False,
        help="Read the published head, ignoring the unpublished working copy (when one exists).",
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
    subparser.add_argument(
        "--layers", action="store_true", default=False,
        help="Print per-track layer rows (the bounded, paged inspection projection) instead of the cut table.",
    )
    subparser.add_argument(
        "--as", dest="as_view", choices=("cuts", "script", "code", "sheet"), default="cuts",
        help="cuts (default): one row per cut. sheet: the cut sheet, an A/V script you can edit and "
        "`timelines apply` (with --range \"word\"..\"word\" or A..B seconds or c30..c31). script: the words in "
        "time order with cuts, layer entrances and element keyframes between them. code: shots and cuts as a "
        "readable program of element calls with resolved keyframes and each element's source path.",
    )
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
    subparser.description = (
        "Compare two saved revisions as an editor would: which clips moved or changed, which cut points "
        "moved (timeline seconds), and the visualize commands that capture only the changed window, "
        "before and after."
    )
    _add_project_arg(subparser)
    subparser.add_argument("ref", help="Timeline UUID, ULID, or slug.")
    subparser.add_argument(
        "--from", dest="from_revision", default=None,
        help="Revision id to compare from (e.g. old_head in an edit's .publication.json).",
    )
    subparser.add_argument(
        "--to", dest="to_revision", default=None,
        help="Revision id to compare to (default: the current head).",
    )
    subparser.add_argument(
        "--as", dest="as_view", choices=("cuts", "script", "code"), default="cuts",
        help="cuts (default): changed clips and moved cut points. script/code: a unified diff of that view.",
    )
    subparser.add_argument("--from-version", type=int, default=None, help=argparse.SUPPRESS)
    subparser.add_argument("--to-version", type=int, default=None, help=argparse.SUPPRESS)
    _add_json_flag(subparser, default=False)
    subparser.add_argument("--published", action="store_true",
                           help="Diff saved revisions only, ignoring the working copy (no --from/--to: the head vs the working copy).")
    subparser.set_defaults(handler=_cmd_diff)


def _configure_lint(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Run every registered check (built-in and pack-provided) over every picture cut, from timeline data "
        "alone (about a second, no capture): faces covered, text off-frame/outside title-safe/too small, layers "
        "covering each other, accents off their word, sfx off their visual, short/long cuts, holds, presenter "
        "share, VO level jumps. Thresholds come from astrid-lint.toml (or --rules). Each line names the fix; "
        "--json carries machine-applicable fixes."
    )
    _add_project_arg(subparser, required=False)
    subparser.add_argument("ref", nargs="?", default=None, help="Timeline UUID, ULID, or slug (default: the project's).")
    subparser.add_argument("--cut", default=None, help="Only this cut (number in timelines show, clip id, or @SECONDS).")
    subparser.add_argument("--range", dest="range", default=None, help="Only cuts overlapping START..END seconds.")
    subparser.add_argument("--revision-id", default=None, help="Lint a saved revision instead of the head.")
    subparser.add_argument("--beats", default=None, metavar="BEATS_JSON",
                           help="Override the beats the music clips carry (app.beats, written by the EDL builder) "
                                "with a cue's beats.json. Beats add accents 0.10–0.20 s off a music hit or downbeat.")
    subparser.add_argument("--min-text-px", dest="min_text_px", type=float, default=None,
                           help="Smallest acceptable text size in px at 1080p (default 32, or the rules file's).")
    subparser.add_argument("--rules", default=None, metavar="FILE",
                           help="Project rules (thresholds/severities) TOML; default: the nearest astrid-lint.toml "
                                "from the working directory upwards.")
    subparser.add_argument("--no-rules", dest="no_rules", action="store_true",
                           help="Ignore any astrid-lint.toml and use the checks' defaults.")
    subparser.add_argument("--list-checks", dest="list_checks", action="store_true",
                           help="List every registered check (built-in and pack), its threshold keys and defaults.")
    subparser.add_argument("--strict", action="store_true", help="Exit 1 when any finding has severity error.")
    subparser.add_argument("--all", action="store_true", help="Also print info lines (EDGE crops, BEAT near-misses).")
    _add_json_flag(subparser, default=False)
    subparser.add_argument("--published", action="store_true",
                           help="Lint the published head, not the working copy (when one exists).")
    subparser.set_defaults(handler=_cmd_lint)


def _configure_visualize(subparser: argparse.ArgumentParser) -> None:
    subparser.epilog = (
        "Next: use `timelines show` for authored rows and timing. For edits, use the "
        "timeline_editing document-checkout recipe (checkout → edit → check → publish). "
        "Use this help page for component, sampling, frame, and resolution controls."
    )
    _add_project_arg(subparser, required=False)
    subparser.add_argument(
        "timeline_ref", nargs="?", default=None,
        help="The timeline (slug, UUID or ULID), as for the other timelines verbs; --timeline-slug also works.",
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
    subparser.add_argument("--at", default=None,
                           help="Any address: seconds (7.2), a word ('\"Building\" in v27', viral), a cut (c30), a layer "
                                "(c41.mink). Ambiguous words list their scoped forms. Alone: one frame; with --preset "
                                "motion: 1 s around it, every frame.")
    subparser.add_argument("--frame", type=int, default=None, help="Capture one exact rendered frame number.")
    subparser.add_argument("--revision-id", default=None, help="Capture/inspect an exact immutable timeline revision.")
    subparser.add_argument(
        "--published", action="store_true", default=False,
        help="Visualize the published head, ignoring the unpublished working copy (when one exists).",
    )
    subparser.add_argument(
        "--every-cut", dest="all_cuts", action="store_true", default=False,
        help="With a working copy and no cut/range selection: every cut, not only the cuts you changed.",
    )
    subparser.add_argument(
        "--compare", dest="compare_with", choices=("published",), default=None,
        help="With a working copy: the published version and the working copy for the same cuts, labelled.",
    )
    subparser.add_argument("--clip", default=None, help="Focus an authored clip id.")
    subparser.add_argument("--highlight", default=None, metavar="ADDRESS",
                           help="Outline one layer over the frames (c41.tool-16; implies --layer bounds); "
                                "with no window, the frames are taken inside it.")
    subparser.add_argument("--occurrence", default=None, help="Focus an exact authored shot occurrence id.")
    subparser.add_argument("--asset", default=None, help="Focus a canonical asset key.")
    subparser.add_argument(
        "--show", action="append", default=None, metavar="COMPONENT[,COMPONENT...]",
        help=(
            "Add synchronized components: inputs, output, text, or audio "
            "(default composed view: output with synchronized inputs, text, and audio; "
            "use --hide inputs for output-only)."
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
        "--preset",
        choices=("overview", "scan", "motion", "beat", "frame", "cut", "compare"),
        default=None,
        help=(
            "What you want to see, one page each: overview (whole film, one tile per cut); scan (a --range at "
            "2 fps, 8 per row, up to 30 s); motion (<= 3 s at every 2nd frame, big 640x360 frames, 3 per row, onion "
            "skins + curves); beat (a frame at each word onset, music hit and sfx, labelled); frame (one exact "
            "frame, 1280x720, with --at or --frame); cut (a cut's motion sheet, with --cut); compare (--from REV: "
            "before/after of what changed). Defaults: no window → overview, --range → scan, --at → frame, --cut → cut."
        ),
    )
    subparser.add_argument(
        "--view",
        choices=("filmstrip", "contact", "motion", "diff"),
        default=None,
        help=(
            "Usually leave this out and use --preset (or just give a window). filmstrip: Rendered paired filmstrip "
            "with input lanes (the only view for drill-down pages; chosen when --show/--sample/--track is used). "
            "contact: ONE overview page of the whole video, one tile per picture cut (capped at 120), 480x270. "
            "motion: one cut (--cut N) as a motion sheet you can judge as stills: frame strip, onion skins, "
            "motion curves, word/beat/sfx sync, stillness, lip-sync and findings (<= 60 frames at 480x270). "
            "diff (--from REV): before/after tiles of only the cuts an edit changed, and the SCOPE condition."
        ),
    )
    subparser.add_argument(
        "--cut", default=None,
        help="motion view: the cut, as its number in `timelines show`, a picture clip id, or @SECONDS.",
    )
    subparser.add_argument(
        "--layer", dest="layers", action="append", default=None, metavar="LAYER[,LAYER...]",
        help="Visualize layers to add (repeatable). Default: every default layer of the view. "
             "See --list-layers (e.g. --layer bounds on contact tiles).",
    )
    subparser.add_argument(
        "--overlay", action="append", default=None, metavar="LAYER",
        help="Alias of --layer for frame overlays (bounds: element bounds, face box, safe areas).",
    )
    subparser.add_argument(
        "--beats", default=None, metavar="BEATS_JSON",
        help="Override the beats the music clips carry (app.beats) with a cue's beats.json "
             "(beats/downbeats/hits in cue seconds).",
    )
    subparser.add_argument(
        "--frame-budget", dest="frame_budget", type=int, default=None,
        help="motion view: maximum captured frames (default 60, at most 120).",
    )
    subparser.add_argument(
        "--preview", action="store_true", default=None,
        help="motion view: also write an animated GIF of the cut (<= 480x270, the captured frames at their real "
             "timing) for people; agents read the sheets.",
    )
    subparser.add_argument("--from", dest="from_revision", default=None,
                           help="diff view: the revision before the edit (e.g. an edit's .publication.json old_head).")
    subparser.add_argument("--to", dest="to_revision", default=None, help="diff view: the revision after (default: head).")
    subparser.add_argument("--edited", default=None, metavar="N[,M]",
                           help="diff view: the cuts you meant to edit; a change anywhere else is a SCOPE warning.")
    subparser.add_argument("--no-capture", dest="no_capture", action="store_true",
                           help="diff view: compare the documents only (no before/after tiles).")
    subparser.add_argument("--rules", default=None, metavar="FILE",
                           help="motion view: project rules TOML (default: the nearest astrid-lint.toml).")
    subparser.add_argument("--no-rules", dest="no_rules", action="store_true",
                           help="motion view: ignore astrid-lint.toml.")
    subparser.add_argument(
        "--plan", action="store_true",
        help="Resolve the view, window, frame count and page layout without capturing (about a second).",
    )
    subparser.add_argument(
        "--list-layers", dest="list_layers", action="store_true",
        help="List the registered visualize layers (built-in and pack-contributed) and exit.",
    )
    subparser.add_argument("--sample", choices=("interval", "clips", "cuts", "shots"), default=None,
                           help="Filmstrip sampling: interval (default), picture clips, cut boundaries, or authored story beats.")
    sampling = subparser.add_mutually_exclusive_group()
    sampling.add_argument("--every", type=float, default=None,
                          help="Sample every N seconds (overrides the preset: scan 0.5 s; filmstrip 0.5 s).")
    sampling.add_argument("--every-frames", type=int, default=None,
                          help="Sample every N frames (overrides the preset: motion 2); replaces --every.")
    subparser.add_argument(
        "--include-cuts", action="store_true", default=None,
        help="With interval sampling, also capture visual cut-neighbor frames.",
    )
    subparser.add_argument(
        "--render-run", default=None,
        help=(
            "Explicit successful render run, or latest (filmstrip default). "
            "An explicit run may be historical/candidate; auto reuse checks the current timeline head."
        ),
    )
    subparser.add_argument("--columns", type=int, default=None,
                           help="Frames per row: window presets 1–16 (scan 8, beat 6, motion 3; the page shrinks "
                                "tiles to stay one page); contact 1–12 (default 10); filmstrip 1–8 (default 5).")
    subparser.add_argument("--page-size", type=int, default=None,
                           help="Filmstrip cards per static page (default: 50 standalone; paired pages use one row, or explicitly opt into up to two rows / 10 cards).")
    subparser.add_argument("--size", default=None, metavar="WIDTHxHEIGHT",
                           help="Frame size for the window presets, e.g. 960x540 (scan/beat 480x270, motion 640x360, "
                                "frame 1280x720 by default). The page stays ONE page, so many frames shrink: for the "
                                "biggest frames show fewer (a shorter --window/--range, or larger --every-frames).")
    subparser.add_argument("--window", dest="window_s", type=float, default=None, metavar="SECONDS",
                           help="With --at: the window width centred on it (motion 3 s, scan 10 s, beat 6 s).")
    subparser.add_argument("--resolution", default=None, metavar="WIDTHxHEIGHT",
                           help="Frame resolution, e.g. 960x540 (default: 480x270 for contact and motion, "
                                "the canvas for filmstrip); applied exactly by the executor.")
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
    # Human summary by default (page path, counts, timing, next commands),
    # like show/diff; --json opts into the SDK envelope.
    _add_json_flag(subparser, default=False)
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
        "--draft", nargs="?", const="main", default=None, metavar="NAME",
        help='Render the working copy (unpublished draft) instead of the published head; NAME defaults to "main".',
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


# -- timelines working copy: checkout / edit / words / status / check / publish / discard ----------
# One SDK call per verb; the logic is in astrid.sdk.timeline_checkout (drafts) and timeline_duplicate.


class _VerbError(Exception):
    """A user-facing failure: the message is printed to stderr and the verb exits with ``code``."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def _guard(default_code: int):
    """Turn a _VerbError or a TimelineEditError into one stderr line and an exit code."""

    def wrap(fn):
        def run(parsed: argparse.Namespace) -> int:
            from astrid.sdk.timeline_checkout import TimelineEditError

            try:
                return fn(parsed)
            except _VerbError as exc:
                print(str(exc), file=sys.stderr)
                return exc.code
            except TimelineEditError as exc:
                print(str(exc), file=sys.stderr)
                return default_code

        run.__name__ = fn.__name__
        return run

    return wrap


def _is_file_ref(ref: str | None) -> bool:
    return bool(ref) and (str(ref).endswith(".json") or Path(str(ref)).is_file())


def _draft_name(parsed: argparse.Namespace) -> str:
    return getattr(parsed, "draft", None) or "main"


def _need_project(parsed: argparse.Namespace) -> str:
    if not parsed.project:
        raise _VerbError("--project P is required (the timeline is named inside its project)", 2)
    return parsed.project


def _cuts_summary(tl: Any) -> str:
    return f"{len(tl.cuts)} cuts · {len(tl.clips())} clips · {len(tl.words())} words"


def _working_copy(parsed: argparse.Namespace, *, create: bool) -> tuple[Any, bool]:
    """(checkout, existed). Create=True makes the working copy from the head when there is none."""
    from astrid.sdk.timeline_checkout import Checkout, find_draft

    project = _need_project(parsed)
    timeline = parsed.timeline
    name = _draft_name(parsed)
    existed = find_draft(project, timeline, name, client=parsed.client) is not None
    if not existed and not create:
        return None, False
    fresh = bool(getattr(parsed, "fresh", False))
    return Checkout.draft(project, timeline, name, client=parsed.client, fresh=fresh), existed


def _describe_edit(before: Mapping[str, Any], after: Mapping[str, Any], tl: Any) -> list[str]:
    """Plain-words lines for the changes between two documents (moments, timeline seconds)."""
    import copy as _copy

    from astrid.sdk.timeline_checkout import Checkout, describe_changes

    return describe_changes(Checkout(_copy.deepcopy(dict(before))), tl)

def _cap(lines: list[str], limit: int = 30) -> list[str]:
    """A long re-flow prints its first lines and a count; the rest is one `timelines diff` away."""
    if len(lines) <= limit:
        return lines
    return lines[:limit] + [f"… and {len(lines) - limit} more (timelines diff shows them all)"]


def _check_lines(report: Any) -> list[str]:
    """After an edit: valid or not, and lint on the cuts it touched (the full report is `timelines check`)."""
    brief = getattr(report, "brief", None)
    if callable(brief):
        return brief()
    lines = str(report).splitlines()
    return [lines[0]] + [ln for ln in lines[1:] if ln.startswith(("!", "  ", "lint"))]


def _edit_range(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    from astrid.sdk.timeline_cuts import diff_bundles

    spans = [c for ch in diff_bundles(before, after)["changes"]
             for c in (ch["before"], ch["after"]) if c]
    if not spans:
        return "0..10"
    lo = max(0.0, min(s[0] for s in spans) - 1.0)
    hi = max(s[1] for s in spans) + 1.0
    return f"{lo:.1f}..{hi:.1f}"


@_guard(1)
def _cmd_checkout(parsed: argparse.Namespace) -> int:
    tl, existed = _working_copy(parsed, create=True)
    name = _draft_name(parsed)
    head = tl.base_revision
    named = len(tl._cut_groups())
    summary = f"{named} cuts · {len(tl.clips())} clips · {len(tl.words())} words" if named else _cuts_summary(tl)
    print(f'working copy "{name}" of {parsed.timeline} · from published {_short_rev(head)} · {summary}'
          + ("" if existed and not parsed.fresh else " · a new working copy, made from the published head"))
    print("show, visualize, lint and diff now read this working copy (--published for the live version)")
    where = f"{parsed.timeline} --project {parsed.project}"
    print("next:")
    print(f"  read     timelines show {where} --as sheet --range c30..c31     (cut ids, \"words\" or seconds)")
    print(f"  edit     change a line of that sheet, then  timelines apply {where} FILE")
    print(f"           or  timelines edit {where} --clip c30.cover --until Astrid")
    print(f'           or  Python: tl = Checkout.draft("{parsed.project}", "{parsed.timeline}"); tl.clip("c30.cover").until("Astrid"); tl.save()')
    print(f"  look     timelines visualize {where} --preset motion --at Astrid   (--compare published: before/after)")
    print(f'  publish  timelines status {where}   ·   timelines publish {where} -m "what changed"')
    return 0


@_guard(2)
def _cmd_edit(parsed: argparse.Namespace) -> int:
    from astrid.sdk.timeline_checkout import Checkout

    voice_ops = any((parsed.line, parsed.insert_line, parsed.remove_line, parsed.gap_after, parsed.from_script))
    timeline_level = parsed.retime or parsed.close_gap_before or parsed.insert or voice_ops
    selector = parsed.clip is not None or parsed.cut is not None
    if selector and timeline_level:
        raise _VerbError("a clip edit and a timeline-level edit (--retime, --close-gap-before, --insert, or a voice "
                         "op) are separate: run one per command", 2)
    if not selector and not timeline_level:
        raise _VerbError("name a clip (--clip QUERY or --cut N) or a timeline-level edit (--retime, "
                         "--close-gap-before WORD, --insert TIME:SECONDS, --line/--insert-line/--remove-line, "
                         "--gap-after SEG=S, --from-script VO.json)", 2)
    if selector and not any(v is not None for v in (parsed.at_word, parsed.at, parsed.nudge, parsed.nudge_frames,
                                                    parsed.extend, parsed.duration, parsed.swap_asset, parsed.on_moment,
                                                    parsed.until_moment, parsed.for_seconds)) and not parsed.set \
            and not getattr(parsed, "remove", False) and not getattr(parsed, "keep", False):
        raise _VerbError("say what to do to the clip: --on, --until, --for, --at-word, --at, --nudge, --nudge-frames, "
                         "--extend, --duration, --set or --swap-asset", 2)
    parsed.set = _parse_set(parsed.set)
    if parsed.duration is not None and parsed.extend is not None:
        raise _VerbError("--duration and --extend both set a length; pick one", 2)
    if parsed.at_word is not None and parsed.at is not None:
        raise _VerbError("--at-word and --at both set a position; pick one", 2)

    if parsed.file:
        tl = Checkout.load(parsed.file)
        target = parsed.file
    else:
        if not parsed.timeline:
            raise _VerbError("name a timeline (timelines edit <timeline> --project P) or pass --file F", 2)
        tl, existed = _working_copy(parsed, create=True)
        if not existed:
            print(f"no working copy yet: checked out {parsed.timeline} from its published head")
        target = None

    before = tl.document()
    _apply_edit(tl, parsed)
    report = tl.check()
    changes = _describe_edit(before, tl.document(), tl)
    if voice_ops:
        for line in _cap(tl.report):
            print(line)
    for line in _cap(changes):
        print(line)
    if not changes:
        print("no change")
    for line in _cap(_check_lines(report)):
        print(line)
    tl.save(target)
    if target is not None:
        print(f'next: timelines check {target}   ·   timelines publish {target} -m "…"')
    else:
        print(f'next: timelines show {parsed.timeline} --project {parsed.project} --range '
              f'{_edit_range(before, tl.document())} (your change)   ·   '
              f'timelines publish {parsed.timeline} --project {parsed.project} -m "…"')
    return 0 if report.valid else 1


def _apply_edit(tl: Any, parsed: argparse.Namespace) -> None:
    """Apply the operations in order: retime, move, nudge, extend, set, swap (timeline-level ops first)."""
    from astrid.sdk.timeline_checkout import TimelineEditError

    if parsed.retime:
        tl.retime()
    if parsed.close_gap_before:
        tl.close_gap(before=parsed.close_gap_before)
    if parsed.insert:
        at, seconds = parsed.insert
        tl.insert_time(at, seconds)
    if parsed.line:
        if not (parsed.take and parsed.words):
            raise _VerbError("--line needs --take (WAV or registry key) and --words (words.json)", 2)
        tl.voice(parsed.line).replace(parsed.take, words=parsed.words, text=parsed.text)
    if parsed.insert_line:
        if not (parsed.after and parsed.take and parsed.words):
            raise _VerbError("--insert-line needs --after SEG, --take and --words", 2)
        tl.insert_line(parsed.insert_line, parsed.take, words=parsed.words, after=parsed.after,
                       gap_after=parsed.gap, text=parsed.text)
    if parsed.remove_line:
        tl.remove_line(parsed.remove_line)
    if parsed.gap_after:
        for item in parsed.gap_after:
            seg, sep, seconds = item.partition("=")
            if not sep or not seg:
                raise _VerbError(f"--gap-after takes SEG=SECONDS, got {item!r}", 2)
            tl.voice(seg).set_gap_after(float(seconds), reflow=False)
        tl.reflow()  # one re-flow for all of them
    if parsed.from_script:
        tl.apply_script(parsed.from_script, takes=parsed.takes, gaps=parsed.script_gaps)
    if parsed.clip is None and parsed.cut is None:
        return
    if parsed.clip is not None:
        clip = tl.clip(parsed.clip, near=parsed.near, cut=parsed.cut)
    else:
        cut_ref = int(parsed.cut) if str(parsed.cut).isdigit() else parsed.cut
        clip = tl.cut(cut_ref).picture
        if clip is None:
            raise TimelineEditError(f"cut {parsed.cut} has no picture clip to edit; use --clip")
    if getattr(parsed, "remove", False):
        clip.remove_layer()
        return
    if getattr(parsed, "keep", False):
        clip.keep()
    if parsed.on_moment is not None:
        clip.on(parsed.on_moment)
    elif parsed.at_word is not None:
        clip.enter_at(tl.word(parsed.at_word, n=parsed.n), offset=parsed.offset or 0.0)
    elif parsed.at is not None:
        clip.enter_at(parsed.at, offset=parsed.offset or 0.0)
    if parsed.until_moment is not None:
        clip.until(parsed.until_moment)
    if parsed.for_seconds is not None:
        clip.hold_for(parsed.for_seconds)
    if parsed.nudge is not None or parsed.nudge_frames is not None:
        clip.nudge(parsed.nudge or 0.0, frames=parsed.nudge_frames or 0)
    if parsed.extend is not None:
        clip.extend(parsed.extend)
    if parsed.duration is not None:
        clip.set_duration(parsed.duration)
    if parsed.set:
        from astrid.sdk.timeline_address import element_schema, unknown_params

        bad = unknown_params(clip.element, list(parsed.set), existing=clip.params)
        if bad and not getattr(parsed, "allow_new_params", False):
            import difflib

            props = sorted((element_schema(clip.element).get("properties") or {}))
            hints = [f"{k} (did you mean {', '.join(difflib.get_close_matches(k, props, n=2))}?)"
                     if difflib.get_close_matches(k, props, n=2) else k for k in bad]
            raise _VerbError(f"{clip.address} is an {clip.element}, which has no param {', '.join(hints)}. "
                             f"It takes: {', '.join(props)}. (See them all: timelines show TL {clip.address}; "
                             "an undeclared param on purpose: --allow-new-params)", 2)
        clip.set(**parsed.set)
    if parsed.swap_asset is not None:
        clip.swap_asset(parsed.swap_asset)


@_guard(2)
def _cmd_words(parsed: argparse.Namespace) -> int:
    from astrid.sdk.timeline_checkout import Checkout

    if parsed.file:
        tl = Checkout.load(parsed.file)
    elif parsed.published:
        tl = Checkout.open(_need_project(parsed), parsed.timeline, client=parsed.client)
    else:
        tl, _existed = _working_copy(parsed, create=True)
    between = None
    if parsed.range:
        lo, hi = _parse_range(parsed.range)
        between = (tl.time(lo), tl.time(hi))
    words = tl.words(between=between)
    if parsed.find:
        needle = parsed.find.strip().lower()
        words = [w for w in words if needle in w.text.lower()]
    cuts = tl.cuts
    for w in words:
        cut = next((c.n for c in cuts if c.start - 1e-6 <= w.start < c.end - 1e-6), "-")
        print(f"  {w.start:.3f}  {w.text}  [{w.id}]  cut {cut}")
    print(f'next: timelines edit {parsed.timeline or "<timeline>"} --project {parsed.project or "<project>"} '
          f"--clip QUERY --at-word WORD")
    return 0


def _parse_range(value: str) -> tuple[str, str]:
    lo, sep, hi = value.partition("..")
    if not sep or not lo or not hi:
        raise _VerbError("--range takes START..END (seconds or m:ss)", 2)
    return lo, hi


@_guard(1)
def _cmd_status(parsed: argparse.Namespace) -> int:
    from astrid.sdk.timeline_checkout import resolve_ids

    tl, _ = _working_copy(parsed, create=False)
    name = _draft_name(parsed)
    if tl is None:
        print(f"no working copy · next: timelines checkout {parsed.timeline} --project {parsed.project}")
        return 0
    changes = tl.changes()
    edits = {"changes": changes}
    print(f'WORKING COPY "{name}" · {len(changes)} unpublished change{"s" if len(changes) != 1 else ""} '
          f"vs published {_short_rev(tl.base_revision)}")
    for line in changes:
        print(f"  {line}")
    report = tl.check()
    for line in _check_lines(report):
        print(line)
    head = resolve_ids(parsed.project, parsed.timeline, client=parsed.client)[2]
    if head != tl.base_revision:
        print(f"the published head moved to {head}; publish will merge (three-way) or report conflicts")
    print(f'next: timelines publish {parsed.timeline} --project {parsed.project} -m "…"' if edits["changes"]
          else f"next: timelines show {parsed.timeline} --project {parsed.project} --as sheet   ·   timelines edit {parsed.timeline} --project {parsed.project} --clip c22.rocket --on viral")
    return 0 if report.valid else 1


def _edit_lines_from(edits: Mapping[str, Any], tl: Any) -> list[str]:
    """Plain-words lines for a working copy's full edit list (against its published base)."""
    return tl.changes()


@_guard(1)
def _cmd_check(parsed: argparse.Namespace) -> int:
    from astrid.sdk.timeline_checkout import Checkout

    if _is_file_ref(parsed.ref):
        report = Checkout.load(parsed.ref).check()
        print(str(report))
        print(f'next: timelines publish {parsed.ref} -m "what changed"')
        return 0 if report.valid else 1
    parsed.timeline = parsed.ref
    tl, _ = _working_copy(parsed, create=False)
    if tl is None:
        print(f"no working copy · next: timelines checkout {parsed.ref} --project {parsed.project}")
        return 0
    report = tl.check()
    print(str(report))
    print(f'next: timelines publish {parsed.ref} --project {parsed.project} -m "what changed"')
    return 0 if report.valid else 1


@_guard(1)
def _cmd_publish(parsed: argparse.Namespace) -> int:
    from astrid.sdk.timeline_checkout import Checkout

    if _is_file_ref(parsed.ref):
        tl = Checkout.load(parsed.ref)
        receipt = tl.publish(parsed.message, idempotency_key=parsed.idempotency_key,
                             client=parsed.client, force=parsed.force)
        return _print_published(receipt, f"timelines diff {tl.bundle.get('timeline_id')} --project "
                                f"{tl.bundle.get('project_id')} --from {receipt.get('old_head')}")
    parsed.timeline = parsed.ref
    tl, _ = _working_copy(parsed, create=False)
    if tl is None or not tl.edits()["changes"]:
        raise _VerbError(f"no unpublished edits to publish · next: timelines checkout {parsed.ref} --project {parsed.project}")
    receipt = tl.publish(parsed.message, idempotency_key=parsed.idempotency_key,
                         client=parsed.client, force=parsed.force)
    tl.discard()
    return _print_published(receipt, f"timelines diff {parsed.ref} --project {parsed.project} --from {receipt.get('old_head')}")


def _print_published(receipt: Mapping[str, Any], next_line: str) -> int:
    if receipt.get("merged"):
        print(f"guard   the head moved since your checkout: {receipt['merged']}")
    else:
        print("guard   the head had not moved since your checkout: nothing to merge, nothing overwritten")
    pinned = receipt.get("narration_pinned") or []
    if pinned:
        print(f"narration re-bound for {len(pinned)} shot(s) whose line text changed")
    print(f"published {_short_rev(receipt.get('new_head'))} (was {_short_rev(receipt.get('old_head'))}) · {receipt.get('message') or ''}".rstrip(" ·"))
    print(f"next: {next_line}")
    return 0


@_guard(1)
def _cmd_discard(parsed: argparse.Namespace) -> int:
    tl, _ = _working_copy(parsed, create=False)
    name = _draft_name(parsed)
    if tl is None:
        print(f"no working copy \"{name}\" to discard · next: timelines checkout {parsed.timeline} --project {parsed.project}")
        return 0
    dropped = len(tl.changes())
    tl.discard()
    print(f'discarded working copy "{name}" of {parsed.timeline} ({dropped} unpublished change{"s" if dropped != 1 else ""} dropped)')
    print(f"next: timelines checkout {parsed.timeline} --project {parsed.project}")
    return 0


@_guard(1)
def _cmd_duplicate(parsed: argparse.Namespace) -> int:
    from astrid.sdk.timeline_duplicate import duplicate_timeline

    if parsed.name:
        print("notice: --name not applied: the runtime's create_empty takes no display name, "
              "so the new timeline is named by its id or --slug")
    result = duplicate_timeline(parsed.project, parsed.timeline, slug=parsed.slug, client=parsed.client)
    print(f"duplicated {parsed.timeline} → {result['timeline_id']} ({result['shots']} shots) · head {result['new_head']}")
    for note in result["notes"]:
        print(f"note: {note}")
    print(f"next: timelines show {result['timeline_id']} --project {parsed.project}")
    return 0


def _parse_set(items: list[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items or []:
        key, sep, raw = item.partition("=")
        if not sep or not key.strip():
            raise _VerbError(f"--set takes key=value, got {item!r}", 2)
        try:
            out[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            out[key.strip()] = raw
    return out


def _parse_insert(value: str) -> tuple[str, float]:
    at, sep, seconds = value.rpartition(":")
    if not sep or not at:
        raise argparse.ArgumentTypeError("--insert takes TIME:SECONDS, e.g. 1:02:0.5")
    try:
        return at, float(seconds)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--insert seconds must be a number: {seconds!r}") from exc



def _add_timeline_args(subparser: argparse.ArgumentParser, *, required_timeline: bool = True) -> None:
    if required_timeline:
        subparser.add_argument("timeline", help="Timeline UUID, ULID, or slug.")
    else:
        subparser.add_argument("timeline", nargs="?", default=None, help="Timeline UUID, ULID, or slug (or use --file).")
    _add_project_arg(subparser, required=False)
    subparser.add_argument("--draft", default=None, help='Working copy name (default "main").')


def _configure_checkout(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Start (or open) the working copy of a timeline: a draft checked out from its published head, kept in "
        "Astrid's data root. Edits go to the working copy; publish sends them to the timeline."
    )
    _add_timeline_args(subparser)
    subparser.add_argument("--fresh", action="store_true", help="Replace the working copy with the current head (drops its edits).")
    subparser.set_defaults(handler=_cmd_checkout)


def _configure_edit(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Edit a clip (or the timeline) in the working copy, in timeline seconds. Operations apply in order: "
        "retime, move (--at-word/--at), nudge, extend/duration, set, swap."
    )
    _add_timeline_args(subparser, required_timeline=False)
    subparser.add_argument("--file", default=None, help="Edit a local checkout file instead of the working copy.")
    selector = subparser.add_argument_group("clip selector (one)")
    selector.add_argument("--clip", default=None, help="Clip by address (c30.cover), layer name, asset key, id or prefix, element, or on-screen text.")
    selector.add_argument("--near", default=None, help="With --clip: the match nearest this word or time.")
    selector.add_argument("--cut", default=None, help="Cut number (show's numbering); alone, the cut's picture clip.")
    move = subparser.add_argument_group("move")
    move.add_argument("--at-word", dest="at_word", default=None, help="Start the clip at this word.")
    move.add_argument("--n", type=int, default=None, help="Which occurrence of --at-word (1-based).")
    move.add_argument("--at", default=None, help="Start the clip at this time (seconds or m:ss).")
    move.add_argument("--offset", type=float, default=None, help="Seconds after the word or time.")
    move.add_argument("--on", dest="on_moment", default=None, metavar="MOMENT",
                      help='Start on a moment: "viral", after "Astrid", beat 2 after "Astrid", c22, +0.8s, "viral" +2f.')
    move.add_argument("--until", dest="until_moment", default=None, metavar="MOMENT",
                      help='End on a moment (its start stays): "Astrid", after "viral", c26.')
    move.add_argument("--for", dest="for_seconds", type=float, default=None, metavar="SECONDS",
                      help="A literal length (prefer --until when the end means something).")
    nudge = subparser.add_argument_group("nudge and length")
    nudge.add_argument("--nudge", type=float, default=None, help="Move by seconds.")
    nudge.add_argument("--nudge-frames", dest="nudge_frames", type=int, default=None, help="Move by frames.")
    nudge.add_argument("--extend", type=float, default=None, help="Lengthen (or, negative, shorten) at the end.")
    nudge.add_argument("--duration", type=float, default=None, help="Set the clip's length in seconds.")
    subparser.add_argument("--set", action="append", default=None, metavar="KEY=VALUE",
                           help="Set an element param (repeatable; JSON values parsed, else a string).")
    subparser.add_argument("--remove", action="store_true",
                           help="Remove the clip (a whole sequence, for one of its steps).")
    subparser.add_argument("--keep", action="store_true",
                           help="Accept an orphan where it is (a fixed time): it stops blocking publish.")
    subparser.add_argument("--allow-new-params", dest="allow_new_params", action="store_true",
                           help="Let --set add a param the element does not declare (normally refused).")
    subparser.add_argument("--swap-asset", dest="swap_asset", default=None, metavar="KEY|FILE",
                           help="Point the clip at another asset key or a local file.")
    timeline_ops = subparser.add_argument_group("timeline-level (no clip selector)")
    timeline_ops.add_argument("--retime", action="store_true", help="Move every anchored clip back onto its word.")
    timeline_ops.add_argument("--close-gap-before", dest="close_gap_before", default=None, metavar="WORD",
                              help="Close the silence before this word and ripple.")
    timeline_ops.add_argument("--insert", type=_parse_insert, default=None, metavar="TIME:SECONDS",
                              help="Open SECONDS of time at TIME and ripple.")
    voice = subparser.add_argument_group("voice lines (timeline-level; the film re-flows and says what moved)")
    voice.add_argument("--line", metavar="SEG", default=None, help="Swap line SEG's take (with --take and --words).")
    voice.add_argument("--take", default=None, help="A WAV path or a registry key (for --line and --insert-line).")
    voice.add_argument("--words", default=None, metavar="WORDS.json", help="The take's words: [[start, end, text], ...].")
    voice.add_argument("--text", default=None, help="The line's script text (narration).")
    voice.add_argument("--insert-line", dest="insert_line", default=None, metavar="SEG", help="Insert a new line SEG.")
    voice.add_argument("--after", default=None, metavar="SEG", help="With --insert-line: the line before it.")
    voice.add_argument("--gap", type=float, default=None, help="With --insert-line: silence after the new line (s).")
    voice.add_argument("--remove-line", dest="remove_line", default=None, metavar="SEG", help="Remove line SEG.")
    voice.add_argument("--gap-after", dest="gap_after", action="append", default=None, metavar="SEG=SECONDS",
                       help="Silence after line SEG (repeatable; one re-flow at the end).")
    voice.add_argument("--from-script", dest="from_script", default=None, metavar="VO.json",
                       help="Bring the voice track in line with a VO script ({segments: [{id, text, gap_after_s}]}): "
                            "lines not in it are removed (their cuts' clips are KEPT as orphans to re-home or remove), "
                            "new ones inserted after the line before them, changed takes swapped; then one re-flow. "
                            "The music bed stays whole. check/publish block until every orphan is handled.")
    voice.add_argument("--takes", default=None, metavar="DIR",
                       help="With --from-script: the folder of <id>.wav + <id>.words.json (default: a vo/ folder "
                            "next to the script's folder, else the script's folder).")
    voice.add_argument("--script-gaps", dest="script_gaps", action="store_true",
                       help="With --from-script: use the script's gap_after_s for every line (default: keep each "
                            "line's declared gap; new lines always take the script's).")
    subparser.set_defaults(handler=_cmd_edit)


def _configure_words(subparser: argparse.ArgumentParser) -> None:
    _add_timeline_args(subparser, required_timeline=False)
    subparser.add_argument("--file", default=None, help="Read a local checkout file instead.")
    subparser.add_argument("--published", action="store_true", help="Read the published head, not the working copy.")
    subparser.add_argument("--find", default=None, help="Only words containing this text.")
    subparser.add_argument("--range", default=None, metavar="A..B", help="Only words between two times (seconds or m:ss).")
    subparser.set_defaults(handler=_cmd_words)


def _configure_undo(subparser: argparse.ArgumentParser) -> None:
    subparser.description = "Undo the last change(s) to the working copy (each edit, apply or re-flow is one step)."
    _add_timeline_args(subparser)
    subparser.add_argument("steps", nargs="?", type=int, default=1, help="How many steps back (default 1).")
    subparser.set_defaults(handler=_cmd_undo)


@_guard(2)
def _cmd_undo(parsed: argparse.Namespace) -> int:
    tl, existed = _working_copy(parsed, create=False)
    if tl is None:
        raise _VerbError(f"no working copy to undo · next: timelines checkout {parsed.timeline} --project {parsed.project}", 2)
    steps = tl.undo(parsed.steps)
    changes = tl.changes()
    print(f"undid {steps} step(s); the working copy now has {len(changes)} unpublished change(s):")
    for line in _cap(changes):
        print(f"  {line}")
    print(f"next: timelines status {parsed.timeline} --project {parsed.project}   ·   timelines undo {parsed.timeline} --project {parsed.project}")
    return 0


def _configure_apply(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Apply an edited cut sheet (from `timelines show --as sheet`, whole or a fragment) to the working copy. "
        "Only the cuts in the file change; then every moment resolves again and the change is checked."
    )
    _add_timeline_args(subparser)
    subparser.add_argument("sheet", help="The edited sheet file.")
    subparser.set_defaults(handler=_cmd_apply)


@_guard(2)
def _cmd_apply(parsed: argparse.Namespace) -> int:
    from pathlib import Path as _Path

    from astrid.sdk.timeline_sheet import SheetError, apply_sheet

    text = _Path(parsed.sheet).expanduser().read_text(encoding="utf-8")
    tl, existed = _working_copy(parsed, create=True)
    if not existed:
        print(f"no working copy yet: checked out {parsed.timeline} from its published head")
    try:
        changes = apply_sheet(tl, text)
    except SheetError as exc:
        raise _VerbError(f"{parsed.sheet}: {exc}", 2) from None
    for line in _cap(changes) or ["no change (the sheet matches the working copy)"]:
        print(line)
    report = tl.check()
    for line in _cap(_check_lines(report)):
        print(line)
    tl.save()
    where = f"{parsed.timeline} --project {parsed.project}"
    print(f"next: timelines visualize {where} --compare published   (before/after of what you changed)")
    print(f'      timelines status {where}   ·   timelines publish {where} -m "what changed"')
    return 0 if report.valid else 1


def _configure_status(subparser: argparse.ArgumentParser) -> None:
    _add_timeline_args(subparser)
    subparser.set_defaults(handler=_cmd_status)


def _configure_check(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("ref", help="Timeline (UUID, ULID or slug) or a checkout file.")
    _add_project_arg(subparser, required=False)
    subparser.add_argument("--draft", default=None, help='Working copy name (default "main").')
    subparser.set_defaults(handler=_cmd_check)


def _configure_publish(subparser: argparse.ArgumentParser) -> None:
    subparser.description = "Publish the working copy (or a checkout file) as one revision; the working copy is then discarded."
    subparser.add_argument("ref", help="Timeline (UUID, ULID or slug) or a checkout file.")
    _add_project_arg(subparser, required=False)
    subparser.add_argument("--draft", default=None, help='Working copy name (default "main").')
    subparser.add_argument("-m", "--message", dest="message", required=True, help="What changed (the revision message).")
    subparser.add_argument("--force", action="store_true", help="Overwrite a conflicting change on the head.")
    _add_idempotency_key(subparser)
    subparser.set_defaults(handler=_cmd_publish)


def _configure_discard(subparser: argparse.ArgumentParser) -> None:
    _add_timeline_args(subparser)
    subparser.set_defaults(handler=_cmd_discard)


def _configure_duplicate(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Create a new timeline holding a copy of this timeline's published head, with new shot ids "
        "(shot ids are global per project). Narration is bound again from the lines' script text when they declare it."
    )
    subparser.add_argument("timeline", help="Source timeline (UUID, ULID or slug); read only.")
    _add_project_arg(subparser)
    subparser.add_argument("--slug", default=None, help="Slug for the new timeline (default: its generated id).")
    subparser.add_argument("--name", default=None, help="Display name (the runtime cannot store one yet: notice only).")
    subparser.set_defaults(handler=_cmd_duplicate)



COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        "create",
        help="Create an empty timeline with a canvas and an empty first head (one SDK call).",
        configure=_configure_create,
    ),
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
        "lint",
        help="Composition and timing checks per picture cut, from timeline data (no capture).",
        configure=_configure_lint,
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
    CommandSpec(
        "checkout",
        help="Start or open the working copy of a timeline (a draft from its published head).",
        configure=_configure_checkout,
    ),
    CommandSpec(
        "edit",
        help="Edit a clip or the timeline in the working copy, in timeline seconds, then check and save.",
        configure=_configure_edit,
    ),
    CommandSpec(
        "apply",
        help="Apply an edited cut sheet (show --as sheet) to the working copy.",
        configure=_configure_apply,
    ),
    CommandSpec(
        "undo",
        help="Undo the last change(s) to the working copy.",
        configure=_configure_undo,
    ),
    CommandSpec(
        "words",
        help="Spoken words with their timeline seconds, ids and cuts (--find, --range).",
        configure=_configure_words,
    ),
    CommandSpec(
        "status",
        help="The working copy's unpublished edits, its check state and whether the head moved.",
        configure=_configure_status,
    ),
    CommandSpec(
        "check",
        help="Validate a working copy (or a checkout file) and show the full report.",
        configure=_configure_check,
    ),
    CommandSpec(
        "publish",
        help="Publish the working copy (or a checkout file) as one revision, then discard the working copy.",
        configure=_configure_publish,
    ),
    CommandSpec(
        "discard",
        help="Delete the working copy (published work is untouched).",
        configure=_configure_discard,
    ),
    CommandSpec(
        "duplicate",
        help="Create a new timeline with a copy of a timeline's head (new shot ids).",
        configure=_configure_duplicate,
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
            "Timeline create/list/show/replace-parent-media/archive/recover/history/diff/lint/visualize/render "
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
