from __future__ import annotations

from astrid.sdk.contracts import DomainResult
from astrid.sdk.remote import RemoteTimelines


class _NativeTransport:
    def __init__(self) -> None:
        self.inspect_calls = []
        self.view_calls = []

    def list_timelines(self, project, *, cursor=None, limit=50):
        return [[{"timeline_id": "tl-1", "slug": "main", "parent_revision_id": "rev-7"}], None]

    def get_timeline(self, timeline_id, *, project_id=None):
        return {"timeline_id": timeline_id, "slug": "main", "parent_revision_id": "rev-7"}

    def inspect_timeline(self, project, timeline, *, options):
        self.inspect_calls.append((project, timeline, options))
        return {
            "schema": "runtime.timeline.declared_inputs/v1",
            "project_id": project,
            "timeline_id": timeline,
            "revision_id": "rev-7",
            "snapshot_digest": "sha256:snapshot",
            "selectors": options,
            "selection_status": "selected",
            "target_count": 1,
            "selected": [{
                "role": "target",
                "occurrence": {"occurrence_id": "occ-1", "shot_id": "shot-1"},
                "clips": [{"clip_id": "clip-1", "track_id": "picture"}],
            }],
        }

    def create_timeline_view(self, project, timeline, *, options):
        self.view_calls.append((project, timeline, options))
        return {
            "inspection": {"revision_id": "rev-7", "snapshot_digest": "sha256:snapshot", "selectors": options},
            "evidence_kind": "declared_inputs",
            "render_requested": False,
            "formats": {"md": {"status": "available", "object_id": "sha256:md"}},
            "artifacts": {"md": {"object_id": "sha256:md", "media_type": "text/markdown"}},
        }


def test_remote_open_composition_uses_runtime_inspection_authority() -> None:
    transport = _NativeTransport()
    result = RemoteTimelines(transport).open_composition(
        "project-1", "main", occurrence="occ-1", neighbors=1
    )
    assert result.ok
    assert result.data["summary"]["snapshot_digest"] == "sha256:snapshot"
    assert result.data["targets"][0]["occurrence_id"] == "occ-1"
    assert transport.inspect_calls == [(
        "project-1", "tl-1", {
            "limit": 50, "detail": False, "neighbors": 1, "revision_id": "rev-7",
            "occurrence": "occ-1",
        }
    )]


def test_remote_visualize_creates_runtime_owned_view_without_executor() -> None:
    transport = _NativeTransport()
    result = RemoteTimelines(transport).visualize(
        "project-1", "main", occurrence="occ-1", neighbors=1, formats=("md", "png")
    )
    assert result.ok
    assert result.data["evidence_kind"] == "declared_inputs"
    assert transport.view_calls[0] == (
        "project-1", "tl-1", {
            "limit": 50, "detail": False, "neighbors": 1, "revision_id": "rev-7",
            "occurrence": "occ-1", "formats": ["md", "png"],
        }
    )


def test_native_visualize_rejects_arbitrary_output_path() -> None:
    result = RemoteTimelines(_NativeTransport()).visualize("project-1", "main", out="view.md")
    assert isinstance(result, DomainResult)
    assert not result.ok
    assert result.error.code == "validation_error"


def test_inputs_mode_never_reads_render_history() -> None:
    transport = _NativeTransport()
    result = RemoteTimelines(transport).visualize(
        "project-1", "main", mode="inputs", occurrence="occ-1"
    )
    assert result.ok
    assert result.data["evidence_kind"] == "declared_inputs"
    # _NativeTransport intentionally has no list_project_runs method.  The
    # render-free input route must still work on that minimal native surface.
    assert transport.view_calls


def test_composed_mode_fails_closed_when_no_exact_render_reader_exists() -> None:
    result = RemoteTimelines(_NativeTransport()).visualize(
        "project-1", "main", mode="composed", occurrence="occ-1"
    )
    assert isinstance(result, DomainResult)
    assert not result.ok
    assert result.error.code == "render_required"
    assert result.error.details["timeline_id"] == "tl-1"
    assert result.error.details["next_actions"]


def test_explicit_composed_run_bypasses_current_head_discovery() -> None:
    transport = _NativeTransport()
    calls = []

    def invoke(capability_id, **kwargs):
        calls.append((capability_id, kwargs))
        return DomainResult.success({"selected": kwargs["inputs"]["render_run"]})

    result = RemoteTimelines(transport, invoker=invoke).visualize(
        "project-1", "main", mode="composed",
        options={"render_run": "candidate-render-7"},
    )

    assert result.ok
    assert result.data == {"selected": "candidate-render-7"}
    assert calls[0][0] == "rendering.timeline_visualize"
    assert calls[0][1]["inputs"]["render_run"] == "candidate-render-7"
    # The native transport deliberately has no run-list reader. Explicit
    # selection is validated by immutable exact-run admission downstream.
    assert transport.view_calls == []
