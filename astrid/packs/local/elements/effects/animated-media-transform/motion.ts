/** Clip-local seconds; dimensions and position use the authored canvas pixels. */
export type TransformKeyframe = {at: number; x: number; y: number; width: number; height: number; opacity: number};
export type SourceSegment = {at: number; sourceStart: number; speed: number};
export type FrameSourceSegment = SourceSegment & {fromFrame: number; durationInFrames: number};
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

export function validateKeyframes(value: unknown): TransformKeyframe[] {
  if (!Array.isArray(value) || value.length === 0) throw new Error('animated-media-transform needs keyframes');
  let previous = -1;
  return value.map((row: TransformKeyframe) => {
    if (!row || !['at', 'x', 'y', 'width', 'height', 'opacity'].every((key) => finite(row[key as keyof TransformKeyframe]))
      || row.at < 0 || row.at <= previous || row.width <= 0 || row.height <= 0 || row.opacity < 0 || row.opacity > 1) {
      throw new Error('Transform keyframes must be finite, ordered, positive-size rectangles with opacity 0–1');
    }
    previous = row.at;
    return {...row};
  });
}

/** Linear interpolation between editable samples; clamp outside the key range. */
export function transformAt(keys: readonly TransformKeyframe[], seconds: number): TransformKeyframe {
  const right = keys.findIndex((key) => key.at > seconds);
  if (right === 0) return keys[0]!;
  if (right < 0) return keys[keys.length - 1]!;
  const a = keys[right - 1]!; const b = keys[right]!;
  const p = (seconds - a.at) / (b.at - a.at);
  return {at: seconds, x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p,
    width: a.width + (b.width - a.width) * p, height: a.height + (b.height - a.height) * p,
    opacity: a.opacity + (b.opacity - a.opacity) * p};
}

/** Shared rounded boundaries partition the clip: no overlapping media elements or gap frames. */
export function sourceSegmentsAtFps(value: unknown, fps: number, durationInFrames: number): FrameSourceSegment[] {
  if (!finite(fps) || fps <= 0 || !Number.isInteger(durationInFrames) || durationInFrames < 1
    || !Array.isArray(value) || value.length === 0) throw new Error('Invalid source playback timing');
  let previous = -1;
  const segments = value.map((row: SourceSegment) => {
    if (!row || !finite(row.at) || !finite(row.sourceStart) || !finite(row.speed)
      || row.at < 0 || row.sourceStart < 0 || row.speed <= 0) throw new Error('Invalid source playback segment');
    const fromFrame = Math.round(row.at * fps);
    if (fromFrame <= previous || fromFrame >= durationInFrames) throw new Error('Source segment boundaries must occupy distinct increasing frames');
    previous = fromFrame;
    return {...row, fromFrame};
  });
  if (segments[0]!.fromFrame !== 0) throw new Error('Source playback must start at clip frame zero');
  return segments.map((segment, index) => ({...segment,
    durationInFrames: (segments[index + 1]?.fromFrame ?? durationInFrames) - segment.fromFrame}));
}

export function sourceSegmentAt(segments: readonly FrameSourceSegment[], frame: number): FrameSourceSegment | undefined {
  return segments.find((segment) => frame >= segment.fromFrame && frame < segment.fromFrame + segment.durationInFrames);
}
