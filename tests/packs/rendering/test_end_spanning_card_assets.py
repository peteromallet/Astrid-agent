"""Managed card overrides stay clip-specific and retain pack fallback assets."""
import copy
from pathlib import Path

import pytest

from astrid.packs.rendering.rendering.renderers.remotion import run as remotion


def test_owner_scoped_card_override_staging(tmp_path):
    clip = {'id': 'first', 'clipType': 'end-spanning-layer',
            'elementRef': {'kind': 'effect', 'packId': 'local', 'id': 'end-spanning-layer'},
            'params': {'cardAssets': {'card0': 'managed-card'}}}
    second = copy.deepcopy(clip)
    second['id'] = 'second'
    second['params'] = {}
    timeline = {'clips': [clip, second]}
    summary = remotion._stage_effect_assets_for_timeline(
        timeline, project_dir=tmp_path, theme_path=None, render_hash='probe',
        asset_registry={'assets': {'managed-card': {'file': '/attempt/materialized/card.png'}}},
    )
    first_assets, second_assets = [c['params']['__astridAssets'] for c in timeline['clips']]
    assert first_assets['card0'] == '/attempt/materialized/card.png'
    assert second_assets['card0'].endswith('card-0-canonical-mink.png')
    assert first_assets['card1'] == second_assets['card1']
    assert (tmp_path / 'public' / first_assets['card1']).is_file()
    assert summary['effects'][0]['source_pack_id'] == 'local'
    assert clip['params']['cardAssets'] == {'card0': 'managed-card'}


@pytest.mark.parametrize('overrides', [[], {'card6': 'key'}, {'card0': ''}, {'card0': '/absolute/card.png'}, {'card0': 'missing'}])
def test_invalid_or_unresolved_overrides_fail_closed(overrides):
    with pytest.raises(ValueError, match='cardAssets|unresolved asset'):
        remotion._resolve_end_spanning_card_assets({'params': {'cardAssets': overrides}}, {'assets': {}})
