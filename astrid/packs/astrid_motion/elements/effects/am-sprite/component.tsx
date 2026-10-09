import type {CSSProperties, ReactElement} from 'react';
import {Img, useCurrentFrame} from 'remotion';
import {
  LOGICAL_PX,
  elementSource,
  finiteNumber,
  integerIn,
  narrowParams,
  oneOf,
  pixelImage,
  stepStart,
  type ElementComponentProps,
} from '../../_shared/am';

// am-sprite: a transparent pixel cutout on the logical grid.
//
// Each art px is `scale` screen px (6 means one art px per logical px). The
// sprite's top-left sits at (x, y) logical px, so positions are multiples of
// LOGICAL_PX screen px. The outer box is scaled from its top-left corner, and
// the flip is applied inside it, so the sprite stays anchored on its corner.
// Keyframes, flips and slide steps update only on step boundaries.

type Keyframe = {frame: number; x: number; y: number; flipX?: boolean};
type SpriteFrames = {frameWidth: number; frameHeight: number; count: number; fps: number};
type Params = {
  src?: string;
  scale?: number;
  x?: number;
  y?: number;
  flipX?: boolean;
  keyframes?: Keyframe[];
  stepFrames?: 2 | 3;
  enter?: 'stamp' | 'slideIn' | 'cut';
  slideFrom?: 'left' | 'right' | 'top' | 'bottom';
  slideDistance?: number;
  frames?: SpriteFrames;
  blinkAt?: number | number[];
  blinkIndex?: number;
};

const ENTERS = ['stamp', 'slideIn', 'cut'] as const;
const SIDES = ['left', 'right', 'top', 'bottom'] as const;
const SLIDE_STEPS = 4;

export default function AmSprite(props: ElementComponentProps): ReactElement | null {
  const frame = useCurrentFrame();
  const params = narrowParams<Params>(props.params);
  const url = elementSource(props.assetEntry, params.src);
  if (!url) {
    return null;
  }

  const fps = props.fps;
  const scale = integerIn(params.scale, 1, 24, 6);
  const stepFrames = integerIn(params.stepFrames, 2, 3, 2);
  const sf = stepStart(frame, stepFrames);

  // Active keyframe: the last one at or before this step. Keyframes apply
  // as held poses, so the sprite never interpolates between them.
  let x = finiteNumber(params.x, 0);
  let y = finiteNumber(params.y, 0);
  let flipX = params.flipX === true;
  const keyframes = [...(params.keyframes ?? [])].sort((a, b) => a.frame - b.frame);
  for (const key of keyframes) {
    if (key.frame <= sf) {
      x = finiteNumber(key.x, x);
      y = finiteNumber(key.y, y);
      flipX = key.flipX ?? flipX;
    }
  }

  let visible = true;
  let dx = 0;
  let dy = 0;
  const enter = oneOf(params.enter, ENTERS, 'cut');
  if (enter === 'stamp') {
    // 2-frame flicker (on, off) then land 1 px lower on frame 2.
    if (frame < 2) {
      visible = frame === 0;
      dy = -1;
    }
  } else if (enter === 'slideIn') {
    const side = oneOf(params.slideFrom, SIDES, 'left');
    const distance = Math.max(0, finiteNumber(params.slideDistance, 24));
    const k = Math.min(SLIDE_STEPS, Math.floor(frame / stepFrames));
    const remaining = Math.round((distance * (SLIDE_STEPS - k)) / SLIDE_STEPS);
    if (side === 'left') dx = -remaining;
    if (side === 'right') dx = remaining;
    if (side === 'top') dy = -remaining;
    if (side === 'bottom') dy = remaining;
  }

  const blinks: number[] = Array.isArray(params.blinkAt)
    ? params.blinkAt
    : params.blinkAt === undefined
      ? []
      : [params.blinkAt];
  const blinking = blinks.some((at) => frame >= at && frame < at + 2);

  const frames = params.frames;
  let index = 0;
  if (frames && frames.count >= 1 && frames.fps > 0) {
    index = Math.floor((frame * frames.fps) / fps) % Math.max(1, Math.round(frames.count));
  }
  if (blinking) {
    if (frames && typeof params.blinkIndex === 'number') {
      index = params.blinkIndex;
    } else {
      visible = false;
    }
  }

  const outer: CSSProperties = {
    position: 'absolute',
    left: (x + dx) * LOGICAL_PX,
    top: (y + dy) * LOGICAL_PX,
    transform: `scale(${scale})`,
    transformOrigin: '0 0',
    lineHeight: 0,
    display: visible ? 'block' : 'none',
  };
  const flip: CSSProperties = {
    transform: flipX ? 'scaleX(-1)' : undefined,
    transformOrigin: 'center center',
  };

  if (frames) {
    const fw = Math.max(1, Math.round(frames.frameWidth));
    const fh = Math.max(1, Math.round(frames.frameHeight));
    return (
      <div style={outer}>
        <div style={{...flip, width: fw, height: fh}}>
          <div style={{position: 'relative', width: fw, height: fh, overflow: 'hidden'}}>
            <Img
              src={url}
              crossOrigin="anonymous"
              style={{position: 'absolute', left: -index * fw, top: 0, display: 'block', maxWidth: 'none', ...pixelImage}}
            />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={outer}>
      <div style={flip}>
        <Img src={url} crossOrigin="anonymous" style={{display: 'block', maxWidth: 'none', ...pixelImage}} />
      </div>
    </div>
  );
}
