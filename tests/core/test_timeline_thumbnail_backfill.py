from __future__ import annotations

from typing import Any

import pytest

from astrid.core.execution import thumbnail_backfill as backfill

SOURCE = "sha256:" + "a" * 64


def _candidate(*, missing: bool = False, nonvisual: bool = False) -> dict[str, Any]:
    placements = []
    shots = {}
    for index in range(5):
        occurrence_id = f"occ-{index}"
        shot_id = f"shot-{index}"
        placements.append({"occurrence_id": occurrence_id, "shot_id": shot_id, "duration_ms": 1000})
        clip: dict[str, Any] = {
            "id": f"clip-{index}", "clipType": "media", "track": "picture",
            "at": 0, "from": 10 + index, "to": 20 + index, "speed": 2,
            "asset": "source",
        }
        assets = {} if missing and index == 4 else {
            "source": {"media_id": SOURCE, "type": "audio" if nonvisual and index == 4 else "video"}
        }
        shots[shot_id] = {"internal_timeline": {
            "tracks": [{"id": "picture", "kind": "visual"}],
            "clips": [clip],
            "registry": {"assets": assets},
        }}
    return {"placements": placements, "shots": shots}


class FakeRuntime:
    def __init__(self):
        self.objects = {SOURCE: b"video bytes"}
        self.associations: dict[tuple[str, float], dict[str, Any]] = {}
        self.fetches: list[str] = []
        self.uploads: list[dict[str, Any]] = []
        self.ensures: list[dict[str, Any]] = []
        self.fail_source_read = False

    def get_project(self, project):
        return {"project_id": project}

    def get_source_frame_thumbnail(self, project_id, source_object_id, source_time_seconds, *, recipe_version=1):
        assert project_id == "p1" and source_object_id == SOURCE and recipe_version == 1
        return self.associations.get((source_object_id, source_time_seconds))

    def get_object(self, object_id):
        self.fetches.append(object_id)
        if self.fail_source_read:
            raise RuntimeError("Runtime source read failed")
        return {"data": self.objects[object_id]}

    def ingest_project_object(self, project_id, data, *, media_type, filename, idempotency_key):
        self.uploads.append({"project_id": project_id, "data": data, "media_type": media_type, "filename": filename, "key": idempotency_key})
        object_id = "sha256:" + str(len(self.uploads)).zfill(64)
        self.objects[object_id] = data
        return {"data": {"object_id": object_id}}

    def ensure_source_frame_thumbnail(self, project_id, thumbnail, *, idempotency_key):
        self.ensures.append({"project_id": project_id, "thumbnail": thumbnail, "key": idempotency_key})
        key = (thumbnail["source_object_id"], thumbnail["selection"]["source_time_seconds"])
        self.associations[key] = dict(thumbnail)
        return {"data": dict(thumbnail)}


