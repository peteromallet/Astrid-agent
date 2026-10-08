"""Narrow parity checks at the public timelines show/visualize boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace

from astrid.packs.timeline.cli import (
    _cmd_show,
    _cmd_visualize,
    _visualization_show_range,
    build_parser,
)
from astrid.sdk.contracts import DomainResult
from astrid.sdk.results import InvocationResult


class _Client:
    def __init__(self) -> None:
        self.visualize_inputs = None
        self.timelines = SimpleNamespace(
            open_composition=self.open_composition,
            visualize=self.visualize,
        )

    def open_composition(self, project, ref, **kwargs):  # noqa: ANN001
        occurrence = kwargs.get("occurrence")
        clip = {
            "clip_id": "clip-b",
            "track_id": "picture",
            "occurrence_id": "occ-b",
            "shot_id": "shot-b",
        }
        return DomainResult.success(
            {
                "kind": "timeline-inspection",
                "summary": {
                    "authority": "canonical_head",
                    "revision_id": "head-1",
                    "head_revision_id": "head-1",
                    "is_current_head": True,
                },
                "query": {"occurrence": occurrence},
                "targets": [{
                    "kind": "clip",
                    "timeline_id": "tl-1",
                    "occurrence_id": "occ-b",
                    "clip_id": "clip-b",
                    "shot_id": "shot-b",
                    "addressable": True,
                }] if occurrence else [],
                "clips": [clip] if occurrence else [],
            }
        )

    def visualize(self, project, ref, **kwargs):  # noqa: ANN001
        self.visualize_inputs = kwargs["options"]
        return DomainResult.success({"kind": "timeline-view", "inspection": {"revision_id": "head-1"}})

    def invoke_result(self, capability_id, **kwargs):  # noqa: ANN001
        self.visualize_inputs = kwargs["inputs"]
        return InvocationResult(
            capability_id=capability_id,
            capability_type="executor",
            native_kind="executor",
            ok=True,
            run_id="run-1",
            kernel_run_id=None,
            kernel_task_id=None,
            kernel_attempt_id=None,
            manifest_path=None,
            outputs={},
        )


def test_show_and_visualize_preserve_one_normalized_occurrence_target(capsys) -> None:
    client = _Client()
    parser = build_parser(client)

    show_args = parser.parse_args(
        ["show", "--project", "demo", "main", "--summary", "--occurrence", "occ-b", "--json"]
    )
    assert _cmd_show(show_args) == 0
    shown = json.loads(capsys.readouterr().out)["data"]
    assert shown["query"]["occurrence"] == "occ-b"
    assert shown["targets"] == [
        {
            "kind": "clip",
            "timeline_id": "tl-1",
            "occurrence_id": "occ-b",
            "clip_id": "clip-b",
            "shot_id": "shot-b",
            "addressable": True,
        }
    ]
    show_to_visualize = shown["navigation"]["commands"]["visualize"]
    assert "timelines visualize" in show_to_visualize
    assert "--revision-id head-1" in show_to_visualize
    assert "--occurrence occ-b" in show_to_visualize

    visualize_args = parser.parse_args(
        ["visualize", "main", "--project", "demo", "--occurrence", "occ-b"]
    )
    assert _cmd_visualize(visualize_args) == 0
    visualized = json.loads(capsys.readouterr().out)["data"]["outputs"]
    assert client.visualize_inputs["occurrence"] == shown["query"]["occurrence"]
    assert "timelines show" in visualized["navigation"]["commands"]["show"]
    assert "--revision-id head-1" in visualized["navigation"]["commands"]["show"]


def test_show_visualize_round_trip_keeps_saved_revision_after_newer_head(capsys) -> None:
    client = _Client()
    parser = build_parser(client)

    show_args = parser.parse_args(
        ["show", "--project", "demo", "main", "--revision-id", "saved-revision", "--range", "2..4", "--json"]
    )
    assert _cmd_show(show_args) == 0
    shown = json.loads(capsys.readouterr().out)["data"]
    assert "--revision-id saved-revision" in shown["navigation"]["commands"]["visualize"]
    assert "--range 2..4" in shown["navigation"]["commands"]["visualize"]

    visualize_args = parser.parse_args(
        ["visualize", "main", "--project", "demo", "--revision-id", "saved-revision", "--range", "2..4"]
    )
    assert _cmd_visualize(visualize_args) == 0
    visualized = json.loads(capsys.readouterr().out)["data"]["outputs"]
    assert "--revision-id saved-revision" in visualized["navigation"]["commands"]["show"]
    assert "--range 2..4" in visualized["navigation"]["commands"]["show"]


def test_visualize_show_navigation_projects_frame_and_timestamp_to_ranges(capsys) -> None:
    client = _Client()
    parser = build_parser(client)

    frame_args = parser.parse_args(
        ["visualize", "main", "--project", "demo", "--frame", "1"]
    )
    assert _cmd_visualize(frame_args) == 0
    frame_outputs = json.loads(capsys.readouterr().out)["data"]["outputs"]
    assert "--range 1/30..1/15" in frame_outputs["navigation"]["commands"]["show"]

    at_args = parser.parse_args(
        ["visualize", "main", "--project", "demo", "--at", "4", "--context", "1"]
    )
    assert _cmd_visualize(at_args) == 0
    at_outputs = json.loads(capsys.readouterr().out)["data"]["outputs"]
    assert "--range 3..5" in at_outputs["navigation"]["commands"]["show"]


def test_visualize_show_navigation_reads_fps_from_frame_index(tmp_path) -> None:
    frame_index = tmp_path / "frame-index.json"
    frame_index.write_text(
        json.dumps({"provenance": {"fps_rational": [24, 1]}}),
        encoding="utf-8",
    )
    assert _visualization_show_range(
        {"frame": 1}, outputs={"frame_index": str(frame_index)}
    ) == "1/24..1/12"


def test_plain_show_returns_the_canonical_inspection_shape(capsys) -> None:
    client = _Client()
    parser = build_parser(client)
    args = parser.parse_args(["show", "--project", "demo", "main", "--json"])
    assert _cmd_show(args) == 0
    shown = json.loads(capsys.readouterr().out)["data"]
    assert shown["kind"] == "timeline-inspection"
    assert shown["summary"]["authority"] == "canonical_head"


def test_show_prefers_shared_open_composition_adapter_when_available(capsys) -> None:
    class OpenClient(_Client):
        def __init__(self) -> None:
            super().__init__()
            self.opened = None
            self.timelines.open_composition = self.open_composition

        def open_composition(self, project, ref, **kwargs):  # noqa: ANN001
            self.opened = (project, ref, kwargs)
            return DomainResult.success({
                "kind": "timeline-inspection", "summary": {"authority": "canonical_head"},
                "query": {"occurrence": kwargs["occurrence"]}, "targets": [], "clips": [],
            })

    client = OpenClient()
    parser = build_parser(client)
    args = parser.parse_args(["show", "--project", "demo", "main", "--summary", "--occurrence", "occ-b", "--json"])
    assert _cmd_show(args) == 0
    assert client.opened[0:2] == ("demo", "main")
    assert client.opened[2]["occurrence"] == "occ-b"
    assert json.loads(capsys.readouterr().out)["data"]["kind"] == "timeline-inspection"


def test_show_fails_closed_without_canonical_adapter(capsys) -> None:
    class LegacyOnly:
        def __init__(self) -> None:
            self.timelines = SimpleNamespace(
                show=lambda *_args: DomainResult.success({"config": {"clips": []}})
            )

    parser = build_parser(LegacyOnly())
    args = parser.parse_args(["show", "--project", "demo", "main", "--json"])
    assert _cmd_show(args) == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "unavailable"
