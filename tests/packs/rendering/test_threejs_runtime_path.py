"""Server-owned runtime resolution, without hosts, task admission, or rendering."""

from pathlib import Path

import pytest

from astrid.core.rendering import remotion_runtime
from astrid.core.rendering.contracts import SCHEMA_VERSION, RenderRequest
from astrid.core.rendering.errors import RendererUnsupportedError
from astrid.packs.rendering.rendering.renderers.remotion import run as remotion_backend
from astrid.packs.rendering.rendering.renderers.threejs import run as threejs


def _request(tmp_path: Path, **kwargs: object) -> RenderRequest:
    return RenderRequest.from_dict(
        {
            "schema_version": SCHEMA_VERSION,
            "timeline_path": str(tmp_path / "timeline.json"),
            "assets_registry_path": None,
            "output_name": "test.mp4",
            **kwargs,
        }
    ).for_backend(threejs.BACKEND_ID)


@pytest.mark.parametrize("configured", [False, True])
def test_threejs_support_and_execution_share_host_project_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: bool
) -> None:
    checkout = tmp_path / "checkout"
    monkeypatch.setattr(remotion_runtime, "REPO_ROOT", checkout)
    monkeypatch.delenv(remotion_runtime.REMOTION_PROJECT_DIR_ENV, raising=False)
    expected = checkout / "remotion"
    if configured:
        expected = tmp_path / "server-owned-bundle"
        monkeypatch.setenv(remotion_runtime.REMOTION_PROJECT_DIR_ENV, str(expected))

    status = remotion_runtime.remotion_runtime_status(require_explicit_project=configured)
    assert status.project_dir == expected
    assert threejs._default_settings().project_dir == expected
    request = _request(tmp_path)
    assert threejs._settings_from_request(request, tmp_path).project_dir == expected

    # Stop execution at project preflight. No runtime, assets, or frames are produced.
    seen: list[Path] = []

    def missing_project(project_dir: Path) -> list[str]:
        seen.append(project_dir)
        return [f"Remotion project directory not found: {project_dir}"]

    monkeypatch.setattr(threejs, "_threejs_project_reasons", missing_project)
    monkeypatch.setattr(threejs, "_binaries_reasons", lambda: [])
    monkeypatch.setattr(threejs, "_serialize_timeline", lambda _path: {})
    monkeypatch.setattr(threejs, "_resolved_theme_for_render", lambda *_args: {})
    monkeypatch.setattr(threejs, "_load_registry_mapping", lambda _path: {})
    monkeypatch.setattr(threejs, "_support_reasons", lambda *_args, **_kwargs: [])

    def forbid_render(*_args: object, **_kwargs: object) -> None:
        pytest.fail("runtime resolution checks must never execute a render")

    monkeypatch.setattr(threejs, "_execute_remotion", forbid_render)
    report = threejs.support(request, workspace=tmp_path)
    assert report.supported is False
    assert str(expected) in report.reasons[0]
    with pytest.raises(RendererUnsupportedError, match="render environment"):
        threejs._protocol_render(request, workspace=tmp_path)
    assert seen == [expected, expected]
    assert not (tmp_path / "outputs").exists()


def test_threejs_invalid_explicit_project_never_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(remotion_runtime.REMOTION_PROJECT_DIR_ENV, "request-relative/remotion")
    status = remotion_runtime.remotion_runtime_status(require_explicit_project=True)
    assert status.project_dir is None
    assert status.reason is not None and "must be an absolute path" in status.reason
    request = _request(tmp_path)
    report = threejs.support(request, workspace=tmp_path)
    assert report.supported is False
    assert report.reasons == [status.reason]
    with pytest.raises(RendererUnsupportedError, match="render environment"):
        threejs._protocol_render(request, workspace=tmp_path)
    assert not (tmp_path / "outputs").exists()


def test_threejs_request_cannot_select_project_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted = tmp_path / "server-owned-bundle"
    monkeypatch.setenv(remotion_runtime.REMOTION_PROJECT_DIR_ENV, str(trusted))
    request = _request(
        tmp_path,
        backend_config={threejs.BACKEND_ID: {"project_dir": str(tmp_path / "untrusted")}},
    )
    report = threejs.support(request, workspace=tmp_path)
    assert report.supported is False
    assert report.reasons == ["unknown rendering.threejs configuration: project_dir"]
    with pytest.raises(RendererUnsupportedError, match="invalid rendering.threejs configuration"):
        threejs._protocol_render(request, workspace=tmp_path)
    assert threejs._default_settings().project_dir == trusted


def test_host_still_requires_explicit_project_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(remotion_runtime.REMOTION_PROJECT_DIR_ENV, raising=False)
    status = remotion_runtime.remotion_runtime_status(require_explicit_project=True)
    assert status.available is False
    assert status.project_dir is None
    assert status.reason == (
        f"{remotion_runtime.REMOTION_PROJECT_DIR_ENV} must point to the server-owned Remotion project"
    )


@pytest.mark.parametrize("installed_node_modules", [False, True])
def test_remotion_missing_dependencies_report_resolved_directory(
    tmp_path: Path, installed_node_modules: bool
) -> None:
    project_dir = tmp_path / "server-owned-bundle"
    project_dir.mkdir()
    (project_dir / "package.json").write_text("{}", encoding="utf-8")
    if installed_node_modules:
        (project_dir / "node_modules").mkdir()
    with pytest.raises(FileNotFoundError, match=str(project_dir / "node_modules")):
        remotion_backend._validate_project_dir(project_dir)


@pytest.mark.parametrize('kind', ['absent', 'directory-without-package', 'file'])
def test_threejs_missing_host_project_fails_support_and_execution(tmp_path, monkeypatch, kind):
    configured = tmp_path / 'configured-project'
    if kind == 'directory-without-package':
        configured.mkdir()
    elif kind == 'file':
        configured.write_text('not a project')
    monkeypatch.setenv(remotion_runtime.REMOTION_PROJECT_DIR_ENV, str(configured))
    monkeypatch.setattr(threejs, '_binaries_reasons', lambda: [])
    monkeypatch.setattr(threejs, '_serialize_timeline', lambda _path: {})
    monkeypatch.setattr(threejs, '_resolved_theme_for_render', lambda *_args: {})
    monkeypatch.setattr(threejs, '_load_registry_mapping', lambda _path: {})
    monkeypatch.setattr(threejs, '_support_reasons', lambda *_args, **_kwargs: [])
    request = _request(tmp_path)
    report = threejs.support(request, workspace=tmp_path)
    assert not report.supported
    assert any(str(configured) in reason for reason in report.reasons)
    with pytest.raises(RendererUnsupportedError, match='render environment'):
        threejs._protocol_render(request, workspace=tmp_path)
    assert not (tmp_path / 'outputs').exists()
