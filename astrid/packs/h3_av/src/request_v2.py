"""Native-v2 media-list normalization kept beside the parent-v1 contract."""

from __future__ import annotations

import hashlib
import json
import math
import re
from fractions import Fraction
from typing import Any, Mapping

from .request import H3Request, H3RequestError, _nonblank, _number, _object, _unknown

FPS = Fraction(24, 1)
SAMPLE_RATE = 48000
PROFILE = "h3_av.native.v2"
_MODALITIES = {"image", "video", "audio"}
_ROLES = {"timeline", "reference"}
_PLACEHOLDER = re.compile(r"(?:\{\{?[^{}]+\}?\}|<[^>]+>|\b(?:TODO|FIXME|PLACEHOLDER)\b)", re.I)


def _fraction(value: Any, path: str, *, minimum: Fraction = Fraction(0)) -> Fraction:
    if isinstance(value, bool):
        raise H3RequestError(f"{path} must be a rational number")
    try:
        if isinstance(value, int):
            result = Fraction(value)
        elif isinstance(value, float) and math.isfinite(value):
            result = Fraction(str(value))
        elif isinstance(value, str) and re.fullmatch(r"\s*[+-]?\d+(?:/\d+)?\s*", value):
            result = Fraction(value.replace(" ", ""))
        else:
            raise ValueError
    except (ValueError, ZeroDivisionError):
        raise H3RequestError(f"{path} must be a rational number") from None
    if result < minimum:
        raise H3RequestError(f"{path} must be >= {float(minimum):g}")
    return result


def _json_number(value: Fraction) -> int | float:
    return value.numerator if value.denominator == 1 else float(value)


def _text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _ticks(value: Fraction, clock: Fraction, path: str) -> int:
    resolved = value * clock
    if resolved.denominator != 1:
        raise H3RequestError(f"{path} is not representable on the native clock")
    return resolved.numerator


def _interval(value: Any, path: str, clock: Fraction) -> tuple[list[int | float], list[int]]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise H3RequestError(f"{path} must be [start, end]")
    start = _fraction(value[0], f"{path}[0]")
    end = _fraction(value[1], f"{path}[1]")
    if end <= start:
        raise H3RequestError(f"{path} must have end > start")
    return [_json_number(start), _json_number(end)], [_ticks(start, clock, f"{path}[0]"), _ticks(end, clock, f"{path}[1]")]


def _asset(value: Any, path: str, modalities: Mapping[str, str] | None) -> tuple[str, str | None]:
    declared: str | None = None
    if isinstance(value, Mapping):
        _unknown(value, {"id", "asset", "modality", "inspection"}, path)
        raw = value.get("id", value.get("asset"))
        declared = value.get("modality")
        inspection = value.get("inspection")
        inspected = inspection.get("modality") if isinstance(inspection, Mapping) else None
        if declared is not None and inspected is not None and declared != inspected:
            raise H3RequestError(f"{path}.modality contradicts asset inspection")
        declared = declared or inspected
    else:
        raw = value
    asset_id = _nonblank(raw, path)
    inspected = modalities.get(asset_id) if modalities is not None else None
    if declared is not None and inspected is not None and declared != inspected:
        raise H3RequestError(f"{path}.modality contradicts asset inspection")
    modality = declared or inspected
    if modality is not None and modality not in _MODALITIES:
        raise H3RequestError(f"{path}.modality must be image, video, or audio")
    return asset_id, modality


def _at(value: Any, path: str) -> tuple[dict[str, int | float], int]:
    item = _object(value, path) or {}
    _unknown(item, {"frame", "seconds"}, path)
    if len(item) != 1:
        raise H3RequestError(f"{path} must contain exactly one of frame or seconds")
    if "frame" in item:
        frame = _fraction(item["frame"], f"{path}.frame")
        if frame.denominator != 1:
            raise H3RequestError(f"{path}.frame must be an integer")
        return {"frame": frame.numerator}, frame.numerator
    seconds = _fraction(item["seconds"], f"{path}.seconds")
    return {"seconds": _json_number(seconds)}, _ticks(seconds, FPS, f"{path}.seconds")


