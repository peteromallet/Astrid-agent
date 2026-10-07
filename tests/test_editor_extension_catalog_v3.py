from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
import yaml

from astrid.core.pack import PackValidationError, discover_packs
from scripts import gen_editor_extension_catalog as generator
from scripts.reshape.package_closure import (
    check_source_resource_closure,
    check_staged_resource_parity,
)

ENTRY = 'ui/live-scenes/extension.tsx'
SUPPORT = 'ui/live-scenes/identity.ts'
ASSET = 'ui/live-scenes/assets/icon.svg'


def fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / 'source'
    root = repo / 'astrid/packs/rendering'
    files = {
        ENTRY: "import { id } from './identity';\nimport icon from './assets/icon.svg?url';\n"
        "export default { manifest: { id, version: '1.0.0', label: 'Live scenes', icon }, activate() {} };\n",
        SUPPORT: "export const id = 'com.reigh.astrid.live-scenes';\n",
        ASSET: '<svg xmlns="http://www.w3.org/2000/svg"/>\n',
        'docs/SKILL.md': '---\nname: rendering\ndescription: Render scenes.\n---\n',
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (root / 'pack.yaml').write_text(yaml.safe_dump({
        'schema_version': 3, 'id': 'rendering', 'name': 'Rendering', 'version': '1.0.0',
        'ui': {'live-scenes': {'type': 'editor', 'entry': ENTRY, 'resources': [
            {'path': SUPPORT, 'kind': 'support'}, {'path': ASSET, 'kind': 'asset'},
        ]}},
        'documentation': {'kind': 'skill', 'path': 'docs/SKILL.md'},
    }))
    core = repo / 'astrid/packs/_core/docs/SKILL.md'
    core.parent.mkdir(parents=True)
    core.write_text('# Core\n')
    return repo


def generate_from(monkeypatch: pytest.MonkeyPatch, repo: Path, output: Path) -> str:
    monkeypatch.setattr(generator, 'discover_packs', lambda: discover_packs(repo / 'astrid/packs'))
    return generator.generate(output)


def test_v3_projection_uses_one_entry_and_f07_resources_in_source_and_staged_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = fixture_repo(tmp_path)
    output = repo / 'astrid/packs/rendering/editor/catalog.ts'
    source = generate_from(monkeypatch, repo, output)
    assert "import editorExtension0 from '../ui/live-scenes/extension'" in source
    assert f"entryPath: '{ENTRY}'" in source
    assert source.count('import editorExtension') == 1
    assert 'identity.ts' not in source and 'icon.svg' not in source
    closure = check_source_resource_closure(repo)
    assert closure.ok, closure.errors
    for relative in (ENTRY, SUPPORT, ASSET):
        projected = 'astrid/packs/rendering/' + relative
        assert closure.digests[projected] == hashlib.sha256((repo / projected).read_bytes()).hexdigest()
    staged = tmp_path / 'installed'
    shutil.copytree(repo, staged)
    assert check_staged_resource_parity(repo, staged).ok
    installed_output = staged / 'astrid/packs/rendering/editor/catalog.ts'
    assert generate_from(monkeypatch, staged, installed_output) == source
    (staged / 'astrid/packs/rendering' / SUPPORT).unlink()
    assert not check_staged_resource_parity(repo, staged).ok
    with pytest.raises(PackValidationError, match='identity.ts.*missing'):
        generate_from(monkeypatch, staged, installed_output)


@pytest.mark.parametrize('mutation,match', [
    ('missing_entry', 'extension.tsx.*missing'),
    ('missing_support', 'identity.ts.*missing'),
    ('missing_asset', 'icon.svg.*missing'),
    ('escaping', 'ui.live-scenes.entry'),
    ('escaping_support', 'ui.live-scenes.resources.0.path'),
    ('escaping_asset', 'ui.live-scenes.resources.1.path'),
    ('symlink_entry', 'symlink'),
    ('symlink_support', 'symlink'),
    ('symlink_asset', 'symlink'),
    ('malformed_declaration', 'ui.live-scenes'),
    ('incompatible_host', 'ui.live-scenes.type'),
    ('non_module', 'browser JavaScript/TypeScript module'),
    ('resource_directory', 'individual support/asset files'),
])
def test_v3_projection_fails_actionably(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        mutation: str, match: str) -> None:
    repo = fixture_repo(tmp_path)
    root = repo / 'astrid/packs/rendering'
    manifest = yaml.safe_load((root / 'pack.yaml').read_text())
    ui = manifest['ui']['live-scenes']
    if mutation.startswith('missing_'):
        (root / {'missing_entry': ENTRY, 'missing_support': SUPPORT, 'missing_asset': ASSET}[mutation]).unlink()
    elif mutation.startswith('symlink_'):
        original = root / {'symlink_entry': ENTRY, 'symlink_support': SUPPORT, 'symlink_asset': ASSET}[mutation]
        destination = tmp_path / 'outside'
        destination.write_bytes(original.read_bytes())
        original.unlink()
        original.symlink_to(destination)
    elif mutation == 'escaping':
        ui['entry'] = '../escape.tsx'
    elif mutation in {'escaping_support', 'escaping_asset'}:
        index = 0 if mutation == 'escaping_support' else 1
        ui['resources'][index]['path'] = '../outside.txt'
    elif mutation == 'malformed_declaration':
        del ui['entry']
    elif mutation == 'incompatible_host':
        ui['type'] = 'app-route'
    elif mutation == 'non_module':
        ui['entry'] = ASSET
    elif mutation == 'resource_directory':
        ui['resources'] = [{'path': 'ui/live-scenes/assets', 'kind': 'asset'}]
    (root / 'pack.yaml').write_text(yaml.safe_dump(manifest))
    with pytest.raises(PackValidationError, match=match):
        generate_from(monkeypatch, repo, root / 'editor/catalog.ts')


def test_projection_retains_sorting_visibility_and_cross_pack_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = fixture_repo(tmp_path)
    packs = repo / 'astrid/packs'
    for pack_id, visibility in [('aaa', 'visible'), ('hidden', 'hidden')]:
        root = packs / pack_id
        root.mkdir()
        (root / 'ui').mkdir()
        (root / 'ui/entry.ts').write_text('export default {};\n')
        (root / 'pack.yaml').write_text(yaml.safe_dump({
            'schema_version': 3, 'id': pack_id, 'name': pack_id, 'version': '1.0.0',
            'visibility': visibility, 'ui': {'editor': {'type': 'editor', 'entry': 'ui/entry.ts'}},
        }))
    result = generate_from(monkeypatch, repo, packs / 'rendering/editor/catalog.ts')
    assert "@astrid/packs/aaa/ui/entry" in result
    assert result.index("packId: 'aaa'") < result.index("packId: 'rendering'")
    assert "packId: 'hidden'" not in result


def test_cli_failure_does_not_overwrite_existing_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    repo = fixture_repo(tmp_path)
    (repo / 'astrid/packs/rendering' / ENTRY).unlink()
    output = tmp_path / 'catalog.ts'
    output.write_text('preserve existing catalog\n')
    monkeypatch.setattr(generator, 'discover_packs', lambda: discover_packs(repo / 'astrid/packs'))
    assert generator.main([str(output)]) == 1
    assert output.read_text() == 'preserve existing catalog\n'
    assert 'editor catalog generation failed:' in capsys.readouterr().err


@pytest.mark.parametrize('fields', [
    {}, {'target': 'video-editor'}, {'compatibility': {'sdk': '1'}},
    {'target': 'video-editor', 'compatibility': {'sdk': '1'}},
])
def test_existing_host_admission_preserves_one_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    fields: dict) -> None:
    repo = fixture_repo(tmp_path)
    manifest_path = repo / 'astrid/packs/rendering/pack.yaml'
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest['ui']['live-scenes'].update(fields)
    manifest_path.write_text(yaml.safe_dump(manifest))
    result = generate_from(monkeypatch, repo, manifest_path.parent / 'editor/catalog.ts')
    assert result.count('import editorExtension') == 1
    assert f"entryPath: '{ENTRY}'" in result
    assert yaml.safe_load(manifest_path.read_text()) == manifest


