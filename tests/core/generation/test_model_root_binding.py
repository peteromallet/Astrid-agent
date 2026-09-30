from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrid.core.generation.model_root import (
    MODEL_ROOT_BINDING_ENV,
    MODEL_ROOT_ENV,
    ModelRootBindingError,
    canonical_model_inventory_digest,
    model_root_binding_from_environment,
    model_root_binding_from_profile,
    use_attested_vibecomfy_models_root,
    validate_model_root_binding,
)


def _payload(
    root: Path,
    *,
    size: int,
    sha256: str,
    qualification_identity: str | None = None,
) -> dict[str, object]:
    inventory = [{"name": "model.bin", "sha256": sha256, "size": size, "subdir": "vae"}]
    return {
        "schema_version": 1,
        "path": str(root),
        "inventory": inventory,
        "inventory_digest": canonical_model_inventory_digest(inventory),
        "qualification_identity": qualification_identity,
    }


def test_model_root_binding_verifies_bytes_and_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "models"
    target = root / "vae" / "model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"model-bytes")
    digest = "sha256:" + hashlib.sha256(b"model-bytes").hexdigest()

    binding = validate_model_root_binding(
        _payload(root, size=target.stat().st_size, sha256=digest)
    )

    assert binding.path == root
    assert binding.as_dict()["inventory_digest"] == canonical_model_inventory_digest(
        list(binding.inventory)
    )


def test_model_root_binding_reuses_stat_bound_qualification_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "models"
    target = root / "vae" / "model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"model-bytes")
    digest = "sha256:" + hashlib.sha256(b"model-bytes").hexdigest()
    payload = _payload(
        root,
        size=target.stat().st_size,
        sha256=digest,
        qualification_identity="storage-release:fixture-1",
    )
    receipt_dir = tmp_path / "support" / "model-verification"

    validate_model_root_binding(
        payload, qualification_receipt_dir=receipt_dir
    )
    assert list(receipt_dir.glob("*.json"))

    def unexpected_hash(_path: Path) -> str:
        raise AssertionError("unchanged qualified model must not be read again")

    monkeypatch.setattr("astrid.core.generation.model_root._file_sha256", unexpected_hash)
    binding = validate_model_root_binding(
        payload, qualification_receipt_dir=receipt_dir
    )
    assert binding.inventory_digest == payload["inventory_digest"]


def test_model_root_binding_changed_qualification_identity_requalifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "models"
    target = root / "vae" / "model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"model-bytes")
    digest = "sha256:" + hashlib.sha256(b"model-bytes").hexdigest()
    receipt_dir = tmp_path / "support" / "model-verification"
    first = _payload(
        root,
        size=target.stat().st_size,
        sha256=digest,
        qualification_identity="storage-release:fixture-1",
    )
    second = {**first, "qualification_identity": "storage-release:fixture-2"}
    validate_model_root_binding(first, qualification_receipt_dir=receipt_dir)

    original_hash = hashlib.sha256
    hashed: list[Path] = []

    def counted_hash(path: Path) -> str:
        hashed.append(path)
        return "sha256:" + original_hash(path.read_bytes()).hexdigest()

    monkeypatch.setattr("astrid.core.generation.model_root._file_sha256", counted_hash)
    binding = validate_model_root_binding(
        second, qualification_receipt_dir=receipt_dir
    )

    assert hashed == [target]
    assert binding.qualification_identity == "storage-release:fixture-2"
    assert len(list(receipt_dir.glob("*.json"))) == 2


