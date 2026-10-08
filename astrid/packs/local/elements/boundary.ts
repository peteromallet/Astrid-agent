import {endSpanningTiming, validSegments, selectedSegmentIndex, positive, nonnegative, type TimingParams} from './effects/end-spanning-layer/timing';
import {sourceSegmentsAtFps, transformAt, validateKeyframes} from './effects/animated-media-transform/motion';

export const VISUAL_SEAM_VERSION = 1 as const;
export const DISCLOSURE_VERSION = 'visual-seam/v1' as const;
export type BoundaryKind = 'activation' | 'source-reset' | 'source-change' | 'rate-change' | 'motion-start' | 'phase-change';
type Json = Record<string, unknown>;
export type BoundaryContext = {
  clip: Json; params?: Json; fps: number; startFrame: number; endFrame: number;
  /** Unclipped renderer origin; visible bounds never reset its local clock. */
  originFrame?: number; path: string[]; source?: string | null; sourceType?: string;
};
export type BoundaryCue = {frame: number; kind: BoundaryKind; id: string; path: string[]};
export type BoundaryDisclosure = {
  version: 1; disclosureVersion: typeof DISCLOSURE_VERSION; effectVersion: 1;
  span: {startFrame: number; endFrame: number; path: string[]}; cues: BoundaryCue[];
  source: Json | null; opaque: string[];
};
const finite = (v: unknown, fallback: number) => typeof v === 'number' && Number.isFinite(v) ? v : fallback;
const sameRect = (a: Json, b: Json) => ['x', 'y', 'width', 'height', 'opacity'].every(k => a[k] === b[k]);
const object = (v: unknown): Json | undefined => v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Json : undefined;
export function transitionFrames(value: unknown, fps: number): number | null {
  const ref = object(value);
  const id = typeof value === 'string' ? value : ref?.id ?? ref?.type;
  if (!['cross-fade', 'crossfade', 'fade'].includes(String(id))) return null;
  const frames = ref?.durationFrames ?? (ref?.duration === undefined ? 8 : finite(ref.duration, -1) * fps);
  return typeof frames === 'number' && Number.isFinite(frames) && frames > 0 ? Math.round(frames) : null;
}

