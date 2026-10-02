"""Explicit draft narration reconciliation over the canonical authoring bundle.

The coordinator uses its caller's existing project authoring authority. Pack
workers can prepare the same candidate, but never acquire publish credentials.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from typing import Any, Callable, Mapping

KEY = "draft_voiceover"
DEFAULT_SETTINGS = {"provider": "edge-tts", "voice": "en-US-ChristopherNeural",
                    "rate": "+0%", "volume": "+0%", "pitch": "+0Hz"}


class VoiceoverSyncError(ValueError):
    pass


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def settings_with_defaults(settings: Mapping[str, Any] | None) -> dict[str, str]:
    result = dict(DEFAULT_SETTINGS)
    supplied = dict(settings or {})
    if set(supplied) - set(result):
        raise VoiceoverSyncError("Unknown speech settings")
    result.update(supplied)
    if any(not isinstance(value, str) or not value for value in result.values()):
        raise VoiceoverSyncError("Speech settings must be nonempty strings")
    if result["provider"] != "edge-tts":
        raise VoiceoverSyncError("Supported speech provider: edge-tts")
    return result


def selected_media(clip: Mapping, internal: Mapping) -> str | None:
    assets = internal.get("registry", {}).get("assets", {})
    for field in ("media_id", "object_id", "asset", "asset_id"):
        value = clip.get(field)
        if isinstance(value, str) and value.startswith("sha256:"):
            return value
        asset = assets.get(value, {}) if isinstance(value, str) else {}
        for key in ("media_id", "object_id", "digest", "content_sha256"):
            identity = asset.get(key)
            if isinstance(identity, str) and identity:
                return identity if identity.startswith("sha256:") else "sha256:" + identity
    return None


def _audio_clips(shot: Mapping) -> list[dict]:
    internal = shot["internal_timeline"]
    tracks = {t["id"]: t.get("kind") for t in internal.get("tracks", [])}
    assets = internal.get("registry", {}).get("assets", {})
    return [clip for clip in internal.get("clips", [])
            if tracks.get(clip.get("track")) in {"audio", "voice", "vo"}
            or str(assets.get(clip.get("asset"), {}).get("type", "")).startswith("audio")]


def plan_draft_voiceover(candidate: Mapping, scripts: Mapping, *, settings=None,
                         occurrence_ids=None, adopt_clip_ids=None,
                         available_media_ids=None) -> dict:
    """Compare verified pinned script rows with the exact selected audio identity.

    An adoption is explicit permission to replace a particular legacy draft
    clip; it is never evidence that its old audio matches the current words.
    """
    voice = settings_with_defaults(settings)
    if scripts.get("revision_id") != candidate["base_parent"]["revision_id"]:
        raise VoiceoverSyncError("Script and candidate must pin the same parent revision")
    rows = {row["occurrence_id"]: row for row in scripts["occurrences"]}
    selected = set(occurrence_ids) if occurrence_ids is not None else None
    known = {p["occurrence_id"] for p in candidate["placements"]}
    if selected is not None and selected - known:
        raise VoiceoverSyncError("Unknown selected occurrence")
    adopted = set(adopt_clip_ids or ())
    clip_occurrences: dict[str, int] = {}
    adoption_placements = [p for p in candidate["placements"]
                          if selected is None or p["occurrence_id"] in selected]
    for placement in adoption_placements:
        shot = candidate["shots"][placement["shot_id"]]
        for clip in _audio_clips(shot):
            clip_occurrences[clip["id"]] = clip_occurrences.get(clip["id"], 0) + 1
    known_clips = set(clip_occurrences)
    if adopted - known_clips:
        raise VoiceoverSyncError("Unknown adoption clip id")
    if any(clip_occurrences[clip_id] > 1 for clip_id in adopted):
        raise VoiceoverSyncError("Adoption clip id is ambiguous across shot occurrences")
    jobs, statuses = [], []
    for placement in candidate["placements"]:
        oid = placement["occurrence_id"]
        if selected is not None and oid not in selected:
            continue
        shot = candidate["shots"][placement["shot_id"]]
        row = rows.get(oid, {})
        status = {"occurrence_id": oid, "shot_id": placement["shot_id"]}
        if row.get("status") not in {"present", "empty"} or not isinstance(row.get("text"), str):
            statuses.append({**status, "status": "blocked_script", "reason": row.get("status", "missing")})
            continue
        text = row["text"]
        if not text.strip():
            statuses.append({**status, "status": "empty_script"})
            continue
        metadata = shot["payload"].get("metadata", {})
        record = metadata.get(KEY, {})
        audio = _audio_clips(shot)
        target = [c for c in audio if c.get("id") == record.get("clip_id")]
        if not target:
            target = [c for c in audio if c["id"] in adopted]
        if record.get("status") in {"final", "recorded", "locked"}:
            statuses.append({**status, "status": "protected_audio"})
            continue
        if len(target) > 1 or (audio and not target):
            statuses.append({**status, "status": "protected_audio", "clip_ids": [c["id"] for c in audio]})
            continue
        clip = target[0] if target else None
        if clip is not None and (clip.get("app", {}).get(KEY, {}).get("status") in {"final", "recorded", "locked"}):
            statuses.append({**status, "status": "protected_audio"})
            continue
        media = selected_media(clip, shot["internal_timeline"]) if clip else None
        owned = record.get("owner") == "video_editing.sync_draft_voiceover" and record.get("status") == "draft"
        fresh = (owned and media and record.get("audio_media_id") == media
                 and record.get("text") == text and record.get("text_digest") == digest(text)
                 and record.get("settings") == voice
                 and (available_media_ids is None or media in available_media_ids))
        if fresh:
            statuses.append({**status, "status": "reused", "clip_id": clip["id"]})
            continue
        if clip is not None and not owned and clip["id"] not in adopted:
            statuses.append({**status, "status": "protected_audio"})
            continue
        jobs.append({**status, "text": text, "settings": voice,
                     "clip_id": clip["id"] if clip else "draft-vo-" + oid,
                     "previous_media_id": media})
        statuses.append({**status, "status": "regenerate"})
    return {"schema_version": 1, "base_head": candidate["base_parent"]["revision_id"],
            "settings": voice, "jobs": jobs, "statuses": statuses}


def _duration(clip: Mapping) -> float:
    if "hold" in clip:
        return float(clip["hold"])
    return (float(clip.get("to", 0)) - float(clip.get("from", 0))) / float(clip.get("speed", 1))


def _default_source_offset(value: Any) -> bool:
    """Match the canonical authoring compiler's untrimmed placement forms."""
    return value in (None, 0, 0.0, {}) or (
        isinstance(value, Mapping)
        and value.get("start", 0) == 0
        and value.get("end", 0) == 0
    )


