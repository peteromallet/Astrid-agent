import { createElement, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ExtensionContext, PanelRenderProps, TimelineClipSummary, TimelineSnapshot, TranscriptOccurrence, TranscriptPanelTextEdit, TranscriptPanelTextEditResult, TranscriptProjectSourceChoice, TranscriptSourceRow, TranscriptSourceWordView, TranscriptWordProjection } from '@astrid/editor-sdk';

export interface TranscriptPanelProps extends PanelRenderProps { readonly context: ExtensionContext }
const EMPTY = { sources: [], occurrences: [] };
const button = 'min-h-10 sm:min-h-8 rounded-md px-2 text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50';
const select = 'min-h-10 sm:min-h-8 min-w-0 max-w-full rounded-md border border-border/70 bg-background px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';
/** Without shots, a pause this long (seconds) inside one clip starts a new paragraph. */
const PARAGRAPH_PAUSE = 2;
/** The editor snaps seeks to frames; read the playhead half a 24 fps frame late so a word click lights that word. */
const FRAME_SNAP = 1 / 48;
// Pack TSX is outside the app's Tailwind content scan: arbitrary values the app never uses are inline.
const READING_AREA_STYLE = { marginInline: '0.75rem', scrollbarGutter: 'stable' } as const;
const PROSE_STYLE = { fontSize: '14px', lineHeight: 1.75 } as const;
// Highlights grow by shadow, never padding, so lighting a word never reflows the line.
const WORD_STYLE = { borderRadius: '0.28em' } as const;
const SPOKEN_WORD_STYLE = { ...WORD_STYLE, boxShadow: '0 0 0 0.12em hsl(var(--primary) / 0.15)' } as const;
const TARGET_WORD_STYLE = { outline: '1px solid hsl(var(--primary) / 0.4)', outlineOffset: '2px' } as const;
const INSPECTOR_STYLE = { position: 'sticky', bottom: '-1rem', marginBottom: '-1rem' } as const;
type Fallback = NonNullable<Extract<TranscriptPanelTextEditResult, { ok: false }>['fallback']>;
type Part = { text: string; word?: TranscriptSourceWordView };
/** A clip the timeline plays, in timeline seconds; `placementId` names the shot placement it plays inside. */
export type PlayedClip = { readonly id: string; readonly assetId: string; readonly start: number; readonly end: number; readonly sourceStart: number; readonly rate: number; readonly placementId?: string };
export type ShotPlacement = { readonly id: string; readonly name: string; readonly start: number; readonly end: number };
export type FilmLine = {
  readonly occurrence: TranscriptOccurrence;
  readonly source: TranscriptSourceRow;
  /** Only the words inside the clip's trim, with their glued punctuation. */
  readonly parts: readonly Part[];
  readonly timing: ReadonlyMap<string, TranscriptWordProjection>;
  readonly start: number;
  readonly end: number;
};
export type FilmParagraph = { readonly key: string; readonly name?: string; readonly start: number; readonly lines: readonly FilmLine[] };
type Target = { key: string; line: FilmLine; word?: TranscriptSourceWordView };
type ProjectSourceLookup = {
  readonly status: 'loading' | 'loaded' | 'error' | 'unavailable';
  readonly choices: readonly TranscriptProjectSourceChoice[];
  readonly message?: string;
};

/** Match against the canonical string. Never reconstruct text from alignment. */
export function alignedTextParts(source: TranscriptSourceRow): Array<{ text: string; word?: TranscriptSourceWordView }> {
  if (!source.words?.length) return [{ text: source.text }];
  let cursor = 0;
  const parts: Array<{ text: string; word?: TranscriptSourceWordView }> = [];
  for (const word of source.words) {
    if (!word.text) return [{ text: source.text }];
    const at = source.text.indexOf(word.text, cursor);
    if (at < 0 || /[\p{L}\p{N}]/u.test(source.text.slice(cursor, at))) return [{ text: source.text }];
    if (at > cursor) parts.push({ text: source.text.slice(cursor, at) });
    parts.push({ text: source.text.slice(at, at + word.text.length), word });
    cursor = at + word.text.length;
  }
  if (/[\p{L}\p{N}]/u.test(source.text.slice(cursor))) return [{ text: source.text }];
  if (cursor < source.text.length) parts.push({ text: source.text.slice(cursor) });
  return parts;
}