def test_model_root_binding_without_qualification_identity_always_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "models"
    target = root / "vae" / "model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"model-bytes")
    digest = "sha256:" + hashlib.sha256(b"model-bytes").hexdigest()
    receipt_dir = tmp_path / "support" / "model-verification"
    trusted = _payload(
        root,
        size=target.stat().st_size,
        sha256=digest,
        qualification_identity="storage-release:fixture-1",
    )
    validate_model_root_binding(trusted, qualification_receipt_dir=receipt_dir)

    original_hash = hashlib.sha256
    hashed: list[Path] = []

    def counted_hash(path: Path) -> str:
        hashed.append(path)
        return "sha256:" + original_hash(path.read_bytes()).hexdigest()

    monkeypatch.setattr("astrid.core.generation.model_root._file_sha256", counted_hash)
    legacy = {key: value for key, value in trusted.items() if key != "qualification_identity"}
    binding = validate_model_root_binding(legacy, qualification_receipt_dir=receipt_dir)

    assert hashed == [target]
    assert binding.qualification_identity is None
    assert len(list(receipt_dir.glob("*.json"))) == 1


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("size", 999, "size mismatch"),
        ("sha256", "sha256:" + "0" * 64, "hash mismatch"),
        ("name", "../model.bin", "safe relative"),
        ("name", ".", "safe relative"),
    ],
)
def test_model_root_binding_rejects_wrong_inventory_fields(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    root = tmp_path / "models"
    target = root / "vae" / "model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"model-bytes")
    digest = "sha256:" + hashlib.sha256(b"model-bytes").hexdigest()
    payload = _payload(root, size=target.stat().st_size, sha256=digest)
    payload_inventory = payload["inventory"]
    assert isinstance(payload_inventory, list)
    payload_inventory[0][field] = value
    payload["inventory_digest"] = canonical_model_inventory_digest(payload_inventory)

    with pytest.raises(ModelRootBindingError, match=message):
        validate_model_root_binding(payload)


def test_model_root_binding_rejects_alias_shadow_and_root_alias(tmp_path: Path) -> None:
    root = tmp_path / "models"
    target = root / "vae" / "model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"model-bytes")
    digest = "sha256:" + hashlib.sha256(b"model-bytes").hexdigest()
    payload = _payload(root, size=target.stat().st_size, sha256=digest)

    (root / "loras").mkdir()
    (root / "loras" / "model.bin").write_bytes(b"shadow")
    with pytest.raises(ModelRootBindingError, match="shadow"):
        validate_model_root_binding(payload)

    (root / "loras" / "model.bin").unlink()
    (root / "loras" / "alias.bin").symlink_to(target)
    with pytest.raises(ModelRootBindingError, match="symlink"):
        validate_model_root_binding(payload)

    with pytest.raises(ModelRootBindingError, match="lexical aliases"):
        validate_model_root_binding({**payload, "path": str(root / ".." / "models")})


def test_model_root_binding_requires_a_complete_nonempty_inventory(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    (root / "model.safetensors").write_bytes(b"fixture")
    empty = {
        "schema_version": 1,
        "path": str(root),
        "inventory": [],
        "inventory_digest": canonical_model_inventory_digest([]),
    }
    with pytest.raises(ModelRootBindingError, match="incomplete.*unlisted=model.safetensors"):
        validate_model_root_binding(empty)

    declared = {
        "name": "model.safetensors",
        "sha256": "sha256:" + hashlib.sha256(b"fixture").hexdigest(),
        "size": len(b"fixture"),
        "subdir": ".",
    }
    payload = {
        "schema_version": 1,
        "path": str(root),
        "inventory": [declared],
        "inventory_digest": canonical_model_inventory_digest([declared]),
    }
    binding = validate_model_root_binding(payload)
    assert binding.inventory[0]["subdir"] == ""


def test_model_root_binding_rejects_missing_declared_file(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    declared = {
        "name": "missing.safetensors",
        "sha256": "sha256:" + "0" * 64,
        "size": 1,
        "subdir": "checkpoints",
    }
    payload = {
        "schema_version": 1,
        "path": str(root),
        "inventory": [declared],
        "inventory_digest": canonical_model_inventory_digest([declared]),
    }
    with pytest.raises(ModelRootBindingError, match="incomplete.*missing=checkpoints/missing.safetensors"):
        validate_model_root_binding(payload)


@pytest.mark.parametrize("root_value", [None, "", "ComfyUI/models"])
def test_model_root_profile_rejects_empty_or_default_binding(
    tmp_path: Path, root_value: object
) -> None:
    profile = {"launch": {"model_root": root_value}}
    with pytest.raises(ModelRootBindingError):
        model_root_binding_from_profile(profile)


def test_child_environment_binding_cannot_disagree_with_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "models"
    root.mkdir()
    model = root / "fixture.safetensors"
    model.write_bytes(b"fixture")
    entry = {
        "name": model.name,
        "sha256": "sha256:" + hashlib.sha256(model.read_bytes()).hexdigest(),
        "size": model.stat().st_size,
        "subdir": ".",
    }
    payload = {
        "schema_version": 1,
        "path": str(root),
        "inventory": [entry],
        "inventory_digest": canonical_model_inventory_digest([entry]),
    }
    monkeypatch.setenv(MODEL_ROOT_ENV, str(root))
    monkeypatch.setenv(MODEL_ROOT_BINDING_ENV, json.dumps(payload, sort_keys=True))
    binding = model_root_binding_from_environment()
    assert binding.path == root
    assert binding.inventory[0]["subdir"] == ""

    monkeypatch.setenv(MODEL_ROOT_ENV, str(tmp_path / "other"))
    with pytest.raises(ModelRootBindingError, match="disagrees"):
        model_root_binding_from_environment()


def test_attested_compile_root_rejects_workflow_metadata_redirect(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    bundle = SimpleNamespace(
        workflow=SimpleNamespace(metadata={"models_root": str(tmp_path / "other")})
    )
    with pytest.raises(ModelRootBindingError, match="disagrees"):
        with use_attested_vibecomfy_models_root(bundle, root):
            pytest.fail("metadata redirect should be rejected before compile")
