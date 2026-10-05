import {useCallback, useRef, useState, type ComponentProps, type ReactElement} from 'react';
import {Img, useRemotionEnvironment} from 'remotion';

type ImageProps = ComponentProps<typeof Img> & {mediaId?: string};

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
  return <PreviewImage key={props.src} {...props} />;
}

function PreviewImage({onError, onImageFrame, mediaId, ...props}: ImageProps): ReactElement {
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const onImageFrameRef = useRef(onImageFrame);
  onImageFrameRef.current = onImageFrame;
  // Img's decode effect depends on this callback. A new callback on each
  // animation frame reassigns src and rearms playback buffering for an already
  // decoded still. Forward the latest consumer without restarting decode.
  const handleImageFrame = useCallback<NonNullable<ImageProps['onImageFrame']>>((image) => {
    setReady(true);
    onImageFrameRef.current?.(image);
  }, []);
  if (failed) {
    return <div role="alert" data-testid="preview-media-error" data-media-state="error" data-media-src={props.src} data-clip-id={mediaId}
      style={{position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center',
        // Terminal recovery controls must sit above later visual/audio Sequence fills.
        zIndex: 30, pointerEvents: 'auto',
        justifyContent: 'center', background: '#321719', color: '#ffe4e6', fontFamily: 'sans-serif', gap: 12}}>
      <span>Image failed to load</span>
      <button type="button" onClick={() => {setReady(false); setFailed(false); setAttempt(value => value + 1);}}>Retry image</button>
    </div>;
  }
  return <>
    <Img {...props} key={attempt} pauseWhenLoading onError={(event) => {
      // Remotion calls this only after its bounded automatic retries. Unmounting
      // is essential: Img's terminal onError does not clear isLoading itself.
      setFailed(true);
      onError?.(event);
    }} onImageFrame={handleImageFrame} />
    {!ready && <div role="status" data-testid="preview-media-loading" data-media-state="loading" data-media-src={props.src} data-clip-id={mediaId}
      style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
        pointerEvents: 'none', background: 'rgba(0, 0, 0, .28)', color: 'white', fontFamily: 'sans-serif'}}>Loading image…</div>}
  </>;
}
