/**
 * Watchlist & Triggers Table Controller
 */
window.AppWatchlist = {
  _allTargets: [],
  _activeFilter: 'ALL',
  _searchQuery: '',
  _radarSearchQuery: '',
  _sortCol: 'date',
  _sortAsc: false,
  _viewMode: 'table', // 'table' | 'cards'
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
      const newTargets = (data && data.targets) ? data.targets : [];
      const newJson = JSON.stringify(newTargets);
      const isUnchanged = (newJson === this._lastTargetsJson);
      this._lastTargetsJson = newJson;
      this._allTargets = newTargets;

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
      const radarEl = document.getElementById('swing-radar-widget');
      if (radarEl) {
        radarEl.innerHTML = `
          <div style="color:var(--text-muted); font-size:12px; text-align:center; padding:12px;">
            ⚠️ Unable to connect to watch database. Retrying...
          </div>
        `;
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
    document.querySelectorAll('.tab-pill-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.tab === tab);
    });
    this.render();
  },

  setSearchQuery(query) {
    this._searchQuery = (query || '').trim().toLowerCase();
    this.render();
  },

  setSort(col) {
    if (this._sortCol === col) {
      this._sortAsc = !this._sortAsc;
    } else {
      this._sortCol = col;
      this._sortAsc = true;
    }
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

  getFilteredTargets() {
    let list = this._allTargets || [];

    // 1. Status Filter Tab
    if (this._activeFilter === 'IN_TRADE') {
      list = list.filter(t => (t.status || '').toUpperCase() === 'IN_TRADE');
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
    const countInZone = all.filter(t => (t.status || '').toUpperCase() === 'IN_ZONE').length;
    const countStalking = all.filter(t => (t.status || '').toUpperCase() === 'STALKING').length;
    const countInTrade = all.filter(t => (t.status || '').toUpperCase() === 'IN_TRADE').length;
    const countInvalid = all.filter(t => (t.status || '').toUpperCase() === 'INVALIDATED').length;

    const setBadge = (id, count) => {
      const el = document.getElementById(id);
      if (el) el.innerText = count;
    };

    setBadge('count-tab-all', countAll);
    setBadge('count-tab-in-zone', countInZone);
    setBadge('count-tab-stalking', countStalking);
    setBadge('count-tab-in-trade', countInTrade);
    setBadge('count-tab-invalidated', countInvalid);
    setBadge('rf-count-intrade', countInTrade);

    const mainCount = document.getElementById('watchlist-total-count');
    if (mainCount) {
      mainCount.innerText = `${countAll} Targets`;
    }
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

  renderTable(container, targets) {
    const sortIndicator = (col) => {
      if (this._sortCol !== col) return '<span style="opacity:0.3; font-size:10px;"> ⇅</span>';
      return this._sortAsc ? ' ▲' : ' ▼';
    };

    const rowsHtml = targets.map(t => {
      const sym = (t.ticker || '').toUpperCase();
      const spot = Number(t.last_price || 0);
      const stop = Number(t.tactical_stop || 0);
      const entryLow = Number(t.entry_zone_low || 0);
      const entryHigh = Number(t.entry_zone_high || 0);
      const t1 = Number(t.target_1 || 0);
      const t2 = Number(t.target_2 || 0);

      const isIdeasOpen = Boolean(this._activeTradeIdeasToggles && this._activeTradeIdeasToggles.has(sym));
      const isOpenPos = false;
      const pos = {};
      const posQty = 1;
      const posAvg = Number(t.entry_price || spot);
      const posPnl = null;
      const posPnlPct = null;

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
      if ((t.status || '').toUpperCase() === 'INVALIDATED') {
        distBadgeClass = 'invalid';
      }

      const statusUpper = (t.status || 'STALKING').toUpperCase();
      let statusBadgeClass = 'stalking';
      let statusIcon = '⏳';
      if (statusUpper === 'IN_ZONE') { statusBadgeClass = 'in_zone'; statusIcon = '🎯'; }
      else if (statusUpper === 'IN_TRADE') { statusBadgeClass = 'in_trade'; statusIcon = '🎯'; }
      else if (statusUpper === 'INVALIDATED') { statusBadgeClass = 'invalidated'; statusIcon = '⚠️'; }
      else if (statusUpper === 'TARGET_HIT') { statusBadgeClass = 'target_hit'; statusIcon = '🏁'; }
      else if (statusUpper === 'MISSED_RUNAWAY') { statusBadgeClass = 'missed_runaway'; statusIcon = '🏃'; }

      const optionsText = t.options_summary || 'No options plan defined';
      const isOptionsActionable = Boolean(t.options_actionable && optionsText !== 'No options plan defined' && !optionsText.includes('None'));
      const optionsBadgeHtml = isOptionsActionable
        ? `<span class="pill" style="font-size:9.5px; padding:1px 5px; background:rgba(16,185,129,0.2); border:1px solid rgba(16,185,129,0.5); color:var(--emerald-light); font-weight:700; margin-right:5px;">⚡ ACTIONABLE</span>`
        : '';

      const pnlHtml = '';

      const ideasCount = (t.trade_ideas || []).length;
      const ideasBtnBg = isIdeasOpen ? 'background:rgba(6,182,212,0.3); border-color:var(--cyan-glow);' : 'background:rgba(6,182,212,0.12); border-color:rgba(6,182,212,0.4);';

      const drawerHtml = isIdeasOpen ? `
        <tr class="trade-ideas-row">
          <td colspan="10" style="padding:14px 18px; background:rgba(8,13,22,0.6); border-top:1px dashed rgba(6,182,212,0.35); border-bottom:1px solid var(--border);">
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; flex-wrap:wrap; gap:8px;">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:15px;">💡</span>
                <span style="font-size:13px; font-weight:800; color:var(--text-main);">Tactical Trade Ideas & Action Gameplan for ${sym}</span>
                ${isOpenPos ? `<span class="pill cyan" style="font-size:9.5px; padding:1px 6px;">💼 Active Position (${posQty} shs @ $${posAvg.toFixed(2)})</span>` : `<span class="pill green" style="font-size:9.5px; padding:1px 6px;">🎯 Stalking Setup</span>`}
              </div>
              <div style="display:flex; align-items:center; gap:8px;">
                <button class="btn secondary" onclick="AppSwing.openPlanExecution('${sym}', '${t.date}')" style="padding:2px 8px; font-size:11px; color:var(--blue); cursor:pointer;">💬 Consult Copilot</button>
                <button class="btn secondary" onclick="AppWatchlist.toggleTradeIdeas('${sym}')" style="padding:2px 8px; font-size:11px; font-weight:700;">✕ Close</button>
              </div>
            </div>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:10px;">
              ${(t.trade_ideas || []).map(idea => `
                <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:10px; display:flex; flex-direction:column; gap:5px;">
                  <div style="display:flex; align-items:center; justify-content:space-between;">
                    <span style="font-weight:700; font-size:11.5px; color:var(--text-main);">${idea.title}</span>
                    <span class="pill ${idea.color || 'cyan'}" style="font-size:9px; padding:1px 5px; font-weight:800;">${idea.badge}</span>
                  </div>
                  <p style="font-size:11px; color:var(--text-muted); line-height:1.45; margin:0;">${idea.action}</p>
                  ${idea.metrics ? `<div style="font-family:'JetBrains Mono',monospace; font-size:10px; color:var(--cyan-glow); margin-top:2px;">${idea.metrics}</div>` : ''}
                </div>
              `).join('')}
            </div>
          </td>
        </tr>
      ` : '';

      return `
        <tr ${isOpenPos ? 'style="background:rgba(6,182,212,0.03);"' : ''}>
          <td>
            <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
              <span class="ticker-cell-sym" onclick="AppSwing.openReportModal('${t.date}', '${t.ticker}')" style="cursor:pointer; color:#38bdf8;" title="Click to open ${t.ticker} Dossier">${t.ticker}</span>
              ${isOpenPos ? `<span class="pill cyan" style="font-size:9px; padding:1px 5px; font-weight:800;" title="You hold an active position in ${t.ticker}">💼 ${posQty} SHS</span>` : ''}
              ${t.conviction ? `<span class="pill" style="font-size:9.5px; padding:1px 5px;">${t.conviction}/10</span>` : ''}
              <button class="btn secondary" onclick="AppSwing.openReportModal('${t.date}', '${t.ticker}')" style="padding:2px 8px; font-size:11px; font-weight:700; background:rgba(6,182,212,0.15); border:1px solid rgba(6,182,212,0.4); color:var(--cyan-glow); cursor:pointer; border-radius:4px; display:inline-flex; align-items:center; gap:4px;" title="Open ${t.ticker} Research Dossier">
                📑 Dossier
              </button>
            </div>
          </td>
          <td>
            <span class="pill" style="font-size:11px;">${t.date || 'N/A'}</span>
          </td>
          <td>
            <span style="font-family:'JetBrains Mono',monospace; font-weight:800; color:var(--cyan-glow); font-size:13.5px;">
              $${spot > 0 ? spot.toFixed(2) : '--.--'}
            </span>
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
              <span style="color:var(--cyan-glow); font-weight:700;">$${t2 > 0 ? t2.toFixed(2) : '--'}</span>
            </span>
          </td>
          <td>
            <span class="status-badge-lg ${statusBadgeClass}">${statusIcon} ${statusUpper}</span>
          </td>
          <td style="max-width:260px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${optionsText}">
            ${optionsBadgeHtml}<span style="font-size:11.5px; color:var(--text-muted);">${optionsText}</span>
          </td>
          <td>
            <div style="display:flex; align-items:center; gap:5px;">
              <button class="btn secondary" onclick="AppWatchlist.toggleTradeIdeas('${t.ticker}')" style="padding:3px 8px; font-size:11px; font-weight:700; color:var(--cyan-glow); ${ideasBtnBg}" title="View Actionable Trade Ideas for ${t.ticker}">
                💡 Ideas (${ideasCount})
              </button>
              <button class="btn secondary" onclick="AppWatchlist.deleteTarget('${t.ticker}', '${t.date}')" style="padding:3px 7px; font-size:11px; background:rgba(244,63,94,0.15); border-color:rgba(244,63,94,0.4); color:var(--rose-light);" title="Untrack ${t.ticker}">
                🗑️
              </button>
            </div>
          </td>
        </tr>
        ${drawerHtml}
      `;
    }).join('');

    container.innerHTML = `
      <div class="watchlist-table-wrap">
        <table class="watchlist-table">
          <thead>
            <tr>
              <th onclick="AppWatchlist.setSort('ticker')">TICKER ${sortIndicator('ticker')}</th>
              <th onclick="AppWatchlist.setSort('date')">DATE ${sortIndicator('date')}</th>
              <th onclick="AppWatchlist.setSort('last_price')">LIVE SPOT ${sortIndicator('last_price')}</th>
              <th>ENTRY ZONE</th>
              <th onclick="AppWatchlist.setSort('distance_to_entry_pct')">DIST % ${sortIndicator('distance_to_entry_pct')}</th>
              <th onclick="AppWatchlist.setSort('tactical_stop')">TACTICAL STOP ${sortIndicator('tactical_stop')}</th>
              <th>TARGET 1 / 2</th>
              <th onclick="AppWatchlist.setSort('status')">STATUS ${sortIndicator('status')}</th>
              <th>OPTIONS STRUCTURE</th>
              <th>ACTIONS</th>
            </tr>
          </thead>
          <tbody>
            ${rowsHtml}
          </tbody>
        </table>
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

        return `
          <div class="target-card">
            <div class="target-header">
              <div class="target-sym">
                <span>${t.ticker}</span>
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
              <div class="level-col"><label>Entry Zone</label><span>$${Number(t.entry_zone_low||0).toFixed(2)} - $${Number(t.entry_zone_high||0).toFixed(2)}</span></div>
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
        <div style="color:var(--text-muted); font-size:12.5px; text-align:center; padding:24px; background:rgba(8,13,22,0.4); border-radius:8px; border:1px dashed var(--border-subtle);">
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

      // Status & Hit Outcome Badge
      let statusBadge = '';
      if (st === 'IN_ZONE') {
        statusBadge = `<span class="badge in_zone">🎯 IN ENTRY ZONE (${distStr})</span>`;
      } else if (st === 'IN_TRADE') {
        statusBadge = `<span class="badge in_trade" style="background:#e0e7ff; color:#3730a3; border:1px solid #c7d2fe; font-weight:800;">🎯 IN TRADE</span>`;
      } else if (st.includes('TARGET') || st === 'TARGET_HIT') {
        const tVal = t.target_1 ? `$${Number(t.target_1).toFixed(2)}` : '';
        statusBadge = `<span class="badge target_hit">🏁 TARGET 1 HIT ${tVal}</span>`;
      } else if (st === 'MISSED_RUNAWAY') {
        statusBadge = `<span class="badge runaway">🚀 MISSED RUNAWAY (${distStr})</span>`;
      } else if (st === 'INVALIDATED' || st === 'STOP_BREACHED') {
        statusBadge = `<span class="badge invalid">🛑 STOP BREACHED</span>`;
      } else {
        statusBadge = `<span class="badge stalking">⏳ STALKING (${distStr ? distStr + ' to zone' : 'Active'})</span>`;
      }

      // Action Directive Banner (What should I take action on)
      let actionHtml = '';
      if (st === 'IN_ZONE') {
        const optNote = t.options_actionable ? `Execute ${t.options_structure || 'spread'} at market, or set limit buy in zone.` : `Confirm limit order fill in $${Number(t.entry_zone_low||0).toFixed(2)}-$${Number(t.entry_zone_high||0).toFixed(2)} zone.`;
        actionHtml = `
          <div style="background:#ecfdf5; border:1px solid #a7f3d0; border-radius:6px; padding:7px 12px; margin-top:8px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">
            <span style="color:#065f46; font-weight:700; font-size:12px;">🔥 TAKE ACTION: Spot is in buy zone! ${optNote}</span>
            <button class="btn" onclick="AppSwing.openPlanExecution('${t.ticker}', '${t.date}')" style="padding:4px 10px; font-size:11px; font-weight:700; background:#059669; color:#ffffff; border-color:#059669; cursor:pointer;" title="Open Trade Plan & Copilot Execution">⚡ Plan Execution</button>
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
      } else if (st === 'INVALIDATED' || st === 'STOP_BREACHED') {
        actionHtml = `
          <div style="background:#fef2f2; border:1px solid #fecaca; border-radius:6px; padding:6px 12px; margin-top:8px; font-size:11.5px; color:#991b1b;">
            <strong>🛑 TAKE ACTION:</strong> Structural floor breached. Cancel all resting limit orders. Trade thesis is invalidated.
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
        <div id="trade-ideas-drawer-${sym}" style="margin-top:10px; background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:12px;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <div style="display:flex; align-items:center; gap:6px;">
              <span style="font-size:14px;">💡</span>
              <strong style="font-size:12.5px; color:var(--text-main);">Tactical Trade Ideas for ${sym}</strong>
              <span class="pill" style="font-size:9.5px; padding:1px 5px; font-weight:700;">Entry Setup</span>
            </div>
            <button class="btn secondary" onclick="AppWatchlist.toggleTradeIdeas('${sym}')" style="padding:2px 7px; font-size:10px;">✕ Close</button>
          </div>
          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:8px;">
            ${ideas.map(idea => `
              <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:8px 10px; font-size:11.5px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:3px;">
                  <strong style="color:var(--blue);">${idea.title}</strong>
                  <span class="pill" style="font-size:9px; padding:1px 4px; font-weight:700;">${idea.category}</span>
                </div>
                <div style="color:var(--text-main); line-height:1.35; margin-bottom:4px;">${idea.action}</div>
                ${(idea.metrics || idea.rationale) ? `<div style="font-family:'JetBrains Mono',monospace; font-size:10.5px; color:var(--cyan-glow); margin-top:3px;">${idea.metrics || idea.rationale}</div>` : ''}
              </div>
            `).join('')}
          </div>
        </div>
      ` : '';

      return `
        <div class="target-card" id="radar-card-${sym}" style="background:var(--bg-surface); border:1px solid var(--border); border-radius:10px; padding:12px 16px; margin-bottom:10px; box-shadow:var(--shadow-card);">
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
                <span>Limit: <strong style="color:var(--text-main); font-family:'JetBrains Mono',monospace;">$${Number(t.entry_zone_low||0).toFixed(2)}–$${Number(t.entry_zone_high||0).toFixed(2)}</strong></span>
                <span style="opacity:0.4;">•</span>
                <span>Stop: <strong style="color:var(--rose); font-family:'JetBrains Mono',monospace;">$${Number(t.tactical_stop||0).toFixed(2)}</strong></span>
                <span style="opacity:0.4;">•</span>
                <span>T1: <strong style="color:var(--emerald); font-family:'JetBrains Mono',monospace;">$${Number(t.target_1||0).toFixed(2)}</strong></span>
                ${t.target_2 ? `<span style="opacity:0.4;">•</span><span>T2: <strong style="color:var(--blue); font-family:'JetBrains Mono',monospace;">$${Number(t.target_2).toFixed(2)}</strong></span>` : ''}
              </div>
            </div>

            <!-- Right: Status / Hit Outcome Badge & Quick Buttons -->
            <div style="display:flex; align-items:center; gap:6px; flex-shrink:0;">
              ${statusBadge}
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
  }
};

// Self-initialize on DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    if (window.AppWatchlist) window.AppWatchlist.loadWatchlist();
  });
} else {
  if (window.AppWatchlist) window.AppWatchlist.loadWatchlist();
}

