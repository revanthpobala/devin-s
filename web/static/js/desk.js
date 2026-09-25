window.AppDesk = {
  _activeRecordScope: 'gated',

  async triggerDeep(ticker, btn) {
    try {
      if (btn) {
        btn.disabled = true;
        btn.innerText = '⏳ Queued...';
      }
      await window.AppApi.triggerResearch(ticker, 'full');
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
          <td style="padding:6px 8px;">${this.fmt(ib.setup)}</td>
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

  async loadToday() {
    try {
      const [todayData, recordData, coverageData] = await Promise.all([
        window.AppApi.request('/api/desk/today'),
        window.AppApi.request('/api/desk/record?scope=all'),
        window.AppApi.request('/api/desk/coverage'),
      ]);

      const cov = coverageData || {};
      const alerts = cov.alerts || 0;
      const localDone = cov.local_done || 0;
      const localPass = cov.local_pass || 0;
      const deepDone = cov.deep_done || 0;
      const deepQueued = cov.deep_queued || [];
      const deepMissing = Array.isArray(cov.deep_missing) ? cov.deep_missing : [];

      const covClass = deepDone < localPass ? 'color:var(--rose-light);' : 'color:var(--text-main);';
      const covTxt = `Alerts ${alerts} · Local ${localDone}/${alerts} · PASS ${localPass} · Deep ${deepDone}/${localPass}${deepQueued.length ? ' · Queued ' + deepQueued.length : ''}`;

      const actionable = (todayData && todayData.actionable) ? todayData.actionable : [];
      const stalking = (todayData && todayData.stalking) ? todayData.stalking : [];
      const found = (todayData && todayData.found) ? todayData.found : [];
      const inbox = (todayData && todayData.inbox) ? todayData.inbox : [];
      const watchList = (todayData && todayData.watch) ? todayData.watch : [];
      const cutList = (todayData && todayData.cut) ? todayData.cut : [];
      const needsYou = (todayData && todayData.needs_you) ? todayData.needs_you : [];

      const inTradeCount = actionable.filter(a => a.status === 'IN_TRADE').length;
      const inZoneCount = actionable.filter(a => a.status === 'IN_ZONE').length;
      const nearCount = actionable.filter(a => a.status !== 'IN_ZONE' && a.status !== 'IN_TRADE' && a.dist !== null && a.dist !== undefined && Math.abs(parseFloat(a.dist)) <= 1.5).length;
      const foundCount = found.reduce((acc, f) => acc + ((f.tickers || []).length), 0);
      const recordSumR = (recordData && recordData.sum_r !== undefined) ? ((recordData.sum_r >= 0 ? '+' : '') + recordData.sum_r.toFixed(2) + ' R') : '0.00 R';

      let html = `<div style="display:flex; flex-direction:column; gap:20px;">`;

      // Coverage bar
      const localMissing = Array.isArray(cov.local_missing) ? cov.local_missing : [];
      html += `<div class="station-card" style="padding:10px 16px;">
        <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px;">
          <div style="font-size:11px; font-weight:800; letter-spacing:0.5px; text-transform:uppercase; color:var(--text-muted);">COVERAGE</div>
          <div style="font-size:12px; font-weight:800; font-family:var(--font-mono); ${covClass}">${covTxt}</div>
        </div>
        ${deepMissing.length > 0 ? `<div style="margin-top:6px; font-size:11px; color:var(--rose-light); font-family:var(--font-mono);">⚠️ Missing Deep Research (${deepMissing.length}): ${deepMissing.slice(0, 10).join(', ')}${deepMissing.length > 10 ? ' ...' : ''}</div>` : ''}
        ${localMissing.length > 0 ? `<div style="margin-top:4px; font-size:10.5px; color:var(--text-muted); font-family:var(--font-mono);">⏳ Awaiting Local Triage (${localMissing.length}): ${localMissing.slice(0, 8).join(', ')}${localMissing.length > 8 ? ' ...' : ''}</div>` : ''}
      </div>`;

      // KPI strip
      html += `<div style="display:flex; gap:12px; flex-wrap:wrap;">
        ${this.kpi('IN TRADE', inTradeCount, '', 'info')}
        ${this.kpi('IN ZONE', inZoneCount, '', 'info')}
        ${this.kpi('NEAR', nearCount, '≤1.5%', 'warn')}
        ${this.kpi('STALKING', stalking.length, '', 'neutral')}
        ${this.kpi('LOCAL TRIAGED', inbox.length, '', 'good')}
        ${this.kpi('ALL-TIME CLOSED R', recordSumR, '', recordData && recordData.sum_r >= 0 ? 'good' : 'bad')}
      </div>`;

      // Act now (card grid, 3 per row)
      html += `<div class="station-card">
        <h3 style="margin-top:0; color:var(--green);"><span style="margin-right:8px;">🎯</span>Act Now</h3>
        <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:14px;">`;

      if (actionable.length === 0) {
        html += `<div style="grid-column:1/-1; text-align:center; padding:20px; color:var(--text-muted);">No actionable suggestions right now.</div>`;
      } else {
        actionable.forEach(r => {
          const ticker = r.ticker || '';
          const sym = ticker.toUpperCase();
          const repDate = (r.date && r.date !== '–') ? r.date.substring(0, 10) : 'latest';
          const status = r.status || 'STALKING';
          const last = r.last_price !== null && r.last_price !== undefined ? parseFloat(r.last_price).toFixed(2) : '–';
          const entryLow = r.entry_low !== null && r.entry_low !== undefined ? parseFloat(r.entry_low).toFixed(2) : '';
          const entryHigh = r.entry_high !== null && r.entry_high !== undefined ? parseFloat(r.entry_high).toFixed(2) : '';
          const stop = r.stop !== null && r.stop !== undefined ? parseFloat(r.stop).toFixed(2) : '–';
          const t1 = r.target_1 !== null && r.target_1 !== undefined ? parseFloat(r.target_1).toFixed(2) : '–';
          const dist = r.dist !== null && r.dist !== undefined ? parseFloat(r.dist).toFixed(2) : '–';
          const distStr = dist !== '–' ? dist + '%' : '–';
          const stopAtr = r.stop_width_atr ? `${r.stop_width_atr} ATR` : '–';

          let statusBadge;
          if (status === 'IN_TRADE') statusBadge = this.pill('IN_TRADE', 'info');
          else if (status === 'IN_ZONE') statusBadge = this.pill('IN_ZONE', 'info');
          else statusBadge = this.pill(status, 'warn');

          let rrTxt;
          let rrTone = 'neutral';
          if (r.live_rr_flag === 'BELOW_STOP') {
            rrTxt = 'AT STOP';
            rrTone = 'bad';
          } else if (r.live_rr_flag === 'AT_STOP') {
            rrTxt = 'AT STOP';
            rrTone = 'bad';
          } else if (r.live_rr !== null && r.live_rr !== undefined) {
            rrTxt = 'R:R ' + parseFloat(r.live_rr).toFixed(2);
            rrTone = 'good';
          } else {
            rrTxt = '–';
            rrTone = 'neutral';
          }

          html += `<div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:10px; padding:14px; display:flex; flex-direction:column; gap:8px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <div style="font-size:18px; font-weight:800; cursor:pointer; color:var(--cyan);" onclick="AppSwing.openReportModal('${repDate}', '${sym}', 'local')">${sym}</div>
              ${statusBadge}
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:6px; font-size:11px; font-family:var(--font-mono);">
              <div><b>Last</b> ${last}</div>
              <div><b>Dist</b> ${distStr}</div>
              <div><b>Zone</b> ${entryLow ? entryLow + ' - ' + entryHigh : '–'}</div>
              <div><b>Stop</b> <span style="color:var(--rose-light);">${stop}</span></div>
              <div><b>T1</b> <span style="color:var(--green);">${t1}</span></div>
              <div><b>R:R</b> ${this.pill(rrTxt, rrTone)}</div>
              <div><b>ATR Stop</b> ${stopAtr}</div>
            </div>
            <div style="display:flex; gap:6px; margin-top:4px;">
              <button class="btn secondary" style="flex:1; font-size:10px; padding:4px 6px;" onclick="AppSwing.openReportModal('${repDate}', '${sym}', 'local')">📑 Dossier</button>
              <button class="btn secondary" style="flex:1; font-size:10px; padding:4px 6px;" onclick="AppDesk.triggerDeep('${sym}', this)">🔬 Research</button>
            </div>
            ${r.llm_playbook ? `<div style="font-size:10px; color:var(--text-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${r.llm_playbook.replace(/"/g, '&quot;')}">${r.llm_playbook}</div>` : ''}
          </div>`;
        });
      }
      html += `</div></div>`;

      // Needs you
      html += `<div class="station-card">
        <h3 style="margin-top:0; color:var(--amber);"><span style="margin-right:8px;">🏃</span>Needs You</h3>
        <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:14px;">`;

      if (needsYou.length === 0) {
        html += `<div style="grid-column:1/-1; text-align:center; padding:20px; color:var(--text-muted);">No deep research pending.</div>`;
      } else {
        needsYou.forEach(r => {
          const ticker = r.ticker || '';
          const sym = ticker.toUpperCase();
          const repDate = (r.date && r.date !== '–') ? r.date.substring(0, 10) : 'latest';
          const last = r.last_price !== null && r.last_price !== undefined ? parseFloat(r.last_price).toFixed(2) : '–';
          const stop = r.stop !== null && r.stop !== undefined ? parseFloat(r.stop).toFixed(2) : '–';
          const t1 = r.target_1 !== null && r.target_1 !== undefined ? parseFloat(r.target_1).toFixed(2) : '–';
          const deepSt = r.deep_status || 'none';

          const setupBadge = r.setup ? `<span class="badge" style="border-color:var(--blue); color:var(--blue); font-size:10px;">${r.setup}</span>` : '';
          const playbookExcerpt = r.llm_playbook ? r.llm_playbook.replace(/\*\*/g, '').substring(0, 140) + '...' : '';

          html += `<div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:10px; padding:14px; display:flex; flex-direction:column; gap:8px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <div style="font-size:18px; font-weight:800; cursor:pointer; color:var(--cyan);" onclick="AppSwing.openReportModal('${repDate}', '${sym}', 'local')">${sym}</div>
              <div style="display:flex; align-items:center; gap:6px;">
                ${setupBadge}
                ${this.pill(deepSt.toUpperCase(), deepSt === 'COMPLETED' ? 'good' : deepSt === 'RUNNING' ? 'info' : 'warn')}
              </div>
            </div>
            <div style="font-size:11px; color:var(--text-muted); font-family:var(--font-mono);">Last ${last}${stop !== '–' ? ' · Stop ' + stop : ''}${t1 !== '–' ? ' · T1 ' + t1 : ''}</div>
            ${r.llm_decision ? `<div style="font-size:10.5px; font-weight:700; color:var(--green);">${r.llm_decision}</div>` : ''}
            ${playbookExcerpt ? `<div style="font-size:11px; color:var(--text-main); background:var(--bg-main); padding:6px 8px; border-radius:6px; border:1px solid var(--border); line-height:1.4;" title="${(r.llm_playbook || '').replace(/"/g, '&quot;')}">${playbookExcerpt}</div>` : ''}
            <div style="display:flex; gap:6px; margin-top:2px;">
              <button class="btn secondary" style="flex:1; font-size:10.5px; padding:4px 8px;" onclick="AppSwing.openReportModal('${repDate}', '${sym}', 'local')">📑 Local Dossier</button>
              <button class="btn secondary" style="flex:1; font-size:10.5px; padding:4px 8px;" onclick="AppDesk.triggerDeep('${sym}', this)">🔬 Run deep</button>
            </div>
          </div>`;
        });
      }
      html += `</div></div>`;

      // Watch (Collapsible card)
      html += `<details class="station-card" style="padding:12px 18px;" ${watchList.length > 0 && watchList.length <= 8 ? 'open' : ''}>
        <summary style="cursor:pointer; display:flex; align-items:center; justify-content:space-between; user-select:none;">
          <div style="font-size:13px; font-weight:800; color:var(--cyan); display:flex; align-items:center; gap:8px;">
            <span>👁</span>Watch (${watchList.length})
          </div>
          <span style="font-size:11px; color:var(--text-muted); font-weight:600;">Click to toggle</span>
        </summary>
        <div style="margin-top:12px;">
          <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px;">
            <thead>
              <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border);">
                <th style="padding:8px; text-align:left;">Ticker</th>
                <th style="padding:8px; text-align:right;">Last</th>
                <th style="padding:8px; text-align:right;">Zone</th>
                <th style="padding:8px; text-align:right;">Stop</th>
                <th style="padding:8px; text-align:right;">T1</th>
              </tr>
            </thead>
            <tbody>`;

      if (watchList.length === 0) {
        html += `<tr><td colspan="5" style="text-align:center; padding:12px; color:var(--text-muted);">No watch suggestions.</td></tr>`;
      } else {
        watchList.forEach(r => {
          const sym = (r.ticker || '').toUpperCase();
          const repDate = (r.date && r.date !== '–') ? r.date.substring(0, 10) : 'latest';
          const last = r.last_price !== null && r.last_price !== undefined ? parseFloat(r.last_price).toFixed(2) : '–';
          const zone = r.entry_low && r.entry_high ? parseFloat(r.entry_low).toFixed(2) + ' - ' + parseFloat(r.entry_high).toFixed(2) : '–';
          html += `<tr style="border-bottom:1px solid var(--border);">
            <td style="padding:8px; font-weight:700; cursor:pointer; color:var(--cyan);" onclick="AppSwing.openReportModal('${repDate}', '${sym}', 'local')">${sym}</td>
            <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${last}</td>
            <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${zone}</td>
            <td style="padding:8px; text-align:right; color:var(--rose-light); font-family:var(--font-mono);">${r.stop ? parseFloat(r.stop).toFixed(2) : '–'}</td>
            <td style="padding:8px; text-align:right; color:var(--green); font-family:var(--font-mono);">${r.target_1 ? parseFloat(r.target_1).toFixed(2) : '–'}</td>
          </tr>`;
        });
      }
      html += `</tbody></table></div></details>`;

      // Stalking (Collapsible card)
      html += `<details class="station-card" style="padding:12px 18px;" ${stalking.length > 0 && stalking.length <= 8 ? 'open' : ''}>
        <summary style="cursor:pointer; display:flex; align-items:center; justify-content:space-between; user-select:none;">
          <div style="font-size:13px; font-weight:800; color:var(--amber); display:flex; align-items:center; gap:8px;">
            <span>⏳</span>Stalking (${stalking.length})
          </div>
          <span style="font-size:11px; color:var(--text-muted); font-weight:600;">Click to toggle</span>
        </summary>
        <div style="margin-top:12px;">
          <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px;">
            <thead>
              <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border);">
                <th style="padding:8px; text-align:left;">Ticker</th>
                <th style="padding:8px; text-align:left;">Lane</th>
                <th style="padding:8px; text-align:right;">Dist %</th>
                <th style="padding:8px; text-align:right;">Zone</th>
                <th style="padding:8px; text-align:right;">Stop</th>
                <th style="padding:8px; text-align:right;">T1</th>
              </tr>
            </thead>
            <tbody>`;

      if (stalking.length === 0) {
        html += `<tr><td colspan="6" style="text-align:center; padding:12px; color:var(--text-muted);">No stalking suggestions.</td></tr>`;
      } else {
        stalking.forEach(r => {
          const sym = (r.ticker || '').toUpperCase();
          const repDate = (r.date && r.date !== '–') ? r.date.substring(0, 10) : 'latest';
          const zone = r.entry_low && r.entry_high ? parseFloat(r.entry_low).toFixed(2) + ' - ' + parseFloat(r.entry_high).toFixed(2) : '–';
          const dist = r.dist !== null && r.dist !== undefined ? parseFloat(r.dist).toFixed(2) + '%' : '–';
          html += `<tr style="border-bottom:1px solid var(--border);">
            <td style="padding:8px; font-weight:700; cursor:pointer; color:var(--cyan);" onclick="AppSwing.openReportModal('${repDate}', '${sym}', 'local')">${sym}</td>
            <td style="padding:8px;">${this.fmt(r.lane)}</td>
            <td style="padding:8px; text-align:right;">${dist}</td>
            <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${zone}</td>
            <td style="padding:8px; text-align:right; color:var(--rose-light); font-family:var(--font-mono);">${r.stop ? parseFloat(r.stop).toFixed(2) : '–'}</td>
            <td style="padding:8px; text-align:right; color:var(--green); font-family:var(--font-mono);">${r.target_1 ? parseFloat(r.target_1).toFixed(2) : '–'}</td>
          </tr>`;
        });
      }
      html += `</tbody></table></div></details>`;

      // CUT (Collapsible card)
      html += `<details class="station-card" style="padding:10px 18px;">
        <summary style="cursor:pointer; display:flex; align-items:center; justify-content:space-between; user-select:none;">
          <div style="font-size:12px; font-weight:800; color:var(--rose-light); display:flex; align-items:center; gap:8px;">
            <span>✂</span>CUT (${cutList.length})
          </div>
          <span style="font-size:11px; color:var(--text-muted); font-weight:600;">${cutList.length} symbols collapsed (click to view)</span>
        </summary>
        <div style="margin-top:10px; display:flex; flex-wrap:wrap; gap:6px;">
          ${cutList.length === 0 ? `<div style="font-size:11px; color:var(--text-muted);">No cut symbols today.</div>` :
            cutList.map(c => {
              const sym = (c.ticker || '').toUpperCase();
              const repDate = (c.date && c.date !== '–') ? c.date.substring(0, 10) : 'latest';
              return `<span class="badge" style="cursor:pointer; border-color:var(--border); color:var(--text-muted);" onclick="AppSwing.openReportModal('${repDate}', '${sym}', 'local')">${sym}</span>`;
            }).join(' ')
          }
        </div>
      </details>`;

      // Section 0a: Today Inbox (Collapsible card with collapsible decision groups & rows)
      if (inbox.length > 0) {
        const passItems = inbox.filter(ib => (ib.llm_decision || '').toUpperCase().includes('PASS'));
        const watchItems = inbox.filter(ib => (ib.llm_decision || '').toUpperCase().includes('WATCH'));
        const cutItems = inbox.filter(ib => (ib.llm_decision || '').toUpperCase().includes('CUT'));
        const otherItems = inbox.filter(ib => !passItems.includes(ib) && !watchItems.includes(ib) && !cutItems.includes(ib));

        html += `<details class="station-card" open style="padding:14px 18px;">
          <summary style="cursor:pointer; display:flex; align-items:center; justify-content:space-between; user-select:none;">
            <div style="font-size:14px; font-weight:800; color:var(--text-main); display:flex; align-items:center; gap:8px;">
              <span>📥</span>Local Triaged Inbox (${inbox.length})
            </div>
            <div style="display:flex; align-items:center; gap:10px; font-size:11px; font-family:var(--font-mono);">
              <span style="color:var(--green); font-weight:700;">PASS: ${passItems.length}</span>
              <span style="color:var(--amber); font-weight:700;">WATCH: ${watchItems.length}</span>
              <span style="color:var(--rose-light); font-weight:700;">CUT: ${cutItems.length}</span>
            </div>
          </summary>
          
          <div style="margin-top:14px; display:flex; flex-direction:column; gap:12px;">
            <!-- PASS Group -->
            <details open style="border:1px solid var(--border); border-radius:8px; padding:10px 14px; background:var(--bg-surface);">
              <summary style="cursor:pointer; font-weight:700; font-size:12px; color:var(--green); display:flex; align-items:center; justify-content:space-between; user-select:none;">
                <span>🟢 Local PASS Decisions (${passItems.length})</span>
                <span style="font-size:10px; color:var(--text-muted); font-weight:400;">Click symbol for dossier · Click row to expand playbook</span>
              </summary>
              <div style="margin-top:8px;">
                ${this.renderInboxTable(passItems, 'pass')}
              </div>
            </details>

            <!-- WATCH Group -->
            <details ${watchItems.length <= 15 ? 'open' : ''} style="border:1px solid var(--border); border-radius:8px; padding:10px 14px; background:var(--bg-surface);">
              <summary style="cursor:pointer; font-weight:700; font-size:12px; color:var(--amber); display:flex; align-items:center; justify-content:space-between; user-select:none;">
                <span>🟡 Local WATCH Decisions (${watchItems.length})</span>
                <span style="font-size:10px; color:var(--text-muted); font-weight:400;">Click to toggle list</span>
              </summary>
              <div style="margin-top:8px;">
                ${this.renderInboxTable(watchItems, 'watch')}
              </div>
            </details>

            <!-- CUT Group -->
            <details style="border:1px solid var(--border); border-radius:8px; padding:10px 14px; background:var(--bg-surface);">
              <summary style="cursor:pointer; font-weight:700; font-size:12px; color:var(--rose-light); display:flex; align-items:center; justify-content:space-between; user-select:none;">
                <span>🔴 Local CUT Decisions (${cutItems.length})</span>
                <span style="font-size:10px; color:var(--text-muted); font-weight:400;">Click to toggle list</span>
              </summary>
              <div style="margin-top:8px;">
                ${this.renderInboxTable(cutItems, 'cut')}
              </div>
            </details>

            ${otherItems.length > 0 ? `
            <!-- Other Group -->
            <details style="border:1px solid var(--border); border-radius:8px; padding:10px 14px; background:var(--bg-surface);">
              <summary style="cursor:pointer; font-weight:700; font-size:12px; color:var(--text-muted); display:flex; align-items:center; justify-content:space-between; user-select:none;">
                <span>⚪ Other Decisions (${otherItems.length})</span>
                <span style="font-size:10px; color:var(--text-muted); font-weight:400;">Click to toggle list</span>
              </summary>
              <div style="margin-top:8px;">
                ${this.renderInboxTable(otherItems, 'other')}
              </div>
            </details>
            ` : ''}
          </div>
        </details>`;
      }

      html += `</div>`;
      const cont = document.getElementById('today-content-container');
      if (cont) cont.innerHTML = html;

    } catch (err) {
      console.error(err);
      const cont = document.getElementById('today-content-container');
      if (cont) cont.innerHTML = `<div style="color:var(--rose-light);">Failed to load Today desk: ${err}</div>`;
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
            <td style="padding:8px; white-space:nowrap;">${this.fmt(r.lane, '–')}</td>
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
              </tr>
            </thead>
            <tbody>`;

        if (Object.keys(lanes).length === 0) {
          html += `<tr><td colspan="8" style="text-align:center; padding:12px; color:var(--text-muted);">No lane data.</td></tr>`;
        } else {
          for (let lane in lanes) {
            const ld = lanes[lane];
            const wColor = ld.win_rate >= 50 ? 'var(--green)' : 'var(--text-main)';
            const rColor = ld.sum_r !== undefined && ld.sum_r !== null && ld.sum_r >= 0 ? 'var(--green)' : 'var(--text-main)';
            const pWin = ld.prior_win !== undefined && ld.prior_win !== null ? ld.prior_win + '%' : '–';
            const pEv = ld.prior_ev !== undefined && ld.prior_ev !== null ? ld.prior_ev.toFixed(2) + ' R' : '–';
            html += `<tr style="border-bottom:1px solid var(--border);">
              <td style="padding:8px; font-weight:700;">${lane}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${ld.total || 0}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${ld.wins || 0}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:var(--text-muted);">${pWin}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:${wColor};">${ld.win_rate ? ld.win_rate.toFixed(1) : 0}%</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:var(--text-muted);">${pEv}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${ld.mean_r !== undefined ? ld.mean_r.toFixed(2) : '0.00'}</td>
              <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:${rColor}; font-weight:700;">${ld.sum_r !== undefined && ld.sum_r !== null ? ((ld.sum_r >= 0 ? '+' : '') + ld.sum_r.toFixed(2)) : '0.00'}</td>
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
