import type {ReactElement} from 'react';
import {AbsoluteFill, Img, useCurrentFrame, useVideoConfig} from 'remotion';
import {
  BlockWipe,
  COLOR,
  LOGICAL_H,
  LOGICAL_PX,
  LOGICAL_W,
  clamp,
  clipFrames,
  elementSource,
  finiteNumber,
  integerIn,
  narrowParams,
  oneOf,
  pixelImage,
  type ElementComponentProps,
  type WipePattern,
} from '../../_shared/am';

// am-snap-plate: a pixel snapshot shown through an integer-zoom viewport.
//
// Geometry: one logical px is LOGICAL_PX (6) screen px at zoom 1. At zoom z the
// plate is drawn at LOGICAL_PX * z screen px per logical px, so each plate
// pixel stays a whole number of screen px and nearest-neighbour sampling is
// exact. Focus and pan are integer logical px; positions stay on the 6 px grid.
// Pans change only on step boundaries (stepFrames), never between them.

type Point = {x: number; y: number};
type Params = {
  /** Static URL or public path. Managed media goes in clip.asset instead. */
  src?: string;
  zoom?: 1 | 2 | 3 | 4;
  focus?: Point;
  pan?: {dx: number; dy: number};
  stepFrames?: 2 | 3;
  tint?: {color?: string; opacity?: number};
  enter?: 'cut' | 'blockWipe';
  exit?: 'cut' | 'blockWipe';
  enterFrames?: number;
  exitFrames?: number;
  pattern?: WipePattern;
  seed?: number;
  wipeColor?: string;
  background?: string;
};

const ENTER_EXIT = ['cut', 'blockWipe'] as const;
const PATTERNS: readonly WipePattern[] = ['diagonal', 'random', 'scan'];

export default function AmSnapPlate(props: ElementComponentProps): ReactElement | null {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const params = narrowParams<Params>(props.params);
  const url = elementSource(props.assetEntry, params.src);
  if (!url) {
    return null;
  }

  const zoom = integerIn(params.zoom, 1, 4, 1);
  const stepFrames = integerIn(params.stepFrames, 2, 3, 2);
  const scale = LOGICAL_PX * zoom;
  // Logical px visible at this zoom. Clamping keeps the view on the plate.
  const viewW = width / scale;
  const viewH = height / scale;

  const steps = Math.floor(frame / stepFrames);
  const focusX0 = finiteNumber(params.focus?.x, LOGICAL_W / 2);
  const focusY0 = finiteNumber(params.focus?.y, LOGICAL_H / 2);
  const dx = finiteNumber(params.pan?.dx, 0);
  const dy = finiteNumber(params.pan?.dy, 0);
  const focusX = Math.round(clamp(focusX0 + dx * steps, viewW / 2, LOGICAL_W - viewW / 2));
  const focusY = Math.round(clamp(focusY0 + dy * steps, viewH / 2, LOGICAL_H - viewH / 2));

  // Left/top of the plate: the focus point lands on the frame centre.
  const left = width / 2 - focusX * scale;
  const top = height / 2 - focusY * scale;

  const fps = props.fps;
  const total = clipFrames(props.clip, fps);
  const enter = oneOf(params.enter, ENTER_EXIT, 'cut');
  const exit = oneOf(params.exit, ENTER_EXIT, 'cut');
  const enterFrames = integerIn(params.enterFrames, 1, 60, 6);
  const exitFrames = integerIn(params.exitFrames, 1, 60, 6);
  const pattern = oneOf<WipePattern>(params.pattern, PATTERNS, 'diagonal');
  const seed = Math.trunc(finiteNumber(params.seed, 1));
  const wipeColor = params.wipeColor ?? COLOR.charcoal;
  const background = params.background ?? COLOR.paper;

  let wipe: ReactElement | null = null;
  const exitStart = total - exitFrames;
  if (enter === 'blockWipe' && frame < enterFrames) {
    wipe = (
      <BlockWipe
        width={width}
        height={height}
        paint={{mode: 'reveal', progress: (frame + 1) / enterFrames, color: wipeColor, pattern, seed}}
      />
    );
  } else if (exit === 'blockWipe' && frame >= exitStart) {
    wipe = (
      <BlockWipe
        width={width}
        height={height}
        paint={{mode: 'cover', progress: (frame - exitStart + 1) / exitFrames, color: wipeColor, pattern, seed}}
      />
    );
  }

  const tint = params.tint;
  const tintOpacity = clamp(finiteNumber(tint?.opacity, 0.25), 0, 1);

  return (
    <AbsoluteFill style={{overflow: 'hidden', backgroundColor: background}}>
      <Img
        src={url}
        crossOrigin="anonymous"
        style={{
          position: 'absolute',
          left,
          top,
          width: LOGICAL_W * scale,
          height: LOGICAL_H * scale,
          maxWidth: 'none',
          maxHeight: 'none',
          display: 'block',
          ...pixelImage,
        }}
      />
      {tint?.color ? (
        <AbsoluteFill style={{backgroundColor: tint.color, opacity: tintOpacity, pointerEvents: 'none'}} />
      ) : null}
      {wipe}
    </AbsoluteFill>
  );
}
