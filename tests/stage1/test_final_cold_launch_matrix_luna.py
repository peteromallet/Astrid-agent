"""No-mock, final Stage 1 cold-launch matrix.

This is intentionally one executable journey rather than another collection of
mocked launcher assertions.  It creates a fresh current-Mac-shaped support
home for a source-bound journey, or reuses the explicitly declared existing
support/discovery environment for an installed journey. It starts the declared
neutral runtime through the shared public workspace lifecycle helper when the
selected profile is source-bound, uses the real Astrid CLI/SDK and generic host,
kills/restarts the runtime, and finally proves the neutral TypeScript client
against the same daemon. The helper also verifies public shutdown and
partial-start cleanup.
The Remotion dependency tree is deliberately not part of this gate: the Stage 1
blueprint requires a real registered Astrid render/FFmpeg journey, while
Remotion's optional dependency closure is environment-specific and is covered
by its separate opt-in acceptance test.

Run directly from the Astrid checkout::

    ASTRID_STAGE1_INPUT_MANIFEST=/absolute/path/to/settled-consumer-input.json \
      envs/integration/bin/python -m pytest -q \
      tests/stage1/test_final_cold_launch_matrix_luna.py

The input manifest is mandatory. It binds the complete source snapshot or
artifact identities, the existing noneditable environment/profile descriptors,
the actual Astrid host/action/client identities, and exact Python import
origins. There is no historical Runtime-commit default and no source-checkout
fallback for an installed run.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
from dataclasses import asdict
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

def _cleanup_installed_owners(boundary, owned_handles, stop_host, publish_owner, *, primary=None):
    """Attempt each original owner even when independent host cleanup fails."""
    original = boundary._process
    errors = []

    def publish(stage, **fields):
        try:
            publish_owner(stage, process=original, **fields)
        except BaseException as exc:
            errors.append(exc)

    try:
        stop_host()
    except BaseException as exc:
        errors.append(exc)
        publish("host-cleanup-unresolved", error=f"{type(exc).__name__}: {exc}")
    if original is not None:
        try:
            publish("failure-cleanup", operation="retained boundary.stop")
            boundary.stop()
            assert original.wait(timeout=0) is not None
            publish("failure-cleanup-reaped", operation="retained boundary.stop")
        except BaseException as exc:
            errors.append(exc)
            publish("cleanup-unresolved", error=f"{type(exc).__name__}: {exc}")
    try:
        assert all(process.poll() is not None for process in owned_handles)
    except BaseException as exc:
        errors.append(exc)
    if errors:
        first = primary if primary is not None else errors[0]
        for error in errors:
            if error is not first:
                first.add_note(f"installed owner cleanup: {type(error).__name__}: {error}")
        if primary is None:
            raise first


ROOT = Path(__file__).resolve().parents[2]
INPUT_MANIFEST_ENV = "ASTRID_STAGE1_INPUT_MANIFEST"
INPUT_MANIFEST_SCHEMA = "astrid.stage1.consumer-input/v1"
PENDING_MARKERS = {"PENDING_PRODUCER_HANDOFF", "PENDING", "UNBOUND"}
REQUIRED_IMPORTS = (
    "astrid",
    "astrid_runtime",
    "runtime_protocol",
    "astrid_runtime_client",
)
EXPECTED_CONSUMER = {
    "host": "astrid.core.execution.generic_host.GenericPackHost",
    "action": "astrid.core.execution.generic_host.RuntimeProtocolClient",
    "client": "astrid.sdk.client.AstridClient",
    "runtime_client": "astrid_runtime_client.generated.WorkspaceClient",
}

ACTIVE_PRODUCT_ROOT = ROOT
ACTIVE_RUNTIME_ROOT: Path | None = None
ACTIVE_WORKING_DIRECTORY = ROOT
ACTIVE_PYTHON = Path(sys.executable).absolute()
ACTIVE_PROFILE_MODE = "unset"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path(value: object, field: str, *, directory: bool | None = None) -> Path:
    if not isinstance(value, str) or not value or value in PENDING_MARKERS:
        raise AssertionError(f"{field} must be an explicit path, got {value!r}")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise AssertionError(f"{field} must be absolute, got {value!r}")
    resolved = candidate.resolve()
    if directory is True and not resolved.is_dir():
        raise AssertionError(f"{field} is not an existing directory: {resolved}")
    if directory is False and not resolved.is_file():
        raise AssertionError(f"{field} is not an existing file: {resolved}")
    return resolved


def _digest_record(record: object, field: str, *, preserve_execution_path: bool = False) -> Path:
    if not isinstance(record, Mapping):
        raise AssertionError(f"{field} must be a path/hash record")
    target = _path(record.get("path"), f"{field}.path", directory=False)
    expected = record.get("sha256")
    if (
        not isinstance(expected, str)
        or len(expected) != 64
        or expected != expected.lower()
        or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise AssertionError(f"{field}.sha256 must be a concrete lowercase SHA-256")
    if expected.lower() != _sha256(target):
        raise AssertionError(
            f"{field}.sha256 does not match {target}: expected {expected}, actual {_sha256(target)}"
        )
    # Execution must retain the venv symlink spelling; the digest still
    # verifies the resolved binary bytes above.
    return Path(str(record["path"])).expanduser().absolute() if preserve_execution_path else target


def _assert_no_pending(value: object, field: str) -> None:
    if isinstance(value, str) and value in PENDING_MARKERS:
        raise AssertionError(f"{field} is still pending producer handoff")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _assert_no_pending(child, f"{field}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_pending(child, f"{field}[{index}]")


def _load_input_manifest() -> dict[str, Any]:
    manifest_value = os.environ.get(INPUT_MANIFEST_ENV)
    if not manifest_value:
        raise AssertionError(
            f"{INPUT_MANIFEST_ENV} is required; bind the settled source snapshot or installed artifact manifest explicitly"
        )
    manifest_path = _path(manifest_value, INPUT_MANIFEST_ENV, directory=False)
    manifest = _json(manifest_path)
    if manifest.get("schema") != INPUT_MANIFEST_SCHEMA:
        raise AssertionError(
            f"{manifest_path} must declare schema {INPUT_MANIFEST_SCHEMA!r}, got {manifest.get('schema')!r}"
        )
    mode = manifest.get("mode")
    if mode not in {"source_snapshot", "installed_artifacts"}:
        raise AssertionError(f"unsupported consumer input mode: {mode!r}")
    _assert_no_pending(manifest.get("tuple"), "tuple")
    _assert_no_pending(manifest.get("environment"), "environment")
    _assert_no_pending(manifest.get("consumer"), "consumer")
    _assert_no_pending(manifest.get("runtime_imports"), "runtime_imports")

    tuple_inputs = manifest.get("tuple")
    if not isinstance(tuple_inputs, Mapping):
        raise AssertionError("manifest tuple is required")
    for name in ("astrid", "runtime", "astrid"):
        component = tuple_inputs.get(name)
        if not isinstance(component, Mapping):
            raise AssertionError(f"tuple.{name} is required")
        _path(component.get("root"), f"tuple.{name}.root", directory=True)
        _digest_record(component.get("identity"), f"tuple.{name}.identity")

    environment = manifest.get("environment")
    if not isinstance(environment, Mapping):
        raise AssertionError("manifest environment is required")
    interpreter = _digest_record(
        environment.get("python"), "environment.python",
        preserve_execution_path=mode == "installed_artifacts",
    )
    if mode == "installed_artifacts" and not environment.get("noneditable") is True:
        raise AssertionError("installed_artifacts requires environment.noneditable=true")
    if mode == "installed_artifacts":
        _path(environment.get("site_packages_root"), "environment.site_packages_root", directory=True)
    for descriptor in ("bootstrap", "monitor", "environment"):
        _digest_record(
            environment.get(f"{descriptor}_descriptor"),
            f"environment.{descriptor}_descriptor",
        )
    _path(environment.get("working_directory"), "environment.working_directory", directory=True)
    if mode == "installed_artifacts":
        _path(environment.get("support_root"), "environment.support_root", directory=True)
        # This same-process installed owner must start from the selected
        # stopped workspace. Bind the exact discovery path now; require its
        # real published file after bootstrap below.
        discovery = _path(environment.get("discovery_path"), "environment.discovery_path")
        if discovery.exists() and not discovery.is_file():
            raise AssertionError("installed discovery path is not a regular file")
        _path(environment.get("realm_root"), "environment.realm_root", directory=True)
    else:
        for field in ("support_root", "discovery_path", "realm_root"):
            _path(environment.get(field), f"environment.{field}")
    if not isinstance(environment.get("realm_id"), str) or not environment["realm_id"]:
        raise AssertionError("environment.realm_id is required")
    source_manifest = environment.get("source_manifest")
    if mode == "source_snapshot":
        _digest_record(source_manifest, "environment.source_manifest")
    elif source_manifest is not None:
        raise AssertionError("installed_artifacts must not inject a source manifest")

    consumer = manifest.get("consumer")
    if not isinstance(consumer, Mapping):
        raise AssertionError("manifest consumer is required")
    for key, expected in EXPECTED_CONSUMER.items():
        if consumer.get(key) != expected:
            raise AssertionError(
                f"consumer.{key} must be the actual existing boundary {expected!r}"
            )
    action_ids = consumer.get("action_ids")
    if not isinstance(action_ids, list) or set(action_ids) != {
        "acceptance.cold_echo",
        "acceptance.cold_ffmpeg",
    }:
        raise AssertionError(
            "consumer.action_ids must name the two existing final-consumer actions"
        )
    if consumer.get("host_executor_id") != "astrid-pack-host":
        raise AssertionError("consumer.host_executor_id must be astrid-pack-host")
    _path(consumer.get("host_root"), "consumer.host_root", directory=True)

    origins = manifest.get("runtime_imports")
    if not isinstance(origins, Mapping):
        raise AssertionError("runtime_imports is required")
    for module_name in REQUIRED_IMPORTS:
        record = origins.get(module_name)
        if not isinstance(record, Mapping):
            raise AssertionError(f"runtime_imports.{module_name} is required")
        _digest_record(
            {"path": record.get("origin"), "sha256": record.get("sha256")},
            f"runtime_imports.{module_name}",
        )

    manifest["_path"] = str(manifest_path)
    manifest["_interpreter"] = str(interpreter)
    return manifest


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _configure_input_profile(manifest: Mapping[str, Any]) -> tuple[Path, Path, Path | None]:
    """Bind imports and subprocess roots to the explicit input manifest."""

    global ACTIVE_PRODUCT_ROOT, ACTIVE_RUNTIME_ROOT, ACTIVE_WORKING_DIRECTORY
    global ACTIVE_PYTHON, ACTIVE_PROFILE_MODE

    mode = str(manifest["mode"])
    tuple_inputs = manifest["tuple"]
    environment = manifest["environment"]
    astrid_root = _path(tuple_inputs["astrid"]["root"], "tuple.astrid.root", directory=True)
    runtime_root = _path(tuple_inputs["runtime"]["root"], "tuple.runtime.root", directory=True)
    working_directory = _path(
        environment["working_directory"], "environment.working_directory", directory=True
    )
    interpreter = Path(manifest["_interpreter"])
    if mode == "installed_artifacts":
        if interpreter != Path(sys.executable).absolute():
            raise AssertionError(
                f"installed interpreter mismatch: manifest={interpreter}, running={ACTIVE_PYTHON}"
            )
        actual_site = Path(sysconfig.get_path("purelib")).resolve()
        expected_site = _path(environment.get("site_packages_root"), "environment.site_packages_root", directory=True)
        if actual_site != expected_site:
            raise AssertionError(f"installed site-packages mismatch: manifest={expected_site}, running={actual_site}")
        if environment.get("prefix") != sys.prefix:
            raise AssertionError("installed environment prefix mismatch")
        sys.path[:] = [
            entry
            for entry in sys.path
            if entry
            and not _inside(Path(entry), ROOT)
            and not _inside(Path(entry), runtime_root)
        ]
        ACTIVE_PRODUCT_ROOT = _path(
            environment["site_packages_root"],
            "environment.site_packages_root",
            directory=True,
        )
    else:
        ACTIVE_PRODUCT_ROOT = astrid_root
        sys.path.insert(0, str(runtime_root))
        sys.path.insert(0, str(astrid_root))
    ACTIVE_RUNTIME_ROOT = runtime_root
    ACTIVE_WORKING_DIRECTORY = working_directory
    ACTIVE_PYTHON = interpreter
    ACTIVE_PROFILE_MODE = mode

    source_manifest = None
    if mode == "source_snapshot":
        source_manifest = _digest_record(
            environment["source_manifest"], "environment.source_manifest"
        )
    return astrid_root, runtime_root, source_manifest


def _assert_import_origins(manifest: Mapping[str, Any]) -> None:
    origins = manifest["runtime_imports"]
    observed: dict[str, str] = {}
    for module_name in REQUIRED_IMPORTS:
        spec = importlib.util.find_spec(module_name)
        if spec is None or spec.origin in {None, "built-in", "frozen"}:
            raise AssertionError(f"{module_name} has no file-backed import origin")
        actual = Path(spec.origin).resolve()
        expected = _path(
            origins[module_name]["origin"],
            f"runtime_imports.{module_name}.origin",
            directory=False,
        )
        if actual != expected:
            raise AssertionError(
                f"{module_name} imported from {actual}, expected exact manifest origin {expected}"
            )
        actual_digest = _sha256(actual)
        if actual_digest != origins[module_name]["sha256"]:
            raise AssertionError(
                f"{module_name} origin digest changed: expected {origins[module_name]['sha256']}, actual {actual_digest}"
            )
        observed[module_name] = str(actual)
    assert observed


def _assert_source_manifest_binding(
    source_manifest: Path | None,
    astrid_root: Path,
    runtime_root: Path,
) -> None:
    if source_manifest is None:
        return
    profile = _json(source_manifest)
    if profile.get("source_checkout") != str(astrid_root):
        raise AssertionError(
            "source manifest source_checkout is not the declared Astrid snapshot root"
        )
    if profile.get("runtime_checkout") != str(runtime_root):
        raise AssertionError(
            "source manifest runtime_checkout is not the declared Runtime snapshot root"
        )


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_input_manifest_fixture(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    """Build only the file-backed inputs needed by the manifest boundary tests."""

    astrid_root = tmp_path / "astrid-snapshot"
    runtime_root = tmp_path / "runtime-snapshot"
    app_root = tmp_path / "astrid-private-candidate"
    working_directory = tmp_path / "working"
    support_root = tmp_path / "support"
    discovery_path = support_root / "runtime" / "discovery.json"
    realm_root = tmp_path / "realm"
    site_packages_root = tmp_path / "site-packages"
    for directory in (
        astrid_root,
        runtime_root,
        app_root,
        working_directory,
        support_root,
        discovery_path.parent,
        realm_root,
        site_packages_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    discovery_path.write_text("{}\n", encoding="utf-8")

    def write(path: Path, content: str) -> dict[str, str]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"path": str(path), "sha256": _sha256(path)}

    identities = {
        "astrid": write(astrid_root / "identity.json", '{"component":"astrid"}\n'),
        "runtime": write(runtime_root / "identity.json", '{"component":"runtime"}\n'),
        "astrid": write(app_root / "identity.json", '{"component":"astrid"}\n'),
    }
    descriptors = {
        name: write(tmp_path / "descriptors" / f"{name}.json", json.dumps({"name": name}))
        for name in ("bootstrap", "monitor", "environment")
    }
    import_origins = {
        module_name: write(
            tmp_path / "origins" / f"{module_name}.py",
            f"# fixture origin for {module_name}\n",
        )
        for module_name in REQUIRED_IMPORTS
    }
    source_manifest = write(
        tmp_path / "source-profile.json",
        json.dumps(
            {
                "source_checkout": str(astrid_root),
                "runtime_checkout": str(runtime_root),
            }
        ),
    )
    manifest = {
        "schema": INPUT_MANIFEST_SCHEMA,
        "mode": "source_snapshot",
        "tuple": {
            name: {"root": str(root), "identity": identities[name]}
            for name, root in (
                ("astrid", astrid_root),
                ("runtime", runtime_root),
                ("astrid", app_root),
            )
        },
        "environment": {
            "python": {
                "path": str(Path(sys.executable).resolve()),
                "sha256": _sha256(Path(sys.executable).resolve()),
            },
            "noneditable": False,
            "site_packages_root": str(site_packages_root),
            "bootstrap_descriptor": descriptors["bootstrap"],
            "monitor_descriptor": descriptors["monitor"],
            "environment_descriptor": descriptors["environment"],
            "working_directory": str(working_directory),
            "support_root": str(support_root),
            "discovery_path": str(discovery_path),
            "realm_root": str(realm_root),
            "realm_id": "fixture-realm",
            "source_manifest": source_manifest,
        },
        "consumer": {
            **EXPECTED_CONSUMER,
            "host_executor_id": "astrid-pack-host",
            "action_ids": ["acceptance.cold_echo", "acceptance.cold_ffmpeg"],
            "host_root": str(astrid_root),
        },
        "runtime_imports": {
            module_name: {
                "origin": record["path"],
                "sha256": record["sha256"],
            }
            for module_name, record in import_origins.items()
        },
    }
    manifest_path = tmp_path / "consumer-input.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return manifest, manifest_path


def test_input_manifest_requires_explicit_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The final consumer cannot silently select a checkout or historical HEAD."""

    monkeypatch.delenv(INPUT_MANIFEST_ENV, raising=False)
    with pytest.raises(AssertionError, match=INPUT_MANIFEST_ENV):
        _load_input_manifest()


