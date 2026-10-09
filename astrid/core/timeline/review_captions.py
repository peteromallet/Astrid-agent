"""Caption phrases for the burned-in render review overlay.

The review overlay shows one caption phrase at a time. A shot is a whole
chapter, so the overlay must never show its entire voiceover script.

- Word-timed: when a shot's VO clips carry ``app.words`` (clip-relative
  seconds), phrases are timed to those words. Word text comes from a third
  element per word when present; otherwise from the shot's voiceover_script
  tokens, matched by count per shot (the builder writes timings only).
- Distributed: without usable word timing, the script is split into sentences
  and each sentence is given a share of the shot proportional to its word
  count. These phrases are marked non-word-aligned.

Phrases break at sentence/clause punctuation or at a silence of at least
``REVIEW_PHRASE_GAP_SECONDS``, and wrap to at most ``REVIEW_MAX_LINES`` lines
of ``REVIEW_LINE_CHARS`` characters.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

REVIEW_LINE_CHARS = 42
REVIEW_MAX_LINES = 2
REVIEW_PHRASE_GAP_SECONDS = 0.25
REVIEW_WEAK_BREAK_MIN_CHARS = 24
REVIEW_HOLD_SECONDS = 0.4

_STRONG_END = ('.', '?', '!', '…')
_WEAK_END = (',', ';', ':', '—', '–')
_CLOSERS = '"\'”’)]»'
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?…])\s+')


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _word_entry(raw: Any) -> tuple[float, float, str | None] | None:
    """Return (start, end, text-or-None) for one ``app.words`` entry, or None if malformed."""
    if isinstance(raw, Mapping):
        start, end = _number(raw.get('s', raw.get('start'))), _number(raw.get('e', raw.get('end')))
        text = raw.get('text', raw.get('tok', raw.get('word')))
    elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
        start, end = _number(raw[0]), _number(raw[1])
        text = raw[2] if len(raw) >= 3 else None
    else:
        return None
    if start is None or end is None or end <= start or start < 0:
        return None
    clean = text.strip() if isinstance(text, str) and text.strip() else None
    return start, end, clean


def _composition_occurrence(clip: Mapping[str, Any]) -> Any:
    app = clip.get('app')
    composition = app.get('astrid_shot_composition') if isinstance(app, Mapping) else None
    return composition.get('occurrence_id') if isinstance(composition, Mapping) else None


def vo_words_for_occurrence(clips: Sequence[Any], occurrence_id: str) -> list[dict[str, Any]]:
    """Absolute-time VO words of one occurrence, sorted by start.

    A flattened clip's ``at`` is its absolute timeline start and ``app.words``
    are seconds from that start. Speed is 1 for builder-written VO, so word
    times are not rescaled.
    """
    words: list[dict[str, Any]] = []
    for clip in clips or []:
        if not isinstance(clip, Mapping):
            continue
        owner = clip.get('shot_occurrence_id') or _composition_occurrence(clip)
        if str(owner or '') != str(occurrence_id):
            continue
        app = clip.get('app')
        raw_words = app.get('words') if isinstance(app, Mapping) else None
        origin = _number(clip.get('at'))
        if not isinstance(raw_words, list) or origin is None:
            continue
        for raw in raw_words:
            entry = _word_entry(raw)
            if entry is None:
                continue
            start, end, text = entry
            words.append({'start': origin + start, 'end': origin + end, 'text': text})
    words.sort(key=lambda word: (word['start'], word['end']))
    return words


def wrap_lines(text: str, width: int = REVIEW_LINE_CHARS) -> list[str]:
    """Greedy word wrap at ``width``; a single over-long word keeps its own line."""
    lines: list[str] = []
    current = ''
    for word in text.split():
        candidate = f'{current} {word}' if current else word
        if len(candidate) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _chunk_sizes(tokens: Sequence[str]) -> list[int]:
    """Sizes of consecutive token chunks that each fit within the line budget."""
    sizes: list[int] = []
    current: list[str] = []
    for token in tokens:
        trial = current + [token]
        if len(wrap_lines(' '.join(trial))) <= REVIEW_MAX_LINES:
            current = trial
        else:
            sizes.append(len(current))
            current = [token]
    if current:
        sizes.append(len(current))
    return sizes


def _phrase_groups(words: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Break a word run at strong punctuation, at weak punctuation once a phrase is long enough, or at a gap."""
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    chars = 0
    for index, word in enumerate(words):
        current.append(word)
        chars += len(word['text']) + 1
        following = words[index + 1] if index + 1 < len(words) else None
        text = word['text'].rstrip(_CLOSERS)
        strong = text.endswith(_STRONG_END)
        weak = text.endswith(_WEAK_END) and chars >= REVIEW_WEAK_BREAK_MIN_CHARS
        gap = following is not None and following['start'] - word['end'] >= REVIEW_PHRASE_GAP_SECONDS
        if following is None or strong or weak or gap:
            groups.append(current)
            current = []
            chars = 0
    return groups


