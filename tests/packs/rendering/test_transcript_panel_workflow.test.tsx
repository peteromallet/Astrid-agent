// @vitest-environment jsdom
import { createElement } from 'react';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ExtensionContext, PanelVideoAdapter, TimelineSnapshot, TranscriptMediaChoice, TranscriptPanelAdapter, TranscriptPanelSnapshot } from '@astrid/editor-sdk';
import { alignedTextParts, filmParagraphs, playedFromReader, projectPlayedOccurrences, TranscriptPanel } from '../../../astrid/packs/rendering/ui/transcript/TranscriptPanel';
afterEach(cleanup);
function setup() {
  let mediaChoices: TranscriptMediaChoice[] = [{ assetId: 'asset', label: 'Interview.mp4', kind: 'video' }];
  let snapshot: TranscriptPanelSnapshot = { sources: [{ sourceItemId: 's', sourceId: 'default', assetId: 'asset', documentId: 'document', documentVersion: 4, start: 0, end: 4, speaker: 'Alex', text: 'Hello, world!  Hello.', words: [{ id: 'w', text: 'Hello', start: 0, end: 1 }, { id: 'w2', text: 'world', start: 1, end: 2 }, { id: 'w3', text: 'Hello', start: 2, end: 3 }] }], occurrences: [{ id: 'o', sourceItemId: 's', sourceId: 'default', assetId: 'asset', clipId: 'clip', timelineStart: 0, timelineEnd: 4, words: [
    { id: 'w', text: 'Hello', start: 0, end: 1, timing: 'word', timelineStart: 0, timelineEnd: 1 },
    { id: 'w2', text: 'world', start: 1, end: 2, timing: 'word', timelineStart: 1, timelineEnd: 2 },
    { id: 'w3', text: 'Hello', start: 2, end: 3, timing: 'word', timelineStart: 2, timelineEnd: 3 },
  ] }] };
  const documents = new Set<() => void>(), playback = new Set<() => void>();
  const subscribe = (set: Set<() => void>) => (listener: () => void) => { set.add(listener); return { dispose: () => { set.delete(listener); } }; };
  let active = true;
  let lane = true;
  const seek = vi.fn<PanelVideoAdapter['seek']>();
  const editText = vi.fn<NonNullable<TranscriptPanelAdapter['editText']>>(async () => ({ ok: true, changed: true }));
  const importTranscript = vi.fn<NonNullable<TranscriptPanelAdapter['importTranscript']>>(async () => ({ assetId: 'asset', sourceId: 'default', importedSegments: 1, replacedSegments: 0, replacedExistingSource: false, documentId: 'document', version: 4, associationSaved: true }));
  const stageTextDraft = vi.fn<NonNullable<TranscriptPanelAdapter['stageTextDraft']>>(() => ({ ok: true, changed: true }));
  const resolveSourceConflict = vi.fn<NonNullable<TranscriptPanelAdapter['resolveSourceConflict']>>(async () => ({ ok: true, changed: true }));
  const adapter: TranscriptPanelAdapter = {
    getSnapshot: () => snapshot, subscribe: subscribe(documents), getMediaChoices: () => mediaChoices,
    getPlayback: () => { throw new Error('The film view reads the playhead, not per-source playback.'); },
    getProjectState: () => ({ supported: true, sources: [{ assetId: 'asset', documentId: 'document', sourceId: 'default', version: 4, status: 'clean', hasDraft: false, canUndo: true, canRedo: false }] }),
    listProjectSources: vi.fn(async () => [{ documentId: 'other-document', sourceId: 'Alt take', version: 1 }]),
    selectProjectSource: vi.fn(async () => ({ assetId: 'asset', sourceId: 'Alt take', importedSegments: 1, replacedSegments: 0, replacedExistingSource: false, associationSaved: true })),
    getTimelineLaneVisible: () => lane, setTimelineLaneVisible: vi.fn((value) => { lane = value; }),
    selectSource: (_selection, source) => ({ ...source, occurrenceId: null }), seek: () => ({ ok: false, reason: 'unavailable' }), editText, importTranscript, stageTextDraft, resolveSourceConflict,
  };
  const video = { transcript: adapter, seek, view: { subscribe: subscribe(playback), getSnapshot: () => ({ playhead: { time: active ? 0.5 : 6 } }) } } as unknown as PanelVideoAdapter;
  const onOpenChange = vi.fn();
  const panel = (visible = true, settingsOpen = false) => createElement(TranscriptPanel, { context: {} as ExtensionContext, instanceId: 'editor:transcript', visible, video, settings: { open: settingsOpen, onOpenChange } });
  const view = render(panel());
  return { ...view, adapter, panel, documents, playback, seek, editText, stageTextDraft, importTranscript, resolveSourceConflict, onOpenChange,
    setMediaChoices: (next: TranscriptMediaChoice[]) => { mediaChoices = next; },
    publish: (next: TranscriptPanelSnapshot) => act(() => { snapshot = next; documents.forEach((listener) => listener()); }),
    stop: () => act(() => { active = false; playback.forEach((listener) => listener()); }), getSnapshot: () => snapshot };
}
const inspect = (view: ReturnType<typeof setup>) => { fireEvent.focus(view.getByRole('group', { name: 'Transcript text' })); };
/** Spoken text only, without the paragraph timecodes and shot names. */
const prose = (view: ReturnType<typeof setup>) => [...view.getByRole('group', { name: 'Transcript text' }).querySelectorAll('p')].map((node) => node.textContent).join('\n');

