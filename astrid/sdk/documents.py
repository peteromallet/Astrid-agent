"""Runtime document families and detached, conflict-safe working copies.

Markdown documents have string content; all other JSON values are lossless.
An adjacent ``.astrid.json`` file binds edits to the original runtime identity.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping

from .contracts import DomainResult, ErrorObject
from .pagination import page_pair
from .remote import _RemoteFamily


def stable_document_id(project_id: str, kind: str, name: str = "default") -> str:
    """Stable one-per-project pack document ID; pass an explicit canonical ID."""
    if any(not isinstance(v, str) or not v.strip() for v in (project_id, kind, name)):
        raise ValueError("project_id, kind and name must be nonempty strings")
    return "document:" + str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([project_id, kind, name])))


def _failure(code: str, message: str, **details: Any) -> DomainResult:
    return DomainResult.failure(ErrorObject(code, message, details))


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _binding(meta: Mapping) -> str:
    return _digest({field: meta[field] for field in ("endpoint", "realm_id", "actor_id", "scope", "project_id", "document_id", "kind", "format", "checkout_id")})


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".astrid.json")


def _write_metadata(path: Path, value: Mapping) -> None:
    fd, raw = tempfile.mkstemp(prefix=".astrid-document-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(raw):
            os.unlink(raw)


class _DocumentFamily(_RemoteFamily):
    def _project(self, project: str | None) -> DomainResult:
        result = self._typed("get_project", project) if project else self._typed("current_project")
        if not result.ok:
            return result
        row = result.data if project else (result.data or {}).get("project")
        if not isinstance(row, Mapping):
            return _failure("not_found", "No project selected. Use astrid projects select <project>.")
        identity = row.get("project_id") or row.get("id")
        if not isinstance(identity, str) or not identity:
            return _failure("protocol_error", "Runtime did not return a canonical project identity.")
        return DomainResult.success(identity)

    def _identity(self) -> dict:
        handshake = self._client._last_handshake
        if not isinstance(handshake, Mapping):
            raise ValueError("An authenticated runtime handshake is required for document checkout.")
        result = {"endpoint": self._client.endpoint.rstrip("/"), "realm_id": handshake.get("realm_id"), "actor_id": handshake.get("actor_id")}
        if any(not isinstance(v, str) or not v for v in result.values()):
            raise ValueError("Runtime identity is incomplete.")
        return result

    def _checkout(self, row: Mapping, file: str | Path, *, scope: str, project_id: str | None, kind: str) -> DomainResult:
        path = Path(file).expanduser().absolute()
        metadata_path = _sidecar(path)
        try:
            if path.exists() or path.is_symlink() or metadata_path.exists() or metadata_path.is_symlink():
                raise FileExistsError("Checkout destination or sidecar already exists; choose a new --file path.")
            content = row["content"]
            format_ = "markdown" if isinstance(content, str) else "json"
            text = content if format_ == "markdown" else json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            metadata = {"schema": 1, **self._identity(), "scope": scope, "project_id": project_id, "document_id": row.get("document_id") or row.get("id"), "kind": kind, "format": format_, "base_version": row["version"], "base_digest": _digest(content), "checkout_id": uuid.uuid4().hex, "retry": None}
            metadata["binding_digest"] = _binding(metadata)
            if not isinstance(metadata["document_id"], str) or not metadata["document_id"]:
                raise ValueError("Runtime returned no document identity.")
            with path.open("x", encoding="utf-8", newline="") as handle:
                handle.write(text)
            # Reserve the second path exclusively as well. If metadata writing
            # fails, retain the working text and report it for recovery.
            with metadata_path.open("x", encoding="utf-8") as handle:
                json.dump(metadata, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            return DomainResult.success({"file": str(path), "metadata_file": str(metadata_path), "document_id": metadata["document_id"], "version": row["version"]})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _failure("checkout_failed", str(exc), file=str(path))

    def checkin(self, file: str | Path) -> DomainResult:
        path = Path(file).expanduser().absolute()
        try:
            metadata_path = _sidecar(path)
            if path.is_symlink() or metadata_path.is_symlink():
                raise ValueError("Working copy and metadata must be regular files, not symlinks.")
            meta = json.loads(metadata_path.read_text(encoding="utf-8"))
            required = {"schema", "endpoint", "realm_id", "actor_id", "scope", "project_id", "document_id", "kind", "format", "base_version", "base_digest", "checkout_id", "retry", "binding_digest"}
            if not isinstance(meta, dict) or set(meta) != required or isinstance(meta["schema"], bool) or meta["schema"] != 1:
                raise ValueError("Malformed checkout metadata; recover by checking out to a new path and copying your edits.")
            if meta["scope"] not in {"document", "user", "project"} or meta["format"] not in {"markdown", "json"}:
                raise ValueError("Invalid checkout scope or format.")
            if isinstance(meta["base_version"], bool) or not isinstance(meta["base_version"], int) or meta["base_version"] < 0:
                raise ValueError("Invalid checkout base version.")
            for field in ("document_id", "kind", "checkout_id", "base_digest"):
                if not isinstance(meta[field], str) or not meta[field]:
                    raise ValueError(f"Invalid checkout {field}.")
            if len(meta["base_digest"]) != 64 or any(c not in "0123456789abcdef" for c in meta["base_digest"]):
                raise ValueError("Invalid checkout base digest.")
            if meta["scope"] == "user":
                if meta["project_id"] is not None or meta["document_id"] != f"preferences:user:{meta['actor_id']}":
                    raise ValueError("User preference identity does not match its owner.")
            elif not isinstance(meta["project_id"], str) or not meta["project_id"]:
                raise ValueError("A pinned project identity is required.")
            if meta["scope"] == "project" and meta["document_id"] != f"preferences:project:{meta['project_id']}":
                raise ValueError("Project preference identity does not match its project.")
            if meta["scope"] != "document" and (meta["kind"] != "astrid.preferences" or meta["format"] != "markdown"):
                raise ValueError("Invalid preference kind or format.")
            if meta["binding_digest"] != _binding(meta):
                raise ValueError("Checkout identity metadata changed. Recover using a fresh checkout and copy your edits.")
            for field, value in self._identity().items():
                if meta[field] != value:
                    raise ValueError(f"Checkout {field} differs from the authenticated runtime; reconnect to its original realm and actor.")
            with path.open("r", encoding="utf-8", newline="") as handle:
                text = handle.read()
            content = text if meta["format"] == "markdown" else json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Invalid JSON constant {value}")))
            body_digest = _digest(content)
            key = "document-checkin:" + _digest([meta["checkout_id"], body_digest, meta["base_version"]])
            # A pending retry must go straight to the mutation so durable
            # server replay can succeed even though its base is now stale.
            retry = {"body_digest": body_digest, "base_version": meta["base_version"], "idempotency_key": key}
            if meta["retry"] is not None:
                pending = meta["retry"]
                if not isinstance(pending, dict) or set(pending) != set(retry) or pending["idempotency_key"] != "document-checkin:" + _digest([meta["checkout_id"], pending["body_digest"], pending["base_version"]]) or pending["base_version"] != meta["base_version"]:
                    raise ValueError("Malformed retry metadata.")
            if body_digest == meta["base_digest"] and meta["retry"] is None:
                remote = self._read_pinned(meta)
                if not remote.ok:
                    return self._preserve(remote, path)
                if remote.data["version"] != meta["base_version"]:
                    return _failure("version_conflict", "Remote document changed. Check out to a new path and reconcile your edits.", file=str(path), expected_version=meta["base_version"], actual_version=remote.data["version"])
                return DomainResult.success({"file": str(path), "changed": False, "version": meta["base_version"]})
            meta["retry"] = retry
            _write_metadata(metadata_path, meta)
            if meta["scope"] == "document":
                result = self._typed("update_document", meta["project_id"], meta["document_id"], content=content, expected_version=meta["base_version"], idempotency_key=key, key=key)
            else:
                result = self._typed("update_preferences", meta["scope"], content, project_id=meta["project_id"], expected_version=meta["base_version"], idempotency_key=key, key=key)
            if not result.ok:
                return self._preserve(result, path)
            meta.update(base_version=result.data["version"], base_digest=body_digest, retry=None)
            _write_metadata(metadata_path, meta)
            data = {"file": str(path), "changed": True, "document_id": meta["document_id"], "version": meta["base_version"]}
            if meta["scope"] == "project" and not content.strip():
                data["message"] = "Project overrides cleared; user preferences apply again."
            return DomainResult.success(data, receipt=result.receipt, idempotency_key=result.idempotency_key)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return _failure("checkin_failed", str(exc), file=str(path))

    def _read_pinned(self, meta: dict) -> DomainResult:
        if meta["scope"] == "document":
            return self._typed("get_document", meta["project_id"], meta["document_id"])
        return self._typed("get_preferences", meta["scope"], project_id=meta["project_id"])

    @staticmethod
    def _preserve(result: DomainResult, path: Path) -> DomainResult:
        return DomainResult.failure(ErrorObject(result.error.code, result.error.message + " Your edited file is preserved; check out to a new path to reconcile changes.", {**result.error.details, "file": str(path)}), idempotency_key=result.idempotency_key)


class Documents(_DocumentFamily):
    def list(self, project: str | None = None, *, kind: str | None = None, limit: int = 50) -> DomainResult:
        resolved = self._project(project)
        if not resolved.ok:
            return resolved
        rows, seen, cursor = [], set(), None
        while True:
            result = self._typed("list_documents", resolved.data, cursor=cursor, limit=limit, kind=kind)
            if not result.ok:
                return result
            page = page_pair(result.data)
            if page is None:
                return _failure("protocol_error", "Runtime returned malformed document pagination.")
            items, next_cursor = page
            rows.extend(items)
            if next_cursor is None:
                return DomainResult.success(rows)
            if next_cursor in seen or next_cursor == cursor or len(seen) >= 10000:
                return _failure("protocol_error", "Runtime returned cyclic document pagination.")
            seen.add(next_cursor)
            cursor = next_cursor

    def show(self, document: str, *, project: str | None = None) -> DomainResult:
        resolved = self._project(project)
        return self._typed("get_document", resolved.data, document) if resolved.ok else resolved

    def create(self, *, kind: str, content: Any, project: str | None = None, document_id: str | None = None, idempotency_key: str | None = None) -> DomainResult:
        resolved = self._project(project)
        if not resolved.ok:
            return resolved
        key = idempotency_key or uuid.uuid4().hex
        return self._typed("create_document", resolved.data, document_id or "document:" + str(uuid.uuid5(uuid.NAMESPACE_URL, key)), kind, content, key=key, idempotency_key=key)

    def update(self, document: str, *, content: Any, expected_version: int, project: str | None = None, idempotency_key: str | None = None) -> DomainResult:
        resolved = self._project(project)
        if not resolved.ok:
            return resolved
        key = idempotency_key or uuid.uuid4().hex
        return self._typed("update_document", resolved.data, document, content=content, expected_version=expected_version, key=key, idempotency_key=key)

    def checkout(self, document: str, *, file: str | Path, project: str | None = None) -> DomainResult:
        resolved = self._project(project)
        if not resolved.ok:
            return resolved
        result = self._typed("get_document", resolved.data, document)
        return self._checkout(result.data, file, scope="document", project_id=resolved.data, kind=result.data["kind"]) if result.ok else result


class Preferences(_DocumentFamily):
    def get(self, scope: str, *, project: str | None = None) -> DomainResult:
        if scope not in {"user", "project"} or (scope == "user" and project is not None):
            return _failure("validation_error", "Choose user or project scope; user preferences do not take a project.")
        return self._typed("get_preferences", scope, project_id=project)

    def update(self, scope: str, content: str, *, expected_version: int, project: str | None = None, idempotency_key: str | None = None) -> DomainResult:
        if scope not in {"user", "project"} or (scope == "user" and project is not None):
            return _failure("validation_error", "Choose user or project scope; user preferences do not take a project.")
        key = idempotency_key or uuid.uuid4().hex
        return self._typed("update_preferences", scope, content, expected_version=expected_version, project_id=project, key=key, idempotency_key=key)

    def view(self, *, project: str | None = None) -> DomainResult:
        user = self.get("user")
        if not user.ok:
            return user
        selected = self._project(project)
        if not selected.ok and (project or selected.error.code != "not_found"):
            return selected
        project_prefs = self.get("project", project=selected.data) if selected.ok else DomainResult.success(None)
        if not project_prefs.ok:
            return project_prefs
        return DomainResult.success({"user": user.data, "project": project_prefs.data, "precedence": "Current request > project preferences > user preferences > defaults"})

    def checkout(self, *, scope: str, file: str | Path, project: str | None = None) -> DomainResult:
        result = self.get(scope, project=project)
        return self._checkout(result.data, file, scope=scope, project_id=result.data.get("project_id"), kind="astrid.preferences") if result.ok else result

    def edit(self, *, scope: str, project: str | None = None, editor: str | None = None) -> DomainResult:
        command = editor or os.environ.get("EDITOR")
        try:
            command_args = shlex.split(command or "")
        except ValueError as exc:
            return _failure("editor_missing", str(exc))
        if not command_args:
            return _failure("editor_missing", "Set $EDITOR, or use preferences checkout/checkin with your editor.")
        directory = Path(tempfile.mkdtemp(prefix="astrid-preferences-"))
        path = directory / "preferences.md"
        result = self.checkout(scope=scope, file=path, project=project)
        if not result.ok:
            return result
        try:
            status = subprocess.run([*command_args, str(path)], check=False).returncode
            if status in {130, -2}:
                return DomainResult.success({"file": str(path), "changed": False, "cancelled": True})
            if status != 0:
                return _failure("editor_failed", "Editor exited without saving preferences to the runtime. Your working copy is preserved.", file=str(path), exit_code=status)
            return self.checkin(path)
        except KeyboardInterrupt:
            return DomainResult.success({"file": str(path), "changed": False, "cancelled": True})
        except OSError as exc:
            return _failure("editor_failed", str(exc), file=str(path))
