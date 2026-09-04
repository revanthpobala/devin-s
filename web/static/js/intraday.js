/**
 * Intraday 0DTE Desk Controller
 */
window.AppIntraday = {
  async loadIntradayPositions() {
    try {
      const data = await window.AppApi.getPositions();
      const container = document.getElementById('intraday-positions-table');
      const countPill = document.getElementById('intraday-pos-count');
      const positions = data.positions || [];
      if (countPill) countPill.innerText = `${positions.length} Open Positions`;

      // Update NY Session Clock
      const now = new Date();
      const nyTime = now.toLocaleTimeString('en-US', {
        timeZone: 'America/New_York',
        hour: '2-digit',
        minute: '2-digit'
      });
      const elClock = document.getElementById('val-et-clock');
      if (elClock) elClock.innerText = `${nyTime} ET`;

      // Also refresh the daily alerts feed
      this.loadDailyAlerts();

      if (!container) return;

      if (positions.length === 0) {
        container.innerHTML = '<div style="color:var(--text-muted); font-size:13px; text-align:center; padding:24px;">No open intraday positions in data/positions.json. All flat.</div>';
        return;
      }

      container.innerHTML = `
        <table class="cockpit-table">
          <thead>
            <tr>
              <th>SYMBOL</th>
              <th>SIDE</th>
              <th>ENTRY</th>
              <th>SPOT</th>
              <th>P&L %</th>
              <th>STOP</th>
              <th>TARGET</th>
              <th>OPENED AT</th>
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
              const pnl = p.pnl_pct !== undefined ? p.pnl_pct : 0.0;
              const pnlColor = window.AppUtils.pnlColor(pnl);
              const stop = p.stop ? `$${parseFloat(p.stop).toFixed(2)}` : 'N/A';
              const target = p.target ? `$${parseFloat(p.target).toFixed(2)}` : 'N/A';
              const opened = (p.opened_at || '').substring(11, 19) || 'Active';

              return `
                <tr>
                  <td style="font-weight:700; color:var(--cyan-glow);">${sym}</td>
                  <td><span class="pill ${isLong ? 'green' : 'red'}" style="padding:1px 6px; font-size:10px;">${side}</span></td>
                  <td>${entry}</td>
                  <td style="font-weight:600;">${spot}</td>
                  <td style="color:${pnlColor}; font-weight:700;">${pnl >= 0 ? '+' : ''}${Number(pnl).toFixed(2)}%</td>
                  <td style="color:var(--rose-light);">${stop}</td>
                  <td style="color:var(--emerald-light);">${target}</td>
                  <td style="color:var(--text-muted); font-size:11px;">${opened}</td>
                  <td style="text-align:right;">
                    <button class="btn danger" onclick="AppIntraday.closePosition('${sym}')" style="padding:3px 8px; font-size:11px;">🛑 Close</button>
                  </td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    } catch (e) {
      console.error('Failed loading intraday positions', e);
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
      if (decEl) decEl.innerText = data.decision || 'DECISION COMPLETE';
      if (playEl) playEl.innerText = data.playbook || '(No playbook commentary returned)';
    } catch (e) {
      if (decEl) decEl.innerText = '❌ EVALUATION ERROR';
      if (playEl) playEl.innerText = e.message;
    }
  },

  async loadDailyAlerts() {
    try {
      const data = await window.AppApi.getWatchAlerts(30);
      const container = document.getElementById('intraday-alerts-table');
      const countPill = document.getElementById('intraday-alerts-count');
      const alerts = data.alerts || [];
      if (countPill) countPill.innerText = `${alerts.length} Alerts Logged`;
      if (!container) return;

      if (alerts.length === 0) {
        container.innerHTML = '<div style="color:var(--text-muted); font-size:13px; text-align:center; padding:20px;">No daily alerts received yet today.</div>';
        return;
      }

      container.innerHTML = `
        <table class="cockpit-table">
          <thead>
            <tr>
              <th>TIME</th>
              <th>TICKER</th>
              <th>ALERT TYPE</th>
              <th>PRICE</th>
              <th>MESSAGE / EVENT</th>
            </tr>
          </thead>
          <tbody>
            ${alerts.map(a => {
              const timeStr = (a.triggered_at || '').substring(11, 19) || a.date;
              const typeUpper = (a.trigger_type || 'ALERT').toUpperCase();
              let badgeColor = 'cyan';
              if (typeUpper.includes('STOP')) badgeColor = 'red';
              else if (typeUpper.includes('TARGET') || typeUpper.includes('ENTERED')) badgeColor = 'green';
              else if (typeUpper.includes('MISSED')) badgeColor = 'amber';

              return `
                <tr>
                  <td style="color:var(--text-muted); font-size:11px; white-space:nowrap;">${timeStr}</td>
                  <td style="font-weight:700; color:var(--cyan-glow);">${a.ticker}</td>
                  <td><span class="pill ${badgeColor}" style="padding:2px 6px; font-size:10px;">${a.trigger_type}</span></td>
                  <td style="font-weight:600;">$${parseFloat(a.spot_price).toFixed(2)}</td>
                  <td style="color:var(--text-main); font-size:12px;">${a.message}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    } catch (e) {
      console.error('Failed loading daily alerts', e);
    }
  }
};