describe('continuous transcript panel', () => {
  it('separates flowing text in one inset scrolling reading surface below controls', () => {
    const view = setup();
    const area = view.getByTestId('transcript-reading-area');
    const text = view.getByRole('group', { name: 'Transcript text' });
    expect(area).toContainElement(text);
    expect(area).toHaveClass('mx-3', 'mb-3', 'min-h-0', 'flex-1', 'overflow-y-auto', 'overscroll-contain', 'rounded-lg', 'border', 'border-border', 'bg-background', 'px-4', 'py-4');
    expect(prose(view)).toBe('Hello, world!  Hello.');
    expect(view.queryByRole('combobox', { name: 'Transcript source' })).toBeNull();
    expect(area).not.toContainElement(view.getByRole('button', { name: 'Following playhead' }));
    expect(area).not.toContainElement(view.getByRole('button', { name: 'Transcript settings' }));
    expect(area).not.toContainElement(view.getByText('1 line'));
    area.scrollTop = 88; fireEvent.scroll(area);
    view.rerender(view.panel(true, true));
    expect(view.getByTestId('transcript-reading-area')).toBe(area);
    expect(area.scrollTop).toBe(88);
    const settings = view.getByRole('dialog', { name: 'Transcript settings' });
    expect(area).not.toContainElement(settings);
    expect(settings.parentElement).toBe(area.parentElement);
    expect(settings).toHaveClass('absolute', 'z-30');
  });

  it('renders canonical punctuation, repeated words and spacing exactly once with segment fallback for uncertain alignment', () => {
    const view = setup();
    expect(prose(view)).toBe('Hello, world!  Hello.');
    expect(view.queryByLabelText('Aligned words')).toBeNull();
    const source = view.getSnapshot().sources[0];
    expect(alignedTextParts({ ...source, words: [{ ...source.words![0], text: 'missing' }] })).toEqual([{ text: source.text }]);
    expect(alignedTextParts({ ...source, words: [source.words![0]] })).toEqual([{ text: source.text }]);
  });
  it('keeps one quiet header: a summary, Follow and settings, with no per-recording picker', () => {
    const view = setup();
    expect(view.queryByRole('combobox')).toBeNull();
    expect(view.getByText('1 line')).toHaveClass('truncate', 'text-muted-foreground');
    expect(view.getByRole('button', { name: 'Following playhead' })).toHaveClass('shrink-0');
    expect(view.getByText('0:00')).toHaveClass('tabular-nums');
  });
  it('offers one roving region, contextual detail/seek/edit at timeline time without word tab stops', () => {
    const view = setup(); const region = view.getByRole('group', { name: 'Transcript text' });
    expect([...region.querySelectorAll('[tabindex]')].every((node) => node.getAttribute('tabindex') === '-1')).toBe(true);
    expect(view.queryByText(/0.00–1.00/)).toBeNull();
    fireEvent.focus(region); fireEvent.keyDown(region, { key: 'ArrowRight' }); fireEvent.keyDown(region, { key: 'Enter' });
    expect(view.seek).toHaveBeenCalledWith(1);
    expect(view.getByText('Word · 1.00–2.00s · Interview.mp4 · Alex')).toBeTruthy();
    fireEvent.click(view.getByText('world'));
    expect(view.seek).toHaveBeenLastCalledWith(1);
    fireEvent.click(view.getByText('0:00'));
    expect(view.seek).toHaveBeenLastCalledWith(0);
    fireEvent.click(view.getByRole('button', { name: 'Seek word' }));
    expect(view.seek).toHaveBeenLastCalledWith(1);
    fireEvent.click(view.getByRole('button', { name: 'Edit word' }));
    expect(view.getByRole('textbox', { name: 'Replacement word' })).toHaveValue('world');
  });
  it('awaits a save, stages the full displayed identity/base and retains failed text', async () => {
    const view = setup(); let finish!: (value: {ok:true;changed:boolean}) => void;
    view.editText.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    inspect(view); fireEvent.click(view.getByRole('button', { name: 'Edit word' }));
    fireEvent.change(view.getByRole('textbox'), { target: { value: 'Hi' } });
    expect(view.stageTextDraft).toHaveBeenCalledWith(expect.objectContaining({ assetId: 'asset', documentId: 'document', baseVersion: 4, text: 'Hi', wordId: 'w' }));
    fireEvent.click(view.getByRole('button', { name: 'Save correction' }));
    expect(view.getByText('1 line · Saving…')).toBeTruthy();
    expect(view.getByRole('textbox')).toHaveValue('Hi');
    await act(async () => finish({ok:true,changed:true}));
    expect(view.queryByRole('textbox')).toBeNull();
    inspect(view); fireEvent.click(view.getByRole('button', { name: 'Edit word' }));
    fireEvent.change(view.getByRole('textbox'), { target: { value: 'Unsent' } });
    view.editText.mockResolvedValueOnce({ ok: false, code: 'conflict', message: 'Newer document; draft retained.' });
    fireEvent.click(view.getByRole('button', { name: 'Save correction' }));
    await waitFor(() => expect(view.getByRole('alert')).toHaveTextContent('draft retained'));
    expect(view.getByRole('textbox')).toHaveValue('Unsent');
  });
  it('cancels an untouched word edit without asking the host to discard a nonexistent draft', async () => {
    const view = setup();
    view.resolveSourceConflict.mockResolvedValueOnce({ ok: false, code: 'source_no_draft', message: 'No saved source draft is available.' });
    inspect(view);
    fireEvent.click(view.getByRole('button', { name: 'Edit word' }));
    expect(view.getByRole('textbox', { name: 'Replacement word' })).toHaveValue('Hello');
    await act(async () => { fireEvent.click(view.getByRole('button', { name: 'Cancel correction' })); });
    await waitFor(() => expect(view.queryByRole('textbox', { name: 'Replacement word' })).toBeNull());
    expect(view.resolveSourceConflict).not.toHaveBeenCalled();
    expect(view.queryByRole('alert')).toBeNull();
  });
  it('discards a draft staged by the current edit when Cancel correction is chosen', async () => {
    const view = setup();
    let hasDraft = false;
    view.adapter.getProjectState = () => ({ supported: true, sources: [{ assetId: 'asset', documentId: 'document', sourceId: 'default', version: 4, status: 'clean', hasDraft, canUndo: true, canRedo: false }] });
    view.stageTextDraft.mockImplementation(() => { hasDraft = true; return { ok: true, changed: true }; });
    inspect(view);
    fireEvent.click(view.getByRole('button', { name: 'Edit word' }));
    fireEvent.change(view.getByRole('textbox', { name: 'Replacement word' }), { target: { value: 'new word' } });
    expect(view.stageTextDraft).toHaveBeenCalledOnce();
    await act(async () => { fireEvent.click(view.getByRole('button', { name: 'Cancel correction' })); });
    await waitFor(() => expect(view.resolveSourceConflict).toHaveBeenCalledWith('document', 'discard'));
  });

  it('preserves a draft that existed before opening a new word edit', async () => {
    const existing = setup();
    existing.adapter.getProjectState = () => ({ supported: true, sources: [{ assetId: 'asset', documentId: 'document', sourceId: 'default', version: 4, status: 'conflict', hasDraft: true, draftPreview: { baseVersion: 3, text: 'prior correction' }, canUndo: false, canRedo: false }] });
    inspect(existing);
    fireEvent.click(existing.getByRole('button', { name: 'Edit word' }));
    fireEvent.change(existing.getByRole('textbox', { name: 'Replacement word' }), { target: { value: 'another word' } });
    expect(existing.stageTextDraft).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(existing.getByRole('button', { name: 'Cancel correction' })); });
    await waitFor(() => expect(existing.queryByRole('textbox', { name: 'Replacement word' })).toBeNull());
    expect(existing.resolveSourceConflict).not.toHaveBeenCalled();
    expect(existing.getByText('prior correction')).toBeTruthy();
  });
  it('does not offer draft recovery actions for a conflict that has no recoverable draft', () => {
    const view = setup();
    view.adapter.getProjectState = () => ({ supported: true, sources: [{ assetId: 'asset', documentId: 'document', sourceId: 'default', version: 5, status: 'conflict', message: 'The source changed while editing.', hasDraft: false, canUndo: false, canRedo: false }] });
    view.publish(view.getSnapshot());
    expect(view.getByText('1 line · Conflict')).toBeTruthy();
    expect(view.getByRole('alert')).toHaveTextContent('The source changed while editing.');
    expect(view.queryByRole('button', { name: 'Reapply draft' })).toBeNull();
    expect(view.queryByRole('button', { name: 'Reload / discard draft' })).toBeNull();
  });
  it('retains draft/source/scroll and live observation through settings and visibility', async () => {
    const view = setup(); inspect(view); fireEvent.click(view.getByRole('button', { name: 'Edit word' }));
    fireEvent.change(view.getByRole('textbox'), { target: { value: 'Draft' } });
    const scroller = view.getByRole('group', { name: 'Transcript text' }).parentElement!.parentElement!;
    Object.defineProperties(scroller, {
      clientHeight: { configurable: true, get: () => 100 },
      scrollHeight: { configurable: true, get: () => 300 },
      getClientRects: { configurable: true, value: () => [{} as DOMRect] },
    });
    scroller.scrollTop = 88; fireEvent.scroll(scroller);
    expect(view.getByRole('button', { name: 'Resume follow' })).toBeTruthy();
    view.rerender(view.panel(true, true)); expect(view.documents.size).toBe(1); expect(view.playback.size).toBe(1);
    expect(view.getByRole('dialog', { name: 'Transcript settings' })).toBeTruthy();
    expect(view.getByRole('group', { name: 'Transcript text' })).toBeVisible();
    expect(view.getByLabelText('Show transcript lane')).toBeChecked();
    fireEvent.click(view.getByLabelText('Show transcript lane'));
    expect(view.adapter.setTimelineLaneVisible).toHaveBeenCalledWith(false);
    view.rerender(view.panel(false, true)); expect(view.getByTestId('astrid-transcript-panel')).not.toBeVisible();
    view.rerender(view.panel()); expect(view.getByRole('textbox')).toHaveValue('Draft'); expect(scroller.scrollTop).toBe(88);
    expect(view.documents.size).toBe(1); expect(view.playback.size).toBe(1);
    view.unmount(); expect(view.documents.size).toBe(0); expect(view.playback.size).toBe(0);
  });
  it('keeps the intended reading position through a hidden clamp and restores it when space returns', () => {
    const observers: Array<{ trigger: () => void }> = [];
    class ResizeObserverStub {
      constructor(private readonly callback: ResizeObserverCallback) { observers.push(this); }
      observe() {}
      disconnect() {}
      trigger() { this.callback([], this as unknown as ResizeObserver); }
    }
    const originalResizeObserver = window.ResizeObserver;
    window.ResizeObserver = ResizeObserverStub;
    try {
      const view = setup();
      const area = view.getByTestId('transcript-reading-area');
      let clientHeight = 100;
      Object.defineProperties(area, {
        clientHeight: { configurable: true, get: () => clientHeight },
        scrollHeight: { configurable: true, get: () => 300 },
        getClientRects: { configurable: true, value: () => [{} as DOMRect] },
      });
      area.scrollTop = 60;
      fireEvent.scroll(area);

      view.rerender(view.panel(false));
      area.scrollTop = 0;
      fireEvent.scroll(area);
      view.rerender(view.panel(true));
      clientHeight = 260;
      area.scrollTop = 0;
      fireEvent.scroll(area);
      expect(area.scrollTop).toBe(0); // max is 40, so do not force an unreachable target.

      clientHeight = 220;
      act(() => observers.at(-1)?.trigger());
      expect(area.scrollTop).toBe(60);
      view.unmount();
    } finally {
      window.ResizeObserver = originalResizeObserver;
    }
  });
  it('clears subtle playback highlight in a gap without losing prose', () => {
    const view = setup(); expect(view.container.querySelector('[data-word-id="w"]')).toHaveClass('bg-primary/15');
    view.stop(); expect(view.container.querySelector('[data-word-id="w"]')).not.toHaveClass('bg-primary/15');
    expect(prose(view)).toBe('Hello, world!  Hello.');
  });
  it('grows word highlights by shadow and outline, never padding, so lighting a word never reflows prose', () => {
    const view = setup();
    const region = view.getByRole('group', { name: 'Transcript text' });
    const word = view.container.querySelector<HTMLElement>('[data-word-id="w"]')!;
    const quiet = view.container.querySelector<HTMLElement>('[data-word-id="w2"]')!;
    expect(word.style.boxShadow).toContain('0.12em');
    expect(quiet.style.boxShadow).toBe('');
    expect(word.style.padding + quiet.style.padding).toBe('');
    fireEvent.focus(region);
    expect(word.style.outlineOffset).toBe('2px');
    expect(prose(view)).toBe('Hello, world!  Hello.');
  });
  it('opens anchored settings from its own header without removing prose', () => {
    const view = setup();
    const settingsButton = view.getByRole('button', { name: 'Transcript settings' });
    expect(settingsButton).toHaveClass('hover:bg-muted', 'focus-visible:ring-2');
    expect(view.getByTestId('transcript-settings-icon')).toHaveAttribute('width', '18');
    fireEvent.click(settingsButton);
    expect(view.onOpenChange).toHaveBeenCalledWith(true);
    view.rerender(view.panel(true, true));
    expect(view.getByRole('dialog', { name: 'Transcript settings' })).toBeVisible();
    expect(view.getByRole('group', { name: 'Transcript text' })).toBeVisible();
    expect(view.getByLabelText('Import transcript JSON')).toBeTruthy();
  });
  it('caps settings to the Transcript panel height and retains internal overflow', () => {
    const view = setup();
    view.rerender(view.panel(true, true));
    const settings = view.getByRole('dialog', { name: 'Transcript settings' });
    const panel = view.getByTestId('astrid-transcript-panel');

    // The absolute containing block is this h-full panel. Keep geometry
    // explicit because pack TSX is outside the app Tailwind content scan.
    expect(settings.parentElement).toBe(panel);
    expect(settings.style.top).toBe('3.25rem');
    expect(settings.style.right).toBe('0.5rem');
    expect(settings.style.width).toBe('calc(100% - 1.5rem)');
    expect(settings.style.maxWidth).toBe('24rem');
    // The height cap leaves room for its top offset and bottom inset.
    expect(settings.style.maxHeight).toBe('min(70dvh, 36rem, calc(100% - 4rem))');
    expect(settings).toHaveClass('overflow-y-auto', 'overscroll-contain');
    expect(view.getByLabelText('Import transcript JSON')).toBeTruthy();
    expect(view.getByRole('button', { name: 'Undo source edit' })).toBeTruthy();
    expect(view.getByRole('button', { name: 'Redo source edit' })).toBeTruthy();
    expect(view.getByRole('group', { name: 'Transcript text' })).toBeVisible();
  });
  it('dismisses the settings popover on outside pointer or Escape and restores gear focus', () => {
    const view = setup();
    view.rerender(view.panel(true, true));
    fireEvent.pointerDown(view.getByRole('group', { name: 'Transcript text' }));
    expect(view.onOpenChange).toHaveBeenLastCalledWith(false);
    view.rerender(view.panel(true, false));
    view.rerender(view.panel(true, true));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(view.onOpenChange).toHaveBeenLastCalledWith(false);
    expect(view.getByRole('button', { name: 'Transcript settings' })).toHaveFocus();
  });
  it('keeps import in settings and selected file bytes available after failed write then retries asynchronously', async () => {
    const view = setup(); view.rerender(view.panel(true, true));
    const document = JSON.stringify({ segments: [{ text: 'Imported', start: 0, end: 1 }] });
    view.importTranscript.mockRejectedValueOnce(new Error('Choose media association'));
    fireEvent.change(view.getByLabelText('Import transcript JSON'), { target: { files: [{ text: async () => document }] } });
    await waitFor(() => expect(view.getByRole('alert')).toHaveTextContent('Choose media association'));
    expect(view.getByLabelText('Match to video or audio')).toHaveValue('asset');
    fireEvent.click(view.getByRole('button', { name: 'Retry import' }));
    await waitFor(() => expect(view.importTranscript).toHaveBeenLastCalledWith({ document, associationAssetId: 'asset' }));
    await waitFor(() => expect(view.queryByRole('button', { name: 'Retry import' })).toBeNull());
  });
  it('reports partial association accurately and does not discard retry bytes', async () => {
    const view = setup(); view.rerender(view.panel(true, true));
    view.importTranscript.mockResolvedValueOnce({ assetId: 'asset', sourceId: 'default', importedSegments: 1, replacedSegments: 0, replacedExistingSource: false, associationSaved: false });
    fireEvent.change(view.getByLabelText('Import transcript JSON'), { target: { files: [{ text: async () => '{}' }] } });
    await waitFor(() => expect(view.getByRole('alert')).toHaveTextContent('association still needs retry'));
    expect(view.getByRole('button', { name: 'Retry import' })).toBeTruthy();
  });
  it('looks up project transcripts for the media once, without playback triggering list requests', async () => {
    const view = setup(); await waitFor(() => expect(view.adapter.listProjectSources).toHaveBeenCalledWith('asset'));
    const count = vi.mocked(view.adapter.listProjectSources!).mock.calls.length;
    view.stop(); expect(vi.mocked(view.adapter.listProjectSources!).mock.calls.length).toBe(count);
    expect(view.adapter.selectProjectSource).not.toHaveBeenCalled();
  });
  it('preserves unsent text and exposes explicit conflict discard/reapply choices', async () => {
    const view = setup(); view.adapter.getProjectState = () => ({ supported: true, sources: [{ assetId: 'asset', documentId: 'document', sourceId: 'default', version: 5, status: 'conflict', hasDraft: true, canUndo: false, canRedo: false }] });
    view.publish(view.getSnapshot());
    expect(view.getByText('1 line · Conflict')).toBeTruthy();
    fireEvent.click(view.getByRole('button', { name: 'Reapply draft' }));
    await waitFor(() => expect(view.resolveSourceConflict).toHaveBeenCalledWith('document', 'reapply'));
  });
  it('shows unresolved canonical-reference errors without cached transcript truth and offers refresh', async () => {
    const view = setup(); const refreshSources = vi.fn(async () => {}); view.adapter.refreshSources = refreshSources;
    view.adapter.getProjectState = () => ({ supported: true, sources: [{ assetId: 'asset', documentId: 'missing', sourceId: '', version: 0, status: 'error', message: 'Missing project transcript. Retry Refresh.', hasDraft: false, canUndo: false, canRedo: false }] });
    view.publish({ sources: [], occurrences: [] });
    expect(view.getByRole('alert')).toHaveTextContent('Missing project transcript');
    await waitFor(() => expect(view.getByText('No transcript for the media on this timeline yet.')).toBeTruthy());
    fireEvent.click(view.getByRole('button', { name: 'Refresh project transcripts' }));
    await waitFor(() => expect(refreshSources).toHaveBeenCalledOnce());
  });

  it('says when the timeline media has no transcript and opens transcript settings from Import', async () => {
    const view = setup();
    vi.mocked(view.adapter.listProjectSources!).mockResolvedValue([]);
    view.publish({ sources: [], occurrences: [] });
    await waitFor(() => expect(view.getByText('No transcript for the media on this timeline yet.')).toBeTruthy());
    expect(view.getByText('No transcript')).toBeTruthy();
    fireEvent.click(view.getByRole('button', { name: 'Import transcript…' }));
    expect(view.onOpenChange).toHaveBeenCalledWith(true);
    view.rerender(view.panel(true, true));
    expect(view.getByLabelText('Import transcript JSON')).toBeTruthy();
  });

  it('keeps every readable line when one media lookup fails, and treats unidentified media as having none', async () => {
    const view = setup();
    view.setMediaChoices([
      { assetId: 'asset', label: 'Interview.mp4', kind: 'video' },
      { assetId: 'bad-media', label: 'Notes.wav', kind: 'audio' },
      { assetId: 'local-media', label: 'Scratch.wav', kind: 'audio' },
    ]);
    vi.mocked(view.adapter.listProjectSources!).mockImplementation(async (assetId) => {
      if (assetId === 'bad-media') throw new Error('Notes.wav is unavailable.');
      if (assetId === 'local-media') throw Object.assign(new Error('No object identity.'), { code: 'missing_media_identity' });
      return [];
    });
    view.rerender(view.panel());
    await waitFor(() => expect(view.getByRole('alert')).toHaveTextContent('Notes.wav is unavailable.'));
    expect(view.getByRole('alert')).not.toHaveTextContent('No object identity');
    expect(prose(view)).toBe('Hello, world!  Hello.');
    expect(view.getByRole('button', { name: 'Refresh project transcripts' })).toBeTruthy();
  });

  it('disables import with the concrete media reason when the timeline has no audio or video', async () => {
    const view = setup();
    view.publish({ sources: [], occurrences: [] });
    view.setMediaChoices([]);
    view.rerender(view.panel());
    expect(view.getByText('Add an audio or video clip to this timeline to see its words.')).toBeTruthy();
    fireEvent.click(view.getByRole('button', { name: 'Transcript settings' }));
    view.rerender(view.panel(true, true));
    expect(view.getByRole('button', { name: 'Import transcript…' })).toBeDisabled();
    expect(view.getByRole('note')).toHaveTextContent('Add an audio or video clip');
  });

  it('disables project import when the provider lacks durable transcript authority', () => {
    const view = setup();
    view.adapter.getProjectState = () => ({ supported: false, reason: 'Reconnect to the Runtime project to save transcripts.', sources: [] });
    view.publish(view.getSnapshot());
    fireEvent.click(view.getByRole('button', { name: 'Transcript settings' }));
    view.rerender(view.panel(true, true));
    expect(view.getByRole('button', { name: 'Import transcript…' })).toBeDisabled();
    expect(view.getByRole('note')).toHaveTextContent('Reconnect to the Runtime project');
  });

});

