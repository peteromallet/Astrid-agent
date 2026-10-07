"""W2.5: CPU/synthetic engine handoff through the real host task/settlement path.

Vibe uses the existing lifecycle CPU command and owned checkout fixture. Wan
uses the W2.2 synthetic upstream in the actual host-owned child interpreter.
No native engine, provider or GPU is loaded; model residency remains unknown.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

from astrid.core.execution import generic_host as gh
from astrid.core.execution.managed_tool_session import (
    SessionCapacityError,
    StaleAdmissionError,
)
from astrid.core.generation.backends import vibecomfy
from astrid.packs.wan2gp.src import driver
from tests.test_generic_host import FakeRuntime
from tests.test_generic_host_vibecomfy_lifecycle import (
    _FakeCheckout,
    _install_fixture,
    _profile,
    _task,
)
from tests.test_wan2gp_adapter_session import _FAKE_API, _fake_upstream


@pytest.fixture
def assembled(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    pack_root = tmp_path / 'packs'
    pack_root.mkdir()
    digest, data = _install_fixture(pack_root)
    upstream, config, trace = _fake_upstream(tmp_path)
    # Extend only the established synthetic upstream, using a native prompt to
    # trigger failure and recording cooperative cancel at the native boundary.
    api = _FAKE_API.replace(
        'if self.settings.get("raise_error"):',
        'if self.settings.get("raise_error") or self.settings.get("prompt") == "FAIL":',
    ).replace(
        '        self.cancel_requested.set()',
        '        self.session._trace("native_cancel", native_job_id=self.job_id)\n'
        '        self.cancel_requested.set()',
    )
    (upstream / 'shared' / 'api.py').write_text(api)
    runtime = FakeRuntime()
    runtime.get_object = lambda requested: data
    events = []
    checkouts = {}
    children = []
    profile = _profile()
    profile['vibecomfy_session']['session_dir'] = str(tmp_path / 'vibe-owner')
    monkeypatch.setattr(gh, '_read_readiness_profile_document', lambda: copy.deepcopy(profile))
    monkeypatch.setattr(gh, '_prepare_vibecomfy_execution_identity', lambda *args: (
        'compatible-vibe', 'model-a', 'template-a',
        {'model_id': 'model-a', 'resident_metadata': {'model_assets': ['model-a']}},
    ))

    class Checkout(_FakeCheckout):
        release_ok = True

        def release(self, *, reason):
            if not self.release_ok:
                events.append('vibe-release-uncertain')
                return {'ok': False, 'released': False}
            evidence = super().release(reason=reason)
            assert self.child.poll() is not None
            events.append('vibe-exited')
            return evidence

    def bind(cls, **kwargs):
        del cls
        birth = kwargs['hc03_profile']['vibecomfy_session']['comfy_process_birth_id']
        events.append(('vibe-bind', birth, kwargs['invocation_identity']))
        # This assertion executes at construction, before any replacement
        # session could allocate, rather than only before its first job.
        assert all(child.poll() is not None for child in children)
        if birth not in checkouts:
            assert all(old.child.poll() is not None for old in checkouts.values())
            checkouts[birth] = Checkout(events, spawn_child=True)
            events.append(('vibe-start', birth))
        else:
            assert checkouts[birth].child.poll() is None
        return checkouts[birth]

    monkeypatch.setattr(vibecomfy.CheckoutServerAdapter, 'from_host_session', classmethod(bind))
    start = gh._ManagedWanChildAdapter.start
    release = gh._ManagedWanChildAdapter.release

    def start_wan(adapter):
        assert all(old.child.poll() is not None for old in checkouts.values())
        assert all(child.poll() is not None for child in children)
        events.append('wan-start')
        start(adapter)
        children.append(adapter.process)

    def release_wan(adapter, **kwargs):
        evidence = release(adapter, **kwargs)
        if evidence['ok']:
            assert adapter.process.poll() is not None
            assert not any(p.pgid == adapter.process.pid for p in gh._process_snapshot().values())
            events.append('wan-exited')
            # Synthetic owner publishes a fresh observed binding only after
            # Wan process-group release. Real HC-03 owner restart is external.
            number = events.count('wan-exited')
            profile['vibecomfy_session'].update(
                process_birth_id=f'vibe-host-after-wan-{number}',
                comfy_process_birth_id=f'comfy-after-wan-{number}',
            )
        return evidence

    monkeypatch.setattr(gh._ManagedWanChildAdapter, 'start', start_wan)
    monkeypatch.setattr(gh._ManagedWanChildAdapter, 'release', release_wan)
    wan_pack = Path(gh.__file__).resolve().parents[2] / 'packs/wan2gp/executors/generate_video'
    host = gh.GenericPackHost(pack_roots=[pack_root, wan_pack], client=runtime,
                              executor_id='w25-integrated-cpu', attempt_base=tmp_path / 'attempts')
    host.discover()
    wan_spec = dict(root=str(upstream), config_path=str(config), python=sys.executable,
                    owner_dir=str(tmp_path / 'wan-owner'), source_digest='synthetic-W2.2',
                    config_digest='synthetic-config', readiness_timeout=5, release_timeout=1)

    def task(name, *, wan=False, prompt='a kite'):
        if wan:
            value = {'task': {'id': name, 'capability': 'wan2gp.generate_video',
                     'project_id': 'fixture-project', 'attempt_id': name + '-attempt', 'fence': 7,
                     'wan_session': wan_spec,
                     'spec': {'spec': {'inputs': {'prompt': prompt, 'model': 'wan-2.2', 'frames': 4}}}}}
        else:
            value = _task(name, digest)
        runtime.tasks[name] = value
        return value

    def run(name, **kwargs):
        return host.run_task(task(name, **kwargs), lease_token='lease-' + name, keep_attempt=True)

    yield host, runtime, run, task, events, checkouts, children, trace, tmp_path
    for checkout in checkouts.values():
        checkout.release_ok = True
    host.shutdown()
    assert not host._active_processes
    assert all(child.poll() is not None for child in children)
    assert all(checkout.child.poll() is not None for checkout in checkouts.values())


def settlement(runtime, name):
    rows = [row for row in runtime.settlements if row[0] == name]
    assert len(rows) == 1
    task_id, lease, payload = rows[0]
    assert lease == 'lease-' + name
    assert payload['attempt_id'] == task_id + '-attempt'
    expected_fence = 7 if task_id.startswith('wan-') else 1
    assert payload['fence'] == expected_fence
    managed = payload['result']['managed_tool_session']
    assert managed['state'] == 'settled'
    assert managed['terminal']['invocation_id'] == f'{task_id}:{task_id}-attempt:{expected_fence}'
    return payload


def test_vibe_wan_vibe_serial_handoff_exact_outputs_and_settlement(assembled, monkeypatch):
    host, runtime, run, task, events, checkouts, children, trace, tmp = assembled
    admissions = []
    admit = host.managed_tool_session.admit

    def capture_admit(**kwargs):
        token = admit(**kwargs)
        admissions.append(token)
        return token

    monkeypatch.setattr(host.managed_tool_session, 'admit', capture_admit)
    for name in ('vibe-first', 'vibe-retained'):
        assert run(name)['task']['status'] == 'completed'
    first_binding = host.managed_tool_session.current_binding
    first_checkout = host.managed_tool_session.current_adapter.backend
    assert first_checkout.child.poll() is None
    assert len(checkouts) == 1
    assert settlement(runtime, 'vibe-first')['result']['managed_tool_session']['binding'] == first_binding.to_dict()
    assert settlement(runtime, 'vibe-retained')['result']['managed_tool_session']['binding'] == first_binding.to_dict()

    # Guard real staging: A/B may not admit a successor while copying or
    # publishing. Native terminal snapshots must precede the actual copy.
    materialize = driver.materialize_host_result
    def guarded(evidence, **kwargs):
        assert evidence['terminal'] is True
        assert evidence['output_snapshots'] == driver.snapshot_native_outputs(
            evidence['result']['generated_files'], evidence['source_root'])
        with pytest.raises(SessionCapacityError):
            admit(capability_id='wan2gp.generate_video', invocation_id='premature-successor')
        return materialize(evidence, **kwargs)
    monkeypatch.setattr(driver, 'materialize_host_result', guarded)
    assert run('wan-A', wan=True)['task']['status'] == 'completed'
    wan_binding = host.managed_tool_session.current_binding
    wan_child = children[0]
    assert events.index('vibe-exited') < events.index('wan-start')
    assert run('wan-B', wan=True)['task']['status'] == 'completed'
    assert host.managed_tool_session.current_binding == wan_binding
    assert len(children) == 1 and wan_child.poll() is None
    a, b = (settlement(runtime, name) for name in ('wan-A', 'wan-B'))
    paths = []
    hashes = []
    for number, (name, payload) in enumerate((('wan-A', a), ('wan-B', b)), 1):
        evidence = payload['result']['wan_native']
        token = admissions[number + 1]
        assert evidence['invocation_id'] == f'{name}:{name}-attempt:7'
        assert evidence['token_id'] == token.token_id
        assert evidence['binding_identity'] == list(token.binding_identity)
        assert evidence['native_job_id'] == f'native-job-{number}'
        assert all(event['identity']['invocation_id'] == evidence['invocation_id'] and
                   event['native_job_id'] == evidence['native_job_id'] for event in evidence['events'])
        attempt = Path(payload['result']['execution_guards']['cleanup_path'])
        assert attempt.is_relative_to(tmp / 'attempts')
        assert name in attempt.name and name + '-attempt' in attempt.name
        assert json.loads((attempt / '.astrid-progress.json').read_text())['percent'] == 1.0
        output = next(o for o in payload['outputs'] if o['name'] == 'generated_videos')
        uploaded = runtime.uploaded_objects[output['digest'].removeprefix('sha256:')]
        assert uploaded['data'] == f'native-wan-bytes-{number}'.encode()
        hashes.append(output['digest'])
        mapped = payload['result']['wan_mapping']
        path = Path(mapped['output_root']) / mapped['generated_files'][0]
        assert path.is_relative_to(attempt)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == output['digest']
        paths.append(path)
    assert hashes[0] != hashes[1] and paths[0].parent != paths[1].parent
    # Late A spool mutation cannot rewrite either copied/published attempt.
    Path(a['result']['wan_native']['result']['generated_files'][0]).write_bytes(b'late-A-write')
    assert [path.read_bytes() for path in paths] == [b'native-wan-bytes-1', b'native-wan-bytes-2']

    assert run('vibe-return')['task']['status'] == 'completed'
    returned_binding = host.managed_tool_session.current_binding
    assert returned_binding.engine_birth_id != first_binding.engine_birth_id
    assert returned_binding.identity_key != wan_binding.identity_key
    assert returned_binding.execution_identity == first_binding.execution_identity
    assert events.index('wan-exited') < events.index(('vibe-start', returned_binding.engine_birth_id))
    assert wan_child.poll() is not None
    assert run('vibe-return-retained')['task']['status'] == 'completed'
    assert host.managed_tool_session.current_binding == returned_binding
    assert len(checkouts) == 2
    for old in admissions[:-2]:
        with pytest.raises(StaleAdmissionError):
            host.managed_tool_session.fence_admission(old, reason='late-old-attempt')
    assert host.managed_tool_session.active
    assert len({token.token_id for token in admissions}) == 6
    assert len(runtime.settlements) == 6 and not runtime.failures
    rows = json.loads(trace.read_text())
    assert [row['event'] for row in rows] == ['init', 'submit', 'submit', 'close']
    print(json.dumps({'handoff': ['Vibe', 'Wan-A', 'Wan-B', 'fresh-compatible-Vibe'],
                      'native_output_sha256': hashes, 'settlements': 6,
                      'events': events, 'model_residency': 'unknown'}, sort_keys=True))


@pytest.mark.parametrize('mode', ['native_failure', 'native_cancel', 'uncertain_cancel', 'stale_event', 'late_result'])
def test_failed_attempt_cannot_publish_or_contaminate_returned_vibe(assembled, monkeypatch, mode):
    host, runtime, run, task, events, checkouts, children, trace, tmp = assembled
    run('vibe-first')
    run('wan-A', wan=True)
    a = settlement(runtime, 'wan-A')['result']['wan_native']
    adapter = host.managed_tool_session.current_adapter
    child = adapter.process
    if mode in {'stale_event', 'late_result'}:
        frame = {'birth': adapter.binding.process_birth_id,
                 'kind': 'event' if mode == 'stale_event' else 'result',
                 'identity': {key: a[key] for key in ('token_id', 'invocation_id', 'session_id', 'generation', 'binding_identity')},
                 'native_job_id': a['native_job_id'], 'result': a['result'],
                 'event': {'kind': 'progress', 'data': {'progress': 0.99}}}
        adapter._buffer.extend(json.dumps(frame).encode() + b'\n')
    elif mode in {'native_cancel', 'uncertain_cancel'}:
        if mode == 'uncertain_cancel':
            # The synthetic native Job receives cancel but ignores it and
            # returns success: intent alone must never publish or settle B.
            api_path = Path(adapter.spec['root']) / 'shared/api.py'
            api_path.write_text(api_path.read_text().replace('        self.cancel_requested.set()', '        pass'))
            # Existing interpreter has imported the old fixture; release it
            # and prepare the changed synthetic source through run_task.
            host.managed_tool_session.release(reason='fixture-native-contract-change')
        current_task = runtime.task
        def cancelled_after_submit(name):
            value = current_task(name)
            if name == 'wan-B' and len([r for r in json.loads(trace.read_text()) if r['event'] == 'submit']) >= 2:
                value['task']['status'] = 'cancelled'
            return value
        monkeypatch.setattr(runtime, 'task', cancelled_after_submit)
    if mode == 'native_cancel':
        assert run('wan-B', wan=True)['status'] == 'cancelled'
    else:
        with pytest.raises(gh.HostError, match='native Wan2GP job failed|stale or unexpected|owned cleanup incomplete'):
            run('wan-B', wan=True, prompt='FAIL' if mode == 'native_failure' else 'a kite')
    assert [row[0] for row in runtime.settlements] == ['vibe-first', 'wan-A']
    assert child.poll() is not None
    assert not list((tmp / 'attempts').glob('*wan-B*/outputs/manifest.json'))
    if mode in {'native_cancel', 'uncertain_cancel'}:
        assert any(row['event'] == 'native_cancel' for row in json.loads(trace.read_text()))
    if mode == 'uncertain_cancel':
        assert host._cleanup_uncertain
        with pytest.raises(gh.HostError, match='cleanup uncertainty'):
            run('vibe-return')
        assert len(checkouts) == 1
    else:
        assert run('vibe-return')['task']['status'] == 'completed'
        settlement(runtime, 'vibe-return')
        assert host.managed_tool_session.active
        assert len(checkouts) == 2
    print(json.dumps({'case': mode, 'settled': [row[0] for row in runtime.settlements],
                      'cleanup_uncertain': host._cleanup_uncertain, 'events': events}, sort_keys=True))


@pytest.mark.parametrize('old_engine', ['vibe', 'wan'])
def test_unverified_release_blocks_replacement_construction(assembled, monkeypatch, old_engine):
    host, runtime, run, task, events, checkouts, children, trace, tmp = assembled
    run('vibe-first')
    if old_engine == 'vibe':
        backend = host.managed_tool_session.current_adapter.backend
        backend.release_ok = False
        next_name, kwargs = 'wan-B', {'wan': True}
    else:
        run('wan-A', wan=True)
        adapter = host.managed_tool_session.current_adapter
        monkeypatch.setattr(adapter, 'release', lambda **kwargs: {'ok': False, 'released': False})
        next_name, kwargs = 'vibe-return', {}
    before = (len(checkouts), len(children), len(runtime.settlements))
    with pytest.raises(SessionCapacityError, match='release was not verified'):
        run(next_name, **kwargs)
    assert (len(checkouts), len(children), len(runtime.settlements)) == before
    assert host.managed_tool_session.occupied and not host.managed_tool_session.active
    with pytest.raises(StaleAdmissionError):
        host.managed_tool_session.admit(capability_id='wan2gp.generate_video', invocation_id='forbidden-successor')
    if old_engine == 'vibe':
        assert backend.child.poll() is None
        backend.release_ok = True
    else:
        assert adapter.process.poll() is None
        monkeypatch.undo()
    print(json.dumps({'failed_release': old_engine, 'replacement_constructed': False,
                      'slot': 'fenced_occupied', 'events': events}, sort_keys=True))
