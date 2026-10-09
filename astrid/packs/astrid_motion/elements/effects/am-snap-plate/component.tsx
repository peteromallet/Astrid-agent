import type {ReactElement} from 'react';
import {AbsoluteFill, Img, useCurrentFrame, useVideoConfig} from 'remotion';
import {
  BlockWipe,
  COLOR,
  LOGICAL_H,
  LOGICAL_W,
  clamp,
  clipFrames,
  elementSource,
  finiteNumber,
  frameList,
  integerIn,
  narrowParams,
  oneOf,
  pixelImage,
  spanList,
  type ElementComponentProps,
  type WipePattern,
} from '../../_shared/am';
import {
  type PresenterTiming,
  type Point,
  type Span,
  presenterView,
} from '../am-presenter/presenter-core';
import {MosaicImage, mosaicBlockAt, type MosaicRamp} from '../../_shared/mosaic';

// am-snap-plate: a pixel snapshot shown through an integer-zoom viewport.
//
// The view is am-presenter's presenterView(), with the same params, so a plate
// under a presenter overlay stays registered through every punch and bob. The
// pan step is folded into the focus before the view is taken; everything else
// (zoom, punch, bob, clamping, rounding) is the shared transform.
//
// Geometry: one logical px is 6 screen px at zoom 1 (presenterView's BASE_SCALE)
// and the composition is 1920x1080. Focus and pan are integer logical px; the
// view stays on the 6 px grid. Pans change only on step boundaries (stepFrames).
//
// Base zoom is 1-3. presenterView caps the base at 3; zoom 4 is reached only by
// a punch (punchAt lifts zoom by one, to 4 at most), exactly as am-presenter does.

type Params = {
  /** Static URL or public path. Managed media goes in clip.asset instead. */
  src?: string;
  zoom?: 1 | 2 | 3;
  focus?: Point;
  pan?: {dx: number; dy: number};
  stepFrames?: 2 | 3;
  /** Clip frames where zoom lifts one step for 6 frames (presenter punch). */
  punchAt?: number[];
  /** 0 | 1: nudge the view down one logical px on stressed words (presenter bob). */
  bob?: number;
  /** [start_s, end_s] word spans: the bob and the phase of stressed words follow them. */
  words?: Span[];
  /** Presenter seed. Drives the bob phase; also the random block-wipe order. */
  seed?: number;
  tint?: {color?: string; opacity?: number};
  enter?: 'cut' | 'blockWipe';
  /** Not `exit`: the timeline reads params.exit as an animation phase. */
  exitWipe?: 'cut' | 'blockWipe';
  enterFrames?: number;
  exitFrames?: number;
  pattern?: WipePattern;
  wipeColor?: string;
  background?: string;
  /** Pixelate transition in: starts as coarse blocks and resolves (see _shared/mosaic). */
  mosaicIn?: MosaicRamp | null;
  /** Pixelate transition out: coarsens into blocks over the clip's last frames. */
  mosaicOut?: MosaicRamp | null;
};

const ENTER_EXIT = ['cut', 'blockWipe'] as const;
const PATTERNS: readonly WipePattern[] = ['diagonal', 'random', 'scan'];

export default function AmSnapPlate(props: ElementComponentProps): ReactElement | null {
  const frame = useCurrentFrame();
  const {width, height, fps: compositionFps} = useVideoConfig();
  const params = narrowParams<Params>(props.params);
  const url = elementSource(props.assetEntry, params.src);
  if (!url) {
    return null;
  }

  const fps = finiteNumber(props.fps, compositionFps);
  const stepFrames = integerIn(params.stepFrames, 2, 3, 2);
  const steps = Math.floor(frame / stepFrames);
  const focusX0 = finiteNumber(params.focus?.x, LOGICAL_W / 2);
  const focusY0 = finiteNumber(params.focus?.y, LOGICAL_H / 2);
  const dx = finiteNumber(params.pan?.dx, 0);
  const dy = finiteNumber(params.pan?.dy, 0);

  const timing: PresenterTiming = {
    fps,
    words: spanList(params.words),
    seed: Math.trunc(finiteNumber(params.seed, 7)),
    blinkEvery: 0,
    noBlink: [],
    bob: integerIn(params.bob, 0, 1, 0),
  };
  const view = presenterView(
    {
      zoom: integerIn(params.zoom, 1, 3, 1),
      focus: {x: focusX0 + dx * steps, y: focusY0 + dy * steps},
      punchAt: frameList(params.punchAt),
    },
    timing,
    frame,
  );

  const total = clipFrames(props.clip, fps);
  const enter = oneOf(params.enter, ENTER_EXIT, 'cut');
  const exitWipe = oneOf(params.exitWipe, ENTER_EXIT, 'cut');
  const enterFrames = integerIn(params.enterFrames, 1, 60, 6);
  const exitFrames = integerIn(params.exitFrames, 1, 60, 6);
  const pattern = oneOf<WipePattern>(params.pattern, PATTERNS, 'diagonal');
  const seed = timing.seed;
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
  } else if (exitWipe === 'blockWipe' && frame >= exitStart) {
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

  const block = mosaicBlockAt(frame, total, params.mosaicIn, params.mosaicOut);
  const rect = {x: view.originX, y: view.originY, w: LOGICAL_W * view.scale, h: LOGICAL_H * view.scale};

  return (
    <AbsoluteFill style={{overflow: 'hidden', backgroundColor: background}}>
      {block > 0 ? (
        <MosaicImage url={url} rect={rect} block={block} width={width} height={height} background={background} />
      ) : (
      <Img
        src={url}
        crossOrigin="anonymous"
        style={{
          position: 'absolute',
          left: view.originX,
          top: view.originY,
          width: LOGICAL_W * view.scale,
          height: LOGICAL_H * view.scale,
          maxWidth: 'none',
          maxHeight: 'none',
          display: 'block',
          ...pixelImage,
        }}
      />
      )}
      {tint?.color ? (
        <AbsoluteFill style={{backgroundColor: tint.color, opacity: tintOpacity, pointerEvents: 'none'}} />
      ) : null}
      {wipe}
    </AbsoluteFill>
  );
}