def _mask(value: Any, path: str, modalities: Mapping[str, str] | None) -> dict[str, Any]:
    if isinstance(value, str):
        asset, modality = _asset(value, path, modalities)
        if modality not in (None, "image", "video"):
            raise H3RequestError(f"{path} must reference an image/video mask")
        return {"asset": asset, "polarity": "black_preserve_white_edit"}
    item = _object(value, path) or {}
    _unknown(item, {"asset", "full_frame", "rectangle", "polygon", "range", "polarity", "shape"}, path)
    geometry = [key for key in ("full_frame", "rectangle", "polygon") if key in item]
    if "asset" in item and geometry:
        raise H3RequestError(f"{path} cannot combine an asset with geometry")
    if "asset" not in item and not geometry:
        raise H3RequestError(f"{path} must contain an asset or geometry")
    result: dict[str, Any] = {}
    if "asset" in item:
        asset, modality = _asset(item["asset"], f"{path}.asset", modalities)
        if modality not in (None, "image", "video"):
            raise H3RequestError(f"{path}.asset must reference an image/video mask")
        result["asset"] = asset
    elif item.get("full_frame") is True:
        result["full_frame"] = True
    elif "rectangle" in item:
        values = item["rectangle"]
        if not isinstance(values, list) or len(values) != 4:
            raise H3RequestError(f"{path}.rectangle must be [x, y, width, height]")
        result["rectangle"] = [_number(value, f"{path}.rectangle[{i}]") for i, value in enumerate(values)]
    else:
        points = item["polygon"]
        if not isinstance(points, list) or len(points) < 3:
            raise H3RequestError(f"{path}.polygon must contain at least three points")
        result["polygon"] = [[_number(coord, f"{path}.polygon[{i}][{axis}]") for axis, coord in enumerate(point)] for i, point in enumerate(points)]
    if "range" in item:
        raw_range = item["range"]
        if not isinstance(raw_range, list) or len(raw_range) != 2 or any(type(value) is not int for value in raw_range) or raw_range[0] < 0 or raw_range[1] <= raw_range[0]:
            raise H3RequestError(f"{path}.range must be a non-negative integer frame interval")
        result["range"] = list(raw_range)
        result["resolved_range"] = list(raw_range)
    if item.get("polarity", "black_preserve_white_edit") not in {"black_preserve_white_edit", "black=preserve_white=editable", "black_protected_white_editable"}:
        raise H3RequestError(f"{path}.polarity must be black-preserve/white-edit")
    result["polarity"] = "black_preserve_white_edit"
    return result


def _settings(value: Any) -> dict[str, Any]:
    item = _object(value, "settings") or {}
    _unknown(item, {"model", "steps", "sampler", "seed", "guidance_scale", "guidance"}, "settings")
    if "guidance" in item and "guidance_scale" in item:
        raise H3RequestError("settings.guidance is an alias for guidance_scale, not a second setting")
    result: dict[str, Any] = {"model": "minimax_h3_ref2va_pruned_int8_convrot.safetensors", "steps": 8, "sampler": "res_multistep", "seed": 123456789, "guidance_scale": 0.95}
    if "model" in item: result["model"] = _nonblank(item["model"], "settings.model")
    if "sampler" in item: result["sampler"] = _nonblank(item["sampler"], "settings.sampler")
    if "steps" in item and (type(item["steps"]) is not int or not 1 <= item["steps"] <= 200): raise H3RequestError("settings.steps must be an integer from 1 to 200")
    if "steps" in item: result["steps"] = item["steps"]
    if "seed" in item and (type(item["seed"]) is not int or item["seed"] < 0): raise H3RequestError("settings.seed must be a non-negative integer")
    if "seed" in item: result["seed"] = item["seed"]
    if "guidance_scale" in item or "guidance" in item: result["guidance_scale"] = _number(item.get("guidance_scale", item.get("guidance")), "settings.guidance_scale", minimum=0)
    return result


