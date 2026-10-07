"""Executor runtime resolution and argv construction."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from .registry import ExecutorRegistry


@dataclass
class _ResolutionScope:
    registry: ExecutorRegistry | None = None


_resolution_scope: ContextVar[_ResolutionScope | None] = ContextVar(
    "executor_resolution_scope", default=None
)


@contextmanager
def executor_resolution_scope() -> Iterator[None]:
    """Use one lazily admitted registry for an invocation's command resolution.

    Every invocation (including nested ones) gets a fresh snapshot. Exit,
    including exceptional exit, restores the caller's scope. Nothing is
    cached across invocations or attached to serialized pipeline arguments.
    """
    token = _resolution_scope.set(_ResolutionScope())
    try:
        yield
    finally:
        _resolution_scope.reset(token)


def executor_resolution_registry() -> ExecutorRegistry:
    """Return the invocation snapshot, or freshly discover outside a scope."""
    from .registry import load_default_registry

    scope = _resolution_scope.get()
    if scope is None:
        return load_default_registry()
    if scope.registry is None:
        # Publish only after discovery, admission and graph validation succeed.
        scope.registry = load_default_registry()
    return scope.registry


def resolve_executor_runtime_module(executor_id: str) -> str:
    """Resolve one qualified executor id to its runtime module.

    Unscoped calls rediscover edits and re-registration. An explicitly scoped
    invocation uses the same admitted definitions for selection and commands.
    """
    if not isinstance(executor_id, str) or executor_id.count(".") != 1:
        raise ValueError(
            f"executor id must be qualified as '<pack>.<executor>', got {executor_id!r}"
        )
    registry = executor_resolution_registry()
    executor = registry.get(executor_id)
    runtime_module = executor.metadata.get("runtime_module")
    if not isinstance(runtime_module, str) or not runtime_module:
        raise ValueError(f"executor {executor.id!r} is missing metadata.runtime_module")
    return runtime_module


def executor_argv(executor_id: str, python_exec: str) -> list[str]:
    """Return argv tokens for a qualified executor's module entrypoint."""
    return [python_exec, "-m", resolve_executor_runtime_module(executor_id)]
