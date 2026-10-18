// @vitest-environment jsdom
import { createElement, isValidElement } from 'react';
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ExtensionContext, PanelRenderProps } from '@astrid/editor-sdk';
import transcriptExtension, {
  TRANSCRIPT_EXTENSION_ID,
  TRANSCRIPT_KIND_CONTRIBUTION_ID,
  TRANSCRIPT_KIND_ID,
  TRANSCRIPT_PANEL_ID,
  TRANSCRIPT_PANEL_RENDER_ID,
} from '../../../astrid/packs/rendering/ui/transcript/extension';
import { TranscriptPanel } from '../../../astrid/packs/rendering/ui/transcript/TranscriptPanel';

afterEach(cleanup);

describe('rendering-pack transcript panel receiving boundary', () => {
  it('declares one panel and returns the renderer registration for disposal', async () => {
    const dispose = vi.fn();
    const registration = { dispose };
    const dataKindDispose = vi.fn();
    const dataKindRegistration = { dispose: dataKindDispose };
    let renderer!: (props: PanelRenderProps) => unknown;
    const registerRenderer = vi.fn((_id: string, value: typeof renderer) => {
      renderer = value;
      return registration;
    });
    const registerDataKind = vi.fn(() => dataKindRegistration);
    const context = { ui: { registerRenderer }, dataKinds: { register: registerDataKind } } as unknown as ExtensionContext;

    expect(transcriptExtension.manifest.id).toBe(TRANSCRIPT_EXTENSION_ID);
    expect(transcriptExtension.manifest.contributions).toEqual([{
      id: TRANSCRIPT_PANEL_ID,
      kind: 'panel',
      label: 'Transcript',
      render: TRANSCRIPT_PANEL_RENDER_ID,
    }, {
      id: TRANSCRIPT_KIND_CONTRIBUTION_ID,
      kind: 'dataKind',
      kindId: TRANSCRIPT_KIND_ID,
      schemaRef: 'astrid.transcript_segment/v1',
      shape: 'interval',
      domain: 'source_seconds',
      label: 'Transcript',
      order: 10,
    }]);
    const handle = await transcriptExtension.activate(context);
    expect(registerRenderer).toHaveBeenCalledWith(TRANSCRIPT_PANEL_RENDER_ID, expect.any(Function));
    expect(registerDataKind).toHaveBeenCalledWith(
      TRANSCRIPT_KIND_ID,
      expect.any(Function),
      undefined,
      { supportsSparseItemWindows: true },
    );
    expect(isValidElement(renderer({ instanceId: 'host-a:transcript', visible: true }))).toBe(true);
    if (handle) handle.dispose();
    expect(dispose).toHaveBeenCalledOnce();
    expect(dataKindDispose).toHaveBeenCalledOnce();
  });

  it('accepts panel-only hosts and keeps instance identity while visibility changes', () => {
    const context = {} as ExtensionContext;
    const panel = (instanceId: string, visible: boolean) => createElement(TranscriptPanel, {
      instanceId, visible, context,
    });
    const first = render(panel('editor-a:transcript', true));
    const second = render(panel('editor-b:transcript', true));
    const firstNode = first.container.firstElementChild!;
    expect(firstNode.getAttribute('data-panel-instance-id')).toBe('editor-a:transcript');
    expect(second.container.firstElementChild!.getAttribute('data-panel-instance-id')).toBe('editor-b:transcript');
    first.rerender(panel('editor-a:transcript', false));
    expect(first.container.firstElementChild).toBe(firstNode);
    expect(firstNode.hasAttribute('hidden')).toBe(true);
    expect(second.container.firstElementChild!.hasAttribute('hidden')).toBe(false);
    first.rerender(panel('editor-a:transcript', true));
    expect(first.container.firstElementChild).toBe(firstNode);
    expect(firstNode.hasAttribute('hidden')).toBe(false);
  });

  it('reports an unsupported host instead of consuming its internal render context', () => {
    const view = render(createElement(TranscriptPanel, { context: {} } as never));
    expect(view.getByRole('alert').textContent).toContain('Update the editor');
  });
});