@pytest.mark.parametrize('field,value', [
    ('target', 'existing-editor'), ('target', 'app-route'),
    ('compatibility', '1'), ('compatibility', {}), ('compatibility', {'sdk': 1}),
    ('compatibility', {'sdk': '2'}), ('compatibility', {'sdk': '>=1'}),
    ('compatibility', {'sdk': '1', 'extra': 'unsupported'}),
])
def test_unsupported_host_constraints_fail_before_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                        capsys: pytest.CaptureFixture[str],
                                                        field: str, value: object) -> None:
    repo = fixture_repo(tmp_path)
    manifest_path = repo / 'astrid/packs/rendering/pack.yaml'
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest['ui']['live-scenes'][field] = value
    manifest_path.write_text(yaml.safe_dump(manifest))
    output = manifest_path.parent / 'editor/catalog.ts'
    output.parent.mkdir()
    output.write_text('preserve previous bytes\n')
    monkeypatch.setattr(generator, 'discover_packs', lambda: discover_packs(repo / 'astrid/packs'))
    assert generator.main([str(output)]) == 1
    assert output.read_text() == 'preserve previous bytes\n'
    error = capsys.readouterr().err
    assert str(manifest_path.resolve()) in error
    assert f'ui.live-scenes.{field}' in error
    assert 'unsupported' in error and 'supports' in error


def test_f08_ui_starter_reaches_catalog_admission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from astrid.core.pack.cli_basic import cmd_new

    repo = tmp_path / 'starter'
    packs = repo / 'astrid/packs'
    packs.mkdir(parents=True)
    root = packs / 'starter'
    assert cmd_new(['starter', '--destination', str(root), '--role', 'ui']) == 0
    result = generate_from(monkeypatch, repo, root / 'editor/catalog.ts')
    assert result.count('import editorExtension') == 1
    assert "entryPath: 'ui/editor/extension.tsx'" in result
    # This checks the declared target; the real donor entry supplies SDK/build
    # proof separately. The starter's placeholder is not activation evidence.
