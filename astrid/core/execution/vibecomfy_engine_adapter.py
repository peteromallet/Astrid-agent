"""Isolated module-local listener adapter for an explicitly selected Vibe build.

This module is stdlib-only. It preserves the selected session module's readiness,
configuration and cleanup code. The private host supplies launch and signal
callbacks; neither numeric PID nor an environment routing hint grants custody.
"""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence


class EngineAdapterError(RuntimeError):
    """A selected engine launch or custody interface cannot be proved."""


class CustodyProcessProxy:
    """Keep async observations/waits while routing every signal to the host."""

    def __init__(self, process: Any, signal_owner: Callable[[int, int], None], observe_exit: Callable[[Any, int], None] | None = None):
        self._process = process
        self._signal_owner = signal_owner
        self._observe_exit = observe_exit
        self._exit_sent = False

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    @property
    def stdin(self):
        return self._process.stdin

    @property
    def stdout(self):
        return self._process.stdout

    @property
    def stderr(self):
        return self._process.stderr

    def _record_exit(self, code: int) -> None:
        if isinstance(code, bool) or not isinstance(code, int):
            raise EngineAdapterError("retained child exit code is unavailable")
        if self._observe_exit is not None and not self._exit_sent:
            self._observe_exit(self._process, code)
            self._exit_sent = True

    async def wait(self):
        code = await self._process.wait()
        self._record_exit(code)
        return code

    async def communicate(self, input=None):
        result = await self._process.communicate(input)
        code = await self._process.wait()
        self._record_exit(code)
        return result

    def send_signal(self, signum: int) -> None:
        self._signal_owner(self.pid, signum)

    def terminate(self) -> None:
        import signal
        self.send_signal(signal.SIGTERM)

    def kill(self) -> None:
        import signal
        self.send_signal(signal.SIGKILL)


class SelectedListenerLauncher:
    """One exact inherited-session listener launch with retained obligations."""

    def __init__(self, *, argv: Sequence[str], cwd: str,
                 launch: Callable[..., Awaitable[Any]],
                 register: Callable[[Any], Awaitable[None]],
                 signal_owner: Callable[[int, int], None],
                 observe_exit: Callable[[Any, int], None] | None = None):
        if not argv or any(not isinstance(part, str) or not part or "\0" in part for part in argv):
            raise EngineAdapterError("selected listener argv is invalid")
        if not Path(argv[0]).is_absolute() or not Path(cwd).is_absolute():
            raise EngineAdapterError("selected listener executable and cwd must be absolute")
        self.argv, self.cwd = tuple(argv), cwd
        self.launch, self.register, self.signal_owner = launch, register, signal_owner
        self.observe_exit = observe_exit
        self.process: Any = None
        self.error: BaseException | None = None
        self._launch_started = False

    async def __call__(self, *argv: str, **kwargs: Any):
        if self._launch_started:
            raise EngineAdapterError("selected listener launch already consumed")
        if tuple(argv) != self.argv or kwargs.get("cwd") != self.cwd:
            raise EngineAdapterError("listener launch differs from selected argv or cwd")
        if kwargs.get("start_new_session", False) or kwargs.get("process_group") is not None:
            raise EngineAdapterError("listener must preserve the inherited engine session")
        if set(kwargs) - {"stdout", "stderr", "env", "cwd", "start_new_session"}:
            raise EngineAdapterError("listener launch contains unselected options")
        self._launch_started = True  # Before await: concurrent/replayed calls cannot duplicate.
        try:
            self.process = await self.launch(*argv, **kwargs)
            # Retain the exact process before registration can fail; the host's
            # pending ledger and initiating error survive dropped Vibe refs.
            await self.register(self.process)
            return CustodyProcessProxy(self.process, self.signal_owner, self.observe_exit)
        except BaseException as exc:
            self.error = exc
            raise


class _ModuleAsyncioProxy:
    def __init__(self, original: Any, launcher: SelectedListenerLauncher):
        self._original, self.create_subprocess_exec = original, launcher

    def __getattr__(self, name: str):
        return getattr(self._original, name)


def install_selected_listener_boundary(module: Any, launcher: SelectedListenerLauncher,
                                       *, expected_source_hash: str,
                                       expected_spawn_hash: str,
                                       expected_cleanup_hashes: Mapping[str, str]) -> None:
    """Replace only the selected module's async-launch alias after exact hashes.

    Missing pins or a different supported seam fail before any engine launch.
    This never changes the process-global asyncio or subprocess modules.
    """
    source_path = Path(getattr(module, "__file__", ""))
    if not source_path.is_absolute() or source_path.is_symlink() or not source_path.is_file():
        raise EngineAdapterError("selected Vibe session source is unavailable")
    actual = "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest()
    if actual != expected_source_hash:
        raise EngineAdapterError("selected Vibe session source hash differs")
    required_cleanup = {"_cleanup_spawn_failure", "_stop_managed_process"}
    if set(expected_cleanup_hashes) != required_cleanup:
        raise EngineAdapterError("selected Vibe cleanup pins are incomplete")
    for name, expected in {"_spawn_comfy_server": expected_spawn_hash, **expected_cleanup_hashes}.items():
        function = getattr(module, name, None)
        try:
            body = inspect.getsource(function).encode("utf-8")
        except (TypeError, OSError) as exc:
            raise EngineAdapterError("selected Vibe listener seam is unavailable") from exc
        if "sha256:" + hashlib.sha256(body).hexdigest() != expected:
            raise EngineAdapterError("selected Vibe launch/cleanup seam hash differs")
    spawn = getattr(module, "_spawn_comfy_server")
    if spawn.__globals__.get("asyncio") is not module.asyncio:
        raise EngineAdapterError("selected Vibe listener launch has no bounded module-local seam")
    module.asyncio = _ModuleAsyncioProxy(module.asyncio, launcher)


