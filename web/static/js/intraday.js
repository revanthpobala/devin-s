/**
 * Intraday 0DTE Desk Controller
 */
window.AppIntraday = {
  _rawPositions: [],
  _posSortField: 'opened_at',
  _posSortAsc: false,
  _rawAlerts: [],
  _alertsSortField: 'timestamp',
  _alertsSortAsc: false,
  _rawSignals: [],
  _sigSortField: 'ticker',
  _sigSortAsc: true,

  dismissBanner() {
    const el = document.getElementById('zero-dte-research-banner');
    if (el) el.style.display = 'none';
    try { localStorage.setItem('dismiss_0dte_banner', 'true'); } catch (e) {}
  },

  checkBanner() {
    try {
      if (localStorage.getItem('dismiss_0dte_banner') === 'true') {
        const el = document.getElementById('zero-dte-research-banner');
        if (el) el.style.display = 'none';
      }
    } catch (e) {}
  },

  sortSignals(field) {
    if (this._sigSortField === field) {
      this._sigSortAsc = !this._sigSortAsc;
    } else {
      this._sigSortField = field;
      this._sigSortAsc = (field === 'ticker' || field === 'status') ? true : false;
    }
    this.renderSignalsTable();
  },

  sigSortIndicator(field) {
    if (this._sigSortField !== field) {
      return '<span style="opacity:0.35; font-size:10px; margin-left:3px;">⇅</span>';
    }
    return `<span style="color:var(--cyan-glow); font-size:11px; font-weight:800; margin-left:3px;">${this._sigSortAsc ? '▲' : '▼'}</span>`;
  },

  async loadIntradaySignals() {
    const container = document.getElementById('odte-signals-container');
    try {
      const data = await window.AppApi.getIntradaySignals();
      this._rawSignals = data.signals || [];
      const countPill = document.getElementById('odte-signals-count-pill');
      if (countPill) countPill.innerText = `${this._rawSignals.length} Grade-A Plays (${data.date || 'Today'})`;

      const hitPill = document.getElementById('odte-targets-hit-pill');
      if (hitPill) {
        if (data.targets_hit > 0) {
          hitPill.style.display = 'inline-flex';
          hitPill.innerText = `🏁 ${data.targets_hit} Target Hit`;
        } else {
          hitPill.style.display = 'none';
        }
      }

      this.renderSignalsTable();
    } catch (e) {
      console.error('Failed loading 0DTE signals', e);
      if (container) container.innerHTML = `<div style="color:var(--text-muted); font-size:12px; text-align:center; padding:16px;">Error loading 0DTE signals: ${e.message}</div>`;
    }
  },

  renderSignalsTable() {
    const container = document.getElementById('odte-signals-container');
    if (!container) return;

    const signals = [...this._rawSignals];
    if (signals.length === 0) {
      container.innerHTML = '<div style="color:var(--text-muted); font-size:13px; text-align:center; padding:24px;">No Grade-A 0DTE signals recorded for today yet. Market monitor active.</div>';
      return;
    }

    signals.sort((a, b) => {
      const f = this._sigSortField;
      if (f === 'ticker') {
        const sa = (a.ticker || '').toLowerCase();
        const sb = (b.ticker || '').toLowerCase();
        return this._sigSortAsc ? sa.localeCompare(sb) : sb.localeCompare(sa);
      }
      if (f === 'entry') return this._sigSortAsc ? (a.entry_price - b.entry_price) : (b.entry_price - a.entry_price);
      if (f === 'spot') return this._sigSortAsc ? (a.spot_price - b.spot_price) : (b.spot_price - a.spot_price);
      if (f === 'pnl') return this._sigSortAsc ? (a.pnl_pct - b.pnl_pct) : (b.pnl_pct - a.pnl_pct);
      if (f === 'status') {
        const sa = (a.status || '').toLowerCase();
        const sb = (b.status || '').toLowerCase();
        return this._sigSortAsc ? sa.localeCompare(sb) : sb.localeCompare(sa);
      }
      return 0;
    });

    container.innerHTML = `
      <table class="cockpit-table">
        <thead>
          <tr>
            <th onclick="AppIntraday.sortSignals('ticker')" style="cursor:pointer; user-select:none;">TICKER & CONTRACT ${this.sigSortIndicator('ticker')}</th>
            <th>SIDE</th>
            <th>GRADE</th>
            <th onclick="AppIntraday.sortSignals('entry')" style="cursor:pointer; user-select:none;">ENTRY PX ${this.sigSortIndicator('entry')}</th>
            <th onclick="AppIntraday.sortSignals('spot')" style="cursor:pointer; user-select:none;">CURRENT SPOT ${this.sigSortIndicator('spot')}</th>
            <th>TARGET 1 / STOP</th>
            <th onclick="AppIntraday.sortSignals('pnl')" style="cursor:pointer; user-select:none;">P&L % ${this.sigSortIndicator('pnl')}</th>
            <th onclick="AppIntraday.sortSignals('status')" style="cursor:pointer; user-select:none;">EXECUTION STATUS ${this.sigSortIndicator('status')}</th>
            <th style="text-align:right;">TACTICAL</th>
          </tr>
        </thead>
        <tbody>
          ${signals.map(s => {
            const sym = s.ticker;
            const side = (s.side || 'LONG').toUpperCase();
            const isLong = side.includes('LONG') || side.includes('CALL') || side.includes('BUY');
            const entryStr = s.entry_price ? `$${parseFloat(s.entry_price).toFixed(2)}` : 'N/A';
            const spotStr = s.spot_price ? `$${parseFloat(s.spot_price).toFixed(2)}` : 'N/A';
            const t1Str = s.target_1 ? `$${parseFloat(s.target_1).toFixed(2)}` : '—';
            const stopStr = s.stop_price ? `$${parseFloat(s.stop_price).toFixed(2)}` : '—';
            const pnl = parseFloat(s.pnl_pct) || 0.0;
            const pnlColor = pnl >= 0 ? '#10b981' : '#ef4444';
            const compI = window.AppUtils ? AppUtils.getCompanyName(sym) : sym;
            const compTitle = (compI || sym).replace(/"/g, '&quot;');

            let statusBadge = s.status_badge || 'amber';
            let statusText = s.status_text || 'Triggered';
            if (s.status === 'ACTIVE_OPEN') {
              statusBadge = 'green';
              statusText = '🟢 Open Position';
            } else if (s.status === 'TARGET_HIT') {
              statusBadge = 'cyan';
              statusText = '🏁 Target 1 Hit';
            } else if (s.status === 'EXITED') {
              statusBadge = 'red';
              statusText = s.exit_info ? `🛑 Exited ($${parseFloat(s.exit_info.exit_price || 0).toFixed(2)})` : '🛑 Exited';
            }

            return `
              <tr>
                <td>
                  <div style="display:flex; align-items:center; gap:8px;">
                    <span class="tv-symbol-hover" data-ticker="${sym}" style="font-weight:700; color:var(--cyan-glow); cursor:pointer;" onclick="if(window.AppSwing) AppSwing.openTradingViewModal('${sym}', '15')" title="${compTitle} ($${sym}) · Hover for Live Chart">${sym}</span>
                    <span style="font-size:11px; font-weight:600; color:var(--text-main); font-family:'JetBrains Mono', monospace;">${s.contract}</span>
                  </div>
                </td>
                <td><span class="pill ${isLong ? 'green' : 'red'}" style="padding:1px 6px; font-size:10px; font-weight:700;">${isLong ? 'CALLS' : 'PUTS'}</span></td>
                <td><span class="pill green" style="padding:1px 6px; font-size:10px; font-weight:700;">Grade ${s.grade || 'A'}</span></td>
                <td style="font-weight:600; font-family:'JetBrains Mono', monospace;">${entryStr}</td>
                <td style="font-weight:700; font-family:'JetBrains Mono', monospace; color:${pnl >= 0 ? '#34d399' : '#fb7185'};">${spotStr}</td>
                <td style="font-size:11px; font-family:'JetBrains Mono', monospace;">
                  <span style="color:var(--emerald-light); font-weight:600;" title="Target 1">T1: ${t1Str}</span>
                  <span style="color:var(--border); margin:0 4px;">|</span>
                  <span style="color:var(--rose-light);" title="Stop Loss">Stop: ${stopStr}</span>
                </td>
                <td style="color:${pnlColor}; font-weight:700; font-family:'JetBrains Mono', monospace;">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%</td>
                <td><span class="pill ${statusBadge}" style="padding:2px 8px; font-size:10.5px; font-weight:700;">${statusText}</span></td>
                <td style="text-align:right; display:flex; gap:6px; justify-content:flex-end; align-items:center;">
                  <button class="btn secondary" onclick="if(window.AppChat) { window.AppChat.askRevChat('Give me a real-time tactical evaluation on the 0DTE setup for $${sym} (Entry: ${entryStr}, Spot: ${spotStr}, Target: ${t1Str}, Stop: ${stopStr}). Should I take/hold/trim this play?'); }" style="padding:2px 7px; font-size:10.5px; font-weight:700; color:var(--blue);" title="Ask Copilot for tactical 0DTE review">💬 Query</button>
                  <button class="btn secondary" onclick="if(window.AppSwing) AppSwing.openTradingViewModal('${sym}', '15')" style="padding:2px 7px; font-size:10.5px; font-weight:700; color:var(--cyan-glow);" title="Open 15m intraday TradingView chart">📈 Chart</button>
                </td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;
  },

  sortPositions(field) {
    if (this._posSortField === field) {
      this._posSortAsc = !this._posSortAsc;
    } else {
      this._posSortField = field;
      this._posSortAsc = (field === 'symbol' || field === 'side') ? true : false;
    }
    this.renderPositionsTable();
  },

  posSortIndicator(field) {
    if (this._posSortField !== field) {
      return '<span style="opacity:0.35; font-size:10px; margin-left:3px;">⇅</span>';
    }
    return `<span style="color:var(--cyan-glow); font-size:11px; font-weight:800; margin-left:3px;">${this._posSortAsc ? '▲' : '▼'}</span>`;
  },

  async loadIntradayPositions() {
    this.checkBanner();
    try {
      const data = await window.AppApi.getPositions();
      const countPill = document.getElementById('intraday-pos-count');
      this._rawPositions = data.positions || [];
      if (countPill) countPill.innerText = `${this._rawPositions.length} Open Positions`;

      // Update NY Session Clock
      const now = new Date();
      const nyTime = now.toLocaleTimeString('en-US', {
        timeZone: 'America/New_York',
        hour: '2-digit',
        minute: '2-digit'
      });
      const elClock = document.getElementById('val-et-clock');
      if (elClock) elClock.innerText = `${nyTime} ET`;

      // Render positions with active sort
      this.renderPositionsTable();

      // Refresh today's 0DTE Grade-A signals table
      this.loadIntradaySignals();

      // Also refresh the daily alerts feed
      this.loadDailyAlerts();
    } catch (e) {
      console.error('Failed loading intraday positions', e);
    }
  },

  renderPositionsTable() {
    const container = document.getElementById('intraday-positions-table');
    if (!container) return;

    const positions = [...this._rawPositions];
    if (positions.length === 0) {
      container.innerHTML = '<div style="color:var(--text-muted); font-size:13px; text-align:center; padding:24px;">No open intraday positions in data/positions.json. All flat.</div>';
      return;
    }

    // Sort positions (default: opened_at descending, newest first)
    positions.sort((a, b) => {
      const field = this._posSortField;
      if (field === 'symbol') {
        const sa = (a.ticker || a.symbol || '').toLowerCase();
        const sb = (b.ticker || b.symbol || '').toLowerCase();
        return this._posSortAsc ? sa.localeCompare(sb) : sb.localeCompare(sa);
      }
      if (field === 'side') {
        const sa = (a.side || '').toLowerCase();
        const sb = (b.side || '').toLowerCase();
        return this._posSortAsc ? sa.localeCompare(sb) : sb.localeCompare(sa);
      }
      if (field === 'entry') {
        const va = parseFloat(a.entry_price || a.alert_price || 0);
        const vb = parseFloat(b.entry_price || b.alert_price || 0);
        return this._posSortAsc ? va - vb : vb - va;
      }
      if (field === 'spot') {
        const va = parseFloat(a.current_price || a.last_price || 0);
        const vb = parseFloat(b.current_price || b.last_price || 0);
        return this._posSortAsc ? va - vb : vb - va;
      }
      if (field === 'pnl') {
        const getPnl = (p) => {
          if (p.pnl_pct !== undefined && !isNaN(p.pnl_pct)) return parseFloat(p.pnl_pct);
          const e = parseFloat(p.entry_price || p.alert_price || 0);
          const s = parseFloat(p.current_price || p.last_price || 0);
          if (e > 0 && s > 0) {
            const isL = (p.side || 'LONG').toUpperCase().includes('LONG') || (p.side || 'LONG').toUpperCase().includes('CALL');
            return isL ? ((s - e) / e) * 100 : ((e - s) / e) * 100;
          }
          return 0;
        };
        const va = getPnl(a);
        const vb = getPnl(b);
        return this._posSortAsc ? va - vb : vb - va;
      }
      if (field === 'stop') {
        const va = parseFloat(a.stop || 0);
        const vb = parseFloat(b.stop || 0);
        return this._posSortAsc ? va - vb : vb - va;
      }
      if (field === 'target') {
        const va = parseFloat(a.target || 0);
        const vb = parseFloat(b.target || 0);
        return this._posSortAsc ? va - vb : vb - va;
      }
      const ta = (a.opened_at || '').toLowerCase();
      const tb = (b.opened_at || '').toLowerCase();
      return this._posSortAsc ? ta.localeCompare(tb) : tb.localeCompare(ta);
    });

    container.innerHTML = `
      <table class="cockpit-table">
        <thead>
          <tr>
            <th onclick="AppIntraday.sortPositions('symbol')" style="cursor:pointer; user-select:none;" title="Click to sort by Symbol">SYMBOL ${this.posSortIndicator('symbol')}</th>
            <th onclick="AppIntraday.sortPositions('side')" style="cursor:pointer; user-select:none;" title="Click to sort by Side">SIDE ${this.posSortIndicator('side')}</th>
            <th onclick="AppIntraday.sortPositions('entry')" style="cursor:pointer; user-select:none;" title="Click to sort by Entry Price">ENTRY ${this.posSortIndicator('entry')}</th>
            <th onclick="AppIntraday.sortPositions('spot')" style="cursor:pointer; user-select:none;" title="Click to sort by Current Spot">SPOT ${this.posSortIndicator('spot')}</th>
            <th onclick="AppIntraday.sortPositions('pnl')" style="cursor:pointer; user-select:none;" title="Click to sort by P&L %">P&L % ${this.posSortIndicator('pnl')}</th>
            <th onclick="AppIntraday.sortPositions('stop')" style="cursor:pointer; user-select:none;" title="Click to sort by Stop Price">STOP ${this.posSortIndicator('stop')}</th>
            <th onclick="AppIntraday.sortPositions('target')" style="cursor:pointer; user-select:none;" title="Click to sort by Target Price">TARGET ${this.posSortIndicator('target')}</th>
            <th onclick="AppIntraday.sortPositions('opened_at')" style="cursor:pointer; user-select:none;" title="Click to sort by Opened At Time">OPENED AT ${this.posSortIndicator('opened_at')}</th>
            <th style="text-align:right;">ACTION</th>
          </tr>
        </thead>
        <tbody>
          ${positions.map(p => {
            const sym = p.ticker || p.symbol || 'N/A';
            const side = (p.side || 'LONG').toUpperCase();
            const isLong = (side === 'LONG' || side === 'BUY' || side === 'CALL');
            const entry = p.entry_price ? `$${parseFloat(p.entry_price).toFixed(2)}` : (p.alert_price ? `$${parseFloat(p.alert_price).toFixed(2)}` : 'N/A');
            const spot = p.current_price ? `$${parseFloat(p.current_price).toFixed(2)}` : (p.last_price ? `$${parseFloat(p.last_price).toFixed(2)}` : 'N/A');
            
            let pnl = p.pnl_pct;
            if (pnl === undefined || isNaN(pnl)) {
              const e = parseFloat(p.entry_price || p.alert_price || 0);
              const s = parseFloat(p.current_price || p.last_price || 0);
              if (e > 0 && s > 0) {
                pnl = isLong ? ((s - e) / e) * 100 : ((e - s) / e) * 100;
              } else {
                pnl = 0.0;
              }
            }
            const pnlColor = window.AppUtils ? window.AppUtils.pnlColor(pnl) : (pnl >= 0 ? '#10b981' : '#ef4444');
            const stop = p.stop ? `$${parseFloat(p.stop).toFixed(2)}` : 'N/A';
            const target = p.target ? `$${parseFloat(p.target).toFixed(2)}` : 'N/A';
            const opened = (p.opened_at || '').substring(11, 19) || 'Active';
            const lastEval = p.last_eval ? p.last_eval.replace(/\n+/g, ' · ') : '0DTE ATM Calls Active · Scale 50% at Target 1, ratchet stop to Breakeven (+0.05c).';

            return `
              <tr>
                <td class="tv-symbol-hover" data-ticker="${sym}" style="font-weight:700; color:var(--cyan-glow); cursor:pointer;" onclick="AppIntraday.queryPosition('${sym}', '${side}', '${entry}', '${spot}', '${pnl}')" title="Click to query $${sym} in REV CHAT · Hover for Live TradingView Chart">${sym} 🔍</td>
                <td><span class="pill ${isLong ? 'green' : 'red'}" style="padding:1px 6px; font-size:10px;">${side}</span></td>
                <td style="font-family:'JetBrains Mono', monospace;">${entry}</td>
                <td style="font-weight:600; font-family:'JetBrains Mono', monospace;">${spot}</td>
                <td style="color:${pnlColor}; font-weight:700; font-family:'JetBrains Mono', monospace;">${pnl >= 0 ? '+' : ''}${Number(pnl).toFixed(2)}%</td>
                <td style="color:var(--rose-light); font-family:'JetBrains Mono', monospace;">${stop}</td>
                <td style="color:var(--emerald-light); font-family:'JetBrains Mono', monospace;">${target}</td>
                <td style="color:var(--text-muted); font-size:11px;">${opened}</td>
                <td style="text-align:right; display:flex; gap:6px; justify-content:flex-end; align-items:center;">
                  <button class="btn secondary" onclick="if(window.AppSwing) AppSwing.openTradingViewModal('${sym}', '15')" style="padding:2px 7px; font-size:10.5px; font-weight:700; color:var(--cyan-glow);" title="Open 15m Chart">📈 Chart</button>
                  <button class="btn secondary" onclick="AppIntraday.queryPosition('${sym}', '${side}', '${entry}', '${spot}', '${pnl}')" style="padding:2px 7px; font-size:10.5px; font-weight:700; color:var(--blue);" title="Ask Copilot to analyze this position">💬 Query</button>
                  <button class="btn danger" onclick="AppIntraday.closePosition('${sym}')" style="padding:2px 7px; font-size:10.5px;">🛑 Close</button>
                </td>
              </tr>
              <tr style="background:rgba(6,182,212,0.03); border-bottom:1px solid var(--border);">
                <td colspan="9" style="padding:6px 14px; font-size:11px; font-family:'JetBrains Mono', monospace; color:var(--text-muted);">
                  <span style="color:#38bdf8; font-weight:700;">TACTICAL PLAYBOOK:</span> ${lastEval}
                </td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;
  },

  queryPosition(sym, side, entry, spot, pnl) {
    if (window.AppChat) {
      window.AppChat.setRevChatFocus(sym);
      const tickerInput = document.getElementById('ticker-input');
      if (tickerInput) tickerInput.value = sym;
      const pnlNum = parseFloat(pnl) || 0.0;
      const pnlStr = (pnlNum >= 0 ? '+' : '') + pnlNum.toFixed(2) + '%';
      window.AppChat.askRevChat(`Evaluate my active intraday ${side} position in $${sym} (Entry: ${entry}, Current Spot: ${spot}, P&L: ${pnlStr}). What is the tactical management directive right now — should I trim, trail stop, or hold for target?`);
    }
  },

  queryAllPositions() {
    if (window.AppChat) {
      window.AppChat.clearRevChatFocus();
      window.AppChat.askRevChat("Review all of my active open intraday positions from data/positions.json. Give me an executive management breakdown for each: which ones are working and should be trimmed/trailed, and which ones are lagging or underwater?");
    }
  },

  async closePosition(ticker) {
    if (!confirm(`Manually close and flatten open position for ${ticker}?`)) return;
    try {
      const data = await window.AppApi.closePosition(ticker);
      if (!data.success) throw new Error(data.message || 'Failed to close position');
      await this.loadIntradayPositions();
      if (window.AppLogs) window.AppLogs.loadLogs();
    } catch (e) {
      alert(`Close Position Error: ${e.message}`);
    }
  },

  async flattenIntradayPositions() {
    if (!confirm('EOD Flatten: Close and flatten ALL open intraday/0DTE positions?')) return;
    try {
      const data = await window.AppApi.flattenIntraday();
      alert(`Successfully flattened ${data.count} intraday position(s).`);
      await this.loadIntradayPositions();
      if (window.AppLogs) window.AppLogs.loadLogs();
    } catch (e) {
      alert(`Flatten Error: ${e.message}`);
    }
  },

  async runSimulate0DTE() {
    const ticker = (document.getElementById('sim-ticker').value || '').trim().toUpperCase();
    const action = document.getElementById('sim-action').value;
    const price = parseFloat(document.getElementById('sim-price').value);
    const why_now = (document.getElementById('sim-why').value || '').trim();

    if (!ticker || isNaN(price)) {
      return alert('Please enter valid ticker and trigger price');
    }

    const outBox = document.getElementById('sim-output-box');
    const decEl = document.getElementById('sim-res-decision');
    const playEl = document.getElementById('sim-res-playbook');

    if (outBox) outBox.style.display = 'block';
    if (decEl) decEl.innerText = '⏳ Evaluating with Qwen3.8-27B...';
    if (playEl) playEl.innerText = 'Running 0DTE rules card (revanth-0dte.md)...';

    try {
      const data = await window.AppApi.simulate0DTE({ ticker, action, price, why_now });
      if (data.status === 'error') {
        if (decEl) decEl.innerText = '❌ EVALUATION ERROR';
        if (playEl) playEl.innerText = data.error || 'Server error';
      } else {
        if (decEl) decEl.innerText = data.decision || 'DECISION COMPLETE';
        if (playEl) playEl.innerText = data.playbook || '(No playbook commentary returned)';
      }
    } catch (e) {
      if (decEl) decEl.innerText = '❌ EVALUATION ERROR';
      if (playEl) playEl.innerText = e.message;
    }
  },

  sortAlerts(field) {
    if (this._alertsSortField === field) {
      this._alertsSortAsc = !this._alertsSortAsc;
    } else {
      this._alertsSortField = field;
      this._alertsSortAsc = (field === 'symbol' || field === 'action') ? true : false;
    }
    this.renderAlertsTable();
  },

  alertSortIndicator(field) {
    if (this._alertsSortField !== field) {
      return '<span style="opacity:0.35; font-size:10px; margin-left:3px;">⇅</span>';
    }
    return `<span style="color:var(--cyan-glow); font-size:11px; font-weight:800; margin-left:3px;">${this._alertsSortAsc ? '▲' : '▼'}</span>`;
  },

  async loadDailyAlerts() {
    try {
      // Fetch TradingView Intraday alerts from SQLite trading_alerts.db
      let data = await window.AppApi.getAlertsHistory(50, null, null, 'Intraday');
      let alerts = data.alerts || [];

      // Fallback if no specific Intraday filter match
      if (alerts.length === 0) {
        const fallbackData = await window.AppApi.getAlertsHistory(30);
        const allAlerts = fallbackData.alerts || [];
        alerts = allAlerts.filter(a => (a.strategy || '').toLowerCase() === 'intraday');
      }

      this._rawAlerts = alerts;
      const countPill = document.getElementById('intraday-alerts-count');
      if (countPill) countPill.innerText = `${alerts.length} Alerts Logged`;

      this.renderAlertsTable();
    } catch (e) {
      console.error('Failed loading daily intraday alerts', e);
    }
  },

  renderAlertsTable() {
    const container = document.getElementById('intraday-alerts-table');
    if (!container) return;

    const alerts = [...this._rawAlerts];
    if (alerts.length === 0) {
      container.innerHTML = '<div style="color:var(--text-muted); font-size:13px; text-align:center; padding:20px;">No intraday alerts received yet today. Click "Check Gmail" to poll.</div>';
      return;
    }

    // Sort alerts (default: timestamp descending, newest first)
    alerts.sort((a, b) => {
      const field = this._alertsSortField;
      if (field === 'symbol') {
        const sa = (a.symbol || '').toLowerCase();
        const sb = (b.symbol || '').toLowerCase();
        return this._alertsSortAsc ? sa.localeCompare(sb) : sb.localeCompare(sa);
      }
      if (field === 'action') {
        const sa = (a.action || '').toLowerCase();
        const sb = (b.action || '').toLowerCase();
        return this._alertsSortAsc ? sa.localeCompare(sb) : sb.localeCompare(sa);
      }
      if (field === 'price') {
        const pa = parseFloat(a.alert_price || a.market_price || 0);
        const pb = parseFloat(b.alert_price || b.market_price || 0);
        return this._alertsSortAsc ? pa - pb : pb - pa;
      }
      if (field === 'score') {
        const sa = parseFloat(a.score || 0);
        const sb = parseFloat(b.score || 0);
        return this._alertsSortAsc ? sa - sb : sb - sa;
      }
      if (field === 'decision') {
        const da = (a.llm_decision || '').toLowerCase();
        const db = (b.llm_decision || '').toLowerCase();
        return this._alertsSortAsc ? da.localeCompare(db) : db.localeCompare(da);
      }
      // Default: timestamp (newest first when _alertsSortAsc is false)
      const ta = (a.timestamp || '').toLowerCase();
      const tb = (b.timestamp || '').toLowerCase();
      return this._alertsSortAsc ? ta.localeCompare(tb) : tb.localeCompare(ta);
    });

    container.innerHTML = `
      <table class="cockpit-table">
        <thead>
          <tr>
            <th onclick="AppIntraday.sortAlerts('timestamp')" style="cursor:pointer; user-select:none;" title="Click to sort by Time">TIME (ET) ${this.alertSortIndicator('timestamp')}</th>
            <th onclick="AppIntraday.sortAlerts('symbol')" style="cursor:pointer; user-select:none;" title="Click to sort by Ticker">TICKER ${this.alertSortIndicator('symbol')}</th>
            <th onclick="AppIntraday.sortAlerts('action')" style="cursor:pointer; user-select:none;" title="Click to sort by Action">ACTION ${this.alertSortIndicator('action')}</th>
            <th onclick="AppIntraday.sortAlerts('price')" style="cursor:pointer; user-select:none;" title="Click to sort by Alert Trigger Price">ALERT PX ${this.alertSortIndicator('price')}</th>
            <th onclick="AppIntraday.sortAlerts('score')" style="cursor:pointer; user-select:none;" title="Click to sort by Score">SCORE / GRADE ${this.alertSortIndicator('score')}</th>
            <th onclick="AppIntraday.sortAlerts('decision')" style="cursor:pointer; user-select:none;" title="Click to sort by AI Decision">AI DECISION ${this.alertSortIndicator('decision')}</th>
            <th style="text-align:right;">TACTICAL</th>
          </tr>
        </thead>
        <tbody>
          ${alerts.map(a => {
            const timeStr = (a.timestamp || '').substring(11, 19) || a.timestamp || '—';
            const actUpper = (a.action || 'ALERT').toUpperCase();
            let badgeColor = 'cyan';
            if (actUpper.includes('PUT') || actUpper.includes('SHORT')) badgeColor = 'red';
            else if (actUpper.includes('CALL') || actUpper.includes('BUY')) badgeColor = 'green';
            else if (actUpper.includes('EXIT')) badgeColor = 'amber';

            const compI = window.AppUtils ? AppUtils.getCompanyName(a.symbol) : a.symbol;
            const compTitle = (compI || a.symbol).replace(/"/g, '&quot;');
            const px = a.alert_price ? `$${parseFloat(a.alert_price).toFixed(2)}` : (a.market_price ? `$${parseFloat(a.market_price).toFixed(2)}` : '—');
            const gradeScore = (a.grade || a.score) ? `${a.grade ? `[${a.grade}] ` : ''}${a.score ? `${a.score}/100` : ''}` : '—';
            const aiDec = a.llm_decision || '<span style="color:var(--text-muted); font-style:italic;">Processing / Stand Aside</span>';
            const alertKey = a.message_id || a.id || a.email_id;

            return `
              <tr>
                <td style="color:var(--text-muted); font-size:11px; white-space:nowrap;">${timeStr}</td>
                <td class="tv-symbol-hover" data-ticker="${a.symbol}" style="font-weight:700; color:var(--cyan-glow); cursor:pointer;" onclick="if(window.AppSwing) AppSwing.openTradingViewModal('${a.symbol}', '15')" title="${compTitle} ($${a.symbol}) · Click to open chart · Hover for Live TradingView Chart">${a.symbol}</td>
                <td><span class="pill ${badgeColor}" style="padding:2px 8px; font-size:10px; font-weight:700;">${actUpper}</span></td>
                <td style="font-weight:600;">${px}</td>
                <td style="font-size:11px; color:var(--text-main); font-weight:600;">${gradeScore}</td>
                <td style="color:var(--text-main); font-size:12px; max-width:280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${(a.llm_decision || '').replace(/"/g, '&quot;')}">${aiDec}</td>
                <td style="text-align:right;">
                  <button class="btn secondary" onclick="if(window.AppAlerts) AppAlerts.openAlertModal('${alertKey}')" style="padding:2px 8px; font-size:10px; font-weight:700; color:var(--cyan-glow);" title="Inspect tactical levels & broker chain">INSPECT</button>
                </td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;
  }
};
