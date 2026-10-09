const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const {test} = require('node:test');
const ts = require('typescript');

// Compile only Astrid-owned TS, as pcm-aac-stitch.test.cjs does. No renderer,
// Chromium or FFmpeg process is started.
const project = path.resolve(__dirname, '..');
const loadTs = (filename) => {
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true},
  }).outputText;
  loaded._compile(source, filename);
  return loaded.exports;
};
const {installFrameStreaming, streamingDecision, STREAM_FRAMES_ENV} = loadTs(path.join(project, 'stream-frames.ts'));
const rendererEntry = require.resolve('@remotion/renderer', {paths: [project]});
const dist = path.dirname(rendererEntry);
const GiB = 1024 ** 3;

test('installed Remotion decides parallel encoding (frame streaming) only through the patched export', () => {
  const renderMedia = fs.readFileSync(path.join(dist, 'render-media.js'), 'utf8');
  // The gate is read from the module's exports at call time, so assigning the
  // export reaches renderMedia. A Remotion upgrade that changes this fails here.
  assert.match(renderMedia, /const prestitcher_memory_usage_1 = require\("\.\/prestitcher-memory-usage"\);/);
  assert.match(renderMedia, /\(0, prestitcher_memory_usage_1\.shouldUseParallelEncoding\)\(\{/);
  assert.equal((renderMedia.match(/shouldUseParallelEncoding/g) || []).length, 1);
  // Streaming keeps frames out of the working dir; file mode writes them there.
  assert.match(renderMedia, /outputDir: parallelEncoding \? null : workingDir/);
  // Stock gate: os.freemem() must exceed ~1 GB/megapixel + 2 GB, which a Mac
  // reporting a few hundred MB "free" never satisfies.
  const stock = require(path.join(dist, 'prestitcher-memory-usage.js'));
  assert.equal(typeof stock.shouldUseParallelEncoding, 'function');
});

test('streaming fits 1080p and review scale on an 8 GB machine, not 4K', () => {
  const eightGb = 8 * GiB;
  assert.equal(streamingDecision({width: 1920, height: 1080, totalMemory: eightGb}).hasEnoughMemory, true);
  assert.equal(streamingDecision({width: 640, height: 360, totalMemory: eightGb}).hasEnoughMemory, true);
  assert.equal(streamingDecision({width: 3840, height: 2160, totalMemory: eightGb}).hasEnoughMemory, false);
  assert.equal(streamingDecision({width: 3840, height: 2160, totalMemory: 32 * GiB}).hasEnoughMemory, true);
});

test('install replaces the installed gate in the shared module instance', () => {
  const gatePath = path.join(dist, 'prestitcher-memory-usage.js');
  const gate = require(gatePath);
  const original = gate.shouldUseParallelEncoding;
  try {
    installFrameStreaming({rendererEntry, totalMemory: 8 * GiB});
    // The same module instance render-media.js holds now streams 1080p.
    const decision = require(gatePath).shouldUseParallelEncoding({width: 1920, height: 1080, logLevel: 'info'});
    assert.deepEqual(decision, {hasEnoughMemory: true, freeMemory: 8 * GiB, estimatedUsage: 1920 * 1080 * 1000});
  } finally {
    gate.shouldUseParallelEncoding = original;
  }
});

test('install fails loudly when a Remotion upgrade moves the gate', () => {
  assert.throws(
    () => installFrameStreaming({rendererEntry: '/nowhere/dist/index.js', load: () => ({})}),
    /does not export shouldUseParallelEncoding/,
  );
});

test('the config installs streaming only when Astrid sets the environment flag', () => {
  const prior = process.env[STREAM_FRAMES_ENV];
  try {
    for (const flag of [undefined, '0', '1']) {
      if (flag === undefined) delete process.env[STREAM_FRAMES_ENV];
      else process.env[STREAM_FRAMES_ENV] = flag;
      const installs = [];
      const Config = new Proxy({}, {get: () => () => {}});
      const filename = path.join(project, 'remotion.config.ts');
      const loaded = new Module(filename, module);
      loaded.filename = filename;
      loaded.paths = Module._nodeModulePaths(project);
      const requireDefault = loaded.require.bind(loaded);
      const overrides = {
        '@remotion/cli/config': {Config},
        './pcm-aac-stitch': {pcmAacMp4Stitch: () => () => {}},
        './stream-frames': {STREAM_FRAMES_ENV, installFrameStreaming: (options) => installs.push(options)},
      };
      loaded.require = (name) => name in overrides ? overrides[name] : requireDefault(name);
      loaded._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
        compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true},
      }).outputText, filename);
      assert.equal(installs.length, flag === '1' ? 1 : 0);
      if (flag === '1') assert.equal(installs[0].rendererEntry, rendererEntry);
    }
  } finally {
    if (prior === undefined) delete process.env[STREAM_FRAMES_ENV];
    else process.env[STREAM_FRAMES_ENV] = prior;
  }
});
