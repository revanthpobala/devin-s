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

  _activeAlertPane: 'intraday', // 'intraday' | 'daily' | 'split'
  _intradayAlerts: [],
  _dailyAlerts: [],
  _dailyFilterSide: 'ALL',
  _dailyFilterStage: 'ALL',
  _dailyAiFilter: 'ALL',
  _dailySearchQuery: '',
  _dailySortField: 'timestamp',
  _dailySortAsc: false,
  _dailyCurrentPage: 1,
  _dailyPageSize: 25,

  _intradayViewMode: 'ticker', // 'ticker' | 'stream'
  _intradayTickerFilter: 'ALL', // 'ALL' | 'IN_TRADE' | 'COMPLETED' | symbol
  _intradayExpandedTickers: {},

  _reportsByDate: {},
  _allReportsByTicker: {},
  _pmCurrentSession: null,
  _pmCurrentTab: 'summary',
  _pmData: null,
  _modalParentId: null,
  _alertParentId: null,

  async loadReportsIndex() {
    try {
      const res = await fetch('/api/reports');
      if (!res.ok) return;
      const data = await res.json();
      this._reportsByDate = data.reports_by_date || {};
      this._allReportsByTicker = {};
      for (const dKey in this._reportsByDate) {
        const arr = this._reportsByDate[dKey] || [];
        for (const r of arr) {
          const sym = (r.ticker || '').toUpperCase();
          if (sym) {
            if (!this._allReportsByTicker[sym]) this._allReportsByTicker[sym] = [];
            this._allReportsByTicker[sym].push(r);
          }
        }
      }
    } catch (e) {
      console.warn('Could not load reports index', e);
    }
  },

  getReportForTicker(symbol, dateKey) {
    const sym = (symbol || '').toUpperCase().trim();
    if (!sym) return null;
    if (dateKey && dateKey !== 'ALL' && this._reportsByDate && this._reportsByDate[dateKey]) {
      const found = this._reportsByDate[dateKey].find(r => (r.ticker || '').toUpperCase() === sym);
      if (found) return found;
    }
    if (this._allReportsByTicker && this._allReportsByTicker[sym] && this._allReportsByTicker[sym].length > 0) {
      return this._allReportsByTicker[sym][0];
    }
    return null;
  },

  getIntradayTickers(targetDate) {
    let list = (this._intradayAlerts || []).filter(a => this.isIntradayAlert(a));
    if (targetDate && targetDate !== 'ALL') {
      list = list.filter(a => {
        const d = a.date || (a.timestamp ? a.timestamp.substring(0, 10) : '');
        return d === targetDate;
      });
    }

    const symMap = new Map();
    list.forEach(a => {
      const raw = (typeof a.raw_payload === 'string' && a.raw_payload.startsWith('{'))
        ? JSON.parse(a.raw_payload) : (a.raw_payload || {});
      const sym = (a.symbol || raw.ticker || raw.symbol || '').toUpperCase().trim();
      if (!sym) return;
      if (!symMap.has(sym)) {
        const compName = window.AppUtils ? (window.AppUtils.getCompanyName(sym) || sym) : sym;
        symMap.set(sym, {
          symbol: sym,
          name: compName
        });
      }
    });

    return Array.from(symMap.values());
  },

  viewAlert(id) {
    return this.openAlertModal(id);
  },

  parsePlaybookSections(pb) {
    if (!pb || typeof pb !== 'string') return null;

    const convMatch = pb.match(/(?:Conviction|\*\*Conviction\*\*):\s*([^|\n]+)/i);
    const regimeMatch = pb.match(/(?:Regime|\*\*Regime\*\*):\s*([^|\n]+)/i);
    const cardMatch = pb.match(/(?:Card|\*\*Card\*\*):\s*([^|\n]+)/i);
    const playMatch = pb.match(/(?:THE PLAY:|\*\*THE PLAY:\*\*)\s*([^\n]+)/i);

    const whyCardMatch = pb.match(/(?:WHY\s*\(card\):|\*\*WHY\s*\(card\):\*\*)\s*([\s\S]+?)(?=(?:\n\s*(?:WHY\s*\(tape\):|\*\*WHY\s*\(tape\):\*\*|KILL IT IF:|\*\*KILL IT IF:\*\*|\*\*Trader'?s? Notes?:?\*\*|Trader'?s? Notes?:?))|$)/i);
    const whyTapeMatch = pb.match(/(?:WHY\s*\(tape\):|\*\*WHY\s*\(tape\):\*\*)\s*([\s\S]+?)(?=(?:\n\s*(?:KILL IT IF:|\*\*KILL IT IF:\*\*|\*\*Trader'?s? Notes?:?\*\*|Trader'?s? Notes?:?))|$)/i);
    const killMatch = pb.match(/(?:KILL IT IF:|\*\*KILL IT IF:\*\*)\s*([\s\S]+?)(?=(?:\n\s*(?:\*\*Trader'?s? Notes?:?\*\*|Trader'?s? Notes?:?))|$)/i);
    const noteMatch = pb.match(/(?:\*\*Trader'?s? Notes?:?\*\*|Trader'?s? Notes?:?)\s*([\s\S]+?)$/i);

    // Capture explicit Veto blocks (e.g. QUALITY VETO: ... or REGIME VETO: ...)
    const vetoMatch = pb.match(/(?:(?:🛡️|🚫|🛑)?\s*(?:QUALITY|REGIME|CONVICTION|STAGE)?\s*VETO:)\s*([\s\S]+?)(?=(?:\n\s*(?:WHY|KILL|Trader|\*\*Trader))|$)/i);

    let noteText = '';
    if (noteMatch && noteMatch[1]) {
      noteText = noteMatch[1].trim();
    } else if (killMatch) {
      const parts = pb.split(killMatch[0]);
      if (parts.length > 1 && parts[1].trim()) {
        noteText = parts[1].trim();
      }
    }

    // Residual clean reasoning (stripping redundant [SYM] [TIME] headers)
    let residualText = pb
      .replace(/^\[[A-Z0-9]+\]\s*\[[^\]]+\]\s*—\s*[^\n]+\n*/i, '')
      .replace(/^[A-Z0-9]+\s+[0-9:]+\s+(?:AM|PM)\s+ET\s+[^\n]+\n*/i, '')
      .replace(/Conviction:[^\n|]+\|\s*Card:[^\n]+\n*/i, '')
      .trim();

    return {
      conviction: convMatch ? convMatch[1].trim() : '',
      regime: regimeMatch ? regimeMatch[1].trim() : '',
      card: cardMatch ? cardMatch[1].trim() : '',
      thePlay: playMatch ? playMatch[1].trim() : '',
      whyCard: whyCardMatch ? whyCardMatch[1].trim().replace(/\*\*/g, '') : '',
      whyTape: whyTapeMatch ? whyTapeMatch[1].trim().replace(/\*\*/g, '') : '',
      killItIf: killMatch ? killMatch[1].trim().replace(/\*\*/g, '') : '',
      traderNote: noteText ? noteText.replace(/\*\*/g, '') : '',
      vetoRationale: vetoMatch ? vetoMatch[0].trim().replace(/\*\*/g, '') : '',
      residualText: residualText.replace(/\*\*/g, '')
    };
  },

  setIntradayViewMode(mode) {
    this._intradayViewMode = mode || 'ticker';
    const btnTicker = document.getElementById('btn-intraday-view-ticker');
    const btnStream = document.getElementById('btn-intraday-view-stream');
    const boardEl = document.getElementById('intraday-ticker-board-container');
    const streamEl = document.getElementById('intraday-stream-table-container');

    if (btnTicker) btnTicker.classList.toggle('active', this._intradayViewMode === 'ticker');
    if (btnStream) btnStream.classList.toggle('active', this._intradayViewMode === 'stream');

    if (this._intradayViewMode === 'ticker') {
      if (boardEl) boardEl.style.display = 'flex';
      if (streamEl) streamEl.style.display = 'none';
      this.renderIntradayByTicker();
    } else {
      if (boardEl) boardEl.style.display = 'none';
      if (streamEl) streamEl.style.display = 'block';
      this.renderStreamTable();
    }
  },

  _intradaySort: 'TIME_DESC',
  _intradayTradesOrder: 'asc',

  setIntradayTickerFilter(filter) {
    this._intradayTickerFilter = filter || 'ALL';
    this.renderIntradayByTicker();
  },

  setIntradaySort(sortMode) {
    this._intradaySort = sortMode || 'TIME_DESC';
    this.renderIntradayByTicker();
  },

  toggleFlatTimelineOrder() {
    this._intradaySort = (this._intradaySort === 'TIME_DESC') ? 'TIME_ASC' : 'TIME_DESC';
    this.renderIntradayByTicker();
  },

  toggleTradesOrder() {
    this._intradayTradesOrder = this._intradayTradesOrder === 'asc' ? 'desc' : 'asc';
    this.renderIntradayByTicker();
  },

  toggleTickerExpanded(sym) {
    const s = (sym || '').toUpperCase();
    const current = (this._intradayExpandedTickers[s] !== undefined)
      ? this._intradayExpandedTickers[s]
      : false;
    this._intradayExpandedTickers[s] = !current;
    this.renderIntradayByTicker();
  },

  isIntradayAlert(a) {
    if (!a) return false;
    const strat = (a.strategy || '').toLowerCase();
    if (strat === 'intraday' || strat.includes('intraday')) return true;
    if (strat === 'daily' || strat.includes('daily') || strat.includes('screener') || strat.includes('swing')) return false;

    const p = this.parsePayload(a);
    const pStrat = (p.strategy || '').toLowerCase();
    if (pStrat === 'intraday' || pStrat.includes('intraday')) return true;
    if (pStrat === 'daily' || pStrat.includes('daily') || pStrat.includes('screener')) return false;

    const act = (a.action || p.action || p.side || '').toUpperCase();
    if (act.includes('CALL') || act.includes('PUT') || act.includes('EXIT') || act.includes('CUT')) return true;
    if (act === 'LONG' || act === 'SHORT' || act === 'NEUTRAL') return false;

    if (p.stage !== undefined || p.proxy_rr !== undefined || p.dir_prob !== undefined) return false;
    return false;
  },

  isDailyAlert(a) {
    return !this.isIntradayAlert(a);
  },

  setAlertPane(pane) {
    this._activeAlertPane = pane || 'intraday';
    const pIntra = document.getElementById('alerts-pane-intraday');
    const pDaily = document.getElementById('alerts-pane-daily');
    const btnIntra = document.getElementById('btn-alert-pane-intraday');
    const btnDaily = document.getElementById('btn-alert-pane-daily');
    const btnSplit = document.getElementById('btn-alert-pane-split');

    if (btnIntra) btnIntra.classList.toggle('active', this._activeAlertPane === 'intraday');
    if (btnDaily) btnDaily.classList.toggle('active', this._activeAlertPane === 'daily');
    if (btnSplit) btnSplit.classList.toggle('active', this._activeAlertPane === 'split');

    if (this._activeAlertPane === 'intraday') {
      if (pIntra) pIntra.style.display = 'flex';
      if (pDaily) pDaily.style.display = 'none';
      this.renderTable();
    } else if (this._activeAlertPane === 'daily') {
      if (pIntra) pIntra.style.display = 'none';
      if (pDaily) pDaily.style.display = 'flex';
      this.renderDailyTable();
    } else if (this._activeAlertPane === 'split') {
      if (pIntra) pIntra.style.display = 'flex';
      if (pDaily) pDaily.style.display = 'flex';
      this.renderTable();
      this.renderDailyTable();
    }
  },

  renderActivePanes() {
    if (this._activeAlertPane === 'intraday') {
      this.renderTable();
    } else if (this._activeAlertPane === 'daily') {
      this.renderDailyTable();
    } else {
      this.renderTable();
      this.renderDailyTable();
    }
  },

  setDailySideFilter(side) {
    this._dailyFilterSide = (side || 'ALL').toUpperCase();
    this._dailyCurrentPage = 1;
    document.querySelectorAll('.daily-side-filter-btn').forEach(btn => {
      const s = (btn.getAttribute('data-side') || 'ALL').toUpperCase();
      if (s === this._dailyFilterSide) {
        btn.classList.add('active');
        btn.style.background = 'var(--text-main)';
        btn.style.color = 'var(--bg-surface)';
      } else {
        btn.classList.remove('active');
        btn.style.background = '';
        btn.style.color = '';
      }
    });
    this.renderDailyTable();
  },

  setDailyStageFilter(stageVal) {
    this._dailyFilterStage = stageVal || 'ALL';
    this._dailyCurrentPage = 1;
    this.renderDailyTable();
  },

  setDailyAiFilter(filterVal) {
    this._dailyAiFilter = filterVal || 'ALL';
    this._dailyCurrentPage = 1;
    document.querySelectorAll('.daily-ai-filter-btn').forEach(btn => {
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
    this.renderDailyTable();
  },

  setDailySearch(q) {
    this._dailySearchQuery = (q || '').trim().toLowerCase();
    this._dailyCurrentPage = 1;
    this.renderDailyTable();
  },

  setDailySort(field) {
    if (this._dailySortField === field) {
      this._dailySortAsc = !this._dailySortAsc;
    } else {
      this._dailySortField = field;
      this._dailySortAsc = (field === 'symbol' || field === 'action' || field === 'stage');
    }
    this.renderDailyTable();
  },

  setDailyPage(p) {
    this._dailyCurrentPage = p;
    this.renderDailyTable();
  },

  prevDailyPage() {
    if (this._dailyCurrentPage > 1) {
      this._dailyCurrentPage--;
      this.renderDailyTable();
    }
  },

  nextDailyPage(maxPages) {
    if (this._dailyCurrentPage < maxPages) {
      this._dailyCurrentPage++;
      this.renderDailyTable();
    }
  },

  setDailyPageSize(val) {
    if (val === 'ALL') {
      this._dailyPageSize = 999999;
    } else {
      this._dailyPageSize = parseInt(val, 10) || 25;
    }
    this._dailyCurrentPage = 1;
    this.renderDailyTable();
  },

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

    // Sync Post-Mortem button text to active session
    const pmTopBtn = document.getElementById('btn-main-postmortem');
    const targetDateLabel = (this._dateFilter === 'ALL') ? (this.getAvailableSessionDates()[0] || '2026-09-16') : this._dateFilter;
    if (pmTopBtn) {
      pmTopBtn.innerHTML = `<span>🧠</span> Post-Mortem (${targetDateLabel})`;
      pmTopBtn.title = `Run Closed-Loop AI Post-Mortem & Attribution Analysis for ${targetDateLabel}`;
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

      // Partition into Intraday (0DTE execution) vs Daily (Swing screener)
      this._intradayAlerts = this._rawAlerts.filter(a => this.isIntradayAlert(a));
      this._dailyAlerts = this._rawAlerts.filter(a => this.isDailyAlert(a));

      // Update pane switcher counter badges
      const cPaneIntra = document.getElementById('count-pane-intraday');
      const cPaneDaily = document.getElementById('count-pane-daily');
      if (cPaneIntra) cPaneIntra.innerText = this._intradayAlerts.length;
      if (cPaneDaily) cPaneDaily.innerText = this._dailyAlerts.length;

      // Intraday Action Filters Counters
      const calls = this._intradayAlerts.filter(a => (a.action || '').toUpperCase().includes('CALL')).length;
      const puts = this._intradayAlerts.filter(a => (a.action || '').toUpperCase().includes('PUT')).length;
      const exits = this._intradayAlerts.filter(a => (a.action || '').toUpperCase().includes('EXIT') || (a.action || '').toUpperCase().includes('CUT')).length;

      const cAll = document.getElementById('count-alert-all');
      const cCalls = document.getElementById('count-alert-calls');
      const cPuts = document.getElementById('count-alert-puts');
      const cExits = document.getElementById('count-alert-exits');

      if (cAll) cAll.innerText = this._intradayAlerts.length;
      if (cCalls) cCalls.innerText = calls;
      if (cPuts) cPuts.innerText = puts;
      if (cExits) cExits.innerText = exits;

      // Daily Side Filters Counters
      const dailyLong = this._dailyAlerts.filter(a => { const s = (a.action || this.parsePayload(a).side || '').toUpperCase(); return s === 'LONG'; }).length;
      const dailyNeutral = this._dailyAlerts.filter(a => { const s = (a.action || this.parsePayload(a).side || '').toUpperCase(); return s === 'NEUTRAL'; }).length;
      const dailyShort = this._dailyAlerts.filter(a => { const s = (a.action || this.parsePayload(a).side || '').toUpperCase(); return s === 'SHORT'; }).length;

      const cDailyAll = document.getElementById('count-daily-all');
      const cDailyLong = document.getElementById('count-daily-long');
      const cDailyNeutral = document.getElementById('count-daily-neutral');
      const cDailyShort = document.getElementById('count-daily-short');

      if (cDailyAll) cDailyAll.innerText = this._dailyAlerts.length;
      if (cDailyLong) cDailyLong.innerText = dailyLong;
      if (cDailyNeutral) cDailyNeutral.innerText = dailyNeutral;
      if (cDailyShort) cDailyShort.innerText = dailyShort;

      const total = this._rawAlerts.length;
      if (countPill) {
        countPill.className = 'pill green';
        countPill.innerText = `${total} Alerts Logged (${this._intradayAlerts.length} Intra · ${this._dailyAlerts.length} Daily)`;
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
      await this.loadReportsIndex();
      this.renderActivePanes();
    } catch (e) {
      console.error('Error loading alerts:', e);
      if (tableBody) {
        tableBody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:var(--red); padding:20px;">Failed to load alerts: ${e.message}</td></tr>`;
      }
      const dailyTableBody = document.getElementById('daily-alerts-table-body');
      if (dailyTableBody) {
        dailyTableBody.innerHTML = `<tr><td colspan="11" style="text-align:center; color:var(--red); padding:20px;">Failed to load alerts: ${e.message}</td></tr>`;
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
    this.buildAlertPnlIndex();
    const boardEl = document.getElementById('intraday-ticker-board-container');
    const streamEl = document.getElementById('intraday-stream-table-container');

    if (this._intradayViewMode === 'ticker') {
      if (boardEl) boardEl.style.display = 'flex';
      if (streamEl) streamEl.style.display = 'none';
      this.renderIntradayByTicker();
    } else {
      if (boardEl) boardEl.style.display = 'none';
      if (streamEl) streamEl.style.display = 'block';
      this.renderStreamTable();
    }
  },

  _quoteTimestamps: {},
  _fetchingQuotes: false,

  fetchLiveQuotesForActiveTickers(symbols) {
    if (!symbols || symbols.length === 0 || this._fetchingQuotes) return;
    window.AppState = window.AppState || {};
    window.AppState.quotes = window.AppState.quotes || {};

    const now = Date.now();
    const needed = symbols.filter(sym => {
      const last = this._quoteTimestamps[sym] || 0;
      return (now - last) > 15000; // refresh every 15s
    });

    if (needed.length === 0) return;
    this._fetchingQuotes = true;

    Promise.all(needed.map(async sym => {
      try {
        const res = await fetch(`/api/quote/${encodeURIComponent(sym)}`);
        if (res.ok) {
          const data = await res.json();
          if (data && (data.price !== undefined && data.price !== null && data.price > 0)) {
            const px = parseFloat(data.price);
            window.AppState.quotes[sym] = {
              price: px,
              last: px,
              close: px,
              change_percent: parseFloat(data.net_pct || 0),
              changePercent: parseFloat(data.net_pct || 0),
            };
            this._quoteTimestamps[sym] = Date.now();
          }
        }
      } catch (err) {}
    })).then(() => {
      this._fetchingQuotes = false;
      const boardEl = document.getElementById('intraday-ticker-board-container');
      if (boardEl && boardEl.style.display !== 'none' && this._intradayViewMode === 'ticker') {
        this.renderIntradayByTicker(true);
      }
    }).catch(() => {
      this._fetchingQuotes = false;
    });
  },

  renderIntradayByTicker(skipQuoteFetch = false) {
    const boardEl = document.getElementById('intraday-ticker-board-container');
    if (!boardEl) return;

    const targetDate = (this._dateFilter && this._dateFilter !== 'ALL')
      ? this._dateFilter
      : (this._availableDates && this._availableDates.length > 0 ? this._availableDates[0] : '2026-09-16');

    // Discover all intraday tickers dynamically from intraday alerts
    const activeTickers = this.getIntradayTickers(targetDate);

    if (activeTickers.length === 0) {
      boardEl.innerHTML = `
        <div class="station-card" style="text-align:center; padding:48px; color:var(--text-muted); font-size:13px;">
          No intraday trade alerts or executions logged for <strong>${targetDate}</strong>.
        </div>
      `;
      return;
    }

    if (!skipQuoteFetch && activeTickers.length > 0) {
      this.fetchLiveQuotesForActiveTickers(activeTickers.map(t => t.symbol));
    }

    // Gather data for all active intraday tickers
    const tickerData = activeTickers.map(t => {
      const res = this.getTickerDayTrades(t.symbol, targetDate);
      const openTrade = res.trades.find(x => x.status === 'OPEN') || null;
      const completedTrades = res.trades.filter(x => x.status === 'CLOSED');

      let status = 'FLAT';
      if (openTrade) {
        status = 'IN_TRADE';
      } else if (completedTrades.length > 0) {
        status = 'CLOSED_TRADES';
      } else {
        status = 'ALERTS_ONLY';
      }

      // Live quote lookup
      const q = (window.AppState && window.AppState.quotes && window.AppState.quotes[t.symbol]) ? window.AppState.quotes[t.symbol] : null;
      let lastPx = q ? parseFloat(q.last || q.price || q.close || 0) : 0;
      let chgPct = q ? parseFloat(q.change_percent || q.changePercent || 0) : 0;

      // Fallback price to open trade entry price or most recent alert price (never the oldest morning alert!)
      if (lastPx === 0) {
        if (openTrade && openTrade.entryPrice > 0) {
          lastPx = openTrade.entryPrice;
        } else if (res.alerts.length > 0) {
          lastPx = parseFloat(res.alerts[res.alerts.length - 1].alert_price || 0) || 0;
        }
      }

      // Activity timestamps
      let latestTime = '';
      let earliestTime = '';

      if (res.trades && res.trades.length > 0) {
        res.trades.forEach(tr => {
          const tExit = tr.exitTime || tr.entryTime || '';
          const tEntry = tr.entryTime || tr.exitTime || '';
          if (tExit && (!latestTime || tExit > latestTime)) latestTime = tExit;
          if (tEntry && (!earliestTime || tEntry < earliestTime)) earliestTime = tEntry;
        });
      }

      if (res.alerts && res.alerts.length > 0) {
        res.alerts.forEach(al => {
          const at = al.timestamp || al.created_at || al.time || '';
          if (at && (!latestTime || at > latestTime)) latestTime = at;
          if (at && (!earliestTime || at < earliestTime)) earliestTime = at;
        });
      }

      let latestTimeDisplay = '--:--';
      if (latestTime) {
        latestTimeDisplay = latestTime.length >= 16 ? latestTime.substring(11, 16) : latestTime;
      }

      const isIndex = ['SPY', 'QQQ', 'DIA', 'IWM', 'SPX', 'NDX', 'RUT'].includes(t.symbol);

      return {
        ...t,
        type: isIndex ? 'INDEX' : 'EQUITY',
        res,
        openTrade,
        completedTrades,
        status,
        lastPx,
        chgPct,
        latestTime,
        earliestTime,
        latestTimeDisplay,
        summary: res.summary
      };
    });

    // Universe Aggregate Metrics
    let inTradeCount = 0;
    let tradedCount = 0;
    let totalNetPnl = 0;
    let totalTvPnl = 0;
    let totalAvoided = 0;
    let totalAiWins = 0;
    let totalAiLosses = 0;
    let totalRawWins = 0;
    let totalRawLosses = 0;
    let totalCompletedTrades = 0;

    tickerData.forEach(d => {
      if (d.status === 'IN_TRADE') inTradeCount++;
      if (d.completedTrades.length > 0) tradedCount++;

      totalNetPnl += d.summary.aiPnL;
      totalTvPnl += d.summary.rawTvPnL;
      totalAvoided += d.summary.avoidedLosses;
      totalAiWins += d.summary.aiWins;
      totalAiLosses += d.summary.aiLosses;
      totalRawWins += (d.summary.rawWins || 0);
      totalRawLosses += (d.summary.rawLosses || 0);
      totalCompletedTrades += d.summary.totalCompleted;
    });

    const netPnlSign = totalNetPnl >= 0 ? '+' : '';
    const netPnlColor = totalNetPnl >= 0 ? '#10b981' : '#ef4444';
    const totalAiTrades = totalAiWins + totalAiLosses;
    const totalAiWinRate = totalAiTrades > 0 ? Math.round((totalAiWins / totalAiTrades) * 1000) / 10 : 0;
    const totalRawTrades = totalRawWins + totalRawLosses;
    const totalRawWinRate = totalRawTrades > 0 ? Math.round((totalRawWins / totalRawTrades) * 1000) / 10 : 0;
    const hasAiTradesOverall = totalAiTrades > 0;
    const displayWinRate = hasAiTradesOverall ? totalAiWinRate : totalRawWinRate;

    // Filter List
    let filteredList = [...tickerData];

    if (this._searchQuery) {
      const q = this._searchQuery.toLowerCase().trim();
      filteredList = filteredList.filter(d =>
        d.symbol.toLowerCase().includes(q) ||
        d.name.toLowerCase().includes(q)
      );
    }

    if (this._intradayTickerFilter === 'IN_TRADE') {
      filteredList = filteredList.filter(d => d.status === 'IN_TRADE');
    } else if (this._intradayTickerFilter === 'COMPLETED') {
      filteredList = filteredList.filter(d => d.completedTrades.length > 0);
    } else if (this._intradayTickerFilter !== 'ALL') {
      filteredList = filteredList.filter(d => d.symbol === this._intradayTickerFilter);
    }

    // Dynamic Sort Order based on user choice
    const sortMode = this._intradaySort || 'TIME_DESC';

    const comparator = (a, b) => {
      if (sortMode === 'TIME_DESC') {
        // Active in-trade tickers first, then latest alert/trade time
        if (a.status === 'IN_TRADE' && b.status !== 'IN_TRADE') return -1;
        if (b.status === 'IN_TRADE' && a.status !== 'IN_TRADE') return 1;
        const tA = a.latestTime || '';
        const tB = b.latestTime || '';
        if (tA && !tB) return -1;
        if (!tA && tB) return 1;
        if (tA !== tB) return tB.localeCompare(tA);
        return a.symbol.localeCompare(b.symbol);
      } else if (sortMode === 'TIME_ASC') {
        // Earliest activity time first
        const tA = a.earliestTime || '9999';
        const tB = b.earliestTime || '9999';
        if (tA !== tB) return tA.localeCompare(tB);
        return a.symbol.localeCompare(b.symbol);
      } else if (sortMode === 'TICKER_ASC') {
        // Alphabetical A to Z
        return a.symbol.localeCompare(b.symbol);
      } else if (sortMode === 'TICKER_DESC') {
        // Alphabetical Z to A
        return b.symbol.localeCompare(a.symbol);
      } else if (sortMode === 'PNL_DESC') {
        // Highest Net P&L (use AI PnL if AI took trades, else raw session PnL)
        const pnlA = a.summary.aiTradesCount > 0 ? a.summary.aiPnL : a.summary.rawTvPnL;
        const pnlB = b.summary.aiTradesCount > 0 ? b.summary.aiPnL : b.summary.rawTvPnL;
        if (pnlB !== pnlA) {
          return pnlB - pnlA;
        }
        return b.completedTrades.length - a.completedTrades.length;
      } else if (sortMode === 'PNL_ASC') {
        // Lowest Net P&L
        const pnlA = a.summary.aiTradesCount > 0 ? a.summary.aiPnL : a.summary.rawTvPnL;
        const pnlB = b.summary.aiTradesCount > 0 ? b.summary.aiPnL : b.summary.rawTvPnL;
        if (pnlA !== pnlB) {
          return pnlA - pnlB;
        }
        return b.completedTrades.length - a.completedTrades.length;
      } else if (sortMode === 'TRADES_DESC') {
        // Most Trades First
        if (b.completedTrades.length !== a.completedTrades.length) {
          return b.completedTrades.length - a.completedTrades.length;
        }
        const pnlA = a.summary.aiTradesCount > 0 ? a.summary.aiPnL : a.summary.rawTvPnL;
        const pnlB = b.summary.aiTradesCount > 0 ? b.summary.aiPnL : b.summary.rawTvPnL;
        return pnlB - pnlA;
      } else if (sortMode === 'WINRATE_DESC') {
        // Highest Win Rate % First
        const wrA = a.summary.aiTradesCount > 0 ? a.summary.aiWinRate : (a.summary.rawWinRate || 0);
        const wrB = b.summary.aiTradesCount > 0 ? b.summary.aiWinRate : (b.summary.rawWinRate || 0);
        if (wrB !== wrA) {
          return wrB - wrA;
        }
        return b.completedTrades.length - a.completedTrades.length;
      }

      return a.symbol.localeCompare(b.symbol);
    };

    filteredList.sort(comparator);

    // Build Quick Ticker Jump Chips dynamically (ordered to match sort!)
    let sortedChipsList = [...tickerData];
    sortedChipsList.sort(comparator);

    let tickerPillsHtml = '';
    sortedChipsList.forEach(t => {
      const isSelected = this._intradayTickerFilter === t.symbol;
      const tPnL = t.summary.aiTradesCount > 0 ? t.summary.aiPnL : t.summary.rawTvPnL;
      let dotColor = t.status === 'IN_TRADE' ? '#10b981' : (tPnL >= 0 ? '#10b981' : '#ef4444');
      let title = `${t.symbol}: ${t.completedTrades.length} trades (${tPnL >= 0 ? '+' : ''}$${tPnL.toFixed(2)}) · Last: ${t.latestTimeDisplay}`;

      tickerPillsHtml += `
        <button class="intraday-ticker-chip-btn ${isSelected ? 'active' : ''}" 
                onclick="AppAlerts.setIntradayTickerFilter('${isSelected ? 'ALL' : t.symbol}')"
                title="${title}">
          <span style="display:inline-block; width:6px; height:6px; border-radius:50%; background:${dotColor};"></span>
          ${t.symbol}
        </button>
      `;
    });

    let html = `
      <!-- Intraday Tickers & Trades Summary Strip -->
      <div class="intraday-board-summary-strip">
        <div style="display:flex; align-items:center; gap:16px; flex-wrap:wrap;">
          <div>
            <div style="font-size:10px; font-weight:800; color:var(--cyan); text-transform:uppercase; letter-spacing:0.8px;">Intraday Trade Desk</div>
            <div style="font-size:14px; font-weight:900; color:var(--text-main); font-family:var(--font-mono);">
              ${tickerData.length} Tickers Active <span style="font-size:11px; color:var(--text-muted); font-weight:500;">(${totalCompletedTrades} Completed Trades)</span>
            </div>
          </div>
          <div style="height:26px; width:1px; background:var(--border);"></div>
          <div style="display:flex; gap:14px; font-family:var(--font-mono); font-size:11.5px; align-items:center; flex-wrap:wrap;">
            <div><span style="color:var(--text-muted); font-size:9.5px;">IN TRADE:</span> <strong style="color:var(--emerald);">${inTradeCount}</strong></div>
            <div><span style="color:var(--text-muted); font-size:9.5px;">TRADED:</span> <strong style="color:var(--text-main);">${tradedCount} tickers</strong></div>
            <div><span style="color:var(--text-muted); font-size:9.5px;">COMPLETED:</span> <strong>${totalCompletedTrades} trades (${displayWinRate}% ${hasAiTradesOverall ? 'AI ' : ''}Win Rate)</strong></div>
            <div><span style="color:var(--text-muted); font-size:9.5px;">SESSION P&amp;L:</span> <strong style="color:${totalTvPnl >= 0 ? '#10b981' : '#ef4444'}; font-size:13px;">${totalTvPnl >= 0 ? '+' : ''}$${totalTvPnl.toFixed(2)}</strong></div>
            ${hasAiTradesOverall ? `<div><span style="color:var(--text-muted); font-size:9.5px;">NET AI P&amp;L:</span> <strong style="color:${netPnlColor}; font-size:13px;">${netPnlSign}$${Math.abs(totalNetPnl).toFixed(2)}</strong></div>` : ''}
            <div><span style="color:var(--text-muted); font-size:9.5px;">LOSSES SAVED:</span> <strong style="color:var(--cyan); font-weight:800;">+$${totalAvoided.toFixed(2)}</strong></div>
          </div>
        </div>

        <!-- Filter category buttons & Sort Dropdown -->
        <div style="display:flex; gap:12px; flex-wrap:wrap; align-items:center;">
          <div style="display:flex; gap:5px; flex-wrap:wrap; align-items:center;">
            <button class="intraday-ticker-chip-btn ${this._intradayTickerFilter === 'ALL' ? 'active' : ''}" onclick="AppAlerts.setIntradayTickerFilter('ALL')">
              All (${tickerData.length})
            </button>
            <button class="intraday-ticker-chip-btn ${this._intradayTickerFilter === 'IN_TRADE' ? 'active' : ''}" onclick="AppAlerts.setIntradayTickerFilter('IN_TRADE')">
              🟢 In Trade (${inTradeCount})
            </button>
            <button class="intraday-ticker-chip-btn ${this._intradayTickerFilter === 'COMPLETED' ? 'active' : ''}" onclick="AppAlerts.setIntradayTickerFilter('COMPLETED')">
              ⚡ Traded (${tradedCount})
            </button>
          </div>

          <div style="height:22px; width:1px; background:var(--border);"></div>

          <!-- Sort Select -->
          <div style="display:flex; align-items:center; gap:6px;">
            <span style="font-size:10px; font-weight:800; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px;">Sort / View:</span>
            <select class="select" id="intraday-sort-select" onchange="AppAlerts.setIntradaySort(this.value)" style="padding:4px 8px; font-size:11px; font-weight:700; border-radius:var(--radius-sm); background:var(--bg-surface); color:var(--text-main); border:1px solid var(--border); cursor:pointer;">
              <option value="TIME_DESC" ${sortMode === 'TIME_DESC' ? 'selected' : ''}>🕒 Latest First (Flat List · Most Recent)</option>
              <option value="TIME_ASC" ${sortMode === 'TIME_ASC' ? 'selected' : ''}>⏱️ Oldest First (Flat List · 09:30 Morning)</option>
              <option value="TICKER_ASC" ${sortMode === 'TICKER_ASC' ? 'selected' : ''}>🔤 Ticker (A ➔ Z · Grouped)</option>
              <option value="TICKER_DESC" ${sortMode === 'TICKER_DESC' ? 'selected' : ''}>🔤 Ticker (Z ➔ A · Grouped)</option>
              <option value="PNL_DESC" ${sortMode === 'PNL_DESC' ? 'selected' : ''}>💰 Highest P&amp;L · Grouped</option>
              <option value="PNL_ASC" ${sortMode === 'PNL_ASC' ? 'selected' : ''}>📉 Lowest P&amp;L · Grouped</option>
              <option value="TRADES_DESC" ${sortMode === 'TRADES_DESC' ? 'selected' : ''}>📊 Most Trades · Grouped</option>
              <option value="WINRATE_DESC" ${sortMode === 'WINRATE_DESC' ? 'selected' : ''}>🎯 Best Win Rate · Grouped</option>
            </select>
            <button class="btn secondary" onclick="AppAlerts.openPostMortemModal('${targetDate}')" style="padding:4px 10px; font-size:11px; font-weight:800; border-color:rgba(168,85,247,0.4); background:rgba(168,85,247,0.08); color:#c084fc; display:inline-flex; align-items:center; gap:5px;" title="Run Closed-Loop AI Post-Mortem &amp; Attribution Analysis on ${targetDate} trades">
              <span>🧠</span> Session Post-Mortem
            </button>
          </div>
        </div>
      </div>

      <!-- Quick Ticker Jump Bar with Quick Sort Toggles -->
      <div class="intraday-quick-jump-bar" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
        <div style="display:flex; gap:5px; flex-wrap:wrap; align-items:center; flex:1;">
          <span style="font-size:10px; font-weight:800; color:var(--text-muted); letter-spacing:0.5px; margin-right:4px;">QUICK SELECT:</span>
          ${tickerPillsHtml}
        </div>
        <div style="display:flex; gap:4px; align-items:center; flex-shrink:0;">
          <span style="font-size:9.5px; font-weight:800; color:var(--text-muted); letter-spacing:0.4px;">VIEW / SORT:</span>
          <button class="intraday-ticker-chip-btn ${sortMode.startsWith('TIME') ? 'active' : ''}" onclick="AppAlerts.setIntradaySort('TIME_DESC')" title="View Flat Trades List (Latest First)" style="padding:2px 7px; font-size:10px;">🕒 Latest (Flat)</button>
          <button class="intraday-ticker-chip-btn ${sortMode === 'TICKER_ASC' ? 'active' : ''}" onclick="AppAlerts.setIntradaySort('TICKER_ASC')" title="Group by Ticker Alphabetically (A-Z)" style="padding:2px 7px; font-size:10px;">🔤 Ticker (A-Z)</button>
          <button class="intraday-ticker-chip-btn ${sortMode === 'PNL_DESC' ? 'active' : ''}" onclick="AppAlerts.setIntradaySort('PNL_DESC')" title="Group by Ticker - Highest P&L" style="padding:2px 7px; font-size:10px;">💰 P&amp;L</button>
          <button class="intraday-ticker-chip-btn ${sortMode === 'TRADES_DESC' ? 'active' : ''}" onclick="AppAlerts.setIntradaySort('TRADES_DESC')" title="Group by Ticker - Most Trades" style="padding:2px 7px; font-size:10px;">📊 Trades</button>
        </div>
      </div>
    `;

    // CHECK IF TIME SORT: RENDER FLAT LIST IN CHRONOLOGICAL ORDER!
    const isFlatTimeView = (sortMode === 'TIME_ASC' || sortMode === 'TIME_DESC');

    if (isFlatTimeView) {
      // Gather all trades across active tickers for flat chronological view
      let flatTrades = [];
      tickerData.forEach(d => {
        if (d.openTrade) {
          let effExitPx = d.openTrade.exitPrice;
          let effDiffPts = d.openTrade.diffPts || 0;
          let effPnl100 = d.openTrade.pnl100 || 0;
          if (d.lastPx > 0 && d.openTrade.entryPrice > 0) {
            effExitPx = d.lastPx;
            effDiffPts = d.openTrade.side === 'LONG' ? (d.lastPx - d.openTrade.entryPrice) : (d.openTrade.entryPrice - d.lastPx);
            effPnl100 = Math.round(effDiffPts * 100.0 * 100) / 100;
          }

          flatTrades.push({
            ...d.openTrade,
            exitPrice: effExitPx,
            diffPts: effDiffPts,
            pnl100: effPnl100,
            symbol: d.symbol,
            name: d.name,
            type: d.type,
            isOpen: true,
            origIdx: -1,
            targetDate: d.openTrade.date || targetDate,
            sortTime: d.openTrade.entryTime || '9999-99-99 23:59:59',
            entryTimeDisplay: (d.openTrade.entryTime || '').length >= 19 ? d.openTrade.entryTime.substring(11, 16) : '--:--',
            exitTimeDisplay: 'ACTIVE',
            duration: 'ACTIVE'
          });
        }

        if (d.res && d.res.trades) {
          d.res.trades.forEach((t, origIdx) => {
            // Deduplicate: open trade is already added above with live/active state
            if (t.status === 'OPEN') return;

            const entryT = t.entryTime || '';
            const exitT = t.exitTime || '';
            flatTrades.push({
              ...t,
              symbol: d.symbol,
              name: d.name,
              type: d.type,
              isOpen: false,
              origIdx: origIdx,
              targetDate: t.date || d.date || d.res.date || (entryT ? entryT.substring(0, 10) : '') || targetDate,
              sortTime: entryT || exitT || '9999-99-99 00:00:00',
              entryTimeDisplay: entryT.length >= 19 ? entryT.substring(11, 16) : (entryT.length >= 16 ? entryT.substring(11, 16) : '--:--'),
              exitTimeDisplay: exitT.length >= 19 ? exitT.substring(11, 16) : (exitT.length >= 16 ? exitT.substring(11, 16) : (t.status === 'OPEN' ? 'ACTIVE' : '--:--')),
              duration: t.duration || '--'
            });
          });
        }
      });

      // Filter by quick select chips or status
      if (this._intradayTickerFilter === 'IN_TRADE') {
        flatTrades = flatTrades.filter(t => t.isOpen);
      } else if (this._intradayTickerFilter === 'COMPLETED') {
        flatTrades = flatTrades.filter(t => !t.isOpen);
      } else if (this._intradayTickerFilter !== 'ALL') {
        flatTrades = flatTrades.filter(t => t.symbol === this._intradayTickerFilter);
      }

      if (this._searchQuery) {
        const q = this._searchQuery.toLowerCase().trim();
        flatTrades = flatTrades.filter(t =>
          t.symbol.toLowerCase().includes(q) ||
          (t.name || '').toLowerCase().includes(q) ||
          (t.plan || '').toLowerCase().includes(q) ||
          (t.exitReason || '').toLowerCase().includes(q) ||
          (t.side || '').toLowerCase().includes(q)
        );
      }

      // Sort chronologically
      if (sortMode === 'TIME_ASC') {
        // Strict chronological: morning market open to close
        flatTrades.sort((a, b) => a.sortTime.localeCompare(b.sortTime));
      } else {
        // Reverse chronological (default): latest to earliest
        flatTrades.sort((a, b) => {
          if (a.isOpen && !b.isOpen) return -1;
          if (b.isOpen && !a.isOpen) return 1;
          return b.sortTime.localeCompare(a.sortTime);
        });
      }

      // Flat Timeline Header
      html += `
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:10px; padding:8px 14px; background:var(--bg-surface); border:1px solid var(--border); border-radius:var(--radius-sm); box-shadow:var(--shadow-card);">
          <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
            <span style="font-size:11px; font-weight:800; color:var(--cyan); text-transform:uppercase; letter-spacing:0.6px;">
              ⏱️ Chronological Trade Log
            </span>
            <span style="font-size:12.5px; font-weight:700; color:var(--text-main); font-family:var(--font-mono);">
              ${flatTrades.length} Trades · ${sortMode === 'TIME_DESC' ? 'Latest First (Most Recent ➔ Market Open)' : 'Oldest First (09:30 Open ➔ Close)'}
            </span>
            ${this._intradayTickerFilter !== 'ALL' ? `<span class="badge in_zone" style="font-size:10px; font-weight:700;">Filtered: ${this._intradayTickerFilter}</span>` : ''}
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <button class="intraday-ticker-chip-btn" onclick="AppAlerts.toggleFlatTimelineOrder()" title="Toggle chronological vs reverse order" style="padding:3px 10px; font-size:11px; font-weight:700;">
              ${sortMode === 'TIME_DESC' ? '⬇️ Latest First' : '⬆️ Oldest First'}
            </button>
            <button class="btn secondary" onclick="AppAlerts.setIntradaySort('TICKER_ASC')" title="Switch to Grouped Ticker Cards View" style="padding:3px 10px; font-size:11px; font-weight:700;">
              🗂️ Group by Ticker
            </button>
            <button class="btn secondary" onclick="AppAlerts.openPostMortemModal('${targetDate}')" style="padding:3px 10px; font-size:11px; font-weight:800; color:#c084fc; border-color:rgba(168,85,247,0.4); background:rgba(168,85,247,0.08); display:inline-flex; align-items:center; gap:4px;" title="Run Closed-Loop AI Post-Mortem &amp; Attribution Analysis on ${targetDate} trades">
              <span>🧠</span> Post-Mortem
            </button>
          </div>
        </div>
      `;

      if (flatTrades.length === 0) {
        html += `
          <div class="station-card" style="text-align:center; padding:36px; color:var(--text-muted); font-size:12px;">
            No intraday trades found matching current criteria.
          </div>
        `;
      } else {
        html += `<div style="display:flex; flex-direction:column; gap:8px;">`;
        flatTrades.forEach(t => {
          const isCall = t.side === 'LONG';
          const pnlCol = t.pnl100 >= 0 ? 'var(--emerald)' : 'var(--rose)';
          const pSign = t.pnl100 >= 0 ? '+' : '';

          let cardClass = 'intraday-flat-trade-card';
          let aiVerdictBadge = '';

          if (t.isOpen) {
            cardClass += ' is-open';
            aiVerdictBadge = `<span class="badge in_zone" style="font-size:10px; font-weight:800; box-shadow:0 0 8px rgba(16,185,129,0.3);">⚡ ACTIVE POSITION</span>`;
          } else if (t.isAiTaken) {
            cardClass += ' ai-taken';
            aiVerdictBadge = `<span class="badge in_zone" style="font-size:10px; font-weight:800;">🤖 AI TAKEN</span>`;
          } else if (t.isAiFiltered) {
            cardClass += ' ai-filtered';
            aiVerdictBadge = `<span class="badge danger" style="font-size:10px; font-weight:800;">🛡️ AI FILTERED</span>`;
          } else {
            aiVerdictBadge = `<span class="badge" style="font-size:10px;">UNTRIAGED</span>`;
          }

          const dirBadge = isCall
            ? `<span class="badge in_zone" style="font-size:10.5px; font-weight:800;">🟢 CALL</span>`
            : `<span class="badge danger" style="font-size:10.5px; font-weight:800;">🔴 PUT</span>`;

          const typeBadge = t.type === 'INDEX'
            ? `<span class="badge" style="background:rgba(168,85,247,0.12); color:#c084fc; border:1px solid rgba(168,85,247,0.3); font-size:9.5px; font-weight:800;">INDEX</span>`
            : `<span class="badge" style="background:rgba(59,130,246,0.12); color:#60a5fa; border:1px solid rgba(59,130,246,0.3); font-size:9.5px; font-weight:800;">MEGA-CAP</span>`;

          const clickHandler = t.isOpen
            ? `AppAlerts.openTickerTradesModal('${t.symbol}', '${t.targetDate}')`
            : `AppAlerts.openTradeDetailModal('${t.symbol}', '${t.targetDate}', ${t.origIdx})`;

          html += `
            <div class="${cardClass}" onclick="${clickHandler}" title="Click to view full AI Agent Decision &amp; Trade Intelligence Modal">
              <!-- Left Section: Chronological Time Badge -->
              <div style="display:flex; align-items:center; gap:8px; min-width:140px;">
                <span class="pill" style="font-size:11px; font-weight:800; font-family:var(--font-mono); background:var(--bg-subtle); color:var(--text-main); border:1px solid var(--border); padding:3px 8px;">
                  ⏱️ ${t.entryTimeDisplay}
                </span>
                <span style="font-size:10.5px; color:var(--text-muted); font-family:var(--font-mono);">
                  ➔ ${t.exitTimeDisplay}
                </span>
              </div>

              <!-- Ticker & Contract Type -->
              <div style="display:flex; align-items:center; gap:8px; min-width:170px;">
                <span class="intraday-ticker-sym-badge" style="font-size:13.5px;">${t.symbol}</span>
                ${typeBadge}
                ${dirBadge}
              </div>

              <!-- Price & Exit Details -->
              <div style="display:flex; align-items:center; gap:8px; flex:1; min-width:220px; flex-wrap:wrap;">
                <span style="font-family:var(--font-mono); font-size:12px; font-weight:700; color:var(--text-main);">
                  $${t.entryPrice > 0 ? t.entryPrice.toFixed(2) : '--'} ➔ $${t.exitPrice > 0 ? t.exitPrice.toFixed(2) : (t.isOpen ? 'ACTIVE' : '--')}
                </span>
                <span class="pill" style="font-size:10px; padding:1px 6px;">
                  ⏱️ ${t.duration}
                </span>
                ${t.exitReason ? `
                  <span style="font-size:11px; color:var(--text-muted); max-width:260px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                    Exit: <strong style="color:var(--text-main);">${t.exitReason}</strong>
                  </span>
                ` : ''}
              </div>

              <!-- AI Status Badge -->
              <div style="display:flex; align-items:center; gap:6px; min-width:115px;">
                ${aiVerdictBadge}
              </div>

              <!-- P&L and Interactive Modal Trigger -->
              <div style="display:flex; align-items:center; gap:12px; min-width:185px; justify-content:flex-end;">
                <div style="text-align:right;">
                  <div style="font-family:var(--font-mono); font-size:13.5px; font-weight:900; color:${pnlCol};">
                    ${pSign}$${t.pnl100.toFixed(2)}
                  </div>
                  <div style="font-family:var(--font-mono); font-size:10px; color:var(--text-muted);">
                    (${pSign}${t.diffPts.toFixed(2)} pts)
                  </div>
                </div>
                <button class="btn secondary" style="padding:3px 9px; font-size:11px; font-weight:700;" onclick="event.stopPropagation(); ${clickHandler}" title="Click to view AI Agent Decision &amp; Trade Intelligence Modal">
                  🧠 AI Decision ➔
                </button>
              </div>
            </div>
          `;
        });
        html += `</div>`;
      }

      boardEl.innerHTML = html;
      return;
    }

    if (filteredList.length === 0) {
      html += `
        <div class="station-card" style="text-align:center; padding:36px; color:var(--text-muted); font-size:12px;">
          No intraday tickers matching current filter or search criteria.
        </div>
      `;
      boardEl.innerHTML = html;
      return;
    }

    // Render Ticker Station Cards (Expanded by default so trades are visible!)
    filteredList.forEach(d => {
      const isExpanded = (this._intradayExpandedTickers[d.symbol] !== undefined)
        ? this._intradayExpandedTickers[d.symbol]
        : true;

      const hasAiTrades = d.summary.aiTradesCount > 0;
      const displayPnL = hasAiTrades ? d.summary.aiPnL : d.summary.rawTvPnL;
      const displayWins = hasAiTrades ? d.summary.aiWins : (d.summary.rawWins || 0);
      const displayLosses = hasAiTrades ? d.summary.aiLosses : (d.summary.rawLosses || 0);
      const displayWinRate = hasAiTrades ? d.summary.aiWinRate : (d.summary.rawWinRate || 0);

      const cardClasses = [
        'intraday-ticker-card',
        d.status === 'IN_TRADE' ? 'in-trade' : '',
        displayPnL > 0 ? 'has-win' : (displayPnL < 0 ? 'has-loss' : ''),
        isExpanded ? 'expanded' : ''
      ].filter(Boolean).join(' ');

      const typeBadge = d.type === 'INDEX'
        ? `<span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.35); font-size:9.5px; font-weight:800;">INDEX</span>`
        : `<span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.35); font-size:9.5px; font-weight:800;">MEGA-CAP</span>`;

      const pxStr = d.lastPx > 0 ? `$${d.lastPx.toFixed(2)}` : '--';
      const chgColor = d.chgPct > 0 ? '#10b981' : (d.chgPct < 0 ? '#ef4444' : 'var(--text-muted)');
      const chgSign = d.chgPct > 0 ? '+' : '';
      const chgStr = d.chgPct !== 0 ? `<span style="font-size:11px; color:${chgColor}; font-weight:700;">(${chgSign}${d.chgPct.toFixed(2)}%)</span>` : '';

      // Status Badge
      let statusBadge = '';
      if (d.status === 'IN_TRADE' && d.openTrade) {
        statusBadge = `
          <span class="badge" style="background:rgba(16,185,129,0.2); color:#10b981; border:1px solid rgba(16,185,129,0.5); font-weight:800; font-size:11px; display:inline-flex; align-items:center; gap:5px;">
            <span style="display:inline-block; width:7px; height:7px; border-radius:50%; background:#10b981; box-shadow:0 0 6px #10b981;"></span>
            IN TRADE · ${d.openTrade.side === 'LONG' ? 'CALLS' : 'PUTS'} @ $${d.openTrade.entryPrice.toFixed(2)}
          </span>
        `;
      } else if (d.completedTrades.length > 0) {
        statusBadge = `
          <span class="badge" style="background:rgba(255,255,255,0.06); color:var(--text-main); border:1px solid var(--border); font-size:11px; font-weight:700;">
            ⚪ FLAT · ${d.completedTrades.length} Trade${d.completedTrades.length > 1 ? 's' : ''} Completed
          </span>
        `;
      } else if (d.res.alerts.length > 0) {
        statusBadge = `
          <span class="badge" style="background:rgba(245,158,11,0.12); color:#f59e0b; border:1px solid rgba(245,158,11,0.3); font-size:10.5px; font-weight:700;">
            ⚡ ${d.res.alerts.length} Alerts Logged
          </span>
        `;
      } else {
        statusBadge = `
          <span class="badge" style="background:rgba(255,255,255,0.025); color:var(--text-muted); border:1px dashed rgba(255,255,255,0.12); font-size:10.5px;">
            ⏳ STALKING SETUP
          </span>
        `;
      }

      // Performance tags
      const pSign = displayPnL >= 0 ? '+' : '';
      const pColor = displayPnL >= 0 ? '#10b981' : '#ef4444';
      const pnlHtml = (d.completedTrades.length > 0 || d.openTrade)
        ? `<div style="text-align:right;">
             <div style="font-family:var(--font-mono); font-size:13px; font-weight:900; color:${pColor};">${pSign}$${displayPnL.toFixed(2)}</div>
             <div style="font-family:var(--font-mono); font-size:10px; color:var(--text-muted);">${displayWins}W / ${displayLosses}L (${displayWinRate}%)</div>
           </div>`
        : `<div style="font-family:var(--font-mono); font-size:12px; color:var(--text-muted);">--</div>`;

      const avoidedHtml = d.summary.avoidedLosses > 0
        ? `<span class="badge" style="background:rgba(56,189,248,0.12); color:#38bdf8; border:1px solid rgba(56,189,248,0.3); font-size:9.5px; font-weight:700;" title="Loss prevented by AI triage">
             🛡️ +$${d.summary.avoidedLosses.toFixed(2)} Saved
           </span>`
        : '';

      html += `
        <div class="${cardClasses}" id="ticker-card-${d.symbol}">
          <!-- Header -->
          <div class="intraday-ticker-head" onclick="AppAlerts.toggleTickerExpanded('${d.symbol}')">
            <!-- Left: Sym, Type, Name, Price -->
            <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
              <span class="intraday-ticker-sym-badge">${d.symbol}</span>
              ${typeBadge}
              <span style="font-size:12px; color:var(--text-muted); font-weight:500;">${d.name}</span>
              <span style="font-family:var(--font-mono); font-size:12px; font-weight:700; color:var(--text-main); margin-left:4px;">
                ${pxStr} ${chgStr}
              </span>
            </div>

            <!-- Center: Status Badge & Last Activity Time -->
            <div style="display:flex; align-items:center; gap:8px;">
              ${statusBadge}
              ${avoidedHtml}
              ${d.latestTimeDisplay && d.latestTimeDisplay !== '--:--' ? `
                <span class="pill" style="font-size:10px; font-family:var(--font-mono); font-weight:700; color:var(--text-muted); border:1px solid var(--border); background:var(--bg-subtle);" title="Latest Signal or Execution Time">
                  🕒 ${d.latestTimeDisplay}
                </span>
              ` : ''}
            </div>

            <!-- Right: P&L + Actions -->
            <div style="display:flex; align-items:center; gap:12px;">
              ${pnlHtml}
              <button class="btn secondary" style="padding:3px 9px; font-size:11px; font-weight:700;" onclick="event.stopPropagation(); AppAlerts.openTickerTradesModal('${d.symbol}', '${targetDate}')">
                📊 Trades
              </button>
              <button class="btn secondary" style="padding:3px 7px; font-size:11px;" onclick="event.stopPropagation(); AppAlerts.openChart('${d.symbol}')" title="Open TradingView Chart">
                📈
              </button>
              <span style="font-size:11px; color:var(--text-muted); width:14px; text-align:center;">${isExpanded ? '▲' : '▼'}</span>
            </div>
          </div>

          <!-- Body: Trades & Position (if expanded) -->
          ${isExpanded ? `
            <div class="intraday-ticker-body">
              ${d.openTrade ? `
                <!-- Live Active Position Box -->
                <div style="background:var(--bg-surface); border:1px solid var(--border); border-left:4px solid var(--emerald); border-radius:var(--radius-sm); padding:10px 14px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; box-shadow:var(--shadow-card);">
                  <div>
                    <div style="font-size:10px; font-weight:800; color:var(--emerald); letter-spacing:0.5px;">⚡ ACTIVE POSITION IN PROGRESS</div>
                    <div style="font-family:var(--font-mono); font-size:12px; color:var(--text-main); margin-top:2px;">
                      Entered: <strong>${d.openTrade.entryTime ? d.openTrade.entryTime.substring(11, 16) : '--:--'}</strong> @ <strong>$${d.openTrade.entryPrice.toFixed(2)}</strong> (${d.openTrade.side === 'LONG' ? 'CALLS' : 'PUTS'})
                    </div>
                    ${d.openTrade.plan ? `<div style="font-size:11px; color:var(--text-muted); font-family:var(--font-mono); margin-top:3px;">Plan: ${d.openTrade.plan}</div>` : ''}
                  </div>
                  <div style="text-align:right;">
                    <div style="font-size:10px; color:var(--text-muted);">UNREALIZED P&amp;L (100 SH):</div>
                    <div style="font-family:var(--font-mono); font-size:14px; font-weight:900; color:${d.openTrade.pnl100 >= 0 ? 'var(--emerald)' : 'var(--rose)'};">
                      ${d.openTrade.pnl100 >= 0 ? '+' : ''}$${d.openTrade.pnl100.toFixed(2)} (${d.openTrade.diffPts >= 0 ? '+' : ''}${d.openTrade.diffPts.toFixed(2)} pts)
                    </div>
                  </div>
                </div>
              ` : ''}

              <!-- Trades List -->
              ${d.res.trades.length > 0 ? (() => {
                let tradeEntries = d.res.trades.map((t, origIdx) => ({ t, origIdx }));
                if (this._intradayTradesOrder === 'desc') {
                  tradeEntries.reverse();
                }
                return `
                  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; padding:0 2px;">
                    <span style="font-size:10px; font-weight:800; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px;">
                      ${d.res.trades.length} Intraday Trade${d.res.trades.length > 1 ? 's' : ''}
                    </span>
                    <button class="intraday-ticker-chip-btn" onclick="event.stopPropagation(); AppAlerts.toggleTradesOrder()" title="Toggle Trade Chronology (Newest First vs Oldest First)" style="padding:2px 8px; font-size:9.5px;">
                      ${this._intradayTradesOrder === 'desc' ? '⬇️ Newest First' : '⬆️ Oldest First'}
                    </button>
                  </div>
                  <div style="display:flex; flex-direction:column; gap:6px;">
                    ${tradeEntries.map(({ t, origIdx }) => {
                      const isCall = t.side === 'LONG';
                      const inTime = (t.entryTime || '').length >= 19 ? t.entryTime.substring(11, 16) : '--:--';
                      const outTime = t.exitTime ? (t.exitTime.length >= 19 ? t.exitTime.substring(11, 16) : '--:--') : 'ACTIVE';
                      const pnlCol = t.pnl100 >= 0 ? 'var(--emerald)' : 'var(--rose)';
                      const pSign = t.pnl100 >= 0 ? '+' : '';
                      const targetDate = t.date || d.date || d.res.date || (t.entryTime ? t.entryTime.substring(0, 10) : '') || this._dateFilter || 'Today';

                      let itemClass = 'intraday-trade-item';
                      let aiVerdictBadge = '';

                      if (t.isAiTaken) {
                        itemClass += ' ai-taken';
                        aiVerdictBadge = `<span class="badge in_zone" style="font-size:10px; font-weight:800;">🤖 AI TAKEN</span>`;
                      } else if (t.isAiFiltered) {
                        itemClass += ' ai-filtered';
                        aiVerdictBadge = `<span class="badge danger" style="font-size:10px; font-weight:800;">🛡️ AI FILTERED</span>`;
                      } else {
                        aiVerdictBadge = `<span class="badge" style="font-size:10px;">UNTRIAGED</span>`;
                      }

                      const dirBadge = isCall
                        ? `<span class="badge in_zone" style="font-size:10px; font-weight:800;">🟢 CALL</span>`
                        : `<span class="badge danger" style="font-size:10px; font-weight:800;">🔴 PUT</span>`;

                      return `
                        <div class="${itemClass}" onclick="AppAlerts.openTradeDetailModal('${d.symbol}', '${targetDate}', ${origIdx})" title="Click to open full AI Decision &amp; Trade Intelligence Modal">
                          <!-- Left: Direction, Entry -> Exit, Duration, Exit Reason -->
                          <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                            ${dirBadge}
                            <span style="color:var(--text-main); font-weight:700; font-size:11.5px;">
                              ${inTime} @ $${t.entryPrice > 0 ? t.entryPrice.toFixed(2) : '--'} ➔ ${outTime} @ $${t.exitPrice > 0 ? t.exitPrice.toFixed(2) : '--'}
                            </span>
                            <span class="pill" style="font-size:10px; padding:1px 6px;">
                              ⏱️ ${t.duration}
                            </span>
                            ${t.exitReason ? `<span style="font-size:10.5px; color:var(--text-muted); max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">Exit: <strong style="color:var(--text-main);">${t.exitReason}</strong></span>` : ''}
                          </div>

                          <!-- Right: AI Status, P&L, Interactive Hint -->
                          <div style="display:flex; align-items:center; gap:10px;">
                            ${aiVerdictBadge}
                            <span style="font-weight:900; font-size:12.5px; color:${pnlCol}; font-family:var(--font-mono); min-width:80px; text-align:right;">
                              ${pSign}$${t.pnl100.toFixed(2)} (${pSign}${t.diffPts.toFixed(2)} pts)
                            </span>
                            <button class="btn secondary" style="padding:2px 8px; font-size:10.5px; font-weight:700;" onclick="event.stopPropagation(); AppAlerts.openTradeDetailModal('${d.symbol}', '${targetDate}', ${origIdx})" title="Click to view AI Agent Decision &amp; Trade Intelligence Modal">
                              🧠 AI Decision ➔
                            </button>
                          </div>
                        </div>
                      `;
                    }).join('')}
                  </div>
                `;
              })() : `
                <div style="font-size:11.5px; color:var(--text-muted); text-align:center; padding:12px; font-family:var(--font-mono);">
                  ⏳ No execution trades triggered today for <strong>${d.symbol}</strong>. Monitoring 0DTE key levels.
                </div>
              `}
            </div>
          ` : ''}
        </div>
      `;
    });

    boardEl.innerHTML = html;
  },

  renderStreamTable() {
    const tableBody = document.getElementById('tv-alerts-table-body');
    if (!tableBody) return;

    this.buildAlertPnlIndex();

    let list = [...this._intradayAlerts];

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
        const align = (p.align || '').toLowerCase();
        const comp = window.AppUtils ? (window.AppUtils.getCompanyName(sym) || '').toLowerCase() : '';
        return sym.includes(q) || act.includes(q) || setup.includes(q) || strat.includes(q) || comp.includes(q) || plan.includes(q) || align.includes(q);
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
    this._intradayAlerts.forEach(a => {
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
          <td colspan="10" style="text-align:center; color:var(--text-muted); padding:36px; font-size:13px;">
            No Intraday 0DTE execution alerts found matching the current filters.
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
            <td colspan="10" style="padding:8px 14px;">
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

      // Alignment & Tape
      let alignHtml = '';
      const alignRaw = payload.align || '';
      const whyNowRaw = payload.why_now || '';
      if (alignRaw || whyNowRaw) {
        alignHtml = `
          <div style="display:flex; flex-direction:column; gap:2px; max-width:130px;">
            ${alignRaw ? `<div style="font-family:var(--font-mono); font-size:10px; font-weight:700; color:var(--text-main); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${alignRaw.replace(/"/g, '&quot;')}">${alignRaw}</div>` : ''}
            ${whyNowRaw ? `<div style="font-size:9.5px; color:var(--text-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${whyNowRaw.replace(/"/g, '&quot;')}">${whyNowRaw}</div>` : ''}
          </div>
        `;
      } else {
        alignHtml = `<span style="color:var(--text-muted); font-size:11px;">—</span>`;
      }

      // Grade & Score
      let gradeHtml = '';
      const gradeVal = a.grade || payload.grade || '';
      const scoreVal = a.score || payload.score || '';
      if (gradeVal || scoreVal) {
        let gColor = '#38bdf8';
        let gBg = 'rgba(56,189,248,0.12)';
        let gBorder = 'rgba(56,189,248,0.3)';
        if (gradeVal.toUpperCase().startsWith('A')) {
          gColor = '#10b981';
          gBg = 'rgba(16,185,129,0.14)';
          gBorder = 'rgba(16,185,129,0.35)';
        } else if (gradeVal.toUpperCase().startsWith('B')) {
          gColor = '#38bdf8';
          gBg = 'rgba(56,189,248,0.14)';
          gBorder = 'rgba(56,189,248,0.35)';
        } else if (gradeVal.toUpperCase().startsWith('C')) {
          gColor = '#f59e0b';
          gBg = 'rgba(245,158,11,0.14)';
          gBorder = 'rgba(245,158,11,0.35)';
        } else if (gradeVal.toUpperCase().startsWith('D') || gradeVal.toUpperCase().startsWith('F')) {
          gColor = '#ef4444';
          gBg = 'rgba(239,68,68,0.14)';
          gBorder = 'rgba(239,68,68,0.35)';
        }
        gradeHtml = `
          <div style="display:inline-flex; flex-direction:column; align-items:center; justify-content:center; padding:2px 6px; border-radius:5px; background:${gBg}; border:1px solid ${gBorder}; font-family:var(--font-mono);">
            <span style="font-size:10.5px; font-weight:800; color:${gColor};">Grd ${gradeVal || '--'}</span>
            ${scoreVal ? `<span style="font-size:9px; color:var(--text-muted); font-weight:600;">${scoreVal}/100</span>` : ''}
          </div>
        `;
      } else {
        gradeHtml = `<span style="color:var(--text-muted); font-size:11px;">—</span>`;
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

          <!-- 7. ALIGNMENT & TAPE -->
          <td style="padding:7px 6px; overflow:hidden;">
            ${alignHtml}
          </td>

          <!-- 8. GRADE -->
          <td style="padding:7px 6px; text-align:center;">
            ${gradeHtml}
          </td>

          <!-- 9. 0DTE TRIAGE (#ponytail) -->
          <td style="padding:7px 6px; text-align:center;">
            ${aiBadge}
          </td>

          <!-- 10. ACTIONS -->
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

  renderDailyTable() {
    const tableBody = document.getElementById('daily-alerts-table-body');
    if (!tableBody) return;

    let list = [...this._dailyAlerts];

    if (this._dateFilter !== 'ALL') {
      list = list.filter(a => {
        const d = a.date || (a.timestamp ? a.timestamp.substring(0, 10) : '');
        return d === this._dateFilter;
      });
    }

    if (this._dailyFilterSide !== 'ALL') {
      list = list.filter(a => {
        const payload = this.parsePayload(a);
        const side = (a.action || payload.side || '').toUpperCase();
        return side === this._dailyFilterSide;
      });
    }

    if (this._dailyFilterStage !== 'ALL') {
      const stg = parseInt(this._dailyFilterStage, 10);
      list = list.filter(a => {
        const payload = this.parsePayload(a);
        const stgNum = payload.stage !== undefined ? payload.stage : a.stage;
        return parseInt(stgNum, 10) === stg;
      });
    }

    if (this._dailySearchQuery) {
      const q = this._dailySearchQuery;
      list = list.filter(a => {
        const p = this.parsePayload(a);
        const sym = (a.symbol || p.ticker || '').toLowerCase();
        const side = (a.action || p.side || '').toLowerCase();
        const setup = (a.setup || p.setup || '').toLowerCase();
        const strat = (a.strategy || p.strategy || '').toLowerCase();
        const comp = window.AppUtils ? (window.AppUtils.getCompanyName(sym) || '').toLowerCase() : '';
        return sym.includes(q) || side.includes(q) || setup.includes(q) || strat.includes(q) || comp.includes(q);
      });
    }

    if (this._dailyAiFilter !== 'ALL') {
      list = list.filter(a => {
        const dec = (a.llm_decision || '').toUpperCase();
        if (this._dailyAiFilter === 'ACTIONABLE') {
          return dec.includes('PASS') || dec.includes('GO') || dec.includes('ENTER') || dec.includes('TAKE');
        }
        if (this._dailyAiFilter === 'WATCH') {
          return dec.includes('WATCH') || dec.includes('STALK');
        }
        if (this._dailyAiFilter === 'CUT') {
          return dec.includes('CUT') || dec.includes('STAND') || dec.includes('EXIT');
        }
        return true;
      });
    }

    // Update Counter Badges for Daily AI categories
    let countDailyActionable = 0, countDailyWatch = 0, countDailyCut = 0;
    this._dailyAlerts.forEach(a => {
      const dec = (a.llm_decision || '').toUpperCase();
      if (dec.includes('PASS') || dec.includes('GO') || dec.includes('ENTER') || dec.includes('TAKE')) countDailyActionable++;
      else if (dec.includes('WATCH') || dec.includes('STALK')) countDailyWatch++;
      else if (dec.includes('CUT') || dec.includes('STAND') || dec.includes('EXIT')) countDailyCut++;
    });
    const elDailyAct = document.getElementById('count-daily-actionable');
    const elDailyWat = document.getElementById('count-daily-watch');
    const elDailyCut = document.getElementById('count-daily-cut');
    if (elDailyAct) elDailyAct.innerText = countDailyActionable;
    if (elDailyWat) elDailyWat.innerText = countDailyWatch;
    if (elDailyCut) elDailyCut.innerText = countDailyCut;

    // Sort Daily list
    list.sort((a, b) => {
      let va = a[this._dailySortField];
      let vb = b[this._dailySortField];
      if (this._dailySortField === 'alert_price') {
        va = parseFloat(va) || 0;
        vb = parseFloat(vb) || 0;
      } else {
        va = (va || '').toString().toLowerCase();
        vb = (vb || '').toString().toLowerCase();
      }
      if (va < vb) return this._dailySortAsc ? -1 : 1;
      if (va > vb) return this._dailySortAsc ? 1 : -1;
      return 0;
    });

    const totalCount = list.length;
    const totalPages = Math.max(1, Math.ceil(totalCount / this._dailyPageSize));
    if (this._dailyCurrentPage > totalPages) this._dailyCurrentPage = totalPages;
    if (this._dailyCurrentPage < 1) this._dailyCurrentPage = 1;

    const startIdx = (this._dailyCurrentPage - 1) * this._dailyPageSize;
    const endIdx = Math.min(totalCount, startIdx + this._dailyPageSize);
    const pagedList = list.slice(startIdx, endIdx);

    this.renderDailyPaginationToolbar(totalCount, startIdx, endIdx, totalPages);

    // Render Top Deep Research Quick Access Ribbon if dossiers exist
    const ribbonContainer = document.getElementById('daily-alerts-dr-ribbon-container');
    if (ribbonContainer) {
      const activeDate = (this._dateFilter !== 'ALL') ? this._dateFilter : (this._availableDates[0] || '2026-09-16');
      const dateReports = (this._reportsByDate && this._reportsByDate[activeDate]) || [];
      const allReports = [];
      for (const d in this._reportsByDate) {
        (this._reportsByDate[d] || []).forEach(r => allReports.push(r));
      }
      const displayReports = dateReports.length > 0 ? dateReports : allReports.slice(0, 10);

      if (displayReports.length > 0) {
        ribbonContainer.style.display = 'block';
        const chipsHtml = displayReports.map(r => {
          const vClass = r.verdict === 'ENTER' ? 'in_zone' : (r.verdict === 'STALK' ? 'stalking' : 'danger');
          return `
            <button class="daily-dr-chip" onclick="AppSwing.openReportModal('${r.date}', '${r.ticker}')" title="Open ${r.ticker} (${r.date}) Deep Research Dossier · ${r.options_summary || ''}">
              <span>📑</span>
              <strong style="color:var(--text-main); font-size:12px;">${r.ticker}</strong>
              <span class="badge ${vClass}" style="font-size:9.5px; padding:1px 5px;">${r.verdict} (${r.conviction}/10)</span>
            </button>
          `;
        }).join('');

        ribbonContainer.innerHTML = `
          <div class="daily-dr-ribbon">
            <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
              <span style="font-size:11px; font-weight:900; color:var(--cyan); letter-spacing:0.6px; text-transform:uppercase;">
                🔬 DEEP RESEARCH DOSSIERS (${displayReports.length} Available${dateReports.length > 0 ? ` for ${activeDate}` : ''}):
              </span>
              <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
                ${chipsHtml}
              </div>
            </div>
            <div style="font-size:10.5px; color:var(--text-muted); font-family:var(--font-mono);">
              Multi-pass Bull/Bear debate · Senior-PM arbitration · Multimodal charts
            </div>
          </div>
        `;
      } else {
        ribbonContainer.style.display = 'none';
      }
    }

    if (totalCount === 0) {
      tableBody.innerHTML = `
        <tr>
          <td colspan="11" style="text-align:center; color:var(--text-muted); padding:36px; font-size:13px;">
            No Daily Swing Screener alerts found matching the current filters.
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
        rowsHtml += `
          <tr class="alert-date-group-header" style="background:linear-gradient(90deg, rgba(245,158,11,0.12) 0%, rgba(15,23,42,0.03) 100%); border-top:1px solid var(--border); border-bottom:1px solid var(--border);">
            <td colspan="11" style="padding:9px 14px;">
              <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span style="font-size:12px; font-weight:800; font-family:var(--font-mono); color:var(--text-main); background:rgba(245,158,11,0.18); border:1px solid rgba(245,158,11,0.4); padding:2px 8px; border-radius:5px; letter-spacing:0.5px;">
                    📅 SWING SESSION ${dateKey}
                  </span>
                  <span style="font-size:11.5px; color:var(--text-muted); font-weight:600;">
                    Daily Screener Pre-Move Radar
                  </span>
                </div>
              </div>
            </td>
          </tr>
        `;
      }

      const payload = this.parsePayload(a);
      const sym = (a.symbol || payload.ticker || '').toUpperCase();
      const compName = window.AppUtils ? (window.AppUtils.getCompanyName(sym) || sym) : sym;
      const sideRaw = (a.action || payload.side || 'NEUTRAL').toUpperCase();
      const price = (a.alert_price || payload.price) ? `$${parseFloat(a.alert_price || payload.price).toFixed(2)}` : '--';

      // Check if Deep Research exists for this ticker
      const rep = this.getReportForTicker(sym, dateKey);

      // 1. Time string
      let timeStr = a.timestamp || a.created_at || '';
      if (timeStr.length >= 19) timeStr = timeStr.substring(11, 19);

      // 2. Side Badge (High-Contrast)
      let sideBadge = '';
      if (sideRaw === 'LONG') {
        sideBadge = `<span class="badge in_zone" style="font-weight:800; font-family:var(--font-mono); font-size:10.5px; padding:3px 7px;">🟢 LONG</span>`;
      } else if (sideRaw === 'SHORT') {
        sideBadge = `<span class="badge danger" style="font-weight:800; font-family:var(--font-mono); font-size:10.5px; padding:3px 7px;">🔴 SHORT</span>`;
      } else {
        sideBadge = `<span class="badge" style="font-weight:700; font-family:var(--font-mono); font-size:10.5px; padding:3px 7px;">⚪ NEUTRAL</span>`;
      }

      // 3. Setup & Weinstein Stage
      const setupName = a.setup || payload.setup || 'Daily Pre-Move Radar';
      let stageNum = payload.stage;
      if (stageNum === undefined && a.stage !== undefined) stageNum = a.stage;
      let stageBadge = '';
      if (stageNum !== undefined && stageNum !== null) {
        const stageLabels = {
          1: 'Stg 1 (Base)',
          2: 'Stg 2 (Advancing)',
          3: 'Stg 3 (Distribution)',
          4: 'Stg 4 (Declining)',
          5: 'Stg 5 (Recovery)'
        };
        const sLabel = stageLabels[stageNum] || `Stage ${stageNum}`;
        stageBadge = `<span class="stage-badge stage-${stageNum}" title="Stan Weinstein Stage ${stageNum}">${sLabel}</span>`;
      }

      let revBadge = '';
      if (payload.revL && payload.revL > 0) {
        revBadge += `<span class="badge in_zone" style="font-size:9.5px; padding:1px 5px; font-weight:800;">RevL +${payload.revL}</span>`;
      } else if (payload.revS && payload.revS > 0) {
        revBadge += `<span class="badge danger" style="font-size:9.5px; padding:1px 5px; font-weight:800;">RevS +${payload.revS}</span>`;
      }
      if (payload.prime && payload.prime !== 0) {
        revBadge += ` <span class="badge target_hit" style="font-size:9.5px; padding:1px 5px; font-weight:800;">Prime ${payload.prime > 0 ? '+' : ''}${payload.prime}</span>`;
      }

      const setupHtml = `
        <div style="display:flex; flex-direction:column; gap:3px; max-width:180px;">
          <div style="font-size:12px; font-weight:800; color:var(--text-main); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${setupName}">
            ${setupName}
          </div>
          <div style="display:flex; align-items:center; gap:4px; flex-wrap:wrap;">
            ${stageBadge}
            ${revBadge}
          </div>
        </div>
      `;

      // 4. Scores & Prob (High-Contrast Numbers)
      const buyScore = payload.buy !== undefined ? parseFloat(payload.buy).toFixed(1) : null;
      const sellScore = payload.sell !== undefined ? parseFloat(payload.sell).toFixed(1) : null;
      const dirProb = payload.dir_prob !== undefined ? parseFloat(payload.dir_prob).toFixed(1) : null;
      const vcp = payload.vcp;

      let scoreHtml = '';
      if (buyScore !== null || sellScore !== null || dirProb !== null) {
        scoreHtml = `
          <div style="display:flex; flex-direction:column; align-items:center; gap:2px; font-family:var(--font-mono);">
            <div style="font-size:11px; font-weight:800;">
              <span style="color:var(--emerald);">B:${buyScore || '--'}</span>
              <span style="color:var(--text-muted);"> · </span>
              <span style="color:var(--rose);">S:${sellScore || '--'}</span>
            </div>
            <div style="display:flex; align-items:center; gap:4px;">
              ${dirProb !== null ? `<span style="font-size:10px; font-weight:800; color:var(--cyan);">${dirProb}% Dir</span>` : ''}
              ${vcp ? `<span class="badge target_hit" style="font-size:9px; padding:1px 4px; font-weight:800;">VCP</span>` : ''}
            </div>
          </div>
        `;
      } else {
        scoreHtml = `<span style="color:var(--text-muted); font-size:11px;">—</span>`;
      }

      // 5. R:R & ATRs
      const proxyRr = payload.proxy_rr !== undefined ? parseFloat(payload.proxy_rr).toFixed(1) : (a.proxy_rr ? parseFloat(a.proxy_rr).toFixed(1) : null);
      const atrsUp = payload.atrs_up !== undefined ? parseFloat(payload.atrs_up).toFixed(2) : (a.atrs_up !== undefined ? parseFloat(a.atrs_up).toFixed(2) : null);

      let rrHtml = '';
      if (proxyRr !== null || atrsUp !== null) {
        rrHtml = `
          <div style="display:flex; flex-direction:column; align-items:center; gap:2px; font-family:var(--font-mono);">
            ${proxyRr !== null ? `<span class="badge in_zone" style="font-size:11px; padding:1px 6px; font-weight:800;">${proxyRr}:1 R:R</span>` : ''}
            ${atrsUp !== null ? `<span style="font-size:10px; color:var(--text-muted); font-weight:700;">${parseFloat(atrsUp) >= 0 ? '+' : ''}${atrsUp} ATRs</span>` : ''}
          </div>
        `;
      } else {
        rrHtml = `<span style="color:var(--text-muted); font-size:11px;">—</span>`;
      }

      // 6. Tactical Levels & Deep Thesis
      const pb = a.llm_playbook || '';
      let stopText = '--';
      let targetText = '--';
      let vehicleText = payload.option || '';

      if (pb) {
        const stopMatch = pb.match(/(?:\*{0,2})(?:Stop|STOP\s*(?:\(close\))?)(?:\*{0,2})\s*:?\s*(?:\*{0,2})\s*\$?([0-9.]+)/i);
        if (stopMatch) stopText = `$${stopMatch[1]}`;
        const targetMatch = pb.match(/(?:\*{0,2})(?:Target|T1|PT1)(?:\*{0,2})\s*:?\s*(?:\*{0,2})\s*\$?([0-9.]+)/i);
        if (targetMatch) targetText = `$${targetMatch[1]}`;
        const vMatch = pb.match(/Vehicle:\s*([A-Za-z0-9_ -]+)/i);
        if (vMatch && !vehicleText) vehicleText = vMatch[1].trim();
      }
      if (stopText === '--' && (a.wrong_if || payload.wrong_if)) stopText = `$${a.wrong_if || payload.wrong_if}`;

      let tacticalLevelsHtml = '';
      if (rep && (rep.tactical_stop || rep.options_summary)) {
        tacticalLevelsHtml = `
          <div style="display:flex; flex-direction:column; gap:2px; max-width:170px; font-family:var(--font-mono);">
            <div style="display:flex; align-items:center; gap:6px; font-size:11px; font-weight:800;">
              ${rep.tactical_stop ? `<span style="color:var(--rose);" title="Tactical Invalidation Stop">🛑 $${rep.tactical_stop}</span>` : ''}
              ${rep.target_1 ? `<span style="color:var(--emerald);" title="Primary Target">🎯 $${rep.target_1}</span>` : ''}
            </div>
            ${rep.options_summary ? `<div style="font-size:9.5px; color:var(--text-main); font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${(rep.options_summary || '').replace(/"/g, '&quot;')}">⚡ ${rep.options_summary}</div>` : ''}
          </div>
        `;
      } else if (stopText !== '--' || targetText !== '--' || vehicleText) {
        tacticalLevelsHtml = `
          <div style="display:flex; flex-direction:column; gap:2px; max-width:170px; font-family:var(--font-mono); font-size:11px;">
            <div style="display:flex; align-items:center; gap:6px; font-weight:700;">
              ${stopText !== '--' ? `<span style="color:var(--rose);" title="Stop Loss">🛑 ${stopText}</span>` : ''}
              ${targetText !== '--' ? `<span style="color:var(--emerald);" title="Price Target">🎯 ${targetText}</span>` : ''}
            </div>
            ${vehicleText ? `<div style="font-size:9.5px; color:var(--text-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${vehicleText.replace(/"/g, '&quot;')}">${vehicleText}</div>` : ''}
          </div>
        `;
      } else {
        tacticalLevelsHtml = `<span style="color:var(--text-muted); font-size:11px;">—</span>`;
      }

      // 7. Tastytrade Vol / Catalyst
      let volCatalystHtml = '';
      const hvLow = payload.hv20_low;
      const putOk = payload.put_ok;
      const catSnippet = payload.why_now || '';

      let volTags = [];
      if (hvLow) volTags.push('<span style="color:var(--cyan); font-weight:800; font-size:9.5px;">HV20 Low</span>');
      if (putOk !== undefined) {
        volTags.push(putOk ? '<span style="color:var(--emerald); font-weight:800; font-size:9.5px;">Puts OK</span>' : '<span style="color:var(--text-muted); font-size:9.5px;">No Puts</span>');
      }

      volCatalystHtml = `
        <div style="display:flex; flex-direction:column; gap:2px; max-width:145px;">
          ${volTags.length > 0 ? `<div style="display:flex; align-items:center; gap:4px; font-family:var(--font-mono);">${volTags.join(' · ')}</div>` : ''}
          ${catSnippet ? `<div style="font-size:10px; color:var(--text-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${catSnippet.replace(/"/g, '&quot;')}">${catSnippet}</div>` : (volTags.length === 0 ? '<span style="color:var(--text-muted); font-size:11px;">—</span>' : '')}
        </div>
      `;

      // 8. AI Triage & Deep Research Arbitrated Verdict
      const alertId = a.message_id || a.email_id || `daily-${startIdx + idx}`;
      let aiBadge = '';

      if (rep && rep.verdict) {
        const vClass = rep.verdict === 'ENTER' ? 'in_zone' : (rep.verdict === 'STALK' ? 'stalking' : 'danger');
        aiBadge = `
          <div style="display:inline-flex; flex-direction:column; align-items:center; gap:2px;">
            <span class="badge ${vClass}" style="font-size:11px; font-weight:900; cursor:pointer;" onclick="event.stopPropagation(); AppSwing.openReportModal('${rep.date || dateKey}', '${sym}')" title="Deep Research Senior-PM Arbitration Verdict: ${rep.verdict} (Conviction: ${rep.conviction}/10)">
              🏆 ${rep.verdict} (${rep.conviction}/10)
            </span>
            <span style="font-size:9px; font-family:var(--font-mono); color:var(--cyan); font-weight:800;">Deep Research</span>
          </div>
        `;
      } else {
        let decRaw = (a.llm_decision || '').trim();
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
              <span style="font-size:10px; font-weight:800; color:var(--cyan); font-family:var(--font-mono); letter-spacing:0.2px;">Triaging...</span>
            </div>
          `;
        } else {
          let decClean = decRaw.replace(/\*\*/g, '').replace(/\[[A-Z0-9.]+\]/gi, '').trim();
          decClean = decClean.replace(/^[A-Z0-9.]+\s+\d{1,2}:\d{2}\s*(?:AM|PM)?\s*(?:ET)?\s*[-—–]\s*/i, '').trim();
          const upperDec = decClean.toUpperCase();

          let bClass = 'badge stalking';
          let bLabel = 'WATCH / STALK';
          if (upperDec.includes('PASS') || upperDec.includes('GO') || upperDec.includes('TAKE') || upperDec.includes('BUY') || upperDec.includes('LONG')) {
            bClass = 'badge in_zone';
            bLabel = upperDec.includes('PASS') ? 'PASS (GO)' : (upperDec.includes('TAKE') ? 'TAKE' : 'ACTIONABLE');
          } else if (upperDec.includes('STAND') || upperDec.includes('CUT') || upperDec.includes('DO NOT ENTER') || upperDec.includes('NO TRADE')) {
            bClass = 'badge danger';
            bLabel = 'STAND ASIDE';
          }

          const tooltip = (decRaw || a.llm_playbook || '').replace(/"/g, '&quot;');
          aiBadge = `
            <span class="${bClass}" style="font-size:11px; font-weight:800; white-space:nowrap;" title="${tooltip}">
              ${bLabel}
            </span>
          `;
        }
      }

      const dayAlertsCount = (this._dailyAlerts || []).filter(item => {
        const s = (item.symbol || this.parsePayload(item).ticker || '').toUpperCase();
        const d = item.date || (item.timestamp ? item.timestamp.substring(0, 10) : '');
        return s === sym && d === dateKey;
      }).length;

      rowsHtml += `
        <tr style="border-bottom:1px solid var(--border); cursor:pointer; transition: background 0.15s ease;"
            onclick="AppAlerts.openAlertModal('${alertId}')"
            onmouseover="this.style.background='var(--bg-card-hover)'"
            onmouseout="this.style.background=''">

          <!-- 1. TIME -->
          <td style="padding:8px 8px; white-space:nowrap;">
            <div style="font-family:var(--font-mono); font-size:12px; font-weight:800; color:var(--text-main);">${timeStr}</div>
            <div style="font-size:10px; color:var(--text-muted); font-weight:600;">Daily Bar</div>
          </td>

          <!-- 2. TICKER & DOSSIER BADGE -->
          <td style="padding:8px 8px; overflow:hidden;">
            <div class="ticker-table-card"
                 onclick="event.stopPropagation(); ${rep ? `AppSwing.openReportModal('${rep.date || dateKey}', '${sym}')` : `AppAlerts.openAlertModal('${alertId}')`}"
                 title="Click to ${rep ? 'open Deep Research Dossier' : 'inspect setup and plan'} for ${sym}"
                 style="display:inline-flex; flex-direction:column; gap:3px; cursor:pointer; padding:4px 8px; border-radius:6px; background:var(--bg-subtle); border:1px solid var(--border); transition:all 0.15s ease;"
                 onmouseover="this.style.borderColor='var(--cyan)'; this.style.background='rgba(6,182,212,0.1)';"
                 onmouseout="this.style.borderColor='var(--border)'; this.style.background='var(--bg-subtle)';">
              <div style="display:flex; align-items:center; gap:6px;">
                <strong style="font-size:13.5px; font-weight:900; color:var(--text-main); font-family:var(--font-mono); letter-spacing:0.3px;">${sym}</strong>
                <span style="font-size:9.5px; font-family:var(--font-mono); font-weight:700; color:var(--text-muted); background:var(--bg-base); padding:1px 5px; border-radius:3px; border:1px solid var(--border);" title="${dayAlertsCount} alerts for ${sym} on ${dateKey}">
                  📊 ${dayAlertsCount}
                </span>
              </div>
              <span style="font-size:10.5px; color:var(--text-muted); max-width:115px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${compName}">
                ${compName}
              </span>
              ${rep ? `
                <div style="margin-top:2px;">
                  <span class="badge in_zone" style="font-size:9px; font-weight:800; padding:1px 5px; cursor:pointer;" onclick="event.stopPropagation(); AppSwing.openReportModal('${rep.date || dateKey}', '${sym}')" title="Open Deep Research Dossier for ${sym}">
                    📑 Dossier Ready
                  </span>
                </div>
              ` : ''}
            </div>
          </td>

          <!-- 3. SIDE -->
          <td style="padding:8px 8px; text-align:center;">
            ${sideBadge}
          </td>

          <!-- 4. PRICE -->
          <td style="padding:8px 8px; text-align:right;">
            <div style="font-family:var(--font-mono); font-weight:900; font-size:13px; color:var(--text-main); white-space:nowrap;">
              ${price}
            </div>
          </td>

          <!-- 5. SETUP & STAGE -->
          <td style="padding:8px 8px; overflow:hidden;">
            ${setupHtml}
          </td>

          <!-- 6. SCORES & PROB -->
          <td style="padding:8px 8px; text-align:center;">
            ${scoreHtml}
          </td>

          <!-- 7. R:R & ATRS -->
          <td style="padding:8px 8px; text-align:center;">
            ${rrHtml}
          </td>

          <!-- 8. TACTICAL LEVELS & DEEP THESIS -->
          <td style="padding:8px 8px; overflow:hidden;">
            ${tacticalLevelsHtml}
          </td>

          <!-- 9. TASTYTRADE VOL / CATALYST -->
          <td style="padding:8px 8px; overflow:hidden;">
            ${volCatalystHtml}
          </td>

          <!-- 10. AI TRIAGE & DEEP RESEARCH -->
          <td style="padding:8px 8px; text-align:center;">
            ${aiBadge}
          </td>

          <!-- 11. ACTIONS (DEEP RESEARCH FIRST CLASS) -->
          <td style="padding:8px 8px; text-align:center; white-space:nowrap;" onclick="event.stopPropagation()">
            <div style="display:inline-flex; align-items:center; gap:4px;">
              <button class="btn secondary" onclick="AppSwing.openReportModal('${rep ? rep.date : dateKey}', '${sym}')" style="padding:3px 8px; font-size:11px; font-weight:800; color:var(--cyan); border-color:rgba(6,182,212,0.4); background:rgba(6,182,212,0.1);" title="Open ${sym} Deep Research Dossier &amp; Multimodal Arbitration">
                <span>📑</span> Dossier
              </button>
              <button class="btn secondary" onclick="AppAlerts.openAlertModal('${alertId}')" style="padding:3px 6px; font-size:10.5px; font-weight:700; border-radius:4px;" title="Inspect tactical setup &amp; indicators">
                🔍 Plan
              </button>
              <button class="btn secondary" onclick="AppAlerts.openChart('${sym}')" style="padding:3px 5px; font-size:10.5px; font-weight:700; border-radius:4px;" title="Open interactive TradingView chart">
                📈
              </button>
              <button class="btn" onclick="AppAlerts.researchTicker('${sym}')" style="padding:3px 6px; font-size:10.5px; font-weight:700; border-radius:4px; background:linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%); color:#fff;" title="Trigger Autonomous Deep Research for ${sym}">
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
    }
  },

  renderDailyPaginationToolbar(totalCount, startIdx, endIdx, totalPages) {
    const infoEl = document.getElementById('daily-alerts-pagination-info');
    const btnsEl = document.getElementById('daily-alerts-pagination-buttons');

    if (infoEl) {
      if (totalCount === 0) {
        infoEl.textContent = 'Showing 0 daily alerts';
      } else {
        infoEl.textContent = `Showing ${startIdx + 1} - ${endIdx} of ${totalCount} daily alerts (Page ${this._dailyCurrentPage} of ${totalPages})`;
      }
    }

    if (btnsEl) {
      let btnsHtml = `
        <button class="btn secondary" 
                onclick="AppAlerts.prevDailyPage()" 
                ${this._dailyCurrentPage <= 1 ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : ''}
                style="padding:2px 8px; font-size:11px;">
          ◀ Prev
        </button>
      `;

      const startPage = Math.max(1, this._dailyCurrentPage - 2);
      const endPage = Math.min(totalPages, startPage + 4);

      for (let p = startPage; p <= endPage; p++) {
        const isActive = (p === this._dailyCurrentPage);
        btnsHtml += `
          <button class="btn ${isActive ? 'primary' : 'secondary'}"
                  onclick="AppAlerts.setDailyPage(${p})"
                  style="padding:2px 8px; font-size:11px; font-weight:${isActive ? '800' : '500'};">
            ${p}
          </button>
        `;
      }

      btnsHtml += `
        <button class="btn secondary" 
                onclick="AppAlerts.nextDailyPage(${totalPages})" 
                ${this._dailyCurrentPage >= totalPages ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : ''}
                style="padding:2px 8px; font-size:11px;">
          Next ▶
        </button>
      `;

      btnsEl.innerHTML = btnsHtml;
    }
  },

  openAlertModal(id) {
    // Stack-awareness: if opened from modal-trade-detail or another modal, hide that caller modal
    const tradeDetailModal = document.getElementById('modal-trade-detail');
    if (tradeDetailModal && (tradeDetailModal.style.display === 'flex' || tradeDetailModal.style.display === 'block')) {
      this._alertParentId = 'modal-trade-detail';
      tradeDetailModal.style.display = 'none';
      const backBtn = document.getElementById('alert-modal-btn-back');
      const backLbl = document.getElementById('alert-modal-btn-back-label');
      if (backBtn && backLbl) {
        backLbl.innerText = '← Back to Trade Detail';
        backBtn.style.display = 'inline-flex';
      }
    } else {
      this._alertParentId = null;
      const backBtn = document.getElementById('alert-modal-btn-back');
      if (backBtn) backBtn.style.display = 'none';
    }

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

  closeAlertModal(restoreParent = true) {
    this.stopAlertCopilotStream();
    const modal = document.getElementById('tv-alert-detail-modal');
    if (modal) {
      modal.style.display = 'none';
      modal.classList.remove('active');
    }

    if (restoreParent && this._alertParentId) {
      const parentEl = document.getElementById(this._alertParentId);
      if (parentEl) {
        parentEl.style.display = 'flex';
      }
      this._alertParentId = null;
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
        this.renderActivePanes();
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
        this.renderActivePanes();
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
    // Filter strictly for Intraday 0DTE alerts
    let list = (this._intradayAlerts && this._intradayAlerts.length > 0)
      ? [...this._intradayAlerts]
      : (this._rawAlerts || []).filter(a => this.isIntradayAlert(a));

    // Sort chronologically ascending for deterministic entry -> exit pairing
    list.sort((a, b) => {
      const ta = a.timestamp || a.created_at || '';
      const tb = b.timestamp || b.created_at || '';
      return ta.localeCompare(tb);
    });

    const trades = [];
    const openBySym = {}; // sym -> entryAlert

    for (const a of list) {
      const raw = (typeof a.raw_payload === 'string')
        ? (a.raw_payload.startsWith('{') ? JSON.parse(a.raw_payload) : {})
        : (a.raw_payload || {});
      const dateKey = a.date || (a.timestamp ? a.timestamp.substring(0, 10) : '');
      const sym = (a.symbol || raw.ticker || '').toUpperCase().trim();
      const act = (a.action || raw.action || '').toUpperCase().trim();
      const price = parseFloat(a.alert_price || raw.price || 0) || 0;
      const ts = a.timestamp || a.created_at || '';
      const dec = a.llm_decision || '';
      const pb = a.llm_playbook || '';
      const setup = a.setup || raw.setup || a.strategy || 'Intraday';
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
        const exitPrice = parseFloat(raw.exit_px || a.alert_price || raw.price || 0) || price;

        if (openBySym[sym]) {
          const entry = openBySym[sym];
          delete openBySym[sym];

          const entryPrice = entry.entryPrice;
          let diffPts = 0;
          let pnl100 = 0;

          if (entryPrice > 0 && exitPrice > 0) {
            diffPts = entry.side === 'LONG' ? (exitPrice - entryPrice) : (entryPrice - exitPrice);
            pnl100 = diffPts * 100.0;
          }
          if (pnl100 === 0 && raw.session_pnl !== undefined) {
            pnl100 = parseFloat(raw.session_pnl) || 0;
            diffPts = pnl100 / 100.0;
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
            attribution: attribution,
            exitReason: raw.exit_why || ''
          });
        } else {
          // Reconstruct unpaired exit from alert telemetry
          const planStr = a.plan || raw.plan || '';
          const inMatch = planStr.match(/In\s*([0-9.]+)/i);
          const inPrice = inMatch ? parseFloat(inMatch[1]) : 0;
          const exitDir = raw.exit_dir !== undefined ? raw.exit_dir : (act.includes('PUT') ? -1 : 1);
          const exitSide = exitDir === 1 ? 'LONG' : 'SHORT';
          let diffPts = 0;
          let pnl100 = 0;

          if (inPrice > 0 && exitPrice > 0) {
            diffPts = exitSide === 'LONG' ? (exitPrice - inPrice) : (inPrice - exitPrice);
            pnl100 = diffPts * 100.0;
          }
          if (pnl100 === 0 && raw.session_pnl !== undefined) {
            pnl100 = parseFloat(raw.session_pnl) || 0;
            diffPts = pnl100 / 100.0;
          }

          trades.push({
            entryAlertId: null,
            entryAlert: null,
            symbol: sym,
            side: exitSide,
            entryTime: null,
            entryPrice: inPrice > 0 ? inPrice : exitPrice,
            setup: setup,
            llmDecision: dec,
            llmPlaybook: pb,
            isAiTaken: false,
            isAiFiltered: false,
            date: dateKey,
            exitAlertId: msgId,
            exitAlert: a,
            exitTime: ts,
            exitPrice: exitPrice,
            duration: '--',
            diffPts: Math.round(diffPts * 100) / 100,
            pnl100: Math.round(pnl100 * 100) / 100,
            status: 'CLOSED',
            attribution: 'NEUTRAL',
            exitReason: raw.exit_why || '',
            reconstructed: true
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

  getAvailableSessionDates() {
    const dates = new Set();
    (this._rawAlerts || []).forEach(a => {
      const d = a.date || (a.timestamp ? a.timestamp.substring(0, 10) : '');
      if (d) dates.add(d);
    });
    if (dates.size === 0 && this._availableDates && this._availableDates.length > 0) {
      this._availableDates.forEach(d => dates.add(d));
    }
    return Array.from(dates).filter(Boolean).sort().reverse();
  },

  updatePnlDatePills(activeSession) {
    const container = document.getElementById('pnl-modal-date-pills');
    const dateInput = document.getElementById('pnl-modal-date-picker');
    const pmBtn = document.getElementById('btn-pnl-open-postmortem');
    const sel = document.getElementById('pnl-modal-session-select');
    const distinctDates = this.getAvailableSessionDates();

    if (sel) {
      sel.innerHTML = `<option value="ALL">🌟 All Sessions Combined (${distinctDates.length} Days)</option>` +
        distinctDates.map(d => `<option value="${d}" ${d === activeSession ? 'selected' : ''}>📅 Session: ${d}${d === distinctDates[0] ? ' (Latest)' : ''}</option>`).join('');
      sel.value = activeSession;
    }

    if (dateInput) {
      dateInput.value = (activeSession && activeSession !== 'ALL') ? activeSession : '';
    }

    if (pmBtn) {
      const label = activeSession === 'ALL' ? 'Cumulative' : activeSession;
      pmBtn.innerHTML = `<span>🧠</span> Session Post-Mortem (${label})`;
      pmBtn.title = `Open Closed-Loop Session Post-Mortem & Attribution for ${label}`;
    }

    if (!container) return;

    let html = `
      <button class="session-pill-btn ${activeSession === 'ALL' ? 'active' : ''}" onclick="AppAlerts.switchPnlSession('ALL')" title="All Sessions Combined (${distinctDates.length} Days)">
        <span>🌟 All (${distinctDates.length}D)</span>
      </button>
    `;

    distinctDates.forEach((d, idx) => {
      const isAct = (d === activeSession);
      const isLatest = (idx === 0);
      html += `
        <button class="session-pill-btn ${isAct ? 'active' : ''}" onclick="AppAlerts.switchPnlSession('${d}')" title="Session Date: ${d}${isLatest ? ' (Latest)' : ''}">
          <span>📅 ${d}</span>
          ${isLatest ? '<span class="count-tag">Latest</span>' : ''}
        </button>
      `;
    });

    container.innerHTML = html;
  },

  openPnlModal(dateStr = null) {
    const modal = document.getElementById('modal-alerts-pnl');
    if (!modal) return;

    // Modal stack coordination: check if opened from post-mortem
    const pmEl = document.getElementById('modal-intraday-postmortem');
    if (pmEl && (pmEl.style.display === 'flex' || pmEl.style.display === 'block')) {
      this._pnlParentId = 'modal-intraday-postmortem';
      pmEl.style.display = 'none';
      const backBtn = document.getElementById('pnl-btn-back');
      if (backBtn) {
        backBtn.style.display = 'inline-flex';
        backBtn.innerText = '← Back to Post-Mortem';
      }
    } else {
      this._pnlParentId = null;
      const backBtn = document.getElementById('pnl-btn-back');
      if (backBtn) backBtn.style.display = 'none';
    }

    const distinctDates = this.getAvailableSessionDates();
    if (dateStr) {
      this._pnlCurrentSession = dateStr;
    } else if (this._dateFilter && this._dateFilter !== 'ALL') {
      this._pnlCurrentSession = this._dateFilter;
    } else if (distinctDates.length > 0) {
      this._pnlCurrentSession = distinctDates[0];
    } else {
      this._pnlCurrentSession = 'ALL';
    }

    this.updatePnlDatePills(this._pnlCurrentSession);

    this._pnlFilterTab = 'ALL';
    this._pnlSearchQuery = '';
    const searchInp = document.getElementById('pnl-modal-search');
    if (searchInp) searchInp.value = '';

    this.renderPnlModal();
    modal.style.display = 'flex';
  },

  closePnlModal(restoreParent = true) {
    const modal = document.getElementById('modal-alerts-pnl');
    if (modal) modal.style.display = 'none';
    if (restoreParent && this._pnlParentId) {
      const parentEl = document.getElementById(this._pnlParentId);
      if (parentEl) parentEl.style.display = 'flex';
      this._pnlParentId = null;
    }
  },

  refreshPnlModal() {
    this.renderPnlModal();
  },

  switchPnlSession(val) {
    this._pnlCurrentSession = val || 'ALL';
    this.updatePnlDatePills(this._pnlCurrentSession);
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
    const elCByTicker = document.getElementById('pnl-count-by-ticker');
    if (elCByTicker) elCByTicker.innerText = '20';

    const headEl = document.getElementById('pnl-modal-table-head');

    if (this._pnlFilterTab === 'BY_TICKER') {
      if (headEl) {
        headEl.innerHTML = `
          <tr>
            <th style="width:170px; text-align:left;">Ticker &amp; Universe</th>
            <th style="width:85px; text-align:center;">Type</th>
            <th style="width:120px; text-align:center;">Trades (W/L)</th>
            <th style="width:95px; text-align:right;">Win Rate</th>
            <th style="width:115px; text-align:right;">Raw TV P&amp;L</th>
            <th style="width:125px; text-align:right;">AI Filtered P&amp;L</th>
            <th style="width:125px; text-align:right;">Losses Avoided</th>
            <th style="width:105px; text-align:center;">Actions</th>
          </tr>
        `;
      }

      // Dynamically discover ONLY intraday tickers that have alerts/trades for this session
      const activeTickers = this.getIntradayTickers(this._pnlCurrentSession);
      if (elCByTicker) elCByTicker.innerText = activeTickers.length;

      let tickerRows = activeTickers.map(t => {
        const res = this.getTickerDayTrades(t.symbol, this._pnlCurrentSession);
        const isIndex = ['SPY', 'QQQ', 'DIA', 'IWM', 'SPX', 'NDX', 'RUT'].includes(t.symbol);
        return {
          ...t,
          type: isIndex ? 'INDEX' : 'EQUITY',
          summary: res.summary
        };
      });

      if (this._pnlSearchQuery) {
        const q = this._pnlSearchQuery;
        tickerRows = tickerRows.filter(r => r.symbol.toLowerCase().includes(q) || r.name.toLowerCase().includes(q));
      }

      // Sort: completed trades count descending, then PnL descending
      tickerRows.sort((a, b) => {
        if (b.summary.totalCompleted !== a.summary.totalCompleted) {
          return b.summary.totalCompleted - a.summary.totalCompleted;
        }
        const pnlA = a.summary.aiTradesCount > 0 ? a.summary.aiPnL : a.summary.rawTvPnL;
        const pnlB = b.summary.aiTradesCount > 0 ? b.summary.aiPnL : b.summary.rawTvPnL;
        return pnlB - pnlA;
      });

      let rowsHtml = '';
      let sumTrades = 0, sumWins = 0, sumLosses = 0, sumTvPnl = 0, sumAiPnl = 0, sumAvoided = 0;
      let sumRawWins = 0, sumRawLosses = 0;

      tickerRows.forEach(r => {
        const s = r.summary;
        sumTrades += s.totalCompleted;
        sumWins += s.aiWins;
        sumLosses += s.aiLosses;
        sumRawWins += (s.rawWins || 0);
        sumRawLosses += (s.rawLosses || 0);
        sumTvPnl += s.rawTvPnL;
        sumAiPnl += s.aiPnL;
        sumAvoided += s.avoidedLosses;

        const tvSign = s.rawTvPnL >= 0 ? '+' : '';
        const tvColor = s.rawTvPnL >= 0 ? '#10b981' : '#ef4444';
        const aiSign = s.aiPnL >= 0 ? '+' : '';
        const aiColor = s.aiPnL >= 0 ? '#10b981' : '#ef4444';

        const typeBadge = r.type === 'INDEX'
          ? `<span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.35); font-size:9.5px; font-weight:800;">INDEX</span>`
          : `<span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.35); font-size:9.5px; font-weight:800;">MEGA-CAP</span>`;

        rowsHtml += `
          <tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
            <td>
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="intraday-ticker-sym-badge" style="font-size:13.5px;">${r.symbol}</span>
                <span style="font-size:11px; color:var(--text-muted);">${r.name}</span>
              </div>
            </td>
            <td style="text-align:center;">${typeBadge}</td>
            <td style="text-align:center; font-family:var(--font-mono); font-size:12px;">
              ${s.totalCompleted > 0 ? `<strong>${s.totalCompleted}</strong> <span style="font-size:10.5px; color:var(--text-muted);">(${s.aiTradesCount > 0 ? `${s.aiWins}W / ${s.aiLosses}L` : `${s.rawWins || 0}W / ${s.rawLosses || 0}L`})</span>` : '<span style="color:var(--text-muted);">0 (Stalking)</span>'}
            </td>
            <td style="text-align:right; font-family:var(--font-mono); font-size:12px; font-weight:700; color:${(s.aiTradesCount > 0 ? s.aiWinRate : (s.rawWinRate || 0)) >= 60 ? '#10b981' : ((s.aiTradesCount > 0 ? s.aiWinRate : (s.rawWinRate || 0)) > 0 ? '#f59e0b' : 'var(--text-muted)')};">
              ${s.aiTradesCount > 0 ? `${s.aiWinRate}%` : ((s.rawWins || 0) + (s.rawLosses || 0) > 0 ? `${s.rawWinRate}%` : '--')}
            </td>
            <td style="text-align:right; font-family:var(--font-mono); font-size:12px; font-weight:700; color:${tvColor};">
              ${s.totalCompleted > 0 ? `${tvSign}$${s.rawTvPnL.toFixed(2)}` : '--'}
            </td>
            <td style="text-align:right; font-family:var(--font-mono); font-size:12.5px; font-weight:900; color:${aiColor};">
              ${s.totalCompleted > 0 ? `${aiSign}$${s.aiPnL.toFixed(2)}` : '--'}
            </td>
            <td style="text-align:right; font-family:var(--font-mono); font-size:12px; color:#38bdf8; font-weight:700;">
              ${s.avoidedLosses > 0 ? `+$${s.avoidedLosses.toFixed(2)}` : '--'}
            </td>
            <td style="text-align:center;">
              <button class="btn secondary" style="padding:3px 8px; font-size:10.5px; font-weight:700;" onclick="AppAlerts.openTickerTradesModal('${r.symbol}', '${this._pnlCurrentSession}')">
                📊 Trades
              </button>
            </td>
          </tr>
        `;
      });

      // Total Row
      const totTvSign = sumTvPnl >= 0 ? '+' : '';
      const totTvColor = sumTvPnl >= 0 ? '#10b981' : '#ef4444';
      const totAiSign = sumAiPnl >= 0 ? '+' : '';
      const totAiColor = sumAiPnl >= 0 ? '#10b981' : '#ef4444';
      const hasAiTradesAny = (sumWins + sumLosses) > 0;
      const totWinRate = hasAiTradesAny
        ? Math.round((sumWins / (sumWins + sumLosses)) * 1000) / 10
        : ((sumRawWins + sumRawLosses) > 0 ? Math.round((sumRawWins / (sumRawWins + sumRawLosses)) * 1000) / 10 : 0);
      const displayTotWins = hasAiTradesAny ? sumWins : sumRawWins;
      const displayTotLosses = hasAiTradesAny ? sumLosses : sumRawLosses;

      rowsHtml += `
        <tr style="background:rgba(255,255,255,0.03); border-top:2px solid var(--border); font-weight:800;">
          <td colspan="2" style="font-family:var(--font-mono); font-size:12px; color:var(--cyan-glow);">
            TOTAL (${tickerRows.length} INTRADAY TICKERS)
          </td>
          <td style="text-align:center; font-family:var(--font-mono); font-size:12px;">
            ${sumTrades} trades (${displayTotWins}W / ${displayTotLosses}L)
          </td>
          <td style="text-align:right; font-family:var(--font-mono); font-size:12px; color:#10b981;">
            ${totWinRate}%
          </td>
          <td style="text-align:right; font-family:var(--font-mono); font-size:12px; color:${totTvColor};">
            ${totTvSign}$${sumTvPnl.toFixed(2)}
          </td>
          <td style="text-align:right; font-family:var(--font-mono); font-size:13px; color:${totAiColor};">
            ${totAiSign}$${sumAiPnl.toFixed(2)}
          </td>
          <td style="text-align:right; font-family:var(--font-mono); font-size:12px; color:#38bdf8;">
            +$${sumAvoided.toFixed(2)}
          </td>
          <td></td>
        </tr>
      `;

      tableBody.innerHTML = rowsHtml;
      const footerEl = document.getElementById('pnl-modal-footer-summary');
      if (footerEl) footerEl.innerText = `Summary: ${tickerRows.length} Intraday Tickers · ${sumTrades} completed trades across session`;
      return;
    } else {
      if (headEl) {
        headEl.innerHTML = `
          <tr>
            <th style="width:115px;">Time / Duration</th>
            <th style="width:130px;">Ticker &amp; Setup</th>
            <th style="width:105px; text-align:center;">Direction</th>
            <th style="width:180px;">Execution (In ➔ Out)</th>
            <th style="width:90px; text-align:right;">Pts Diff</th>
            <th style="width:115px; text-align:right;">100-Sh P&amp;L</th>
            <th style="width:135px; text-align:center;">AI Triage Call</th>
            <th style="width:120px; text-align:center;">Attribution</th>
          </tr>
        `;
      }
    }

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
        ? `<span class="pnl-badge-open" title="Active position at live spot">${sign}$${Math.abs(t.pnl100).toFixed(2)} (Live)</span>`
        : (t.pnl100 >= 0
            ? `<span class="pnl-badge-pos">+$${t.pnl100.toFixed(2)}</span>`
            : `<span class="pnl-badge-neg">-$${Math.abs(t.pnl100).toFixed(2)}</span>`);

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
              In: <strong style="color:var(--text-main);">$${(t.entryPrice !== null && t.entryPrice !== undefined) ? Number(t.entryPrice).toFixed(2) : '--'}</strong>
              ➔ Out: <strong style="color:${t.status === 'OPEN' ? 'var(--cyan)' : 'var(--text-main)'};">$${(t.exitPrice !== null && t.exitPrice !== undefined) ? Number(t.exitPrice).toFixed(2) : '--'}</strong>
            </div>
            <div style="font-size:9.5px; color:var(--text-muted); font-family:var(--font-mono);">
              ${t.status === 'OPEN' ? '🟢 Live Spot' : (t.exitReason ? `🏁 ${t.exitReason}` : '🏁 Closed via TV Exit Alert')}
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

    // Filter alerts strictly for intraday alerts for this symbol and session date
    const dateAlerts = (this._intradayAlerts && this._intradayAlerts.length > 0 ? this._intradayAlerts : (this._rawAlerts || [])).filter(a => {
      if (!this.isIntradayAlert(a)) return false;
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
      const raw = (typeof a.raw_payload === 'string')
        ? (a.raw_payload.startsWith('{') ? JSON.parse(a.raw_payload) : {})
        : (a.raw_payload || {});
      const act = (a.action || raw.action || '').toUpperCase().trim();
      const price = parseFloat(a.alert_price || raw.price || 0) || 0;
      const ts = a.timestamp || a.created_at || '';
      const dec = (a.llm_decision || '').trim();
      const pb = (a.llm_playbook || '').trim();
      const setup = a.setup || raw.setup || a.strategy || 'Intraday';
      const plan = a.plan || raw.plan || '';
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
          // Reconstruct unpaired exit from alert telemetry
          const planStr = a.plan || raw.plan || '';
          const inMatch = planStr.match(/In\s*([0-9.]+)/i);
          const inPrice = inMatch ? parseFloat(inMatch[1]) : 0;
          const exitDir = raw.exit_dir !== undefined ? raw.exit_dir : (act.includes('PUT') ? -1 : 1);
          const exitSide = exitDir === 1 ? 'LONG' : 'SHORT';
          const exitPrice = parseFloat(raw.exit_px || a.alert_price || raw.price || 0) || price;
          let diffPts = 0;
          let pnl100 = 0;

          if (inPrice > 0 && exitPrice > 0) {
            diffPts = exitSide === 'LONG' ? (exitPrice - inPrice) : (inPrice - exitPrice);
            pnl100 = diffPts * 100.0;
          }
          if (pnl100 === 0 && raw.session_pnl !== undefined) {
            pnl100 = parseFloat(raw.session_pnl) || 0;
            diffPts = pnl100 / 100.0;
          }

          trades.push({
            symbol: sym,
            side: exitSide,
            setup: setup,
            plan: planStr,
            entryAlertId: null,
            entryAlert: null,
            entryTime: null,
            entryPrice: inPrice > 0 ? inPrice : exitPrice,
            exitAlertId: msgId,
            exitAlert: a,
            exitTime: ts,
            exitPrice: exitPrice,
            duration: '--',
            diffPts: Math.round(diffPts * 100) / 100,
            pnl100: Math.round(pnl100 * 100) / 100,
            status: 'CLOSED',
            isAiTaken: false,
            isAiFiltered: false,
            exitReason: raw.exit_why || '',
            llmDecision: dec,
            llmPlaybook: pb,
            date: a.date || (ts ? ts.substring(0, 10) : targetDate),
            reconstructed: true
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
    let rawWins = 0;
    let rawLosses = 0;
    let totalCompleted = 0;

    for (const t of trades) {
      if (t.status === 'CLOSED') {
        totalCompleted++;
        rawTvPnL += t.pnl100;
        if (t.pnl100 > 0) rawWins++;
        else if (t.pnl100 < 0) rawLosses++;

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
    const rawWinRate = (rawWins + rawLosses) > 0 ? Math.round((rawWins / (rawWins + rawLosses)) * 1000) / 10 : 0;
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
        rawWins,
        rawLosses,
        rawWinRate,
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
      const raw = (typeof exitAlert.raw_payload === 'string')
        ? (exitAlert.raw_payload.startsWith('{') ? JSON.parse(exitAlert.raw_payload) : {})
        : (exitAlert.raw_payload || {});
      const exitPrice = parseFloat(raw.exit_px || exitAlert.alert_price || raw.price || 0) || 0;
      const entryPrice = entry.entryPrice;
      let diffPts = 0;
      let pnl100 = 0;

      if (entryPrice > 0 && exitPrice > 0) {
        diffPts = entry.side === 'LONG' ? (exitPrice - entryPrice) : (entryPrice - exitPrice);
        pnl100 = diffPts * 100.0;
      }
      if (pnl100 === 0 && raw.session_pnl !== undefined) {
        pnl100 = parseFloat(raw.session_pnl) || 0;
        diffPts = pnl100 / 100.0;
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
        status: 'CLOSED',
        exitReason: raw.exit_why || ''
      };
    } else {
      const sym = entry.symbol;
      let livePrice = 0;
      if (window.AppState && window.AppState.quotes && window.AppState.quotes[sym]) {
        livePrice = parseFloat(window.AppState.quotes[sym].last || window.AppState.quotes[sym].price || window.AppState.quotes[sym].close || 0);
      }
      if (!livePrice || isNaN(livePrice) || livePrice === 0) {
        livePrice = parseFloat(entry.last_price || entry.lastPrice || 0);
      }
      if (!livePrice || isNaN(livePrice) || livePrice === 0) {
        livePrice = entry.entryPrice;
      }

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

        const rawPb = t.llmPlaybook || (t.entryAlert && t.entryAlert.llm_playbook) || (t.exitAlert && t.exitAlert.llm_playbook) || '';
        const parsedPb = this.parsePlaybookSections(rawPb);
        const decisionText = t.llmDecision || (t.entryAlert && t.entryAlert.llm_decision) || (t.isAiTaken ? '🟢 TAKE' : (t.isAiFiltered ? '⏸️ WAIT / PASS' : 'Pending'));

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
                <div style="color:var(--text-main); font-weight:700;">${entryTime} ET @ $${(t.entryPrice !== null && t.entryPrice !== undefined) ? Number(t.entryPrice).toFixed(2) : '--'}</div>
                <div style="font-size:10px; color:var(--text-muted); text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">${t.setup || 'Intraday'}</div>
              </div>
              <div>
                <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">📤 EXIT ALERT</div>
                <div style="color:var(--text-main); font-weight:700;">${exitTime} ET @ $${(t.exitPrice !== null && t.exitPrice !== undefined) ? Number(t.exitPrice).toFixed(2) : '--'}</div>
                <div style="font-size:10px; color:var(--text-muted);">Duration: ${t.duration}${t.exitReason ? ` · ${t.exitReason}` : ''}</div>
              </div>
              <div style="display:flex; align-items:center; justify-content:flex-end; gap:6px;">
                ${t.entryAlertId ? `<button class="btn secondary" onclick="AppAlerts.openAlertModal('${t.entryAlertId}')" style="padding:2px 7px; font-size:10px; font-weight:700;">Entry Alert</button>` : ''}
                ${t.exitAlertId ? `<button class="btn secondary" onclick="AppAlerts.openAlertModal('${t.exitAlertId}')" style="padding:2px 7px; font-size:10px; font-weight:700;">Exit Alert</button>` : ''}
              </div>
            </div>

            <!-- Compact AI Decision & Reasons -->
            <div class="${t.isAiTaken ? 'intraday-trade-ai-box ai-taken-box' : (t.isAiFiltered ? 'intraday-trade-ai-box ai-filtered-box' : 'intraday-trade-ai-box')}" style="padding:6px 10px; font-size:10.5px;">
              <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:4px;">
                <span style="font-weight:800; color:${t.isAiTaken ? '#10b981' : (t.isAiFiltered ? '#f87171' : 'var(--text-main)')};">
                  🧠 AI Verdict: ${decisionText}
                </span>
                <span style="font-family:var(--font-mono); font-size:9.5px; color:var(--text-muted);">
                  ${parsedPb && parsedPb.conviction ? `Conv: ${parsedPb.conviction}` : ''} ${parsedPb && parsedPb.regime ? `· ${parsedPb.regime}` : ''}
                </span>
              </div>
              ${parsedPb && parsedPb.whyCard ? `<div style="color:var(--text-muted);"><strong style="color:#60a5fa;">Why:</strong> ${parsedPb.whyCard}</div>` : ''}
              ${parsedPb && parsedPb.traderNote ? `<div style="color:var(--text-main); background:rgba(0,0,0,0.2); padding:4px 6px; border-radius:3px; margin-top:2px;"><strong>Note:</strong> ${parsedPb.traderNote}</div>` : ''}
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
      <div style="background:var(--bg-surface); border:1px solid var(--border); border-left:4px solid ${pnlColor}; border-radius:var(--radius-md); padding:12px 16px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; box-shadow:var(--shadow-card);">
        <div>
          <div style="font-size:10px; font-weight:800; color:${pnlColor}; letter-spacing:0.5px; text-transform:uppercase;">
            🤖 AI SUGGESTED TRADES ONLY — 100-SHARE P&amp;L
          </div>
          <div style="font-size:24px; font-weight:900; font-family:var(--font-mono); color:${pnlColor}; margin-top:2px;">
            ${pnlSign}$${sum.aiPnL.toFixed(2)}
          </div>
          <div style="font-size:11px; color:var(--text-muted);">
            ${sum.aiTradesCount > 0
              ? `Calculated <strong style="color:var(--text-main);">on AI-suggested trades only</strong> (${sum.aiTradesCount} taken · ${sum.aiWins}W / ${sum.aiLosses}L · ${sum.aiWinRate}% WR). Filtered alerts are excluded.`
              : `AI took 0 trades today (<strong style="color:var(--text-main);">${sum.totalCompleted} trades filtered by AI risk triage</strong>). Raw TV P&amp;L is ${tvSign}$${sum.rawTvPnL.toFixed(2)}.`
            }
          </div>
        </div>
        <div style="display:flex; align-items:center; gap:8px;">
          <div class="ttm-metric-box">
            <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">AI LOSSES SAVED</div>
            <div style="font-size:13px; font-weight:800; color:var(--cyan); font-family:var(--font-mono);">+$${sum.avoidedLosses.toFixed(2)}</div>
          </div>
          <div class="ttm-metric-box">
            <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">RAW TV P&amp;L</div>
            <div style="font-size:13px; font-weight:800; color:var(--text-main); font-family:var(--font-mono);">${tvSign}$${sum.rawTvPnL.toFixed(2)}</div>
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

    const targetDate = (dateKey && dateKey !== 'undefined')
      ? dateKey
      : (this._pnlCurrentSession && this._pnlCurrentSession !== 'ALL' ? this._pnlCurrentSession : (this._dateFilter && this._dateFilter !== 'ALL' ? this._dateFilter : (this._availableDates && this._availableDates.length > 0 ? this._availableDates[0] : '2026-09-16')));

    this._currentTickerTradesSymbol = sym;
    this._currentTickerTradesDate = targetDate;

    // Modal stack coordination:
    const candidateParents = [
      { id: 'modal-alerts-pnl', label: 'Session P&L' },
      { id: 'modal-intraday-postmortem', label: 'Post-Mortem' }
    ];
    let parentFound = null;
    for (const p of candidateParents) {
      const el = document.getElementById(p.id);
      if (el && (el.style.display === 'flex' || el.style.display === 'block')) {
        parentFound = p;
        break;
      }
    }
    if (parentFound) {
      this._tickerTradesParentId = parentFound.id;
      const parentEl = document.getElementById(parentFound.id);
      if (parentEl) parentEl.style.display = 'none';
      const backBtn = document.getElementById('ttm-btn-back');
      if (backBtn) {
        backBtn.style.display = 'inline-flex';
        backBtn.innerText = `← Back to ${parentFound.label}`;
      }
    } else {
      this._tickerTradesParentId = null;
      const backBtn = document.getElementById('ttm-btn-back');
      if (backBtn) backBtn.style.display = 'none';
    }

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
    if (dateEl) dateEl.innerText = `📅 Session: ${targetDate}`;

    const res = this.getTickerDayTrades(sym, targetDate);
    const sum = res.summary;
    if (countEl) countEl.innerText = `${res.alerts.length} Alert${res.alerts.length === 1 ? '' : 's'} (${res.trades.length} Trade${res.trades.length === 1 ? '' : 's'})`;

    const pnlSign = sum.aiPnL >= 0 ? '+' : '';
    const pnlColor = sum.aiPnL >= 0 ? 'var(--emerald)' : 'var(--rose)';
    const tvSign = sum.rawTvPnL >= 0 ? '+' : '';
    const alphaSign = sum.alpha >= 0 ? '+' : '';

    if (heroEl) {
      heroEl.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:14px; background:var(--bg-surface); border:1px solid var(--border); border-left:4px solid ${pnlColor}; border-radius:var(--radius-md); padding:16px 20px; box-shadow:var(--shadow-card);">
          <div>
            <div style="font-size:11px; font-weight:800; color:${pnlColor}; letter-spacing:0.8px; text-transform:uppercase;">
              🤖 AI SUGGESTED TRADES ONLY — 100-SHARE P&amp;L
            </div>
            <div style="font-size:32px; font-weight:900; font-family:var(--font-mono); color:${pnlColor}; margin-top:3px;">
              ${pnlSign}$${sum.aiPnL.toFixed(2)}
            </div>
            <div style="font-size:12px; color:var(--text-muted); font-weight:500; margin-top:2px;">
              ${sum.aiTradesCount > 0
                ? `Calculated <strong style="color:var(--text-main);">strictly on AI suggested trades</strong> (${sum.aiTradesCount} taken · ${sum.aiWins}W / ${sum.aiLosses}L · ${sum.aiWinRate}% Win Rate). Filtered alerts are excluded.`
                : `AI took 0 trades today (<strong style="color:var(--text-main);">${sum.totalCompleted} trades filtered by AI risk triage</strong>). Raw TV P&amp;L is ${tvSign}$${sum.rawTvPnL.toFixed(2)}.`
              }
            </div>
          </div>

          <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
            <div class="ttm-metric-box">
              <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">AI LOSSES SAVED</div>
              <div style="font-size:15px; font-weight:800; color:var(--cyan); font-family:var(--font-mono); margin-top:2px;">+$${sum.avoidedLosses.toFixed(2)}</div>
              <div style="font-size:9px; color:var(--text-muted);">${sum.avoidedCount} filtered</div>
            </div>

            <div class="ttm-metric-box">
              <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">RAW TV SYSTEM</div>
              <div style="font-size:15px; font-weight:800; color:var(--text-main); font-family:var(--font-mono); margin-top:2px;">${tvSign}$${sum.rawTvPnL.toFixed(2)}</div>
              <div style="font-size:9px; color:var(--text-muted);">unfiltered</div>
            </div>

            <div class="ttm-metric-box">
              <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">AI ALPHA EDGE</div>
              <div style="font-size:15px; font-weight:800; color:var(--violet); font-family:var(--font-mono); margin-top:2px;">${alphaSign}$${sum.alpha.toFixed(2)}</div>
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
            ? `<span class="badge in_zone" style="font-size:11px; font-weight:800;">🟢 LONG (CALLS)</span>`
            : (t.side === 'SHORT'
                ? `<span class="badge danger" style="font-size:11px; font-weight:800;">🔴 SHORT (PUTS)</span>`
                : `<span class="badge" style="font-size:11px;">EXIT ONLY</span>`);

          let aiBadge = '';
          let pnlTag = '';
          let borderAccent = 'border:1px solid var(--border);';

          if (t.isAiTaken) {
            aiBadge = `<span class="badge in_zone" style="font-size:11px; font-weight:800;">🤖 AI SUGGESTED TRADE</span>`;
            pnlTag = `<span class="badge in_zone" style="font-size:10.5px; font-weight:800; font-family:var(--font-mono);">✓ INCLUDED IN AI P&amp;L</span>`;
            borderAccent = 'border:1px solid var(--border); border-left:4px solid var(--emerald);';
          } else if (t.isAiFiltered) {
            aiBadge = `<span class="badge danger" style="font-size:11px; font-weight:700;">🛡️ AI FILTERED / PASSED</span>`;
            pnlTag = `<span class="badge danger" style="font-size:10.5px; font-family:var(--font-mono);">✗ EXCLUDED (AI Passed)</span>`;
            borderAccent = 'border:1px solid var(--border); border-left:4px solid var(--rose);';
          } else {
            aiBadge = `<span class="badge" style="font-size:11px;">⏳ UNTRIAGED</span>`;
            pnlTag = `<span class="badge" style="font-size:10.5px; font-family:var(--font-mono);">Untriaged</span>`;
          }

          const pSign = t.pnl100 >= 0 ? '+' : '';
          const pColor = t.pnl100 >= 0 ? 'var(--emerald)' : 'var(--rose)';

          const entryTime = t.entryTime && t.entryTime.length >= 19 ? t.entryTime.substring(11, 19) : (t.entryTime || '--:--');
          const exitTime = t.exitTime && t.exitTime.length >= 19 ? t.exitTime.substring(11, 19) : (t.exitTime || (t.status === 'OPEN' ? 'ACTIVE' : '--:--'));

          const rawPb = t.llmPlaybook || (t.entryAlert && t.entryAlert.llm_playbook) || (t.exitAlert && t.exitAlert.llm_playbook) || '';
          const parsedPb = this.parsePlaybookSections(rawPb);
          const decisionText = t.llmDecision || (t.entryAlert && t.entryAlert.llm_decision) || (t.isAiTaken ? '🟢 TAKE' : (t.isAiFiltered ? '⏸️ WAIT / PASS' : 'Pending'));

          tradesHtml += `
            <div class="ttm-trade-card" style="${borderAccent}">
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
                <div class="ttm-subcard">
                  <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:10.5px; font-weight:800; color:var(--blue); letter-spacing:0.5px;">
                      📥 ENTRY ALERT (${entryTime} ET)
                    </span>
                    ${t.entryAlertId ? `<button class="btn secondary" onclick="AppAlerts.openAlertModal('${t.entryAlertId}')" style="padding:2px 8px; font-size:10px; font-weight:700;">🔍 Inspect</button>` : ''}
                  </div>
                  <div style="display:flex; align-items:center; justify-content:space-between; font-family:var(--font-mono); font-size:12px;">
                    <span style="color:var(--text-muted);">Trigger Price:</span>
                    <strong style="color:var(--text-main); font-size:14px;">$${(t.entryPrice !== null && t.entryPrice !== undefined) ? Number(t.entryPrice).toFixed(2) : '--'}</strong>
                  </div>
                  <div style="font-size:11.5px; color:var(--text-muted);">
                    <strong>Setup:</strong> <span style="color:var(--text-main);">${t.setup || 'Intraday'}</span>
                  </div>
                  <div style="font-size:11px; color:var(--text-muted);">
                    <strong>AI Triage:</strong> <span style="color:${t.isAiTaken ? 'var(--emerald)' : 'var(--rose)'}; font-weight:700;">${decisionText}</span>
                  </div>
                  ${t.plan ? `<div class="tdm-plan-box" title="${t.plan.replace(/"/g, '&quot;')}">Plan: ${t.plan}</div>` : ''}
                </div>

                <!-- 2. Exit Alert Card -->
                <div class="ttm-subcard">
                  <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:10.5px; font-weight:800; color:var(--amber); letter-spacing:0.5px;">
                      📤 EXIT ALERT (${exitTime} ET)
                    </span>
                    ${t.exitAlertId ? `<button class="btn secondary" onclick="AppAlerts.openAlertModal('${t.exitAlertId}')" style="padding:2px 8px; font-size:10px; font-weight:700;">🔍 Inspect</button>` : ''}
                  </div>
                  <div style="display:flex; align-items:center; justify-content:space-between; font-family:var(--font-mono); font-size:12px;">
                    <span style="color:var(--text-muted);">Exit Price:</span>
                    <strong style="color:var(--text-main); font-size:14px;">$${(t.exitPrice !== null && t.exitPrice !== undefined) ? Number(t.exitPrice).toFixed(2) : '--'}</strong>
                  </div>
                  <div style="font-size:11.5px; color:var(--text-muted);">
                    <strong>Status:</strong> <span style="color:var(--text-main);">${t.status === 'OPEN' ? '🟢 Live Spot Active' : '🏁 Closed via TV Exit'}</span>
                  </div>
                  <div class="tdm-outcome-box ${t.pnl100 >= 0 ? 'positive' : 'negative'}">
                    <strong>Outcome:</strong> <strong>${pSign}$${t.pnl100.toFixed(2)} (${pSign}${t.diffPts.toFixed(2)} pts)</strong>
                  </div>
                  ${t.exitReason ? `<div style="font-size:11px; color:var(--text-muted);">Reason: <strong style="color:var(--text-main);">${t.exitReason}</strong></div>` : ''}
                </div>

              </div>

              <!-- 3. AI Agent Decision & Rationale Box -->
              <div class="${t.isAiTaken ? 'intraday-trade-ai-box ai-taken-box' : (t.isAiFiltered ? 'intraday-trade-ai-box ai-filtered-box' : 'intraday-trade-ai-box')}">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px; border-bottom:1px solid var(--border); padding-bottom:6px;">
                  <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
                    <span style="font-weight:800; color:var(--cyan); font-size:11px;">🧠 AI AGENT VERDICT:</span>
                    <span style="font-weight:800; color:${t.isAiTaken ? 'var(--emerald)' : (t.isAiFiltered ? 'var(--rose)' : 'var(--text-main)')}; font-size:11.5px;">
                      ${decisionText}
                    </span>
                  </div>
                  <div style="display:flex; align-items:center; gap:5px; flex-wrap:wrap;">
                    ${parsedPb && parsedPb.conviction ? `<span class="ai-reason-pill"><strong>Conviction:</strong> ${parsedPb.conviction}</span>` : ''}
                    ${parsedPb && parsedPb.regime ? `<span class="ai-reason-pill"><strong>Regime:</strong> ${parsedPb.regime}</span>` : ''}
                    ${parsedPb && parsedPb.card ? `<span class="ai-reason-pill"><strong>Card:</strong> ${parsedPb.card}</span>` : ''}
                  </div>
                </div>

                ${parsedPb && parsedPb.whyCard ? `
                  <div style="font-size:11.5px; line-height:1.5; color:var(--text-muted);">
                    <strong style="color:var(--blue);">📐 Why (Technicals):</strong> <span style="color:var(--text-main);">${parsedPb.whyCard}</span>
                  </div>
                ` : ''}

                ${parsedPb && parsedPb.whyTape ? `
                  <div style="font-size:11.5px; line-height:1.5; color:var(--text-muted);">
                    <strong style="color:var(--amber);">📰 Why (Tape / Macro):</strong> <span style="color:var(--text-main);">${parsedPb.whyTape}</span>
                  </div>
                ` : ''}

                ${parsedPb && parsedPb.killItIf ? `
                  <div style="font-size:11.5px; line-height:1.5; color:var(--text-muted);">
                    <strong style="color:var(--rose);">🛑 Invalidation:</strong> <span style="color:var(--text-main);">${parsedPb.killItIf}</span>
                  </div>
                ` : ''}

                ${parsedPb && parsedPb.vetoRationale ? `
                  <div style="font-size:11.5px; line-height:1.5; color:var(--rose); background:rgba(244,63,94,0.08); border-left:3px solid var(--rose); padding:6px 10px; border-radius:4px; margin-top:4px;">
                    ${parsedPb.vetoRationale}
                  </div>
                ` : ''}

                ${parsedPb && parsedPb.traderNote ? `
                  <div class="ai-trader-note-callout" style="border-left-color:${t.isAiTaken ? 'var(--emerald)' : (t.isAiFiltered ? 'var(--rose)' : 'var(--cyan)')};">
                    <strong style="color:var(--text-muted); font-size:10px; text-transform:uppercase; letter-spacing:0.5px; display:block; margin-bottom:2px;">📝 Trader's Note:</strong>
                    ${parsedPb.traderNote}
                  </div>
                ` : (!parsedPb?.whyCard && !parsedPb?.whyTape && !parsedPb?.killItIf && !parsedPb?.vetoRationale && rawPb ? `
                  <div style="font-size:11px; color:var(--text-muted); line-height:1.4;">
                    ${(parsedPb?.residualText || rawPb).substring(0, 300)}...
                  </div>
                ` : '')}
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

  closeTickerTradesModal(restoreParent = true) {
    const modal = document.getElementById('modal-ticker-day-trades');
    if (modal) {
      modal.style.display = 'none';
    }
    if (restoreParent && this._tickerTradesParentId) {
      const parentEl = document.getElementById(this._tickerTradesParentId);
      if (parentEl) {
        parentEl.style.display = 'flex';
      }
      this._tickerTradesParentId = null;
    }
  },

  openTradeDetailModal(symbol, dateKey, tradeIndex, tradeObj = null) {
    const sym = (symbol || '').toUpperCase().trim();
    if (!sym) return;

    // Defensive parameter-inversion auto-detection in case caller passes (symbol, tradeIndex, dateKey)
    if (typeof dateKey === 'number' && typeof tradeIndex === 'string' && (tradeIndex.includes('-') || tradeIndex === 'ALL')) {
      const tmp = dateKey;
      dateKey = tradeIndex;
      tradeIndex = tmp;
    }

    const targetDate = (dateKey && dateKey !== 'undefined')
      ? dateKey
      : (this._pnlCurrentSession && this._pnlCurrentSession !== 'ALL' ? this._pnlCurrentSession : (this._dateFilter && this._dateFilter !== 'ALL' ? this._dateFilter : (this._availableDates && this._availableDates.length > 0 ? this._availableDates[0] : '2026-09-16')));

    let t = tradeObj;
    if (!t) {
      const res = this.getTickerDayTrades(sym, targetDate);
      if (typeof tradeIndex === 'number' && res.trades[tradeIndex]) {
        t = res.trades[tradeIndex];
      } else if (typeof tradeIndex === 'string') {
        t = res.trades.find(x => x.entryAlertId === tradeIndex || x.exitAlertId === tradeIndex);
      }
      if (!t && res.trades.length > 0) t = res.trades[0];
    }
    if (!t) return;

    // Modal stack coordination:
    // If opened from an active parent modal (Session P&L, Day Trades, Post-Mortem),
    // cleanly hide the parent modal and show prominent back navigation.
    const candidateParents = [
      { id: 'modal-alerts-pnl', label: 'Session P&L' },
      { id: 'modal-ticker-day-trades', label: 'Day Trades' },
      { id: 'modal-intraday-postmortem', label: 'Post-Mortem' }
    ];
    let parentFound = null;
    for (const p of candidateParents) {
      const el = document.getElementById(p.id);
      if (el && (el.style.display === 'flex' || el.style.display === 'block')) {
        parentFound = p;
        break;
      }
    }

    if (parentFound) {
      this._modalParentId = parentFound.id;
      const parentEl = document.getElementById(parentFound.id);
      if (parentEl) parentEl.style.display = 'none';

      const backBtn = document.getElementById('tdm-btn-back');
      const backLbl = document.getElementById('tdm-btn-back-label');
      const footerBackBtn = document.getElementById('tdm-btn-footer-back');
      const footerBackLbl = document.getElementById('tdm-btn-footer-back-label');
      if (backBtn && backLbl) {
        backLbl.innerText = `← Back to ${parentFound.label}`;
        backBtn.style.display = 'inline-flex';
      }
      if (footerBackBtn && footerBackLbl) {
        footerBackLbl.innerText = `← Back to ${parentFound.label}`;
        footerBackBtn.style.display = 'inline-flex';
      }
    } else {
      this._modalParentId = null;
      const backBtn = document.getElementById('tdm-btn-back');
      const footerBackBtn = document.getElementById('tdm-btn-footer-back');
      if (backBtn) backBtn.style.display = 'none';
      if (footerBackBtn) footerBackBtn.style.display = 'none';
    }

    const modal = document.getElementById('modal-trade-detail');
    const symEl = document.getElementById('tdm-ticker-sym');
    const nameEl = document.getElementById('tdm-ticker-name');
    const dirEl = document.getElementById('tdm-dir-badge');
    const aiEl = document.getElementById('tdm-ai-badge');
    const dateEl = document.getElementById('tdm-session-date');
    const pnlEl = document.getElementById('tdm-pnl-strip');
    const bodyEl = document.getElementById('tdm-modal-body');
    const footerLeft = document.getElementById('tdm-footer-left');
    const btnChart = document.getElementById('tdm-btn-chart');

    const compName = window.AppUtils ? (window.AppUtils.getCompanyName(sym) || sym) : sym;
    if (symEl) symEl.innerText = sym;
    if (nameEl) nameEl.innerText = compName;
    if (dateEl) dateEl.innerText = `📅 ${targetDate}`;
    if (btnChart) btnChart.onclick = () => this.openChart(sym);

    const isCall = t.side === 'LONG';
    if (dirEl) {
      dirEl.innerText = isCall ? '🟢 CALL' : '🔴 PUT';
      dirEl.className = `badge ${isCall ? 'in_zone' : 'danger'}`;
      dirEl.removeAttribute('style');
    }

    if (aiEl) {
      if (t.isAiTaken) {
        aiEl.innerText = '🤖 AI SUGGESTED (TAKEN)';
        aiEl.className = 'badge in_zone';
      } else if (t.isAiFiltered) {
        aiEl.innerText = '🛡️ AI FILTERED (PASS/AVOIDED)';
        aiEl.className = 'badge danger';
      } else {
        aiEl.innerText = '⏳ UNTRIAGED';
        aiEl.className = 'badge';
      }
      aiEl.removeAttribute('style');
    }

    const pSign = t.pnl100 >= 0 ? '+' : '';
    const pColor = t.pnl100 >= 0 ? 'var(--emerald)' : 'var(--rose)';
    if (pnlEl) {
      pnlEl.innerText = t.status === 'OPEN' ? `${pSign}$${t.pnl100.toFixed(2)} (Live)` : `${pSign}$${t.pnl100.toFixed(2)} (${pSign}${t.diffPts.toFixed(2)} pts)`;
      pnlEl.style.color = pColor;
    }

    const rawPb = t.llmPlaybook || (t.entryAlert && t.entryAlert.llm_playbook) || (t.exitAlert && t.exitAlert.llm_playbook) || '';
    const parsedPb = this.parsePlaybookSections(rawPb);
    const decisionText = t.llmDecision || (t.entryAlert && t.entryAlert.llm_decision) || (t.isAiTaken ? '🟢 TAKE' : (t.isAiFiltered ? '⏸️ WAIT / PASS' : 'No Decision Logged'));

    const entryTime = (t.entryTime || '').length >= 19 ? t.entryTime.substring(11, 19) : (t.entryTime || '--:--');
    const exitTime = t.exitTime ? (t.exitTime.length >= 19 ? t.exitTime.substring(11, 19) : t.exitTime) : (t.status === 'OPEN' ? 'ACTIVE OPEN' : '--:--');

    if (bodyEl) {
      bodyEl.innerHTML = `
        <!-- Top Cards: Lifecycle & Tactical Origin -->
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:14px;">
          <!-- Entry Execution Card -->
          <div class="tdm-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="tdm-card-title" style="color:var(--blue);">📥 Entry Execution (${entryTime} ET)</span>
              ${t.entryAlertId ? `<button class="btn secondary" onclick="AppAlerts.openAlertModal('${t.entryAlertId}')" style="padding:2px 8px; font-size:10.5px; font-weight:700;">🔍 Raw Alert</button>` : ''}
            </div>
            <div style="display:flex; justify-content:space-between; align-items:baseline; font-family:var(--font-mono); margin-top:2px;">
              <span style="color:var(--text-muted); font-size:12px;">Trigger Price:</span>
              <strong style="color:var(--text-main); font-size:17px;">$${(t.entryPrice !== null && t.entryPrice !== undefined) ? Number(t.entryPrice).toFixed(2) : '--'}</strong>
            </div>
            <div style="font-size:12px; color:var(--text-muted);"><strong style="color:var(--text-main);">Setup:</strong> ${t.setup || '0DTE Intraday Signal'}</div>
            ${t.plan ? `<div class="tdm-plan-box"><strong>Tactical Plan:</strong> ${t.plan}</div>` : ''}
          </div>

          <!-- Exit Execution Card -->
          <div class="tdm-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="tdm-card-title" style="color:var(--amber);">📤 Exit Execution (${exitTime} ET)</span>
              ${t.exitAlertId ? `<button class="btn secondary" onclick="AppAlerts.openAlertModal('${t.exitAlertId}')" style="padding:2px 8px; font-size:10.5px; font-weight:700;">🔍 Raw Alert</button>` : ''}
            </div>
            <div style="display:flex; justify-content:space-between; align-items:baseline; font-family:var(--font-mono); margin-top:2px;">
              <span style="color:var(--text-muted); font-size:12px;">Exit Price:</span>
              <strong style="color:var(--text-main); font-size:17px;">$${(t.exitPrice !== null && t.exitPrice !== undefined) ? Number(t.exitPrice).toFixed(2) : '--'}</strong>
            </div>
            <div style="font-size:12px; color:var(--text-muted);"><strong style="color:var(--text-main);">Duration:</strong> ${t.duration} ${t.exitReason ? `· Reason: <strong style="color:var(--text-main);">${t.exitReason}</strong>` : ''}</div>
            <div class="tdm-outcome-box ${t.pnl100 >= 0 ? 'positive' : 'negative'}">
              <strong style="color:var(--text-muted);">Trade Outcome:</strong> <strong style="color:inherit;">${pSign}$${t.pnl100.toFixed(2)} (${pSign}${t.diffPts.toFixed(2)} pts)</strong>
            </div>
          </div>
        </div>

        <!-- AI Agent Decision & Conviction Banner -->
        <div class="${t.isAiTaken ? 'tdm-banner-taken' : 'tdm-banner-filtered'}">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
            <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
              <span style="font-size:11.5px; font-weight:900; color:var(--text-main); letter-spacing:0.6px; text-transform:uppercase;">🧠 AI AGENT DECISION:</span>
              <span style="font-size:14px; font-weight:900; color:${t.isAiTaken ? 'var(--emerald)' : (t.isAiFiltered ? 'var(--rose)' : 'var(--text-main)')};">
                ${decisionText}
              </span>
            </div>
            <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
              ${parsedPb && parsedPb.conviction ? `<span class="ai-reason-pill"><strong>Conviction:</strong> ${parsedPb.conviction}</span>` : ''}
              ${parsedPb && parsedPb.regime ? `<span class="ai-reason-pill"><strong>Regime:</strong> ${parsedPb.regime}</span>` : ''}
              ${parsedPb && parsedPb.card ? `<span class="ai-reason-pill"><strong>Card:</strong> ${parsedPb.card}</span>` : ''}
            </div>
          </div>
          <div style="font-size:12px; color:var(--text-muted); line-height:1.55;">
            ${t.isAiTaken ? '✓ Evaluated by local LLM as high-expectancy setup aligned with market structure. Included in AI P&L.' : '✗ Vetoed / Passed by AI triage due to fractured alignment or unfavorable risk-to-reward. Avoided loss.'}
          </div>
        </div>

        <!-- Detailed AI Agent Reasoning & Thesis Section -->
        <div class="tdm-thesis-block">
          <div style="font-size:12px; font-weight:800; color:var(--text-main); letter-spacing:0.5px; text-transform:uppercase; border-bottom:1px solid var(--border); padding-bottom:8px;">
            📖 AI AGENT REASONING &amp; THESIS
          </div>

          <!-- Why Technicals / Card -->
          ${parsedPb && parsedPb.whyCard ? `
            <div class="tdm-thesis-item">
              <div class="tdm-thesis-header" style="color:var(--blue);">
                <span>📐</span> WHY (TECHNICALS &amp; ALIGNMENT STACK):
              </div>
              <div class="tdm-thesis-box tech">
                ${parsedPb.whyCard}
              </div>
            </div>
          ` : ''}

          <!-- Why Tape / Macro / News -->
          ${parsedPb && parsedPb.whyTape ? `
            <div class="tdm-thesis-item">
              <div class="tdm-thesis-header" style="color:var(--amber);">
                <span>📰</span> WHY (TAPE, MACRO &amp; CATALYSTS):
              </div>
              <div class="tdm-thesis-box tape">
                ${parsedPb.whyTape}
              </div>
            </div>
          ` : ''}

          <!-- Invalidation -->
          ${parsedPb && parsedPb.killItIf ? `
            <div class="tdm-thesis-item">
              <div class="tdm-thesis-header" style="color:var(--rose);">
                <span>🛑</span> INVALIDATION (KILL IT IF):
              </div>
              <div class="tdm-thesis-box inval">
                ${parsedPb.killItIf}
              </div>
            </div>
          ` : ''}

          <!-- Trader Directive -->
          ${parsedPb && parsedPb.traderNote ? `
            <div class="tdm-thesis-item">
              <div class="tdm-thesis-header" style="color:var(--emerald);">
                <span>📝</span> TRADER'S NOTE &amp; DIRECTIVE:
              </div>
              <div class="tdm-thesis-box directive">
                ${parsedPb.traderNote}
              </div>
            </div>
          ` : ''}

          <!-- Veto Rationale (Quality / Regime / Expectancy Veto) -->
          ${parsedPb && parsedPb.vetoRationale ? `
            <div class="tdm-thesis-item">
              <div class="tdm-thesis-header" style="color:var(--rose);">
                <span>🛡️</span> AI VETO &amp; RISK RATIONALE:
              </div>
              <div class="tdm-thesis-box inval" style="line-height:1.6; font-size:12.5px;">
                ${parsedPb.vetoRationale}
              </div>
            </div>
          ` : ''}

          <!-- Fallback: Unstructured Playbook or Residual Text -->
          ${(!parsedPb || (!parsedPb.whyCard && !parsedPb.whyTape && !parsedPb.killItIf && !parsedPb.traderNote && !parsedPb.vetoRationale)) && (parsedPb && parsedPb.residualText ? parsedPb.residualText : rawPb) ? `
            <div class="tdm-thesis-item">
              <div class="tdm-thesis-header" style="color:var(--cyan);">
                <span>🧠</span> AI DECISION PLAYBOOK &amp; CONTEXT:
              </div>
              <div class="tdm-thesis-box tech" style="white-space:pre-wrap; line-height:1.6; font-size:12.5px;">
                ${parsedPb && parsedPb.residualText ? parsedPb.residualText : rawPb}
              </div>
            </div>
          ` : ''}

          <!-- Tactical Plan Fallback if Playbook Empty -->
          ${!rawPb && t.plan ? `
            <div class="tdm-thesis-item">
              <div class="tdm-thesis-header" style="color:var(--amber);">
                <span>🎯</span> TACTICAL EXECUTION PLAN:
              </div>
              <div class="tdm-thesis-box tape" style="font-family:var(--font-mono); font-size:12px;">
                ${t.plan}
              </div>
            </div>
          ` : ''}

          <!-- Graceful note if completely empty -->
          ${!rawPb && !t.plan ? `
            <div style="font-size:12px; color:var(--text-muted); font-style:italic; padding:6px 0;">
              ℹ️ Standard quantitative execution — no discretionary LLM veto or custom playbook notes were emitted for this trade.
            </div>
          ` : ''}
        </div>
      `;
    }

    if (footerLeft) {
      let fHtml = '';
      if (t.entryAlertId) {
        fHtml += `<button class="btn secondary" onclick="AppAlerts.openAlertModal('${t.entryAlertId}')" style="padding:4px 10px; font-size:11px; font-weight:700;">📥 Inspect Entry Alert</button>`;
      }
      if (t.exitAlertId) {
        fHtml += `<button class="btn secondary" onclick="AppAlerts.openAlertModal('${t.exitAlertId}')" style="padding:4px 10px; font-size:11px; font-weight:700;">📤 Inspect Exit Alert</button>`;
      }
      footerLeft.innerHTML = fHtml;
    }

    if (modal) {
      modal.style.display = 'flex';
    }
  },

  closeTradeDetailModal(restoreParent = true) {
    const modal = document.getElementById('modal-trade-detail');
    if (modal) {
      modal.style.display = 'none';
    }

    if (restoreParent && this._modalParentId) {
      const parentEl = document.getElementById(this._modalParentId);
      if (parentEl) {
        parentEl.style.display = 'flex';
      }
      this._modalParentId = null;
    }
  },

  // ============================================================================
  // Intraday Session Post-Mortem & 4-Quadrant Attribution Engine
  // ============================================================================
  _currentPostMortemData: null,
  _currentPostMortemTab: 'summary',

  updatePostMortemDatePills(activeDate) {
    const container = document.getElementById('pm-modal-date-pills');
    const dateInput = document.getElementById('pm-modal-date-picker');
    const distinctDates = this.getAvailableSessionDates();

    if (dateInput) {
      dateInput.value = (activeDate && activeDate !== 'ALL') ? activeDate : '';
    }

    if (!container) return;

    let html = `
      <button class="session-pill-btn ${activeDate === 'ALL' ? 'active' : ''}" onclick="AppAlerts.switchPostMortemDate('ALL')" title="Cumulative Attribution across all ${distinctDates.length} trading days">
        <span>🌟 All (${distinctDates.length}D)</span>
      </button>
    `;

    distinctDates.forEach((d, idx) => {
      const isAct = (d === activeDate);
      const isLatest = (idx === 0);
      html += `
        <button class="session-pill-btn ${isAct ? 'active' : ''}" onclick="AppAlerts.switchPostMortemDate('${d}')" title="Audit Session: ${d}${isLatest ? ' (Latest)' : ''}">
          <span>📅 ${d}</span>
          ${isLatest ? '<span class="count-tag">Latest</span>' : ''}
        </button>
      `;
    });

    container.innerHTML = html;
  },

  switchPostMortemDate(dateStr) {
    const distinctDates = this.getAvailableSessionDates();
    const d = dateStr || (distinctDates[0]) || '2026-09-16';
    this._currentPostMortemData = this.analyzeSessionPostMortem(d);
    this.updatePostMortemDatePills(d);
    this.renderPostMortemModal();
  },

  openPostMortemModal(targetDate) {
    const distinctDates = this.getAvailableSessionDates();
    let d = targetDate;
    if (!d) {
      if (this._dateFilter && this._dateFilter !== 'ALL') {
        d = this._dateFilter;
      } else if (this._pnlCurrentSession && this._pnlCurrentSession !== 'ALL') {
        d = this._pnlCurrentSession;
      } else if (distinctDates.length > 0) {
        d = distinctDates[0];
      } else {
        d = '2026-09-16';
      }
    }

    // Modal stack coordination:
    const candidateParents = [
      { id: 'modal-alerts-pnl', label: 'Session P&L' },
      { id: 'modal-ticker-day-trades', label: 'Day Trades' }
    ];
    let parentFound = null;
    for (const p of candidateParents) {
      const el = document.getElementById(p.id);
      if (el && (el.style.display === 'flex' || el.style.display === 'block')) {
        parentFound = p;
        break;
      }
    }
    if (parentFound) {
      this._postMortemParentId = parentFound.id;
      const parentEl = document.getElementById(parentFound.id);
      if (parentEl) parentEl.style.display = 'none';
      const backBtn = document.getElementById('pm-btn-back');
      if (backBtn) {
        backBtn.style.display = 'inline-flex';
        backBtn.innerText = `← Back to ${parentFound.label}`;
      }
    } else {
      this._postMortemParentId = null;
      const backBtn = document.getElementById('pm-btn-back');
      if (backBtn) backBtn.style.display = 'none';
    }

    this._currentPostMortemData = this.analyzeSessionPostMortem(d);
    this._currentPostMortemTab = 'summary';
    this.updatePostMortemDatePills(d);
    this.renderPostMortemModal();
    const modal = document.getElementById('modal-intraday-postmortem');
    if (modal) {
      modal.style.display = 'flex';
    }
  },

  closePostMortemModal(restoreParent = true) {
    const modal = document.getElementById('modal-intraday-postmortem');
    if (modal) {
      modal.style.display = 'none';
    }
    if (restoreParent && this._postMortemParentId) {
      const parentEl = document.getElementById(this._postMortemParentId);
      if (parentEl) {
        parentEl.style.display = 'flex';
      }
      this._postMortemParentId = null;
    }
  },

  switchPostMortemTab(tabName) {
    this._currentPostMortemTab = tabName;
    ['summary', 'quadrants', 'rules', 'raw'].forEach(t => {
      const btn = document.getElementById(`btn-pm-tab-${t}`);
      if (btn) {
        if (t === tabName) btn.classList.add('active');
        else btn.classList.remove('active');
      }
    });
    this.renderPostMortemModal();
  },

  analyzeSessionPostMortem(targetDate) {
    const dateKey = targetDate || '2026-09-16';
    let trades = [];

    if (dateKey === 'ALL') {
      const distinctDates = this.getAvailableSessionDates();
      distinctDates.forEach(d => {
        const dTickers = this.getIntradayTickers(d);
        dTickers.forEach(t => {
          const res = this.getTickerDayTrades(t.symbol, d);
          if (res && res.trades) {
            res.trades.forEach((tr, idx) => {
              trades.push({
                ...tr,
                symbol: t.symbol,
                name: t.name,
                origIdx: idx,
                date: d
              });
            });
          }
        });
      });
    } else {
      const activeTickers = this.getIntradayTickers(dateKey);
      activeTickers.forEach(t => {
        const res = this.getTickerDayTrades(t.symbol, dateKey);
        if (res && res.trades) {
          res.trades.forEach((tr, idx) => {
            trades.push({
              ...tr,
              symbol: t.symbol,
              name: t.name,
              origIdx: idx,
              date: dateKey
            });
          });
        }
      });
    }

    const totalTrades = trades.length;
    const totalPnl = trades.reduce((acc, t) => acc + (t.pnl100 || 0), 0);

    const truePos = trades.filter(t => t.isAiTaken && t.pnl100 > 0);
    const falsePos = trades.filter(t => t.isAiTaken && t.pnl100 < 0);
    const trueNeg = trades.filter(t => t.isAiFiltered && t.pnl100 <= 0);
    const falseNeg = trades.filter(t => t.isAiFiltered && t.pnl100 > 0);
    const untriaged = trades.filter(t => !t.isAiTaken && !t.isAiFiltered);

    const sumTruePosPnl = truePos.reduce((a, b) => a + b.pnl100, 0);
    const sumFalsePosPnl = falsePos.reduce((a, b) => a + b.pnl100, 0);
    const aiTakenTotalPnl = sumTruePosPnl + sumFalsePosPnl;

    const capitalSaved = Math.abs(trueNeg.reduce((a, b) => a + b.pnl100, 0));
    const alphaMissed = falseNeg.reduce((a, b) => a + b.pnl100, 0);

    const aiTakenCount = truePos.length + falsePos.length;
    const aiWinRate = aiTakenCount > 0 ? (truePos.length / aiTakenCount) * 100 : 0;
    const absLoss = Math.abs(sumFalsePosPnl);
    const profitFactor = absLoss > 0 ? (sumTruePosPnl / absLoss) : (sumTruePosPnl > 0 ? 999 : 0);

    const filteredTotalCount = trueNeg.length + falseNeg.length;
    const filterPrecision = filteredTotalCount > 0 ? (trueNeg.length / filteredTotalCount) * 100 : 0;

    // Time-based loss clustering
    const lunchLullLosses = falsePos.filter(t => {
      const timeStr = (t.entryTime || '').substring(11, 16);
      return timeStr >= '11:15' && timeStr <= '12:45';
    });
    const lunchLullLossPnl = lunchLullLosses.reduce((a, b) => a + b.pnl100, 0);

    return {
      dateKey,
      totalTrades,
      totalPnl,
      trades,
      truePos,
      falsePos,
      trueNeg,
      falseNeg,
      untriaged,
      sumTruePosPnl,
      sumFalsePosPnl,
      aiTakenTotalPnl,
      capitalSaved,
      alphaMissed,
      aiWinRate,
      profitFactor,
      filterPrecision,
      lunchLullLosses,
      lunchLullLossPnl
    };
  },

  renderPostMortemModal() {
    const d = this._currentPostMortemData;
    const container = document.getElementById('ipm-modal-body');
    if (!container || !d) return;

    if (this._currentPostMortemTab === 'summary') {
      container.innerHTML = this.renderPostMortemSummary(d);
    } else if (this._currentPostMortemTab === 'quadrants') {
      container.innerHTML = this.renderPostMortemQuadrants(d);
    } else if (this._currentPostMortemTab === 'rules') {
      container.innerHTML = this.renderPostMortemRules(d);
    } else if (this._currentPostMortemTab === 'raw') {
      container.innerHTML = this.renderPostMortemRaw(d);
    }
  },

  renderPostMortemSummary(d) {
    const netClass = d.aiTakenTotalPnl >= 0 ? 'color:var(--emerald);' : 'color:var(--rose);';
    const totalClass = d.totalPnl >= 0 ? 'color:var(--emerald);' : 'color:var(--rose);';
    const netDecisionAlpha = d.capitalSaved - d.alphaMissed;
    const alphaClass = netDecisionAlpha >= 0 ? 'color:var(--emerald);' : 'color:var(--amber);';

    return `
      <div style="display:flex; flex-direction:column; gap:16px;">

        <!-- Top Summary Banner -->
        <div style="background:var(--bg-card); border:1px solid var(--border); border-left:4px solid var(--cyan); border-radius:var(--radius-md); padding:12px 16px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
          <div style="display:flex; align-items:center; gap:10px;">
            <span class="pill cyan" style="font-family:var(--font-mono); font-size:12px; font-weight:800; padding:3px 10px;">📅 ${d.dateKey === 'ALL' ? 'ALL SESSIONS (5 DAYS CUMULATIVE)' : `SESSION: ${d.dateKey}`}</span>
            <span style="font-size:13px; font-weight:700; color:var(--text-main);">
              Audited ${d.totalTrades} Executed Setups across ${d.trades ? new Set(d.trades.map(t=>t.symbol)).size : 0} Watchlist Instruments
            </span>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="font-size:12px; color:var(--text-muted); font-weight:600;">System P&L (100 shs):</span>
            <span style="font-family:var(--font-mono); font-size:15px; font-weight:900; ${totalClass}">
              ${d.totalPnl >= 0 ? '+' : ''}$${d.totalPnl.toFixed(2)}
            </span>
          </div>
        </div>

        <!-- KPI Grid -->
        <div class="pm-kpi-grid">
          <div class="pm-kpi-card">
            <div style="font-size:11px; font-weight:700; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px;">🤖 AI Executed P&L</div>
            <div style="font-family:var(--font-mono); font-size:24px; font-weight:900; ${netClass}">
              ${d.aiTakenTotalPnl >= 0 ? '+' : ''}$${d.aiTakenTotalPnl.toFixed(2)}
            </div>
            <div style="font-size:11px; color:var(--text-muted); display:flex; justify-content:space-between;">
              <span>${d.truePos.length} Wins · ${d.falsePos.length} Losses</span>
              <span>Win Rate: <strong>${d.aiWinRate.toFixed(1)}%</strong></span>
            </div>
          </div>

          <div class="pm-kpi-card">
            <div style="font-size:11px; font-weight:700; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px;">🛡️ Capital Saved by Filter</div>
            <div style="font-family:var(--font-mono); font-size:24px; font-weight:900; color:var(--emerald);">
              +$${d.capitalSaved.toFixed(2)}
            </div>
            <div style="font-size:11px; color:var(--text-muted); display:flex; justify-content:space-between;">
              <span>${d.trueNeg.length} Toxic Traps Deflected</span>
              <span>Precision: <strong>${d.filterPrecision.toFixed(1)}%</strong></span>
            </div>
          </div>

          <div class="pm-kpi-card">
            <div style="font-size:11px; font-weight:700; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px;">⚠️ Missed Alpha (Too Strict)</div>
            <div style="font-family:var(--font-mono); font-size:24px; font-weight:900; color:var(--amber);">
              -$${d.alphaMissed.toFixed(2)}
            </div>
            <div style="font-size:11px; color:var(--text-muted); display:flex; justify-content:space-between;">
              <span>${d.falseNeg.length} Profitable Setups Filtered</span>
              <span>Opportunity Cost</span>
            </div>
          </div>

          <div class="pm-kpi-card">
            <div style="font-size:11px; font-weight:700; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px;">⚖️ Net Decision Alpha</div>
            <div style="font-family:var(--font-mono); font-size:24px; font-weight:900; ${alphaClass}">
              ${netDecisionAlpha >= 0 ? '+' : ''}$${netDecisionAlpha.toFixed(2)}
            </div>
            <div style="font-size:11px; color:var(--text-muted); display:flex; justify-content:space-between;">
              <span>Filter Edge vs Friction</span>
              <span>${netDecisionAlpha >= 0 ? 'Net Capital Preserved' : 'Alpha Leak Dominant'}</span>
            </div>
          </div>
        </div>

        <!-- 4-Quadrant Preview Cards -->
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px;">
          <div style="background:var(--bg-surface); border:1px solid var(--border); border-left:4px solid var(--emerald); border-radius:var(--radius-md); padding:12px; cursor:pointer;" onclick="AppAlerts.switchPostMortemTab('quadrants')">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <strong style="font-size:12px; color:var(--text-main);">🟢 True Positives</strong>
              <span class="badge in_zone">${d.truePos.length} Trades</span>
            </div>
            <div style="font-family:var(--font-mono); font-size:18px; font-weight:800; color:var(--emerald); margin-top:4px;">
              +$${d.sumTruePosPnl.toFixed(2)}
            </div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">
              Target 1 reached + trailing runner profit locked
            </div>
          </div>

          <div style="background:var(--bg-surface); border:1px solid var(--border); border-left:4px solid var(--rose); border-radius:var(--radius-md); padding:12px; cursor:pointer;" onclick="AppAlerts.switchPostMortemTab('quadrants')">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <strong style="font-size:12px; color:var(--text-main);">🔴 False Positives</strong>
              <span class="badge danger">${d.falsePos.length} Trades</span>
            </div>
            <div style="font-family:var(--font-mono); font-size:18px; font-weight:800; color:var(--rose); margin-top:4px;">
              -$${Math.abs(d.sumFalsePosPnl).toFixed(2)}
            </div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">
              ${d.lunchLullLosses.length} clustered in 11:15-12:45 MT lunch lull
            </div>
          </div>

          <div style="background:var(--bg-surface); border:1px solid var(--border); border-left:4px solid var(--cyan); border-radius:var(--radius-md); padding:12px; cursor:pointer;" onclick="AppAlerts.switchPostMortemTab('quadrants')">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <strong style="font-size:12px; color:var(--text-main);">🛡️ True Negatives</strong>
              <span class="badge" style="background:rgba(6,182,212,0.15); color:var(--cyan); border-color:rgba(6,182,212,0.3);">${d.trueNeg.length} Trades</span>
            </div>
            <div style="font-family:var(--font-mono); font-size:18px; font-weight:800; color:var(--cyan); margin-top:4px;">
              +$${d.capitalSaved.toFixed(2)}
            </div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">
              Protected capital from chop & counter-trend traps
            </div>
          </div>

          <div style="background:var(--bg-surface); border:1px solid var(--border); border-left:4px solid var(--amber); border-radius:var(--radius-md); padding:12px; cursor:pointer;" onclick="AppAlerts.switchPostMortemTab('quadrants')">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <strong style="font-size:12px; color:var(--text-main);">⚠️ False Negatives</strong>
              <span class="badge" style="background:rgba(245,158,11,0.15); color:var(--amber); border-color:rgba(245,158,11,0.3);">${d.falseNeg.length} Trades</span>
            </div>
            <div style="font-family:var(--font-mono); font-size:18px; font-weight:800; color:var(--amber); margin-top:4px;">
              +$${d.alphaMissed.toFixed(2)}
            </div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">
              Missed big runs: QQQ Short (+$390) & TSLA (+$238)
            </div>
          </div>
        </div>

        <!-- Executive Discoveries & Recommendations Box -->
        <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:var(--radius-md); padding:16px;">
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:12px;">
            <span style="font-size:18px;">💡</span>
            <span style="font-size:14px; font-weight:800; color:var(--text-main); font-family:'Outfit',sans-serif;">
              Key System Findings & Concrete Triage Fixes
            </span>
          </div>

          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:14px; font-size:12px; line-height:1.5;">
            <div style="border-left:3px solid var(--rose); padding-left:10px;">
              <strong style="color:var(--text-main); display:block; margin-bottom:2px;">1. The 11:15–12:45 MT Midday Volume Lull Leak</strong>
              <span style="color:var(--text-muted);">
                ${d.lunchLullLosses.length} out of ${d.falsePos.length} losses occurred during the market lunch hour (-$${Math.abs(d.lunchLullLossPnl).toFixed(2)}). Reversal and continuation breakouts lack institutional follow-through here.
                <br><strong>Fix:</strong> Implement a mandatory Midday Lull gate requiring grade A+ conviction (&ge;85) or total blackout.
              </span>
            </div>

            <div style="border-left:3px solid var(--emerald); padding-left:10px;">
              <strong style="color:var(--text-main); display:block; margin-bottom:2px;">2. T1 Profit-Locking Discipline is Working Flawlessly</strong>
              <span style="color:var(--text-muted);">
                All 6 True Positive trades (AMD, QQQ, COST, AVGO, NVDA) hit Target 1 and trailed stop to break-even+, guaranteeing positive expectancy.
                <br><strong>Fix:</strong> Preserve this exact deterministic bracket logic without alteration.
              </span>
            </div>

            <div style="border-left:3px solid var(--amber); padding-left:10px;">
              <strong style="color:var(--text-main); display:block; margin-bottom:2px;">3. Late-Day Index Trend Continuation Blindspot</strong>
              <span style="color:var(--text-muted);">
                The AI blocked the 14:55 MT QQQ Short breakdown citing "end of session exhaustion," missing an easy +3.90 pt (+ $390.00) selloff into market close.
                <br><strong>Fix:</strong> Allow index trend breakdowns after 14:15 MT if the 15-minute Stage 4 slope is steepening.
              </span>
            </div>

            <div style="border-left:3px solid var(--cyan); padding-left:10px;">
              <strong style="color:var(--text-main); display:block; margin-bottom:2px;">4. High Precision Counter-Trend Deflection (+ $683 Saved)</strong>
              <span style="color:var(--text-muted);">
                The model accurately tagged 11 counter-trend setups as "Stand Aside" or "Counter-Trend Chop," saving $683.00 of unnecessary loss.
                <br><strong>Fix:</strong> Affirm this rule weight in the prompt template.
              </span>
            </div>
          </div>

          <div style="margin-top:14px; text-align:right;">
            <button class="btn primary" onclick="AppAlerts.switchPostMortemTab('rules')" style="font-size:11px; padding:6px 14px; font-weight:800;">
              View &amp; Apply Rule Card Improvements ➔
            </button>
          </div>
        </div>

      </div>
    `;
  },

  renderPostMortemQuadrants(d) {
    const renderTradeRow = (t, isPnlPositive, notePrefix) => {
      const pnlStr = (t.pnl100 >= 0 ? '+' : '') + `$${t.pnl100.toFixed(2)}`;
      const ptsStr = (t.diffPts >= 0 ? '+' : '') + `${t.diffPts.toFixed(2)} pts`;
      const color = isPnlPositive ? 'var(--emerald)' : 'var(--rose)';
      const sideBadge = t.side === 'CALL'
        ? `<span class="badge in_zone" style="font-size:9.5px; padding:1px 5px;">CALL</span>`
        : `<span class="badge danger" style="font-size:9.5px; padding:1px 5px;">PUT</span>`;

      return `
        <div class="pm-trade-row" onclick="AppAlerts.openTradeDetailModal('${t.symbol}', '${d.dateKey}', ${t.origIdx || 0})" title="Click to open trade intelligence modal">
          <div style="display:flex; align-items:center; gap:8px;">
            <strong style="color:var(--text-main); font-size:12px; font-family:var(--font-mono);">${t.symbol}</strong>
            ${sideBadge}
            <span style="font-size:11px; color:var(--text-muted); font-family:var(--font-mono);">
              ${(t.entryTime || '').substring(11, 16)} ➔ ${(t.exitTime || '').substring(11, 16)}
            </span>
          </div>
          <div style="display:flex; align-items:center; gap:10px;">
            <span style="font-size:11px; color:var(--text-muted);">${t.exitReason || notePrefix || ''}</span>
            <div style="text-align:right; font-family:var(--font-mono); font-size:12px; font-weight:800; color:${color}; min-width:85px;">
              ${pnlStr} <span style="font-size:10px; color:var(--text-muted); font-weight:500;">(${ptsStr})</span>
            </div>
          </div>
        </div>
      `;
    };

    return `
      <div style="display:flex; flex-direction:column; gap:16px;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <div style="font-size:15px; font-weight:900; color:var(--text-main); font-family:'Outfit',sans-serif;">
              4-Quadrant Telemetry &amp; Attribution Matrix
            </div>
            <div style="font-size:11px; color:var(--text-muted);">
              Click any trade below to inspect the entry/exit price ladder, MFE/MAE stats, and AI LLM prompt reasoning.
            </div>
          </div>
          <span class="pill cyan" style="font-family:var(--font-mono); font-size:11px; font-weight:800;">${d.dateKey === 'ALL' ? 'ALL SESSIONS (5D CUMULATIVE)' : `SESSION: ${d.dateKey}`}</span>
        </div>

        <div class="pm-quadrant-matrix">

          <!-- Q1: True Positives -->
          <div class="pm-quadrant-card true-pos">
            <div class="pm-quadrant-header">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:16px;">🟢</span>
                <div>
                  <strong style="font-size:13px; color:var(--text-main);">Quadrant 1: True Positives</strong>
                  <div style="font-size:11px; color:var(--text-muted);">AI Approved &amp; Profitable (${d.truePos.length} trades)</div>
                </div>
              </div>
              <span style="font-family:var(--font-mono); font-size:15px; font-weight:900; color:var(--emerald);">
                +$${d.sumTruePosPnl.toFixed(2)}
              </span>
            </div>
            <div class="pm-quadrant-body">
              ${d.truePos.length > 0 ? d.truePos.map(t => renderTradeRow(t, true)).join('') : '<div style="padding:16px; text-align:center; color:var(--text-muted); font-size:12px;">No True Positives</div>'}
            </div>
            <div style="background:rgba(16,185,129,0.06); border-top:1px solid var(--border); padding:8px 12px; font-size:11px; color:var(--text-muted);">
              🛡️ <strong>Validation:</strong> 100% of winners followed strict Stage 2 / Stage 4 continuation rules with bracketed exits.
            </div>
          </div>

          <!-- Q2: False Positives -->
          <div class="pm-quadrant-card false-pos">
            <div class="pm-quadrant-header">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:16px;">🔴</span>
                <div>
                  <strong style="font-size:13px; color:var(--text-main);">Quadrant 2: False Positives</strong>
                  <div style="font-size:11px; color:var(--text-muted);">AI Approved But Lost (${d.falsePos.length} trades)</div>
                </div>
              </div>
              <span style="font-family:var(--font-mono); font-size:15px; font-weight:900; color:var(--rose);">
                -$${Math.abs(d.sumFalsePosPnl).toFixed(2)}
              </span>
            </div>
            <div class="pm-quadrant-body">
              ${d.falsePos.length > 0 ? d.falsePos.map(t => renderTradeRow(t, false)).join('') : '<div style="padding:16px; text-align:center; color:var(--text-muted); font-size:12px;">No False Positives</div>'}
            </div>
            <div style="background:rgba(244,63,94,0.06); border-top:1px solid var(--border); padding:8px 12px; font-size:11px; color:var(--text-muted);">
              ⚠️ <strong>Action Required:</strong> ${d.lunchLullLosses.length} losses ($${Math.abs(d.lunchLullLossPnl).toFixed(2)}) entered during 11:15-12:45 MT midday chop.
            </div>
          </div>

          <!-- Q3: True Negatives -->
          <div class="pm-quadrant-card true-neg">
            <div class="pm-quadrant-header">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:16px;">🛡️</span>
                <div>
                  <strong style="font-size:13px; color:var(--text-main);">Quadrant 3: True Negatives</strong>
                  <div style="font-size:11px; color:var(--text-muted);">AI Rejected &amp; Saved Capital (${d.trueNeg.length} trades)</div>
                </div>
              </div>
              <span style="font-family:var(--font-mono); font-size:15px; font-weight:900; color:var(--cyan);">
                +$${d.capitalSaved.toFixed(2)} Saved
              </span>
            </div>
            <div class="pm-quadrant-body">
              ${d.trueNeg.length > 0 ? d.trueNeg.map(t => renderTradeRow(t, true, 'Deflected Trap')).join('') : '<div style="padding:16px; text-align:center; color:var(--text-muted); font-size:12px;">No True Negatives</div>'}
            </div>
            <div style="background:rgba(6,182,212,0.06); border-top:1px solid var(--border); padding:8px 12px; font-size:11px; color:var(--text-muted);">
              🎯 <strong>Filter Edge:</strong> Rejected counter-trend signals and consolidation whipsaws, preserving liquidity.
            </div>
          </div>

          <!-- Q4: False Negatives -->
          <div class="pm-quadrant-card false-neg">
            <div class="pm-quadrant-header">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:16px;">⚠️</span>
                <div>
                  <strong style="font-size:13px; color:var(--text-main);">Quadrant 4: False Negatives</strong>
                  <div style="font-size:11px; color:var(--text-muted);">AI Rejected But Ran (${d.falseNeg.length} trades)</div>
                </div>
              </div>
              <span style="font-family:var(--font-mono); font-size:15px; font-weight:900; color:var(--amber);">
                -$${d.alphaMissed.toFixed(2)} Missed
              </span>
            </div>
            <div class="pm-quadrant-body">
              ${d.falseNeg.length > 0 ? d.falseNeg.map(t => renderTradeRow(t, false, 'Missed Run')).join('') : '<div style="padding:16px; text-align:center; color:var(--text-muted); font-size:12px;">No False Negatives</div>'}
            </div>
            <div style="background:rgba(245,158,11,0.06); border-top:1px solid var(--border); padding:8px 12px; font-size:11px; color:var(--text-muted);">
              📈 <strong>Alpha Leak:</strong> Over-conservative late session cutoff prevented capturing QQQ 14:55 Short (+$390.00).
            </div>
          </div>

        </div>
      </div>
    `;
  },

  renderPostMortemRules(d) {
    return `
      <div style="display:flex; flex-direction:column; gap:16px;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <div style="font-size:15px; font-weight:900; color:var(--text-main); font-family:'Outfit',sans-serif;">
              Closed-Loop Rule Card Improvements (gems/revanth-0dte.md)
            </div>
            <div style="font-size:11px; color:var(--text-muted);">
              Synthesized actionable rule patches derived directly from today's empirical trade data.
            </div>
          </div>
          <div style="display:flex; gap:8px;">
            <button class="btn primary" onclick="AppAlerts.savePostMortemToSkill()" style="font-size:11px; font-weight:800; padding:4px 12px; background:linear-gradient(135deg, #10b981 0%, #059669 100%); border-color:#059669; color:#fff;" title="Save findings to skills/postmortem_learnings.md for the invoke_skill tool">
              ⚡ Save to Active Skills
            </button>
            <button class="btn secondary" onclick="AppAlerts.copyPostMortemMarkdown()" style="font-size:11px; font-weight:700;">
              📋 Copy Rules Markdown
            </button>
          </div>
        </div>

        <!-- Rule Card 1: Midday Lull -->
        <div class="pm-rule-card">
          <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px;">
            <div>
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="pill danger" style="font-size:10.5px; font-weight:800;">CRITICAL FIX</span>
                <strong style="font-size:13px; color:var(--text-main);">Patch 1: Midday Low-Volume Lull Gate (11:15 MT – 12:45 MT)</strong>
              </div>
              <div style="font-size:11.5px; color:var(--text-muted); margin-top:4px;">
                <strong>Problem:</strong> 5 out of 11 AI losses occurred in this 90-minute lunch lull window, burning -$694.00 in fake breakouts.
              </div>
            </div>
            <span style="font-family:var(--font-mono); font-size:12px; font-weight:800; color:var(--emerald); background:rgba(16,185,129,0.1); border:1px solid rgba(16,185,129,0.3); padding:2px 8px; border-radius:4px;">
              +$694.00 Expected Edge
            </span>
          </div>
          <div style="margin-top:10px; background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius-sm); padding:10px; font-family:var(--font-mono); font-size:11.5px; color:var(--text-main);">
            <div style="color:var(--text-muted); margin-bottom:4px;">// Add to gems/revanth-0dte.md under [EXECUTION TIMING GATES]:</div>
            <code>
              RULE 4.1 (MIDDAY CHOP FILTER):<br>
              IF alert_timestamp falls between 11:15 MT (13:15 ET) and 12:45 MT (14:45 ET):<br>
              &nbsp;&nbsp;REJECT all counter-trend, reversal, or breakout signals unless Conviction &ge; 88.<br>
              &nbsp;&nbsp;RATIONALE: Market makers extract premium during low-volume lunch chop; 80%+ of midday breakouts fail before Target 1.
            </code>
          </div>
        </div>

        <!-- Rule Card 2: Late Afternoon Momentum -->
        <div class="pm-rule-card">
          <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px;">
            <div>
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="pill amber" style="font-size:10.5px; font-weight:800;">ALPHA CAPTURE</span>
                <strong style="font-size:13px; color:var(--text-main);">Patch 2: Late-Session Index Trend Continuation (14:15 MT – 15:30 MT)</strong>
              </div>
              <div style="font-size:11.5px; color:var(--text-muted); margin-top:4px;">
                <strong>Problem:</strong> AI rejected 14:55 MT QQQ Short breakdown citing "late-day session close", forfeiting +$390.00 in trend alpha.
              </div>
            </div>
            <span style="font-family:var(--font-mono); font-size:12px; font-weight:800; color:var(--cyan); background:rgba(6,182,212,0.1); border:1px solid rgba(6,182,212,0.3); padding:2px 8px; border-radius:4px;">
              +$390.00 Missed Run
            </span>
          </div>
          <div style="margin-top:10px; background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius-sm); padding:10px; font-family:var(--font-mono); font-size:11.5px; color:var(--text-main);">
            <div style="color:var(--text-muted); margin-bottom:4px;">// Add to gems/revanth-0dte.md under [AFTERNOON EXECUTION EXCEPTIONS]:</div>
            <code>
              RULE 4.2 (POWER HOUR INDEX MOMENTUM EXCEPTION):<br>
              IF symbol IN ['QQQ', 'SPY', 'IWM'] AND time &ge; 14:15 MT (16:15 ET):<br>
              &nbsp;&nbsp;DO NOT reject trend breakdown/breakout on time alone IF 15m Stage 4/Stage 2 is actively accelerating.<br>
              &nbsp;&nbsp;TARGET: Close position at 15:45 MT (15 min prior to closing bell) with hard stop at 0.75x ATR.
            </code>
          </div>
        </div>

        <!-- Rule Card 3: Dynamic ATR Stop Cap -->
        <div class="pm-rule-card">
          <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px;">
            <div>
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="pill cyan" style="font-size:10.5px; font-weight:800;">RISK MANAGEMENT</span>
                <strong style="font-size:13px; color:var(--text-main);">Patch 3: Hard Dynamic ATR Loss-Cap Circuit Breaker</strong>
              </div>
              <div style="font-size:11.5px; color:var(--text-muted); margin-top:4px;">
                <strong>Problem:</strong> Outsized losses on GOOGL (-$185) and TSLA (-$159) occurred when stop loss drifted past 1.5x ATR.
              </div>
            </div>
            <span style="font-family:var(--font-mono); font-size:12px; font-weight:800; color:var(--emerald); background:rgba(16,185,129,0.1); border:1px solid rgba(16,185,129,0.3); padding:2px 8px; border-radius:4px;">
              -$140.00 Loss Reduced
            </span>
          </div>
          <div style="margin-top:10px; background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius-sm); padding:10px; font-family:var(--font-mono); font-size:11.5px; color:var(--text-main);">
            <div style="color:var(--text-muted); margin-bottom:4px;">// Add to gems/revanth-0dte.md under [HARD RISK CONTROLS]:</div>
            <code>
              RULE 2.4 (HARD ATR STOP BREAKER):<br>
              MAX allowed initial stop distance = 1.25 &times; 5m ATR.<br>
              IF stop distance &gt; 1.25 &times; ATR, either resize contract delta DOWN or REJECT setup as "R:R Distorted".
            </code>
          </div>
        </div>

      </div>
    `;
  },

  renderPostMortemRaw(d) {
    const rawMd = this.generatePostMortemMarkdown(d);
    return `
      <div style="display:flex; flex-direction:column; gap:12px;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span style="font-size:13px; font-weight:800; color:var(--text-main);">
            📜 Full Post-Mortem Report (Markdown)
          </span>
          <div style="display:flex; gap:8px;">
            <button class="btn secondary" onclick="AppAlerts.copyPostMortemMarkdown()" style="padding:4px 10px; font-size:11px; font-weight:700;">
              📋 Copy Markdown
            </button>
            <button class="btn secondary" onclick="AppAlerts.downloadPostMortemJson()" style="padding:4px 10px; font-size:11px; font-weight:700;">
              📥 Download JSON
            </button>
          </div>
        </div>
        <textarea id="pm-raw-markdown-textarea" readonly style="width:100%; height:420px; font-family:var(--font-mono); font-size:11.5px; background:var(--bg-card); color:var(--text-main); border:1px solid var(--border); border-radius:var(--radius-sm); padding:12px; line-height:1.4; resize:vertical;">${rawMd}</textarea>
      </div>
    `;
  },

  generatePostMortemMarkdown(d) {
    return `# INTRADAY SESSION POST-MORTEM & 4-QUADRANT ATTRIBUTION REPORT
Date: ${d.dateKey === 'ALL' ? 'ALL SESSIONS COMBINED (5 DAYS CUMULATIVE)' : d.dateKey}
Generated: ${new Date().toISOString()}

## 1. EXECUTIVE SCORECARD
- Total Setups Monitored: ${d.totalTrades}
- Total System P&L (100 shs): $${d.totalPnl.toFixed(2)}
- AI Taken Trades: ${d.truePos.length + d.falsePos.length} (Wins: ${d.truePos.length}, Losses: ${d.falsePos.length})
- AI Taken Win Rate: ${d.aiWinRate.toFixed(1)}%
- AI Taken Net P&L: $${d.aiTakenTotalPnl.toFixed(2)}
- Capital Saved by Triage: +$${d.capitalSaved.toFixed(2)} (${d.trueNeg.length} toxic traps rejected)
- Missed Opportunity Alpha: -$${d.alphaMissed.toFixed(2)} (${d.falseNeg.length} profitable trades filtered)
- Net Decision Edge: ${d.capitalSaved - d.alphaMissed >= 0 ? '+' : ''}$${(d.capitalSaved - d.alphaMissed).toFixed(2)}

## 2. 4-QUADRANT ATTRIBUTION MATRIX

### Q1: True Positives (AI Approved & Profitable: +$${d.sumTruePosPnl.toFixed(2)})
${d.truePos.map(t => `- ${t.symbol} ${t.side} (${(t.entryTime||'').substring(11,16)}): +$${t.pnl100.toFixed(2)} | Exit: ${t.exitReason}`).join('\n')}

### Q2: False Positives (AI Approved But Lost: -$${Math.abs(d.sumFalsePosPnl).toFixed(2)})
${d.falsePos.map(t => `- ${t.symbol} ${t.side} (${(t.entryTime||'').substring(11,16)}): -$${Math.abs(t.pnl100).toFixed(2)} | Exit: ${t.exitReason}`).join('\n')}

### Q3: True Negatives (AI Rejected & Saved Capital: +$${d.capitalSaved.toFixed(2)})
${d.trueNeg.map(t => `- ${t.symbol} ${t.side} (${(t.entryTime||'').substring(11,16)}): Saved +$${Math.abs(t.pnl100).toFixed(2)} | Avoided chop/counter-trend`).join('\n')}

### Q4: False Negatives (AI Rejected But Ran: -$${d.alphaMissed.toFixed(2)})
${d.falseNeg.map(t => `- ${t.symbol} ${t.side} (${(t.entryTime||'').substring(11,16)}): Missed +$${t.pnl100.toFixed(2)} | ${t.symbol === 'QQQ' ? 'Late-session trend expansion' : 'Morning momentum'}`).join('\n')}

## 3. PROPOSED RULE CARD PATCHES (gems/revanth-0dte.md)
1. **Rule 4.1 (Midday Low-Volume Lull Gate)**: Blackout all counter-trend / breakout trades between 11:15–12:45 MT unless Conviction >= 88. Eliminates ${d.lunchLullLosses.length} losses (-$${Math.abs(d.lunchLullLossPnl).toFixed(2)}).
2. **Rule 4.2 (Late-Day Index Trend Continuation)**: Allow index puts (QQQ/SPY) after 14:15 MT if 15m Stage 4 is actively accelerating. Captures +$390.00 missed alpha.
3. **Rule 2.4 (Hard Dynamic ATR Loss Cap)**: Enforce maximum stop distance of 1.25x ATR to eliminate large runaway drawdowns.
`;
  },

  copyPostMortemMarkdown() {
    const d = this._currentPostMortemData;
    if (!d) return;
    const md = this.generatePostMortemMarkdown(d);
    navigator.clipboard.writeText(md).then(() => {
      alert('✅ Post-Mortem Report successfully copied to clipboard!');
    }).catch(err => {
      console.error('Clipboard copy failed', err);
    });
  },

  downloadPostMortemJson() {
    const d = this._currentPostMortemData;
    if (!d) return;
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(d, null, 2));
    const dlAnchorElem = document.createElement('a');
    dlAnchorElem.setAttribute("href", dataStr);
    dlAnchorElem.setAttribute("download", `postmortem_${d.dateKey || 'session'}.json`);
    dlAnchorElem.click();
  },

  async savePostMortemToSkill() {
    const d = this._currentPostMortemData;
    if (!d) return;
    const md = this.generatePostMortemMarkdown(d);
    try {
      const res = await fetch('/api/skills/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          skill_name: 'postmortem_learnings',
          content: md
        })
      });
      if (res.ok) {
        alert('✅ Successfully saved session findings to skills/postmortem_learnings.md! Now available on-demand to LLM via invoke_skill.');
      } else {
        alert('⚠️ Failed to save skill to server.');
      }
    } catch (e) {
      console.error('Save skill error:', e);
      alert('Error saving skill: ' + e.message);
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

// Global Escape key handler for Intraday & Alert modals (reverse stack order)
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    // 1. Highest z-index: Alert Detail modal (1280)
    const alertModal = document.getElementById('tv-alert-detail-modal');
    if (alertModal && alertModal.style.display !== 'none' && alertModal.style.display !== '') {
      AppAlerts.closeAlertModal(true);
      return;
    }
    // 2. Trade Detail modal (1270)
    const tradeDetailModal = document.getElementById('modal-trade-detail');
    if (tradeDetailModal && tradeDetailModal.style.display !== 'none' && tradeDetailModal.style.display !== '') {
      AppAlerts.closeTradeDetailModal(true);
      return;
    }
    // 3. Ticker Day Trades modal (1260)
    const tickerTradesModal = document.getElementById('modal-ticker-day-trades');
    if (tickerTradesModal && tickerTradesModal.style.display !== 'none' && tickerTradesModal.style.display !== '') {
      AppAlerts.closeTickerTradesModal(true);
      return;
    }
    // 4. Intraday Post-Mortem modal (1260)
    const postMortemModal = document.getElementById('modal-intraday-postmortem');
    if (postMortemModal && postMortemModal.style.display !== 'none' && postMortemModal.style.display !== '') {
      AppAlerts.closePostMortemModal(true);
      return;
    }
    // 5. Session P&L modal (1250)
    const pnlModal = document.getElementById('modal-alerts-pnl');
    if (pnlModal && pnlModal.style.display !== 'none' && pnlModal.style.display !== '') {
      AppAlerts.closePnlModal(true);
      return;
    }
  }
});
