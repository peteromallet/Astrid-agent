import type {FfmpegOverrideFn} from '@remotion/renderer';
import path from 'node:path';

/** Keep AAC priming metadata in its final MP4 container, rather than ADTS.
 *
 * Remotion 4.0.509's createAudio() returns audio.wav for pcm-16. Its stitcher
 * normally copies that stream; only this invocation's final command encodes
 * AAC. The private MOV name satisfies Remotion's PCM filename validation;
 * an explicit output format produces the existing MP4 artifact contract.
 */
export const pcmAacMp4Stitch = (expectedOutput: string): FfmpegOverrideFn => {
  if (!expectedOutput.endsWith('.mov')) {
    throw new Error('Astrid PCM/AAC capture requires a private .mov staging name');
  }
  const invocationRoot = path.dirname(path.resolve(expectedOutput));
  const positions = (args: string[], flag: string) => args.flatMap((arg, i) => arg === flag ? [i] : []);
  const once = (args: string[], flag: string, value?: string) => {
    const indices = positions(args, flag);
    return indices.length === 1 && (value === undefined || args[indices[0] + 1] === value);
  };
  const withinInvocation = (filename: string) => {
    const relative = path.relative(invocationRoot, path.resolve(filename));
    return relative !== '' && relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
  };
  // Remotion emits option/value pairs and one final output; reject an extra
  // bare filename rather than accidentally applying options to another output.
  const singleOutput = (args: string[]) => {
    for (let i = 0; i < args.length - 1; i++) {
      if (!args[i].startsWith('-')) return false;
      if (args[i] !== '-y') {
        if (++i >= args.length - 1) return false;
      }
    }
    return true;
  };
  return ({type, args}) => {
    const output = args[args.length - 1];
    if (type === 'pre-stitcher') {
      // PCM selection also changes Remotion's video-only pre-encode filename
      // from .mp4 to .mkv. Keep the original MP4 container/timebase, otherwise
      // Matroska's millisecond timestamps can perturb the copied video clock.
      if (!output?.startsWith(invocationRoot + path.sep)) return args;
      if (
        !withinInvocation(output) ||
        !output.endsWith('/pre-encode.mkv') ||
        !singleOutput(args) ||
        !once(args, '-i', '-') ||
        !once(args, '-f', 'image2pipe') ||
        !once(args, '-r') ||
        !once(args, '-c:v', 'libx264') ||
        !once(args, '-vcodec', 'mjpeg') ||
        !once(args, '-video_track_timescale', '90000') ||
        args.some((arg) => ['-c:a', '-acodec', '-codec:a', '-codec:v', '-an', '-map'].includes(arg))
      ) {
        throw new Error('Unexpected Remotion PCM video pre-encode command');
      }
      return [...args.slice(0, -1), '-f', 'mp4', output];
    }
    if (type !== 'stitcher' || args[args.length - 1] !== expectedOutput) {
      return args;
    }
    const inputs = args.flatMap((arg, index) => arg === '-i' ? [index] : []);
    const audioCodec = args.indexOf('-c:a');
    const videoCodec = args.indexOf('-c:v');
    if (
      !singleOutput(args) ||
      inputs.length !== 2 ||
      !withinInvocation(args[inputs[1] + 1]) ||
      !args[inputs[1] + 1]?.endsWith('/audio.wav') ||
      audioCodec !== inputs[1] + 2 ||
      args[audioCodec + 1] !== 'copy' ||
      !once(args, '-c:a') ||
      !once(args, '-c:v') ||
      videoCodec < 0 ||
      !['copy', 'libx264'].includes(args[videoCodec + 1]) ||
      !once(args, '-movflags', 'faststart') ||
      args.some((arg) => ['-acodec', '-codec:a', '-codec:v', '-ar', '-ac', '-b:a', '-cutoff', '-an'].includes(arg))
    ) {
      throw new Error('Unexpected Remotion PCM/AAC stitch command; refusing an uncorrected MP4');
    }
    return [
      ...args.slice(0, audioCodec),
      '-c:a', 'libfdk_aac', '-b:a', '320k', '-ar', '48000', '-ac', '2', '-cutoff', '18000',
      ...args.slice(audioCodec + 2, -1),
      '-f', 'mp4', expectedOutput,
    ];
  };
};
