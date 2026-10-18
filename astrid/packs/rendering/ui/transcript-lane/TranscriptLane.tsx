import { createPortal } from 'react-dom';
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent } from 'react';
import type { DataLaneRenderItem, DataLaneRendererProps } from '@astrid/editor-sdk';

export const TRANSCRIPT_LANE_MAX_ROWS = 3;

export interface TranscriptLaneLayout {
  readonly rowByOccurrenceId: ReadonlyMap<string, number | null>;
  readonly overflowGroups: readonly Readonly<{
    readonly key: string;
    readonly start: number;
    readonly items: readonly DataLaneRenderItem[];
  }>[];
}

interface MutableOverlapGroup {
  readonly items: DataLaneRenderItem[];
  readonly visible: Array<{ item: DataLaneRenderItem; row: number }>;
  readonly overflow: DataLaneRenderItem[];
  end: number;
}

/**
 * Greedily pack host-mapped half-open intervals into at most three rows.
 * Occurrence ids are the only keys: equal source ids on different assets or
 * clips remain separate controls. No timing is projected or deduplicated here.
 */
export function computeTranscriptLaneLayout(
  items: readonly DataLaneRenderItem[],
  protectedOccurrenceId?: string,
): TranscriptLaneLayout {
  const sorted = items
    .map((item, sourceIndex) => ({ item, sourceIndex }))
    .sort((a, b) => a.item.timelineStart - b.item.timelineStart
      || a.item.id.localeCompare(b.item.id)
      || a.sourceIndex - b.sourceIndex);
  const rowByOccurrenceId = new Map<string, number | null>();
  const overflowGroups: Array<{ key: string; start: number; items: DataLaneRenderItem[] }> = [];
  let current: MutableOverlapGroup | null = null;

  const finishGroup = () => {
    if (!current) return;
    if (current.overflow.length > 0) {
      const promoted = protectedOccurrenceId === undefined
        ? undefined
        : current.overflow.find((item) => item.id === protectedOccurrenceId);
      if (promoted) {
        const displaced = current.visible.find((placement) =>
          placement.row === TRANSCRIPT_LANE_MAX_ROWS - 1
          && placement.item.timelineStart < promoted.timelineEnd
          && placement.item.timelineEnd > promoted.timelineStart);
        if (displaced) {
          rowByOccurrenceId.set(displaced.item.id, null);
          current.overflow.splice(current.overflow.indexOf(promoted), 1, displaced.item);
          current.visible.splice(current.visible.indexOf(displaced), 1, { item: promoted, row: TRANSCRIPT_LANE_MAX_ROWS - 1 });
          rowByOccurrenceId.set(promoted.id, TRANSCRIPT_LANE_MAX_ROWS - 1);
        }
      }
      overflowGroups.push({
        key: current.items[0]?.id ?? 'transcript-overflow',
        start: Math.min(...current.overflow.map((item) => item.timelineStart)),
        items: [...current.overflow],
      });
    }
    current = null;
  };

  for (const { item } of sorted) {
    const start = item.timelineStart;
    const end = item.timelineEnd;
    const valid = Number.isFinite(start) && Number.isFinite(end) && end > start;
    if (!valid) {
      finishGroup();
      rowByOccurrenceId.set(item.id, 0);
      continue;
    }
    if (current && start >= current.end) finishGroup();
    if (!current) current = { items: [], visible: [], overflow: [], end: end };

    current.items.push(item);
    current.end = Math.max(current.end, end);
    const usedRows = new Set(current.visible
      .filter((placement) => placement.item.timelineEnd > start)
      .map((placement) => placement.row));
    let row = 0;
    while (usedRows.has(row) && row < TRANSCRIPT_LANE_MAX_ROWS) row += 1;
    if (row < TRANSCRIPT_LANE_MAX_ROWS) {
      current.visible.push({ item, row });
      rowByOccurrenceId.set(item.id, row);
    } else {
      current.overflow.push(item);
      rowByOccurrenceId.set(item.id, null);
    }
  }
  finishGroup();

  return { rowByOccurrenceId, overflowGroups };
}

