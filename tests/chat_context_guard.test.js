// Chat desk-context + streaming-state guards.
//
// The chat's board context is assembled from whichever desk is open, and each desk's data is
// optional: a feed can be missing, a desk can render before its first poll lands, and a
// position row can arrive with null fields. A single unguarded access takes the whole sidebar
// chat down (no board context, and a broken send path), so every builder is exercised here
// with hostile input and must degrade rather than throw.
//
// Run: node tests/chat_context_guard.test.js

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const chatSrc = fs.readFileSync(path.join(__dirname, '..', 'web', 'static', 'js', 'chat.js'), 'utf8');

function extract(name) {
  const i = chatSrc.indexOf('  ' + name + '(');
  assert.ok(i >= 0, `missing ${name}() in chat.js`);
  const j = chatSrc.indexOf('\n  },', i) + 5;
  return chatSrc.slice(i, j).trim().replace(/,\s*$/, '');
}

const methods = [
  'buildPortfolioBoardContext', 'buildDeskBoardContext', 'buildTodayDeskContext',
  'buildWatchlistDeskContext', 'buildIntradayDeskContext', 'deskLabel',
  'updatePortfolioModeBadge', '_paintRcStream', '_flushRcStreamRender', '_scheduleRcStreamRender',
].map(extract).join(',\n');

const load = (state, extra) => {
  global.window = {
    AppState: state, AppChat: {}, AppUtils: { renderMarkdown: (s) => String(s) },
    AppDesk: null, AppWatchlist: null, AppIntraday: null, AppPortfolio: null,
  };
  Object.assign(global.window, extra || {});
  global.document = { getElementById: () => null };
  return new Function(`const self = {${methods}}; return self;`)();
};

let pass = 0, fail = 0;
const run = (label, fn) => {
  try { fn(); pass++; console.log(`  ok    ${label}`); }
  catch (e) { fail++; console.log(`  FAIL  ${label}\n          ${e.message}`); }
};

console.log('\nDESK CONTEXT GUARDS');

