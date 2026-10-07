"""Internal child used by :class:`GenericPackHost` for one capability run.

This module is intentionally tiny: admission, leases, output publication, and
settlement remain in the parent host/runtime.  Only pack runtime code executes
here, under ``ASTRID_INTERNAL_INVOCATION=1``.
"""

from __future__ import annotations

import json
import os
import runpy
import socket
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

from .generic_host import _capability_digest, _source_digest_for_roots, _verify_action_admission


def _install_inherited_bridge(fd: str):
    from astrid.sdk._child_bridge import _install_child_bridge

    channel = socket.socket(fileno=int(fd))
    channel.set_inheritable(False)
    return _install_child_bridge(channel)


def _run_child_command(fd: str, encoded: str) -> int:
    """Install before Python command code, preserving its argv and entry point."""
    args = json.loads(encoded)
    if not isinstance(args, list) or not args or any(not isinstance(arg, str) for arg in args):
        raise ValueError("invalid private Python command")
    while args and args[0] in {"-u", "-B"}:
        if args.pop(0) == "-B":
            sys.dont_write_bytecode = True
    if not args or args[0].startswith("-") and args[0] not in {"-c", "-m"}:
        raise ValueError("unsupported delegating Python command flags")
    bridge = _install_inherited_bridge(fd)
    try:
        if args[0] == "-c":
            sys.argv = ["-c", *args[2:]]
            sys.path[0] = ""
            original_main = sys.modules["__main__"]
            command_main = ModuleType("__main__")
            sys.modules["__main__"] = command_main
            try:
                exec(compile(args[1], "<string>", "exec"), command_main.__dict__)
            finally:
                sys.modules["__main__"] = original_main
        elif args[0] == "-m":
            sys.argv = [args[1], *args[2:]]
            sys.path[0] = os.getcwd()
            runpy.run_module(args[1], run_name="__main__", alter_sys=True)
        else:
            sys.argv = args
            sys.path[0] = str(Path(args[0]).resolve().parent)
            runpy.run_path(args[0], run_name="__main__")
        return 0
    finally:
        bridge.close()


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if is_dataclass(value):
        return _json_value(asdict(value))
    return str(value)


def _request_object(raw: Mapping[str, Any], *, kind: str, capability_id: str) -> Any:
    request = dict(raw)
    if kind == "executor":
        from astrid.core.execution.executor.runner import ExecutorRunRequest

        request.setdefault("executor_id", capability_id)
        return ExecutorRunRequest(**request)
    from astrid.core.execution.orchestrator.runner import OrchestratorRunRequest

    request.setdefault("orchestrator_id", capability_id)
    return OrchestratorRunRequest(**request)


