const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const {test} = require('node:test');
const ts = require('typescript');

// Loads the Astrid-owned ReviewOverlay.tsx in-process with stubbed `remotion`
// hooks and JSX runtime, so the overlay's element tree can be inspected. No
// renderer, browser or FFmpeg process is started.
const project = path.resolve(__dirname, '..');
const frame = {current: 0, fps: 24, width: 1920, height: 1080};
const stubs = {
  remotion: {
    AbsoluteFill: 'AbsoluteFill',
    useCurrentFrame: () => frame.current,
    useVideoConfig: () => ({fps: frame.fps, width: frame.width, height: frame.height}),
  },
  'react/jsx-runtime': {
    jsx: (type, props) => ({type, props}),
    jsxs: (type, props) => ({type, props}),
    Fragment: 'Fragment',
  },
};
const loadTs = (filename) => {
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  const requireDefault = loaded.require.bind(loaded);
  loaded.require = (name) => (name in stubs ? stubs[name] : requireDefault(name));
  const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true},
  }).outputText;
  loaded._compile(source, filename);
  return loaded.exports;
};
const overlay = loadTs(path.join(project, 'src', 'ReviewOverlay.tsx'));
const {ReviewOverlay, reviewCaption, reviewLabel} = overlay;

// Phrases as the Python builder (astrid/core/timeline/review_captions.py) emits them.
const TWO_LINES = "One year ago, I invited testers into a\nDiscord and told them that this new tool";
const review = {
  shots: [{shot_id: 'ch01', name: '01 TOMORROW', at: 0, hold: 10}],
  render_dimensions: {width: 1920, height: 1080},
  speech: {
    status: 'projected',
    phrases: [
      {id: 'review:occ:0', text: TWO_LINES, status: 'projected', render_interval: {start: 0.1, end: 4.2}, timing_basis: 'vo_word', word_aligned: true},
      {id: 'review:occ:1', text: "It wasn't.", status: 'projected', render_interval: {start: 7.46, end: 8.34}, timing_basis: 'vo_word', word_aligned: true},
      {id: 'review:occ:2', text: 'Draft line that must never show.', status: 'draft', render_interval: {start: 0, end: 10}},
    ],
  },
};
const WHOLE_SCRIPT = `${TWO_LINES} It wasn't. Draft line that must never show.`;

const at = (seconds) => { frame.current = Math.round(seconds * frame.fps); return frame.current; };

// Collects every element node under `node` that carries a style, in document order.
const styledNodes = (node, out = []) => {
  if (node === null || node === undefined || typeof node !== 'object') return out;
  if (Array.isArray(node)) { node.forEach(child => styledNodes(child, out)); return out; }
  if (node.props && node.props.style) out.push(node);
  if (node.props) styledNodes(node.props.children, out);
  return out;
};
const allText = (node, out = []) => {
  if (typeof node === 'string' || typeof node === 'number') { out.push(String(node)); return out; }
  if (node === null || node === undefined || typeof node !== 'object') return out;
  if (Array.isArray(node)) { node.forEach(child => allText(child, out)); return out; }
  if (node.props) allText(node.props.children, out);
  return out;
};
// The bottom caption is the only styled node with the caption's font weight.
const captionNode = (tree) => styledNodes(tree).find(node => node.props.style.fontWeight === 600) ?? null;

test('reviewCaption shows the single active phrase, with its line breaks kept', () => {
  assert.equal(reviewCaption(review, at(2), 24), TWO_LINES);
  assert.equal(reviewCaption(review, at(8), 24), "It wasn't.");
});

test('reviewCaption shows nothing between phrases and never a draft or the whole script', () => {
  assert.equal(reviewCaption(review, at(6), 24), null);
  assert.equal(reviewCaption(review, at(9.5), 24), null);
  for (const seconds of [0, 2, 6, 8]) {
    const caption = reviewCaption(review, at(seconds), 24);
    if (caption) assert.notEqual(caption, WHOLE_SCRIPT);
    assert.ok(!(caption ?? '').includes('Draft line'));
  }
});

test('overlapping phrases resolve to the one that started last', () => {
  const overlapping = {shots: [], speech: {status: 'projected', phrases: [
    {id: 'a', text: 'earlier', status: 'projected', render_interval: {start: 1, end: 5}},
    {id: 'b', text: 'later', status: 'projected', render_interval: {start: 2, end: 3}},
  ]}};
  assert.equal(reviewCaption(overlapping, at(2.5), 24), 'later');
});

test('overlay element renders only the current phrase as pre-wrapped caption text', () => {
  const tree = ReviewOverlay({review});
  const caption = captionNode(tree);
  assert.ok(caption, 'caption element is rendered during a phrase');
  assert.equal(caption.props.children, reviewCaption(review, at(2), 24));
  assert.equal(caption.props.style.whiteSpace, 'pre-wrap');
  const texts = allText(tree);
  assert.ok(texts.includes(TWO_LINES));
  assert.ok(!texts.some(text => text.includes('Draft line')), 'drafts are never drawn');
  assert.ok(!texts.some(text => text.includes("It wasn't.") && text !== "It wasn't."), 'no other phrase is merged in');
});

test('overlay draws no caption between phrases but keeps the shot label and timecode', () => {
  at(6);
  const tree = ReviewOverlay({review});
  assert.equal(captionNode(tree), null);
  const label = reviewLabel(review, frame.current, frame.fps);
  assert.equal(label, '01 TOMORROW  ·  00:00:06.000');
  assert.ok(allText(tree).includes(label));
});
