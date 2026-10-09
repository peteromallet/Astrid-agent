// Evaluate elements' own motion maths: each element folder may ship motion.ts
// exporting motionAt(params, clipFrame, fps) -> {prop: number}. The component
// draws with the same functions, so these values ARE the element's motion.
//
// usage: node element_motion.mjs <remotion project dir>   (JSON on stdin/stdout)
// stdin:  {"modules": {"am-presenter": "/abs/motion.ts"},
//          "requests": [{"id": "clip", "type": "am-presenter", "params": {}, "fps": 30, "frames": 120}]}
// stdout: {"values": {"clip": [{...frame 0}, {...frame 1}, ...]}, "errors": {"am-x": "why"}}
//
// esbuild comes from the renderer's own project; npm imports (react, remotion)
// are stubbed, because motion maths must not touch them.
import {createRequire} from 'node:module';
import path from 'node:path';

const projectDir = process.argv[2];
const esbuild = createRequire(path.join(projectDir, 'package.json'))('esbuild');

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));

const stub = new Proxy(function () {}, {get: (_t, key) => (key === '__esModule' ? false : stub), apply: () => stub});
const load = async (file) => {
  const out = await esbuild.build({
    entryPoints: [file], bundle: true, write: false, format: 'cjs', platform: 'node', jsx: 'automatic',
    external: ['react', 'react/*', 'react-dom', 'react-dom/*', 'remotion', '@remotion/*', '@banodoco/*'], logLevel: 'silent',
  });
  const module = {exports: {}};
  new Function('require', 'module', 'exports', out.outputFiles[0].text)(() => stub, module, module.exports);
  if (typeof module.exports.motionAt !== 'function') throw new Error(`${file} does not export motionAt`);
  return module.exports.motionAt;
};

const fns = {};
const errors = {};
for (const [type, file] of Object.entries(input.modules || {})) {
  try {
    fns[type] = await load(file);
  } catch (error) {
    errors[type] = String(error && error.message ? error.message : error).split('\n')[0];
  }
}
const values = {};
for (const request of input.requests || []) {
  const fn = fns[request.type];
  if (!fn) continue;
  try {
    const rows = [];
    for (let frame = 0; frame < request.frames; frame += 1) rows.push(fn(request.params || {}, frame, request.fps));
    values[request.id] = rows;
  } catch (error) {
    errors[request.type] = String(error && error.message ? error.message : error).split('\n')[0];
  }
}
process.stdout.write(JSON.stringify({values, errors}));
