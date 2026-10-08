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
          <td style="padding:6px 8px; font-weight:700; color:var(--cyan);"><a href="javascript:void(0)" class="tv-symbol-hover" data-ticker="${sym}" title="$${sym} · Click to open Dossier · Hover for Live TradingView Chart" onclick="event.stopPropagation(); AppSwing.openReportModal('${repDate}', '${sym}', 'local')">${sym}</a></td>
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
      excluded: 'var(--rose-light)',
      'hard cut': 'var(--rose-light)',
      unmeasured: 'var(--text-muted)',
      'watch only': 'var(--amber)'
    }[tag] || 'var(--text-muted)';

    let tip = tag;
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
  _isPollingToday: false,

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
    if (silent && document.activeElement && (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA')) {
      return;
    }
    try {
      const [todayData, recordData, coverageData, briefingData, feedbackData, missedMovesData] = await Promise.all([
        window.AppApi.request('/api/desk/today'),
        window.AppApi.request('/api/desk/record?scope=all').catch(() => null),
        window.AppApi.request('/api/desk/coverage').catch(() => null),
        window.AppApi.request('/api/desk/morning-briefing').catch(() => null),
        window.AppApi.request('/api/desk/feedback-loop').catch(() => null),
        window.AppApi.request('/api/desk/missed-moves').catch(() => null),
      ]);

      this._lastTodayData = todayData;

      const pulse = (todayData && todayData.market_pulse) || {};
      const topOpps = (todayData && todayData.top_opportunities) || (briefingData && briefingData.tier1_actionable) || [];
      const actionableAlerts = (todayData && todayData.actionable_alerts) || [];
      const feedback = feedbackData || (todayData && todayData.feedback_loop) || {};
      const openPositions = feedback.open_positions || [];
      const closedPositions = feedback.closed_positions || [];
      const scorecard = feedback.scorecard || {};

      const inZoneOpps = topOpps.filter(o => o.in_zone);
      const coilingOpps = topOpps.filter(o => !o.in_zone && o.priority_tier <= 2);
      const highConvOpps = topOpps.filter(o => o.conviction >= 7);

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
      const phaseBadgeTone = pulse.market_open ? 'green' : (pulse.market_phase === 'AFTER_HOURS' || pulse.market_phase === 'PRE_MARKET') ? 'amber' : 'red';

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
                <span class="pill ${phaseBadgeTone}" style="font-weight:800; font-size:10px;">
                  <span class="dot pulse"></span> ${marketText}
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
              <div style="font-size:11px; font-family:var(--font-mono);"><span style="color:var(--text-muted); font-weight:700;">SPY</span> <b style="color:var(--text-main);">${spyPx}</b></div>
              <div style="color:var(--border);">|</div>
              <div style="font-size:11px; font-family:var(--font-mono);"><span style="color:var(--text-muted); font-weight:700;">QQQ</span> <b style="color:var(--text-main);">${qqqPx}</b></div>
              <div style="color:var(--border);">|</div>
              <div style="font-size:11px; font-family:var(--font-mono);"><span style="color:var(--text-muted); font-weight:700;">VIX</span> <b style="color:${(pulse.vix || 0) > 20 ? 'var(--rose-light)' : 'var(--green)'};">${vixPx}</b></div>
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
            <span>Alerts: <b style="color:var(--text-main);">${todayData.funnel.alerts || 0}</b></span>
            <span style="color:var(--text-muted);">➔</span>
            <span>Local Done: <b style="color:var(--text-main);">${todayData.funnel.local_done || 0}</b></span>
            <span style="color:var(--text-muted);">➔</span>
            <span>Local PASS: <b style="color:var(--cyan);">${todayData.funnel.local_pass || 0}</b></span>
            <span style="color:var(--text-muted);">➔</span>
            <span>Measured: <b style="color:var(--purple-light);">${todayData.funnel.measured || 0}</b></span>
            <span style="color:var(--text-muted);">➔</span>
            <span>Deep Queued: <b style="color:var(--amber);">${todayData.funnel.deep_queued || 0}</b></span>
            <span style="color:var(--text-muted);">➔</span>
            <span>Deep Done: <b style="color:var(--blue-light);">${todayData.funnel.deep_done || 0}</b></span>
            <span style="color:var(--text-muted);">➔</span>
            <span>Actionable: <b style="color:var(--green); font-size:12px;">${todayData.funnel.actionable || 0}</b></span>
          </div>
        </div>` : ''}

        <!-- Cockpit Primary Navigation Tabs -->
        <div style="display:flex; gap:8px; margin-top:14px; padding-top:12px; border-top:1px solid var(--border); flex-wrap:wrap;">
          <button id="subtab-today-opps" class="btn ${activeTab === 'opps' ? '' : 'secondary'}" style="font-size:12px; font-weight:800; padding:6px 16px;" onclick="AppDesk.setTodaySubTab('opps')">
            🎯 Actionable Opportunities (${topOpps.length})
          </button>
          <button id="subtab-today-alerts" class="btn ${activeTab === 'alerts' ? '' : 'secondary'}" style="font-size:12px; font-weight:800; padding:6px 16px;" onclick="AppDesk.setTodaySubTab('alerts')">
            🚨 Actionable Alerts (${actionableAlerts.length})
          </button>
          <button id="subtab-today-feedback" class="btn ${activeTab === 'positions' ? '' : 'secondary'}" style="font-size:12px; font-weight:800; padding:6px 16px;" onclick="AppDesk.setTodaySubTab('positions')">
            📊 Positions &amp; Feedback Loop (${openPositions.length} Open · ${closedPositions.length} Evaluated)
          </button>
          <button id="subtab-today-coverage" class="btn ${activeTab === 'coverage' ? '' : 'secondary'}" style="font-size:12px; font-weight:800; padding:6px 16px;" onclick="AppDesk.setTodaySubTab('coverage')">
            🛡️ Pipeline Coverage &amp; Deferrals (${(coverageData && (coverageData.deep_cap_deferred || []).length) || 0})
          </button>
          <button id="subtab-today-missed" class="btn ${activeTab === 'missed_moves' ? '' : 'secondary'}" style="font-size:12px; font-weight:800; padding:6px 16px;" onclick="AppDesk.setTodaySubTab('missed_moves')">
            📉 Missed Moves (${(missedMovesData && (missedMovesData.missed_moves || []).length) || 0})
          </button>
        </div>
      </div>
      `;

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
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
              <div style="font-size:11.5px; font-weight:800; color:var(--amber);">
                ⏳ CLOSEST TO GATE (${todayData.closest_to_gate.length} Setups Missing Exactly 1 Condition)
              </div>
              <span style="font-size:10.5px; color:var(--text-muted);">One condition away from becoming actionable</span>
            </div>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
              ${todayData.closest_to_gate.map(c => `
                <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:4px 8px; font-size:11px; display:flex; align-items:center; gap:6px;">
                  <b style="color:var(--text-main); font-family:var(--font-mono);">${c.ticker}</b>
                  ${c.wait_for ? `<span style="color:var(--cyan); font-family:var(--font-mono); font-size:10px;">(${c.wait_for})</span>` : ''}
                  <span style="color:var(--amber); font-size:10px;">⚠️ Missing: ${c.missing_condition}</span>
                </div>
              `).join('')}
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
              <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:12px; padding:16px; display:flex; flex-direction:column; justify-content:space-between; gap:12px; box-shadow:0 4px 12px rgba(0,0,0,0.2); transition:transform 0.15s ease, border-color 0.15s ease;">
                <div>
                  
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
                  <a href="https://www.tradingview.com/chart/?symbol=${sym}" target="_blank" class="btn secondary" style="font-size:11px; padding:6px 10px; text-decoration:none; display:inline-flex; align-items:center; justify-content:center;" title="Open TradingView Chart">
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
                              <button class="btn secondary" style="padding:3px 8px; font-size:10px;" onclick="AppDesk.triggerResearch('${item.ticker}')">Run Research</button>
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
              <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:10px; margin-bottom:18px;">
                <div style="background:var(--bg-main); border:1px solid rgba(239,68,68,0.3); border-radius:8px; padding:10px 14px;">
                  <div style="font-size:10px; color:var(--rose-light); font-weight:800; text-transform:uppercase;">🕒 Stale Data / Frozen</div>
                  <div style="font-size:18px; font-weight:900; color:var(--rose-light); margin-top:4px;">${rollup.stale_data || 0}</div>
                  <div style="font-size:9.5px; color:var(--text-muted); margin-top:2px;">Old bar / frozen spot</div>
                </div>
                <div style="background:var(--bg-main); border:1px solid rgba(245,158,11,0.3); border-radius:8px; padding:10px 14px;">
                  <div style="font-size:10px; color:var(--amber); font-weight:800; text-transform:uppercase;">✂️ CUT Without Edge</div>
                  <div style="font-size:18px; font-weight:900; color:var(--amber); margin-top:4px;">${rollup.CUT || 0}</div>
                  <div style="font-size:9.5px; color:var(--text-muted); margin-top:2px;">Killed on stagnation</div>
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
                <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:10px 14px;">
                  <div style="font-size:10px; color:var(--text-muted); font-weight:800; text-transform:uppercase;">⚠️ Degenerate / No Levels</div>
                  <div style="font-size:18px; font-weight:900; color:var(--text-main); margin-top:4px;">${rollup.no_levels || 0}</div>
                  <div style="font-size:9.5px; color:var(--text-muted); margin-top:2px;">Zone equaled spot</div>
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
        const cont = document.getElementById('today-content-container');
        if (cont) cont.innerHTML = html;

      } catch (err) {
      console.error('Failed loading Today desk:', err);
      const cont = document.getElementById('today-content-container');
      if (cont && !silent) {
        cont.innerHTML = `<div style="color:var(--rose-light); padding:20px; text-align:center;">Failed to load Today desk: ${err}</div>`;
      }
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

          html += `<svg viewBox="0 0 624 110" preserveAspectRatio="none" style="width:100%; height:100%; display:block;">
            <line x1="0" y1="${zeroY}" x2="624" y2="${zeroY}" stroke="var(--border)" stroke-width="1"/>
            <polyline points="${points}" fill="none" stroke="var(--blue)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="${12 + ((ec.length - 1) / (ec.length - 1)) * 600}" cy="${lastY}" r="3" fill="${lastColor}"/>
          </svg>`;
        } else if (data.equity_curve && data.equity_curve.length === 1) {
          const c = data.equity_curve[0];
          html += `<div style="text-align:center; color:var(--text-muted); padding:20px;">Only ${c.cum_r.toFixed(2)} R — need at least 2 data points for an equity curve.</div>`;
        } else {
          html += `<div style="text-align:center; color:var(--text-muted); padding:20px;">No equity curve data yet.</div>`;
        }
        html += `</div></div>`;

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
