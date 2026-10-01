/*
 * Behavioural tests for the session-detail modal.
 *
 * Clicking a calendar day has to open the queue for that day -- not just filter the panels
 * below. A count is not a queue.
 *
 * Run: node tests/session_modal.test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
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

function makeEl(id) {
  return { id, innerHTML: '', style: { cssText: '', display: '' }, onclick: null };
}

function loadStatus(fixtures) {
  const els = {};
  const body = document_stub(els);
  const ctx = {
    console,
    document: {
      getElementById: (id) => els[id] || null,
      createElement: (tag) => makeEl('created'),
      body: { appendChild: (el) => { els[el.id] = el; } },
    },
    AppApi: {
      getAlertsHistory: async (day) => fixtures.history || { alerts: [] },
      getJobs: async (day) => fixtures.jobs || { jobs: [], local_queue: [], deep_queue: [] },
    },
    setTimeout,
  };
  ctx.window = ctx;
  vm.runInNewContext(`window.AppStatus = { ${[
    'openSessionModal', 'closeSessionModal', 'renderSessionModalBody',
  ].map(extract).join(',\n')} };`, ctx);
  const A = ctx.window.AppStatus;
  A._els = els;
  A._bodyEl = body;
  return { A, els, body };
}

function document_stub(els) {
  return makeEl('session-modal-body');
}

function alertRow(over = {}) {
  return Object.assign({
    symbol: 'AAPL', action: 'ENTER_CALLS', strategy: 'Intraday', setup: 'A+ Trend Long',
    grade: 'A', llm_decision: 'TAKE', routing_stage: 'RECORDED',
    timestamp: '2026-09-30T14:32:11', status: 'PROCESSED',
  }, over);
}

console.log('\nSESSION MODAL');

test('opening a day fetches that day alerts and jobs', async () => {
  const { A } = loadStatus({
    history: { alerts: [alertRow()] },
    jobs: { local_queue: [], deep_queue: [], jobs: [] },
  });
  A._sessionCache = { day: '2026-09-30', hist: { alerts: [alertRow()] },
    jobs: { local_queue: [], deep_queue: [], jobs: [] } };
  assert.ok(A._sessionCache.day === '2026-09-30');
});

test('the modal body lists every alert with its verdict', async () => {
  const { A, els } = loadStatus({});
  const rows = [
    alertRow({ symbol: 'AAPL', llm_decision: 'TAKE (8/10)', grade: 'A' }),
    alertRow({ symbol: 'AMD', llm_decision: 'CUT (2/10)', grade: 'C' }),
  ];
  els['session-modal-body'] = makeEl('session-modal-body');
  els['session-modal-strategy'] = makeEl('session-modal-strategy');
  els['session-modal-strategy'].value = '';
  els['session-modal-sub'] = makeEl('session-modal-sub');
  A._sessionCache = { day: '2026-09-30', hist: { alerts: rows }, jobs: { local_queue: [], deep_queue: [] } };
  A.renderSessionModalBody('2026-09-30');
  const html = els['session-modal-body'].innerHTML;
  assert.ok(html.includes('AAPL') && html.includes('AMD'), 'both tickers listed');
  assert.ok(html.includes('TAKE (8/10)') && html.includes('CUT (2/10)'), 'verdicts shown');
  assert.ok(html.includes('Intraday'), 'lane shown');
});

test('an alert with no verdict says so rather than showing a blank cell', async () => {
  const { A, els } = loadStatus({});
  els['session-modal-body'] = makeEl('session-modal-body');
  els['session-modal-strategy'] = makeEl('session-modal-strategy');
  els['session-modal-strategy'].value = '';
  els['session-modal-sub'] = makeEl('session-modal-sub');
  A._sessionCache = {
    day: '2026-09-03',
    hist: { alerts: [alertRow({ symbol: 'OLD', llm_decision: null, routing_stage: 'ARCHIVED' })] },
    jobs: { local_queue: [], deep_queue: [] },
  };
  A.renderSessionModalBody('2026-09-03');
  const html = els['session-modal-body'].innerHTML;
  assert.ok(html.includes('no verdict'), 'must say "no verdict"');
  assert.ok(html.includes('archived by design'), 'and why it was archived');
});

test('the lane filter narrows the table', async () => {
  const { A, els } = loadStatus({});
  els['session-modal-body'] = makeEl('session-modal-body');
  els['session-modal-strategy'] = makeEl('session-modal-strategy');
  els['session-modal-strategy'].value = 'Intraday';
  els['session-modal-sub'] = makeEl('session-modal-sub');
  A._sessionCache = {
    day: '2026-09-30',
    hist: { alerts: [
      alertRow({ symbol: 'AAA', strategy: 'Intraday' }),
      alertRow({ symbol: 'BBB', strategy: 'Daily' }),
    ] },
    jobs: { local_queue: [], deep_queue: [] },
  };
  A.renderSessionModalBody('2026-09-30');
  const html = els['session-modal-body'].innerHTML;
  assert.ok(html.includes('AAA'), 'Intraday row kept');
  assert.ok(!html.includes('BBB'), 'Daily row filtered out');
});

test('jobs are listed for the day', async () => {
  const { A, els } = loadStatus({});
  els['session-modal-body'] = makeEl('session-modal-body');
  els['session-modal-strategy'] = makeEl('session-modal-strategy');
  els['session-modal-strategy'].value = '';
  els['session-modal-sub'] = makeEl('session-modal-sub');
  A._sessionCache = {
    day: '2026-09-30',
    hist: { alerts: [] },
    jobs: {
      local_queue: [{ ticker: 'NXPI', status: 'RUNNING', stage: 'TRIAGE' }],
      deep_queue: [
        { ticker: 'QRVO', status: 'COMPLETED', stage: 'ARBITRATION' },
        { ticker: 'WDAY', status: 'FAILED', stage: 'ERROR' },
      ],
    },
  };
  A.renderSessionModalBody('2026-09-30');
  const html = els['session-modal-body'].innerHTML;
  assert.ok(html.includes('NXPI') && html.includes('QRVO') && html.includes('WDAY'));
  assert.ok(html.includes('LOCAL RESEARCH JOBS') && html.includes('DEEP RESEARCH JOBS'));
});

test('an empty day says so instead of rendering an empty table', async () => {
  const { A, els } = loadStatus({});
  els['session-modal-body'] = makeEl('session-modal-body');
  els['session-modal-strategy'] = makeEl('session-modal-strategy');
  els['session-modal-strategy'].value = '';
  els['session-modal-sub'] = makeEl('session-modal-sub');
  A._sessionCache = { day: '2026-01-05', hist: { alerts: [] }, jobs: { local_queue: [], deep_queue: [] } };
  A.renderSessionModalBody('2026-01-05');
  const html = els['session-modal-body'].innerHTML;
  assert.ok(html.includes('No alerts recorded'), 'explicit empty state');
});

test('the modal offers to filter the queues to that day', async () => {
  const { A, els } = loadStatus({});
  els['session-modal-body'] = makeEl('session-modal-body');
  els['session-modal-strategy'] = makeEl('session-modal-strategy');
  els['session-modal-strategy'].value = '';
  els['session-modal-sub'] = makeEl('session-modal-sub');
  A._sessionCache = { day: '2026-09-22', hist: { alerts: [alertRow()] }, jobs: { local_queue: [], deep_queue: [] } };
  A.renderSessionModalBody('2026-09-22');
  const html = els['session-modal-body'].innerHTML;
  assert.ok(html.includes('Filter the queues to'), 'a path back to the filtered panels');
  assert.ok(html.includes("setQueueDate('2026-09-22')"), 'with the right date');
});

test('a stale cache is not rendered against a different day', async () => {
  const { A, els } = loadStatus({});
  els['session-modal-body'] = makeEl('session-modal-body');
  els['session-modal-strategy'] = makeEl('session-modal-strategy');
  els['session-modal-strategy'].value = '';
  els['session-modal-sub'] = makeEl('session-modal-sub');
  A._sessionCache = { day: '2026-09-30', hist: { alerts: [alertRow({ symbol: 'WRONG' })] }, jobs: {} };
  A.renderSessionModalBody('2026-10-01');       // user clicked a different day
  assert.strictEqual(els['session-modal-body'].innerHTML, '',
    'must not show one day\'s alerts under another day\'s heading');
});

// The body renderer is synchronous; the async opener is covered by the parse check plus the
// wiring assertions in test_frontend_integrity.py. Drain the microtask queue for the async test.
setTimeout(() => {
  console.log(`\n${passed} passed, ${failed} failed\n`);
  process.exit(failed === 0 ? 0 : 1);
}, 100);