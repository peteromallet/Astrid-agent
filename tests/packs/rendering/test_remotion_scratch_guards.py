from __future__ import annotations

import pytest

from astrid.packs.rendering.backends.remotion import run as remotion_run


def test_scratch_outside_the_public_directory_is_accepted(tmp_path) -> None:
    project = tmp_path / "project"
    (project / "public").mkdir(parents=True)
    remotion_run._assert_outside_public(tmp_path / "attempt" / ".remotion-runtime-1", project)


def test_scratch_inside_the_public_directory_is_refused(tmp_path) -> None:
    project = tmp_path / "project"
    inside = project / "public" / "astrid-effects" / "render-hash"
    with pytest.raises(RuntimeError, match="inside the public directory"):
        remotion_run._assert_outside_public(inside, project)
