// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DataLaneRenderItem, DataLaneRendererProps } from '@astrid/editor-sdk';
import {
  computeTranscriptLaneLayout,
  readTranscriptLaneText,
  TranscriptLane,
  TRANSCRIPT_LANE_MAX_ROWS,
} from '../../../astrid/packs/rendering/ui/transcript-lane/TranscriptLane';

afterEach(cleanup);

const occurrence = (
  id: string,
  start: number,
  end: number,
  overrides: Partial<DataLaneRenderItem> = {},
): DataLaneRenderItem => ({
  id,
  sourceItemId: 'source-segment',
  sourceArtifactRef: { assetId: 'asset-a' },
  clipId: 'clip-a',
  timelineStart: start,
  timelineEnd: end,
  payload: { text: `Transcript text for ${id} — complete and readable.` },
  ...overrides,
});

function rendererProps(
  items: readonly DataLaneRenderItem[],
  overrides: Partial<DataLaneRendererProps> = {},
): DataLaneRendererProps {
  return {
    kindId: 'astrid.transcript',
    schemaRef: 'astrid.transcript_segment/v1',
    shape: 'interval',
    domain: 'source_seconds',
    startLeft: 0,
    pixelsPerSecond: 4,
    items,
    itemWindow: { startIndex: 0, endIndex: items.length, totalItemCount: items.length },
    ...overrides,
  };
}

