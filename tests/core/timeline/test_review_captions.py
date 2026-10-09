"""Review overlay caption phrases: phrase splitting, word timing and script fallback."""
from __future__ import annotations

import pytest

from astrid.core.timeline.review_captions import (
    REVIEW_LINE_CHARS,
    REVIEW_MAX_LINES,
    review_speech_phrases,
    vo_words_for_occurrence,
    wrap_lines,
)


def timed(*entries):
    """Words as (start, end, text) triples in absolute seconds."""
    return [{'start': s, 'end': e, 'text': t} for s, e, t in entries]


def phrases_for(words, script=None, start=0.0, end=20.0):
    return review_speech_phrases(occurrence_id='occ-1', shot_id='ch01', shot_start=start, shot_end=end,
                                 words=words, script_text=script, binding={'binding_id': 'b1', 'head': 2})


def assert_within_budget(phrase):
    lines = phrase['text'].split('\n')
    assert len(lines) <= REVIEW_MAX_LINES
    for line in lines:
        assert len(line) <= REVIEW_LINE_CHARS or ' ' not in line, line


def test_breaks_at_sentence_punctuation_and_at_silence():
    words = timed((0.0, 0.3, 'Hello'), (0.3, 0.6, 'there.'),
                  (0.9, 1.2, 'Next'), (1.2, 1.5, 'one'),  # 0.3 s silence after "there." -> break at the sentence end
                  (1.9, 2.2, 'ends'), (2.2, 2.5, 'here.'))
    assert [p['text'] for p in phrases_for(words)] == ['Hello there.', 'Next one', 'ends here.']


def test_gap_under_threshold_does_not_break_a_phrase():
    words = timed((0.0, 0.3, 'one'), (0.3, 0.6, 'two'), (0.8, 1.0, 'three.'))  # 0.2 s gap: no break
    assert [p['text'] for p in phrases_for(words)] == ['one two three.']


def test_long_phrase_wraps_to_two_lines_of_42_and_splits_into_timed_chunks():
    tokens = ('alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike '
              'november oscar papa quebec romeo sierra tango uniform victor whiskey').split()
    words = [{'start': i * 0.4, 'end': i * 0.4 + 0.35, 'text': t} for i, t in enumerate(tokens)]
    phrases = phrases_for(words, end=30.0)
    assert len(phrases) >= 2
    for phrase in phrases:
        assert_within_budget(phrase)
    # Chunks cover the words in order without overlap.
    rebuilt = ' '.join(' '.join(p['text'].split('\n')) for p in phrases).split()
    assert rebuilt == tokens
    for before, after in zip(phrases, phrases[1:]):
        assert before['render_interval']['end'] <= after['render_interval']['start'] + 1e-9


def test_word_text_comes_from_script_tokens_by_count():
    script = 'One year ago, I invited testers. It was not ready.'
    assert len(script.split()) == 10
    # Timing-only entries, one per script token, as the builder writes them.
    words = [{'start': 0.5 * i, 'end': 0.5 * i + 0.4, 'text': None} for i in range(10)]
    phrases = phrases_for(words, script=script)
    assert all(p['word_aligned'] is True and p['timing_basis'] == 'vo_word' for p in phrases)
    assert phrases[0]['text'] == 'One year ago, I invited testers.'
    assert phrases[1]['text'] == 'It was not ready.'
    assert phrases[1]['render_interval']['start'] == pytest.approx(10 * 0.5 - 2.0)


def test_mismatched_word_count_falls_back_to_distributed_sentences():
    script = 'First sentence here. Second sentence follows.'
    words = [{'start': 0.0, 'end': 0.5, 'text': None}, {'start': 0.5, 'end': 1.0, 'text': None}]
    phrases = phrases_for(words, script=script, start=0.0, end=10.0)
    assert [p['word_aligned'] for p in phrases] == [False, False]
    assert {p['timing_basis'] for p in phrases} == {'shot_script_distributed'}
    assert phrases[0]['text'] == 'First sentence here.'


def test_script_fallback_never_emits_the_whole_script_and_covers_the_shot():
    sentences = [f'Sentence number {n} has a few ordinary words in it.' for n in range(1, 6)]
    script = ' '.join(sentences)
    phrases = phrases_for([], script=script, start=10.0, end=40.0)
    assert len(phrases) >= 5
    assert all(p['text'] != script for p in phrases)
    assert all(len(p['text'].split('\n')) <= REVIEW_MAX_LINES for p in phrases)
    assert phrases[0]['render_interval']['start'] == pytest.approx(10.0)
    assert phrases[-1]['render_interval']['end'] == pytest.approx(40.0)
    for before, after in zip(phrases, phrases[1:]):
        assert before['render_interval']['end'] == pytest.approx(after['render_interval']['start'])
    for phrase in phrases:
        assert_within_budget(phrase)
        assert phrase['word_aligned'] is False


def test_phrases_carry_the_shot_and_binding_provenance():
    phrases = phrases_for(timed((0.0, 0.5, 'Hi.')))
    assert phrases[0]['shot_occurrence_id'] == 'occ-1'
    assert phrases[0]['shot_id'] == 'ch01'
    assert phrases[0]['binding_id'] == 'b1' and phrases[0]['head'] == 2
    assert phrases[0]['id'].startswith('review:occ-1:')


def test_no_text_and_no_words_yields_no_phrases():
    assert phrases_for([], script=None) == []
    assert phrases_for([], script='   ') == []
    assert review_speech_phrases(occurrence_id='o', shot_id='s', shot_start=5, shot_end=5,
                                 words=[], script_text='Text.') == []


def test_vo_words_are_absolute_and_scoped_to_one_occurrence():
    clips = [
        {'id': 'occ-1:vo-a', 'at': 10.0, 'shot_occurrence_id': 'occ-1',
         'app': {'words': [[0.0, 0.5], [0.5, 1.0, 'two']]}},
        {'id': 'occ-1:vo-b', 'at': 12.0, 'shot_occurrence_id': 'occ-1', 'app': {'words': [[0.25, 0.75]]}},
        {'id': 'occ-2:vo', 'at': 0.0, 'shot_occurrence_id': 'occ-2', 'app': {'words': [[0.0, 1.0]]}},
        {'id': 'occ-1:pic', 'at': 10.0, 'shot_occurrence_id': 'occ-1', 'app': {}},
        {'id': 'bad', 'at': 'x', 'shot_occurrence_id': 'occ-1', 'app': {'words': [[0.0, 1.0]]}},
    ]
    words = vo_words_for_occurrence(clips, 'occ-1')
    assert [(w['start'], w['end'], w['text']) for w in words] == [
        (10.0, 10.5, None), (10.5, 11.0, 'two'), (12.25, 12.75, None)]


def test_wrap_lines_respects_the_budget_and_keeps_long_words_whole():
    lines = wrap_lines('a' * 50 + ' short words follow here for wrapping')
    assert lines[0] == 'a' * 50
    assert all(len(line) <= REVIEW_LINE_CHARS or ' ' not in line for line in lines)
