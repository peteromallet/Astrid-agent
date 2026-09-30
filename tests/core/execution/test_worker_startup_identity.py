from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from astrid.core.execution.generic_host import (
    HostError,
    _startup_identity_attestation,
    source_checkout_closure,
    source_checkout_closure_digest,
    source_checkout_digest,
)


def _source_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    packs = checkout / "astrid" / "packs" / "h3_av"
    packs.mkdir(parents=True)
    (packs / "pack.yaml").write_text("id: h3_av\n", encoding="utf-8")
    (checkout / "astrid" / "core" / "execution").mkdir(parents=True)
    (checkout / "astrid" / "core" / "execution" / "generic_host.py").write_text(
        "# host fixture\n", encoding="utf-8"
    )
    (checkout / "astrid" / "sdk").mkdir(parents=True)
    (checkout / "astrid" / "sdk" / "marker.py").write_text(
        "# sdk fixture\n", encoding="utf-8"
    )
    (checkout / "astrid" / "omp_agent.py").write_text(
        "# launcher fixture\n", encoding="utf-8"
    )
    (checkout / "astrid" / "__init__.py").write_text("# package fixture\n", encoding="utf-8")
    (checkout / "astrid" / "version.py").write_text(
        "__version__ = 'fixture'\n", encoding="utf-8"
    )
    (checkout / "astrid" / "__main__.py").write_text("# main fixture\n", encoding="utf-8")
    (checkout / "astrid" / "runtime_cli.py").write_text("# runtime cli fixture\n", encoding="utf-8")
    (checkout / "astrid" / "sdk" / "workspace_client.py").write_text(
        "# workspace client fixture\n", encoding="utf-8"
    )
    (checkout / "banodoco_workspace_client").mkdir()
    (checkout / "banodoco_workspace_client" / "__init__.py").write_text(
        "# vendored client fixture\n", encoding="utf-8"
    )
    (checkout / "banodoco_workspace_client" / "generated.py").write_text(
        "# generated client fixture\n", encoding="utf-8"
    )
    (checkout / "banodoco_workspace_client" / "contract_metadata.py").write_text(
        "# contract fixture\n", encoding="utf-8"
    )
    return checkout


def test_startup_attestation_normalizes_and_records_exact_target(tmp_path: Path) -> None:
    checkout = _source_checkout(tmp_path)
    target = {
        "kind": "runpod",
        "pod_id": "pod-5090",
        "provider_account_ref": "runpod-default",
    }

    receipt = _startup_identity_attestation(
        source_checkout=checkout,
        source_inventory_identity="inventory-1",
        expected_source_checkout_digest=source_checkout_digest(checkout),
        expected_source_closure_digest=source_checkout_closure_digest(checkout),
        boot_manifest_hash="sha256:boot",
        require_target=True,
        target_json=json.dumps(target),
    )

    assert receipt["target"] == target
    assert receipt["target_digest"] == "sha256:" + hashlib.sha256(
        json.dumps(target, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert receipt["source"] == {
        "checkout": str(checkout),
        "checkout_digest": source_checkout_digest(checkout),
        "closure_digest": source_checkout_closure_digest(checkout),
        "closure": source_checkout_closure(checkout)["components"],
        "inventory_identity": "inventory-1",
    }
    assert {entry["component"] for entry in receipt["source"]["closure"]} == {
        "host", "core", "sdk", "launcher", "astrid_entrypoint", "astrid_version", "astrid_main",
        "astrid_runtime_cli", "vendored_workspace_client",
    }


def test_source_closure_digest_changes_when_sdk_or_launcher_changes(tmp_path: Path) -> None:
    checkout = _source_checkout(tmp_path)
    before = source_checkout_closure_digest(checkout)
    (checkout / "astrid" / "sdk" / "marker.py").write_text(
        "# sdk changed\n", encoding="utf-8"
    )
    assert source_checkout_closure_digest(checkout) != before


def test_sanitized_child_import_proves_current_host_sdk_version_and_vendored_client() -> None:
    checkout = Path(__file__).resolve().parents[3]
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(checkout),
        "PYTHONNOUSERSITE": "1",
    }
    code = (
        "import astrid, astrid.runtime_cli; "
        "import astrid.version as version; "
        "import astrid.core.execution.generic_host as host; "
        "import astrid.sdk.workspace_client as sdk; "
        "import banodoco_workspace_client as vendor; "
        "from pathlib import Path; "
        "root = Path.cwd().resolve(); "
        "assert all(Path(module.__file__).resolve().is_relative_to(root) "
        "for module in (astrid, version, host, sdk, vendor)); "
        "print('sanitized-import-ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=checkout,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "sanitized-import-ok"


def test_sanitized_imported_version_module_changes_measured_closure(tmp_path: Path) -> None:
    """The checkout census measures imported version code, not external deps."""
    checkout = _source_checkout(tmp_path)
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(checkout),
        "PYTHONNOUSERSITE": "1",
    }
    code = (
        "import astrid, astrid.version as version; "
        "import astrid.core.execution.generic_host as host; "
        "import astrid.sdk.workspace_client as sdk; "
        "import banodoco_workspace_client as vendor; "
        "from pathlib import Path; "
        "root = Path.cwd().resolve(); "
        "assert all(Path(module.__file__).resolve().is_relative_to(root) "
        "for module in (astrid, version, host, sdk, vendor)); "
        "print('sanitized-import-ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=checkout,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "sanitized-import-ok"
    before = source_checkout_closure_digest(checkout)
    (checkout / "astrid" / "version.py").write_text(
        "__version__ = 'changed-imported-module'\n", encoding="utf-8"
    )
    assert source_checkout_closure_digest(checkout) != before


def test_startup_fails_before_readiness_on_source_mismatch(tmp_path: Path) -> None:
    checkout = _source_checkout(tmp_path)

    with pytest.raises(HostError, match="source identity mismatch"):
        _startup_identity_attestation(
            source_checkout=checkout,
            source_inventory_identity="inventory-1",
            expected_source_checkout_digest="stale-source-digest",
            require_target=False,
        )


def test_startup_fails_before_readiness_when_required_target_is_missing(tmp_path: Path) -> None:
    with pytest.raises(HostError, match="requires an explicit execution target"):
        _startup_identity_attestation(
            source_checkout=_source_checkout(tmp_path),
            source_inventory_identity="inventory-1",
            require_target=True,
            target_json="",
        )


def test_startup_rejects_malformed_target_before_claims(tmp_path: Path) -> None:
    with pytest.raises(HostError, match="not a valid execution target"):
        _startup_identity_attestation(
            source_checkout=_source_checkout(tmp_path),
            source_inventory_identity="inventory-1",
            target_json=json.dumps({"kind": "runpod", "pod_id": "only-pod-id"}),
        )
