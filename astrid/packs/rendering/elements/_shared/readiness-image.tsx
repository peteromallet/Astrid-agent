import {useCallback, useEffect, useRef, useState, type ComponentProps, type ReactElement} from 'react';
import {Img, useRemotionEnvironment} from 'remotion';

type ImageProps = ComponentProps<typeof Img> & {mediaId?: string};
type PreviewPhase = 'pending' | 'ready' | 'error';

// Short waits are common when a still is already warm. Keep those waits
// truthful in the DOM without making a prominent overlay flash on screen.
const LOADING_PRESENTATION_DELAY_MS = 150;

/** A terminal preview failure removes Img, releasing its native loading handles.
 * Rendering uses the strict primitive: a failed asset must fail the output.
 * Native pauseWhenLoading already excludes speculative pre/postmount sequences.
 */
export function ReadinessImage(props: ImageProps): ReactElement {
  const environment = useRemotionEnvironment();
  if (environment.isRendering || environment.isClientSideRendering) {
    const {mediaId: _mediaId, ...imageProps} = props;
    return <Img {...imageProps} pauseWhenLoading />;
  }
  // A changed occurrence can legitimately reuse the same URL. Keying both
  // identities ensures that it cannot inherit readiness or failure state from
  // the previous mounted occurrence.
  return <PreviewImage key={`${props.mediaId ?? ''}\u0000${props.src}`} {...props} />;
}

function PreviewImage({onError, onImageFrame, mediaId, ...props}: ImageProps): ReactElement {
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [showLoading, setShowLoading] = useState(false);
  const phaseRef = useRef<PreviewPhase>('pending');
  const attemptRef = useRef(0);
  const mountedRef = useRef(true);
  const onImageFrameRef = useRef(onImageFrame);
  const onErrorRef = useRef(onError);
  onImageFrameRef.current = onImageFrame;
  onErrorRef.current = onError;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (mountedRef.current && attemptRef.current === attempt && phaseRef.current === 'pending') {
        setShowLoading(true);
      }
    }, LOADING_PRESENTATION_DELAY_MS);
    return () => clearTimeout(timer);
  }, [attempt]);

  // Img's decode effect depends on this callback. A new callback on each
  // animation frame reassigns src and rearms playback buffering for an already
  // decoded still. Forward the latest consumer without restarting decode.
  const handleImageFrame = useCallback<NonNullable<ImageProps['onImageFrame']>>((image) => {
    // The callback may belong to an Img from a superseded retry. It must not
    // settle the current occurrence or forward a stale presentation event.
    if (!mountedRef.current || attemptRef.current !== attempt || phaseRef.current === 'error') {
      return;
    }
    if (phaseRef.current === 'pending') {
      phaseRef.current = 'ready';
      setReady(true);
      setShowLoading(false);
    }
    onImageFrameRef.current?.(image);
  }, [attempt]);

  const handleImageError = useCallback<NonNullable<ImageProps['onError']>>((event) => {
    // Ignore late failures from a replaced source/occurrence or a previous
    // attempt. A usable current image also remains usable after a stale error.
    if (!mountedRef.current || attemptRef.current !== attempt || phaseRef.current !== 'pending') {
      return;
    }
    phaseRef.current = 'error';
    setFailed(true);
    setShowLoading(false);
    onErrorRef.current?.(event);
  }, [attempt]);

  const retry = useCallback(() => {
    const nextAttempt = attemptRef.current + 1;
    attemptRef.current = nextAttempt;
    phaseRef.current = 'pending';
    setFailed(false);
    setReady(false);
    setShowLoading(false);
    setAttempt(nextAttempt);
  }, []);

  if (failed) {
    return <div role="alert" data-testid="preview-media-error" data-media-state="error" data-media-src={props.src} data-clip-id={mediaId}
      style={{position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center',
        // Terminal recovery controls must sit above later visual/audio Sequence fills.
        zIndex: 30, pointerEvents: 'auto',
        justifyContent: 'center', background: '#321719', color: '#ffe4e6', fontFamily: 'sans-serif', gap: 12}}>
      <span>Image failed to load</span>
      <button type="button" onClick={retry}>Retry image</button>
    </div>;
  }
  return <>
    <Img {...props} key={attempt} pauseWhenLoading data-media-state={ready ? 'ready' : 'pending'} data-media-attempt={attempt}
      onError={handleImageError} onImageFrame={handleImageFrame} />
    {!ready && showLoading && <div role="status" data-testid="preview-media-loading" data-media-state="pending" data-media-src={props.src} data-clip-id={mediaId}
      style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
        pointerEvents: 'none', background: 'rgba(0, 0, 0, .28)', color: 'white', fontFamily: 'sans-serif'}}>Loading image…</div>}
  </>;
}
