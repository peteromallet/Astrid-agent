import { useEffect, useState } from 'react';
import type { PanelRenderProps } from '@astrid/editor-sdk';

interface BeatCue {
  readonly id: string;
  readonly label: string;
  readonly seconds: number;
}

const CUES: readonly BeatCue[] = [
  { id: 'opening', label: 'Opening beat', seconds: 0.8 },
  { id: 'title', label: 'Title hit', seconds: 3.2 },
];

export function BeatCuePanel({ instanceId, visible, video }: PanelRenderProps) {
  const [playhead, setPlayhead] = useState<number | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible || !video) return;
    let active = true;
    const refresh = () => {
      const snapshot = video.view.getSnapshot();
      if (active) setPlayhead(snapshot.surfaceMounted ? snapshot.playhead.time : null);
    };
    const subscription = video.view.subscribe(refresh);
    refresh();
    return () => {
      active = false;
      subscription.dispose();
    };
  }, [visible, video]);

  return (
    <section
      aria-label="Beat cues"
      data-panel-instance-id={instanceId}
      hidden={!visible}
      className="flex min-h-0 flex-col gap-3 p-3 text-sm"
    >
      {!video ? (
        <p role="alert">Open this panel in the video editor with a timeline selected.</p>
      ) : playhead === null ? (
        <p role="status">Waiting for the timeline surface to load…</p>
      ) : (
        <>
          <p>Playhead: {playhead.toFixed(2)}s</p>
          {!CUES.length ? (
            <p role="status">No beat cues in this example yet.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {CUES.map((cue) => (
                <li key={cue.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setError('');
                      try {
                        video.seek(cue.seconds);
                      } catch (cause) {
                        setError(cause instanceof Error
                          ? `Could not seek: ${cause.message}`
                          : 'Could not seek. Reopen the timeline and try again.');
                      }
                    }}
                  >
                    Seek to {cue.label} ({cue.seconds.toFixed(1)}s)
                  </button>
                </li>
              ))}
            </ul>
          )}
          {error && <p role="alert">{error}</p>}
        </>
      )}
    </section>
  );
}
