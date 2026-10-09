const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const {EventEmitter} = require('node:events');
const {test} = require('node:test');
const ts = require('typescript');

const loadSource = (filename, source, overrides = {}) => {
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  const requireDefault = loaded.require.bind(loaded);
  loaded.require = (name) => name in overrides ? overrides[name] : requireDefault(name);
  loaded._compile(source, filename);
  return loaded.exports;
};
// Compile only Astrid-owned TS; dependency JS is read and exercised through
// local require overrides. No renderer or FFmpeg process is started.
const loadTs = (filename, overrides = {}) => loadSource(filename, ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true},
}).outputText, overrides);
const project = path.resolve(__dirname, '..');
const {pcmAacMp4Stitch} = loadTs(path.join(project, 'pcm-aac-stitch.ts'));
const output = '/attempt/.remotion-runtime-test/capture.mov';
const audio = '/attempt/.remotion-runtime-test/remotion-audio/audio.wav';
const preVideo = '/attempt/.remotion-runtime-test/remotion-render-test/pre-encode.mkv';
const rewrite = pcmAacMp4Stitch(output);
const audioEncoding = ['-c:a', 'libfdk_aac', '-b:a', '320k', '-ar', '48000', '-ac', '2', '-cutoff', '18000'];
const metadata = ['-movflags', 'faststart', '-map_metadata', '-1', '-metadata', 'comment=unchanged', '-y'];

test('installed Remotion selects MKV for the PCM intermediate and accepts the private H264/MOV CLI name', () => {
  const dist = path.join(project, 'node_modules/@remotion/renderer/dist');
  const {getFileExtensionFromCodec} = require(path.join(dist, 'get-extension-from-codec.js'));
  const {validateOutputFilename} = require(path.join(dist, 'validate-output-filename.js'));
  const {videoCodecOption} = require(path.join(dist, 'options/video-codec.js'));
  assert.equal(getFileExtensionFromCodec('h264', 'aac'), 'mp4');
  assert.equal(getFileExtensionFromCodec('h264', 'pcm-16'), 'mkv');
  assert.throws(() => validateOutputFilename({codec: 'h264', audioCodecSetting: 'pcm-16', extension: 'mp4', preferLossless: false, separateAudioTo: null}), /output filename/);
  assert.doesNotThrow(() => validateOutputFilename({codec: 'h264', audioCodecSetting: 'pcm-16', extension: 'mov', preferLossless: false, separateAudioTo: null}));
  assert.equal(videoCodecOption.getValue({commandLine: {codec: 'h264'}}, {outName: output, downloadName: null, compositionCodec: null, configFile: null, uiCodec: null}).value, 'h264');
});

test('final stitch encodes the PCM mix exactly once and retains copied H264/timing/metadata', () => {
  const video = ['-c:v', 'copy', '-colorspace:v', 'bt709', '-color_primaries:v', 'bt709', '-color_trc:v', 'bt709', '-color_range', 'tv'];
  const args = ['-i', preVideo, '-i', audio, '-c:a', 'copy', ...video, ...metadata, output];
  const original = [...args];
  assert.deepEqual(rewrite({type: 'stitcher', args}), ['-i', preVideo, '-i', audio, ...audioEncoding, ...video, ...metadata, '-f', 'mp4', output]);
  assert.deepEqual(args, original);
});

test('image-sequence stitch preserves authored frame clock and H264 encoder settings', () => {
  const inputs = ['-r', '30', '-f', 'image2', '-s', '320x180', '-start_number', '12', '-i', '/frames/frame%07d.jpeg', '-i', audio];
  const video = ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-video_track_timescale', '90000', '-crf', '18', '-maxrate', '15552k', '-bufsize', '31104k'];
  const args = [...inputs, '-c:a', 'copy', ...video, ...metadata, output];
  assert.deepEqual(rewrite({type: 'stitcher', args}), [...inputs, ...audioEncoding, ...video, ...metadata, '-f', 'mp4', output]);
});

