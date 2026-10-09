"""The host never posts a settlement the Runtime would refuse unread (64 MiB body limit)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from astrid.core.execution import generic_host
from astrid.core.execution.generic_host import GenericPackHost, HostError

BINDING = dict(run_id="run", task_id="task", attempt_id="attempt", lease_id="lease", fence=1, runtime_epoch=1)


def _host(uploaded: list):
    class Client:
        INLINE_SETTLEMENT_OUTPUTS = True

        def upload_object(self, path, **kwargs):
            uploaded.append((Path(path).name, kwargs["output_key"]))
            return SimpleNamespace(digest="sha256:" + "e" * 64, size=Path(path).stat().st_size)

    host = object.__new__(GenericPackHost)
    host.client = Client()
    return host


def _output(path: Path, size: int, name: str) -> dict:
    with path.open("wb") as stream:
        stream.truncate(size)
    return {"name": name, "path": str(path), "media_type": "application/zip"}


def test_large_outputs_are_uploaded_and_small_ones_inlined(tmp_path):
    uploaded: list = []
    host = _host(uploaded)
    small = _output(tmp_path / "manifest.json", 1024, "filmstrip_manifest")
    big = _output(tmp_path / "bundle.zip", generic_host.INLINE_OUTPUT_MAX_BYTES + 1, "filmstrip_bundle")
    rows = host._upload_outputs([small, big], project_id="p", **BINDING)
    assert "data_base64" in rows[0] and "data_base64" not in rows[1]
    assert uploaded == [("bundle.zip", "filmstrip_bundle")]


def test_an_output_over_the_object_limit_fails_with_a_next_step(tmp_path):
    host = _host([])
    huge = _output(tmp_path / "bundle.zip", generic_host.RUNTIME_OBJECT_LIMIT_BYTES + 1, "filmstrip_bundle")
    with pytest.raises(HostError, match="--resolution 480x270"):
        host._upload_outputs([huge], project_id="p", **BINDING)


def test_without_an_upload_binding_the_inline_budget_is_preflighted(tmp_path):
    host = _host([])
    big = _output(tmp_path / "bundle.zip", generic_host.INLINE_SETTLEMENT_BUDGET_BYTES, "filmstrip_bundle")
    with pytest.raises(HostError, match="inline settlement budget"):
        host._upload_outputs([big], project_id="p")
