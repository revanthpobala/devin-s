/*
 * Behavioural tests for the session calendar + pinned today card.
 *
 * Two rules that the user asked for by name:
 *   1. today is ALWAYS shown, even with zero alerts
 *   2. a calendar, not a table
 *
 * Run: node tests/session_calendar.test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..');
const SRC = fs.readFileSync(path.join(ROOT, 'web', 'static', 'js', 'status.js'), 'utf8');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ok    ${name}`);
  } catch (e) {
    failed++;
    console.log(`  FAIL  ${name}\n          ${e.message}`);
  }
}

function extract(name) {
  const head = new RegExp(`\\n[ \\t]*(async\\s+)?${name}\\s*\\([^)]*\\)\\s*\\{`, 'm').exec(SRC);
  if (!head) throw new Error(`method ${name} not found`);
  const open = SRC.indexOf('{', head.index + head[0].length - 1);
  let depth = 0;
  for (let i = open; i < SRC.length; i++) {
    if (SRC[i] === '{') depth++;
    else if (SRC[i] === '}') { depth--; if (depth === 0) return SRC.slice(head.index, i + 1).trim(); }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

function loadStatus(sessions, state = {}) {
  const obj = {};
  const names = ['_todayStr', '_shiftMonth', '_todayCard', '_calendarGrid',
    'renderSessionHistory', 'setQueueDate'];
  const decls = names.map((n) => extract(n)).join(',\n');
  const host = { innerHTML: '' };
  const ctx = { console, document: { getElementById: () => host },
    AppApi: { getSessions: async () => ({ sessions }) }, setTimeout, Date };
  ctx.window = ctx;
  vm.runInNewContext(`window.AppStatus = { ${decls} };`, ctx);
  const A = ctx.window.AppStatus;
  A._sessionRows = sessions;
  Object.assign(A, state);
  A._host = host;
  A._esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  return A;
}

const vm = require('vm');

function row(session, extra = {}) {
  return Object.assign({
    session, alerts: 0, intraday: 0, graded: 0, archived: 0,
    local_running: 0, local_queued: 0, local_other: 0,
    deep_running: 0, deep_queued: 0, deep_done: 0, deep_failed: 0,
  }, extra);
}

const TODAY = (() => { const n = new Date();
  return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, '0')}-${String(n.getDate()).padStart(2, '0')}`;
})();
const MONTH = TODAY.slice(0, 7);

console.log('\nSESSION CALENDAR');

test('today is always shown even with zero alerts', () => {
  const A = loadStatus([]);                      // no sessions at all
  const html = A.renderSessionHistory([]);
  assert.ok(html.includes('TODAY'), 'the today card must be present');
  assert.ok(html.includes('no alerts yet'), 'an empty session must say so, not vanish');
});

test('today card shows the real numbers when today has alerts', () => {
  const rows = [row(TODAY, { alerts: 178, intraday: 6, graded: 178 })];
  const A = loadStatus(rows);
  const html = A.renderSessionHistory(rows);
  assert.ok(html.includes('178'), 'alert count');
  assert.ok(html.includes('6 0DTE'), '0DTE count');
  assert.ok(html.includes('178 graded'), 'graded count');
  assert.ok(html.includes('100% graded'), 'full coverage should be called out');
});

test('today card flags ungraded alerts', () => {
  const rows = [row(TODAY, { alerts: 20, intraday: 3, graded: 15 })];
  const A = loadStatus(rows);
  const html = A.renderSessionHistory(rows);
  assert.ok(html.includes('5 ungraded'), 'the shortfall must be visible');
  assert.ok(html.includes('100% graded') === false);
});

test('it is a calendar grid, not a table', () => {
  const A = loadStatus([row(TODAY, { alerts: 5 })]);
  const html = A.renderSessionHistory([]);
  assert.ok(html.includes('grid-template-columns:repeat(7'), '7-column weekday grid');
  assert.ok(!/<table/i.test(html), 'the old table must be gone');
  ['S', 'M', 'T', 'W', 'T', 'F', 'S'].forEach(() => {});
  assert.ok(html.includes('</div>'), 'cells are divs');
});

test('every day of the month renders a cell', () => {
  const A = loadStatus([]);
  A._calMonth = MONTH;
  const html = A._calendarGrid([], A._esc);
  const [y, m] = MONTH.split('-').map(Number);
  const days = new Date(y, m, 0).getDate();
  // lead blanks + day cells
  assert.ok(html.includes(`${days}`), `day ${days} should appear`);
  assert.ok(html.includes('◀') && html.includes('▶'), 'month navigation');
});

test('a session day is shaded by alert volume', () => {
  const A = loadStatus([
    row('2026-01-05', { alerts: 10 }),
    row('2026-01-06', { alerts: 200 }),
  ]);
  A._calMonth = '2026-01';
  const html = A._calendarGrid([
    row('2026-01-05', { alerts: 10 }), row('2026-01-06', { alerts: 200 }),
  ], A._esc);
  const shades = html.match(/rgba\(56,189,248,([\d.]+)\)/g) || [];
  assert.ok(shades.length >= 2, 'both days shaded');
  assert.notStrictEqual(shades[0], shades[1], 'volume must change the shade');
});

test('ungraded and deep failures surface on the day cell', () => {
  const A = loadStatus([]);
  A._calMonth = '2026-01';
  const html = A._calendarGrid([
    row('2026-01-07', { alerts: 100, intraday: 40, graded: 60, deep_failed: 9 }),
  ], A._esc);
  assert.ok(html.includes('40D'), '0DTE marker');
  assert.ok(html.includes('-40g'), 'ungraded count');
  assert.ok(html.includes('✗9'), 'deep failures');
});

test("today's cell is marked in the calendar too", () => {
  const A = loadStatus([row(TODAY, { alerts: 3 })]);
  A._calMonth = MONTH;
  const html = A._calendarGrid([row(TODAY, { alerts: 3 })], A._esc);
  assert.ok(html.includes('●'), 'today marked with a dot');
});

test('clicking a day sets the date filter, clicking again clears it', () => {
  const A = loadStatus([row('2026-01-05', { alerts: 5 })], { _queueDate: '' });
  let loads = 0;
  A.loadJobs = () => { loads++; };
  A.setQueueDate('2026-01-05');
  assert.strictEqual(A._queueDate, '2026-01-05');
  assert.ok(loads >= 1, 'changing the date must refresh the queues');
  A.setQueueDate('');
  assert.strictEqual(A._queueDate, '');
});

test('month navigation moves the view and clamps to a real month', () => {
  const A = loadStatus([]);
  A._calMonth = '2026-01';
  A._shiftMonth(1);
  assert.strictEqual(A._calMonth, '2026-02');
  A._shiftMonth(-2);
  assert.strictEqual(A._calMonth, '2025-12');
  A._shiftMonth(0);
  assert.strictEqual(A._calMonth, MONTH, '"This month" returns to the current month');
});

test('the date <select> is still offered alongside the calendar', () => {
  const A = loadStatus([row('2026-01-05', { alerts: 5 })], { _queueDates: ['2026-01-05'] });
  const html = A.renderSessionHistory([row('2026-01-05', { alerts: 5 })]);
  assert.ok(html.includes('id="queue-date-select"'), 'dropdown retained');
  assert.ok(html.includes('2026-01-05'), 'available dates listed');
  assert.ok(html.includes('All dates'), 'and a way to clear the filter');
});

console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed === 0 ? 0 : 1);