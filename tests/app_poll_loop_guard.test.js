/*
 * POLL WIRING: who calls loadToday(true), how often, and what it is allowed to do.
 *
 * The 6s poll is the whole reason the desk used to tear down its DOM twice a minute, and the
 * silent flag is the only thing standing between it and a half-typed feedback note. Nothing
 * pinned either. These are behaviour tests: app.js is executed, its intervals are captured, and
 * they are fired.
 *
 * Run: node tests/app_poll_loop_guard.test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..');
const APP_SRC = fs.readFileSync(path.join(ROOT, 'web', 'static', 'js', 'app.js'), 'utf8');

function bootApp({ currentDesk = 'today' } = {}) {
  const intervals = [];
  const listeners = {};
  const loadTodayCalls = [];

  const windowStub = {
    AppState: { currentDesk },
    AppDesk: {
      loadToday: (...args) => { loadTodayCalls.push(args); return Promise.resolve(); },
      loadJournal: () => Promise.resolve(),
      loadRecord: () => Promise.resolve(),
    },
    AppApi: { getTickers: async () => ({ tickers: [] }) },
    location: { search: '', pathname: '/', hash: '' },
    localStorage: { getItem: () => null, setItem: () => {} },
    addEventListener: () => {},
  };

  const documentStub = {
    // Panes exist for every desk, so a switch stays on this page instead of navigating away.
    getElementById: (id) => (id.startsWith('desk-pane-') ? { style: {} } : null),
    documentElement: { getAttribute: () => 'light', setAttribute: () => {} },
    addEventListener: (ev, fn) => { (listeners[ev] = listeners[ev] || []).push(fn); },
  };

  const ctx = {
    window: windowStub,
    document: documentStub,
    console,
    URLSearchParams,
    localStorage: windowStub.localStorage,
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    clearInterval: () => {},
    setTimeout: () => 0,
    clearTimeout: () => {},
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(APP_SRC, ctx, { filename: 'app.js' });

  const fireDomReady = () => (listeners.DOMContentLoaded || []).forEach((fn) => fn());
  fireDomReady();

  const reset = () => { loadTodayCalls.length = 0; };

  return {
    reset, windowStub, intervals, loadTodayCalls,
    /** Run every interval registered at `ms`, as the browser would on a tick. */
    tick: (ms) => intervals.filter((i) => i.ms === ms).map((i) => i.fn()),
    counts: () => intervals.map((i) => i.ms),
  };
}

let passed = 0;
let failed = 0;
function test(name, fn) {
  return Promise.resolve().then(fn).then(
    () => { passed += 1; console.log(`  ok    ${name}`); },
    (e) => { failed += 1; console.log(`  FAIL  ${name}\n          ${String(e.message).split('\n')[0]}`); },
  );
}