/** Trusted, closed dispatch. A caller cannot install or execute a disclosure callback. */
export function boundaryReport(context: BoundaryContext): BoundaryDisclosure {
  const {clip, fps, startFrame, endFrame, path} = context;
  const params = context.params ?? (clip.params as Json | undefined) ?? {};
  const origin = context.originFrame ?? startFrame;
  const report: BoundaryDisclosure = {version: VISUAL_SEAM_VERSION, disclosureVersion: DISCLOSURE_VERSION, effectVersion: 1,
    span: {startFrame, endFrame, path: [...path]}, cues: [], source: null, opaque: []};
  const cue = (local: number, kind: BoundaryKind, id: string) => {
    const frame = origin + local;
    if (frame >= startFrame && frame < endFrame) report.cues.push({frame, kind, id, path: [...path]});
  };
  const type = clip.clipType ?? (clip.elementRef as Json | undefined)?.id;
  try {
    if (!Number.isFinite(fps) || fps <= 0 || !Number.isInteger(startFrame) || !Number.isInteger(endFrame) || endFrame <= startFrame) throw new Error('invalid visible span');
    if (params.disclosureVersion !== undefined && params.disclosureVersion !== DISCLOSURE_VERSION) report.opaque.push('unsupported disclosure version');
    if (clip.elementRef !== undefined) report.opaque.push('unverified element reference timing');
    // Animation references can be strings, lists, renderer id objects, or the
    // editor's type/duration shape. Unknown timing stays visible as opacity.
    for (const phase of ['entrance', 'exit'] as const) {
      const raw = clip[phase];
      if (raw === undefined) continue;
      for (const entry of Array.isArray(raw) ? raw : [raw]) {
        const ref = object(entry);
        const id = typeof entry === 'string' ? entry : ref?.id ?? ref?.type;
        const defaults: Record<string, number> = {fade: 12, 'fade-up': 18, 'scale-in': 18, 'slide-left': 18, 'slide-up': 12, 'type-on': 120};
        const duration = ref?.durationFrames ?? (ref?.duration === undefined ? defaults[String(id)] : finite(ref.duration, -1) * fps);
        if (duration === undefined || typeof duration !== 'number' || !Number.isFinite(duration) || duration < 0 || !Object.prototype.hasOwnProperty.call(defaults, String(id))) {
          report.opaque.push(`unsupported ${phase} timing`); continue;
        }
        if (duration > 0) cue(phase === 'entrance' ? 1 : Math.max(0, endFrame - origin - Math.round(duration) + 1), 'motion-start', phase);
      }
    }
    if (clip.continuous !== undefined) report.opaque.push('unsupported continuous timing');
    if (clip.transition !== undefined && transitionFrames(clip.transition, fps) === null) report.opaque.push('unsupported transition timing');
    if (type === 'end-spanning-layer') {
      const timing = endSpanningTiming(clip as {at: number}, params as TimingParams, fps);
      const [prep, iteration, anchors] = timing.frames as [number, number, number, number];
      const delay = Math.max(0, finite(params.revealDelaySeconds, 0));
      const fade = Math.max(0, finite(params.revealDurationSeconds, 0));
      const reveal = fade > 0 ? Math.floor(delay * fps) + 1 : Math.ceil(delay * fps);
      cue(reveal, 'activation', 'reveal');
      if (fade > 0) cue(reveal, 'motion-start', 'reveal-opacity');
      for (const [index, frame] of [prep, iteration, anchors].entries()) {
        // These are phase ownership boundaries, distinct from changed geometry.
        if (frame >= reveal) cue(frame, 'phase-change', ['iteration', 'anchors', 'workflow'][index]!);
        if (frame + 1 >= reveal) cue(Math.max(frame + 1, reveal), 'motion-start', ['move-up', 'move-down', 'workflow-move'][index]!);
      }
      const segments = validSegments(params.timelineSegments);
      const index = selectedSegmentIndex(params as TimingParams, segments);
      const selected = segments[index]!;
      report.source = {binding: context.source ?? null, selectedSegmentId: selected.id ?? null, selectedSegmentIndex: index,
        sourceStart: nonnegative(selected.sourceStart) ?? selected.start, sourceEnd: positive(selected.sourceEnd) ?? selected.end,
        speed: positive(selected.speed) ?? 1, prepSourceStart: positive(params.prepSourceStart) ?? 74.08,
        prepSourceEnd: positive(params.prepSourceEnd) ?? 84.3, prepSourceSpeed: positive(params.prepSourceSpeed) ?? 1};
      if (context.source) {
        if (reveal < prep) cue(reveal, 'source-reset', 'prep-source');
        cue(Math.max(anchors + 1, reveal), 'source-reset', 'workflow-source');
      }
    } else if (type === 'animated-media-transform') {
      const keys = validateKeyframes(params.keyframes);
      const moving = keys.slice(1).map((b, i) => !sameRect(keys[i]!, b));
      for (let i = 0; i < moving.length; i++) {
        if (!moving[i]) continue;
        const key = keys[i]!;
        // First frame after the interpolation origin, not the destination key.
        const sample = Math.floor(key.at * fps) + 1;
        if (!sameRect(transformAt(keys, (sample - 1) / fps), transformAt(keys, sample / fps))) {
          if (i === 0 || !moving[i - 1]) cue(sample, 'motion-start', `key-${i}`);
          if (i > 0) cue(Math.ceil(key.at * fps), 'phase-change', `key-${i}`);
        }
      }
      const duration = Math.max(1, Math.round(finite(clip.hold, 1) * fps));
      const segments = context.sourceType?.startsWith('image') ? [] : sourceSegmentsAtFps(params.sourceSegments
        ?? [{at: 0, sourceStart: finite(clip.from, 0), speed: finite(clip.speed, 1)}], fps, duration);
      report.source = {binding: context.source ?? null, segments: segments.map(s => ({fromFrame: s.fromFrame,
        durationInFrames: s.durationInFrames, sourceStartFrame: Math.round(s.sourceStart * fps), speed: s.speed}))};
      for (let i = 1; i < segments.length; i++) {
        const a = segments[i - 1]!; const b = segments[i]!;
        const continued = Math.round(a.sourceStart * fps) + (b.fromFrame - a.fromFrame) * a.speed;
        if (Math.abs(Math.round(b.sourceStart * fps) - continued) > 1e-9) cue(b.fromFrame, 'source-reset', `source-${i}`);
        if (a.speed !== b.speed) cue(b.fromFrame, 'rate-change', `source-${i}`);
      }
    } else if (type === 'media' || type === 'hold' || type === 'video' || type === 'image') {
      // Ordinary timeline keys are a separate adapter; only scalar linear/step
      // interpolation is disclosed here. Other interpolation remains opaque.
      const keys = clip.keyframes as Record<string, {time: number; value: unknown; interpolation?: string}[]> | undefined;
      for (const [property, rows] of Object.entries(keys ?? {})) {
        if (!['x', 'y', 'width', 'height', 'scale', 'rotation', 'opacity', 'translateX', 'translateY'].includes(property)
          || !Array.isArray(rows) || rows.some((k, i) => !Number.isFinite(k.time) || k.time < 0 || (i > 0 && k.time <= rows[i - 1]!.time)
            || typeof k.value !== 'number' || !Number.isFinite(k.value) || (k.interpolation !== undefined && !['linear', 'step', 'hold'].includes(k.interpolation)))) {
          report.opaque.push(`unsupported keyframes: ${property}`); continue;
        }
        for (let i = 1; i < rows.length; i++) {
          const a = rows[i - 1]!; const b = rows[i]!;
          if (a.value === b.value) continue;
          const frame = a.interpolation === 'step' || a.interpolation === 'hold' ? Math.ceil(b.time * fps) : Math.floor(a.time * fps) + 1;
          cue(frame, 'motion-start', `${property}-${i}`);
        }
      }
      report.source = {binding: context.source ?? null};
    } else {
      report.opaque.push('unknown effect timing');
    }
  } catch (error) {
    // A failed source adapter must not erase already known entrance/motion.
    report.opaque.push(`failed disclosure: ${error instanceof Error ? error.message : String(error)}`);
  }
  report.cues.sort((a, b) => a.frame - b.frame || a.kind.localeCompare(b.kind) || a.id.localeCompare(b.id));
  return report;
}