@pytest.fixture
def fake_runtime(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(backfill, "_timeline_closure", lambda *_args: ("t1", "head-1", _candidate()))
    monkeypatch.setattr(backfill, "extract_thumbnail", lambda _source, output, _type, *, source_time_seconds: output.write_bytes(f"jpeg:{source_time_seconds}".encode()))
    return runtime


def test_five_cuts_share_one_fetch_and_use_midpoint_trim_speed(fake_runtime):
    result = backfill.run_timeline_thumbnail_backfill(fake_runtime, project="p1", timeline="t1")

    assert result.attached == 5
    assert result.source_fetches == 1
    assert fake_runtime.fetches == [SOURCE]
    assert [row["source_time_seconds"] for row in result.requests] == [11, 12, 13, 14, 15]
    assert [row["thumbnail"]["selection"]["source_time_seconds"] for row in fake_runtime.ensures] == [11, 12, 13, 14, 15]


def test_dry_run_performs_no_object_reads_or_writes(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(backfill, "_timeline_closure", lambda *_args: ("t1", "head-1", _candidate()))

    result = backfill.run_timeline_thumbnail_backfill(runtime, project="p1", timeline="t1", dry_run=True)

    assert result.dry_run and result.attached == 5
    assert not runtime.fetches and not runtime.uploads and not runtime.ensures


def test_repeat_finds_existing_associations_and_writes_nothing(fake_runtime):
    backfill.run_timeline_thumbnail_backfill(fake_runtime, project="p1", timeline="t1")
    fake_runtime.fetches.clear()
    fake_runtime.uploads.clear()
    fake_runtime.ensures.clear()

    result = backfill.run_timeline_thumbnail_backfill(fake_runtime, project="p1", timeline="t1")

    assert result.already_ready == 5 and result.attached == 0
    assert not fake_runtime.fetches and not fake_runtime.uploads and not fake_runtime.ensures


def test_duplicate_source_time_is_processed_once(monkeypatch):
    runtime = FakeRuntime()
    candidate = _candidate()
    for shot_id in ("shot-1", "shot-2"):
        candidate["shots"][shot_id]["internal_timeline"]["clips"][0]["from"] = 10
    monkeypatch.setattr(backfill, "_timeline_closure", lambda *_args: ("t1", "head-1", candidate))
    monkeypatch.setattr(backfill, "extract_thumbnail", lambda _source, output, _type, *, source_time_seconds: output.write_bytes(b"jpeg"))

    result = backfill.run_timeline_thumbnail_backfill(runtime, project="p1", timeline="t1")

    assert result.scanned == 5
    assert result.attached == 3
    assert len(runtime.ensures) == 3


@pytest.mark.parametrize(("missing", "nonvisual", "expected"), [(True, False, "no_visual_asset_on_primary_track"), (False, True, "non_visual_asset")])
def test_missing_and_nonvisual_assets_are_reported(monkeypatch, missing, nonvisual, expected):
    runtime = FakeRuntime()
    monkeypatch.setattr(backfill, "_timeline_closure", lambda *_args: ("t1", "head-1", _candidate(missing=missing, nonvisual=nonvisual)))

    result = backfill.run_timeline_thumbnail_backfill(runtime, project="p1", timeline="t1", dry_run=True)

    assert any(row["reason"] == expected for row in result.diagnostics)


def test_runtime_failure_is_reported_and_other_requests_continue(fake_runtime):
    fake_runtime.fail_source_read = True

    result = backfill.run_timeline_thumbnail_backfill(fake_runtime, project="p1", timeline="t1")

    assert result.unavailable == 5
    assert result.attached == 0
    assert len(fake_runtime.fetches) == 1
    assert all("Runtime source read failed" in row["reason"] for row in result.diagnostics)


def test_reader_uses_the_timeline_listing_head_and_exact_revision_ids(monkeypatch):
    parent = {"revision_id": "head-9", "payload": {"occurrences": [
        {"shot_id": "shot-1", "shot_revision_id": "shot-rev-2"},
    ]}}
    shot = {"revision_id": "shot-rev-2", "internal_timeline_revision_id": "internal-3"}
    internal = {"revision_id": "internal-3"}
    runtime = FakeRuntime()
    runtime.list_timelines = lambda project_id, **_kwargs: [[{"timeline_id": "t1", "slug": "main", "head_revision_id": "head-9"}], None]
    runtime.get_project_parent_composition_revision = lambda project_id, timeline_id, revision_id: parent
    runtime.get_project_shot_revision = lambda project_id, shot_id, revision_id: shot
    runtime.get_project_timeline_revision = lambda project_id, timeline_id, revision_id: internal
    calls = []

    def open_bundle(parent_revision, *, shot_revisions, internal_timeline_revisions):
        calls.append((parent_revision["revision_id"], shot_revisions[0]["revision_id"], internal_timeline_revisions[0]["revision_id"]))
        return _candidate()

    monkeypatch.setattr(backfill, "open_authoring_bundle", open_bundle)
    timeline_id, head, _candidate_result = backfill._timeline_closure(runtime, "p1", "main")

    assert (timeline_id, head) == ("t1", "head-9")
    assert calls == [("head-9", "shot-rev-2", "internal-3")]


def test_import_helper_attaches_typed_thumbnail_without_creating_generation(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(backfill, "extract_thumbnail", lambda _source, output, _type, *, source_time_seconds: output.write_bytes(b"jpeg"))

    descriptor = backfill.ensure_imported_source_thumbnail(
        runtime,
        project_id="p1",
        source_object_id=SOURCE,
        source_bytes=b"video bytes",
        media_type="video/mp4",
        idempotency_key="import-1",
    )

    assert descriptor is not None
    assert descriptor["selection"] == {"kind": "source_frame", "source_time_seconds": 0.001}
    assert len(runtime.uploads) == 1
    assert len(runtime.ensures) == 1