run('portfolio: no AppPortfolio object', () => {
  assert.strictEqual(load({ currentDesk: 'portfolio' }).buildPortfolioBoardContext(), null);
});
run('portfolio: sub-objects all null', () => {
  const h = load({ currentDesk: 'portfolio' }, { AppPortfolio: { summaryData: null, analysisData: null, positionsData: [] } });
  assert.strictEqual(h.buildPortfolioBoardContext(), null);
});
run('portfolio: position rows with null fields', () => {
  const h = load({ currentDesk: 'portfolio' }, {
    AppPortfolio: {
      summaryData: { total_liquidation_value: null, total_day_pnl: null, total_day_pnl_pct: null, total_unrealized_pnl: null },
      analysisData: { allocation: {}, performance: {}, briefing: {}, recent_trades: null },
      positionsData: [{ symbol: 'X', asset_type: 'OPTION', quantity: null, average_price: null, current_price: null,
        market_value: null, option_strike: null, option_expiration: null }],
    },
  });
  const out = h.buildPortfolioBoardContext();
  assert.ok(out, 'expected a context object');
  assert.ok(out.context.length > 30, 'context too short');
  assert.ok(!out.context.includes('$null'), 'literal $null leaked into the context');
  assert.ok(!out.context.includes('undefined'), 'literal undefined leaked into the context');
});
run('portfolio: recent trades with null fields', () => {
  const h = load({ currentDesk: 'portfolio' }, {
    AppPortfolio: {
      summaryData: { total_liquidation_value: 10 },
      analysisData: { recent_trades: [{ date: null, category: null, order_type: null, legs: null, price: null }] },
      positionsData: [{ symbol: 'X', asset_type: 'EQUITY', quantity: 1, market_value: 1 }],
    },
  });
  const out = h.buildPortfolioBoardContext();
  assert.ok(out && !out.context.includes('$null'));
});
run('desk dispatch: unknown desk falls back', () => {
  const d = load({ currentDesk: 'nope' }).buildDeskBoardContext();
  assert.ok(d && d.context.includes('ACTIVE DESK'));
});
run('desk label: maps known desks', () => {
  const h = load({});
  global.window.AppState.currentDesk = 'swing';
  assert.strictEqual(h.deskLabel(), 'Swing Desk');
  global.window.AppState.currentDesk = 'today';
  assert.strictEqual(h.deskLabel(), 'Today');
  global.window.AppState.currentDesk = 'watchlist';
  assert.strictEqual(h.deskLabel(), 'Watchlist');
  global.window.AppState.currentDesk = 'intraday';
  assert.strictEqual(h.deskLabel(), 'Intraday');
  global.window.AppState.currentDesk = 'unknown-desk';
  assert.strictEqual(h.deskLabel(), 'Desk');
});
run('intraday: empty positions renders guidance', () => {
  const h = load({ currentDesk: 'intraday' }, { AppIntraday: { _rawPositions: [] } });
  const d = h.buildIntradayDeskContext();
  assert.ok(d && d.context.includes('no open intraday positions'));
});
run('watchlist: null target list', () => {
  const h = load({ currentDesk: 'watchlist' }, { AppWatchlist: { _allTargets: null } });
  assert.strictEqual(h.buildWatchlistDeskContext(), null);
});
run('watchlist: targets with null fields', () => {
  const h = load({ currentDesk: 'watchlist' }, {
    AppWatchlist: { _allTargets: [{ ticker: 'A', status: null, last_price: null, entry_zone_low: null }] },
  });
  const out = h.buildWatchlistDeskContext();
  assert.ok(out && !out.context.includes('$null'));
});
run('today: empty data returns null', () => {
  const h = load({ currentDesk: 'today' }, { AppDesk: { _todaySubTab: 'opps', _oppsFilter: 'ALL', _lastTodayData: {} } });
  assert.strictEqual(h.buildTodayDeskContext(), null);
});
run('today: pulse only, no opportunities', () => {
  const h = load({ currentDesk: 'today' }, { AppDesk: { _todaySubTab: 'opps', _oppsFilter: 'ALL' } });
  global.window.AppDesk._lastTodayData = { market_pulse: { spy: 1 }, top_opportunities: [], actionable_alerts: [] };
  const out = h.buildTodayDeskContext();
  assert.ok(out && out.context.includes('SPY'));
});
run('today: action panel with null members', () => {
  const h = load({ currentDesk: 'today' }, { AppDesk: { _todaySubTab: 'opps', _oppsFilter: 'ALL' } });
  global.window.AppDesk._lastTodayData = {
    market_pulse: { spy: 1 },
    top_opportunities: [{ ticker: 'A', side: null, spot_price: null, dist_pct: null, live_rr: null,
      conviction: null, priority_tier: null, vehicle_type: null, entry_zone_low: null,
      entry_zone_high: null, stop_loss: null, target_1: null, score: null }],
    actionable_alerts: [null],
    action_panel: { bat_signal: null, risk_flags: { A: null }, event_radar: [null], calibration: { real: null, coverage: null } },
  };
  h.buildTodayDeskContext();
});

console.log('\nSTREAMING STATE GUARDS');

run('paint: missing fullText does not throw', () => {
  const h = load({});
  const bubble = { innerHTML: '', querySelector: () => null, insertAdjacentHTML: () => {} };
  assert.doesNotThrow(() => h._paintRcStream({ bubble, title: 'T', t0: Date.now() }));
});
run('flush: empty text clears state', () => {
  const h = load({});
  h._rcStreamState = { bubble: null, fullText: '', title: '' };
  h._rcStreamRaf = null;
  h._flushRcStreamRender(null, '', '', false, true);
  assert.strictEqual(h._rcStreamState, null);
});
run('flush: cancelled partial answer renders', () => {
  const h = load({});
  const rendered = [];
  h._rcStreamState = null;
  h._flushRcStreamRender(
    { innerHTML: '', querySelector: () => null, insertAdjacentHTML: (p, html) => rendered.push(html) },
    'partial answer', 'REV CHAT', false, true,
  );
  assert.ok(rendered.join('').includes('partial answer'));
});
run('schedule: coalesces into a single timer', () => {
  const h = load({});
  let timers = 0;
  global.setTimeout = () => { timers++; return 1; };
  const bubble = { innerHTML: '', querySelector: () => null, insertAdjacentHTML: () => {} };
  h._rcStreamRaf = null;
  h._rcStreamState = null;
  h._scheduleRcStreamRender(bubble, 'a', 'T');
  h._scheduleRcStreamRender(bubble, 'ab', 'T');
  h._scheduleRcStreamRender(bubble, 'abc', 'T');
  assert.strictEqual(timers, 1, `expected 1 coalesced timer, got ${timers}`);
});

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