def engine_main(payload: Mapping[str, Any]) -> int:
    """Run the pinned daemon in its dedicated process and one local seam."""
    import asyncio
    import importlib.util
    import json
    import os
    import runpy
    import socket
    import sys
    import time

    broker_path = Path(__file__).with_name("custody_broker.py")
    spec = importlib.util.spec_from_file_location("_astrid_engine_custody", broker_path)
    if spec is None or spec.loader is None:
        raise EngineAdapterError("selected host custody consumer is unavailable")
    custody = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(custody)
    launch = payload["engine_launch"]
    pins = launch["adapter_pins"]
    if "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != pins["adapter_source_sha256"]:
        raise EngineAdapterError("selected host adapter artifact differs")
    descriptor = int(os.environ.pop("ASTRID_ENGINE_CONTROL_FD"))
    if descriptor < 3:
        raise EngineAdapterError("private engine control channel is invalid")
    channel = socket.socket(fileno=descriptor)
    channel.settimeout(5)
    original_async_launch = asyncio.create_subprocess_exec
    reference: dict[str, Any] | None = None

    async def registered_launch(*argv: str, **kwargs: Any):
        registration = dict(payload["listener_registration"])
        # The exact selected argv is wrapped only for admitted pre/post-exec
        # registration. The listener keeps the engine's inherited session.
        env = {**kwargs.pop("env", os.environ), **registration}
        executable = payload["listener_wrapper_executable"]
        return await original_async_launch(*custody.custody_wrapper_argv(executable), env=env, **kwargs)

    async def registered(process: Any) -> None:
        nonlocal reference
        authority = custody.RoleCustodyAuthority(Path(payload["custody_scope"]), "engine_listener")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            selected = authority.reference()
            if selected["target"].get("pid") == process.pid and "audit_token_sha256" in selected["target"]:
                # Pending pre-exec is insufficient: await the active post-exec
                # record before exposing a signal-capable proxy to Vibe.
                with authority._guard(exclusive=False):
                    if authority._read()["state"] == "active":
                        reference = selected
                        return
            await asyncio.sleep(0.005)
        raise EngineAdapterError("listener registration did not seal; obligation retained")

    def current_reference(pid: int):
        authority = custody.RoleCustodyAuthority(Path(payload["custody_scope"]), "engine_listener")
        selected = authority.reference()
        if selected["target"].get("pid") != pid:
            raise EngineAdapterError("listener evidence lacks exact retained custody")
        return selected

    def observe_exit(process: Any, code: int) -> None:
        selected = current_reference(process.pid)
        request = {"version": custody.ENGINE_CONTROL_VERSION, "command": "observe_owned_listener_exit",
                   "operation_id": payload["operation_id"], "channel_id": payload["channel_id"],
                   "owner_epoch": payload["owner_epoch"], "role": "engine_listener",
                   "generation": selected["generation"], "target": selected["target"], "exit_code": code}
        custody._send_frame(channel, request)
        response = custody._read_frame(channel)
        if response != {**{k: v for k, v in request.items() if k != "command"}, "status": "exit_recorded"}:
            raise EngineAdapterError("retained listener exit acknowledgement is uncertain")

    def signal_owner(pid: int, signum: int) -> None:
        selected = current_reference(pid)
        request = {"version": custody.ENGINE_CONTROL_VERSION, "command": "signal_owned_listener",
                   "operation_id": payload["operation_id"], "channel_id": payload["channel_id"],
                   "owner_epoch": payload["owner_epoch"], "role": "engine_listener",
                   "generation": selected["generation"], "target": selected["target"], "signal": signum}
        custody._send_frame(channel, request)
        response = custody._read_frame(channel)
        if response != {**{k: v for k, v in request.items() if k != "command"}, "status": "signaled"}:
            raise EngineAdapterError("listener signal acknowledgement is uncertain")

    import vibecomfy.runtime.session as session_module
    selected = SelectedListenerLauncher(argv=launch["listener_argv"], cwd=str(launch["config"]["cwd"]),
                                        launch=registered_launch, register=registered, signal_owner=signal_owner, observe_exit=observe_exit)
    install_selected_listener_boundary(session_module, selected,
                                       expected_source_hash=pins["session_source_sha256"],
                                       expected_spawn_hash=pins["spawn_sha256"],
                                       expected_cleanup_hashes={"_cleanup_spawn_failure": pins["cleanup_sha256"],
                                                                "_stop_managed_process": pins["stop_sha256"]})
    sys.argv = ["vibecomfy.commands.session", "--daemon", "--id", Path(launch["session_root"]).name,
                "--require-source-attestation", "--launch-token", payload["launch_token"],
                "--config", json.dumps(launch["config"], sort_keys=True, separators=(",", ":"))]
    runpy.run_module("vibecomfy.commands.session", run_name="__main__")
    return 0


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("private engine adapter requires one selected payload")
    raise SystemExit(engine_main(json.loads(sys.argv[1])))
