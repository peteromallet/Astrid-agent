from __future__ import annotations

import asyncio
import signal
from types import SimpleNamespace

import pytest

from astrid.core.execution.vibecomfy_engine_adapter import (
    CustodyProcessProxy, EngineAdapterError, SelectedListenerLauncher,
    install_selected_listener_boundary,
)


class Process:
    pid = 321
    returncode = None
    stdin = stdout = stderr = None

    async def wait(self):
        self.returncode = 0
        return 0

    async def communicate(self, input=None):
        return b"out", b"err"

    def kill(self):
        pytest.fail("numeric process kill bypass")

    terminate = send_signal = kill


def test_proxy_keeps_observations_and_routes_each_signal_to_host():
    process, calls = Process(), []
    proxy = CustodyProcessProxy(process, lambda pid, sig: calls.append((pid, sig)))
    proxy.terminate()
    proxy.kill()
    assert calls == [(321, signal.SIGTERM), (321, signal.SIGKILL)]
    assert proxy.pid == 321
    assert asyncio.run(proxy.communicate()) == (b"out", b"err")
    assert asyncio.run(proxy.wait()) == 0
    assert proxy.returncode == 0


def launcher(*, register_error=None):
    calls = []

    async def launch(*argv, **kwargs):
        calls.append((argv, kwargs))
        return Process()

    async def register(process):
        if register_error:
            raise register_error

    return SelectedListenerLauncher(argv=("/selected/python", "-m", "comfy.cmd.main", "serve"),
                                    cwd="/selected/root", launch=launch, register=register,
                                    signal_owner=lambda *_: None), calls


def test_same_launch_cannot_spawn_twice_and_preserves_inherited_options():
    selected, calls = launcher()
    kwargs = {"cwd": "/selected/root", "env": {"SELECTED": "yes"}, "stdout": object(), "stderr": object()}
    asyncio.run(selected(*selected.argv, **kwargs))
    assert calls == [(selected.argv, kwargs)]
    with pytest.raises(EngineAdapterError, match="consumed"):
        asyncio.run(selected(*selected.argv, **kwargs))
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["argv", "cwd", "session", "extra"])
def test_unselected_launch_never_spawns(change):
    selected, calls = launcher()
    argv, kwargs = selected.argv, {"cwd": selected.cwd}
    if change == "argv":
        argv = ("/unselected/python", *argv[1:])
    elif change == "cwd":
        kwargs["cwd"] = "/unselected/root"
    elif change == "session":
        kwargs["start_new_session"] = True
    else:
        kwargs["shell"] = True
    with pytest.raises(EngineAdapterError):
        asyncio.run(selected(*argv, **kwargs))
    assert not calls


def test_registration_failure_retains_process_obligation_and_initiating_error():
    error = RuntimeError("registration failed")
    selected, calls = launcher(register_error=error)
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(selected(*selected.argv, cwd=selected.cwd))
    assert raised.value is error
    assert selected.error is error
    assert selected.process.pid == 321
    with pytest.raises(EngineAdapterError, match="consumed"):
        asyncio.run(selected(*selected.argv, cwd=selected.cwd))
    assert len(calls) == 1


def test_missing_selected_source_fails_before_mutating_global_asyncio(tmp_path):
    selected, _ = launcher()
    module = SimpleNamespace(__file__=str(tmp_path / "absent.py"), asyncio=asyncio)
    with pytest.raises(EngineAdapterError, match="unavailable"):
        install_selected_listener_boundary(module, selected, expected_source_hash="sha256:missing",
                                           expected_spawn_hash="sha256:missing", expected_cleanup_hashes={})
    assert module.asyncio is asyncio
