/*
 * Behavioural tests for REV CHAT focus.
 *
 * These load the real focus functions out of chat.js and run them against a stub DOM, because the
 * bug was behavioural: clearing could not be expressed, so the focus appeared stuck.
 *
 * Run: node tests/rev_chat_focus.test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..');
const CHAT_SRC = fs.readFileSync(path.join(ROOT, 'web', 'static', 'js', 'chat.js'), 'utf8');
const INTRADAY_SRC = fs.readFileSync(path.join(ROOT, 'web', 'static', 'js', 'intraday.js'), 'utf8');
const SWING_SRC = fs.readFileSync(path.join(ROOT, 'web', 'static', 'js', 'swing.js'), 'utf8');

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

/** Pull a named method's full source out of chat.js by brace matching. */
function extractMethod(src, name) {
  const head = new RegExp(`\\n[ \\t]*(async\\s+)?${name}\\s*\\([^)]*\\)\\s*\\{`, 'm').exec(src);
  if (!head) throw new Error(`method ${name} not found`);
  const open = src.indexOf('{', head.index + head[0].length - 1);
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.slice(head.index, i + 1).trim();
    }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

function loadChat(extraDom = {}) {
  const elements = Object.assign({
    'rev-chat-focus-badge': { innerText: '', className: '', title: '' },
    'rev-chat-focus-input': { value: '' },
    'ticker-input': { value: 'WMT' },
  }, extraDom);

  const state = { revChatFocusTicker: '', activeChatTicker: '', currentReportData: null };

  const document = { getElementById: (id) => (id in elements ? elements[id] : null) };
  const window = { AppState: state };
  const ctx = { window, document, console, setTimeout, clearTimeout };
  ctx.globalThis = ctx;
  vm.createContext(ctx);

  // Rebuild the focus helpers as a plain object literal. Shorthand method syntax is already
  // valid as a property value, so no `name:` prefix.
  const names = ['_focusTicker', '_syncSidebarFocusInput', 'setRevChatFocus',
    'clearRevChatFocus', 'clearSidebarFocus', 'updateSidebarFocusBadge',
    'applySidebarFocusInput'];
  const decls = names.map((n) => extractMethod(CHAT_SRC, n)).join(',\n');
  vm.runInContext(`window.AppChat = { ${decls} }; globalThis.AppChat = window.AppChat;`, ctx);

  return { AppChat: ctx.AppChat, state, elements, document, ctx };
}

const badgeText = (h) => h.elements['rev-chat-focus-badge'].innerText;

console.log('\nREV CHAT focus');

test('defaults to Market with nothing focused', () => {
  const h = loadChat();
  h.AppChat.updateSidebarFocusBadge();
  assert.strictEqual(badgeText(h), '🎯 Focus: Market');
});

test('setting focus shows the ticker', () => {
  const h = loadChat();
  h.AppChat.setRevChatFocus('glw');
  assert.strictEqual(badgeText(h), '🎯 Focus: $GLW', badgeText(h));
  assert.strictEqual(h.state.revChatFocusTicker, 'GLW');
});

test('CLEAR STICKS even when a report is open', () => {
  // The reported bug: currentReportData leaked the ticker straight back into the badge.
  const h = loadChat();
  h.state.currentReportData = { ticker: 'GLW' };
  h.AppChat.setRevChatFocus('GLW');
  assert.strictEqual(badgeText(h), '🎯 Focus: $GLW');

  h.AppChat.clearSidebarFocus();
  assert.strictEqual(badgeText(h), '🎯 Focus: Market',
    'clear was undone by currentReportData: ' + badgeText(h));
  assert.strictEqual(h.state.revChatFocusTicker, '');
  assert.strictEqual(h.state.activeChatTicker, '');
});

test('CLEAR STICKS even when #ticker-input holds a value', () => {
  // #ticker-input defaults to "WMT" (the research launcher), so a fallback to it made
  // the badge show $WMT in broad-market mode.
  const h = loadChat();
  h.elements['ticker-input'].value = 'WMT';
  h.AppChat.clearSidebarFocus();
  assert.strictEqual(h.elements['ticker-input'].value, '');
  assert.strictEqual(badgeText(h), '🎯 Focus: Market', badgeText(h));
});

test('a cleared focus is not resurrected by the send path', () => {
  const h = loadChat();
  h.state.currentReportData = { ticker: 'GLW' };
  h.elements['ticker-input'].value = 'GLW';
  h.AppChat.clearSidebarFocus();

  // Reproduce the send-path resolution from chat.js.
  let activeTicker = h.AppChat._focusTicker();
  if (!activeTicker && !h.state.revChatFocusCleared) {
    activeTicker = (
      (h.state.activeChatTicker) ||
      (h.state.currentReportData ? h.state.currentReportData.ticker : '') ||
      (h.document.getElementById('ticker-input') ? h.document.getElementById('ticker-input').value : '') ||
      ''
    ).trim().toUpperCase();
    if (['GENERAL', 'AUTO', 'NONE', 'ALL', 'STOCK', 'MARKET'].includes(activeTicker)) activeTicker = '';
  }
  assert.strictEqual(activeTicker, '',
    'sending a message after clearing must not re-lock the focus');
});

