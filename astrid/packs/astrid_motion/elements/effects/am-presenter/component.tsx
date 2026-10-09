import type {CSSProperties, ReactElement} from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {
  type ElementComponentProps,
  narrowParams,
} from '../../../../rendering/elements/_shared/contracts';
import {
  COLOR,
  FAMILY,
  LOGICAL_H,
  LOGICAL_W,
  clamp,
  clipFrames,
  finiteNumber,
  integerIn,
} from '../../_shared/am';
import {mosaicBlockAt, type MosaicRamp} from '../../_shared/mosaic';
import {
  type Anchor,
  type MouthState,
  type PresenterTiming,
  type PresenterView,
  type Point,
  type Span,
  blinkClosedAt,
  mouthStateAt,
  presenterView,
} from './presenter-core';

// am-presenter: the Remotion-animated stand-in for the speaker. It draws ONLY
// the pixel overlay (mouth, eyes, blinks, bob) plus crisp UI chrome (label,
// REC and timecode, crop marks, status chip). The plate is a separate
// am-snap-plate clip on the track below. It must use the SAME zoom, focus,
// punchAt, words, seed, blinkEvery, noBlink and bob, so presenterView() puts
// both layers on the same pixels.
//
// Overlay pixels are logical px (1 = 6 screen px at zoom 1). Each rect is an
// absolutely positioned div on the view grid, so edges never blur.

type ChipSpec = {text: string; swapTo?: string; swapAt?: number};

type Params = {
  zoom?: number;
  focus?: Point;
  mouth?: Anchor;
  skin?: string;
  lip?: string;
  inner?: string;
  edge?: string;
  words?: Span[];
  eyes?: Anchor[];
  blinkEvery?: number;
  noBlink?: Span[];
  seed?: number;
  bob?: number;
  punchAt?: number[];
  label?: string;
  timecodeStart?: string;
  chip?: ChipSpec | null;
  /** full: crop marks, REC and label (default). label: the label chip only. none: no chrome. */
  chrome?: 'full' | 'label' | 'none';
  /** Slow continuous push {to, frames, at?}; give the plate the same value. */
  push?: {to: number; frames: number; at?: number} | null;
  /** Same ramps as the plate's: the face overlay hides while the plate is a mosaic. */
  mosaicIn?: MosaicRamp | null;
  mosaicOut?: MosaicRamp | null;
};

type Resolved = {
  zoom: number;
  focus: Point;
  mouth: Anchor;
  skin: string;
  lip: string;
  inner: string;
  edge: string;
  words: Span[];
  eyes: Anchor[];
  blinkEvery: number;
  noBlink: Span[];
  seed: number;
  bob: number;
  punchAt: number[];
  label: string;
  timecodeStart: string;
  chip: ChipSpec | null;
  chrome: 'full' | 'label' | 'none';
};

// Defaults match the synthetic preview plate (320x180). Replace mouth/eyes with
// the anchors measured on the real P-01 plate.
const DEFAULT_MOUTH: Anchor = {x: 151, y: 104, w: 19};
const DEFAULT_EYES: Anchor[] = [
  {x: 146, y: 85, w: 3},
  {x: 171, y: 85, w: 3},
];

const hexOr = (value: unknown, fallback: string): string =>
  typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value.trim()) ? value.trim() : fallback;

const anchorOr = (value: unknown, fallback: Anchor): Anchor => {
  if (!value || typeof value !== 'object') return fallback;
  const a = value as Record<string, unknown>;
  return {
    x: integerIn(a.x, 0, LOGICAL_W - 1, fallback.x),
    y: integerIn(a.y, 0, LOGICAL_H - 1, fallback.y),
    w: integerIn(a.w, 1, 40, fallback.w),
  };
};

const spansOf = (value: unknown): Span[] => {
  if (!Array.isArray(value)) return [];
  const spans: Span[] = [];
  for (const item of value) {
    if (!Array.isArray(item) || item.length < 2) continue;
    const start = finiteNumber(item[0], Number.NaN);
    const end = finiteNumber(item[1], Number.NaN);
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) spans.push([start, end]);
  }
  return spans.sort((a, b) => a[0] - b[0]);
};

