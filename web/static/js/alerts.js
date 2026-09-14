/**
 * Dedicated TradingView Alerts Desk Controller
 * Features: High-Capacity Ingestion (1000+), Calendar Session Filter,
 * Client-Side Pagination, Wyckoff Stage Extraction & Tactical Details Modal.
 */
window.AppAlerts = {
  _rawAlerts: [],
  _activeSubTab: 'tv',
  _filterAction: 'ALL',
  _aiFilter: 'ALL',
  _dateFilter: 'ALL',
  _searchQuery: '',
  _sortField: 'timestamp',
  _sortAsc: false,
  _limit: 1000,
  _currentPage: 1,
  _pageSize: 25,
  _calendarMonth: (new Date()).getMonth(),
  _calendarYear: (new Date()).getFullYear(),
  _availableDates: [],
  _isFetching: false,
  _currentModalAlert: null,
  _isCopilotDrawerOpen: false,
  _alertCopilotSessionId: null,
  _alertCopilotHistory: [],
  _alertCopilotAbortController: null,
  _alertCopilotReader: null,
  _isAlertCopilotStreaming: false,
  _lastCopilotTicker: null,
  _pnlCurrentSession: 'ALL',
  _pnlFilterTab: 'ALL',
  _pnlSearchQuery: '',
  _pnlAlertIndex: {},

  setAiFilter(filterVal) {
    this._aiFilter = filterVal;
    this._currentPage = 1;
    document.querySelectorAll('.alerts-ai-filter-btn').forEach(btn => {
      const bAi = btn.getAttribute('data-ai');
      if (bAi === filterVal) {
        btn.classList.add('active');
        btn.style.background = 'var(--text-main)';
        btn.style.color = 'var(--bg-surface)';
      } else {
        btn.classList.remove('active');
        btn.style.background = '';
        btn.style.color = '';
      }
    });
    this.renderTable();
  },

  switchSubTab(tab) {
    this._activeSubTab = tab;
    const tvView = document.getElementById('alerts-view-tv');
    const schwabView = document.getElementById('alerts-view-schwab');
    const btnTv = document.getElementById('btn-subtab-tv');
    const btnSchwab = document.getElementById('btn-subtab-schwab');

    if (tab === 'schwab') {
      if (tvView) tvView.style.display = 'none';
      if (schwabView) schwabView.style.display = 'flex';
      if (btnTv) {
        btnTv.className = 'btn secondary alerts-subtab-btn';
        btnTv.style.background = '';
        btnTv.style.color = '';
      }
      if (btnSchwab) {
        btnSchwab.className = 'btn alerts-subtab-btn active';
        btnSchwab.style.background = 'var(--text-main)';
        btnSchwab.style.color = 'var(--bg-surface)';
      }
      if (window.AppSwing && typeof window.AppSwing.loadSchwabScreener === 'function') {
        window.AppSwing.loadSchwabScreener();
      }
    } else {
      if (tvView) tvView.style.display = 'flex';
      if (schwabView) schwabView.style.display = 'none';
      if (btnTv) {
        btnTv.className = 'btn alerts-subtab-btn active';
        btnTv.style.background = 'var(--text-main)';
        btnTv.style.color = 'var(--bg-surface)';
      }
      if (btnSchwab) {
        btnSchwab.className = 'btn secondary alerts-subtab-btn';
        btnSchwab.style.background = '';
        btnSchwab.style.color = '';
      }
      this.loadAlerts();
    }
  },

  setLimit(val) {
    this._limit = parseInt(val, 10) || 1000;
    this._currentPage = 1;
    this.loadAlerts();
  },

  setFilter(action) {
    this._filterAction = (action || 'ALL').toUpperCase();
    this._currentPage = 1;
    document.querySelectorAll('.alerts-filter-btn').forEach(b => {
      const a = b.getAttribute('data-action') || 'ALL';
      b.className = `btn secondary alerts-filter-btn ${a === this._filterAction ? 'active' : ''}`;
    });
    this.renderTable();
  },

  setDateFilter(date) {
    this.selectCalendarDate(date || 'ALL');
  },

  toggleCalendarPicker(forceOpen = null) {
    const popover = document.getElementById('tv-alerts-calendar-popover');
    if (!popover) return;
    const shouldOpen = (forceOpen !== null) ? forceOpen : (popover.style.display === 'none' || !popover.style.display);
    if (shouldOpen) {
      popover.style.display = 'block';
      this.renderCalendar();
    } else {
      popover.style.display = 'none';
    }
  },

  closeCalendarPicker() {
    const popover = document.getElementById('tv-alerts-calendar-popover');
    if (popover) popover.style.display = 'none';
  },

  prevCalendarMonth(event) {
    if (event) event.stopPropagation();
    this._calendarMonth--;
    if (this._calendarMonth < 0) {
      this._calendarMonth = 11;
      this._calendarYear--;
    }
    this.renderCalendar();
  },

  nextCalendarMonth(event) {
    if (event) event.stopPropagation();
    this._calendarMonth++;
    if (this._calendarMonth > 11) {
      this._calendarMonth = 0;
      this._calendarYear++;
    }
    this.renderCalendar();
  },

  async selectCalendarDate(dateStr) {
    this._dateFilter = dateStr || 'ALL';
    this._currentPage = 1;
    this.updateCalendarTriggerLabel();
    this.renderCalendar();
    this.closeCalendarPicker();

    const dateSelect = document.getElementById('tv-alerts-date-select');
    if (dateSelect) dateSelect.value = this._dateFilter;

    await this.loadAlerts();
  },

  selectLatestCalendarDate() {
    if (this._availableDates && this._availableDates.length > 0) {
      this.selectCalendarDate(this._availableDates[0]);
    } else {
      this.selectCalendarDate('ALL');
    }
  },

  updateCalendarTriggerLabel() {
    const labelEl = document.getElementById('tv-alerts-calendar-label');
    const badgeEl = document.getElementById('tv-alerts-calendar-badge');
    const btnEl = document.getElementById('tv-alerts-calendar-btn');
    if (!labelEl) return;

    if (this._dateFilter === 'ALL') {
      labelEl.innerText = 'All Sessions';
      if (badgeEl) {
        badgeEl.innerText = `${this._availableDates.length} Days`;
        badgeEl.className = 'pill cyan';
      }
      if (btnEl) btnEl.classList.remove('has-filter');
    } else {
      labelEl.innerText = `Session: ${this._dateFilter}`;
      if (badgeEl) {
        const count = this._rawAlerts.filter(a => (a.date || (a.timestamp ? a.timestamp.substring(0, 10) : '')) === this._dateFilter).length;
        badgeEl.innerText = `${count} Alert${count === 1 ? '' : 's'}`;
        badgeEl.className = 'pill green';
      }
      if (btnEl) btnEl.classList.add('has-filter');
    }
  },

  renderCalendar() {
    const monthYearEl = document.getElementById('tv-alerts-calendar-month-year');
    const gridEl = document.getElementById('tv-alerts-calendar-days');
    if (!gridEl) return;

    const monthNames = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    if (monthYearEl) {
      monthYearEl.innerText = `${monthNames[this._calendarMonth]} ${this._calendarYear}`;
    }

    const alertCountByDate = {};
    (this._rawAlerts || []).forEach(a => {
      const d = a.date || (a.timestamp ? a.timestamp.substring(0, 10) : '');
      if (d) {
        alertCountByDate[d] = (alertCountByDate[d] || 0) + 1;
      }
    });

    const firstDayIndex = new Date(this._calendarYear, this._calendarMonth, 1).getDay();
    const daysInMonth = new Date(this._calendarYear, this._calendarMonth + 1, 0).getDate();
    const prevMonthDays = new Date(this._calendarYear, this._calendarMonth, 0).getDate();

    const todayStr = new Date().toISOString().substring(0, 10);
    let cellsHtml = '';

    for (let i = firstDayIndex - 1; i >= 0; i--) {
      cellsHtml += `<div class="calendar-day-cell other-month">${prevMonthDays - i}</div>`;
    }

    for (let day = 1; day <= daysInMonth; day++) {
      const mStr = String(this._calendarMonth + 1).padStart(2, '0');
      const dStr = String(day).padStart(2, '0');
      const fullDateStr = `${this._calendarYear}-${mStr}-${dStr}`;

      const count = alertCountByDate[fullDateStr] || 0;
      const hasAlerts = count > 0 || (this._availableDates || []).includes(fullDateStr);
      const isSelected = (this._dateFilter === fullDateStr);
      const isToday = (fullDateStr === todayStr);

      let classes = ['calendar-day-cell'];
      if (hasAlerts) classes.push('has-alerts');
      if (isSelected) classes.push('is-selected');
      if (isToday) classes.push('is-today');

      const tooltip = hasAlerts ? `${fullDateStr}: ${count > 0 ? count : 'Available'} alerts` : fullDateStr;

      cellsHtml += `
        <div class="${classes.join(' ')}" onclick="AppAlerts.selectCalendarDate('${fullDateStr}')" title="${tooltip}">
          <span>${day}</span>
          ${hasAlerts ? '<span class="alert-dot"></span>' : ''}
        </div>
      `;
    }

    const totalCells = firstDayIndex + daysInMonth;
    const remaining = (7 - (totalCells % 7)) % 7;
    for (let i = 1; i <= remaining; i++) {
      cellsHtml += `<div class="calendar-day-cell other-month">${i}</div>`;
    }

    gridEl.innerHTML = cellsHtml;
  },

  setSearch(q) {
    this._searchQuery = (q || '').trim().toLowerCase();
    this._currentPage = 1;
    this.renderTable();
  },

  setSort(field) {
    if (this._sortField === field) {
      this._sortAsc = !this._sortAsc;
    } else {
      this._sortField = field;
      this._sortAsc = (field === 'symbol' || field === 'action' || field === 'stage');
    }
    this.renderTable();
  },

  setPage(p) {
    this._currentPage = p;
    this.renderTable();
  },

  prevPage() {
    if (this._currentPage > 1) {
      this._currentPage--;
      this.renderTable();
    }
  },

  nextPage(maxPages) {
    if (this._currentPage < maxPages) {
      this._currentPage++;
      this.renderTable();
    }
  },

  setPageSize(val) {
    if (val === 'ALL') {
      this._pageSize = 999999;
    } else {
      this._pageSize = parseInt(val, 10) || 25;
    }
    this._currentPage = 1;
    this.renderTable();
  },

  async refreshAlerts() {
    const pill = document.getElementById('tv-alerts-count-pill');
    if (pill) pill.innerText = 'Refreshing...';
    await this.loadAlerts();
  },

  async pollGmailNow() {
    try {
      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast('Checking Gmail for fresh TradingView alerts (main.py --once)...', 'info');
      }
      await window.AppApi.checkGmailAlerts();
      setTimeout(() => {
        this.loadAlerts();
      }, 3500);
    } catch (e) {
      console.error('Failed to poll Gmail alerts:', e);
      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast(`Gmail Poll Error: ${e.message}`, 'error');
      }
    }
  },

  parsePayload(a) {
    if (!a) return {};
    let p = a.raw_payload || a.raw_alert || {};
    if (typeof p === 'string') {
      try {
        p = JSON.parse(p);
      } catch (e) {
        p = {};
      }
    }
    return p;
  },

  async loadAlerts(keepPage = false) {
    if (this._isFetching) return;
    this._isFetching = true;
    const savedPage = keepPage ? this._currentPage : null;
    const tableBody = document.getElementById('tv-alerts-table-body');
    const countPill = document.getElementById('tv-alerts-count-pill');
    try {
      const dateParam = (this._dateFilter && this._dateFilter !== 'ALL') ? this._dateFilter : null;
      const data = await window.AppApi.getAlertsHistory(this._limit, dateParam);
      this._rawAlerts = (data && data.alerts) ? data.alerts : [];

      const total = this._rawAlerts.length;
      const calls = this._rawAlerts.filter(a => (a.action || '').toUpperCase().includes('CALL')).length;
      const puts = this._rawAlerts.filter(a => (a.action || '').toUpperCase().includes('PUT')).length;
      const exits = this._rawAlerts.filter(a => (a.action || '').toUpperCase().includes('EXIT') || (a.action || '').toUpperCase().includes('CUT')).length;

      const cAll = document.getElementById('count-alert-all');
      const cCalls = document.getElementById('count-alert-calls');
      const cPuts = document.getElementById('count-alert-puts');
      const cExits = document.getElementById('count-alert-exits');

      if (cAll) cAll.innerText = total;
      if (cCalls) cCalls.innerText = calls;
      if (cPuts) cPuts.innerText = puts;
      if (cExits) cExits.innerText = exits;

      if (countPill) {
        countPill.className = 'pill green';
        countPill.innerText = `${total} Alerts Logged`;
      }

      const distinctDates = [...new Set(this._rawAlerts.map(a => a.date || (a.timestamp ? a.timestamp.substring(0, 10) : 'Today')))].filter(Boolean).sort().reverse();
      if (this._availableDates.length === 0 || distinctDates.length >= this._availableDates.length) {
        this._availableDates = distinctDates;
      }

      if (distinctDates.length > 0) {
        const refDate = (this._dateFilter !== 'ALL') ? this._dateFilter : distinctDates[0];
        if (refDate && refDate.includes('-')) {
          const parts = refDate.split('-');
          this._calendarYear = parseInt(parts[0], 10);
          this._calendarMonth = parseInt(parts[1], 10) - 1;
        }
      }

      this.updateCalendarTriggerLabel();
      this.renderCalendar();

      const dateSelect = document.getElementById('tv-alerts-date-select');
      if (dateSelect) {
        const currentVal = this._dateFilter;
        dateSelect.innerHTML = `<option value="ALL">📅 All Session Dates (${distinctDates.length})</option>` +
          distinctDates.map(d => `<option value="${d}" ${d === currentVal ? 'selected' : ''}>📅 Session: ${d}</option>`).join('');
      }

      const sessPill = document.getElementById('tv-alerts-session-pill');
      if (sessPill) {
        if (this._dateFilter !== 'ALL') {
          sessPill.innerText = `Session: ${this._dateFilter}`;
        } else if (distinctDates.length > 0) {
          sessPill.innerText = `Latest: ${distinctDates[0]}`;
        }
      }

      if (savedPage) this._currentPage = savedPage;
      this.renderTable();
    } catch (e) {
      console.error('Error loading alerts:', e);
      if (tableBody) {
        tableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--red); padding:20px;">Failed to load alerts: ${e.message}</td></tr>`;
      }
      if (countPill) {
        countPill.className = 'pill red';
        countPill.innerText = 'Load Error';
      }
    } finally {
      this._isFetching = false;
    }
  },

  renderTable() {
    const tableBody = document.getElementById('tv-alerts-table-body');
    if (!tableBody) return;

    this.buildAlertPnlIndex();

    let list = [...this._rawAlerts];

    if (this._dateFilter !== 'ALL') {
      list = list.filter(a => {
        const d = a.date || (a.timestamp ? a.timestamp.substring(0, 10) : '');
        return d === this._dateFilter;
      });
    }

    if (this._filterAction !== 'ALL') {
      list = list.filter(a => {
        const act = (a.action || '').toUpperCase();
        if (this._filterAction === 'ENTER_CALLS') return act.includes('CALL');
        if (this._filterAction === 'ENTER_PUTS') return act.includes('PUT');
        if (this._filterAction === 'EXIT') return act.includes('EXIT') || act.includes('CUT');
        return act === this._filterAction;
      });
    }

    if (this._searchQuery) {
      const q = this._searchQuery;
      list = list.filter(a => {
        const p = this.parsePayload(a);
        const sym = (a.symbol || p.ticker || '').toLowerCase();
        const act = (a.action || p.action || '').toLowerCase();
        const setup = (a.setup || p.setup || '').toLowerCase();
        const strat = (a.strategy || p.strategy || '').toLowerCase();
        const plan = (a.plan || p.plan || '').toLowerCase();
        const comp = window.AppUtils ? (window.AppUtils.getCompanyName(sym) || '').toLowerCase() : '';
        return sym.includes(q) || act.includes(q) || setup.includes(q) || strat.includes(q) || comp.includes(q) || plan.includes(q);
      });
    }

    if (this._aiFilter !== 'ALL') {
      list = list.filter(a => {
        const dec = (a.llm_decision || '').toUpperCase();
        if (this._aiFilter === 'ACTIONABLE') {
          return dec.includes('PASS') || dec.includes('GO') || dec.includes('ENTER') || dec.includes('TAKE');
        }
        if (this._aiFilter === 'WATCH') {
          return dec.includes('WATCH') || dec.includes('STALK');
        }
        if (this._aiFilter === 'CUT') {
          return dec.includes('CUT') || dec.includes('STAND') || dec.includes('EXIT');
        }
        return true;
      });
    }

    // Update Counter Badges for AI categories
    let countActionable = 0, countWatch = 0, countCut = 0;
    this._rawAlerts.forEach(a => {
      const dec = (a.llm_decision || '').toUpperCase();
      if (dec.includes('PASS') || dec.includes('GO') || dec.includes('ENTER') || dec.includes('TAKE')) countActionable++;
      else if (dec.includes('WATCH') || dec.includes('STALK')) countWatch++;
      else if (dec.includes('CUT') || dec.includes('STAND') || dec.includes('EXIT')) countCut++;
    });
    const elAct = document.getElementById('count-alert-actionable');
    const elWat = document.getElementById('count-alert-watch');
    const elCut = document.getElementById('count-alert-cut');
    if (elAct) elAct.innerText = countActionable;
    if (elWat) elWat.innerText = countWatch;
    if (elCut) elCut.innerText = countCut;

    // Sort
    list.sort((a, b) => {
      let va = a[this._sortField];
      let vb = b[this._sortField];
      if (this._sortField === 'alert_price') {
        va = parseFloat(va) || 0;
        vb = parseFloat(vb) || 0;
      } else {
        va = (va || '').toString().toLowerCase();
        vb = (vb || '').toString().toLowerCase();
      }
      if (va < vb) return this._sortAsc ? -1 : 1;
      if (va > vb) return this._sortAsc ? 1 : -1;
      return 0;
    });

    const totalCount = list.length;
    const totalPages = Math.max(1, Math.ceil(totalCount / this._pageSize));
    if (this._currentPage > totalPages) this._currentPage = totalPages;
    if (this._currentPage < 1) this._currentPage = 1;

    const startIdx = (this._currentPage - 1) * this._pageSize;
    const endIdx = Math.min(totalCount, startIdx + this._pageSize);
    const pagedList = list.slice(startIdx, endIdx);

    this.renderPaginationToolbar(totalCount, startIdx, endIdx, totalPages);

    if (totalCount === 0) {
      tableBody.innerHTML = `
        <tr>
          <td colspan="8" style="text-align:center; color:var(--text-muted); padding:36px; font-size:13px;">
            No TradingView alerts found matching the current filters.
          </td>
        </tr>
      `;
      return;
    }

    let rowsHtml = '';
    let lastDateGroup = null;
    let hasPendingOnPage = false;

    pagedList.forEach((a, idx) => {
      const dateKey = a.date || (a.timestamp ? a.timestamp.substring(0, 10) : 'Today');
      
      if (this._dateFilter === 'ALL' && dateKey !== lastDateGroup) {
        lastDateGroup = dateKey;
        const sessData = this.computeSessionTrades(dateKey);
        const k = sessData.kpis;
        const sign = k.aiTakenPnL >= 0 ? '+' : '';
        const col = k.aiTakenPnL >= 0 ? '#10b981' : '#f87171';
        rowsHtml += `
          <tr class="alert-date-group-header" style="background:linear-gradient(90deg, rgba(6,182,212,0.10) 0%, rgba(15,23,42,0.02) 100%); border-top:1px solid var(--border); border-bottom:1px solid var(--border);">
            <td colspan="8" style="padding:8px 14px;">
              <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span style="font-size:11.5px; font-weight:800; font-family:var(--font-mono); color:var(--text-main); background:rgba(6,182,212,0.14); border:1px solid rgba(6,182,212,0.3); padding:2px 8px; border-radius:5px; letter-spacing:0.5px;">
                    📅 SESSION ${dateKey}
                  </span>
                  <span style="font-size:11px; color:var(--text-muted); font-weight:500;">
                    Live Market Stream
                  </span>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                  <span style="font-size:11px; font-weight:700; color:${col}; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.1); padding:2px 8px; border-radius:5px; font-family:var(--font-mono);" title="AI Realized 100-Share P&amp;L">
                    AI P&amp;L: ${sign}$${k.aiTakenPnL.toFixed(2)} (${k.aiTakenWins}/${k.aiTakenTotal} W)
                  </span>
                  <span style="font-size:11px; font-weight:700; color:#38bdf8; background:rgba(6,182,212,0.1); border:1px solid rgba(6,182,212,0.25); padding:2px 8px; border-radius:5px; font-family:var(--font-mono);" title="Losses prevented by AI passing/standing aside">
                    🛡️ Saved: +$${k.aiAvoidedPnL.toFixed(2)}
                  </span>
                  <button class="btn secondary" onclick="event.stopPropagation(); AppAlerts.openPnlModal('${dateKey}')" style="padding:2px 8px; font-size:10.5px; font-weight:800; color:var(--cyan-glow); border-radius:5px; border-color:rgba(6,182,212,0.4);" title="Open detailed Intraday PnL modal for this session">
                    📊 View PnL
                  </button>
                </div>
              </div>
            </td>
          </tr>
        `;
      }

      const payload = this.parsePayload(a);
      const sym = (a.symbol || payload.ticker || '').toUpperCase();
      const compName = window.AppUtils ? (window.AppUtils.getCompanyName(sym) || sym) : sym;
      const act = (a.action || payload.action || payload.event || payload.side || 'ALERT').toUpperCase();
      const price = (a.alert_price || payload.price) ? `$${parseFloat(a.alert_price || payload.price).toFixed(2)}` : '--';

      // 1. Action Badge Styling & Text
      let actionLabel = act.replace(/_/g, ' ');
      let badgeStyle = 'background:rgba(6,182,212,0.12); color:var(--cyan); border:1px solid rgba(6,182,212,0.3);';
      let icon = '⚡';
      if (act.includes('CALL')) {
        badgeStyle = 'background:rgba(16,185,129,0.14); color:#10b981; border:1px solid rgba(16,185,129,0.35);';
        icon = '🟢';
        actionLabel = 'ENTER CALLS';
      } else if (act.includes('PUT')) {
        badgeStyle = 'background:rgba(239,68,68,0.14); color:#ef4444; border:1px solid rgba(239,68,68,0.35);';
        icon = '🔴';
        actionLabel = 'ENTER PUTS';
      } else if (act.includes('EXIT') || act.includes('CUT')) {
        badgeStyle = 'background:rgba(245,158,11,0.14); color:#f59e0b; border:1px solid rgba(245,158,11,0.35);';
        icon = '🚪';
        actionLabel = 'EXIT';
      }

      // 2. Time string
      let timeStr = a.timestamp || a.created_at || '';
      if (timeStr.length >= 19) {
        timeStr = timeStr.substring(11, 19);
      }

      // 3. Tactical Plan & Setup Extraction
      const setupName = a.setup || payload.setup || a.strategy || payload.strategy || 'Intraday';
      let planRaw = a.plan || payload.plan || '';
      if (!planRaw) {
        if (payload.proxy_rr || a.proxy_rr) {
          const rr = payload.proxy_rr || a.proxy_rr;
          const atrs = payload.atrs_up || a.atrs_up;
          planRaw = `Proxy R:R ${rr}${atrs !== undefined ? ` · ATRs Up ${atrs}` : ''}`;
        } else if (payload.act_now) {
          planRaw = payload.act_now;
        }
      }

      let planHtml = '';
      if (planRaw) {
        const heldMatch = planRaw.match(/(?:Held|In)\s*([0-9.]+)/i);
        const stopMatch = planRaw.match(/Stop\s*([0-9.]+)/i);
        const t1Match = planRaw.match(/T1\s*([0-9.]+)/i);
        const rrMatch = planRaw.match(/\(R:R\s*([0-9.]+)\)/i) || planRaw.match(/R:R\s*([0-9.]+)/i);

        if (heldMatch || stopMatch || t1Match) {
          const heldVal = heldMatch ? heldMatch[1] : '';
          const stopVal = stopMatch ? stopMatch[1] : '';
          const t1Val = t1Match ? t1Match[1] : '';
          const rrVal = rrMatch ? rrMatch[1] : '';

          planHtml = `
            <div style="display:flex; flex-direction:column; gap:3px; max-width:225px;">
              <div style="display:flex; align-items:center; gap:5px; font-family:var(--font-mono); font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                ${heldVal ? `<span style="font-weight:700; color:var(--text-main);">${heldVal}</span>` : ''}
                ${stopVal ? `<span style="color:var(--red-light); font-weight:600;" title="Stop Loss">🛑 ${stopVal}</span>` : ''}
                ${t1Val ? `<span style="color:var(--green-light); font-weight:600;" title="Profit Target 1">🎯 ${t1Val}</span>` : ''}
                ${rrVal ? `<span class="badge" style="background:#ecfdf5; color:#065f46; border:1px solid #a7f3d0; font-size:9.5px; padding:1px 4px; border-radius:3px; font-weight:800;">${rrVal}:1</span>` : ''}
              </div>
              <div style="display:flex; align-items:center; gap:4px;">
                <span style="background:var(--bg-subtle); color:var(--text-muted); font-size:9.5px; font-weight:600; padding:1px 5px; border-radius:3px; border:1px solid var(--border); font-family:var(--font-mono); max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${setupName}">
                  ${setupName}
                </span>
              </div>
            </div>
          `;
        } else {
          planHtml = `
            <div style="display:flex; flex-direction:column; gap:3px; max-width:225px;">
              <div style="font-size:11px; font-weight:600; font-family:var(--font-mono); color:var(--text-main); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${planRaw.replace(/"/g, '&quot;')}">
                ${planRaw}
              </div>
              <div>
                <span style="background:var(--bg-subtle); color:var(--text-muted); font-size:9.5px; font-weight:600; padding:1px 5px; border-radius:3px; border:1px solid var(--border); font-family:var(--font-mono);">
                  ${setupName}
                </span>
              </div>
            </div>
          `;
        }
      } else {
        planHtml = `
          <div>
            <span style="background:var(--bg-subtle); color:var(--text-muted); font-size:10px; font-weight:600; padding:2px 6px; border-radius:4px; border:1px solid var(--border); font-family:var(--font-mono);">
              ${setupName}
            </span>
          </div>
        `;
      }

      // 4. Strategy & Vehicle (Clean structured chip — never circular bubble!)
      const strat = a.strategy || payload.strategy || payload.setup || '';
      let optVehicle = payload.option || '';
      if (!optVehicle && a.llm_playbook) {
        const vMatch = a.llm_playbook.match(/Vehicle:\s*([A-Za-z0-9_ -]+)/i);
        if (vMatch) optVehicle = vMatch[1];
      }

      let vehicleHtml = '';
      const rawVeh = (optVehicle || '').trim();
      const rawVehLower = rawVeh.toLowerCase();

      if (rawVehLower.startsWith('manage:') || rawVehLower.includes('scale half')) {
        let cleanPlan = rawVeh.replace(/^manage:\s*/i, '').trim();
        if (cleanPlan.toLowerCase().includes('scale half at t1, trail rest')) {
          cleanPlan = 'Scale ½ @ T1, Trail';
        }
        vehicleHtml = `
          <div style="display:inline-flex; flex-direction:column; align-items:center; justify-content:center; max-width:130px; padding:3px 6px; border-radius:6px; background:var(--bg-subtle); border:1px solid var(--border); line-height:1.2; text-align:center;" title="${rawVeh.replace(/"/g, '&quot;')}">
            <span style="font-size:8.5px; font-weight:800; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px;">MANAGEMENT</span>
            <span style="font-size:10.5px; font-weight:600; color:var(--text-main); margin-top:1px;">${cleanPlan}</span>
          </div>
        `;
      } else if (rawVehLower.includes('0dte') || rawVehLower.includes('atm') || rawVehLower.includes('lotto')) {
        vehicleHtml = `
          <div style="display:inline-flex; flex-direction:column; align-items:center; justify-content:center; max-width:130px; padding:3px 6px; border-radius:6px; background:rgba(6,182,212,0.1); border:1px solid rgba(6,182,212,0.3); line-height:1.2; text-align:center;" title="${rawVeh.replace(/"/g, '&quot;')}">
            <span style="font-size:11px; font-weight:800; color:var(--cyan);">0DTE · ATM</span>
            <span style="font-size:9.5px; font-weight:600; color:var(--text-muted); margin-top:1px;">Lotto 1-OTM</span>
          </div>
        `;
      } else if (rawVehLower.includes('debit spread') || rawVehLower.includes('iv rich')) {
        vehicleHtml = `
          <div style="display:inline-flex; flex-direction:column; align-items:center; justify-content:center; max-width:130px; padding:3px 6px; border-radius:6px; background:rgba(168,85,247,0.1); border:1px solid rgba(168,85,247,0.3); line-height:1.2; text-align:center;" title="${rawVeh.replace(/"/g, '&quot;')}">
            <span style="font-size:11px; font-weight:800; color:#c084fc;">Debit Spread</span>
            <span style="font-size:9.5px; font-weight:600; color:var(--text-muted); margin-top:1px;">IV Rich</span>
          </div>
        `;
      } else if (rawVeh.toUpperCase().includes('CALL')) {
        vehicleHtml = `
          <span class="badge" style="background:rgba(16,185,129,0.12); color:#10b981; border:1px solid rgba(16,185,129,0.3); font-weight:800; border-radius:6px; padding:3px 8px; font-size:10.5px; text-transform:none;">
            🟢 ${rawVeh}
          </span>
        `;
      } else if (rawVeh.toUpperCase().includes('PUT')) {
        vehicleHtml = `
          <span class="badge" style="background:rgba(239,68,68,0.12); color:#ef4444; border:1px solid rgba(239,68,68,0.3); font-weight:800; border-radius:6px; padding:3px 8px; font-size:10.5px; text-transform:none;">
            🔴 ${rawVeh}
          </span>
        `;
      } else if (rawVeh) {
        vehicleHtml = `
          <span class="badge" style="background:var(--bg-subtle); color:var(--text-main); border:1px solid var(--border); font-weight:600; border-radius:6px; padding:3px 7px; font-size:10.5px; text-transform:none; max-width:130px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${rawVeh.replace(/"/g, '&quot;')}">
            ${rawVeh}
          </span>
        `;
      } else if (strat && strat !== 'Intraday' && strat !== 'Daily Radar') {
        vehicleHtml = `
          <span class="badge" style="background:var(--bg-subtle); color:var(--text-muted); border:1px solid var(--border); font-weight:600; border-radius:6px; padding:3px 7px; font-size:10.5px; text-transform:none;">
            ${strat}
          </span>
        `;
      } else {
        vehicleHtml = `<span style="color:var(--text-muted); font-size:12px;">—</span>`;
      }

      // 5. AI Triage (#ponytail) Badge
      const alertId = a.message_id || a.email_id || `row-${startIdx + idx}`;
      let aiBadge = '';
      let decRaw = (a.llm_decision || '').trim();

      // Fallback extraction from playbook if decision is stub / backtick
      if (!decRaw || decRaw === '```' || decRaw === '...' || decRaw === 'AI EVALUATED') {
        if (a.llm_playbook) {
          const pbLines = a.llm_playbook.split('\n').map(l => l.trim().replace(/^\*+|\*+$/g, '')).filter(Boolean);
          for (const l of pbLines) {
            if (l.startsWith('```') || l.startsWith('---')) continue;
            if (/🟢|🔴|⏸|⛔|TAKE|WAIT|STAND|GO|EXIT|PASS/i.test(l)) {
              decRaw = l;
              break;
            }
          }
        }
      }

      if (!decRaw || decRaw === '```' || decRaw === '...' || decRaw === 'AI EVALUATED') {
        hasPendingOnPage = true;
        aiBadge = `
          <div style="display:inline-flex; align-items:center; gap:4px; padding:3px 8px; border-radius:6px; background:rgba(6,182,212,0.08); border:1px dashed rgba(6,182,212,0.4); cursor:pointer;" 
               onclick="event.stopPropagation(); AppAlerts.runLocalResearchSingle('${alertId}', '${sym}')" 
               title="Autonomous background triage active via local LLM. Click to prioritize.">
            <span class="dot pulse cyan" style="width:6px; height:6px; background:var(--cyan); border-radius:50%; box-shadow:0 0 6px var(--cyan); display:inline-block;"></span>
            <span style="font-size:10px; font-weight:800; color:var(--cyan-glow); font-family:var(--font-mono); letter-spacing:0.2px;">Triaging...</span>
          </div>
        `;
      } else {
        let decClean = decRaw.replace(/\*\*/g, '').replace(/\[[A-Z0-9.]+\]/gi, '').trim();
        decClean = decClean.replace(/^[A-Z0-9.]+\s+\d{1,2}:\d{2}\s*(?:AM|PM)?\s*(?:ET)?\s*[-—–]\s*/i, '').trim();

        const upperDec = decClean.toUpperCase();
        let bBg = 'rgba(6,182,212,0.12)';
        let bColor = 'var(--cyan)';
        let bBorder = 'rgba(6,182,212,0.3)';
        let bIcon = '⚡';
        let bLabel = decClean;

        if (upperDec.includes('TAKE CALLS') || upperDec.includes('ENTER CALLS') || upperDec.includes('GO (ENTER CALLS)') || (upperDec.includes('CALLS') && upperDec.includes('GO'))) {
          bBg = 'rgba(16,185,129,0.15)';
          bColor = '#10b981';
          bBorder = 'rgba(16,185,129,0.4)';
          bIcon = '🟢';
          bLabel = 'TAKE CALLS';
        } else if (upperDec.includes('TAKE PUTS') || upperDec.includes('ENTER PUTS') || upperDec.includes('GO (ENTER PUTS)') || (upperDec.includes('PUTS') && upperDec.includes('GO'))) {
          bBg = 'rgba(239,68,68,0.15)';
          bColor = '#ef4444';
          bBorder = 'rgba(239,68,68,0.4)';
          bIcon = '🔴';
          bLabel = 'TAKE PUTS';
        } else if (upperDec.includes('STAND ASIDE') || upperDec.includes('PASS') || upperDec.includes('NO TRADE') || upperDec.includes('DO NOT ENTER')) {
          bBg = 'rgba(239,68,68,0.08)';
          bColor = '#f87171';
          bBorder = 'rgba(239,68,68,0.25)';
          bIcon = '⛔';
          bLabel = upperDec.includes('STAND ASIDE') ? 'STAND ASIDE' : 'PASS';
        } else if (upperDec.includes('WAIT') || upperDec.includes('STALK')) {
          bBg = 'rgba(245,158,11,0.12)';
          bColor = '#f59e0b';
          bBorder = 'rgba(245,158,11,0.35)';
          bIcon = '⏸';
          bLabel = 'WAIT / STALK';
        } else if (upperDec.includes('EXIT')) {
          bBg = 'rgba(245,158,11,0.12)';
          bColor = '#f59e0b';
          bBorder = 'rgba(245,158,11,0.35)';
          bIcon = '🚪';
          bLabel = 'EXIT';
        } else {
          if (bLabel.length > 18) bLabel = bLabel.substring(0, 16) + '…';
        }

        const tooltip = (decRaw || a.llm_playbook || '').replace(/"/g, '&quot;');
        aiBadge = `
          <span class="badge" style="background:${bBg}; color:${bColor}; border:1px solid ${bBorder}; border-radius:6px; padding:3px 8px; font-size:11px; font-weight:800; white-space:nowrap; display:inline-flex; align-items:center; gap:4px; text-transform:none;" title="${tooltip}">
            <span>${bIcon}</span> ${bLabel}
          </span>
        `;
      }

        let pnlChipHtml = '';
        const matchedTrade = this._pnlAlertIndex ? this._pnlAlertIndex[alertId] : null;
        if (matchedTrade) {
          if (matchedTrade.exitAlertId === alertId && matchedTrade.status === 'CLOSED') {
            const pSign = matchedTrade.pnl100 >= 0 ? '+' : '';
            pnlChipHtml = `<div style="margin-top:3px;"><span class="${matchedTrade.pnl100 >= 0 ? 'pnl-badge-pos' : 'pnl-badge-neg'}" style="font-size:9.5px; padding:1px 5px;" title="Matched with entry @ $${matchedTrade.entryPrice.toFixed(2)} (${matchedTrade.duration})">100sh: ${pSign}$${matchedTrade.pnl100.toFixed(2)}</span></div>`;
          } else if (matchedTrade.entryAlertId === alertId) {
            if (matchedTrade.status === 'CLOSED') {
              const pSign = matchedTrade.pnl100 >= 0 ? '+' : '';
              pnlChipHtml = `<div style="margin-top:3px;"><span class="${matchedTrade.pnl100 >= 0 ? 'pnl-badge-pos' : 'pnl-badge-neg'}" style="font-size:9px; padding:1px 4px; opacity:0.85;" title="Closed at $${matchedTrade.exitPrice.toFixed(2)}">➔ Exit: ${pSign}$${matchedTrade.pnl100.toFixed(2)}</span></div>`;
            } else {
              pnlChipHtml = `<div style="margin-top:3px;"><span class="pnl-badge-open" style="font-size:9px; padding:1px 4px;">⚡ Active Open</span></div>`;
            }
          }
        }

      const dayAlertsCount = (this._rawAlerts || []).filter(item => {
        const s = (item.symbol || (item.raw_payload ? item.raw_payload.ticker : '') || '').toUpperCase();
        const d = item.date || (item.timestamp ? item.timestamp.substring(0, 10) : '');
        return s === sym && d === dateKey;
      }).length;

      rowsHtml += `
        <tr style="border-bottom:1px solid var(--border); cursor:pointer; transition: background 0.15s ease;"
            onclick="AppAlerts.openAlertModal('${alertId}')"
            onmouseover="this.style.background='var(--bg-card-hover)'"
            onmouseout="this.style.background=''">
          
          <!-- 1. TIME (ET) -->
          <td style="padding:7px 6px; white-space:nowrap;">
            <div style="font-family:var(--font-mono); font-size:11.5px; font-weight:700; color:var(--text-main);">${timeStr}</div>
            <div style="font-size:9.5px; color:var(--text-muted); font-weight:600;">ET Session</div>
          </td>

          <!-- 2. TICKER & COMPANY (Interactive Card) -->
          <td style="padding:7px 6px; overflow:hidden;">
            <div class="ticker-table-card"
                 onclick="event.stopPropagation(); AppAlerts.openTickerTradesModal('${sym}', '${dateKey}')"
                 title="Click to view all ${dateKey} intraday trades for ${sym} (AI-only P&amp;L)"
                 style="display:inline-flex; flex-direction:column; gap:2px; cursor:pointer; padding:3px 7px; border-radius:6px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); transition:all 0.15s ease;"
                 onmouseover="this.style.borderColor='var(--cyan)'; this.style.background='rgba(6,182,212,0.1)';"
                 onmouseout="this.style.borderColor='rgba(255,255,255,0.08)'; this.style.background='rgba(255,255,255,0.03)';">
              <div style="display:flex; align-items:center; gap:5px;">
                <strong style="font-size:13px; font-weight:800; color:var(--cyan-glow); font-family:var(--font-mono); letter-spacing:0.3px;">${sym}</strong>
                <span style="font-size:9.5px; font-family:var(--font-mono); font-weight:700; color:var(--text-muted); background:rgba(0,0,0,0.3); padding:1px 5px; border-radius:3px; border:1px solid var(--border);" title="${dayAlertsCount} alerts for ${sym} on ${dateKey}">
                  📊 ${dayAlertsCount}
                </span>
              </div>
              <span style="font-size:10px; color:var(--text-muted); max-width:115px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${compName}">
                ${compName}
              </span>
            </div>
          </td>

          <!-- 3. ACTION BADGE -->
          <td style="padding:7px 6px; text-align:center;">
            <span style="padding:3px 6px; border-radius:5px; font-size:10.5px; font-weight:800; display:inline-flex; align-items:center; gap:3px; font-family:var(--font-mono); white-space:nowrap; ${badgeStyle}">
              <span>${icon}</span> ${actionLabel}
            </span>
            ${pnlChipHtml}
          </td>

          <!-- 4. TRIGGER PRICE -->
          <td style="padding:7px 6px; text-align:right;">
            <div style="font-family:var(--font-mono); font-weight:800; font-size:12.5px; color:var(--text-main); white-space:nowrap;">
              ${price}
            </div>
          </td>

          <!-- 5. TACTICAL PLAN & SETUP -->
          <td style="padding:7px 6px; overflow:hidden;">
            ${planHtml}
          </td>

          <!-- 6. STRATEGY & VEHICLE -->
          <td style="padding:7px 6px; text-align:center;">
            ${vehicleHtml}
          </td>

          <!-- 7. AI TRIAGE (#ponytail) -->
          <td style="padding:7px 6px; text-align:center;">
            ${aiBadge}
          </td>

          <!-- 8. ACTIONS -->
          <td style="padding:7px 6px; text-align:center; white-space:nowrap;" onclick="event.stopPropagation()">
            <div style="display:inline-flex; align-items:center; gap:3px;">
              <button class="btn secondary" onclick="AppAlerts.openTickerTradesModal('${sym}', '${dateKey}')" style="padding:3px 6px; font-size:10.5px; font-weight:700; color:var(--cyan-glow); border-radius:4px;" title="View all ${dateKey} trades for ${sym} (AI-only P&amp;L)">
                📊 Trades
              </button>
              <button class="btn secondary" onclick="AppAlerts.openAlertModal('${alertId}')" style="padding:3px 6px; font-size:10.5px; font-weight:700; border-radius:4px;" title="Inspect tactical plan &amp; broker chain">
                🔍 Plan
              </button>
              <button class="btn secondary" onclick="AppAlerts.openChart('${sym}')" style="padding:3px 5px; font-size:10.5px; font-weight:700; border-radius:4px;" title="Open interactive TradingView chart">
                📈
              </button>
              <button class="btn" onclick="AppAlerts.researchTicker('${sym}')" style="padding:3px 6px; font-size:10.5px; font-weight:700; border-radius:4px;" title="Launch 1-Click deep research for ${sym}">
                🚀
              </button>
            </div>
          </td>
        </tr>
      `;
    });

    tableBody.innerHTML = rowsHtml;

    if (hasPendingOnPage) {
      this.startAutoTriagePolling();
    } else {
      this.stopAutoTriagePolling();
    }
  },

  renderPaginationToolbar(totalCount, startIdx, endIdx, totalPages) {
    const infoEl = document.getElementById('tv-alerts-pagination-info');
    const btnsEl = document.getElementById('tv-alerts-pagination-buttons');

    if (infoEl) {
      if (totalCount === 0) {
        infoEl.textContent = 'Showing 0 alerts';
      } else {
        infoEl.textContent = `Showing ${startIdx + 1} - ${endIdx} of ${totalCount} alerts (Page ${this._currentPage} of ${totalPages})`;
      }
    }

    if (btnsEl) {
      let btnsHtml = `
        <button class="btn secondary" 
                onclick="AppAlerts.prevPage()" 
                ${this._currentPage <= 1 ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : ''}
                style="padding:2px 8px; font-size:11px;">
          ◀ Prev
        </button>
      `;

      const startPage = Math.max(1, this._currentPage - 2);
      const endPage = Math.min(totalPages, startPage + 4);

      for (let p = startPage; p <= endPage; p++) {
        const isActive = (p === this._currentPage);
        btnsHtml += `
          <button class="btn ${isActive ? 'primary' : 'secondary'}"
                  onclick="AppAlerts.setPage(${p})"
                  style="padding:2px 8px; font-size:11px; font-weight:${isActive ? '800' : '500'};">
            ${p}
          </button>
        `;
      }

      btnsHtml += `
        <button class="btn secondary" 
                onclick="AppAlerts.nextPage(${totalPages})" 
                ${this._currentPage >= totalPages ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : ''}
                style="padding:2px 8px; font-size:11px;">
          Next ▶
        </button>
      `;

      btnsEl.innerHTML = btnsHtml;
    }
  },

  openAlertModal(id) {
    const a = (this._rawAlerts || []).find((item, idx) => {
      return (item.message_id && item.message_id === id) ||
             (item.email_id && String(item.email_id) === String(id)) ||
             (String(idx) === String(id)) ||
             (`row-${idx}` === id);
    });
    if (!a) return;

    this._currentModalAlert = a;
    const payload = this.parsePayload(a);
    const sym = (a.symbol || payload.ticker || '').toUpperCase();
    const compName = window.AppUtils ? (window.AppUtils.getCompanyName(sym) || sym) : sym;
    const act = (a.action || payload.action || payload.event || payload.side || 'ALERT').toUpperCase();
    const price = (a.alert_price || payload.price) ? `$${parseFloat(a.alert_price || payload.price).toFixed(2)}` : '--';

    const tickerEl = document.getElementById('alert-modal-ticker');
    const compEl = document.getElementById('alert-modal-company');
    const badgeEl = document.getElementById('alert-modal-action-badge');
    const priceEl = document.getElementById('alert-modal-price');
    const timeEl = document.getElementById('alert-modal-time');
    const statusEl = document.getElementById('alert-modal-status-pill');

    if (tickerEl) tickerEl.innerText = sym;
    if (compEl) compEl.innerText = compName;
    if (priceEl) priceEl.innerText = price;
    if (timeEl) timeEl.innerText = `${a.timestamp || a.date || ''} (ET)`;
    if (statusEl) statusEl.innerText = `Status: ${a.status || 'INGESTED'}`;

    if (badgeEl) {
      badgeEl.innerText = act;
      let badgeClass = 'pill cyan';
      if (act.includes('CALL')) badgeClass = 'pill green';
      else if (act.includes('PUT')) badgeClass = 'pill red';
      else if (act.includes('EXIT') || act.includes('CUT')) badgeClass = 'pill amber';
      badgeEl.className = badgeClass;
    }

    const btnChart = document.getElementById('alert-modal-btn-chart');
    const btnResearch = document.getElementById('alert-modal-btn-research');
    const btnCopilot = document.getElementById('alert-modal-btn-copilot');
    if (btnChart) btnChart.onclick = () => this.openChart(sym);
    if (btnResearch) btnResearch.onclick = () => { this.closeAlertModal(); this.researchTicker(sym); };
    if (btnCopilot) btnCopilot.onclick = () => { this.askCopilotFromModal(); };

    const pb = a.llm_playbook || '';
    let ponytailText = '';
    let stopText = '--';
    let targetText = '--';
    let vehicleText = '--';
    let modeText = '--';
    let catalystText = '--';
    let ivRankText = '--';
    let ivPctText = '--';
    let hv30Text = '--';
    let spreadText = '--';
    let earningsText = '--';

    if (pb) {
      const ptMatch = pb.match(/\*\*#ponytail Assessment\*\*:\s*([^\n\r*]+)/i);
      if (ptMatch) ponytailText = ptMatch[1].trim();

      const stopMatch = pb.match(/(?:\*{0,2})(?:Stop|STOP\s*(?:\(close\))?)(?:\*{0,2})\s*:?\s*(?:\*{0,2})\s*\$?([0-9.]+)/i);
      if (stopMatch) stopText = `$${stopMatch[1]}`;

      const targetMatch = pb.match(/(?:\*{0,2})(?:Target|T1|PT1)(?:\*{0,2})\s*:?\s*(?:\*{0,2})\s*\$?([0-9.]+)/i);
      if (targetMatch) targetText = `$${targetMatch[1]}`;

      const vehicleMatch = pb.match(/Vehicle:\s*([A-Za-z0-9_ -]+)/i);
      if (vehicleMatch) vehicleText = vehicleMatch[1].trim();
      else if (/Position\s*Closed|Flat\s*until|STAND\s*ASIDE/i.test(pb)) vehicleText = 'STAND ASIDE / FLAT';

      const modeMatch = pb.match(/\*\*(?:Entry Mode|THE PLAY)\*\*:\s*([^\n\r*]+)/i);
      if (modeMatch) modeText = modeMatch[1].trim();

      const convMatch = pb.match(/Conviction:\s*([0-9]+\/[0-9]+)/i);
      if (convMatch) {
        modeText = modeText !== '--' ? `${convMatch[1]} · ${modeText}` : `Conviction ${convMatch[1]}`;
      }

      const catMatch = pb.match(/\*\*Catalyst\*\*:\s*([^\n\r*]+)/i);
      if (catMatch) catalystText = catMatch[1].trim();

      const earnMatch = pb.match(/\*\*Expected Earnings\*\*:\s*([^\n\r*]+)/i);
      if (earnMatch) earningsText = earnMatch[1].trim();

      const volMatch = pb.match(/\*\*Tastytrade Volatility\*\*:\s*IV Rank\s*([0-9.]+)%?\s*\(IV Percentile\s*([0-9.]+)%?,\s*30d HV\s*([0-9.]+)%?,\s*Spread\s*([0-9.-]+)%?\)/i);
      if (volMatch) {
        ivRankText = `${volMatch[1]}%`;
        ivPctText = `${volMatch[2]}%`;
        hv30Text = `${volMatch[3]}%`;
        spreadText = `${volMatch[4]}%`;
      }
    }

    // Fallbacks from decoded payload or alert
    if (earningsText === '--') earningsText = payload.expected_earnings || (payload.earnings_days !== undefined ? `${payload.earnings_days}d` : '--');
    if (catalystText === '--') catalystText = payload.why_now || a.setup || payload.setup || '--';
    if (ivRankText === '--') ivRankText = payload.iv_rank ? `${payload.iv_rank}%` : (payload.premium || '--');
    if (stopText === '--' && (a.wrong_if || payload.wrong_if)) stopText = `$${a.wrong_if || payload.wrong_if}`;
    if (vehicleText === '--') vehicleText = payload.option || 'SHARES';

    // ⚡ Local Research Card
    const lrVerdictEl = document.getElementById('alert-modal-lr-verdict');
    const lrPonytailEl = document.getElementById('alert-modal-lr-ponytail-text');
    const lrStopEl = document.getElementById('alert-modal-lr-stop');
    const lrTargetEl = document.getElementById('alert-modal-lr-target');
    const lrVehicleEl = document.getElementById('alert-modal-lr-vehicle');
    const lrModeEl = document.getElementById('alert-modal-lr-mode');
    const lrIvEl = document.getElementById('alert-modal-lr-iv');
    const lrEarningsEl = document.getElementById('alert-modal-lr-earnings');
    const lrCatalystEl = document.getElementById('alert-modal-lr-catalyst');

    if (lrVerdictEl) {
      if (a.llm_decision) {
        lrVerdictEl.innerText = a.llm_decision;
        const dUpper = a.llm_decision.toUpperCase();
        if (dUpper.includes('PASS') || dUpper.includes('GO') || dUpper.includes('ENTER')) {
          lrVerdictEl.className = 'pill green';
        } else if (dUpper.includes('WATCH') || dUpper.includes('STALK')) {
          lrVerdictEl.className = 'pill amber';
        } else if (dUpper.includes('CUT') || dUpper.includes('STAND') || dUpper.includes('EXIT')) {
          lrVerdictEl.className = 'pill red';
        } else {
          lrVerdictEl.className = 'pill cyan';
        }
      } else {
        lrVerdictEl.innerText = '⏳ PENDING EVALUATION';
        lrVerdictEl.className = 'pill';
      }
    }
    if (lrPonytailEl) {
      const critiqueText = pb || ponytailText || 'Click "Run Local Research (#ponytail)" above to evaluate floor defense, risk-reward edge, and tactical invalidation via local GPU LLM without scraping.';
      if (window.AppUtils && typeof window.AppUtils.renderMarkdown === 'function') {
        lrPonytailEl.innerHTML = window.AppUtils.renderMarkdown(critiqueText);
      } else {
        lrPonytailEl.innerText = critiqueText;
      }
    }
    if (lrStopEl) lrStopEl.innerText = stopText;
    if (lrTargetEl) lrTargetEl.innerText = targetText;
    if (lrVehicleEl) lrVehicleEl.innerText = vehicleText;
    if (lrModeEl) lrModeEl.innerText = modeText !== '--' ? modeText : (payload.setup || a.setup || 'Market Signal');
    if (lrIvEl) lrIvEl.innerText = ivRankText;
    if (lrEarningsEl) lrEarningsEl.innerText = earningsText;
    if (lrCatalystEl) lrCatalystEl.innerText = catalystText;

    // Card 1: Tactical Setup & Risk
    const planEl = document.getElementById('alert-modal-plan-content');
    const gradeEl = document.getElementById('alert-modal-grade-badge');
    const wrongIfEl = document.getElementById('alert-modal-wrong-if');
    const optionEl = document.getElementById('alert-modal-option');

    const planText = a.plan || payload.plan || payload.act_now || (payload.proxy_rr ? `Proxy R:R ${payload.proxy_rr} · ATRs Up ${payload.atrs_up || '--'}` : (a.setup || 'Tactical trade plan ready for execution.'));
    if (planEl) planEl.innerText = planText;

    const grade = a.grade || payload.grade || '';
    const score = a.score || payload.score || '';
    if (gradeEl) {
      if (grade || score) {
        gradeEl.innerText = `Grade ${grade || '--'} (${score || '--'}/100)`;
        gradeEl.style.display = 'inline-block';
      } else {
        gradeEl.style.display = 'none';
      }
    }
    if (wrongIfEl) wrongIfEl.innerText = stopText !== '--' ? stopText : (a.wrong_if || payload.wrong_if || '--');
    if (optionEl) optionEl.innerText = vehicleText !== '--' ? vehicleText : (payload.option || 'SHARES');

    // Card 2: Tastytrade Volatility & Options
    const ivBox = document.getElementById('alert-modal-iv-rank-box');
    const hvBox = document.getElementById('alert-modal-hv30-box');
    const spreadBox = document.getElementById('alert-modal-spread-box');
    const liqBox = document.getElementById('alert-modal-liquidity-box');
    const regPill = document.getElementById('alert-modal-iv-regime-pill');

    if (ivBox) ivBox.innerText = ivPctText !== '--' ? `${ivRankText} / ${ivPctText}` : ivRankText;
    if (hvBox) hvBox.innerText = hv30Text;
    if (spreadBox) spreadBox.innerText = spreadText;
    if (liqBox) liqBox.innerText = payload.liquidity ? `${payload.liquidity} ★` : '3 ★ (Liquid)';

    if (regPill) {
      const numIv = parseFloat(ivRankText.replace('%', ''));
      if (!isNaN(numIv)) {
        if (numIv >= 50) {
          regPill.innerText = 'High IV (Sell Premium)';
          regPill.className = 'pill green';
        } else if (numIv < 35) {
          regPill.innerText = 'Discounted IV (Buy Debit)';
          regPill.className = 'pill cyan';
        } else {
          regPill.innerText = 'Normal IV';
          regPill.className = 'pill';
        }
      } else {
        regPill.innerText = 'VOL REGIME';
        regPill.className = 'pill cyan';
      }
    }

    // Card 3: Event Calendar & Catalysts
    const cardEarn = document.getElementById('alert-modal-card-earnings');
    const cardCat = document.getElementById('alert-modal-card-catalyst');
    const whyNowEl = document.getElementById('alert-modal-why-now');
    if (cardEarn) cardEarn.innerText = earningsText;
    if (cardCat) cardCat.innerText = catalystText;
    if (whyNowEl) whyNowEl.innerText = payload.why_now || a.setup || payload.setup || 'Market Signal';

    // Card 4: Price Action & Execution Telemetry
    const cardSpot = document.getElementById('alert-modal-card-spot');
    const cardBias = document.getElementById('alert-modal-card-bias');
    const cardStrat = document.getElementById('alert-modal-card-strategy');
    const bias = (payload.bias || payload.side || '').toUpperCase();
    if (cardSpot) cardSpot.innerText = price;
    if (cardBias) cardBias.innerText = `${act} ${bias ? '(' + bias + ')' : ''}`;
    if (cardStrat) cardStrat.innerText = a.strategy || payload.strategy || 'Daily/Swing Radar';

    // Fetch real-time data ONLY when alert is viewed (on-demand, zero bulk load)
    if (window.AppApi && typeof window.AppApi.getTickerQuote === 'function' && sym) {
      window.AppApi.getTickerQuote(sym).then(q => {
        if (q && q.price && Number(q.price) > 0) {
          const livePrice = parseFloat(q.price).toFixed(2);
          const chg = parseFloat(q.net_change || 0.0);
          const chgPct = parseFloat(q.net_pct || q.net_percent_change || 0.0);
          const chgStr = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)} (${chgPct >= 0 ? '+' : ''}${chgPct.toFixed(2)}%)`;
          const chgColor = chg >= 0 ? '#10b981' : '#ef4444';

          if (cardSpot) {
            cardSpot.innerHTML = `<span>$${livePrice}</span> <span style="font-size:11px; color:${chgColor}; font-weight:700; margin-left:6px;">${chgStr} (Live)</span>`;
          }

          const priceEl = document.getElementById('alert-modal-price');
          if (priceEl) {
            priceEl.innerHTML = `${price} <span style="font-size:11px; color:${chgColor}; font-weight:700; margin-left:6px;" title="Live Spot Quote">· Live $${livePrice}</span>`;
          }
        }
      }).catch(err => {
        console.debug('On-demand quote fetch skipped for viewed alert:', err);
      });
    }

    // Initialize Chart Preview
    this.switchModalChartType('interactive');

    // Raw JSON Viewer
    const jsonPre = document.getElementById('alert-modal-raw-json');
    if (jsonPre) {
      const fullObj = {
        ...a,
        decoded_payload: payload
      };
      jsonPre.innerText = JSON.stringify(fullObj, null, 2);
    }

    // Update In-Modal Copilot Ticker and State
    const copilotTickerEl = document.getElementById('alert-modal-copilot-ticker');
    if (copilotTickerEl) copilotTickerEl.innerText = sym;

    // Reset chat session unconditionally so EVERY opened alert starts FRESH with clean context
    this._lastCopilotTicker = sym;
    this.clearAlertCopilotChat();

    // Render Ticker Day Trades & AI-Only P&L lifecycle card inside this alert modal
    const dateKey = a.date || (a.timestamp ? a.timestamp.substring(0, 10) : 'Today');
    this.renderAlertModalTickerTrades(sym, dateKey, a.message_id || a.email_id || id);

    const modal = document.getElementById('tv-alert-detail-modal');
    if (modal) modal.style.display = 'flex';
  },

  closeAlertModal() {
    this.stopAlertCopilotStream();
    const modal = document.getElementById('tv-alert-detail-modal');
    if (modal) {
      modal.style.display = 'none';
      modal.classList.remove('active');
    }
  },

  toggleAlertCopilotDrawer(open = null) {
    if (open === null) {
      open = !this._isCopilotDrawerOpen;
    }
    this._isCopilotDrawerOpen = open;
    const container = document.getElementById('alert-modal-content-container');
    const drawer = document.getElementById('alert-modal-copilot-drawer');
    const resizer = document.getElementById('alert-modal-chat-resizer');
    const btn = document.getElementById('alert-modal-btn-copilot');

    if (open) {
      if (container) {
        container.style.maxWidth = '1480px';
        container.style.width = '96vw';
      }
      if (drawer) {
        drawer.style.display = 'flex';
      }
      if (resizer) {
        resizer.style.display = 'flex';
      }
      if (btn) {
        btn.classList.add('active');
        btn.style.background = 'var(--cyan-glow, #06b6d4)';
        btn.style.color = '#020617';
        btn.innerHTML = '💬 Copilot Active';
      }
      this.initAlertModalResize();
      const input = document.getElementById('alert-modal-chat-input');
      if (input) setTimeout(() => input.focus(), 150);
    } else {
      if (container) {
        container.style.maxWidth = '960px';
        container.style.width = '92vw';
      }
      if (drawer) {
        drawer.style.display = 'none';
      }
      if (resizer) {
        resizer.style.display = 'none';
      }
      if (btn) {
        btn.classList.remove('active');
        btn.style.background = '';
        btn.style.color = '';
        btn.innerHTML = '💬 Ask Copilot About Setup';
      }
    }
  },

  initAlertModalResize() {
    const resizer = document.getElementById('alert-modal-chat-resizer');
    const drawer = document.getElementById('alert-modal-copilot-drawer');
    const modalContent = document.getElementById('alert-modal-content-container');
    if (!resizer || !drawer || resizer._hasResizeListener) return;

    resizer._hasResizeListener = true;
    let dragging = false;

    resizer.addEventListener('mousedown', (e) => {
      e.preventDefault();
      dragging = true;
      resizer.classList.add('active');
      document.body.classList.add('chat-resizing');
    });

    window.addEventListener('mousemove', (e) => {
      if (!dragging || !modalContent) return;
      const rect = modalContent.getBoundingClientRect();
      const newWidth = rect.right - e.clientX;
      const maxW = modalContent.clientWidth * 0.65;
      const clamped = Math.max(300, Math.min(newWidth, maxW));
      drawer.style.width = `${clamped}px`;
    });

    window.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      resizer.classList.remove('active');
      document.body.classList.remove('chat-resizing');
    });
  },

  askCopilotFromModal() {
    const a = this._currentModalAlert || {};
    const payload = this.parsePayload(a);
    const sym = (a.symbol || payload.ticker || 'STOCK').toUpperCase();
    const act = (a.action || payload.action || payload.side || 'ALERT').toUpperCase();
    
    // Ensure drawer is open without closing the modal
    this.toggleAlertCopilotDrawer(true);

    // If chat thread is empty, send initial Two-Pass prompt automatically
    if (!this._alertCopilotHistory || this._alertCopilotHistory.length === 0) {
      const initialPrompt = `What is going on with ${sym} (${act}) right now? Run Pass 1: assess current state, check live price vs entry zone, binary risk/earnings, and volatility. In case of ANY ambiguity, ask me to clarify with options before proceeding.`;
      this.sendModalCopilotMsg(initialPrompt);
    } else {
      const input = document.getElementById('alert-modal-chat-input');
      if (input) input.focus();
    }
  },

  sendQuickCopilotPrompt(type) {
    this.toggleAlertCopilotDrawer(true);
    const a = this._currentModalAlert || {};
    const payload = this.parsePayload(a);
    const sym = (a.symbol || payload.ticker || 'STOCK').toUpperCase();
    const act = (a.action || payload.action || payload.side || 'ALERT').toUpperCase();

    let prompt = '';
    if (type === 'two_pass') {
      prompt = `What is going on with ${sym} (${act}) right now? Run Pass 1: assess current state, check live price vs entry zone, binary risk/earnings, and volatility. In case of ANY ambiguity, ask me to clarify with options before proceeding.`;
    } else if (type === 'setup') {
      prompt = `Explain the tactical entry zone, stop loss, and R:R math for this ${sym} setup. Is price defending a structural floor? If not in-zone, clarify.`;
    } else if (type === 'stagnation') {
      prompt = `If ${sym} is stagnating, consolidating, or range-bound here, what is the best strategy: sell cash-secured puts, covered calls, or wait for breakout confirmation? Explain the trade-offs and theta decay.`;
    } else if (type === 'volatility') {
      prompt = `Analyze the Tastytrade IV Rank, IV percentile, and 30d HV spread for ${sym}. Should I buy debit or sell premium?`;
    } else if (type === 'catalysts') {
      prompt = `Check expected earnings dates and breaking catalysts for ${sym}. Are there upcoming binary risk events?`;
    } else if (type === 'options') {
      prompt = `Fetch live options chain for ${sym} and identify the most liquid strikes with favorable risk-reward.`;
    } else if (type === 'math') {
      prompt = `Execute Python code to compute 14-day ATR, HV20 realized volatility, and Monte Carlo probability of hitting target vs stop for ${sym}.`;
    } else {
      prompt = `Provide a quantitative analysis and playbook for ${sym}.`;
    }

    this.sendModalCopilotMsg(prompt);
  },

  handleAlertCopilotSendClick() {
    if (this._isAlertCopilotStreaming) {
      this.stopAlertCopilotStream();
    } else {
      this.sendModalCopilotMsg();
    }
  },

  stopAlertCopilotStream() {
    if (this._alertCopilotReader) {
      try { this._alertCopilotReader.cancel(); } catch (e) {}
      this._alertCopilotReader = null;
    }
    if (this._alertCopilotAbortController) {
      try { this._alertCopilotAbortController.abort(); } catch (e) {}
      this._alertCopilotAbortController = null;
    }
    this._isAlertCopilotStreaming = false;
    const sendBtn = document.getElementById('alert-modal-chat-send');
    if (sendBtn) {
      sendBtn.innerText = '💬 Send';
      sendBtn.className = 'btn';
      sendBtn.style.background = 'linear-gradient(135deg, #06b6d4, #0284c7)';
    }
  },

  clearAlertCopilotChat() {
    this.stopAlertCopilotStream();
    this._alertCopilotHistory = [];
    const a = this._currentModalAlert || {};
    const sym = (a.symbol || 'STOCK').toUpperCase();
    const alertId = a.id || Date.now();
    this._alertCopilotSessionId = `sess_alert_${sym}_${alertId}_${Math.random().toString(36).substring(2, 7)}`;
    const thread = document.getElementById('alert-modal-chat-thread');
    if (thread) {
      thread.innerHTML = `
        <div id="alert-modal-chat-intro" style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:12px 14px;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
            <span style="font-weight:700; color:var(--cyan-glow); font-size:11px;">🤖 COPILOT GROUNDED IN ${sym} ALERT</span>
            <span class="pill cyan" style="font-size:9.5px; padding:1px 6px;">FRESH SESSION</span>
          </div>
          <div style="font-size:11.5px; color:var(--text-muted); line-height:1.45; margin-bottom:10px;" id="alert-modal-chat-intro-desc">
            Two-Pass Protocol active: evaluates current state, checks binary risks & entry levels, and asks in case of ambiguity before locking execution.
          </div>
          <div style="display:flex; flex-direction:column; gap:5px;">
            <button class="btn" style="background:linear-gradient(135deg, #06b6d4, #0284c7); color:#fff; font-size:11px; padding:5px 10px; text-align:left; border-radius:6px; cursor:pointer;" onclick="AppAlerts.sendQuickCopilotPrompt('two_pass')">
              ⚡ <strong>Pass 1</strong>: What is going on? (State & Ambiguity Check)
            </button>
            <button class="btn secondary" style="font-size:11px; padding:5px 10px; text-align:left; border-radius:6px; cursor:pointer;" onclick="AppAlerts.sendQuickCopilotPrompt('setup')">
              🎯 <strong>Pass 2</strong>: Tactical Execution Levels (Entry, Stop, Target, R:R)
            </button>
            <button class="btn secondary" style="font-size:11px; padding:5px 10px; text-align:left; border-radius:6px; cursor:pointer;" onclick="AppAlerts.sendQuickCopilotPrompt('volatility')">
              📐 <strong>Options</strong>: Tastytrade IV Rank & Vehicle Selection
            </button>
          </div>
        </div>
      `;
    }
  },

  async sendModalCopilotMsg(customPrompt = null) {
    if (this._isAlertCopilotStreaming) return;
    const input = document.getElementById('alert-modal-chat-input');
    const question = (customPrompt || (input ? input.value : '')).trim();
    if (!question) return;
    if (input) input.value = '';

    const a = this._currentModalAlert || {};
    const payload = this.parsePayload(a);
    const sym = (a.symbol || payload.ticker || 'STOCK').toUpperCase();
    const price = a.price ? `$${parseFloat(a.price).toFixed(2)}` : (payload.price ? `$${parseFloat(payload.price).toFixed(2)}` : '$0.00');
    const act = (a.action || payload.action || payload.side || 'ALERT').toUpperCase();

    if (!this._alertCopilotSessionId) {
      this._alertCopilotSessionId = `sess_alert_${sym}_${Date.now()}`;
    }

    const thread = document.getElementById('alert-modal-chat-thread');

    // 1. Append user message bubble
    const userBubble = document.createElement('div');
    userBubble.style = 'align-self:flex-end; max-width:88%; background:var(--blue); color:#ffffff; border-radius:10px; padding:8px 12px; font-size:12.5px; line-height:1.4; box-shadow:0 1px 3px rgba(0,0,0,0.15);';
    userBubble.innerText = question;
    if (thread) {
      thread.appendChild(userBubble);
      thread.scrollTop = thread.scrollHeight;
    }
    this._alertCopilotHistory.push({ role: 'user', content: question });

    // 2. Prepare Assistant message bubble
    const assistantBubble = document.createElement('div');
    assistantBubble.style = 'align-self:flex-start; max-width:94%; background:var(--bg-subtle); border:1px solid var(--border); border-radius:10px; padding:10px 14px; font-size:12px; line-height:1.5; color:var(--text-main); box-shadow:var(--shadow-card);';
    assistantBubble.innerHTML = `
      <div style="font-size:10px; font-weight:700; color:var(--cyan-glow); margin-bottom:4px; display:flex; justify-content:space-between; align-items:center;">
        <span>🤖 COPILOT ($${sym})</span>
        <button class="btn danger" onclick="AppAlerts.stopAlertCopilotStream()" style="padding:1px 6px; font-size:9.5px; font-weight:700; cursor:pointer;">⏹ Stop</button>
      </div>
      <div><em>Connecting to local model stream...</em></div>
    `;
    if (thread) {
      thread.appendChild(assistantBubble);
      thread.scrollTop = thread.scrollHeight;
    }

    const sendBtn = document.getElementById('alert-modal-chat-send');
    if (sendBtn) {
      sendBtn.innerText = '⏹ Stop';
      sendBtn.className = 'btn danger';
      sendBtn.style.background = 'var(--rose, #f43f5e)';
    }

    this._isAlertCopilotStreaming = true;
    this._alertCopilotAbortController = new AbortController();

    // 3. Assemble rich modal board telemetry context
    const lrVerdict = document.getElementById('alert-modal-lr-verdict')?.innerText || '';
    const lrPonytail = document.getElementById('alert-modal-lr-ponytail-text')?.innerText || '';
    const stopLoss = document.getElementById('alert-modal-lr-stop')?.innerText || '';
    const target = document.getElementById('alert-modal-lr-target')?.innerText || '';
    const vehicle = document.getElementById('alert-modal-lr-vehicle')?.innerText || '';
    const ivRank = document.getElementById('alert-modal-lr-iv')?.innerText || '';
    const earnings = document.getElementById('alert-modal-lr-earnings')?.innerText || '';
    const catalyst = document.getElementById('alert-modal-lr-catalyst')?.innerText || '';
    const plan = document.getElementById('alert-modal-plan-content')?.innerText || '';

    const boardContext = `
**CURRENT TRADINGVIEW ALERT MODAL CONTEXT FOR ${sym}:**
- **Ticker & Spot**: ${sym} @ ${price}
- **Action & Strategy**: ${act} · ${a.strategy || payload.strategy || 'Daily Radar'}
- **Tactical Plan**: ${plan}
- **Tactical Stop**: ${stopLoss} | **Target**: ${target}
- **Recommended Vehicle**: ${vehicle}
- **Tastytrade Volatility**: IV Rank ${ivRank}
- **Earnings & Catalysts**: Expected Earnings: ${earnings} | Catalyst: ${catalyst}
- **Local Research (#ponytail) Triage**: Verdict ${lrVerdict} | PM Critique: ${lrPonytail}
`;

    let fullAnswer = '';
    try {
      const response = await fetch('/api/copilot/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: this._alertCopilotAbortController.signal,
        body: JSON.stringify({
          question,
          ticker: sym,
          date: a.created_at ? a.created_at.split('T')[0] : (new Date()).toISOString().split('T')[0],
          history: this._alertCopilotHistory.slice(-8),
          session_id: this._alertCopilotSessionId,
          board_context: boardContext
        })
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);

      const reader = response.body.getReader();
      this._alertCopilotReader = reader;
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith('data: ')) {
            const jsonStr = trimmed.slice(6);
            try {
              const data = JSON.parse(jsonStr);
              if (data.session_id) this._alertCopilotSessionId = data.session_id;
              if (data.status === 'connecting') {
                assistantBubble.innerHTML = `
                  <div style="font-size:10px; font-weight:700; color:var(--cyan-glow); margin-bottom:4px; display:flex; justify-content:space-between; align-items:center;">
                    <span>🤖 COPILOT ($${sym})</span>
                    <button class="btn danger" onclick="AppAlerts.stopAlertCopilotStream()" style="padding:1px 6px; font-size:9.5px; font-weight:700; cursor:pointer;">⏹ Stop</button>
                  </div>
                  <div><em>Analyzing broker telemetry and quantitative math...</em></div>
                `;
              } else if (data.token) {
                fullAnswer += data.token;
                const rendered = window.AppUtils && typeof window.AppUtils.renderMarkdown === 'function' ? window.AppUtils.renderMarkdown(fullAnswer) : fullAnswer;
                assistantBubble.innerHTML = `
                  <div style="font-size:10px; font-weight:700; color:var(--cyan-glow); margin-bottom:4px; display:flex; justify-content:space-between; align-items:center;">
                    <span>🤖 COPILOT ($${sym})</span>
                    <button class="btn danger" onclick="AppAlerts.stopAlertCopilotStream()" style="padding:1px 6px; font-size:9.5px; font-weight:700; cursor:pointer;">⏹ Stop</button>
                  </div>
                  <div>${rendered}</div>
                `;
                if (thread) thread.scrollTop = thread.scrollHeight;
              } else if (data.error) {
                fullAnswer += `\n\n❌ **Error:** ${data.error}`;
                assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--rose-light); margin-bottom:4px;">❌ ERROR</div><div>${fullAnswer}</div>`;
              }
            } catch (err) {}
          }
        }
      }

      const finalRendered = window.AppUtils && typeof window.AppUtils.renderMarkdown === 'function' ? window.AppUtils.renderMarkdown(fullAnswer) : fullAnswer;
      assistantBubble.innerHTML = `
        <div style="font-size:10px; font-weight:700; color:var(--cyan-glow); margin-bottom:4px;">
          <span>🤖 COPILOT ($${sym})</span>
        </div>
        <div>${finalRendered}</div>
      `;
      if (fullAnswer.trim()) {
        this._alertCopilotHistory.push({ role: 'assistant', content: fullAnswer });
      }
    } catch (e) {
      const isAbort = e.name === 'AbortError' || (this._alertCopilotAbortController && this._alertCopilotAbortController.signal.aborted);
      if (fullAnswer.trim()) {
        const savedText = isAbort ? `${fullAnswer}\n\n*[⏹ Stream stopped by user]*` : `${fullAnswer}\n\n*[⚠️ Connection interrupted: ${e.message}]*`;
        const rendered = window.AppUtils && typeof window.AppUtils.renderMarkdown === 'function' ? window.AppUtils.renderMarkdown(savedText) : savedText;
        this._alertCopilotHistory.push({ role: 'assistant', content: savedText });
        assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--cyan-glow); margin-bottom:4px;">🤖 COPILOT ($${sym})</div><div>${rendered}</div>`;
      } else {
        const errText = isAbort ? '*[⏹ Stream cancelled]*' : `❌ Error: ${e.message}`;
        assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--rose-light); margin-bottom:4px;">❌ NOTICE</div><div>${errText}</div>`;
      }
    } finally {
      this._isAlertCopilotStreaming = false;
      this._alertCopilotAbortController = null;
      this._alertCopilotReader = null;
      if (sendBtn) {
        sendBtn.innerText = '💬 Send';
        sendBtn.className = 'btn';
        sendBtn.style.background = 'linear-gradient(135deg, #06b6d4, #0284c7)';
      }
      if (thread) thread.scrollTop = thread.scrollHeight;
    }
  },

  switchModalChartType(type) {
    const host = document.getElementById('alert-modal-chart-host');
    const badge = document.getElementById('alert-modal-chart-badge');
    const btnTv = document.getElementById('alert-modal-chart-toggle-tv');
    const btnImg = document.getElementById('alert-modal-chart-toggle-img');
    if (!host || !this._currentModalAlert) return;
    const sym = (this._currentModalAlert.symbol || '').toUpperCase();

    if (type === 'image') {
      if (btnTv) btnTv.classList.remove('active');
      if (btnImg) btnImg.classList.add('active');
      if (badge) badge.innerText = 'Captured Screenshot';
      const imgSrc = this._currentModalAlert.chart_url || `/data/raw/latest/${sym}/${sym}_normal_chart.png`;
      host.innerHTML = `<img src="${imgSrc}" style="width:100%; height:100%; object-fit:contain; background:#02050c;" onerror="this.onerror=null; this.parentElement.innerHTML='<div style=\\'color:var(--text-muted); font-size:12px; text-align:center; padding:40px;\\'>No captured screenshot yet.<br><button class=\\'btn secondary\\' style=\\'margin-top:8px; padding:3px 10px; font-size:11px;\\' onclick=\\'AppAlerts.scrapeNormalChartForModal()\\'>📸 Scrape Normal Chart Now</button></div>';" />`;
    } else {
      if (btnImg) btnImg.classList.remove('active');
      if (btnTv) btnTv.classList.add('active');
      if (badge) badge.innerText = 'Interactive TradingView';
      host.innerHTML = `<iframe src="https://s.tradingview.com/widgetembed/?frameElementId=tradingview_widget&symbol=${sym}&interval=D&hidesidetoolbar=1&symboledit=0&saveimage=0&toolbarbg=0a0e1a&studies=[]&theme=dark&style=1&timezone=America%2FNew_York&withdateranges=1&hideideas=1" style="width:100%; height:100%; border:none;" allowfullscreen></iframe>`;
    }
  },

  async scrapeNormalChartForModal() {
    if (!this._currentModalAlert) return;
    const sym = (this._currentModalAlert.symbol || '').toUpperCase();
    const btn = document.getElementById('alert-modal-btn-scrape-normal');
    const origHtml = btn ? btn.innerHTML : '📸 Scrape Normal Chart';
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '⏳ Scraping TV...';
    }
    try {
      const res = await fetch('/api/alerts/scrape-chart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: sym })
      });
      const data = await res.json();
      const chartUrl = data.chart_url || data.image_url;
      if (data.status === 'ok' && chartUrl) {
        this._currentModalAlert.chart_url = chartUrl;
        this.switchModalChartType('image');
        if (window.AppUtils && window.AppUtils.showToast) {
          window.AppUtils.showToast(`📸 Normal chart scraped for ${sym}`, 'success');
        }
      } else {
        alert(data.error || 'Failed to scrape chart');
      }
    } catch (e) {
      console.error(e);
      alert('Error scraping chart: ' + e.message);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = origHtml;
      }
    }
  },

  async runLocalResearchForModal() {
    if (!this._currentModalAlert) return;
    const btn = document.getElementById('alert-modal-btn-run-lr');
    const originalText = btn ? btn.innerHTML : '⚡ Run Local Research (#ponytail)';
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner" style="display:inline-block; width:12px; height:12px; border:2px solid #fff; border-top-color:transparent; border-radius:50%; animation:spin 0.8s linear infinite;"></span> Running #ponytail LLM...';
    }

    try {
      const resp = await fetch('/api/alerts/local-research', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message_id: this._currentModalAlert.message_id,
          symbol: this._currentModalAlert.symbol,
          use_tools: true
        })
      });
      const data = await resp.json();
      if (data.status === 'ok' && data.result) {
        const res = data.result;
        this._currentModalAlert.llm_decision = res.llm_decision;
        this._currentModalAlert.llm_playbook = res.llm_playbook;

        // Update in raw alerts list
        const idx = this._rawAlerts.findIndex(x => (x.message_id || x.email_id) === (this._currentModalAlert.message_id || this._currentModalAlert.email_id));
        if (idx !== -1) {
          this._rawAlerts[idx].llm_decision = res.llm_decision;
          this._rawAlerts[idx].llm_playbook = res.llm_playbook;
        }

        // Re-populate modal & table
        this.openAlertModal(this._currentModalAlert.message_id || this._currentModalAlert.email_id);
        this.renderTable();
        if (window.AppUtils && window.AppUtils.showToast) {
          window.AppUtils.showToast(`✅ #ponytail triage complete: ${res.llm_decision}`, 'success');
        }
      } else {
        const errMsg = data.error || data.detail || (resp.status !== 200 ? `Server returned ${resp.status}` : 'Unknown error');
        console.error('Local research failed:', data);
        if (window.AppUtils && window.AppUtils.showToast) {
          window.AppUtils.showToast(`⚠️ Local research failed: ${errMsg}`, 'error');
        } else {
          alert('Local research failed: ' + errMsg);
        }
      }
    } catch (err) {
      console.error('Error running local research:', err);
      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast(`⚠️ Network error: ${err.message}`, 'error');
      } else {
        alert('Network error running local research: ' + err.message);
      }
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    }
  },

  async runLocalResearchSingle(alertId, symbol) {
    if (window.AppUtils && window.AppUtils.showToast) {
      window.AppUtils.showToast(`🤖 Running local research for ${symbol}...`, 'info');
    }
    try {
      const resp = await fetch('/api/alerts/local-research', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message_id: alertId, symbol: symbol, use_tools: true })
      });
      const data = await resp.json();
      if (data.status === 'ok' && data.result) {
        const res = data.result;
        const target = this._rawAlerts.find(a => (a.message_id || a.email_id) === alertId);
        if (target) {
          target.llm_decision = res.llm_decision;
          target.llm_playbook = res.llm_playbook;
        }
        this.renderTable();
        if (window.AppUtils && window.AppUtils.showToast) {
          window.AppUtils.showToast(`✅ ${symbol} Triage: ${res.llm_decision}`, 'success');
        }
      }
    } catch (e) {
      console.error('Local research error:', e);
    }
  },

  _autoTriagePollTimer: null,

  startAutoTriagePolling() {
    if (this._autoTriagePollTimer) return;
    this._autoTriagePollTimer = setInterval(async () => {
      try {
        const resp = await fetch('/api/alerts/auto-triage-status');
        const data = await resp.json();
        if (data.status === 'ok') {
          const counter = document.getElementById('alerts-pending-counter');
          if (counter) {
            if (data.pending_count > 0) {
              counter.style.display = 'inline-block';
              counter.textContent = `${data.pending_count} pending`;
            } else {
              counter.style.display = 'none';
            }
          }
          if (data.pending_count === 0) {
            this.stopAutoTriagePolling();
            await this.loadAlerts(true);
          } else if (data.is_processing || data.total_triaged > 0) {
            await this.loadAlerts(true);
          }
        }
      } catch (e) {
        // silent background poll error
      }
    }, 4000);
  },

  stopAutoTriagePolling() {
    if (this._autoTriagePollTimer) {
      clearInterval(this._autoTriagePollTimer);
      this._autoTriagePollTimer = null;
    }
  },

  async batchEvaluatePending() {
    if (window.AppUtils && window.AppUtils.showToast) {
      window.AppUtils.showToast('🚀 Expedited batch local LLM triage for pending alerts...', 'info');
    }
    try {
      const resp = await fetch('/api/alerts/evaluate-pending', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limit: 50, date: this._dateFilter !== 'ALL' ? this._dateFilter : null })
      });
      const data = await resp.json();
      this.startAutoTriagePolling();
      setTimeout(() => this.loadAlerts(true), 3000);
    } catch (e) {
      console.error('Batch evaluation error:', e);
    }
  },

  copyAlertJson() {
    if (!this._currentModalAlert) return;
    const payload = this.parsePayload(this._currentModalAlert);
    const fullObj = {
      ...this._currentModalAlert,
      decoded_payload: payload
    };
    const jsonStr = JSON.stringify(fullObj, null, 2);
    navigator.clipboard.writeText(jsonStr).then(() => {
      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast('📋 Copied raw alert JSON to clipboard!', 'success');
      }
    }).catch(err => {
      console.warn('Clipboard copy error:', err);
    });
  },

  openChart(sym) {
    if (window.AppSwing && typeof window.AppSwing.openTradingViewModal === 'function') {
      window.AppSwing.openTradingViewModal(sym, 'D');
    }
  },

  researchTicker(sym) {
    if (window.App && window.App.switchDesk) {
      window.App.switchDesk('swing');
    }
    if (window.AppSwing) {
      window.AppSwing.setTicker(sym);
      window.AppSwing.launchResearch();
    }
  },

  /* ==========================================================================
     Intraday Alerts PnL & AI Attribution Engine
     ========================================================================== */

  computeSessionTrades(targetDate = 'ALL') {
    let list = [...this._rawAlerts];
    // Sort chronologically ascending for deterministic entry -> exit pairing
    list.sort((a, b) => {
      const ta = a.timestamp || a.created_at || '';
      const tb = b.timestamp || b.created_at || '';
      return ta.localeCompare(tb);
    });

    const trades = [];
    const openBySym = {}; // sym -> entryAlert

    for (const a of list) {
      const dateKey = a.date || (a.timestamp ? a.timestamp.substring(0, 10) : '');
      const sym = (a.symbol || (a.raw_payload ? a.raw_payload.ticker : '') || '').toUpperCase().trim();
      const act = (a.action || (a.raw_payload ? a.raw_payload.action : '') || '').toUpperCase().trim();
      const price = parseFloat(a.alert_price || (a.raw_payload ? a.raw_payload.price : 0)) || 0;
      const ts = a.timestamp || a.created_at || '';
      const dec = a.llm_decision || '';
      const pb = a.llm_playbook || '';
      const setup = a.setup || (a.raw_payload ? a.raw_payload.setup : '') || a.strategy || 'Intraday';
      const msgId = a.message_id || a.email_id || `${ts}_${sym}_${act}`;

      if (!sym) continue;

      const isCall = act.includes('CALL');
      const isPut = act.includes('PUT');
      const isExit = act.includes('EXIT') || act.includes('CUT');

      if ((isCall || isPut) && !isExit) {
        const side = isCall ? 'LONG' : 'SHORT';
        const isTaken = /TAKE|GO\s*\(|BUY\s*CALL|BUY\s*PUT/i.test(dec) || /TAKE\s*CALLS|TAKE\s*PUTS/i.test(pb);
        const isFiltered = /PASS|STAND\s*ASIDE|WAIT|DO\s*NOT\s*ENTER|VETO/i.test(dec) || /STAND\s*ASIDE|DO\s*NOT\s*ENTER/i.test(pb);

        openBySym[sym] = {
          entryAlertId: msgId,
          entryAlert: a,
          symbol: sym,
          side: side,
          entryTime: ts,
          entryPrice: price,
          setup: setup,
          llmDecision: dec,
          llmPlaybook: pb,
          isAiTaken: isTaken,
          isAiFiltered: isFiltered && !isTaken,
          date: dateKey,
          status: 'OPEN'
        };
      } else if (isExit) {
        if (openBySym[sym]) {
          const entry = openBySym[sym];
          delete openBySym[sym];

          const exitPrice = price;
          const entryPrice = entry.entryPrice;
          let diffPts = 0;
          let pnl100 = 0;

          if (entryPrice > 0 && exitPrice > 0) {
            diffPts = entry.side === 'LONG' ? (exitPrice - entryPrice) : (entryPrice - exitPrice);
            pnl100 = diffPts * 100.0;
          }

          let durStr = '--';
          if (entry.entryTime && ts) {
            try {
              const d1 = new Date(entry.entryTime.replace(' ', 'T'));
              const d2 = new Date(ts.replace(' ', 'T'));
              const diffMin = Math.round((d2 - d1) / 60000);
              if (!isNaN(diffMin) && diffMin >= 0) {
                if (diffMin < 60) durStr = `${diffMin}m`;
                else durStr = `${Math.floor(diffMin / 60)}h ${diffMin % 60}m`;
              }
            } catch (e) {}
          }

          let attribution = 'NEUTRAL';
          if (entry.isAiTaken) {
            attribution = pnl100 >= 0 ? 'AI_REALIZED_WIN' : 'AI_REALIZED_LOSS';
          } else if (entry.isAiFiltered) {
            attribution = pnl100 <= 0 ? 'AI_AVOIDED_LOSS' : 'AI_MISSED_WIN';
          }

          trades.push({
            ...entry,
            exitAlertId: msgId,
            exitAlert: a,
            exitTime: ts,
            exitPrice: exitPrice,
            duration: durStr,
            diffPts: Math.round(diffPts * 100) / 100,
            pnl100: Math.round(pnl100 * 100) / 100,
            status: 'CLOSED',
            attribution: attribution
          });
        }
      }
    }

    // Leftover open positions
    for (const sym in openBySym) {
      const openTrade = openBySym[sym];
      const livePrice = (window.AppState && window.AppState.quotes && window.AppState.quotes[sym])
        ? parseFloat(window.AppState.quotes[sym].last || window.AppState.quotes[sym].price)
        : openTrade.entryPrice;

      let diffPts = 0;
      let pnl100 = 0;
      if (openTrade.entryPrice > 0 && livePrice > 0) {
        diffPts = openTrade.side === 'LONG' ? (livePrice - openTrade.entryPrice) : (openTrade.entryPrice - livePrice);
        pnl100 = diffPts * 100.0;
      }

      trades.push({
        ...openTrade,
        exitAlertId: null,
        exitAlert: null,
        exitTime: null,
        exitPrice: livePrice,
        duration: 'Live',
        diffPts: Math.round(diffPts * 100) / 100,
        pnl100: Math.round(pnl100 * 100) / 100,
        status: 'OPEN',
        attribution: openTrade.isAiTaken ? 'ACTIVE_TAKEN' : 'ACTIVE_FILTERED'
      });
    }

    let filteredTrades = trades;
    if (targetDate && targetDate !== 'ALL') {
      filteredTrades = trades.filter(t => t.date === targetDate);
    }

    // Sort completed trades descending by entryTime
    filteredTrades.sort((a, b) => (b.entryTime || '').localeCompare(a.entryTime || ''));

    let aiTakenPnL = 0;
    let aiTakenWins = 0;
    let aiTakenLosses = 0;
    let aiAvoidedPnL = 0;
    let aiAvoidedCount = 0;
    let aiMissedPnL = 0;
    let tvRawPnL = 0;
    let completedCount = 0;
    let openCount = 0;

    for (const t of filteredTrades) {
      if (t.status === 'OPEN') {
        openCount++;
        continue;
      }
      completedCount++;
      tvRawPnL += t.pnl100;

      if (t.isAiTaken) {
        aiTakenPnL += t.pnl100;
        if (t.pnl100 > 0) aiTakenWins++;
        else aiTakenLosses++;
      } else if (t.isAiFiltered) {
        if (t.pnl100 < 0) {
          aiAvoidedPnL += Math.abs(t.pnl100);
          aiAvoidedCount++;
        } else if (t.pnl100 > 0) {
          aiMissedPnL += t.pnl100;
        }
      }
    }

    const aiTakenTotal = aiTakenWins + aiTakenLosses;
    const aiWinRate = aiTakenTotal > 0 ? (aiTakenWins / aiTakenTotal * 100) : 0;
    const netAlpha = aiTakenPnL - tvRawPnL;

    return {
      trades: filteredTrades,
      allTradesCount: trades.length,
      kpis: {
        aiTakenPnL: Math.round(aiTakenPnL * 100) / 100,
        aiTakenWins,
        aiTakenLosses,
        aiTakenTotal,
        aiWinRate: Math.round(aiWinRate * 10) / 10,
        aiAvoidedPnL: Math.round(aiAvoidedPnL * 100) / 100,
        aiAvoidedCount,
        aiMissedPnL: Math.round(aiMissedPnL * 100) / 100,
        tvRawPnL: Math.round(tvRawPnL * 100) / 100,
        netAlpha: Math.round(netAlpha * 100) / 100,
        completedCount,
        openCount
      }
    };
  },

  buildAlertPnlIndex() {
    this._pnlAlertIndex = {};
    const res = this.computeSessionTrades('ALL');
    for (const t of res.trades) {
      if (t.exitAlertId) {
        this._pnlAlertIndex[t.exitAlertId] = t;
      }
      if (t.entryAlertId) {
        this._pnlAlertIndex[t.entryAlertId] = t;
      }
    }
  },

  openPnlModal(dateStr = null) {
    const modal = document.getElementById('modal-alerts-pnl');
    if (!modal) return;

    const distinctDates = [...new Set(this._rawAlerts.map(a => a.date || (a.timestamp ? a.timestamp.substring(0, 10) : 'Today')))].filter(Boolean).sort().reverse();
    if (dateStr) {
      this._pnlCurrentSession = dateStr;
    } else if (this._dateFilter && this._dateFilter !== 'ALL') {
      this._pnlCurrentSession = this._dateFilter;
    } else if (distinctDates.length > 0) {
      this._pnlCurrentSession = distinctDates[0];
    } else {
      this._pnlCurrentSession = 'ALL';
    }

    // Populate session dropdown
    const sel = document.getElementById('pnl-modal-session-select');
    if (sel) {
      sel.innerHTML = `<option value="ALL">🌟 All Sessions Combined (${distinctDates.length} Days)</option>` +
        distinctDates.map(d => `<option value="${d}" ${d === this._pnlCurrentSession ? 'selected' : ''}>📅 Session: ${d}${d === distinctDates[0] ? ' (Latest)' : ''}</option>`).join('');
    }

    this._pnlFilterTab = 'ALL';
    this._pnlSearchQuery = '';
    const searchInp = document.getElementById('pnl-modal-search');
    if (searchInp) searchInp.value = '';

    this.renderPnlModal();
    modal.style.display = 'flex';
  },

  closePnlModal() {
    const modal = document.getElementById('modal-alerts-pnl');
    if (modal) modal.style.display = 'none';
  },

  refreshPnlModal() {
    this.renderPnlModal();
  },

  switchPnlSession(val) {
    this._pnlCurrentSession = val;
    this.renderPnlModal();
  },

  setPnlTab(tab) {
    this._pnlFilterTab = tab;
    document.querySelectorAll('#pnl-modal-filter-tabs .pnl-tab-btn').forEach(btn => {
      const bTab = btn.getAttribute('data-pnl-tab');
      btn.className = `pnl-tab-btn ${bTab === tab ? 'active' : ''}`;
    });
    this.renderPnlModal();
  },

  setPnlSearch(q) {
    this._pnlSearchQuery = (q || '').trim().toLowerCase();
    this.renderPnlModal();
  },

  renderPnlModal() {
    const tableBody = document.getElementById('pnl-modal-table-body');
    if (!tableBody) return;

    const data = this.computeSessionTrades(this._pnlCurrentSession);
    const k = data.kpis;

    // Update KPI Cards
    const elTaken = document.getElementById('pnl-kpi-ai-taken');
    const elTakenSub = document.getElementById('pnl-kpi-ai-taken-sub');
    const elAvoided = document.getElementById('pnl-kpi-avoided');
    const elAvoidedSub = document.getElementById('pnl-kpi-avoided-sub');
    const elTv = document.getElementById('pnl-kpi-tv-raw');
    const elTvSub = document.getElementById('pnl-kpi-tv-raw-sub');
    const elAlpha = document.getElementById('pnl-kpi-net-alpha');
    const elAlphaSub = document.getElementById('pnl-kpi-net-alpha-sub');

    if (elTaken) {
      const sign = k.aiTakenPnL >= 0 ? '+' : '';
      elTaken.innerText = `${sign}$${k.aiTakenPnL.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      elTaken.style.color = k.aiTakenPnL >= 0 ? '#10b981' : '#ef4444';
      if (elTakenSub) elTakenSub.innerText = `${k.aiTakenTotal} trades · ${k.aiWinRate}% win rate (${k.aiTakenWins}W / ${k.aiTakenLosses}L)`;
    }

    if (elAvoided) {
      elAvoided.innerText = `+$${k.aiAvoidedPnL.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      if (elAvoidedSub) elAvoidedSub.innerText = `${k.aiAvoidedCount} losing alerts filtered out by AI`;
    }

    if (elTv) {
      const sign = k.tvRawPnL >= 0 ? '+' : '';
      elTv.innerText = `${sign}$${k.tvRawPnL.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      elTv.style.color = k.tvRawPnL >= 0 ? '#10b981' : '#f87171';
      if (elTvSub) elTvSub.innerText = `${k.completedCount} total matched TV alerts`;
    }

    if (elAlpha) {
      const sign = k.netAlpha >= 0 ? '+' : '';
      elAlpha.innerText = `${sign}$${k.netAlpha.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      elAlpha.style.color = k.netAlpha >= 0 ? '#c084fc' : '#f87171';
      if (elAlphaSub) elAlphaSub.innerText = `Net AI Value Added (vs blind TV signals)`;
    }

    // Filter list by Tab
    let list = data.trades;
    const countAll = list.length;
    let countTaken = 0, countAvoided = 0, countOpen = 0, countWin = 0, countLoss = 0;

    list.forEach(t => {
      if (t.isAiTaken) countTaken++;
      if (t.isAiFiltered) countAvoided++;
      if (t.status === 'OPEN') countOpen++;
      if (t.status === 'CLOSED') {
        if (t.pnl100 > 0) countWin++;
        else countLoss++;
      }
    });

    const elCAll = document.getElementById('pnl-count-all');
    const elCTaken = document.getElementById('pnl-count-taken');
    const elCAvoided = document.getElementById('pnl-count-avoided');
    const elCOpen = document.getElementById('pnl-count-open');
    const elCWin = document.getElementById('pnl-count-win');
    const elCLoss = document.getElementById('pnl-count-loss');

    if (elCAll) elCAll.innerText = countAll;
    if (elCTaken) elCTaken.innerText = countTaken;
    if (elCAvoided) elCAvoided.innerText = countAvoided;
    if (elCOpen) elCOpen.innerText = countOpen;
    if (elCWin) elCWin.innerText = countWin;
    if (elCLoss) elCLoss.innerText = countLoss;

    if (this._pnlFilterTab === 'TAKEN') list = list.filter(t => t.isAiTaken);
    else if (this._pnlFilterTab === 'AVOIDED') list = list.filter(t => t.isAiFiltered);
    else if (this._pnlFilterTab === 'OPEN') list = list.filter(t => t.status === 'OPEN');
    else if (this._pnlFilterTab === 'WIN') list = list.filter(t => t.status === 'CLOSED' && t.pnl100 > 0);
    else if (this._pnlFilterTab === 'LOSS') list = list.filter(t => t.status === 'CLOSED' && t.pnl100 <= 0);

    if (this._pnlSearchQuery) {
      const q = this._pnlSearchQuery;
      list = list.filter(t => t.symbol.toLowerCase().includes(q) || (t.setup && t.setup.toLowerCase().includes(q)));
    }

    if (list.length === 0) {
      tableBody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:36px; color:var(--text-muted); font-size:12px;">No intraday trades match the selected session / filter.</td></tr>`;
      return;
    }

    let rowsHtml = '';
    list.forEach(t => {
      const isCall = t.side === 'LONG';
      const dirBadge = isCall
        ? `<span class="badge" style="background:rgba(16,185,129,0.12); color:#10b981; border:1px solid rgba(16,185,129,0.3); font-weight:800; font-size:10px;">🟢 CALLS (Long)</span>`
        : `<span class="badge" style="background:rgba(239,68,68,0.12); color:#ef4444; border:1px solid rgba(239,68,68,0.3); font-weight:800; font-size:10px;">🔴 PUTS (Short)</span>`;

      const compName = window.AppUtils ? (window.AppUtils.getCompanyName(t.symbol) || t.symbol) : t.symbol;

      const sign = t.pnl100 >= 0 ? '+' : '';
      const pnlBadge = t.status === 'OPEN'
        ? `<span class="pnl-badge-open" title="Active position at live spot">${sign}$${t.pnl100.toFixed(2)} (Live)</span>`
        : (t.pnl100 >= 0
            ? `<span class="pnl-badge-pos">+${sign}$${t.pnl100.toFixed(2)}</span>`
            : `<span class="pnl-badge-neg">${t.pnl100.toFixed(2)}</span>`);

      let aiBadge = '';
      if (t.isAiTaken) {
        aiBadge = `<span class="badge" style="background:rgba(16,185,129,0.14); color:#10b981; border:1px solid rgba(16,185,129,0.35); font-size:10px; font-weight:800;">🟢 TAKE ${t.side === 'LONG' ? 'CALLS' : 'PUTS'}</span>`;
      } else if (t.isAiFiltered) {
        aiBadge = `<span class="badge" style="background:rgba(239,68,68,0.08); color:#f87171; border:1px solid rgba(239,68,68,0.25); font-size:10px; font-weight:800;">⛔ PASS / WAIT</span>`;
      } else {
        aiBadge = `<span class="badge" style="background:var(--bg-subtle); color:var(--text-muted); font-size:10px;">—</span>`;
      }

      let attribBadge = '';
      if (t.status === 'OPEN') {
        attribBadge = `<span class="badge" style="background:rgba(6,182,212,0.1); color:var(--cyan); border:1px solid rgba(6,182,212,0.3); font-size:10px; font-weight:700;">⚡ Open Position</span>`;
      } else if (t.attribution === 'AI_REALIZED_WIN') {
        attribBadge = `<span class="badge" style="background:rgba(16,185,129,0.15); color:#10b981; border:1px solid rgba(16,185,129,0.4); font-size:10px; font-weight:800;">🏆 AI Won</span>`;
      } else if (t.attribution === 'AI_REALIZED_LOSS') {
        attribBadge = `<span class="badge" style="background:rgba(239,68,68,0.15); color:#ef4444; border:1px solid rgba(239,68,68,0.4); font-size:10px; font-weight:800;">🛑 AI Loss</span>`;
      } else if (t.attribution === 'AI_AVOIDED_LOSS') {
        attribBadge = `<span class="badge" style="background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.4); font-size:10px; font-weight:800;" title="AI prevented this loss by standing aside">🛡️ Avoided Loss</span>`;
      } else if (t.attribution === 'AI_MISSED_WIN') {
        attribBadge = `<span class="badge" style="background:rgba(245,158,11,0.12); color:#f59e0b; border:1px solid rgba(245,158,11,0.35); font-size:10px; font-weight:700;" title="Profitable setup filtered out">⚠️ Missed Win</span>`;
      }

      const inTime = (t.entryTime || '').length >= 19 ? t.entryTime.substring(11, 16) : '--:--';
      const outTime = t.exitTime ? (t.exitTime.length >= 19 ? t.exitTime.substring(11, 16) : '--:--') : 'LIVE';

      rowsHtml += `
        <tr>
          <!-- Time / Duration -->
          <td>
            <div style="font-family:var(--font-mono); font-weight:700; color:var(--text-main);">${inTime} ➔ ${outTime}</div>
            <div style="font-size:10px; color:var(--text-muted); font-family:var(--font-mono);">Duration: ${t.duration}</div>
          </td>

          <!-- Ticker & Setup -->
          <td>
            <div style="font-weight:800; font-size:13px; font-family:var(--font-mono); color:var(--text-main);">${t.symbol}</div>
            <div style="font-size:10px; color:var(--text-muted); text-overflow:ellipsis; overflow:hidden; white-space:nowrap; max-width:130px;" title="${compName} · ${t.setup}">
              ${t.setup}
            </div>
          </td>

          <!-- Direction -->
          <td style="text-align:center;">
            ${dirBadge}
          </td>

          <!-- Execution In -> Out -->
          <td>
            <div style="font-family:var(--font-mono); font-size:11px; font-weight:600;">
              In: <strong style="color:var(--text-main);">$${t.entryPrice.toFixed(2)}</strong>
              ➔ Out: <strong style="color:${t.status === 'OPEN' ? 'var(--cyan)' : 'var(--text-main)'};">$${t.exitPrice ? t.exitPrice.toFixed(2) : '--'}</strong>
            </div>
            <div style="font-size:9.5px; color:var(--text-muted); font-family:var(--font-mono);">
              ${t.status === 'OPEN' ? '🟢 Live Spot' : '🏁 Closed via TV Exit Alert'}
            </div>
          </td>

          <!-- Pts Diff -->
          <td style="text-align:right; font-family:var(--font-mono); font-weight:700; color:${t.diffPts >= 0 ? '#10b981' : '#ef4444'};">
            ${t.diffPts >= 0 ? '+' : ''}${t.diffPts.toFixed(2)} pts
          </td>

          <!-- 100-Sh P&L -->
          <td style="text-align:right;">
            ${pnlBadge}
          </td>

          <!-- AI Triage Call -->
          <td style="text-align:center;">
            ${aiBadge}
          </td>

          <!-- Attribution -->
          <td style="text-align:center;">
            ${attribBadge}
          </td>
        </tr>
      `;
    });

    tableBody.innerHTML = rowsHtml;

    const footerSummary = document.getElementById('pnl-modal-footer-summary');
    if (footerSummary) {
      footerSummary.innerText = `Showing ${list.length} trades · Session: ${this._pnlCurrentSession}`;
    }
  },

  askCopilot(sym, action) {
    const prompt = `Review the TradingView alert for ${sym} (${action}). What is the tactical setup, risk parameters, and recommended trade structure?`;
    if (window.AppChat && typeof window.AppChat.openChatWithPrompt === 'function') {
      window.AppChat.openChatWithPrompt(prompt, sym);
    } else if (window.AppChat && typeof window.AppChat.sendCopilotMsg === 'function') {
      const input = document.getElementById('rev-chat-input');
      if (input) {
        input.value = prompt;
        window.AppChat.sendCopilotMsg();
      }
    }
  },

  _currentTickerTradesSymbol: null,
  _currentTickerTradesDate: null,

  getTickerDayTrades(symbol, targetDate) {
    const sym = (symbol || '').toUpperCase().trim();
    if (!sym) return { symbol: '', dateKey: targetDate, trades: [], alerts: [], summary: {} };

    // Filter raw alerts for this symbol and session date
    const dateAlerts = (this._rawAlerts || []).filter(a => {
      const aSym = (a.symbol || (a.raw_payload ? a.raw_payload.ticker : '') || '').toUpperCase().trim();
      const aDate = a.date || (a.timestamp ? a.timestamp.substring(0, 10) : '');
      if (aSym !== sym) return false;
      if (targetDate && targetDate !== 'ALL' && aDate !== targetDate) return false;
      return true;
    });

    // Sort ascending by timestamp for deterministic entry -> exit sequence
    dateAlerts.sort((a, b) => {
      const ta = a.timestamp || a.created_at || '';
      const tb = b.timestamp || b.created_at || '';
      return ta.localeCompare(tb);
    });

    const trades = [];
    let currentOpen = null;

    for (const a of dateAlerts) {
      const act = (a.action || (a.raw_payload ? a.raw_payload.action : '') || '').toUpperCase().trim();
      const price = parseFloat(a.alert_price || (a.raw_payload ? a.raw_payload.price : 0)) || 0;
      const ts = a.timestamp || a.created_at || '';
      const dec = (a.llm_decision || '').trim();
      const pb = (a.llm_playbook || '').trim();
      const setup = a.setup || (a.raw_payload ? a.raw_payload.setup : '') || a.strategy || 'Intraday';
      const plan = a.plan || (a.raw_payload ? a.raw_payload.plan : '') || '';
      const msgId = a.message_id || a.email_id || `${ts}_${sym}_${act}`;

      const isCall = act.includes('CALL');
      const isPut = act.includes('PUT');
      const isExit = act.includes('EXIT') || act.includes('CUT');

      if ((isCall || isPut) && !isExit) {
        if (currentOpen) {
          trades.push(this._finalizeTickerTrade(currentOpen, null));
        }

        const side = isCall ? 'LONG' : 'SHORT';
        const isTaken = /TAKE\s*CALL|TAKE\s*PUT|GO\s*\(|BUY\s*CALL|BUY\s*PUT/i.test(dec) || /TAKE\s*CALLS|TAKE\s*PUTS/i.test(pb) || /^\s*TAKE\b/i.test(dec);
        const isFiltered = /PASS|STAND\s*ASIDE|WAIT|DO\s*NOT\s*ENTER|VETO/i.test(dec) || /STAND\s*ASIDE|DO\s*NOT\s*ENTER/i.test(pb);

        currentOpen = {
          symbol: sym,
          side: side,
          setup: setup,
          plan: plan,
          entryAlertId: msgId,
          entryAlert: a,
          entryTime: ts,
          entryPrice: price,
          llmDecision: dec,
          llmPlaybook: pb,
          isAiTaken: isTaken,
          isAiFiltered: isFiltered && !isTaken,
          date: a.date || (ts ? ts.substring(0, 10) : targetDate)
        };
      } else if (isExit) {
        if (currentOpen) {
          trades.push(this._finalizeTickerTrade(currentOpen, a));
          currentOpen = null;
        } else {
          trades.push({
            symbol: sym,
            side: 'EXIT_ONLY',
            setup: setup,
            plan: '',
            entryAlertId: null,
            entryAlert: null,
            entryTime: null,
            entryPrice: null,
            exitAlertId: msgId,
            exitAlert: a,
            exitTime: ts,
            exitPrice: price,
            duration: '--',
            diffPts: 0,
            pnl100: 0,
            status: 'UNPAIRED_EXIT',
            isAiTaken: false,
            isAiFiltered: false,
            llmDecision: dec,
            llmPlaybook: pb,
            date: a.date || (ts ? ts.substring(0, 10) : targetDate)
          });
        }
      }
    }

    if (currentOpen) {
      trades.push(this._finalizeTickerTrade(currentOpen, null));
    }

    // Calculate AI-only P&L and comparison KPIs
    let aiPnL = 0;
    let aiWins = 0;
    let aiLosses = 0;
    let aiTradesCount = 0;
    let avoidedLosses = 0;
    let avoidedCount = 0;
    let rawTvPnL = 0;
    let totalCompleted = 0;

    for (const t of trades) {
      if (t.status === 'CLOSED') {
        totalCompleted++;
        rawTvPnL += t.pnl100;

        if (t.isAiTaken) {
          aiPnL += t.pnl100;
          aiTradesCount++;
          if (t.pnl100 > 0) aiWins++;
          else if (t.pnl100 < 0) aiLosses++;
        } else if (t.isAiFiltered) {
          if (t.pnl100 < 0) {
            avoidedLosses += Math.abs(t.pnl100);
            avoidedCount++;
          }
        }
      } else if (t.status === 'OPEN') {
        if (t.isAiTaken) {
          aiTradesCount++;
        }
      }
    }

    const aiWinRate = (aiWins + aiLosses) > 0 ? Math.round((aiWins / (aiWins + aiLosses)) * 1000) / 10 : 0;
    const alpha = aiPnL - rawTvPnL;

    return {
      symbol: sym,
      dateKey: targetDate,
      alerts: dateAlerts,
      trades: trades,
      summary: {
        aiPnL: Math.round(aiPnL * 100) / 100,
        aiWins,
        aiLosses,
        aiTradesCount,
        aiWinRate,
        avoidedLosses: Math.round(avoidedLosses * 100) / 100,
        avoidedCount,
        rawTvPnL: Math.round(rawTvPnL * 100) / 100,
        alpha: Math.round(alpha * 100) / 100,
        totalCompleted,
        totalTrades: trades.length
      }
    };
  },

  _finalizeTickerTrade(entry, exitAlert) {
    if (exitAlert) {
      const exitPrice = parseFloat(exitAlert.alert_price || (exitAlert.raw_payload ? exitAlert.raw_payload.price : 0)) || 0;
      const entryPrice = entry.entryPrice;
      let diffPts = 0;
      let pnl100 = 0;

      if (entryPrice > 0 && exitPrice > 0) {
        diffPts = entry.side === 'LONG' ? (exitPrice - entryPrice) : (entryPrice - exitPrice);
        pnl100 = diffPts * 100.0;
      }

      let durStr = '--';
      if (entry.entryTime && exitAlert.timestamp) {
        try {
          const d1 = new Date(entry.entryTime.replace(' ', 'T'));
          const d2 = new Date(exitAlert.timestamp.replace(' ', 'T'));
          const diffMin = Math.round((d2 - d1) / 60000);
          if (!isNaN(diffMin) && diffMin >= 0) {
            if (diffMin < 60) durStr = `${diffMin}m`;
            else durStr = `${Math.floor(diffMin / 60)}h ${diffMin % 60}m`;
          }
        } catch (e) {}
      }

      const msgId = exitAlert.message_id || exitAlert.email_id || `${exitAlert.timestamp}_${entry.symbol}_EXIT`;

      return {
        ...entry,
        exitAlertId: msgId,
        exitAlert: exitAlert,
        exitTime: exitAlert.timestamp || exitAlert.created_at || '',
        exitPrice: exitPrice,
        duration: durStr,
        diffPts: Math.round(diffPts * 100) / 100,
        pnl100: Math.round(pnl100 * 100) / 100,
        status: 'CLOSED'
      };
    } else {
      const sym = entry.symbol;
      const livePrice = (window.AppState && window.AppState.quotes && window.AppState.quotes[sym])
        ? parseFloat(window.AppState.quotes[sym].last || window.AppState.quotes[sym].price)
        : entry.entryPrice;

      let diffPts = 0;
      let pnl100 = 0;
      if (entry.entryPrice > 0 && livePrice > 0) {
        diffPts = entry.side === 'LONG' ? (livePrice - entry.entryPrice) : (entry.entryPrice - livePrice);
        pnl100 = diffPts * 100.0;
      }

      return {
        ...entry,
        exitAlertId: null,
        exitAlert: null,
        exitTime: null,
        exitPrice: livePrice,
        duration: 'Active Open',
        diffPts: Math.round(diffPts * 100) / 100,
        pnl100: Math.round(pnl100 * 100) / 100,
        status: 'OPEN'
      };
    }
  },

  renderAlertModalTickerTrades(symbol, dateKey, currentAlertId) {
    const cardEl = document.getElementById('alert-modal-ticker-day-trades-card');
    if (!cardEl) return;

    const res = this.getTickerDayTrades(symbol, dateKey);
    const sum = res.summary;
    const pnlSign = sum.aiPnL >= 0 ? '+' : '';
    const pnlColor = sum.aiPnL >= 0 ? '#10b981' : '#ef4444';
    const tvSign = sum.rawTvPnL >= 0 ? '+' : '';

    let tradesHtml = '';
    if (res.trades.length === 0) {
      tradesHtml = `
        <div style="font-size:12px; color:var(--text-muted); text-align:center; padding:16px;">
          No other intraday trades logged for <strong>${symbol}</strong> on ${dateKey}.
        </div>
      `;
    } else {
      res.trades.forEach((t, idx) => {
        const isCurrent = (t.entryAlertId === currentAlertId || t.exitAlertId === currentAlertId);
        const borderStyle = isCurrent
          ? 'border:1.5px solid var(--cyan); box-shadow:0 0 12px rgba(6,182,212,0.25); background:rgba(6,182,212,0.06);'
          : 'border:1px solid rgba(255,255,255,0.08); background:rgba(0,0,0,0.2);';

        const isCall = t.side === 'LONG';
        const dirBadge = isCall
          ? `<span class="badge" style="background:rgba(16,185,129,0.14); color:#10b981; border:1px solid rgba(16,185,129,0.35); font-weight:800; font-size:10px;">🟢 LONG (CALLS)</span>`
          : (t.side === 'SHORT'
              ? `<span class="badge" style="background:rgba(239,68,68,0.14); color:#ef4444; border:1px solid rgba(239,68,68,0.35); font-weight:800; font-size:10px;">🔴 SHORT (PUTS)</span>`
              : `<span class="badge" style="background:var(--bg-subtle); color:var(--text-muted); font-size:10px;">EXIT ONLY</span>`);

        const pSign = t.pnl100 >= 0 ? '+' : '';
        const pColor = t.pnl100 >= 0 ? '#10b981' : '#ef4444';

        let aiBadge = '';
        let pnlTag = '';
        if (t.isAiTaken) {
          aiBadge = `<span class="badge" style="background:rgba(16,185,129,0.15); color:#10b981; border:1px solid rgba(16,185,129,0.4); font-size:10px; font-weight:800;">🤖 AI SUGGESTED TRADE</span>`;
          pnlTag = `<span style="color:#10b981; font-size:9.5px; font-weight:700; font-family:var(--font-mono);">✓ INCLUDED IN AI P&amp;L</span>`;
        } else if (t.isAiFiltered) {
          aiBadge = `<span class="badge" style="background:rgba(239,68,68,0.1); color:#f87171; border:1px solid rgba(239,68,68,0.3); font-size:10px; font-weight:700;">🛡️ AI FILTERED / AVOIDED</span>`;
          pnlTag = `<span style="color:var(--text-muted); font-size:9.5px; font-style:italic; font-family:var(--font-mono);">✗ EXCLUDED (AI Passed)</span>`;
        } else {
          aiBadge = `<span class="badge" style="background:rgba(255,255,255,0.06); color:var(--text-muted); border:1px solid var(--border); font-size:10px;">⏳ UNTRIAGED</span>`;
          pnlTag = `<span style="color:var(--text-muted); font-size:9.5px; font-family:var(--font-mono);">Untriaged</span>`;
        }

        const entryTime = t.entryTime && t.entryTime.length >= 19 ? t.entryTime.substring(11, 19) : (t.entryTime || '--:--');
        const exitTime = t.exitTime && t.exitTime.length >= 19 ? t.exitTime.substring(11, 19) : (t.exitTime || (t.status === 'OPEN' ? 'ACTIVE' : '--:--'));

        tradesHtml += `
          <div style="border-radius:8px; padding:10px 12px; display:flex; flex-direction:column; gap:8px; ${borderStyle}">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-weight:800; font-family:var(--font-mono); font-size:12px; color:var(--text-main);">
                  Trade #${idx + 1}
                </span>
                ${dirBadge}
                ${aiBadge}
                ${isCurrent ? '<span class="pill cyan" style="font-size:9px; padding:1px 5px;">📍 Currently Viewing</span>' : ''}
              </div>
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-family:var(--font-mono); font-size:13px; font-weight:800; color:${pColor};" title="100-Share P&amp;L">
                  ${t.status === 'OPEN' ? `${pSign}$${t.pnl100.toFixed(2)} (Live)` : `${pSign}$${t.pnl100.toFixed(2)} (${pSign}${t.diffPts.toFixed(2)} pts)`}
                </span>
                ${pnlTag}
              </div>
            </div>

            <!-- Grouped Alerts Details -->
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:8px; font-size:11px; font-family:var(--font-mono); background:rgba(0,0,0,0.15); padding:8px 10px; border-radius:6px;">
              <div>
                <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">📥 ENTRY ALERT</div>
                <div style="color:var(--text-main); font-weight:700;">${entryTime} ET @ $${t.entryPrice !== null ? t.entryPrice.toFixed(2) : '--'}</div>
                <div style="font-size:10px; color:var(--text-muted); text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">${t.setup || 'Intraday'}</div>
              </div>
              <div>
                <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">📤 EXIT ALERT</div>
                <div style="color:var(--text-main); font-weight:700;">${exitTime} ET @ $${t.exitPrice ? t.exitPrice.toFixed(2) : '--'}</div>
                <div style="font-size:10px; color:var(--text-muted);">Duration: ${t.duration}</div>
              </div>
              <div style="display:flex; align-items:center; justify-content:flex-end; gap:6px;">
                ${t.entryAlertId ? `<button class="btn secondary" onclick="AppAlerts.openAlertModal('${t.entryAlertId}')" style="padding:2px 7px; font-size:10px; font-weight:700;">Entry Alert</button>` : ''}
                ${t.exitAlertId ? `<button class="btn secondary" onclick="AppAlerts.openAlertModal('${t.exitAlertId}')" style="padding:2px 7px; font-size:10px; font-weight:700;">Exit Alert</button>` : ''}
              </div>
            </div>
          </div>
        `;
      });
    }

    cardEl.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; padding-bottom:8px; border-bottom:1px solid rgba(255,255,255,0.06);">
        <div style="display:flex; align-items:center; gap:8px;">
          <span style="font-size:12px; font-weight:800; color:var(--cyan-glow); letter-spacing:0.5px;">
            📅 ${symbol} SESSION TRADES (${dateKey})
          </span>
          <span class="pill" style="font-size:10px; font-family:var(--font-mono);">${res.alerts.length} Alerts · ${res.trades.length} Trades</span>
        </div>
        <button class="btn secondary" onclick="AppAlerts.openTickerTradesModal('${symbol}', '${dateKey}')" style="padding:3px 9px; font-size:11px; font-weight:800; color:var(--cyan-glow); border-color:rgba(6,182,212,0.4);" title="Open expanded Day Trades modal for ${symbol}">
          🗖 Expand AI P&amp;L View
        </button>
      </div>

      <!-- Hero P&L Strip (AI Suggested Trades Only) -->
      <div style="background:linear-gradient(135deg, rgba(16,185,129,0.12) 0%, rgba(6,182,212,0.06) 100%); border:1px solid rgba(16,185,129,0.3); border-radius:8px; padding:10px 14px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
        <div>
          <div style="font-size:10px; font-weight:800; color:#10b981; letter-spacing:0.5px; text-transform:uppercase;">
            🤖 AI SUGGESTED TRADES ONLY — 100-SHARE P&amp;L
          </div>
          <div style="font-size:22px; font-weight:900; font-family:var(--font-mono); color:${pnlColor}; margin-top:2px;">
            ${pnlSign}$${sum.aiPnL.toFixed(2)}
          </div>
          <div style="font-size:10.5px; color:var(--text-muted);">
            Calculated <strong style="color:var(--text-main);">on AI-suggested trades only</strong> (${sum.aiTradesCount} taken · ${sum.aiWins}W / ${sum.aiLosses}L · ${sum.aiWinRate}% WR). Filtered alerts are excluded.
          </div>
        </div>
        <div style="display:flex; align-items:center; gap:8px;">
          <div style="background:rgba(0,0,0,0.25); border:1px solid rgba(255,255,255,0.08); padding:5px 10px; border-radius:5px; text-align:center;">
            <div style="font-size:9px; color:var(--text-muted); font-weight:700;">AI LOSSES SAVED</div>
            <div style="font-size:12px; font-weight:800; color:#38bdf8; font-family:var(--font-mono);">+$${sum.avoidedLosses.toFixed(2)}</div>
          </div>
          <div style="background:rgba(0,0,0,0.25); border:1px solid rgba(255,255,255,0.08); padding:5px 10px; border-radius:5px; text-align:center;">
            <div style="font-size:9px; color:var(--text-muted); font-weight:700;">RAW TV P&amp;L</div>
            <div style="font-size:12px; font-weight:800; color:var(--text-main); font-family:var(--font-mono);">${tvSign}$${sum.rawTvPnL.toFixed(2)}</div>
          </div>
        </div>
      </div>

      <!-- Grouped Trades List -->
      <div style="display:flex; flex-direction:column; gap:8px; margin-top:4px;">
        ${tradesHtml}
      </div>
    `;
  },

  openTickerTradesModal(symbol, dateKey) {
    const sym = (symbol || '').toUpperCase().trim();
    if (!sym) return;

    this._currentTickerTradesSymbol = sym;
    this._currentTickerTradesDate = dateKey;

    const modal = document.getElementById('modal-ticker-day-trades');
    const symEl = document.getElementById('ttm-ticker-sym');
    const nameEl = document.getElementById('ttm-ticker-name');
    const dateEl = document.getElementById('ttm-session-date');
    const countEl = document.getElementById('ttm-alert-count');
    const heroEl = document.getElementById('ttm-pnl-hero');
    const listEl = document.getElementById('ttm-trades-list');

    const compName = window.AppUtils ? (window.AppUtils.getCompanyName(sym) || sym) : sym;
    if (symEl) symEl.innerText = sym;
    if (nameEl) nameEl.innerText = compName;
    if (dateEl) dateEl.innerText = `📅 Session: ${dateKey}`;

    const res = this.getTickerDayTrades(sym, dateKey);
    const sum = res.summary;
    if (countEl) countEl.innerText = `${res.alerts.length} Alert${res.alerts.length === 1 ? '' : 's'} (${res.trades.length} Trade${res.trades.length === 1 ? '' : 's'})`;

    const pnlSign = sum.aiPnL >= 0 ? '+' : '';
    const pnlColor = sum.aiPnL >= 0 ? '#10b981' : '#ef4444';
    const tvSign = sum.rawTvPnL >= 0 ? '+' : '';
    const alphaSign = sum.alpha >= 0 ? '+' : '';

    if (heroEl) {
      heroEl.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; background:linear-gradient(135deg, rgba(16,185,129,0.14) 0%, rgba(6,182,212,0.06) 100%); border:1px solid rgba(16,185,129,0.35); border-radius:10px; padding:16px;">
          <div>
            <div style="font-size:11px; font-weight:800; color:#10b981; letter-spacing:0.8px; text-transform:uppercase;">
              🤖 AI SUGGESTED TRADES ONLY — 100-SHARE P&amp;L
            </div>
            <div style="font-size:32px; font-weight:900; font-family:var(--font-mono); color:${pnlColor}; margin-top:3px;">
              ${pnlSign}$${sum.aiPnL.toFixed(2)}
            </div>
            <div style="font-size:12px; color:var(--text-muted); font-weight:500; margin-top:2px;">
              Calculated <strong style="color:var(--text-main);">strictly on AI suggested trades</strong> (${sum.aiTradesCount} taken · ${sum.aiWins}W / ${sum.aiLosses}L · ${sum.aiWinRate}% Win Rate). Filtered alerts are excluded.
            </div>
          </div>

          <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
            <div style="background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.08); padding:8px 12px; border-radius:6px; text-align:center; min-width:90px;">
              <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">AI LOSSES SAVED</div>
              <div style="font-size:14px; font-weight:800; color:#38bdf8; font-family:var(--font-mono); margin-top:2px;">+$${sum.avoidedLosses.toFixed(2)}</div>
              <div style="font-size:9px; color:var(--text-muted);">${sum.avoidedCount} filtered</div>
            </div>

            <div style="background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.08); padding:8px 12px; border-radius:6px; text-align:center; min-width:90px;">
              <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">RAW TV SYSTEM</div>
              <div style="font-size:14px; font-weight:800; color:var(--text-main); font-family:var(--font-mono); margin-top:2px;">${tvSign}$${sum.rawTvPnL.toFixed(2)}</div>
              <div style="font-size:9px; color:var(--text-muted);">unfiltered</div>
            </div>

            <div style="background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.08); padding:8px 12px; border-radius:6px; text-align:center; min-width:90px;">
              <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">AI ALPHA EDGE</div>
              <div style="font-size:14px; font-weight:800; color:#c084fc; font-family:var(--font-mono); margin-top:2px;">${alphaSign}$${sum.alpha.toFixed(2)}</div>
              <div style="font-size:9px; color:var(--text-muted);">vs baseline</div>
            </div>
          </div>
        </div>
      `;
    }

    if (listEl) {
      if (res.trades.length === 0) {
        listEl.innerHTML = `
          <div style="text-align:center; padding:40px; color:var(--text-muted); font-size:13px;">
            No intraday trade alerts found for <strong>${sym}</strong> on ${dateKey}.
          </div>
        `;
      } else {
        let tradesHtml = '';
        res.trades.forEach((t, idx) => {
          const isCall = t.side === 'LONG';
          const dirBadge = isCall
            ? `<span class="badge" style="background:rgba(16,185,129,0.15); color:#10b981; border:1px solid rgba(16,185,129,0.4); font-weight:800; font-size:11px; padding:3px 8px;">🟢 LONG (CALLS)</span>`
            : (t.side === 'SHORT'
                ? `<span class="badge" style="background:rgba(239,68,68,0.15); color:#ef4444; border:1px solid rgba(239,68,68,0.4); font-weight:800; font-size:11px; padding:3px 8px;">🔴 SHORT (PUTS)</span>`
                : `<span class="badge" style="background:var(--bg-subtle); color:var(--text-muted); font-size:11px;">EXIT ONLY</span>`);

          let aiBadge = '';
          let pnlTag = '';
          let borderGlow = 'border:1px solid rgba(255,255,255,0.08); background:rgba(15,23,42,0.4);';

          if (t.isAiTaken) {
            aiBadge = `<span class="badge" style="background:rgba(16,185,129,0.18); color:#10b981; border:1px solid rgba(16,185,129,0.45); font-size:11px; font-weight:800; padding:3px 9px;">🤖 AI SUGGESTED TRADE</span>`;
            pnlTag = `<span style="color:#10b981; font-size:10.5px; font-weight:800; font-family:var(--font-mono); background:rgba(16,185,129,0.12); padding:2px 8px; border-radius:4px; border:1px solid rgba(16,185,129,0.3);">✓ INCLUDED IN AI P&amp;L</span>`;
            borderGlow = 'border:1px solid rgba(16,185,129,0.35); background:rgba(16,185,129,0.03);';
          } else if (t.isAiFiltered) {
            aiBadge = `<span class="badge" style="background:rgba(239,68,68,0.12); color:#f87171; border:1px solid rgba(239,68,68,0.3); font-size:11px; font-weight:700; padding:3px 9px;">🛡️ AI FILTERED / PASSED</span>`;
            pnlTag = `<span style="color:var(--text-muted); font-size:10.5px; font-style:italic; font-family:var(--font-mono); background:rgba(255,255,255,0.04); padding:2px 8px; border-radius:4px; border:1px solid var(--border);">✗ EXCLUDED (AI Passed)</span>`;
          } else {
            aiBadge = `<span class="badge" style="background:rgba(255,255,255,0.06); color:var(--text-muted); border:1px solid var(--border); font-size:11px; padding:3px 8px;">⏳ UNTRIAGED</span>`;
            pnlTag = `<span style="color:var(--text-muted); font-size:10.5px; font-family:var(--font-mono);">Untriaged</span>`;
          }

          const pSign = t.pnl100 >= 0 ? '+' : '';
          const pColor = t.pnl100 >= 0 ? '#10b981' : '#ef4444';

          const entryTime = t.entryTime && t.entryTime.length >= 19 ? t.entryTime.substring(11, 19) : (t.entryTime || '--:--');
          const exitTime = t.exitTime && t.exitTime.length >= 19 ? t.exitTime.substring(11, 19) : (t.exitTime || (t.status === 'OPEN' ? 'ACTIVE' : '--:--'));

          tradesHtml += `
            <div style="border-radius:10px; padding:14px 16px; display:flex; flex-direction:column; gap:12px; ${borderGlow}">
              <!-- Trade Header -->
              <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                  <span style="font-weight:800; font-family:var(--font-mono); font-size:14px; color:var(--text-main);">
                    Trade #${idx + 1}
                  </span>
                  ${dirBadge}
                  ${aiBadge}
                  <span style="font-size:11px; color:var(--text-muted); font-family:var(--font-mono);">
                    Duration: <strong>${t.duration}</strong>
                  </span>
                </div>
                <div style="display:flex; align-items:center; gap:10px;">
                  <span style="font-family:var(--font-mono); font-size:16px; font-weight:900; color:${pColor};">
                    ${t.status === 'OPEN' ? `${pSign}$${t.pnl100.toFixed(2)} (Live)` : `${pSign}$${t.pnl100.toFixed(2)} (${pSign}${t.diffPts.toFixed(2)} pts)`}
                  </span>
                  ${pnlTag}
                </div>
              </div>

              <!-- Grouped Trade Alerts (Entry ➔ Exit) -->
              <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:12px;">
                
                <!-- 1. Entry Alert Card -->
                <div style="background:rgba(0,0,0,0.25); border:1px solid rgba(255,255,255,0.06); border-radius:8px; padding:10px 12px; display:flex; flex-direction:column; gap:6px;">
                  <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:10px; font-weight:800; color:var(--cyan-glow); letter-spacing:0.5px;">
                      📥 ENTRY ALERT (${entryTime} ET)
                    </span>
                    ${t.entryAlertId ? `<button class="btn secondary" onclick="AppAlerts.openAlertModal('${t.entryAlertId}')" style="padding:1px 6px; font-size:10px; font-weight:700;">🔍 Inspect</button>` : ''}
                  </div>
                  <div style="display:flex; align-items:center; justify-content:space-between; font-family:var(--font-mono); font-size:12px;">
                    <span>Trigger Price:</span>
                    <strong style="color:var(--text-main); font-size:13px;">$${t.entryPrice !== null ? t.entryPrice.toFixed(2) : '--'}</strong>
                  </div>
                  <div style="font-size:11px; color:var(--text-muted);">
                    <strong>Setup:</strong> <span style="color:var(--text-main);">${t.setup || 'Intraday'}</span>
                  </div>
                  <div style="font-size:11px; color:var(--text-muted); background:rgba(0,0,0,0.2); padding:5px 8px; border-radius:4px; margin-top:2px;">
                    <strong>AI Triage:</strong> <span style="color:${t.isAiTaken ? '#10b981' : '#f87171'}; font-weight:700;">${t.llmDecision || 'Pending'}</span>
                  </div>
                  ${t.plan ? `<div style="font-size:10px; color:var(--text-muted); font-family:var(--font-mono); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${t.plan.replace(/"/g, '&quot;')}">Plan: ${t.plan}</div>` : ''}
                </div>

                <!-- 2. Exit Alert Card -->
                <div style="background:rgba(0,0,0,0.25); border:1px solid rgba(255,255,255,0.06); border-radius:8px; padding:10px 12px; display:flex; flex-direction:column; gap:6px;">
                  <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:10px; font-weight:800; color:var(--amber); letter-spacing:0.5px;">
                      📤 EXIT ALERT (${exitTime} ET)
                    </span>
                    ${t.exitAlertId ? `<button class="btn secondary" onclick="AppAlerts.openAlertModal('${t.exitAlertId}')" style="padding:1px 6px; font-size:10px; font-weight:700;">🔍 Inspect</button>` : ''}
                  </div>
                  <div style="display:flex; align-items:center; justify-content:space-between; font-family:var(--font-mono); font-size:12px;">
                    <span>Exit Price:</span>
                    <strong style="color:var(--text-main); font-size:13px;">$${t.exitPrice ? t.exitPrice.toFixed(2) : '--'}</strong>
                  </div>
                  <div style="font-size:11px; color:var(--text-muted);">
                    <strong>Status:</strong> <span style="color:var(--text-main);">${t.status === 'OPEN' ? '🟢 Live Spot Active' : '🏁 Closed via TV Exit'}</span>
                  </div>
                  <div style="font-size:11px; color:var(--text-muted); background:rgba(0,0,0,0.2); padding:5px 8px; border-radius:4px; margin-top:2px;">
                    <strong>Outcome:</strong> <span style="font-weight:700; color:${pColor};">${pSign}$${t.pnl100.toFixed(2)} (${pSign}${t.diffPts.toFixed(2)} pts)</span>
                  </div>
                </div>

              </div>
            </div>
          `;
        });
        listEl.innerHTML = tradesHtml;
      }
    }

    if (modal) {
      modal.style.display = 'flex';
    }
  },

  closeTickerTradesModal() {
    const modal = document.getElementById('modal-ticker-day-trades');
    if (modal) {
      modal.style.display = 'none';
    }
  },

  openChartFromTickerModal() {
    if (this._currentTickerTradesSymbol) {
      this.openChart(this._currentTickerTradesSymbol);
    }
  }
};

document.addEventListener('click', (e) => {
  const wrapper = document.getElementById('tv-alerts-calendar-wrapper');
  const popover = document.getElementById('tv-alerts-calendar-popover');
  if (wrapper && popover && popover.style.display !== 'none') {
    if (!wrapper.contains(e.target)) {
      popover.style.display = 'none';
    }
  }
});
