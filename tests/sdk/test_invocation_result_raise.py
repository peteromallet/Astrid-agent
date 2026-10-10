"""InvocationResult.raise_for_error: a FAILED task must not pass silently."""

from __future__ import annotations

import pytest

from astrid.sdk.exceptions import CapabilityRuntimeError
from astrid.sdk.results import InvocationResult


def _result(*, ok: bool, error=None, raw_result=None, run_id="run-1") -> InvocationResult:
    return InvocationResult(
        capability_id="pixel.snap",
        capability_type="executor",
        native_kind="executor",
        ok=ok,
        error=error,
        raw_result=raw_result or {},
        run_id=run_id,
    )


def test_raise_for_error_raises_with_the_typed_error_message() -> None:
    failed = _result(
        ok=False,
        error={"message": "grid 51x65 is larger than the 31x64 source", "sdk_category": "runtime"},
    )
    with pytest.raises(CapabilityRuntimeError) as caught:
        failed.raise_for_error()
    text = str(caught.value)
    assert "pixel.snap FAILED" in text
    assert "run run-1" in text
    assert "grid 51x65 is larger than the 31x64 source" in text


def test_raise_for_error_falls_back_to_raw_result_error_then_generic_text() -> None:
    from_raw = _result(ok=False, raw_result={"error": {"message": "boom from raw"}})
    with pytest.raises(CapabilityRuntimeError, match="boom from raw"):
        from_raw.raise_for_error()

    bare = _result(ok=False)
    with pytest.raises(CapabilityRuntimeError, match="task did not succeed"):
        bare.raise_for_error()


def test_raise_for_error_returns_self_on_success_so_it_chains() -> None:
    ok = _result(ok=True)
    assert ok.raise_for_error() is ok
