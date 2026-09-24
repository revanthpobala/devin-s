/**
 * Watchlist & Triggers Table Controller
 */
window.AppWatchlist = {
  _allTargets: [],
  _activeFilter: 'ACTIONABLE',
  _auditData: null,
  _activeAuditTab: 'ALL',
  _auditSearchQuery: '',
  _searchQuery: '',
  _radarSearchQuery: '',
  _sortCol: 'date',
  _sortAsc: false,
  _viewMode: 'table', // 'table' | 'cards'
  _groupBy: 'none',   // 'none' | 'date' | 'ticker' | 'status'
  _pageSize: 10,      // 10 | 25 | 50 | 'all'
  _currentPage: 1,
  _collapsedGroups: new Set(),
  _isPolling: false,
  _activeOpenCharts: new Set(),
  _activeSnapshotToggles: new Set(),
  _activeTradeIdeasToggles: new Set(),
  _lastTargetsJson: '',

  toggleTradeIdeas(sym) {
    const s = (sym || '').toUpperCase().trim();
    if (!s) return;
    if (this._activeTradeIdeasToggles.has(s)) {
      this._activeTradeIdeasToggles.delete(s);
    } else {
      this._activeTradeIdeasToggles.add(s);
    }
    this.render();
    this.renderRadarWidget();
  },

  async loadWatchlist() {
    try {
      const data = await window.AppApi.getWatchTargets();
      if (!data) return;
      const newTargets = Array.isArray(data.targets) ? data.targets : [];
      const newJson = JSON.stringify(newTargets);
      const isUnchanged = (newJson === this._lastTargetsJson);
      this._lastTargetsJson = newJson;
      this._allTargets = newTargets;
      this._perfData = data.performance || null;

      // Sync audit summary badge in toolbar (quietly)
      try { this.loadAuditSummaryOnly(); } catch (e) { }

      // If data has not changed at all, skip full DOM rebuilds entirely
      if (isUnchanged) return;

      // Always update full table tab if on watchlist desk
      try { this.render(); } catch (err1) { console.error('Error in render():', err1); }

      // If a user is actively viewing an open live chart in the radar widget, do NOT wipe the iframe!
      if (this._activeOpenCharts && this._activeOpenCharts.size > 0) {
        this._updateRadarCardPricesOnly();
        return;
      }

      try { this.renderRadarWidget(); } catch (err2) { console.error('Error in renderRadarWidget():', err2); }
    } catch (e) {
      console.error('Failed loading watchlist targets', e);
      if (!this._allTargets || this._allTargets.length === 0) {
        const radarEl = document.getElementById('swing-radar-widget');
        if (radarEl) {
          radarEl.innerHTML = `
            <div style="color:var(--text-muted); font-size:12px; text-align:center; padding:12px;">
              ⚠️ Unable to connect to watch database. Retrying...
            </div>
          `;
        }
      }
    }
  },

  _updateRadarCardPricesOnly() {
    if (!this._allTargets) return;
    this._allTargets.forEach(t => {
      const sym = (t.ticker || '').toUpperCase();
      const spotEl = document.getElementById(`radar-spot-${sym}`);
      if (spotEl && t.last_price) {
        const spot = Number(t.last_price || 0);
        spotEl.textContent = spot > 0 ? `$${spot.toFixed(2)}` : '--.--';
      }
    });
  },

  _lastPricePollTime: null,

  async pollRealtimeSilently() {
    if (this._isPolling) return;
    this._isPolling = true;
    const radarPill = document.getElementById('radar-count-pill');
    if (radarPill) radarPill.innerText = '⚡ Refreshing Quotes...';

    try {
      const res = await window.AppApi.pollWatchTargets();
      this._lastPricePollTime = new Date();
      await this.loadWatchlist();
      this.updateLivePriceTimestamp();
    } catch (e) {
      console.warn('Silent live price poll failed:', e);
      if (radarPill) radarPill.innerText = 'Realtime Quotes';
    } finally {
      this._isPolling = false;
    }
  },

  updateLivePriceTimestamp() {
    const radarPill = document.getElementById('radar-count-pill');
    if (!radarPill) return;
    if (this._lastPricePollTime) {
      const timeStr = this._lastPricePollTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      radarPill.innerHTML = `🟢 Live ${timeStr} <span style="font-size:10px; opacity:0.8;">(Auto 1m)</span>`;
      radarPill.className = 'pill green';
    }
  },

  async pollRealtimeNow() {
    if (this._isPolling) return;
    this._isPolling = true;
    const btn = document.getElementById('btn-poll-watchlist');
    if (btn) {
      btn.innerHTML = '🔄 Polling Realtime Quotes...';
      btn.disabled = true;
    }

    try {
      const res = await window.AppApi.pollWatchTargets();
      this._lastPricePollTime = new Date();
      if (res && res.updated_count !== undefined) {
        if (window.AppStatus) {
          window.AppStatus.showToast(`✅ Polled and updated ${res.updated_count} targets in realtime!`);
        }
      }
      await this.loadWatchlist();
      this.updateLivePriceTimestamp();
    } catch (e) {
      alert(`Polling error: ${e.message}`);
    } finally {
      this._isPolling = false;
      if (btn) {
        btn.innerHTML = '🔄 Poll Realtime Prices';
        btn.disabled = false;
      }
    }
  },

  setFilterTab(tab) {
    this._activeFilter = tab;
    this._currentPage = 1;
    document.querySelectorAll('.tab-pill-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.tab === tab);
    });
    this.render();
  },

  setSearchQuery(query) {
    this._searchQuery = (query || '').trim().toLowerCase();
    this._currentPage = 1;
    this.render();
  },

  setSort(col) {
    if (this._sortCol === col) {
      this._sortAsc = !this._sortAsc;
    } else {
      this._sortCol = col;
      this._sortAsc = true;
    }
    this._currentPage = 1;
    this.render();
  },

  setViewMode(mode) {
    this._viewMode = mode;
    const btnTbl = document.getElementById('view-btn-table');
    const btnCards = document.getElementById('view-btn-cards');
    if (btnTbl) btnTbl.classList.toggle('active', mode === 'table');
    if (btnCards) btnCards.classList.toggle('active', mode === 'cards');
    this.render();
  },

  setGroupBy(mode) {
    this._groupBy = mode || 'none';
    ['none', 'date', 'ticker', 'status'].forEach(m => {
      const btn = document.getElementById(`grp-btn-${m}`);
      if (btn) btn.classList.toggle('active', m === this._groupBy);
    });

    const toggleBtn = document.getElementById('btn-toggle-all-groups');
    if (toggleBtn) {
      toggleBtn.style.display = (this._groupBy === 'none') ? 'none' : 'inline-flex';
      toggleBtn.innerHTML = '▼ Collapse All';
    }

    this._collapsedGroups.clear();
    this._currentPage = 1;
    this.render();
  },

  setPageSize(size) {
    this._pageSize = (size === 'all') ? 'all' : (parseInt(size, 10) || 10);
    this._currentPage = 1;
    const select = document.getElementById('select-watchlist-pagesize');
    if (select && select.value !== String(size)) {
      select.value = String(size);
    }
    this.render();
  },

  setPage(page) {
    this._currentPage = page;
    this.render();
    const c = document.getElementById('watchlist-content-container');
    if (c) c.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  },

  toggleGroup(groupId) {
    if (this._collapsedGroups.has(groupId)) {
      this._collapsedGroups.delete(groupId);
    } else {
      this._collapsedGroups.add(groupId);
    }
    this.render();
  },

  toggleAllGroups() {
    const groupIds = this._getCurrentGroupIds();
    const btn = document.getElementById('btn-toggle-all-groups');
    const allCollapsed = groupIds.length > 0 && groupIds.every(id => this._collapsedGroups.has(id));
    if (allCollapsed) {
      this._collapsedGroups.clear();
      if (btn) btn.innerHTML = '▼ Collapse All';
    } else {
      groupIds.forEach(id => this._collapsedGroups.add(id));
      if (btn) btn.innerHTML = '▶ Expand All';
    }
    this.render();
  },

  _getCurrentGroupIds() {
    const targets = this.getFilteredTargets();
    const ids = [];
    if (this._groupBy === 'date') {
      const seen = new Set();
      targets.forEach(t => {
        const dt = t.date || 'Undated';
        if (!seen.has(dt)) { seen.add(dt); ids.push(`date-${dt}`); }
      });
    } else if (this._groupBy === 'ticker') {
      const seen = new Set();
      targets.forEach(t => {
        const sym = (t.ticker || '').toUpperCase();
        if (sym && !seen.has(sym)) { seen.add(sym); ids.push(`ticker-${sym}`); }
      });
    } else if (this._groupBy === 'status') {
      const seen = new Set();
      targets.forEach(t => {
        const st = (t.status || 'STALKING').toUpperCase();
        if (!seen.has(st)) { seen.add(st); ids.push(`status-${st}`); }
      });
    }
    return ids;
  },

  getFilteredTargets() {
    let list = this._allTargets || [];

    // 1. Status Filter Tab
    if (this._activeFilter === 'ACTIONABLE') {
      list = list.filter(t => {
        const st = (t.status || '').toUpperCase();
        // Exclude resolved, stopped, or runaway setups from Actionable/Near Zone
        if (['TARGET_HIT', 'COMPLETED', 'INVALIDATED', 'STOP_BREACHED', 'STOPPED', 'MISSED_RUNAWAY'].includes(st)) {
          return false;
        }
        const dist = t.distance_to_entry_pct;
        const isNear = dist !== null && dist !== undefined && Math.abs(dist) <= 1.5;
        return st === 'IN_ZONE' || st === 'IN_TRADE' || isNear || Boolean(t.options_actionable);
      });
    } else if (this._activeFilter === 'IN_TRADE') {
      list = list.filter(t => (t.status || '').toUpperCase() === 'IN_TRADE');
    } else if (this._activeFilter === 'TARGET_HIT') {
      list = list.filter(t => ['TARGET_HIT', 'COMPLETED'].includes((t.status || '').toUpperCase()));
    } else if (this._activeFilter !== 'ALL') {
      list = list.filter(t => (t.status || '').toUpperCase() === this._activeFilter);
    }

    // 2. Search Query
    if (this._searchQuery) {
      const q = this._searchQuery;
      list = list.filter(t => {
        const sym = (t.ticker || '').toLowerCase();
        const dt = (t.date || '').toLowerCase();
        const st = (t.status || '').toLowerCase();
        const opt = (t.options_summary || '').toLowerCase();
        const v = (t.verdict || '').toLowerCase();
        return sym.includes(q) || dt.includes(q) || st.includes(q) || opt.includes(q) || v.includes(q);
      });
    }

    // 3. Sorting
    list = [...list].sort((a, b) => {
      let valA = a[this._sortCol];
      let valB = b[this._sortCol];

      if (valA === null || valA === undefined) valA = this._sortAsc ? Infinity : -Infinity;
      if (valB === null || valB === undefined) valB = this._sortAsc ? Infinity : -Infinity;

      if (typeof valA === 'string') {
        return this._sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
      }
      return this._sortAsc ? valA - valB : valB - valA;
    });

    return list;
  },

  updateFilterCounts() {
    const all = this._allTargets || [];
    const countAll = all.length;
    const countActionable = all.filter(t => {
      const st = (t.status || '').toUpperCase();
      if (['TARGET_HIT', 'COMPLETED', 'INVALIDATED', 'STOP_BREACHED', 'STOPPED', 'MISSED_RUNAWAY'].includes(st)) {
        return false;
      }
      const dist = t.distance_to_entry_pct;
      const isNear = dist !== null && dist !== undefined && Math.abs(dist) <= 1.5;
      return st === 'IN_ZONE' || st === 'IN_TRADE' || isNear || Boolean(t.options_actionable);
    }).length;
    const countInZone = all.filter(t => (t.status || '').toUpperCase() === 'IN_ZONE').length;
    const countStalking = all.filter(t => (t.status || '').toUpperCase() === 'STALKING').length;
    const countInTrade = all.filter(t => (t.status || '').toUpperCase() === 'IN_TRADE').length;
    const countTargetHit = all.filter(t => ['TARGET_HIT', 'COMPLETED'].includes((t.status || '').toUpperCase())).length;
    const countInvalid = all.filter(t => ['INVALIDATED', 'STOP_BREACHED'].includes((t.status || '').toUpperCase())).length;

    const setBadge = (id, count) => {
      const el = document.getElementById(id);
      if (el) el.innerText = count;
    };

    setBadge('count-tab-all', countAll);
    setBadge('count-tab-actionable', countActionable);
    setBadge('count-tab-in-zone', countInZone);
    setBadge('count-tab-stalking', countStalking);
    setBadge('count-tab-in-trade', countInTrade);
    setBadge('count-tab-target-hit', countTargetHit);
    setBadge('count-tab-invalidated', countInvalid);
    setBadge('rf-count-intrade', countInTrade);

    const mainCount = document.getElementById('watchlist-total-count');
    if (mainCount) {
      mainCount.innerText = `${countAll} Targets`;
    }

    this.renderPerformanceStrip(all);

    const filtered = this.getFilteredTargets();
    const subBadge = document.getElementById('watchlist-pagination-badge');
    if (subBadge) {
      if (filtered.length === 0) {
        subBadge.innerText = '0 Targets';
      } else if (this._groupBy === 'none') {
        if (this._pageSize === 'all' || filtered.length <= this._pageSize) {
          subBadge.innerText = `Showing ${filtered.length} Targets`;
        } else {
          const startIdx = (this._currentPage - 1) * this._pageSize + 1;
          const endIdx = Math.min(this._currentPage * this._pageSize, filtered.length);
          subBadge.innerText = `Showing ${startIdx}–${endIdx} of ${filtered.length}`;
        }
      } else if (this._groupBy === 'date') {
        const uniqueDates = new Set(filtered.map(t => t.date || 'Undated')).size;
        subBadge.innerText = `${filtered.length} Targets in ${uniqueDates} Dates`;
      } else if (this._groupBy === 'ticker') {
        const uniqueTickers = new Set(filtered.map(t => (t.ticker || '').toUpperCase())).size;
        subBadge.innerText = `${filtered.length} Targets in ${uniqueTickers} Tickers`;
      } else if (this._groupBy === 'status') {
        const uniqueStatuses = new Set(filtered.map(t => (t.status || 'STALKING').toUpperCase())).size;
        subBadge.innerText = `${filtered.length} Targets in ${uniqueStatuses} Categories`;
      }
    }
  },

  renderPerformanceStrip(all) {
    const strip = document.getElementById('watchlist-performance-strip');
    if (!strip) return;

    let wonDollars = 0;
    let lostDollars = 0;
    let activeDollars = 0;
    let wonCount = 0;
    let lostCount = 0;
    let actCount = 0;

    all.forEach(t => {
      const st = (t.status || 'STALKING').toUpperCase();
      const dist = t.distance_to_entry_pct;
      const isNear = dist !== null && dist !== undefined && Math.abs(dist) <= 1.0;
      if (t.is_actionable || st === 'IN_ZONE' || st === 'IN_TRADE' || isNear || t.options_actionable) {
        actCount++;
      }

      const isFilled = Boolean(t.fill_price || t.was_filled || t.user_taken);
      const hasR = t.r_multiple !== undefined && t.r_multiple !== null;
      const rVal = hasR ? Number(t.r_multiple) : 0;

      if (st === 'TARGET_HIT' || st === 'COMPLETED') {
        if (hasR) {
          wonCount++;
          wonDollars += rVal;
        }
      } else if (st === 'STOP_BREACHED' || st === 'STOPPED' || (st === 'INVALIDATED' && isFilled)) {
        if (hasR) {
          lostCount++;
          lostDollars += rVal;
        }
      } else if (st === 'IN_TRADE' || st === 'IN_ZONE') {
        if (hasR) {
          activeDollars += rVal;
        }
      }
    });

    const totalResolved = wonCount + lostCount;
    const winRate = totalResolved > 0 ? ((wonCount / totalResolved) * 100).toFixed(1) : '0.0';
    const avgWin$ = wonCount > 0 ? (wonDollars / wonCount).toFixed(2) : '0.00';
    const avgLoss$ = lostCount > 0 ? (lostDollars / lostCount).toFixed(2) : '0.00';
    const netDollars = (wonDollars + lostDollars + activeDollars).toFixed(2);
    const netSign = Number(netDollars) >= 0 ? '+' : '';
    const netColor = Number(netDollars) >= 0 ? 'var(--emerald-light, #10b981)' : 'var(--rose-light, #f43f5e)';
    const profitFactor = Math.abs(lostDollars) > 0 ? (Math.abs(wonDollars) / Math.abs(lostDollars)).toFixed(2) : '99.9';

    strip.innerHTML = `
      <div class="perf-kpi-card win-rate" style="cursor:pointer;" onclick="AppWatchlist.setFilterTab('TARGET_HIT')" title="Click to view all winning trades">
        <span class="perf-kpi-title">🏆 Resolved Win Rate</span>
        <div class="perf-kpi-val" style="color:#10b981;">
          ${winRate}%
          <span class="perf-kpi-sub">(${wonCount} Won / ${lostCount} Stopped)</span>
        </div>
      </div>

      <div class="perf-kpi-card avg-win">
        <span class="perf-kpi-title">📈 Avg Win (Suggested Trades)</span>
        <div class="perf-kpi-val" style="color:#34d399;">
          +${Number(avgWin$).toFixed(2)}R
          <span class="perf-kpi-sub">${wonCount} Spreads Hit</span>
        </div>
      </div>

      <div class="perf-kpi-card avg-loss" style="cursor:pointer;" onclick="AppWatchlist.setFilterTab('INVALIDATED')" title="Click to view all stopped trades">
        <span class="perf-kpi-title">📉 Avg Loss (Defined Risk)</span>
        <div class="perf-kpi-val" style="color:#f87171;">
          -${Math.abs(Number(avgLoss$)).toFixed(2)}R
          <span class="perf-kpi-sub">${lostCount} Stopped</span>
        </div>
      </div>

      <div class="perf-kpi-card net-alpha">
        <span class="perf-kpi-title">💰 Net Profit (Suggested Trades)</span>
        <div class="perf-kpi-val" style="color:${netColor};">
          ${netSign}${Math.abs(Number(netDollars)).toFixed(2)}R
          <span class="perf-kpi-sub">(${profitFactor} Profit Factor)</span>
        </div>
      </div>

      <div class="perf-kpi-card actionable" style="cursor:pointer;" onclick="AppWatchlist.setFilterTab('ACTIONABLE')" title="Click to filter to immediately Actionable Now setups">
        <span class="perf-kpi-title">⚡ Actionable Now</span>
        <div class="perf-kpi-val" style="color:#f59e0b;">
          ${actCount} Setups
          <span class="perf-kpi-sub">In Zone / In Trade</span>
        </div>
      </div>
    `;
  },

  render() {
    this.updateFilterCounts();
    const container = document.getElementById('watchlist-content-container');
    if (!container) return;

    const targets = this.getFilteredTargets();

    if (targets.length === 0) {
      container.innerHTML = `
        <div style="text-align:center; padding:40px; color:var(--text-muted); font-size:13px;">
          No targets found matching the current filter criteria.
        </div>
      `;
      return;
    }

    if (this._viewMode === 'table') {
      this.renderTable(container, targets);
    } else {
      this.renderCards(container, targets);
    }
  },

  renderTableHeader() {
    const sortIndicator = (col) => {
      if (this._sortCol !== col) return '<span style="opacity:0.3; font-size:10px;"> ⇅</span>';
      return this._sortAsc ? ' ▲' : ' ▼';
    };

    return `
      <thead>
        <tr>
          <th onclick="AppWatchlist.setSort('ticker')">TICKER ${sortIndicator('ticker')}</th>
          <th onclick="AppWatchlist.setSort('date')">DATE ${sortIndicator('date')}</th>
          <th onclick="AppWatchlist.setSort('last_price')">LIVE SPOT ${sortIndicator('last_price')}</th>
          <th onclick="AppWatchlist.setSort('r_multiple')">SUGGESTED TRADE / R-MULTIPLE ${sortIndicator('r_multiple')}</th>
          <th>ENTRY ZONE</th>
          <th onclick="AppWatchlist.setSort('distance_to_entry_pct')">DIST % ${sortIndicator('distance_to_entry_pct')}</th>
          <th onclick="AppWatchlist.setSort('tactical_stop')">TACTICAL STOP ${sortIndicator('tactical_stop')}</th>
          <th>TARGET 1 / 2</th>
          <th onclick="AppWatchlist.setSort('status')">STATUS ${sortIndicator('status')}</th>
          <th>OPTIONS STRUCTURE</th>
          <th>ACTIONS</th>
        </tr>
      </thead>
    `;
  },

  renderTargetRow(t) {
    const sym = (t.ticker || '').toUpperCase();
    const spot = Number(t.last_price || 0);
    const stop = Number(t.tactical_stop || 0);
    const entryLow = Number(t.entry_zone_low || 0);
    const entryHigh = Number(t.entry_zone_high || 0);
    const entryMid = Number(t.entry_midpoint || ((entryLow + entryHigh) / 2) || spot);
    const t1 = Number(t.target_1 || 0);
    const t2 = Number(t.target_2 || 0);

    const isIdeasOpen = Boolean(this._activeTradeIdeasToggles && this._activeTradeIdeasToggles.has(sym));
    const isOpenPos = false;
    const posQty = 1;
    const posAvg = Number(t.entry_price || spot);

    const dist = t.distance_to_entry_pct;
    let distBadgeClass = 'far';
    let distText = 'N/A';
    if (dist !== null && dist !== undefined) {
      const absDist = Math.abs(dist);
      if (absDist <= 1.0) distBadgeClass = 'near';
      else if (absDist <= 3.0) distBadgeClass = 'moderate';
      else distBadgeClass = 'far';
      distText = `${dist >= 0 ? '+' : ''}${Number(dist).toFixed(2)}%`;
    }
    if ((t.status || '').toUpperCase() === 'INVALIDATED' || (t.status || '').toUpperCase() === 'STOP_BREACHED') {
      distBadgeClass = 'invalid';
    }

    const statusUpper = (t.status || 'STALKING').toUpperCase();
    let statusBadgeClass = 'stalking';
    let statusIcon = '⏳';
    if (statusUpper === 'IN_ZONE') { statusBadgeClass = 'in_zone'; statusIcon = '🎯'; }
    else if (statusUpper === 'IN_TRADE') { statusBadgeClass = 'in_trade'; statusIcon = '🎯'; }
    else if (statusUpper === 'TESTING_SUPPORT') { statusBadgeClass = 'testing_support'; statusIcon = '⚠️'; }
    else if (t.reclaimed) { statusBadgeClass = 'reclaimed'; statusIcon = '📈'; }
    else if (statusUpper === 'INVALIDATED' || statusUpper === 'STOP_BREACHED') { statusBadgeClass = 'invalidated'; statusIcon = '🛑'; }
    else if (statusUpper === 'TARGET_HIT' || statusUpper === 'COMPLETED') { statusBadgeClass = 'target_hit'; statusIcon = '🏁'; }
    else if (statusUpper === 'MISSED_RUNAWAY') { statusBadgeClass = 'missed_runaway'; statusIcon = '🚀'; }

    // Dynamic Outcome / Profit & Loss Badge by Suggested Trades
    let pnlHtml = '—';
    const rVal = (t.r_multiple !== undefined && t.r_multiple !== null) ? Number(t.r_multiple) : null;
    const tradeLabel = t.trade_label || (t.trade_type === 'OPTIONS' ? 'Options Spread' : 'Shares');

    if (statusUpper === 'TARGET_HIT' || statusUpper === 'COMPLETED') {
      if (rVal !== null) {
        const rStr = Math.abs(rVal).toFixed(2);
        pnlHtml = `
          <div style="display:flex; flex-direction:column; gap:2px;">
            <span class="pill" style="color:#10b981; background:rgba(16,185,129,0.18); border:1px solid #10b981; font-weight:800; font-family:'JetBrains Mono',monospace; font-size:11.5px;" title="Realized win on suggested trade ${tradeLabel}">
              +${rStr} R WIN 🏆
            </span>
            <span style="font-size:10px; color:var(--text-muted); font-weight:700;">
              ${tradeLabel}
            </span>
          </div>
        `;
      } else {
        pnlHtml = `
          <div style="display:flex; flex-direction:column; gap:2px;">
            <span class="pill" style="color:#10b981; background:rgba(16,185,129,0.18); border:1px solid #10b981; font-weight:800; font-family:'JetBrains Mono',monospace; font-size:11.5px;" title="Target reached on ${tradeLabel}">
              TARGET HIT 🏁
            </span>
            <span style="font-size:10px; color:var(--text-muted); font-weight:700;">
              ${tradeLabel}
            </span>
          </div>
        `;
      }
    } else if (statusUpper === 'INVALIDATED' || statusUpper === 'STOP_BREACHED' || statusUpper === 'STOPPED') {
      if (rVal !== null) {
        const isZeroR = (Math.abs(rVal) < 0.01);
        const lossStr = isZeroR ? '0.00 R UNFILLED' : `-${Math.abs(rVal).toFixed(2)} R STOP 🛑`;
        const pillColor = isZeroR ? '#94a3b8' : '#f43f5e';
        const pillBg = isZeroR ? 'rgba(148,163,184,0.18)' : 'rgba(244,63,94,0.18)';
        const pillBorder = isZeroR ? '#94a3b8' : '#f43f5e';
        pnlHtml = `
          <div style="display:flex; flex-direction:column; gap:2px;">
            <span class="pill" style="color:${pillColor}; background:${pillBg}; border:1px solid ${pillBorder}; font-weight:800; font-family:'JetBrains Mono',monospace; font-size:11.5px;" title="${isZeroR ? 'Invalidated before fill' : 'Defined stop loss'} on ${tradeLabel}">
              ${lossStr}
            </span>
            <span style="font-size:10px; color:var(--text-muted); font-weight:700;">
              ${tradeLabel}
            </span>
          </div>
        `;
      } else {
        pnlHtml = `
          <div style="display:flex; flex-direction:column; gap:2px;">
            <span class="pill" style="color:#94a3b8; background:rgba(148,163,184,0.18); border:1px solid #94a3b8; font-weight:800; font-family:'JetBrains Mono',monospace; font-size:11.5px;" title="Invalidated before fill on ${tradeLabel}">
              UNFILLED 🛑
            </span>
            <span style="font-size:10px; color:var(--text-muted); font-weight:700;">
              ${tradeLabel}
            </span>
          </div>
        `;
      }
    } else if ((statusUpper === 'IN_TRADE' || statusUpper === 'IN_ZONE') && spot > 0) {
      if (rVal !== null) {
        const pnlSign = rVal >= 0 ? '+' : '-';
        const pnlColor = rVal >= 0 ? '#10b981' : '#f43f5e';
        const pnlBg = rVal >= 0 ? 'rgba(16,185,129,0.14)' : 'rgba(244,63,94,0.14)';
        const pnlBorder = rVal >= 0 ? 'rgba(16,185,129,0.45)' : 'rgba(244,63,94,0.45)';
        const rStr = Math.abs(rVal).toFixed(2);
        pnlHtml = `
          <div style="display:flex; flex-direction:column; gap:2px;">
            <span class="pill" style="color:${pnlColor}; background:${pnlBg}; border:1px solid ${pnlBorder}; font-weight:800; font-family:'JetBrains Mono',monospace; font-size:11.5px;" title="Live theoretical value on suggested trade ${tradeLabel}">
              ${pnlSign}${rStr} R LIVE ⚡
            </span>
            <span style="font-size:10px; color:var(--text-muted); font-weight:700;">
              ${tradeLabel}
            </span>
          </div>
        `;
      } else {
        pnlHtml = `
          <div style="display:flex; flex-direction:column; gap:2px;">
            <span class="pill in_zone" style="font-weight:800; font-family:'JetBrains Mono',monospace; font-size:11.5px;">
              IN ZONE 🎯
            </span>
            <span style="font-size:10px; color:var(--text-muted); font-weight:700;">
              ${tradeLabel}
            </span>
          </div>
        `;
      }
    } else if (statusUpper === 'MISSED_RUNAWAY') {
      pnlHtml = `
        <div style="display:flex; flex-direction:column; gap:2px;">
          <span class="pill" style="color:#c084fc; background:rgba(168,85,247,0.18); border:1px solid #a855f7; font-weight:800; font-family:'JetBrains Mono',monospace; font-size:11px;" title="Price escaped before filling entry">
            MISSED RUNAWAY 🏃
          </span>
          <span style="font-size:10px; color:var(--text-muted); font-weight:600;">
            ${tradeLabel}
          </span>
        </div>
      `;
    } else {
      pnlHtml = `
        <div style="display:flex; flex-direction:column; gap:2px;">
          <span style="font-family:'JetBrains Mono',monospace; font-size:11.5px; font-weight:700; color:var(--cyan-glow);">
            ${rVal !== null ? `${rVal >= 0 ? '+' : ''}${rVal.toFixed(2)} R` : '—'}
          </span>
          <span style="font-size:10px; color:var(--text-muted); font-weight:600;">
            ${tradeLabel} ${t.rr_ratio ? `(${t.rr_ratio}:1 R:R)` : ''}
          </span>
        </div>
      `;
    }

    const optionsText = t.options_summary || 'No options plan defined';
    const isOptionsActionable = Boolean(t.options_actionable && optionsText !== 'No options plan defined' && !optionsText.includes('None'));
    const optionsBadgeHtml = isOptionsActionable
      ? `<span class="pill" style="font-size:9.5px; padding:1px 5px; background:rgba(16,185,129,0.2); border:1px solid rgba(16,185,129,0.5); color:var(--emerald-light); font-weight:700; margin-right:5px;">⚡ ACTIONABLE</span>`
      : '';

    const ideasCount = (t.trade_ideas || []).length;
    const ideasBtnBg = isIdeasOpen ? 'background:rgba(6,182,212,0.25); border:1px solid var(--cyan); color:#38bdf8;' : 'background:rgba(6,182,212,0.08); border:1px solid rgba(6,182,212,0.35); color:var(--cyan);';

    const drawerHtml = isIdeasOpen ? `
      <tr class="trade-ideas-row">
        <td colspan="11" style="padding:14px 18px; background:var(--bg-card); border-top:1px solid var(--border); border-bottom:1px solid var(--border);">
          <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; flex-wrap:wrap; gap:8px;">
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-size:14px;">⚡</span>
              <span style="font-family:'Outfit',sans-serif; font-size:13px; font-weight:800; color:var(--text-main); letter-spacing:0.2px;">Tactical Trade Ideas & Action Gameplan for ${sym}</span>
              ${isOpenPos ? `<span class="pill cyan" style="font-size:9.5px; padding:1px 6px;">💼 Active Position (${posQty} shs @ $${posAvg.toFixed(2)})</span>` : `<span class="pill green" style="font-size:9.5px; padding:1px 6px;">🎯 Stalking Setup</span>`}
            </div>
            <div style="display:flex; align-items:center; gap:8px;">
              <button class="btn secondary" onclick="AppSwing.openPlanExecution('${sym}', '${t.date}')" style="padding:3px 9px; font-size:10.5px; font-family:var(--font-mono); font-weight:700; border-radius:2px; color:var(--blue); cursor:pointer;">💬 Consult Copilot</button>
              <button class="btn secondary" onclick="AppWatchlist.toggleTradeIdeas('${sym}')" style="padding:3px 9px; font-size:10.5px; font-family:var(--font-mono); font-weight:700; border-radius:2px;">✕ Close</button>
            </div>
          </div>
          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:10px;">
            ${(t.trade_ideas || []).map(idea => `
              <div style="background:var(--bg-subtle); border:1px solid var(--border); border-left:3px solid var(--cyan); border-radius:3px; padding:10px 12px; display:flex; flex-direction:column; gap:5px;">
                <div style="display:flex; align-items:center; justify-content:space-between;">
                  <span style="font-family:var(--font-sans); font-weight:800; font-size:11.5px; color:var(--text-main);">${idea.title}</span>
                  <span class="pill ${idea.color || 'cyan'}" style="font-size:9.5px; padding:1px 5px; font-weight:800;">${idea.badge}</span>
                </div>
                <p style="font-size:11px; color:var(--text-muted); line-height:1.45; margin:0;">${idea.action}</p>
                ${idea.metrics ? `<div style="font-family:'JetBrains Mono',monospace; font-size:10.5px; color:var(--cyan); margin-top:2px; font-weight:700;">${idea.metrics}</div>` : ''}
              </div>
            `).join('')}
          </div>
        </td>
      </tr>
    ` : '';

    const compW = window.AppUtils ? AppUtils.getCompanyName(t.ticker) : t.ticker;
    const compTitle = (compW || t.ticker).replace(/"/g, '&quot;');

    return `
      <tr ${isOpenPos ? 'style="background:rgba(6,182,212,0.03);"' : ''}>
        <td>
          <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
            <span class="ticker-cell-sym" onclick="AppSwing.openReportModal('${t.date}', '${t.ticker}')" style="cursor:pointer; color:#38bdf8;" title="${compTitle} ($${t.ticker}) - Click to open Dossier" data-ticker="${t.ticker}">${t.ticker}</span>
            ${(t.side === 'SHORT' || (stop > 0 && entryHigh > 0 && stop > entryHigh)) ? `<span class="pill red" style="font-size:9.5px; padding:1px 5px; font-weight:800;">🔴 SHORT</span>` : ''}
            ${isOpenPos ? `<span class="pill cyan" style="font-size:9.5px; padding:1px 5px; font-weight:800;" title="You hold an active position in ${t.ticker}">💼 ${posQty} SHS</span>` : ''}
            ${t.conviction ? `<span class="pill" style="font-size:9.5px; padding:1px 5px;">${t.conviction}/10</span>` : ''}
            <button class="btn secondary" onclick="AppSwing.openReportModal('${t.date}', '${t.ticker}')" style="padding:2px 7px; font-size:10.5px; font-family:var(--font-mono); font-weight:700; background:rgba(6,182,212,0.1); border:1px solid rgba(6,182,212,0.4); color:var(--cyan); cursor:pointer; border-radius:2px; display:inline-flex; align-items:center; gap:4px;" title="Open ${t.ticker} Research Dossier">
              📑 Dossier
            </button>
            <button class="btn secondary" onclick="AppSwing.openTradingViewModal('${t.ticker}', 'D')" style="padding:2px 7px; font-size:10.5px; font-family:var(--font-mono); font-weight:700; background:var(--bg-subtle); border:1px solid var(--border); color:var(--text-main); cursor:pointer; border-radius:2px; display:inline-flex; align-items:center; gap:4px;" title="Open Real-Time Interactive TradingView Chart">
              📈 Chart
            </button>
          </div>
        </td>
        <td>
          <span class="pill" style="font-size:10.5px;">${t.date || 'N/A'}</span>
        </td>
        <td>
          <span style="font-family:'JetBrains Mono',monospace; font-weight:800; color:var(--cyan); font-size:13.5px;">
            $${spot > 0 ? spot.toFixed(2) : '--.--'}
          </span>
        </td>
        <td>
          ${pnlHtml}
        </td>
        <td>
          <span style="font-family:'JetBrains Mono',monospace; font-weight:600; color:var(--amber-light);">
            $${entryLow > 0 ? entryLow.toFixed(2) : '--'} - $${entryHigh > 0 ? entryHigh.toFixed(2) : '--'}
          </span>
        </td>
        <td>
          <span class="dist-pill ${distBadgeClass}">${distText}</span>
        </td>
        <td>
          <span style="font-family:'JetBrains Mono',monospace; font-weight:600; color:var(--rose-light);">
            $${stop > 0 ? stop.toFixed(2) : '--'}
          </span>
        </td>
        <td>
          <span style="font-family:'JetBrains Mono',monospace; font-size:11.5px;">
            <span style="color:var(--emerald-light); font-weight:700;">$${t1 > 0 ? t1.toFixed(2) : '--'}</span>
            <span style="color:var(--text-muted);"> / </span>
            <span style="color:var(--cyan); font-weight:700;">$${t2 > 0 ? t2.toFixed(2) : '--'}</span>
          </span>
        </td>
        <td>
          <span class="status-badge-lg ${statusBadgeClass}">${statusIcon} ${statusUpper}</span>
        </td>
        <td style="max-width:260px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${optionsText}">
          ${optionsBadgeHtml}<span style="font-size:11.5px; color:var(--text-muted); font-family:var(--font-mono);">${optionsText}</span>
        </td>
        <td>
          <div style="display:flex; align-items:center; gap:5px;">
            <button class="btn secondary" onclick="AppWatchlist.toggleTradeIdeas('${t.ticker}')" style="padding:2px 7px; font-size:10.5px; font-family:var(--font-mono); font-weight:700; border-radius:2px; ${ideasBtnBg}" title="View Actionable Trade Ideas for ${t.ticker}">
              💡 Ideas (${ideasCount})
            </button>
            <button class="btn secondary" onclick="AppWatchlist.deleteTarget('${t.ticker}', '${t.date}')" style="padding:2px 6px; font-size:10.5px; border-radius:2px; background:rgba(244,63,94,0.1); border:1px solid rgba(244,63,94,0.35); color:var(--rose-light);" title="Untrack ${t.ticker}">
              🗑️
            </button>
          </div>
        </td>
      </tr>
      ${drawerHtml}
    `;
  },

  renderTable(container, targets) {
    if (this._groupBy === 'date') {
      this.renderGroupedByDate(container, targets);
    } else if (this._groupBy === 'ticker') {
      this.renderGroupedByTicker(container, targets);
    } else if (this._groupBy === 'status') {
      this.renderGroupedByStatus(container, targets);
    } else {
      this.renderFlatTable(container, targets);
    }
  },

  renderFlatTable(container, targets) {
    const totalItems = targets.length;
    const pageSize = this._pageSize === 'all' ? totalItems : this._pageSize;
    const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
    if (this._currentPage > totalPages) this._currentPage = 1;

    const startIdx = (this._currentPage - 1) * pageSize;
    const pagedTargets = this._pageSize === 'all' ? targets : targets.slice(startIdx, startIdx + pageSize);

    const rowsHtml = pagedTargets.map(t => this.renderTargetRow(t)).join('');

    container.innerHTML = `
      <div class="watchlist-table-wrap">
        <table class="watchlist-table">
          ${this.renderTableHeader()}
          <tbody>
            ${rowsHtml}
          </tbody>
        </table>
      </div>
      ${this.renderPagination(totalItems, 'targets')}
    `;
  },

  renderGroupedByDate(container, targets) {
    const grouped = {};
    const dateOrder = [];
    targets.forEach(t => {
      const dt = t.date || 'Undated';
      if (!grouped[dt]) {
        grouped[dt] = [];
        dateOrder.push(dt);
      }
      grouped[dt].push(t);
    });

    dateOrder.sort((a, b) => b.localeCompare(a));

    const totalGroups = dateOrder.length;
    const pageSize = this._pageSize === 'all' ? totalGroups : this._pageSize;
    const totalPages = Math.max(1, Math.ceil(totalGroups / pageSize));
    if (this._currentPage > totalPages) this._currentPage = 1;

    const startIdx = (this._currentPage - 1) * pageSize;
    const pagedDates = this._pageSize === 'all' ? dateOrder : dateOrder.slice(startIdx, startIdx + pageSize);

    const groupsHtml = pagedDates.map(dateStr => {
      const groupTargets = grouped[dateStr];
      const inZoneCount = groupTargets.filter(t => (t.status || '').toUpperCase() === 'IN_ZONE').length;
      const inTradeCount = groupTargets.filter(t => (t.status || '').toUpperCase() === 'IN_TRADE').length;
      const stalkingCount = groupTargets.filter(t => (t.status || '').toUpperCase() === 'STALKING').length;
      const invalidCount = groupTargets.filter(t => (t.status || '').toUpperCase() === 'STOP_BREACHED' || ((t.status || '').toUpperCase() === 'INVALIDATED' && (t.r_multiple !== undefined && t.r_multiple !== null && Number(t.r_multiple) < 0))).length;
      const targetHitCount = groupTargets.filter(t => ['TARGET_HIT', 'COMPLETED'].includes((t.status || '').toUpperCase())).length;

      const dateNetR = groupTargets.reduce((acc, t) => {
        const val = t.r_multiple !== undefined && t.r_multiple !== null ? Number(t.r_multiple) : 0;
        return acc + val;
      }, 0);
      const datePnlSign = dateNetR >= 0 ? '+' : '-';
      const datePnlColor = dateNetR >= 0 ? '#10b981' : '#f43f5e';

      const groupId = `date-${dateStr}`;
      const isCollapsed = this._collapsedGroups.has(groupId);
      const rowsHtml = groupTargets.map(t => this.renderTargetRow(t)).join('');

      return `
        <div class="watch-group-section" id="group-date-${dateStr}">
          <div class="watch-group-header" onclick="AppWatchlist.toggleGroup('${groupId}')" title="Click to collapse or expand this date group">
            <div class="watch-group-title">
              <span class="watch-group-chevron">${isCollapsed ? '▶' : '▼'}</span>
              <span class="watch-group-icon">📅</span>
              <span class="watch-group-name">Research Date: <strong>${dateStr}</strong></span>
              <span class="pill cyan" style="font-size:10.5px; padding:2px 8px; font-weight:700;">${groupTargets.length} ${groupTargets.length === 1 ? 'Target' : 'Targets'}</span>
              ${dateNetR !== 0 ? `<span class="pill" style="font-size:10px; padding:2px 7px; font-weight:800; color:${datePnlColor}; background:rgba(${dateNetR >= 0 ? '16,185,129' : '244,63,94'},0.12); border:1px solid ${datePnlColor}; font-family:'JetBrains Mono',monospace;">${datePnlSign}${Math.abs(dateNetR).toFixed(2)}R</span>` : ''}
              ${targetHitCount > 0 ? `<span class="badge target_hit" style="font-size:10px; padding:2px 7px;">🏁 ${targetHitCount} Hit</span>` : ''}
              ${inZoneCount > 0 ? `<span class="badge in_zone" style="font-size:10px; padding:2px 7px;">🎯 ${inZoneCount} In Zone</span>` : ''}
              ${inTradeCount > 0 ? `<span class="badge in_trade" style="font-size:10px; padding:2px 7px;">💼 ${inTradeCount} In Trade</span>` : ''}
              ${stalkingCount > 0 ? `<span class="badge stalking" style="font-size:10px; padding:2px 7px;">⏳ ${stalkingCount} Stalking</span>` : ''}
              ${invalidCount > 0 ? `<span class="badge invalidated" style="font-size:10px; padding:2px 7px;">⚠️ ${invalidCount} Invalid</span>` : ''}
            </div>
            <div class="watch-group-actions">
              <span style="font-size:11px; color:var(--text-muted); font-weight:600;">
                ${isCollapsed ? '▶ Expand' : '▼ Collapse'}
              </span>
            </div>
          </div>
          ${!isCollapsed ? `
            <div class="watchlist-table-wrap">
              <table class="watchlist-table">
                ${this.renderTableHeader()}
                <tbody>
                  ${rowsHtml}
                </tbody>
              </table>
            </div>
          ` : ''}
        </div>
      `;
    }).join('');

    container.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:12px;">
        ${groupsHtml}
      </div>
      ${this.renderPagination(totalGroups, 'date groups')}
    `;
  },

  renderGroupedByTicker(container, targets) {
    const grouped = {};
    const tickerOrder = [];
    targets.forEach(t => {
      const sym = (t.ticker || '').toUpperCase();
      if (!grouped[sym]) {
        grouped[sym] = [];
        tickerOrder.push(sym);
      }
      grouped[sym].push(t);
    });

    tickerOrder.sort((a, b) => a.localeCompare(b));

    const totalGroups = tickerOrder.length;
    const pageSize = this._pageSize === 'all' ? totalGroups : this._pageSize;
    const totalPages = Math.max(1, Math.ceil(totalGroups / pageSize));
    if (this._currentPage > totalPages) this._currentPage = 1;

    const startIdx = (this._currentPage - 1) * pageSize;
    const pagedTickers = this._pageSize === 'all' ? tickerOrder : tickerOrder.slice(startIdx, startIdx + pageSize);

    const groupsHtml = pagedTickers.map(sym => {
      const groupTargets = grouped[sym];
      const primary = groupTargets[0];
      const spot = Number(primary.last_price || 0);
      const isShort = (primary.side === 'SHORT' || (primary.tactical_stop > 0 && primary.entry_zone_high > 0 && primary.tactical_stop > primary.entry_zone_high));

      const compName = window.AppUtils ? AppUtils.getCompanyName(sym) : sym;
      const compTitle = (compName || sym).replace(/"/g, '&quot;');

      const dist = primary.distance_to_entry_pct;
      let distBadgeClass = 'far';
      let distText = 'N/A';
      if (dist !== null && dist !== undefined) {
        const absDist = Math.abs(dist);
        if (absDist <= 1.0) distBadgeClass = 'near';
        else if (absDist <= 3.0) distBadgeClass = 'moderate';
        else distBadgeClass = 'far';
        distText = `${dist >= 0 ? '+' : ''}${Number(dist).toFixed(2)}%`;
      }

      const statusUpper = (primary.status || 'STALKING').toUpperCase();
      let statusBadgeClass = 'stalking';
      let statusIcon = '⏳';
      if (statusUpper === 'IN_ZONE') { statusBadgeClass = 'in_zone'; statusIcon = '🎯'; }
      else if (statusUpper === 'IN_TRADE') { statusBadgeClass = 'in_trade'; statusIcon = '🎯'; }
      else if (statusUpper === 'INVALIDATED' || statusUpper === 'STOP_BREACHED') { statusBadgeClass = 'invalidated'; statusIcon = '⚠️'; }
      else if (statusUpper === 'TARGET_HIT' || statusUpper === 'COMPLETED') { statusBadgeClass = 'target_hit'; statusIcon = '🏁'; }
      else if (statusUpper === 'MISSED_RUNAWAY') { statusBadgeClass = 'missed_runaway'; statusIcon = '🏃'; }

      let tickerPnlBadge = '';
      const pR = primary.r_multiple !== undefined && primary.r_multiple !== null ? Number(primary.r_multiple) : 0;
      if (pR !== 0) {
        const rSign = pR >= 0 ? '+' : '-';
        const rColor = pR >= 0 ? '#10b981' : '#f43f5e';
        tickerPnlBadge = `<span class="pill" style="font-size:10px; padding:2px 7px; font-weight:800; color:${rColor}; background:rgba(${pR >= 0 ? '16,185,129' : '244,63,94'},0.12); border:1px solid ${rColor}; font-family:'JetBrains Mono',monospace;">${rSign}${Math.abs(pR).toFixed(2)}R (${primary.trade_label || ''})</span>`;
      }

      const ideasCount = (primary.trade_ideas || []).length;
      const isIdeasOpen = Boolean(this._activeTradeIdeasToggles && this._activeTradeIdeasToggles.has(sym));
      const ideasBtnBg = isIdeasOpen ? 'background:rgba(6,182,212,0.3); border-color:var(--cyan-glow);' : 'background:rgba(6,182,212,0.12); border-color:rgba(6,182,212,0.4);';

      const groupId = `ticker-${sym}`;
      const isCollapsed = this._collapsedGroups.has(groupId);
      const rowsHtml = groupTargets.map(t => this.renderTargetRow(t)).join('');

      return `
        <div class="watch-group-section" id="group-ticker-${sym}">
          <div class="watch-group-header" onclick="AppWatchlist.toggleGroup('${groupId}')" title="Click to collapse or expand ${sym}">
            <div class="watch-group-title">
              <span class="watch-group-chevron">${isCollapsed ? '▶' : '▼'}</span>
              <span class="watch-group-icon">🏷️</span>
              <span class="watch-group-name" style="color:#38bdf8; font-size:15px; letter-spacing:0.5px;">${sym}</span>
              <span style="font-size:12px; color:var(--text-muted); font-weight:600; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${compTitle}">${compName}</span>
              ${isShort ? `<span class="pill" style="font-size:9px; padding:1px 5px; font-weight:800; background:rgba(239,68,68,0.2); border:1px solid rgba(239,68,68,0.5); color:#f87171;">🔴 SHORT</span>` : ''}
              ${spot > 0 ? `<span style="font-family:'JetBrains Mono',monospace; font-weight:800; color:var(--cyan-glow); font-size:13.5px; margin-left:4px;">$${spot.toFixed(2)}</span>` : ''}
              ${tickerPnlBadge}
              <span class="dist-pill ${distBadgeClass}" style="font-size:10px; padding:2px 6px;">${distText}</span>
              <span class="status-badge-lg ${statusBadgeClass}" style="font-size:10.5px; padding:2px 7px;">${statusIcon} ${statusUpper}</span>
              ${primary.conviction ? `<span class="pill" style="font-size:9.5px; padding:1px 5px;">${primary.conviction}/10</span>` : ''}
            </div>
            <div class="watch-group-actions" onclick="event.stopPropagation();">
              <button class="btn secondary" onclick="AppSwing.openReportModal('${primary.date}', '${sym}')" style="padding:2px 8px; font-size:11px; font-weight:700; color:var(--cyan-glow); background:rgba(6,182,212,0.12); border-color:rgba(6,182,212,0.4); cursor:pointer;" title="Open ${sym} Research Dossier">
                📑 Dossier
              </button>
              <button class="btn secondary" onclick="AppSwing.openTradingViewModal('${sym}', 'D')" style="padding:2px 7px; font-size:11px; font-weight:700; cursor:pointer;" title="Open Interactive Chart">
                📈 Chart
              </button>
              <button class="btn secondary" onclick="AppWatchlist.toggleTradeIdeas('${sym}')" style="padding:2px 8px; font-size:11px; font-weight:700; color:var(--cyan-glow); ${ideasBtnBg} cursor:pointer;" title="Trade Ideas">
                💡 Ideas (${ideasCount})
              </button>
              <span class="pill cyan" style="font-size:10px; padding:2px 6px; font-weight:700;">${groupTargets.length} ${groupTargets.length === 1 ? 'Record' : 'Records'}</span>
            </div>
          </div>
          ${!isCollapsed ? `
            <div class="watchlist-table-wrap">
              <table class="watchlist-table">
                ${this.renderTableHeader()}
                <tbody>
                  ${rowsHtml}
                </tbody>
              </table>
            </div>
          ` : ''}
        </div>
      `;
    }).join('');

    container.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:12px;">
        ${groupsHtml}
      </div>
      ${this.renderPagination(totalGroups, 'tickers')}
    `;
  },

  renderGroupedByStatus(container, targets) {
    const statusDefs = [
      { key: 'IN_ZONE', name: 'IN ZONE — Entry Trigger Active', icon: '🎯', badgeClass: 'in_zone', borderColor: '#10b981' },
      { key: 'IN_TRADE', name: 'IN TRADE — Active Portfolio Positions', icon: '💼', badgeClass: 'in_trade', borderColor: '#06b6d4' },
      { key: 'STALKING', name: 'STALKING — Monitoring Price Action', icon: '⏳', badgeClass: 'stalking', borderColor: '#f59e0b' },
      { key: 'TARGET_HIT', name: 'TARGET HIT — Tactical Targets Reached', icon: '🏁', badgeClass: 'target_hit', borderColor: '#3b82f6' },
      { key: 'MISSED_RUNAWAY', name: 'MISSED RUNAWAY — Price Escaped Entry Zone', icon: '🏃', badgeClass: 'missed_runaway', borderColor: '#a855f7' },
      { key: 'INVALIDATED', name: 'INVALIDATED — Stop Breached / Thesis Void', icon: '⚠️', badgeClass: 'invalidated', borderColor: '#ef4444' }
    ];

    const grouped = {};
    targets.forEach(t => {
      const st = (t.status || 'STALKING').toUpperCase();
      if (!grouped[st]) grouped[st] = [];
      grouped[st].push(t);
    });

    const activeStatuses = statusDefs.filter(def => grouped[def.key] && grouped[def.key].length > 0);
    Object.keys(grouped).forEach(k => {
      if (!statusDefs.find(d => d.key === k)) {
        activeStatuses.push({ key: k, name: k, icon: '📌', badgeClass: 'stalking', borderColor: '#64748b' });
      }
    });

    const groupsHtml = activeStatuses.map(def => {
      const groupTargets = grouped[def.key];
      const groupId = `status-${def.key}`;
      const isCollapsed = this._collapsedGroups.has(groupId);
      const rowsHtml = groupTargets.map(t => this.renderTargetRow(t)).join('');

      return `
        <div class="watch-group-section" id="group-status-${def.key}">
          <div class="watch-group-header" onclick="AppWatchlist.toggleGroup('${groupId}')" style="border-left-color:${def.borderColor};" title="Click to collapse or expand ${def.name}">
            <div class="watch-group-title">
              <span class="watch-group-chevron">${isCollapsed ? '▶' : '▼'}</span>
              <span class="watch-group-icon">${def.icon}</span>
              <span class="watch-group-name">${def.name}</span>
              <span class="badge ${def.badgeClass}" style="font-size:10.5px; padding:2px 8px; font-weight:800;">
                ${groupTargets.length} ${groupTargets.length === 1 ? 'Target' : 'Targets'}
              </span>
            </div>
            <div class="watch-group-actions">
              <span style="font-size:11px; color:var(--text-muted); font-weight:600;">
                ${isCollapsed ? '▶ Expand' : '▼ Collapse'}
              </span>
            </div>
          </div>
          ${!isCollapsed ? `
            <div class="watchlist-table-wrap">
              <table class="watchlist-table">
                ${this.renderTableHeader()}
                <tbody>
                  ${rowsHtml}
                </tbody>
              </table>
            </div>
          ` : ''}
        </div>
      `;
    }).join('');

    container.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:12px;">
        ${groupsHtml}
      </div>
      <div class="watchlist-pagination-bar">
        <div style="font-size:12px; color:var(--text-muted); font-weight:600;">
          Showing all <strong>${activeStatuses.length}</strong> status groups (<strong>${targets.length}</strong> total targets)
        </div>
        <div class="pagination-nav">
          <span class="pill cyan" style="font-size:11px; padding:3px 9px;">All Categories Active</span>
        </div>
      </div>
    `;
  },

  renderPagination(totalCount, unitLabel = 'targets') {
    if (totalCount <= 0) return '';
    const pageSize = this._pageSize === 'all' ? totalCount : this._pageSize;
    const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
    const currentPage = Math.min(this._currentPage, totalPages);

    if (this._pageSize === 'all' || totalPages <= 1) {
      return `
        <div class="watchlist-pagination-bar">
          <div style="font-size:12px; color:var(--text-muted); font-weight:600;">
            Showing all <strong>${totalCount}</strong> ${unitLabel}
          </div>
          <div class="pagination-nav">
            <span class="pill cyan" style="font-size:11px; padding:3px 9px;">Page 1 of 1</span>
          </div>
        </div>
      `;
    }

    const startIdx = (currentPage - 1) * pageSize + 1;
    const endIdx = Math.min(currentPage * pageSize, totalCount);

    let pages = [];
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pages.push(i);
    } else {
      pages.push(1);
      if (currentPage > 3) pages.push('...');
      const start = Math.max(2, currentPage - 1);
      const end = Math.min(totalPages - 1, currentPage + 1);
      for (let i = start; i <= end; i++) pages.push(i);
      if (currentPage < totalPages - 2) pages.push('...');
      pages.push(totalPages);
    }

    const pageButtonsHtml = pages.map(p => {
      if (p === '...') return `<span style="padding:0 4px; color:var(--text-muted); font-size:12px;">...</span>`;
      const isActive = p === currentPage;
      return `
        <button class="btn secondary pagination-btn ${isActive ? 'active' : ''}" 
                onclick="AppWatchlist.setPage(${p})" 
                ${isActive ? 'disabled' : ''}>
          ${p}
        </button>
      `;
    }).join('');

    return `
      <div class="watchlist-pagination-bar">
        <div style="font-size:12px; color:var(--text-muted); font-weight:600;">
          Showing <strong>${startIdx}–${endIdx}</strong> of <strong>${totalCount}</strong> ${unitLabel}
        </div>
        <div class="pagination-nav">
          <button class="btn secondary pagination-btn" onclick="AppWatchlist.setPage(1)" ${currentPage === 1 ? 'disabled' : ''} title="First Page">⏮</button>
          <button class="btn secondary pagination-btn" onclick="AppWatchlist.setPage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''} title="Previous Page">◀ Prev</button>
          ${pageButtonsHtml}
          <button class="btn secondary pagination-btn" onclick="AppWatchlist.setPage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''} title="Next Page">Next ▶</button>
          <button class="btn secondary pagination-btn" onclick="AppWatchlist.setPage(${totalPages})" ${currentPage === totalPages ? 'disabled' : ''} title="Last Page">⏭</button>
        </div>
      </div>
    `;
  },

  renderCards(container, targets) {
    // Render visual cards grouped by date
    const grouped = {};
    targets.forEach(t => {
      const dt = t.date || 'Undated';
      if (!grouped[dt]) grouped[dt] = [];
      grouped[dt].push(t);
    });

    const sortedDates = Object.keys(grouped).sort((a, b) => b.localeCompare(a));

    container.innerHTML = sortedDates.map(dateStr => {
      const groupTargets = grouped[dateStr];
      const inZoneCount = groupTargets.filter(t => t.status === 'IN_ZONE').length;
      const stalkingCount = groupTargets.filter(t => t.status !== 'IN_ZONE').length;

      const cardsHtml = groupTargets.map(t => {
        const statusClass = t.status === 'IN_ZONE' ? 'in_zone' : 'stalking';
        const distSign = (t.distance_to_entry_pct >= 0 ? '+' : '');
        const distDisplay = t.distance_to_entry_pct !== null && t.distance_to_entry_pct !== undefined
          ? `${distSign}${Number(t.distance_to_entry_pct).toFixed(2)}%`
          : 'N/A';
        const optionsText = t.options_summary || 'No options structure defined';

        const spot = Number(t.last_price || 0);
        const stop = Number(t.tactical_stop || (spot * 0.95));
        const entry = Number(t.entry_zone_high || spot);
        const t1 = Number(t.target_1 || (spot * 1.05));
        const t2 = Number(t.target_2 || (spot * 1.10));

        const compR = window.AppUtils ? AppUtils.getCompanyName(t.ticker) : t.ticker;
        const compTitle = (compR || t.ticker).replace(/"/g, '&quot;');

        return `
          <div class="target-card">
            <div class="target-header">
              <div class="target-sym">
                <span class="radar-ticker-sym" title="${compTitle} ($${t.ticker})" data-ticker="${t.ticker}" style="cursor:help;">${t.ticker}</span>
                <span class="spot-badge">$${spot.toFixed(2)}</span>
                <span class="badge ${statusClass}">${t.status} (${distDisplay})</span>
              </div>
              <button class="btn secondary" onclick="AppSwing.openReportModal('${t.date}', '${t.ticker}')" style="padding:5px 12px; font-size:12px;">📑 Read Dossier</button>
            </div>
            
            <div class="price-ladder-box">
              <div class="price-ladder-labels">
                <span style="color:var(--rose-light);">Stop $${stop.toFixed(2)}</span>
                <span style="color:var(--amber-light);">Entry $${entry.toFixed(2)}</span>
                <span style="color:var(--cyan-glow); font-weight:700;">Spot $${spot.toFixed(2)}</span>
                <span style="color:var(--emerald-light);">T1 $${t1.toFixed(2)}</span>
                <span style="color:var(--violet-light);">T2 $${t2.toFixed(2)}</span>
              </div>
              <div class="price-ladder-track">
                <div class="price-ladder-progress" style="width: 100%;"></div>
              </div>
            </div>

            <div class="levels-row">
              <div class="level-col"><label>Entry Zone</label><span>$${Number(t.entry_zone_low || 0).toFixed(2)} - $${Number(t.entry_zone_high || 0).toFixed(2)}</span></div>
              <div class="level-col"><label>Tactical Stop</label><span style="color:var(--rose-light);">$${stop.toFixed(2)}</span></div>
              <div class="level-col"><label>Target 1</label><span style="color:var(--emerald-light);">$${t1.toFixed(2)}</span></div>
              <div class="level-col"><label>Target 2</label><span style="color:var(--cyan-glow);">$${t2.toFixed(2)}</span></div>
            </div>
            <div class="options-snippet">
              <strong>Options Plan:</strong> ${optionsText}
            </div>
          </div>
        `;
      }).join('');

      return `
        <div class="date-accordion-card">
          <div class="date-accordion-head" style="cursor:default;">
            <div class="date-accordion-title">
              <span>📅 Research Date: <strong>${dateStr}</strong></span>
            </div>
            <div style="display:flex; align-items:center; gap:8px;">
              ${inZoneCount > 0 ? `<span class="badge in_zone" style="font-size:11px;">🎯 ${inZoneCount} In Zone</span>` : ''}
              ${stalkingCount > 0 ? `<span class="badge stalking" style="font-size:11px;">⏳ ${stalkingCount} Stalking</span>` : ''}
              <span class="pill cyan" style="font-size:11px; padding:3px 8px;">${groupTargets.length} Targets</span>
            </div>
          </div>
          <div class="date-accordion-body" style="display:grid;">
            ${cardsHtml}
          </div>
        </div>
      `;
    }).join('');
  },

  _radarSort: 'research_time',
  _radarFilter: 'ALL',

  setRadarSort(sortKey) {
    this._radarSort = sortKey || 'research_time';
    this.renderRadarWidget();
  },

  setRadarFilter(filterKey) {
    this._radarFilter = filterKey || 'ALL';
    document.querySelectorAll('[data-radar-filter]').forEach(b => {
      b.classList.toggle('active', b.getAttribute('data-radar-filter') === this._radarFilter);
    });
    this.renderRadarWidget();
  },

  filterRadar(query) {
    this._radarSearchQuery = (query || '').trim().toUpperCase();
    this.renderRadarWidget();
  },

  _formatResearchTime(tsStr, dateFallback) {
    if (!tsStr) return dateFallback ? `📅 ${dateFallback}` : '';
    try {
      const d = new Date(tsStr);
      if (isNaN(d.getTime())) return `📅 ${dateFallback || tsStr}`;
      const now = new Date();
      const isToday = d.toDateString() === now.toDateString();
      const timePart = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      if (isToday) {
        return `⏱️ Today ${timePart}`;
      }
      const datePart = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
      return `⏱️ ${datePart}, ${timePart}`;
    } catch (e) {
      return `📅 ${dateFallback || tsStr}`;
    }
  },

  renderRadarWidget() {
    const radarEl = document.getElementById('swing-radar-widget');
    if (!radarEl) return;

    const allTargets = this._allTargets || [];
    if (allTargets.length === 0) {
      radarEl.innerHTML = `
        <div style="color:var(--text-muted); font-size:13px; text-align:center; padding:24px;">
          No active research targets in database. Launch research or scan queue above to populate targets.
        </div>
      `;
      return;
    }

    const zoneCount = allTargets.filter(t => (t.status || '').toUpperCase() === 'IN_ZONE').length;
    const intradeCount = allTargets.filter(t => (t.status || '').toUpperCase() === 'IN_TRADE').length;
    const stalkCount = allTargets.filter(t => (t.status || '').toUpperCase() === 'STALKING').length;
    const targetCount = allTargets.filter(t => (t.status || '').toUpperCase().includes('TARGET')).length;
    const runawayCount = allTargets.filter(t => (t.status || '').toUpperCase() === 'MISSED_RUNAWAY').length;
    const invalidCount = allTargets.filter(t => (t.status || '').toUpperCase() === 'INVALIDATED' || (t.status || '').toUpperCase() === 'STOP_BREACHED').length;

    const setBadge = (id, count) => {
      const el = document.getElementById(id);
      if (el) el.innerText = String(count);
    };
    setBadge('rf-count-all', allTargets.length);
    setBadge('rf-count-intrade', intradeCount);
    setBadge('rf-count-zone', zoneCount);
    setBadge('rf-count-stalk', stalkCount);
    setBadge('rf-count-target', targetCount);
    setBadge('rf-count-runaway', runawayCount);
    setBadge('rf-count-invalid', invalidCount);

    // Apply Filter Tab
    let targets = allTargets;
    if (this._radarFilter === 'IN_TRADE') {
      targets = targets.filter(t => (t.status || '').toUpperCase() === 'IN_TRADE');
    } else if (this._radarFilter === 'IN_ZONE') {
      targets = targets.filter(t => (t.status || '').toUpperCase() === 'IN_ZONE');
    } else if (this._radarFilter === 'STALKING') {
      targets = targets.filter(t => (t.status || '').toUpperCase() === 'STALKING');
    } else if (this._radarFilter === 'TARGET_HIT') {
      targets = targets.filter(t => (t.status || '').toUpperCase().includes('TARGET'));
    } else if (this._radarFilter === 'MISSED_RUNAWAY') {
      targets = targets.filter(t => (t.status || '').toUpperCase() === 'MISSED_RUNAWAY');
    } else if (this._radarFilter === 'INVALIDATED') {
      targets = targets.filter(t => (t.status || '').toUpperCase() === 'INVALIDATED' || (t.status || '').toUpperCase() === 'STOP_BREACHED');
    }

    // Apply search query filter if entered
    if (this._radarSearchQuery) {
      const q = this._radarSearchQuery;
      targets = targets.filter(t =>
        (t.ticker || '').toUpperCase().includes(q) ||
        (t.status || '').toUpperCase().includes(q) ||
        (t.date || '').includes(q) ||
        (t.options_summary || '').toUpperCase().includes(q) ||
        (t.options_structure || '').toUpperCase().includes(q) ||
        (t.verdict || '').toUpperCase().includes(q)
      );
    }

    if (targets.length === 0) {
      radarEl.innerHTML = `
        <div style="color:var(--text-muted); font-size:12.5px; text-align:center; padding:24px; background:var(--bg-subtle); border-radius:8px; border:1px dashed var(--border-subtle);">
          🔍 No targets matching active filter (<strong>${this._radarFilter}</strong>) or search "<strong>${this._radarSearchQuery || ''}</strong>".
        </div>
      `;
      return;
    }

    // Sort targets according to _radarSort
    const sorted = [...targets].sort((a, b) => {
      const sortMode = this._radarSort || 'research_time';
      if (sortMode === 'research_time') {
        const timeA = a.research_timestamp || a.updated_at || a.date || '';
        const timeB = b.research_timestamp || b.updated_at || b.date || '';
        return timeB.localeCompare(timeA); // Newest research time first
      } else if (sortMode === 'status') {
        const order = { 'IN_TRADE': 1, 'IN_ZONE': 2, 'TARGET_HIT': 3, 'TARGET_1_REACHED': 3, 'STALKING': 4, 'MISSED_RUNAWAY': 5, 'INVALIDATED': 6 };
        const rankA = order[(a.status || '').toUpperCase()] || 99;
        const rankB = order[(b.status || '').toUpperCase()] || 99;
        if (rankA !== rankB) return rankA - rankB;
        const timeA = a.research_timestamp || '';
        const timeB = b.research_timestamp || '';
        return timeB.localeCompare(timeA);
      } else if (sortMode === 'distance') {
        const distA = a.distance_to_entry_pct !== null && a.distance_to_entry_pct !== undefined ? Math.abs(a.distance_to_entry_pct) : 999;
        const distB = b.distance_to_entry_pct !== null && b.distance_to_entry_pct !== undefined ? Math.abs(b.distance_to_entry_pct) : 999;
        return distA - distB;
      } else if (sortMode === 'ticker') {
        return (a.ticker || '').localeCompare(b.ticker || '');
      } else {
        const dateA = a.date || '';
        const dateB = b.date || '';
        return dateB.localeCompare(dateA);
      }
    });

    const topTargets = sorted.slice(0, 10);

    const cardsHtml = topTargets.map(t => {
      const sym = (t.ticker || '').toUpperCase();
      const spot = Number(t.last_price || 0);
      const spotStr = spot > 0 ? `$${spot.toFixed(2)}` : '--.--';
      const timeBadge = this._formatResearchTime(t.research_timestamp, t.date);

      const dist = t.distance_to_entry_pct;
      const distStr = (dist !== null && dist !== undefined) ? `${dist >= 0 ? '+' : ''}${Number(dist).toFixed(2)}%` : '';
      const st = (t.status || '').toUpperCase();
      const chg = (t.net_change !== null && t.net_change !== undefined) ? Number(t.net_change) : null;
      const chgPct = (t.net_percent_change !== null && t.net_percent_change !== undefined) ? Number(t.net_percent_change) : null;
      const chgColor = (chg !== null && chg >= 0) ? '#059669' : '#dc2626';
      const chgStr = (chg !== null && chgPct !== null && spot > 0)
        ? `<span style="font-family:'JetBrains Mono',monospace; font-size:11px; color:${chgColor}; font-weight:700;">${chg >= 0 ? '+' : ''}${chg.toFixed(2)} (${chgPct >= 0 ? '+' : ''}${chgPct.toFixed(2)}%)</span>`
        : '';
      const schwabPill = (t.quote_source === 'SCHWAB')
        ? `<span class="pill green" style="font-size:8.5px; padding:1px 4px; font-weight:800; background:rgba(5,150,105,0.15); border:1px solid rgba(5,150,105,0.4); color:#059669;" title="Live Quote from Schwab">SCHWAB LIVE</span>`
        : '';

      const ideas = t.trade_ideas || [];
      const isIdeasOpen = Boolean(this._activeTradeIdeasToggles && this._activeTradeIdeasToggles.has(sym));
      const isOpen = Boolean(this._activeOpenCharts && this._activeOpenCharts.has(sym));
      const isSnap = Boolean(this._activeSnapshotToggles && this._activeSnapshotToggles.has(sym));
      const interval = this._inlineTvIntervals[sym] || 'D';
      const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      const tvSrc = isOpen
        ? `https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(sym)}&interval=${encodeURIComponent(interval)}&hidesidetoolbar=0&symboledit=1&saveimage=1&toolbarbg=f1f3f6&studies=%5B%5D&theme=${theme}&style=1&timezone=America%2FNew_York`
        : 'about:blank';

      // Check invalidation and actual stop status
      const invPrice = Number(t.invalidation_price || t.tactical_stop || 0);
      const isShort = (t.side || '').toUpperCase() === 'SHORT';
      const isActuallyStopped = invPrice > 0 && spot > 0 && (
        isShort ? (spot >= invPrice) : (spot <= invPrice)
      );
      const isTestingSupport = st === 'TESTING_SUPPORT';
      const isReclaimed = Boolean(t.reclaimed) || (!isActuallyStopped && (st === 'INVALIDATED' || st === 'STOP_BREACHED') && spot > 0);

      // Status & Hit Outcome Badge
      let statusBadge = '';
      if (st === 'IN_ZONE') {
        statusBadge = `<span class="badge in_zone">🎯 IN ENTRY ZONE (${distStr || 'Active'})</span>`;
      } else if (st === 'IN_TRADE') {
        statusBadge = `<span class="badge in_trade" style="background:#e0e7ff; color:#3730a3; border:1px solid #c7d2fe; font-weight:800;">🎯 IN TRADE</span>`;
      } else if (st.includes('TARGET') || st === 'TARGET_HIT') {
        const tVal = t.target_1 ? `$${Number(t.target_1).toFixed(2)}` : '';
        statusBadge = `<span class="badge target_hit">🏁 TARGET 1 HIT ${tVal}</span>`;
      } else if (st === 'MISSED_RUNAWAY') {
        const runawaySuffix = distStr ? ` (${distStr})` : ' (Past Target)';
        statusBadge = `<span class="badge runaway">🚀 MISSED RUNAWAY${runawaySuffix}</span>`;
      } else if (isTestingSupport) {
        statusBadge = `<span class="badge" style="background:#fef3c7; color:#92400e; border:1px solid #fde68a; font-weight:800;">⚠️ TESTING SUPPORT</span>`;
      } else if (isReclaimed) {
        statusBadge = `<span class="badge" style="background:#dcfce7; color:#15803d; border:1px solid #86efac; font-weight:800;">📈 RECLAIMED SUPPORT</span>`;
      } else if ((st === 'INVALIDATED' || st === 'STOP_BREACHED') && isActuallyStopped) {
        statusBadge = `<span class="badge invalid">🛑 STOP BREACHED</span>`;
      } else {
        statusBadge = `<span class="badge stalking">⏳ STALKING (${distStr ? distStr + ' to zone' : 'Active'})</span>`;
      }

      // Action Directive Banner (What should I take action on)
      let actionHtml = '';
      if (st === 'IN_ZONE') {
        const optNote = t.options_actionable ? `Execute ${t.options_structure || 'spread'} at market, or set limit buy in zone.` : `Confirm limit order fill in $${Number(t.entry_zone_low || 0).toFixed(2)}-$${Number(t.entry_zone_high || 0).toFixed(2)} zone.`;
        actionHtml = `
          <div style="background:#ecfdf5; border:1px solid #a7f3d0; border-radius:6px; padding:7px 12px; margin-top:8px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">
            <span style="color:#065f46; font-weight:700; font-size:12px;">🔥 TAKE ACTION: Spot is in buy zone! ${optNote}</span>
            <button class="btn" onclick="AppSwing.openPlanExecution('${t.ticker}', '${t.date}')" style="padding:4px 10px; font-size:11px; font-weight:700; background:#059669; color:#ffffff; border-color:#059669; cursor:pointer;" title="Open Trade Plan & Copilot Execution">⚡ Plan Execution</button>
          </div>
        `;
      } else if (st === 'IN_TRADE') {
        const pnlPct = (t.unrealized_pnl_pct !== undefined && t.unrealized_pnl_pct !== null) ? Number(t.unrealized_pnl_pct) : 0;
        const pnlColor = pnlPct >= 0 ? '#059669' : '#dc2626';
        const pnlStr = `<span style="font-family:'JetBrains Mono',monospace; font-weight:800; color:${pnlColor};">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%</span>`;
        actionHtml = `
          <div style="background:#eef2ff; border:1px solid #c7d2fe; border-radius:6px; padding:7px 12px; margin-top:8px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">
            <span style="color:#3730a3; font-weight:700; font-size:12px;">💼 TAKE ACTION: Position Active (${pnlStr})! Stalking Target 1 ($${Number(t.target_1 || 0).toFixed(2)}). Hold and manage risk.</span>
            <button class="btn secondary" onclick="AppSwing.openPlanExecution('${t.ticker}', '${t.date}')" style="padding:3px 9px; font-size:11px; cursor:pointer;" title="Open Trade Plan & Copilot Execution">Manage Position</button>
          </div>
        `;
      } else if (st.includes('TARGET') || st === 'TARGET_HIT') {
        actionHtml = `
          <div style="background:#ecfeff; border:1px solid #a5f3fc; border-radius:6px; padding:7px 12px; margin-top:8px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">
            <span style="color:#0e7490; font-weight:700; font-size:12px;">🎉 TAKE ACTION: Target 1 achieved! Trim 50% profits and trail stop to breakeven.</span>
            <button class="btn secondary" onclick="AppSwing.openPlanExecution('${t.ticker}', '${t.date}')" style="padding:3px 9px; font-size:11px; cursor:pointer;" title="Open Trade Plan to Manage Runner">Manage Runner</button>
          </div>
        `;
      } else if (st === 'MISSED_RUNAWAY') {
        actionHtml = `
          <div style="background:#fffbeb; border:1px solid #fde68a; border-radius:6px; padding:6px 12px; margin-top:8px; font-size:11.5px; color:#92400e;">
            <strong>⚠️ TAKE ACTION:</strong> Stock expanded to target without filling entry. <strong>DO NOT CHASE at market.</strong> Stand aside and wait for next base.
          </div>
        `;
      } else if (isTestingSupport) {
        actionHtml = `
          <div style="background:#fffbeb; border:1px solid #fde68a; border-radius:6px; padding:7px 12px; margin-top:8px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">
            <span style="color:#92400e; font-weight:700; font-size:12px;">⚠️ TAKE ACTION: Structural floor probe. Spot ($${spot.toFixed(2)}) is testing support ($${invPrice.toFixed(2)}). Thesis invalidation requires Daily Close Below. Stand firm; do not panic sell intraday noise wicks.</span>
            <button class="btn secondary" onclick="AppSwing.openReportModal('${t.date}', '${t.ticker}', 'plan')" style="padding:2px 8px; font-size:11px; cursor:pointer;" title="Inspect Support & Invalidation Rules">Inspect Floor</button>
          </div>
        `;
      } else if (isReclaimed) {
        actionHtml = `
          <div style="background:#ecfdf5; border:1px solid #a7f3d0; border-radius:6px; padding:7px 12px; margin-top:8px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">
            <span style="color:#065f46; font-weight:700; font-size:12px;">📈 TAKE ACTION: Structural floor defended! Spot ($${spot.toFixed(2)}) dipped and rebounded above stop ($${invPrice.toFixed(2)}). Setup remains active—stalk entry or manage runner.</span>
            <button class="btn" onclick="AppSwing.openPlanExecution('${t.ticker}', '${t.date}')" style="padding:4px 10px; font-size:11px; font-weight:700; background:#059669; color:#ffffff; border-color:#059669; cursor:pointer;" title="Open Trade Plan & Copilot Execution">⚡ Plan Execution</button>
          </div>
        `;
      } else if ((st === 'INVALIDATED' || st === 'STOP_BREACHED') && isActuallyStopped) {
        actionHtml = `
          <div style="background:#fef2f2; border:1px solid #fecaca; border-radius:6px; padding:6px 12px; margin-top:8px; font-size:11.5px; color:#991b1b;">
            <strong>🛑 TAKE ACTION:</strong> Structural floor breached ($${invPrice.toFixed(2)}). Cancel resting limit orders. Trade thesis is invalidated.
          </div>
        `;
      } else {
        actionHtml = `
          <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:6px; padding:6px 12px; margin-top:8px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px; font-size:11.5px; color:var(--text-muted);">
            <span><strong style="color:var(--text-main);">⏳ TAKE ACTION:</strong> Stalking entry zone. Keep resting GTC limit order active at $${Number(t.entry_zone_high || 0).toFixed(2)}. Do not chase.</span>
            <button class="btn secondary" onclick="AppSwing.openReportModal('${t.date}', '${t.ticker}', 'plan')" style="padding:2px 8px; font-size:11px; cursor:pointer;" title="Inspect Suggested Levels & Plan">Inspect Levels</button>
          </div>
        `;
      }

      // Clean options summary
      let cleanOpt = (t.options_summary || '').replace(/[\u2010-\u2015\u2212]/g, '-').replace(/[\u2018\u2019]/g, "'").replace(/[\u201C\u201D]/g, '"').trim();
      if (!cleanOpt && t.options_structure && t.options_structure !== 'NONE') {
        cleanOpt = t.options_structure;
      }
      const optDisplay = cleanOpt || 'None suggested (Equity pullback plan only)';

      // Collapsible Trade Ideas Drawer HTML
      const ideasDrawerHtml = (isIdeasOpen && ideas.length > 0) ? `
        <div id="trade-ideas-drawer-${sym}" style="margin-top:10px; background:var(--bg-subtle); border:1px solid var(--border); border-radius:3px; padding:12px;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <div style="display:flex; align-items:center; gap:6px;">
              <span style="font-size:14px;">⚡</span>
              <strong style="font-family:'Outfit',sans-serif; font-size:12.5px; color:var(--text-main); letter-spacing:0.2px;">Tactical Trade Ideas for ${sym}</strong>
              <span class="pill" style="font-size:9.5px; padding:1px 5px; font-weight:700;">Entry Setup</span>
            </div>
            <button class="btn secondary" onclick="AppWatchlist.toggleTradeIdeas('${sym}')" style="padding:2px 7px; font-size:10px; font-family:var(--font-mono); font-weight:700; border-radius:2px;">✕ Close</button>
          </div>
          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:8px;">
            ${ideas.map(idea => `
              <div style="background:var(--bg-surface); border:1px solid var(--border); border-left:3px solid var(--cyan); border-radius:3px; padding:8px 10px; font-size:11.5px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:3px;">
                  <strong style="font-family:var(--font-sans); color:var(--blue); font-weight:800;">${idea.title}</strong>
                  <span class="pill" style="font-size:9px; padding:1px 4px; font-weight:700;">${idea.category}</span>
                </div>
                <div style="color:var(--text-main); line-height:1.35; margin-bottom:4px;">${idea.action}</div>
                ${(idea.metrics || idea.rationale) ? `<div style="font-family:'JetBrains Mono',monospace; font-size:10.5px; color:var(--cyan); margin-top:3px; font-weight:700;">${idea.metrics || idea.rationale}</div>` : ''}
              </div>
            `).join('')}
          </div>
        </div>
      ` : '';

      return `
        <div class="target-card" id="radar-card-${sym}" style="background:var(--bg-surface); border:1px solid var(--border); border-radius:3px; padding:12px 16px; margin-bottom:10px; box-shadow:var(--shadow-card);">
          <!-- Main Row: Left (Symbol, Time, Spot) | Center (TRADE SETUP) | Right (Status, Buttons) -->
          <div style="display:flex; align-items:center; justify-content:space-between; gap:14px;">
            
            <!-- Left: Ticker, Timestamp & Live Schwab Quote -->
            <div style="display:flex; align-items:center; gap:10px; min-width:230px; flex-shrink:0;">
              <button class="btn secondary" onclick="AppSwing.openReportModal('${t.date}', '${t.ticker}')" style="padding:4px 10px; font-size:12.5px; font-weight:800; color:var(--blue); border-color:#bfdbfe; background:#eff6ff;" title="Open ${t.ticker} Dossier">
                📑 $${t.ticker}
              </button>
              <span class="pill" style="font-size:10px; padding:2px 6px; font-weight:600;">${timeBadge}</span>
              <div style="display:flex; flex-direction:column; align-items:flex-start; gap:1px;">
                <div style="display:flex; align-items:center; gap:5px;">
                  <span id="radar-spot-${sym}" style="font-family:'JetBrains Mono',monospace; font-size:14px; color:var(--text-main); font-weight:800;">${spotStr}</span>
                  ${schwabPill}
                </div>
                ${chgStr}
              </div>
            </div>

            <!-- Center: Tactical Trade Setup (Options Play & Equity Plan) -->
            <div style="flex:1; min-width:0; display:flex; flex-direction:column; gap:3px; padding:0 12px; border-left:1px solid var(--border); border-right:1px solid var(--border);">
              <!-- Options Play -->
              <div style="display:flex; align-items:center; gap:6px; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                <span style="color:var(--blue); font-weight:700; font-size:10.5px; text-transform:uppercase; letter-spacing:0.3px; flex-shrink:0;">Options Play:</span>
                <span style="font-family:'JetBrains Mono',monospace; color:var(--text-main); font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${optDisplay}">
                  ${optDisplay}
                </span>
                ${t.options_actionable ? '<span class="pill green" style="font-size:9.5px; padding:1px 5px; font-weight:800; flex-shrink:0;">ACTIONABLE</span>' : ''}
              </div>

              <!-- Equity Plan -->
              <div style="display:flex; align-items:center; gap:8px; font-size:11.5px; color:var(--text-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                <span style="font-weight:700; color:var(--text-muted); font-size:10.5px; text-transform:uppercase; flex-shrink:0;">Equity Plan:</span>
                <span>Limit: <strong style="color:var(--text-main); font-family:'JetBrains Mono',monospace;">$${Number(t.entry_zone_low || 0).toFixed(2)}–$${Number(t.entry_zone_high || 0).toFixed(2)}</strong></span>
                <span style="opacity:0.4;">•</span>
                <span>Stop: <strong style="color:var(--rose); font-family:'JetBrains Mono',monospace;">$${Number(t.tactical_stop || 0).toFixed(2)}</strong></span>
                <span style="opacity:0.4;">•</span>
                <span>T1: <strong style="color:var(--emerald); font-family:'JetBrains Mono',monospace;">$${Number(t.target_1 || 0).toFixed(2)}</strong></span>
                ${t.target_2 ? `<span style="opacity:0.4;">•</span><span>T2: <strong style="color:var(--blue); font-family:'JetBrains Mono',monospace;">$${Number(t.target_2).toFixed(2)}</strong></span>` : ''}
              </div>
            </div>

            <!-- Right: Status / Hit Outcome Badge & Quick Buttons -->
            <div style="display:flex; align-items:center; gap:6px; flex-shrink:0;">
              ${statusBadge}
              ${st === 'IN_TRADE' ? `
                <button class="btn secondary" onclick="AppWatchlist.updateTargetStatus('${t.ticker}', 'STALKING', ${t.id || t.row_id || 'null'})" style="padding:3px 7px; font-size:10.5px; font-weight:700; color:#4b5563; background:#f3f4f6; border:1px solid #d1d5db; cursor:pointer;" title="Reset back to Stalking">↩️ Reset</button>
              ` : (st === 'STALKING' || st === 'IN_ZONE') ? `
                <button class="btn secondary" onclick="AppWatchlist.updateTargetStatus('${t.ticker}', 'IN_TRADE', ${t.id || t.row_id || 'null'})" style="padding:3px 7px; font-size:10.5px; font-weight:700; color:#059669; background:#ecfdf5; border:1px solid #a7f3d0; cursor:pointer;" title="Mark as Filled in Broker / Active Trade">✅ Filled</button>
              ` : ''}
              ${ideas.length > 0 ? `
                <button class="btn secondary" onclick="AppWatchlist.toggleTradeIdeas('${sym}')" style="padding:3px 8px; font-size:11px; font-weight:700; color:#3730a3; background:#eef2ff; border-color:#c7d2fe;" title="Toggle Structured Trade Ideas">💡 Ideas (${ideas.length})</button>
              ` : ''}
              <button class="btn secondary" onclick="AppWatchlist.toggleInlineChart('${t.ticker}', '${t.date}')" style="padding:3px 9px; font-size:11.5px; font-weight:800; color:var(--blue); border-color:#bfdbfe; background:#eff6ff;" title="Open Live TradingView Chart for ${t.ticker}">📈 Live Chart</button>
              <button class="btn secondary" onclick="AppSwing.openPlanExecution('${t.ticker}', '${t.date}')" style="padding:3px 8px; font-size:11px; color:var(--blue); cursor:pointer;" title="Ask Copilot about ${t.ticker}">💬</button>
              <button class="btn secondary" onclick="AppSwing.openReportModal('${t.date}', '${t.ticker}')" style="padding:3px 8px; font-size:11px;" title="Open Full Dossier">📖</button>
              <button class="btn secondary" onclick="AppWatchlist.deleteTarget('${t.ticker}', '${t.date}')" style="padding:3px 6px; font-size:11px; background:#fef2f2; border:1px solid #fecaca; color:#b91c1c;" title="Untrack ${t.ticker}">🗑️</button>
            </div>

          </div>

          <!-- Bottom: Action Directive Banner -->
          ${actionHtml}

          <!-- Collapsible Structured Trade Ideas Drawer -->
          ${ideasDrawerHtml}

          <!-- Inline Expandable Live Interactive TradingView Chart Drawer -->
          <div id="inline-chart-${sym}" style="display:${isOpen ? 'block' : 'none'}; margin-top:10px; background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; flex-wrap:wrap; gap:8px;">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:15px;">📈</span>
                <span style="font-family:'Outfit',sans-serif; font-size:13.5px; font-weight:800; color:var(--text-main);">${sym} Live Interactive TradingView Chart</span>
                <span class="pill green" style="font-size:9.5px; padding:1px 6px;">Live Real-Time Feed</span>
                <a href="https://www.tradingview.com/chart/jPAQSlZC/?symbol=${encodeURIComponent(sym)}&interval=D" target="_blank" class="btn secondary" style="padding:2px 8px; font-size:11px; font-weight:700; color:var(--blue); text-decoration:none; display:inline-flex; align-items:center; gap:4px;" title="Open in your logged-in TradingView session with the Rev - Enhanced v2 layout (All Indicators)">
                  🚀 Personal Layout (All Indicators) ↗
                </a>
              </div>
              <div style="display:flex; align-items:center; gap:6px;">
                <!-- Timeframe Pills -->
                <div style="display:flex; align-items:center; gap:2px; background:var(--bg-surface); padding:2px; border:1px solid var(--border); border-radius:6px;">
                  <button class="btn secondary" id="tv-btn-d-${sym}" onclick="AppWatchlist.setTvInterval('${sym}', 'D')" style="padding:2px 7px; font-size:10.5px; font-weight:700;">1D</button>
                  <button class="btn secondary" id="tv-btn-60-${sym}" onclick="AppWatchlist.setTvInterval('${sym}', '60')" style="padding:2px 7px; font-size:10.5px; font-weight:700;">1H</button>
                  <button class="btn secondary" id="tv-btn-15-${sym}" onclick="AppWatchlist.setTvInterval('${sym}', '15')" style="padding:2px 7px; font-size:10.5px; font-weight:700;">15m</button>
                  <button class="btn secondary" id="tv-btn-5-${sym}" onclick="AppWatchlist.setTvInterval('${sym}', '5')" style="padding:2px 7px; font-size:10.5px; font-weight:700;">5m</button>
                  <button class="btn secondary" id="tv-btn-w-${sym}" onclick="AppWatchlist.setTvInterval('${sym}', 'W')" style="padding:2px 7px; font-size:10.5px; font-weight:700;">1W</button>
                </div>
                <button class="btn secondary" onclick="AppWatchlist.toggleStaticSnapshot('${sym}')" style="padding:2px 8px; font-size:10.5px;" title="Toggle between Live TV Chart and Historical Scraped Snapshot">📸 Scraped Snapshot</button>
                <button class="btn secondary" onclick="AppWatchlist.closeInlineChart('${sym}')" style="padding:2px 8px; font-size:10.5px; font-weight:700;">✕ Close</button>
              </div>
            </div>

            <!-- Live TradingView Frame -->
            <div id="inline-tv-wrap-${sym}" style="display:${isSnap ? 'none' : 'block'}; height:480px; width:100%; border-radius:6px; overflow:hidden; border:1px solid var(--border); background:var(--bg-surface);">
              <iframe id="inline-tv-frame-${sym}" style="width:100%; height:100%; border:none;" src="${tvSrc}"></iframe>
            </div>

            <!-- Scraped Snapshot View (Hidden by default, toggleable) -->
            <div id="inline-snapshot-wrap-${sym}" style="display:${isSnap ? 'block' : 'none'}; text-align:center; min-height:220px; background:var(--bg-surface); border-radius:6px; overflow:hidden; border:1px solid var(--border); padding:10px;">
              <div style="margin-bottom:8px; font-size:11.5px; color:var(--text-muted); display:flex; justify-content:space-between; align-items:center;">
                <span>Historical Scraped Chart Snapshot (Date: ${t.date})</span>
                <div style="display:flex; gap:4px;">
                  <button class="btn secondary" onclick="AppWatchlist.setInlineChartType('${sym}', '${t.date}', 'zoom')" style="padding:2px 6px; font-size:10px;">90d Zoom</button>
                  <button class="btn secondary" onclick="AppWatchlist.setInlineChartType('${sym}', '${t.date}', 'plain')" style="padding:2px 6px; font-size:10px;">Plain</button>
                </div>
              </div>
              <img id="inline-chart-img-${sym}" src="/api/charts/latest/${sym}/zoom" style="max-width:100%; max-height:450px; object-fit:contain; border-radius:6px; cursor:pointer;" alt="${sym} Chart" onclick="AppWatchlist.openChartFullscreen('${sym}', '${t.date}')" title="Click to view full screen" />
            </div>
          </div>
        </div>
      `;
    }).join('');

    this._currentVisibleTargets = topTargets;

    radarEl.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:10px;">
        
        <!-- Multi-Chart Battle Station Toolbar -->
        <div style="display:flex; justify-content:space-between; align-items:center; background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:8px 14px; margin-bottom:2px; flex-wrap:wrap; gap:8px;">
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="font-size:14px;">🖥️</span>
            <span style="font-size:12.5px; font-weight:800; color:var(--text-main);">Live Multi-Chart Desk:</span>
            <span style="font-size:11.5px; color:var(--text-muted);">${topTargets.length} active research candidates ready on tape</span>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <button class="btn secondary" onclick="AppWatchlist.expandAllCharts()" style="padding:4px 12px; font-size:11.5px; font-weight:800; color:var(--blue); border-color:#bfdbfe; background:#eff6ff;" title="Open all ${topTargets.length} Live TradingView Charts on one screen simultaneously">
              📈 Open All (${topTargets.length}) Live Charts
            </button>
            <button class="btn secondary" onclick="AppWatchlist.collapseAllCharts()" style="padding:4px 10px; font-size:11.5px; font-weight:700;" title="Collapse all open charts">
              📉 Collapse All
            </button>
          </div>
        </div>

        ${cardsHtml}

        <div style="display:flex; align-items:center; justify-content:space-between; margin-top:8px; flex-wrap:wrap; gap:8px;">
          <span style="font-size:12px; color:var(--text-muted);">Displaying top ${topTargets.length} of ${targets.length} research targets (Sorted: <strong>${this._radarSort === 'research_time' ? 'Deep Research Execution Time' : this._radarSort}</strong>)</span>
          <button class="btn" onclick="App.switchDesk('watchlist')" style="font-size:12px; padding:6px 14px;">
            📊 View Full Watchlist & Historical Table (${allTargets.length} Targets) ↗
          </button>
        </div>
      </div>
    `;

  },

  _lightboxTicker: null,
  _lightboxDate: null,
  _lightboxType: 'zoom',
  _inlineTvIntervals: {},
  _currentVisibleTargets: [],

  expandAllCharts() {
    const targets = this._currentVisibleTargets || [];
    targets.forEach(t => {
      const sym = (t.ticker || '').toUpperCase();
      this._activeOpenCharts.add(sym);
      const el = document.getElementById(`inline-chart-${sym}`);
      if (el) {
        el.style.display = 'block';
        const frame = document.getElementById(`inline-tv-frame-${sym}`);
        if (frame && (!frame.src || frame.src.includes('about:blank'))) {
          const interval = this._inlineTvIntervals[sym] || 'D';
          const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
          frame.src = `https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(sym)}&interval=${encodeURIComponent(interval)}&hidesidetoolbar=0&symboledit=1&saveimage=1&toolbarbg=f1f3f6&studies=%5B%5D&theme=${theme}&style=1&timezone=America%2FNew_York`;
        }
      }
    });
  },

  collapseAllCharts() {
    this._activeOpenCharts.clear();
    const targets = this._currentVisibleTargets || [];
    targets.forEach(t => {
      const sym = (t.ticker || '').toUpperCase();
      const el = document.getElementById(`inline-chart-${sym}`);
      if (el) el.style.display = 'none';
    });
  },

  closeInlineChart(ticker) {
    const sym = ticker.toUpperCase();
    this._activeOpenCharts.delete(sym);
    const el = document.getElementById(`inline-chart-${sym}`);
    if (el) el.style.display = 'none';
  },

  toggleInlineChart(ticker, date) {
    const sym = ticker.toUpperCase();
    const el = document.getElementById(`inline-chart-${sym}`);
    if (!el) return;
    const isHidden = el.style.display === 'none' || !el.style.display;

    if (isHidden) {
      this._activeOpenCharts.add(sym);
      el.style.display = 'block';
      const frame = document.getElementById(`inline-tv-frame-${sym}`);
      if (frame && (!frame.src || frame.src.includes('about:blank'))) {
        const interval = this._inlineTvIntervals[sym] || 'D';
        const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
        frame.src = `https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(sym)}&interval=${encodeURIComponent(interval)}&hidesidetoolbar=0&symboledit=1&saveimage=1&toolbarbg=f1f3f6&studies=%5B%5D&theme=${theme}&style=1&timezone=America%2FNew_York`;
      }
    } else {
      this._activeOpenCharts.delete(sym);
      el.style.display = 'none';
    }
  },

  setTvInterval(ticker, interval) {
    const sym = ticker.toUpperCase();
    this._inlineTvIntervals[sym] = interval;
    const frame = document.getElementById(`inline-tv-frame-${sym}`);
    if (frame) {
      const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      frame.src = `https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(sym)}&interval=${encodeURIComponent(interval)}&hidesidetoolbar=0&symboledit=1&saveimage=1&toolbarbg=f1f3f6&studies=%5B%5D&theme=${theme}&style=1&timezone=America%2FNew_York`;
    }
  },

  toggleStaticSnapshot(ticker) {
    const sym = ticker.toUpperCase();
    const tvWrap = document.getElementById(`inline-tv-wrap-${sym}`);
    const snapWrap = document.getElementById(`inline-snapshot-wrap-${sym}`);
    if (!tvWrap || !snapWrap) return;
    const isTvVisible = tvWrap.style.display !== 'none';
    tvWrap.style.display = isTvVisible ? 'none' : 'block';
    snapWrap.style.display = isTvVisible ? 'block' : 'none';
    if (isTvVisible) {
      this._activeSnapshotToggles.add(sym);
    } else {
      this._activeSnapshotToggles.delete(sym);
    }
  },

  setInlineChartType(ticker, date, type) {
    const sym = ticker.toUpperCase();
    const img = document.getElementById(`inline-chart-img-${sym}`);
    if (img) {
      img.src = `/api/charts/latest/${encodeURIComponent(sym)}/${type}?t=${Date.now()}`;
    }
  },

  openChartFullscreen(ticker, date) {
    const sym = ticker || this._activeChartTicker || 'TICKER';
    const d = date || this._activeChartDate || '';
    const modal = document.getElementById('chart-lightbox-modal');
    const title = document.getElementById('lightbox-chart-title');
    const badge = document.getElementById('lightbox-chart-badge');
    const img = document.getElementById('lightbox-chart-img');

    if (modal && img) {
      this._lightboxTicker = sym;
      this._lightboxDate = d;
      if (title) title.textContent = `$${sym} TradingView Technical Chart (${d || 'Latest'})`;
      if (badge) badge.textContent = (this._activeChartType === 'zoom' ? '90-Day Zoom' : this._activeChartType.toUpperCase());
      img.src = `/api/charts/latest/${encodeURIComponent(sym)}/${this._activeChartType || 'zoom'}?t=${Date.now()}`;
      modal.style.display = 'flex';
    }
  },

  closeChartFullscreen() {
    const modal = document.getElementById('chart-lightbox-modal');
    if (modal) modal.style.display = 'none';
  },

  setLightboxType(type) {
    this._activeChartType = type;
    const sym = this._lightboxTicker || this._activeChartTicker;
    const img = document.getElementById('lightbox-chart-img');
    const badge = document.getElementById('lightbox-chart-badge');
    if (img && sym) {
      img.src = `/api/charts/latest/${encodeURIComponent(sym)}/${type}?t=${Date.now()}`;
    }
    if (badge) {
      badge.textContent = type === 'zoom' ? '90-Day Zoom' : type.toUpperCase();
    }
    ['zoom', 'plain', 'full'].forEach(t => {
      const btn = document.getElementById(`lightbox-btn-${t}`);
      if (btn) {
        if (t === type) {
          btn.style.color = 'var(--cyan-glow)';
          btn.style.borderColor = 'var(--cyan-glow)';
          btn.style.fontWeight = '800';
        } else {
          btn.style.color = '';
          btn.style.borderColor = '';
          btn.style.fontWeight = 'normal';
        }
      }
    });
  },

  async deleteTarget(ticker, date) {
    if (!ticker) return;
    const confirmMsg = `Untrack and remove ${ticker} (${date || 'all dates'}) from active Watchlist & Live Alerts?`;
    if (!confirm(confirmMsg)) return;

    try {
      const res = await window.AppApi.deleteWatchTarget(ticker, date);
      if (window.AppStatus) {
        window.AppStatus.showToast(`🗑️ Untracked and removed ${ticker} from Watchlist`);
      }

      // Automatically close modal if currently viewing this ticker
      if (window.AppState && window.AppState.currentReportData && window.AppState.currentReportData.ticker === ticker) {
        if (window.AppSwing && window.AppSwing.closeReportModal) {
          window.AppSwing.closeReportModal();
        }
      }

      await this.loadWatchlist();
    } catch (e) {
      alert(`Failed to untrack ${ticker}: ${e.message}`);
    }
  },

  async updateTargetStatus(ticker, newStatus, rowId = null) {
    if (!ticker && !rowId) return;
    try {
      const userTaken = (newStatus === 'IN_TRADE') ? 1 : 0;
      const res = await fetch('/api/watch-targets/status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker, status: newStatus, id: rowId, row_id: rowId, user_taken: userTaken }),
      });
      const data = await res.json();
      if (res.ok && data.status === 'ok') {
        if (window.AppStatus && window.AppStatus.showToast) {
          window.AppStatus.showToast(`⚡ ${ticker || 'Target'} status updated to ${newStatus}`);
        }
        await this.loadWatchlist();
      } else {
        alert(data.detail || data.error || 'Failed updating status');
      }
    } catch (e) {
      console.error('Error updating target status:', e);
    }
  },

  // =========================================================================
  // ON-DEMAND SUGGESTED TRADES AUDIT & PERFORMANCE TRAIL MODAL
  // =========================================================================
  _auditPage: 1,
  _auditPageSize: 10,
  _auditWindow: 100,
  _activeAuditTab: 'ALL',
  _auditSearchQuery: '',
  _auditSearchTimer: null,

  async openAuditModal() {
    const modal = document.getElementById('modal-watchlist-audit');
    if (!modal) return;
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    await this.loadAuditData();
  },

  closeAuditModal() {
    const modal = document.getElementById('modal-watchlist-audit');
    if (modal) modal.style.display = 'none';
    document.body.style.overflow = '';
  },

  async loadAuditSummaryOnly() {
    try {
      if (!this._auditData) {
        this._auditData = await window.AppApi.getTradesAudit({ window: 100, page: 1, page_size: 1 });
      }
      this.updateAuditToolbarBadge();
    } catch (e) {
      // Quiet fail on background sync
    }
  },

  async loadAuditData(forceEvaluate = false) {
    try {
      const params = {
        tab: this._activeAuditTab || 'ALL',
        search: this._auditSearchQuery || '',
        page: this._auditPage || 1,
        page_size: this._auditPageSize || 10,
        window: (this._auditWindow !== undefined) ? this._auditWindow : 100,
      };

      let res;
      if (forceEvaluate) {
        await window.AppApi.evaluateTradesAudit(params.window);
        res = await window.AppApi.getTradesAudit(params);
      } else {
        res = await window.AppApi.getTradesAudit(params);
      }
      this._auditData = res;
      this.renderAuditModal();
      this.updateAuditToolbarBadge();
    } catch (err) {
      console.error('Failed to load trades audit data:', err);
    }
  },

  updateAuditToolbarBadge() {
    const badge = document.getElementById('audit-pill-badge');
    if (!badge || !this._auditData || !this._auditData.summary) return;
    const s = this._auditData.summary;
    const net = Number(s.net_profit || 0);
    const sign = net >= 0 ? '+' : '';
    const wr = s.resolved_win_rate !== undefined ? `${s.resolved_win_rate}% WR` : '';
    const winLabel = (s.window && s.window > 0) ? `Last ${s.window}` : 'All';
    badge.innerText = `${sign}${net.toFixed(2)}R (${wr}) . ${winLabel}`;
    badge.className = net >= 0 ? 'pill green' : 'pill red';
  },

  async triggerAuditEvaluation() {
    const spinner = document.getElementById('re-evaluate-spinner');
    const icon = document.getElementById('re-evaluate-icon');
    const btn = document.getElementById('btn-re-evaluate-audit');
    if (spinner) spinner.style.display = 'inline-block';
    if (icon) icon.style.display = 'none';
    if (btn) btn.disabled = true;

    try {
      await this.loadAuditData(true);
      if (window.AppStatus && window.AppStatus.showToast) {
        window.AppStatus.showToast('✅ Evaluated realtime quotes for active setups on demand without API overload.');
      }
    } catch (e) {
      console.error(e);
      if (window.AppStatus && window.AppStatus.showToast) {
        window.AppStatus.showToast('❌ Error evaluating suggested trades: ' + e.message);
      }
    } finally {
      if (spinner) spinner.style.display = 'none';
      if (icon) icon.style.display = 'inline-block';
      if (btn) btn.disabled = false;
    }
  },

  setAuditTab(tab) {
    this._activeAuditTab = tab;
    this._auditPage = 1;
    const container = document.getElementById('audit-modal-filter-tabs');
    if (container) {
      container.querySelectorAll('button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.auditTab === tab);
      });
    }
    this.loadAuditData();
  },

  setAuditSearch(q) {
    if (this._auditSearchTimer) clearTimeout(this._auditSearchTimer);
    this._auditSearchTimer = setTimeout(() => {
      this._auditSearchQuery = (q || '').trim();
      this._auditPage = 1;
      this.loadAuditData();
    }, 250);
  },

  setAuditPage(page) {
    if (!this._auditData || !this._auditData.pagination) return;
    const p = this._auditData.pagination;
    if (page === 'last') {
      this._auditPage = p.total_pages || 1;
    } else {
      this._auditPage = Math.max(1, Math.min(p.total_pages || 1, parseInt(page, 10) || 1));
    }
    this.loadAuditData();
  },

  prevAuditPage() {
    if (this._auditPage > 1) {
      this._auditPage--;
      this.loadAuditData();
    }
  },

  nextAuditPage() {
    const totalPages = this._auditData?.pagination?.total_pages || 1;
    if (this._auditPage < totalPages) {
      this._auditPage++;
      this.loadAuditData();
    }
  },

  setAuditPageSize(size) {
    this._auditPageSize = parseInt(size, 10) || 10;
    this._auditPage = 1;
    this.loadAuditData();
  },

  setAuditWindow(win) {
    this._auditWindow = parseInt(win, 10);
    this._auditPage = 1;
    this.loadAuditData();
  },

  renderAuditModal() {
    if (!this._auditData) return;
    const s = this._auditData.summary || {};
    const tc = this._auditData.tab_counts || {};
    const p = this._auditData.pagination || {};
    const trades = this._auditData.trades || [];

    // 1. Update Evaluation Timestamp
    const timeEl = document.getElementById('modal-audit-evaluated-time');
    if (timeEl && s.last_evaluated_at) {
      const dt = new Date(s.last_evaluated_at);
      timeEl.innerText = isNaN(dt.getTime()) ? s.last_evaluated_at : dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' ' + dt.toLocaleDateString();
    }

    // 2. Render 5 KPI Cards in Modal (Calculated on the Last 100 Trades Window by default)
    this.renderAuditKpis(s);

    // 3. Update Audit Tab Badges
    const setAuditBadge = (id, count) => {
      const el = document.getElementById(id);
      if (el) el.innerText = (count !== undefined) ? count : 0;
    };
    setAuditBadge('audit-tab-count-all', tc.all);
    setAuditBadge('audit-tab-count-options', tc.options);
    setAuditBadge('audit-tab-count-shares', tc.shares);
    setAuditBadge('audit-tab-count-won', tc.won);
    setAuditBadge('audit-tab-count-stopped', tc.stopped);
    setAuditBadge('audit-tab-count-active', tc.active);
    setAuditBadge('audit-tab-count-stalking', tc.stalking);

    // 4. Update Pagination Bar
    this.renderAuditPagination(p);

    // 5. Render Table Rows (Only current page rows)
    this.renderAuditTableOnly(trades);
  },

  renderAuditPagination(p) {
    const rangeEl = document.getElementById('audit-pagination-range');
    const curEl = document.getElementById('audit-current-page');
    const totEl = document.getElementById('audit-total-pages');
    const btnFirst = document.getElementById('btn-audit-page-first');
    const btnPrev = document.getElementById('btn-audit-page-prev');
    const btnNext = document.getElementById('btn-audit-page-next');
    const btnLast = document.getElementById('btn-audit-page-last');

    if (rangeEl) {
      if (!p || p.total_items === 0) {
        rangeEl.innerText = 'No trades found';
      } else {
        const winTxt = (p.window && p.window > 0) ? ` (in last ${p.window} trades)` : '';
        rangeEl.innerText = `Showing ${p.start_index}–${p.end_index} of ${p.total_items} Trades${winTxt}`;
      }
    }

    if (curEl) curEl.innerText = p.page || 1;
    if (totEl) totEl.innerText = p.total_pages || 1;

    if (btnFirst) btnFirst.disabled = !p.has_prev;
    if (btnPrev) btnPrev.disabled = !p.has_prev;
    if (btnNext) btnNext.disabled = !p.has_next;
    if (btnLast) btnLast.disabled = !p.has_next;

    const selectSize = document.getElementById('select-audit-pagesize');
    if (selectSize && String(p.page_size) !== selectSize.value) {
      selectSize.value = String(p.page_size || 10);
    }
  },

  renderAuditKpis(s) {
    const container = document.getElementById('modal-audit-kpi-strip');
    if (!container) return;

    const winRate = s.resolved_win_rate !== undefined ? Number(s.resolved_win_rate).toFixed(1) : '0.0';
    const wonCount = s.won_count || 0;
    const lostCount = s.lost_count || 0;
    const avgWin = Number(s.avg_win || 0);
    const avgLoss = Number(s.avg_loss || 0);
    const net = Number(s.net_profit || 0);
    const netSign = net >= 0 ? '+' : '';
    const netColor = net >= 0 ? '#10b981' : '#f43f5e';
    const pf = s.profit_factor !== undefined ? Number(s.profit_factor).toFixed(2) : '0.00';
    const actCount = s.actionable_count || 0;
    const winNote = (s.window && s.window > 0) ? `Last ${s.window}` : 'All Time';

    container.innerHTML = `
      <div class="perf-kpi-card win-rate" style="cursor:pointer;" onclick="AppWatchlist.setAuditTab('WON')" title="Click to filter to won trades">
        <span class="perf-kpi-title">🏆 Win Rate (${winNote})</span>
        <div class="perf-kpi-val" style="color:#10b981;">
          ${winRate}%
          <span class="perf-kpi-sub">(${wonCount} Won / ${lostCount} Stopped)</span>
        </div>
      </div>

      <div class="perf-kpi-card avg-win">
        <span class="perf-kpi-title">📈 Avg Win (${winNote})</span>
        <div class="perf-kpi-val" style="color:#34d399;">
          +${avgWin.toFixed(2)} R
          <span class="perf-kpi-sub">${wonCount} Trades Hit</span>
        </div>
      </div>

      <div class="perf-kpi-card avg-loss" style="cursor:pointer;" onclick="AppWatchlist.setAuditTab('STOPPED')" title="Click to filter to stopped trades">
        <span class="perf-kpi-title">📉 Avg Loss (Defined Risk)</span>
        <div class="perf-kpi-val" style="color:#f87171;">
          -${Math.abs(avgLoss).toFixed(2)} R
          <span class="perf-kpi-sub">${lostCount} Stopped</span>
        </div>
      </div>

      <div class="perf-kpi-card net-alpha">
        <span class="perf-kpi-title">💰 Net Return (${winNote})</span>
        <div class="perf-kpi-val" style="color:${netColor};">
          ${netSign}${Math.abs(net).toFixed(2)} R
          <span class="perf-kpi-sub">(${pf} Profit Factor)</span>
        </div>
      </div>

      <div class="perf-kpi-card actionable" style="cursor:pointer;" onclick="AppWatchlist.setAuditTab('ACTIVE')" title="Click to filter to active setups">
        <span class="perf-kpi-title">⚡ Actionable Now</span>
        <div class="perf-kpi-val" style="color:#f59e0b;">
          ${actCount} Setups
          <span class="perf-kpi-sub">In Zone / In Trade</span>
        </div>
      </div>
    `;
  },

  renderAuditTableOnly(trades) {
    const tbody = document.getElementById('audit-table-tbody');
    if (!tbody) return;

    if (!trades || trades.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:40px; color:var(--text-muted);">No suggested trades match this filter on this page.</td></tr>`;
      return;
    }

    tbody.innerHTML = trades.map(t => {
      const isOptions = t.trade_type === 'OPTIONS';
      const typeBadge = isOptions
        ? `<span class="pill cyan" style="font-size:10px; padding:1px 6px; font-weight:700;">OPTIONS</span>`
        : `<span class="pill blue" style="font-size:10px; padding:1px 6px; font-weight:700;">SHARES</span>`;

      let statusBadge = '';
      const st = (t.status || 'STALKING').toUpperCase();
      if (st === 'TARGET_HIT' || st === 'COMPLETED') {
        statusBadge = `<span class="pill green" style="font-size:10.5px; padding:2px 7px; font-weight:800;">🏁 TARGET HIT</span>`;
      } else if (st === 'STOP_BREACHED' || st === 'INVALIDATED' || st === 'STOPPED') {
        statusBadge = `<span class="pill red" style="font-size:10.5px; padding:2px 7px; font-weight:800;">🛑 STOPPED</span>`;
      } else if (st === 'IN_TRADE') {
        statusBadge = `<span class="pill cyan" style="font-size:10.5px; padding:2px 7px; font-weight:800;"><span class="dot pulse"></span>💼 IN TRADE</span>`;
      } else if (st === 'IN_ZONE') {
        statusBadge = `<span class="pill green" style="font-size:10.5px; padding:2px 7px; font-weight:800;">🎯 IN ZONE</span>`;
      } else if (st === 'MISSED_RUNAWAY') {
        statusBadge = `<span class="pill" style="font-size:10px; padding:2px 6px; opacity:0.7;">RUNAWAY</span>`;
      } else {
        statusBadge = `<span class="pill amber" style="font-size:10.5px; padding:2px 7px; font-weight:700;">⏳ STALKING</span>`;
      }

      // Key levels text
      let levelsHtml = '';
      if (isOptions) {
        const strikes = (t.long_strike && t.short_strike) ? `$${t.long_strike}/$${t.short_strike}` : '';
        const exp = (t.options_expiration && t.options_expiration !== 'N/A') ? t.options_expiration : '';
        const debit = t.target_debit > 0 ? `$${Number(t.target_debit).toFixed(2)} Debit` : 'Credit';
        const maxP = Number(t.max_profit || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
        const maxL = Number(t.max_loss || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
        levelsHtml = `<div style="font-size:11.5px; font-weight:600;">${strikes} ${exp} (${debit})</div>
                      <div style="font-size:10.5px; color:var(--text-muted);">Max: +$${maxP} / -$${maxL}</div>`;
      } else {
        const entryStr = t.entry_price ? `$${Number(t.entry_price).toFixed(2)}` : '--';
        const stopStr = t.tactical_stop ? `$${Number(t.tactical_stop).toFixed(2)}` : '--';
        const t1Str = t.target_1 ? `$${Number(t.target_1).toFixed(2)}` : '--';
        levelsHtml = `<div style="font-size:11.5px; font-weight:600;">Entry: ${entryStr} • Stop: ${stopStr}</div>
                      <div style="font-size:10.5px; color:var(--text-muted);">Target: ${t1Str}</div>`;
      }

      // Spot and distance
      const spot = Number(t.last_price || 0);
      const spotStr = spot > 0 ? `$${spot.toFixed(2)}` : '--';
      const dist = t.distance_to_entry_pct;
      let distStr = '--';
      let distColor = 'var(--text-muted)';
      if (dist !== null && dist !== undefined) {
        distStr = dist === 0 ? '0.0% (In Zone)' : `${dist > 0 ? '+' : ''}${dist.toFixed(1)}%`;
        distColor = Math.abs(dist) <= 1.5 ? '#10b981' : (dist > 0 ? '#f59e0b' : '#94a3b8');
      }

      // PnL (R-Multiple) & ROC %
      let pnlDisplay = '—';
      let rocDisplay = '—';
      let pnlColor = 'var(--text-muted)';

      if (t.r_multiple !== null && t.r_multiple !== undefined) {
        const rVal = Number(t.r_multiple);
        const rSign = rVal > 0 ? '+' : (rVal < 0 ? '-' : '');
        pnlDisplay = `${rSign}${Math.abs(rVal).toFixed(2)} R`;
        pnlColor = rVal >= 0 ? '#10b981' : '#f87171';
        if (isOptions && t.max_loss && Number(t.max_loss) > 0) {
          rocDisplay = `${rSign}${(rVal * 100).toFixed(1)}%`;
        }
      }

      if (!isOptions && t.pnl_pct !== null && t.pnl_pct !== undefined && (st === 'IN_TRADE' || st === 'TARGET_HIT' || st === 'STOPPED' || st === 'STOP_BREACHED')) {
        const pPct = Number(t.pnl_pct);
        const pSign = pPct > 0 ? '+' : (pPct < 0 ? '-' : '');
        rocDisplay = `${pSign}${Math.abs(pPct).toFixed(1)}%`;
      }

      return `
        <tr style="border-bottom:1px solid var(--border); transition:background 0.15s;" onmouseover="this.style.background='var(--bg-subtle)'" onmouseout="this.style.background='transparent'">
          <td style="padding:10px 14px;">
            <div style="font-family:'Outfit',sans-serif; font-size:14px; font-weight:800; color:var(--text-main);">${t.ticker}</div>
            <div style="font-size:11px; color:var(--text-muted);">${t.date}</div>
          </td>
          <td style="padding:10px 14px;">
            <div style="display:flex; align-items:center; gap:6px;">
              ${typeBadge}
              <span style="font-size:10px; font-weight:700; color:var(--text-muted);">${t.side}</span>
            </div>
            <div style="font-size:12px; font-weight:600; color:var(--text-main); margin-top:2px;">${t.trade_label || t.trade_structure}</div>
          </td>
          <td style="padding:10px 14px;">
            ${levelsHtml}
          </td>
          <td style="padding:10px 14px;">
            ${statusBadge}
          </td>
          <td style="padding:10px 14px; text-align:right;">
            <div style="font-family:'JetBrains Mono',monospace; font-size:12.5px; font-weight:700;">${spotStr}</div>
            <div style="font-size:10.5px; font-weight:600; color:${distColor};">${distStr}</div>
          </td>
          <td style="padding:10px 14px; text-align:right;">
            <span style="font-family:'JetBrains Mono',monospace; font-size:13px; font-weight:800; color:${pnlColor};">${pnlDisplay}</span>
          </td>
          <td style="padding:10px 14px; text-align:right;">
            <span style="font-family:'JetBrains Mono',monospace; font-size:12px; font-weight:700; color:${pnlColor};">${rocDisplay}</span>
          </td>
          <td style="padding:10px 14px; max-width:240px;">
            <div style="font-size:11.5px; color:var(--text-muted); line-height:1.35;">${t.outcome_notes || '--'}</div>
          </td>
        </tr>
      `;
    }).join('');
  }
};

// Global escape listener for audit modal
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const auditModal = document.getElementById('modal-watchlist-audit');
    if (auditModal && auditModal.style.display !== 'none') {
      window.AppWatchlist.closeAuditModal();
    }
  }
});

// Self-initialize on DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    if (window.AppWatchlist) window.AppWatchlist.loadWatchlist();
  });
} else {
  if (window.AppWatchlist) window.AppWatchlist.loadWatchlist();
}

