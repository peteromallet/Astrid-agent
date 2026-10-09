"""Owner identity and support-resource revisions for reconciled V3 elements."""
from dataclasses import replace
from pathlib import Path
import re

import pytest

from astrid.core.element import catalog
from astrid.core.element.registry import load_default_registry
from astrid.core.pack.canonical import validate_canonical_pack
from scripts.gen_effect_registry import _fingerprint_element

ROOT = Path(__file__).resolve().parents[2]


def test_personal_pack_and_owner_qualified_catalog_resources():
    pack = validate_canonical_pack(ROOT / 'astrid/packs/local')
    assert pack.definition.id == 'local'
    assert pack.definition.name == 'Personal'
    rendering_pack = validate_canonical_pack(ROOT / 'astrid/packs/rendering')
    readiness_resources = [
        resource for resource in rendering_pack.resources
        if resource.path == 'elements/_shared/readiness-image.tsx'
    ]
    assert len(readiness_resources) == 1
    assert readiness_resources[0].kind == 'resource:support'
    rendering_helper = ROOT / 'astrid/packs/rendering' / readiness_resources[0].path
    assert rendering_helper.is_file()
    assert not any('/local/' in spec for spec in re.findall(
        r"(?:from\s*|import\s*)['\"]([^'\"]+)['\"]", rendering_helper.read_text()
    ))
    registry = load_default_registry()
    descriptors = catalog.list_element_descriptors(include_shadowed=True)
    rows = {(item['packId'], item['kind'], item['id']): item for item in descriptors}
    assert len(rows) == 25
    assert ('local', 'effect', 'animated-media-transform') in rows
    for owner in ('local', 'rendering'):
        definition = registry.get('effects', 'text-card', pack_id=owner)
        assert rows[(owner, 'effect', 'text-card')]['revision'] == catalog._element_revision(definition)
        assert definition.component.is_file()
    for entry in pack.resources:
        assert (ROOT / "astrid/packs/local" / entry.path).is_file()
    end = registry.get('effects', 'end-spanning-layer', pack_id='local')
    assert {a.name: a.path.name for a in end.assets}['card0'] == 'card-0-canonical-mink.png'
    animated = registry.get('effects', 'animated-media-transform', pack_id='local')
    assert {p.name for p in animated.support_files} == {'motion.ts', 'readiness-image.tsx'}
    assert rows[('local', 'effect', 'animated-media-transform')]['renderability'] == {
        'preview': 'supported', 'browserExport': 'supported', 'workerExport': 'supported',
    }


def _portable_definition(root: Path):
    original = load_default_registry().get('effects', 'animated-media-transform', pack_id='local')
    element_root = root / 'rendering/elements/effects/animated-media-transform'
    element_root.mkdir(parents=True)
    (element_root / 'component.tsx').write_bytes(original.component.read_bytes())
    support = []
    for path in original.support_files:
        dest = root / path.relative_to(ROOT / 'astrid/packs/local')
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
        support.append(dest)
    return replace(original, root=element_root, component=element_root / 'component.tsx', support_files=tuple(support))


def test_declared_helpers_invalidate_catalog_pins_and_render_fingerprints(tmp_path):
    first = _portable_definition(tmp_path / 'first')
    relocated = _portable_definition(tmp_path / 'elsewhere')
    revision, fingerprint = catalog._element_revision(first), _fingerprint_element(first)
    assert catalog._element_revision(relocated) == revision
    assert _fingerprint_element(relocated) == fingerprint
    for helper in relocated.support_files:
        before_revision = catalog._element_revision(relocated)
        before_fingerprint = _fingerprint_element(relocated)
        helper.write_text(helper.read_text() + '\n// changed helper\n')
        assert catalog._element_revision(relocated) != before_revision
        assert _fingerprint_element(relocated) != before_fingerprint
    relocated.support_files[0].unlink()
    with pytest.raises(FileNotFoundError):
        catalog._element_revision(relocated)
    with pytest.raises(FileNotFoundError):
        _fingerprint_element(relocated)
