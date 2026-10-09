"""Fail-closed checks for the explicit Astrid runtime handshake boundary."""

from __future__ import annotations

import json
import subprocess
from functools import partial
from pathlib import Path

import pytest

from astrid.sdk import autobootstrap
from astrid.sdk.client import RUNTIME_PAIRING_FIX, AstridClient
from astrid.sdk.exceptions import ServiceUnavailableError
from astrid.sdk.workspace_client import (
    PROTOCOL,
    SCHEMA_DIGEST,
    WorkspaceClient,
    WorkspaceClientError,
    resolve_runtime_connection,
)
from banodoco_workspace_client import ApiError
from banodoco_workspace_client.contract_metadata import SOURCE_COMMIT
from banodoco_workspace_client.generated import WorkspaceClient as GeneratedWorkspaceClient


TARGETED_EXECUTION_BINDING_CAPABILITY = "execution_binding.targeted.v1"


class _Workspace:
    def __init__(self, health: object, handshake: object) -> None:
        self._health = health
        self._handshake = handshake

    def health(self) -> object:
        return self._health

    def handshake(self, *_args: object) -> object:
        return self._handshake


def _health(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "status": "ok",
        "protocol": PROTOCOL,
        "schema_digest": SCHEMA_DIGEST,
        "runtime_epoch": 1,
        "runtime_instance_id": "instance-1",
        "runtime_session_id": "runtime-session-1",
    }
    value.update(overrides)
    return value


def _health_without(field: str) -> dict[str, object]:
    value = _health()
    del value[field]
    return value


def _handshake(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "protocol": PROTOCOL,
        "schema_digest": SCHEMA_DIGEST,
        "session_id": "session-1",
        "actor_id": "actor-1",
        "realm_id": "realm-1",
        "scopes": [
            "projects:read",
            "projects:write",
            "objects:read",
            "objects:write",
            "tasks:read",
            "tasks:write",
        ],
        "capabilities": [TARGETED_EXECUTION_BINDING_CAPABILITY],
    }
    value.update(overrides)
    return value


def _handshake_without(field: str) -> dict[str, object]:
    value = _handshake()
    del value[field]
    return value


@pytest.mark.parametrize(
    "response",
    [
        _health(protocol="workspace.v999"),
        _health(schema_digest="sha256:" + "0" * 64),
        _health(status="degraded"),
        _health(runtime_instance_id=""),
        _health(runtime_session_id=42),
        _health_without("runtime_instance_id"),
        _health_without("runtime_session_id"),
        _health(extra="tampered"),
    ],
)
def test_open_rejects_tampered_health(monkeypatch: pytest.MonkeyPatch, response: object) -> None:
    monkeypatch.setattr(
        "astrid.sdk.workspace_client.WorkspaceClient",
        lambda *_args: _Workspace(response, _handshake()),
    )
    with pytest.raises(ServiceUnavailableError) as error:
        AstridClient.open(
            endpoint="http://127.0.0.1:1",
            credential="token",
            realm_id="realm-1",
            actor_id="actor-1",
            client_name="test",
            client_version="1",
            protocol_version=PROTOCOL,
        )
    assert error.value.details["reason"] in {"protocol_error", "identity_mismatch"}
    if error.value.details["reason"] == "protocol_error":
        assert error.value.details["next_action"] == RUNTIME_PAIRING_FIX
        assert SOURCE_COMMIT in error.value.details["next_action"]
    else:
        assert error.value.details["next_action"] == "banodoco-local up --profile astrid"


@pytest.mark.parametrize(
    "response",
    [
        _handshake(protocol="workspace.v999"),
        _handshake(schema_digest="sha256:" + "0" * 64),
        _handshake(session_id=""),
        _handshake(actor_id="attacker"),
        _handshake(realm_id="wrong-realm"),
        _handshake(scopes=["projects:read"]),
        _handshake(scopes=[
            "projects:read", "projects:write", "objects:read", "objects:write",
            "tasks:read", "tasks:write", "admin",
        ]),
        _handshake(extra="tampered"),
        _handshake_without("capabilities"),
        _handshake(capabilities="execution_binding.targeted.v1"),
        _handshake(capabilities=[]),
        _handshake(capabilities=[""]),
        _handshake(capabilities=[42]),
        _handshake(capabilities=["other.v1"]),
    ],
)
def test_open_rejects_tampered_handshake(monkeypatch: pytest.MonkeyPatch, response: object) -> None:
    monkeypatch.setattr(
        "astrid.sdk.workspace_client.WorkspaceClient",
        lambda *_args: _Workspace(_health(), response),
    )
    with pytest.raises(ServiceUnavailableError) as error:
        AstridClient.open(
            endpoint="http://127.0.0.1:1",
            credential="token",
            realm_id="realm-1",
            actor_id="actor-1",
            client_name="test",
            client_version="1",
            protocol_version=PROTOCOL,
        )
    assert error.value.details["reason"] in {"protocol_error", "identity_mismatch"}


