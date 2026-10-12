/*
 * RE-RENDER BEHAVIOUR: what a 6s poll actually does to the desk, observed not string-matched.
 *
 * This replaces the substring assertions that used to live at the bottom of
 * desk_rerender_guard.test.js. They passed while the behaviour they described was entirely
 * broken: `cont.scrollTop` was always 0 because #today-content-container has no overflow rule,
 * so `if (prevScroll > 0)` never fired and the page jumped to the top on every poll. Nothing in a
 * source-text check can see that; only running the code can.
 *
 * Run: node tests/desk_loadtoday_rerender.test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const { loadDesk, idsIn, POLL_REQUESTS } = require('./helpers/desk_dom_harness');

const FIXTURE = path.join(__dirname, 'fixtures', 'desk_loadtoday', 'happy_opps.json');
const SCROLL_AT_REST = 1234;

let passed = 0;
let failed = 0;
function test(name, fn) {
  return fn().then(
    () => { passed += 1; console.log(`  ok    ${name}`); },
    (e) => { failed += 1; console.log(`  FAIL  ${name}\n${String(e.message).split('\n').map((l) => `          ${l}`).join('\n')}`); },
  );
}

(async () => {
  console.log('\nDESK RE-RENDER BEHAVIOUR');

  const fixture = JSON.parse(fs.readFileSync(FIXTURE, 'utf8'));

  await test('the window scroll position survives a poll', async () => {
    const h = loadDesk(fixture, { scrollY: SCROLL_AT_REST });
    await h.AppDesk.loadToday();
    assert.strictEqual(h.windowStub.scrollY, SCROLL_AT_REST);
    await h.AppDesk.loadToday(true);
    assert.strictEqual(h.windowStub.scrollY, SCROLL_AT_REST,
      'the poll moved the page: the user is now somewhere else entirely');
  });

  await test('focus is restored to the same control AFTER the swap, and does not undo the scroll', async () => {
    const h = loadDesk(fixture, { scrollY: SCROLL_AT_REST });
    h.AppDesk._todaySubTab = 'positions';
    await h.AppDesk.loadToday();
    // A feedback note the user is typing in. It exists in the current DOM.
    const first = h.elements.get('fb-notes-MSFT_trade_1760000000');
    assert.ok(first, 'the positions tab fixture has no fb-notes input to focus');
    h.documentStub.activeElement = first;

    // A controller reload (deep-research dispatch, position action, sub-tab switch): the only
    // path that still swaps the DOM.
    await h.AppDesk.loadToday();

    const after = h.elements.get('fb-notes-MSFT_trade_1760000000');
    assert.ok(after, 'the re-rendered DOM lost the feedback input the user was typing in');
    assert.ok(after !== first, 'the input was not re-created by the swap');
    assert.strictEqual(after.focusCalls, 1, 'focus was not restored to the re-created control');
    // focus() scrolls the element into view; the restore has to be the last thing that runs.
    assert.strictEqual(h.windowStub.scrollY, SCROLL_AT_REST,
      'focus() scroll-into-view won: the page is back at the top');
  });

  await test('a poll leaves the focused control alone entirely', async () => {
    const h = loadDesk(fixture, { scrollY: SCROLL_AT_REST });
    h.AppDesk._todaySubTab = 'positions';
    await h.AppDesk.loadToday();
    const note = h.elements.get('fb-notes-MSFT_trade_1760000000');
    h.documentStub.activeElement = note;
    note.value = 'half a thought';
    const writes = h.container.writes;
    const scrollY = h.windowStub.scrollY;

    await h.AppDesk.loadToday(true);

    // Not "focus restored": not destroyed in the first place.
    assert.strictEqual(h.container.writes, writes, 'the poll rewrote the desk');
    assert.strictEqual(note.focusCalls, 0, 'the poll touched the focused control');
    assert.strictEqual(h.elements.get('fb-notes-MSFT_trade_1760000000'), note,
      'the poll replaced the element the user is typing in');
    assert.strictEqual(note.value, 'half a thought', 'the poll ate the typed note');
    assert.strictEqual(h.windowStub.scrollY, scrollY, 'the poll moved the page');
  });

  await test('a changed payload re-renders the memoised blocks in place', async () => {
    const h = loadDesk(fixture, { scrollY: 0 });
    await h.AppDesk.loadToday();
    const before = h.rendererCalls.renderRegimeStrip + h.rendererCalls.renderBatSignal;
    const stripBefore = h.elements.get('desk-regime-strip').innerHTML;
    // The memoised subtrees are keyed on market_regime / action_panel, not market_pulse.
    fixture.today.market_regime.vix.price = 41.5;
    fixture.today.action_panel.bat_signal.action = 'HALF SIZE';
    await h.AppDesk.loadToday(true);
    const after = h.rendererCalls.renderRegimeStrip + h.rendererCalls.renderBatSignal;
    assert.ok(after > before, 'a changed payload did not refresh the regime / bat signal');
    assert.ok(h.elements.get('desk-regime-strip').innerHTML !== stripBefore,
      'the regime strip div was not rewritten with the new payload');
    assert.ok(h.elements.get('desk-regime-strip').innerHTML.includes('41.50'),
      'the new VIX never reached the DOM');
    // ...and it still did so without touching the rest of the desk.
    assert.strictEqual(h.container.writes, 1, 'the poll rewrote the whole desk to move one number');
  });

  await test('an unchanged payload does not rewrite the memoised subtrees', async () => {
    const h = loadDesk(fixture, { scrollY: 0 });
    await h.AppDesk.loadToday();
    const strip = h.elements.get('desk-regime-strip');
    const signal = h.elements.get('desk-bat-signal');
    // The first poll is allowed to write: the stub DOM starts its children empty. Every poll
    // after that must be a no-op for an identical payload.
    await h.AppDesk.loadToday(true);
    const stripWrites = strip.writes;
    const signalWrites = signal.writes;
    await h.AppDesk.loadToday(true);
    assert.strictEqual(strip.writes, stripWrites, 'the regime strip was re-written for an identical payload');
    assert.strictEqual(signal.writes, signalWrites, 'the bat signal was re-written for an identical payload');
  });

  await test('a poll moves the live numbers nobody else updates', async () => {
    const h = loadDesk(fixture, { scrollY: 0 });
    await h.AppDesk.loadToday();
    fixture.today.market_pulse.spy = 6000.01;
    fixture.today.market_pulse.qqq = 700.07;
    fixture.today.market_pulse.vix = 44.22;
    fixture.today.market_pulse.market_status_text = 'SENTINEL_LIVE_STATUS';
    fixture.today.funnel.actionable = 99;
    fixture.today.top_opportunities.push({ ticker: 'SENTINEL_LIVE' });   // sub-tab badge: 3 -> 4
    fixture.today.actionable_alerts.push({ symbol: 'SENTINEL_LIVE2' }); // sub-tab badge: 2 -> 3
    await h.AppDesk.loadToday(true);

    assert.strictEqual(h.elements.get('desk-spy-px').innerText, '$6000.01');
    assert.strictEqual(h.elements.get('desk-qqq-px').innerText, '$700.07');
    assert.strictEqual(h.elements.get('desk-vix-px').innerText, '44.22');
    assert.strictEqual(h.elements.get('desk-vix-px').style.color, 'var(--rose-light)',
      'a VIX above 20 must turn the strip red');
    assert.strictEqual(h.elements.get('desk-market-status-text').innerText, 'SENTINEL_LIVE_STATUS');
    assert.strictEqual(h.elements.get('desk-funnel-actionable').innerText, '99');
    assert.strictEqual(h.elements.get('subtab-count-opps').innerText, '4');
    assert.strictEqual(h.elements.get('subtab-count-alerts').innerText, '3');
  });

  await test('a controller reload is never committed by a poll that started first', async () => {
    // The in-flight guard: a 6s tick that lands while a controller reload is running is dropped
    // rather than half-committing over it.
    const h = loadDesk(fixture, { scrollY: 0 });
    let releaseToday;
    let stalled = false;
    const original = h.windowStub.AppApi.request.bind(h.windowStub.AppApi);
    h.windowStub.AppApi.request = (url) => {
      // Stall only the first `today` fetch, so the reload is in flight when the poll lands.
      if (url.startsWith('/api/desk/today') && !stalled) {
        stalled = true;
        return new Promise((res) => { releaseToday = () => res(JSON.parse(JSON.stringify(fixture.today))); });
      }
      return original(url);
    };

    const reload = h.AppDesk.loadToday();          // controller reload in flight
    assert.ok(releaseToday, 'the today fetch was never started');
    const requestsBefore = h.requests.length;
    await h.AppDesk.loadToday(true);               // 6s tick arrives mid-reload
    assert.strictEqual(h.requests.length, requestsBefore,
      'the poll fired its own fetches instead of dropping behind the in-flight guard');
    releaseToday();
    await reload;
    assert.strictEqual(h.container.writes, 1, 'the reload still committed exactly once');
    assert.ok(h.container.innerHTML.includes('MARKET DYNAMICS'), 'the reload committed');

    // ...and the guard is released, so the next poll still works (and only polls what it needs).
    await h.AppDesk.loadToday(true);
    assert.strictEqual(h.requests.length, requestsBefore + POLL_REQUESTS.length, 'the guard never released');
  });

  await test('the movers/ledger subtrees render exactly once across two polls', async () => {
    const h = loadDesk(fixture, { scrollY: SCROLL_AT_REST });
    await h.AppDesk.loadToday();
    await h.AppDesk.loadToday(true);
    assert.strictEqual(h.rendererCalls.renderRegimeStrip, 1,
      `regime strip re-parsed ${h.rendererCalls.renderRegimeStrip}x for one payload`);
    assert.strictEqual(h.rendererCalls.renderBatSignal, 1,
      `bat signal re-parsed ${h.rendererCalls.renderBatSignal}x for one payload`);
  });

  await test('a controller reload survives an unchanged payload', async () => {
    const h = loadDesk(fixture, { scrollY: 0 });
    await h.AppDesk.loadToday();
    const before = h.container.innerHTML;
    await h.AppDesk.loadToday();
    assert.strictEqual(h.container.innerHTML, before,
      'a controller reload with an identical payload produced different HTML');
  });

  await test('a silent poll does not clobber the desk when today fails to load', async () => {
    const h = loadDesk(fixture, { scrollY: SCROLL_AT_REST, failWith: ['/api/desk/today'] });
    await h.AppDesk.loadToday();
    assert.ok(h.container.innerHTML.includes('Failed to load Today desk'),
      'a non-silent failure should tell the user');
    const htmlAtFailure = h.container.innerHTML;
    await h.AppDesk.loadToday(true);
    assert.strictEqual(h.container.innerHTML, htmlAtFailure,
      'a silent failure overwrote the desk with an error page');
  });

  await test('the desk keeps the sub-tab the user is on', async () => {
    const h = loadDesk(fixture, { scrollY: 0 });
    await h.AppDesk.loadToday();
    h.AppDesk.setTodaySubTab('positions');
    await h.flush();
    const hasPositions = h.container.innerHTML.includes('ACTIVE OPEN POSITIONS');
    assert.ok(hasPositions, 'setTodaySubTab(positions) did not render the positions panel');
    await h.AppDesk.loadToday(true);
    assert.ok(h.container.innerHTML.includes('ACTIVE OPEN POSITIONS'),
      'the poll dropped the user back to the default sub-tab');
  });

  await test('ids survive a re-render so handlers keep resolving', async () => {
    const h = loadDesk(fixture, { scrollY: 0 });
    h.AppDesk._todaySubTab = 'positions';
    await h.AppDesk.loadToday();
    const before = idsIn(h.container.innerHTML);
    await h.AppDesk.loadToday(true);
    const after = idsIn(h.container.innerHTML);
    assert.deepStrictEqual(after, before, 'the poll changed the element ids the handlers bind to');
  });

  await test('a poll over the hostile payload patches live values without throwing', async () => {
    // Null fields, null array holes, every level: _patchLive is the path that runs twice a
    // minute, so it must survive the payloads that break rendering.
    const hostile = JSON.parse(fs.readFileSync(
      path.join(__dirname, 'fixtures', 'desk_loadtoday', 'hostile_nulls.json'), 'utf8'));
    const h = loadDesk(hostile, { scrollY: 0 });
    h.AppDesk._todaySubTab = 'radar';
    await h.AppDesk.loadToday();
    assert.strictEqual(h.container.writes, 1);

    const regime = h.elements.get('desk-regime-strip');
    const signal = h.elements.get('desk-bat-signal');
    const writes = h.container.writes;
    const err = [];
    const originalError = console.error;
    console.error = (...a) => err.push(a[0]);

    try {
      await h.AppDesk.loadToday(true);
    } finally {
      console.error = originalError;
    }

    assert.deepStrictEqual(err.map(String), [], 'the poll threw on the hostile payload');
    assert.strictEqual(h.container.writes, writes, 'the poll still touched the desk');
    assert.ok(regime && signal, 'the stable anchors were not rendered');
    // ...and it still moved the live values.
    assert.strictEqual(h.elements.get('desk-spy-px').innerText, '–');
    // 2 top_opportunities, one of them a null hole -> 1 real row, which is the point of _list().
    assert.strictEqual(h.elements.get('subtab-count-opps').innerText, '1');
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
