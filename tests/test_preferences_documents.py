from __future__ import annotations
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from astrid.sdk.client import AstridClient
from astrid.sdk.documents import stable_document_id
from astrid.sdk.remote import RemoteAstridClient
from astrid.sdk.workspace_client import WorkspaceClientError
from astrid.core.cli.domain_product import run_product_family, command_requires_pack_host


class Runtime:
    def __init__(self):
        self.endpoint = "http://127.0.0.1:1234"
        self._last_handshake = {"realm_id": "realm-a", "actor_id": "actor-a"}
        self.selected = "project-a"
        self.rows = {}
        self.replays = {}
        self.writes = []
        self.lose_response = False
        self.page_calls = []
    def get_project(self, ref):
        return {"project_id": {"slug-a": "project-a"}.get(ref, ref)}
    def current_project(self):
        return {"project": {"project_id": self.selected} if self.selected else None}
    def get_preferences(self, scope, project_id=None):
        owner = self._last_handshake["actor_id"] if scope == "user" else project_id or self.selected
        if not owner:
            raise WorkspaceClientError(404, "not_found", "No project selected", {})
        doc = f"preferences:{scope}:{owner}"
        return copy.deepcopy(self.rows.get(doc, {"scope": scope, "actor_id" if scope == "user" else "project_id": owner, "document_id": doc, "content": "", "version": 0}))
    def update_preferences(self, scope, content, *, expected_version, idempotency_key, project_id=None):
        row = self.get_preferences(scope, project_id)
        return self._update(row, content, expected_version, idempotency_key)
    def _update(self, row, content, expected_version, key):
        doc = row["document_id"]
        submitted = [doc, content, expected_version]
        self.writes.append((doc, content, expected_version, key))
        if key in self.replays:
            body, result = self.replays[key]
            if submitted != body:
                raise WorkspaceClientError(409, "idempotency_conflict", "key changed", {})
            return copy.deepcopy(result)
        if row["version"] != expected_version:
            raise WorkspaceClientError(409, "version_conflict", "Remote document changed", {})
        row.update(content=content, version=expected_version + 1)
        self.rows[doc] = copy.deepcopy(row)
        result = {"data": row, "receipt": None}
        self.replays[key] = [copy.deepcopy(submitted), copy.deepcopy(result)]
        if self.lose_response:
            self.lose_response = False
            raise WorkspaceClientError(0, "transport_error", "lost response", {})
        return result
    def create_document(self, project, doc, kind, content, *, idempotency_key):
        row = {"project_id": project, "document_id": doc, "kind": kind, "content": content, "version": 0}
        return self._update(row, content, 0, idempotency_key)
    def get_document(self, project, doc):
        row = self.rows[doc]
        if row["project_id"] != project:
            raise WorkspaceClientError(404, "not_found", "Not found", {})
        return copy.deepcopy(row)
    def update_document(self, project, doc, *, content, expected_version, idempotency_key):
        return self._update(self.get_document(project, doc), content, expected_version, idempotency_key)
    def list_documents(self, project, *, cursor, limit, kind=None):
        self.page_calls.append([project, cursor, kind])
        rows = [copy.deepcopy(row) for row in self.rows.values() if row.get("project_id") == project and (kind is None or row.get("kind") == kind)]
        offset = int(cursor or 0)
        return [rows[offset:offset + limit], str(offset + limit) if offset + limit < len(rows) else None]


@pytest.fixture
def client():
    runtime = Runtime()
    return AstridClient(remote=RemoteAstridClient(runtime)), runtime


def test_user_only_combined_and_selection(client, capsys):
    c, r = client
    assert c.preferences.view().data["project"]["project_id"] == "project-a"
    r.selected = None
    assert c.preferences.get("user").ok
    assert c.preferences.view().data["project"] is None
    assert not c.preferences.get("project").ok
    assert not r.rows
    assert run_product_family("preferences", ["user", "--json"], client=c) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"ok", "data", "error", "receipt", "idempotency_key"}
    assert payload["data"]["scope"] == "user"
    assert run_product_family("preferences", [], client=c) == 0
    assert "User preferences" in capsys.readouterr().out