for (const fps of ['30', '30000/1001']) {
  test(`video-only pre-encoding retains input fps ${fps}, MP4 container and 90000 clock`, () => {
    const args = ['-r', fps, '-f', 'image2pipe', '-s', '320x180', '-vcodec', 'mjpeg', '-i', '-', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-video_track_timescale', '90000', '-crf', '18', '-y', preVideo];
    const original = [...args];
    assert.deepEqual(rewrite({type: 'pre-stitcher', args}), [...args.slice(0, -1), '-f', 'mp4', preVideo]);
    assert.deepEqual(args, original);
  });
}

for (const fps of [30, 30000 / 1001]) {
  test(`installed pre-stitcher builder at fps ${fps} changes exactly the output muxer pair`, () => {
    const dist = path.join(project, 'node_modules/@remotion/renderer/dist');
    const {getFileExtensionFromCodec} = require(path.join(dist, 'get-extension-from-codec.js'));
    const intermediate = path.join(path.dirname(preVideo), `pre-encode.${getFileExtensionFromCodec('h264', 'pcm-16')}`);
    const seen = [];
    const filename = path.join(dist, 'prespawn-ffmpeg.js');
    const {prespawnFfmpeg} = loadSource(filename, fs.readFileSync(filename, 'utf8'), {
      './call-ffmpeg': {callFf: ({args}) => {
        seen.push([...args]);
        const task = new EventEmitter();
        task.stderr = new EventEmitter();
        return task;
      }},
      './probe-encoder': {resolveHardwareAcceleration: () => 'disable'},
    });
    const options = {
      fps, width: 320, height: 180, codec: 'h264', imageFormat: 'jpeg', pixelFormat: 'yuv420p',
      proResProfile: undefined, x264Preset: 'medium', gopSize: null, crf: 18,
      videoBitrate: null, encodingMaxRate: '15552K', encodingBufferSize: '31104K',
      colorSpace: 'bt709', hardwareAcceleration: 'disable', indent: false,
      logLevel: 'error', binariesDirectory: null, outputLocation: intermediate,
      signal: undefined, ffmpegOverride: null,
    };
    prespawnFfmpeg(options);
    const source = seen[0];
    prespawnFfmpeg({...options, ffmpegOverride: rewrite});
    assert.equal(seen.length, 2);
    assert.deepEqual(seen[1], [...source.slice(0, -1), '-f', 'mp4', intermediate]);
    assert.equal(source[source.indexOf('-r') + 1], fps);
    assert.equal(source[source.indexOf('-f') + 1], 'image2pipe');
    assert.equal(source[source.indexOf('-video_track_timescale') + 1], '90000');
    const final = ['-i', intermediate, '-i', audio, '-c:a', 'copy', '-c:v', 'copy', ...metadata, output];
    const stitched = rewrite({type: 'stitcher', args: final});
    assert.deepEqual(stitched.slice(0, 4), ['-i', intermediate, '-i', audio]);
    assert.equal(stitched[stitched.indexOf('-c:v') + 1], 'copy');
  });
}

test('malformed owned pre-encode commands fail closed, including escapes, duplicate options and extra outputs', () => {
  const valid = ['-r', '30', '-f', 'image2pipe', '-s', '320x180', '-vcodec', 'mjpeg', '-i', '-', '-c:v', 'libx264', '-video_track_timescale', '90000', '-y', preVideo];
  const insert = (extra) => [...valid.slice(0, -1), ...extra, preVideo];
  for (const args of [
    valid.map((arg) => arg === '90000' ? '1000' : arg),
    valid.filter((arg) => !['-video_track_timescale', '90000'].includes(arg)),
    valid.map((arg) => arg === 'libx264' ? 'prores_ks' : arg),
    [...valid.slice(0, -1), path.dirname(output) + '/../../outside/pre-encode.mkv'],
    [...valid.slice(0, -1), path.dirname(output) + '/unexpected.mkv'],
    insert(['-video_track_timescale', '90000']),
    insert(['-c:v', 'libx264']),
    insert(['-i', audio]),
    insert(['-c:a', 'copy']),
    insert(['-codec:v', 'libx264']),
    insert(['/attempt/extra.mp4']),
  ]) assert.throws(() => rewrite({type: 'pre-stitcher', args}), /Unexpected Remotion/);
  const sibling = [...valid.slice(0, -1), path.dirname(output) + '-sibling/pre-encode.mkv'];
  assert.equal(rewrite({type: 'pre-stitcher', args: sibling}), sibling);
});

test('unrelated invocations, alpha/ProRes and unscoped pre-stitcher commands remain identical', () => {
  for (const type of ['stitcher', 'pre-stitcher']) {
    for (const other of ['/other/capture.mov', '/other/pre-encode.mkv', '/other/alpha.mov', '/other/output.mp4']) {
      const args = ['-i', '/other/audio.aac', '-c:a', 'copy', '-c:v', 'prores_ks', other];
      assert.equal(rewrite({type, args}), args);
    }
  }
});

test('a drifted command in the selected invocation fails before producing an uncorrected artifact', () => {
  const valid = ['-i', preVideo, '-i', audio, '-c:a', 'copy', '-c:v', 'copy', ...metadata, output];
  for (const args of [
    valid.map((arg) => arg === audio ? audio.replace('.wav', '.aac') : arg),
    valid.map((arg) => arg === 'faststart' ? 'empty_moov' : arg),
    ['-i', preVideo, '-an', '-c:v', 'copy', output],
    [...valid.slice(0, -1), '-c:a', 'copy', output],
    ['-i', preVideo, '-i', audio, '-c:a', 'copy', '-c:v', 'prores_ks', ...metadata, output],
  ]) assert.throws(() => rewrite({type: 'stitcher', args}), /Unexpected Remotion/);
  assert.throws(() => rewrite({type: 'pre-stitcher', args: ['-c:v', 'prores_ks', preVideo]}), /Unexpected Remotion/);
  assert.throws(() => pcmAacMp4Stitch('/attempt/output.mp4'), /private .mov/);
});

test('Astrid config registers the hook only for an explicitly scoped capture output', () => {
  const prior = process.env.ASTRID_REMOTION_PCM_AAC_OUTPUT;
  try {
    for (const selected of [undefined, output]) {
      if (selected) process.env.ASTRID_REMOTION_PCM_AAC_OUTPUT = selected;
      else delete process.env.ASTRID_REMOTION_PCM_AAC_OUTPUT;
      const hooks = [];
      const Config = new Proxy({}, {get: (_, name) => (...args) => {if (name === 'overrideFfmpegCommand') hooks.push(args[0]);}});
      const streamFrames = {STREAM_FRAMES_ENV: 'ASTRID_REMOTION_STREAM_FRAMES', installFrameStreaming: () => {}};
      loadTs(path.join(project, 'remotion.config.ts'), {'@remotion/cli/config': {Config}, './pcm-aac-stitch': {pcmAacMp4Stitch}, './stream-frames': streamFrames});
      assert.equal(hooks.length, selected ? 1 : 0);
    }
  } finally {
    if (prior === undefined) delete process.env.ASTRID_REMOTION_PCM_AAC_OUTPUT;
    else process.env.ASTRID_REMOTION_PCM_AAC_OUTPUT = prior;
  }
});
