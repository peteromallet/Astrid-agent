import {useMemo, type ReactElement} from 'react';
import {Sequence, useCurrentFrame} from 'remotion';
import {Video} from '@remotion/media';
import {type ElementComponentProps, narrowParams} from '../../../../rendering/elements/_shared/contracts';
import {ReadinessImage} from '../../../../rendering/elements/_shared/readiness-image';
import {sourceSegmentsAtFps, transformAt, validateKeyframes} from './motion';

type Params = {keyframes?: unknown; sourceSegments?: unknown; fit?: 'contain' | 'cover' | 'fill'};

/** One managed media source and one timeline clip; motion and source time remain independently editable. */
export default function AnimatedMediaTransform({clip, params: raw, assetEntry, fps}: ElementComponentProps): ReactElement | null {
  const frame = useCurrentFrame();
  const params = narrowParams<Params>(raw);
  const keys = useMemo(() => validateKeyframes(params.keyframes), [params.keyframes]);
  const durationInFrames = Math.max(1, Math.round((clip.hold ?? 1) * fps));
  const entry = assetEntry as {src?: string; file?: string; url?: string; type?: string} | undefined;
  // Browser uses its resolved media URL; worker staging supplies a renderable file.
  const src = entry?.src || entry?.file || entry?.url;
  const isImage = entry?.type?.startsWith('image');
  const segments = useMemo(() => isImage ? [] : sourceSegmentsAtFps(
    params.sourceSegments ?? [{at: 0, sourceStart: clip.from ?? 0, speed: clip.speed ?? 1}], fps, durationInFrames,
  ), [isImage, params.sourceSegments, clip.from, clip.speed, fps, durationInFrames]);
  if (!src) throw new Error('animated-media-transform requires a resolved managed asset');
  const rect = transformAt(keys, frame / fps);
  const style = {display: 'block', width: '100%', height: '100%', objectFit: params.fit ?? 'contain'} as const;
  const volume = typeof clip.volume === 'number' ? Math.max(0, clip.volume) : 1;
  // Keep source sections mounted ahead of their boundary. Remotion freezes a
  // premounted section on its first frame and hides its wrapper, allowing Video
  // to prepare the decoder before it becomes visible. Only one section is active.
  return <div data-animated-media-transform="true" style={{position: 'absolute', left: rect.x, top: rect.y,
    width: rect.width, height: rect.height, opacity: rect.opacity, overflow: 'hidden'}}>
    {isImage ? <ReadinessImage src={src} mediaId={clip.id} style={style} /> : segments.map((segment) => <Sequence key={segment.fromFrame} from={segment.fromFrame}
      durationInFrames={segment.durationInFrames} premountFor={Math.ceil(fps * 2)} postmountFor={Math.ceil(fps / 2)}>
      <Video src={src} trimBefore={Math.round(segment.sourceStart * fps)} playbackRate={segment.speed}
        muted={volume === 0} volume={volume} style={style} />
    </Sequence>)}
  </div>;
}