/** Transcript payload is opaque to the host; the registered schema owns text. */
export function readTranscriptLaneText(payload: unknown): string {
  if (payload !== null && typeof payload === 'object' && 'text' in payload) {
    const text: unknown = payload.text;
    if (typeof text === 'string' && text.trim() !== '') return text;
  }
  return '(no text)';
}

function itemSourceLabel(item: DataLaneRenderItem): string {
  const assetId = item.sourceArtifactRef?.assetId;
  const sourceId = item.sourceItemId;
  return [assetId ? `asset ${assetId}` : null, sourceId ? `source ${sourceId}` : null]
    .filter((value): value is string => value !== null)
    .join(', ');
}

function navigationDirection(event: KeyboardEvent<HTMLButtonElement>) {
  switch (event.key) {
    case 'ArrowLeft':
    case 'ArrowUp': return 'previous' as const;
    case 'ArrowRight':
    case 'ArrowDown': return 'next' as const;
    case 'Home': return 'first' as const;
    case 'End': return 'last' as const;
    default: return null;
  }
}

function TranscriptSegmentControl({
  item,
  row,
  props,
  absoluteIndex,
  totalItemCount,
  rowCount,
}: {
  readonly item: DataLaneRenderItem;
  readonly row: number;
  readonly props: DataLaneRendererProps;
  readonly absoluteIndex: number;
  readonly totalItemCount: number;
  readonly rowCount: number;
}) {
  const [focused, setFocused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [tooltipPosition, setTooltipPosition] = useState<{ left: number; top: number; maxWidth: number } | null>(null);
  const controlRef = useRef<HTMLButtonElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const tooltipCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tooltipId = useId();
  const tooltipOpen = focused || hovered;
  const text = readTranscriptLaneText(item.payload);
  const selected = props.selectedItemIds !== undefined
    ? props.selectedItemIds.includes(item.id)
    : props.selectedItemId === undefined
      ? item.id === props.activeItemId
      : item.id === props.selectedItemId;
  const playing = props.playingItemIds?.includes(item.id) === true;
  const left = item.timelineStart * props.pixelsPerSecond;
  const width = Math.max(1, (item.timelineEnd - item.timelineStart) * props.pixelsPerSecond);
  const style: CSSProperties = {
    position: 'absolute',
    top: `${(row * 100) / rowCount}%`,
    left,
    width,
    height: `${100 / rowCount}%`,
    boxSizing: 'border-box',
    overflow: 'hidden',
    zIndex: selected ? 25 : playing ? 15 : 1,
    padding: '0 6px',
    borderRadius: 4,
    border: selected
      ? '1px solid var(--video-editor-accent-ring)'
      : '1px solid hsl(var(--border))',
    outline: selected ? '2px solid var(--video-editor-accent-ring)' : undefined,
    outlineOffset: selected ? 1 : undefined,
    boxShadow: playing ? 'inset 0 0 0 2px var(--video-editor-accent-fg)' : undefined,
    background: selected
      ? 'var(--video-editor-accent-bg-strong)'
      : 'hsl(var(--card) / 0.9)',
    color: selected ? 'var(--video-editor-accent-fg)' : 'hsl(var(--foreground))',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    textAlign: 'center',
    font: 'inherit',
    fontSize: 10,
    lineHeight: 'normal',
    cursor: 'pointer',
  };

  useLayoutEffect(() => {
    if (!tooltipOpen || !controlRef.current) {
      setTooltipPosition(null);
      return;
    }
    const updatePosition = () => {
      const rect = controlRef.current?.getBoundingClientRect();
      if (!rect) return;
      const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
      const maxWidth = Math.max(1, Math.min(480, viewportWidth - 16));
      // Start just below the target so the intrinsic tooltip size can be
      // measured after its portal mounts. The next layout effect places it
      // precisely above the occurrence whenever there is room.
      setTooltipPosition({ left: rect.left, top: rect.bottom + 8, maxWidth });
    };
    updatePosition();
  }, [tooltipOpen]);

  useLayoutEffect(() => {
    if (!tooltipOpen || !tooltipPosition || !controlRef.current || !tooltipRef.current) return;
    const updatePosition = () => {
      const target = controlRef.current?.getBoundingClientRect();
      const tooltip = tooltipRef.current?.getBoundingClientRect();
      if (!target || !tooltip) return;
      const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
      const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
      const width = Math.min(viewportWidth - 16, tooltip.width || tooltipRef.current?.scrollWidth || 1);
      const height = Math.min(viewportHeight - 16, tooltip.height || tooltipRef.current?.scrollHeight || 32);
      const left = Math.max(8, Math.min(target.left + target.width / 2 - width / 2, viewportWidth - width - 8));
      const roomAbove = target.top - height - 8;
      const top = roomAbove >= 8
        ? roomAbove
        : Math.max(8, Math.min(target.bottom + 8, viewportHeight - height - 8));
      setTooltipPosition((current) => current && current.left === left && current.top === top && current.maxWidth === Math.min(480, viewportWidth - 16)
        ? current
        : { left, top, maxWidth: Math.max(1, Math.min(480, viewportWidth - 16)) });
    };
    updatePosition();
    window.addEventListener('resize', updatePosition);
    window.addEventListener('scroll', updatePosition, true);
    return () => {
      window.removeEventListener('resize', updatePosition);
      window.removeEventListener('scroll', updatePosition, true);
    };
  }, [tooltipOpen, tooltipPosition]);

  const showTooltip = () => {
    if (tooltipCloseTimer.current !== null) clearTimeout(tooltipCloseTimer.current);
    tooltipCloseTimer.current = null;
    setHovered(true);
  };
  const queueTooltipClose = () => {
    if (tooltipCloseTimer.current !== null) clearTimeout(tooltipCloseTimer.current);
    tooltipCloseTimer.current = setTimeout(() => { setHovered(false); tooltipCloseTimer.current = null; }, 100);
  };
  useEffect(() => () => {
    if (tooltipCloseTimer.current !== null) clearTimeout(tooltipCloseTimer.current);
  }, []);

  return (
    <>
    <button
      ref={controlRef}
      type="button"
      data-testid="transcript-lane-occurrence"
      data-item-id={item.id}
      data-clip-id={item.clipId}
      data-source-asset={item.sourceArtifactRef?.assetId}
      data-selected={String(selected)}
      data-focused={String(focused)}
      data-playing={String(playing)}
      aria-pressed={selected}
      aria-describedby={tooltipOpen ? tooltipId : undefined}
      aria-label={`Transcript segment: ${text}${itemSourceLabel(item) ? `, ${itemSourceLabel(item)}` : ''}, ${item.timelineStart.toFixed(2)} to ${item.timelineEnd.toFixed(2)} seconds`}
      aria-posinset={absoluteIndex + 1}
      aria-setsize={totalItemCount}
      title={`${text}${itemSourceLabel(item) ? ` · ${itemSourceLabel(item)}` : ''}`}
      tabIndex={item.id === props.activeItemId ? 0 : -1}
      className="group focus-visible:z-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      style={style}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      onMouseEnter={showTooltip}
      onMouseLeave={queueTooltipClose}
      onClick={(event: MouseEvent<HTMLButtonElement>) => {
        event.stopPropagation();
        if (event.shiftKey || event.metaKey || event.ctrlKey) {
          props.onSelectItem?.(item.id, { additive: true });
        } else {
          props.onSelectItem?.(item.id);
        }
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') { setHovered(false); setFocused(false); return; }
        const direction = navigationDirection(event);
        if (!direction) return;
        event.preventDefault();
        event.stopPropagation();
        props.onNavigateItem?.(item.id, direction);
      }}
    >
      <span aria-hidden="true" className="block min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
        {text}
      </span>
    </button>
    {tooltipOpen && tooltipPosition && typeof document !== 'undefined' && createPortal(
      <div
        ref={tooltipRef}
        id={tooltipId}
        role="tooltip"
        className="fixed z-[10000] max-h-[min(22.5rem,calc(100dvh-1rem))] w-max overflow-auto whitespace-pre-wrap break-words rounded border border-border bg-popover p-2 text-left text-xs leading-snug text-popover-foreground shadow-md"
        style={{ position: 'fixed', zIndex: 10000, left: tooltipPosition.left, top: tooltipPosition.top, maxWidth: tooltipPosition.maxWidth, overflowWrap: 'anywhere' }}
        onMouseEnter={showTooltip}
        onMouseLeave={queueTooltipClose}
      >{text}</div>,
      document.body,
    )}
    </>
  );
}

/** Production transcript dataKind renderer; it only paints host-projected occurrences. */
export function TranscriptLane(props: DataLaneRendererProps) {
  const protectedOccurrenceId = props.focusItemId ?? props.activeItemId;
  const layout = computeTranscriptLaneLayout(props.items, protectedOccurrenceId);
  const rowCount = Math.max(1, ...[...layout.rowByOccurrenceId.values()]
    .filter((row): row is number => row !== null)
    .map((row) => row + 1));
  const itemIndices = props.itemWindow?.itemIndices;
  const startIndex = props.itemWindow?.startIndex ?? 0;
  const totalItemCount = props.itemWindow?.totalItemCount ?? props.items.length;
  const itemIndex = new Map(props.items.map((item, index) => [
    item.id,
    itemIndices?.[index] ?? startIndex + index,
  ]));
  const overflowItems = new Map<string, DataLaneRenderItem>();
  for (const group of layout.overflowGroups) {
    for (const item of group.items) overflowItems.set(item.id, item);
  }

  return (
    <div
      data-testid="transcript-lane-renderer"
      role="group"
      aria-label="Transcript occurrences"
      style={{ position: 'relative', width: '100%', height: '100%', overflow: 'visible' }}
    >
      {props.items.map((item) => {
        const row = layout.rowByOccurrenceId.get(item.id);
        if (row === null || row === undefined) return null;
        return (
          <TranscriptSegmentControl
            key={item.id}
            item={item}
            row={row}
            props={props}
            absoluteIndex={itemIndex.get(item.id) ?? 0}
            totalItemCount={totalItemCount}
            rowCount={rowCount}
          />
        );
      })}
      {layout.overflowGroups.map((group) => {
        const first = group.items[0];
        const playingCount = group.items.filter((item) => props.playingItemIds?.includes(item.id)).length;
        const firstText = first ? readTranscriptLaneText(first.payload) : '';
        const label = `${group.items.length} more overlapping transcript segments${playingCount > 0 ? `, ${playingCount} currently playing` : ''}${firstText ? `; first: ${firstText}` : ''}`;
        return (
          <button
            key={`overflow:${group.key}`}
            type="button"
            data-testid="transcript-lane-overflow"
            data-overflow-count={group.items.length}
            data-playing={String(playingCount > 0)}
            aria-label={`${label}. Select to reveal the first hidden occurrence.`}
            title={label}
            tabIndex={0}
            className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            style={{
              position: 'absolute',
              top: `${((rowCount - 1) * 100) / rowCount}%`,
              left: group.start * props.pixelsPerSecond,
              zIndex: 30,
              minWidth: 36,
              height: `${100 / rowCount}%`,
              border: 0,
              borderRadius: 4,
              background: 'var(--video-editor-accent-bg-strong)',
              color: 'var(--video-editor-accent-fg)',
              fontSize: 10,
              cursor: 'pointer',
            }}
            onClick={(event: MouseEvent<HTMLButtonElement>) => {
              event.stopPropagation();
              if (first) props.onSelectItem?.(first.id);
            }}
            onKeyDown={(event) => {
              const direction = navigationDirection(event);
              if (!direction) return;
              event.preventDefault();
              event.stopPropagation();
              if (first) props.onNavigateItem?.(first.id, direction);
            }}
          >
            +{group.items.length}
          </button>
        );
      })}
      <span className="sr-only" data-testid="transcript-lane-overflow-items">
        {[...overflowItems.values()].map((item) => readTranscriptLaneText(item.payload)).join(' ')}
      </span>
    </div>
  );
}
