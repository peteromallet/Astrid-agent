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
        raise AssertionError("canonical inspection must not read the legacy timeline document")

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


class _SelectedNativeTransport(_NativeTransport):
    def current_project(self):
        return {"project": {"project_id": "project-1", "slug": "demo"}}

    def get_project(self, project):
        assert project == "project-1"
        return {
            "project_id": "project-1",
            "slug": "demo",
            "metadata": {"default_timeline_id": "main"},
        }


def test_remote_show_uses_runtime_current_project_and_project_default() -> None:
    transport = _SelectedNativeTransport()
    result = RemoteTimelines(transport).show(None, None)
    assert result.ok
    assert transport.inspect_calls[0][0:2] == ("project-1", "tl-1")


def test_remote_list_without_selection_returns_recovery_error() -> None:
    class _NoSelection(_SelectedNativeTransport):
        def current_project(self):
            return {"selection": None}

    result = RemoteTimelines(_NoSelection()).list(None)
    assert not result.ok
    assert result.error.code == "not_found"
    assert result.error.details["next_action"] == "astrid projects select <project>"


def test_remote_default_timeline_must_belong_to_selected_project() -> None:
    class _CrossProjectDefault(_SelectedNativeTransport):
        def get_project(self, project):
            row = super().get_project(project)
            row["metadata"] = {"default_timeline_id": "other-project-timeline"}
            return row

    result = RemoteTimelines(_CrossProjectDefault()).show(None, None)
    assert not result.ok
    assert result.error.code == "not_found"
    assert result.error.details == {
        "project": "project-1",
        "ref": "other-project-timeline",
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


def test_remote_show_is_canonical_current_head_alias() -> None:
    transport = _NativeTransport()
    result = RemoteTimelines(transport).show("project-1", "main")
    assert result.ok
    assert result.data["summary"]["revision_id"] == "rev-7"
    assert transport.inspect_calls[0][1] == "tl-1"


def test_remote_open_composition_forwards_native_cursor_without_legacy_read() -> None:
    transport = _NativeTransport()
    result = RemoteTimelines(transport).open_composition(
        "project-1", "main", cursor="cursor-1", limit=3
    )
    assert result.ok
    assert transport.inspect_calls[0][2]["cursor"] == "cursor-1"
    assert transport.inspect_calls[0][2]["limit"] == 3


def test_native_projection_keeps_parent_effect_targets_without_shot_identity() -> None:
    transport = _NativeTransport()

    original_inspect = transport.inspect_timeline

    def inspect(project, timeline, *, options):  # noqa: ANN001
        data = original_inspect(project, timeline, options=options)
        data["selected"] = []
        data["selected_parent_clips"] = [{
            "clip_id": "effect-1",
            "track_id": "effects",
            "clip_type": "element",
            "target_kind": "parent_clip",
            "element_ref": {"id": "blur", "kind": "effect"},
            "parameters": {"amount": 0.5},
        }]
        return data

    transport.inspect_timeline = inspect
    result = RemoteTimelines(transport).open_composition("project-1", "main")
    assert result.ok
    assert result.data["targets"] == [{
        "kind": "parent_clip",
        "target_kind": "parent_clip",
        "timeline_id": "tl-1",
        "clip_id": "effect-1",
        "track_id": "effects",
        "element_ref": {"id": "blur", "kind": "effect"},
        "addressable": True,
    }]
    assert result.data["clips"][0]["target_kind"] == "parent_clip"


def test_remote_visualize_creates_runtime_owned_view_without_executor() -> None:
    transport = _NativeTransport()
    result = RemoteTimelines(transport).visualize(
        "project-1", "main", occurrence="occ-1", neighbors=1, formats=("md", "png")
    )
    assert isinstance(result, DomainResult)
    assert not result.ok
    assert result.error.code == "render_required"
    assert result.error.details["input_only_available"] is True
    assert result.error.details["next_actions"]
    assert transport.view_calls == []


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
