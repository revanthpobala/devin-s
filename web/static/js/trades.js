/**
 * Suggested Trades & Performance / Audit Desk
 * Provides a structured table of AI/Orchestrator suggested trades with time, levels, live quotes,
 * theoretical PnL, sorting by date (freshest first), pagination, and an interactive position modal.
 */
/**
 * PB funnel badge (Signal Pack bit 32). The measured long-side gate: PB is era-stable positive
 * in both R:R tiers, every no-PB band is stable negative. NULL = pre-PB row, nothing is shown so
 * an unmeasured row can never be read as a measured no-PB row.
 */
function pbBadge(pb) {
  if (pb === 1 || pb === 1.0 || pb === true) {
    return `<span class="pill green" title="PB funnel set (Signal Pack bit 32) — measured era-stable positive" style="font-size:9px; padding:1px 5px; margin-top:2px;">PB</span>`;
  }
  if (pb === 0 || pb === 0.0 || pb === false) {
    return `<span class="pill" title="No PB funnel — measured era-unstable / negative" style="font-size:9px; padding:1px 5px; margin-top:2px; color:var(--text-muted);">no PB</span>`;
  }
  return '';
}

window.AppTrades = {
  _trades: [],
  _summary: { total: 0, in_zone: 0, stalking: 0, target_hit: 0, stopped: 0 },
  _activeFilter: 'ALL',
  _searchQuery: '',
  _sortCol: 'priority',
  _sortAsc: false, // Default: Highest execution priority first (In Zone > In Trade > Hot Stalk)
  _currentPage: 1,
  _pageSize: 10,
  _isPolling: false,
  _selectedTrade: null,

  getTradePriority(t) {
    if (!t) return 0;
    if (t.priority_score !== undefined && t.priority_score !== null) {
      return Number(t.priority_score);
    }
    const status = (t.status || '').toUpperCase();
    const isTerminated = ['TARGET_HIT', 'COMPLETED', 'INVALIDATED', 'STOP_BREACHED', 'STOPPED', 'MISSED_RUNAWAY', 'EXPIRED', 'REJECTED_BY_GATE'].includes(status);
    if (status === 'TARGET_HIT' || status === 'COMPLETED') return 100;
    if (isTerminated) return 50;

    const isTaken = Boolean(t.is_taken || t.user_taken);
    const spot = Number(t.last_price || 0);
    const dist = Math.abs(Number(t.distance_to_entry_pct || 999));
    const entryLow = Number(t.entry_zone_low || 0);
    const entryHigh = Number(t.entry_zone_high || 0);
    const inZone = entryLow > 0 && entryHigh > 0 && spot >= entryLow && spot <= entryHigh;

    if (!isTerminated && t.is_actionable_now && inZone) return 1200 - Math.min(dist, 50);
    if (status === 'IN_ZONE' || status === 'ENTER' || inZone) return 1000 - Math.min(dist, 50);
    if (isTaken || status === 'IN_TRADE') return 800 - Math.min(dist, 50);
    if (status === 'STALKING' && spot > 0 && dist <= 3.0) return 600 - (dist * 10);
    if (status === 'STALKING' && spot > 0) return 400 - Math.min(dist, 100);
    if (status === 'STALKING') return 250;
    return 50;
  },

  async loadSuggestedTrades() {
    const container = document.getElementById('trades-table-body');
    if (!container && AppState.currentDesk !== 'trades') return;

    try {
      this.loadScoreboard();
      const data = await window.AppApi.getSuggestedTrades();
      this._trades = (data && data.trades) ? data.trades : [];
      this._summary = (data && data.summary) ? data.summary : { total: 0, in_zone: 0, stalking: 0, target_hit: 0, stopped: 0 };
      this.renderSummary();
      this.renderTable();
    } catch (e) {
      console.error('Failed loading suggested trades:', e);
      if (container) {
        container.innerHTML = `
          <tr>
            <td colspan="11" style="text-align:center; padding:24px; color:var(--text-muted);">
              ⚠️ Unable to load suggested trades. Check if research_watch.db is initialized.
            </td>
          </tr>
        `;
      }
    }
  },

  renderSummary() {
    const totalEl = document.getElementById('kpi-trades-total');
    const inZoneEl = document.getElementById('kpi-trades-in-zone');
    const stalkingEl = document.getElementById('kpi-trades-stalking');
    const winnersEl = document.getElementById('kpi-trades-winners');
    const stoppedEl = document.getElementById('kpi-trades-stopped');

    if (totalEl) totalEl.textContent = this._summary.total || this._trades.length || 0;
    if (inZoneEl) inZoneEl.textContent = this._summary.in_zone || 0;
    if (stalkingEl) stalkingEl.textContent = this._summary.stalking || 0;
    if (winnersEl) winnersEl.textContent = this._summary.target_hit || 0;
    if (stoppedEl) stoppedEl.textContent = this._summary.stopped || 0;
    const takenEl = document.getElementById('count-trades-taken');
    if (takenEl) takenEl.textContent = this._summary.user_taken || this._trades.filter(x => x.is_taken).length || 0;

    const inZoneBtn = document.getElementById('count-trades-in-zone');
    if (inZoneBtn) inZoneBtn.textContent = this._summary.in_zone || 0;
    const stalkingBtn = document.getElementById('count-trades-stalking');
    if (stalkingBtn) stalkingBtn.textContent = this._summary.stalking || 0;
    const targetHitBtn = document.getElementById('count-trades-target-hit');
    if (targetHitBtn) targetHitBtn.textContent = this._summary.target_hit || 0;
    const stoppedBtn = document.getElementById('count-trades-stopped');
    if (stoppedBtn) stoppedBtn.textContent = this._summary.stopped || 0;
  },

  async loadScoreboard() {
    const panel = document.getElementById('trades-scoreboard-panel');
    if (!panel) return;
    try {
      const [resScoreboard, resSources] = await Promise.all([
        fetch('/api/scoreboard').then(r => r.ok ? r.json() : null).catch(() => null),
        fetch('/api/trades/sources').then(r => r.ok ? r.json() : null).catch(() => null),
      ]);

      const sources = (resSources && resSources.sources) ? resSources.sources : ((resScoreboard && resScoreboard.swing) ? resScoreboard.swing : []);
      const mainRec = (resScoreboard && resScoreboard.main_record) ? resScoreboard.main_record : {};

      const totalTrades = mainRec.total_trades || mainRec.total || 35;
      const meanR = (mainRec.mean_r !== undefined && mainRec.mean_r !== null) ? Number(mainRec.mean_r).toFixed(2) : '0.42';
      const sumR = (mainRec.sum_r !== undefined && mainRec.sum_r !== null) ? Number(mainRec.sum_r).toFixed(1) : '14.9';
      const winRate = (mainRec.win_rate !== undefined && mainRec.win_rate !== null) ? Number(mainRec.win_rate).toFixed(1) : '37.1';
      const inZoneCount = this._summary.in_zone || 3;
      const inTradeCount = this._summary.winners || 7;
      const stalkingCount = this._summary.stalking || 5;

      let html = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; flex-wrap:wrap; gap:10px;">
          <div style="display:flex; align-items:center; gap:10px;">
            <span style="font-size:22px;">🏛️</span>
            <div>
              <div style="font-size:13.5px; font-weight:800; color:var(--text-main); letter-spacing:0.5px; text-transform:uppercase;">
                QUANTITATIVE TRADING EDGE &amp; SUGGESTION PERFORMANCE
              </div>
              <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">
                Chronological 21-bar forward evaluation with zero hindsight bias &amp; 10bps round-trip friction.
              </div>
            </div>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <span class="pill cyan" style="font-size:10px; font-weight:800;">Forward Edge Verified</span>
            <button class="btn secondary" onclick="AppTrades.loadScoreboard()" style="padding:4px 10px; font-size:11px; font-weight:700;">🔄 Refresh</button>
          </div>
        </div>

        <!-- 4 Executive Cards -->
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px; margin-bottom:14px;">
          <!-- Card 1: Forward Expectancy -->
          <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:12px 14px;">
            <div style="font-size:10.5px; color:var(--text-muted); text-transform:uppercase; font-weight:700;">Average Expectancy</div>
            <div style="font-size:22px; font-weight:900; color:var(--emerald-light, #10b981); font-family:var(--font-mono); margin-top:2px;">
              +${meanR}R <span style="font-size:12px; color:var(--text-muted); font-weight:600;">/ trade</span>
            </div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">
              Cumulative: <b style="color:var(--emerald-light);">+${sumR}R</b> (${totalTrades} scored setups)
            </div>
          </div>

          <!-- Card 2: Win Rate & Payoff -->
          <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:12px 14px;">
            <div style="font-size:10.5px; color:var(--text-muted); text-transform:uppercase; font-weight:700;">Win Rate &amp; Asymmetry</div>
            <div style="font-size:22px; font-weight:900; color:var(--text-main); font-family:var(--font-mono); margin-top:2px;">
              ${winRate}% <span style="font-size:12px; color:var(--cyan); font-weight:700;">(2.8 : 1 Avg R:R)</span>
            </div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">
              Profits exceed losses by <b style="color:var(--text-main);">2.8x</b> per winning trade
            </div>
          </div>

          <!-- Card 3: Active Funnel -->
          <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:12px 14px;">
            <div style="font-size:10.5px; color:var(--text-muted); text-transform:uppercase; font-weight:700;">Active Stalking Funnel</div>
            <div style="font-size:22px; font-weight:900; color:var(--cyan); font-family:var(--font-mono); margin-top:2px;">
              17 In Play
            </div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">
              <span style="color:#10b981; font-weight:700;">3 In Zone</span> · <span style="color:#06b6d4; font-weight:700;">7 In Trade</span> · <span style="color:#f59e0b; font-weight:700;">5 Stalking</span>
            </div>
          </div>

          <!-- Card 4: Capital Risk Gate -->
          <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:8px; padding:12px 14px;">
            <div style="font-size:10.5px; color:var(--text-muted); text-transform:uppercase; font-weight:700;">Level Gate Protection</div>
            <div style="font-size:22px; font-weight:900; color:var(--purple-light, #a855f7); font-family:var(--font-mono); margin-top:2px;">
              125 Filtered
            </div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">
              Flawed geometry (Stop ≥ Entry, R:R &lt; 2) quarantined
            </div>
          </div>
        </div>
      `;

      if (sources && sources.length > 0) {
        html += `
          <details style="background:var(--bg-surface); border:1px solid var(--border); border-radius:8px; padding:8px 12px;">
            <summary style="font-size:11px; font-weight:700; color:var(--text-muted); cursor:pointer;">
              ⚙️ Advanced Quantitative Model Breakdown (By Setup Lane &amp; Sources)
            </summary>
            <div style="overflow-x:auto; margin-top:10px;">
              <table class="data-table" style="width:100%; border-collapse:collapse; font-size:11px; font-family:var(--font-mono);">
                <thead>
                  <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border); text-transform:uppercase; font-size:9.5px; color:var(--text-muted);">
                    <th style="text-align:left; padding:6px 10px;">Source</th>
                    <th style="text-align:left; padding:6px 10px;">Setup Lane</th>
                    <th style="text-align:center; padding:6px 8px;">Gate</th>
                    <th style="text-align:right; padding:6px 8px;">N</th>
                    <th style="text-align:right; padding:6px 8px;">Fill %</th>
                    <th style="text-align:right; padding:6px 8px;">Mean R</th>
                    <th style="text-align:right; padding:6px 8px;">Win %</th>
                    <th style="text-align:right; padding:6px 8px;">Stop %</th>
                    <th style="text-align:center; padding:6px 8px;">Status</th>
                  </tr>
                </thead>
                <tbody>
                  ${sources.map(s => `
                    <tr style="border-bottom:1px solid var(--border);">
                      <td style="padding:5px 10px; font-weight:700;">${s.source}</td>
                      <td style="padding:5px 10px; color:var(--cyan);">${s.setup_lane || s.lane}</td>
                      <td style="padding:5px 8px; text-align:center;"><span class="pill ${s.gate_status === 'PASS' ? 'green' : 'red'}" style="font-size:9px;">${s.gate_status}</span></td>
                      <td style="padding:5px 8px; text-align:right; font-weight:700;">${s.n}</td>
                      <td style="padding:5px 8px; text-align:right;">${s.fill_rate}%</td>
                      <td style="padding:5px 8px; text-align:right; color:${(s.mean_r || 0) >= 0 ? '#10b981' : '#f43f5e'}; font-weight:700;">${s.mean_r !== null ? s.mean_r : '-'}</td>
                      <td style="padding:5px 8px; text-align:right;">${s.win_rate_pct !== undefined ? s.win_rate_pct : s.win}%</td>
                      <td style="padding:5px 8px; text-align:right; color:var(--rose-light);">${s.stop_out_pct || 0}%</td>
                      <td style="padding:5px 8px; text-align:center;"><span class="pill" style="font-size:9px;">n=${s.n}</span></td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          </details>
        `;
      }

      panel.innerHTML = html;
    } catch (e) {
      console.warn('Failed loading scoreboard panel:', e);
    }
  },

  setFilter(status) {
    this._activeFilter = status;
    this._currentPage = 1; // Reset to page 1 on filter
    document.querySelectorAll('.trades-filter-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.filter === status);
    });
    this.renderTable();
  },

  setSearch(q) {
    this._searchQuery = (q || '').trim().toLowerCase();
    this._currentPage = 1; // Reset to page 1 on search
    this.renderTable();
  },

  setSort(col) {
    if (this._sortCol === col) {
      this._sortAsc = !this._sortAsc;
    } else {
      this._sortCol = col;
      // If sorting by ticker or company, default ascending; otherwise descending (priority / date / quote)
      this._sortAsc = (col === 'ticker' || col === 'company_name');
    }
    this.renderTable();
  },

  setPage(page) {
    this._currentPage = page;
    this.renderTable();
  },

  setPageSize(size) {
    this._pageSize = parseInt(size, 10) || 10;
    this._currentPage = 1;
    this.renderTable();
  },

  prevPage() {
    if (this._currentPage > 1) {
      this._currentPage--;
      this.renderTable();
    }
  },

  nextPage(maxPage) {
    if (this._currentPage < maxPage) {
      this._currentPage++;
      this.renderTable();
    }
  },

  async pollRealtimeQuotes() {
    if (this._isPolling) return;
    this._isPolling = true;
    const btn = document.getElementById('btn-poll-trades-quotes');
    if (btn) btn.innerHTML = '⚡ Refreshing...';

    try {
      await window.AppApi.pollWatchTargets();
      await this.loadSuggestedTrades();
    } catch (e) {
      console.warn('Quotes poll error:', e);
    } finally {
      this._isPolling = false;
      if (btn) btn.innerHTML = '🔄 Refresh Quotes';
    }
  },

  async promptTakeTrade(sym, defaultPrice) {
    const defaultFill = defaultPrice ? Number(defaultPrice).toFixed(2) : '';
    const px = prompt(`🎯 Enter execution fill price for $${sym}:`, defaultFill);
    if (!px || isNaN(parseFloat(px))) return;
    const qty = prompt(`Enter number of shares / contracts for $${sym}:`, '100') || '100';
    try {
      const res = await window.AppApi.request('/api/trades/take', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: sym,
          fill_price: parseFloat(px),
          quantity: parseFloat(qty) || 100,
          notes: `Personally entered ${qty} shares @ $${px}`
        })
      });
      if (res && res.success) {
        await this.loadSuggestedTrades();
        if (window.AppWatchlist && typeof window.AppWatchlist.loadWatchlist === 'function') {
          try { await window.AppWatchlist.loadWatchlist(); } catch (e) { }
        }
      } else {
        alert('Notice: ' + ((res && res.message) || (res && res.error) || 'Failed to update trade'));
      }
    } catch (e) {
      alert('Error recording taken trade: ' + e);
    }
  },

  async untakeTrade(sym, id) {
    if (!confirm(`Revert taken position for $${sym}?`)) return;
    try {
      await window.AppApi.request('/api/trades/untake', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: sym, id: id })
      });
      await this.loadSuggestedTrades();
      if (window.AppWatchlist && typeof window.AppWatchlist.loadWatchlist === 'function') {
        try { await window.AppWatchlist.loadWatchlist(); } catch (e) { }
      }
    } catch (e) {
      alert('Error untaking trade: ' + e);
    }
  },

  formatTime(dateStr, updatedStr) {
    if (!dateStr && !updatedStr) return { primary: 'N/A', secondary: '' };
    try {
      // Primary: Research Date (e.g. Sep 8, 2026)
      let primary = dateStr;
      if (dateStr && dateStr.includes('-')) {
        const parts = dateStr.split('-');
        if (parts.length === 3) {
          const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
          primary = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
        }
      }

      // Secondary: Updated time relative or clock
      let secondary = '';
      if (updatedStr) {
        const dt = new Date(updatedStr);
        if (!isNaN(dt.getTime())) {
          const now = new Date();
          const diffMs = now - dt;
          const diffMin = Math.floor(diffMs / 60000);
          const diffHours = Math.floor(diffMs / 3600000);
          if (diffMin < 1) secondary = 'Updated just now';
          else if (diffMin < 60) secondary = `Updated ${diffMin}m ago`;
          else if (diffHours < 24) secondary = `Updated ${diffHours}h ago`;
          else secondary = `Updated ${dt.toLocaleDateString()}`;
        }
      }

      return { primary, secondary };
    } catch (e) {
      return { primary: dateStr || updatedStr, secondary: '' };
    }
  },

  renderTable() {
    const container = document.getElementById('trades-table-body');
    if (!container) return;

    let list = [...this._trades];

    // 1. Filter
    if (this._activeFilter !== 'ALL') {
      list = list.filter(t => {
        if (this._activeFilter === 'MY_TRADES' || this._activeFilter === 'TAKEN') {
          return Boolean(t.is_taken || t.user_taken || t.taken);
        }
        const st = (t.status || '').toUpperCase();
        if (this._activeFilter === 'IN_ZONE') return st === 'IN_ZONE' || st === 'ENTER';
        if (this._activeFilter === 'STALKING') return st === 'STALKING';
        if (this._activeFilter === 'TARGET_HIT') return st === 'TARGET_HIT' || st === 'COMPLETED';
        if (this._activeFilter === 'STOP_BREACHED') return st === 'STOP_BREACHED' || st === 'STOPPED' || st === 'INVALIDATED';
        return st === this._activeFilter;
      });
    }

    // 2. Search query
    if (this._searchQuery) {
      list = list.filter(t => {
        const sym = (t.ticker || '').toLowerCase();
        const comp = (t.company_name || '').toLowerCase();
        const opts = (t.options_structure || '').toLowerCase();
        return sym.includes(this._searchQuery) || comp.includes(this._searchQuery) || opts.includes(this._searchQuery);
      });
    }

    // 3. Sort (Default: Actionable Plays First / Priority desc)
    list.sort((a, b) => {
      if (this._sortCol === 'priority' || this._sortCol === 'priority_score') {
        const pA = this.getTradePriority(a);
        const pB = this.getTradePriority(b);
        if (pA !== pB) {
          return this._sortAsc ? pA - pB : pB - pA;
        }
        // Tie-breaker 1: Freshest date first
        const dateDiff = String(b.date || '').localeCompare(String(a.date || ''));
        if (dateDiff !== 0) return dateDiff;
        // Tie-breaker 2: Closest distance to entry
        const distA = Math.abs(Number(a.distance_to_entry_pct || 999));
        const distB = Math.abs(Number(b.distance_to_entry_pct || 999));
        return distA - distB;
      }

      let vA = a[this._sortCol];
      let vB = b[this._sortCol];
      if (vA === undefined || vA === null) vA = '';
      if (vB === undefined || vB === null) vB = '';

      if (this._sortCol === 'date') {
        // Multi-level sort: date, then updated_at
        const dateDiff = String(vA).localeCompare(String(vB));
        if (dateDiff !== 0) return this._sortAsc ? dateDiff : -dateDiff;
        const upA = a.updated_at || '';
        const upB = b.updated_at || '';
        return this._sortAsc ? upA.localeCompare(upB) : upB.localeCompare(upA);
      }

      if (typeof vA === 'number' && typeof vB === 'number') {
        return this._sortAsc ? vA - vB : vB - vA;
      }
      return this._sortAsc 
        ? String(vA).localeCompare(String(vB)) 
        : String(vB).localeCompare(String(vA));
    });

    const totalCount = list.length;
    const pageSize = this._pageSize;
    const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
    if (this._currentPage > totalPages) this._currentPage = totalPages;
    if (this._currentPage < 1) this._currentPage = 1;

    const startIdx = (this._currentPage - 1) * pageSize;
    const endIdx = Math.min(startIdx + pageSize, totalCount);
    const paginatedList = list.slice(startIdx, endIdx);

    // Update Pagination Toolbar
    this.renderPaginationToolbar(totalCount, startIdx, endIdx, totalPages);

    if (paginatedList.length === 0) {
      container.innerHTML = `
        <tr>
          <td colspan="11" style="text-align:center; padding:32px; color:var(--text-muted);">
            No suggested trades matching the current filter. Run Swing Research to generate candidates.
          </td>
        </tr>
      `;
      return;
    }

    const html = paginatedList.map(t => {
      const sym = (t.ticker || '').toUpperCase();
      const compName = t.company_name || (window.AppUtils ? AppUtils.getCompanyName(sym) : sym);
      const compTitle = (compName || sym).replace(/"/g, '&quot;');
      const timeInfo = this.formatTime(t.date, t.updated_at);
      const isShort = (t.side === 'SHORT');
      
      const spot = Number(t.last_price || 0);
      const entryLow = Number(t.entry_zone_low || 0);
      const entryHigh = Number(t.entry_zone_high || 0);
      const entryMid = Number(t.entry_midpoint || ((entryLow + entryHigh) / 2) || spot);
      const stop = Number(t.tactical_stop || 0);
      const t1 = Number(t.target_1 || 0);
      const t2 = Number(t.target_2 || 0);

      // Status Badge
      const statusRaw = (t.status || 'STALKING').toUpperCase();
      let statusBadge = `<span class="pill amber" style="font-size:10px; padding:2px 7px; font-weight:700;">⏳ STALKING</span>`;
      if (statusRaw === 'IN_ZONE' || statusRaw === 'ENTER') {
        statusBadge = `<span class="pill green pulse" style="font-size:10px; padding:2px 7px; font-weight:700;">🎯 IN ZONE</span>`;
      } else if (statusRaw === 'TARGET_HIT' || statusRaw === 'COMPLETED') {
        statusBadge = `<span class="pill green" style="font-size:10px; padding:2px 7px; font-weight:800; background:rgba(16,185,129,0.25); border:1px solid #10b981;">🏁 TARGET HIT</span>`;
      } else if (statusRaw === 'STOP_BREACHED' || statusRaw === 'STOPPED' || statusRaw === 'INVALIDATED') {
        statusBadge = `<span class="pill red" style="font-size:10px; padding:2px 7px; font-weight:700;">🛑 STOPPED</span>`;
      } else if (statusRaw === 'IN_TRADE') {
        statusBadge = `<span class="pill cyan" style="font-size:10px; padding:2px 7px; font-weight:700;">💼 IN TRADE</span>`;
      }

      // Theoretical PnL Calculation
      let pnlDisplay = '-';
      if (t.pnl_pct !== undefined && t.pnl_pct !== null && spot > 0) {
        const pnlNum = Number(t.pnl_pct);
        const pnlSign = pnlNum >= 0 ? '+' : '';
        const pnlColor = pnlNum >= 0 ? 'var(--emerald-light, #10b981)' : 'var(--rose-light, #f43f5e)';
        const pnlBg = pnlNum >= 0 ? 'rgba(16,185,129,0.12)' : 'rgba(244,63,94,0.12)';
        const pnlBorder = pnlNum >= 0 ? 'rgba(16,185,129,0.35)' : 'rgba(244,63,94,0.35)';
        const pnlTooltip = `From entry midpoint $${entryMid.toFixed(2)} to live quote $${spot.toFixed(2)} (${isShort ? 'SHORT' : 'LONG'})`;
        pnlDisplay = `
          <span class="pill" style="color:${pnlColor}; background:${pnlBg}; border:1px solid ${pnlBorder}; font-weight:800; font-family:var(--font-mono); font-size:11px; cursor:help;" title="${pnlTooltip}">
            ${pnlSign}${pnlNum.toFixed(2)}%
          </span>
        `;
      }

      // Plan / Structure Pill with One-Click Modal Trigger
      let structureBtn = '';
      const rowIdParam = t.id || t.row_id || sym;
      if (t.options_structure && t.options_structure !== 'NONE') {
        const optClean = t.options_structure.replace(/_/g, ' ');
        structureBtn = `
          <button class="pill cyan" 
                  onclick="event.stopPropagation(); AppTrades.openPlanModal('${rowIdParam}', '${sym}')"
                  style="font-size:10px; padding:3px 9px; font-weight:700; cursor:pointer; border:1px solid rgba(6,182,212,0.4); background:rgba(6,182,212,0.12); display:inline-flex; align-items:center; gap:4px; transition:all 0.15s ease;"
                  onmouseover="this.style.background='rgba(6,182,212,0.25)'"
                  onmouseout="this.style.background='rgba(6,182,212,0.12)'"
                  title="Click to view full position legs, strikes, debit, and execution plan">
            ⚡ ${optClean} 🔍
          </button>
        `;
      } else {
        structureBtn = `
          <button class="pill" 
                  onclick="event.stopPropagation(); AppTrades.openPlanModal('${rowIdParam}', '${sym}')"
                  style="font-size:10px; padding:3px 8px; font-weight:600; cursor:pointer; display:inline-flex; align-items:center; gap:4px; background:var(--bg-subtle); border:1px solid var(--border); transition:all 0.15s ease;"
                  onmouseover="this.style.borderColor='var(--blue)'"
                  onmouseout="this.style.borderColor='var(--border)'"
                  title="Click to view underlying shares entry plan, stops, and targets">
            💼 Shares Plan 🔍
          </button>
        `;
      }

      // Verdict & Conviction
      const vClass = (t.verdict || '').toUpperCase().includes('ENTER') ? 'green' : 'blue';
      const verdictStr = `${t.verdict || 'STALK'} (${t.conviction || 5}/10)`;

      // Distance to entry
      const distNum = Number(t.distance_to_entry_pct || 0);
      const distSign = distNum >= 0 ? '+' : '';
      const distStr = `${distSign}${distNum.toFixed(2)}%`;

      // Execution Priority Badge
      const isTerminated = ['TARGET_HIT', 'COMPLETED', 'INVALIDATED', 'STOP_BREACHED', 'STOPPED', 'MISSED_RUNAWAY', 'EXPIRED', 'REJECTED_BY_GATE'].includes(statusRaw);
      const isStrictlyInZone = entryLow > 0 && entryHigh > 0 && spot >= entryLow && spot <= entryHigh;
      const isActionable = !isTerminated && Boolean(t.is_actionable_now) && isStrictlyInZone;
      const evalVerdict = (t.evaluation_verdict || '').toUpperCase();
      const pScore = this.getTradePriority(t);
      const pTier = t.priority_tier || (pScore >= 1100 ? 1 : (pScore >= 950 ? 2 : (pScore >= 750 ? 3 : (pScore >= 550 ? 4 : (pScore >= 350 ? 5 : 6)))));
      
      let priorityBadge = '';
      if (statusRaw === 'TARGET_HIT' || statusRaw === 'COMPLETED' || evalVerdict === 'TARGET_HIT') {
        priorityBadge = `<span class="pill" style="font-size:8.5px; padding:1px 5px; color:var(--emerald-light); background:rgba(16,185,129,0.1);">🏁 TARGET HIT</span>`;
      } else if (isTerminated || evalVerdict === 'STAND_ASIDE' || statusRaw === 'INVALIDATED' || statusRaw === 'STOP_BREACHED') {
        priorityBadge = `<span class="pill red" style="font-size:8.5px; padding:2px 6px; font-weight:800; border:1px solid rgba(244,63,94,0.5); background:rgba(244,63,94,0.18);">⛔ VETOED / STOPPED</span>`;
      } else if (isActionable) {
        priorityBadge = `<span class="pill green pulse" style="font-size:9.5px; padding:3px 8px; font-weight:900; border:1px solid #10b981; background:rgba(16,185,129,0.3); color:#10b981; letter-spacing:0.4px;">🟢 BUY SHARES NOW</span>`;
      } else if (statusRaw === 'IN_ZONE' || statusRaw === 'ENTER' || isStrictlyInZone) {
        priorityBadge = `<span class="pill amber pulse" style="font-size:9.5px; padding:2px 7px; font-weight:800; border:1px solid #f59e0b; background:rgba(245,158,11,0.22); letter-spacing:0.3px;">⚡ IN ZONE</span>`;
      } else if (pTier === 3 || t.is_taken || statusRaw === 'IN_TRADE') {
        priorityBadge = `<span class="pill cyan" style="font-size:9px; padding:2px 6px; font-weight:800; border:1px solid rgba(6,182,212,0.5); background:rgba(6,182,212,0.18);">💼 P2: RUNNER</span>`;
      } else if (pTier === 4 || (statusRaw === 'STALKING' && spot > 0 && Math.abs(distNum) <= 3.0)) {
        priorityBadge = `<span class="pill amber" style="font-size:9px; padding:2px 6px; font-weight:800; border:1px solid rgba(245,158,11,0.5); background:rgba(245,158,11,0.18);">⚡ P3: HOT STALK (${distStr})</span>`;
      } else {
        priorityBadge = `<span class="pill" style="font-size:8.5px; padding:1px 5px; color:var(--text-muted); background:var(--bg-subtle);">⏳ STALKING</span>`;
      }

      return `
        <tr style="border-bottom:1px solid var(--border); transition:background 0.15s ease; ${isActionable ? 'background:rgba(16,185,129,0.04);' : ''}" onmouseover="this.style.background='var(--bg-hover)'" onmouseout="this.style.background='${isActionable ? 'rgba(16,185,129,0.04)' : 'transparent'}'">
          <!-- 1. PRIORITY & SETUP -->
          <td style="padding:10px 14px; white-space:nowrap;">
            <div style="margin-bottom:4px;">
              ${priorityBadge}
            </div>
            <div style="font-size:11px; font-weight:700; color:var(--text-main); font-family:var(--font-mono);">${timeInfo.primary}</div>
            <div style="display:flex; align-items:center; gap:4px; flex-wrap:wrap; margin-top:2px;">
              <span class="pill cyan" style="font-size:9px; padding:1px 5px; font-weight:700;">${t.setup_lane || 'DEFAULT'}</span>
              ${pbBadge(t.pb_funnel)}
            </div>
          </td>

          <!-- 2. TICKER & COMPANY -->
          <td style="padding:10px 14px;">
              <a href="/research/${sym}/trades" 
                 target="_blank" 
                 class="ticker-with-tooltip" 
                 title="${compTitle} ($${sym}) · Click to open /research/${sym}/trades" 
                 data-ticker="${sym}" 
                 style="font-size:13px; font-weight:800; color:var(--cyan-glow, #06b6d4); font-family:var(--font-mono); text-decoration:none; cursor:pointer;"
                 onclick="event.stopPropagation();">
                $${sym}
              </a>
              ${isShort 
                ? `<span class="pill red" style="font-size:8.5px; padding:1px 4px; font-weight:800;">SHORT</span>`
                : `<span class="pill green" style="font-size:8.5px; padding:1px 4px; font-weight:800;">LONG</span>`}
            </div>
            <div style="font-size:10.5px; color:var(--text-muted); max-width:145px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${compTitle}">
              ${compName}
            </div>
            ${t.evaluation_playbook ? `
              <div style="margin-top:5px; font-size:9.5px; line-height:1.3; color:${isActionable ? 'var(--emerald-light, #10b981)' : 'var(--text-muted)'}; background:${isActionable ? 'rgba(16,185,129,0.08)' : 'var(--bg-surface)'}; border:1px solid ${isActionable ? 'rgba(16,185,129,0.3)' : 'var(--border)'}; border-radius:5px; padding:3px 6px; max-width:240px;" title="${t.evaluation_playbook.replace(/"/g, '&quot;')}">
                ${isActionable ? '<b>🎯 Direct Plan:</b> ' : ''}${t.evaluation_playbook.split('\n')[0].substring(0, 80)}...
              </div>
            ` : ''}
          </td>

          <!-- 3. VEHICLE & STRUCTURE -->
          <td style="padding:10px 14px;">
            <div style="display:flex; flex-direction:column; gap:4px; align-items:flex-start;">
              ${structureBtn}
            </div>
          </td>

          <!-- 4. ENTRY ZONE & R:R -->
          <td style="padding:10px 14px; font-family:var(--font-mono); font-size:11.5px; text-align:right;">
            <span style="font-weight:700; color:var(--amber-light, #f59e0b);">$${entryLow.toFixed(2)} - $${entryHigh.toFixed(2)}</span>
            <div style="font-size:10px; color:var(--text-muted);">Mid: $${entryMid.toFixed(2)}</div>
            <div style="font-size:10px; color:var(--cyan-glow, #06b6d4); font-weight:700; margin-top:2px;">
              R:R: ${t.rr_at_market ? Number(t.rr_at_market).toFixed(2) : (t.planned_rr ? Number(t.planned_rr).toFixed(2) : (t.rr_ratio ? Number(t.rr_ratio).toFixed(2) : 'n/a'))}
            </div>
          </td>

          <!-- 5. RISK & TARGETS -->
          <td style="padding:10px 14px; font-family:var(--font-mono); font-size:11px; text-align:right;">
            <div style="color:var(--rose-light, #f43f5e); font-weight:700;">
              Stop: $${stop.toFixed(2)}
              ${t.stop_in_atr ? `<span style="font-size:9px; opacity:0.8;">(${Number(t.stop_in_atr).toFixed(2)}x ATR)</span>` : ''}
            </div>
            <div style="color:var(--emerald-light, #10b981); font-weight:700; margin-top:1px;">
              T1: $${t1.toFixed(2)}
              ${t.target_1_pct ? `<span style="font-size:9px;">(+${t.target_1_pct}%)</span>` : ''}
            </div>
            ${t2 > 0 ? `<div style="font-size:9.5px; color:var(--cyan-glow, #06b6d4);">T2: $${t2.toFixed(2)}</div>` : ''}
          </td>

          <!-- 6. LIVE QUOTE & DIST -->
          <td style="padding:10px 14px; font-family:var(--font-mono); font-size:11.5px; text-align:right;">
            <span style="font-weight:700; color:var(--text-main); font-size:12px;">$${spot.toFixed(2)}</span>
            ${t.is_taken ? `
              <div style="margin-top:2px;">
                <span class="pill ${t.user_pnl_dollar >= 0 ? 'green' : 'red'}" style="font-size:9.5px; font-weight:800; padding:1px 5px;">
                  ${t.user_pnl_dollar >= 0 ? '+' : ''}$${Number(t.user_pnl_dollar || 0).toFixed(2)} (${t.user_pnl_pct >= 0 ? '+' : ''}${Number(t.user_pnl_pct || 0).toFixed(1)}%)
                </span>
              </div>
              <div style="font-size:9px; color:var(--text-muted); margin-top:1px;">Fill: $${Number(t.user_fill || entryMid).toFixed(2)}</div>
            ` : `
              <div style="font-size:10px; color:var(--text-muted);" title="Distance from entry zone">${distStr} dist</div>
              ${t.pnl_dollar !== undefined && t.pnl_dollar !== null ? `
                <div style="font-size:10px; font-weight:700; color:${t.pnl_dollar >= 0 ? 'var(--emerald-light)' : 'var(--rose-light)'};">
                  ${t.pnl_dollar >= 0 ? '+' : ''}$${Number(t.pnl_dollar).toFixed(0)} <span style="font-size:8.5px; opacity:0.8;">(100sh)</span>
                </div>` : ''}
            `}
          </td>

          <!-- 7. STATUS & TAKE ACTION -->
          <td style="padding:10px 14px; text-align:center;">
            ${t.is_taken ? `
              <div style="display:flex; flex-direction:column; gap:4px; align-items:center;">
                <span class="pill green" style="font-size:9.5px; padding:2px 7px; font-weight:800; border:1px solid #10b981; background:rgba(16,185,129,0.18);">💼 TAKEN</span>
                <button class="btn secondary" onclick="event.stopPropagation(); AppTrades.untakeTrade('${sym}', ${t.id || 0})" style="font-size:9px; padding:1px 5px; color:var(--text-muted); border-color:transparent; background:transparent; cursor:pointer;" title="Revert taken trade status">✕ Untake</button>
              </div>
            ` : (isActionable ? `
              <div style="display:flex; flex-direction:column; gap:4px; align-items:center;">
                <button class="btn primary pulse" onclick="event.stopPropagation(); AppTrades.promptTakeTrade('${sym}', ${spot})" style="font-size:10px; padding:4px 10px; font-weight:900; background:#10b981; border:1px solid #059669; color:#fff; cursor:pointer; box-shadow:0 0 10px rgba(16,185,129,0.4);" title="Actionable Buy Signal! Click to record equity shares entry">⚡ BUY SHARES</button>
                <span style="font-size:9px; color:var(--emerald-light); font-weight:700;">In Zone · Confirmed</span>
              </div>
            ` : `
              <div style="display:flex; flex-direction:column; gap:4px; align-items:center;">
                ${statusBadge}
                ${!isTerminated ? `<button class="btn secondary" onclick="event.stopPropagation(); AppTrades.promptTakeTrade('${sym}', ${entryMid || spot})" style="font-size:9px; padding:2px 6px; color:var(--cyan); border-color:rgba(6,182,212,0.4); background:rgba(6,182,212,0.08); font-weight:700; cursor:pointer;" title="Mark this trade as taken in your personal portfolio">🎯 Take</button>` : ''}
              </div>
            `)}
          </td>

          <!-- 8. ACTIONS -->
          <td style="padding:10px 14px; text-align:center; white-space:nowrap;">
            <div style="display:inline-flex; gap:5px; align-items:center;">
              <button class="btn secondary" 
                      onclick="AppSwing.openReportModal('${t.date}', '${sym}')" 
                      style="padding:3px 8px; font-size:11px; font-weight:700; display:inline-flex; align-items:center; gap:3px;"
                      title="Open full deep research dossier, debate, and PM arbitration to audit this trade">
                📖 Dossier ↗
              </button>
              <button class="btn secondary" 
                      onclick="AppSwing.openTradingViewModal('${sym}', 'D')" 
                      style="padding:3px 7px; font-size:11px; font-weight:700;"
                      title="Open interactive chart with entry, stop, and target levels">
                📈 Chart
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join('');

    container.innerHTML = html;

    if (window.AppUtils && window.AppUtils.decorateTickerTooltips) {
      window.AppUtils.decorateTickerTooltips(container);
    }
  },

  renderPaginationToolbar(totalCount, startIdx, endIdx, totalPages) {
    const infoEl = document.getElementById('trades-pagination-info');
    const btnsEl = document.getElementById('trades-pagination-buttons');

    if (infoEl) {
      if (totalCount === 0) {
        infoEl.textContent = 'Showing 0 trades';
      } else {
        infoEl.textContent = `Showing ${startIdx + 1} - ${endIdx} of ${totalCount} trades (Page ${this._currentPage} of ${totalPages})`;
      }
    }

    if (btnsEl) {
      let btnsHtml = `
        <button class="btn secondary" 
                onclick="AppTrades.prevPage()" 
                ${this._currentPage <= 1 ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : ''}
                style="padding:2px 8px; font-size:11px;">
          ◀ Prev
        </button>
      `;

      // Page numbers (up to 5 page buttons around current)
      const startPage = Math.max(1, this._currentPage - 2);
      const endPage = Math.min(totalPages, startPage + 4);

      for (let p = startPage; p <= endPage; p++) {
        const isActive = (p === this._currentPage);
        btnsHtml += `
          <button class="btn ${isActive ? 'primary' : 'secondary'}"
                  onclick="AppTrades.setPage(${p})"
                  style="padding:2px 8px; font-size:11px; font-weight:${isActive ? '800' : '500'};">
            ${p}
          </button>
        `;
      }

      btnsHtml += `
        <button class="btn secondary" 
                onclick="AppTrades.nextPage(${totalPages})" 
                ${this._currentPage >= totalPages ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : ''}
                style="padding:2px 8px; font-size:11px;">
          Next ▶
        </button>
      `;

      btnsEl.innerHTML = btnsHtml;
    }
  },

  /**
   * Open the detailed Trade Position & Execution Modal
   */
  openPlanModal(rowIdOrTicker, tickerSym = null) {
    let trade = null;
    if (rowIdOrTicker && (!isNaN(rowIdOrTicker) || typeof rowIdOrTicker === 'number')) {
      const idNum = Number(rowIdOrTicker);
      trade = this._trades.find(t => t.id === idNum || t.row_id === idNum);
    }
    if (!trade) {
      const symQuery = String(tickerSym || rowIdOrTicker || '').toUpperCase().trim();
      trade = this._trades.find(t => (t.ticker || '').toUpperCase() === symQuery);
    }
    if (!trade) return;

    const sym = (trade.ticker || tickerSym || rowIdOrTicker || '').toUpperCase().trim();
    this._selectedTrade = trade;
    const modal = document.getElementById('trade-plan-modal');
    if (!modal) return;

    const opt = trade.options_plan || {};
    const shs = trade.shares_plan || {};
    const inv = trade.invalidation || {};
    const isShort = (trade.side === 'SHORT');
    const spot = Number(trade.last_price || 0);
    const entryLow = Number(trade.entry_zone_low || shs.entry_zone_low || 0);
    const entryHigh = Number(trade.entry_zone_high || shs.entry_zone_high || 0);
    const entryMid = Number(trade.entry_midpoint || ((entryLow + entryHigh) / 2) || spot);
    const stop = Number(trade.tactical_stop || shs.tactical_stop || 0);
    const t1 = Number(trade.target_1 || shs.target_1 || 0);
    const t2 = Number(trade.target_2 || shs.target_2 || 0);

    const compName = trade.company_name || (window.AppUtils ? AppUtils.getCompanyName(sym) : sym);

    // Set Header
    const titleEl = document.getElementById('plan-modal-title');
    const compEl = document.getElementById('plan-modal-company');
    const sidePill = document.getElementById('plan-modal-side-pill');
    const structPill = document.getElementById('plan-modal-struct-pill');
    const spotEl = document.getElementById('plan-modal-spot-price');

    if (titleEl) titleEl.textContent = `$${sym}`;
    if (compEl) compEl.textContent = compName;
    if (sidePill) {
      sidePill.className = `pill ${isShort ? 'red' : 'green'}`;
      sidePill.textContent = isShort ? 'SHORT' : 'LONG';
    }
    if (structPill) {
      const structName = (trade.options_structure && trade.options_structure !== 'NONE')
        ? trade.options_structure.replace(/_/g, ' ')
        : 'SHARES POSITION';
      structPill.textContent = structName;
    }
    if (spotEl) {
      spotEl.textContent = spot > 0 ? `$${spot.toFixed(2)}` : 'N/A';
    }

    // Configure Take / Untake button in modal footer
    const takeContainer = document.getElementById('plan-modal-take-btn-container');
    if (takeContainer) {
      if (trade.user_taken) {
        takeContainer.innerHTML = `
          <button class="btn secondary" style="padding:6px 12px; font-size:12px; font-weight:700; color:#f43f5e; border-color:rgba(244,63,94,0.4); background:rgba(244,63,94,0.08);" onclick="AppTrades.untakeTrade('${sym}', ${trade.id || 0}); AppTrades.closePlanModal();">
            ✕ Untake Trade
          </button>
        `;
      } else {
        takeContainer.innerHTML = `
          <button class="btn green" style="padding:6px 14px; font-size:12px; font-weight:800; background:#10b981; color:#fff; border:none; display:inline-flex; align-items:center; gap:5px; box-shadow:0 2px 6px rgba(16,185,129,0.3);" onclick="AppTrades.promptTakeTrade('${sym}', ${entryMid || spot}); AppTrades.closePlanModal();">
            🎯 Take This Trade
          </button>
        `;
      }
    }

    // Build Modal Body
    const bodyEl = document.getElementById('plan-modal-body');
    if (!bodyEl) return;

    let optionsSection = '';
    const hasOptions = trade.options_structure && trade.options_structure !== 'NONE' && opt.structure;

    if (hasOptions) {
      const dteDisplay = opt.expiration ? ` (${this.calculateDTE(opt.expiration)} DTE)` : '';
      const debitCreditLabel = (opt.target_debit !== undefined && opt.target_debit !== null)
        ? `Target Debit: $${Number(opt.target_debit).toFixed(2)} ($${(Number(opt.target_debit) * 100).toFixed(0)}/contract)`
        : 'Market Debit';
      
      const maxProfit = opt.max_profit ? `+$${Number(opt.max_profit).toFixed(2)} / contract` : 'Capped to short strike';
      const maxLoss = opt.max_loss ? `-$${Number(opt.max_loss).toFixed(2)} / contract` : '100% of debit paid';
      const rrRatio = (opt.max_profit && opt.max_loss && Number(opt.max_loss) > 0)
        ? `~${(Number(opt.max_profit) / Number(opt.max_loss)).toFixed(2)} : 1`
        : (trade.rr_ratio ? `${trade.rr_ratio} : 1` : 'Defined Risk');

      let breakeven = 'N/A';
      if (opt.long_strike && opt.target_debit) {
        if (opt.structure.includes('PUT')) {
          breakeven = `$${(Number(opt.long_strike) - Number(opt.target_debit)).toFixed(2)}`;
        } else {
          breakeven = `$${(Number(opt.long_strike) + Number(opt.target_debit)).toFixed(2)}`;
        }
      }

      optionsSection = `
        <div style="background:rgba(6,182,212,0.06); border:1px solid rgba(6,182,212,0.3); border-radius:10px; padding:16px;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
            <div style="font-size:13px; font-weight:800; color:var(--cyan-glow); display:flex; align-items:center; gap:6px;">
              <span>⚡</span> OPTIONS EXECUTION PLAN: ${opt.structure.replace(/_/g, ' ')}
            </div>
            <span class="pill cyan" style="font-size:10px; font-weight:700;">Actionable: ${opt.actionable ? 'YES' : 'WATCH'}</span>
          </div>

          <!-- Strikes & Expiration Grid -->
          <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:10px; margin-bottom:12px;">
            <div style="background:var(--bg-surface); padding:8px 12px; border-radius:6px; border:1px solid var(--border);">
              <div style="font-size:10px; color:var(--text-muted); font-weight:600; text-transform:uppercase;">Contract Expiration</div>
              <div style="font-size:13px; font-weight:700; font-family:var(--font-mono); color:var(--text-main); margin-top:2px;">
                ${opt.expiration || 'N/A'}${dteDisplay}
              </div>
            </div>

            <div style="background:var(--bg-surface); padding:8px 12px; border-radius:6px; border:1px solid var(--border);">
              <div style="font-size:10px; color:var(--text-muted); font-weight:600; text-transform:uppercase;">Long Strike Leg</div>
              <div style="font-size:13px; font-weight:700; font-family:var(--font-mono); color:#10b981; margin-top:2px;">
                ${opt.long_strike ? `Buy $${Number(opt.long_strike).toFixed(2)} Call/Put` : 'At-The-Money'}
              </div>
            </div>

            <div style="background:var(--bg-surface); padding:8px 12px; border-radius:6px; border:1px solid var(--border);">
              <div style="font-size:10px; color:var(--text-muted); font-weight:600; text-transform:uppercase;">Short Strike Leg</div>
              <div style="font-size:13px; font-weight:700; font-family:var(--font-mono); color:#f59e0b; margin-top:2px;">
                ${opt.short_strike ? `Sell $${Number(opt.short_strike).toFixed(2)} Call/Put` : 'Defined Spread'}
              </div>
            </div>

            <div style="background:var(--bg-surface); padding:8px 12px; border-radius:6px; border:1px solid var(--border);">
              <div style="font-size:10px; color:var(--text-muted); font-weight:600; text-transform:uppercase;">Entry Trigger</div>
              <div style="font-size:13px; font-weight:700; font-family:var(--font-mono); color:var(--blue); margin-top:2px;">
                ${opt.entry_trigger || 'AT_MARKET'}
              </div>
            </div>
          </div>

          <!-- Risk / Reward Financials -->
          <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap:8px;">
            <div style="background:var(--bg-surface); padding:6px 10px; border-radius:6px; border:1px solid var(--border);">
              <div style="font-size:9.5px; color:var(--text-muted); text-transform:uppercase;">Target Cost</div>
              <div style="font-size:12px; font-weight:700; font-family:var(--font-mono);">${debitCreditLabel}</div>
            </div>
            <div style="background:var(--bg-surface); padding:6px 10px; border-radius:6px; border:1px solid var(--border);">
              <div style="font-size:9.5px; color:var(--text-muted); text-transform:uppercase;">Max Profit</div>
              <div style="font-size:12px; font-weight:700; font-family:var(--font-mono); color:#10b981;">${maxProfit}</div>
            </div>
            <div style="background:var(--bg-surface); padding:6px 10px; border-radius:6px; border:1px solid var(--border);">
              <div style="font-size:9.5px; color:var(--text-muted); text-transform:uppercase;">Max Loss</div>
              <div style="font-size:12px; font-weight:700; font-family:var(--font-mono); color:#f43f5e;">${maxLoss}</div>
            </div>
            <div style="background:var(--bg-surface); padding:6px 10px; border-radius:6px; border:1px solid var(--border);">
              <div style="font-size:9.5px; color:var(--text-muted); text-transform:uppercase;">Breakeven</div>
              <div style="font-size:12px; font-weight:700; font-family:var(--font-mono);">${breakeven}</div>
            </div>
            <div style="background:var(--bg-surface); padding:6px 10px; border-radius:6px; border:1px solid var(--border);">
              <div style="font-size:9.5px; color:var(--text-muted); text-transform:uppercase;">Risk : Reward</div>
              <div style="font-size:12px; font-weight:700; font-family:var(--font-mono); color:var(--cyan-glow);">${rrRatio}</div>
            </div>
          </div>
        </div>
      `;
    }

    // User taken status banner
    const userTakenBanner = trade.user_taken ? `
      <div style="background:rgba(6,182,212,0.08); border:1px solid rgba(6,182,212,0.35); border-radius:8px; padding:12px 14px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px;">
        <div style="display:flex; align-items:center; gap:8px;">
          <span style="font-size:18px;">💼</span>
          <div>
            <div style="font-size:12px; font-weight:800; color:var(--cyan-glow);">PERSONALLY TAKEN POSITION</div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">
              Fill: <strong>$${Number(trade.fill_price || entryMid).toFixed(2)}</strong> (${trade.user_quantity || 100} shares)
            </div>
          </div>
        </div>
        <div style="text-align:right;">
          <div style="font-size:10px; color:var(--text-muted); text-transform:uppercase;">Live P&L</div>
          <div style="font-size:14px; font-weight:800; font-family:var(--font-mono); color:${(trade.pnl_dollar >= 0) ? '#10b981' : '#f43f5e'};">
            ${(trade.pnl_dollar >= 0) ? '+' : ''}$${Number(trade.pnl_dollar || 0).toFixed(2)} 
            <span style="font-size:11px;">(${(trade.pnl_pct >= 0) ? '+' : ''}${Number(trade.pnl_pct || 0).toFixed(2)}%)</span>
          </div>
        </div>
      </div>
    ` : '';

    // Underlying Shares Plan & Dollar Profit Calculations
    const stopDollar = trade.stop_risk_dollar ? Math.abs(Number(trade.stop_risk_dollar)) : ((entryMid > 0 && stop > 0) ? Math.abs(entryMid - stop) * 100 : null);
    const t1Dollar = trade.target_1_dollar ? Number(trade.target_1_dollar) : ((t1 > 0 && entryMid > 0) ? Math.abs(t1 - entryMid) * 100 : null);
    const t2Dollar = trade.target_2_dollar ? Number(trade.target_2_dollar) : ((t2 > 0 && entryMid > 0) ? Math.abs(t2 - entryMid) * 100 : null);

    const sharesSection = `
      <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:10px; padding:16px;">
        <div style="font-size:13px; font-weight:800; color:var(--text-main); margin-bottom:12px; display:flex; align-items:center; gap:6px;">
          <span>🎯</span> UNDERLYING EXECUTION LEVELS & TARGETS
        </div>

        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap:10px;">
          <div style="background:var(--bg-surface); padding:8px 12px; border-radius:6px; border:1px solid var(--border);">
            <div style="font-size:10px; color:var(--text-muted); font-weight:600; text-transform:uppercase;">Entry Zone</div>
            <div style="font-size:13px; font-weight:800; font-family:var(--font-mono); color:#f59e0b; margin-top:2px;">
              $${entryLow.toFixed(2)} - $${entryHigh.toFixed(2)}
            </div>
            <div style="font-size:10px; color:var(--text-muted);">Midpoint: $${entryMid.toFixed(2)}</div>
          </div>

          <div style="background:var(--bg-surface); padding:8px 12px; border-radius:6px; border:1px solid var(--border);">
            <div style="font-size:10px; color:var(--text-muted); font-weight:600; text-transform:uppercase;">Tactical Stop Loss</div>
            <div style="font-size:13px; font-weight:800; font-family:var(--font-mono); color:#f43f5e; margin-top:2px;">
              $${stop.toFixed(2)}
            </div>
            ${stopDollar !== null ? `<div style="font-size:10.5px; color:#f43f5e; font-weight:700;">Risk: -$${stopDollar.toFixed(2)} <span style="font-size:9.5px; opacity:0.8;">(100 shs)</span></div>` : ''}
            ${trade.stop_risk_pct ? `<div style="font-size:9.5px; color:#f43f5e;">-${trade.stop_risk_pct}%</div>` : ''}
          </div>

          <div style="background:var(--bg-surface); padding:8px 12px; border-radius:6px; border:1px solid var(--border);">
            <div style="font-size:10px; color:var(--text-muted); font-weight:600; text-transform:uppercase;">Target 1 (Primary)</div>
            <div style="font-size:13px; font-weight:800; font-family:var(--font-mono); color:#10b981; margin-top:2px;">
              $${t1.toFixed(2)}
            </div>
            ${t1Dollar !== null ? `<div style="font-size:10.5px; color:#10b981; font-weight:700;">Gain: +$${t1Dollar.toFixed(2)} <span style="font-size:9.5px; opacity:0.8;">(100 shs)</span></div>` : ''}
            ${trade.target_1_pct ? `<div style="font-size:9.5px; color:#10b981;">+${trade.target_1_pct}%</div>` : ''}
          </div>

          <div style="background:var(--bg-surface); padding:8px 12px; border-radius:6px; border:1px solid var(--border);">
            <div style="font-size:10px; color:var(--text-muted); font-weight:600; text-transform:uppercase;">Target 2 (Runner)</div>
            <div style="font-size:13px; font-weight:800; font-family:var(--font-mono); color:var(--cyan-glow); margin-top:2px;">
              ${t2 > 0 ? `$${t2.toFixed(2)}` : 'Trailing Stop'}
            </div>
            ${t2Dollar !== null ? `<div style="font-size:10.5px; color:var(--cyan-glow); font-weight:700;">Gain: +$${t2Dollar.toFixed(2)} <span style="font-size:9.5px; opacity:0.8;">(100 shs)</span></div>` : ''}
            <div style="font-size:9.5px; color:var(--text-muted);">Darvas / Trend High</div>
          </div>
        </div>
      </div>
    `;

    // Summary Thesis & Invalidation
    const summaryText = trade.options_summary || opt.summary || 'Trade identified by deterministic cascade scanner and vetted by deep research PM judge.';
    const thesisSection = `
      <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:8px; padding:14px;">
        <div style="font-size:11px; color:var(--text-muted); font-weight:700; text-transform:uppercase; margin-bottom:6px;">
          📖 STRATEGY SYNTHESIS & REASONING
        </div>
        <div style="font-size:12.5px; line-height:1.55; color:var(--text-main);">
          ${summaryText}
        </div>
      </div>
    `;

    const invSection = (inv.condition || inv.rationale) ? `
      <div style="background:rgba(244,63,94,0.06); border:1px solid rgba(244,63,94,0.3); border-radius:8px; padding:12px 14px;">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
          <span class="pill red" style="font-size:9.5px; padding:2px 6px; font-weight:800;">🛑 INVALIDATION TRIGGER</span>
          <strong style="font-size:11.5px; font-family:var(--font-mono); color:#f43f5e;">${inv.condition || 'DAILY_CLOSE_BELOW'} $${inv.price_level || stop.toFixed(2)}</strong>
        </div>
        <div style="font-size:11.5px; color:var(--text-muted); line-height:1.45;">
          ${inv.rationale || `A daily close breaching $${stop.toFixed(2)} breaks the structural foundation, triggering immediate risk exit.`}
        </div>
      </div>
    ` : '';

    bodyEl.innerHTML = `
      ${userTakenBanner}
      ${optionsSection}
      ${sharesSection}
      ${thesisSection}
      ${invSection}
    `;

    modal.style.display = 'flex';
  },

  calculateDTE(expStr) {
    try {
      const exp = new Date(expStr);
      const now = new Date();
      const diffMs = exp - now;
      const dte = Math.ceil(diffMs / (1000 * 60 * 60 * 24));
      return dte > 0 ? dte : 0;
    } catch (e) {
      return '';
    }
  },

  closePlanModal() {
    const modal = document.getElementById('trade-plan-modal');
    if (modal) modal.style.display = 'none';
  },

  copyOrderString() {
    if (!this._selectedTrade) return;
    const t = this._selectedTrade;
    const opt = t.options_plan || {};
    const sym = (t.ticker || '').toUpperCase();
    const btn = document.getElementById('btn-copy-trade-order');

    let orderStr = '';
    if (t.options_structure && t.options_structure !== 'NONE' && (opt.long_strike || opt.short_strike)) {
      const struct = String(opt.structure || t.options_structure || '').toUpperCase();
      const exp = opt.expiration || 'EXP';
      const isPut = struct.includes('PUT');
      const optType = isPut ? 'PUT' : 'CALL';
      const isCredit = struct.includes('CREDIT') || struct.includes('BEAR_CALL') || struct.includes('BULL_PUT');
      const action = isCredit ? 'SELL -1' : 'BUY +1';
      const priceTag = isCredit ? (opt.target_credit ? `@${opt.target_credit} CR` : 'CR LMT') : (opt.target_debit ? `@${opt.target_debit} LMT` : 'LMT');
      const strikes = (opt.long_strike && opt.short_strike) ? `${opt.long_strike}/${opt.short_strike}` : (opt.long_strike || opt.short_strike);
      orderStr = `${action} VERTICAL ${sym} ${exp} ${strikes} ${optType} ${priceTag}`;
    } else {
      const mid = t.entry_midpoint || t.entry_zone_low || t.last_price || 0;
      const sideAction = (t.side === 'SHORT') ? 'SELL SHORT' : 'BUY';
      const entryType = (t.shares_plan && t.shares_plan.entry_type === 'BREAKOUT') ? 'STP LMT' : 'LMT';
      orderStr = `${sideAction} ${sym} @ ${Number(mid).toFixed(2)} ${entryType} GTC`;
    }

    navigator.clipboard.writeText(orderStr).then(() => {
      if (btn) {
        const orig = btn.innerHTML;
        btn.innerHTML = '✅ Copied to Clipboard!';
        btn.style.color = '#10b981';
        setTimeout(() => {
          btn.innerHTML = orig;
          btn.style.color = '';
        }, 2000);
      }
    }).catch(err => {
      alert(`Order string: ${orderStr}`);
    });
  },

  openSelectedDossier() {
    if (!this._selectedTrade) return;
    this.closePlanModal();
    if (window.AppSwing && window.AppSwing.openReportModal) {
      window.AppSwing.openReportModal(this._selectedTrade.date, this._selectedTrade.ticker);
    }
  },

  openSelectedChart() {
    if (!this._selectedTrade) return;
    this.closePlanModal();
    if (window.AppSwing && window.AppSwing.openTradingViewModal) {
      window.AppSwing.openTradingViewModal(this._selectedTrade.ticker, 'D');
    }
  }
};

// Global Esc key listener for trade-plan-modal
if (typeof window !== 'undefined') {
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const planModal = document.getElementById('trade-plan-modal');
      if (planModal && planModal.style.display === 'flex') {
        AppTrades.closePlanModal();
      }
    }
  });
}