def _phrase(prefix: str, index: int, text: str, start: float, end: float, meta: Mapping[str, Any],
            basis: str, aligned: bool) -> dict[str, Any]:
    phrase = {
        'id': f'{prefix}:{index}',
        'text': text,
        'status': 'projected',
        'render_interval': {'start': round(start, 6), 'end': round(end, 6)},
        'timing_basis': basis,
        'word_aligned': aligned,
    }
    phrase.update(meta)
    return phrase


def _timed_phrases(words: Sequence[dict[str, Any]], *, prefix: str, shot_start: float, shot_end: float,
                   meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    spans: list[tuple[str, float, float]] = []
    for group in _phrase_groups(words):
        tokens = [word['text'] for word in group]
        cursor = 0
        for size in _chunk_sizes(tokens):
            chunk = group[cursor:cursor + size]
            cursor += size
            start = max(shot_start, chunk[0]['start'])
            end = min(shot_end, chunk[-1]['end'])
            if end > start:
                spans.append(('\n'.join(wrap_lines(' '.join(word['text'] for word in chunk))), start, end))
    phrases: list[dict[str, Any]] = []
    for index, (text, start, end) in enumerate(spans):
        following = spans[index + 1][1] if index + 1 < len(spans) else shot_end
        # Hold briefly so the caption does not blink between close words, but
        # never into the next phrase or past the shot.
        hold_end = min(end + REVIEW_HOLD_SECONDS, following, shot_end)
        phrases.append(_phrase(prefix, index, text, start, max(end, hold_end), meta,
                               'vo_word', True))
    return phrases


def _script_phrases(script: str, *, prefix: str, shot_start: float, shot_end: float,
                    meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    units: list[tuple[str, int]] = []
    for sentence in _SENTENCE_SPLIT.split(' '.join(script.split())):
        tokens = sentence.split()
        cursor = 0
        for size in _chunk_sizes(tokens):
            chunk = tokens[cursor:cursor + size]
            cursor += size
            units.append(('\n'.join(wrap_lines(' '.join(chunk))), len(chunk)))
    total = sum(count for _text, count in units)
    if not units or total <= 0:
        return []
    phrases: list[dict[str, Any]] = []
    cursor_time = shot_start
    span = shot_end - shot_start
    for index, (text, count) in enumerate(units):
        end = shot_end if index == len(units) - 1 else cursor_time + span * count / total
        phrases.append(_phrase(prefix, index, text, cursor_time, end, meta,
                               'shot_script_distributed', False))
        cursor_time = end
    return phrases


def review_speech_phrases(*, occurrence_id: str, shot_id: str, shot_start: float, shot_end: float,
                          words: Sequence[Mapping[str, Any]], script_text: str | None,
                          binding: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Review caption phrases for one shot occurrence. Never returns the whole script as one phrase."""
    if not (math.isfinite(shot_start) and math.isfinite(shot_end)) or shot_end <= shot_start:
        return []
    meta: dict[str, Any] = {'shot_id': shot_id, 'shot_occurrence_id': occurrence_id}
    if binding:
        meta.update({key: binding.get(key) for key in ('binding_id', 'head', 'media_id') if binding.get(key) is not None})
    prefix = f'review:{occurrence_id}'
    tokens = script_text.split() if isinstance(script_text, str) else []
    if words:
        if all(word.get('text') for word in words):
            texts: list[str] | None = [str(word['text']) for word in words]
        elif tokens and len(tokens) == len(words):
            texts = tokens
        else:
            texts = None
        if texts is not None:
            timed = [{'start': float(word['start']), 'end': float(word['end']), 'text': text}
                     for word, text in zip(words, texts)]
            phrases = _timed_phrases(timed, prefix=prefix, shot_start=shot_start, shot_end=shot_end, meta=meta)
            if phrases:
                return phrases
    if tokens:
        return _script_phrases(script_text or '', prefix=prefix, shot_start=shot_start, shot_end=shot_end, meta=meta)
    return []


__all__ = [
    'REVIEW_HOLD_SECONDS', 'REVIEW_LINE_CHARS', 'REVIEW_MAX_LINES', 'REVIEW_PHRASE_GAP_SECONDS',
    'review_speech_phrases', 'vo_words_for_occurrence', 'wrap_lines',
]
