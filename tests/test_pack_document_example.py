"""Executable pack-author journey for runtime-owned project documents.

The example deliberately uses only the public AstridClient document facade.
The in-memory transport below stands in for the runtime boundary; it does not
access Astrid's local project files or private database.
"""

from __future__ import annotations

import copy

import pytest

from astrid.sdk import AstridClient
from astrid.sdk.documents import stable_document_id
from astrid.sdk.remote import RemoteAstridClient
from astrid.sdk.workspace_client import WorkspaceClientError


KIND = "example_pack.project_brief"


def consume_project_brief(client: AstridClient, project_id: str, markdown: str) -> dict:
    """Find or create a namespaced brief, update it, then consume its current revision."""
    document_id = stable_document_id(project_id, KIND, name="default")

    listed = client.documents.list(project=project_id, kind=KIND)
    if not listed.ok:
        raise RuntimeError(listed.error.message)

    matches = listed.data
    if matches:
        document = matches[0]
        if (
            document.get("project_id") != project_id
            or document.get("document_id") != document_id
            or document.get("kind") != KIND
        ):
            raise RuntimeError("Runtime returned a project brief with mismatched identity.")
    else:
        created = client.documents.create(
            project=project_id,
            document_id=document_id,
            kind=KIND,
            content=markdown,
            idempotency_key=f"example-pack-create-brief:{project_id}",
        )
        if not created.ok:
            raise RuntimeError(created.error.message)
        document = created.data

    # Pin the write to the version returned by list/create. A concurrent edit
    # must surface as a conflict instead of being silently treated as current.
    if document["content"] != markdown:
        updated = client.documents.update(
            document["document_id"],
            project=project_id,
            content=markdown,
            expected_version=document["version"],
            idempotency_key=(
                f"example-pack-update-brief:{project_id}:"
                f"{document['document_id']}:{document['version']}"
            ),
        )
        if not updated.ok:
            raise RuntimeError(updated.error.message)

    # Read by explicit runtime project and document identity immediately before
    # consuming content, so provenance names the exact revision used.
    current = client.documents.show(document_id, project=project_id)
    if not current.ok:
        raise RuntimeError(current.error.message)
    consumed = current.data
    if (
        consumed.get("project_id") != project_id
        or consumed.get("document_id") != document_id
        or consumed.get("kind") != KIND
        or not isinstance(consumed.get("content"), str)
    ):
        raise RuntimeError("Runtime returned a project brief with mismatched identity or format.")

    return {
        "project_id": consumed["project_id"],
        "document_id": consumed["document_id"],
        "version": consumed["version"],
        "content": consumed["content"],
        "provenance": {
            "project_id": consumed["project_id"],
            "document_id": consumed["document_id"],
            "version": consumed["version"],
        },
    }


