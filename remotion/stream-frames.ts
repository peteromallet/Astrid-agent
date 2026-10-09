import os from 'node:os';
import path from 'node:path';

/** Stream rendered frames into the encoder instead of keeping them on disk.
 *
 * @remotion/renderer 4.0.509 pipes each frame straight into a pre-spawned
 * FFmpeg ("parallel encoding") only when `os.freemem()` exceeds about 1 GB per
 * composition megapixel plus 2 GB (`dist/prestitcher-memory-usage.js`). macOS
 * counts only never-used pages as free (a few hundred MB on a busy Mac), so
 * that check always fails there. Remotion then writes every frame to
 * `$TMPDIR/react-motion-render*` and encodes them all at the end, so scratch
 * grows with duration: about 1 GB of frames for a 2:46 review render.
 *
 * Astrid opts in per render with ASTRID_REMOTION_STREAM_FRAMES=1 and replaces
 * only that one decision. It streams when Remotion's own pre-encoder estimate
 * fits in half of physical memory, which covers 1080p on an 8 GB Mac.
 */
export const STREAM_FRAMES_ENV = 'ASTRID_REMOTION_STREAM_FRAMES';

// Remotion's empirical pre-encoder footprint: about 1 GB per million pixels.
const PRESTITCHER_BYTES_PER_PIXEL = 1000;

export type ParallelEncodingDecision = {
  hasEnoughMemory: boolean;
  freeMemory: number;
  estimatedUsage: number;
};

export const streamingDecision = ({
  width,
  height,
  totalMemory,
}: {
  width: number;
  height: number;
  totalMemory: number;
}): ParallelEncodingDecision => {
  const estimatedUsage = width * height * PRESTITCHER_BYTES_PER_PIXEL;
  return {
    hasEnoughMemory: estimatedUsage <= totalMemory / 2,
    freeMemory: totalMemory,
    estimatedUsage,
  };
};

/** Replace Remotion's parallel-encoding memory gate in this process.
 *
 * `render-media.js` reads `shouldUseParallelEncoding` from this module's
 * exports at call time, so assigning the export takes effect for every
 * render. Fails loudly when a Remotion upgrade moves the gate, rather than
 * silently falling back to writing every frame to disk.
 */
export const installFrameStreaming = ({
  rendererEntry,
  totalMemory = os.totalmem(),
  load = require,
}: {
  rendererEntry: string;
  totalMemory?: number;
  load?: (id: string) => unknown;
}): void => {
  const target = path.join(path.dirname(rendererEntry), 'prestitcher-memory-usage.js');
  const gate = load(target) as {shouldUseParallelEncoding?: unknown};
  if (typeof gate?.shouldUseParallelEncoding !== 'function') {
    throw new Error(
      `Astrid frame streaming: ${target} does not export shouldUseParallelEncoding. ` +
        'Update remotion/stream-frames.ts for this Remotion version.',
    );
  }
  gate.shouldUseParallelEncoding = ({width, height}: {width: number; height: number}) =>
    streamingDecision({width, height, totalMemory});
};
