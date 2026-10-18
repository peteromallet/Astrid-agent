import { createElement } from 'react';
import {
  defineExtension,
  type ContributionId,
  type ExtensionContext,
  type ExtensionId,
  type PanelRenderProps,
} from '@astrid/editor-sdk';
import { BeatCuePanel } from './BeatCuePanel';

const EXTENSION_ID = 'com.example.beat-cues' as ExtensionId;
const PANEL_ID = 'beat-cues-panel' as ContributionId;
const RENDER_ID = `${EXTENSION_ID}.panel`;

/** Tiny panel-only authoring example; the V3 pack key is declared separately. */
export const beatCueExtension = defineExtension({
  manifest: {
    id: EXTENSION_ID,
    version: '1.0.0',
    apiVersion: 1,
    label: 'Beat Cues',
    description: 'Jump to a few example beat cues in the current timeline.',
    contributions: [{
      id: PANEL_ID,
      kind: 'panel',
      label: 'Beat Cues',
      render: RENDER_ID,
    }],
  },

  activate(ctx: ExtensionContext) {
    return ctx.ui.registerRenderer<PanelRenderProps>(
      RENDER_ID,
      (props) => createElement(BeatCuePanel, props),
    );
  },
});

export default beatCueExtension;
