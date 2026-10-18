import { createElement } from 'react';
import {
  defineExtension,
  type ContributionId,
  type ExtensionContext,
  type ExtensionId,
  type PanelRenderProps,
} from '@astrid/editor-sdk';
import { TranscriptPanel } from './TranscriptPanel';
import { TranscriptLane } from '../transcript-lane/TranscriptLane';

/** Stable release identity; the V3 pack owner remains `rendering`. */
export const TRANSCRIPT_EXTENSION_ID = 'haus.astrid.transcript-lane';
export const TRANSCRIPT_PANEL_ID = 'transcript-lane-panel';
export const TRANSCRIPT_PANEL_RENDER_ID = `${TRANSCRIPT_EXTENSION_ID}.panel`;
export const TRANSCRIPT_KIND_CONTRIBUTION_ID = 'transcript-lane-kind';
export const TRANSCRIPT_KIND_ID = 'astrid.transcript';
export const TRANSCRIPT_SCHEMA_REF = 'astrid.transcript_segment/v1';

export const transcriptExtension = defineExtension({
  manifest: {
    id: TRANSCRIPT_EXTENSION_ID as ExtensionId,
    version: '1.0.0',
    apiVersion: 1,
    label: 'Transcript',
    description: 'Rendering-pack transcript panel for the Astrid editor.',
    contributions: [{
      id: TRANSCRIPT_PANEL_ID as ContributionId,
      kind: 'panel',
      label: 'Transcript',
      render: TRANSCRIPT_PANEL_RENDER_ID,
    }, {
      id: TRANSCRIPT_KIND_CONTRIBUTION_ID as ContributionId,
      kind: 'dataKind',
      kindId: TRANSCRIPT_KIND_ID,
      schemaRef: TRANSCRIPT_SCHEMA_REF,
      shape: 'interval',
      domain: 'source_seconds',
      label: 'Transcript',
      order: 10,
    }],
  },

  activate(ctx: ExtensionContext) {
    // Return a React element rather than invoking the component: hooks and
    // local drafts belong to the host-retained panel instance, not activation.
    // Registration disposal is host-owned and removes this renderer on unload.
    const panelRegistration = ctx.ui.registerRenderer<PanelRenderProps>(
      TRANSCRIPT_PANEL_RENDER_ID,
      (props) => createElement(TranscriptPanel, { ...props, context: ctx }),
    );
    const dataKindRegistration = ctx.dataKinds.register(
      TRANSCRIPT_KIND_ID,
      TranscriptLane,
      undefined,
      { supportsSparseItemWindows: true },
    );
    return {
      dispose() {
        try {
          dataKindRegistration.dispose();
        } finally {
          panelRegistration.dispose();
        }
      },
    };
  },
});

export default transcriptExtension;