/** What the timeline plays, from the generic reader: its own output clips and the clips inside placed shots. */
export function playedFromReader(snapshot: TimelineSnapshot): { clips: PlayedClip[]; placements: ShotPlacement[]; media: string[] } {
  const clips: PlayedClip[] = [];
  const media = new Set<string>();
  // Source lanes hold raw material the film never plays.
  const sourceTracks = new Set((snapshot.tracks ?? []).filter((track) => track.role === 'source').map((track) => track.id));
  const add = (clip: Pick<TimelineClipSummary, 'id' | 'track' | 'at' | 'duration' | 'clipType' | 'sourceOffset' | 'rate' | 'assetKey' | 'assetKind'>, placementId?: string) => {
    if (!clip.assetKey || clip.clipType === 'shot' || (!placementId && sourceTracks.has(clip.track))) return;
    if (clip.assetKind === 'audio' || clip.assetKind === 'video') media.add(clip.assetKey);
    const rate = clip.rate !== undefined && clip.rate > 0 ? clip.rate : 1;
    clips.push({ id: clip.id, assetId: clip.assetKey, start: clip.at, end: clip.at + clip.duration, sourceStart: clip.sourceOffset ?? 0, rate, ...(placementId ? { placementId } : {}) });
  };
  snapshot.clips.forEach((clip) => add(clip));
  const placements = (snapshot.shots ?? []).map((shot) => {
    shot.clips.forEach((clip) => add(clip, shot.id));
    return { id: shot.id, name: shot.name, start: shot.at, end: shot.at + shot.duration };
  });
  return { clips, placements, media: [...media] };
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const byTime = (a: TranscriptOccurrence, b: TranscriptOccurrence) => a.timelineStart - b.timelineStart || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

/** Every transcript segment placed in every played clip of its media, inside the clip's trim. */
export function projectPlayedOccurrences(clips: readonly PlayedClip[], sources: readonly TranscriptSourceRow[]): TranscriptOccurrence[] {
  const byAsset = new Map<string, TranscriptSourceRow[]>();
  for (const source of sources) byAsset.set(source.assetId, [...(byAsset.get(source.assetId) ?? []), source]);
  const occurrences: TranscriptOccurrence[] = [];
  for (const clip of clips) {
    if (![clip.start, clip.end, clip.sourceStart, clip.rate].every(Number.isFinite) || clip.rate <= 0) continue;
    for (const row of byAsset.get(clip.assetId) ?? []) {
      const visibleStart = Math.max(row.start, clip.sourceStart);
      const visibleEnd = Math.min(row.end, clip.sourceStart + (clip.end - clip.start) * clip.rate);
      if (!(visibleEnd > visibleStart)) continue;
      const toTimeline = (time: number) => clip.start + (time - clip.sourceStart) / clip.rate;
      occurrences.push({
        id: `${row.sourceItemId}@${clip.id}`, sourceItemId: row.sourceItemId, sourceId: row.sourceId, assetId: row.assetId, clipId: clip.id,
        timelineStart: toTimeline(visibleStart), timelineEnd: toTimeline(visibleEnd),
        words: (row.words ?? []).map((word) => {
          // A word outside its own segment is not trusted alignment; the segment still seeks.
          const trusted = finite(word.start) && finite(word.end) && word.start >= 0 && word.end > word.start && word.start < row.end && word.end > row.start;
          const start = Math.max(word.start, visibleStart), end = Math.min(word.end, visibleEnd);
          const timing = !trusted ? 'unusable' as const : end <= start ? 'outside' as const : 'word' as const;
          return { ...word, timing, timelineStart: timing === 'word' ? toTimeline(start) : null, timelineEnd: timing === 'word' ? toTimeline(end) : null };
        }),
      });
    }
  }
  return occurrences.sort(byTime);
}

/** Keep the words the clip plays, plus punctuation glued to the first and last of them. */
function partsInClip(parts: readonly Part[], timing: ReadonlyMap<string, TranscriptWordProjection>): Part[] {
  if (!parts.some((part) => part.word)) return [...parts];
  const inside = (part: Part | undefined) => Boolean(part?.word) && timing.get(part!.word!.id)?.timing !== 'outside';
  const first = parts.findIndex(inside);
  if (first < 0) return [];
  let last = parts.length - 1;
  while (!inside(parts[last])) last -= 1;
  const lead = /\S*$/.exec(parts[first - 1]?.text ?? '')![0];
  const tail = /^\S*/.exec(parts[last + 1]?.text ?? '')![0];
  return [...(lead ? [{ text: lead }] : []), ...parts.slice(first, last + 1), ...(tail ? [{ text: tail }] : [])];
}

function mostOverlap(placements: readonly ShotPlacement[], start: number, end: number): ShotPlacement | undefined {
  let best: ShotPlacement | undefined;
  let most = 0;
  for (const placement of placements) {
    const overlap = Math.min(Math.max(end, start + 1e-3), placement.end) - Math.max(start, placement.start);
    if (overlap > most) { most = overlap; best = placement; }
  }
  return best;
}

/**
 * Every spoken line in timeline order. One paragraph per shot placement; a clip
 * outside every shot joins the shot it overlaps most, else forms its own
 * paragraphs, broken at long pauses.
 */
export function filmParagraphs(
  occurrences: readonly TranscriptOccurrence[],
  sources: readonly TranscriptSourceRow[],
  clips: readonly PlayedClip[] = [],
  placements: readonly ShotPlacement[] = [],
): FilmParagraph[] {
  const rows = new Map(sources.map((source) => [JSON.stringify([source.assetId, source.sourceItemId]), source]));
  // One transcript per media: its project transcript when it has one.
  const chosen = new Map<string, TranscriptSourceRow>();
  for (const source of sources) {
    const current = chosen.get(source.assetId);
    if (!current || (!current.documentId && source.documentId)) chosen.set(source.assetId, source);
  }
  const placementOfClip = new Map(clips.flatMap((clip) => clip.placementId ? [[clip.id, clip.placementId] as const] : []));
  const placementById = new Map(placements.map((placement) => [placement.id, placement]));
  const paragraphs = new Map<string, { key: string; name?: string; start: number; lines: FilmLine[] }>();
  const clipRun = new Map<string, { end: number; index: number }>();
  for (const occurrence of [...occurrences].sort(byTime)) {
    const source = rows.get(JSON.stringify([occurrence.assetId, occurrence.sourceItemId]));
    if (!source || chosen.get(source.assetId)?.sourceId !== source.sourceId) continue;
    const timing = new Map(occurrence.words.map((word) => [word.id, word]));
    const parts = partsInClip(alignedTextParts(source), timing);
    if (!parts.some((part) => part.text.trim())) continue;
    const timed = parts.flatMap((part) => {
      const word = part.word && timing.get(part.word.id);
      return word?.timing === 'word' ? [word] : [];
    });
    const start = timed[0]?.timelineStart ?? occurrence.timelineStart;
    const end = timed[timed.length - 1]?.timelineEnd ?? occurrence.timelineEnd;
    const placement = placementById.get(placementOfClip.get(occurrence.clipId) ?? '') ?? mostOverlap(placements, start, end);
    let key = `shot:${placement?.id}`;
    if (!placement) {
      const run = clipRun.get(occurrence.clipId);
      const index = !run ? 0 : start - run.end < PARAGRAPH_PAUSE ? run.index : run.index + 1;
      clipRun.set(occurrence.clipId, { end: Math.max(end, run?.end ?? end), index });
      key = `clip:${occurrence.clipId}:${index}`;
    }
    const paragraph = paragraphs.get(key) ?? { key, ...(placement ? { name: placement.name } : {}), start, lines: [] };
    paragraph.lines.push({ occurrence, source, parts, timing, start, end });
    paragraphs.set(key, paragraph);
  }
  return [...paragraphs.values()];
}

const clock = (seconds: number) => {
  const whole = Math.max(0, Math.floor(seconds));
  const h = Math.floor(whole / 3600), m = Math.floor((whole % 3600) / 60), s = String(whole % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
};

/** The whole timeline's words. Canonical document state and mutation authority stay in the host adapter. */
export function TranscriptPanel(props: TranscriptPanelProps) {
  const adapter = props.video?.transcript;
  const settingsOpen = props.settings?.open ?? false;
  const observing = props.visible;
  const [revision, refresh] = useState(0);
  const [edit, setEdit] = useState<TranscriptPanelTextEdit | null>(null);
  const [fallback, setFallback] = useState<Fallback | null>(null);
  const [following, setFollowing] = useState(true);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [pendingDocument, setPendingDocument] = useState<string | null>(null);
  const [association, setAssociation] = useState<string | null>(null);
  const [projectLookups, setProjectLookups] = useState<Record<string, ProjectSourceLookup>>({});
  const [targetKey, setTargetKey] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const prose = useRef<HTMLDivElement>(null);
  const editor = useRef<HTMLTextAreaElement>(null);
  const importInput = useRef<HTMLInputElement>(null);
  const settingsTrigger = useRef<HTMLButtonElement>(null);
  const settingsPopover = useRef<HTMLDivElement>(null);
  const skipFocusDetails = useRef(false);
  const editDraft = useRef<{ documentId: string | null; hadDraftAtStart: boolean; stagedHere: boolean } | null>(null);
  const programmedScroll = useRef<number | null>(null);
  const savedScrollTop = useRef(0);
  const scrollRestorePending = useRef(false);
  const userScrollIntent = useRef(false);
  const request = useRef(0);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; request.current += 1; }, []);
  useEffect(() => {
    if (!observing || !adapter) return;
    const documentSubscription = adapter.subscribe(() => refresh((value) => value + 1));
    const playbackSubscription = props.video?.view.subscribe(() => refresh((value) => value + 1));
    refresh((value) => value + 1);
    return () => { documentSubscription.dispose(); playbackSubscription?.dispose(); };
  }, [props.visible, observing, adapter, props.video]);
  useEffect(() => {
    if (!settingsOpen || !props.visible) return;
    const dismissOutside = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node) || settingsPopover.current?.contains(target) || settingsTrigger.current?.contains(target)) return;
      props.settings?.onOpenChange(false);
    };
    const dismissEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      props.settings?.onOpenChange(false);
      settingsTrigger.current?.focus();
    };
    document.addEventListener('pointerdown', dismissOutside);
    document.addEventListener('keydown', dismissEscape);
    return () => {
      document.removeEventListener('pointerdown', dismissOutside);
      document.removeEventListener('keydown', dismissEscape);
    };
  }, [settingsOpen, props.visible, props.settings]);

  const snapshot = adapter?.getSnapshot() ?? EMPTY;
  const mediaChoices = adapter?.getMediaChoices() ?? [];
  // The generic reader sees inside placed shots; without it, the adapter's own clip projection is used.
  let timeline: TimelineSnapshot | undefined;
  try { timeline = props.context?.creative?.reader?.snapshot(); } catch { timeline = undefined; }
  const played = useMemo(() => timeline ? playedFromReader(timeline) : null, [timeline]);
  const occurrences = useMemo(() => played ? projectPlayedOccurrences(played.clips, snapshot.sources) : snapshot.occurrences, [played, snapshot]);
  const paragraphs = useMemo(() => filmParagraphs(occurrences, snapshot.sources, played?.clips, played?.placements), [occurrences, snapshot.sources, played]);
  const lines = paragraphs.flatMap((paragraph) => paragraph.lines);
  const playhead = observing ? props.video?.view.getSnapshot?.().playhead.time : undefined;
  const time = playhead === undefined ? undefined : playhead + FRAME_SNAP;
  const sounding = (line: FilmLine) => time !== undefined && time >= line.occurrence.timelineStart && time < line.occurrence.timelineEnd;
  const speaking = (line: FilmLine, wordId: string) => {
    const word = line.timing.get(wordId);
    return time !== undefined && word?.timing === 'word' && time >= word.timelineStart! && time < word.timelineEnd!;
  };
  const playingLine = lines.find(sounding);
  const playingWord = playingLine?.parts.find((part) => part.word && speaking(playingLine, part.word.id))?.word;
  const playingKey = playingLine ? JSON.stringify([playingLine.occurrence.id, playingWord?.id ?? null]) : null;
  const { targets, targetIndex } = useMemo(() => {
    const all: Target[] = paragraphs.flatMap((paragraph) => paragraph.lines).flatMap((line) => line.parts.some((part) => part.word)
      ? line.parts.flatMap((part) => part.word ? [{ key: JSON.stringify([line.occurrence.id, part.word.id]), line, word: part.word }] : [])
      : [{ key: JSON.stringify([line.occurrence.id]), line }]);
    return { targets: all, targetIndex: new Map(all.map((item, index) => [item.key, index])) };
  }, [paragraphs]);
  const target = targets[targetIndex.get(targetKey ?? '') ?? 0];
  const activeEdit = Boolean(edit) && lines.some((line) => line.source.sourceItemId === edit?.sourceItemId && line.source.assetId === edit?.assetId);
  const projectState = adapter?.getProjectState?.();
  const documentId = target?.line.source.documentId;
  const state = projectState?.sources.find((source) => source.documentId === documentId && source.assetId === target?.line.source.assetId);
  const referenceErrors = projectState?.sources.filter((source) => source.status === 'error') ?? [];
  const mediaIds = JSON.stringify([...new Set([...(played?.media ?? []), ...mediaChoices.map((media) => media.assetId)])]);
  const lookups = (JSON.parse(mediaIds) as string[]).map((assetId) => projectLookups[assetId]);
  const loading = Boolean(adapter?.listProjectSources) && projectState?.supported !== false && lookups.some((lookup) => !lookup || lookup.status === 'loading');
  const lookupError = lookups.find((lookup) => lookup?.status === 'error');
  const mediaLabel = (assetId: string) => mediaChoices.find((media) => media.assetId === assetId)?.label ?? assetId;
  // An import matches the only recording by default; with several, the file names its media or the user picks.
  const associationValue = association ?? (mediaChoices.length === 1 ? mediaChoices[0].assetId : '');

  const projectLookupRequest = useRef(new Map<string, number>());
  const refreshProjectSourceChoices = useCallback(async (assetIds: readonly string[]) => {
    if (!adapter?.listProjectSources || adapter.getProjectState?.().supported === false) return;
    await Promise.all(assetIds.map(async (assetId) => {
      const requestId = (projectLookupRequest.current.get(assetId) ?? 0) + 1;
      projectLookupRequest.current.set(assetId, requestId);
      setProjectLookups((previous) => ({
        ...previous,
        [assetId]: { status: 'loading', choices: previous[assetId]?.choices ?? [] },
      }));
      try {
        const choices = await adapter.listProjectSources!(assetId);
        if (!mounted.current || projectLookupRequest.current.get(assetId) !== requestId) return;
        setProjectLookups((previous) => ({ ...previous, [assetId]: { status: 'loaded', choices } }));
      } catch (cause) {
        if (!mounted.current || projectLookupRequest.current.get(assetId) !== requestId) return;
        // Media with no project object identity cannot carry a project transcript: nothing to find.
        const unidentified = (cause as { code?: unknown } | null)?.code === 'missing_media_identity';
        setProjectLookups((previous) => ({
          ...previous,
          [assetId]: unidentified ? { status: 'loaded', choices: [] } : {
            status: 'error',
            // Keep the last known choices for this media, and all choices for
            // other media, when one Runtime lookup fails.
            choices: previous[assetId]?.choices ?? [],
            message: cause instanceof Error ? cause.message : 'Could not load project transcripts for this media.',
          },
        }));
      }
    }));
  }, [adapter]);
  useEffect(() => {
    if (!props.visible || !adapter?.listProjectSources) return;
    void refreshProjectSourceChoices(JSON.parse(mediaIds) as string[]);
  }, [props.visible, adapter, mediaIds, refreshProjectSourceChoices]);
  useLayoutEffect(() => {
    const container = scroll.current;
    if (!container) return;
    const restoreWhenAvailable = () => {
      const target = savedScrollTop.current;
      if (target <= 0) { scrollRestorePending.current = false; return; }
      if (!observing || container.getClientRects().length === 0 || container.clientHeight <= 0) {
        scrollRestorePending.current = true;
        return;
      }
      const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
      if (target > maxScrollTop + 1) {
        scrollRestorePending.current = true;
        return;
      }
      if (Math.abs(container.scrollTop - target) > 1) {
        programmedScroll.current = target;
        container.scrollTop = target;
      }
      scrollRestorePending.current = false;
    };
    restoreWhenAvailable();
    if (!observing || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(restoreWhenAvailable);
    observer.observe(container);
    if (container.firstElementChild) observer.observe(container.firstElementChild);
    return () => observer.disconnect();
  }, [observing]);
  useEffect(() => {
    const container = scroll.current;
    if (!observing || !following || !playingKey || !container) return;
    const [occurrenceId, wordId] = JSON.parse(playingKey) as [string, string | null];
    const line = [...container.querySelectorAll<HTMLElement>('[data-occurrence]')].find((node) => node.dataset.occurrence === occurrenceId);
    if (!line) return;
    const word = wordId ? [...line.querySelectorAll<HTMLElement>('[data-word-id]')].find((node) => node.dataset.wordId === wordId) : undefined;
    const rect = (word ?? line).getBoundingClientRect();
    const bounds = container.getBoundingClientRect();
    // The pinned inspector covers the bottom of the reading area.
    const bottom = container.querySelector<HTMLElement>('[data-transcript-inspector]')?.getBoundingClientRect().top ?? bounds.bottom;
    if (rect.top < bounds.top || rect.bottom > Math.min(bounds.bottom, bottom)) {
      // Read ahead: the spoken line settles a third of the way down, not at the edge.
      container.scrollTop += rect.top - bounds.top - Math.round(bounds.height / 3);
      programmedScroll.current = container.scrollTop; savedScrollTop.current = container.scrollTop;
    }
  }, [observing, following, playingKey, revision]);
  useEffect(() => { if (activeEdit && observing) editor.current?.focus(); }, [activeEdit, observing]);

  if (typeof props.instanceId !== 'string' || typeof props.visible !== 'boolean') return createElement('div', { role: 'alert' }, 'Transcript requires a host with the panel instance API. Update the editor.');

  const run = async (operation: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setError(''); setMessage('');
    try { await operation(); }
    catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : 'Could not save transcript. Your draft is retained.'); }
    finally { if (mounted.current) { setBusy(false); refresh((value) => value + 1); } }
  };
  const restoreReadingFocus = () => { skipFocusDetails.current = true; prose.current?.focus(); };
  const seekTime = (seconds: number) => {
    if (!props.video) { setError('Seeking is unavailable in this host.'); return false; }
    props.video.seek(seconds); setError('');
    return true;
  };
  const seek = (item: Target) => {
    const word = item.word && item.line.timing.get(item.word.id);
    setTargetKey(item.key); setDetailsOpen(true);
    if (!seekTime(word?.timing === 'word' ? word.timelineStart! : item.line.occurrence.timelineStart)) return;
    setMessage(item.word && word?.timing !== 'word' ? 'Word timing unavailable; moved to the segment start.' : '');
  };
  const startEdit = (item: Target) => {
    const source = item.line.source;
    const identity = { sourceItemId: source.sourceItemId, assetId: source.assetId, documentId: source.documentId, baseVersion: source.documentVersion };
    editDraft.current = { documentId: identity.documentId ?? null, hadDraftAtStart: state?.hasDraft ?? false, stagedHere: false };
    setEdit(item.word ? { ...identity, type: 'word', wordId: item.word.id, text: item.word.text } : { ...identity, type: 'segment', text: source.text });
    setFallback(null); setError(''); setMessage(''); setDetailsOpen(true);
  };
  const importDocument = async (document: string, associationAssetId?: string) => {
    if (!adapter?.importTranscript) return;
    if (projectState?.supported !== true) throw new Error(projectState?.reason ?? 'Project transcript saving is unavailable in this provider.');
    if (!mediaChoices.length) throw new Error('Add an audio or video clip to this timeline before importing a transcript.');
    // Keep the exact selected bytes until the durable write and association succeed.
    setPendingDocument(document);
    setAssociation(associationAssetId ?? '');
    const receipt = await adapter.importTranscript({ document, ...(associationAssetId ? { associationAssetId } : {}) });
    await refreshProjectSourceChoices([receipt.assetId]);
    if (receipt.associationSaved === false) throw new Error(receipt.message ?? 'Project transcript saved. Timeline association still needs retry.');
    setPendingDocument(null); setAssociation(null);
    setMessage(receipt.message ?? 'Project transcript saved.');
  };
  const history = (action: 'undo' | 'redo' | 'discard' | 'reapply') => run(async () => {
    if (!documentId) return;
    const result = action === 'undo' ? await adapter?.undoSource?.(documentId) : action === 'redo' ? await adapter?.redoSource?.(documentId) : await adapter?.resolveSourceConflict?.(documentId, action);
    if (result && !result.ok) throw new Error(result.message);
    if (action === 'discard' || action === 'reapply') { editDraft.current = null; setEdit(null); setFallback(null); }
    setMessage(action === 'discard' ? 'Reloaded saved transcript; local draft discarded.' : 'Project transcript saved.');
  });
  const metadata = (item: Target) => {
    const word = item.word && item.line.timing.get(item.word.id);
    const [start, end] = word?.timing === 'word' ? [word.timelineStart!, word.timelineEnd!] : [item.line.occurrence.timelineStart, item.line.occurrence.timelineEnd];
    return `${item.word ? 'Word' : 'Segment'} · ${start.toFixed(2)}–${end.toFixed(2)}s · ${mediaLabel(item.line.source.assetId)}${item.line.source.speaker ? ` · ${item.line.source.speaker}` : ''}`;
  };
  const status = busy || state?.status === 'saving' ? 'Saving…' : state?.status === 'conflict' ? 'Conflict' : state?.hasDraft || state?.status === 'error' ? 'Unsaved' : null;
  const shotCount = paragraphs.filter((paragraph) => paragraph.name).length;
  const summary = lines.length ? [shotCount ? `${shotCount} ${shotCount === 1 ? 'shot' : 'shots'}` : '', `${lines.length} ${lines.length === 1 ? 'line' : 'lines'}`, status ?? ''].filter(Boolean).join(' · ')
    : loading ? 'Loading transcripts…' : 'No transcript';
  const importDisabled = busy || !adapter?.importTranscript || !adapter.listProjectSources || projectState?.supported !== true || mediaChoices.length === 0;
  const importDisabledReason = mediaChoices.length === 0
    ? 'Add an audio or video clip to this timeline before importing a transcript.'
    : projectState?.supported !== true
      ? projectState?.reason ?? 'Project transcript saving is unavailable in this provider.'
      : !adapter?.listProjectSources
        ? 'Project transcript selection is unavailable in this provider.'
      : null;
  const refreshAll = () => void run(async () => { await adapter?.refreshSources?.(); await refreshProjectSourceChoices(JSON.parse(mediaIds) as string[]); });

  const inspector = (detailsOpen && target) || (activeEdit && edit);
  return <section aria-label="Transcript" data-testid="astrid-transcript-panel" data-panel-instance-id={props.instanceId} data-panel-visible={String(props.visible)} hidden={!props.visible} style={{ display: props.visible ? undefined : 'none' }}
    className="relative box-border flex h-full min-h-0 min-w-0 max-w-full flex-col text-sm">
    {!adapter ? <p role="status" className="p-3">Transcript is unavailable in this host. Open a loaded video editor timeline.</p> : <>
      <div className="shrink-0 px-3 pt-3 pb-2">
        <div className="flex min-w-0 items-center gap-1">
          <p role="status" className="min-w-0 flex-1 truncate pl-1 text-xs text-muted-foreground">{summary}</p>
          <button type="button" className={`${button} shrink-0 text-muted-foreground`} aria-label={following ? 'Following playhead' : 'Resume follow'} aria-pressed={following} onClick={() => setFollowing(!following)}>{following ? '↳ Follow' : 'Resume follow'}</button>
          <button ref={settingsTrigger} type="button" aria-label="Transcript settings" title="Transcript settings" aria-expanded={settingsOpen} aria-controls={`${props.instanceId}-settings`} className={`${button} flex w-10 shrink-0 items-center justify-center px-0 sm:w-8 text-muted-foreground`} onClick={() => props.settings?.onOpenChange(!settingsOpen)}>
            <svg data-testid="transcript-settings-icon" aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 1.72l-.26 1.9a7.36 7.36 0 0 0-1.59.92l-1.8-.74a2 2 0 0 0-2.49.91l-.22.38a2 2 0 0 0 .5 2.6l1.52 1.18a7.9 7.9 0 0 0 0 1.84l-1.52 1.18a2 2 0 0 0-.5 2.6l.22.38a2 2 0 0 0 2.49.91l1.8-.74c.49.38 1.02.69 1.59.92l.26 1.9a2 2 0 0 0 2 1.72h.44a2 2 0 0 0 2-1.72l.26-1.9a7.36 7.36 0 0 0 1.59-.92l1.8.74a2 2 0 0 0 2.49-.91l.22-.38a2 2 0 0 0-.5-2.6l-1.52-1.18a7.9 7.9 0 0 0 0-1.84l1.52-1.18a2 2 0 0 0 .5-2.6l-.22-.38a2 2 0 0 0-2.49-.91l-1.8.74a7.36 7.36 0 0 0-1.59-.92l-.26-1.9a2 2 0 0 0-2-1.72z" /><circle cx="12" cy="12" r="3" /></svg>
          </button>
        </div>
        {(error || state?.message || referenceErrors.length > 0 || lookupError) && <p role="alert" className="mt-2 text-xs text-destructive">{error || state?.message || referenceErrors.map((source) => source.message).filter(Boolean).join(' ') || lookupError?.message || 'Project transcript could not be loaded.'}</p>}
        {(referenceErrors.length > 0 || lookupError) && <button type="button" className={button} disabled={busy || !adapter.listProjectSources} onClick={refreshAll}>Refresh project transcripts</button>}
        {message && <p role="status" className="mt-2 text-xs text-muted-foreground">{message}</p>}
        {state?.hasDraft && !activeEdit && <div className="mt-2 flex flex-wrap gap-1 text-xs"><span className="w-full">{state.status === 'conflict' ? 'A newer saved transcript is available. Your draft is retained.' : 'An unsaved source draft is available.'}</span>{state.draftPreview && <details className="w-full"><summary className="cursor-pointer py-2">Review local draft</summary><p className="whitespace-pre-wrap break-words text-muted-foreground">{state.draftPreview.text}</p></details>}<button className={button} disabled={busy} onClick={() => void history('discard')}>Reload / discard draft</button><button className={button} disabled={busy} onClick={() => void history('reapply')}>Reapply draft</button></div>}
      </div>
      {settingsOpen && <div ref={settingsPopover} id={`${props.instanceId}-settings`} role="dialog" aria-label="Transcript settings" style={{ top: '3.25rem', right: '0.5rem', width: 'calc(100% - 1.5rem)', maxWidth: '24rem', maxHeight: 'min(70dvh, 36rem, calc(100% - 4rem))' }} className="absolute z-30 overflow-y-auto overscroll-contain rounded-lg border border-border bg-popover p-4 text-popover-foreground shadow-xl">
        <div className="space-y-5">
          <div><h3 className="font-medium">Transcript settings</h3><p className="mt-1 text-xs text-muted-foreground">Edits apply everywhere this project transcript is used.</p></div>
          <label className="flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" checked={adapter.getTimelineLaneVisible?.() ?? true} disabled={!adapter.setTimelineLaneVisible} onChange={(event) => { adapter.setTimelineLaneVisible?.(event.target.checked); refresh((value) => value + 1); }} />Show transcript lane</label>
          <div className="space-y-2">
            <label className="flex flex-col gap-1 text-xs">Match to video or audio
              <select className={select} value={associationValue} disabled={busy || !mediaChoices.length} onChange={(event) => setAssociation(event.target.value)}>
                <option value="">Use media named in transcript file</option>
                {mediaChoices.map((choice) => <option key={choice.assetId} value={choice.assetId}>{choice.label}</option>)}
              </select>
            </label>
            <p className="text-[11px] text-muted-foreground">If the file names a video or audio clip, it must match your choice.</p>
            <button type="button" className={`${button} border border-border`} disabled={importDisabled} title={importDisabledReason ?? undefined} onClick={() => importInput.current?.click()}>Import transcript…</button>
            <input ref={importInput} className="sr-only" tabIndex={-1} type="file" aria-label="Import transcript JSON" accept=".json,application/json" disabled={importDisabled} onChange={(event) => {
              const file = event.target.files?.[0]; event.target.value = ''; if (!file) return;
              const token = ++request.current;
              void run(async () => {
                let document: string;
                try { document = await file.text(); }
                catch { throw new Error('Could not read this file. Choose a readable transcript JSON file and try again.'); }
                if (token === request.current) await importDocument(document, associationValue || undefined);
              });
            }} /></div>
          {importDisabledReason && <p role="note" className="text-xs text-muted-foreground">{importDisabledReason}</p>}
          {pendingDocument !== null && <div className="space-y-2"><p className="text-xs text-muted-foreground">The selected file is preserved. Correct its media association or retry its pending save.</p><button className={button} disabled={busy || !mediaChoices.length || projectState?.supported !== true} onClick={() => void run(() => importDocument(pendingDocument, associationValue || undefined))}>Retry import</button><button className={button} disabled={busy} onClick={() => { setPendingDocument(null); setAssociation(null); }}>Cancel import</button></div>}
          {target && !documentId && <button type="button" className={`${button} border border-border`} disabled={busy || !adapter.promoteSource} onClick={() => void run(async () => {
            const receipt = await adapter.promoteSource!({ assetId: target.line.source.assetId, sourceId: target.line.source.sourceId });
            if (receipt.associationSaved === false) throw new Error(receipt.message ?? 'Project transcript saved. Timeline association needs retry.');
            setMessage(receipt.message ?? 'Project transcript saved.');
          })}>Save {mediaLabel(target.line.source.assetId)} as project transcript</button>}
          {projectState?.supported === false && <p className="text-xs text-muted-foreground">{projectState.reason}</p>}
          {documentId && <div className="flex flex-wrap gap-2"><button className={button} disabled={busy || !state?.canUndo} onClick={() => void history('undo')}>Undo source edit</button><button className={button} disabled={busy || !state?.canRedo} onClick={() => void history('redo')}>Redo source edit</button></div>}
        </div>
      </div>}
      <div ref={scroll} data-testid="transcript-reading-area" className="mx-3 mb-3 min-h-0 flex-1 overflow-y-auto overscroll-contain rounded-lg border border-border bg-background px-4 py-4" style={READING_AREA_STYLE}
        onWheel={() => { userScrollIntent.current = true; scrollRestorePending.current = false; setFollowing(false); }} onTouchMove={() => { userScrollIntent.current = true; scrollRestorePending.current = false; setFollowing(false); }} onPointerDown={() => { userScrollIntent.current = true; scrollRestorePending.current = false; }} onKeyDown={(event) => { if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', ' '].includes(event.key)) { userScrollIntent.current = true; scrollRestorePending.current = false; } }} onScroll={() => {
          const container = scroll.current;
          if (!container) return;
          const current = container.scrollTop;
          if (programmedScroll.current !== null && current === programmedScroll.current) { programmedScroll.current = null; userScrollIntent.current = false; return; }
          if (!observing || container.getClientRects().length === 0 || container.clientHeight <= 0) {
            scrollRestorePending.current = true;
            return;
          }
          if (userScrollIntent.current) {
            userScrollIntent.current = false;
            savedScrollTop.current = current;
            scrollRestorePending.current = false;
            programmedScroll.current = null;
            setFollowing(false);
            return;
          }
          const saved = savedScrollTop.current;
          const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
          if (scrollRestorePending.current && current < saved - 1) {
            if (saved > maxScrollTop + 1) return;
            programmedScroll.current = saved;
            container.scrollTop = saved;
            return;
          }
          if (current < saved - 1 && saved > maxScrollTop + 1) {
            scrollRestorePending.current = true;
            return;
          }
          savedScrollTop.current = current;
          scrollRestorePending.current = false;
          programmedScroll.current = null; setFollowing(false);
        }}>
        <div>
          {!lines.length ? <div className="py-8 text-center">
            {!mediaChoices.length && !(played?.media.length)
              ? <p className="text-muted-foreground">Add an audio or video clip to this timeline to see its words.</p>
              : loading
                ? <p role="status" className="text-muted-foreground">Loading transcripts…</p>
                : <p className="text-muted-foreground">No transcript for the media on this timeline yet.</p>}
            {!settingsOpen && <button type="button" className={`${button} mt-2`} disabled={importDisabled} title={importDisabledReason ?? undefined} onClick={() => props.settings?.onOpenChange(true)}>Import transcript…</button>}
          </div> : <>
            <p id={`${props.instanceId}-help`} className="sr-only">Use arrow keys to move between words. Enter or Space seeks and opens details. Tab reaches Edit. Select and copy text normally.</p>
            <div ref={prose} role="group" aria-label="Transcript text" aria-describedby={`${props.instanceId}-help`} tabIndex={0} aria-activedescendant={target ? `${props.instanceId}-target-${targetIndex.get(target.key)}` : undefined}
              className="space-y-6 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              onFocus={() => { if (skipFocusDetails.current) { skipFocusDetails.current = false; return; } setDetailsOpen(true); }} onKeyDown={(event) => {
                if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
                const index = target ? targetIndex.get(target.key) ?? 0 : 0;
                if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) {
                  event.preventDefault(); setFollowing(false); setDetailsOpen(true);
                  const next = event.key === 'Home' ? 0 : event.key === 'End' ? targets.length - 1 : Math.max(0, Math.min(targets.length - 1, index + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1)));
                  setTargetKey(targets[next]?.key ?? null);
                  // The film is long: keep the keyboard target on screen.
                  document.getElementById(`${props.instanceId}-target-${next}`)?.scrollIntoView?.({ block: 'nearest' });
                } else if ((event.key === 'Enter' || event.key === ' ') && target) { event.preventDefault(); seek(target); }
                else if (event.key === 'Escape') setDetailsOpen(false);
              }}>
              {paragraphs.map((paragraph) => {
                const current = paragraph.lines.some((line) => line === playingLine);
                return <div key={paragraph.key} data-paragraph={paragraph.key}>
                  <div className="mb-1 flex min-w-0 select-none items-baseline gap-2.5 text-[11px] leading-5">
                    <button type="button" tabIndex={-1} title={`Seek to ${clock(paragraph.start)}`} onMouseDown={(event) => event.preventDefault()} onClick={() => { seekTime(paragraph.start); }}
                      className={`shrink-0 rounded-sm tabular-nums hover:text-foreground ${current ? 'text-foreground' : 'text-muted-foreground/70'}`}>{clock(paragraph.start)}</button>
                    {paragraph.name && <span className={`min-w-0 truncate font-medium uppercase tracking-[0.08em] ${current ? 'text-foreground' : 'text-muted-foreground'}`}>{paragraph.name}</span>}
                  </div>
                  <p className="whitespace-pre-wrap break-words text-[14px] font-normal leading-[1.75] text-foreground" style={PROSE_STYLE}>
                    {paragraph.lines.map((line, lineIndex) => {
                      const aligned = line.parts.some((part) => part.word);
                      const segmentKey = JSON.stringify([line.occurrence.id]);
                      const segmentTarget = !aligned ? targets[targetIndex.get(segmentKey) ?? -1] : undefined;
                      const previous = paragraph.lines[lineIndex - 1];
                      const previousText = previous?.parts.map((part) => part.text).join('') ?? '';
                      const text = line.parts.map((part) => part.text).join('');
                      // Existing whitespace wins; otherwise lines read as prose, and a new speaker starts a block.
                      const separator = previous && !/\s$/.test(previousText) && !/^\s/.test(text) ? previous.source.speaker !== line.source.speaker && (previous.source.speaker || line.source.speaker) ? '\n\n' : ' ' : '';
                      return <span key={line.occurrence.id}>{separator}<span data-occurrence={line.occurrence.id} data-source-item={line.source.sourceItemId} data-source-asset={line.source.assetId} aria-current={line === playingLine ? 'true' : undefined}
                        className={!aligned ? `cursor-pointer rounded-sm hover:bg-muted ${detailsOpen && segmentTarget && target?.key === segmentTarget.key ? 'outline outline-1 outline-ring' : ''}` : undefined}
                        {...(segmentTarget ? { id: `${props.instanceId}-target-${targetIndex.get(segmentKey)}`, role: 'button', tabIndex: -1, title: metadata(segmentTarget), onClick: () => { if (window.getSelection()?.isCollapsed !== false) seek(segmentTarget); } } : {})}>
                        {line.parts.map((part, index) => {
                          const key = part.word ? JSON.stringify([line.occurrence.id, part.word.id]) : '';
                          const item = part.word ? targets[targetIndex.get(key) ?? -1] : undefined;
                          return item ? <span key={index} id={`${props.instanceId}-target-${targetIndex.get(key)}`} data-word-id={part.word!.id} role="button" tabIndex={-1} title={metadata(item)} onClick={() => { if (window.getSelection()?.isCollapsed !== false) seek(item); }} style={{ ...(speaking(line, part.word!.id) ? SPOKEN_WORD_STYLE : WORD_STYLE), ...(detailsOpen && target?.key === item.key ? TARGET_WORD_STYLE : {}) }}
                            className={`cursor-pointer hover:bg-muted ${speaking(line, part.word!.id) ? 'bg-primary/15 text-foreground' : ''}`}>{part.text}</span> : part.text;
                        })}
                      </span></span>;
                    })}
                  </p>
                </div>;
              })}
            </div>
          </>}
          {inspector && <div data-transcript-inspector="" className="sticky z-10 -mx-4 mt-4 border-t border-border/60 bg-background px-4 pb-4 pt-2" style={INSPECTOR_STYLE}>
            {detailsOpen && target && !activeEdit && <div aria-label="Transcript selection details" onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); setDetailsOpen(false); restoreReadingFocus(); } }}>
              <p className="text-[11px] text-muted-foreground">{metadata(target)}</p>
              <div className="mt-1 flex flex-wrap gap-1"><button type="button" className={button} onClick={() => seek(target)}>Seek {target.word ? 'word' : 'segment'}</button><button type="button" className={button} disabled={busy || !adapter.editText} onClick={() => startEdit(target)}>Edit {target.word ? 'word' : 'segment'}</button><button type="button" className={button} onClick={() => { setDetailsOpen(false); restoreReadingFocus(); }}>Dismiss details</button></div>
            </div>}
            {activeEdit && edit && <form className="space-y-2" onSubmit={(event) => { event.preventDefault(); void run(async () => {
              if (!adapter.editText) return;
              const result = await adapter.editText(edit);
              if (!result.ok) { setFallback(result.fallback ?? null); throw new Error(result.message); }
              editDraft.current = null; setEdit(null); setFallback(null); setMessage(edit.documentId ? 'Project transcript saved.' : 'Timeline correction applied; save the timeline to keep it.'); restoreReadingFocus();
            }); }} onKeyDown={(event) => { event.stopPropagation(); if (event.key === 'Escape') { editDraft.current = null; setEdit(null); setDetailsOpen(false); restoreReadingFocus(); } }}>
              <label className="flex flex-col gap-1 text-xs">{edit.type === 'word' ? 'Replacement word' : 'Replacement segment'}<textarea ref={editor} rows={3} className="w-full min-w-0 resize-y rounded-md border border-border bg-background p-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" value={edit.text} disabled={busy} onChange={(event) => {
                const next = { ...edit, text: event.target.value }; setEdit(next);
                const draft = editDraft.current;
                if (draft && next.text.trim() && draft.documentId === next.documentId && !draft.hadDraftAtStart) {
                  const result = adapter.stageTextDraft?.(next);
                  if (result && !result.ok) setError(result.message);
                  else if (result?.ok) draft.stagedHere = true;
                }
              }} /></label>
              {edit.type === 'segment' && <p className="text-xs text-muted-foreground">Replacing the segment retains its extent and clears word alignment.</p>}
              <div className="flex flex-wrap gap-1"><button type="submit" className={`${button} bg-primary/10`} disabled={busy || !edit.text.trim()}>Save correction</button><button type="button" className={button} disabled={busy} onClick={() => void run(async () => {
                const draft = editDraft.current;
                const saved = draft?.documentId && adapter.getProjectState?.().sources.some((source) => source.documentId === draft.documentId && source.assetId === edit.assetId && source.hasDraft);
                if (draft?.stagedHere && !draft.hadDraftAtStart && saved) { const result = await adapter.resolveSourceConflict?.(draft.documentId!, 'discard'); if (result && !result.ok) throw new Error(result.message); }
                editDraft.current = null; setEdit(null); setFallback(null); restoreReadingFocus();
              })}>Cancel correction</button>{fallback && <button type="button" className={button} onClick={() => { setEdit(fallback); setFallback(null); setError(''); }}>Replace whole segment instead</button>}</div>
            </form>}
          </div>}
        </div>
      </div>
    </>}
  </section>;
}