def normalize_v2(raw: Mapping[str, Any], *, asset_modalities: Mapping[str, str] | None = None) -> H3Request:
    _unknown(raw, {"version", "prompt", "duration", "media", "settings", "continuation"}, "request")
    if raw.get("version") != 2: raise H3RequestError("version must be 2")
    prompt = _nonblank(raw.get("prompt"), "prompt")
    media_raw = raw.get("media")
    if not isinstance(media_raw, list) or not media_raw: raise H3RequestError("media must be a non-empty array")
    continuation = raw.get("continuation", False)
    if type(continuation) is not bool: raise H3RequestError("continuation must be boolean")
    duration = None if raw.get("duration") is None else _json_number(_fraction(raw["duration"], "duration"))
    if duration == 0: raise H3RequestError("duration must be > 0 when supplied")
    occurrences: list[dict[str, Any]] = []
    ids: set[str] = set()
    reference_counts = {"image": 0, "video": 0, "audio": 0}
    for index, raw_item in enumerate(media_raw):
        path = f"media[{index}]"
        item = _object(raw_item, path) or {}
        _unknown(item, {"id", "asset", "role", "modality", "at", "range", "edit", "audio", "hard", "latent_pin"}, path)
        role = item.get("role")
        if role not in _ROLES: raise H3RequestError(f"{path}.role must be timeline or reference")
        asset, inspected = _asset(item.get("asset"), f"{path}.asset", asset_modalities)
        modality = item.get("modality", inspected)
        if modality is not None and modality not in _MODALITIES: raise H3RequestError(f"{path}.modality must be image, video, or audio")
        identifier = _nonblank(item["id"], f"{path}.id") if "id" in item else f"occurrence-{index + 1}"
        if identifier in ids: raise H3RequestError(f"{path}.occurrence_id {identifier!r} is duplicated")
        ids.add(identifier)
        occurrence: dict[str, Any] = {"occurrence_id": identifier, "asset": asset, "role": role}
        if "id" in item: occurrence["id"] = identifier
        if modality is not None: occurrence["modality"] = modality
        else: occurrence["inspection"] = "deferred"
        if role == "reference":
            if any(key in item for key in ("at", "edit", "hard", "latent_pin")): raise H3RequestError(f"{path} reference entries cannot contain placement or edit fields")
            if modality in {"image", "video", "audio"}:
                reference_counts[modality] += 1
                label = {"image": "Picture", "video": "Video", "audio": "Audio"}[modality]
                occurrence["model_tag"] = f"<{label} {reference_counts[modality]}>"
            if "audio" in item:
                if modality != "video" or type(item["audio"]) is not bool: raise H3RequestError(f"{path}.audio is only valid for video references")
                occurrence["audio"] = item["audio"]
            elif modality == "video": occurrence["audio"] = True
            if "range" in item:
                if modality != "video": raise H3RequestError(f"{path}.range is unsupported for {modality or 'unresolved'} references")
                occurrence["range"], occurrence["resolved_range"] = _interval(item["range"], f"{path}.range", FPS)
        else:
            if "at" not in item: raise H3RequestError(f"{path}.at is required for timeline media")
            at, at_frame = _at(item["at"], f"{path}.at")
            occurrence.update({"at": at, "resolved_at": {"unit": "frames", "value": at_frame}, "hard": item.get("hard", False), "edit": []})
            if type(occurrence["hard"]) is not bool: raise H3RequestError(f"{path}.hard must be boolean")
            if "range" in item:
                if modality not in {"video", "audio"}:
                    raise H3RequestError(f"{path}.range is unsupported for {modality or 'unresolved'} timeline media")
                clock = FPS if modality == "video" else Fraction(SAMPLE_RATE)
                occurrence["range"], occurrence["resolved_range"] = _interval(item["range"], f"{path}.range", clock)
            edits = item.get("edit", [])
            if not isinstance(edits, list): raise H3RequestError(f"{path}.edit must be an array")
            for edit_index, raw_edit in enumerate(edits):
                edit_path = f"{path}.edit[{edit_index}]"
                edit = _object(raw_edit, edit_path) or {}
                _unknown(edit, {"stream", "during", "mask", "guides", "text", "dialogue", "channels", "action", "keep"}, edit_path)
                stream = edit.get("stream")
                if stream not in {"video", "audio"}: raise H3RequestError(f"{edit_path}.stream must be video or audio")
                action = "preserve" if edit.get("keep") is True else edit.get("action", "generate")
                if action not in {"generate", "preserve"}: raise H3RequestError(f"{edit_path}.action must be generate or preserve")
                clock = FPS if stream == "video" else Fraction(SAMPLE_RATE)
                if "during" not in edit: raise H3RequestError(f"{edit_path}.during is required")
                during, resolved = _interval(edit["during"], f"{edit_path}.during", clock)
                normalized: dict[str, Any] = {"stream": stream, "during": during, "resolved": {"unit": "frames" if stream == "video" else "samples", "range": resolved}, "action": action}
                if stream == "video": normalized["mask"] = _mask(edit.get("mask", {"full_frame": True}), f"{edit_path}.mask", asset_modalities)
                if stream == "audio": normalized["channels"] = edit.get("channels", "all")
                if "text" in edit or "dialogue" in edit:
                    text = _nonblank(edit.get("text", edit.get("dialogue")), f"{edit_path}.text")
                    if _PLACEHOLDER.search(text): raise H3RequestError(f"{edit_path}.text must be literal dialogue; unresolved placeholder found")
                    normalized["text"] = text
                if "guides" in edit:
                    guides = edit["guides"] if isinstance(edit["guides"], list) else [edit["guides"]]
                    normalized["guides"] = [_nonblank(guide, f"{edit_path}.guides[{i}]") for i, guide in enumerate(guides)]
                occurrence["edit"].append(normalized)
        occurrences.append(occurrence)
    by_id = {item.get("id", item["occurrence_id"]): item for item in occurrences}
    for item in occurrences:
        if item["role"] != "timeline": continue
        if item["resolved_at"]["value"] in {other["resolved_at"]["value"] for other in occurrences if other is not item and other["role"] == "timeline"}: raise H3RequestError("conflicting hard anchors share the same placement")
        for edit in item["edit"]:
            for guide in edit.get("guides", []):
                target = by_id.get(guide)
                if target is None: raise H3RequestError(f"guide {guide!r} is dangling")
                if target["role"] != "reference": raise H3RequestError(f"guide {guide!r} points to timeline media, not a reference")
    if duration is None:
        ends = [Fraction(item["resolved_at"]["value"], FPS) + Fraction(item.get("range", [0, 1])[1] if item.get("range") else 1, FPS) for item in occurrences if item["role"] == "timeline"]
        if not ends: raise H3RequestError("duration is required for a wholly generative request")
        duration = _json_number(max(ends))
    duration_fraction = _fraction(duration, "duration")
    _ticks(duration_fraction, FPS, "duration")
    value = {"version": 2, "prompt": prompt, "duration": duration, "media": occurrences, "settings": _settings(raw.get("settings", {})), "continuation": continuation, "profile": PROFILE, "output_count": 1, "model_tags": {item["occurrence_id"]: item["model_tag"] for item in occurrences if "model_tag" in item}}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return H3Request(value=value, digest=hashlib.sha256(encoded).hexdigest())


