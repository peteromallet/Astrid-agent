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
    for (const key of FIELDS) if (clip[key] !== undefined) metadata[key] = clean(clip[key]);
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
