/*
 * The sidebar chat must resume after a page change: its session id lives in localStorage and the
 * thread is reloaded from the server on load.
 *
 * Run: node tests/rev_chat_persist.test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'web', 'static', 'js', 'chat.js'), 'utf8');

function load(store, fetchImpl) {
  const win = { AppState: { revChatHistory: [] } };
  const ctx = {
    window: win,
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    document: { addEventListener() {}, getElementById: () => null },
    fetch: fetchImpl || (async () => ({ json: async () => ({ messages: [] }) })),
    console,
    setTimeout: (fn) => global.setTimeout(fn, 5),
  };
  ctx.window.window = win;
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx);
  return { chat: ctx.window.AppChat, win };
}

let failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok    ${name}`); } catch (e) { failed++; console.log(`  FAIL  ${name}\n          ${e.message}`); }
}

(async () => {
  await test('session id survives a page reload', () => {
    const store = {};
    load(store).chat._revSessionId = 'sess_REV_123';
    assert.strictEqual(load(store).chat._revSessionId, 'sess_REV_123');
  });

  await test('portfolio mode survives a page reload', () => {
    const store = {};
    load(store).chat._portfolioMode = true;
    assert.strictEqual(load(store).chat._portfolioMode, true);
  });

  await test('restoreRevChat reloads the thread from the server', async () => {
    const store = { rev_chat_sid: 'sess_REV_9' };
    const seen = [];
    const { chat, win } = load(store, async (url) => { seen.push(url); return { json: async () => ({ messages: [{ role: 'user', content: 'hi' }, { role: 'assistant', content: 'yo' }] }) }; });
    chat.renderRevChat = () => {};
    await chat.restoreRevChat();
    assert.ok(seen[0].includes('session_id=sess_REV_9'));
    assert.strictEqual(win.AppState.revChatHistory.length, 2);
  });

  await test('an unanswered question shows a placeholder, then the saved answer', async () => {
    const store = { rev_chat_sid: 'sess_REV_7' };
    let calls = 0;
    const { chat, win } = load(store, async () => {
      calls++;
      const msgs = [{ role: 'user', content: 'q' }];
      if (calls >= 2) msgs.push({ role: 'assistant', content: 'done' });
      return { json: async () => ({ messages: msgs }) };
    });
    chat.renderRevChat = () => {};
    await chat.restoreRevChat();
    assert.ok(win.AppState.revChatHistory.some((m) => m.pending));
    await new Promise((r) => global.setTimeout(r, 100));
    assert.ok(!win.AppState.revChatHistory.some((m) => m.pending));
    assert.strictEqual(win.AppState.revChatHistory[win.AppState.revChatHistory.length - 1].content, 'done');
  });

  await test('a new chat replaces the stored session id', () => {
    const store = { rev_chat_sid: 'sess_REV_old' };
    const { chat } = load(store);
    chat.renderRevChat = () => {};
    chat.startNewChat('sidebar');
    assert.notStrictEqual(store.rev_chat_sid, 'sess_REV_old');
  });

  await test('portfolio mode is false by default in a clean store', () => {
    const store = {};
    const { chat } = load(store);
    assert.strictEqual(chat._portfolioMode, false);
  });

  await test('togglePortfolioCopilot toggles mode on and off', () => {
    const store = {};
    const { chat } = load(store);
    chat.renderRevChat = () => {};
    assert.strictEqual(chat._portfolioMode, false);
    chat.togglePortfolioCopilot();
    assert.strictEqual(chat._portfolioMode, true);
    assert.strictEqual(store.rev_chat_portfolio, '1');
    chat.togglePortfolioCopilot();
    assert.strictEqual(chat._portfolioMode, false);
    assert.strictEqual(store.rev_chat_portfolio, undefined);
  });

  console.log(failed ? `\n${failed} failed` : '\nall passed');
  process.exit(failed ? 1 : 0);
})();

