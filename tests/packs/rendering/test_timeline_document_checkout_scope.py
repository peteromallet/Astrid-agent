from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "astrid/packs/rendering/skill/scripts/timeline_document.py"
SPEC = importlib.util.spec_from_file_location("timeline_document_checkout_scope", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class TimelineReader:
    def __init__(self, result):
        self.result = result
        self.args = None

    def open_composition(self, project_id, timeline_ref):
        self.args = (project_id, timeline_ref)
        return SimpleNamespace(ok=True, data=self.result)


def test_checkout_scope_uses_canonical_inspection_and_pins_returned_id():
    timelines = TimelineReader(
        {
            "summary": {"head_revision_id": "parent-head"},
            "native_inspection": {
                "project_id": "project-id",
                "timeline_id": "timeline-id",
                "head_revision_id": "parent-head",
            },
        }
    )
    client = SimpleNamespace(timelines=timelines)

    timeline_id, inspection = MODULE.resolve_timeline_scope(client, "project-id", "timeline-slug")

    assert timelines.args == ("project-id", "timeline-slug")
    assert timeline_id == "timeline-id"
    assert inspection["summary"]["head_revision_id"] == "parent-head"


def test_checkout_scope_fails_closed_if_inspection_does_not_confirm_project():
    timelines = TimelineReader(
        {
            "summary": {"head_revision_id": "parent-head"},
            "native_inspection": {
                "project_id": "other-project",
                "timeline_id": "timeline-id",
                "head_revision_id": "parent-head",
            },
        }
    )
    client = SimpleNamespace(timelines=timelines)

    with pytest.raises(RuntimeError, match="requested project"):
        MODULE.resolve_timeline_scope(client, "project-id", "timeline-slug")
