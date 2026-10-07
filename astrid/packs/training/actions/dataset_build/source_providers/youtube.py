"""Generic YouTube source provider using existing Astrid actions."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from astrid import sdk

from ....shared.items import deterministic_id, make_candidate_item
from ..acquisition import (
    limit_hint_from_config,
    record_acquisition_result,
    request_from_config,
    string_set,
)
from ..budget import ChildWorkMeter
from ..media import extract_clip_ffmpeg, ffprobe_metadata

Runner = Callable[..., subprocess.CompletedProcess[str]]


class YouTubeSourceProvider:
    provider_id = "youtube"

    def __init__(
        self,
        *,
        runner: Runner = subprocess.run,
        prober: Callable[[Path], dict[str, Any]] = ffprobe_metadata,
        child_work_meter: ChildWorkMeter | None = None,
        materialization_root: Path | None = None,
        **_: Any,
    ) -> None:
        self._runner = runner
        self._prober = prober
        self._meter = child_work_meter
        self._root = (materialization_root or Path.cwd() / "outputs").resolve()
        self._media: dict[Path, tuple[dict[str, Any], Any]] = {}

    def acquire(self, config: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
        if self._meter is None:
            raise ValueError("YouTube child work requires the run-scoped finite child_work_meter")
        out_dir = (
            Path(str(config.get("out_dir", "runs/dataset-build/youtube"))).expanduser().resolve()
        )
        downloads_dir = out_dir / "downloads"
        scenes_dir = out_dir / "scenes"
        clips_dir = out_dir / "clips"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        scenes_dir.mkdir(parents=True, exist_ok=True)
        clips_dir.mkdir(parents=True, exist_ok=True)

        dataset_config = (
            config.get("dataset_config")
            if isinstance(config.get("dataset_config"), Mapping)
            else {}
        )
        clip_config = (
            dataset_config.get("clip_config", {}) if isinstance(dataset_config, Mapping) else {}
        )
        min_duration = float(config.get("min_duration_s", clip_config.get("min_duration_s", 0)))
        max_duration = float(config.get("max_duration_s", clip_config.get("max_duration_s", 60)))
        max_scenes = int(
            config.get("max_scenes_per_source", clip_config.get("max_scenes_per_source", 20))
        )

        request = request_from_config(config)
        processed_source_ids = string_set(
            config.get("processed_source_ids"), request.get("processed_source_ids")
        )
        exclude_source_ids = string_set(
            config.get("exclude_source_ids"), request.get("exclude_source_ids")
        )
        exclude_candidate_ids = string_set(
            config.get("exclude_candidate_ids"), request.get("exclude_candidate_ids")
        )
        exclude_media_hashes = string_set(
            config.get("exclude_media_hashes"), request.get("exclude_media_hashes")
        )
        limit_hint = limit_hint_from_config(config, request)
        considered = 0
        skipped_processed = 0
        skipped_excluded = 0
        skipped_duplicate_media = 0
        if limit_hint == 0:
            record_acquisition_result(
                self,
                config,
                provider_id=self.provider_id,
                request=request,
                considered=0,
                yielded=0,
            )
            return
        yielded = 0

        try:
            for source_index, source in enumerate(_configured_sources(config, dataset_config)):
                considered += 1
                source_id = youtube_source_key(source)
                if source_id in processed_source_ids:
                    skipped_processed += 1
                    continue
                if source_id in exclude_source_ids:
                    skipped_excluded += 1
                    continue
                operation = deterministic_id(
                    str(out_dir),
                    request.get("round_index", 0),
                    source_index,
                    source_id,
                    prefix="yt_work",
                )
                self._child_keys = (operation + "_download", operation + "_scenes")
                self._meter.check_acquisition(self._child_keys)
                video_path = self._download_source(
                    source, source_id=source_id, downloads_dir=downloads_dir
                )
                scenes = self._detect_scenes(
                    video_path, scenes_dir / f"{source_id}.scenes.json"
                )
                if not scenes:
                    scenes = [{"start": 0.0, "end": _duration_or_zero(video_path, self._prober)}]
                for scene_index, scene in enumerate(scenes[:max_scenes]):
                    start_s, end_s = _scene_bounds(scene)
                    duration = end_s - start_s
                    if duration < min_duration or duration > max_duration:
                        continue
                    clip_id = deterministic_id(
                        self.provider_id, source["kind"], source["value"], scene_index, prefix="yt"
                    )
                    if clip_id in exclude_candidate_ids:
                        skipped_excluded += 1
                        continue
                    clip_path = clips_dir / f"{clip_id}.mp4"
                    extract_clip_ffmpeg(
                        video_path,
                        start_s=start_s,
                        end_s=end_s,
                        out_path=clip_path,
                        runner=self._runner,
                    )
                    metadata = dict(self._prober(clip_path))
                    candidate = make_candidate_item(
                        source_type=self.provider_id,
                        source_id=clip_id,
                        source_url=source["value"]
                        if source["kind"] == "url"
                        else f"ytsearch:{source['value']}",
                        media_path=clip_path,
                        media_type="video",
                        source_metadata=metadata,
                        duration_s=duration,
                        clip_start_s=start_s,
                        clip_end_s=end_s,
                        scene_index=scene_index,
                        derived_from={
                            "source_id": source_id,
                            "source_type": self.provider_id,
                            "transformation": "scene_extract",
                        },
                        rights=config.get("rights"),
                    )
                    if str(candidate.get("content_hash") or "") in exclude_media_hashes:
                        skipped_duplicate_media += 1
                        continue
                    yield candidate
                    yielded += 1
                    if limit_hint is not None and yielded >= limit_hint:
                        return
        finally:
            record_acquisition_result(
                self,
                config,
                provider_id=self.provider_id,
                request=request,
                considered=considered,
                yielded=yielded,
                skipped_processed=skipped_processed,
                skipped_excluded=skipped_excluded,
                skipped_duplicate_media=skipped_duplicate_media,
            )

    def _download_source(
        self, source: Mapping[str, str], *, source_id: str, downloads_dir: Path
    ) -> Path:
        inputs = {"query": source["value"], "mode": "video"}
        child = self._invoke(
            "youtube.youtube_audio",
            inputs,
            self._child_keys[0],
            downloads_dir / f"{source_id}.child.json",
        )
        row, local, path = self._materialize(child, "media")
        self._media[path] = (row, local)
        return path

    def _detect_scenes(self, video_path: Path, out_path: Path) -> list[dict[str, Any]]:
        row, local = self._media[video_path]
        # Revalidate the original association and local file immediately before
        # submission. The public binding and host snapshot remain authoritative.
        child, media_row = self._download_result, row
        _, verified_local, verified_path = self._materialize(child, "media")
        if verified_path != video_path or verified_local != local:
            raise ValueError("YouTube materialization changed before Scenes submission")
        producer = local.producer_file()
        self._meter.register(media_row, producer, name="video")
        scenes = self._invoke(
            "editorial.scenes",
            {"video": producer},
            self._child_keys[1],
            out_path.with_suffix(".child.json"),
        )
        _, _, path = self._materialize(scenes, "scenes")
        payload = path.read_bytes()
        raw = json.loads(payload)
        values = (
            raw if isinstance(raw, list) else raw.get("scenes") if isinstance(raw, dict) else None
        )
        if not isinstance(values, list) or any(not isinstance(scene, dict) for scene in values):
            raise ValueError("Scenes child returned an invalid scene list")
        # Preserve the provider's scene checkpoint and successful empty fallback.
        out_path.write_bytes(payload)
        return values

    def _invoke(self, capability: str, inputs: Mapping[str, Any], key: str, receipt: Path):
        self._meter.admit(key, capability, inputs)
        try:
            child = sdk.invoke(
                capability,
                kind="action",
                inputs=inputs,
                child_key=key,
                wait=True,
                timeout_seconds=600.0,
                poll_seconds=0.1,
            )
        except Exception as exc:
            receipt.write_text(
                json.dumps(
                    {
                        "capability_id": capability,
                        "child_key": key,
                        "error": str(exc),
                        "admission": "uncertain",
                    }
                ),
                encoding="utf-8",
            )
            raise
        receipt.write_text(
            json.dumps(
                {
                    "capability_id": capability,
                    "child_key": key,
                    "task_id": child.kernel_task_id,
                    "attempt_id": child.kernel_attempt_id,
                    "run_id": child.kernel_run_id,
                    "ok": child.ok,
                    "error": child.error,
                    "outputs": child.outputs.get("managed_outputs", []),
                }
            ),
            encoding="utf-8",
        )
        if child.ok is not True or child.raw_result.get("state") != "completed":
            raise RuntimeError(f"{capability} child did not complete: {child.error}")
        if capability == "youtube.youtube_audio":
            self._download_result = child
        return child

    def _materialize(self, child, port: str):
        if child.ok is not True or not all(
            isinstance(value, str) and value
            for value in (child.kernel_task_id, child.kernel_attempt_id, child.kernel_run_id)
        ):
            raise ValueError("child output requires exact successful task/attempt identity")
        rows = child.outputs.get("managed_outputs")
        if not isinstance(rows, list):
            raise ValueError("child has no managed output descriptors")
        matches = [
            row for row in rows if isinstance(row, Mapping) and row.get("output_port") == port
        ]
        if len(matches) != 1:
            raise ValueError(f"child must return exactly one {port} output")
        row = dict(matches[0])
        if (row.get("task_id"), row.get("attempt_id"), row.get("run_id")) != (
            child.kernel_task_id,
            child.kernel_attempt_id,
            child.kernel_run_id,
        ):
            raise ValueError("child output task/attempt identity changed")
        if port == "media":
            self._meter.check_registration(row)
        self._meter.retain(row)
        local = child.materialize_output(row["association_id"])
        if dict(local.output) != row:
            raise ValueError("materialized child output descriptor changed")
        filename = Path(local.filename)
        if filename.is_absolute() or ".." in filename.parts:
            raise ValueError("materialized child filename escapes output root")
        path = (self._root / filename).resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(self._root):
            raise ValueError("materialized child file escapes output root")
        if path.stat().st_size != row["size"]:
            raise ValueError("materialized child size changed")
        sha = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                sha.update(chunk)
        if "sha256:" + sha.hexdigest() != row["digest"]:
            raise ValueError("materialized child digest changed")
        return row, local, path


def _configured_sources(
    config: Mapping[str, Any], dataset_config: Mapping[str, Any]
) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for key in ("source_urls", "urls"):
        for url in config.get(key, []) or []:
            sources.append({"kind": "url", "value": str(url)})
    for query in config.get("search_queries", []) or []:
        sources.append({"kind": "query", "value": str(query)})
    buckets = dataset_config.get("buckets", {}) if isinstance(dataset_config, Mapping) else {}
    for bucket in buckets.values():
        if isinstance(bucket, Mapping):
            for query in bucket.get("search_queries", []) or []:
                sources.append({"kind": "query", "value": str(query)})
    return sources


def youtube_source_key(source: Mapping[str, str]) -> str:
    return deterministic_id(
        "youtube", source.get("kind", ""), source.get("value", ""), prefix="yt_source"
    )


def _scene_bounds(scene: Mapping[str, Any]) -> tuple[float, float]:
    start = scene.get("start", scene.get("start_s", scene.get("start_time", 0)))
    end = scene.get("end", scene.get("end_s", scene.get("end_time", 0)))
    return float(start), float(end)


def _duration_or_zero(video_path: Path, prober: Callable[[Path], dict[str, Any]]) -> float:
    try:
        return float(prober(video_path).get("duration_s", 0.0))
    except Exception:  # noqa: BLE001 - fallback lets caller skip zero-duration full-source candidate
        return 0.0