describe('production transcript lane renderer', () => {
  it('retains exact host-mapped interval geometry and exposes the full label at low zoom', () => {
    const text = 'A deliberately long transcript segment whose complete wording remains available at every zoom level.';
    const item = occurrence('source-segment@clip-reused', 4.25, 5.5, {
      clipId: 'clip-reused',
      payload: { text },
    });
    render(<TranscriptLane {...rendererProps([item])} />);

    const control = screen.getByTestId('transcript-lane-occurrence');
    expect(control).toHaveStyle({ left: '17px', width: '5px', top: '0%', height: '100%' });
    expect(control).toHaveAttribute('aria-label', expect.stringContaining(text));
    expect(control).toHaveAttribute('title', expect.stringContaining(text));
    expect(within(control).getByText(text)).toBeTruthy();
    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.mouseEnter(control);
    const tooltip = screen.getByRole('tooltip');
    expect(tooltip.textContent).toBe(text);
    expect(tooltip).toHaveStyle({ position: 'fixed' });
    expect(control).toHaveAttribute('aria-describedby', tooltip.id);
    expect(document.body).toContainElement(tooltip);

    cleanup();
    render(<TranscriptLane {...rendererProps([item], { pixelsPerSecond: 120 })} />);
    const zoomedControl = screen.getByTestId('transcript-lane-occurrence');
    expect(zoomedControl).toHaveStyle({ left: '510px', width: '150px' });
    fireEvent.focus(zoomedControl);
    expect(screen.getByRole('tooltip').textContent).toBe(text);
    expect(readTranscriptLaneText({ text: '  ' })).toBe('(no text)');
  });

  it('anchors the content-sized full-text tooltip to the item and flips below only when above space is short', () => {
    const originalHeight = window.innerHeight;
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 320 });
    let targetRect = { left: 200, top: 210, width: 60, height: 24 };
    const makeRect = (left: number, top: number, width: number, height: number) => ({
      x: left, y: top, left, top, width, height, right: left + width, bottom: top + height, toJSON: () => ({}),
    }) as DOMRect;
    const originalRect = HTMLElement.prototype.getBoundingClientRect;
    const rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.getAttribute('data-testid') === 'transcript-lane-occurrence') return makeRect(targetRect.left, targetRect.top, targetRect.width, targetRect.height);
      if (this.getAttribute('role') === 'tooltip') return makeRect(0, 0, 150, 64);
      return originalRect.call(this);
    });
    try {
      const text = 'Lantern marks the crossing.';
      const item = occurrence('crossing', 2, 4, { payload: { text } });
      const above = render(<TranscriptLane {...rendererProps([item])} />);
      fireEvent.mouseEnter(screen.getByTestId('transcript-lane-occurrence'));
      const tooltip = screen.getByRole('tooltip');
      expect(tooltip.textContent).toBe(text);
      expect(tooltip.style.top).toBe('138px');
      expect(tooltip.style.left).toBe('155px');
      expect(tooltip.style.maxWidth).toBe('480px');
      above.unmount();

      targetRect = { left: 200, top: 20, width: 60, height: 24 };
      render(<TranscriptLane {...rendererProps([item])} />);
      fireEvent.mouseEnter(screen.getByTestId('transcript-lane-occurrence'));
      expect(screen.getByRole('tooltip').style.top).toBe('52px');
    } finally {
      rectSpy.mockRestore();
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: originalHeight });
    }
  });

  it('keeps repeated and cross-asset occurrences distinct from selection, focus, and simultaneous playback', () => {
    const first = occurrence('same-source@clip-a', 0, 2, {
      clipId: 'clip-a',
      sourceArtifactRef: { assetId: 'asset-a' },
    });
    const second = occurrence('same-source@clip-b', 1, 3, {
      clipId: 'clip-b',
      sourceArtifactRef: { assetId: 'asset-b' },
    });
    render(<TranscriptLane {...rendererProps([first, second], {
      activeItemId: second.id,
      playingItemIds: [first.id, second.id],
    })} />);

    const controls = screen.getAllByTestId('transcript-lane-occurrence');
    expect(controls.map((control) => control.style.height)).toEqual(['50%', '50%']);
    expect(controls.map((control) => control.style.top)).toEqual(['0%', '50%']);
    expect(controls.map((node) => node.getAttribute('data-item-id'))).toEqual([first.id, second.id]);
    expect(controls.map((node) => node.getAttribute('data-clip-id'))).toEqual(['clip-a', 'clip-b']);
    expect(controls.map((node) => node.getAttribute('data-source-asset'))).toEqual(['asset-a', 'asset-b']);
    expect(controls.map((node) => node.getAttribute('data-playing'))).toEqual(['true', 'true']);
    expect(controls.map((node) => node.getAttribute('data-selected'))).toEqual(['false', 'true']);
    expect(controls.map((node) => node.getAttribute('aria-pressed'))).toEqual(['false', 'true']);
    expect(controls.map((node) => node.getAttribute('tabindex'))).toEqual(['-1', '0']);
    fireEvent.focus(controls[0]);
    expect(controls[0]).toHaveAttribute('data-focused', 'true');
    expect(controls[0]).toHaveAttribute('data-selected', 'false');
    expect(controls[0]).toHaveAttribute('data-playing', 'true');
    fireEvent.blur(controls[0]);
    expect(controls[0]).toHaveAttribute('data-focused', 'false');
  });

  it('separates host selection from the keyboard roving stop, including explicit clear', () => {
    const first = occurrence('first', 0, 2);
    const second = occurrence('second', 1, 3);
    const view = render(<TranscriptLane {...rendererProps([first, second], {
      activeItemId: 'first', selectedItemId: 'second',
    })} />);

    const controls = screen.getAllByTestId('transcript-lane-occurrence');
    expect(controls.map((control) => control.getAttribute('data-selected'))).toEqual(['false', 'true']);
    expect(controls.map((control) => control.getAttribute('tabindex'))).toEqual(['0', '-1']);

    view.rerender(<TranscriptLane {...rendererProps([first, second], {
      activeItemId: 'first', selectedItemId: null,
    })} />);
    expect(controls.map((control) => control.getAttribute('data-selected'))).toEqual(['false', 'false']);
    expect(controls.map((control) => control.getAttribute('tabindex'))).toEqual(['0', '-1']);
    fireEvent.focus(controls[1]);
    expect(controls[1]).toHaveAttribute('data-focused', 'true');
    expect(controls.map((control) => control.getAttribute('data-selected'))).toEqual(['false', 'false']);

    view.rerender(<TranscriptLane {...rendererProps([first, second], {
      activeItemId: 'first', selectedItemId: 'second',
    })} />);
    expect(controls.map((control) => control.getAttribute('data-selected'))).toEqual(['false', 'true']);
    expect(controls.map((control) => control.getAttribute('tabindex'))).toEqual(['0', '-1']);
  });

  it('paints every host-selected occurrence while keeping the active item as the roving keyboard stop', () => {
    const first = occurrence('first', 0, 2);
    const second = occurrence('second', 2, 4);
    const third = occurrence('third', 4, 6);
    const view = render(<TranscriptLane {...rendererProps([first, second, third], {
      activeItemId: 'second',
      selectedItemId: 'first',
      selectedItemIds: ['first', 'third'],
    })} />);

    let controls = screen.getAllByTestId('transcript-lane-occurrence');
    expect(controls.map((control) => control.getAttribute('data-selected'))).toEqual(['true', 'false', 'true']);
    expect(controls.map((control) => control.getAttribute('tabindex'))).toEqual(['-1', '0', '-1']);

    view.rerender(<TranscriptLane {...rendererProps([first, second, third], {
      activeItemId: 'second',
      selectedItemId: 'first',
      selectedItemIds: [],
    })} />);
    controls = screen.getAllByTestId('transcript-lane-occurrence');
    expect(controls.map((control) => control.getAttribute('data-selected'))).toEqual(['false', 'false', 'false']);
    expect(controls.map((control) => control.getAttribute('tabindex'))).toEqual(['-1', '0', '-1']);
  });

  it.each([
    ['Shift', { shiftKey: true }],
    ['Command', { metaKey: true }],
    ['Control', { ctrlKey: true }],
  ] as const)('forwards %s click as an additive selection intent', (_modifier, modifier) => {
    const item = occurrence('occurrence', 0, 1);
    const onSelectItem = vi.fn();
    render(<TranscriptLane {...rendererProps([item], { onSelectItem })} />);

    fireEvent.click(screen.getByTestId('transcript-lane-occurrence'), modifier);
    expect(onSelectItem).toHaveBeenCalledWith('occurrence', { additive: true });
  });

  it('keeps idle segments neutral while selection, playback, and keyboard focus stay distinct', () => {
    const first = occurrence('first', 0, 2);
    const second = occurrence('second', 2, 4);
    const view = render(<TranscriptLane {...rendererProps([first, second], {
      activeItemId: 'first', selectedItemId: null, playingItemIds: ['second'],
    })} />);
    const [firstControl, secondControl] = screen.getAllByTestId('transcript-lane-occurrence');

    expect(firstControl.style.background).toBe('hsl(var(--card) / 0.9)');
    expect(firstControl.style.color).toBe('hsl(var(--foreground))');
    expect(firstControl.style.border).toBe('1px solid hsl(var(--border))');
    expect(secondControl.style.background).toBe('hsl(var(--card) / 0.9)');
    expect(secondControl.style.boxShadow).toBe('inset 0 0 0 2px var(--video-editor-accent-fg)');
    expect(firstControl.className).toContain('focus-visible:ring-2');

    view.rerender(<TranscriptLane {...rendererProps([first, second], {
      activeItemId: 'first', selectedItemId: 'first', playingItemIds: ['second'],
    })} />);
    expect(firstControl.style.background).toBe('var(--video-editor-accent-bg-strong)');
    expect(firstControl.style.color).toBe('var(--video-editor-accent-fg)');
    expect(firstControl.style.outline).toBe('2px solid var(--video-editor-accent-ring)');
    expect(secondControl.style.background).toBe('hsl(var(--card) / 0.9)');
    expect(secondControl.style.boxShadow).toBe('inset 0 0 0 2px var(--video-editor-accent-fg)');
  });

  it('bounds dense overlap to three rows, reports overflow, and promotes keyboard selection into view', () => {
    const items = Array.from({ length: 6 }, (_, index) =>
      occurrence(`segment-${index}@clip-${index}`, 10, 12, {
        sourceItemId: 'shared-logical-source-id',
        sourceArtifactRef: { assetId: `asset-${index}` },
        clipId: `clip-${index}`,
      }));
    const placement = computeTranscriptLaneLayout(items);
    expect(TRANSCRIPT_LANE_MAX_ROWS).toBe(3);
    expect([...placement.rowByOccurrenceId.values()].filter((row) => row !== null)).toHaveLength(3);
    expect(placement.overflowGroups[0].items.map((item) => item.id)).toEqual([
      'segment-3@clip-3', 'segment-4@clip-4', 'segment-5@clip-5',
    ]);

    const onSelectItem = vi.fn();
    const onNavigateItem = vi.fn();
    const view = render(<TranscriptLane {...rendererProps(items, { onSelectItem, onNavigateItem })} />);
    const visibleControls = screen.getAllByTestId('transcript-lane-occurrence');
    expect(visibleControls).toHaveLength(3);
    expect(visibleControls.map((control) => control.style.height)).toEqual([
      '33.333333333333336%', '33.333333333333336%', '33.333333333333336%',
    ]);
    const overflow = screen.getByTestId('transcript-lane-overflow');
    expect(overflow).toHaveAttribute('data-overflow-count', '3');
    expect(overflow).toHaveAttribute('tabindex', '0');
    expect(overflow).toHaveAttribute('aria-label', expect.stringContaining('3 more overlapping transcript segments'));
    fireEvent.click(overflow);
    expect(onSelectItem).toHaveBeenCalledWith('segment-3@clip-3');
    fireEvent.keyDown(screen.getAllByTestId('transcript-lane-occurrence')[0], { key: 'ArrowRight' });
    expect(onNavigateItem).toHaveBeenCalledWith('segment-0@clip-0', 'next');

    view.rerender(<TranscriptLane {...rendererProps(items, { activeItemId: 'segment-5@clip-5' })} />);
    const renderedIds = screen.getAllByTestId('transcript-lane-occurrence')
      .map((node) => node.getAttribute('data-item-id'));
    expect(renderedIds).toContain('segment-5@clip-5');
    expect(screen.getByTestId('transcript-lane-overflow')).toHaveAttribute('data-overflow-count', '3');
    expect(screen.getByTestId('transcript-lane-overflow-items').textContent).toContain('segment-4@clip-4');
  });
});
