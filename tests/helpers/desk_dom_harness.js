/*
 * Shared stub DOM + desk.js loader for the JS behaviour tests.
 *
 * Why a harness instead of string assertions: the desk bugs that mattered were all behavioural
 * (scroll read from the wrong element, focus restored before the scroll, memoised blocks
 * re-parsing every poll). A substring test passes while the behaviour is still broken, which is
 * exactly what happened to `desk_rerender_guard.test.js` before this existed.
 *
 * The model is deliberately small but faithful in the ways that matter:
 *   * #today-content-container records every innerHTML assignment (count + value)
 *   * an assignment DESTROYS the previous children and re-creates every element the new HTML
 *     declares, so "the focused input was replaced" is a real state transition, not a fiction
 *   * focus() scrolls the window to the top, as browsers do, so restore ordering is observable
 *   * window.scrollY / scrollTo are the only scroll state: #today-content-container has no
 *     overflow rule anywhere in the CSS, so the window is the scroller
 *
 * Used by: desk_loadtoday_characterization.test.js, desk_loadtoday_rerender.test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..', '..');
const DESK_SRC = fs.readFileSync(path.join(ROOT, 'web', 'static', 'js', 'desk.js'), 'utf8');

/** The requests loadToday fires, in order, mapped to the fixture key that answers them.
 *  Order matches the `requests` array in loadToday(); briefing and feedback are poll-excluded. */
const ENDPOINTS = {
  '/api/desk/today': 'today',
  '/api/desk/coverage': 'coverage',
  '/api/desk/missed-moves': 'missed-moves',
  '/api/desk/morning-briefing': 'briefing',
  '/api/desk/feedback-loop': 'feedback',
};

/** What a single NON-SILENT loadToday fires, in order. */
const FULL_REQUESTS = [
  '/api/desk/today',
  '/api/desk/coverage',
  '/api/desk/missed-moves',
  '/api/desk/morning-briefing',
  '/api/desk/feedback-loop',
];

/** What a single SILENT (6s poll) loadToday fires: no briefing, no feedback-loop. */
const POLL_REQUESTS = ['/api/desk/today', '/api/desk/coverage', '/api/desk/missed-moves'];

const ID_ATTR = /id="([\w-]+)"/g;

function makeElement(id, win) {
  const el = {
    id, _html: '', writes: 0, detached: false, focusCalls: 0, focused: false,
    // A real HTMLElement always has these; the poll path writes className / style.color.
    style: {}, className: '', innerText: '',
  };
  Object.defineProperty(el, 'innerHTML', {
    configurable: true,
    get() { return this._html; },
    set(v) { this.writes += 1; this._html = String(v); },
  });
  el.focus = function focus() {
    this.focusCalls += 1;
    this.focused = true;
    // A browser scrolls the focused element into view; that is what used to clobber the restore.
    if (win) win.scrollY = 0;
  };
  el.blur = function blur() { this.focused = false; };
  el.addEventListener = function addEventListener() {};
  return el;
}

/**
 * @param {object} schemas  fixture map: endpoint key -> payload (the same object is returned on
 *                          every request, so `_lastTodayData` identity is a real assertion)
 * @param {object} [opts]
 * @param {string[]} [opts.failWith]  request URLs that reject
 * @param {number}   [opts.scrollY]   starting window scroll position
 * @param {object}   [opts.activeElementId]  id of the element the user is focused in
 */
function loadDesk(schemas, opts = {}) {
  const requests = [];
  const scrollCalls = [];
  const elements = new Map();

  const windowStub = {
    scrollY: opts.scrollY || 0,
    scrollTo(x, y) { this.scrollY = y; scrollCalls.push([x, y]); },
    location: { href: '' },
    AppApi: {
      request: async (url) => {
        requests.push(url);
        if (opts.failWith && opts.failWith.includes(url)) throw new Error(`stub failure: ${url}`);
        const key = ENDPOINTS[url];
        if (!key) throw new Error(`unexpected request ${url}`);
        const payload = schemas[key];
        return payload === undefined ? {} : payload;
      },
    },
  };

  // The container is the only element that survives an innerHTML swap of its own children.
  const rebuild = (container, html) => {
    elements.delete(container.id);
    for (const key of [...elements.keys()]) elements.delete(key);
    elements.set(container.id, container);
    container._html = String(html);
    container.writes += 1;
    let m;
    ID_ATTR.lastIndex = 0;
    while ((m = ID_ATTR.exec(String(html))) !== null) {
      if (!elements.has(m[1])) elements.set(m[1], makeElement(m[1], windowStub));
    }
  };

  const container = makeElement('today-content-container', null);
  container.innerHTML = '';
  elements.set(container.id, container);
  // Re-implement the setter so the swap rebuilds the child registry.
  Object.defineProperty(container, 'innerHTML', {
    get() { return this._html; },
    set(v) { rebuild(container, v); },
  });

  // Forget the harness's own warm-up write so a caller can count what loadToday() did.
  container.writes = 0;

  const documentStub = {
    getElementById: (id) => (elements.has(id) ? elements.get(id) : null),
    activeElement: null,
    addEventListener: () => {},
    createElement: () => makeElement('anon', windowStub),
  };
  if (opts.activeElementId && elements.has(opts.activeElementId)) {
    documentStub.activeElement = elements.get(opts.activeElementId);
  }

  const ctx = { window: windowStub, document: documentStub, console, setTimeout, clearTimeout };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(DESK_SRC, ctx, { filename: 'desk.js' });

  const AppDesk = windowStub.AppDesk;
  if (!AppDesk || typeof AppDesk.loadToday !== 'function') {
    throw new Error('desk.js did not export window.AppDesk.loadToday');
  }

  // Observe without changing behaviour.
  const memoKeys = [];
  const rendererCalls = { renderRegimeStrip: 0, renderBatSignal: 0, renderRadarAndCalibration: 0 };
  const originalMemo = AppDesk.memoRender;
  AppDesk.memoRender = function memoRender(key, data, fn) {
    memoKeys.push(key);
    return originalMemo.call(this, key, data, fn);
  };
  for (const name of Object.keys(rendererCalls)) {
    const original = AppDesk[name];
    if (typeof original !== 'function') throw new Error(`missing renderer ${name}`);
    AppDesk[name] = function wrapped() {
      rendererCalls[name] += 1;
      return original.apply(this, arguments);
    };
  }

  // Controllers call loadToday() without awaiting it (setTodaySubTab, setOppsFilter), so a test
  // needs a way to wait for the in-flight commit before asserting on the DOM.
  const originalLoad = AppDesk.loadToday;
  let pending = null;
  AppDesk.loadToday = function loadToday(...args) {
    const p = Promise.resolve(originalLoad.apply(this, args));
    pending = p;
    p.then(() => { if (pending === p) pending = null; }, () => { if (pending === p) pending = null; });
    return p;
  };
  const flush = async () => { while (pending) await pending; };

  return { AppDesk, container, elements, requests, scrollCalls, memoKeys, rendererCalls, windowStub, documentStub, flush };
}

/** Element ids the emitted HTML declares. */
function idsIn(html) {
  const out = [];
  let m;
  ID_ATTR.lastIndex = 0;
  while ((m = ID_ATTR.exec(String(html))) !== null) out.push(m[1]);
  return out;
}

module.exports = { loadDesk, idsIn, ENDPOINTS, FULL_REQUESTS, POLL_REQUESTS, DESK_SRC };
