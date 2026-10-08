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
let boundaryReport, endSpanningTiming;
try {
  execFileSync(process.execPath, [compiler, '--strict', '--target', 'ES2020', '--module', 'commonjs', '--outDir', build,
    join(root, 'astrid/packs/local/elements/boundary.ts')], {cwd: root, stdio: 'pipe'});
  const require = createRequire(import.meta.url);
  ({boundaryReport} = require(join(build, 'boundary.js')));
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
