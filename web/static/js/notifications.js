/** Notification center: bell in the header, panel with Alerts and Trust tabs. */
(function () {
  const SEV = { CRITICAL: '#ef4444', WARN: '#f59e0b', INFO: '#38bdf8' };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let panel, badge, bell, tab = 'alerts';

  async function getJson(url, opts) {
    const r = await fetch(url, opts);
    return r.json();
  }

  function setBadge(unread, notifier) {
    const total = (unread && unread.total) || 0;
    badge.textContent = total > 99 ? '99+' : String(total);
    badge.style.display = total ? 'inline-block' : 'none';
    badge.style.background = unread && unread.CRITICAL ? SEV.CRITICAL : SEV.WARN;
    bell.title = notifier && notifier.alive ? 'Notifications' : 'Notifier is not running: alerts are not being generated';
    bell.style.opacity = notifier && notifier.alive ? '1' : '0.45';
  }

  async function poll() {
    try {
      const d = await getJson('/api/notifications/unread');
      setBadge(d.unread, d.notifier);
    } catch (e) { /* UI keeps working without the bell */ }
  }

  function tickersOf(n) {
    if (n.ticker) return [n.ticker];
    return [...new Set([...(n.body || '').matchAll(/^\[([A-Z0-9.]+)\]/gm)].map((m) => m[1]))];
  }

  function row(n) {
    const t = (n.created_at || '').replace('T', ' ').slice(5, 16);
    const tks = tickersOf(n);
    const chips = !n.ticker && tks.length ? `<div style="margin:5px 0 0 16px;display:flex;flex-wrap:wrap;gap:5px">${tks.map((k) => `<span data-explain="${esc(k)}" style="padding:1px 7px;border:1px solid var(--border);border-radius:9px;font-size:11px;cursor:pointer;color:var(--cyan)">${esc(k)}</span>`).join('')}</div>` : '';
    return `<div data-nid="${n.id}" ${n.ticker ? `data-explain="${esc(n.ticker)}"` : ''} style="padding:8px 10px;border-bottom:1px solid var(--border);opacity:${n.read_at ? 0.55 : 1};${n.ticker ? 'cursor:pointer' : ''}" title="${n.ticker ? 'Click: open ' + esc(n.ticker) + ' in chat and explain this alert' : ''}">
      <div style="display:flex;gap:8px;align-items:center">
        <span style="width:8px;height:8px;border-radius:50%;background:${SEV[n.severity] || SEV.INFO};flex:none"></span>
        <b style="font-size:12px;flex:1">${esc(n.title)}</b>
        <span style="font-size:10px;color:var(--text-muted);white-space:nowrap">${esc(t)} UTC</span>
        <button class="notif-row-close" data-close-nid="${n.id}" title="Dismiss / Mark read" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:13px;padding:0 3px;margin-left:4px;line-height:1;border-radius:4px" onmouseover="this.style.color='var(--text-main)'" onmouseout="this.style.color='var(--text-muted)'">✕</button>
      </div>
      ${n.body ? `<div style="font-size:11px;color:var(--text-muted);margin:3px 0 0 16px;white-space:pre-line">${esc(n.body)}</div>` : ''}${chips}
    </div>`;
  }

  function explain(tk, n) {
    const prompt = `Explain this alert for ${tk}: "${n.title}". ${String(n.body || '').split('\n').find((l) => l.includes('[' + tk + ']')) || n.body || ''} ` +
      'Use get_prior_plan_outcome and get_candles to show what the levels did since the plan, where price sits against every tracked zone, ' +
      'the R:R at market against the floor (1.5 yellow, 2.0 and up green, 1.0 to 1.5 orange), and whether this is buy, wait or ignore. Say plainly what is unmeasured. Keep it short.';
    panel.style.display = 'none';
    if (window.AppChat && window.AppChat.openChatWithPrompt) window.AppChat.openChatWithPrompt(prompt, tk);
  }

  function stat(label, s) {
    if (!s || !s.n) return `<div style="margin-bottom:10px"><b>${label}</b><div style="font-size:11px;color:var(--text-muted)">no scored trades</div></div>`;
    return `<div style="margin-bottom:10px"><b>${label}</b> <span style="font-size:11px;color:var(--text-muted)">n=${s.n}</span>
      <div style="font-size:12px">win ${s.win_rate}% (${s.win_ci[0]}-${s.win_ci[1]}) · mean ${s.mean_r}R (${s.mean_ci[0]} to ${s.mean_ci[1]}) · median ${s.median_r}R</div>
      <div style="font-size:11px;color:var(--text-muted)">without top 2 winners: ${s.mean_ex_top2}R · flat ${s.flat}</div>
      <div style="font-size:11px;color:var(--amber)">${esc(s.verdict)}</div></div>`;
  }

  function agentBlock(a) {
    if (!a || a.error) return '';
    const c = a.closed || {};
    const open = (a.open || []).map((o) => `${esc(o.ticker)} ${o.state === 'OPEN' ? 'open' : 'limit'}`).join(', ') || 'none';
    return `<div style="margin-top:10px;font-size:11px"><b>🤖 Agent (paper)</b>: ${c.n ? `${c.n} closed, win ${c.win_rate}%, mean ${c.mean_r}R. ${esc(c.verdict)}` : 'no closed trades yet'}<br>Open: ${open} · passed on ${a.passes || 0} setups. No broker orders are sent.</div>`;
  }

  function scoredLine(sc) {
    if (!sc) return '';
    const f = (n, c) => `${n}: ${c.would_have_won} reached T1, ${c.would_have_lost} hit stop, ${c.open} open`;
    return `<div style="color:var(--text-muted)">After the fact (plan level, overstates winners): ${f('you skipped', sc.skipped)}; ${f('agent passed', sc.agent_passed)}</div>`;
  }

  function decisionBlock(dc) {
    if (dc && dc.scored && !dc.n) return `<div style="margin-top:10px;font-size:11px"><b>Your calls vs the system</b>${scoredLine(dc.scored)}</div>`;
    if (!dc || dc.error || !dc.n) return '<div style="margin-top:10px;font-size:11px;color:var(--text-muted)"><b>Your calls vs the system</b><br>None logged yet. Use Take or Skip on the Trades tab; each stores what the system said at that moment.</div>';
    const rows = Object.keys(dc.by_system).map((k) => `<div>System said <b>${esc(k)}</b>: ${Object.entries(dc.by_system[k]).map(([a, n]) => `${esc(a.replace('_', ' ').toLowerCase())} ×${n}`).join(', ')}</div>`).join('');
    return `<div style="margin-top:10px;font-size:11px"><b>Your calls vs the system</b> (n=${dc.n})${rows}${scoredLine(dc.scored)}</div>`;
  }

  async function render() {
    const body = panel.querySelector('#notif-body');
    panel.querySelectorAll('[data-tab]').forEach((b) => { b.style.fontWeight = b.dataset.tab === tab ? '800' : '500'; });
    body.innerHTML = '<div style="padding:12px;font-size:12px">Loading…</div>';
    try {
      if (tab === 'alerts') {
        const PAGE = 10;
        const first = await getJson('/api/notifications?limit=' + PAGE);
        setBadge(first.unread, first.notifier);
        let items = first.items.slice();
        let more = first.has_more;
        const draw = () => {
          body.innerHTML = (items.length ? items.map(row).join('') : '<div style="padding:12px;font-size:12px;color:var(--text-muted)">No notifications yet.</div>')
            + (more ? '<div id="notif-more" style="padding:10px;text-align:center;font-size:12px;color:var(--text-muted);cursor:pointer">Load older</div>' : '');
        };
        let loading = false;
        const loadMore = async () => {
          if (loading || !more || !items.length) return;
          loading = true;
          try {
            const d = await getJson('/api/notifications?limit=' + PAGE + '&before_id=' + items[items.length - 1].id);
            items = items.concat(d.items);
            more = d.has_more;
            draw();
          } finally { loading = false; }
        };
        draw();
        panel.onscroll = () => { if (panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 80) loadMore(); };
        body.onclick = (e) => {
          if (e.target.closest('#notif-more')) { loadMore(); return; }
          const closeBtn = e.target.closest('[data-close-nid]');
          if (closeBtn) {
            e.stopPropagation();
            const nid = closeBtn.dataset.closeNid;
            const rowEl = closeBtn.closest('[data-nid]');
            if (rowEl) {
              rowEl.style.transition = 'opacity 0.2s';
              rowEl.style.opacity = '0.35';
              closeBtn.remove();
            }
            getJson('/api/notifications/read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: Number(nid) }) }).then(poll);
            return;
          }
          const el = e.target.closest('[data-explain]');
          if (!el) return;
          const rowEl = el.closest('[data-nid]');
          const n = items.find((x) => String(x.id) === rowEl.dataset.nid);
          if (!n) return;
          getJson('/api/notifications/read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: n.id }) }).then(poll);
          explain(el.dataset.explain, n);
        };
      } else if (tab === 'risk') {
        const d = await getJson('/api/risk/snapshot');
        if (d.loading) { body.innerHTML = '<div style="padding:12px;font-size:12px">Computing (first run pulls betas, about 10s). Reopen the tab.</div>'; return; }
        const pc = (v) => (v == null ? 'n/a' : v + '%');
        const dial = d.regime_dial || {};
        const flags = (d.flags || []).map((f) => `<div style="color:#f59e0b">⚠ ${esc(f)}</div>`).join('') || '<div style="color:#22c55e">No limits breached</div>';
        const top = (d.top || []).map((r) => `<tr><td>${esc(r.symbol)}</td><td style="text-align:right">${pc(r.pct_book)}</td><td style="text-align:right">${r.beta == null ? 'n/a' : r.beta}</td><td>${esc(r.sector)}</td></tr>`).join('');
        const sec = (d.sectors || []).slice(0, 5).map((s) => `${esc(s.sector)} ${pc(s.pct_book)}`).join(' · ');
        const h = d.hedge;
        body.innerHTML = `<div style="padding:10px 12px;font-size:12px;line-height:1.5">
          ${d.error ? '<div style="color:#ef4444">' + esc(d.error) + '</div>' : ''}
          <div>${flags}</div>
          <div style="margin-top:8px"><b>New-risk size dial: ${esc(dial.multiplier)}x</b> <span style="color:var(--text-muted)">${esc(dial.why)} (policy, not measured)</span></div>
          <div style="margin-top:6px">Book $${(d.book || 0).toLocaleString()} · invested ${pc(d.invested_pct)} · cash-like ${pc(d.cash_like_pct)} · option premium ${pc(d.options_pct)}</div>
          <div>SPY-beta-weighted exposure: <b>${pc(d.beta_weighted_pct)}</b> of book</div>
          <div>Open risk (tracked stops): <b>$${(d.open_risk || 0).toLocaleString()} (${pc(d.open_risk_pct)})</b> of ${d.risk_cap_pct}% cap · positions ${d.tracked}/${d.position_cap}</div>
          <div style="color:var(--text-muted)">Holdings with no tracked stop: ${esc((d.untracked_holdings || []).join(', ') || 'none')}</div>
          <table style="width:100%;margin-top:8px;font-size:11px"><tr style="color:var(--text-muted)"><td>Name</td><td style="text-align:right">Book</td><td style="text-align:right">Beta</td><td>Sector</td></tr>${top}</table>
          <div style="margin-top:6px;color:var(--text-muted)">${sec}</div>
          ${h ? `<div style="margin-top:8px"><b>Hedge sizing</b> (unmeasured): neutralize beta = $${h.spy_notional_to_neutralize.toLocaleString()} SPY-equivalent; a 25% cut = $${h.spy_notional_for_25pct_cut.toLocaleString()}.<br><span style="color:var(--text-muted)">${esc(h.how)} ${esc(h.note)}</span></div>` : ''}
        </div>`;
      } else if (tab === 'backend') {
        const d = await getJson('/api/backend/status');
        const up = d.uptime_s == null ? '' : ` · up ${Math.floor(d.uptime_s / 60)}m`;
        const btn = d.alive
          ? '<button id="be-stop" style="padding:5px 12px;border-radius:6px;border:1px solid #ef4444;background:none;color:#ef4444;cursor:pointer;font-weight:700">Kill backend</button>'
          : '<button id="be-start" style="padding:5px 12px;border-radius:6px;border:1px solid #22c55e;background:none;color:#22c55e;cursor:pointer;font-weight:700">Start backend</button>';
        body.innerHTML = `<div style="padding:10px 12px;font-size:12px">
          <div style="margin-bottom:8px"><b>${d.alive ? 'Running' : (d.stopped_by_user ? 'Stopped by you' : 'Not running')}</b> <span style="color:var(--text-muted)">PID ${esc(d.pid || '-')}${up}</span></div>
          <div style="margin-bottom:8px;color:var(--text-muted)">Screener: ${d.screener.paused ? 'paused' : (d.screener.is_scanning ? 'scanning' : 'idle')} · scans ${esc(d.screener.scan_count || 0)} · deep today ${esc(d.screener.auto_deep_count_today || 0)}<br>
          ${esc(d.screener.last_status || '')}${d.screener.last_error ? '<br><span style="color:#ef4444">' + esc(d.screener.last_error) + '</span>' : ''}<br>
          Triage: pending ${esc(d.triage.pending_count || 0)} · done ${esc(d.triage.total_triaged || 0)} · LLM ${d.triage.llm_online ? 'online' : 'offline'}</div>
          <div style="margin-bottom:8px;color:var(--text-muted)">Backup: ${d.last_backup ? esc(d.last_backup.date) + ' · ' + d.last_backup.databases + ' dbs' + (d.last_backup.failed.length ? ' · ' + d.last_backup.failed.length + ' failed' : '') : 'not yet'}${d.last_backup_error ? ' <span style="color:#ef4444">' + esc(d.last_backup_error) + '</span>' : ''}<br>
          Options chain log: ${d.last_chain ? esc(d.last_chain.date) + ' · ' + d.last_chain.rows + ' rows · ' + d.last_chain.tickers + ' names' + (d.last_chain.failed.length ? ' · failed ' + esc(d.last_chain.failed.join(',')) : '') : 'not yet (runs after 4:10pm ET)'}${d.last_chain_error ? ' <span style="color:#ef4444">' + esc(d.last_chain_error) + '</span>' : ''}</div>
          ${btn}
          <div style="margin-top:8px;font-size:11px;color:var(--text-muted)">Runs triage, screener, deep research and the watch loop with no page open. Kill stops all of it and running research; it will not auto-start again until you press Start.</div></div>`;
        const stop = body.querySelector('#be-stop'), start = body.querySelector('#be-start');
        if (stop) stop.onclick = async () => { if (!confirm('Kill the research backend? Running research jobs will be terminated.')) return; stop.disabled = true; stop.textContent = 'Stopping…'; await getJson('/api/backend/stop', { method: 'POST' }); render(); };
        if (start) start.onclick = async () => { start.disabled = true; await getJson('/api/backend/start', { method: 'POST' }); setTimeout(render, 6000); };
      } else {
        const d = await getJson('/api/notifications/trust');
        if (d.error) { body.innerHTML = `<div style="padding:12px;font-size:12px">${esc(d.error)}</div>`; return; }
        const rep = Object.keys(d.repeat_tickers || {}).map((k) => `${k}×${d.repeat_tickers[k]}`).join(', ') || 'none';
        body.innerHTML = `<div style="padding:10px 12px">
          ${stat('System suggestions (gate passed)', d.gated)}
          ${stat('Gate-rejected (control)', d.rejected)}
          ${stat('Trades you took', d.taken)}
          <div style="font-size:11px;color:var(--text-muted)">Pending (no outcome yet): ${d.pending_gated} · Taken total: ${d.taken_total}<br>
          Repeated tickers (correlated): ${esc(rep)}<br>${esc(d.note)}</div>
          ${agentBlock(d.agent)}${decisionBlock(d.decisions)}</div>`;
      }
    } catch (e) {
      body.innerHTML = '<div style="padding:12px;font-size:12px">Could not load.</div>';
    }
  }

  async function markAll() {
    await getJson('/api/notifications/read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    render();
  }

  function build() {
    const host = document.querySelector('header');
    if (!host || document.getElementById('notif-bell')) return;
    bell = document.createElement('button');
    bell.id = 'notif-bell';
    bell.style.cssText = 'position:relative;margin-left:auto;background:none;border:1px solid var(--border);border-radius:8px;padding:4px 9px;cursor:pointer;font-size:15px;color:var(--text-main)';
    bell.innerHTML = '🔔<span id="notif-badge" style="display:none;position:absolute;top:-6px;right:-6px;min-width:16px;padding:0 4px;border-radius:9px;font-size:10px;font-weight:800;color:#fff;text-align:center"></span>';
    host.appendChild(bell);
    badge = bell.querySelector('#notif-badge');

    panel = document.createElement('div');
    panel.style.cssText = 'display:none;position:fixed;top:54px;right:14px;width:430px;max-height:70vh;overflow:auto;z-index:9999;background:var(--bg-card,#111827);border:1px solid var(--border);border-radius:10px;box-shadow:0 10px 30px rgba(0,0,0,.45)';
    panel.innerHTML = `<div style="display:flex;gap:12px;align-items:center;padding:8px 10px;border-bottom:1px solid var(--border);position:sticky;top:0;background:inherit;z-index:1">
      <button data-tab="alerts" style="background:none;border:0;cursor:pointer;color:inherit">Alerts</button>
      <button data-tab="trust" style="background:none;border:0;cursor:pointer;color:inherit">Trust</button>
      <button data-tab="risk" style="background:none;border:0;cursor:pointer;color:inherit">Risk</button>
      <button data-tab="backend" style="background:none;border:0;cursor:pointer;color:inherit">Backend</button>
      <button id="notif-readall" style="margin-left:auto;background:none;border:0;cursor:pointer;font-size:11px;color:var(--cyan)">Mark all read</button>
      <button id="notif-close" style="background:none;border:0;cursor:pointer;font-size:14px;color:var(--text-muted);padding:0 4px;margin-left:4px;line-height:1" title="Close panel" onmouseover="this.style.color='var(--text-main)'" onmouseout="this.style.color='var(--text-muted)'">✕</button></div>
      <div id="notif-body"></div>`;
    document.body.appendChild(panel);

    bell.onclick = () => {
      const open = panel.style.display === 'block';
      panel.style.display = open ? 'none' : 'block';
      if (!open) render();
    };
    panel.querySelectorAll('[data-tab]').forEach((b) => { b.onclick = () => { tab = b.dataset.tab; render(); }; });
    panel.querySelector('#notif-readall').onclick = markAll;
    panel.querySelector('#notif-close').onclick = () => { panel.style.display = 'none'; };
    document.addEventListener('click', (e) => {
      if (!panel.contains(e.target) && !bell.contains(e.target)) panel.style.display = 'none';
    });
    poll();
    setInterval(poll, 60000);
    live();
  }

  let toastHost, audioCtx;
  function beep(sev) {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = sev === 'CRITICAL' ? 880 : 660;
      g.gain.value = 0.06;
      o.connect(g); g.connect(audioCtx.destination);
      o.start(); o.stop(audioCtx.currentTime + (sev === 'CRITICAL' ? 0.35 : 0.15));
    } catch (e) { /* audio blocked until the first click */ }
  }

  function toast(n) {
    toastHost = toastHost || Object.assign(document.body.appendChild(document.createElement('div')), {});
    toastHost.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:10000;display:flex;flex-direction:column;gap:8px;max-width:360px';
    const el = document.createElement('div');
    const tk = tickersOf(n)[0];
    el.style.cssText = `position:relative;padding:9px 12px;border-radius:8px;background:var(--bg-card,#111827);border:1px solid var(--border);border-left:4px solid ${SEV[n.severity] || SEV.INFO};box-shadow:0 6px 20px rgba(0,0,0,.45);font-size:12px;cursor:${tk ? 'pointer' : 'default'};transition:all 0.25s ease;`;
    el.innerHTML = `<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">
      <b style="flex:1">${esc(n.title)}</b>
      <button class="notif-toast-close" title="Close" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:14px;padding:0 2px;line-height:1;margin-left:4px;flex:none;opacity:0.8" onmouseover="this.style.color='var(--text-main)';this.style.opacity='1'" onmouseout="this.style.color='var(--text-muted)';this.style.opacity='0.8'">✕</button>
    </div>${n.body ? `<div style="color:var(--text-muted);margin-top:3px;white-space:pre-line">${esc(String(n.body).split('\n').slice(0, 3).join('\n'))}</div>` : ''}${tk ? '<div style="color:var(--cyan);margin-top:4px">Click: open in chat and explain</div>' : ''}`;

    let timer = null;
    const dismiss = () => {
      if (timer) clearTimeout(timer);
      el.style.opacity = '0';
      el.style.transform = 'translateY(10px)';
      setTimeout(() => { if (el.parentNode) el.remove(); }, 250);
    };

    const closeBtn = el.querySelector('.notif-toast-close');
    if (closeBtn) {
      closeBtn.onclick = (e) => {
        e.stopPropagation();
        dismiss();
        fetch('/api/notifications/read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: n.id }) }).then(poll);
      };
    }
    el.onclick = (e) => {
      if (e.target.closest('.notif-toast-close')) return;
      dismiss();
      if (!tk) return;
      fetch('/api/notifications/read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: n.id }) }).then(poll);
      explain(tk, n);
    };
    toastHost.appendChild(el);
    if (n.severity !== 'CRITICAL') timer = setTimeout(dismiss, n.severity === 'WARN' ? 20000 : 8000);
  }

  function live() {
    if (!window.EventSource) return;
    const es = new EventSource('/api/notifications/stream');
    es.onmessage = (e) => {
      const n = JSON.parse(e.data);
      poll();
      toast(n);
      if (n.severity === 'INFO') return;
      beep(n.severity);
      if (document.hidden && window.Notification && Notification.permission === 'granted') {
        const sys = new Notification(n.title, { body: String(n.body || '').split('\n')[0], tag: 'n' + n.id });
        sys.onclick = () => { window.focus(); const tk = tickersOf(n)[0]; if (tk) explain(tk, n); };
      }
    };
    bell.addEventListener('click', () => { if (window.Notification && Notification.permission === 'default') Notification.requestPermission(); }, { once: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