@pytest.mark.parametrize("content", ["# Brief\n\nKeep this wording.\n", {"unknown": [None, True, 2.5, {"deep": "🌈"}]}, [1, "two"], None, 3])
def test_document_roundtrip(client, tmp_path, content):
    c, r = client
    created = c.documents.create(project="slug-a", kind="pack.brief", content=content)
    doc = created.data["document_id"]
    path = tmp_path / "brief"
    assert c.documents.checkout(doc, file=path, project="slug-a").ok
    assert c.documents.checkin(path).data["changed"] is False
    new = content + "More\n" if isinstance(content, str) else {"old": content, "new": "preserved"}
    path.write_text(new if isinstance(new, str) else json.dumps(new))
    result = c.documents.checkin(path)
    assert result.ok
    assert c.documents.show(doc).data["content"] == new
    assert c.documents.show(doc).data["kind"] == "pack.brief"


def test_existing_checkout_refused(client, tmp_path):
    c, _ = client
    path = tmp_path / "prefs.md"
    path.write_text("existing")
    assert not c.preferences.checkout(scope="user", file=path).ok
    assert path.read_text() == "existing"
    path.unlink()
    path.with_name(path.name + ".astrid.json").write_text("existing metadata")
    assert not c.preferences.checkout(scope="user", file=path).ok
    assert not path.exists()


@pytest.mark.parametrize("field,value", [("realm_id", "other"), ("actor_id", "other"), ("endpoint", "http://127.0.0.1:9999"), ("project_id", "other")])
def test_identity_mismatch_preserves(client, tmp_path, field, value):
    c, r = client
    path = tmp_path / "prefs.md"
    assert c.preferences.checkout(scope="project", file=path).ok
    sidecar = path.with_name(path.name + ".astrid.json")
    meta = json.loads(sidecar.read_text())
    meta[field] = value
    sidecar.write_text(json.dumps(meta))
    path.write_text("edited")
    assert not c.preferences.checkin(path).ok
    assert path.read_text() == "edited"
    assert not r.writes


def test_project_switch_cannot_retarget(client, tmp_path):
    c, r = client
    path = tmp_path / "prefs.md"
    c.preferences.checkout(scope="project", file=path)
    r.selected = "project-b"
    path.write_text("Use X here")
    assert c.preferences.checkin(path).ok
    assert r.writes[0][0] == "preferences:project:project-a"


def test_lost_response_replays_exact_key_and_changed_body_new_key(client, tmp_path):
    c, r = client
    path = tmp_path / "prefs.md"
    c.preferences.checkout(scope="user", file=path)
    path.write_text("Use X")
    r.lose_response = True
    assert not c.preferences.checkin(path).ok
    key = r.writes[-1][-1]
    assert c.preferences.checkin(path).ok
    assert r.writes[-1][-1] == key
    assert len(r.replays) == 1
    path.write_text("Use Y")
    assert c.preferences.checkin(path).ok
    assert r.writes[-1][-1] != key


def test_commit_then_metadata_write_failure_replays(client, tmp_path, monkeypatch):
    import astrid.sdk.documents as module
    c, r = client
    path = tmp_path / "prefs.md"
    c.preferences.checkout(scope="user", file=path)
    path.write_text("Use X")
    original = module._write_metadata
    calls = []
    def fail_second(*args):
        calls.append(True)
        if len(calls) == 2:
            raise OSError("receipt lost")
        return original(*args)
    monkeypatch.setattr(module, "_write_metadata", fail_second)
    assert not c.preferences.checkin(path).ok
    monkeypatch.setattr(module, "_write_metadata", original)
    assert c.preferences.checkin(path).ok
    assert r.writes[0][-1] == r.writes[1][-1]
    assert len(r.replays) == 1