test('without a clear, the send path still adopts a typed ticker', () => {
  // Clearing must not break the legitimate "type a ticker, then ask" flow.
  const h = loadChat();
  h.elements['ticker-input'].value = 'AAPL';
  let activeTicker = h.AppChat._focusTicker();
  if (!activeTicker && !h.state.revChatFocusCleared) {
    activeTicker = (h.document.getElementById('ticker-input').value || '').trim().toUpperCase();
  }
  assert.strictEqual(activeTicker, 'AAPL');
});

test('on-demand focus from the sidebar input', () => {
  const h = loadChat();
  h.elements['rev-chat-focus-input'].value = 'NVDA';
  h.AppChat.applySidebarFocusInput();
  assert.strictEqual(h.state.revChatFocusTicker, 'NVDA');
  assert.strictEqual(badgeText(h), '🎯 Focus: $NVDA');
  assert.strictEqual(h.elements['rev-chat-focus-input'].value, 'NVDA',
    'the box should keep what you typed');
});

test('blanking the sidebar input returns to Market', () => {
  const h = loadChat();
  h.AppChat.setRevChatFocus('TSLA');
  h.elements['rev-chat-focus-input'].value = '';
  h.AppChat.applySidebarFocusInput();
  assert.strictEqual(h.state.revChatFocusTicker, '');
  assert.strictEqual(badgeText(h), '🎯 Focus: Market');
});

test('sentinel words clear rather than becoming a ticker', () => {
  for (const w of ['MARKET', 'GENERAL', 'AUTO', 'NONE', 'ALL', 'STOCK', '']) {
    const h = loadChat();
    h.AppChat.setRevChatFocus(w);
    assert.strictEqual(h.state.revChatFocusTicker, '', `"${w}" became a ticker`);
  }
});

test('focus round-trips: set, clear, set again', () => {
  const h = loadChat();
  h.AppChat.setRevChatFocus('AMD');
  h.AppChat.clearSidebarFocus();
  h.state.currentReportData = { ticker: 'AMD' };   // the thing that used to resurrect it
  h.AppChat.setRevChatFocus('INTC');
  assert.strictEqual(badgeText(h), '🎯 Focus: $INTC', badgeText(h));
  h.AppChat.clearSidebarFocus();
  assert.strictEqual(badgeText(h), '🎯 Focus: Market', badgeText(h));
});

test('no module writes revChatFocusTicker outside the setter', () => {
  // One write path, or the badge goes stale the next time someone adds a call site.
  const allowed = new Set([
    'window.AppState.revChatFocusTicker = t;',
    "window.AppState.revChatFocusTicker = '';",
  ]);
  for (const [name, src] of [['chat.js', CHAT_SRC], ['intraday.js', INTRADAY_SRC], ['swing.js', SWING_SRC]]) {
    src.split('\n').forEach((line, i) => {
      if (/AppState\.revChatFocusTicker\s*=/.test(line)) {
        const t = line.trim();
        assert.ok(allowed.has(t),
          `${name}:${i + 1} writes focus directly -> "${t}". Use setRevChatFocus/clearRevChatFocus.`);
      }
    });
  }
});

test('the badge element id used by chat.js exists in index.html', () => {
  const html = fs.readFileSync(path.join(ROOT, 'web', 'index.html'), 'utf8');
  assert.ok(html.includes('id="rev-chat-focus-badge"'), 'badge element missing from index.html');
  assert.ok(html.includes('id="rev-chat-focus-input"'), 'focus input missing from index.html');
});

test('the sidebar input is wired to Enter and blur', () => {
  const html = fs.readFileSync(path.join(ROOT, 'web', 'index.html'), 'utf8');
  // The element spans several lines, so match the whole tag rather than one line.
  const i = html.indexOf('id="rev-chat-focus-input"');
  assert.ok(i > 0, 'focus input not found');
  const tag = html.slice(html.lastIndexOf('<input', i), html.indexOf('>', i) + 1);
  assert.ok(tag.includes("event.key==='Enter'"), 'Enter should apply the focus');
  assert.ok(tag.includes('onblur="AppChat.applySidebarFocusInput()"'), 'blur should apply/clear it');
  assert.ok(html.includes('onclick="AppChat.clearSidebarFocus()"'), 'badge must stay clickable to clear');
});

console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed === 0 ? 0 : 1);