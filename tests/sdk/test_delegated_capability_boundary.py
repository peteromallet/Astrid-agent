from __future__ import annotations

import pytest

from astrid.sdk.remote import RemoteAstridClient
from astrid.sdk.workspace_client import DelegatedWorkspaceClient, WorkspaceClientError


_DIGEST = "sha256:" + "a" * 64


def _delegated_client(rows: list[dict[str, object]]) -> tuple[DelegatedWorkspaceClient, dict[str, object]]:
    client = DelegatedWorkspaceClient(
        "http://127.0.0.1:1",
        "worker-token",
        authority="child-authority",
        capability_rows=rows,
    )
    captured: dict[str, object] = {}

    def admit_delegated_task(*, authority: str, task: dict[str, object], idempotency_key: str):
        captured.update(authority=authority, task=task, idempotency_key=idempotency_key)
        return {"data": {"task_id": "child-task"}, "receipt": None}

    client.admit_delegated_task = admit_delegated_task  # type: ignore[method-assign]
    return client, captured


def test_delegated_policy_projects_to_catalog_shape_and_admits_child() -> None:
    client, captured = _delegated_client([
        {"capability_id": "h3_av.prepare", "capability_digest": _DIGEST}
    ])

    assert client.list_capabilities() == [[
        {"capability_id": "h3_av.prepare", "definition_digest": _DIGEST}
    ], None]

    result = RemoteAstridClient(client).tasks.create(
        project_id=None,
        capability="h3_av.prepare",
        spec={},
        input_manifest=[],
        idempotency_key="child-admission",
    )

    assert result.ok
    assert captured["authority"] == "child-authority"
    assert captured["task"]["capability_digest"] == _DIGEST  # type: ignore[index]


def test_delegated_admission_refreshes_authority_against_parent_lease() -> None:
    client = DelegatedWorkspaceClient(
        "http://127.0.0.1:1",
        "worker-token",
        authority="stale-authority",
        capability_rows=[
            {"capability_id": "h3_av.prepare", "capability_digest": _DIGEST}
        ],
        parent_context={
            "attempt_id": "parent-attempt",
            "lease_id": "parent-lease",
            "fence": 3,
            "runtime_epoch": 85,
        },
    )
    refreshes: list[dict[str, object]] = []

    def call(operation: str, *args: object, **kwargs: object):
        refreshes.append({"operation": operation, "args": args, **kwargs})
        assert operation == "issue_child_authority"
        return {"authority": f"fresh-{len(refreshes)}", "expires_at": "2099-01-01T00:00:00Z"}

    client._call_generated = call  # type: ignore[method-assign]
    seen: list[str] = []

    def admit(*, authority: str, task: dict[str, object], idempotency_key: str):
        seen.append(authority)
        return {"data": {"task_id": "child-task"}, "receipt": None}

    client.admit_delegated_task = admit  # type: ignore[method-assign]
    result = RemoteAstridClient(client).tasks.create(
        project_id=None,
        capability="h3_av.prepare",
        spec={},
        input_manifest=[],
        idempotency_key="child-refresh",
    )

    assert result.ok
    assert seen == ["fresh-1"]
    assert refreshes[0]["args"] == ("parent-attempt",)
    assert refreshes[0]["lease_id"] == "parent-lease"
    assert refreshes[0]["fence"] == 3
    assert refreshes[0]["runtime_epoch"] == 85


def test_parent_context_requires_runtime_epoch() -> None:
    with pytest.raises(ValueError, match="parent lease context is incomplete"):
        DelegatedWorkspaceClient(
            "http://127.0.0.1:1",
            "worker-token",
            authority="authority",
            capability_rows=[
                {"capability_id": "h3_av.prepare", "capability_digest": _DIGEST}
            ],
            parent_context={
                "attempt_id": "parent-attempt",
                "lease_id": "parent-lease",
                "fence": 3,
            },
        )


def test_delegated_admission_retries_same_request_after_authority_race() -> None:
    client = DelegatedWorkspaceClient(
        "http://127.0.0.1:1",
        "worker-token",
        authority="stale-authority",
        capability_rows=[
            {"capability_id": "h3_av.prepare", "capability_digest": _DIGEST}
        ],
        parent_context={
            "attempt_id": "parent-attempt",
            "lease_id": "parent-lease",
            "fence": 3,
            "runtime_epoch": 85,
        },
    )
    refreshes = 0

    def call(operation: str, *args: object, **kwargs: object):
        nonlocal refreshes
        refreshes += 1
        assert operation == "issue_child_authority"
        return {"authority": f"fresh-{refreshes}"}

    client._call_generated = call  # type: ignore[method-assign]
    calls: list[tuple[str, dict[str, object], str]] = []

    def admit(*, authority: str, task: dict[str, object], idempotency_key: str):
        calls.append((authority, task, idempotency_key))
        if len(calls) == 1:
            raise WorkspaceClientError(401, "unauthorized", "child authority no longer matches parent attempt")
        return {"data": {"task_id": "child-task"}, "receipt": None}

    client.admit_delegated_task = admit  # type: ignore[method-assign]
    result = RemoteAstridClient(client).tasks.create(
        project_id=None,
        capability="h3_av.prepare",
        spec={},
        input_manifest=[],
        idempotency_key="child-race",
    )

    assert result.ok
    assert [item[0] for item in calls] == ["fresh-1", "fresh-2"]
    assert calls[0][1] == calls[1][1]
    assert calls[0][2] == calls[1][2] == "child-race"


def test_generated_staged_child_key_is_scoped_to_parent_attempt() -> None:
    def make(attempt_id: str) -> dict[str, object]:
        client, captured = _delegated_client([
            {"capability_id": "h3_av.prepare", "capability_digest": _DIGEST}
        ])
        client._parent_context = {  # type: ignore[attr-defined]
            "attempt_id": attempt_id,
            "lease_id": "parent-lease",
            "fence": 3,
            "runtime_epoch": 85,
        }
        def admit_task(**kwargs: object):
            captured.update(kwargs)
            return {"data": {"task_id": "child-task"}, "receipt": None}

        client.admit_task = admit_task  # type: ignore[method-assign]
        result = RemoteAstridClient(client).tasks.create(
            project_id=None,
            capability="h3_av.prepare",
            spec={},
            input_manifest=[],
            stage="prepare",
            input_refs=[],
        )
        assert result.ok
        return captured

    first = make("parent-attempt-1")
    replay = make("parent-attempt-1")
    retry = make("parent-attempt-2")
    assert first["idempotency_key"] == replay["idempotency_key"]
    assert first["idempotency_key"] != retry["idempotency_key"]


def test_delegated_policy_rejects_conflicting_digest_names() -> None:
    with pytest.raises(ValueError, match="conflicting digests"):
        _delegated_client([
            {
                "capability_id": "h3_av.prepare",
                "capability_digest": _DIGEST,
                "definition_digest": "sha256:" + "b" * 64,
            }
        ])


@pytest.mark.parametrize("digest", ["", "sha256:bad", "sha256:" + "A" * 64])
def test_delegated_policy_rejects_invalid_digest(digest: str) -> None:
    with pytest.raises(ValueError, match="invalid digest"):
        _delegated_client([
            {"capability_id": "h3_av.prepare", "capability_digest": digest}
        ])