def test_input_manifest_accepts_hash_bound_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every required fixture identity is accepted only with its actual digest."""

    _, manifest_path = _write_input_manifest_fixture(tmp_path)
    monkeypatch.setenv(INPUT_MANIFEST_ENV, str(manifest_path))
    loaded = _load_input_manifest()
    assert loaded["_path"] == str(manifest_path)
    assert loaded["_interpreter"] == str(Path(sys.executable).resolve())


def test_input_manifest_rejects_pending_or_tampered_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing/pending producer handoff and changed origin bytes fail closed."""

    manifest, manifest_path = _write_input_manifest_fixture(tmp_path / "missing")
    manifest["tuple"]["runtime"].pop("identity")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    monkeypatch.setenv(INPUT_MANIFEST_ENV, str(manifest_path))
    with pytest.raises(AssertionError, match="tuple.runtime.identity"):
        _load_input_manifest()

    manifest, manifest_path = _write_input_manifest_fixture(tmp_path / "pending")
    manifest["tuple"]["astrid"]["identity"]["sha256"] = "PENDING"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    monkeypatch.setenv(INPUT_MANIFEST_ENV, str(manifest_path))
    with pytest.raises(AssertionError, match="still pending producer handoff"):
        _load_input_manifest()

    manifest, manifest_path = _write_input_manifest_fixture(tmp_path / "tampered")
    manifest["runtime_imports"]["astrid"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    monkeypatch.setenv(INPUT_MANIFEST_ENV, str(manifest_path))
    with pytest.raises(AssertionError, match="runtime_imports.astrid.sha256"):
        _load_input_manifest()


def test_input_manifest_rejects_installed_source_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installed proof cannot be manufactured by retaining a source profile."""

    manifest, manifest_path = _write_input_manifest_fixture(tmp_path)
    manifest["mode"] = "installed_artifacts"
    manifest["environment"]["noneditable"] = True
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    monkeypatch.setenv(INPUT_MANIFEST_ENV, str(manifest_path))
    with pytest.raises(AssertionError, match="must not inject a source manifest"):
        _load_input_manifest()


def test_source_manifest_binding_rejects_wrong_declared_root(tmp_path: Path) -> None:
    """A source journey remains meaningful but cannot bind a moving/wrong root."""

    manifest, _ = _write_input_manifest_fixture(tmp_path)
    tuple_inputs = manifest["tuple"]
    environment = manifest["environment"]
    astrid_root = Path(tuple_inputs["astrid"]["root"])
    runtime_root = Path(tuple_inputs["runtime"]["root"])
    source_manifest = Path(environment["source_manifest"]["path"])
    _assert_source_manifest_binding(source_manifest, astrid_root, runtime_root)

    source_manifest.write_text(
        json.dumps(
            {
                "source_checkout": str(tmp_path / "wrong-astrid-root"),
                "runtime_checkout": str(runtime_root),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="source_checkout"):
        _assert_source_manifest_binding(source_manifest, astrid_root, runtime_root)


def _load_public_workspace_fixture():
    """Load the single shared public workspace lifecycle helper."""
    helper = (
        ROOT.parent.parent
        / "receiving"
        / "coordination"
        / "evidence"
        / "B"
        / "shared-public-workspace-fixture"
        / "public_workspace.py"
    )
    spec = importlib.util.spec_from_file_location(
        "astrid_shared_public_workspace_fixture", helper
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"shared public workspace fixture is unavailable: {helper}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _result_data(value: Any) -> Mapping[str, Any]:
    data = getattr(value, "data", None)
    if isinstance(data, Mapping):
        return data
    if isinstance(value, Mapping):
        candidate = value.get("data", value)
        return candidate if isinstance(candidate, Mapping) else value
    raise AssertionError(f"expected mapping result, got {value!r}")


def _run(
    argv: list[str],
    env: Mapping[str, str],
    *,
    check: bool = True,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        cwd=cwd or ACTIVE_WORKING_DIRECTORY,
        env=dict(env),
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"command failed ({completed.returncode}): {' '.join(argv)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def _assert_cli_ok(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Astrid CLI did not emit JSON: {completed.stdout!r}") from exc
    assert result.get("ok") is True, result
    assert set(result) == {"ok", "data", "error", "receipt", "idempotency_key"}, result
    return result


def _wait_dead(pid: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        time.sleep(0.05)
    raise AssertionError(f"runtime pid {pid} did not exit after SIGKILL")


def _write_launcher(path: Path, interpreter: Path) -> None:
    # Use the exact interpreter bound by the input manifest, so this is the
    # actual neutral CLI process and not an installed/ambient launcher.
    path.write_text(
        "#!/bin/sh\n"
        f"exec {interpreter!s} -m astrid_runtime \"$@\"\n",
        encoding="utf-8",
    )
    path.chmod(0o700)


def test_final_cold_launch_matrix_no_mocks(tmp_path: Path) -> None:
    """Exercise the complete supported Stage 1 launch and execution seam."""

    input_manifest = _load_input_manifest()
    astrid_root, runtime_root, source_manifest = _configure_input_profile(input_manifest)
    _assert_source_manifest_binding(source_manifest, astrid_root, runtime_root)
    _assert_import_origins(input_manifest)
    consumer = input_manifest["consumer"]
    expected_host_root = _path(consumer["host_root"], "consumer.host_root", directory=True)
    environment = input_manifest["environment"]
    installed_mode = input_manifest["mode"] == "installed_artifacts"
    execution_namespace = None
    if installed_mode:
        namespace_path = _digest_record(
            input_manifest.get("execution_namespace"), "execution_namespace"
        )
        namespace_binding = _json(namespace_path)
        assert namespace_binding["schema"] == "astrid.stage1.execution-attempt/v1"
        assert namespace_binding["realm_id"] == environment["realm_id"]
        execution_namespace = namespace_binding["namespace"]
        assert isinstance(execution_namespace, str) and 0 < len(execution_namespace) <= 128

    def execution_key(label: str) -> str:
        # Each admitted validation gets new tasks; durable project/media keys
        # and previously completed tasks remain unchanged in this same realm.
        key = f"{execution_namespace}-{label}" if execution_namespace is not None else label
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,255}", key)
        return key

    prior_queue_cleanup = None
    if installed_mode and input_manifest.get("prior_owned_queue_cleanup") is not None:
        cleanup_path = _digest_record(
            input_manifest["prior_owned_queue_cleanup"], "prior_owned_queue_cleanup"
        )
        prior_queue_cleanup = _json(cleanup_path)
        assert prior_queue_cleanup["schema"] == "astrid.stage1.prior-owned-queue-cleanup/v1"
        assert prior_queue_cleanup["realm_id"] == environment["realm_id"]
        _digest_record(prior_queue_cleanup["failed_attempt_receipt"], "failed_attempt_receipt")
        _digest_record(prior_queue_cleanup["failed_attempt_input"], "failed_attempt_input")

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.fail("Stage 1 requires ffmpeg and ffprobe for the real render lane")
    if shutil.which("node") is None or shutil.which("npm") is None:
        pytest.fail("Stage 1 TypeScript second-client proof requires node and npm")

    # The existing TypeScript conformance lane runs npm in a disposable tree.
    # Copy only its declared Runtime inputs so the later monitored route does
    # not duplicate unrelated snapshot/build material. Python import origins
    # remain bound to the exact manifest roots above; this copy is never a
    # provenance substitute.
    runtime_input = tmp_path / "runtime-input"
    for relative in (Path("packages") / "typescript", Path("conformance")):
        shutil.copytree(runtime_root / relative, runtime_input / relative)
    runtime_python = runtime_root / "packages" / "python"
    home = (
        _path(environment["home"], "environment.home", directory=True)
        if installed_mode
        else tmp_path / "home"
    )
    support = (
        _path(environment["support_root"], "environment.support_root", directory=True)
        if installed_mode
        else home / "Library" / "Application Support" / "Astrid Runtime"
    )
    launcher = tmp_path / "astrid-runtime"
    _write_launcher(launcher, ACTIVE_PYTHON)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["ASTRID_RUNTIME_HOME"] = str(home)
    # Keep later direct CLI/SDK calls on the same disposable support root that
    # the public lifecycle helper creates and launches.
    env["ASTRID_RUNTIME_DATA_ROOT"] = str(support)
    env["ASTRID_RUNTIME_LAUNCHER"] = str(launcher)
    if source_manifest is not None:
        env["ASTRID_RUNTIME_SOURCE_MANIFEST"] = str(source_manifest)
        env["PYTHONPATH"] = os.pathsep.join(
            (
                str(astrid_root),
                str(runtime_root),
                str(runtime_python),
                str(Path(importlib.util.find_spec("yaml").origin).parent.parent),
            )
        )
    else:
        # Installed mode must not inherit checkout/runtime source injection.
        env.pop("ASTRID_RUNTIME_SOURCE_MANIFEST", None)
        env.pop("PYTHONPATH", None)

    runtime_pid: int | None = None
    installed_boundary = None
    installed_paths = None
    installed_config = None
    installed_owned_handles: list[Any] = []
    installed_host_handles: list[tuple[Any, dict[str, Any]]] = []
    owner_events: list[dict[str, Any]] = []
    owner_receipt = os.environ.get("ASTRID_STAGE1_OWNER_RECEIPT")

    def publish_owner(phase: str, *, process=None, operation=None, error=None) -> None:
        if not installed_mode:
            return
        discovery_value = _json(discovery_path) if discovery_path.is_file() else {}
        host_marker = support / "runtime" / "generic-host.json"
        host_value = _json(host_marker) if host_marker.is_file() else {}
        event = {
            "phase": phase, "realm_id": environment["realm_id"],
            "runtime_instance_id": discovery_value.get("runtime_instance_id"),
            "runtime_birth_id": discovery_value.get("process_birth_id"),
            "original_runtime_handle": process is not None,
            "runtime_pid": process.pid if process is not None else None,
            "runtime_wait_returncode": process.returncode if process is not None else None,
            "host_pid": host_value.get("pid"), "host_birth_id": host_value.get("process_birth_id"),
            "host_ownership": "existing ensure_pack_host creator retains original handle; marker alone is observation",
            "host_handles": [{"pid": handle.pid, "birth_id": state.get("process_birth_id"), "wait_returncode": handle.returncode}
                             for _handle, state in installed_host_handles],
            "operation": operation, "error": error,
        }
        if process is not None:
            broker = getattr(process, "_runtime_custody_broker", None)
            event["retained_runtime_broker"] = broker is not None
            event["runtime_sole_reaper"] = bool(getattr(process, "_runtime_boundary_sole_reaper", False))
        owner_events.append(event)
        if owner_receipt:
            target = _path(owner_receipt, "ASTRID_STAGE1_OWNER_RECEIPT")
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(json.dumps({
                "schema": "astrid.stage1.installed-original-owner/v1",
                "events": owner_events,
                "full_native_graph_cleanup_proven": False,
                "interruption_kind": "retained owner termination and same-realm cold boot; no forced-crash/public idle restart claim",
            }, sort_keys=True, default=str), encoding="utf-8")
            os.replace(temporary, target)

    if installed_mode:
        from astrid_runtime.bootstrap import BootstrapConfig
        from astrid_runtime.paths import RuntimePaths
        from astrid_runtime.runtime_boundary import LocalRuntimeBoundary
        installed_paths = RuntimePaths.current_mac(home, data_root=support)
        installed_boundary = LocalRuntimeBoundary()
        installed_config = BootstrapConfig(profile="astrid")
        discovery_path = _path(environment["discovery_path"], "environment.discovery_path")
        assert "PYTHONPATH" not in env and "ASTRID_RUNTIME_SOURCE_MANIFEST" not in env

    workspace_session = None
    host_pids: set[int] = set()
    initial_host_state: dict[str, Any] = {}
    restarted_host_state: dict[str, Any] = {}

    def remember_host_state() -> dict[str, Any]:
        state_path = support / "runtime" / "generic-host.json"
        if not state_path.is_file():
            return {}
        state = _json(state_path)
        ready_path = support / "runtime" / "generic-host.ready.json"
        manifest_path = support / "runtime" / "astrid-host" / "boot-manifest.json"
        if (
            state.get("source_checkout") != str(expected_host_root)
            or state.get("ready_file") != str(ready_path)
            or state.get("boot_manifest_path") != str(manifest_path)
            or not state.get("boot_manifest_hash")
        ):
            return {}
        host_pids.add(int(state["pid"]))
        return state

    def start_owned_installed_host(launch: Mapping[str, Any]) -> None:
        from astrid.sdk import host_bootstrap
        host_bootstrap.ensure_pack_host(launch, reconfigure_action="inspect the same selected qualification workspace")
        state = remember_host_state()
        assert state, "actual installed host identity is not bound to this qualification"
        installed_host_handles.append((None, state))  # the registration is the handle
        publish_owner("host-original-handle-retained", process=installed_owned_handles[-1], operation="installed ensure_pack_host")

    def stop_owned_installed_host() -> None:
        from astrid.sdk import host_bootstrap
        for _handle, state in installed_host_handles:
            host_bootstrap._stop_registered_host(state)
        publish_owner("host-original-handles-reaped", process=installed_boundary._process, operation="existing retained host cleanup")

    try:
        # Use the shared public lifecycle helper only for a source-bound
        # profile. Installed mode reuses the existing explicit support,
        # discovery, bootstrap and monitor descriptors without source
        # injection or implicit workspace creation.
        workspace_help = _run(
            [str(ACTIVE_PYTHON), "-m", "astrid_runtime", "workspace", "--help"],
            env,
            check=False,
        )
        realm_root = (
            _path(environment["realm_root"], "environment.realm_root", directory=True)
            if installed_mode
            else tmp_path / "canonical-realm"
        )
        if workspace_help.returncode == 0 and source_manifest is not None:
            assert "create" in workspace_help.stdout and "attach" in workspace_help.stdout
            workspace_session = _load_public_workspace_fixture().start_public_workspace(
                python=ACTIVE_PYTHON,
                env=env,
                home=home,
                support_root=support,
                realm_root=realm_root,
                source_manifest=source_manifest,
                realm_id="stage1-cold-final",
                display_name="Stage 1 Cold Final",
            )
            started = workspace_session.up_result
            discovery_path = workspace_session.discovery_path
        else:
            if source_manifest is not None:
                assert "invalid choice" in workspace_help.stderr and "workspace" in workspace_help.stderr
            if installed_mode:
                from astrid_runtime.bootstrap import bootstrap
                # These are the real installed public lifecycle components;
                # keep their original child handle alive through this journey.
                started = asdict(bootstrap(installed_paths, installed_boundary, installed_config))
                original = installed_boundary._process
                assert original is not None and original.poll() is None
                installed_owned_handles.append(original)
                publish_owner("started", process=original, operation="installed bootstrap")
                start_owned_installed_host(started)
            else:
                first = _run(
                    [
                        str(ACTIVE_PYTHON), "-m", "astrid_runtime", "up", "--profile", "astrid",
                        *((["--source-manifest", str(source_manifest)] if source_manifest is not None else [])), "--json",
                    ], env,
                )
                started = json.loads(first.stdout)
            discovery_path = (
                _path(environment["discovery_path"], "environment.discovery_path", directory=False)
                if installed_mode
                else support / "runtime" / "discovery.json"
            )
        assert started["status"] in {"started", "reconnected"}, started
        assert started["realm_id"] and started["actor_id"]
        if installed_mode:
            assert discovery_path == _path(
                environment["discovery_path"],
                "environment.discovery_path",
                directory=False,
            )
        catalog_path = support / "runtime" / "catalog.json"
        discovery = _json(discovery_path)
        catalog = _json(catalog_path)
        runtime_pid = int(discovery["pid"])
        if workspace_session is not None:
            assert workspace_session.runtime_pid == runtime_pid
        assert catalog["selected_realm_id"] == started["realm_id"]
        assert discovery["active_realm"] == started["realm_id"]
        assert discovery["endpoint"].startswith("http://127.0.0.1:")
        serialized_discovery = json.dumps(discovery)
        # Runtime advertises the worker credential *path* so the pack host can
        # bootstrap with its least-privilege actor.  The bearer value itself
        # must never appear in discovery.
        assert discovery.get("worker_credential_file")
        assert "database" not in serialized_discovery.lower()

        credential_file = Path(started["credential_file"])
        assert credential_file == support / "credentials" / "astrid.json"
        assert stat.S_IMODE(credential_file.stat().st_mode) == 0o600
        assert stat.S_IMODE((support / "runtime").stat().st_mode) == 0o700
        owner_token_file = support / "runtime" / "credentials" / "owner.token"
        assert owner_token_file.is_file()
        owner_token = owner_token_file.read_text(encoding="utf-8").strip()
        assert owner_token
        assert owner_token not in serialized_discovery
        worker_credential_file = Path(discovery["worker_credential_file"])
        assert worker_credential_file.is_file()
        assert stat.S_IMODE(worker_credential_file.stat().st_mode) == 0o600
        worker_token = worker_credential_file.read_text(encoding="utf-8").strip()
        assert worker_token and worker_token != owner_token
        assert discovery["worker_actor"] == "astrid-pack-host"
        assert tuple(discovery["worker_scopes"]) == (
            "handshake",
            "worker:register",
            "worker:execute",
            "projects:read",
            "tasks:read",
            "objects:read",
            "objects:write",
        )

        # A second real launch with the first-run manifest removed exercises
        # persisted-profile/env-less relaunch rather than mocked delegation.
        envless = dict(env)
        envless.pop("ASTRID_RUNTIME_SOURCE_MANIFEST", None)
        second = _run(
            [str(ACTIVE_PYTHON), "-m", "astrid_runtime", "up", "--profile", "astrid", "--json"],
            envless,
        )
        reconnected = json.loads(second.stdout)
        assert reconnected["status"] == "reconnected", reconnected
        assert reconnected["realm_id"] == started["realm_id"]
        assert int(_json(discovery_path)["pid"]) == runtime_pid

        # Concurrent real launches: both subprocesses use the same persisted
        # source profile and support lock. Exactly one may start an owner.
        concurrent = [
            subprocess.Popen(
                [str(ACTIVE_PYTHON), "-m", "astrid_runtime", "up", "--profile", "astrid", "--json"],
                cwd=ACTIVE_WORKING_DIRECTORY,
                env=envless,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        outputs = []
        for process in concurrent:
            stdout, stderr = process.communicate(timeout=45)
            assert process.returncode == 0, (stdout, stderr)
            outputs.append(json.loads(stdout))
        assert {value["status"] for value in outputs} <= {"reconnected", "started"}
        assert all(value["realm_id"] == started["realm_id"] for value in outputs)
        assert len({int(_json(discovery_path)["pid"])}) == 1

        # Astrid CLI admission, then SDK-domain composition over the same
        # explicit endpoint/credential context.
        project_result = _assert_cli_ok(
            _run(
                [str(ACTIVE_PYTHON), "-m", "astrid", "projects", "create", "cold-final", "--name", "Cold Final", "--idempotency-key", "cold-project", "--json"],
                envless,
            )
        )
        project_data = project_result["data"]
        project_id = str(project_data.get("project_id") or project_data.get("id") or "cold-final")
        source = tmp_path / "managed-source.txt"
        source.write_text("managed input", encoding="utf-8")
        media_result = _assert_cli_ok(
            _run(
                [str(ACTIVE_PYTHON), "-m", "astrid", "media", "import", str(source), "--project", project_id, "--idempotency-key", "cold-media", "--json"],
                envless,
            )
        )
        media_data = media_result["data"]
        object_id = str(media_data.get("object_id") or media_data["digest"])
        source.unlink()
        worker_seed = tmp_path / "worker-seed.txt"
        worker_seed.write_text("cold input", encoding="utf-8")
        seed_result = _assert_cli_ok(
            _run(
                [str(ACTIVE_PYTHON), "-m", "astrid", "media", "import", str(worker_seed), "--project", project_id, "--idempotency-key", "cold-worker-seed", "--json"],
                envless,
            )
        )
        seed_data = seed_result["data"]
        seed_object_id = str(seed_data.get("object_id") or seed_data["digest"])
        worker_seed.unlink()

        from astrid.core.execution.generic_host import GenericPackHost, RuntimeProtocolClient
        from astrid.sdk.client import AstridClient
        from astrid_runtime_client import WorkspaceClient
        from astrid_runtime_client.generated import WorkspaceClient as GeneratedWorkspaceClient

        # Bind the public export to its exact defining class and installed bytes.
        assert WorkspaceClient is GeneratedWorkspaceClient
        if installed_mode:
            defining_record = input_manifest["runtime_imports"]["astrid_runtime_client.generated"]
            defining_path = _digest_record(
                {"path": defining_record["origin"], "sha256": defining_record["sha256"]},
                "runtime_imports.astrid_runtime_client.generated",
            )
            assert defining_path == expected_host_root / "astrid_runtime_client" / "generated.py"
            defining_spec = importlib.util.find_spec(WorkspaceClient.__module__)
            assert defining_spec is not None and defining_spec.origin is not None
            assert Path(defining_spec.origin).resolve() == defining_path

        actual_consumer = {
            "host": f"{GenericPackHost.__module__}.{GenericPackHost.__qualname__}",
            "action": f"{RuntimeProtocolClient.__module__}.{RuntimeProtocolClient.__qualname__}",
            "client": f"{AstridClient.__module__}.{AstridClient.__qualname__}",
            "runtime_client": f"{WorkspaceClient.__module__}.{WorkspaceClient.__qualname__}",
        }
        assert actual_consumer == {
            key: consumer[key] for key in EXPECTED_CONSUMER
        }

        explicit = AstridClient.open(
            endpoint=started["endpoint"],
            credential=credential_file,
            realm_id=started["realm_id"],
            actor_id=started["actor_id"],
            client_name="stage1-final-cold-matrix",
            client_version="stage1",
            protocol_version="workspace.v1",
        )
        initial_host_state = remember_host_state()
        assert initial_host_state, "validated generic-host marker was not created for this test"
        if installed_mode:
            publish_owner("host-ready", process=installed_owned_handles[-1], operation="actual installed host marker observed")
        with explicit as client:
            assert client.media.verify(project_id, object_id).ok
            if prior_queue_cleanup is not None:
                # Only the exactly admitted failed probe may be cancelled.
                # Retain every earlier completed/cancelled task unchanged.
                terminal_before = {}
                for retained in prior_queue_cleanup["retained_terminal_tasks"]:
                    value = client.tasks.show(retained["task_id"])
                    assert value.ok
                    data = dict(_result_data(value))
                    assert data["state"] == retained["state"]
                    terminal_before[retained["task_id"]] = data
                cleanup_events = []
                for owned in prior_queue_cleanup["tasks"]:
                    assert owned["project_id"] == project_id
                    value = client.tasks.show(owned["task_id"])
                    assert value.ok
                    data = _result_data(value)
                    for field in ("task_id", "project_id", "capability_id", "capability_digest", "idempotency_key"):
                        assert data[field] == owned[field]
                    assert data["state"] == "queued"
                    key = execution_key("cleanup-" + owned["task_id"])
                    cancellation = client.tasks.cancel(owned["task_id"], idempotency_key=key)
                    assert cancellation.ok and _result_data(cancellation)["state"] == "cancelled"
                    readback = client.tasks.show(owned["task_id"])
                    assert readback.ok and _result_data(readback)["state"] == "cancelled"
                    cleanup_events.append({"task_id": owned["task_id"], "project_id": project_id,
                                           "idempotency_key": key, "before": "queued", "after": "cancelled"})
                for task_id, before in terminal_before.items():
                    value = client.tasks.show(task_id)
                    assert value.ok and dict(_result_data(value)) == before
                assert owner_receipt is not None
                Path(owner_receipt).with_name("prior-owned-task-cleanup.json").write_text(
                    json.dumps({"realm_id": environment["realm_id"], "events": cleanup_events,
                                "retained_terminal_tasks_unchanged": list(terminal_before)}, indent=2) + "\n",
                    encoding="utf-8",
                )
            timeline = client.timelines.create_empty(
                project=project_id,
                timeline_id="cold-final-main",
                config={"tracks": []},
                idempotency_key="cold-timeline",
            )
            assert timeline.ok
            timeline_data = _result_data(timeline)
            assert timeline_data["timeline_id"] == "cold-final-main"
            assert timeline_data["project_id"] == project_id
            assert timeline_data["head_revision_id"]
            timeline_readback = client.timelines.open_composition(
                project_id, "cold-final-main", revision_id=timeline_data["head_revision_id"]
            )
            assert timeline_readback.ok and isinstance(timeline_readback.data, Mapping)
            shot = client.shots.create(
                project=project_id,
                shot={"shot_id": "cold-shot", "name": "Cold Shot"},
                idempotency_key="cold-shot",
            )
            reference = client.references.create(
                project=project_id,
                reference_id="cold-reference",
                kind="object",
                name="Cold Reference",
                media_id=object_id,
                idempotency_key="cold-reference",
            )
            assert shot.ok and reference.ok

            pack = tmp_path / "cpu-pack"
            pack.mkdir()
            (pack / "executor.yaml").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "acceptance.cold_echo",
                        "name": "Cold Echo",
                        "kind": "external",
                        "version": "1.0",
                        "command": {"argv": [str(ACTIVE_PYTHON), "-c", "from pathlib import Path; Path('{out}/answer.txt').write_text('cold worker')"]},
                        "outputs": [{"name": "answer", "type": "file", "path_template": "{out}/answer.txt", "artifact_type": "text/plain"}],
                        "metadata": {"adapter_family": "cpu", "resource_keys": ["cpu"]},
                    }
                ),
                encoding="utf-8",
            )
            host = GenericPackHost(
                pack_roots=[pack],
                client=RuntimeProtocolClient(started["endpoint"], worker_token),
                executor_id=consumer["host_executor_id"],
                attempt_root=tmp_path / "attempt",
                boot_manifest_path=initial_host_state["boot_manifest_path"],
                boot_manifest_hash=initial_host_state["boot_manifest_hash"],
            )
            record = host.discover()[0]
            assert record.id in consumer["action_ids"]
            assert host.preflight(record.id)[0].ready is True
            host.register(deliberate=True)
            # The input is already attached to this project, while the
            # fixture executor emits a distinct output. This proves the host
            # uses the claimed task project_id for output ingest rather than
            # relying on a duplicate-input association shortcut.
            admitted = client.tasks.create(
                project_id=project_id,
                capability=record.id,
                spec={},
                input_manifest=[seed_object_id],
                idempotency_key=execution_key("cold-worker-task"),
            )
            assert admitted.ok
            task_id = str(_result_data(admitted)["task_id"])
            settled = host.run(once=True)
            assert len(settled) == 1 and settled[0].state == "succeeded"
            task = client.tasks.show(task_id)
            assert task.ok and _result_data(task)["state"] in {"succeeded", "completed"}
            output_digest = "sha256:" + hashlib.sha256(b"cold worker").hexdigest()
            assert host.client.generated.get_object(output_digest).data == b"cold worker"

            cancelled = client.tasks.create(project_id=project_id, capability=record.id, spec={}, idempotency_key=execution_key("cold-cancel-task"))
            assert cancelled.ok
            cancelled_id = str(_result_data(cancelled)["task_id"])
            cancellation = client.tasks.cancel(cancelled_id, idempotency_key=execution_key("cold-cancel"))
            assert cancellation.ok and _result_data(cancellation)["state"] == "cancelled"
            retry = client.tasks.retry(cancelled_id, idempotency_key=execution_key("cold-retry"))
            assert retry.ok and _result_data(retry)["state"] == "queued"
            # Close the retried project-scoped probe before the standalone
            # worker probe; otherwise the FIFO claim would (correctly) select
            # that earlier task first.
            cancelled_again = client.tasks.cancel(cancelled_id, idempotency_key=execution_key("cold-cancel-after-retry"))
            assert cancelled_again.ok and _result_data(cancelled_again)["state"] == "cancelled"

            # Claim a task without settling it; killing the real runtime then
            # proves epoch fencing and recovery on the persisted realm.
            recovery_task = client.tasks.create(
                project_id=project_id,
                capability=record.id,
                spec={},
                input_manifest=[seed_object_id],
                idempotency_key=execution_key("cold-recovery-task"),
            )
            assert recovery_task.ok
            recovery_id = str(_result_data(recovery_task)["task_id"])
            old_epoch = int(host.client.health()["runtime_epoch"])
            claim = host.client.claim_next(executor_id=consumer["host_executor_id"], capability_ids=[record.id], idempotency_key=execution_key("cold-recovery-claim"))
            assert claim is not None and getattr(claim, "task_id", None) == recovery_id
            old_attempt = claim

        if installed_mode:
            # An intentionally unsettled claim makes ordinary public restart
            # refuse at its idle fence. The actual retained launch owner's
            # supported cleanup primitive supplies bounded termination and
            # wait evidence, followed by a cold boot of the SAME realm.
            original = installed_boundary._process
            assert original is installed_owned_handles[-1]
            publish_owner("before-interruption", process=original, operation="retained boundary.stop")
            stop_owned_installed_host()
            installed_boundary.stop()
            assert original.wait(timeout=0) is not None
            publish_owner("interrupted-reaped", process=original, operation="retained boundary.stop")
            runtime_pid = None
            restarted = asdict(bootstrap(installed_paths, installed_boundary, installed_config))
            original = installed_boundary._process
            assert original is not None and original.poll() is None
            installed_owned_handles.append(original)
            publish_owner("restarted", process=original, operation="installed bootstrap same realm")
            start_owned_installed_host(restarted)
        else:
            # Preserve the source profile's deliberate forced-crash proof.
            os.kill(runtime_pid, signal.SIGKILL)
            _wait_dead(runtime_pid)
            runtime_pid = None
            restarted_process = _run(
                [str(ACTIVE_PYTHON), "-m", "astrid_runtime", "up", "--profile", "astrid", "--json"],
                envless,
            )
            restarted = json.loads(restarted_process.stdout)
        assert restarted["status"] == "started"
        assert restarted["realm_id"] == started["realm_id"]
        new_discovery = _json(discovery_path)
        runtime_pid = int(new_discovery["pid"])
        restarted_host_state = remember_host_state()
        assert restarted_host_state, "validated restarted generic-host marker was not created for this test"
        if installed_mode:
            publish_owner("restarted-host-ready", process=installed_owned_handles[-1], operation="actual installed host marker observed")
        new_worker_credential_file = Path(new_discovery["worker_credential_file"])
        assert stat.S_IMODE(new_worker_credential_file.stat().st_mode) == 0o600
        new_token = new_worker_credential_file.read_text(encoding="utf-8").strip()
        assert new_token and new_token != owner_token

        generated = WorkspaceClient(restarted["endpoint"], new_token)
        astrid_token = str(_json(Path(restarted["credential_file"])).get("token") or "")
        assert astrid_token and astrid_token != owner_token
        owner_generated = WorkspaceClient(restarted["endpoint"], astrid_token)
        new_epoch = int(generated.health()["runtime_epoch"])
        assert new_epoch > old_epoch
        with pytest.raises(Exception):
            generated.settle_attempt(
                old_attempt.attempt_id,
                {
                    "lease_id": old_attempt.lease_id,
                    "fence": old_attempt.fence,
                    "runtime_epoch": old_epoch,
                    "outputs": [],
                },
                idempotency_key=execution_key("cold-stale-settlement"),
            )
        restarted_host = GenericPackHost(
            pack_roots=[pack],
            client=RuntimeProtocolClient(restarted["endpoint"], new_token),
            executor_id="astrid-pack-host",
            attempt_root=tmp_path / "attempt-restarted",
            boot_manifest_path=restarted_host_state["boot_manifest_path"],
            boot_manifest_hash=restarted_host_state["boot_manifest_hash"],
        )
        restarted_host.discover()
        restarted_host.register(deliberate=True)
        resumed = restarted_host.run(once=True)
        assert len(resumed) == 1 and resumed[0].state == "succeeded"
        assert generated.get_task(recovery_id).state in {"succeeded", "completed"}

        # Real FFmpeg validity/provenance lane.  This is a real external
        # process invoked by the generic host, not a mocked renderer.  The
        # output and its provenance are uploaded to the runtime before the
        # attempt directory is removed, proving the durable handoff.
        render_pack = tmp_path / "ffmpeg-pack"
        render_pack.mkdir()
        ffmpeg = shutil.which("ffmpeg")
        assert ffmpeg is not None
        render_code = (
            "from pathlib import Path; import hashlib, json, subprocess; "
            "out=Path('{out}'); video=out/'render.mp4'; "
            f"subprocess.run([{ffmpeg!r}, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=10:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True); "
            "provenance=out/'render.mp4.provenance.json'; "
            "provenance.write_text(json.dumps({'renderer':'ffmpeg','sha256':hashlib.sha256(video.read_bytes()).hexdigest()})); "
            "(out/'manifest.json').write_text(json.dumps({'outputs':["
            "{'name':'render','path':'render.mp4','artifact_type':'video/mp4','content_hash':'sha256:'+hashlib.sha256(video.read_bytes()).hexdigest(),'bytes':video.stat().st_size,'ordinal':0,'role':'result','is_primary':True},"
            "{'name':'provenance','path':'render.mp4.provenance.json','artifact_type':'application/json','content_hash':'sha256:'+hashlib.sha256(provenance.read_bytes()).hexdigest(),'bytes':provenance.stat().st_size,'ordinal':1,'role':'auxiliary','is_primary':False}"
            "]}, sort_keys=True))"
        )
        (render_pack / "executor.yaml").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "acceptance.cold_ffmpeg",
                    "name": "Cold FFmpeg",
                    "kind": "external",
                    "version": "1.0",
                    "command": {"argv": [str(ACTIVE_PYTHON), "-c", render_code]},
                    "outputs": [
                        {"name": "render", "type": "file", "path_template": "{out}/render.mp4", "artifact_type": "video/mp4"},
                        {"name": "provenance", "type": "file", "path_template": "{out}/render.mp4.provenance.json", "artifact_type": "application/json"},
                    ],
                    "metadata": {"adapter_family": "cpu", "resource_keys": ["cpu"]},
                }
            ),
            encoding="utf-8",
        )
        render_attempt = tmp_path / "render-attempt"
        render_host = GenericPackHost(
            pack_roots=[render_pack],
            client=RuntimeProtocolClient(restarted["endpoint"], new_token),
            executor_id="astrid-pack-host",
            attempt_root=render_attempt,
            boot_manifest_path=restarted_host_state["boot_manifest_path"],
            boot_manifest_hash=restarted_host_state["boot_manifest_hash"],
        )
        render_record = render_host.discover()[0]
        assert render_record.id in consumer["action_ids"]
        assert render_host.preflight(render_record.id)[0].ready is True
        render_host.register()
        # Task admission is user-scoped control-plane work.  The worker token
        # intentionally has no tasks:write after the scope cutover.
        render_task = owner_generated.admit_task(
            capability_id=render_record.id,
            capability_digest=render_record.capability_digest,
            input_object_ids=[],
            project_id=project_id,
            idempotency_key=execution_key("cold-ffmpeg-task"),
            spec={},
        )
        render_results = render_host.run(once=True)
        assert len(render_results) == 1 and render_results[0].state == "succeeded"
        video = next(render_attempt.rglob("render.mp4"), None)
        provenance = next(render_attempt.rglob("render.mp4.provenance.json"), None)
        assert video is not None and provenance is not None and video.stat().st_size > 0
        probe = _run(
            ["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "default=nw=1:nk=1", str(video)],
            envless,
        )
        assert "mp4" in probe.stdout
        for artifact in (video, provenance):
            digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
            assert generated.get_object(digest).data == artifact.read_bytes()
        assert json.loads(provenance.read_text(encoding="utf-8"))["renderer"] == "ffmpeg"
        shutil.rmtree(render_attempt)
        assert not render_attempt.exists()
        completed_render = generated.get_task(render_task.task_id)
        assert completed_render.state in {"succeeded", "completed"}
        completion = completed_render.result
        assert isinstance(completion, Mapping)
        assert completion["adapter_family"] == "cpu"
        assert completion["capability_digest"] == render_record.capability_digest
        assert completion["source_digest"] == render_record.source_digest
        assert completion["dependency_digest"] == render_record.dependency_digest
        assert completion["provenance"]["kind"] == "astrid.boot_manifest"
        assert completion["provenance"]["sha256"] == restarted_host_state["boot_manifest_hash"]
        assert completion["process_evidence"]["child_boundary"] == "subprocess"
        assert completion["process_evidence"]["attempt_id"]
        assert completion["process_evidence"]["returncode"] == 0

        # The neutral doctor path must remain healthy after all mutations.
        doctor = _run(
            [str(ACTIVE_PYTHON), "-m", "astrid_runtime", "doctor", "--home", str(home), "--json"],
            envless,
        )
        doctor_result = json.loads(doctor.stdout)
        assert doctor_result["healthy"] is True
        realm_root = Path(new_discovery["active_realm"] if False else catalog["realms"][0]["data_root"])
        assert (realm_root / "realm.sqlite3").is_file() or (realm_root / "workspace.sqlite3").is_file()

        # Compile and execute the neutral TypeScript actor against this same
        # restarted endpoint. npm/build are intentionally explicit so a stale
        # globally installed package cannot satisfy the proof.
        runtime_pkg = runtime_input / "packages" / "typescript"
        _run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], {**envless, "CI": "1"}, check=True, cwd=runtime_pkg)
        _run(["npm", "run", "build"], {**envless, "CI": "1"}, check=True, cwd=runtime_pkg)
        conformance = runtime_input / "conformance"
        _run([str(runtime_pkg / "node_modules" / ".bin" / "tsc"), "-p", str(conformance / "tsconfig.json")], {**envless, "CI": "1"}, check=True)
        actor = conformance / "dist" / "conformance" / "fake-second-product.js"
        assert actor.is_file()
        actor_result = _run(["node", str(actor), "--endpoint", restarted["endpoint"], "--token", astrid_token, "--worker-token", new_token, "--executor-id", str(new_discovery["worker_actor"])], {**envless, "ASTRID_RUNTIME_OWNER_TOKEN": "", "ASTRID_RUNTIME_LOCAL_OWNER_TOKEN": ""})
        actor_payload = json.loads(actor_result.stdout)
        assert actor_payload["product"] == "neutral-gallery"
        assert actor_payload["realm_id"] == started["realm_id"]
        assert "claim-heartbeat-fenced-settlement-cas-output" in actor_payload["steps"]

        if installed_mode:
            from astrid_runtime.bootstrap import down
            original = installed_boundary._process
            assert original is installed_owned_handles[-1]
            publish_owner("before-final-down", process=original, operation="installed down")
            stop_owned_installed_host()
            stopped = down(installed_paths, installed_boundary)
            assert stopped["status"] == "stopped" and stopped["realm_id"] == started["realm_id"]
            assert original.wait(timeout=0) is not None
            assert not discovery_path.exists() and not installed_paths.instance_lock_path.exists()
            publish_owner("final-down-reaped", process=original, operation=stopped)
            runtime_pid = None
        if workspace_session is not None:
            workspace_session.runtime_pid = runtime_pid
            stopped = workspace_session.stop()
            runtime_pid = None
            assert stopped["owner_stopped"] is True
            stopped_status = stopped["status"]
            assert stopped_status["discovery"] is None
            stopped_support = stopped_status["support"]
            assert stopped_support["healthy"] is True
            assert stopped_support["catalog_present"] is True
            assert stopped_support["discovery_present"] is False
            assert stopped_support["realm_id"] == started["realm_id"]
            assert stopped_support["issues"] == []
            stopped_provenance = stopped["provenance"]
            assert stopped_provenance["source_manifest_sha256"] == hashlib.sha256(
                source_manifest.read_bytes()
            ).hexdigest()
            assert stopped_provenance["support_root"] == str(support)
    finally:
        if installed_mode:
            _cleanup_installed_owners(
                installed_boundary, installed_owned_handles,
                stop_owned_installed_host, publish_owner,
                primary=sys.exc_info()[1],
            )
        try:
            if (
                workspace_session is not None
                and not workspace_session.closed
            ):
                if runtime_pid is not None:
                    workspace_session.runtime_pid = runtime_pid
                workspace_session.stop()
                runtime_pid = None
        finally:
            for host_pid in (() if installed_mode else host_pids):
                try:
                    os.kill(host_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if runtime_pid is not None and not installed_mode:
                try:
                    os.kill(runtime_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


def test_execution_digest_preserves_venv_symlink_and_rejects_tamper(tmp_path: Path) -> None:
    binary = tmp_path / "base-python"
    binary.write_bytes(b"qualified executable bytes")
    spelling = tmp_path / "venv" / "bin" / "python"
    spelling.parent.mkdir(parents=True)
    spelling.symlink_to(binary)
    record = {"path": str(spelling), "sha256": _sha256(binary)}
    assert _digest_record(record, "python", preserve_execution_path=True) == spelling
    assert _digest_record(record, "python") == binary
    record["sha256"] = "0" * 64
    with pytest.raises(AssertionError, match="does not match"):
        _digest_record(record, "python", preserve_execution_path=True)


def test_installed_profile_rejects_wrong_site_packages(tmp_path: Path) -> None:
    manifest, _ = _write_input_manifest_fixture(tmp_path)
    manifest["mode"] = "installed_artifacts"
    manifest["environment"]["prefix"] = sys.prefix
    manifest["_interpreter"] = str(Path(sys.executable).absolute())
    with pytest.raises(AssertionError, match="installed site-packages mismatch"):
        _configure_input_profile(manifest)


@pytest.mark.parametrize("host_fails,boundary_fails,has_primary", [
    (False, False, False), (True, False, False), (False, True, False),
    (True, True, False), (True, False, True), (False, True, True), (True, True, True),
])
def test_installed_finally_attempts_original_runtime_despite_host_failure(
    host_fails, boundary_fails, has_primary,
):
    from types import SimpleNamespace

    calls = []
    host_error = RuntimeError("host graph unresolved")
    boundary_error = RuntimeError("original boundary unresolved")
    primary = ValueError("workflow primary") if has_primary else None
    process = SimpleNamespace(returncode=None)
    process.wait = lambda timeout: process.returncode
    process.poll = lambda: process.returncode

    def stop_host():
        calls.append("host")
        if host_fails:
            raise host_error

    def stop_boundary():
        calls.append(("boundary", boundary._process))
        if boundary_fails:
            raise boundary_error
        process.returncode = 0

    boundary = SimpleNamespace(_process=process, stop=stop_boundary)
    published = []
    def publish(stage, **fields):
        assert fields["process"] is process
        published.append(stage)

    if primary is None and (host_fails or boundary_fails):
        with pytest.raises(RuntimeError) as captured:
            _cleanup_installed_owners(boundary, [process], stop_host, publish, primary=primary)
        assert captured.value is (host_error if host_fails else boundary_error)
        if host_fails and boundary_fails:
            assert any("original boundary unresolved" in note for note in host_error.__notes__)
    else:
        _cleanup_installed_owners(boundary, [process], stop_host, publish, primary=primary)
        if primary is not None:
            assert str(primary) == "workflow primary"
            assert any("owner cleanup" in note for note in primary.__notes__)
    assert calls == ["host", ("boundary", process)]
    if not boundary_fails:
        assert "failure-cleanup-reaped" in published
    else:
        assert "cleanup-unresolved" in published
