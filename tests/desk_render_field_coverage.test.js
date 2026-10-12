/*
 * FIELD COVERAGE: every field the desk render consumes must reach the HTML.
 *
 * This is the test that makes the render refactor safe. The characterization snapshots prove the
 * HTML did not change; this proves the payload still DRIVES the HTML. A field that stops being
 * rendered (a dropped column, a renamed key, a stale accessor) passes the snapshots and shows the
 * user a stale number forever -- which is exactly the class of bug nothing else in this repo
 * catches, because no test linked payload -> output.
 *
 * How: take the recorded happy-path payload, overwrite one field with a sentinel, render, and
 * assert the sentinel appears in the emitted HTML.
 *
 * Run: node tests/desk_render_field_coverage.test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const { loadDesk } = require('./helpers/desk_dom_harness');

const FIXTURE_PATH = path.join(__dirname, 'fixtures', 'desk_loadtoday', 'happy_opps.json');
const BASE = JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf8'));

/** [label, sub-tab, mutate(payload), sentinel that must appear in the HTML] */
const CASES = [
  // ---- market pulse strip -------------------------------------------------
  ['pulse.market_status_text', 'opps', (d) => { d.today.market_pulse.market_status_text = 'SENTINEL_STATUS'; }, 'SENTINEL_STATUS'],
  ['pulse.spy', 'opps', (d) => { d.today.market_pulse.spy = 4242.42; }, '$4242.42'],
  ['pulse.qqq', 'opps', (d) => { d.today.market_pulse.qqq = 909.11; }, '$909.11'],
  ['pulse.vix', 'opps', (d) => { d.today.market_pulse.vix = 27.77; }, '27.77'],
  ['pulse.market_open', 'opps', (d) => { d.today.market_pulse.market_open = false; d.today.market_pulse.market_phase = 'PRE_MARKET'; }, 'pill amber'],

  // ---- regime strip -------------------------------------------------------
  ['market_regime.as_of_et', 'opps', (d) => { d.today.market_regime.as_of_et = 'SENTINEL_ET'; }, 'SENTINEL_ET'],
  ['market_regime.fear_greed.score', 'opps', (d) => { d.today.market_regime.fear_greed.score = 77; }, '>77<span'],
  ['market_regime.fear_greed.rating', 'opps', (d) => { d.today.market_regime.fear_greed.rating = 'SENTINEL_RATING'; }, 'SENTINEL_RATING'],
  ['market_regime.fear_greed.flag', 'opps', (d) => { d.today.market_regime.fear_greed.flag = 'SENTINEL_FLAG — take'; }, 'SENTINEL_FLAG'],
  ['market_regime.vix.price', 'opps', (d) => { d.today.market_regime.vix.price = 19.19; }, '19.19'],
  ['market_regime.vix.band', 'opps', (d) => { d.today.market_regime.vix.band = 'SENTINEL_BAND — x'; }, 'SENTINEL_BAND'],
  ['market_regime.tide.trend', 'opps', (d) => { d.today.market_regime.tide.trend = 'SENTINEL_TIDE'; }, 'SENTINEL_TIDE'],
  ['market_regime.tide.spy', 'opps', (d) => { d.today.market_regime.tide.spy = 1234.5; }, '$1234.50'],
  ['market_regime.tide.ema20', 'opps', (d) => { d.today.market_regime.tide.ema20 = 1111.2; }, '$1111.20'],
  ['market_regime.tide.sma50', 'opps', (d) => { d.today.market_regime.tide.sma50 = 2222.3; }, '$2222.30'],
  ['market_regime.tide.spy_20d_return', 'opps', (d) => { d.today.market_regime.tide.spy_20d_return = 4.44; }, '+4.44%'],
  ['market_regime.breadth.universe', 'opps', (d) => { d.today.market_regime.breadth.universe = 1234; }, 'BREADTH (1234)'],
  ['market_regime.breadth.advancing', 'opps', (d) => { d.today.market_regime.breadth.advancing = 812; }, '▲ 812'],
  ['market_regime.breadth.declining', 'opps', (d) => { d.today.market_regime.breadth.declining = 121; }, '▼ 121'],
  ['market_regime.breadth.flat', 'opps', (d) => { d.today.market_regime.breadth.flat = 3; }, '▬ 3'],
  ['market_regime.movers.drops[].ticker', 'opps', (d) => { d.today.market_regime.movers.drops[0].ticker = 'DRPTICK'; }, 'DRPTICK'],
  ['market_regime.movers.drops[].chg_pct', 'opps', (d) => { d.today.market_regime.movers.drops[1].chg_pct = -9.9; }, '-9.9%'],
  ['market_regime.movers.spikes[].ticker', 'opps', (d) => { d.today.market_regime.movers.spikes[0].ticker = 'SPKTICK'; }, 'SPKTICK'],
  ['market_regime.movers.drops[].held', 'opps', (d) => { d.today.market_regime.movers.drops[1].held = true; }, 'HELD'],
  ['market_regime.movers.universe_size', 'opps', (d) => { d.today.market_regime.movers.universe_size = 555; }, '555 scanned'],
  ['market_regime.movers.alerts[].ticker', 'opps', (d) => { d.today.market_regime.movers.alerts[0].ticker = 'DRASTICK'; }, 'DRASTICK'],

  // ---- bat signal ---------------------------------------------------------
  ['action_panel.bat_signal.action', 'opps', (d) => { d.today.action_panel.bat_signal.action = 'SENTINEL_ACTION'; }, 'SENTINEL_ACTION'],
  ['action_panel.bat_signal.why', 'opps', (d) => { d.today.action_panel.bat_signal.why = 'SENTINEL_WHY'; }, 'SENTINEL_WHY'],
  ['action_panel.bat_signal.size', 'opps', (d) => { d.today.action_panel.bat_signal.size = 'SENTINEL_SIZE'; }, 'SENTINEL_SIZE'],
  ['action_panel.bat_signal.headline', 'opps', (d) => { d.today.action_panel.bat_signal.headline = 'SENTINEL_HEADLINE'; }, 'SENTINEL_HEADLINE'],
  ['action_panel.bat_signal.counts.in_zone', 'opps', (d) => { d.today.action_panel.bat_signal.counts.in_zone = 77; }, '>77</b>in zone'],
  ['action_panel.bat_signal.counts.coiling', 'opps', (d) => { d.today.action_panel.bat_signal.counts.coiling = 66; }, '>66</b>coiling'],
  ['action_panel.bat_signal.counts.held_in_zone', 'opps', (d) => { d.today.action_panel.bat_signal.counts.held_in_zone = 55; }, '55</b>held'],
  ['action_panel.bat_signal.counts.earnings_blackout', 'opps', (d) => { d.today.action_panel.bat_signal.counts.earnings_blackout = 44; }, '>44</b>earnings blackout'],
  ['action_panel.bat_signal.counts.open_positions', 'opps', (d) => { d.today.action_panel.bat_signal.counts.open_positions = 33; }, '>33</b>open monitored'],

  // ---- funnel + sub-tab badges -------------------------------------------
  ['funnel.alerts', 'opps', (d) => { d.today.funnel.alerts = 71; }, '>71</b>'],
  ['funnel.local_done', 'opps', (d) => { d.today.funnel.local_done = 72; }, '>72</b>'],
  ['funnel.local_pass', 'opps', (d) => { d.today.funnel.local_pass = 73; }, '>73</b>'],
  ['funnel.measured', 'opps', (d) => { d.today.funnel.measured = 74; }, '>74</b>'],
  ['funnel.deep_queued', 'opps', (d) => { d.today.funnel.deep_queued = 75; }, '>75</b>'],
  ['funnel.deep_done', 'opps', (d) => { d.today.funnel.deep_done = 76; }, '>76</b>'],
  ['funnel.actionable', 'opps', (d) => { d.today.funnel.actionable = 78; }, '>78</b>'],
  ['top_opportunities.length (opps badge)', 'opps', (d) => { d.today.top_opportunities.push({ ticker: 'SENTINELOPP', conviction: 1, priority_tier: 9, in_zone: false }); }, 'SENTINELOPP'],
  ['actionable_alerts.length (alerts badge + row)', 'alerts', (d) => { d.today.actionable_alerts.push({ symbol: 'SENTINELALERT', side: 'LONG', action: 'BUY' }); }, ['SENTINELALERT', '<span id="subtab-count-alerts">3</span>']],
  ['open_positions.length (positions badge + row)', 'positions', (d) => { d.feedback.open_positions.push({ ticker: 'SENTINELPOS' }); }, ['SENTINELPOS', 'ACTIVE OPEN POSITIONS (3)']],
  ['closed_positions.length (positions badge + row)', 'positions', (d) => { d.feedback.closed_positions.push({ trade_id: 'SENTINELTRADE', ticker: 'SENTINELCLOSED' }); }, ['SENTINELCLOSED', 'Evaluated Trades']],
  ['deep_cap_deferred.length (coverage badge + row)', 'coverage', (d) => { d.coverage.deep_cap_deferred.push({ ticker: 'SENTINELDEF', reason: 'r' }); }, ['SENTINELDEF', 'Deferred']],
  ['watch_no_deep.length (coverage badge + row)', 'coverage', (d) => { d.coverage.watch_no_deep.push({ ticker: 'SENTINELWND', reason: 'r' }); }, ['SENTINELWND', 'Stalking']],
  ['missed_moves.length (missed badge + row)', 'missed_moves', (d) => { d['missed-moves'].missed_moves.push({ ticker: 'SENTINELMM' }); }, ['SENTINELMM', 'Moves Analyzed']],
  // The radar badge renders '!' or '0' rather than a count, so only the row is assertable here.
  ['event_radar.length (radar row)', 'radar', (d) => { d.today.action_panel.event_radar.push({ ticker: 'SENTINELRAD', chg_pct: 1 }); }, ['SENTINELRAD', 'EVENT RADAR']],

  // ---- opportunity cards --------------------------------------------------
  ['top_opportunities[].ticker', 'opps', (d) => { d.today.top_opportunities[0].ticker = 'SENTINELTICK'; }, 'SENTINELTICK'],
  ['top_opportunities[].spot_price', 'opps', (d) => { d.today.top_opportunities[0].spot_price = 321.32; }, '$321.32'],
  ['top_opportunities[].side', 'opps', (d) => { d.today.top_opportunities[0].side = 'SENTINEL_SIDE'; }, 'SENTINEL_SIDE'],
  ['top_opportunities[].in_zone', 'opps', (d) => { d.today.top_opportunities[1].in_zone = true; }, '🎯 IN ZONE'],
  ['top_opportunities[].state_label', 'opps', (d) => { const o = d.today.top_opportunities[0]; o.state_label = 'SENTINEL_STATE'; o.in_zone = false; }, 'SENTINEL_STATE'],
  ['top_opportunities[].stage', 'opps', (d) => { d.today.top_opportunities[0].stage = 'SCREENER_COIL'; }, '⚡ SCREENER COIL'],
  ['top_opportunities[].ran_since_cut', 'opps', (d) => { d.today.top_opportunities[0].ran_since_cut = 'SENTINEL_RAN'; }, 'SENTINEL_RAN'],
  ['top_opportunities[].vintage', 'opps', (d) => { d.today.top_opportunities[0].vintage = 'SENTINEL_VINTAGE'; }, 'SENTINEL_VINTAGE'],
  ['top_opportunities[].conviction', 'opps', (d) => { d.today.top_opportunities[0].conviction = 9; }, 'Conviction: <b>9/10</b>'],
  ['top_opportunities[].score', 'opps', (d) => { d.today.top_opportunities[0].score = 93; }, 'Score: <b>93/100</b>'],
  ['top_opportunities[].entry_low', 'opps', (d) => { d.today.top_opportunities[0].entry_low = 151.15; }, '$151.15'],
  ['top_opportunities[].entry_high', 'opps', (d) => { d.today.top_opportunities[0].entry_high = 161.16; }, '$161.16'],
  ['top_opportunities[].tactical_stop', 'opps', (d) => { d.today.top_opportunities[0].tactical_stop = 141.14; }, '$141.14'],
  ['top_opportunities[].target_1', 'opps', (d) => { d.today.top_opportunities[0].target_1 = 171.17; }, '$171.17'],
  ['top_opportunities[].live_rr', 'opps', (d) => { d.today.top_opportunities[0].live_rr = 3.33; }, 'R:R 3.33:1'],
  ['top_opportunities[].vehicle_label', 'opps', (d) => { d.today.top_opportunities[0].vehicle_label = 'SENTINEL_VEHICLE'; }, 'SENTINEL_VEHICLE'],
  ['top_opportunities[].pm_bullets', 'opps', (d) => { d.today.top_opportunities[0].pm_bullets = ['SENTINEL_BULLET']; }, '• SENTINEL_BULLET'],
  ['top_opportunities[].wait_for', 'opps', (d) => { d.today.top_opportunities[0].wait_for = 'SENTINEL_WAIT'; }, 'Stalk Target Limit:</b> SENTINEL_WAIT'],
  ['top_opportunities[].pb_message', 'opps', (d) => { const o = d.today.top_opportunities[0]; o.pb_message = 'SENTINEL_PB'; o.wait_for = null; }, 'SENTINEL_PB'],
  ['top_opportunities[].gate_reasons', 'opps', (d) => { d.today.top_opportunities[0].gate_reasons = ['SENTINEL_GATE']; }, 'SENTINEL_GATE'],
  ['top_opportunities[].invalidation.rationale', 'opps', (d) => { d.today.top_opportunities[0].invalidation = { level: 1, rationale: 'SENTINEL_INVAL' }; }, 'SENTINEL_INVAL'],
  ['top_opportunities[].quote_source', 'opps', (d) => { d.today.top_opportunities[0].quote_source = 'TYPEDSOURCE'; }, 'typedsource'],
  ['top_opportunities[].report_date', 'opps', (d) => { d.today.top_opportunities[0].report_date = '2020-01-02'; }, '2020-01-02'],
  ['top_opportunities[].dist_pct', 'opps', (d) => { d.today.top_opportunities[1].dist_pct = 4.44; }, '+4.44%'],
  ['action_panel.risk_flags[ticker].flags[].label', 'opps', (d) => { d.today.action_panel.risk_flags.MSFT.flags[0].label = 'SENTINEL_FLAG'; }, 'SENTINEL_FLAG'],
  ['action_panel.risk_flags[ticker].flags[].note', 'opps', (d) => { d.today.action_panel.risk_flags.MSFT.flags[0].note = 'SENTINEL_NOTE'; }, 'SENTINEL_NOTE'],

  // ---- closest to gate ----------------------------------------------------
  ['closest_to_gate[].ticker', 'opps', (d) => { d.today.closest_to_gate[0].ticker = 'SENTINEL_CTG'; }, 'SENTINEL_CTG'],
  ['closest_to_gate[].missing_condition', 'opps', (d) => { d.today.closest_to_gate[0].missing_condition = 'SENTINEL_MISS'; }, 'SENTINEL_MISS'],
  ['closest_to_gate[].wait_for', 'opps', (d) => { d.today.closest_to_gate[0].wait_for = 'SENTINEL_WF'; }, '(SENTINEL_WF)'],
  ['closest_to_gate[].last_price', 'opps', (d) => { d.today.closest_to_gate[0].last_price = 333.33; }, '$333.33'],
  ['closest_to_gate[].dist', 'opps', (d) => { d.today.closest_to_gate[0].dist = 1.11; }, '+1.1%'],

  // ---- alerts sub-tab -----------------------------------------------------
  ['actionable_alerts[].symbol', 'alerts', (d) => { d.today.actionable_alerts[0].symbol = 'SENTINEL_SYM'; }, 'SENTINEL_SYM'],
  ['actionable_alerts[].action', 'alerts', (d) => { d.today.actionable_alerts[0].action = 'SENTINEL_ACTION_STR'; }, 'SENTINEL_ACTION_STR'],
  ['actionable_alerts[].strategy', 'alerts', (d) => { d.today.actionable_alerts[0].strategy = 'SENTINEL_STRAT'; }, 'SENTINEL_STRAT'],
  ['actionable_alerts[].state_label', 'alerts', (d) => { d.today.actionable_alerts[0].state_label = 'SENTINEL_ALSTATE'; }, 'SENTINEL_ALSTATE'],
  ['actionable_alerts[].entry_price', 'alerts', (d) => { d.today.actionable_alerts[0].entry_price = 100.1; }, '$100.10'],
  ['actionable_alerts[].spot_price', 'alerts', (d) => { d.today.actionable_alerts[0].spot_price = 200.2; }, '$200.20'],
  ['actionable_alerts[].stop_price', 'alerts', (d) => { d.today.actionable_alerts[0].stop_price = 300.3; }, '$300.30'],
  ['actionable_alerts[].target_price', 'alerts', (d) => { d.today.actionable_alerts[0].target_price = 400.4; }, '$400.40'],
  ['actionable_alerts[].live_rr', 'alerts', (d) => { d.today.actionable_alerts[0].live_rr = 4.4; }, '4.4:1'],
  ['actionable_alerts[].setup', 'alerts', (d) => { d.today.actionable_alerts[0].setup = 'SENTINEL_SETUP'; }, 'SENTINEL_SETUP'],
  ['actionable_alerts[].in_zone', 'alerts', (d) => { d.today.actionable_alerts[1].in_zone = true; }, '🎯 IN ZONE'],

  // ---- positions / feedback sub-tab --------------------------------------
  ['feedback.scorecard.win_rate_pct', 'positions', (d) => { d.feedback.scorecard.win_rate_pct = 71.5; }, '71.5%'],
  ['feedback.scorecard.wins', 'positions', (d) => { d.feedback.scorecard.wins = 22; }, '>22W'],
  ['feedback.scorecard.losses', 'positions', (d) => { d.feedback.scorecard.losses = 33; }, '/ 33L'],
  ['feedback.scorecard.total_realized_r', 'positions', (d) => { d.feedback.scorecard.total_realized_r = 12.5; }, '+12.5R'],
  ['feedback.scorecard.profit_factor', 'positions', (d) => { d.feedback.scorecard.profit_factor = 2.75; }, '>2.75</b>'],
  ['feedback.scorecard.expectancy', 'positions', (d) => { d.feedback.scorecard.expectancy = 0.44; }, '>0.44R/trade'],
  ['open_positions[].entry_price', 'positions', (d) => { d.feedback.open_positions[0].entry_price = 501.5; }, '$501.50'],
  ['open_positions[].spot_price', 'positions', (d) => { d.feedback.open_positions[0].spot_price = 502.5; }, '$502.50'],
  ['open_positions[].stop_loss', 'positions', (d) => { d.feedback.open_positions[0].stop_loss = 503.5; }, '$503.50'],
  ['open_positions[].target', 'positions', (d) => { d.feedback.open_positions[0].target = 504.5; }, '$504.50'],
  ['open_positions[].unrealized_r', 'positions', (d) => { d.feedback.open_positions[0].unrealized_r = 1.25; }, '+1.25R'],
  ['open_positions[].unrealized_pnl', 'positions', (d) => { d.feedback.open_positions[0].unrealized_pnl = 505.5; }, '+$505.50'],
  ['open_positions[].pnl_pct', 'positions', (d) => { d.feedback.open_positions[0].pnl_pct = 6.6; }, '(6.6%)'],
  ['open_positions[].scaled_at_t1', 'positions', (d) => { d.feedback.open_positions[1].scaled_at_t1 = true; }, '🎯 T1 Scaled (BE Locked)'],
  ['open_positions[].be_locked', 'positions', (d) => { d.feedback.open_positions[1].be_locked = true; }, '🛡️ BE Trailed'],
  ['closed_positions[].outcome', 'positions', (d) => { d.feedback.closed_positions[0].outcome = 'SENTINEL_OUTCOME'; }, 'SENTINEL_OUTCOME'],
  ['closed_positions[].realized_r', 'positions', (d) => { d.feedback.closed_positions[0].realized_r = 3.5; }, '+3.50R'],
  ['closed_positions[].exit_reason', 'positions', (d) => { d.feedback.closed_positions[0].exit_reason = 'SENTINEL_EXITWHY'; }, '(SENTINEL_EXITWHY)'],
  ['closed_positions[].exit_price', 'positions', (d) => { d.feedback.closed_positions[0].exit_price = 601.5; }, '$601.50'],
  ['closed_positions[].execution_grade', 'positions', (d) => { d.feedback.closed_positions[0].execution_grade = 'D'; }, 'Grade: D'],
  ['closed_positions[].diagnostic', 'positions', (d) => { d.feedback.closed_positions[0].diagnostic = 'SENTINEL_DIAG'; }, 'SENTINEL_DIAG'],
  ['closed_positions[].user_notes', 'positions', (d) => { d.feedback.closed_positions[0].user_notes = 'SENTINEL_NOTE_FB'; }, 'value="SENTINEL_NOTE_FB"'],
  ['closed_positions[].user_lesson', 'positions', (d) => { d.feedback.closed_positions[0].user_lesson = 'SENTINEL_LESSON'; }, 'value="SENTINEL_LESSON"'],
  ['closed_positions[].trade_id (fb form ids)', 'positions', (d) => { d.feedback.closed_positions[0].trade_id = 'SENTINEL_TRADEID'; }, 'fb-grade-SENTINEL_TRADEID'],

  // ---- coverage sub-tab ---------------------------------------------------
  ['coverage.deep_cap_deferred[].reason', 'coverage', (d) => { d.coverage.deep_cap_deferred[0].reason = 'SENTINEL_CAPREASON'; }, 'SENTINEL_CAPREASON'],
  ['coverage.watch_no_deep[].reason', 'coverage', (d) => { d.coverage.watch_no_deep[0].reason = 'SENTINEL_WNDREASON'; }, 'SENTINEL_WNDREASON'],
  ['coverage.alerts', 'coverage', (d) => { d.coverage.alerts = 81; }, '>81</div>'],
  ['coverage.local_done', 'coverage', (d) => { d.coverage.local_done = 82; }, '>82</div>'],
  ['coverage.local_pass', 'coverage', (d) => { d.coverage.local_pass = 83; }, '>83</div>'],

  // ---- missed moves sub-tab ----------------------------------------------
  ['missed_moves[].ticker', 'missed_moves', (d) => { d['missed-moves'].missed_moves[0].ticker = 'SENTINEL_MMTICK'; }, 'SENTINEL_MMTICK'],
  ['missed_moves[].move_pct', 'missed_moves', (d) => { d['missed-moves'].missed_moves[0].move_pct = 15.5; }, '15.5%'],
  ['missed_moves[].last_verdict', 'missed_moves', (d) => { d['missed-moves'].missed_moves[0].last_verdict = 'SENTINEL_VERDICT'; }, 'SENTINEL_VERDICT'],
  ['missed_moves[].lane', 'missed_moves', (d) => { d['missed-moves'].missed_moves[0].lane = 'SENTINEL_LANE'; }, 'SENTINEL_LANE'],
  ['missed_moves[].root_cause_reason', 'missed_moves', (d) => { d['missed-moves'].missed_moves[0].root_cause_reason = 'SENTINEL_RC'; }, 'SENTINEL_RC'],
  ['missed_moves[].details', 'missed_moves', (d) => { d['missed-moves'].missed_moves[0].details = 'SENTINEL_DETAILS'; }, 'SENTINEL_DETAILS'],
  ['weekly_rollup.stale_data', 'missed_moves', (d) => { d['missed-moves'].weekly_rollup.stale_data = 91; }, '>91</div>'],
  ['weekly_rollup.chased', 'missed_moves', (d) => { d['missed-moves'].weekly_rollup.chased = 92; }, '>92</div>'],
  ['weekly_rollup["no-PB"]', 'missed_moves', (d) => { d['missed-moves'].weekly_rollup['no-PB'] = 93; }, '>93</div>'],
  ['weekly_rollup.cap', 'missed_moves', (d) => { d['missed-moves'].weekly_rollup.cap = 94; }, '>94</div>'],
  ['weekly_rollup.no_setup', 'missed_moves', (d) => { d['missed-moves'].weekly_rollup.no_setup = 95; }, '>95</div>'],
  ['weekly_rollup.no_levels', 'missed_moves', (d) => { d['missed-moves'].weekly_rollup.no_levels = 96; }, '>96</div>'],

  // ---- radar sub-tab ------------------------------------------------------
  ['event_radar[].ticker', 'radar', (d) => { d.today.action_panel.event_radar[0].ticker = 'SENTINEL_ERTICK'; }, 'SENTINEL_ERTICK'],
  ['event_radar[].chg_pct', 'radar', (d) => { d.today.action_panel.event_radar[0].chg_pct = -7.7; }, '-7.7%'],
  ['event_radar[].price', 'radar', (d) => { d.today.action_panel.event_radar[0].price = 41.41; }, '$41.41'],
  ['event_radar[].volume', 'radar', (d) => { d.today.action_panel.event_radar[0].volume = 12345678; }, 'vol 12,345,678'],
  ['event_radar[].cause', 'radar', (d) => { d.today.action_panel.event_radar[0].cause = 'SENTINEL_CAUSE'; }, 'SENTINEL_CAUSE'],
  ['event_radar[].stance', 'radar', (d) => { d.today.action_panel.event_radar[0].stance = 'SENTINEL_STANCE'; }, 'SENTINEL_STANCE'],
  ['event_radar[].exposure.contract', 'radar', (d) => { d.today.action_panel.event_radar[0].exposure.contract = 'SENTINEL_LEG'; }, 'SENTINEL_LEG'],
  ['event_radar[].est_book_impact', 'radar', (d) => { d.today.action_panel.event_radar[0].est_book_impact = -1842; }, '-$1,842'],
  ['calibration.paper recent ledger', 'radar', (d) => { d.today.action_panel.calibration.paper.total = 88; }, '88'],
];

let passed = 0;
let failed = 0;

(async () => {
  console.log('\nDESK FIELD COVERAGE (payload -> HTML)');

  for (const [label, subtab, mutate, wanted] of CASES) {
    const sentinels = Array.isArray(wanted) ? wanted : [wanted];
    const data = JSON.parse(JSON.stringify(BASE));
    mutate(data);
    const h = loadDesk(data);
    h.AppDesk._todaySubTab = subtab;
    await h.AppDesk.loadToday();
    const html = h.container.innerHTML;
    const missing = sentinels.filter((s) => !html.includes(s));
    if (missing.length === 0) {
      passed += 1;
    } else {
      failed += 1;
      console.log(`  FAIL  ${label}: sentinel(s) ${missing.map((s) => JSON.stringify(s)).join(', ')} never reached the DOM`);
    }
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
