// Hostile-data guards for the Today desk's render layer.
//
// The regime / bat-signal / calibration panels are all fed by optional, failure-tolerant
// services: any one of them can be missing, null, or half-populated when the desk renders
// (a feed down, a first paint before the poll lands, an empty calibration). These tests
// assert that every renderer degrades to a safe empty string or partial UI instead of
// throwing, because a single unguarded `.toFixed()` in one card takes the whole desk down.
//
// Run: node tests/desk_render_guard.test.js

const fs = require('fs');
const assert = require('assert');

const deskSrc = fs.readFileSync(require('path').join(__dirname, '..', 'web', 'static', 'js', 'desk.js'), 'utf8');
const chatSrc = fs.readFileSync(require('path').join(__dirname, '..', 'web', 'static', 'js', 'chat.js'), 'utf8');

function extract(s, name) {
  const i = s.indexOf('  ' + name + '(');
  assert.ok(i >= 0, `method not found in source: ${name}()`);
  const j = s.indexOf('\n  },', i) + 5;
  return s.slice(i, j).trim().replace(/,\s*$/, '');
}

const methods = [
  extract(deskSrc, 'renderBatSignal'),
  extract(deskSrc, 'renderRegimeStrip'),
  extract(deskSrc, 'renderRadarAndCalibration'),
].join(',\n');

const loadDesk = () =>
  new Function('window', 'document', 'AppChat',
    `const self = {${methods}}; return self;`)(
    { AppChat: { askRevChat: () => {} } },
    { getElementById: () => null },
    { askRevChat: () => {} },
  );

let pass = 0, fail = 0;
const run = (label, fn) => {
  try {
    fn();
    pass++;
    console.log(`  ok    ${label}`);
  } catch (e) {
    fail++;
    console.log(`  FAIL  ${label}\n          ${e.message}`);
  }
};

console.log('\nDESK RENDER GUARDS');

run('bat signal: empty panel', () => assert.strictEqual(loadDesk().renderBatSignal({}), ''));
run('bat signal: null bat_signal', () => assert.strictEqual(loadDesk().renderBatSignal({ bat_signal: null }), ''));
run('bat signal: missing counts', () => loadDesk().renderBatSignal({ bat_signal: { action: 'WAIT', tone: 'green' } }));
run('bat signal: unknown tone falls back', () => loadDesk().renderBatSignal({ bat_signal: { action: 'X', tone: 'magenta', counts: {} } }));

run('regime: null', () => assert.strictEqual(loadDesk().renderRegimeStrip(null), ''));
run('regime: unavailable', () => assert.strictEqual(loadDesk().renderRegimeStrip({ available: false }), ''));
run('regime: fear&greed with null score/rating/flag', () =>
  loadDesk().renderRegimeStrip({ available: true, fear_greed: { score: null, rating: null, flag: null } }));
run('regime: vix null price/band', () =>
  loadDesk().renderRegimeStrip({ available: true, vix: { price: null, band: null }, fear_greed: null }));
run('regime: tide with all-null numbers', () =>
  loadDesk().renderRegimeStrip({ available: true, tide: { spy: null, ema20: null, sma50: null, trend: null, is_bullish: false } }));
run('regime: breadth with zero universe', () =>
  loadDesk().renderRegimeStrip({ available: true, breadth: { universe: 0 } }));
run('regime: breadth with null counters', () =>
  loadDesk().renderRegimeStrip({ available: true, breadth: { universe: 10, advancing: null, declining: null, flat: null } }));
run('regime: movers with null fields', () =>
  loadDesk().renderRegimeStrip({
    available: true,
    movers: { drops: [{}], spikes: [{ ticker: 'X', chg_pct: null, price: null, volume: null, drastic: true, held: false }] },
  }));
run('regime: drastic list containing nulls', () =>
  loadDesk().renderRegimeStrip({ available: true, movers: { alerts: [{ ticker: 'X', chg_pct: null }, null] } }));

run('radar+calib: empty panel', () => loadDesk().renderRadarAndCalibration({}));
run('radar+calib: nulls everywhere', () =>
  loadDesk().renderRadarAndCalibration({
    event_radar: null,
    calibration: { real: null, model: null, paper: null, coverage: null, reasoning: null, unmeasured_lanes: null },
  }));
run('radar+calib: empty objects', () =>
  loadDesk().renderRadarAndCalibration({
    event_radar: [],
    calibration: { real: {}, model: {}, paper: { aggregate: {} }, coverage: {}, reasoning: [], unmeasured_lanes: [] },
  }));
run('radar+calib: real pool with null metrics', () =>
  loadDesk().renderRadarAndCalibration({
    calibration: {
      real: { scored: 1, win_rate_pct: null, breakeven_win_rate_pct: null, profit_factor: null, expectancy: null, total_realized_r: null },
    },
  }));
run('radar+calib: paper pool with null metrics', () =>
  loadDesk().renderRadarAndCalibration({
    calibration: {
      paper: {
        open: 2,
        aggregate: {
          resolved: null, thesis_correct_rate_pct: null, avg_r: null, profit_factor: null,
          target_hit: null, stop_hit: null, avg_mfe_pct: null, avg_mae_pct: null, total_pnl_dollars: null,
        },
      },
    },
  }));
run('radar+calib: recent paper rows with nulls', () =>
  loadDesk().renderRadarAndCalibration({
    calibration: {
      paper: {
        open: 0,
        aggregate: { resolved: 1 },
        recent: [{
          ticker: 'A', side: null, thesis_spot: null, exit_spot: null,
          pnl_dollars: null, r_multiple: null, outcome: null, created_date: null, resolved_at: null,
        }],
      },
    },
  }));
run('radar+calib: coverage with nulls', () =>
  loadDesk().renderRadarAndCalibration({
    calibration: {
      coverage: { available: true, closed_total: 10, with_exit_price: null, exit_coverage_pct: null, stop_coverage_pct: null, worst_months: null },
    },
  }));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
