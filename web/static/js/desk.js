if (typeof pbBadge !== 'function') {
  window.pbBadge = function(pb) {
    if (pb === 1 || pb === 1.0 || pb === true) {
      return ` <span class="pill green" title="PB funnel set (Signal Pack bit 32) — measured era-stable positive" style="font-size:9px; padding:1px 5px; margin-top:2px;">PB</span>`;
    }
    if (pb === 0 || pb === 0.0 || pb === false) {
      return ` <span class="pill" title="No PB funnel — measured era-unstable / negative" style="font-size:9px; padding:1px 5px; margin-top:2px; color:var(--text-muted);">no PB</span>`;
    }
    return '';
  };
}

window.AppDesk = {
  _activeRecordScope: 'gated',

  async triggerDeep(ticker, btn, tier = 'MID') {
    try {
      if (btn) {
        btn.disabled = true;
        btn.innerText = '⏳ Queued...';
      }
      await window.AppApi.triggerResearch(ticker, 'full', null, true, tier);
      setTimeout(() => { window.AppDesk.loadToday(); }, 1200);
    } catch (e) {
      alert('Failed to trigger research: ' + e);
      if (btn) {
        btn.disabled = false;
        btn.innerText = '🔬 Run deep';
      }
    }
  },

  // Memoised renderers for the slow-changing regime/action blocks. The Today desk rebuilds its
  // entire DOM every 6s, so these blocks are rendered once per distinct payload instead of
  // re-parsing a large subtree (movers, ledger, calibration tables) on every poll.
  _memo: {},

  memoRender(key, data, fn) {
    let sig;
    try { sig = JSON.stringify(data); } catch (e) { sig = String(Date.now()); }
    if (this._memo[key] && this._memo[key].sig === sig) return this._memo[key].html;
    const html = fn(data);
    this._memo[key] = { sig, html };
    return html;
  },

  renderDeployPlan(plan) {
    if (!plan || plan.error || !plan.tranches) return '';
    const money = (v) => `$${Math.round(v || 0).toLocaleString()}`;
    const pf = plan.portfolio || {};
    const cx = plan.context || {};
    const sz = plan.sizing || {};
    const orderRow = (o) => `<div style="display:flex; justify-content:space-between; gap:10px; font-family:var(--font-mono); font-size:11px; padding:3px 0; border-top:1px dashed var(--border);"><b>${o.ticker}</b><span>${o.type} ${o.shares} @ ${o.entry}</span><span style="color:var(--text-dim);">stop ${o.stop} · T1 ${o.target} · R:R ${o.rr}</span><span>${money(o.notional)} · risk ${money(o.risk)} · wt ${o.weight_after_pct}%</span></div>`;
    const tranche = (t) => `<div style="flex:1 1 220px; min-width:200px; background:var(--bg-main); border:1px solid var(--border); border-radius:6px; padding:8px 10px;"><div style="font-size:10px; font-weight:900; letter-spacing:0.6px; color:var(--text-dim);">${t.label.toUpperCase()}</div><div style="font-size:14px; font-weight:900; font-family:var(--font-mono);">${money(t.budget)}</div>${(t.orders || []).length ? t.orders.map(orderRow).join('') : '<div style="font-size:10.5px; color:var(--text-dim); margin-top:4px;">no qualifying order</div>'}</div>`;
    const vet = (plan.vetoes || []).map(v => `<span style="font-size:10px; font-family:var(--font-mono); color:var(--text-dim);"><b>${v.ticker}</b> ${v.veto}</span>`).join(' · ');
    const conc = (plan.concentration || []).map(c => `${c.ticker} ${c.weight_pct}%`).join(' · ');
    return `
    <div class="station-card" style="padding:12px 18px; border-left:4px solid var(--cyan-glow);">
      <div style="display:flex; align-items:center; gap:9px; margin-bottom:6px; flex-wrap:wrap;">
        <span style="font-size:13px; font-weight:900; letter-spacing:0.7px;">DEPLOY PLAN</span>
        <span style="font-size:10px; font-family:var(--font-mono); color:var(--text-dim);">dry powder ${money((pf.dry_powder || {}).total)} (${pf.dry_powder_pct}% of ${money(pf.total)}) · risk ${sz.risk_pct_per_name}% per name · name cap ${sz.name_cap_pct}% · min R:R ${sz.min_rr}</span>
      </div>
      <div style="display:flex; gap:8px; flex-wrap:wrap;">${plan.tranches.map(tranche).join('')}</div>
      ${vet ? `<div style="margin-top:7px; line-height:1.6;">Skipped: ${vet}</div>` : ''}
      ${cx.vix_band ? `<div style="margin-top:6px; font-size:11px;"><b>VIX ${cx.vix} · ${cx.vix_band.range} · ${cx.vix_band.sentiment}</b>: ${cx.vix_band.pace}. Options: ${cx.vix_band.options}.${(plan.income_candidates || []).length ? ` Over the ${sz.held_cap_pct}% cap, covered-call candidates: <b>${plan.income_candidates.join(', ')}</b>.` : ''}</div>` : ''}
      ${conc ? `<div style="margin-top:5px; font-size:10px; font-family:var(--font-mono); color:var(--text-dim);">Concentration: ${conc}</div>` : ''}
      <div style="margin-top:5px; font-size:10px; color:var(--text-dim);">VIX ${cx.vix ?? '–'} · Fear &amp; Greed ${cx.fear_greed ?? '–'} · ${cx.tide || ''}. ${plan.note || ''}</div>
    </div>`;
  },

  renderAlertSetups(d) {
    const setups = (d && d.setups) || {};
    const names = Object.keys(setups);
    if (!names.length) return '';
    const f = (v, dp = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(dp));
    const row = (k) => {
      const e = setups[k] || {};
      const col = (e.mean_r_rel || 0) > 0 ? 'var(--green)' : 'var(--rose-light)';
      return `<div style="display:flex; gap:12px; flex-wrap:wrap; font-family:var(--font-mono); font-size:11px; padding:3px 0; border-top:1px dashed var(--border);"><b style="min-width:190px;">${k}</b><span>n ${e.n || 0}${e.pending ? ` (+${e.pending} pending)` : ''}</span><span>R ${f(e.mean_r)} / med ${f(e.median_r)}</span><span style="color:${col};">vs tape ${f(e.mean_r_rel)}</span><span>win ${f(e.win_pct, 0)}%</span><span>breadth ${f(e.breadth_pct, 0)}% of ${e.tickers || 0}</span><span style="color:${e.status === 'MEASURED' ? 'var(--green)' : 'var(--amber)'};">${e.status || 'UNMEASURED'}</span></div>`;
    };
    return `
    <div class="station-card" style="padding:10px 18px; border-left:4px solid var(--amber);">
      <div style="font-size:12px; font-weight:900; letter-spacing:0.7px; margin-bottom:4px;">EMAIL ALERT SETUPS — 21-BAR R vs TAPE</div>
      ${names.map(row).join('')}
      <div style="margin-top:5px; font-size:10px; color:var(--text-dim);">${d.note || ''}</div>
    </div>`;
  },

  _refreshAlertSetups() {
    const el = document.getElementById('desk-alert-setups');
    if (!el || (Date.now() - (this._alertSetupsAt || 0)) < 300000) return;
    this._alertSetupsAt = Date.now();
    fetch('/api/notifications/trust').then(r => r.json()).then(t => this._fillSide('desk-alert-setups', this.renderAlertSetups(t && t.alert_setups))).catch(() => {});
  },

  // The side panels fetch on their own timers and a full render rebuilds their divs empty, so the last
  // good HTML is kept and put back instead of leaving a blank until the next timer fires.
  _fillSide(id, html) {
    this._side = this._side || {};
    this._side[id] = html;
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  },

  _restoreSide() {
    const side = this._side || {};
    for (const id of ['desk-macro', 'desk-deploy-plan', 'desk-alert-setups', 'desk-thesis', 'desk-regime-lane']) {
      const el = document.getElementById(id);
      if (el && side[id] !== undefined) el.innerHTML = side[id];
    }
    this._refreshMacro();
    this._refreshDeployPlan();
    this._refreshAlertSetups();
    this._refreshThesis();
    this._refreshRegimeLane();
  },

  renderMacro(d) {
    const rel = (d && d.releases) || [];
    const spreads = (d && d.spreads) || [];
    const ox = (d && d.opex) || {};
    if (!rel.length && !spreads.length && !ox.date) return '';
    const money0 = (v) => `$${Math.round(v || 0).toLocaleString()}`;
    // Prompts are built from the flagged rows themselves and kept here, so no quote in a title can break an onclick.
    this._macroPrompts = [];
    const ask = (label, text, cls) => {
      this._macroPrompts.push(text);
      return `<button class="btn ${cls || 'secondary'}" style="font-size:10px; font-weight:800; padding:2px 9px;" onclick="AppChat.askPortfolioCopilot(AppDesk._macroPrompts[${this._macroPrompts.length - 1}])">💬 ${label}</button>`;
    };
    const titles = (r) => r.releases.map(x => `${x.title} (fcst ${x.forecast || 'n/a'}, prev ${x.previous || 'n/a'})`).join(', ');
    const legs = (r) => r.exposed.map(x => `${x.underlying} ${x.expiry} (${x.legs} leg${x.legs !== 1 ? 's' : ''})`).join('; ');
    const next = rel[0];
    const spreadHtml = spreads.map(s => {
      const name = `${s.underlying} ${s.expiry} ${s.long_strike}/${s.short_strike} ${s.type.toLowerCase()} spread x${s.qty}`;
      const prompt = `My ${name} risks ${money0(s.downside)} to make ${money0(s.upside)}${s.ratio ? ` (${s.ratio}:1)` : ''}. ` +
        (next ? `The next release is ${next.when}: ${titles(next)}. ` : 'No release is due in the next 48h. ') +
        'What has to happen for me to lose it, and should I close it now, at the open, or hold? Use my real book and no direction predictions.';
      return `<div style="padding:5px 0; border-top:1px dashed var(--border); font-size:11px; color:var(--rose-light); display:flex; gap:8px; align-items:center; flex-wrap:wrap;"><span><b>${name}:</b> risk ${money0(s.downside)} to make ${money0(s.upside)}${s.ratio ? ` (${s.ratio}:1)` : ''}. Closing removes the risk for the leftover gain.</span>${ask('Ask about this spread', prompt, 'danger')}</div>`;
    }).join('');
    const g = (r) => {
      const hot = r.exposed.length;
      const col = r.same_day ? 'var(--rose-light)' : (hot ? 'var(--amber, #f59e0b)' : 'var(--text-dim)');
      const items = r.releases.map(x => `${x.title}${x.forecast ? ` (fcst ${x.forecast}, prev ${x.previous || '–'})` : ''}`).join(' · ');
      const ex = hot ? `<div style="margin-top:3px; font-size:11px; color:${col};"><b>${r.same_day ? 'Your options expire that day' : 'Your options expire within a week'}:</b> ${r.exposed.map(x => `${x.underlying} ${x.expiry} (${x.legs} leg${x.legs !== 1 ? 's' : ''})`).join('; ')}. ${r.pre_open ? 'Prints before the open; you can only react from 9:30 ET.' : ''}</div>` : '';
      const pb = (r.playbook || []).map(x => `<div style="font-size:10px; color:var(--text-dim);"><b>${x.release}</b> · ${x.hot} ${x.cool}</div>`).join('');
      const plan = r.same_day ? `<div style="margin-top:3px; font-size:11px;"><b>Plan:</b> check futures before the open; the print lands first and you can only act from 9:30 ET. Decide hold or close before then.</div>` : '';
      const prompt = `${r.when} (in ${r.hours}h): ${titles(r)}. ` +
        (hot ? `My open options exposed: ${legs(r)}${r.same_day ? ', expiring that day' : ''}. ` : 'None of my open options expire near it. ') +
        'Using my real book, give the dollar impact on my positions if it prints hot, in line, or cool' + (hot ? ', and whether to hold or close the exposed options before it' : '') + '. No direction predictions.';
      return `<div style="padding:5px 0; border-top:1px dashed var(--border);"><div style="font-size:11px; display:flex; gap:8px; align-items:center; flex-wrap:wrap;"><span><b style="font-family:var(--font-mono);">${r.when}</b> · in ${r.hours}h</span>${ask('Ask about this release', prompt)}</div><div style="font-size:11px; color:var(--text-dim);">${items}</div>${pb}${ex}${plan}</div>`;
    };
    let opexHtml = '';
    if (ox.date) {
      const when = ox.days === 0 ? 'TODAY' : (ox.days === 1 ? 'TOMORROW' : `in ${ox.days} days`);
      const legTxt = (ox.legs || []).map(l => `${l.underlying} ${l.strike || ''} ${(l.type || '').toLowerCase()} x${l.qty}`).join('; ');
      const prompt = `${ox.quarterly ? 'Quarterly' : 'Monthly'} opex is ${when} (${ox.date}). ` +
        (legTxt ? `My legs expiring that day: ${legTxt}. ` : 'None of my open options expire that day. ') +
        'Using my real book, how far is each from the nearest round strike, and should I hold, close or roll before the close? Timing and exposure only, no direction predictions.';
      opexHtml = `<div style="padding:5px 0; border-top:1px dashed var(--border); font-size:11px; display:flex; gap:8px; align-items:center; flex-wrap:wrap; color:${ox.legs && ox.legs.length ? 'var(--rose-light)' : 'var(--amber, #f59e0b)'};"><span><b>${ox.quarterly ? 'QUARTERLY' : 'MONTHLY'} OPEX ${when} (${ox.date}):</b> ${legTxt ? `your legs expire: ${legTxt}` : 'no open option expires that day'}. Pinning near round strikes is possible; not a measured edge.</span>${ask('Ask about opex', prompt, ox.legs && ox.legs.length ? 'danger' : 'secondary')}</div>`;
    }
    return `<div class="station-card" style="padding:12px 18px; border-left:4px solid ${rel.some(r => r.same_day) || (ox.legs && ox.legs.length) ? 'var(--rose-light)' : 'var(--cyan-glow)'};"><div style="font-size:13px; font-weight:900; letter-spacing:0.7px; margin-bottom:4px;">ECONOMIC DATA · NEXT 48H · RISK CHECK <span style="font-size:10px; font-weight:400; color:var(--text-dim);">(reading guide is policy, not a forecast)</span></div>${opexHtml}${spreadHtml}${rel.map(g).join('')}</div>`;
  },

  _refreshMacro() {
    const el = document.getElementById('desk-macro');
    if (!el || (Date.now() - (this._macroAt || 0)) < 300000) return;
    this._macroAt = Date.now();
    fetch('/api/desk/macro').then(r => r.json()).then(d => this._fillSide('desk-macro', this.renderMacro(d))).catch(() => {});
  },

  _refreshDeployPlan() {
    const el = document.getElementById('desk-deploy-plan');
    if (!el || (Date.now() - (this._deployPlanAt || 0)) < 60000) return;
    this._deployPlanAt = Date.now();
    fetch('/api/desk/deploy-plan').then(r => r.json()).then(p => this._fillSide('desk-deploy-plan', this.renderDeployPlan(p))).catch(() => {});
  },

  renderRegimeLane(d) {
    const l = (d && d.lane) || {};
    const p = d && d.protection;
    if (!l.available && !p) return '';
    const z = l.available && l.lanes ? l.lanes.ZONE_RR : null;
    const lane = l.available ? `<div style="font-size:11px;"><b>Breadth ${l.advancing_pct}% advancing · ${l.quintile}</b>${z ? ` · zone R:R lane here ${z.r_rel > 0 ? '+' : ''}${z.r_rel}R (n=${z.n})` : ''}
      <div style="font-size:10px; color:var(--text-dim);">${l.note}</div></div>` : '';
    const prot = p ? `<div style="margin-top:6px; font-size:11px;"><b style="color:var(--amber);">Event risk on your book:</b> ${p.events.join(' · ')}<br>${p.idea}
      <div style="font-size:10px; color:var(--text-dim);">${p.note}</div></div>` : '';
    return `<div class="station-card" style="padding:10px 16px;">${lane}${prot}</div>`;
  },

  _refreshRegimeLane() {
    const el = document.getElementById('desk-regime-lane');
    if (!el || (Date.now() - (this._regimeLaneAt || 0)) < 300000) return;
    this._regimeLaneAt = Date.now();
    fetch('/api/desk/regime-lane').then(r => r.json()).then(d => this._fillSide('desk-regime-lane', this.renderRegimeLane(d))).catch(() => {});
  },

  renderThesis(d) {
    const rows = (d && d.theses) || [];
    if (!rows.length) return '';
    const sm = d.summary || {};
    const pct = (v) => (v === null || v === undefined) ? '–' : `${v > 0 ? '+' : ''}${v}%`;
    const tone = (st) => st === 'BROKEN' ? 'var(--rose-light)' : 'var(--green)';
    const body = rows.map(r => `<tr><td><b>${r.ticker}</b></td><td style="color:${tone(r.status)}">${r.status}${r.broken_reason ? ' · ' + r.broken_reason : ''}</td>
      <td>${r.invalidation_px ? r.invalidation_px.toFixed(2) : '–'}</td><td>${r.tranches}/3</td><td>${pct(r.paper_pnl_pct)}</td>
      <td>${r.last_verdict || '–'} ${r.last_update ? '(' + r.last_update + ')' : ''}</td></tr>`).join('');
    const sc = (sm.scored || {});
    const scored = Object.keys(sc).map(h => `${h}d n=${sc[h].n}${sc[h].mean_excess_pct === null ? '' : ' ' + pct(sc[h].mean_excess_pct) + ' vs SPY'}`).join(' · ');
    return `<details class="station-card" style="padding:10px 16px;"><summary style="cursor:pointer; font-size:12px; font-weight:800;">📒 THESIS LEDGER (paper) · ${sm.active || 0} active · ${sm.broken || 0} broken · ${sm.tranches || 0} tranches</summary>
      <div style="font-size:10px; color:var(--text-dim); margin:6px 0;">${scored} · ${sm.note || ''}</div>
      <table style="width:100%; font-size:11px; font-family:var(--font-mono);"><tr style="color:var(--text-dim)"><th align="left">Name</th><th align="left">Status</th><th align="left">Invalidation</th><th align="left">Tranches</th><th align="left">Paper P/L</th><th align="left">Last research</th></tr>${body}</table></details>`;
  },

  _refreshThesis() {
    const el = document.getElementById('desk-thesis');
    if (!el || (Date.now() - (this._thesisAt || 0)) < 300000) return;
    this._thesisAt = Date.now();
    fetch('/api/desk/thesis-ledger').then(r => r.json()).then(d => this._fillSide('desk-thesis', this.renderThesis(d))).catch(() => {});
  },

  renderBatSignal(panel) {
    const bat = (panel && panel.bat_signal) || {};
    if (!bat || !bat.action) return '';
    const tones = {
      green: { bg: 'rgba(16,185,129,0.09)', border: 'rgba(16,185,129,0.45)', fg: 'var(--green)' },
      amber: { bg: 'rgba(245,158,11,0.09)', border: 'rgba(245,158,11,0.45)', fg: 'var(--amber)' },
      rose: { bg: 'rgba(220,38,38,0.09)', border: 'rgba(220,38,38,0.45)', fg: 'var(--rose-light)' },
      cyan: { bg: 'rgba(6,182,212,0.09)', border: 'rgba(6,182,212,0.45)', fg: 'var(--cyan-glow)' },
    };
    const t = tones[bat.tone] || tones.amber;
    const c = bat.counts || {};
    const chip = (label, val, tone) => `<span style="display:inline-flex; align-items:center; gap:4px; background:var(--bg-main); border:1px solid var(--border); border-radius:5px; padding:3px 8px; font-size:10px; font-family:var(--font-mono);"><b style="color:${tone || 'var(--text-main)'}; font-size:12px;">${val}</b>${label}</span>`;

    return `
    <div class="station-card" style="padding:14px 18px; border-left:4px solid ${t.fg}; background:linear-gradient(90deg, ${t.bg} 0%, var(--bg-surface) 60%);">
      <div style="display:flex; align-items:flex-start; justify-content:space-between; gap:16px; flex-wrap:wrap;">
        <div style="flex:1 1 380px; min-width:0;">
          <div style="display:flex; align-items:center; gap:9px; margin-bottom:6px;">
            <span style="font-size:17px;">🦇</span>
            <span style="font-size:13.5px; font-weight:900; letter-spacing:0.7px; color:var(--text-main);">TODAY'S BAT SIGNAL</span>
            <span style="font-size:9px; font-family:var(--font-mono); color:var(--text-dim);">deterministic · ${panel.as_of_et || ''}</span>
          </div>
          <div style="font-size:17px; font-weight:900; font-family:var(--font-display); color:${t.fg}; letter-spacing:0.4px; margin-bottom:5px;">→ ${bat.action}</div>
          <div style="font-size:12px; color:var(--text-muted); line-height:1.5; max-width:720px;">${bat.why || ''}</div>
          ${bat.size ? `<div style="margin-top:6px; font-size:11px; font-weight:800; color:var(--text-main);">Suggested sizing: <span style="font-family:var(--font-mono);">${bat.size}</span></div>` : ''}
        </div>
        <div style="flex:0 1 260px; min-width:200px;">
          <div style="font-size:9px; font-weight:900; letter-spacing:0.7px; color:var(--text-dim); margin-bottom:6px;">WHY (LIVE COUNTS)</div>
          <div style="display:flex; flex-wrap:wrap; gap:5px;">
            ${chip('in zone', c.in_zone || 0, 'var(--green)')}
            ${chip('coiling', c.coiling || 0, 'var(--amber)')}
            ${chip('held & in zone', c.held_in_zone || 0, 'var(--cyan-glow)')}
            ${chip('earnings blackout', c.earnings_blackout || 0, (c.earnings_blackout ? 'var(--rose-light)' : undefined))}
            ${chip('open monitored', c.open_positions || 0)}
          </div>
          ${bat.headline ? `<div style="margin-top:8px; font-size:10px; font-family:var(--font-mono); color:var(--text-dim); line-height:1.5;">${bat.headline}</div>` : ''}
          <button class="btn" style="margin-top:9px; width:100%; font-size:10.5px; font-weight:800; justify-content:center;" onclick="AppChat.askRevChat('Give me my bat signal for right now: the posture you would take, why, and the exact per-position actions for my book.')">💬 Ask the copilot to act on this</button>
        </div>
      </div>
    </div>`;
  },

  renderRadarAndCalibration(panel) {
    // Null holes in event_radar used to throw here, and because the radar block is memoised the
    // whole desk then froze on the next poll with nothing shown to the user.
    const radar = this._list(panel.event_radar);
    const calib = panel.calibration || {};
    const real = calib.real || {};
    const model = calib.model || {};
    const paper = calib.paper || {};
    const cov = calib.coverage || {};
    const split = calib.pool_split || {};

    const radarCards = radar.map(m => {
      const up = (m.chg_pct || 0) >= 0;
      const tone = up ? 'var(--green)' : 'var(--rose-light)';
      const exp = m.exposure || null;
      const imp = m.est_book_impact || 0;
      return `
      <div style="background:var(--bg-main); border:1px solid var(--border); border-left:4px solid ${m.held ? 'var(--cyan-glow)' : 'var(--border-subtle)'}; border-radius:8px; padding:12px 14px;">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:10px; flex-wrap:wrap;">
          <div style="display:flex; align-items:center; gap:8px;">
            <b style="font-family:var(--font-mono); font-size:14px; color:var(--cyan-glow);">${m.ticker}</b>
            <b style="font-family:var(--font-display); font-size:16px; color:${tone};">${up ? '+' : ''}${m.chg_pct}%</b>
            <span style="font-size:9px; font-family:var(--font-mono); color:var(--text-dim);">$${m.price} · vol ${(m.volume || 0).toLocaleString()}</span>
            ${m.held ? '<span class="pill green" style="font-size:8.5px; padding:1px 6px;">YOU HOLD THIS</span>' : ''}
            ${m.on_board ? '<span class="pill cyan" style="font-size:8.5px; padding:1px 6px;">ON BOARD</span>' : ''}
          </div>
          <button class="btn secondary" style="font-size:9.5px; padding:2px 8px;" onclick="AppChat.askRevChat('${m.ticker}$')">Ask about ${m.ticker}</button>
        </div>
        <div style="margin-top:7px; font-size:11.5px; color:var(--text-muted); line-height:1.5;">
          <b style="color:var(--text-main);">Cause:</b> ${m.cause || 'unknown'}
        </div>
        ${exp ? `<div style="margin-top:6px; font-size:11px; font-family:var(--font-mono); color:var(--cyan-glow);">Your leg: ${exp.contract || exp.symbol || ''} · ${parseFloat(exp.quantity || 0).toFixed(2)} ${exp.asset_type === 'OPTION' ? 'ct' : 'sh'} · MV $${parseFloat(exp.market_value || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })} · Unreal ${parseFloat(exp.unrealized_pnl || 0) >= 0 ? '+' : '-'}$${Math.abs(parseFloat(exp.unrealized_pnl || 0)).toLocaleString('en-US', { maximumFractionDigits: 0 })}</div>
        <div style="margin-top:3px; font-size:11px; font-weight:800; color:${imp >= 0 ? 'var(--green)' : 'var(--rose-light)'};">Estimated book impact of this move: ${imp >= 0 ? '+' : '-'}$${Math.abs(imp).toLocaleString('en-US', { maximumFractionDigits: 0 })}</div>` : ''}
        <div style="margin-top:7px; font-size:11px; color:var(--text-main); line-height:1.5;"><b>Stance:</b> ${m.stance}</div>
      </div>`;
    }).join('');

    const unmeasured = (calib.unmeasured_lanes || []).map(u => `
      <div style="display:flex; align-items:center; gap:8px; font-size:10.5px; font-family:var(--font-mono);">
        <b style="color:var(--purple-light);">${u.rule}</b>
        <div style="flex:1 1 120px; height:6px; background:var(--bg-subtle); border-radius:3px; overflow:hidden;">
          <div style="width:${Math.min(100, (u.observations / u.required) * 100).toFixed(0)}%; height:100%; background:var(--purple);"></div>
        </div>
        <span style="color:var(--text-muted);">${u.observations}/${u.required} obs</span>
      </div>`).join('');

    const stat = (label, val, tone, sub) => `<div style="background:var(--bg-main); border:1px solid var(--border); border-radius:7px; padding:8px 11px; min-width:100px;">
      <div style="font-size:8.5px; font-weight:900; letter-spacing:0.6px; color:var(--text-dim);">${label}</div>
      <div style="font-size:17px; font-weight:900; font-family:var(--font-display); color:${tone || 'var(--text-main)'}; line-height:1.15;">${val}</div>
      ${sub ? `<div style="font-size:8.5px; color:var(--text-dim); font-family:var(--font-mono); margin-top:1px;">${sub}</div>` : ''}
    </div>`;

    const poolStats = (s, tone) => `
      <div style="display:flex; gap:7px; flex-wrap:wrap; margin-bottom:9px;">
        ${stat('SCORED', (s.scored || 0) + (s.unmeasured ? ` / ${s.scored + s.unmeasured}` : ''), 'var(--text-main)', 'unmeasured excluded')}
        ${stat('WIN RATE', s.win_rate_pct !== null && s.win_rate_pct !== undefined ? Number(s.win_rate_pct).toFixed(0) + '%' : '–', tone)}
        ${stat('BREAKEVEN', s.breakeven_win_rate_pct !== null && s.breakeven_win_rate_pct !== undefined ? Number(s.breakeven_win_rate_pct).toFixed(0) + '%' : '–', 'var(--text-dim)', 'from payoff ratio')}
        ${stat('PROFIT FACTOR', s.profit_factor !== null && s.profit_factor !== undefined ? Number(s.profit_factor).toFixed(2) : '–', s.profit_factor >= 1.2 ? 'var(--green)' : 'var(--rose-light)')}
        ${stat('EXPECTANCY', s.expectancy !== null && s.expectancy !== undefined ? (s.expectancy >= 0 ? '+' : '') + Number(s.expectancy).toFixed(2) + 'R' : '–', (s.expectancy || 0) >= 0.2 ? 'var(--green)' : 'var(--rose-light)')}
        ${stat('TOTAL REALIZED', s.total_realized_r !== null && s.total_realized_r !== undefined ? (s.total_realized_r >= 0 ? '+' : '') + Number(s.total_realized_r).toFixed(1) + 'R' : '–', (s.total_realized_r || 0) >= 0 ? 'var(--green)' : 'var(--rose-light)')}
      </div>`;

    const covWarn = (cov.available && cov.closed_total) ? `
      <div style="background:rgba(245,158,11,0.07); border:1px solid rgba(245,158,11,0.35); border-radius:7px; padding:9px 12px; margin-bottom:11px;">
        <div style="font-size:9.5px; font-weight:900; letter-spacing:0.6px; color:var(--amber); margin-bottom:4px;">⚠️ DATA QUALITY — READ THE WIN RATE WITH THIS</div>
        <div style="font-size:11px; color:var(--text-muted); line-height:1.55;">
          Of <b style="color:var(--text-main);">${cov.closed_total}</b> closed trades, only <b style="color:var(--amber);">${cov.with_exit_price} (${cov.exit_coverage_pct}%)</b> recorded an exit price and <b style="color:var(--amber);">${cov.stop_coverage_pct}%</b> a stop level. A trade without a stop cannot produce an R multiple, so it is counted as <b>unmeasured</b> — never as breakeven.
          ${(cov.worst_months || []).map(w => `<div style="margin-top:4px; font-family:var(--font-mono); font-size:10px;">• ${w.mon}: ${w.missing_exit}/${w.n} closed trades missing an exit price</div>`).join('')}
        </div>
      </div>` : '';

    return `
    <div style="display:flex; flex-direction:column; gap:16px;">
      <div class="station-card" style="padding:14px 18px;">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:10px;">
          <span style="font-size:15px;">📡</span>
          <span style="font-size:13px; font-weight:900; letter-spacing:0.5px;">EVENT RADAR — DRASTIC MOVERS vs YOUR BOOK</span>
          <span style="font-size:9px; color:var(--text-dim); font-family:var(--font-mono);">≥5% moves, joined to cause and exposure</span>
        </div>
        ${radar.length ? `<div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(430px, 1fr)); gap:10px;">${radarCards}</div>` :
          `<div style="padding:18px; text-align:center; color:var(--text-muted); font-size:11.5px; background:var(--bg-main); border:1px dashed var(--border); border-radius:8px;">No drastic (≥5%) movers in the tracked universe right now. The radar populates automatically when something breaks.</div>`}
      </div>

      <div class="station-card" style="padding:14px 18px;">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:10px; flex-wrap:wrap;">
          <span style="font-size:15px;">🎓</span>
          <span style="font-size:13px; font-weight:900; letter-spacing:0.5px;">CALIBRATION — WHAT HAS ACTUALLY WORKED FOR YOU</span>
          <button class="btn secondary" style="margin-left:auto; font-size:9.5px; padding:3px 9px;" onclick="AppChat.askRevChat('Given my real-trade calibration, which of todays setups should I take and which should I skip, and why?')">💬 Apply this to today</button>
        </div>

        ${covWarn}

        <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:12px;">
          <div style="flex:1 1 300px; min-width:270px; background:var(--bg-subtle); border:1px solid var(--border); border-left:3px solid var(--emerald); border-radius:0 8px 8px 0; padding:9px 12px;">
            <div style="display:flex; align-items:center; gap:7px; margin-bottom:7px; flex-wrap:wrap;">
              <b style="font-size:10.5px; letter-spacing:0.5px; color:var(--emerald-light);">📄 PAPER · 100 SHARES AT THESIS SPOT</b>
              <span style="font-size:9px; color:var(--text-dim); font-family:var(--font-mono);">${paper.open || 0} live · ${(paper.aggregate || {}).resolved || 0} resolved</span>
            </div>
            ${(paper.aggregate || {}).resolved ? `
            ${poolStats(paper.aggregate || {}, 'var(--emerald-light)')}
            <div style="display:flex; gap:7px; flex-wrap:wrap; margin-bottom:9px;">
              ${stat('THESIS CORRECT', ((paper.aggregate || {}).thesis_correct_rate_pct !== null && (paper.aggregate || {}).thesis_correct_rate_pct !== undefined ? Number((paper.aggregate || {}).thesis_correct_rate_pct).toFixed(0) + '%' : '–'), 'var(--emerald-light)', 'reached T1')}
              ${stat('TARGET HIT', (paper.aggregate || {}).target_hit || 0, 'var(--green)')}
              ${stat('STOP HIT', (paper.aggregate || {}).stop_hit || 0, 'var(--rose-light)')}
              ${stat('MFE / MAE', `${(paper.aggregate || {}).avg_mfe_pct !== null && (paper.aggregate || {}).avg_mfe_pct !== undefined ? Number((paper.aggregate || {}).avg_mfe_pct).toFixed(1) : '–'}% / ${(paper.aggregate || {}).avg_mae_pct !== null && (paper.aggregate || {}).avg_mae_pct !== undefined ? Number((paper.aggregate || {}).avg_mae_pct).toFixed(1) : '–'}%`, 'var(--text-main)', 'max fav / adv')}
            </div>
            <div style="font-size:9.5px; color:var(--text-dim); line-height:1.5;">A deterministic 100-share position opened at the spot captured when the thesis was generated, then walked forward on real daily bars (next-open fill, stop-first ambiguity) until the thesis resolved. This is the <b style="color:var(--text-muted)">screen's own hit rate</b>, independent of how you executed.</div>
            ` : `<div style="font-size:11px; color:var(--text-muted);">${(paper.open || 0)} paper position(s) still carrying forward — no thesis has resolved yet, so the hit rate is not yet measurable.</div>`}
          </div>

          <div style="flex:1 1 300px; min-width:270px; background:var(--bg-subtle); border:1px solid var(--border); border-left:3px solid var(--cyan-glow); border-radius:0 8px 8px 0; padding:9px 12px;">
            <div style="display:flex; align-items:center; gap:7px; margin-bottom:7px; flex-wrap:wrap;">
              <b style="font-size:10.5px; letter-spacing:0.5px; color:var(--cyan-glow);">🟢 REAL TRADES</b>
              <span style="font-size:9px; color:var(--text-dim); font-family:var(--font-mono);">${split.real_rows || 0} rows — what the tracker actually opened/closed</span>
            </div>
            ${real.scored ? poolStats(real) : `<div style="font-size:11px; color:var(--text-muted);">No measurable real trades yet — they need both an exit price and a stop level.</div>`}
          </div>

          <div style="flex:1 1 300px; min-width:270px; background:var(--bg-subtle); border:1px solid var(--border); border-left:3px solid var(--purple); border-radius:0 8px 8px 0; padding:9px 12px;">
            <div style="display:flex; align-items:center; gap:7px; margin-bottom:7px; flex-wrap:wrap;">
              <b style="font-size:10.5px; letter-spacing:0.5px; color:var(--purple-light);">🟣 MODEL (SIMULATED)</b>
              <span style="font-size:9px; color:var(--text-dim); font-family:var(--font-mono);">${split.model_rows || 0} rows — bar-walk backtest on suggested setups</span>
            </div>
            ${model.scored ? poolStats(model) : `<div style="font-size:11px; color:var(--text-muted);">No simulated outcomes on record.</div>`}
            <div style="font-size:9.5px; color:var(--text-dim); line-height:1.5;">Shown for reference only. A backtest with perfect fills is not evidence about your live execution — do not size from this pool.</div>
          </div>
        </div>

        ${((paper.recent || []).length) ? `
        <div style="margin-bottom:11px;">
          <div style="font-size:9px; font-weight:900; letter-spacing:0.6px; color:var(--text-dim); margin-bottom:6px;">RECENTLY RESOLVED PAPER THESES (100 shares from the thesis spot)</div>
          <div style="display:flex; flex-direction:column; gap:4px;">
            ${paper.recent.slice(0, 6).map(r => `
              <div style="display:flex; align-items:center; gap:9px; font-size:10.5px; font-family:var(--font-mono); background:var(--bg-main); border:1px solid var(--border); border-radius:6px; padding:5px 9px; flex-wrap:wrap;">
                <b style="color:var(--cyan-glow); min-width:48px;">${r.ticker}</b>
                <span style="color:var(--text-dim);">${r.side}</span>
                <span>thesis $${Number(r.thesis_spot || 0).toFixed(2)} → exit $${Number(r.exit_spot || 0).toFixed(2)}</span>
                <b style="color:${(r.pnl_dollars || 0) >= 0 ? 'var(--green)' : 'var(--rose-light)'};">${(r.pnl_dollars || 0) >= 0 ? '+' : ''}$${Number(r.pnl_dollars || 0).toFixed(0)}</b>
                <span style="color:var(--text-dim);">${r.r_multiple !== null && r.r_multiple !== undefined ? Number(r.r_multiple).toFixed(2) + 'R' : '–'}</span>
                <span style="padding:1px 6px; border-radius:3px; font-size:9px; font-weight:800; background:${r.outcome === 'TARGET_HIT' ? 'rgba(16,185,129,0.16)' : 'rgba(244,63,94,0.16)'}; color:${r.outcome === 'TARGET_HIT' ? 'var(--green)' : 'var(--rose-light)'};">${r.outcome}</span>
                <span style="margin-left:auto; color:var(--text-dim); font-size:9px;">${r.created_date} → ${r.resolved_at}</span>
              </div>`).join('')}
          </div>
        </div>` : ''}

        ${(calib.reasoning || []).length ? `
        <div style="background:var(--bg-subtle); border:1px solid var(--border); border-left:3px solid var(--purple); border-radius:0 7px 7px 0; padding:9px 12px;">
          <div style="font-size:9px; font-weight:900; letter-spacing:0.6px; color:var(--purple-light); margin-bottom:5px;">WHAT THIS MEANS FOR YOUR NEXT TRADE</div>
          ${calib.reasoning.map(r => `<div style="font-size:11.5px; color:var(--text-muted); line-height:1.55; margin-bottom:4px;">• ${r}</div>`).join('')}
        </div>` : ''}

        ${unmeasured ? `
        <div style="margin-top:11px;">
          <div style="font-size:9px; font-weight:900; letter-spacing:0.6px; color:var(--text-dim); margin-bottom:6px;">UNMEASURED LANES — NOT YET ALLOWED TO CLAIM AN EDGE (n≥200 required)</div>
          <div style="display:flex; flex-direction:column; gap:5px;">${unmeasured}</div>
        </div>` : ''}
      </div>
    </div>`;
  },

  renderRegimeStrip(regime) {
    if (!regime || !regime.available) return '';
    const fg = regime.fear_greed || null;
    const vix = regime.vix || null;
    const tide = regime.tide || null;
    const breadth = regime.breadth || {};
    const moversRaw = regime.movers || {};
    // A feed can hand back a list with null holes; every row must be a real object.
    const _clean = (a) => (Array.isArray(a) ? a.filter(m => m && typeof m === 'object') : []);
    const movers = {
      universe_size: moversRaw.universe_size || 0,
      drops: _clean(moversRaw.drops),
      spikes: _clean(moversRaw.spikes),
      alerts: _clean(moversRaw.alerts),
    };
    const drops = movers.drops || [];
    const spikes = movers.spikes || [];
    const drastic = movers.alerts || [];

    const gauge = (score) => {
      const pct = Math.max(2, Math.min(98, score));
      const tone = score >= 75 ? 'var(--rose-light)' : score >= 55 ? 'var(--green)' : score >= 45 ? 'var(--amber)' : score >= 25 ? 'var(--cyan)' : 'var(--cyan-glow)';
      return `<div style="width:104px;">
        <div style="position:relative; height:7px; background:linear-gradient(90deg, var(--cyan-glow), var(--cyan), var(--amber), var(--green), var(--rose-light)); border-radius:4px; overflow:hidden;">
          <div style="position:absolute; top:-3px; left:calc(${pct}% - 1.5px); width:3px; height:13px; background:var(--text-main); border-radius:2px; box-shadow:0 0 4px rgba(0,0,0,0.6);"></div>
        </div>
        <div style="display:flex; justify-content:space-between; font-size:8px; font-family:var(--font-mono); color:var(--text-dim); margin-top:3px;"><span>FEAR</span><span>GREED</span></div>
        <div style="font-size:10px; color:${tone}; font-weight:800; margin-top:2px;">${fg ? (fg.flag || '').split('—')[0].trim() : ''}</div>
      </div>`;
    };

    const moverChip = (m) => {
      const up = (m.chg_pct || 0) >= 0;
      const tone = m.drastic ? (up ? 'var(--green)' : 'var(--rose-light)') : 'var(--amber)';
      return `<span style="display:inline-flex; align-items:center; gap:5px; background:var(--bg-main); border:1px solid ${m.drastic ? tone : 'var(--border)'}; border-radius:6px; padding:3px 8px; font-size:10.5px; font-family:var(--font-mono); cursor:pointer;" title="${(m.ticker || '')} ${up ? '+' : ''}${(m.chg_pct || 0)}% | $${m.price || 0} | vol ${((m.volume || 0)).toLocaleString()}${m.held ? ' | YOU HOLD THIS' : ''} | Click to focus chat" onclick="AppChat.askRevChat('${m.ticker}$')">
        <b style="color:var(--cyan-glow);">${m.ticker || '?'}</b>
        <b style="color:${tone};">${up ? '+' : ''}${(m.chg_pct || 0)}%</b>
        ${m.drastic ? '<span style="font-size:9px;">⚡</span>' : ''}
        ${m.held ? '<span style="font-size:8.5px; color:var(--green); font-weight:800;">HELD</span>' : ''}
      </span>`;
    };

    return `
    <div class="station-card" style="padding:12px 18px; border-left:4px solid var(--purple); background:linear-gradient(180deg, rgba(168,85,247,0.05) 0%, var(--bg-surface) 100%);">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">

        <div style="display:flex; align-items:center; gap:10px;">
          <div style="font-size:18px;">📡</div>
          <div>
            <div style="font-size:13px; font-weight:900; letter-spacing:0.6px; color:var(--text-main);">MARKET REGIME &amp; SENTIMENT</div>
            <div style="font-size:10px; color:var(--text-dim); font-family:var(--font-mono);">live gauges · ${regime.as_of_et || ''}</div>
          </div>
        </div>

        <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
          ${fg ? `<div style="display:flex; align-items:center; gap:9px; background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:7px 12px;">
            <div>
              <div style="font-size:9.5px; font-family:var(--font-mono); color:var(--text-muted); font-weight:700; letter-spacing:0.4px;">CNN FEAR &amp; GREED</div>
              <div style="font-size:20px; font-weight:900; font-family:var(--font-display); color:var(--text-main); line-height:1;">${fg.score ? fg.score.toFixed(0) : '–'}<span style="font-size:10px; color:var(--text-dim); font-weight:700;">/100</span></div>
              <div style="font-size:9.5px; font-weight:800; color:var(--text-muted);">${fg.rating || ''}</div>
            </div>
            ${gauge(fg.score || 0)}
          </div>` : ''}

          ${vix ? `<div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:7px 12px;">
            <div style="font-size:9.5px; font-family:var(--font-mono); color:var(--text-muted); font-weight:700; letter-spacing:0.4px;">VIX</div>
            <div style="font-size:18px; font-weight:900; font-family:var(--font-display); color:var(--text-main); line-height:1.1;">${vix.price !== null && vix.price !== undefined ? Number(vix.price).toFixed(2) : '–'}</div>
            <div style="font-size:9.5px; font-weight:700; max-width:190px; color:var(--text-muted);">${(vix.band || '').split('—')[0].trim() || 'n/a'}</div>
          </div>` : ''}

          ${tide ? `<div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:7px 12px;">
            <div style="font-size:9.5px; font-family:var(--font-mono); color:var(--text-muted); font-weight:700; letter-spacing:0.4px;">MARKET TIDE (SPY)</div>
            <div style="font-size:12.5px; font-weight:900; color:${tide.is_bullish ? 'var(--green)' : 'var(--rose-light)'};">${tide.trend || 'n/a'}</div>
            <div style="font-size:9.5px; font-family:var(--font-mono); color:var(--text-muted);">SPY ${tide.spy !== null && tide.spy !== undefined ? '$' + Number(tide.spy).toFixed(2) : '–'} · 20EMA ${tide.ema20 !== null && tide.ema20 !== undefined ? '$' + Number(tide.ema20).toFixed(2) : '–'} · 50SMA ${tide.sma50 !== null && tide.sma50 !== undefined ? '$' + Number(tide.sma50).toFixed(2) : '–'}</div>
            <div style="font-size:9.5px; font-family:var(--font-mono); color:var(--text-dim);">20d return ${tide.spy_20d_return !== null && tide.spy_20d_return !== undefined ? (Number(tide.spy_20d_return) >= 0 ? '+' : '') + Number(tide.spy_20d_return).toFixed(2) + '%' : 'n/a'}</div>
          </div>` : ''}

          ${breadth.universe ? `<div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:7px 12px;">
            <div style="font-size:9.5px; font-family:var(--font-mono); color:var(--text-muted); font-weight:700; letter-spacing:0.4px;">BREADTH (${breadth.universe})</div>
            <div style="font-size:12px; font-weight:800; font-family:var(--font-mono);"><span style="color:var(--green);">▲ ${breadth.advancing || 0}</span> <span style="color:var(--rose-light);">▼ ${breadth.declining || 0}</span> <span style="color:var(--text-dim);">▬ ${breadth.flat || 0}</span></div>
            <div style="height:5px; width:100%; min-width:96px; background:var(--bg-subtle); border-radius:3px; margin-top:5px; display:flex; overflow:hidden;">
              <div style="width:${((breadth.advancing || 0) / breadth.universe * 100).toFixed(1)}%; background:var(--green);"></div>
              <div style="width:${((breadth.flat || 0) / breadth.universe * 100).toFixed(1)}%; background:var(--text-dim);"></div>
              <div style="width:${((breadth.declining || 0) / breadth.universe * 100).toFixed(1)}%; background:var(--rose-light);"></div>
            </div>
          </div>` : ''}
        </div>
      </div>

      ${(drops.length || spikes.length) ? `
      <div style="margin-top:11px; padding-top:10px; border-top:1px solid var(--border); display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
        <div style="display:flex; align-items:center; gap:6px;">
          <span style="font-size:11px;">🌊</span>
          <span style="font-size:10px; font-weight:900; letter-spacing:0.6px; color:var(--text-muted);">DRAMATIC MOVERS<br><span style="font-size:8.5px; font-weight:700; color:var(--text-dim);">|Δ| ≥ 3% today · ⚡ ≥ 5%</span></span>
        </div>
        <div style="display:flex; gap:5px; flex-wrap:wrap; align-items:center;">
          ${drops.map(moverChip).join('')}
          <span style="color:var(--text-dim); font-size:10px;">│</span>
          ${spikes.map(moverChip).join('')}
        </div>
        <span style="font-size:9.5px; color:var(--text-dim); font-family:var(--font-mono); margin-left:auto;">${(movers.universe_size || 0)} scanned · click a chip to focus the chat</span>
      </div>` : ''}

      ${drastic.length ? `
      <div style="margin-top:9px; display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
        <span style="font-size:9.5px; font-weight:900; color:var(--rose-light); letter-spacing:0.5px;">⚡ DRASTIC (≥5%):</span>
        ${drastic.map(d => `<span style="font-size:10px; font-family:var(--font-mono); padding:2px 7px; border-radius:4px; background:rgba(220,38,38,0.1); border:1px solid rgba(220,38,38,0.35); color:var(--rose-light);">${d.ticker || '?'} ${(d.chg_pct || 0) >= 0 ? '+' : ''}${d.chg_pct || 0}%${d.held ? ' · HELD' : ''}</span>`).join('')}
        <button class="btn secondary" style="font-size:9.5px; padding:2px 8px; margin-left:auto;" onclick="AppChat.askRevChat('Walk me through the drastic movers right now, why they moved, and whether any affect my holdings.')">💬 Explain the movers</button>
      </div>` : ''}
    </div>`;
  },

  toggleRowDrawer(drawerId, iconId) {
    const d = document.getElementById(drawerId);
    if (!d) return;
    const isHidden = (d.style.display === 'none');
    d.style.display = isHidden ? 'table-row' : 'none';
    const ic = document.getElementById(iconId);
    if (ic) ic.innerText = isHidden ? '▼' : '▶';
  },

  toggleBriefingTier2() {
    const c = document.getElementById('briefing-t2-container');
    const t = document.getElementById('briefing-t2-toggle');
    if (!c) return;
    const isHidden = (c.style.display === 'none' || !c.style.display);
    c.style.display = isHidden ? 'block' : 'none';
    if (t) t.innerText = isHidden ? '▼' : '▶';
  },

  async refreshBriefing(btn) {
    if (btn) {
      btn.disabled = true;
      btn.innerText = '🔄 Quoting...';
    }
    try {
      await window.AppApi.request('/api/desk/morning-briefing/refresh', { method: 'POST' });
      await this.loadToday();
    } catch (e) {
      alert('Failed refreshing morning briefing quotes: ' + e);
      if (btn) {
        btn.disabled = false;
        btn.innerText = '🔄 Refresh Live Quotes';
      }
    }
  },

  async setTastytradeAlert(ticker, btn) {
    if (btn) {
      btn.disabled = true;
      btn.innerText = '⏳ Setting...';
    }
    try {
      await window.AppApi.request('/api/tastytrade-alerts/sync-ticker', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: ticker })
      });
      if (btn) {
        btn.innerText = '✅ TT Set';
        btn.style.borderColor = 'var(--green)';
        btn.style.color = 'var(--green)';
      }
    } catch (e) {
      alert('Failed setting Tastytrade cloud alert for ' + ticker + ': ' + e);
      if (btn) {
        btn.disabled = false;
        btn.innerText = '📲 TT Alerts';
      }
    }
  },

  renderInboxTable(items, prefix) {
    if (!items || items.length === 0) {
      return `<div style="padding:10px; color:var(--text-muted); font-size:11px; text-align:center;">No items.</div>`;
    }
    let out = `<table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px;">
      <thead>
        <tr style="background:var(--bg-surface); border-bottom:1px solid var(--border);">
          <th style="width:24px; padding:6px; text-align:center;"></th>
          <th style="padding:6px 8px; text-align:left;">Symbol</th>
          <th style="padding:6px 8px; text-align:left;">Setup</th>
          <th style="padding:6px 8px; text-align:center;">Decision</th>
          <th style="padding:6px 8px; text-align:center;">Deep Status</th>
          <th style="padding:6px 8px; text-align:right;">Action</th>
        </tr>
      </thead>
      <tbody>`;
    items.forEach((ib, idx) => {
      const sym = (ib.ticker || '').toUpperCase();
      const repDate = (ib.date && ib.date !== '–') ? ib.date.substring(0, 10) : 'latest';
      const dec = ib.llm_decision || '';
      const decTone = dec.includes('PASS') ? 'good' : dec.includes('WATCH') ? 'warn' : dec.includes('CUT') ? 'bad' : 'neutral';
      const rowId = `inbox-drawer-${prefix}-${idx}`;
      const iconId = `inbox-icon-${prefix}-${idx}`;
      out += `
        <tr style="border-bottom:1px solid var(--border); cursor:pointer;" onclick="AppDesk.toggleRowDrawer('${rowId}', '${iconId}')">
          <td style="padding:6px; text-align:center; color:var(--text-muted); font-size:10px;"><span id="${iconId}">▶</span></td>
          <td style="padding:6px 8px; font-weight:700; color:var(--cyan);"><a href="javascript:void(0)" class="tv-symbol-hover" data-ticker="${sym}" title="$${sym} · Click for modal · Ctrl/Cmd+Click for new tab · Hover for Live Chart" onclick="if(window.AppSwing){ AppSwing.handleTickerClick(event, '${sym}', '${repDate}', 'local'); }">${sym}</a></td>
          <td style="padding:6px 8px;">${this.fmt(ib.setup)}${this.setupTagChip(ib)}</td>
          <td style="padding:6px 8px; text-align:center;">${this.pill(dec || '–', decTone)}</td>
          <td style="padding:6px 8px; text-align:center;">${this.pill((ib.deep_status || 'none').toUpperCase(), ib.deep_status === 'COMPLETED' ? 'good' : ib.deep_status === 'RUNNING' ? 'info' : 'neutral')}</td>
          <td style="padding:6px 8px; text-align:right;">
            <button class="btn secondary" style="font-size:10px; padding:2px 8px;" onclick="event.stopPropagation(); AppDesk.triggerDeep('${sym}', this)">🔬 Run deep</button>
          </td>
        </tr>
        <tr id="${rowId}" style="display:none; background:var(--bg-main);">
          <td colspan="6" style="padding:10px 14px; border-bottom:1px solid var(--border);">
            <div style="display:flex; flex-direction:column; gap:6px;">
              <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">
                <div style="font-size:11px; font-weight:700; color:var(--text-muted);">
                  SETUP: <span style="color:var(--text-main);">${this.fmt(ib.setup)}</span> · 
                  GATE: <span style="color:var(--text-main);">${this.fmt(ib.gate_status, 'PENDING')}</span>
                </div>
                <div style="display:flex; gap:6px;">
                  <button class="btn secondary" style="font-size:10px; padding:3px 8px;" onclick="AppSwing.openReportModal('${repDate}', '${sym}', 'local')">📑 Open Local Dossier</button>
                  <button class="btn secondary" style="font-size:10px; padding:3px 8px;" onclick="AppDesk.triggerDeep('${sym}', this)">🔬 Dispatch Deep</button>
                </div>
              </div>
              ${ib.llm_playbook ? `<div style="font-size:11px; color:var(--text-main); background:var(--bg-surface); padding:8px 10px; border-radius:6px; border:1px solid var(--border); line-height:1.4;"><b>Playbook / Thesis:</b> ${ib.llm_playbook}</div>` : ''}
            </div>
          </td>
        </tr>
      `;
    });
    out += `</tbody></table>`;
    return out;
  },

  fmt(v, d) {
    d = d || '–';
    return (v === null || v === undefined || v === '') ? d : v;
  },

  // PB funnel chip. 'unmeasured' is rendered as a dash, never as a green check.
  pbChip(bucket) {
    if (!bucket || bucket === 'unmeasured' || bucket === 'pre-PB') return '';
    const isPb = bucket === 'PB';
    const tone = isPb ? 'var(--green)' : 'var(--text-muted)';
    const label = isPb ? 'PB' : 'no PB';
    return `<span title="${isPb ? 'PB funnel set: the measured long-side gate' : 'PB funnel clear: a measured exclusion'}"
                 style="margin-left:4px; font-size:9.5px; font-weight:800; color:${tone};">${label}</span>`;
  },

  // TradingView setup name -> measured lane. A+ Trend Long and Early Action Long are the two
  // biggest families in the inbox (41 and 27 rows) and neither has ever been measured, so they
  // are tagged rather than left to read like a tradable setup.
  setupTagChip(ib) {
    const tag = ib.setup_tag;
    if (!tag) return '';
    const tone = {
      measured: 'var(--green)',
      'no edge': 'var(--rose-light)',
      'chase risk': 'var(--rose-light)',
      excluded: 'var(--rose-light)',
      'hard cut': 'var(--rose-light)',
      unmeasured: 'var(--text-muted)',
      'watch only': 'var(--amber)'
    }[tag] || 'var(--text-muted)';

    let tip = tag;
    if (tag === 'chase risk') tip = 'A+ Trend Long measured below the same-day tape (see Alert setups scoreboard). Chase risk, not a buy cue.';
    if (tag === 'no edge') tip = 'Early Action maps to ACTION (code 2), which measured flat/unstable. Not an edge.';
    if (tag === 'unmeasured') tip = 'This alert family has never been measured. Treat levels only.';
    if (tag === 'measured' && ib.setup_prior_win) {
      tip = `Measured: ${parseFloat(ib.setup_prior_win).toFixed(0)}% win / ${parseFloat(ib.setup_prior_ev) >= 0 ? '+' : ''}${parseFloat(ib.setup_prior_ev).toFixed(2)}R`;
    }
    const prior = (tag === 'measured' && ib.setup_prior_win)
      ? ` <span style="color:var(--text-muted);">${parseFloat(ib.setup_prior_win).toFixed(0)}% / ${parseFloat(ib.setup_prior_ev) >= 0 ? '+' : ''}${parseFloat(ib.setup_prior_ev).toFixed(2)}R</span>`
      : '';
    return `<span title="${tip}" style="display:block; font-size:9.5px; font-weight:800; color:${tone}; margin-top:2px;">${tag.toUpperCase()}${prior}</span>`;
  },

  // Stop width in ATR. Under 0.7 ATR the stop sits inside daily noise, so any R:R computed from
  // it is an artifact -- the corpus median is 0.69 ATR and 65% of stops get hit.
  stopAtrCell(r) {
    const v = r.stop_width_atr;
    if (v === null || v === undefined || v === '') {
      return `<span style="color:var(--text-muted);" title="No ATR at signal, so the stop width cannot be measured.">–</span>`;
    }
    const tight = parseFloat(v) < 0.7;
    return `<span style="font-family:var(--font-mono); font-weight:${tight ? 800 : 600};
                   color:${tight ? 'var(--amber)' : 'var(--text-main)'};"
                   title="${tight ? 'Stop is inside daily noise (&lt;0.7 ATR). The R:R on this row is an artifact of the denominator, not an edge.' : 'Stop distance in ATR'}">
              ${parseFloat(v).toFixed(2)}${tight ? ' ⚠️' : ''}</span>`;
  },

  // The measured base rate for the row's lane, so the ticker is read against it.
  lanePriorCell(r) {
    if (r.lane_prior_win === null || r.lane_prior_win === undefined) {
      return `<span style="color:var(--text-muted);">no prior</span>`;
    }
    const win = parseFloat(r.lane_prior_win);
    const ev = parseFloat(r.lane_prior_ev);
    const tone = ev > 0.05 ? 'var(--green)' : (ev < 0 ? 'var(--rose-light)' : 'var(--text-muted)');
    return `<span style="color:${tone}; font-weight:600;"
                  title="Measured win rate and expectancy for the ${r.lane || ''} lane${r.pb_bucket === 'PB' ? ' with the PB funnel set' : ''}.">
              ${win.toFixed(0)}% / ${(ev >= 0 ? '+' : '') + ev.toFixed(2)}R</span>`;
  },

  // R:R gate control. Sits on the KPI strip because it decides what "needs you" contains, and it
  // shows the measured lane prior next to the number so the bar is tuned against the base rate
  // rather than against a hunch. Preset buttons, bounds, and defaults all come from the server --
  // nothing about the gates is hardcoded here.
  rrGateControl(todayData) {
    const gates = (todayData && todayData.rr_gates) ? todayData.rr_gates : null;
    if (!gates || !gates.values) return '';

    const v = gates.values, b = gates.bounds || {}, ov = gates.overridden || {}, hints = gates.hints || {};
    const presets = gates.presets || [];

    const presetRow = presets.length ? `
      <div style="display:flex; gap:4px; align-items:center; margin-left:2px;">
        ${presets.map(p => `
          <button class="btn secondary"
                  style="font-size:11px; font-weight:800; padding:3px 8px; font-family:var(--font-mono);
                         ${p.active ? 'border-color:var(--amber); color:var(--amber); background:rgba(251,191,36,0.12); font-weight:900;' : ''}"
                  title="${p.blurb}"
                  onclick="AppDesk.applyRRPreset('${p.name}')">${p.label}</button>`).join('')}
      </div>` : '';

    const input = (key) => {
      const bd = b[key] || { min: 0, max: 100 };
      const isOver = ov[key];
      return `<label style="display:flex; flex-direction:column; gap:2px; font-size:10px;
                     font-weight:700; color:var(--text-muted); letter-spacing:0.3px;">
          <span>${(gates.labels && gates.labels[key]) || key.toUpperCase()}</span>
          <input id="rr-${key}" type="number" step="0.1" min="${bd.min}" max="${bd.max}"
                 value="${v[key]}"
                 title="${hints[key] || ''}"
                 style="width:88px; padding:3px 6px; font-size:12px; font-weight:700;
                        font-family:var(--font-mono); border-radius:5px;
                        border:1px solid ${isOver ? 'var(--amber)' : 'var(--border)'};
                        background:var(--bg-input, var(--bg-main)); color:var(--text-main);" />
          ${isOver ? `<span style="font-size:9px; color:var(--amber);">changed from ${gates.defaults[key]}</span>` : ''}
        </label>`;
    };

    return `<div style="display:flex; align-items:center; gap:10px; padding:8px 12px;
                          border:1px dashed var(--border); border-radius:10px;
                          background:var(--bg-subtle); flex-wrap:wrap;">
      <div style="display:flex; flex-direction:column; gap:2px;">
        <div style="font-size:10px; font-weight:800; letter-spacing:0.5px; color:var(--text-muted);">R:R GATES</div>
        <div style="font-size:9.5px; color:var(--text-muted); font-weight:600; max-width:210px; line-height:1.3;">
          Moves the needs-you queue and the ENTRY push gate. It does not loosen the stop-ATR floor
          or the PB requirement.</div>
      </div>
      ${presetRow}
      ${input('rr_market_min')}
      ${input('rr_hi_rr')}
      <div style="display:flex; flex-direction:column; gap:4px;">
        <button class="btn secondary" style="font-size:10.5px; padding:3px 10px;"
                title="Apply, then reload the desk"
                onclick="AppDesk.saveRRConfig()">Save</button>
        <button class="btn secondary" style="font-size:10.5px; padding:3px 10px;"
                title="Restore the measured defaults"
                onclick="AppDesk.resetRRConfig()">Reset</button>
      </div>
      <div id="rr-config-status" style="font-size:10px; font-weight:700; color:var(--text-muted);"></div>
    </div>`;
  },

  async applyRRPreset(name) {
    const status = document.getElementById('rr-config-status');
    if (status) { status.textContent = 'applying…'; status.style.color = 'var(--text-muted)'; }
    try {
      const res = await fetch(`/api/desk/rr-config/preset/${encodeURIComponent(name)}`, { method: 'POST' });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      if (status) { status.textContent = `${body.active_preset || name} applied`; status.style.color = 'var(--green)'; }
      this.loadToday();
    } catch (e) {
      if (status) { status.textContent = e.message; status.style.color = 'var(--rose-light)'; }
    }
  },

  async saveRRConfig() {
    const status = document.getElementById('rr-config-status');
    const payload = {};
    for (const key of ['rr_market_min', 'rr_hi_rr']) {
      const el = document.getElementById(`rr-${key}`);
      if (!el) continue;
      const parsed = parseFloat(el.value);
      // parseFloat('') is NaN, which JSON.stringify turns into null, which the route drops as
      // "leave alone" -- so a cleared box silently no-ops while still reporting 'saved'.
      if (Number.isFinite(parsed)) payload[key] = parsed;
      else {
        if (status) {
          status.textContent = `${key} is not a number`;
          status.style.color = 'var(--rose-light)';
        }
        return;
      }
    }
    if (!Object.keys(payload).length) return;

    if (status) { status.textContent = 'saving…'; status.style.color = 'var(--text-muted)'; }
    try {
      await fetch('/api/desk/rr-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
        return body;
      });
      if (status) { status.textContent = 'saved'; status.style.color = 'var(--green)'; }
      this.loadToday();
    } catch (e) {
      if (status) { status.textContent = e.message; status.style.color = 'var(--rose-light)'; }
    }
  },

  async resetRRConfig() {
    const status = document.getElementById('rr-config-status');
    if (status) { status.textContent = 'resetting…'; status.style.color = 'var(--text-muted)'; }
    try {
      const res = await fetch('/api/desk/rr-config/reset', { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (status) { status.textContent = 'reset'; status.style.color = 'var(--green)'; }
      this.loadToday();
    } catch (e) {
      if (status) { status.textContent = e.message; status.style.color = 'var(--rose-light)'; }
    }
  },

  pill(text, tone) {
    tone = tone || 'neutral';
    const colors = {
      neutral: 'var(--text-muted)',
      info: 'var(--cyan)',
      warn: 'var(--amber)',
      bad: 'var(--rose-light)',
      good: 'var(--green)'
    };
    return `<span class="badge" style="border-color:${colors[tone]}; color:${colors[tone]};">${text}</span>`;
  },

  kpi(label, value, sub, tone) {
    tone = tone || 'neutral';
    const colors = {
      neutral: 'var(--text-main)',
      info: 'var(--cyan)',
      warn: 'var(--amber)',
      bad: 'var(--rose-light)',
      good: 'var(--green)'
    };
    return `<div style="background:var(--bg-surface); padding:10px 14px; border-radius:8px; border:1px solid var(--border); flex:1; min-width:100px; text-align:center;">
      <div style="font-size:10px; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px; font-weight:700;">${label}</div>
      <div style="font-size:18px; font-weight:800; color:${colors[tone]}; font-family:var(--font-mono); margin-top:2px;">${value}</div>
      <div style="font-size:10px; color:var(--text-muted); margin-top:2px;">${sub || ''}</div>
    </div>`;
  },

  _todaySubTab: 'opps',
  _oppsFilter: 'ALL',
  _lastTodayData: null,
  _lastCoverageData: null,
  _lastMissedMovesData: null,
  _lastBriefingData: null,
  _lastFeedbackData: null,
  _isPollingToday: false,
  _pollingStartedAt: 0,

  renderSkeleton() {
    return `
      <div style="display:flex; flex-direction:column; gap:16px;">
        <div class="station-card" style="padding:16px 20px; border-left:4px solid var(--cyan-glow); min-height:76px; display:flex; align-items:center; justify-content:space-between; animation:pulse 1.5s infinite ease-in-out;">
          <div style="display:flex; align-items:center; gap:12px;">
            <div style="width:28px; height:28px; background:var(--bg-subtle); border-radius:6px;"></div>
            <div>
              <div style="width:220px; height:16px; background:var(--bg-subtle); border-radius:4px; margin-bottom:6px;"></div>
              <div style="width:320px; height:12px; background:var(--bg-subtle); border-radius:4px;"></div>
            </div>
          </div>
          <div style="width:160px; height:28px; background:var(--bg-subtle); border-radius:6px;"></div>
        </div>
        <div class="station-card" style="padding:20px; min-height:220px; animation:pulse 1.5s infinite ease-in-out;">
          <div style="display:flex; justify-content:space-between; margin-bottom:16px;">
            <div style="width:240px; height:18px; background:var(--bg-subtle); border-radius:4px;"></div>
            <div style="width:120px; height:18px; background:var(--bg-subtle); border-radius:4px;"></div>
          </div>
          <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:12px;">
            <div style="height:110px; background:var(--bg-subtle); border-radius:8px;"></div>
            <div style="height:110px; background:var(--bg-subtle); border-radius:8px;"></div>
            <div style="height:110px; background:var(--bg-subtle); border-radius:8px;"></div>
          </div>
        </div>
      </div>
    `;
  },

  // Feedback fields the user has typed into. The full re-render replaces the whole sub-tab
  // subtree, so their values are captured before the swap and put back after it; a controller
  // reload (deep-research dispatch, position action, sub-tab switch) must not eat an unsaved note.
  _dirtyFeedbackIds: new Set(),
  _feedbackGuardBound: false,

  /** Null holes and non-objects out of a feed array. Same rule as renderRegimeStrip's _clean. */
  _list(a) {
    return Array.isArray(a) ? a.filter(r => r && typeof r === 'object') : [];
  },

  _phaseTone(pulse) {
    const p = pulse || {};
    return p.market_open ? 'green' : (p.market_phase === 'AFTER_HOURS' || p.market_phase === 'PRE_MARKET') ? 'amber' : 'red';
  },

  _setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.innerText = String(text);
  },

  /**
   * Write a memoised block into a stable element, and skip the write entirely when memoRender
   * hands back the string it produced last time. Comparing the cached HTML to what is already in
   * the DOM is what keeps the poll from re-parsing a 2-4k subtree every 6s, while still honouring
   * the memo contract at memoRender().
   */
  _writeMemo(elId, key, data, fn) {
    const html = this.memoRender(key, data, fn);
    const el = document.getElementById(elId);
    if (el && el.innerHTML !== html) el.innerHTML = html;
  },

  /**
   * The 6s poll path. Touches ONLY the three areas that actually change between polls:
   *   1. the market-status pill and the SPY/QQQ/VIX strip
   *   2. the regime strip and the bat signal (stable divs, memo-skipped)
   *   3. the funnel counts and the six sub-tab count badges
   * Everything else -- the opportunity cards, the alerts table, the positions/feedback forms,
   * the coverage and missed-moves panels -- is rebuilt by its controller, which is what stops a
   * twice-a-minute teardown of ~900 lines of DOM.
   */
  _patchLive() {
    const data = this._lastTodayData || {};
    const pulse = data.market_pulse || {};
    const regime = data.market_regime || {};
    const panel = data.action_panel || {};
    const topOpps = this._list(data.top_opportunities);
    const alerts = this._list(data.actionable_alerts);
    const feedback = data.feedback_loop || {};
    const openPositions = this._list(feedback.open_positions);
    const closedPositions = this._list(feedback.closed_positions);
    const cov = this._lastCoverageData || {};
    const mm = this._lastMissedMovesData || {};
    const capDeferred = this._list(cov.deep_cap_deferred);
    const watchNoDeep = this._list(cov.watch_no_deep);
    const missedList = this._list(mm.missed_moves);
    const radar = this._list(panel.event_radar);
    const f = data.funnel || {};

    // 1. market pill + indices strip
    const pill = document.getElementById('desk-market-pill');
    if (pill) pill.className = `pill ${this._phaseTone(pulse)}`;
    this._setText('desk-market-status-text', pulse.market_status_text || '🟢 CONTINUOUS SURVEILLANCE ACTIVE');
    this._setText('desk-spy-px', pulse.spy ? `$${pulse.spy.toFixed(2)}` : '–');
    this._setText('desk-qqq-px', pulse.qqq ? `$${pulse.qqq.toFixed(2)}` : '–');
    this._setText('desk-vix-px', pulse.vix ? `${pulse.vix.toFixed(2)}` : '–');
    const vixEl = document.getElementById('desk-vix-px');
    if (vixEl) vixEl.style.color = (pulse.vix || 0) > 20 ? 'var(--rose-light)' : 'var(--green)';

    // 2. regime strip + bat signal
    this._writeMemo('desk-regime-strip', 'regime_strip', regime, (r) => this.renderRegimeStrip(r));
    this._writeMemo('desk-bat-signal', 'bat_signal', panel, (p) => this.renderBatSignal(p));
    this._refreshDeployPlan();
    this._refreshMacro();
    this._refreshAlertSetups();
    this._refreshThesis();
    this._refreshRegimeLane();

    // 3. funnel counts
    this._setText('desk-funnel-alerts', f.alerts || 0);
    this._setText('desk-funnel-local-done', f.local_done || 0);
    this._setText('desk-funnel-local-pass', f.local_pass || 0);
    this._setText('desk-funnel-measured', f.measured || 0);
    this._setText('desk-funnel-deep-queued', f.deep_queued || 0);
    this._setText('desk-funnel-deep-done', f.deep_done || 0);
    this._setText('desk-funnel-actionable', Math.max(f.actionable || 0, f.screener_actionable || 0));

    // 3b. the six sub-tab count badges
    this._setText('subtab-count-opps', topOpps.length);
    this._setText('subtab-count-alerts', alerts.length);
    this._setText('subtab-count-positions-open', openPositions.length);
    this._setText('subtab-count-positions-closed', closedPositions.length);
    this._setText('subtab-count-coverage', capDeferred.length);
    this._setText('subtab-count-coverage-stalking', watchNoDeep.length);
    this._setText('subtab-count-missed', missedList.length);
    this._setText('subtab-count-radar', radar.length ? '!' : '0');
  },

  /**
   * The full commit: sub-tab switches, filter changes, and every controller reload.
   * Preserves the window scroll and the focused control across the swap, and puts back anything
   * the user typed into a feedback form.
   */
  _renderFull(html) {
    const cont = document.getElementById('today-content-container');
    if (!cont) return;

    // #today-content-container has no overflow rule anywhere in the CSS, so the WINDOW is the
    // scroller: reading cont.scrollTop was always 0 and the restore never ran.
    const prevScroll = window.scrollY || 0;
    const prevFocusId = document.activeElement && document.activeElement.id;
    const dirty = this._captureDirtyFeedback();

    cont.innerHTML = html;

    this._restoreDirtyFeedback(dirty);
    if (prevFocusId) {
      const refocus = document.getElementById(prevFocusId);
      if (refocus && typeof refocus.focus === 'function') refocus.focus();
    }
    // Last: focus() scrolls the element into view and would otherwise win.
    if (prevScroll > 0) window.scrollTo(0, prevScroll);

    this._bindFeedbackDirtyGuard(cont);
    this._restoreSide();
  },

  /**
   * Event-delegated dirty tracking. The forms are re-created on every full render, so per-element
   * listeners would have to be re-attached each time; one pair of listeners on the container
   * covers every current and future #fb-* control.
   *
   * `input` fires as the user types; `change` covers the grade <select>, which never fires
   * `input` and is therefore invisible to the silent guard at the top of loadToday.
   */
  _bindFeedbackDirtyGuard(cont) {
    if (this._feedbackGuardBound || !cont || typeof cont.addEventListener !== 'function') return;
    const mark = (ev) => {
      const id = ev && ev.target && ev.target.id;
      if (id && /^fb-(grade|notes|lesson)-/.test(id)) this._dirtyFeedbackIds.add(id);
    };
    cont.addEventListener('input', mark);
    cont.addEventListener('change', mark);
    this._feedbackGuardBound = true;
  },

  _captureDirtyFeedback() {
    const saved = {};
    this._dirtyFeedbackIds.forEach((id) => {
      const el = document.getElementById(id);
      if (el && 'value' in el) saved[id] = el.value;
    });
    return saved;
  },

  _restoreDirtyFeedback(saved) {
    Object.keys(saved).forEach((id) => {
      const el = document.getElementById(id);
      if (el && 'value' in el) el.value = saved[id];
    });
  },

  setTodaySubTab(tab) {
    this._todaySubTab = tab || 'opps';
    this.loadToday();
  },

  setOppsFilter(filter) {
    this._oppsFilter = filter || 'ALL';
    this.loadToday();
  },

  async openActionablePosition(ticker, side, entry, stop, target, vehicle, notes) {
    const sym = (ticker || '').toUpperCase();
    const entryPx = parseFloat(entry) || 0;
    const stopPx = parseFloat(stop) || 0;
    const targetPx = parseFloat(target) || 0;
    if (!confirm(`🚀 Confirm OPENING ${side} Position on $${sym}?\n\n• Entry: $${entryPx.toFixed(2)}\n• Stop Loss: $${stopPx.toFixed(2)}\n• Target 1: $${targetPx.toFixed(2)}\n• Vehicle: ${vehicle || 'Equity'}\n\nThis will start live position surveillance and R-accounting.`)) return;

    try {
      const res = await fetch('/api/desk/actionable-position', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: sym,
          side: side.toUpperCase(),
          strategy: 'Swing',
          entry_price: entryPx,
          stop: stopPx,
          target: targetPx,
          quantity: 100,
          instrument_type: vehicle && vehicle.toLowerCase().includes('spread') ? 'OPTION' : 'EQUITY',
          notes: notes || `Opened from Actionable Opportunity Cockpit (${vehicle || 'Equity'})`
        })
      });
      const data = await res.json();
      if (data.status === 'ok') {
        alert(`✅ Position opened for $${sym}!\nSurveillance active in Position Monitor & Alert DB.`);
        this._todaySubTab = 'positions';
        this.loadToday();
      } else {
        alert(`⚠️ Could not open position: ${data.message || data.error}`);
      }
    } catch (err) {
      alert(`Error opening position: ${err}`);
    }
  },

  async managePosition(ticker, action, price = null, reason = null) {
    const sym = (ticker || '').toUpperCase();
    const actionLabels = {
      'SCALE_50': '🎯 Scale 50% Profit at Target 1 and Ratchet Runner Stop to Breakeven',
      'TRAIL_BE': '🛡️ Ratchet Stop to Break-Even (Lock Zero-Risk)',
      'CLOSE': '🛑 Close and Flatten Position'
    };
    if (!confirm(`Execute action on $${sym}:\n${actionLabels[action] || action}?`)) return;

    try {
      const res = await fetch('/api/desk/manage-position', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: sym,
          action: action,
          price: price ? parseFloat(price) : null,
          reason: reason || actionLabels[action]
        })
      });
      const data = await res.json();
      if (data.status === 'ok') {
        alert(`✅ ${data.message}`);
        this.loadToday();
      } else {
        alert(`⚠️ Action failed: ${data.message || data.error}`);
      }
    } catch (err) {
      alert(`Error executing action: ${err}`);
    }
  },

  async saveFeedbackNotes(tradeId, ticker) {
    const gradeEl = document.getElementById(`fb-grade-${tradeId}`);
    const notesEl = document.getElementById(`fb-notes-${tradeId}`);
    const lessonEl = document.getElementById(`fb-lesson-${tradeId}`);
    const btn = document.getElementById(`fb-save-btn-${tradeId}`);

    const grade = gradeEl ? gradeEl.value : null;
    const notes = notesEl ? notesEl.value : '';
    const lesson = lessonEl ? lessonEl.value : '';

    if (btn) btn.innerText = '💾 Saving...';

    try {
      const res = await fetch('/api/desk/feedback-loop/notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          trade_id: String(tradeId),
          ticker: ticker,
          grade: grade,
          notes: notes,
          lesson: lesson
        })
      });
      const data = await res.json();
      if (data.status === 'ok') {
        if (btn) {
          btn.innerText = '✅ Saved';
          btn.style.color = 'var(--green)';
          setTimeout(() => {
            btn.innerText = 'Save Notes';
            btn.style.color = '';
          }, 2000);
        }
      } else {
        alert(`Failed saving notes: ${data.error}`);
        if (btn) btn.innerText = 'Save Notes';
      }
    } catch (err) {
      alert(`Error saving feedback note: ${err}`);
      if (btn) btn.innerText = 'Save Notes';
    }
  },

  async loadToday(silent = false) {
    const now = Date.now();
    if (this._isPollingToday) {
      if (this._pollingStartedAt && (now - this._pollingStartedAt) > 10000) {
        console.warn('⚠️ Prior Today polling timed out after 10s. Resetting lock.');
        this._isPollingToday = false;
      } else {
        // Skip poll because prior request is still in flight
        return;
      }
    }
    if (silent && document.activeElement && (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA')) {
      return;
    }
    this._isPollingToday = true;
    this._pollingStartedAt = now;

    // Load skeleton into container if empty and not a background silent poll
    const cont = document.getElementById('today-content-container');
    if (cont && !cont.innerHTML.trim() && !silent) {
      cont.innerHTML = this.renderSkeleton();
    }

    try {
      // Primary fast operational feeds: all served from instant in-memory cache (<50ms)
      const requests = [
        window.AppApi.request('/api/desk/today', { timeoutMs: 30000 }),
        window.AppApi.request('/api/desk/coverage', { timeoutMs: 6000 }).catch(() => null),
        window.AppApi.request('/api/desk/missed-moves', { timeoutMs: 6000 }).catch(() => null),
      ];

      // Fetch feedback-loop lazily ONLY when on positions subtab
      let feedbackPromise = Promise.resolve(this._lastFeedbackData || null);
      if (this._todaySubTab === 'positions' && !silent) {
        feedbackPromise = window.AppApi.request('/api/desk/feedback-loop', { timeoutMs: 8000 }).catch(() => null);
      }

      // Fetch morning-briefing lazily ONLY when on briefing subtab
      let briefingPromise = Promise.resolve(this._lastBriefingData || null);
      if (this._todaySubTab === 'briefing' && !silent) {
        briefingPromise = window.AppApi.request('/api/desk/morning-briefing', { timeoutMs: 8000 }).catch(() => null);
      }

      const [todayData, coverageData, missedMovesData] = await Promise.all(requests);
      const feedbackData = await feedbackPromise;
      const briefingData = await briefingPromise;

      if (feedbackData) this._lastFeedbackData = feedbackData;
      if (briefingData) this._lastBriefingData = briefingData;
      this._lastTodayData = todayData;
      this._lastCoverageData = coverageData;
      this._lastMissedMovesData = missedMovesData;

      if (silent) {
        // The poll writes into the three live areas and nothing else. Building the ~900-line
        // desk string and throwing it away would be the cost with none of the benefit.
        this._patchLive();
        return;
      }

      const pulse = (todayData && todayData.market_pulse) || {};
      // One rule, one place: _phaseTone is what the poll writes into the pill, so the reloaded
      // desk has to use the same helper or the two paths disagree on the tone.
      const phaseBadgeTone = this._phaseTone(pulse);
      // A feed can hand back a list with null holes. Without this the very first filter throws,
      // and because the 6s poll is silent the whole desk then freezes with no visible error.
      const topOpps = this._list((todayData && todayData.top_opportunities) || (briefingData && briefingData.tier1_actionable));
      const actionableAlerts = this._list((todayData && todayData.actionable_alerts));
      const feedback = feedbackData || (todayData && todayData.feedback_loop) || {};
      const openPositions = this._list(feedback.open_positions);
      const closedPositions = this._list(feedback.closed_positions);
      const scorecard = feedback.scorecard || {};

      const inZoneOpps = topOpps.filter(o => o.in_zone);
      const coilingOpps = topOpps.filter(o => !o.in_zone && o.priority_tier <= 2);
      const highConvOpps = topOpps.filter(o => o.conviction >= 7);

      // Sub-tab badge counts, computed once through _list() exactly as _patchLive() computes them.
      // They used to be re-derived inline with a raw `.length`, so a null hole made a controller
      // reload show N and the next poll silently rewrite the badge to N-1.
      const capDeferred = this._list(coverageData && coverageData.deep_cap_deferred);
      const watchNoDeep = this._list(coverageData && coverageData.watch_no_deep);
      const missedList = this._list(missedMovesData && missedMovesData.missed_moves);
      const radar = this._list(todayData && todayData.action_panel && todayData.action_panel.event_radar);

      const activeTab = this._todaySubTab || 'opps';
      const oppFilter = this._oppsFilter || 'ALL';

      let filteredOpps = [...topOpps];
      if (oppFilter === 'IN_ZONE') filteredOpps = inZoneOpps;
      else if (oppFilter === 'COILING') filteredOpps = coilingOpps;
      else if (oppFilter === 'HIGH_CONVICTION') filteredOpps = highConvOpps;

      let html = `<div style="display:flex; flex-direction:column; gap:16px;">`;

      // =========================================================================
      // 1. LIVE MARKET DYNAMICS & SURVEILLANCE PULSE BAR
      // =========================================================================
      const marketText = pulse.market_status_text || '🟢 CONTINUOUS SURVEILLANCE ACTIVE';
      const spyPx = pulse.spy ? `$${pulse.spy.toFixed(2)}` : '–';
      const qqqPx = pulse.qqq ? `$${pulse.qqq.toFixed(2)}` : '–';
      const vixPx = pulse.vix ? `${pulse.vix.toFixed(2)}` : '–';

      const regime = todayData.market_regime || {};
      const actionPanel = todayData.action_panel || {};
      // Memoised, and wrapped in stable divs so the poll can write the new string straight into
      // the DOM: identical payloads render once per 6s poll instead of re-parsing these subtrees
      // every cycle, and a changed payload updates the div without rebuilding the desk.
      html += `<div id="desk-regime-strip">${this.memoRender('regime_strip', regime, (r) => this.renderRegimeStrip(r))}</div>`;
      html += `<div id="desk-bat-signal">${this.memoRender('bat_signal', actionPanel, (p) => this.renderBatSignal(p))}</div>`;
      html += `<div id="desk-macro"></div>`;
      html += `<div id="desk-deploy-plan"></div>`;
      html += `<div id="desk-alert-setups"></div>`;
      html += `<div id="desk-regime-lane"></div>`;
      html += `<div id="desk-thesis"></div>`;

      html += `
      <div class="station-card" style="padding:14px 20px; border-left:4px solid var(--cyan-glow); background:linear-gradient(180deg, rgba(6,182,212,0.06) 0%, var(--bg-surface) 100%);">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:14px;">
          
          <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
            <div style="font-size:22px;">⚡</div>
            <div>
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:15px; font-weight:800; letter-spacing:0.5px; color:var(--text-main);">
                  MARKET DYNAMICS &amp; EXECUTION COCKPIT
                </span>
                <span id="desk-market-pill" class="pill ${phaseBadgeTone}" style="font-weight:800; font-size:10px;">
                  <span class="dot pulse"></span> <span id="desk-market-status-text">${marketText}</span>
                </span>
              </div>
              <div style="font-size:11.5px; color:var(--text-muted); margin-top:3px;">
                Continuous Screening across Schwab 1000 &amp; TradingView · Live Quotes · Proximity &amp; R:R Updates
              </div>
            </div>
          </div>

          <!-- Market Indices Strip -->
          <div style="display:flex; align-items:center; gap:14px; flex-wrap:wrap;">
            <div style="display:flex; gap:10px; align-items:center; background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:6px 14px;">
               <div style="font-size:11px; font-family:var(--font-mono);"><span style="color:var(--text-muted); font-weight:700;">SPY</span> <b id="desk-spy-px" style="color:var(--text-main);">${spyPx}</b></div>
              <div style="color:var(--border);">|</div>
              <div style="font-size:11px; font-family:var(--font-mono);"><span style="color:var(--text-muted); font-weight:700;">QQQ</span> <b id="desk-qqq-px" style="color:var(--text-main);">${qqqPx}</b></div>
              <div style="color:var(--border);">|</div>
              <div style="font-size:11px; font-family:var(--font-mono);"><span style="color:var(--text-muted); font-weight:700;">VIX</span> <b id="desk-vix-px" style="color:${(pulse.vix || 0) > 20 ? 'var(--rose-light)' : 'var(--green)'};">${vixPx}</b></div>
            </div>

            <button class="btn secondary" style="font-size:11px; padding:6px 12px; font-weight:700; display:inline-flex; align-items:center; gap:5px;" onclick="AppDesk.loadToday()">
              🔄 <span>Refresh Live</span>
            </button>
          </div>

        </div>

        <!-- Pipeline Funnel Summary Strip (Task B2) -->
        ${(todayData && todayData.funnel) ? `
        <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; margin-top:12px; padding:8px 14px; background:var(--bg-main); border:1px solid var(--border); border-radius:8px; font-size:11.5px; flex-wrap:wrap;">
          <div style="font-weight:800; color:var(--text-muted); font-size:10px; text-transform:uppercase; letter-spacing:0.5px; display:flex; align-items:center; gap:5px;">
            <span>⚡ PIPELINE FUNNEL:</span>
          </div>
          <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-family:var(--font-mono); font-size:11px;">
            <span>Alerts: <b id="desk-funnel-alerts" style="color:var(--text-main);">${todayData.funnel.alerts || 0}</b></span>
            <span style="color:var(--text-muted);">➔</span>
            <span>Local Done: <b id="desk-funnel-local-done" style="color:var(--text-main);">${todayData.funnel.local_done || 0}</b></span>
            <span style="color:var(--text-muted);">➔</span>
            <span>Local PASS: <b id="desk-funnel-local-pass" style="color:var(--cyan);">${todayData.funnel.local_pass || 0}</b></span>
            <span style="color:var(--text-muted);">➔</span>
            <span>Measured: <b id="desk-funnel-measured" style="color:var(--purple-light);">${todayData.funnel.measured || 0}</b></span>
            <span style="color:var(--text-muted);">➔</span>
            <span>Deep Queued: <b id="desk-funnel-deep-queued" style="color:var(--amber);">${todayData.funnel.deep_queued || 0}</b></span>
            <span style="color:var(--text-muted);">➔</span>
            <span>Deep Done: <b id="desk-funnel-deep-done" style="color:var(--blue-light);">${todayData.funnel.deep_done || 0}</b></span>
            <span style="color:var(--text-muted);">➔</span>
            <span>Actionable: <b id="desk-funnel-actionable" style="color:var(--green); font-size:12px;">${Math.max(todayData.funnel.actionable || 0, todayData.funnel.screener_actionable || 0)}</b></span>
          </div>
        </div>` : ''}

        <!-- Cockpit Primary Navigation Tabs -->
        <div style="display:flex; gap:8px; margin-top:14px; padding-top:12px; border-top:1px solid var(--border); flex-wrap:wrap;">
          <button id="subtab-today-opps" class="btn ${activeTab === 'opps' ? '' : 'secondary'}" style="font-size:12px; font-weight:800; padding:6px 16px;" onclick="AppDesk.setTodaySubTab('opps')">
            🎯 Actionable Opportunities (<span id="subtab-count-opps">${topOpps.length}</span>)
          </button>
          <button id="subtab-today-alerts" class="btn ${activeTab === 'alerts' ? '' : 'secondary'}" style="font-size:12px; font-weight:800; padding:6px 16px;" onclick="AppDesk.setTodaySubTab('alerts')">
            🚨 Actionable Alerts (<span id="subtab-count-alerts">${actionableAlerts.length}</span>)
          </button>
          <button id="subtab-today-feedback" class="btn ${activeTab === 'positions' ? '' : 'secondary'}" style="font-size:12px; font-weight:800; padding:6px 16px;" onclick="AppDesk.setTodaySubTab('positions')">
            📊 Positions &amp; Feedback Loop (<span id="subtab-count-positions-open">${openPositions.length}</span> Open · <span id="subtab-count-positions-closed">${closedPositions.length}</span> Evaluated)
          </button>
          <button id="subtab-today-coverage" class="btn ${activeTab === 'coverage' ? '' : 'secondary'}" style="font-size:12px; font-weight:800; padding:6px 16px;" onclick="AppDesk.setTodaySubTab('coverage')">
            🛡️ Pipeline Coverage &amp; Deferrals (<span id="subtab-count-coverage">${capDeferred.length}</span> Deferred · <span id="subtab-count-coverage-stalking">${watchNoDeep.length}</span> Stalking)
          </button>
          <button id="subtab-today-missed" class="btn ${activeTab === 'missed_moves' ? '' : 'secondary'}" style="font-size:12px; font-weight:800; padding:6px 16px;" onclick="AppDesk.setTodaySubTab('missed_moves')">
            📉 Missed Moves (<span id="subtab-count-missed">${missedList.length}</span>)
          </button>
          <button id="subtab-today-radar" class="btn ${activeTab === 'radar' ? '' : 'secondary'}" style="font-size:12px; font-weight:800; padding:6px 16px;" onclick="AppDesk.setTodaySubTab('radar')" title="Drastic movers with their cause and their impact on your book, plus your own setup performance">
            📡 Event Radar &amp; Calibration (<span id="subtab-count-radar">${radar.length ? '!' : '0'}</span>)
          </button>
        </div>
      </div>
      `;

      // =========================================================================
      // 2b. SUBTAB: EVENT RADAR & CALIBRATION
      // =========================================================================
      if (activeTab === 'radar') {
        html += this.memoRender('radar_calib', todayData.action_panel || {}, (p) => this.renderRadarAndCalibration(p));
      }

      // =========================================================================
      // 2. SUBTAB: ACTIONABLE OPPORTUNITIES (PROPER DEEP RESEARCH & SCREENING)
      // =========================================================================
      if (activeTab === 'opps') {
        html += `
        <div class="station-card" style="padding:16px 20px;">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:14px;">
            <div>
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:18px;">🎯</span>
                <span style="font-size:14px; font-weight:900; color:var(--text-main); letter-spacing:0.5px;">
                  TOP QUALIFIED ACTIONABLE OPPORTUNITIES
                </span>
                <span class="pill cyan" style="font-weight:800; font-size:10px;">${filteredOpps.length} Setups</span>
              </div>
              <div style="font-size:11px; color:var(--text-muted); margin-top:3px;">
                Institutional Senior PM directives with defined risk floors, binding invalidations, and specific options spread blueprints.
              </div>
            </div>

            <!-- Opportunity Filter Buttons -->
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
              <button class="btn ${oppFilter === 'ALL' ? '' : 'secondary'}" style="padding:4px 10px; font-size:11px;" onclick="AppDesk.setOppsFilter('ALL')">All (${topOpps.length})</button>
              <button class="btn ${oppFilter === 'IN_ZONE' ? '' : 'secondary'}" style="padding:4px 10px; font-size:11px;" onclick="AppDesk.setOppsFilter('IN_ZONE')">🎯 In Entry Zone (${inZoneOpps.length})</button>
              <button class="btn ${oppFilter === 'COILING' ? '' : 'secondary'}" style="padding:4px 10px; font-size:11px;" onclick="AppDesk.setOppsFilter('COILING')">⚡ Coiling Triggers (${coilingOpps.length})</button>
              <button class="btn ${oppFilter === 'HIGH_CONVICTION' ? '' : 'secondary'}" style="padding:4px 10px; font-size:11px;" onclick="AppDesk.setOppsFilter('HIGH_CONVICTION')">💎 High Conviction (${highConvOpps.length})</button>
            </div>
          </div>

          <!-- Closest to Gate Strip (Task d7) -->
          ${(todayData && todayData.closest_to_gate && todayData.closest_to_gate.length > 0) ? `
          <div style="margin-bottom:14px; background:rgba(245,158,11,0.06); border:1px solid rgba(245,158,11,0.25); border-radius:8px; padding:10px 14px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
              <div style="font-size:11.5px; font-weight:800; color:var(--amber);">
                ⏳ CLOSEST TO GATE (${todayData.closest_to_gate.length} Setups Missing Exactly 1 Condition)
              </div>
              <span style="font-size:10.5px; color:var(--text-muted);">One condition away from actionable · Click setup to open Dossier & Plan</span>
            </div>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
              ${this._list(todayData.closest_to_gate).map(c => {
                const repDate = (c.date && c.date !== '–') ? c.date.substring(0, 10) : 'latest';
                return `
                <div class="closest-gate-pill"
                     onclick="if(window.AppSwing && typeof window.AppSwing.openReportModal === 'function'){ window.AppSwing.openReportModal('${repDate}', '${c.ticker}', 'plan', event); } else { window.location.href='/research/${c.ticker}'; }"
                     onmouseenter="this.style.borderColor='rgba(245,158,11,0.7)'; this.style.background='rgba(245,158,11,0.12)'; this.style.transform='translateY(-1px)';"
                     onmouseleave="this.style.borderColor='var(--border)'; this.style.background='var(--bg-surface)'; this.style.transform='none';"
                     title="${c.ticker} · Click to open Dossier & Tactical Plan (Missing: ${c.missing_condition})"
                     style="background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:5px 9px; font-size:11px; display:inline-flex; align-items:center; gap:6px; cursor:pointer; transition:all 0.15s ease; box-shadow:0 1px 3px rgba(0,0,0,0.15); user-select:none;">
                  <b style="color:var(--cyan); font-family:var(--font-mono); font-size:11.5px;">${c.ticker}</b>
                  ${c.last_price ? `<span style="color:var(--text-main); font-family:var(--font-mono); font-size:10.5px; font-weight:700;">$${parseFloat(c.last_price).toFixed(2)}</span>` : ''}
                  ${(c.dist !== undefined && c.dist !== null) ? `<span style="color:${c.dist === 0 ? 'var(--green)' : 'var(--text-muted)'}; font-family:var(--font-mono); font-size:10px; font-weight:600;">${c.dist === 0 ? '🎯 In Zone' : (c.dist > 0 ? `+${parseFloat(c.dist).toFixed(1)}%` : `${parseFloat(c.dist).toFixed(1)}%`)}</span>` : ''}
                  ${c.wait_for ? `<span style="color:var(--cyan); font-family:var(--font-mono); font-size:10px; font-weight:700;">(${c.wait_for})</span>` : ''}
                  <span style="color:var(--amber); font-size:10px; font-weight:600;">⚠️ ${c.missing_condition}</span>
                  <a href="/research/${c.ticker}" target="_blank" onclick="event.stopPropagation();" title="Open /research/${c.ticker} in new tab" style="color:var(--text-muted); font-size:10.5px; margin-left:2px; text-decoration:none; opacity:0.75;" onmouseenter="this.style.opacity='1'; this.style.color='var(--cyan)';" onmouseleave="this.style.opacity='0.75'; this.style.color='var(--text-muted)';">↗</a>
                </div>
              `;}).join('')}
            </div>
          </div>` : ''}

          <!-- Opportunities Grid -->
          <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(380px, 1fr)); gap:16px;">
        `;

        if (filteredOpps.length === 0) {
          html += `
            <div style="grid-column:1/-1; text-align:center; padding:32px; color:var(--text-muted); font-size:13px; background:var(--bg-main); border-radius:10px; border:1px dashed var(--border);">
              No setups matching filter "${oppFilter}". Check all opportunities or launch deep research from Radar.
            </div>
          `;
        } else {
          filteredOpps.forEach(opp => {
            const sym = opp.ticker;
            const spot = opp.spot_price ? `$${parseFloat(opp.spot_price).toFixed(2)}` : '–';
            const repDate = opp.report_date || 'latest';
            const dist = opp.dist_pct !== null && opp.dist_pct !== undefined ? opp.dist_pct : 0.0;
            const distTxt = opp.in_zone ? '🎯 IN ZONE' : (dist > 0 ? `+${dist.toFixed(2)}%` : `${dist.toFixed(2)}%`);
            const distTone = opp.in_zone ? 'var(--green)' : (Math.abs(dist) <= 1.5 ? 'var(--cyan)' : 'var(--amber)');

            const sideTone = opp.side === 'LONG' ? 'var(--green)' : 'var(--rose-light)';
            const rrTxt = opp.live_rr > 0 ? `R:R ${opp.live_rr.toFixed(2)}:1` : '–';

            // Deterministic risk cross-check: already held / earnings blackout / cluster / chasing / weak R:R
            const riskEntry = ((todayData.action_panel || {}).risk_flags || {})[(sym || '').toUpperCase()];
            const riskChips = (riskEntry && riskEntry.flags) ? riskEntry.flags.map(f => `
              <span style="display:inline-flex; align-items:center; gap:4px; background:var(--bg-subtle); border:1px solid ${f.tone === 'rose' ? 'rgba(220,38,38,0.45)' : (f.tone === 'cyan' ? 'rgba(6,182,212,0.45)' : 'rgba(245,158,11,0.45)')}; border-radius:4px; padding:2px 7px; font-size:9px; font-weight:800; letter-spacing:0.3px; color:${f.tone === 'rose' ? 'var(--rose-light)' : (f.tone === 'cyan' ? 'var(--cyan-glow)' : 'var(--amber)')};" title="${(f.note || '').replace(/"/g, '&quot;')}">${f.tone === 'rose' ? '⛔' : (f.tone === 'cyan' ? '👤' : '⚠️')} ${f.label}</span>`).join('') : '';

            const isOptions = opp.vehicle_type === 'OPTIONS';
            const vehicleBg = isOptions ? 'rgba(168, 85, 247, 0.08)' : 'rgba(56, 189, 248, 0.08)';
            const vehicleBorder = isOptions ? 'rgba(168, 85, 247, 0.3)' : 'rgba(56, 189, 248, 0.3)';
            const vehicleIcon = isOptions ? '📦' : '📊';

            const eLow = opp.entry_low ? `$${parseFloat(opp.entry_low).toFixed(2)}` : '–';
            const eHigh = opp.entry_high ? `$${parseFloat(opp.entry_high).toFixed(2)}` : '–';
            const stopVal = opp.tactical_stop ? `$${parseFloat(opp.tactical_stop).toFixed(2)}` : '–';
            const t1Val = opp.target_1 ? `$${parseFloat(opp.target_1).toFixed(2)}` : '–';
            const t2Val = opp.target_2 ? `$${parseFloat(opp.target_2).toFixed(2)}` : '–';

            html += `
              <div style="background:var(--bg-main); border:1px solid ${riskChips ? 'var(--border-subtle)' : 'var(--border)'}; border-radius:12px; padding:16px; display:flex; flex-direction:column; justify-content:space-between; gap:12px; box-shadow:0 4px 12px rgba(0,0,0,0.2); transition:transform 0.15s ease, border-color 0.15s ease;">
                <div>
                  ${riskChips ? `<div style="display:flex; flex-wrap:wrap; gap:5px; margin-bottom:9px;">${riskChips}</div>` : ''}
                  
                  <!-- Card Header -->
                  <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                    <div>
                      <div style="display:flex; align-items:center; gap:8px;">
                        <span style="font-size:20px; font-weight:900; color:var(--cyan); cursor:pointer;" onclick="AppSwing.openReportModal('${repDate}', '${sym}', 'plan')">
                          ${sym}
                        </span>
                        <span class="badge" style="border-color:${sideTone}; color:${sideTone}; font-weight:800; font-size:10px;">
                          ${opp.side}
                        </span>
                        <span class="badge" style="border-color:${distTone}; color:${distTone}; font-weight:800; font-size:10px;">
                          ${opp.in_zone ? '🎯 IN ZONE' : opp.state_label}
                        </span>
                        ${opp.stage === 'SCREENER_COIL' 
                          ? `<span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.3); font-weight:800; font-size:9.5px;">⚡ SCREENER COIL</span>` 
                          : `<span class="badge" style="background:rgba(16,185,129,0.15); color:#34d399; border:1px solid rgba(16,185,129,0.3); font-weight:800; font-size:9.5px;">🔬 DEEP RESEARCH</span>`
                        }
                        ${opp.ran_since_cut ? `<span class="badge" style="background:rgba(239,68,68,0.12); color:#f87171; border:1px solid rgba(239,68,68,0.3); font-weight:800; font-size:9.5px;" title="Price moved > 1 ATR since triage — candidate needs re-triage">🏃 ${opp.ran_since_cut}</span>` : ''}
                      </div>
                      <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; font-size:10.5px; color:var(--text-muted); margin-top:3px;">
                        <span class="pill ${String(opp.vintage || '').includes('TODAY') ? 'green' : 'cyan'}" style="font-size:9.5px; font-weight:800; padding:1px 6px;">
                          ${opp.vintage || repDate}
                        </span>
                        <span>Conviction: <b>${opp.conviction}/10</b></span>
                        <span>·</span>
                        <span>Score: <b>${opp.score}/100</b></span>
                      </div>
                    </div>

                    <div style="text-align:right;">
                      <div style="font-size:18px; font-weight:900; font-family:var(--font-mono); color:var(--text-main);">${spot}</div>
                      <div style="font-size:11px; font-family:var(--font-mono); font-weight:800; color:${distTone};">${distTxt}</div>
                      ${opp.quote_source ? `<div style="font-size:9px; color:var(--text-muted); font-family:var(--font-mono); margin-top:1px;">${opp.quote_source.toLowerCase().includes('fallback') ? '⚠️ fallback quote' : opp.quote_source.toLowerCase()}</div>` : ''}
                    </div>
                  </div>

                  <!-- Tactical Geometry Grid -->
                  <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:6px; margin-top:10px; background:var(--bg-surface); padding:8px 10px; border-radius:8px; border:1px solid var(--border); font-family:var(--font-mono); font-size:11px;">
                    <div>
                      <div style="font-size:9.5px; color:var(--text-muted); text-transform:uppercase;">Entry Zone</div>
                      <div style="font-weight:700; color:var(--text-main);">${eLow}–${eHigh}</div>
                    </div>
                    <div>
                      <div style="font-size:9.5px; color:var(--rose-light); text-transform:uppercase;">Stop Loss</div>
                      <div style="font-weight:700; color:var(--rose-light);">${stopVal}</div>
                    </div>
                    <div>
                      <div style="font-size:9.5px; color:var(--green); text-transform:uppercase;">Target 1 (50%)</div>
                      <div style="font-weight:700; color:var(--green);">${t1Val}</div>
                    </div>
                  </div>

                  <!-- Execution Vehicle Blueprint -->
                  <div style="margin-top:10px; background:${vehicleBg}; border:1px solid ${vehicleBorder}; border-radius:8px; padding:10px 12px; font-size:11.5px; line-height:1.4;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                      <div style="font-size:10px; font-weight:800; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px;">
                        ${vehicleIcon} EXECUTION BLUEPRINT
                      </div>
                      <span class="badge" style="border-color:${(opp.live_rr && opp.live_rr < 2.0) ? 'var(--amber)' : 'var(--green)'}; color:${(opp.live_rr && opp.live_rr < 2.0) ? 'var(--amber)' : 'var(--green)'}; font-size:9.5px; font-weight:800;">
                        ${rrTxt}${(opp.live_rr && opp.live_rr > 0 && opp.live_rr < 2.0) ? ' · below measured floor' : ''}
                      </span>
                    </div>
                    <div style="font-weight:700; color:var(--text-main);">${opp.vehicle_label}</div>
                    ${opp.rr_paths_text ? `
                    <div style="margin-top:6px; font-size:10.5px; color:var(--text-muted); font-weight:700; font-family:var(--font-mono);" title="Each path's R:R is measured at its own entry — the limit zone at entry_high, the breakout at its trigger. A broken trigger path has targets at or below the trigger and was demoted, not blended.">
                      ${opp.rr_paths_text}
                    </div>` : ''}
                  </div>

                  <!-- PM Thesis / Takeaways -->
                  ${opp.pm_bullets && opp.pm_bullets.length > 0 ? `
                  <div style="margin-top:10px; font-size:11px; color:var(--text-main); line-height:1.4; background:var(--bg-surface); border-radius:8px; padding:8px 10px; border:1px solid var(--border);">
                    <div style="font-size:9.5px; font-weight:800; color:var(--text-muted); text-transform:uppercase; margin-bottom:4px;">⚖️ Institutional PM Directive / Thesis:</div>
                    ${opp.pm_bullets.map(b => `<div style="margin-bottom:3px;">• ${b}</div>`).join('')}
                  </div>` : ''}

                  <!-- Stalk Limit Price Target or Honest PB Note (Task f3) -->
                  ${opp.wait_for ? `
                  <div style="margin-top:8px; font-size:11px; color:var(--cyan); background:rgba(6,182,212,0.08); padding:6px 8px; border-radius:6px; border:1px solid rgba(6,182,212,0.25); font-family:var(--font-mono); font-weight:700;">
                    🎯 <b>Stalk Target Limit:</b> ${opp.wait_for} <span style="font-weight:400; font-size:10px; color:var(--text-muted);">(to achieve measured R:R floor)</span>
                  </div>` : (opp.pb_message ? `
                  <div style="margin-top:8px; font-size:10.5px; color:var(--amber); background:rgba(245,158,11,0.08); padding:6px 8px; border-radius:6px; border:1px solid rgba(245,158,11,0.25); font-weight:700;">
                    ⏳ <b>Structure Note:</b> ${opp.pb_message}
                  </div>` : '')}

                  <!-- Gate Reasons / Blocker -->
                  ${opp.gate_reasons && opp.gate_reasons.length > 0 ? `
                  <div style="margin-top:8px; font-size:10.5px; color:var(--amber); background:rgba(245,158,11,0.08); padding:6px 8px; border-radius:6px; border:1px solid rgba(245,158,11,0.25);">
                    ⚠️ <b>Gate Blocker:</b> ${opp.gate_reasons[0]}
                  </div>` : ''}

                  <!-- Invalidation Rule -->
                  ${opp.invalidation && (opp.invalidation.rationale || opp.invalidation.level) ? `
                  <div style="margin-top:8px; font-size:10px; color:var(--rose-light); font-family:var(--font-mono); background:rgba(244,63,94,0.05); padding:6px 8px; border-radius:6px; border:1px solid rgba(244,63,94,0.2);">
                    🛑 <b>Invalidation:</b> ${opp.invalidation.rationale || `Defended floor stop breached at $${opp.invalidation.level}`}
                  </div>` : ''}

                </div>

                <!-- 1-Click Action Buttons -->
                <div style="display:flex; gap:6px; margin-top:10px; flex-wrap:wrap;">
                  <button class="btn" style="flex:1; min-width:110px; font-size:11px; padding:6px 10px; font-weight:800; background:linear-gradient(135deg, #059669 0%, #10b981 100%); color:white;" onclick="AppDesk.openActionablePosition('${sym}', '${opp.side}', ${opp.entry_low || opp.spot_price || 0}, ${opp.tactical_stop || 0}, ${opp.target_1 || 0}, '${opp.vehicle_label ? opp.vehicle_label.replace(/'/g, '') : 'Equity'}')">
                    🚀 Take Trade
                  </button>
                  <button class="btn secondary" style="flex:1; min-width:90px; font-size:11px; padding:6px 8px; font-weight:700; color:var(--cyan); border-color:rgba(6,182,212,0.4);" onclick="AppSwing.openReportModal('${repDate}', '${sym}', 'plan')">
                    📑 Dossier
                  </button>
                  ${opp.needs_deep_research 
                    ? `<button class="btn" style="font-size:11px; padding:6px 12px; font-weight:800; background:linear-gradient(135deg, #7c3aed 0%, #a855f7 100%); color:white;" onclick="AppDesk.triggerDeep('${sym}', this)" title="Dispatch candidate into Autonomous Deep Research">
                        🔬 Run Deep Research
                      </button>`
                    : `<button class="btn secondary" style="font-size:11px; padding:6px 10px;" onclick="AppDesk.triggerDeep('${sym}', this)" title="Re-run Multi-Pass Deep Research">
                        🔬 Research
                      </button>`
                  }
                  <a href="https://www.tradingview.com/chart/jPAQSlZC/?symbol=${sym}&interval=D" target="_blank" class="btn secondary" style="font-size:11px; padding:6px 10px; text-decoration:none; display:inline-flex; align-items:center; justify-content:center;" title="Open in your logged-in TradingView session with the Rev - Enhanced v2 layout">
                    📈 TV
                  </a>
                </div>

              </div>
            `;
          });
        }

        html += `</div></div>`;
      }

      // =========================================================================
      // 3. SUBTAB: ACTIONABLE ALERTS STREAM (TRADINGVIEW & SCREENERS)
      // =========================================================================
      else if (activeTab === 'alerts') {
        html += `
        <div class="station-card" style="padding:16px 20px;">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:14px;">
            <div>
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:18px;">🚨</span>
                <span style="font-size:14px; font-weight:900; color:var(--text-main); letter-spacing:0.5px;">
                  REAL-TIME ACTIONABLE ALERTS COCKPIT
                </span>
                <span class="pill cyan" style="font-weight:800; font-size:10px;">${actionableAlerts.length} Active Triggers</span>
              </div>
              <div style="font-size:11px; color:var(--text-muted); margin-top:3px;">
                Live-filtered alerts with proximity to entry, measured risk/reward, and 1-click execution.
              </div>
            </div>

            <div style="display:flex; gap:8px; align-items:center;">
              <button class="btn secondary" style="font-size:11px; padding:4px 10px;" onclick="AppAlerts.checkGmailNow()">
                📥 Check Gmail (TV Alerts)
              </button>
              <button class="btn secondary" style="font-size:11px; padding:4px 10px;" onclick="App.switchDesk('alerts')">
                View All Ingested ↗
              </button>
            </div>
          </div>

          <!-- Actionable Alerts Table -->
          <div style="overflow-x:auto;">
            <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px; min-width:850px;">
              <thead>
                <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border);">
                  <th style="padding:8px 10px; text-align:left;">Symbol</th>
                  <th style="padding:8px 10px; text-align:left;">Signal / Action</th>
                  <th style="padding:8px 10px; text-align:center;">State / Proximity</th>
                  <th style="padding:8px 10px; text-align:right;">Trigger Px</th>
                  <th style="padding:8px 10px; text-align:right;">Spot</th>
                  <th style="padding:8px 10px; text-align:right;">Dist %</th>
                  <th style="padding:8px 10px; text-align:right;">Stop</th>
                  <th style="padding:8px 10px; text-align:right;">Target</th>
                  <th style="padding:8px 10px; text-align:right;">R:R</th>
                  <th style="padding:8px 10px; text-align:right;">Actions</th>
                </tr>
              </thead>
              <tbody>
        `;

        if (actionableAlerts.length === 0) {
          html += `<tr><td colspan="10" style="text-align:center; padding:24px; color:var(--text-muted);">No fresh actionable alerts right now. System is polling Gmail &amp; market feeds.</td></tr>`;
        } else {
          actionableAlerts.forEach(a => {
            const sym = a.symbol;
            const side = a.side || 'LONG';
            const sideTone = side === 'LONG' ? 'var(--green)' : 'var(--rose-light)';
            const stateTone = a.in_zone ? 'var(--green)' : (Math.abs(a.dist_pct || 0) <= 1.5 ? 'var(--cyan)' : 'var(--amber)');
            const rrTxt = (a.live_rr || 0) > 0 ? `${a.live_rr.toFixed(1)}:1` : '–';

            html += `
              <tr style="border-bottom:1px solid var(--border);">
                <td style="padding:8px 10px; font-weight:800; color:var(--cyan); cursor:pointer;" onclick="AppSwing.openReportModal('${(a.date || '').substring(0, 10)}', '${sym}', 'local')">
                  ${sym}
                </td>
                <td style="padding:8px 10px;">
                  <span class="badge" style="border-color:${sideTone}; color:${sideTone}; font-weight:800; font-size:10px;">
                    ${a.action} (${a.strategy || 'Daily'})
                  </span>
                </td>
                <td style="padding:8px 10px; text-align:center;">
                  <span class="badge" style="border-color:${stateTone}; color:${stateTone}; font-weight:800; font-size:10px;">
                    ${a.state_label}
                  </span>
                </td>
                <td style="padding:8px 10px; text-align:right; font-family:var(--font-mono); font-weight:700;">$${(a.entry_price || 0).toFixed(2)}</td>
                <td style="padding:8px 10px; text-align:right; font-family:var(--font-mono);">$${(a.spot_price || 0).toFixed(2)}</td>
                <td style="padding:8px 10px; text-align:right; font-family:var(--font-mono); color:${stateTone}; font-weight:800;">
                  ${a.in_zone ? '🎯 IN ZONE' : (a.dist_pct > 0 ? `+${a.dist_pct}%` : `${a.dist_pct}%`)}
                </td>
                <td style="padding:8px 10px; text-align:right; font-family:var(--font-mono); color:var(--rose-light);">$${(a.stop_price || 0).toFixed(2)}</td>
                <td style="padding:8px 10px; text-align:right; font-family:var(--font-mono); color:var(--green);">$${(a.target_price || 0).toFixed(2)}</td>
                <td style="padding:8px 10px; text-align:right; font-family:var(--font-mono); font-weight:700; color:var(--green);">${rrTxt}</td>
                <td style="padding:8px 10px; text-align:right;">
                  <div style="display:inline-flex; gap:4px;">
                    <button class="btn" style="font-size:10px; padding:3px 8px; font-weight:800;" onclick="AppDesk.openActionablePosition('${sym}', '${side}', ${a.entry_price || 0}, ${a.stop_price || 0}, ${a.target_price || 0}, 'Alert Execution', '${(a.setup || 'Alert Trigger').replace(/'/g, '')}')">
                      🚀 Take
                    </button>
                    <button class="btn secondary" style="font-size:10px; padding:3px 8px;" onclick="AppDesk.triggerDeep('${sym}', this)">
                      🔬 Deep
                    </button>
                  </div>
                </td>
              </tr>
            `;
          });
        }

        html += `</tbody></table></div></div>`;
      }

      // =========================================================================
      // 4. SUBTAB: POSITION SURVEILLANCE & EVALUATION FEEDBACK LOOP
      // =========================================================================
      else if (activeTab === 'positions') {
        html += `
        <div style="display:flex; flex-direction:column; gap:16px;">

          <!-- Scorecard KPI Banner -->
          <div class="station-card" style="padding:14px 20px; display:flex; gap:20px; flex-wrap:wrap; align-items:center;">
            <div>
              <div style="font-size:13px; font-weight:900; color:var(--text-main); letter-spacing:0.5px;">
                🏆 PERFORMANCE SCORECARD &amp; FEEDBACK LOOP
              </div>
              <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">
                Audit of trade lifecycle outcomes, discipline grading, and continuous error elimination.
              </div>
            </div>

            <div style="display:flex; gap:14px; flex-wrap:wrap; font-family:var(--font-mono); font-size:11px; margin-left:auto;">
              <div><span style="color:var(--text-muted);">Total Trades:</span> <b>${scorecard.total_trades || 0}</b></div>
              <div><span style="color:var(--text-muted);">Win Rate:</span> <b style="color:var(--green);">${scorecard.win_rate_pct || 0}%</b></div>
              <div><span style="color:var(--text-muted);">Wins/Losses:</span> <b>${scorecard.wins || 0}W / ${scorecard.losses || 0}L</b></div>
              <div><span style="color:var(--text-muted);">Total Realized R:</span> <b style="color:${(scorecard.total_realized_r || 0) >= 0 ? 'var(--green)' : 'var(--rose-light)'};">${(scorecard.total_realized_r || 0) >= 0 ? '+' : ''}${scorecard.total_realized_r || 0}R</b></div>
              <div><span style="color:var(--text-muted);">Profit Factor:</span> <b>${scorecard.profit_factor || 1.0}</b></div>
              <div><span style="color:var(--text-muted);">Expectancy:</span> <b>${scorecard.expectancy || 0.0}R/trade</b></div>
            </div>
          </div>

          <!-- PART A: ACTIVE OPEN POSITIONS SURVEILLANCE -->
          <div class="station-card" style="padding:16px 20px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; flex-wrap:wrap; gap:10px;">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:16px;">💼</span>
                <span style="font-size:13px; font-weight:800; color:var(--text-main); text-transform:uppercase;">
                  ACTIVE OPEN POSITIONS (${openPositions.length})
                </span>
                <span class="pill green" style="font-size:9.5px;">Live R-Ratcheting Active</span>
              </div>
              <div style="font-size:11px; color:var(--text-muted);">
                Single Source of Truth: data/positions.json + trading_alerts.db
              </div>
            </div>

            <div style="overflow-x:auto;">
              <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px; min-width:850px;">
                <thead>
                  <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border);">
                    <th style="padding:8px 10px; text-align:left;">Ticker</th>
                    <th style="padding:8px 10px; text-align:left;">Side</th>
                    <th style="padding:8px 10px; text-align:right;">Entry</th>
                    <th style="padding:8px 10px; text-align:right;">Spot</th>
                    <th style="padding:8px 10px; text-align:right;">Stop Loss</th>
                    <th style="padding:8px 10px; text-align:right;">Target</th>
                    <th style="padding:8px 10px; text-align:right;">Unrealized R</th>
                    <th style="padding:8px 10px; text-align:right;">P&amp;L ($)</th>
                    <th style="padding:8px 10px; text-align:center;">Scale / Trail State</th>
                    <th style="padding:8px 10px; text-align:right;">Execution Actions</th>
                  </tr>
                </thead>
                <tbody>
          `;

          if (openPositions.length === 0) {
            html += `<tr><td colspan="10" style="text-align:center; padding:20px; color:var(--text-muted);">No open positions currently being tracked. Click "Take Trade" on any actionable opportunity to begin surveillance.</td></tr>`;
          } else {
            openPositions.forEach(p => {
              const sym = p.ticker;
              const side = p.side;
              const sideTone = side === 'LONG' ? 'var(--green)' : 'var(--rose-light)';
              const pnlColor = (p.unrealized_pnl || 0) >= 0 ? 'var(--green)' : 'var(--rose-light)';
              const rColor = (p.unrealized_r || 0) >= 0 ? 'var(--green)' : 'var(--rose-light)';

              html += `
                <tr style="border-bottom:1px solid var(--border);">
                  <td style="padding:8px 10px; font-weight:800; color:var(--cyan); cursor:pointer;" onclick="AppSwing.openReportModal(null, '${sym}', 'plan')">
                    ${sym}
                  </td>
                  <td style="padding:8px 10px;">
                    <span class="badge" style="border-color:${sideTone}; color:${sideTone}; font-weight:800; font-size:10px;">
                      ${side}
                    </span>
                  </td>
                  <td style="padding:8px 10px; text-align:right; font-family:var(--font-mono); font-weight:700;">$${(p.entry_price || 0).toFixed(2)}</td>
                  <td style="padding:8px 10px; text-align:right; font-family:var(--font-mono); font-weight:800;">$${(p.spot_price || 0).toFixed(2)}</td>
                  <td style="padding:8px 10px; text-align:right; font-family:var(--font-mono); color:var(--rose-light);">$${(p.stop_loss || 0).toFixed(2)}</td>
                  <td style="padding:8px 10px; text-align:right; font-family:var(--font-mono); color:var(--green);">$${(p.target || 0).toFixed(2)}</td>
                  <td style="padding:8px 10px; text-align:right; font-family:var(--font-mono); font-weight:800; color:${rColor};">
                    ${(p.unrealized_r || 0) >= 0 ? '+' : ''}${(p.unrealized_r || 0).toFixed(2)}R
                  </td>
                  <td style="padding:8px 10px; text-align:right; font-family:var(--font-mono); font-weight:800; color:${pnlColor};">
                    ${(p.unrealized_pnl || 0) >= 0 ? '+' : ''}$${(p.unrealized_pnl || 0).toFixed(2)} (${p.pnl_pct || 0}%)
                  </td>
                  <td style="padding:8px 10px; text-align:center;">
                    ${p.scaled_at_t1 
                      ? `<span class="badge" style="border-color:var(--green); color:var(--green); font-size:9.5px; font-weight:800;">🎯 T1 Scaled (BE Locked)</span>`
                      : (p.be_locked ? `<span class="badge" style="border-color:var(--cyan); color:var(--cyan); font-size:9.5px;">🛡️ BE Trailed</span>` : `<span class="badge" style="border-color:var(--border); color:var(--text-muted); font-size:9.5px;">Full Size</span>`)
                    }
                  </td>
                  <td style="padding:8px 10px; text-align:right;">
                    <div style="display:inline-flex; gap:4px;">
                      ${!p.scaled_at_t1 ? `
                        <button class="btn secondary" style="font-size:10px; padding:3px 8px; color:var(--green); font-weight:700;" onclick="AppDesk.managePosition('${sym}', 'SCALE_50', ${p.spot_price || 0})" title="Lock 50% Profit and Ratchet Stop to Breakeven">
                          Scale 50%
                        </button>
                      ` : ''}
                      ${!p.be_locked ? `
                        <button class="btn secondary" style="font-size:10px; padding:3px 8px;" onclick="AppDesk.managePosition('${sym}', 'TRAIL_BE')" title="Ratchet Stop to Breakeven">
                          Trail BE
                        </button>
                      ` : ''}
                      <button class="btn secondary" style="font-size:10px; padding:3px 8px; color:var(--rose-light); font-weight:700;" onclick="AppDesk.managePosition('${sym}', 'CLOSE', ${p.spot_price || 0})" title="Flatten Position">
                        Flatten
                      </button>
                    </div>
                  </td>
                </tr>
              `;
            });
          }

          html += `</tbody></table></div></div>`;

          // PART B: EVALUATED CLOSED POSITIONS & POST-MORTEM FEEDBACK LOOP
          html += `
          <div class="station-card" style="padding:16px 20px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; flex-wrap:wrap; gap:10px;">
              <div>
                <div style="display:flex; align-items:center; gap:8px;">
                  <span style="font-size:16px;">🔍</span>
                  <span style="font-size:13px; font-weight:900; color:var(--text-main); text-transform:uppercase;">
                    POST-MORTEM EVALUATION FEEDBACK LOOP ("WHAT NEEDS IMPROVING")
                  </span>
                  <span class="pill cyan" style="font-size:9.5px;">${closedPositions.length} Evaluated Trades</span>
                </div>
                <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">
                  Automated execution diagnosis, grade auditing, and persistent lesson retention to eliminate recurring mistakes.
                </div>
              </div>
            </div>

            <div style="display:flex; flex-direction:column; gap:12px;">
          `;

          if (closedPositions.length === 0) {
            html += `<div style="text-align:center; padding:24px; color:var(--text-muted); font-size:12.5px;">No closed trades evaluated yet. As trades exit, automated post-mortem diagnoses will appear here.</div>`;
          } else {
            closedPositions.forEach(c => {
              const tradeId = c.trade_id;
              const sym = c.ticker;
              const outcome = c.outcome || 'SCRATCH';
              const outcomeTone = outcome === 'WIN' ? 'var(--green)' : (outcome === 'LOSS' ? 'var(--rose-light)' : 'var(--amber)');
              const rTone = (c.realized_r || 0) >= 0 ? 'var(--green)' : 'var(--rose-light)';
              const grade = c.execution_grade || c.auto_grade || 'B';

              html += `
                <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:10px; padding:14px 16px; display:flex; flex-direction:column; gap:10px;">
                  
                  <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:10px;">
                    <div style="display:flex; align-items:center; gap:10px;">
                      <span style="font-size:16px; font-weight:900; color:var(--cyan); cursor:pointer;" onclick="AppSwing.openReportModal(null, '${sym}', 'plan')">
                        ${sym}
                      </span>
                      <span class="badge" style="border-color:${outcomeTone}; color:${outcomeTone}; font-weight:800; font-size:10px;">
                        ${outcome}
                      </span>
                      <span class="badge" style="border-color:var(--text-main); color:var(--text-main); font-weight:900; font-size:10px;">
                        Grade: ${grade}
                      </span>
                      <span style="font-size:11px; font-family:var(--font-mono); color:var(--text-muted);">
                        Entry: $${(c.entry_price || 0).toFixed(2)} ➔ Exit: $${(c.exit_price || 0).toFixed(2)}
                      </span>
                    </div>

                    <div style="text-align:right; font-family:var(--font-mono);">
                      <span style="font-size:15px; font-weight:900; color:${rTone};">
                        ${(c.realized_r || 0) >= 0 ? '+' : ''}${(c.realized_r || 0).toFixed(2)}R
                      </span>
                      <span style="font-size:10.5px; color:var(--text-muted); margin-left:6px;">(${c.exit_reason})</span>
                    </div>
                  </div>

                  <!-- Automated Diagnostic Box -->
                  <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:8px 12px; font-size:11px; line-height:1.4; color:var(--text-main);">
                    <b>⚡ Diagnostic:</b> ${c.diagnostic}
                  </div>

                  <!-- Interactive Lesson / Feedback Form -->
                  <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; background:rgba(0,0,0,0.15); padding:8px 10px; border-radius:6px; border:1px dashed var(--border);">
                    <div style="display:flex; align-items:center; gap:6px;">
                      <span style="font-size:10.5px; font-weight:700; color:var(--text-muted);">Grade:</span>
                      <select id="fb-grade-${tradeId}" style="font-size:11px; padding:2px 6px; background:var(--bg-input); color:var(--text-main); border:1px solid var(--border); border-radius:4px;">
                        <option value="A" ${grade === 'A' ? 'selected' : ''}>A (Disciplined)</option>
                        <option value="B" ${grade === 'B' ? 'selected' : ''}>B (Good)</option>
                        <option value="C" ${grade === 'C' ? 'selected' : ''}>C (Mistake / Chased)</option>
                        <option value="D" ${grade === 'D' ? 'selected' : ''}>D (Rule Breached)</option>
                      </select>
                    </div>

                    <input type="text" id="fb-lesson-${tradeId}" value="${(c.user_lesson || '').replace(/"/g, '&quot;')}" placeholder="What to improve on next trade? (e.g. stop too tight, chased entry...)" style="flex:2; min-width:200px; font-size:11px; padding:4px 8px; background:var(--bg-input); color:var(--text-main); border:1px solid var(--border); border-radius:4px;">

                    <input type="text" id="fb-notes-${tradeId}" value="${(c.user_notes || '').replace(/"/g, '&quot;')}" placeholder="Trader notes..." style="flex:1; min-width:140px; font-size:11px; padding:4px 8px; background:var(--bg-input); color:var(--text-main); border:1px solid var(--border); border-radius:4px;">

                    <button class="btn secondary" id="fb-save-btn-${tradeId}" style="font-size:10.5px; padding:4px 10px; font-weight:700;" onclick="AppDesk.saveFeedbackNotes('${tradeId}', '${sym}')">
                      Save Notes
                    </button>
                  </div>

                </div>
              `;
            });
          }

          html += `</div></div></div>`;
        }

        else if (activeTab === 'coverage') {
          const cov = coverageData || {};
          const capDeferred = cov.deep_cap_deferred || [];
          const watchNoDeep = cov.watch_no_deep || [];

          html += `
          <div style="display:flex; flex-direction:column; gap:16px;">
            <div class="station-card" style="padding:16px 20px;">
              <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:14px;">
                <div>
                  <div style="display:flex; align-items:center; gap:8px;">
                    <span style="font-size:18px;">🛡️</span>
                    <span style="font-size:14px; font-weight:900; color:var(--text-main); letter-spacing:0.5px;">
                      PIPELINE COVERAGE &amp; DEFERRAL AUDIT
                    </span>
                    <span class="pill cyan" style="font-weight:800; font-size:10px;">${capDeferred.length} Deferred · ${watchNoDeep.length} Stalking</span>
                  </div>
                  <div style="font-size:11px; color:var(--text-muted); margin-top:3px;">
                    Explicit accounting of all pipeline bottlenecks. No setups are silently dropped.
                  </div>
                </div>
              </div>

              <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:10px; margin-bottom:16px;">
                <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:10px 14px; text-align:center;">
                  <div style="font-size:10px; color:var(--text-muted); font-weight:800; text-transform:uppercase;">Total Ingested</div>
                  <div style="font-size:18px; font-weight:900; color:var(--text-main); margin-top:4px;">${cov.alerts || 0}</div>
                </div>
                <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:10px 14px; text-align:center;">
                  <div style="font-size:10px; color:var(--text-muted); font-weight:800; text-transform:uppercase;">Local Researched</div>
                  <div style="font-size:18px; font-weight:900; color:var(--cyan); margin-top:4px;">${cov.local_done || 0}</div>
                </div>
                <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:10px 14px; text-align:center;">
                  <div style="font-size:10px; color:var(--text-muted); font-weight:800; text-transform:uppercase;">Local PASS</div>
                  <div style="font-size:18px; font-weight:900; color:var(--green); margin-top:4px;">${cov.local_pass || 0}</div>
                </div>
                <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:10px 14px; text-align:center;">
                  <div style="font-size:10px; color:var(--text-muted); font-weight:800; text-transform:uppercase;">Deep Completed</div>
                  <div style="font-size:18px; font-weight:900; color:var(--blue-light); margin-top:4px;">${cov.deep_done || 0}</div>
                </div>
                <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:10px 14px; text-align:center;">
                  <div style="font-size:10px; color:var(--text-muted); font-weight:800; text-transform:uppercase;">Deep Queued</div>
                  <div style="font-size:18px; font-weight:900; color:var(--amber); margin-top:4px;">${(cov.deep_queued || []).length}</div>
                </div>
              </div>

              <!-- DEEP CAP DEFERRED TABLE -->
              <div style="margin-bottom:20px;">
                <div style="font-size:12.5px; font-weight:800; color:var(--amber); margin-bottom:8px; display:flex; align-items:center; gap:6px;">
                  <span>⏳ DEEP_CAP_DEFERRED (${capDeferred.length})</span>
                  <span style="font-size:10.5px; color:var(--text-muted); font-weight:normal;">– Passed local triage but deferred by daily research cap</span>
                </div>
                ${capDeferred.length === 0 ? `
                  <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:14px; text-align:center; color:var(--text-muted); font-size:11.5px;">
                    No candidates deferred by daily research cap today.
                  </div>
                ` : `
                  <div style="overflow-x:auto; border:1px solid var(--border); border-radius:8px;">
                    <table style="width:100%; border-collapse:collapse; font-size:11.5px;">
                      <thead>
                        <tr style="background:var(--bg-main); text-align:left; color:var(--text-muted);">
                          <th style="padding:8px 12px;">Ticker</th>
                          <th style="padding:8px 12px;">Deferral Reason</th>
                          <th style="padding:8px 12px; text-align:right;">Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${capDeferred.map(item => `
                          <tr style="border-top:1px solid var(--border);">
                            <td style="padding:8px 12px; font-weight:900; color:var(--cyan); cursor:pointer;" onclick="AppSwing.openReportModal(null, '${item.ticker}', 'plan')">
                              ${item.ticker}
                            </td>
                            <td style="padding:8px 12px; color:var(--text-main);">
                              ${item.reason || 'Deferred by daily GPU / deep research slot cap'}
                            </td>
                            <td style="padding:8px 12px; text-align:right;">
                              <button class="btn secondary" style="padding:3px 8px; font-size:10px;" onclick="AppDesk.triggerDeep('${item.ticker}', this)" title="Dispatch this deferred candidate into Deep Research">Run Research</button>
                            </td>
                          </tr>
                        `).join('')}
                      </tbody>
                    </table>
                  </div>
                `}
              </div>

              <!-- WATCH NO DEEP TABLE -->
              <div>
                <div style="font-size:12.5px; font-weight:800; color:var(--text-main); margin-bottom:8px; display:flex; align-items:center; gap:6px;">
                  <span>⏳ WATCH_NO_DEEP (${watchNoDeep.length})</span>
                  <span style="font-size:10.5px; color:var(--text-muted); font-weight:normal;">– Free local triage assigned WATCH; stalking trigger levels</span>
                </div>
                ${watchNoDeep.length === 0 ? `
                  <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:14px; text-align:center; color:var(--text-muted); font-size:11.5px;">
                    No candidates stalking in WATCH_NO_DEEP today.
                  </div>
                ` : `
                  <div style="overflow-x:auto; border:1px solid var(--border); border-radius:8px;">
                    <table style="width:100%; border-collapse:collapse; font-size:11.5px;">
                      <thead>
                        <tr style="background:var(--bg-main); text-align:left; color:var(--text-muted);">
                          <th style="padding:8px 12px;">Ticker</th>
                          <th style="padding:8px 12px;">Stalking Reason &amp; Setup Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${watchNoDeep.map(item => `
                          <tr style="border-top:1px solid var(--border);">
                            <td style="padding:8px 12px; font-weight:900; color:var(--cyan); cursor:pointer;" onclick="AppSwing.openReportModal(null, '${item.ticker}', 'plan')">
                              ${item.ticker}
                            </td>
                            <td style="padding:8px 12px; color:var(--text-muted);">
                              ${item.reason || 'Awaiting entry zone / price compression trigger'}
                            </td>
                          </tr>
                        `).join('')}
                      </tbody>
                    </table>
                  </div>
                `}
              </div>
            </div>
          </div>
          `;
        }

        else if (activeTab === 'missed_moves') {
          const mm = missedMovesData || {};
          const missedList = mm.missed_moves || [];
          const rollup = mm.weekly_rollup || {};

          html += `
          <div style="display:flex; flex-direction:column; gap:16px;">
            <div class="station-card" style="padding:16px 20px;">
              <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:14px;">
                <div>
                  <div style="display:flex; align-items:center; gap:8px;">
                    <span style="font-size:18px;">📉</span>
                    <span style="font-size:14px; font-weight:900; color:var(--text-main); letter-spacing:0.5px;">
                      MISSED-MOVE SCORECARD &amp; ROOT CAUSE ROLLUP
                    </span>
                    <span class="pill amber" style="font-weight:800; font-size:10px;">${missedList.length} Moves Analyzed</span>
                  </div>
                  <div style="font-size:11px; color:var(--text-muted); margin-top:3px;">
                    Tuning signal to eliminate history-writing: tracks universe names up ≥8% in 5 sessions joined to our last verdict and failure mode.
                  </div>
                </div>
              </div>

              <!-- Weekly Rollup Strip -->
              <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:10px; margin-bottom:18px;">
                <div style="background:var(--bg-main); border:1px solid rgba(239,68,68,0.3); border-radius:8px; padding:10px 14px;">
                  <div style="font-size:10px; color:var(--rose-light); font-weight:800; text-transform:uppercase;">🕒 Stale Data</div>
                  <div style="font-size:18px; font-weight:900; color:var(--rose-light); margin-top:4px;">${rollup.stale_data || 0}</div>
                  <div style="font-size:9.5px; color:var(--text-muted); margin-top:2px;">Old bar / frozen spot</div>
                </div>
                <div style="background:var(--bg-main); border:1px solid rgba(245,158,11,0.3); border-radius:8px; padding:10px 14px;">
                  <div style="font-size:10px; color:var(--amber); font-weight:800; text-transform:uppercase;">🏃 Chased / Breakout</div>
                  <div style="font-size:18px; font-weight:900; color:var(--amber); margin-top:4px;">${rollup.chased || 0}</div>
                  <div style="font-size:9.5px; color:var(--text-muted); margin-top:2px;">Above zone / extended</div>
                </div>
                <div style="background:var(--bg-main); border:1px solid rgba(139,92,246,0.3); border-radius:8px; padding:10px 14px;">
                  <div style="font-size:10px; color:var(--purple-light); font-weight:800; text-transform:uppercase;">🚫 Filtered (No-PB)</div>
                  <div style="font-size:18px; font-weight:900; color:var(--purple-light); margin-top:4px;">${rollup['no-PB'] || 0}</div>
                  <div style="font-size:9.5px; color:var(--text-muted); margin-top:2px;">Filtered before research</div>
                </div>
                <div style="background:var(--bg-main); border:1px solid rgba(6,182,212,0.3); border-radius:8px; padding:10px 14px;">
                  <div style="font-size:10px; color:var(--cyan); font-weight:800; text-transform:uppercase;">🛑 Cap Deferred</div>
                  <div style="font-size:18px; font-weight:900; color:var(--cyan); margin-top:4px;">${rollup.cap || 0}</div>
                  <div style="font-size:9.5px; color:var(--text-muted); margin-top:2px;">Passed but cap reached</div>
                </div>
                <div style="background:var(--bg-main); border:1px solid rgba(16,185,129,0.3); border-radius:8px; padding:10px 14px;">
                  <div style="font-size:10px; color:var(--green); font-weight:800; text-transform:uppercase;">⏳ No Setup / Watch</div>
                  <div style="font-size:18px; font-weight:900; color:var(--green); margin-top:4px;">${rollup.no_setup || 0}</div>
                  <div style="font-size:9.5px; color:var(--text-muted); margin-top:2px;">Held in shadow watch</div>
                </div>
                <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:10px 14px;">
                  <div style="font-size:10px; color:var(--text-muted); font-weight:800; text-transform:uppercase;">⚠️ No Levels / Other</div>
                  <div style="font-size:18px; font-weight:900; color:var(--text-main); margin-top:4px;">${(rollup.no_levels || 0) + (rollup.other || 0)}</div>
                  <div style="font-size:9.5px; color:var(--text-muted); margin-top:2px;">Degenerate / uncaught</div>
                </div>
              </div>

              <!-- MISSED MOVES TABLE -->
              <div style="overflow-x:auto; border:1px solid var(--border); border-radius:8px;">
                <table style="width:100%; border-collapse:collapse; font-size:11.5px;">
                  <thead>
                    <tr style="background:var(--bg-main); text-align:left; color:var(--text-muted);">
                      <th style="padding:10px 12px;">Ticker</th>
                      <th style="padding:10px 12px;">5-Day Move</th>
                      <th style="padding:10px 12px;">Dossier Spot</th>
                      <th style="padding:10px 12px;">Live Spot</th>
                      <th style="padding:10px 12px;">Last Verdict</th>
                      <th style="padding:10px 12px;">Lane</th>
                      <th style="padding:10px 12px;">Root Cause Diagnosis</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${missedList.map(item => `
                      <tr style="border-top:1px solid var(--border);">
                        <td style="padding:10px 12px; font-weight:900; color:var(--cyan); cursor:pointer;" onclick="AppSwing.openReportModal(null, '${item.ticker}', 'plan')">
                          ${item.ticker}
                          ${item.is_backfill ? `<span class="badge amber" style="font-size:9px; padding:1px 5px; margin-left:6px; font-weight:800;">BACKFILL</span>` : `<span class="badge cyan" style="font-size:9px; padding:1px 5px; margin-left:6px; font-weight:800;">LIVE</span>`}
                        </td>
                        <td style="padding:10px 12px; font-weight:900; color:var(--green);">
                          +${item.move_pct}%
                        </td>
                        <td style="padding:10px 12px; font-family:var(--font-mono);">
                          $${item.dossier_spot}
                        </td>
                        <td style="padding:10px 12px; font-family:var(--font-mono); font-weight:800; color:var(--text-main);">
                          $${item.live_spot}
                        </td>
                        <td style="padding:10px 12px;">
                          <span class="badge ${item.last_verdict === 'PASS' ? 'green' : (item.last_verdict === 'CUT' ? 'red' : 'amber')}">
                            ${item.last_verdict}
                          </span>
                        </td>
                        <td style="padding:10px 12px; font-family:var(--font-mono); font-size:10.5px;">
                          ${item.lane || '–'}
                        </td>
                        <td style="padding:10px 12px;">
                          <div style="font-weight:800; color:${item.root_cause_reason === 'stale_data' ? 'var(--rose-light)' : 'var(--amber)'};">
                            ${item.root_cause_reason}
                          </div>
                          <div style="font-size:10px; color:var(--text-muted); margin-top:2px;">
                            ${item.details || item.cut_reason || ''}
                          </div>
                        </td>
                      </tr>
                    `).join('')}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          `;
        }

        html += `</div>`;
        this._renderFull(html);

      } catch (err) {
        console.error('Failed loading Today desk:', err);
        const cont = document.getElementById('today-content-container');
        if (cont && !silent) {
          cont.innerHTML = `<div style="color:var(--rose-light); padding:20px; text-align:center;">Failed to load Today desk: ${err}</div>`;
        }
      } finally {
        this._isPollingToday = false;
        this._pollingStartedAt = 0;
      }
    },


  async loadJournal(page = 1) {
    try {
      const statusFilter = document.getElementById('journal-filter-status') ? document.getElementById('journal-filter-status').value : '';
      const laneFilter = document.getElementById('journal-filter-lane') ? document.getElementById('journal-filter-lane').value : '';
      const tickerFilter = document.getElementById('journal-filter-ticker') ? document.getElementById('journal-filter-ticker').value.trim() : '';
      const includeRejected = document.getElementById('journal-filter-rejected') ? (document.getElementById('journal-filter-rejected').checked ? 1 : 0) : 0;

      const query = new URLSearchParams({ page: String(page), include_rejected: String(includeRejected) });
      if (statusFilter) query.set('status', statusFilter);
      if (laneFilter) query.set('lane', laneFilter);
      if (tickerFilter) query.set('ticker', tickerFilter.toUpperCase());

      const data = await window.AppApi.request(`/api/desk/journal?${query.toString()}`);

      const summary = data.summary || {};

      let html = `<div style="display:flex; flex-direction:column; gap:16px;">`;

      // Summary strip
      html += `<div class="station-card" style="padding:12px 18px; display:flex; gap:20px; flex-wrap:wrap; align-items:center;">
        <h3 style="margin:0; margin-right:auto;"><span style="margin-right:8px;">📖</span>Journal</h3>
        <div style="display:flex; gap:12px; flex-wrap:wrap; font-size:11px; font-family:var(--font-mono); font-weight:700;">
          <div><span style="color:var(--text-muted);">Total</span> <b>${summary.total || 0}</b></div>
          <div><span style="color:var(--text-muted);">Closed</span> <b>${summary.closed || 0}</b></div>
          <div><span style="color:var(--text-muted);">Wins</span> <b style="color:var(--green);">${summary.wins || 0}</b></div>
          <div><span style="color:var(--text-muted);">Win</span> <b>${summary.win_pct ? summary.win_pct.toFixed(1) : 0}%</b></div>
          <div><span style="color:var(--text-muted);">Sum R</span> <b style="color:${summary.sum_r && summary.sum_r >= 0 ? 'var(--green)' : 'var(--rose-light)'};">${summary.sum_r !== undefined && summary.sum_r !== null ? ((summary.sum_r >= 0 ? '+' : '') + summary.sum_r.toFixed(2)) : '0.00'}</b></div>
        </div>
      </div>`;

      // Filter bar
      html += `<div class="station-card" style="display:flex; gap:10px; align-items:center; flex-wrap:wrap; padding:10px 18px;">
        <select id="journal-filter-status" onchange="AppDesk.loadJournal()" style="padding:4px 8px; font-size:12px; background:var(--bg-surface); color:var(--text-main); border:1px solid var(--border); border-radius:6px;">
          <option value="">All Statuses</option>
          <option value="OPEN" ${statusFilter === 'OPEN' ? 'selected' : ''}>Open</option>
          <option value="FILLED" ${statusFilter === 'FILLED' ? 'selected' : ''}>Filled</option>
          <option value="CLOSED" ${statusFilter === 'CLOSED' ? 'selected' : ''}>Closed</option>
          <option value="EXPIRED" ${statusFilter === 'EXPIRED' ? 'selected' : ''}>Expired</option>
          <option value="REJECTED" ${statusFilter === 'REJECTED' ? 'selected' : ''}>Rejected</option>
        </select>
        <select id="journal-filter-lane" onchange="AppDesk.loadJournal()" style="padding:4px 8px; font-size:12px; background:var(--bg-surface); color:var(--text-main); border:1px solid var(--border); border-radius:6px;">
          <option value="">All Lanes</option>
          <option value="RR_SETUP" ${laneFilter === 'RR_SETUP' ? 'selected' : ''}>RR_SETUP</option>
          <option value="CODE20" ${laneFilter === 'CODE20' ? 'selected' : ''}>CODE20</option>
          <option value="OVERSOLD" ${laneFilter === 'OVERSOLD' ? 'selected' : ''}>OVERSOLD</option>
          <option value="RSI2" ${laneFilter === 'RSI2' ? 'selected' : ''}>RSI2</option>
          <option value="UNLANED" ${laneFilter === 'UNLANED' ? 'selected' : ''}>UNLANED</option>
        </select>
        <input type="text" id="journal-filter-ticker" value="${tickerFilter}" placeholder="Ticker..." onkeydown="if(event.key==='Enter') AppDesk.loadJournal()" onblur="AppDesk.loadJournal()" style="padding:4px 8px; font-size:12px; background:var(--bg-surface); color:var(--text-main); border:1px solid var(--border); border-radius:6px; width:90px; text-transform:uppercase;">
        <label style="font-size:12px; display:flex; align-items:center; gap:4px; cursor:pointer;">
          <input type="checkbox" id="journal-filter-rejected" onchange="AppDesk.loadJournal()" ${includeRejected ? 'checked' : ''}> Include Rejected
        </label>
        <div style="margin-left:auto; display:flex; gap:6px;">
          <button class="btn secondary" onclick="AppDesk.loadJournal(${page - 1})" ${page <= 1 ? 'disabled' : ''} style="font-size:11px; padding:4px 10px;">Prev</button>
          <span style="font-size:11px; color:var(--text-muted); align-self:center;">Page ${page}</span>
          <button class="btn secondary" onclick="AppDesk.loadJournal(${page + 1})" ${data.items && data.items.length < 50 ? 'disabled' : ''} style="font-size:11px; padding:4px 10px;">Next</button>
        </div>
      </div>`;

      // Table
      html += `<div class="station-card" style="padding:0; overflow-x:auto;">
        <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px; min-width:960px;">
          <thead>
            <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border);">
              <th style="width:24px; padding:8px; text-align:center;"></th>
              <th style="padding:8px; text-align:left;">Date</th>
              <th style="padding:8px; text-align:left;">Ticker</th>
              <th style="padding:8px; text-align:left;">Lane</th>
              <th style="padding:8px; text-align:center;">Status</th>
              <th style="padding:8px; text-align:left;">Entry</th>
              <th style="padding:8px; text-align:right;">Stop</th>
              <th style="padding:8px; text-align:right;">T1</th>
              <th style="padding:8px; text-align:right;">Fill</th>
              <th style="padding:8px; text-align:right;">Exit</th>
              <th style="padding:8px; text-align:right;">R</th>
              <th style="padding:8px; text-align:right;">Bars</th>
              <th style="padding:8px; text-align:left;">Exit reason</th>
            </tr>
          </thead>
          <tbody>`;

      if (!data.items || data.items.length === 0) {
        html += `<tr><td colspan="13" style="text-align:center; padding:16px; color:var(--text-muted);">No journal entries found.</td></tr>`;
      } else {
        data.items.forEach(r => {
          const derivedStatus = r.derived_status || 'OPEN';
          const sym = (r.ticker || '').toUpperCase();
          const dateStr = r.date ? r.date.substring(0, 10) : '–';

          let statusBadge;
          if (derivedStatus === 'IN_TRADE') statusBadge = this.pill('IN_TRADE', 'info');
          else if (derivedStatus === 'IN_ZONE') statusBadge = this.pill('IN_ZONE', 'info');
          else if (derivedStatus === 'FILLED') statusBadge = this.pill('FILLED', 'info');
          else if (derivedStatus === 'CLOSED') statusBadge = this.pill('CLOSED', 'neutral');
          else if (derivedStatus === 'EXPIRED') statusBadge = this.pill('EXPIRED', 'warn');
          else if (derivedStatus === 'REJECTED') statusBadge = this.pill('REJECTED', 'bad');
          else statusBadge = this.pill(derivedStatus, 'neutral');

          const isRejected = derivedStatus === 'REJECTED';
          const rowStyle = isRejected ? 'color:var(--text-muted);' : '';

          const planEntry = r.plan_entry || '–';
          const planStop = r.plan_stop || '–';
          const planT1 = r.plan_t1 || '–';
          const fillPx = r.fill_price !== null && r.fill_price !== undefined ? parseFloat(r.fill_price).toFixed(2) : '–';
          const exitPx = r.exit_price !== null && r.exit_price !== undefined ? parseFloat(r.exit_price).toFixed(2) : '–';
          const rNet = (derivedStatus === 'CLOSED' && r.r_net !== null && r.r_net !== undefined) ? parseFloat(r.r_net).toFixed(2) : '–';
          const rNetColor = rNet !== '–' && parseFloat(rNet) > 0 ? 'color:var(--green);' : rNet !== '–' && parseFloat(rNet) < 0 ? 'color:var(--rose-light);' : '';

          const drawerId = `journal-drawer-${r.id}`;
          const iconId = `journal-icon-${r.id}`;

          html += `<tr style="border-bottom:1px solid var(--border); cursor:pointer; ${rowStyle}" onclick="AppDesk.toggleRowDrawer('${drawerId}', '${iconId}')">
            <td style="padding:8px; text-align:center; color:var(--text-muted); font-size:10px;"><span id="${iconId}">▶</span></td>
            <td style="padding:8px; white-space:nowrap;">${dateStr}</td>
            <td style="padding:8px; font-weight:700; white-space:nowrap;"><a href="javascript:void(0)" onclick="event.stopPropagation(); AppSwing.openReportModal('${dateStr}', '${sym}')">${sym}</a></td>
            <td style="padding:8px; white-space:nowrap;">${this.fmt(r.lane, '–')}${pbBadge(r.pb_funnel)}</td>
            <td style="padding:8px; text-align:center; white-space:nowrap;">${statusBadge}</td>
            <td style="padding:8px; font-family:var(--font-mono); font-size:11px; white-space:nowrap;">${planEntry}</td>
            <td style="padding:8px; text-align:right; color:var(--rose-light); font-family:var(--font-mono); white-space:nowrap;">${planStop}</td>
            <td style="padding:8px; text-align:right; color:var(--green); font-family:var(--font-mono); white-space:nowrap;">${planT1}</td>
            <td style="padding:8px; text-align:right; font-family:var(--font-mono); white-space:nowrap;">${fillPx}</td>
            <td style="padding:8px; text-align:right; font-family:var(--font-mono); white-space:nowrap;">${exitPx}</td>
            <td style="padding:8px; text-align:right; font-weight:700; font-family:var(--font-mono); white-space:nowrap; ${rNetColor}">${rNet}</td>
            <td style="padding:8px; text-align:right; white-space:nowrap;">${r.bars_held !== null && r.bars_held !== undefined ? r.bars_held : '–'}</td>
            <td style="padding:8px; font-size:11px; max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${isRejected ? (r.exit_reason || '').replace(/"/g, '&quot;') : ''}">${isRejected ? (r.exit_reason || '–').substring(0, 40) : (r.exit_reason || '–')}${isRejected && r.exit_reason && r.exit_reason.length > 40 ? '...' : ''}</td>
          </tr>`;
          html += `<tr id="${drawerId}" style="display:none; background:var(--bg-main);">
            <td colspan="13" style="padding:16px;">
              <div style="display:flex; gap:20px; flex-wrap:wrap;">
                <div style="flex:1; min-width:240px;">
                  <h4 style="margin-top:0; color:var(--text-muted); font-size:11px; text-transform:uppercase; letter-spacing:0.5px;">Plan vs Outcome</h4>
                  <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; font-size:11px;">
                    <div><b>Entry Plan:</b> ${planEntry}</div>
                    <div><b>Fill:</b> ${fillPx}</div>
                    <div><b>Stop Plan:</b> <span style="color:var(--rose-light);">${planStop}</span></div>
                    <div><b>Exit:</b> ${exitPx}</div>
                    <div><b>T1 Plan:</b> <span style="color:var(--green);">${planT1}</span></div>
                    <div><b>MAE R:</b> ${r.mae_r !== null && r.mae_r !== undefined ? parseFloat(r.mae_r).toFixed(2) : '–'}</div>
                  </div>
                  ${!isRejected ? `
                  <div style="margin-top:12px;">
                    <b>Notes:</b>
                    <textarea id="journal-notes-${r.id}" style="width:100%; padding:8px; border:1px solid var(--border); border-radius:4px; margin-top:4px; font-size:11px; min-height:80px; resize:vertical; background:var(--bg-surface); color:var(--text-main);">${r.notes || ''}</textarea>
                    <button class="btn secondary" onclick="AppDesk.saveJournalNotes(${r.id})" style="margin-top:6px; font-size:10px; padding:4px 12px;">Save</button>
                  </div>` : `
                  <div style="margin-top:12px;">
                    <b>Rejection Reason:</b>
                    <div style="margin-top:4px; font-size:11px; color:var(--rose-light); background:var(--bg-surface); padding:8px; border:1px solid var(--border); border-radius:4px;">${r.exit_reason || 'Level gate rejected'}</div>
                  </div>`}
                </div>
                <div style="flex:1; min-width:240px;">
                  <h4 style="margin-top:0; color:var(--text-muted); font-size:11px; text-transform:uppercase; letter-spacing:0.5px;">Chart Snapshot</h4>
                  <img src="/api/charts/${dateStr}/${sym}/90d" style="width:100%; border-radius:4px; border:1px solid var(--border); max-height:300px; object-fit:contain;" onerror="this.style.display='none'">
                </div>
              </div>
            </td>
          </tr>`;
        });
      }
      html += `</tbody></table></div></div>`;

      const cont = document.getElementById('journal-content-container');
      if (cont) cont.innerHTML = html;

    } catch (err) {
      console.error(err);
      const cont = document.getElementById('journal-content-container');
      if (cont) cont.innerHTML = `<div style="color:var(--rose-light);">Failed to load Journal: ${err}</div>`;
    }
  },

  async saveJournalNotes(id) {
    const textarea = document.getElementById(`journal-notes-${id}`);
    if (!textarea) return;
    const notes = textarea.value;
    try {
      await window.AppApi.request(`/api/desk/journal/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes })
      });
      textarea.style.borderColor = 'var(--green)';
      setTimeout(() => { textarea.style.borderColor = ''; }, 1000);
    } catch (err) {
      textarea.style.borderColor = 'var(--rose-light)';
      alert("Failed to update notes: " + err);
    }
  },

  async loadRecord(scope) {
    try {
      if (scope) this._activeRecordScope = scope;
      const activeScope = this._activeRecordScope || 'gated';

      const data = await window.AppApi.request(`/api/desk/record?scope=${activeScope}`);

      const totalScored = data.total_scored || 0;
      const firstEta = data.first_score_eta || null;

      let html = `<div style="display:flex; flex-direction:column; gap:20px;">`;

      // Scope toggle
      html += `<div class="station-card" style="display:flex; align-items:center; gap:12px; padding:10px 18px;">
        <div style="font-weight:800; font-size:13px; text-transform:uppercase; letter-spacing:0.5px; color:var(--text-muted);">RECORD</div>
        <div style="display:flex; gap:6px; margin-left:auto;">
          <button id="record-btn-gated" class="btn secondary" style="font-size:11px; padding:4px 12px; ${activeScope === 'gated' ? 'background:var(--text-main); color:var(--bg-surface); border-color:var(--text-main);' : ''}" onclick="AppDesk.loadRecord('gated')">Gated (Default)</button>
          <button id="record-btn-all" class="btn secondary" style="font-size:11px; padding:4px 12px; ${activeScope === 'all' ? 'background:var(--text-main); color:var(--bg-surface); border-color:var(--text-main);' : ''}" onclick="AppDesk.loadRecord('all')">All History</button>
        </div>
      </div>`;

      // Empty state for gated
      if (activeScope === 'gated' && totalScored === 0) {
        html += `<div class="station-card" style="text-align:center; padding:24px;">
          <div style="font-size:13px; color:var(--text-muted); margin-bottom:8px;">No gated trades scored yet.</div>
          <div style="font-size:12px; color:var(--text-muted);">First results around <b>${firstEta || '–'}</b>.</div>
          <button class="btn secondary" onclick="AppDesk.loadRecord('all')" style="margin-top:12px; font-size:11px; padding:6px 14px;">Show all history</button>
        </div>`;
      } else {
        // KPIs
        const sumR = data.sum_r !== undefined ? ((data.sum_r >= 0 ? '+' : '') + data.sum_r.toFixed(2) + ' R') : '0.00 R';
        const meanR = data.mean_r !== undefined ? data.mean_r.toFixed(2) : '0.00';
        const medianR = data.median_r !== undefined ? data.median_r.toFixed(2) : '0.00';
        const wonCount = data.won_count || 0;
        const lossCount = data.loss_count || 0;

        html += `<div class="station-card" style="padding:12px 18px; display:flex; gap:20px; flex-wrap:wrap; align-items:center;">
          <div style="display:flex; gap:16px; flex-wrap:wrap; font-size:11px; font-family:var(--font-mono); font-weight:700;">
            <div><span style="color:var(--text-muted);">Scored</span> <b>${totalScored}</b></div>
            <div><span style="color:var(--text-muted);">Wins</span> <b style="color:var(--green);">${wonCount}</b></div>
            <div><span style="color:var(--text-muted);">Losses</span> <b>${lossCount}</b></div>
            <div><span style="color:var(--text-muted);">Win</span> <b>${data.win_pct !== undefined ? data.win_pct.toFixed(1) : 0}%</b></div>
            <div><span style="color:var(--text-muted);">Sum R</span> <b style="color:${data.sum_r !== undefined && data.sum_r >= 0 ? 'var(--green)' : 'var(--rose-light)'};">${sumR}</b></div>
            <div><span style="color:var(--text-muted);">Mean</span> <b>${meanR}</b></div>
            <div><span style="color:var(--text-muted);">Median</span> <b>${medianR}</b></div>
            <div><span style="color:var(--text-muted);">Max DD</span> <b style="color:var(--rose-light);" title="Worst peak-to-trough run of the curve, in R">${data.max_drawdown_r !== undefined && data.max_drawdown_r !== null ? '-' + data.max_drawdown_r.toFixed(2) + ' R' : '–'}</b></div>
          </div>
          ${totalScored < 100 ? `<div style="padding:6px 12px; background:var(--bg-subtle); border:1px dashed var(--amber); color:var(--amber); font-size:11px; border-radius:6px; margin-left:auto;">⚠️ n &lt; 100: metrics are not statistically significant</div>` : ''}
        </div>`;

        // Equity curve (SVG polyline with 0 baseline)
        html += `<div class="station-card">
          <h3 style="margin-top:0;"><span style="margin-right:8px;">📈</span>Equity Curve (Cumulative R)</h3>
          <div style="height:200px; width:100%; position:relative; background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:8px 12px;">`;

        if (data.equity_curve && data.equity_curve.length > 1) {
          const ec = data.equity_curve;
          const width = ec.length;
          const maxAbs = Math.max(...ec.map(c => Math.abs(c.cum_r)), 1);
          const yScale = 90 / (maxAbs * 2);
          const zeroY = 100;

          let points = ec.map((c, i) => {
            const x = 12 + (i / (ec.length - 1)) * (ec.length > 1 ? 600 : 0);
            const y = zeroY - c.cum_r * yScale;
            return `${x},${y}`;
          }).join(' ');

          const lastC = ec[ec.length - 1];
          const lastY = zeroY - lastC.cum_r * yScale;
          const lastColor = lastC.cum_r >= 0 ? 'var(--green)' : 'var(--rose-light)';

          // Every point is a trade: native <title> for the hover tooltip, and a click that opens
          // the research dossier behind it, exactly like the Journal rows do. Points without a
          // signal date or ticker stay plain circles.
          const dotTitles = ec.map((c, i) => {
            const x = 12 + (i / (ec.length - 1)) * (ec.length > 1 ? 600 : 0);
            const y = zeroY - c.cum_r * yScale;
            const color = c.r_net >= 0 ? 'var(--green)' : 'var(--rose-light)';
            const tip = `${c.ticker || '?'} · exit ${c.date || '?'} · ${c.r_net >= 0 ? '+' : ''}${c.r_net.toFixed(2)}R · cum ${c.cum_r >= 0 ? '+' : ''}${c.cum_r.toFixed(2)}R`;
            const clickable = c.ticker
              ? ` style="cursor:pointer;" onclick="AppSwing.openReportModal('${c.signal_date || ''}', '${String(c.ticker).replace(/'/g, "\\'")}')"`
              : '';
            return `<circle cx="${x}" cy="${y}" r="3" fill="${color}"${clickable}><title>${tip}</title></circle>`;
          }).join('');

          html += `<svg viewBox="0 0 624 110" preserveAspectRatio="none" style="width:100%; height:100%; display:block;">
            <line x1="0" y1="${zeroY}" x2="624" y2="${zeroY}" stroke="var(--border)" stroke-width="1"/>
            <polyline points="${points}" fill="none" stroke="var(--blue)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            ${dotTitles}
            <circle cx="${12 + ((ec.length - 1) / (ec.length - 1)) * 600}" cy="${lastY}" r="3" fill="${lastColor}"/>
          </svg>`;
        } else if (data.equity_curve && data.equity_curve.length === 1) {
          const c = data.equity_curve[0];
          html += `<div style="text-align:center; color:var(--text-muted); padding:20px;">Only ${c.cum_r.toFixed(2)} R — need at least 2 data points for an equity curve.</div>`;
        } else {
          html += `<div style="text-align:center; color:var(--text-muted); padding:20px;">No equity curve data yet.</div>`;
        }
        // The 200px box holds only the SVG; the axis labels and the drill-in hint sit under it.
        html += `</div>`;

        if (data.equity_curve && data.equity_curve.length > 1) {
          const ec = data.equity_curve;
          const firstPt = ec[0];
          const lastPt = ec[ec.length - 1];
          html += `<div style="display:flex; justify-content:space-between; font-size:10px; color:var(--text-muted); font-family:var(--font-mono); margin-top:4px;">
            <span>${firstPt.date || '–'}</span>
            <span>${lastPt.date || '–'}</span>
          </div>
          <div style="font-size:10px; color:var(--text-muted); text-align:right; margin-top:2px;">click any point → research dossier · hover for the trade</div>`;
        }
        html += `</div>`;

        // Lanes table
        const lanes = data.by_lane || {};
        html += `<div class="station-card">
          <h3 style="margin-top:0;"><span style="margin-right:8px;">🛣️</span>Lanes</h3>
          <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px;">
            <thead>
              <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border);">
                <th style="padding:8px; text-align:left;">Lane</th>
                <th style="padding:8px; text-align:right;">N</th>
                <th style="padding:8px; text-align:right;">Wins</th>
                <th style="padding:8px; text-align:right;">Prior Win</th>
                <th style="padding:8px; text-align:right;">Actual Win %</th>
                <th style="padding:8px; text-align:right;">Prior EV</th>
                <th style="padding:8px; text-align:right;">Actual Mean R</th>
                <th style="padding:8px; text-align:right;">Sum R</th>
                <th style="padding:8px; text-align:right;">PB / no PB split</th>
              </tr>
            </thead>
            <tbody>`;

        if (Object.keys(lanes).length === 0) {
          html += `<tr><td colspan="9" style="text-align:center; padding:12px; color:var(--text-muted);">No lane data.</td></tr>`;
        } else {
          for (let lane in lanes) {
            const ld = lanes[lane];
            const wColor = ld.win_rate >= 50 ? 'var(--green)' : 'var(--text-main)';
            const rColor = ld.sum_r !== undefined && ld.sum_r !== null && ld.sum_r >= 0 ? 'var(--green)' : 'var(--text-main)';
            const pWin = ld.prior_win !== undefined && ld.prior_win !== null ? ld.prior_win + '%' : '–';
            const pEv = ld.prior_ev !== undefined && ld.prior_ev !== null ? ld.prior_ev.toFixed(2) + ' R' : '–';
            // PB funnel split. Rows written before the Signal Pack bit existed land in
            // 'pre-PB' and are never folded into a measured half.
            const split = ld.pb_split || {};
            const splitHtml = ['PB', 'no PB', 'pre-PB'].filter(k => split[k] && split[k].total > 0)
              .map(k => {
                const b = split[k];
                return `<span title="${k} — measured, not a prior">${k}: n=${b.total}, ${b.win_rate.toFixed(1)}%, ${b.mean_r >= 0 ? '+' : ''}${b.mean_r.toFixed(2)}R</span>`;
              }).join('<br>') || '–';
            html += `<tr style="border-bottom:1px solid var(--border);">
              <td style="padding:8px; font-weight:700;">${lane}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${ld.total || 0}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${ld.wins || 0}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:var(--text-muted);">${pWin}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:${wColor};">${ld.win_rate ? ld.win_rate.toFixed(1) : 0}%</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:var(--text-muted);">${pEv}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${ld.mean_r !== undefined ? ld.mean_r.toFixed(2) : '0.00'}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:${rColor}; font-weight:700;">${ld.sum_r !== undefined && ld.sum_r !== null ? ((ld.sum_r >= 0 ? '+' : '') + ld.sum_r.toFixed(2)) : '0.00'}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:var(--text-muted); font-size:11px; line-height:1.5;">${splitHtml}</td>
            </tr>`;
          }
        }
        html += `</tbody></table></div>`;
      }

      html += `</div>`;
      const cont = document.getElementById('record-content-container');
      if (cont) cont.innerHTML = html;

    } catch (err) {
      console.error(err);
      const cont = document.getElementById('record-content-container');
      if (cont) cont.innerHTML = `<div style="color:var(--rose-light);">Failed to load Record: ${err}</div>`;
    }
  }
};
