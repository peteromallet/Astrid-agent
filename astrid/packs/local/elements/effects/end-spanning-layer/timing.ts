/** Pure renderer timing. No React, media loading, or submitted code execution. */
export type TimelineSegment = {id?: string; start: number; end: number; sourceStart?: number; sourceEnd?: number; speed?: number; label: string; title: string};
export type TimingParams = {
  phaseDurations?: {prep?: number; iteration?: number; anchors?: number; workflow?: number};
  prepSeconds?: number; iterationSeconds?: number; anchorsSeconds?: number; workflowSeconds?: number;
  selectedSegmentId?: string; selectedSegmentIndex?: number; timelineSegments?: TimelineSegment[];
};
export const positive = (v: unknown): number | null => typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null;
export const nonnegative = (v: unknown): number | null => typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null;
const DEFAULT_SEGMENTS: TimelineSegment[] = [
  {id: 'astrid', start: 0, end: 5, label: '00', title: 'ASTRID — through the glasses'},
  {id: 'choice', start: 5, end: 8, sourceStart: 74.9347, sourceEnd: 76.4, speed: 0.4884, label: '01', title: 'Two options'},
  {id: 'blue', start: 8, end: 19, sourceStart: 79.12, sourceEnd: 80.87, label: '02', title: 'Blue pill — manual tools'},
  {id: 'red', start: 19, end: 22, label: '03', title: 'Red pill'},
  {id: 'creature', start: 22, end: 26.9167, label: '04', title: 'Creature reveal — glasses edit'},
  {id: 'reflection', start: 26.9167, end: 43.4927, label: '05', title: 'Into the minkhole — reflection close-up'},
];
export function phaseValues(params: TimingParams, clipSeconds: number): [number, number, number, number] {
  const configured = params.phaseDurations ?? {};
  const values = [positive(params.prepSeconds) ?? positive(configured.prep), positive(params.iterationSeconds) ?? positive(configured.iteration),
    positive(params.anchorsSeconds) ?? positive(configured.anchors), positive(params.workflowSeconds) ?? positive(configured.workflow)];
  return values.every((v): v is number => v !== null) ? values as [number, number, number, number]
    : [0.24, 0.22, 0.2, 0.34].map(w => clipSeconds * w) as [number, number, number, number];
}
export function validSegments(value: unknown): TimelineSegment[] {
  if (!Array.isArray(value)) return DEFAULT_SEGMENTS;
  const result = value.filter((s): s is TimelineSegment => !!s && typeof s === 'object'
    && typeof s.start === 'number' && Number.isFinite(s.start) && typeof s.end === 'number' && Number.isFinite(s.end) && s.end > s.start
    && typeof s.label === 'string' && s.label.length > 0 && typeof s.title === 'string' && s.title.length > 0);
  return result.length ? result : DEFAULT_SEGMENTS;
}
export function selectedSegmentIndex(params: TimingParams, segments: TimelineSegment[]): number {
  const requested = typeof params.selectedSegmentIndex === 'number' && Number.isFinite(params.selectedSegmentIndex) && params.selectedSegmentIndex >= 0 ? params.selectedSegmentIndex : null;
  return typeof params.selectedSegmentId === 'string' ? Math.max(0, segments.findIndex(s => s.id === params.selectedSegmentId))
    : requested === null ? Math.min(segments.length - 1, 2) : Math.min(segments.length - 1, Math.floor(requested));
}
export function endSpanningTiming(clip: {hold?: number; to?: number; at: number}, params: TimingParams, fps: number) {
  const clipSeconds = positive(clip.hold) ?? positive((clip.to ?? 0) - clip.at) ?? 30;
  const seconds = phaseValues(params, clipSeconds);
  let cumulative = 0;
  const frames = seconds.map(s => Math.round((cumulative += s) * fps));
  return {clipSeconds, seconds, frames, effectEndFrame: Math.max(frames[3]!, Math.round(clipSeconds * fps))};
}
