import copy
import hashlib
from types import SimpleNamespace

import pytest

from astrid.packs.video_editing import draft_voiceover as vo

OLD = 'sha256:' + '1' * 64
NEW = 'sha256:' + '2' * 64


def fixture(owned=True, audio=True):
    shot = {'payload': {'metadata': {}}, 'internal_timeline': {
        'tracks': [{'id': 'picture', 'kind': 'visual'}, {'id': 'vo', 'kind': 'audio'}],
        'clips': [{'id': 'image', 'asset': 'image', 'at': 0, 'hold': 3, 'track': 'picture'}],
        'registry': {'assets': {}}}}
    if audio:
        shot['internal_timeline']['clips'].append({'id': 'vo', 'media_id': OLD, 'at': 0,
                                                  'from': 0, 'to': 2, 'track': 'vo'})
    if owned:
        shot['payload']['metadata'][vo.KEY] = {'owner': 'video_editing.sync_draft_voiceover',
            'status': 'draft', 'clip_id': 'vo', 'audio_media_id': OLD, 'text': 'Hello',
            'text_digest': vo.digest('Hello'), 'settings': dict(vo.DEFAULT_SETTINGS)}
    candidate = {'project_id': 'p', 'timeline_id': 't', 'base_parent': {'revision_id': 'h'},
        'parent': {'clips': [{'id': 'background', 'at': 0, 'hold': 6, 'track': 'picture'}],
                   'config': {'duration': 6}},
        'placements': [
            {'shot_id': 's1', 'occurrence_id': 'o1', 'placement': {'start_ms': 0}, 'duration_ms': 3000},
            {'shot_id': 's2', 'occurrence_id': 'o2', 'placement': {'start_ms': 3000}, 'duration_ms': 3000}],
        'shots': {'s1': shot, 's2': copy.deepcopy(shot)}}
    scripts = {'revision_id': 'h', 'occurrences': [
        {'occurrence_id': 'o1', 'status': 'present', 'text': 'Hello'},
        {'occurrence_id': 'o2', 'status': 'present', 'text': 'Hello'}]}
    return candidate, scripts


def synthesis(text, settings):
    return {'text': text, 'settings': settings, 'audio_media_id': NEW, 'duration_seconds': 4,
            'run_id': 'r', 'task_id': 'task'}


def test_exact_words_voice_and_selected_identity_reuse():
    c,s = fixture()
    assert not vo.plan_draft_voiceover(c,s)['jobs']
    assert [r['status'] for r in vo.plan_draft_voiceover(c,s)['statuses']] == ['reused','reused']
    s['occurrences'][0]['text'] = 'Hello!'
    assert len(vo.plan_draft_voiceover(c,s)['jobs']) == 1
    assert len(vo.plan_draft_voiceover(c,s,settings={'rate': '+5%'})['jobs']) == 2
    c['shots']['s2']['internal_timeline']['clips'][1]['media_id'] = NEW
    assert len(vo.plan_draft_voiceover(c,s)['jobs']) == 2


def test_missing_audio_regenerates_and_legacy_is_explicit():
    c,s = fixture(audio=False)
    assert len(vo.plan_draft_voiceover(c,s)['jobs']) == 2
    c,s = fixture(owned=False)
    assert not vo.plan_draft_voiceover(c,s)['jobs']
    assert all(r['status']=='protected_audio' for r in vo.plan_draft_voiceover(c,s)['statuses'])
    with pytest.raises(vo.VoiceoverSyncError, match='ambiguous'):
        vo.plan_draft_voiceover(c,s,adopt_clip_ids=['vo'])
    assert len(vo.plan_draft_voiceover(c,s,adopt_clip_ids=['vo'],occurrence_ids=['o1'])['jobs']) == 1
    c['shots']['s1']['payload']['metadata'][vo.KEY] = {'status': 'final'}
    assert len(vo.plan_draft_voiceover(c,s,adopt_clip_ids=['vo'],occurrence_ids=['o2'])['jobs']) == 1


def test_adoption_rejects_clip_ids_reused_across_occurrences():
    c,s=fixture(owned=False)
    with pytest.raises(vo.VoiceoverSyncError, match='ambiguous'):
        vo.plan_draft_voiceover(c,s,adopt_clip_ids=['vo'])