const readParams = (raw: unknown): Resolved => {
  const p = narrowParams<Params>(raw);
  const chipRaw = p.chip && typeof p.chip === 'object' ? p.chip : null;
  return {
    zoom: integerIn(p.zoom, 1, 3, 1),
    focus: {
      x: clamp(finiteNumber(p.focus?.x, 160), 0, LOGICAL_W),
      y: clamp(finiteNumber(p.focus?.y, 90), 0, LOGICAL_H),
    },
    mouth: anchorOr(p.mouth, DEFAULT_MOUTH),
    skin: hexOr(p.skin, '#EDB98E'),
    lip: hexOr(p.lip, '#2B1A14'),
    inner: hexOr(p.inner, '#7A3510'),
    edge: hexOr(p.edge, COLOR.ink),
    words: spansOf(p.words),
    eyes: Array.isArray(p.eyes) ? p.eyes.map((e) => anchorOr(e, {x: 0, y: 0, w: 3})) : DEFAULT_EYES,
    blinkEvery: finiteNumber(p.blinkEvery, 3.4),
    noBlink: spansOf(p.noBlink),
    seed: Math.trunc(finiteNumber(p.seed, 7)),
    bob: integerIn(p.bob, 0, 1, 0),
    punchAt: Array.isArray(p.punchAt)
      ? p.punchAt.filter((n): n is number => typeof n === 'number' && Number.isFinite(n) && n >= 0)
      : [],
    label: typeof p.label === 'string' ? p.label : 'PLACEHOLDER · POM ON CAMERA · TAKE 03',
    timecodeStart: typeof p.timecodeStart === 'string' ? p.timecodeStart : '01:02:14:00',
    chrome: p.chrome === 'label' || p.chrome === 'none' ? p.chrome : 'full',
    chip: chipRaw && typeof chipRaw.text === 'string'
      ? {
          text: chipRaw.text,
          swapTo: typeof chipRaw.swapTo === 'string' ? chipRaw.swapTo : undefined,
          swapAt: typeof chipRaw.swapAt === 'number' ? chipRaw.swapAt : undefined,
        }
      : null,
  };
};

type Px = {x: number; y: number; w: number; h: number; color: string};

const pxStyle = (r: Px, v: PresenterView): CSSProperties => ({
  position: 'absolute',
  left: v.originX + r.x * v.scale,
  top: v.originY + r.y * v.scale,
  width: r.w * v.scale,
  height: r.h * v.scale,
  background: r.color,
});

// Mouth pixels. Closed draws nothing: the plate's own closed line shows.
// Half and open first erase the plate's line with skin, then draw the opening.
const mouthPixels = (state: MouthState, m: Anchor, skin: string, lip: string, inner: string): Px[] => {
  if (state === 'closed') return [];
  const erase: Px = {x: m.x - 1, y: m.y - 1, w: m.w + 2, h: 4, color: skin};
  if (state === 'half') {
    return [erase, {x: m.x + 2, y: m.y, w: m.w - 4, h: 1, color: lip}];
  }
  return [
    erase,
    {x: m.x + 2, y: m.y, w: m.w - 4, h: 2, color: lip},
    {x: m.x + 3, y: m.y + 1, w: m.w - 6, h: 1, color: inner},
  ];
};

// Blink: skin fill over the open eye, plus a lid line through its middle.
const blinkPixels = (eyes: Anchor[], skin: string, lip: string): Px[] =>
  eyes.flatMap((e) => [
    {x: e.x - 1, y: e.y - 1, w: e.w + 2, h: 4, color: skin},
    {x: e.x, y: e.y + 1, w: e.w, h: 1, color: lip},
  ]);

// ---- UI chrome (crisp, not pixelated) --------------------------------------

const PANEL = COLOR.panel;
const INK = COLOR.ink;
const REC_RED = '#E5382B';
const CROP_ARM = 40;
const CROP_INSET = 48;
const CROP_THICK = 2;

const CropMarks = (): ReactElement => {
  const corners: Array<[number, number, number, number]> = [
    [CROP_INSET, CROP_INSET, 1, 1],
    [1920 - CROP_INSET, CROP_INSET, -1, 1],
    [CROP_INSET, 1080 - CROP_INSET, 1, -1],
    [1920 - CROP_INSET, 1080 - CROP_INSET, -1, -1],
  ];
  return (
    <>
      {corners.map(([cx, cy, dx, dy]) => (
        <div key={`${cx}-${cy}`} style={{position: 'absolute', background: PANEL, pointerEvents: 'none'}}>
          <div
            style={{
              position: 'absolute',
              left: dx > 0 ? cx : cx - CROP_ARM,
              top: dy > 0 ? cy : cy - CROP_THICK,
              width: CROP_ARM,
              height: CROP_THICK,
              background: PANEL,
            }}
          />
          <div
            style={{
              position: 'absolute',
              left: dx > 0 ? cx : cx - CROP_THICK,
              top: dy > 0 ? cy : cy - CROP_ARM,
              width: CROP_THICK,
              height: CROP_ARM,
              background: PANEL,
            }}
          />
        </div>
      ))}
    </>
  );
};

