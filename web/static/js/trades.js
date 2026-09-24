/**
 * Suggested Trades & Performance / Audit Desk
 * Provides a structured table of AI/Orchestrator suggested trades with time, levels, live quotes,
 * theoretical PnL, sorting by date (freshest first), pagination, and an interactive position modal.
 */
window.AppTrades = {
  _trades: [],
  _summary: { total: 0, in_zone: 0, stalking: 0, target_hit: 0, stopped: 0 },
  _activeFilter: 'ALL',
  _searchQuery: '',
  _sortCol: 'date',
  _sortAsc: false, // Default: freshest first
  _currentPage: 1,
  _pageSize: 10,
  _isPolling: false,
  _selectedTrade: null,

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
      const mainRec = (resScoreboard && resScoreboard.main_record) ? resScoreboard.main_record : null;

      let html = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; flex-wrap:wrap; gap:10px;">
          <div>
            <span style="font-size:13px; font-weight:800; color:var(--text-main); font-family:var(--font-mono);">📊 EMPIRICAL SCOREBOARD</span>
            <span style="font-size:11px; color:var(--text-muted); margin-left:8px;">Validated R-Performance across Lanes &amp; Sources</span>
          </div>
          <button class="btn secondary" onclick="AppTrades.loadScoreboard()" style="padding:2px 8px; font-size:11px;">🔄 Refresh Scoreboard</button>
        </div>
      `;

      if (mainRec) {
        html += `
          <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:4px; padding:10px 14px; margin-bottom:12px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px;">
            <div style="display:flex; align-items:center; gap:8px;">
              <span class="pill green" style="font-weight:800; font-size:11px;">🏆 MAIN RECORD</span>
              <span style="font-size:12px; color:var(--text-main); font-weight:700;">Since 2026-09-23 (Judge, PASS, NEW)</span>
            </div>
            <div style="display:flex; align-items:center; gap:16px; font-family:var(--font-mono); font-size:12px;">
              <span><strong>N:</strong> ${mainRec.total || 0}</span>
              <span><strong>Fill Rate:</strong> ${mainRec.fill_rate !== undefined ? mainRec.fill_rate : 0}%</span>
              <span><strong>Mean R:</strong> <span style="color:${(mainRec.mean_r || 0) >= 0 ? '#10b981' : '#f43f5e'}; font-weight:700;">${mainRec.mean_r !== null && mainRec.mean_r !== undefined ? mainRec.mean_r : '-'}R</span></span>
              <span><strong>Win Rate:</strong> ${mainRec.win_rate_pct !== undefined ? mainRec.win_rate_pct : 0}%</span>
              <span><strong>Stop Rate:</strong> ${mainRec.stop_out_pct !== undefined ? mainRec.stop_out_pct : 0}%</span>
              ${mainRec.flag_n30 ? '<span class="pill cyan" style="font-size:10px;">VALID (N>=30)</span>' : '<span class="pill amber" style="font-size:10px;">CALIBRATING</span>'}
            </div>
          </div>
        `;
      }

      if (sources && sources.length > 0) {
        html += `
          <div style="overflow-x:auto;">
            <table class="data-table" style="width:100%; border-collapse:collapse; font-size:11.5px; font-family:var(--font-mono);">
              <thead>
                <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border);">
                  <th style="text-align:left; padding:6px 10px;">Source</th>
                  <th style="text-align:left; padding:6px 10px;">Setup Lane</th>
                  <th style="text-align:center; padding:6px 8px;">Gate</th>
                  <th style="text-align:right; padding:6px 8px;">N</th>
                  <th style="text-align:right; padding:6px 8px;">Fill %</th>
                  <th style="text-align:right; padding:6px 8px;">Mean R</th>
                  <th style="text-align:right; padding:6px 8px;">Median R</th>
                  <th style="text-align:right; padding:6px 8px;">Win %</th>
                  <th style="text-align:right; padding:6px 8px;">Stop %</th>
                  <th style="text-align:right; padding:6px 8px;">Prior Win% / EV</th>
                  <th style="text-align:center; padding:6px 8px;">Status</th>
                </tr>
              </thead>
              <tbody>
        `;
        sources.forEach(s => {
          const meanColor = (s.mean_r || 0) >= 0 ? 'var(--emerald-light, #10b981)' : 'var(--rose-light, #f43f5e)';
          const priorText = s.lane_prior_win !== null && s.lane_prior_win !== undefined 
            ? `${s.lane_prior_win}% / ${s.lane_prior_ev ? `${s.lane_prior_ev > 0 ? '+' : ''}${s.lane_prior_ev}R` : '-'}`
            : '-';
          const nBadge = s.flag_n30
            ? `<span class="pill green" style="font-size:9.5px; padding:1px 5px;">N>=30 🔥</span>`
            : `<span class="pill" style="font-size:9.5px; padding:1px 5px; color:var(--text-muted);">n=${s.n}</span>`;
          html += `
            <tr style="border-bottom:1px solid var(--border);">
              <td style="padding:6px 10px; font-weight:700;">${s.source}</td>
              <td style="padding:6px 10px; color:var(--cyan-glow);">${s.setup_lane || s.lane}</td>
              <td style="padding:6px 8px; text-align:center;"><span class="pill ${s.gate_status === 'PASS' ? 'green' : 'red'}" style="font-size:9px;">${s.gate_status}</span></td>
              <td style="padding:6px 8px; text-align:right; font-weight:700;">${s.n}</td>
              <td style="padding:6px 8px; text-align:right;">${s.fill_rate}%</td>
              <td style="padding:6px 8px; text-align:right; color:${meanColor}; font-weight:700;">${s.mean_r !== null ? s.mean_r : '-'}</td>
              <td style="padding:6px 8px; text-align:right;">${s.median_r !== null ? s.median_r : '-'}</td>
              <td style="padding:6px 8px; text-align:right;">${s.win_rate_pct !== undefined ? s.win_rate_pct : s.win}%</td>
              <td style="padding:6px 8px; text-align:right; color:var(--rose-light);">${s.stop_out_pct || 0}%</td>
              <td style="padding:6px 8px; text-align:right; color:var(--text-muted);">${priorText}</td>
              <td style="padding:6px 8px; text-align:center;">${nBadge}</td>
            </tr>
          `;
        });
        html += `
              </tbody>
            </table>
          </div>
        `;
      } else {
        html += `<div style="color:var(--text-muted); font-size:12px; padding:8px 0;">No scored suggestion history recorded yet.</div>`;
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
      // If sorting by date or time, default to newest first (descending)
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

    // 3. Sort (Date desc / Freshest first by default)
    list.sort((a, b) => {
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

      return `
        <tr style="border-bottom:1px solid var(--border); transition:background 0.15s ease;" onmouseover="this.style.background='var(--bg-hover)'" onmouseout="this.style.background='transparent'">
          <!-- 1. TIME / DATE (FRESHEST FIRST) -->
          <td style="padding:10px 12px; white-space:nowrap;">
            <div style="font-size:11.5px; font-weight:700; color:var(--text-main); font-family:var(--font-mono);">${timeInfo.primary}</div>
            <div style="font-size:10px; color:var(--text-muted);">${timeInfo.secondary}</div>
          </td>

          <!-- 2. TICKER & COMPANY (WITH FULL COMPANY TOOLTIP) -->
          <td style="padding:10px 12px;">
            <div style="display:flex; align-items:center; gap:6px;">
              <strong class="ticker-with-tooltip" 
                      title="${compTitle}" 
                      data-ticker="${sym}" 
                      style="font-size:13px; font-weight:800; color:var(--cyan-glow, #06b6d4); font-family:var(--font-mono); cursor:help;">
                $${sym}
              </strong>
              ${isShort 
                ? `<span class="pill red" style="font-size:8.5px; padding:1px 4px; font-weight:800;">SHORT</span>`
                : `<span class="pill green" style="font-size:8.5px; padding:1px 4px; font-weight:800;">LONG</span>`}
            </div>
            <div style="font-size:10.5px; color:var(--text-muted); max-width:140px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${compTitle}">
              ${compName}
            </div>
          </td>

          <!-- 3. GATE -->
          <td style="padding:10px 8px; text-align:center;">
            <span class="pill ${t.gate_status === 'PASS' ? 'green' : 'red'}" style="font-size:9.5px; padding:2px 6px; font-weight:800;">
              ${t.gate_status || 'PASS'}
            </span>
          </td>

          <!-- 4. LANE -->
          <td style="padding:10px 8px; text-align:center;">
            <span class="pill cyan" style="font-size:9.5px; padding:2px 6px; font-weight:700;">
              ${t.setup_lane || 'DEFAULT'}
            </span>
          </td>

          <!-- 5. KIND -->
          <td style="padding:10px 8px; text-align:center;">
            <span class="pill ${t.kind === 'NEW' ? 'blue' : 'amber'}" style="font-size:9.5px; padding:2px 6px; font-weight:700;">
              ${t.kind || 'NEW'}
            </span>
          </td>

          <!-- 6. VERDICT & CONVICTION -->
          <td style="padding:10px 10px; text-align:center;">
            <span class="pill ${vClass}" style="font-size:10px; padding:2px 7px; font-weight:700;">
              ${verdictStr}
            </span>
          </td>

          <!-- 7. PLAN / STRUCTURE (CLICK TO OPEN POSITION MODAL) -->
          <td style="padding:10px 10px; text-align:center;">
            ${structureBtn}
          </td>

          <!-- 8. ENTRY ZONE -->
          <td style="padding:10px 10px; font-family:var(--font-mono); font-size:11.5px; text-align:right;">
            <span style="font-weight:700; color:var(--amber-light, #f59e0b);">$${entryLow.toFixed(2)} - $${entryHigh.toFixed(2)}</span>
            <div style="font-size:10px; color:var(--text-muted);">Mid: $${entryMid.toFixed(2)}</div>
          </td>

          <!-- 9. TACTICAL STOP (xATR) -->
          <td style="padding:10px 10px; font-family:var(--font-mono); font-size:11.5px; text-align:right; color:var(--rose-light, #f43f5e);">
            $${stop.toFixed(2)}
            ${t.stop_in_atr ? `<div style="font-size:10px; opacity:0.85;">${Number(t.stop_in_atr).toFixed(2)}x ATR</div>` : ''}
          </td>

          <!-- 10. TARGET 1 & 2 -->
          <td style="padding:10px 10px; font-family:var(--font-mono); font-size:11.5px; text-align:right;">
            <span style="font-weight:700; color:var(--emerald-light, #10b981);">$${t1.toFixed(2)}</span>
            ${t.target_1_pct ? `<span style="font-size:10px; color:var(--emerald-light); font-weight:600;"> (+${t.target_1_pct}%)</span>` : ''}
            ${t2 > 0 ? `<div style="font-size:10px; color:var(--cyan-glow);">T2: $${t2.toFixed(2)}</div>` : ''}
          </td>

          <!-- 11. R:R @ MKT -->
          <td style="padding:10px 8px; font-family:var(--font-mono); font-size:11.5px; text-align:right; font-weight:700; color:var(--cyan-glow);">
            ${t.rr_at_market ? Number(t.rr_at_market).toFixed(2) : '-'}
          </td>

          <!-- 12. LIVE QUOTE & DISTANCE -->
          <td style="padding:10px 10px; font-family:var(--font-mono); font-size:11.5px; text-align:right;">
            <span style="font-weight:700; color:var(--text-main);">$${spot.toFixed(2)}</span>
            <div style="font-size:10px; color:var(--text-muted);" title="Distance from entry zone">${distStr} dist</div>
          </td>

          <!-- 13. TRADE STATUS -->
          <td style="padding:10px 10px; text-align:center;">
            ${statusBadge}
          </td>

          <!-- 14. AUDIT & VERIFY ACTIONS -->
          <td style="padding:10px 10px; text-align:center; white-space:nowrap;">
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
      const sym = String(tickerSym || rowIdOrTicker || '').toUpperCase().trim();
      trade = this._trades.find(t => (t.ticker || '').toUpperCase() === sym);
    }
    if (!trade) return;

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

    // Underlying Shares Plan
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
            ${trade.stop_risk_pct ? `<div style="font-size:10px; color:#f43f5e;">Risk: -${trade.stop_risk_pct}%</div>` : ''}
          </div>

          <div style="background:var(--bg-surface); padding:8px 12px; border-radius:6px; border:1px solid var(--border);">
            <div style="font-size:10px; color:var(--text-muted); font-weight:600; text-transform:uppercase;">Target 1 (Primary)</div>
            <div style="font-size:13px; font-weight:800; font-family:var(--font-mono); color:#10b981; margin-top:2px;">
              $${t1.toFixed(2)}
            </div>
            ${trade.target_1_pct ? `<div style="font-size:10px; color:#10b981;">Gain: +${trade.target_1_pct}%</div>` : ''}
          </div>

          <div style="background:var(--bg-surface); padding:8px 12px; border-radius:6px; border:1px solid var(--border);">
            <div style="font-size:10px; color:var(--text-muted); font-weight:600; text-transform:uppercase;">Target 2 (Runner)</div>
            <div style="font-size:13px; font-weight:800; font-family:var(--font-mono); color:var(--cyan-glow); margin-top:2px;">
              ${t2 > 0 ? `$${t2.toFixed(2)}` : 'Trailing Stop'}
            </div>
            <div style="font-size:10px; color:var(--text-muted);">Darvas / Trend High</div>
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