def test_empty_missing_multiple_and_stale_script_handling():
    c,s = fixture()
    s['occurrences'][0].update(status='empty',text='')
    s['occurrences'][1].update(status='multiple',text=None)
    assert [r['status'] for r in vo.plan_draft_voiceover(c,s)['statuses']] == ['empty_script','blocked_script']
    s['revision_id']='different'
    with pytest.raises(vo.VoiceoverSyncError,match='same parent'):
        vo.plan_draft_voiceover(c,s)


def test_failure_never_changes_input_candidate():
    c,s = fixture()
    s['occurrences'][0]['text']='New one';s['occurrences'][1]['text']='New two'
    before = copy.deepcopy(c)
    def fail_second(text,settings):
        if text=='New two': raise RuntimeError('Provider failed')
        return synthesis(text,settings)
    with pytest.raises(RuntimeError,match='Provider failed'):
        vo.prepare_draft_voiceover(c,s,synthesize=fail_second,timing_policy='ripple')
    assert c==before


def test_ripple_updates_placements_stills_and_spanning_parent():
    c,s = fixture()
    s['occurrences'][0]['text']='Long new narration'
    out=vo.prepare_draft_voiceover(c,s,synthesize=synthesis,timing_policy='ripple',padding_seconds=.15)
    work=out['candidate']
    assert work['placements'][0]['duration_ms']==4150
    assert work['placements'][1]['placement']['start_ms']==4150
    assert work['shots']['s1']['internal_timeline']['clips'][0]['hold']==4.15
    assert work['shots']['s1']['internal_timeline']['clips'][1]['to']==4
    assert work['parent']['clips'][0]['hold']==7.15
    assert work['parent']['config']['duration']==7.15
    assert work['shots']['s1']['payload']['metadata'][vo.KEY]['audio_media_id']==NEW
    assert c['placements'][0]['duration_ms']==3000


def test_regenerated_audio_keeps_authored_selector_and_updates_source_mirrors():
    c,s=fixture(owned=False)
    shot=c['shots']['s1']
    clip=next(row for row in shot['internal_timeline']['clips'] if row['id']=='vo')
    clip.pop('media_id')
    clip['asset']='voice'
    shot['internal_timeline']['registry']['assets']['voice']={
        'media_id':OLD,'duration':2,'type':'audio'}
    shot['payload']['assets']=[{'asset_id':'voice','role':'audio','media_type':'audio/wav',
        'object_id':OLD,'digest':OLD,'source':{'media_id':OLD,'content_sha256':OLD,'duration':2}}]
    shot['payload']['items']=[{'item_id':'voice-item','media_id':OLD,
        'metadata':{'asset_key':'vo'}}]
    out=vo.prepare_draft_voiceover(c,s,synthesize=synthesis,timing_policy='ripple',
        occurrence_ids=['o1'],adopt_clip_ids=['vo'])
    updated=out['candidate']['shots']['s1']
    audio=next(row for row in updated['internal_timeline']['clips'] if row['id']=='vo')
    assert audio['asset']=='voice' and 'media_id' not in audio
    assert updated['internal_timeline']['registry']['assets']['voice']=={
        'media_id':NEW,'duration':4,'type':'audio'}
    assert updated['payload']['assets'][0]['object_id']==NEW
    assert updated['payload']['assets'][0]['digest']==NEW
    assert updated['payload']['assets'][0]['source']['media_id']==NEW
    assert updated['payload']['items'][0]['media_id']==NEW


def test_ripple_accepts_canonical_untrimmed_rational_placement_fields():
    c,s = fixture();s['occurrences'][0]['text']='Long new narration'
    c['placements'][0]['source_offset']={'start': 0, 'end': 0}
    c['placements'][0]['speed']={'numerator': 1, 'denominator': 1}
    out=vo.prepare_draft_voiceover(c,s,synthesize=synthesis,timing_policy='ripple')
    assert out['candidate']['placements'][0]['duration_ms']==4150


