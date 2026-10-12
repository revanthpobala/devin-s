// Verifies the two sequencing fixes in desk.js:
//   1. memoRender returns identical HTML for identical payloads (and re-renders on change)
//   2. the spawner of loadToday preserves scroll position + focus across a re-render
//
// These two behaviours are what keep the 6-second desk poll from destroying the user's place
// (scroll jump, lost focus) and from re-parsing the movers/ledger subtrees every cycle.
//
// Run: node tests/desk_rerender_guard.test.js

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, '..', 'web', 'static', 'js', 'desk.js'), 'utf8');

function extract(name) {
  const i = src.indexOf('  ' + name + '(');
  assert.ok(i >= 0, `missing ${name}()`);
  const j = src.indexOf('\n  },', i) + 5;
  return src.slice(i, j).trim().replace(/,\s*$/, '');
}

const AppDesk = new Function(
  `const self = {${extract('memoRender')}, ${extract('renderRegimeStrip')}, ${extract('renderBatSignal')}}; return self;`,
)();

let pass = 0, fail = 0;
const run = (label, fn) => {
  try { fn(); pass++; console.log(`  ok    ${label}`); }
  catch (e) { fail++; console.log(`  FAIL  ${label}\n          ${e.message}`); }
};

console.log('\nRERENDER GUARDS');

run('memoRender: same payload returns the same string (no re-parse)', () => {
  AppDesk._memo = {};
  const a = AppDesk.memoRender('k', { x: 1 }, () => 'HTML-A');
  const b = AppDesk.memoRender('k', { x: 1 }, () => 'HTML-A');
  assert.strictEqual(a, b);
  let calls = 0;
  AppDesk.memoRender('k2', { y: 2 }, () => { calls++; return 'H'; });
  AppDesk.memoRender('k2', { y: 2 }, () => { calls++; return 'H'; });
  assert.strictEqual(calls, 1, `renderer ran ${calls} times for one payload`);
});

run('memoRender: changed payload re-renders', () => {
  AppDesk._memo = {};
  const a = AppDesk.memoRender('k', { x: 1 }, () => 'A');
  const b = AppDesk.memoRender('k', { x: 2 }, () => 'B');
  assert.strictEqual(a, 'A');
  assert.strictEqual(b, 'B');
});

run('memoRender: unserialisable payload falls back instead of throwing', () => {
  AppDesk._memo = {};
  const circular = {};
  circular.self = circular;
  assert.doesNotThrow(() => AppDesk.memoRender('k3', circular, () => 'X'));
});

run('regime strip memoised end-to-end', () => {
  AppDesk._memo = {};
  const regime = { available: true, fear_greed: { score: 50, rating: 'NEUTRAL', flag: 'NEUTRAL' }, movers: { spikes: [] } };
  const first = AppDesk.memoRender('regime_strip', regime, (r) => AppDesk.renderRegimeStrip(r));
  const second = AppDesk.memoRender('regime_strip', regime, (r) => AppDesk.renderRegimeStrip(r));
  assert.ok(first.length > 0);
  assert.strictEqual(first, second);
});

run('empty regime memoises to empty string', () => {
  AppDesk._memo = {};
  const out = AppDesk.memoRender('regime_strip', {}, (r) => AppDesk.renderRegimeStrip(r));
  assert.strictEqual(out, '');
});

console.log('\nSCROLL/FFOCUS PRESERVATION (loadToday source contract)');

run('loadToday preserves scrollTop across the innerHTML swap', () => {
  const i = src.indexOf('const prevScroll = cont.scrollTop;');
  assert.ok(i > 0, 'prevScroll capture missing');
  const window = src.slice(i, i + 400);
  assert.ok(window.includes('cont.innerHTML = html;'), 'innerHTML assignment missing');
  assert.ok(window.includes('cont.scrollTop = prevScroll;'), 'scrollTop restore missing');
});

run('loadToday restores focus to the previously focused control', () => {
  const i = src.indexOf('const prevFocusId = document.activeElement && document.activeElement.id;');
  assert.ok(i > 0, 'activeElement capture missing');
  const window = src.slice(i, i + 500);
  assert.ok(window.includes('refocus.focus();'), 'focus restore missing');
});

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