def read_prepared_request(raw: Mapping[str, Any], expected_digest: Any, *, require_normalized_v2: bool = False) -> H3Request:
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest): raise H3RequestError("prepared request digest is missing or malformed")
    if raw.get("version") == 2 and raw.get("profile") == PROFILE and raw.get("output_count") == 1:
        encoded = json.dumps(dict(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        request = H3Request(value=dict(raw), digest=hashlib.sha256(encoded).hexdigest())
    else:
        request = normalize_v2(raw) if raw.get("version") == 2 else H3Request(value=dict(raw), digest=expected_digest)
    if request.digest != expected_digest: raise H3RequestError("prepared request digest does not match its normalized request")
    if require_normalized_v2 and request.value.get("version") != 2: raise H3RequestError("prepared v2 request is missing normalized derived fields")
    return request


def branch_for(request: H3Request) -> str:
    if request.value.get("version") != 2: raise H3RequestError("native-v2 branch selection requires a v2 request")
    media = request.value["media"]
    timelines = [item for item in media if item["role"] == "timeline"]
    if not timelines: return "source_free"
    if all(item.get("modality") == "audio" for item in timelines): return "audio_only"
    if request.value.get("continuation") and any(item.get("modality") == "video" for item in timelines): return "extension_context"
    return "source_backed_v2v"


__all__ = ["PROFILE", "SAMPLE_RATE", "FPS", "branch_for", "normalize_v2", "read_prepared_request"]
