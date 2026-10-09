import {Composition} from 'remotion';
import {FontProvider} from '../../../../../remotion/src/fonts';
import {Preview} from './Preview';

// FontProvider is the real remotion/src/fonts.ts, so previews load the same
// shipped faces (Gelasio, Departure Mono, Inter) as the render.
export const Root = () => (
  <>
    <FontProvider />
    <Composition id="Scene" component={Preview} durationInFrames={90} fps={30} width={1920} height={1080} defaultProps={{layers: [], assets: {}}} />
  </>
);