def _default_speed(value: Any) -> bool:
    """Match the compiler's numeric and rational representations of 1x."""
    return value in (None, 1, 1.0) or (
        isinstance(value, Mapping)
        and value.get("numerator") == 1
        and value.get("denominator") == 1
    )


def _interval(clip: dict, start: float, end: float) -> None:
    old_duration = _duration(clip)
    if end <= start:
        raise VoiceoverSyncError("Retime would collapse a clip")
    clip["at"] = round(start, 9)
    if "hold" in clip:
        clip["hold"] = round(end - start, 9)
    else:
        clip["speed"] = float(clip.get("speed", 1)) * old_duration / (end - start)


def retime_candidate(candidate: dict, durations: Mapping[str, float], *, policy: str,
                     padding_seconds: float = 0.15) -> None:
    """Preserve placement lengths or ripple a nonoverlapping shot sequence.

    Ripple scales visual intervals and moves parent layers with the same
    piecewise time map. Audio other than the owned draft is never stretched.
    Parameterized clips need an explicit app.draft_voiceover_timing='stretch'
    declaration before their interval may be scaled; parameter internals remain
    the component owner's responsibility. Source offsets/speeds are rejected.
    """
    if policy not in {"preserve", "ripple"}:
        raise VoiceoverSyncError("timing_policy must be preserve or ripple")
    if not math.isfinite(padding_seconds) or padding_seconds < 0:
        raise VoiceoverSyncError("padding_seconds must be finite and nonnegative")
    placements = sorted(candidate["placements"], key=lambda p: p["placement"]["start_ms"])
    spans, delta = [], 0.0
    previous_end = -1.0
    for p in placements:
        start = p["placement"]["start_ms"] / 1000
        length = p["duration_ms"] / 1000
        if p["occurrence_id"] in durations:
            if (not _default_source_offset(p.get("source_offset"))
                    or not _default_speed(p.get("speed"))):
                raise VoiceoverSyncError("Draft narration retiming requires untrimmed speed-1 occurrences")
            audio_duration = durations[p["occurrence_id"]]
            if policy == "preserve" and audio_duration > length + 1e-9:
                raise VoiceoverSyncError("New narration exceeds preserved shot duration; choose ripple")
            new_length = math.ceil((audio_duration + padding_seconds) * 1000) / 1000 if policy == "ripple" else length
        else:
            new_length = length
        if policy == "ripple" and start < previous_end - 1e-9:
            raise VoiceoverSyncError("Ripple requires nonoverlapping shot placements")
        spans.append((start, start + length, start + delta, start + delta + new_length))
        previous_end = start + length
        if new_length != length:
            shot = candidate["shots"][p["shot_id"]]
            owned_id = shot["payload"].get("metadata", {}).get(KEY, {}).get("clip_id")
            for clip in shot["internal_timeline"].get("clips", []):
                if clip.get("id") == owned_id:
                    continue
                if clip in _audio_clips(shot):
                    raise VoiceoverSyncError("Ripple cannot stretch another audio clip")
                if clip.get("params") and clip.get("app", {}).get("draft_voiceover_timing") != "stretch":
                    raise VoiceoverSyncError("Parameterized internal clip needs explicit stretch permission")
                a = float(clip.get("at", 0)); b = a + _duration(clip)
                _interval(clip, a * new_length / length, b * new_length / length)
            # config may carry an admitted duration mirror.
            if "duration" in shot["internal_timeline"]:
                shot["internal_timeline"]["duration"] = new_length
        p["placement"]["start_ms"] = round((start + delta) * 1000)
        p["duration_ms"] = round(new_length * 1000)
        delta += new_length - length
    if policy == "preserve" or not delta and all(a == c and b == d for a,b,c,d in spans):
        return
    def warp(t):
        shift = 0.0
        for a, b, c, d in spans:
            if t < a:
                return t + shift
            if t <= b:
                return c + (t - a) * (d - c) / (b - a)
            shift = d - b
        return t + shift
    for clip in candidate["parent"].get("clips", []):
        a = float(clip.get("at", 0)); b = a + _duration(clip)
        c, d = warp(a), warp(b)
        if abs((d-c)-(b-a)) > 1e-8:
            if clip.get("params") and clip.get("app", {}).get("draft_voiceover_timing") != "stretch":
                raise VoiceoverSyncError("Parameterized parent layer needs explicit stretch permission")
            tracks = candidate["parent"].get("config", {}).get("tracks", [])
            if any(t.get("id") == clip.get("track") and t.get("kind") == "audio" for t in tracks):
                raise VoiceoverSyncError("Ripple cannot stretch parent audio")
        _interval(clip, c, d)
    config = candidate["parent"].get("config", {})
    if "clips" in config:
        config["clips"] = copy.deepcopy(candidate["parent"].get("clips", []))
    if "duration" in config:
        config["duration"] = warp(float(config["duration"]))