describe('whole-film reading', () => {
  const word = (id: string, text: string, start: number, end: number) => ({ id, text, start, end });
  const sources = [
    { sourceItemId: 'a1', sourceId: 'astrid.narration', assetId: 'take-a', documentId: 'doc-a', documentVersion: 1, start: 0, end: 2, text: 'First line, inside the trim.',
      words: [word('a-first', 'First', 0, 0.4), word('a-line', 'line', 0.4, 0.8), word('a-inside', 'inside', 0.9, 1.3), word('a-the', 'the', 1.3, 1.5), word('a-trim', 'trim', 1.5, 2)] },
    { sourceItemId: 'b1', sourceId: 'astrid.narration', assetId: 'take-b', documentId: 'doc-b', documentVersion: 1, start: 0, end: 1.4, text: 'Second take speaks.',
      words: [word('b-second', 'Second', 0, 0.5), word('b-take', 'take', 0.5, 0.9), word('b-speaks', 'speaks', 0.9, 1.4)] },
    // A timeline-only transcript of the same take: the project transcript wins.
    { sourceItemId: 'b-local', sourceId: 'default', assetId: 'take-b', start: 0, end: 1.4, text: 'Duplicate words.' },
    { sourceItemId: 'c1', sourceId: 'astrid.narration', assetId: 'take-c', documentId: 'doc-c', documentVersion: 1, start: 0, end: 1.2, text: 'A wall-crossing line.',
      words: [word('c-a', 'A', 0, 0.2), word('c-wall', 'wall-crossing', 0.2, 0.8), word('c-line', 'line', 0.8, 1.2)] },
  ];
  const clip = (id: string, asset: string, at: number, duration: number, sourceOffset = 0, assetKind: 'audio' | 'video' | 'image' = 'audio') => ({ id, track: 'vo', at, duration, sourceOffset, rate: 1, assetKey: asset, assetKind });
  // Shots arrive out of order; the reading is in timeline order.
  const timeline = {
    tracks: [{ id: 'vo', kind: 'audio', label: 'VO', muted: false }, { id: 'raw', kind: 'audio', label: 'Raw', role: 'source', muted: false }],
    clips: [
      // Raw material on a Source lane is never played, so it is never read.
      { ...clip('raw-c', 'take-c', 0, 1.2), track: 'raw', managed: false },
      { ...clip('music', 'music', 0, 30), managed: false },
      { ...clip('clip-c', 'take-c', 9.8, 1.2), managed: false },
      { ...clip('shell', 'shell-asset', 0, 10), clipType: 'shot', managed: false },
    ],
    shots: [
      { id: 'occ-2', shotId: 'two', name: '02 Two', at: 10, duration: 10, clips: [clip('occ-2:b', 'take-b', 12, 1.4)] },
      { id: 'occ-1', shotId: 'one', name: '01 One', at: 0, duration: 10, clips: [clip('occ-1:a', 'take-a', 3, 0.9, 0.4), clip('occ-1:plate', 'plate', 0, 10, 0, 'image')] },
    ],
  } as unknown as TimelineSnapshot;

  it('reads every clip, inside shots too, in timeline order: one paragraph per shot with only the words inside each trim', () => {
    const played = playedFromReader(timeline);
    expect(played.media.sort()).toEqual(['music', 'take-a', 'take-b', 'take-c']);
    const paragraphs = filmParagraphs(projectPlayedOccurrences(played.clips, sources), sources, played.clips, played.placements);
    expect(paragraphs.map((paragraph) => paragraph.name)).toEqual(['01 One', '02 Two']);
    const text = paragraphs.map((paragraph) => paragraph.lines.map((line) => line.parts.map((part) => part.text).join('')));
    // The trim keeps "line, inside"; the clip outside every shot joins the shot it overlaps most.
    expect(text).toEqual([['line, inside'], ['A wall-crossing line.', 'Second take speaks.']]);
    const [first] = paragraphs[0].lines;
    expect(first.start).toBe(3);
    expect(first.timing.get('a-inside')).toMatchObject({ timing: 'word', timelineStart: 3.5, timelineEnd: 3.9 });
    expect(first.timing.get('a-the')?.timing).toBe('outside');
    expect(paragraphs[1].start).toBeCloseTo(9.8);
  });

  it('without shots, gives each clip its own paragraphs, broken at long pauses', () => {
    const rows = [
      { sourceItemId: 'x1', sourceId: 'default', assetId: 'talk', start: 0, end: 2, text: 'Opening thought.' },
      { sourceItemId: 'x2', sourceId: 'default', assetId: 'talk', start: 2.5, end: 4, text: 'Same breath.' },
      { sourceItemId: 'x3', sourceId: 'default', assetId: 'talk', start: 7, end: 9, text: 'After a pause.' },
    ];
    const clips = [{ id: 'talk-clip', assetId: 'talk', start: 0, end: 10, sourceStart: 0, rate: 1 }, { id: 'again', assetId: 'talk', start: 20, end: 22, sourceStart: 0, rate: 1 }];
    const paragraphs = filmParagraphs(projectPlayedOccurrences(clips, rows), rows, clips, []);
    expect(paragraphs.map((paragraph) => paragraph.lines.map((line) => line.source.text).join(' '))).toEqual(['Opening thought. Same breath.', 'After a pause.', 'Opening thought.']);
    expect(paragraphs.every((paragraph) => paragraph.name === undefined)).toBe(true);
  });

  it('renders the film from the reader, lights the spoken word and seeks at film time', async () => {
    const seek = vi.fn();
    const listProjectSources = vi.fn(async (_assetId: string) => []);
    const adapter = {
      getSnapshot: () => ({ sources, occurrences: [] }), subscribe: () => ({ dispose() {} }), getMediaChoices: () => [],
      getProjectState: () => ({ supported: true, sources: [] }), listProjectSources,
    } as unknown as TranscriptPanelAdapter;
    const video = { transcript: adapter, seek, view: { subscribe: () => ({ dispose() {} }), getSnapshot: () => ({ playhead: { time: 12.6 } }) } } as unknown as PanelVideoAdapter;
    const context = { creative: { reader: { snapshot: () => timeline } } } as unknown as ExtensionContext;
    const view = render(createElement(TranscriptPanel, { context, instanceId: 'film', visible: true, video }));
    expect(view.getByText('2 shots · 3 lines')).toBeTruthy();
    expect([...view.container.querySelectorAll('[data-paragraph]')].map((node) => node.textContent)).toEqual([
      '0:0301 Oneline, inside', '0:0902 TwoA wall-crossing line. Second take speaks.',
    ]);
    expect(view.container.querySelector('[data-word-id="b-take"]')).toHaveClass('bg-primary/15');
    expect(view.container.querySelector('[data-occurrence="b1@occ-2:b"]')).toHaveAttribute('aria-current', 'true');
    fireEvent.click(view.getByText('inside'));
    expect(seek).toHaveBeenLastCalledWith(3.5);
    expect(view.getByText('Word · 3.50–3.90s · take-a')).toBeTruthy();
    await waitFor(() => expect(listProjectSources).toHaveBeenCalledTimes(4));
    expect(listProjectSources.mock.calls.map(([assetId]) => assetId).sort()).toEqual(['music', 'take-a', 'take-b', 'take-c']);
  });
});