const TC_RE = /^(\d{1,2}):(\d{2}):(\d{2}):(\d{2})$/;
const parseTimecode = (value: string, fps: number): number => {
  const m = TC_RE.exec(value.trim());
  if (!m) return 0;
  return ((Number(m[1]) * 60 + Number(m[2])) * 60 + Number(m[3])) * fps + Number(m[4]);
};
const formatTimecode = (frames: number, fps: number): string => {
  const f = frames % fps;
  const totalSeconds = Math.floor(frames / fps);
  const pad = (n: number): string => String(n).padStart(2, '0');
  return [
    pad(Math.floor(totalSeconds / 3600) % 100),
    pad(Math.floor(totalSeconds / 60) % 60),
    pad(totalSeconds % 60),
    pad(f),
  ].join(':');
};

const monoChip: CSSProperties = {
  position: 'absolute',
  fontFamily: `'${FAMILY.label}', monospace`,
  fontSize: 22,
  lineHeight: '22px',
  letterSpacing: '0.12em',
  textTransform: 'uppercase',
  whiteSpace: 'nowrap',
  color: INK,
  background: PANEL,
  border: `1px solid ${INK}`,
  padding: '10px 16px',
};

export default function AmPresenter(props: ElementComponentProps): ReactElement | null {
  const frame = useCurrentFrame();
  const config = useVideoConfig();
  const fps = finiteNumber(props.fps, config.fps);
  const p = readParams(props.params);

  const timing: PresenterTiming = {
    fps,
    words: p.words,
    seed: p.seed,
    blinkEvery: p.blinkEvery,
    noBlink: p.noBlink,
    bob: p.bob,
  };
  const rawPush = narrowParams<Params>(props.params).push;
  const view = presenterView(
    {zoom: p.zoom, focus: p.focus, punchAt: p.punchAt, push: rawPush && typeof rawPush === 'object' ? rawPush : null},
    timing,
    frame,
  );

  // While the plate under this overlay is a mosaic, the crisp face pixels would
  // float over blocks, so they hide for those frames.
  const raw = narrowParams<Params>(props.params);
  const mosaic = mosaicBlockAt(frame, clipFrames(props.clip, fps), raw.mosaicIn, raw.mosaicOut) > 6;
  const pixels: Px[] = mosaic
    ? []
    : [
        ...mouthPixels(mouthStateAt(timing, frame), p.mouth, p.skin, p.lip, p.inner),
        ...(blinkClosedAt(timing, frame) ? blinkPixels(p.eyes, p.skin, p.lip) : []),
      ];

  // A bob pushes the plate down one logical px, uncovering a strip at the top
  // edge (only when the view is already at the plate's top edge). Paint it.
  const bobStrip: CSSProperties | null = view.originY > 0
    ? {position: 'absolute', left: 0, top: 0, width: 1920, height: view.originY, background: p.edge}
    : null;

  const chip = p.chip;
  const chipText = chip && chip.swapAt !== undefined && frame >= chip.swapAt ? (chip.swapTo ?? chip.text) : chip?.text;
  const chipFlash = chip !== null && chip.swapAt !== undefined && frame >= chip.swapAt && frame < chip.swapAt + 2;
  const recOn = Math.floor(frame / Math.max(1, Math.round(fps / 2))) % 2 === 0;
  const timecode = formatTimecode(parseTimecode(p.timecodeStart, fps) + frame, fps);

  return (
    <div style={{position: 'absolute', inset: 0, overflow: 'hidden', pointerEvents: 'none'}}>
      {bobStrip ? <div style={bobStrip} /> : null}
      {pixels.map((r, i) => (
        <div key={i} style={pxStyle(r, view)} />
      ))}

      {p.chrome === 'full' ? <CropMarks /> : null}

      {p.chrome === 'full' ? (
        <div style={{position: 'absolute', left: 104, top: 40, display: 'flex', alignItems: 'center', gap: 14,
          fontFamily: `'${FAMILY.label}', monospace`, fontSize: 22, lineHeight: '22px', color: PANEL,
          textShadow: `2px 2px 0 ${INK}`, whiteSpace: 'nowrap'}}>
          <div style={{width: 12, height: 12, background: recOn ? REC_RED : 'transparent'}} />
          <span>REC</span>
          <span>{timecode}</span>
        </div>
      ) : null}

      {p.chrome === 'none' || !p.label ? null : p.chrome === 'label' ? (
        <div style={{...monoChip, left: 48, bottom: 36, fontSize: 16, lineHeight: '16px', padding: '6px 10px',
          letterSpacing: '0.1em', opacity: 0.9}}>{p.label}</div>
      ) : (
        <div style={{...monoChip, left: 104, bottom: 44}}>{p.label}</div>
      )}

      {chip && chipText !== undefined ? (
        <div
          style={{
            ...monoChip,
            right: 104,
            top: 40,
            background: chipFlash ? COLOR.orange : PANEL,
            color: chipFlash ? PANEL : INK,
          }}
        >
          {chipText}
        </div>
      ) : null}
    </div>
  );
}
