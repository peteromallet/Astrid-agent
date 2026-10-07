"""Invocation snapshots never become a stale process-wide discovery cache."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from astrid.core.execution.executor import registry as registry_module
from astrid.core.execution.executor.argv import (
    executor_argv,
    executor_resolution_registry,
    executor_resolution_scope,
)


def registry(module):
    definition = SimpleNamespace(id="fixture.action", metadata={"runtime_module": module})
    return SimpleNamespace(get={"fixture.action": definition}.__getitem__)


def test_scope_is_lazy_reuses_admission_and_refreshes_next_invocation(monkeypatch):
    load = Mock(side_effect=[registry("first.run"), registry("edited.run")])
    monkeypatch.setattr(registry_module, "load_default_registry", load)
    with executor_resolution_scope():
        load.assert_not_called()
        snapshot = executor_resolution_registry()
        assert executor_resolution_registry() is snapshot
        for _ in range(20):
            assert executor_argv("fixture.action", "/python with spaces") == [
                "/python with spaces", "-m", "first.run"
            ]
        assert load.call_count == 1
    with executor_resolution_scope():
        assert executor_argv("fixture.action", "python")[-1] == "edited.run"
    assert load.call_count == 2


def test_unscoped_resolution_still_observes_each_registry_change(monkeypatch):
    load = Mock(side_effect=[registry("first.run"), registry("edited.run")])
    monkeypatch.setattr(registry_module, "load_default_registry", load)
    assert executor_argv("fixture.action", "python")[-1] == "first.run"
    assert executor_argv("fixture.action", "python")[-1] == "edited.run"


def test_nested_exception_restores_outer_scope_and_exit_discards_it(monkeypatch):
    load = Mock(side_effect=[registry("outer.run"), registry("inner.run"), registry("next.run")])
    monkeypatch.setattr(registry_module, "load_default_registry", load)
    with executor_resolution_scope():
        assert executor_argv("fixture.action", "python")[-1] == "outer.run"
        with pytest.raises(RuntimeError, match="stop"):
            with executor_resolution_scope():
                assert executor_argv("fixture.action", "python")[-1] == "inner.run"
                raise RuntimeError("stop")
        assert executor_argv("fixture.action", "python")[-1] == "outer.run"
    assert executor_argv("fixture.action", "python")[-1] == "next.run"
    assert load.call_count == 3


def test_failed_admission_is_not_cached(monkeypatch):
    load = Mock(side_effect=[ValueError("invalid declaration"), registry("fixed.run")])
    monkeypatch.setattr(registry_module, "load_default_registry", load)
    with executor_resolution_scope():
        with pytest.raises(ValueError, match="invalid declaration"):
            executor_argv("fixture.action", "python")
        assert executor_argv("fixture.action", "python")[-1] == "fixed.run"
    assert load.call_count == 2


def test_other_thread_does_not_inherit_invocation_snapshot(monkeypatch):
    load = Mock(side_effect=[registry("main.run"), registry("thread.run")])
    monkeypatch.setattr(registry_module, "load_default_registry", load)
    with executor_resolution_scope():
        assert executor_argv("fixture.action", "python")[-1] == "main.run"
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(executor_argv, "fixture.action", "python").result()[-1] == "thread.run"
        assert executor_argv("fixture.action", "python")[-1] == "main.run"


def test_invalid_id_does_not_trigger_discovery(monkeypatch):
    load = Mock()
    monkeypatch.setattr(registry_module, "load_default_registry", load)
    with executor_resolution_scope(), pytest.raises(ValueError, match="qualified"):
        executor_argv("unqualified", "python")
    load.assert_not_called()


@pytest.mark.parametrize("module", [None, "", 7])
def test_scope_preserves_runtime_module_validation(monkeypatch, module):
    monkeypatch.setattr(registry_module, "load_default_registry", lambda: registry(module))
    with executor_resolution_scope(), pytest.raises(ValueError, match="runtime_module"):
        executor_argv("fixture.action", "python")


def test_scope_preserves_unknown_executor_error(monkeypatch):
    monkeypatch.setattr(registry_module, "load_default_registry", lambda: registry("fixture.run"))
    with executor_resolution_scope(), pytest.raises(KeyError):
        executor_argv("fixture.missing", "python")
