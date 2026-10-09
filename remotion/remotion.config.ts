import {Config} from '@remotion/cli/config';
import {pcmAacMp4Stitch} from './pcm-aac-stitch';
import {installFrameStreaming, STREAM_FRAMES_ENV} from './stream-frames';
// Pack element aliases (every in-tree pack with an elements root, plus
// ASTRID_PACKS_PATH roots) and the @theme/@workspace aliases are defined once
// in webpack-alias.mjs, next to the smoke bundle that uses the same aliases.
import {applyRemotionPrimitiveAliases} from './webpack-alias.mjs';

// Set only by Astrid's invocation-scoped Three.js MP4 capture path. Ordinary
// Remotion and alpha/ProRes renders retain their existing audio mux behavior.
const pcmAacOutput = process.env.ASTRID_REMOTION_PCM_AAC_OUTPUT;
// Frames are PNG (lossless) for ordinary renders: JPEG turned an 8-colour
// pixel plate into 1,266-2,422 colours (measured on am-snap-plate). The PCM/AAC
// override validates its pre-encode command against `-vcodec mjpeg`, so that
// path keeps JPEG frames.
Config.setVideoImageFormat(pcmAacOutput ? 'jpeg' : 'png');
Config.setOverwriteOutput(true);
Config.setChromiumOpenGlRenderer('swangle');
if (pcmAacOutput) {
  Config.overrideFfmpegCommand(pcmAacMp4Stitch(pcmAacOutput));
}
Config.overrideWebpackConfig(applyRemotionPrimitiveAliases);
// Set by Astrid's opaque H.264 renders: pipe frames into the encoder instead of
// keeping one image per frame in $TMPDIR until the end (see stream-frames.ts).
if (process.env[STREAM_FRAMES_ENV] === '1') {
  installFrameStreaming({rendererEntry: require.resolve('@remotion/renderer')});
}
