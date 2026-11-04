// Source guards for the Decision tab (research_report.js) and the real-candle chart
// (trade_level_graph.js). Run: node tests/research_decision.test.js

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const rd = fs.readFileSync(path.join(__dirname, '..', 'web', 'static', 'js', 'research_report.js'), 'utf8');
const tlg = fs.readFileSync(path.join(__dirname, '..', 'web', 'static', 'js', 'trade_level_graph.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'research.html'), 'utf8');

// 1. Decision module fetches the structured report and real candles — never synthetic bars.
assert.ok(rd.includes('/report-json'), 'report-json endpoint not used');
assert.ok(rd.includes('/candles'), 'candles endpoint not used');
assert.ok(!/Math\.random|Math\.sin/.test(rd), 'synthetic price noise found in research_report.js');
assert.ok(!/_generateHistoricalBars|generateHistoricalBars/.test(tlg),
          'synthetic bar generator still present in trade_level_graph.js');
assert.ok(tlg.includes('/candles'), 'trade_level_graph.js does not fetch real candles');

// 2. All six decision codes are styled.
['BUY_NOW', 'BUY_ON_PULLBACK', 'WAIT', 'HOLD', 'SELL', 'AVOID'].forEach(k =>
  assert.ok(rd.includes(k), `decision code ${k} unhandled`));

// 3. All six horizons render.
['1W', '1M', '3M', '6M', '1Y', 'MULTI_YEAR'].forEach(k =>
  assert.ok(rd.includes(`'${k}'`), `horizon ${k} missing`));

// 4. Sections the plan requires are all rendered.
['scenarios_bbb', 'catalysts', 'horizons', 'quant', 'sources', 'wrong_if',
 'vs_alternatives', 'options_menu', 'audit'].forEach(k =>
  assert.ok(rd.includes(k), `report field ${k} not rendered`));

// 4b. Plan components exist: range toggle, context levels, earnings markers,
// scenario axis bar, horizon whisker, catalyst timeline, drawdown + earnings
// histogram, RS bars, freshness badge, kind badges, intraday out-of-scope.
['rd-range', 'context_levels', 'median_move', 'rd-whisk', 'drawdownSvg',
 'earningsHist', 'rsBar', 'rd-kind', 'INTRADAY', 'generated_at', 'rd-drawer'].forEach(k =>
  assert.ok(rd.includes(k), `decision component ${k} missing`));
assert.ok(rd.includes('sma(closes'), 'MA series not computed');

// 5. The tab is wired into research.html.
assert.ok(html.includes("id=\"tab-dec-btn\""), 'decision tab button missing');
assert.ok(html.includes("tab === 'dec'"), 'decision tab branch missing');
assert.ok(html.includes('research_report.js'), 'research_report.js not included');
assert.ok(html.includes('research_report.css'), 'research_report.css not included');
assert.ok(html.includes('ResearchDecision.render'), 'render call missing');
assert.ok(html.includes('has_report_json'), 'default-tab flag unused');

// 6. The empty state is honest — it must not render a fake report.
assert.ok(rd.includes('No Decision Report'), 'honest empty state missing');

console.log('research_decision tests: all assertions passed');