def test_connection_rejects_non_loopback_and_symlinked_credentials(
    tmp_path: Path,
) -> None:
    credential = tmp_path / "credential.json"
    credential.write_text('{"token":"secret"}', encoding="utf-8")
    symlink = tmp_path / "credential-link.json"
    symlink.symlink_to(credential)
    with pytest.raises(WorkspaceClientError, match="loopback"):
        resolve_runtime_connection("https://runtime.example", credential)
    with pytest.raises(WorkspaceClientError, match="symlink"):
        resolve_runtime_connection("http://127.0.0.1:1", symlink)


def test_launcher_rejects_symlinked_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_manifest = tmp_path / "real-manifest.json"
    real_manifest.write_text('{"profile":"astrid"}', encoding="utf-8")
    link = tmp_path / "manifest-link.json"
    link.symlink_to(real_manifest)
    monkeypatch.setenv("BANODOCO_LOCAL_SOURCE_MANIFEST", str(link))
    with pytest.raises(autobootstrap.AutoBootstrapError, match="source manifest is unsafe"):
        autobootstrap.ensure_runtime()


def test_launcher_rejects_non_loopback_advertisement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"profile":"astrid"}', encoding="utf-8")
    monkeypatch.setenv("BANODOCO_LOCAL_SOURCE_MANIFEST", str(manifest))
    monkeypatch.setenv("BANODOCO_LOCAL_LAUNCHER", "/bin/true")
    monkeypatch.setattr(
        autobootstrap.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            '{"status":"started","realm_id":"realm","actor_id":"actor","endpoint":"https://runtime.example"}',
            "",
        ),
    )
    with pytest.raises(autobootstrap.AutoBootstrapError, match="unsafe endpoint"):
        autobootstrap.ensure_runtime()


def test_launcher_credential_environment_is_read_as_a_safe_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credential = tmp_path / "owner.token"
    credential.write_text("secret-token\n", encoding="utf-8")
    monkeypatch.setenv("BANODOCO_RUNTIME_CREDENTIAL", str(credential))
    monkeypatch.setattr(
        autobootstrap,
        "ensure_runtime",
        lambda: {
            "status": "reconnected",
            "realm_id": "realm-1",
            "endpoint": "http://127.0.0.1:1",
            "actor_id": "actor-1",
            "credential_file": "",
        },
    )
    def fake_workspace(endpoint: str, token: str) -> _Workspace:
        assert endpoint == "http://127.0.0.1:1"
        assert token == "secret-token"
        return _Workspace(_health(), _handshake())

    monkeypatch.setattr("astrid.sdk.workspace_client.WorkspaceClient", fake_workspace)
    AstridClient.open_from_launcher(
        client_name="test", client_version="1", protocol_version=PROTOCOL
    )


# --- Failure classification through the real generated decoder -------------
#
# These drive ``AstridClient.open`` with an injected HTTP transport so the
# generated client decodes the wire bodies itself (missing fields raise there).

OLD_RUNTIME_DIGEST = "sha256:" + "e" * 64


def _good_health() -> dict[str, object]:
    return dict(_health())


def _old_runtime_health() -> dict[str, object]:
    # Shape of the bc74a4b Runtime: no runtime_session_id / runtime_instance_id.
    return {"status": "ok", "protocol": PROTOCOL, "runtime_epoch": 1, "schema_digest": OLD_RUNTIME_DIGEST}


def _open_over_transport(monkeypatch: pytest.MonkeyPatch, routes: dict[tuple[str, str], object]) -> AstridClient:
    def transport(method, path, _headers, _body):
        response = routes[(method, path)]
        if isinstance(response, BaseException):
            raise response
        return 200, {}, json.dumps(response).encode()

    monkeypatch.setattr(
        "astrid.sdk.workspace_client.GeneratedWorkspaceClient",
        partial(GeneratedWorkspaceClient, transport=transport),
    )
    return AstridClient.open(
        endpoint="http://127.0.0.1:1",
        credential="token",
        realm_id="realm-1",
        actor_id="actor-1",
        client_name="test",
        client_version="1",
        protocol_version=PROTOCOL,
    )