def test_stale_noop_detected(client, tmp_path):
    c, _ = client
    path = tmp_path / "prefs.md"
    c.preferences.checkout(scope="user", file=path)
    c.preferences.update("user", "remote", expected_version=0)
    result = c.preferences.checkin(path)
    assert not result.ok and result.error.code == "version_conflict"
    assert path.read_text() == ""


@pytest.mark.parametrize("raw", ["bad json", "{}", '{"schema":true}', '[]'])
def test_malformed_sidecar_preserved(client, tmp_path, raw):
    c, _ = client
    path = tmp_path / "prefs.md"
    c.preferences.checkout(scope="user", file=path)
    path.with_name(path.name + ".astrid.json").write_text(raw)
    path.write_text("keep me")
    result = c.preferences.checkin(path)
    assert not result.ok and result.error.details["file"] == str(path)
    assert path.read_text() == "keep me"


def test_editor_cancel_noop_and_failure_preservation(client, monkeypatch):
    import astrid.sdk.documents as module
    c, r = client
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    result = c.preferences.edit(scope="user", editor="fake-editor")
    assert result.ok and not result.data["changed"] and not r.writes
    def fail(args, **kwargs):
        Path(args[-1]).write_text("edited but failed")
        return SimpleNamespace(returncode=2)
    monkeypatch.setattr(module.subprocess, "run", fail)
    result = c.preferences.edit(scope="user", editor="fake-editor")
    assert not result.ok
    assert Path(result.error.details["file"]).read_text() == "edited but failed"
    assert not r.writes


def test_kind_filter_all_pages_and_stable_id(client):
    c, r = client
    for n in range(3):
        c.documents.create(kind="pack.brief", content=str(n))
    c.documents.create(kind="other.brief", content="omit")
    rows = c.documents.list(kind="pack.brief", limit=1)
    assert rows.ok and len(rows.data) == 3
    assert len(r.page_calls) == 3 and all(call[2] == "pack.brief" for call in r.page_calls)
    assert stable_document_id("project-a", "pack.brief") == stable_document_id("project-a", "pack.brief")
    assert stable_document_id("project-a", "pack.brief") != stable_document_id("project-b", "pack.brief")


def test_cli_generic_journey(client, tmp_path, capsys):
    c, _ = client
    source = tmp_path / "source.json"
    source.write_text('{"extra": [1, null]}')
    assert run_product_family("documents", ["create", "--kind", "pack.brief", "--file", str(source), "--format", "json", "--json"], client=c) == 0
    doc = json.loads(capsys.readouterr().out)["data"]["document_id"]
    assert run_product_family("documents", ["list", "--kind", "pack.brief", "--json"], client=c) == 0
    assert len(json.loads(capsys.readouterr().out)["data"]) == 1
    assert run_product_family("documents", ["show", doc, "--json"], client=c) == 0
    assert json.loads(capsys.readouterr().out)["data"]["content"] == {"extra": [1, None]}
    path = tmp_path / "copy.json"
    assert run_product_family("documents", ["checkout", doc, "--file", str(path), "--json"], client=c) == 0
    capsys.readouterr()
    path.write_text('{"extra": [1, null], "new": true}')
    assert run_product_family("documents", ["checkin", str(path), "--json"], client=c) == 0
    assert json.loads(capsys.readouterr().out)["data"]["changed"]


