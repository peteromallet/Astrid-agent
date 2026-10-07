"""F05 ordinary v3 actions use the existing admitted host child boundary."""
from __future__ import annotations

import json
import os
import py_compile
import sys
from pathlib import Path

import pytest
import yaml

from astrid.core.execution.generic_host import GenericPackHost, HostCancelled, HostError
from astrid.core.execution.executor.runner import ExecutorRunRequest, run_executor
from astrid.core.execution.executor.registry import ExecutorRegistry
from tests.test_generic_host import FakeRuntime, _write_manifest


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("ASTRID_SOURCE_STATE", str(tmp_path / "absent-state.json"))
    monkeypatch.delenv("ASTRID_PACKS_PATH", raising=False)


def pack(tmp_path, *, code="def run(value='default'):\n    return value\n", path="actions/hello.py", inputs=None, outputs=None, conditions=None):
    root = tmp_path / "nested repo" / "unrelated"
    source = root / path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(code)
    declaration = {
        "description": "An unrelated ordinary action.",
        "invocation": {"kind": "python", "path": path, "function": "run"},
        "inputs": inputs if inputs is not None else [{"name": "value", "type": "string", "required": False, "default": "default"}],
        "outputs": outputs if outputs is not None else {},
        "metadata": {"adapter_family": "cpu", "resource_keys": ["cpu"], "project_scope": "optional"},
    }
    if conditions is not None:
        declaration["conditions"] = conditions
    (root / "pack.yaml").write_text(yaml.safe_dump({"schema_version": 3, "id": "unrelated", "name": "Unrelated", "version": "1.0.0", "actions": {"calculate": declaration}}))
    runtime = FakeRuntime()
    host = GenericPackHost(pack_roots=[root], client=runtime)
    records = host.discover()
    assert [r.id for r in records] == ["unrelated.calculate"]
    definition, admission = host.admit(capability_kind="executor", capability_id="unrelated.calculate")
    return root, host, runtime, definition, admission


def invoke(tmp_path, host, definition, admission, *, inputs=None, dry_run=False, cancelled=None):
    attempt = tmp_path / "attempt"
    return host.invoke_capability(capability_kind="executor", capability_id="unrelated.calculate",
                                  request={"inputs": inputs or {}, "out": str(attempt / "outputs"),
                                           "project": "demo", "project_was_auto_resolved": True,
                                           "python_exec": sys.executable, "verbose": True,
                                           "dry_run": dry_run, "invocation": "runtime"},
                                  attempt=attempt, definition=definition, admission=admission,
                                  cancelled=cancelled)


