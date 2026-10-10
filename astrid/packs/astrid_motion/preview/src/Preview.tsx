import type {ReactElement} from 'react';
import {AbsoluteFill, Img, Sequence, useVideoConfig} from 'remotion';
import {elementSource, type ElementComponentProps} from '../../elements/_shared/am';

// Control: the same plate through a plain <Img> with no pixelated style. It shows
// the renderer's default smoothing, which is the Q2 comparison.
const ControlPlain = (props: ElementComponentProps): ReactElement | null => {
  const url = elementSource(props.assetEntry, undefined);
  if (!url) return null;
  return (
    <AbsoluteFill>
      <Img src={url} crossOrigin="anonymous" style={{position: 'absolute', left: 0, top: 0, width: 1920, height: 1080}} />
    </AbsoluteFill>
  );
};

type Comp = (props: ElementComponentProps) => ReactElement | null;

// Every element of the pack, found by the bundler (webpack's require.context): a new element is
// previewable as soon as its folder exists. No hand-kept list (one used to drop am-churn silently).
declare const require: {context: (dir: string, deep: boolean, match: RegExp) => {keys: () => string[]; (key: string): {default: Comp}}};
const elements = require.context('../../elements/effects', true, /^\.\/am-[a-z0-9-]+\/component\.tsx$/);
const REGISTRY: Record<string, Comp> = {
  ...Object.fromEntries(elements.keys().map((key) => [key.split('/')[1], elements(key).default])),
  'control-plain': ControlPlain,
};

export type Layer = {element: string; at: number; hold: number; asset?: string; params?: Record<string, unknown>};
export type AssetDef = {file?: string; type?: string; resolution?: string};
export type SceneProps = {layers: Layer[]; assets: Record<string, AssetDef>};

// Mounts each layer the way the composition does: clip-relative frames,
// clip.params as params, and clip.asset resolved to assetEntry.
export const Preview = ({layers, assets}: SceneProps): ReactElement => {
  const {fps} = useVideoConfig();
  return (
    <AbsoluteFill style={{backgroundColor: '#F7F4ED'}}>
      {layers.map((layer, index) => {
        const Component = REGISTRY[layer.element];
        if (!Component) {
          // never a silent blank: say which element, and which exist
          throw new Error(`preview: no element ${layer.element}; elements: ${Object.keys(REGISTRY).sort().join(', ')}`);
        }
        const params = layer.params ?? {};
        const clip = {id: 'l' + index, at: layer.at, track: 'fx', clipType: layer.element, hold: layer.hold, asset: layer.asset, params};
        return (
          <Sequence key={index} from={Math.round(layer.at * fps)} durationInFrames={Math.max(1, Math.round(layer.hold * fps))}>
            <Component
              clip={clip as never}
              params={params}
              theme={{} as never}
              fps={fps}
              assetEntry={layer.asset ? assets[layer.asset] : undefined}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
