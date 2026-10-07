"""Shared H3 errors used across action and bundle boundaries."""

from __future__ import annotations


class PreparationError(ValueError):
    """The request or its declared assets cannot be prepared safely."""


class CompilationError(ValueError):
    """A prepared request cannot be represented by the selected H3 graph."""


__all__ = ["PreparationError", "CompilationError"]