def test_static_discover_admit_dry_run_and_owned_child(tmp_path):
    sentinel = tmp_path / "imported"
    root, host, runtime, definition, admission = pack(tmp_path, code=f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('entry')\ndef run(value):\n    return {{'value': value, 'pid': __import__('os').getpid()}}\n")
    (root / "__init__.py").write_text(f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('initializer')\n")
    # Refresh after deliberate initializer addition, which invalidates admission.
    host.discover()
    definition, admission = host.admit(capability_kind="executor", capability_id="unrelated.calculate")
    assert not sentinel.exists()
    assert admission["source_roots"] == [str(root)] and admission["python_path_roots"] == []
    assert invoke(tmp_path, host, definition, admission, dry_run=True).ok
    assert not sentinel.exists()
    result = invoke(tmp_path, host, definition, admission)
    assert result.payload["action_result"]["value"] == "default"
    assert result.payload["action_result"]["pid"] != os.getpid()
    assert result.outputs == [] and sentinel.exists()
    host.register()
    assert runtime.registrations[0][1]["resource_keys"] == ["cpu"]


@pytest.mark.parametrize("value", [None, False, 0, "", [], [1, False], {"ok": False, "outputs": [{"path": "/fake"}], "returncode": 99}])
def test_strict_json_results_are_nested_data(tmp_path, value):
    _, host, _, definition, admission = pack(tmp_path, code=f"def run():\n    return {value!r}\n", inputs=[])
    result = invoke(tmp_path, host, definition, admission)
    assert result.ok and result.returncode == 0 and result.outputs == []
    assert result.payload["action_result"] == value


@pytest.mark.parametrize("expression", ["float('nan')", "float('inf')", "object()", "(1, 2)", "{1: 'bad'}", "__import__('pathlib').Path('/fake')"])
def test_non_json_result_fails(tmp_path, expression):
    _, host, runtime, definition, admission = pack(tmp_path, code=f"def run():\n    return {expression}\n", inputs=[])
    with pytest.raises(HostError, match="strict JSON"):
        invoke(tmp_path, host, definition, admission)
    assert runtime.settlements == []


def test_output_schema_validates_return_without_file_ports(tmp_path):
    _, host, _, definition, admission = pack(tmp_path, code="def run(value):\n    return {'answer': 42}\n", outputs={"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]})
    assert definition["outputs"] == []
    assert invoke(tmp_path, host, definition, admission).payload["action_result"] == {"answer": 42}
    definition["metadata"]["action_outputs_schema"]["properties"]["answer"]["type"] = "string"
    from astrid.core.execution.generic_host import _capability_digest
    admission["capability_digest"] = _capability_digest(definition)
    with pytest.raises(HostError, match="answer"):
        invoke(tmp_path, host, definition, admission)


def test_input_schema_before_import_and_only_effective_kwargs(tmp_path):
    sentinel = tmp_path / "imported"
    schema = {"type": "object", "properties": {"value": {"type": "integer", "minimum": 1, "default": 3}}, "additionalProperties": False}
    _, host, _, definition, admission = pack(tmp_path, code=f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\ndef run(**kwargs):\n    return kwargs\n", inputs=schema)
    with pytest.raises(HostError, match="minimum"):
        invoke(tmp_path, host, definition, admission, inputs={"value": 0})
    assert not sentinel.exists()
    assert invoke(tmp_path, host, definition, admission).payload["action_result"] == {"value": 3}


@pytest.mark.parametrize("path, imports", [("actions/hello.py", "from .helper import value\nfrom ..shared import extra\n"), ("actions/larger/run.py", "from ..helper import value\nfrom ...shared import extra\n")])
def test_relative_helpers_initializers_and_source_only_bytecode(tmp_path, path, imports):
    root, host, _, _, _ = pack(tmp_path, path=path, code=imports+"import json\ndef run(value=None):\n    from . import initialized\n    return [value, extra, initialized, json.loads('true')]\n")
    (root / "actions/helper.py").write_text("value = 'cached'\n")
    original_stat = (root / "actions/helper.py").stat()
    py_compile.compile(str(root / "actions/helper.py"), doraise=True)
    (root / "actions/helper.py").write_text("value = 'source'\n")
    os.utime(root / "actions/helper.py", ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    (root / "shared.py").write_text("extra = 'shared'\n")
    (root / Path(path).parent / "__init__.py").write_text("initialized = 'initializer'\n")
    # Function parameter shadows imported value; alias through initializer to prove helper source.
    (root / path).write_text(imports+"import json\ndef run(**kwargs):\n    from . import initialized\n    return [value, extra, initialized, json.loads('true')]\n")
    host.discover()
    definition, admission = host.admit(capability_kind="executor", capability_id="unrelated.calculate")
    assert invoke(tmp_path, host, definition, admission).payload["action_result"] == ["source", "shared", "initializer", True]


@pytest.mark.parametrize("target", ["actions/hello.py", "actions/helper.py", "__init__.py", "pack.yaml", "contract.json"])
def test_whole_pack_mutation_invalidates_admission(tmp_path, target):
    root, host, _, _, _ = pack(tmp_path)
    for name in ["actions/helper.py", "__init__.py", "contract.json"]:
        (root / name).write_text("{}" if name.endswith('.json') else "# admitted\n")
    host.discover()
    definition, admission = host.admit(capability_kind="executor", capability_id="unrelated.calculate")
    (root / target).write_text((root / target).read_text() + "\n# changed\n")
    with pytest.raises(HostError, match="source digest changed"):
        invoke(tmp_path, host, definition, admission)


@pytest.mark.parametrize("tamper", ["root", "source_roots", "ancestor", "digest", "missing"])
def test_admission_identity_is_required(tmp_path, tamper):
    root, host, _, definition, admission = pack(tmp_path)
    if tamper == "root":
        definition["metadata"]["pack_root"] = str(root.parent)
    elif tamper == "source_roots":
        admission["source_roots"] = [str(root / "actions")]
    elif tamper == "ancestor":
        admission["python_path_roots"] = [str(root.parent)]
    elif tamper == "digest":
        admission["capability_digest"] = "bad"
    else:
        admission = {}
    with pytest.raises(HostError, match="admission|admitted|namespace"):
        invoke(tmp_path, host, definition, admission)


@pytest.mark.parametrize("path,function", [("/tmp/escape.py", "run"), ("actions/../escape.py", "run"), ("actions/hello.py", "bad-name")])
def test_malformed_entrypoint_rejected_without_import(tmp_path, path, function):
    _, host, _, definition, admission = pack(tmp_path)
    definition["metadata"]["action_invocation"].update(path=path, function=function)
    from astrid.core.execution.generic_host import _capability_digest
    admission["capability_digest"] = _capability_digest(definition)
    with pytest.raises(HostError, match="invalid Python action"):
        invoke(tmp_path, host, definition, admission)


def test_symlink_escape_rejected(tmp_path):
    root, host, _, definition, admission = pack(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("raise AssertionError('escape imported')\n")
    (root / "actions/hello.py").unlink()
    (root / "actions/hello.py").symlink_to(outside)
    assert host.discover() == ()
    with pytest.raises(HostError, match="source digest changed"):
        invoke(tmp_path, host, definition, admission)


@pytest.mark.parametrize("code,error", [("other = 1\n", "missing or not callable"), ("run = 1\n", "missing or not callable"), ("def run(value):\n    raise RuntimeError('intentional failure')\n", "intentional failure")])
def test_action_failure_uses_existing_host_error(tmp_path, code, error):
    _, host, runtime, definition, admission = pack(tmp_path, code=code)
    with pytest.raises(HostError, match=error):
        invoke(tmp_path, host, definition, admission)
    assert runtime.settlements == []


def test_condition_skip_and_public_runner_bypass_are_import_free(tmp_path, monkeypatch):
    _, host, _, definition, admission = pack(tmp_path, code="raise AssertionError('must not import')\n", conditions=[{"kind": "skip_if_input", "input": "value"}])
    assert invoke(tmp_path, host, definition, admission).ok
    definition["conditions"] = []
    from astrid.core.execution.executor.schema import validate_executor_definition
    monkeypatch.setenv("ASTRID_INTERNAL_INVOCATION", "1")
    request = ExecutorRunRequest(executor_id="unrelated.calculate", out=tmp_path / "out", project="demo", project_was_auto_resolved=True)
    with pytest.raises(ValueError, match="admitted internal host worker"):
        run_executor(request, ExecutorRegistry([validate_executor_definition(definition)]))


def test_command_action_and_v2_command_harvest(tmp_path):
    root, _, _, _, _ = pack(tmp_path)
    manifest = yaml.safe_load((root / "pack.yaml").read_text())
    action = manifest["actions"]["calculate"]
    action["invocation"] = {"kind": "command", "command": {"argv": ["{python_exec}", "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])", "{out}/answer.txt", "{value}"]}}
    action["outputs"] = [{"name": "answer", "type": "file", "mode": "create", "path_template": "{out}/answer.txt", "artifact_type": "text/plain"}]
    (root / "pack.yaml").write_text(yaml.safe_dump(manifest))
    _write_manifest(tmp_path / "legacy")
    runtime = FakeRuntime()
    host = GenericPackHost(pack_roots=[root, tmp_path / "legacy"], client=runtime)
    host.discover()
    assert set(host.capabilities) == {"unrelated.calculate", "test.echo"}
    host.register()
    for index, capability in enumerate(["unrelated.calculate", "test.echo"]):
        task = {"task": {"id": f"task-{index}", "capability": capability, "project_id": "demo", "attempt_id": f"attempt-{index}", "fence": 1, "spec": {"spec": {"inputs": {"value": "argv with spaces"} if index == 0 else {}}}}}
        runtime.tasks[f"task-{index}"] = task
        assert host.run_task(task, lease_token="lease")["task"]["status"] == "completed"
    assert len(runtime.settlements) == 2
    assert any(obj["data"] == b"argv with spaces" for obj in runtime.uploaded_objects.values())


def test_cancelled_python_action_cannot_return_success(tmp_path):
    _, host, runtime, definition, admission = pack(tmp_path, code="import time\ndef run(value):\n    time.sleep(10)\n    return value\n")
    with pytest.raises(HostCancelled):
        invoke(tmp_path, host, definition, admission, cancelled=lambda: True)
    assert runtime.settlements == []


@pytest.mark.parametrize("path", ["actions/hello-world.py", "actions/a-folder/script.v1.py"])
def test_canonical_punctuation_paths_use_opaque_module_names(tmp_path, path):
    _, host, _, definition, admission = pack(tmp_path, path=path)
    assert invoke(tmp_path, host, definition, admission).payload["action_result"] == "default"


def task_for(runtime, capability="unrelated.calculate"):
    task = {"task": {"id": "python-task", "capability": capability, "project_id": "demo", "attempt_id": "python-attempt", "fence": 1, "spec": {"spec": {"inputs": {}}}}}
    runtime.tasks["python-task"] = task
    return task


def test_python_return_settles_with_trusted_evidence_and_no_files(tmp_path):
    _, host, runtime, _, _ = pack(tmp_path, code="def run(value):\n    return {'ok': False, 'returncode': 99, 'outputs': ['/fake']}\n")
    host.register()
    assert host.run_task(task_for(runtime), lease_token="lease")["task"]["status"] == "completed"
    receipt = runtime.settlements[0][2]
    assert receipt["outputs"] == []
    assert receipt["result"]["action_result"] == {"ok": False, "returncode": 99, "outputs": ["/fake"]}
    assert receipt["result"]["process_evidence"]["returncode"] == 0
    assert receipt["result"]["process_evidence"]["process_id"] != os.getpid()
    assert runtime.uploaded_objects == {}


@pytest.mark.parametrize("creates_file", [False, True])
def test_python_declared_files_require_real_attempt_output(tmp_path, creates_file):
    code = "def run(value):\n"
    if creates_file:
        code += "    from pathlib import Path\n    Path('outputs').mkdir(exist_ok=True)\n    Path('outputs/answer.txt').write_text(value)\n"
    code += "    return {'outputs': [{'path': '/fake'}]}\n"
    _, host, runtime, _, _ = pack(tmp_path, code=code, outputs=[{"name": "answer", "type": "file", "mode": "create", "path_template": "{out}/answer.txt", "artifact_type": "text/plain"}])
    host.register()
    if creates_file:
        assert host.run_task(task_for(runtime), lease_token="lease")["task"]["status"] == "completed"
        assert next(iter(runtime.uploaded_objects.values()))["data"] == b"default"
    else:
        with pytest.raises(HostError, match="output"):
            host.run_task(task_for(runtime), lease_token="lease")
        assert runtime.settlements == [] and runtime.uploaded_objects == {}


def test_private_imports_add_no_repository_or_pack_pythonpath(tmp_path):
    root, host, _, definition, admission = pack(tmp_path, code="import os, sys\ndef run(value):\n    return {'sys_path': sys.path, 'pythonpath': os.environ['PYTHONPATH']}\n")
    returned = invoke(tmp_path, host, definition, admission).payload["action_result"]
    assert str(root) not in returned["sys_path"] and str(root.parent) not in returned["sys_path"]
    assert str(root) not in returned["pythonpath"].split(os.pathsep)
    assert str(root.parent) not in returned["pythonpath"].split(os.pathsep)


def test_legacy_child_command_and_ancestor_import_fence(tmp_path):
    _write_manifest(tmp_path / "legacy")
    host = GenericPackHost(pack_roots=[tmp_path / "legacy"])
    definition, admission = host.admit("executor", "test.echo")
    attempt = tmp_path / "legacy-attempt"
    request = {"inputs": {}, "out": str(attempt / "outputs"), "project": "demo", "project_was_auto_resolved": True, "python_exec": sys.executable, "invocation": "runtime"}
    escaped = dict(admission, python_path_roots=[str(tmp_path)])
    with pytest.raises(HostError, match="outside the source fence"):
        host.invoke_capability(capability_kind="executor", capability_id="test.echo", request=request, attempt=attempt, definition=definition, admission=escaped)
    (attempt / "outputs").mkdir(parents=True, exist_ok=True)
    result = host.invoke_capability(capability_kind="executor", capability_id="test.echo", request=request, attempt=attempt, definition=definition, admission=admission)
    assert result.ok and result.outputs[0]["name"] == "answer"


@pytest.mark.parametrize("value", [None, ""])
def test_schema_required_value_preserves_json_semantics(tmp_path, value):
    schema = {"type": "object", "properties": {"value": {"type": ["null", "string"]}}, "required": ["value"]}
    _, host, _, definition, admission = pack(tmp_path, inputs=schema)
    assert invoke(tmp_path, host, definition, admission, inputs={"value": value}).payload["action_result"] == value


def test_cancellation_after_child_result_blocks_success(tmp_path):
    _, host, runtime, definition, admission = pack(tmp_path)
    result_path = tmp_path / "attempt/.astrid-capability-result.json"
    observed = []
    def cancelled():
        if result_path.exists():
            observed.append(json.loads(result_path.read_text()))
            return True
        return False
    with pytest.raises(HostCancelled):
        invoke(tmp_path, host, definition, admission, cancelled=cancelled)
    assert observed[-1]["ok"] is True
    assert runtime.settlements == [] and runtime.uploaded_objects == {}


def test_legacy_builtin_pipeline_selection_is_preserved(tmp_path, monkeypatch):
    from astrid.core.execution.executor import runner
    from astrid.core.execution.executor.schema import validate_executor_definition
    executor = validate_executor_definition({"id": "legacy.pipeline", "name": "Legacy", "kind": "built_in", "version": "1.0.0", "metadata": {"pipeline_step": "existing-step"}})
    request = ExecutorRunRequest(executor_id=executor.id, out=tmp_path, project="demo")
    expected = runner.ExecutorRunResult(executor_id=executor.id, kind="built_in", payload={"legacy": True})
    calls = []
    def builtin(definition, request):
        calls.append((definition.id, request.executor_id))
        return expected
    monkeypatch.setattr(runner, "_run_builtin_executor", builtin)
    assert runner._run_executor_inner(request, executor) is expected
    assert calls == [(executor.id, executor.id)]


def test_legacy_orchestrator_child_command_route(tmp_path):
    source = tmp_path / "legacy_orchestrator"
    source.mkdir()
    (source / "orchestrator.yaml").write_text(yaml.safe_dump({"id": "legacy.compose", "name": "Legacy Compose", "kind": "external", "version": "1.0.0", "runtime": {"kind": "command", "command": {"argv": ["{python_exec}", "-c", "pass"]}}, "inputs": [], "outputs": []}))
    host = GenericPackHost(pack_roots=[source])
    definition, admission = host.admit("orchestrator", "legacy.compose")
    attempt = tmp_path / "orchestrator-attempt"
    result = host.invoke_capability(capability_kind="orchestrator", capability_id="legacy.compose", request={"inputs": {}, "out": str(attempt / "outputs"), "project": "demo", "project_was_auto_resolved": True, "python_exec": sys.executable, "invocation": "runtime"}, attempt=attempt, definition=definition, admission=admission)
    assert result.ok and result.returncode == 0


def test_public_sdk_and_host_share_exact_definition_admission(tmp_path):
    import astrid
    from astrid.core.execution.generic_host import _capability_digest
    root, _, _, definition, admission = pack(tmp_path)
    capability = astrid.get_capability("unrelated.calculate", kind="action", extra_pack_roots=(str(root),))
    assert capability.definition == definition
    assert _capability_digest(capability.definition) == admission["capability_digest"]


@pytest.mark.parametrize("previous_headless", [None, "host-original"])
@pytest.mark.parametrize("loader_fails", [False, True])
def test_host_identity_uses_shared_canonical_bundle_staging(tmp_path, monkeypatch, previous_headless, loader_fails):
    import hashlib
    import inspect
    from types import SimpleNamespace

    from astrid.core.execution.generic_host import _prepare_vibecomfy_execution_identity
    from astrid.packs.vibecomfy import production_engine

    if previous_headless is None:
        monkeypatch.delenv("VIBECOMFY_HEADLESS", raising=False)
    else:
        monkeypatch.setenv("VIBECOMFY_HEADLESS", previous_headless)
    expected_bytes = {
        "workflow.py": b"# synthetic canonical Python member\n",
        "workflow.vibe.json": b'{"identity":"canonical-fixture","revision":"revision-1"}\n',
        "source.json": b'{"source":"synthetic fixture"}\n',
    }
    inputs = {}
    for port, basename in zip(("python", "companion", "source"), expected_bytes):
        member = tmp_path / f"materialized-{port}.bin"
        member.write_bytes(expected_bytes[basename])
        inputs[port] = str(member)
    scratch = tmp_path / "attempt" / "identity"
    profile = {"verified_facts": {"exact": {"model_digest": "model-content-digest"}}}
    loaded = production_engine.LoadedWorkflow(
        resolved=SimpleNamespace(workflow=SimpleNamespace(
            requirements={"runtime": {"comfy_version": "fixture-version"}},
            metadata={"model_assets": ["synthetic-model"], "prompt": "excluded"},
        )),
        model_id="synthetic-model", template_id="synthetic-template",
        workflow_identity="canonical-fixture", workflow_revision="revision-1",
        workflow_content_digest="canonical-content-digest",
    )
    staged = []

    def load_boundary(workflow_path, loader_scratch):
        helper = inspect.currentframe().f_back.f_locals["staged_workflow_path"]
        assert helper.__module__ == "astrid.packs.vibecomfy.shared.bundle_inputs"
        assert Path(inspect.getsourcefile(helper.__wrapped__)).name == "bundle_inputs.py"
        assert os.environ["VIBECOMFY_HEADLESS"] == "1"
        assert workflow_path.name == "workflow.py"
        assert workflow_path.parent.parent == scratch
        assert workflow_path.parent.name.startswith("astrid-vibecomfy-bundle-")
        assert loader_scratch == scratch / "canonical-loader"
        assert {p.name: p.read_bytes() for p in workflow_path.parent.iterdir()} == expected_bytes
        staged.append(workflow_path.parent)
        if loader_fails:
            raise production_engine.ProductionEngineError("synthetic loader failure")
        return loaded

    # Only canonical loading is mocked; staging and both identity functions are real.
    monkeypatch.setattr(production_engine, "load_workflow_path", load_boundary)
    before = (os.environ.copy(), sys.path[:], sys.meta_path[:], Path.cwd())
    if loader_fails:
        with pytest.raises(HostError, match="canonical input preflight failed: synthetic loader failure") as error:
            _prepare_vibecomfy_execution_identity(inputs, scratch, profile)
        assert isinstance(error.value.__cause__, production_engine.ProductionEngineError)
    else:
        expected_identity = hashlib.sha256(json.dumps({
            "model_id": "synthetic-model", "template_id": "synthetic-template",
            "model_digest": "model-content-digest", "workflow_identity": "canonical-fixture",
            "workflow_revision": "revision-1", "workflow_content_digest": "canonical-content-digest",
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        assert _prepare_vibecomfy_execution_identity(inputs, scratch, profile) == (
            expected_identity, "synthetic-model", "synthetic-template", {
                "model_id": "synthetic-model",
                "requirements": {"runtime": {"comfy_version": "fixture-version"}},
                "resident_metadata": {"model_assets": ["synthetic-model"]},
            },
        )
    assert len(staged) == 1 and not staged[0].exists()
    assert list(scratch.iterdir()) == []
    assert {basename: Path(inputs[port]).read_bytes()
            for port, basename in zip(("python", "companion", "source"), expected_bytes)} == expected_bytes
    assert (os.environ.copy(), sys.path[:], sys.meta_path[:], Path.cwd()) == before