@pytest.mark.parametrize('field,value', [
    ('source_offset', {'start': 10, 'end': 0}),
    ('speed', {'numerator': 2, 'denominator': 1}),
])
def test_ripple_rejects_nondefault_canonical_placement_fields(field, value):
    c,s=fixture();s['occurrences'][0]['text']='Long'
    c['placements'][0][field]=value
    with pytest.raises(vo.VoiceoverSyncError, match='untrimmed speed-1'):
        vo.prepare_draft_voiceover(c,s,synthesize=synthesis,timing_policy='ripple')


def test_preserve_rejects_overflow_without_retiming():
    c,s=fixture();s['occurrences'][0]['text']='New'
    with pytest.raises(vo.VoiceoverSyncError,match='exceeds preserved'):
        vo.prepare_draft_voiceover(c,s,synthesize=synthesis,timing_policy='preserve')
    def short(text,settings): return {**synthesis(text,settings),'duration_seconds': 2.5}
    work=vo.prepare_draft_voiceover(c,s,synthesize=short,timing_policy='preserve')['candidate']
    assert work['placements']==c['placements']
    assert work['parent']==c['parent']


@pytest.mark.parametrize('unsafe', ['overlap','speed','source_offset','other_audio','parameterized'])
def test_unsafe_ripple_is_rejected(unsafe):
    c,s=fixture();s['occurrences'][0]['text']='Long'
    if unsafe=='overlap':c['placements'][1]['placement']['start_ms']=2000
    elif unsafe=='speed':c['placements'][0]['speed']=2
    elif unsafe=='source_offset':c['placements'][0]['source_offset']=1
    elif unsafe=='other_audio':c['shots']['s1']['internal_timeline']['clips'].append({'id':'music','track':'vo','from':0,'to':2,'at':0})
    else:c['parent']['clips'][0]['params']={'phases':[1,2]}
    with pytest.raises(vo.VoiceoverSyncError):
        vo.prepare_draft_voiceover(c,s,synthesize=synthesis,timing_policy='ripple')


def coordinator_client(c,s,current='h',fail_publish=False):
    media_bytes=b'coordinator-owned-audio'
    media_id='sha256:'+hashlib.sha256(media_bytes).hexdigest()
    for shot in c['shots'].values():
        clips=vo._audio_clips(shot)
        for clip in clips:
            clip['media_id']=media_id
        record=shot['payload']['metadata'].get(vo.KEY)
        if record:
            record['audio_media_id']=media_id
    class Media:
        def read_bytes(self, object_id):
            assert object_id==media_id
            return media_bytes

    class Bound:
        def __init__(self):self.published=False
        def open(self):return copy.deepcopy(c)
        def validate(self,work):return {}
        def diff(self,work):return {'changes':['audio']}
        def preview(self,work):return {'candidate_digest':'sha256:'+'3'*64}
        def publish(self,work,**kwargs):
            if fail_publish:raise RuntimeError('CAS conflict')
            self.published=True;return {'publication':{'head':'new'}}
    bound=Bound(); calls=[0]
    def opened(*args,**kwargs):
        calls[0]+=1
        return {'summary':{'head_revision_id':'h' if calls[0]==1 else current}}
    client=SimpleNamespace(endpoint='http://localhost:8787', media=Media(),
        projects=SimpleNamespace(show=lambda *a:{'project_id':'p'}),
        timelines=SimpleNamespace(open_composition=opened,resolve_scope=lambda *a:{'project_id':'p','timeline_id':'t'},script=lambda *a,**k:s),
        open_authoring_target=lambda target:bound)
    return client,bound


def test_coordinator_noop_and_plan_do_not_synthesize_or_publish(monkeypatch):
    c,s=fixture();client,bound=coordinator_client(c,s)
    monkeypatch.setattr(vo,'runtime_synthesizer',lambda *a:pytest.fail('No synthesis'))
    assert vo.sync_draft_voiceover(client,'p','t',timing_policy='preserve')['status']=='unchanged'
    assert not bound.published
    s['occurrences'][0]['text']='New';client,bound=coordinator_client(c,s)
    assert vo.sync_draft_voiceover(client,'p','t',timing_policy='preserve',plan_only=True)['status']=='planned'
    assert not bound.published