def prepare_draft_voiceover(candidate: Mapping, scripts: Mapping, *, synthesize: Callable,
                            settings=None, occurrence_ids=None, adopt_clip_ids=None,
                            timing_policy: str, padding_seconds=0.15,
                            available_media_ids=None) -> dict:
    plan = plan_draft_voiceover(candidate, scripts, settings=settings,
        occurrence_ids=occurrence_ids, adopt_clip_ids=adopt_clip_ids,
        available_media_ids=available_media_ids)
    work = copy.deepcopy(candidate)
    generated, durations = [], {}
    # Complete every synthesis before changing even the detached candidate.
    for job in plan["jobs"]:
        result = dict(synthesize(job["text"], job["settings"]))
        duration = float(result["duration_seconds"])
        media = result["audio_media_id"]
        if not math.isfinite(duration) or duration <= 0 or not isinstance(media, str) or not media.startswith("sha256:"):
            raise VoiceoverSyncError("Speech result lacks verified managed audio and duration")
        if result.get("text") != job["text"] or result.get("settings") != job["settings"]:
            raise VoiceoverSyncError("Speech provenance differs from requested words/settings")
        generated.append((job, result))
    for job, result in generated:
        shot = work["shots"][job["shot_id"]]
        internal = shot["internal_timeline"]
        clips = internal.setdefault("clips", [])
        clip = next((c for c in clips if c["id"] == job["clip_id"]), None)
        if clip is None:
            track = "draft-vo"
            tracks = internal.setdefault("tracks", [])
            if not any(t["id"] == track for t in tracks):
                tracks.append({"id": track, "kind": "audio"})
            clip = {"id": job["clip_id"], "track": track, "clipType": "media", "volume": 1}
            clips.append(clip)
        # Use the selected managed identity directly, retaining registry alternatives.
        for key in ("asset", "asset_id", "object_id", "hold", "speed"):
            clip.pop(key, None)
        clip.update(media_id=result["audio_media_id"], at=0, **{"from": 0, "to": result["duration_seconds"]})
        shot["payload"].setdefault("metadata", {})[KEY] = {
            "schema_version": 1, "owner": "video_editing.sync_draft_voiceover", "status": "draft",
            "clip_id": clip["id"], "audio_media_id": result["audio_media_id"],
            "text": job["text"], "text_digest": digest(job["text"]), "settings": job["settings"],
            "duration_seconds": result["duration_seconds"], "generation_run_id": result.get("run_id"),
            "generation_task_id": result.get("task_id"), "source_parent_head": plan["base_head"],
        }
        durations[job["occurrence_id"]] = result["duration_seconds"]
    retime_candidate(work, durations, policy=timing_policy, padding_seconds=padding_seconds)
    return {"candidate": work, "plan": plan, "changed": bool(generated),
            "generated": [{"occurrence_id": job["occurrence_id"], **result} for job,result in generated]}