def run(payload_path: str | Path) -> int:
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("capability worker payload must be an object")
    kind = str(payload.get("capability_kind") or "")
    capability_id = str(payload.get("capability_id") or "")
    request_payload = payload.get("request")
    if kind not in {"executor", "orchestrator"} or not capability_id:
        raise ValueError("capability worker payload has invalid capability identity")
    if not isinstance(request_payload, Mapping):
        raise ValueError("capability worker payload request must be an object")

    definition_payload = payload.get("definition")
    admission = payload.get("admission")
    if not isinstance(definition_payload, Mapping) or not isinstance(admission, Mapping):
        raise ValueError("capability worker requires an admitted definition and admission fence")
    if str(definition_payload.get("id") or "") != capability_id:
        raise ValueError("admitted definition does not match capability identity")
    expected_digest = str(admission.get("capability_digest") or "")
    if expected_digest and _capability_digest(definition_payload) != expected_digest:
        raise ValueError("admitted capability definition digest changed")
    expected_version = admission.get("version")
    if expected_version is not None and str(definition_payload.get("version")) != str(expected_version):
        raise ValueError("admitted capability version changed")
    _verify_action_admission(definition_payload, admission)
    expected_source = str(admission.get("source_digest") or "")
    source_roots = admission.get("source_roots")
    if isinstance(source_roots, (list, tuple)) and source_roots:
        roots = tuple(Path(str(value)).expanduser().resolve() for value in source_roots)
    elif admission.get("source_root"):
        roots = (Path(str(admission["source_root"])).expanduser().resolve(),)
    else:
        roots = ()
    if roots and expected_source and _source_digest_for_roots(roots) != expected_source:
        raise ValueError("admitted capability source digest changed")
    if kind == "executor":
        from astrid.core.execution.executor.registry import ExecutorRegistry
        from astrid.core.execution.executor.runner import run_executor

        from astrid.core.execution.executor.schema import validate_executor_definition

        registry = ExecutorRegistry([validate_executor_definition(definition_payload)])
        result = run_executor(_request_object(request_payload, kind=kind, capability_id=capability_id), registry,
                              _admission=admission)
    else:
        from astrid.core.execution.orchestrator.registry import OrchestratorRegistry
        from astrid.core.execution.orchestrator.schema import validate_orchestrator_definition
        from astrid.core.execution.orchestrator.runner import run_orchestrator

        result = run_orchestrator(
            _request_object(request_payload, kind=kind, capability_id=capability_id),
            OrchestratorRegistry([validate_orchestrator_definition(definition_payload)]),
        )

    result_path = Path(str(payload["result_path"]))
    result_path.write_text(
        json.dumps(
            {
                "ok": bool(getattr(result, "ok", False)),
                "returncode": getattr(result, "returncode", None),
                "outputs": _json_value(getattr(result, "outputs", {}) or {}),
                "payload": _json_value(getattr(result, "payload", {}) or {}),
                "error": _json_value(getattr(result, "error", None)),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return 0 if bool(getattr(result, "ok", False)) else 1


def serve_wan_session() -> int:
    """Private host JSONL pipe; no Runtime admission or settlement authority.

    Import/initialize upstream only here, once per owned interpreter. Native
    request/event/publication mapping belongs to the pack integration. This
    control seam accepts already-native settings and returns terminal evidence.
    """
    # Keep even native writes to fd 1 off the control channel.
    wire = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    session = None
    birth = ""
    wire_lock = __import__("threading").Lock()
    state_lock = __import__("threading").RLock()
    active: dict[str, Any] = {"job": None, "identity": None, "native_job_id": None}
    last_identity: dict[str, Any] | None = None

    def send(frame: Mapping[str, Any]) -> None:
        with wire_lock:
            wire.write(json.dumps({"birth": birth, **frame}, sort_keys=True) + "\n")
            wire.flush()

    def native_job_id(job: Any) -> str:
        for name in ("job_id", "id", "task_id"):
            value = getattr(job, name, None)
            if value not in (None, ""):
                return str(value)
        # The upstream API exposes the SessionJob object, not a separate
        # scheduler id. Preserve that native object identity for association;
        # this is not a second job registry or generated task id.
        return f"session-job:{id(job):x}"

    def run_job(identity: Mapping[str, Any], settings: Mapping[str, Any]) -> None:
        job = None
        native_id = None
        event_thread = None
        try:
            job = session.submit_task(dict(settings))
            native_id = native_job_id(job)
            with state_lock:
                active.update(job=job, identity=dict(identity), native_job_id=native_id)
            send({"kind": "job_started", "identity": identity, "native_job_id": native_id})

            def forward_events() -> None:
                events = getattr(job, "events", None)
                iterator = getattr(events, "iter", None)
                if not callable(iterator):
                    return
                try:
                    for event in iterator(timeout=0.1):
                        send({
                            "kind": "event",
                            "identity": identity,
                            "native_job_id": native_id,
                            "event": _json_value(event),
                            "event_kind": str(getattr(event, "kind", "event")),
                        })
                except Exception as exc:
                    send({
                        "kind": "event_error",
                        "identity": identity,
                        "native_job_id": native_id,
                        "error": str(exc),
                    })

            event_thread = __import__("threading").Thread(
                target=forward_events, name="astrid-wan-events", daemon=True
            )
            event_thread.start()
            result = job.result()
            event_thread.join(timeout=1.0)
            if event_thread.is_alive():
                raise RuntimeError("native Wan event stream did not reach terminal completion")
            native_result = {
                "success": getattr(result, "success", False) is True,
                "generated_files": _json_value(getattr(result, "generated_files", [])),
                "errors": _json_value(getattr(result, "errors", [])),
                "total_tasks": _json_value(getattr(result, "total_tasks", 0)),
                "successful_tasks": _json_value(getattr(result, "successful_tasks", 0)),
                "failed_tasks": _json_value(getattr(result, "failed_tasks", 0)),
            }
            # Preserve optional artifacts/native result metadata alongside the
            # normal mapping; never rewrite native media metadata.
            serialized = _json_value(result) if is_dataclass(result) else _json_value(vars(result))
            if isinstance(serialized, Mapping):
                native_result.update(serialized)
            from astrid.packs.wan2gp.src.driver import snapshot_native_outputs

            snapshots = snapshot_native_outputs(native_result["generated_files"], frame_init["output_dir"]) if native_result["success"] is True else []
            terminal = {"kind": "result", "identity": identity, "native_job_id": native_id,
                        "result": native_result, "output_snapshots": snapshots}
        except Exception as exc:
            if event_thread is not None:
                event_thread.join(timeout=1.0)
                if event_thread.is_alive():
                    exc = RuntimeError(
                        f"{exc}; native Wan event stream did not reach terminal completion"
                    )
            terminal = {
                "kind": "error",
                "identity": identity,
                "native_job_id": native_id,
                "error": str(exc),
            }
        with state_lock:
            # Admission cannot race terminal emission or predecessor
            # cleanup. A failed send leaves the child occupied.
            send(terminal)
            if event_thread is None or not event_thread.is_alive():
                active.update(job=None, identity=None, native_job_id=None)

    try:
        raw = sys.stdin.buffer.readline(1048577)
        if not raw.endswith(b"\n") or len(raw) > 1048576:
            raise ValueError("invalid Wan initialization frame")
        frame = json.loads(raw)
        birth = frame["birth"]
        root = Path(frame["root"]).resolve()
        os.chdir(root)
        sys.path.insert(0, str(root))
        # This import stays inside the retained child: the general host never
        # imports the heavy Wan runtime.  The selected upstream API is the
        # native ``shared.api.init`` entry point.
        from shared.api import init

        frame_init = dict(frame["init"])
        session = init(**frame_init)
        send({"kind": "ready", "pid": os.getpid()})
        while True:
            raw = sys.stdin.buffer.readline(1048577)
            if not raw:
                break
            if not raw.endswith(b"\n") or len(raw) > 1048576:
                raise ValueError("invalid Wan control frame")
            frame = json.loads(raw)
            if frame.get("birth") != birth:
                raise ValueError("stale Wan session command")
            if frame.get("op") == "close":
                with state_lock:
                    job = active["job"]
                if job is not None:
                    cancel = getattr(job, "cancel", None)
                    if callable(cancel):
                        cancel()
                break
            if frame.get("op") != "run":
                if frame.get("op") == "cancel":
                    identity = frame.get("identity")
                    with state_lock:
                        job = active["job"]
                        current_identity = active["identity"]
                        native_id = active["native_job_id"]
                    if job is None or identity != current_identity:
                        send({"kind": "error", "identity": identity, "native_job_id": native_id,
                              "error": "stale or missing native Wan job"})
                        continue
                    cancel = getattr(job, "cancel", None)
                    if not callable(cancel):
                        send({"kind": "error", "identity": identity, "native_job_id": native_id,
                              "error": "native Wan job does not support cancellation"})
                        continue
                    cancel()
                    send({"kind": "cancel_requested", "identity": identity,
                          "native_job_id": native_id})
                    continue
                raise ValueError("unsupported Wan control command")
            identity = frame["identity"]
            if not isinstance(identity, Mapping) or not all(identity.get(key) for key in ("token_id", "invocation_id", "session_id", "generation", "binding_identity")):
                raise ValueError("invalid Wan admission identity")
            with state_lock:
                if active["identity"] is not None or identity == last_identity:
                    send({"kind": "error", "identity": identity,
                          "error": "Wan session already has an active native job"})
                    continue
                active["identity"] = dict(identity)
                last_identity = dict(identity)
            worker = __import__("threading").Thread(
                target=run_job,
                args=(identity, frame["settings"]),
                name="astrid-wan-job",
                daemon=True,
            )
            worker.start()
    except Exception as exc:
        send({"kind": "error", "error": str(exc)})
        return 1
    finally:
        if session is not None:
            session.close()
        wire.close()
    return 0


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--child-command" and len(sys.argv) == 4:
            raise SystemExit(_run_child_command(sys.argv[2], sys.argv[3]))
        bridge = None
        if len(sys.argv) == 4 and sys.argv[2] == "--child-bridge":
            bridge = _install_inherited_bridge(sys.argv[3])
        elif len(sys.argv) != 2:
            raise ValueError("invalid private worker launch controls")
        try:
            raise SystemExit(serve_wan_session() if sys.argv[1] == "--wan-session" else run(sys.argv[1]))
        finally:
            if bridge is not None:
                bridge.close()
    except Exception as exc:
        # Keep traceback on stderr for the host's bounded diagnostic.  The
        # parent never treats a missing/partial result as successful.
        import traceback

        traceback.print_exc()
        raise SystemExit(1) from exc
