/*
 * CHARACTERIZATION: the exact HTML `loadToday()` writes into #today-content-container.
 *
 * This file exists to pin today's behaviour before the render commit is refactored
 * (plan §1.1). It loads the REAL desk.js into a stub DOM -- no extraction, no re-implementation
 * -- feeds it recorded API payloads, and compares the emitted HTML byte-for-byte against the
 * committed snapshot in tests/__snapshots__/desk_loadtoday/.
 *
 * What it pins, per fixture:
 *   * the emitted HTML, byte-identical
 *   * `_lastTodayData` identity (a payload rebuild shows up here first)
 *   * which memoRender keys were touched, and how often each renderer actually ran
 *   * how many times the container's innerHTML was assigned across a full + a poll cycle
 *   * that the window scroll position survives the swap
 *
 * The companion tests/desk_render_field_coverage.test.js asserts payload -> HTML, i.e. that no
 * consumed field silently stops rendering. Together: characterise, then rewrite.
 *
 * Run:        node tests/desk_loadtoday_characterization.test.js
 * Re-record:  node tests/desk_loadtoday_characterization.test.js --update
 */
'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const { loadDesk, ENDPOINTS, FULL_REQUESTS } = require('./helpers/desk_dom_harness');

const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'desk_loadtoday');
const SNAPSHOT_DIR = path.join(__dirname, '__snapshots__', 'desk_loadtoday');

const UPDATE = process.argv.includes('--update');

/**
 * How many innerHTML writes each call performs today:
 *   full (controller reload) -> 1, the whole string in one assignment
 *   silent (6s poll)         -> 0, _patchLive writes three live areas instead
 * That asymmetry is the whole point of the refactor these snapshots guard.
 */
const INNERHTML_WRITES_PER_FULL_CALL = 1;
const INNERHTML_WRITES_PER_POLL_CALL = 0;

const scenarios = [
  { name: 'happy_opps', fixture: 'happy_opps' },
  { name: 'opps_filter_in_zone', fixture: 'happy_opps', filter: 'IN_ZONE' },
  { name: 'opps_filter_coiling', fixture: 'happy_opps', filter: 'COILING' },
  { name: 'opps_no_match', fixture: 'happy_opps', filter: 'ARMED' },
  { name: 'alerts_tab', fixture: 'happy_opps', subtab: 'alerts' },
  { name: 'positions_tab', fixture: 'happy_opps', subtab: 'positions' },
  { name: 'coverage_tab', fixture: 'happy_opps', subtab: 'coverage' },
  { name: 'missed_moves_tab', fixture: 'happy_opps', subtab: 'missed_moves' },
  { name: 'radar_tab', fixture: 'happy_opps', subtab: 'radar' },
  { name: 'hostile_nulls', fixture: 'hostile_nulls' },
  { name: 'fetch_failure', fixture: 'happy_opps', failWith: ['/api/desk/coverage', '/api/desk/missed-moves'] },
];

const fixtureCache = {};
function readFixture(name) {
  if (!fixtureCache[name]) {
    fixtureCache[name] = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, `${name}.json`), 'utf8'));
  }
  return fixtureCache[name];
}

async function render(scenario, fixture) {
  const h = loadDesk(fixture, { failWith: scenario.failWith, scrollY: 1234 });
  if (scenario.subtab) h.AppDesk._todaySubTab = scenario.subtab;
  if (scenario.filter) h.AppDesk._oppsFilter = scenario.filter;

  await h.AppDesk.loadToday();
  const writesAfterFirst = h.container.writes;
  const htmlAfterFirst = h.container.innerHTML;
  const firstCallRequests = h.requests.slice();

  // The 6s poll path. Same payload -> the memoised blocks must not re-render.
  await h.AppDesk.loadToday(true);
  const writesAfterSecond = h.container.writes;

  return { h, writesAfterFirst, writesAfterSecond, htmlAfterFirst, firstCallRequests };
}

let passed = 0;
let failed = 0;
const failures = [];

function check(label, fn) {
  try {
    fn();
    passed += 1;
    console.log(`  ok    ${label}`);
  } catch (e) {
    failed += 1;
    const first = String(e.message).split('\n')[0];
    failures.push(`${label}: ${first}`);
    console.log(`  FAIL  ${label}\n          ${first}`);
  }
}