def _data(result):
    if hasattr(result, "ok"):
        if not result.ok:
            raise VoiceoverSyncError(str(result.error))
        return result.data
    return result.get("data", result)


def _artifact_identity(row):
    for key in ("object_id", "media_id", "content_hash", "digest", "sha256"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value if value.startswith("sha256:") else "sha256:" + value
    raise VoiceoverSyncError("Managed artifact has no digest")


def runtime_synthesizer(client, project):
    """Invoke speech through SDK; verify provenance bytes from managed outputs."""
    # A deliberate regeneration must not replay a failed or missing old output.
    # Share one identity within this batch; a later explicit sync gets a new one.
    request_context = {"draft_voiceover_sync": uuid.uuid4().hex}
    def synthesize(text, settings):
        import astrid.sdk as sdk
        result = sdk.invoke("generation.generate_speech", kind="executor", project=project,
            inputs={"text": text, **settings}, client=client, wait=True,
            idempotency_context=request_context)
        if not result.ok:
            raise VoiceoverSyncError(str(result.error))
        artifacts = result.outputs.get("managed_outputs") or result.outputs.get("artifacts") or []
        manifest_row = next((r for r in artifacts if r.get("name") in {"speech_manifest", "manifest", "manifest.json"}
                             or str(r.get("path", "")).endswith("manifest.json")), None)
        audio_row = next((r for r in artifacts if r.get("name") in {"speech", "generated_audio"}
                          or str(r.get("path", "")).endswith("speech.wav")), None)
        if manifest_row is None or audio_row is None:
            raise VoiceoverSyncError("Completed speech run has no managed WAV/provenance outputs")
        manifest = json.loads(client.media.read_bytes(_artifact_identity(manifest_row)))
        audio_id = _artifact_identity(audio_row)
        audio_bytes = client.media.read_bytes(audio_id)
        if ("sha256:" + hashlib.sha256(audio_bytes).hexdigest() != audio_id
                or manifest.get("audio_sha256") != audio_id.removeprefix("sha256:")
                or manifest.get("text_sha256") != digest(text).removeprefix("sha256:")):
            raise VoiceoverSyncError("Managed speech provenance failed hash verification")
        return {"text": manifest["text"], "settings": manifest["settings"],
                "audio_media_id": audio_id, "duration_seconds": manifest["duration_seconds"],
                "run_id": result.run_id, "task_id": result.kernel_task_id}
    return synthesize


def sync_draft_voiceover(client, project: str, timeline: str, *, timing_policy: str,
                         settings=None, occurrence_ids=None, adopt_clip_ids=None,
                         padding_seconds=0.15, plan_only=False) -> dict:
    """One caller-authorized operation: pin, compare, generate, validate, CAS save.

    No render is requested. Protected/empty/missing-script rows are reported.
    The source script is read from pinned runtime text; callers cannot inject it.
    """
    if timing_policy not in {"preserve", "ripple"}:
        raise VoiceoverSyncError("timing_policy must be preserve or ripple")
    if not math.isfinite(padding_seconds) or padding_seconds < 0:
        raise VoiceoverSyncError("padding_seconds must be finite and nonnegative")
    shown = _data(client.timelines.open_composition(project, timeline))
    head = shown["summary"]["head_revision_id"]
    scope = _data(client.timelines.resolve_scope(project, timeline))
    project_row = _data(client.projects.show(project))
    project_id = project_row.get("project_id") or project_row["id"]
    timeline_id = scope["timeline_id"]
    endpoint = getattr(client, "endpoint", None)
    endpoint = getattr(endpoint, "url", endpoint)
    bound = client.open_authoring_target({"endpoint": endpoint, "project_id": project_id,
        "timeline_id": timeline_id, "head_revision_id": head,
        "capabilities": {"edit": {"status": "available", "route": "authoring-bundle validate/commit"}}})
    candidate = bound.open()
    scripts = _data(client.timelines.script(project_id, timeline_id, revision_id=head))
    options = dict(settings=settings, occurrence_ids=occurrence_ids, adopt_clip_ids=adopt_clip_ids)
    plan = plan_draft_voiceover(candidate, scripts, **options)
    # Fresh provenance is reusable only while its managed audio bytes remain
    # readable and match the selected immutable identity. A missing object is
    # regenerated; authorization, integrity, and protocol errors stay visible.
    available_media_ids = set()
    for row in plan["statuses"]:
        if row.get("status") != "reused":
            continue
        shot = candidate["shots"][row["shot_id"]]
        clip = next(c for c in _audio_clips(shot) if c["id"] == row["clip_id"])
        media = selected_media(clip, shot["internal_timeline"])
        try:
            payload = client.media.read_bytes(media)
        except Exception as exc:
            if getattr(exc, "code", None) == "not_found":
                continue
            raise
        if not isinstance(payload, bytes) or "sha256:" + hashlib.sha256(payload).hexdigest() != media:
            raise VoiceoverSyncError("Selected draft audio failed managed-byte hash verification")
        available_media_ids.add(media)
    plan = plan_draft_voiceover(candidate, scripts,
        **options, available_media_ids=available_media_ids)
    if plan_only or not plan["jobs"]:
        return {"status": "planned" if plan_only else "unchanged", "plan": plan, "published": False}
    prepared = prepare_draft_voiceover(candidate, scripts,
        synthesize=runtime_synthesizer(client, project_id), timing_policy=timing_policy,
        padding_seconds=padding_seconds, available_media_ids=available_media_ids, **options)
    # Detect edits made while generation was in flight. Publication repeats the
    # check and Runtime CAS closes the final race. Never rebase stale speech.
    current = _data(client.timelines.open_composition(project_id, timeline_id))
    if current["summary"]["head_revision_id"] != head:
        raise VoiceoverSyncError("Timeline changed during synthesis; rerun sync from its new head")
    bound.validate(prepared["candidate"])
    diff = bound.diff(prepared["candidate"])
    frozen = bound.preview(prepared["candidate"])
    receipt = bound.publish(prepared["candidate"], idempotency_key=frozen["candidate_digest"])
    return {"status": "published", "published": True, "plan": plan, "diff": diff,
            "generated": prepared["generated"], "receipt": receipt}
