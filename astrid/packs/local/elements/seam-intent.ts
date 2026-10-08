import {DISCLOSURE_VERSION, type BoundaryCue} from './boundary';

type Json = Record<string, unknown>;
export type IntentOwner = {
  path: string[]; startFrame: number; endFrame: number; originFrame: number;
  clip: Json; source: string | null;
};
export type SeamIntent = {
  contextVersion: typeof DISCLOSURE_VERSION;
  frame: number; kind: 'hard-cut' | 'transition' | 'synchronized';
  participants: string[]; context: Json;
};

/** A pause grants exactly one picture-lane interval, never adjacent gaps. */
export type VisualPause = {kind: 'pause'; track: string; startFrame: number; endFrame: number};
export function pauseCovers(gaps: unknown, track: string, startFrame: number, endFrame: number): boolean {
  return endFrame > startFrame && Array.isArray(gaps) && gaps.some(p => p && p.kind === 'pause'
    && p.track === track && p.startFrame === startFrame && p.endFrame === endFrame
    && Number.isInteger(p.startFrame) && Number.isInteger(p.endFrame));
}

/** Actual boundary owners plus nearby cue contributors; spanning pictures
 * without cues are unrelated to this acknowledgement. */
export function relevantIntentOwners(owners: readonly IntentOwner[], boundaryOwners: readonly IntentOwner[], cues: readonly BoundaryCue[]): IntentOwner[] {
  const paths = new Set([...boundaryOwners.map(o => JSON.stringify(o.path)), ...cues.map(c => JSON.stringify(c.path))]);
  return owners.filter(o => paths.has(JSON.stringify(o.path)));
}

/** Auxiliary visibility starts at the owner span even when its clock is opaque.
 * Primary picture ownership is already represented by the editorial cut. */
export function opaqueActivationCues(owners: readonly (IntentOwner & {primary: boolean; disclosure: {opaque: readonly string[]}})[], frames: ReadonlySet<number>): BoundaryCue[] {
  return owners.filter(o => !o.primary && o.disclosure.opaque.length > 0
    && [-2, -1, 0, 1, 2].some(delta => frames.has(o.startFrame + delta)))
    .map(o => ({frame: o.startFrame, kind: 'activation', id: 'owner-activation', path: o.path}));
}

/** Owner IDs are local to the admitted timeline; transport/project prefixes
 * and flattened occurrence clip IDs do not change their identity. */
export function intentPath(path: readonly string[]): string[] {
  return path[0] === 'parent' ? path.slice(2)
    : path[0] === 'track' ? path.slice(2) : [...path];
}
export function cueIdentity(cue: BoundaryCue): string {
  return JSON.stringify([intentPath(cue.path), cue.kind, cue.id, cue.frame]);
}
function clean(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(clean);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .filter(([key, v]) => v !== undefined && !['report', 'intents', 'visualSeamIntents', 'visual_seam_report'].includes(key))
    .map(([key, v]) => [key, clean(v)]));
  return value;
}
const FIELDS = ['params', 'keyframes', 'entrance', 'exit', 'continuous', 'transition', 'effects', 'elementRef',
  'x', 'y', 'width', 'height', 'opacity', 'scale', 'rotation', 'translateX', 'translateY',
  'cropTop', 'cropBottom', 'cropLeft', 'cropRight'] as const;
/** JSON context deliberately avoids language-specific JSON number hashing.
 * Runtime compares the recomputed object, never trusts submitted witnesses. */
export function intentContext(fps: number, frame: number, owners: readonly IntentOwner[]): Json {
  const byPath = new Map<string, Json>();
  for (const owner of owners) {
    const clip = owner.clip;
    const path = intentPath(owner.path);
    const metadata: Json = {clipType: clip.clipType ?? clip.clip_type ?? 'media', track: clip.track ?? 'video',
      from: clip.from ?? 0, speed: clip.speed ?? 1};
    for (const key of FIELDS) {
      const value = clip[key];
      // Projection can attach an empty local-effect container when only
      // timeline effects were authored. It is the same optional data as absence.
      if (value === undefined || key === 'effects' && value !== null && typeof value === 'object'
        && Object.keys(value).length === 0) continue;
      metadata[key] = clean(value);
    }
    byPath.set(JSON.stringify(path), {path, span: [owner.startFrame, owner.endFrame, owner.originFrame],
      source: owner.source, clip: metadata});
  }
  return {version: DISCLOSURE_VERSION, fps, frame, owners: [...byPath.entries()]
    .sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([, owner]) => owner)};
}
function ordered(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(ordered);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([k, v]) => [k, ordered(v)]));
  return value;
}
export function sameContext(a: unknown, b: unknown): boolean {
  return JSON.stringify(ordered(a)) === JSON.stringify(ordered(b));
}