(async () => {
  if (UPDATE && !fs.existsSync(SNAPSHOT_DIR)) fs.mkdirSync(SNAPSHOT_DIR, { recursive: true });

  console.log('\nDESK loadToday CHARACTERIZATION');

  for (const scenario of scenarios) {
    const fixture = readFixture(scenario.fixture);
    const snapshotPath = path.join(SNAPSHOT_DIR, `${scenario.name}.html`);
    const { h, writesAfterFirst, writesAfterSecond, firstCallRequests } = await render(scenario, fixture);
    const todayFailed = (scenario.failWith || []).includes('/api/desk/today');

    check(`${scenario.name}: fired the desk requests a controller reload needs`, () => {
      // Briefing and feedback-loop are fetched only while their own sub-tab is open.
      const expected = FULL_REQUESTS.concat(scenario.subtab === 'positions' ? ['/api/desk/feedback-loop'] : [],
        scenario.subtab === 'briefing' ? ['/api/desk/morning-briefing'] : []);
      assert.deepStrictEqual(firstCallRequests, expected);
      // Every request must be answerable from the fixture map, or a fixture edit goes unread.
      for (const url of expected) assert.ok(ENDPOINTS[url], `no fixture key for ${url}`);
    });

    check(`${scenario.name}: HTML is byte-identical to the snapshot`, () => {
      if (UPDATE) {
        fs.writeFileSync(snapshotPath, h.container.innerHTML);
        return;
      }
      const expected = fs.readFileSync(snapshotPath, 'utf8');
      if (expected !== h.container.innerHTML) {
        const at = [...expected].findIndex((c, i) => c !== h.container.innerHTML[i]);
        throw new Error(
          `snapshot drift at char ${at}\n` +
          `  expected: ${JSON.stringify(expected.slice(Math.max(0, at - 60), at + 60))}\n` +
          `  actual:   ${JSON.stringify(h.container.innerHTML.slice(Math.max(0, at - 60), at + 60))}\n` +
          `  re-record with: node tests/desk_loadtoday_characterization.test.js --update`
        );
      }
    });

    check(`${scenario.name}: _lastTodayData keeps the payload identity`, () => {
      if (todayFailed) return;
      assert.strictEqual(h.AppDesk._lastTodayData, fixture.today);
    });

    check(`${scenario.name}: the poll never touches the container at all`, () => {
      assert.strictEqual(writesAfterFirst, INNERHTML_WRITES_PER_FULL_CALL,
        `the controller reload wrote ${writesAfterFirst} times`);
      // A silent poll that hit a fetch failure writes nothing either: the error path refuses to
      // blow away the desk the user is reading.
      const expectedTotal = todayFailed ? INNERHTML_WRITES_PER_FULL_CALL
        : INNERHTML_WRITES_PER_FULL_CALL + INNERHTML_WRITES_PER_POLL_CALL;
      assert.strictEqual(writesAfterSecond, expectedTotal,
        `a full load plus a poll wrote ${writesAfterSecond} times in total`);
    });

    check(`${scenario.name}: memoised regime + bat signal render once for one payload`, () => {
      if (!todayFailed) {
        assert.ok(h.memoKeys.includes('regime_strip'), `memo keys were ${h.memoKeys}`);
        assert.ok(h.memoKeys.includes('bat_signal'), `memo keys were ${h.memoKeys}`);
      }
      // Two polls with one payload: each renderer runs at most once.
      assert.ok(h.rendererCalls.renderRegimeStrip <= 1,
        `renderRegimeStrip ran ${h.rendererCalls.renderRegimeStrip}x for one payload`);
      assert.ok(h.rendererCalls.renderBatSignal <= 1,
        `renderBatSignal ran ${h.rendererCalls.renderBatSignal}x for one payload`);
    });

    check(`${scenario.name}: window scroll is preserved across the swap`, () => {
      assert.strictEqual(h.windowStub.scrollY, 1234, 'window.scrollY was not restored');
      assert.ok(h.scrollCalls.some((c) => c[1] === 1234), 'scrollTo was never called with the saved position');
    });

    check(`${scenario.name}: radar memo key only on the radar tab`, () => {
      const radarRuns = h.rendererCalls.renderRadarAndCalibration;
      if (scenario.subtab === 'radar') assert.ok(radarRuns >= 1, 'radar subtab never rendered the radar/calibration block');
      else assert.strictEqual(radarRuns, 0, `radar block rendered ${radarRuns}x outside the radar tab`);
    });
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failures.length) {
    console.log('\nFAILURES:');
    failures.forEach((f) => console.log(`  - ${f}`));
  }
  process.exit(failed ? 1 : 0);
})();