def test_all_entry_routes(client, monkeypatch, capsys):
    import astrid.omp_agent as launcher
    import astrid.core.gateway as gateway
    c, _ = client
    monkeypatch.setattr(AstridClient, "open_from_launcher", lambda **kwargs: c)
    assert launcher.main(["preferences", "user", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["scope"] == "user"
    assert gateway.main(["preferences", "context", "--json"]) == 0  # astrid-tools entry
    assert json.loads(capsys.readouterr().out)["data"]["project"]["project_id"] == "project-a"
    import astrid.__main__ as entry
    assert entry.main(["documents", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == []
    assert not command_requires_pack_host("preferences", [])
    assert not command_requires_pack_host("documents", ["list"])


def test_editor_interrupt_is_cancelled(client, monkeypatch):
    import astrid.sdk.documents as module
    c, r = client
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=130))
    result = c.preferences.edit(scope="user", editor="fake-editor")
    assert result.ok and result.data["cancelled"] and not r.writes


def test_markdown_crlf_noop(client, tmp_path):
    c, r = client
    result = c.documents.create(kind="pack.brief", content="line\r\nnext\r\n")
    path = tmp_path / "brief.md"
    c.documents.checkout(result.data["document_id"], file=path)
    writes = len(r.writes)
    assert c.documents.checkin(path).data["changed"] is False
    assert len(r.writes) == writes


def test_changed_pending_body_uses_new_key_and_preserves_conflict(client, tmp_path):
    c, r = client
    path = tmp_path / "prefs.md"
    c.preferences.checkout(scope="user", file=path)
    path.write_text("first")
    r.lose_response = True
    assert not c.preferences.checkin(path).ok
    first_key = r.writes[-1][-1]
    path.write_text("second")
    result = c.preferences.checkin(path)
    assert not result.ok and result.error.code == "version_conflict"
    assert r.writes[-1][-1] != first_key
    assert path.read_text() == "second"


def test_project_override_clear_explains_fallback(client, tmp_path):
    c, r = client
    c.preferences.update("project", "Use X here", expected_version=0)
    path = tmp_path / "prefs.md"
    c.preferences.checkout(scope="project", file=path)
    path.write_text("")
    result = c.preferences.checkin(path)
    assert result.ok and "user preferences apply again" in result.data["message"]


def test_human_create_names_document_and_conflict_names_working_file(client, tmp_path, capsys):
    c, r = client
    source = tmp_path / "source.md"
    source.write_text("brief")
    assert run_product_family("documents", ["create", "--kind", "pack.brief", "--file", str(source)], client=c) == 0
    assert "document:" in capsys.readouterr().out
    path = tmp_path / "preferences.md"
    c.preferences.checkout(scope="user", file=path)
    c.preferences.update("user", "remote", expected_version=0)
    path.write_text("local")
    assert run_product_family("preferences", ["checkin", str(path)], client=c) == 1
    assert str(path) in capsys.readouterr().err
    assert path.read_text() == "local"


def test_explicit_null_update_forwarded(client):
    c, r = client
    doc = c.documents.create(kind="pack.brief", content={"before": True}).data
    updated = c.documents.update(doc["document_id"], content=None, expected_version=1)
    assert updated.ok and updated.data["content"] is None
    assert r.writes[-1][1] is None


def test_workspace_adapter_forwards_explicit_null():
    from astrid.sdk.workspace_client import WorkspaceClient
    captured = {}
    class Generated:
        def update_document(self, *args, **kwargs):
            captured.update(kwargs)
            return {"content": kwargs["content"]}
    transport = object.__new__(WorkspaceClient)
    transport._generated = Generated()
    assert transport.update_document("project", "document", content=None, expected_version=1, idempotency_key="key")["content"] is None
    assert "content" in captured and captured["content"] is None


def test_workspace_adapter_kind_only_update_omits_content():
    from astrid.sdk.workspace_client import WorkspaceClient
    captured = {}
    class Generated:
        def update_document(self, *args, **kwargs):
            captured.update(kwargs)
            return {"kind": kwargs["kind"]}
    transport = object.__new__(WorkspaceClient)
    transport._generated = Generated()
    transport.update_document("project", "document", kind="pack.brief", expected_version=1, idempotency_key="key")
    assert "content" not in captured
    assert captured["kind"] == "pack.brief"