class InMemoryRuntime:
    """Small workspace-contract fixture; all document state lives at runtime."""

    def __init__(self) -> None:
        self.endpoint = "http://127.0.0.1:4317"
        self._last_handshake = {"realm_id": "fixture-realm", "actor_id": "fixture-actor"}
        self.selected_project = "project:other"
        self.projects = {"project:target", "project:other"}
        self.documents: dict[str, dict] = {}
        self.next_list_race: tuple[str, str] | None = None
        self.calls: list[tuple] = []

    def get_project(self, project: str) -> dict:
        if project not in self.projects:
            raise WorkspaceClientError(404, "not_found", "Project not found", {})
        self.calls.append(("get_project", project))
        return {"project_id": project}

    def current_project(self) -> dict:
        self.calls.append(("current_project", self.selected_project))
        return {"project": {"project_id": self.selected_project}}

    def list_documents(
        self, project: str, *, cursor: str | None, limit: int, kind: str | None = None
    ) -> tuple[list[dict], None]:
        self.calls.append(("list_documents", project, kind))
        rows = [
            copy.deepcopy(row)
            for row in self.documents.values()
            if row["project_id"] == project and (kind is None or row["kind"] == kind)
        ]
        if self.next_list_race is not None:
            document_id, content = self.next_list_race
            self.next_list_race = None
            row = self.documents[document_id]
            row.update(content=content, version=row["version"] + 1)
        return rows[:limit], None

    def create_document(
        self, project: str, document_id: str, kind: str, content, *, idempotency_key: str
    ) -> dict:
        self.calls.append(("create_document", project, document_id, idempotency_key))
        if project not in self.projects:
            raise WorkspaceClientError(404, "not_found", "Project not found", {})
        if document_id in self.documents:
            raise WorkspaceClientError(409, "already_exists", "Document already exists", {})
        row = {
            "project_id": project,
            "document_id": document_id,
            "kind": kind,
            "content": content,
            "version": 1,
        }
        self.documents[document_id] = copy.deepcopy(row)
        return {"data": copy.deepcopy(row), "receipt": self._receipt("document.create", project, document_id, idempotency_key, row["version"], row)}

    def get_document(self, project: str, document_id: str) -> dict:
        self.calls.append(("get_document", project, document_id))
        row = self.documents.get(document_id)
        if row is None or row["project_id"] != project:
            raise WorkspaceClientError(404, "not_found", "Document not found", {})
        return copy.deepcopy(row)

    def update_document(
        self,
        project: str,
        document_id: str,
        *,
        content,
        expected_version: int,
        idempotency_key: str,
    ) -> dict:
        self.calls.append(
            ("update_document", project, document_id, expected_version, idempotency_key)
        )
        row = self.documents.get(document_id)
        if row is None or row["project_id"] != project:
            raise WorkspaceClientError(404, "not_found", "Document not found", {})
        if row["version"] != expected_version:
            raise WorkspaceClientError(409, "version_conflict", "Document version changed", {})
        row.update(content=content, version=expected_version + 1)
        return {"data": copy.deepcopy(row), "receipt": self._receipt("document.update", project, document_id, idempotency_key, row["version"], row)}

    @staticmethod
    def _receipt(command_kind: str, project_id: str, document_id: str, key: str, version: int, result: dict) -> dict:
        return {
            "receipt_id": f"receipt:{key}",
            "command_kind": command_kind,
            "idempotency_key": key,
            "request_hash": "a" * 64,
            "project_id": project_id,
            "project_seq": [version, version],
            "event_ids": [f"event:{key}"],
            "result": copy.deepcopy(result),
            "created_at": "2026-10-02T00:00:00Z",
        }


@pytest.fixture
def runtime_client() -> tuple[AstridClient, InMemoryRuntime]:
    runtime = InMemoryRuntime()
    return AstridClient(remote=RemoteAstridClient(runtime)), runtime


def test_pack_journey_pins_project_kind_and_consumed_version(runtime_client) -> None:
    client, runtime = runtime_client
    project_id = "project:target"
    first_markdown = "# Project brief\n\nFirst draft.\n"
    document_id = stable_document_id(project_id, KIND, name="default")

    first = consume_project_brief(client, project_id, first_markdown)
    assert first["project_id"] == project_id
    assert first["document_id"] == document_id
    assert first["version"] == 1
    assert first["content"] == first_markdown
    assert first["provenance"] == {
        "project_id": project_id,
        "document_id": document_id,
        "version": 1,
    }
    assert ("create_document", project_id, document_id, f"example-pack-create-brief:{project_id}") in runtime.calls
    assert all(call[1] == project_id for call in runtime.calls if call[0] in {"get_project", "list_documents", "create_document", "get_document"})
    assert all(call[2] == KIND for call in runtime.calls if call[0] == "list_documents")
    assert runtime.selected_project == "project:other"

    # A concurrent runtime edit after list makes that result stale. The SDK
    # update carries the listed version and returns a conflict; it is never
    # returned as consumed content. A fresh invocation updates and shows v2.
    runtime.next_list_race = (document_id, "# Project brief\n\nConcurrent edit.\n")
    with pytest.raises(RuntimeError, match="Document version changed"):
        consume_project_brief(client, project_id, "# Project brief\n\nPack update.\n")
    assert runtime.documents[document_id]["version"] == 2

    final_markdown = "# Project brief\n\nPack update.\n"
    final = consume_project_brief(client, project_id, final_markdown)
    assert final["content"] == final_markdown
    assert final["version"] == runtime.documents[document_id]["version"] == 3
    assert final["provenance"] == {
        "project_id": project_id,
        "document_id": document_id,
        "version": 3,
    }
    assert any(call[0] == "update_document" and call[3] == 1 for call in runtime.calls)
    assert any(call[0] == "get_document" and call[1:] == (project_id, document_id) for call in runtime.calls)
