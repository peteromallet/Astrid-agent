/** Pure TypeScript conformance tests; needs the renderer's TypeScript compiler. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, mkdtempSync, rmSync, existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';

const root = resolve(import.meta.dirname, '..');
const compiler = process.env.TS_COMPILER ?? [join(root, 'remotion/node_modules/typescript/bin/tsc'), join(root, 'node_modules/typescript/bin/tsc')].find(existsSync);
assert.ok(compiler, 'Install renderer dependencies or set TS_COMPILER to the TypeScript compiler');
const build = mkdtempSync(join(tmpdir(), 'astrid-boundary-'));
let boundaryReport, endSpanningTiming, transitionFrames, intentContext, cueIdentity, sameContext;
try {
  execFileSync(process.execPath, [compiler, '--strict', '--target', 'ES2020', '--module', 'commonjs', '--outDir', build,
    join(root, 'astrid/packs/local/elements/boundary.ts'), join(root, 'astrid/packs/local/elements/seam-intent.ts')], {cwd: root, stdio: 'pipe'});
  const require = createRequire(import.meta.url);
  ({boundaryReport, transitionFrames} = require(join(build, 'boundary.js')));
  ({intentContext, cueIdentity, sameContext} = require(join(build, 'seam-intent.js')));
  ({endSpanningTiming} = require(join(build, 'effects/end-spanning-layer/timing.js')));
} finally {
  rmSync(build, {recursive: true, force: true});
}
const fixture = JSON.parse(readFileSync(join(root, 'tests/fixtures/visual-boundary-v1.json')));
for (const vector of fixture.vectors) {
  test(vector.name, () => assert.deepEqual(boundaryReport(vector.context), vector.expected));
}
test('cumulative phase sampling and explicit/all-or-fallback precedence', () => {
  assert.deepEqual(endSpanningTiming({at: 33.8, hold: 27}, {phaseDurations: {prep: 8.166666666666666, iteration: 3.7, anchors: 3.1666666666666665, workflow: 10.866666666666667}}, 30).frames, [245, 356, 451, 777]);
  assert.deepEqual(endSpanningTiming({at: 0, hold: 10}, {prepSeconds: 1}, 24).frames, [58, 110, 158, 240]);
});
test('does not execute supplied effect code', () => {
  const result = boundaryReport({clip: {clipType: 'submitted', code: 'throw new Error("executed")'}, fps: 30, startFrame: 0, endFrame: 100, path: ['parent', 'p', 'effect', 'e']});
  assert.deepEqual(result.opaque, ['unknown effect timing']);
});
test('incoming fade and unknown element reference disclose independently', () => {
  const result = boundaryReport({clip: {clipType: 'media', entrance: {type: 'fade', duration: 0.5},
    elementRef: {id: 'custom', kind: 'effect'}}, fps: 30, startFrame: 30, endFrame: 60, path: ['parent', 't', 'clip', 'b']});
  assert.deepEqual(result.cues, [{frame: 31, kind: 'motion-start', id: 'entrance', path: ['parent', 't', 'clip', 'b']}]);
  assert.deepEqual(result.opaque, ['unverified element reference timing']);
});
test('only recognized transition timing is usable', () => {
  assert.equal(transitionFrames({type: 'not-real', duration: 1}, 30), null);
  assert.equal(transitionFrames({type: 'crossfade', duration: 0.2}, 30), 6);
  assert.equal(transitionFrames({type: 'fade', durationFrames: -1}, 30), null);
});
test('failed playback disclosure does not erase known motion', () => {
  const result = boundaryReport({clip: {clipType: 'animated-media-transform', hold: 1, entrance: 'fade',
    params: {keyframes: [{at: 0, x: 0, y: 0, width: 10, height: 10, opacity: 1},
      {at: 0.5, x: 20, y: 0, width: 10, height: 10, opacity: 1}], sourceSegments: []}},
    fps: 30, startFrame: 30, endFrame: 60, path: ['parent', 't', 'clip', 'b']});
  assert.ok(result.cues.some(c => c.id === 'entrance'));
  assert.ok(result.cues.some(c => c.id === 'key-0'));
  assert.ok(result.opaque.some(reason => reason.startsWith('failed disclosure:')));
});
test('portable context binds authored timing and ignores derived reports', () => {
  const owner = {path: ['parent', 't', 'clip', 'a'], startFrame: 0, endFrame: 30, originFrame: 0,
    clip: {clipType: 'media', track: 'v', params: {report: {blocked: false}, x: 1}}, source: null};
  const context = intentContext(30, 30, [owner]);
  assert.ok(sameContext(context, intentContext(30, 30, [{...owner, clip: {...owner.clip, params: {x: 1}}}])));
  assert.ok(!sameContext(context, intentContext(24, 30, [owner])));
  assert.equal(cueIdentity({path: owner.path, frame: 31, kind: 'motion-start', id: 'entrance'}), '[["clip","a"],"motion-start","entrance",31]');
});