def test_coordinator_detects_concurrent_head_and_cas_failure(monkeypatch):
    c,s=fixture();s['occurrences'][0]['text']='New'
    monkeypatch.setattr(vo,'runtime_synthesizer',lambda *a:synthesis)
    client,bound=coordinator_client(c,s,current='new-head')
    with pytest.raises(vo.VoiceoverSyncError,match='changed during synthesis'):
        vo.sync_draft_voiceover(client,'p','t',timing_policy='ripple')
    assert not bound.published
    client,bound=coordinator_client(c,s,fail_publish=True)
    with pytest.raises(RuntimeError,match='CAS conflict'):
        vo.sync_draft_voiceover(client,'p','t',timing_policy='ripple')
    assert not bound.published


def test_coordinator_publishes_once_with_provenance(monkeypatch):
    c,s=fixture();s['occurrences'][0]['text']='New'
    monkeypatch.setattr(vo,'runtime_synthesizer',lambda *a:synthesis)
    client,bound=coordinator_client(c,s)
    result=vo.sync_draft_voiceover(client,'p','t',timing_policy='ripple')
    assert result['published'] and bound.published
    assert result['generated'][0]['text']=='New'


def test_coordinator_regenerates_missing_fresh_audio_and_surfaces_other_read_errors(monkeypatch):
    c,s=fixture();client,bound=coordinator_client(c,s)
    monkeypatch.setattr(vo,'runtime_synthesizer',lambda *a:synthesis)
    def missing(_object_id):
        error=RuntimeError('missing');error.code='not_found';raise error
    client.media.read_bytes=missing
    result=vo.sync_draft_voiceover(client,'p','t',timing_policy='ripple')
    assert result['published'] and len(result['generated'])==2

    c,s=fixture();client,bound=coordinator_client(c,s)
    def forbidden(_object_id):
        error=RuntimeError('forbidden');error.code='forbidden';raise error
    client.media.read_bytes=forbidden
    with pytest.raises(RuntimeError,match='forbidden'):
        vo.sync_draft_voiceover(client,'p','t',timing_policy='preserve')
    assert not bound.published


def test_synthesizer_verifies_managed_provenance_and_fresh_request_context(monkeypatch):
    import hashlib,json
    import astrid.sdk as sdk
    audio=b'actual-wave-bytes';audio_id='sha256:'+hashlib.sha256(audio).hexdigest()
    manifest={'text':'Hello','text_sha256':vo.digest('Hello')[7:],
              'settings':vo.DEFAULT_SETTINGS,'audio_sha256':audio_id[7:],'duration_seconds':2}
    manifest_bytes=json.dumps(manifest).encode();manifest_id='sha256:'+hashlib.sha256(manifest_bytes).hexdigest()
    reads={audio_id:audio,manifest_id:manifest_bytes};contexts=[]
    def invoke(*args,**kwargs):
        contexts.append(kwargs['idempotency_context'])
        return SimpleNamespace(ok=True,run_id='run',kernel_task_id='task',outputs={'artifacts':[
            {'name':'speech','digest':audio_id},{'name':'speech_manifest','digest':manifest_id}]})
    monkeypatch.setattr(sdk,'invoke',invoke)
    client=SimpleNamespace(media=SimpleNamespace(read_bytes=lambda identity:reads[identity]))
    synth=vo.runtime_synthesizer(client,'p')
    result=synth('Hello',vo.DEFAULT_SETTINGS)
    assert result['audio_media_id']==audio_id and result['duration_seconds']==2
    synth('Hello',vo.DEFAULT_SETTINGS)
    vo.runtime_synthesizer(client,'p')('Hello',vo.DEFAULT_SETTINGS)
    assert contexts[0]==contexts[1] and contexts[1]!=contexts[2]
    reads[audio_id]=b'corrupt'
    with pytest.raises(vo.VoiceoverSyncError,match='hash verification'):
        synth('Hello',vo.DEFAULT_SETTINGS)


def test_invalid_timing_policy_fails_even_noop():
    c,s=fixture();client,_=coordinator_client(c,s)
    with pytest.raises(vo.VoiceoverSyncError,match='timing_policy'):
        vo.sync_draft_voiceover(client,'p','t',timing_policy='guess')
