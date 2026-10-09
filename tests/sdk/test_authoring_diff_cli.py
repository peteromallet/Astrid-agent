from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from astrid.core.cli.domain_output import print_result
from astrid.packs.timeline.cli import build_parser
from astrid.sdk.contracts import DomainResult, ErrorObject
from tests.sdk.test_authoring_revision_diff import HistoricalReader


def _parse(reader, *options):
    parser = build_parser(SimpleNamespace(timelines=reader))
    return parser.parse_args(['diff', 'main', '--project', 'project-1',
                              '--from-revision', 'parent-1', '--to-revision', 'parent-2', *options])


@pytest.mark.parametrize('options', [(), ('--json',), ('--format', 'json')])
def test_actual_cli_json_modes_keep_exact_envelope_and_result(options, capsys):
    reader = HistoricalReader()
    parsed = _parse(reader, *options)
    assert parsed.handler(parsed) == 0
    out, err = capsys.readouterr()
    envelope = json.loads(out)
    assert set(envelope) == {'ok', 'data', 'error', 'receipt', 'idempotency_key'}
    assert envelope['ok'] is True and err == ''
    assert envelope['data'] == json.loads(json.dumps(reader.diff(
        'project-1', 'main', from_revision='parent-1', to_revision='parent-2').data))


@pytest.mark.parametrize('mode', ['readable', 'details'])
def test_actual_cli_human_modes_keep_labels_pair_unknown_changes_and_all_details(mode, capsys):
    reader = HistoricalReader()
    parsed = _parse(reader, '--format', mode)
    assert parsed.handler(parsed) == 0
    out, err = capsys.readouterr()
    assert err == ''
    assert 'Opening' in out and 'occ-1' in out and 'occ-2' in out
    assert 'picture-1' in out and 'parent-1' in out and 'parent-2' in out
    assert 'rect' in out and 'metadata' in out and 'changed' in out
    if mode == 'details':
        result = reader.diff('project-1', 'main', from_revision='parent-1', to_revision='parent-2')
        for change in result.data['changes']:
            assert change['path'] in out


@pytest.mark.parametrize('mode', ['readable', 'details'])
def test_human_error_keeps_stderr_exit_and_does_not_render_partial_success(mode, capsys):
    reader = HistoricalReader()
    del reader.rows['internal-2']
    parsed = _parse(reader, '--format', mode)
    assert parsed.handler(parsed) == 1
    out, err = capsys.readouterr()
    assert out == ''
    assert err.startswith('error not_found:')
    assert 'Opening' not in err


def test_json_and_format_are_mutually_exclusive_before_any_sdk_read():
    reader = HistoricalReader()
    with pytest.raises(SystemExit) as error:
        _parse(reader, '--json', '--format', 'readable')
    assert error.value.code == 2
    assert reader.calls == []


def test_explicit_legacy_pair_details_retains_full_legacy_result_without_pinned_claim(capsys):
    reader = HistoricalReader()
    parser = build_parser(SimpleNamespace(timelines=reader))
    parsed = parser.parse_args(['diff', 'main', '--project', 'project-1',
                                '--from-version', '1', '--to-version', '2', '--format', 'details'])
    assert parsed.handler(parsed) == 0
    out, err = capsys.readouterr()
    assert err == ''
    assert 'from_version' in out and 'to_version' in out
    assert 'legacy' in out.lower()
    assert 'pinned composition comparison' not in out


def test_shared_success_renderer_never_changes_json_or_failure_contract(capsys):
    calls = []
    def callback(data):
        calls.append(data)
        return 'readable report'
    success = DomainResult.success({'timeline_id': 'main'})
    failure = DomainResult.failure(ErrorObject('not_found', 'missing', {}))
    assert print_result(success, human_renderer=callback) == 0
    assert capsys.readouterr().out == 'readable report\n'
    assert calls == [{'timeline_id': 'main'}]
    calls.clear()
    assert print_result(success, as_json=True, human_renderer=callback) == 0
    assert json.loads(capsys.readouterr().out) == success.as_dict()
    assert calls == []
    assert print_result(failure, human_renderer=callback) == 1
    output = capsys.readouterr()
    assert output.out == '' and output.err == 'error not_found: missing\n'
    assert calls == []