def test_missing_health_field_is_protocol_error_naming_both_digests(monkeypatch):
    routes = {("GET", "/v1/health"): _old_runtime_health()}
    with pytest.raises(ServiceUnavailableError) as error:
        _open_over_transport(monkeypatch, routes)

    details = error.value.details
    assert details["reason"] == "protocol_error"
    assert details["cause_class"] == "KeyError"
    assert "runtime_session_id" in details["cause_message"]
    assert details["expected_schema_digest"] == SCHEMA_DIGEST
    assert details["expected_runtime_commit"] == SOURCE_COMMIT
    assert details["actual_schema_digest"] == OLD_RUNTIME_DIGEST
    assert details["next_action"] == RUNTIME_PAIRING_FIX
    message = str(error.value)
    assert SCHEMA_DIGEST in message and OLD_RUNTIME_DIGEST in message and SOURCE_COMMIT in message
    assert "rejected the explicit client context" not in message


def test_handshake_digest_mismatch_names_both_digests(monkeypatch):
    routes = {
        ("GET", "/v1/health"): _good_health(),
        ("POST", "/v1/handshake"): _handshake(schema_digest=OLD_RUNTIME_DIGEST),
    }
    with pytest.raises(ServiceUnavailableError) as error:
        _open_over_transport(monkeypatch, routes)

    details = error.value.details
    assert details["reason"] == "protocol_error"
    assert details["field"] == "handshake.schema_digest"
    assert details["actual_schema_digest"] == OLD_RUNTIME_DIGEST
    message = str(error.value)
    assert f"expected {SCHEMA_DIGEST}" in message
    assert OLD_RUNTIME_DIGEST in message
    assert SOURCE_COMMIT in message


def test_health_digest_mismatch_message_names_both_digests(monkeypatch):
    workspace = _Workspace(_health(schema_digest=OLD_RUNTIME_DIGEST), _handshake())
    monkeypatch.setattr("astrid.sdk.workspace_client.WorkspaceClient", lambda *_args: workspace)
    with pytest.raises(ServiceUnavailableError) as error:
        AstridClient.open(
            endpoint="http://127.0.0.1:1",
            credential="token",
            realm_id="realm-1",
            actor_id="actor-1",
            client_name="test",
            client_version="1",
            protocol_version=PROTOCOL,
        )
    assert error.value.details["actual_schema_digest"] == OLD_RUNTIME_DIGEST
    assert SCHEMA_DIGEST in str(error.value) and OLD_RUNTIME_DIGEST in str(error.value)


def test_missing_handshake_field_is_protocol_error_not_transport(monkeypatch):
    handshake = _handshake()
    del handshake["session_id"]
    routes = {("GET", "/v1/health"): _good_health(), ("POST", "/v1/handshake"): handshake}
    with pytest.raises(ServiceUnavailableError) as error:
        _open_over_transport(monkeypatch, routes)

    assert error.value.details["reason"] == "protocol_error"
    assert error.value.details["operation"] == "handshake"
    assert error.value.details["cause_class"] == "KeyError"
    assert "session_id" in error.value.details["cause_message"]


def test_connection_refusal_stays_transport_error_with_original_cause(monkeypatch):
    routes = {("GET", "/v1/health"): ConnectionRefusedError("connection refused by 127.0.0.1:1")}
    with pytest.raises(ServiceUnavailableError) as error:
        _open_over_transport(monkeypatch, routes)

    details = error.value.details
    assert details["reason"] == "transport_error"
    assert details["cause_class"] == "ConnectionRefusedError"
    assert details["cause_message"] == "connection refused by 127.0.0.1:1"
    assert "connection refused by 127.0.0.1:1" in str(error.value)
    assert details["next_action"] == "banodoco-local up --profile astrid"


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_text"),
    [
        (ApiError(401, "unauthorized", "bad token", details={"realm": "r"}), "unauthorized", "bad token"),
        (ApiError(0, "invalid_response", "expected JSON object"), "protocol_error", "expected JSON object"),
        (KeyError("runtime_session_id"), "protocol_error", "runtime_session_id"),
        (TypeError("int() argument must be a string"), "protocol_error", "int() argument"),
        (OSError("socket closed"), "transport_error", "socket closed"),
    ],
)
def test_runtime_failures_are_classified_and_keep_original_cause(error, expected_code, expected_text):
    workspace = WorkspaceClient.__new__(WorkspaceClient)
    workspace._generated = None
    workspace._observed_schema_digest = lambda: None  # no live Runtime in this unit test
    translated = workspace._translate_failure("health", error)

    assert translated.code == expected_code
    assert translated.details["operation"] == "health"
    assert translated.details["cause_class"] == type(error).__name__
    assert expected_text in translated.details["cause_message"]
    assert expected_text in translated.message
    if expected_code == "unauthorized":
        assert translated.details["realm"] == "r"


def test_unknown_operation_is_client_misuse_not_a_runtime_protocol_error():
    workspace = WorkspaceClient.__new__(WorkspaceClient)
    workspace._generated = None
    with pytest.raises(ValueError, match="unknown generated workspace operation"):
        workspace._call_generated("not_a_real_operation")