(async () => {
  console.log('\nPOLL LOOP WIRING');

  test('the CI-invoked suites have the files they need on disk', () => {
    // CI runs these suites explicitly (there is no JS test runner), and a partial checkout fails
    // with a bare ENOENT that looks like a regression in a passing suite. Fail loudly instead.
    const required = [
      'helpers/desk_dom_harness.js',
      'fixtures/desk_loadtoday/happy_opps.json',
      'fixtures/desk_loadtoday/hostile_nulls.json',
      '__snapshots__/desk_loadtoday/happy_opps.html',
      '__snapshots__/desk_loadtoday/hostile_nulls.html',
    ];
    for (const rel of required) {
      const p = path.join(__dirname, rel);
      assert.ok(fs.existsSync(p), `missing ${rel} -- stage it with the CI change in ci.yml`);
    }
  });

  await test('exactly one 6s interval polls the desk, and it polls silently', async () => {
    const app = bootApp();
    app.reset();
    const sixes = app.intervals.filter((i) => i.ms === 6000);
    assert.strictEqual(sixes.length, 1, `expected one 6s interval, got ${sixes.length}`);
    app.tick(6000);
    assert.deepStrictEqual(app.loadTodayCalls, [[true]],
      'the 6s tick must call loadToday(true) and nothing else');
  });

  await test('the 6s poll is gated on the today desk being the visible one', async () => {
    // App.init() boots into the today desk, so the gate has to be exercised after boot.
    const app = bootApp();
    app.reset();
    app.windowStub.AppState.currentDesk = 'watchlist';
    app.tick(6000);
    assert.deepStrictEqual(app.loadTodayCalls, [],
      'the poll rebuilt the today desk while the user was on another page');
    app.windowStub.AppState.currentDesk = 'today';
    app.tick(6000);
    assert.deepStrictEqual(app.loadTodayCalls, [[true]]);
  });

  await test('the 2.5s fast loop never touches the desk', async () => {
    const app = bootApp();
    app.reset();
    app.tick(2500);
    app.tick(2500);
    assert.deepStrictEqual(app.loadTodayCalls, [], 'the fast loop called loadToday');
  });

  await test('switching to the today desk reloads it non-silently', async () => {
    const app = bootApp({ currentDesk: 'watchlist' });
    app.reset();
    app.windowStub.App.switchDesk('today');
    await Promise.resolve();
    assert.deepStrictEqual(app.loadTodayCalls, [[]],
      'a desk switch must do a full (non-silent) load');
  });

  await test('silent=true early-returns while a text field has focus', async () => {
    const { loadDesk } = require('./helpers/desk_dom_harness');
    const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests', 'fixtures', 'desk_loadtoday', 'happy_opps.json'), 'utf8'));
    const h = loadDesk(fixture);
    const input = { id: 'fb-notes-X', tagName: 'INPUT', value: 'half typed thought', focus() {} };
    h.documentStub.activeElement = input;
    await h.AppDesk.loadToday(true);
    assert.deepStrictEqual(h.requests, [], 'a silent poll overwrote the field the user is typing in');
    assert.strictEqual(h.container.writes, 0, 'a silent poll still re-rendered the desk');
  });

  await test('a poll still runs when a non-text control has focus', async () => {
    const { loadDesk, POLL_REQUESTS } = require('./helpers/desk_dom_harness');
    const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests', 'fixtures', 'desk_loadtoday', 'happy_opps.json'), 'utf8'));
    const h = loadDesk(fixture);
    h.documentStub.activeElement = { id: 'subtab-today-opps', tagName: 'BUTTON', focus() {} };
    await h.AppDesk.loadToday(true);
    assert.strictEqual(h.requests.length, POLL_REQUESTS.length, 'the poll did not run');
    // A focused SELECT is NOT covered by the silent guard today. Pinned so the gap is visible
    // rather than discovered by a user losing their feedback grade.
    const h2 = loadDesk(fixture);
    h2.documentStub.activeElement = { id: 'fb-grade-X', tagName: 'SELECT', value: 'A', focus() {} };
    await h2.AppDesk.loadToday(true);
    assert.strictEqual(h2.requests.length, POLL_REQUESTS.length,
      'a focused SELECT is still clobbered by the poll -- see _dirtyFeedbackIds in the refactor');
  });

  await test('the poll does not fetch briefing or feedback-loop, which it never reads', async () => {
    const { loadDesk, POLL_REQUESTS } = require('./helpers/desk_dom_harness');
    const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests', 'fixtures', 'desk_loadtoday', 'happy_opps.json'), 'utf8'));
    const h = loadDesk(fixture);
    await h.AppDesk.loadToday(true);
    for (const url of POLL_REQUESTS) assert.ok(h.requests.includes(url), `poll did not fetch ${url}`);
    assert.ok(!h.requests.includes('/api/desk/morning-briefing'),
      'the poll still fetches morning-briefing, which it never reads');
    assert.ok(!h.requests.includes('/api/desk/feedback-loop'),
      'the poll still fetches feedback-loop, which it never reads');
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
