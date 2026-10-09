"""Read one canonical timeline through the runtime SDK, read-only.

The steps mirror ``timeline_document.py checkout``: resolve the project and the
timeline ref through public inspection, pin its current parent head, then open
the exact parent, shot and internal-timeline closure. Nothing is published.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def _typed(result: Any) -> Any:
    if not result.ok:
        raise RuntimeError(str(result.error))
    return result.data


def _data(response: Any) -> Any:
    if isinstance(response, Mapping):
        return response.get("data", response)
    return response


def _workspace() -> Any:
    from astrid.sdk.autobootstrap import ensure_runtime
    from astrid.sdk.workspace_client import WorkspaceClient, resolve_runtime_connection

    receipt = ensure_runtime(start_pack_host=False)
    endpoint, token = resolve_runtime_connection(receipt["endpoint"], Path(receipt["credential_file"]))
    return WorkspaceClient(endpoint, token)


def read_bundle(project_ref: str, timeline_ref: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (authoring bundle, identity) for the current head of one timeline."""
    from astrid.sdk import AstridClient
    from astrid.sdk.authoring_bundle import open_authoring_bundle

    with AstridClient.open_from_launcher(start_pack_host=False) as client:
        project = _typed(client.projects.show(project_ref))
        project_id = project.get("project_id") or project["id"]
        inspection = _typed(client.timelines.open_composition(project_id, timeline_ref))

    native = inspection.get("native_inspection") or {}
    timeline_id = native.get("timeline_id")
    head = (inspection.get("summary") or {}).get("head_revision_id") or native.get("head_revision_id")
    if not isinstance(timeline_id, str) or not isinstance(head, str) or not timeline_id or not head:
        raise RuntimeError("timeline inspection did not return a timeline id and a current head")

    transport = _workspace()
    parent = _data(transport.get_project_parent_composition_revision(project_id, timeline_id, head))
    payload = parent.get("payload") or {}
    shots: dict[str, Any] = {}
    internals: dict[str, Any] = {}
    for occurrence in payload.get("occurrences", []):
        shot_revision = occurrence.get("shot_revision_id") or occurrence["revision_id"]
        if shot_revision not in shots:
            shots[shot_revision] = _data(
                transport.get_project_shot_revision(project_id, occurrence["shot_id"], shot_revision)
            )
        shot = shots[shot_revision]
        internal_revision = (
            shot.get("internal_timeline_revision_id") or shot["payload"]["internal_timeline_revision_id"]
        )
        if internal_revision not in internals:
            internals[internal_revision] = _data(
                transport.get_project_timeline_revision(project_id, timeline_id, internal_revision)
            )
    bundle = open_authoring_bundle(
        parent,
        shot_revisions=list(shots.values()),
        internal_timeline_revisions=list(internals.values()),
    )
    identity = {"project_id": str(project_id), "timeline_id": timeline_id, "head_revision_id": head}
    return bundle, identity
