/**
 * Swing Stalking Desk Controller
 */
window.AppSwing = {
  setTicker(sym) {
    const input = document.getElementById('ticker-input');
    if (input) input.value = sym;
    const revInput = document.getElementById('rev-chat-ticker');
    if (revInput) revInput.value = sym;

    document.querySelectorAll('.ticker-pill-btn').forEach(b => {
      b.className = `ticker-pill-btn ${b.innerText === sym ? 'active' : ''}`;
    });
    if (window.AppChat && typeof window.AppChat.updateSidebarFocusBadge === 'function') {
      window.AppChat.updateSidebarFocusBadge(sym);
    }
  },

  async launchResearch(dateOverride) {
    const ticker = (document.getElementById('ticker-input').value || '').trim().toUpperCase();
    const mode = document.getElementById('mode-select').value;
    if (!ticker) return alert('Please enter a ticker');

    try {
      const activeDate = dateOverride || (window.AppState ? window.AppState.currentArchiveDate : null) || (document.getElementById('archive-date-select') ? document.getElementById('archive-date-select').value : null);
      await window.AppApi.triggerResearch(ticker, mode, activeDate);
      if (window.AppStatus) {
        await window.AppStatus.updateStatus();
        await window.AppStatus.loadJobs();
      }
    } catch (e) {
      alert(`Research Alert: ${e.message}`);
    }
  },

  _allTargets: [],
  _targetsSearchQuery: '',
  _collapsedTargetDates: new Set(),
  async loadWatchTargets() {
    if (window.AppWatchlist) {
      await window.AppWatchlist.loadWatchlist();
    }
    await this.refreshResearchQueue();
    await this.loadTastytradeAlerts();
    await this.loadCalibrationScoreboard();
  },

  async refreshResearchQueue() {
    const pillsEl = document.getElementById('research-queue-pills');
    if (!pillsEl) return;

    try {
      const activeDate = (window.AppState ? window.AppState.currentArchiveDate : null) || (document.getElementById('archive-date-select') ? document.getElementById('archive-date-select').value : null);
      const data = await window.AppApi.getResearchQueue(activeDate);
      const queue = (data && data.queue) ? data.queue : [];
      if (queue.length === 0) {
        pillsEl.innerHTML = '<span style="font-size:11px; color:var(--green-light); font-weight:700;">✅ All priority candidates researched!</span>';
        return;
      }

      pillsEl.innerHTML = queue.map(item => {
        const isReady = (item.status === 'READY_FOR_RESEARCH');
        const badgeColor = isReady ? 'var(--blue)' : 'var(--amber)';
        const btnStyle = isReady
          ? 'background:#eff6ff; border-color:#bfdbfe; color:#1d4ed8;'
          : 'background:#fffbeb; border-color:#fde68a; color:#b45309;';

        return `
          <div style="display:inline-flex; align-items:center; gap:6px; background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:3px 8px; box-shadow:0 1px 2px rgba(0,0,0,0.03);">
            <strong style="color:var(--text-main); font-size:12px;">$${item.ticker}</strong>
            <span style="font-size:10.5px; color:${badgeColor}; font-weight:600;">${item.reason}</span>
            <button class="btn secondary" onclick="AppSwing.runResearchAction('${item.ticker}', '${item.action}', '${item.date || ''}')" style="padding:2px 7px; font-size:10.5px; font-weight:700; ${btnStyle}">
              ${item.action_label}
            </button>
          </div>
        `;
      }).join('');
    } catch (e) {
      console.warn('Failed loading research queue:', e);
      pillsEl.innerHTML = '<span style="font-size:11px; color:var(--text-muted);">Queue scan unavailable.</span>';
    }
  },

  async runResearchAction(ticker, mode, date) {
    this.setTicker(ticker);
    const modeSelect = document.getElementById('mode-select');
    if (modeSelect) modeSelect.value = mode || 'full';
    await this.launchResearch(date);
    setTimeout(() => this.refreshResearchQueue(), 3000);
  },

  renderWatchTargets() {
    const listEl = document.getElementById('targets-list');
    const countEl = document.getElementById('targets-count');
    if (!listEl) return;

    let targets = this._allTargets || [];
    const query = this._targetsSearchQuery;

    // Apply live search filter
    if (query) {
      targets = targets.filter(t => {
        const sym = (t.ticker || '').toLowerCase();
        const dt = (t.date || '').toLowerCase();
        const st = (t.status || '').toLowerCase();
        const opt = (t.options_summary || '').toLowerCase();
        const lp = String(t.last_price || '');
        return sym.includes(query) || dt.includes(query) || st.includes(query) || opt.includes(query) || lp.includes(query);
      });
    }

    if (countEl) {
      countEl.innerText = query ? `${targets.length} / ${this._allTargets.length} Targets` : `${targets.length} Targets`;
    }

    if (targets.length === 0) {
      listEl.innerHTML = query
        ? `<div style="color:var(--text-muted); font-size:13px; padding:20px; text-align:center;">No stalking targets match "<strong>${query}</strong>".</div>`
        : '<div style="color:var(--text-muted); font-size:13px; padding:20px; text-align:center;">No active watch targets registered in SQLite DB.</div>';
      return;
    }

    // Group targets by Date
    const grouped = {};
    targets.forEach(t => {
      const dt = t.date || 'Undated';
      if (!grouped[dt]) grouped[dt] = [];
      grouped[dt].push(t);
    });

    const sortedDates = Object.keys(grouped).sort((a, b) => b.localeCompare(a));
    const latestDate = sortedDates[0];

    listEl.innerHTML = sortedDates.map(dateStr => {
      const groupTargets = grouped[dateStr];
      const isAutoCollapsed = (!query && dateStr !== latestDate && !this._collapsedTargetDates.has(`EXP_${dateStr}`)) || this._collapsedTargetDates.has(dateStr);
      const isCollapsed = query ? false : isAutoCollapsed;

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
            
            <!-- Visual Price Ladder -->
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
        <div class="date-accordion-card ${isCollapsed ? 'collapsed' : ''}" id="date-group-${dateStr}">
          <div class="date-accordion-head" onclick="AppSwing.toggleTargetDateGroup('${dateStr}')">
            <div class="date-accordion-title">
              <span class="date-accordion-icon">▼</span>
              <span>📅 Research Date: <strong>${dateStr}</strong></span>
              ${dateStr === latestDate ? '<span class="pill green" style="font-size:10px; padding:2px 6px;">Latest</span>' : ''}
            </div>
            <div style="display:flex; align-items:center; gap:8px;">
              ${inZoneCount > 0 ? `<span class="badge in_zone" style="font-size:11px;">🎯 ${inZoneCount} In Zone</span>` : ''}
              ${stalkingCount > 0 ? `<span class="badge stalking" style="font-size:11px;">⏳ ${stalkingCount} Stalking</span>` : ''}
              <span class="pill cyan" style="font-size:11px; padding:3px 8px;">${groupTargets.length} ${groupTargets.length === 1 ? 'Target' : 'Targets'}</span>
            </div>
          </div>
          <div class="date-accordion-body">
            ${cardsHtml}
          </div>
        </div>
      `;
    }).join('');
  },

  async loadTastytradeAlerts() {
    try {
      const data = await window.AppApi.getTastytradeAlerts();
      const container = document.getElementById('alerts-grouped-container');
      const containerMini = document.getElementById('alerts-grouped-container-mini');
      const countEl = document.getElementById('alerts-count');
      const countMini = document.getElementById('alerts-count-mini');
      const alerts = data.alerts || [];
      if (countEl) countEl.innerText = `${alerts.length} Cloud Alerts`;
      if (countMini) countMini.innerText = `${alerts.length} Active`;

      if (!container && !containerMini) return;

      if (alerts.length === 0) {
        const emptyMsg = '<div style="color:var(--text-muted); text-align:center; padding:16px;">No cloud alerts currently active on Tastytrade.</div>';
        if (container) container.innerHTML = emptyMsg;
        if (containerMini) containerMini.innerHTML = emptyMsg;
        return;
      }

      // Group alerts by symbol
      const groups = {};
      alerts.forEach(a => {
        const sym = (a.symbol || '').toUpperCase();
        if (!groups[sym]) groups[sym] = [];
        groups[sym].push(a);
      });

      const fullHtml = Object.keys(groups).sort().map(sym => {
        const alertsList = groups[sym];

        const subRows = alertsList.map(a => {
          const op = (a.operator === 'LessThanOrEqualTo' || a.operator === '<') ? '<' : '>';
          const thresh = parseFloat(a.threshold).toFixed(2);
          const exp = (a['expires-at'] || '90d Default').substring(0, 10);
          const id = a['alert-external-id'];

          const isSupport = (op === '<');
          const badgeType = isSupport ? 'buy' : 'target';
          const label = isSupport ? 'Support / Entry' : 'Resistance / Target';

          return `
            <div class="alert-sub-row">
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="alert-trigger-type ${badgeType}">${label}</span>
                <span>Last ${op} <strong>$${thresh}</strong></span>
              </div>
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="color:var(--text-muted); font-size:11px;">Exp: ${exp}</span>
                <button class="btn secondary" onclick="AppSwing.openEditAlertModal('${id}', '${sym}', '${op}', ${thresh}, '${exp}')" style="padding:2px 6px; font-size:11px;" title="Edit Alert">✏️</button>
                <button class="btn danger" onclick="AppSwing.deleteAlert('${id}')" style="padding:2px 6px; font-size:11px;" title="Delete Alert">✕</button>
              </div>
            </div>
          `;
        }).join('');

        return `
          <div class="alert-group-card">
            <div class="alert-group-header">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-family:'Outfit',sans-serif; font-size:16px; font-weight:800; color:var(--cyan-glow);">${sym}</span>
                <span class="pill cyan" style="font-size:10px; padding:2px 6px;">${alertsList.length} Triggers</span>
              </div>
              <div style="display:flex; gap:6px;">
                <button class="btn secondary" onclick="AppSwing.openCreateAlertFor('${sym}')" style="padding:3px 8px; font-size:11px;">+ Add</button>
                <button class="btn danger" onclick="AppSwing.deleteGroup('${sym}')" style="padding:3px 8px; font-size:11px;">Clear All</button>
              </div>
            </div>
            <div style="display:flex; flex-direction:column; gap:6px;">
              ${subRows}
            </div>
          </div>
        `;
      }).join('');

      if (container) container.innerHTML = fullHtml;
      if (containerMini) containerMini.innerHTML = fullHtml;
    } catch (e) {
      console.error('Failed loading Tastytrade alerts', e);
    }
  },

  openCreateAlertFor(sym) {
    this.openCreateAlertModal();
    const symEl = document.getElementById('modal-alert-sym');
    if (symEl) symEl.value = sym;
  },

  async deleteGroup(sym) {
    if (!confirm(`Delete all cloud alerts for ${sym}?`)) return;
    try {
      const data = await window.AppApi.getTastytradeAlerts();
      const toDelete = (data.alerts || []).filter(a => (a.symbol || '').toUpperCase() === sym.toUpperCase());
      for (const a of toDelete) {
        await window.AppApi.deleteTastytradeAlert(a['alert-external-id']);
      }
      await this.loadTastytradeAlerts();
    } catch (e) {
      alert(`Error deleting alerts: ${e.message}`);
    }
  },

  async deleteAlert(id) {
    if (!confirm('Delete this cloud alert on Tastytrade?')) return;
    try {
      await window.AppApi.deleteTastytradeAlert(id);
      await this.loadTastytradeAlerts();
    } catch (e) {
      alert(`Error deleting alert: ${e.message}`);
    }
  },

  openCreateAlertModal() {
    document.getElementById('alert-modal-title').innerText = 'CREATE CLOUD ALERT';
    document.getElementById('modal-alert-id').value = '';
    const currentTicker = (document.getElementById('ticker-input').value || '').trim().toUpperCase() || 'WMT';
    document.getElementById('modal-alert-sym').value = currentTicker;
    document.getElementById('modal-alert-op').value = '<';
    document.getElementById('modal-alert-thresh').value = '';
    document.getElementById('modal-alert-exp').value = '2026-10-02';
    document.getElementById('alert-editor-modal').style.display = 'flex';
  },

  openEditAlertModal(id, sym, op, thresh, exp) {
    document.getElementById('alert-modal-title').innerText = `EDIT ALERT: ${sym}`;
    document.getElementById('modal-alert-id').value = id;
    document.getElementById('modal-alert-sym').value = sym;
    document.getElementById('modal-alert-op').value = op;
    document.getElementById('modal-alert-thresh').value = thresh;
    document.getElementById('modal-alert-exp').value = exp;
    document.getElementById('alert-editor-modal').style.display = 'flex';
  },

  closeAlertModal() {
    const modal = document.getElementById('alert-editor-modal');
    if (modal) modal.style.display = 'none';
  },

  async submitAlertModal() {
    const id = document.getElementById('modal-alert-id').value.trim();
    const symbol = document.getElementById('modal-alert-sym').value.trim().toUpperCase();
    const operator = document.getElementById('modal-alert-op').value;
    const threshold = parseFloat(document.getElementById('modal-alert-thresh').value);
    const expires_at = document.getElementById('modal-alert-exp').value.trim();

    if (!symbol || isNaN(threshold)) {
      return alert('Please enter valid symbol and price threshold');
    }

    try {
      if (id) {
        await window.AppApi.modifyTastytradeAlert({ alert_id: id, symbol, operator, threshold, expires_at });
      } else {
        await window.AppApi.createTastytradeAlert({ symbol, operator, threshold, expires_at });
      }
      this.closeAlertModal();
      await this.loadTastytradeAlerts();
    } catch (e) {
      alert(`Alert Error: ${e.message}`);
    }
  },

  async loadReportArchive(date = null) {
    try {
      const data = await window.AppApi.getReports(date);
      window.AppState.allReportDates = data.dates || [];
      window.AppState.currentArchiveDate = date || data.selected_date || (window.AppState.allReportDates[0] || '');
      this._allArchiveReports = (data.reports_by_date && data.reports_by_date[window.AppState.currentArchiveDate]) || [];

      const sel = document.getElementById('archive-date-select');
      if (sel) {
        sel.innerHTML = window.AppState.allReportDates.map(d => {
          const isSelected = (d === window.AppState.currentArchiveDate) ? 'selected' : '';
          return `<option value="${d}" ${isSelected}>📅 ${d}</option>`;
        }).join('');
      }

      this.renderArchiveReports();
    } catch (e) {
      console.error('Failed loading report archive', e);
    }
  },

  filterArchiveReports(query) {
    this._archiveSearchQuery = (query || '').trim().toLowerCase();
    this.renderArchiveReports();
  },

  renderArchiveReports() {
    const container = document.getElementById('archive-reports-list');
    const countPill = document.getElementById('archive-count');
    if (!container) return;

    let reports = this._allArchiveReports || [];
    const query = this._archiveSearchQuery;

    if (query) {
      reports = reports.filter(r => {
        const sym = (r.ticker || '').toLowerCase();
        const verd = (r.verdict || '').toLowerCase();
        const opt = (r.options_summary || '').toLowerCase();
        return sym.includes(query) || verd.includes(query) || opt.includes(query);
      });
    }

    if (countPill) {
      countPill.innerText = query
        ? `${reports.length} / ${this._allArchiveReports.length} Reports`
        : `${reports.length} Reports (${window.AppState.currentArchiveDate})`;
    }

    if (reports.length === 0) {
      container.innerHTML = query
        ? `<div style="color:var(--text-muted); font-size:13px; padding:20px; text-align:center; grid-column: 1/-1;">No research dossiers match "<strong>${query}</strong>" for ${window.AppState.currentArchiveDate}.</div>`
        : `<div style="color:var(--text-muted); font-size:13px; padding:20px; text-align:center; grid-column: 1/-1;">No research dossiers found for ${window.AppState.currentArchiveDate}.</div>`;
      return;
    }

    container.innerHTML = reports.map(r => {
      const verdictStr = r.verdict || 'ANALYZED';
      const verdictClass = window.AppUtils.getVerdictBadgeClass(verdictStr);
      const optText = r.options_summary || 'Detailed strategy inside 3-model dossier.';
      const entryStr = (r.entry_zone && r.entry_zone.length >= 2)
        ? `$${Number(r.entry_zone[0]).toFixed(2)} - $${Number(r.entry_zone[1]).toFixed(2)}`
        : 'At Trigger';
      const stopStr = r.tactical_stop ? `$${Number(r.tactical_stop).toFixed(2)}` : 'N/A';
      const t1Str = r.target_1 ? `$${Number(r.target_1).toFixed(2)}` : 'N/A';
      const t2Str = r.target_2 ? `$${Number(r.target_2).toFixed(2)}` : 'N/A';

      return `
        <div class="target-card">
          <div class="target-header">
            <div class="target-sym">
              <span style="color:var(--cyan-glow);">${r.ticker}</span>
              <span class="badge ${verdictClass}">${verdictStr}</span>
            </div>
            <button class="btn secondary" onclick="AppSwing.openReportModal('${r.date}', '${r.ticker}')" style="padding:5px 12px; font-size:12px;">📑 Open Dossier</button>
          </div>

          <div class="options-snippet">
            <strong>Strategy:</strong> ${optText}
          </div>

          <div class="levels-row">
            <div class="level-col"><label>Entry Zone</label><span>${entryStr}</span></div>
            <div class="level-col"><label>Tactical Stop</label><span style="color:var(--rose-light);">${stopStr}</span></div>
            <div class="level-col"><label>Target 1</label><span style="color:var(--emerald-light);">${t1Str}</span></div>
            <div class="level-col"><label>Target 2</label><span style="color:var(--cyan-glow);">${t2Str}</span></div>
          </div>
        </div>
      `;
    }).join('');
  },

  changeArchiveDate(date) {
    this.loadReportArchive(date);
  },

  stepArchiveDate(step) {
    const dates = window.AppState.allReportDates || [];
    if (dates.length === 0) return;
    let idx = dates.indexOf(window.AppState.currentArchiveDate);
    if (idx === -1) idx = 0;
    let newIdx = idx - step;
    if (newIdx < 0) newIdx = 0;
    if (newIdx >= dates.length) newIdx = dates.length - 1;
    this.loadReportArchive(dates[newIdx]);
  },

  async openPlanExecution(ticker, date) {
    if (!ticker) return;
    await this.openReportModal(date, ticker, 'plan');
    if (window.AppChat && typeof window.AppChat.askModalCopilot === 'function') {
      window.AppChat.askModalCopilot(`What is the exact execution step for $${ticker} right now?`);
    }
  },

  async openReportModal(date, ticker, initialTab = null) {
    if (date === 'undefined' || date === 'null' || date === '') date = null;
    if (ticker === 'undefined' || ticker === 'null' || ticker === '') ticker = null;

    const isDate = v => v && (String(v).includes('-') || ['latest', 'today', 'now'].includes(String(v).toLowerCase().trim()));
    if (date && !ticker) {
      if (!isDate(date)) {
        ticker = date;
        date = null;
      }
    } else if (date && ticker) {
      if (!isDate(date) && isDate(ticker)) {
        const tmp = date;
        date = ticker;
        ticker = tmp;
      }
    }
    ticker = (ticker || '').toUpperCase().trim();
    if (!ticker) return;

    if (initialTab) {
      window.AppState.activeDossierTab = initialTab;
    }

    const modal = document.getElementById('report-modal');
    if (!modal) return;
    modal.classList.remove('minimized');
    modal.style.display = 'flex';
    document.getElementById('modal-ticker-title').innerText = `${ticker} RESEARCH REPORT (${date || 'Latest'})`;
    const pulse = document.getElementById('modal-minimized-pulse');
    if (pulse) pulse.style.display = 'none';
    const minBtn = document.getElementById('btn-modal-min-toggle');
    if (minBtn) minBtn.innerHTML = '<span style="font-size:11px;">🗕</span> Minimize';
    document.getElementById('modal-body').innerHTML = '<div style="padding:40px; text-align:center;">Loading dossier...</div>';

    try {
      const reqDate = date || (window.AppState ? window.AppState.currentArchiveDate : null) || 'latest';
      const data = await window.AppApi.getReportBundle(reqDate, ticker);
      data.ticker = ticker;
      const actualDate = data.date || reqDate;
      data.date = actualDate;
      window.AppState.currentReportData = data;
      document.getElementById('modal-ticker-title').innerText = `${ticker} RESEARCH REPORT (${actualDate})`;
      if (window.AppChat && typeof window.AppChat.updateSidebarFocusBadge === 'function') {
        window.AppChat.updateSidebarFocusBadge(ticker);
      }

      // Render Dedicated Suggested Positions Side Pane
      this.renderSuggestedPositionsPane(data);

      // Populate Date Switcher dropdown
      const dateSelect = document.getElementById('modal-report-date-select');
      const histCountEl = document.getElementById('modal-hist-count');
      const dates = data.available_dates || [actualDate];
      if (histCountEl) histCountEl.innerText = dates.length;

      if (dateSelect) {
        dateSelect.innerHTML = dates.map(d => {
          const isSel = (d === actualDate) ? 'selected' : '';
          const matchItem = (data.historical_timeline || []).find(x => x.date === d);
          const spotLabel = (matchItem && matchItem.spot) ? ` ($${Number(matchItem.spot).toFixed(2)})` : '';
          return `<option value="${d}" ${isSel}>${d}${spotLabel}</option>`;
        }).join('');
      }

      const key = `${ticker}_${actualDate}`;
      if (!window.AppState.modalChatHistories) window.AppState.modalChatHistories = {};
      window.AppState.modalChatHistory = window.AppState.modalChatHistories[key] || [];
      
      const activeTab = initialTab || window.AppState.activeDossierTab || 'arb';
      this.switchDossierTab(activeTab);

      // Display live current price and start polling
      this.refreshModalLivePrice(ticker, data.live_price);
      if (this._modalLivePricePollTimer) clearInterval(this._modalLivePricePollTimer);
      this._modalLivePricePollTimer = setInterval(() => {
        const m = document.getElementById('report-modal');
        if (m && m.style.display !== 'none') {
          this.refreshModalLivePrice();
        } else {
          clearInterval(this._modalLivePricePollTimer);
          this._modalLivePricePollTimer = null;
        }
      }, 15000);

      if (window.AppChat) {
        window.AppChat.initModalChatForTicker(ticker, actualDate);
      }
    } catch (e) {
      document.getElementById('modal-body').innerText = `Failed loading report: ${e.message}`;
    }
  },

  togglePositionsPane(forceState = null) {
    const pane = document.getElementById('modal-positions-pane');
    const btn = document.getElementById('btn-toggle-positions-pane');
    if (!pane) return;

    const shouldShow = (forceState !== null) ? !!forceState : (pane.style.display === 'none');
    pane.style.display = shouldShow ? 'flex' : 'none';
    window.AppState.showPositionsPane = shouldShow;

    if (btn) {
      if (shouldShow) {
        btn.style.color = '#059669';
        btn.style.borderColor = '#059669';
        btn.style.background = 'rgba(5, 150, 105, 0.12)';
      } else {
        btn.style.color = 'var(--text-muted)';
        btn.style.borderColor = 'var(--border)';
        btn.style.background = 'transparent';
      }
    }
  },

  renderSuggestedPositionsPane(data) {
    const pane = document.getElementById('modal-positions-pane');
    if (!pane) return;
    const wl = data ? data.watch_levels : null;
    if (!wl) {
      this.togglePositionsPane(false);
      return;
    }

    // Show pane by default if user hasn't explicitly closed it
    const shouldShow = window.AppState.showPositionsPane !== false;
    this.togglePositionsPane(shouldShow);

    // 0. Active Position Banner cleanup
    let posBanner = document.getElementById('pane-active-position-banner');
    if (posBanner) {
      posBanner.style.display = 'none';
    }

    // 1. Verdict & Status
    const vBadge = document.getElementById('pane-verdict-badge');
    const verdict = (wl.verdict || 'ENTER').toUpperCase();
    const conv = wl.conviction ? ` (${wl.conviction}/10)` : '';
    if (vBadge) {
      vBadge.textContent = `${verdict}${conv}`;
      const vClass = (window.AppUtils && typeof window.AppUtils.getVerdictBadgeClass === 'function')
        ? window.AppUtils.getVerdictBadgeClass(verdict)
        : 'in_zone';
      vBadge.className = `badge ${vClass}`;
    }

    const stBadge = document.getElementById('pane-status-badge');
    const st = (wl.status || 'STALKING').toUpperCase();
    if (stBadge) {
      stBadge.textContent = st;
      stBadge.className = (st === 'IN_ZONE' || st === 'IN_TRADE') ? 'pill green' : (st === 'INVALIDATED' ? 'pill red' : 'pill amber');
    }

    // 2. Prices
    const spotEl = document.getElementById('pane-spot-price');
    if (spotEl) {
      spotEl.textContent = data.spot_price ? `$${Number(data.spot_price).toFixed(2)}` : '--';
    }
    const liveEl = document.getElementById('pane-live-price');
    if (liveEl) {
      liveEl.textContent = data.live_price ? `$${Number(data.live_price).toFixed(2)}` : '--';
    }

    // 3. Options Play
    const optPlan = wl.options_plan || {};
    const optAct = document.getElementById('pane-opt-actionable');
    if (optAct) {
      optAct.style.display = optPlan.actionable ? 'inline-block' : 'none';
    }

    const optName = document.getElementById('pane-opt-name');
    if (optName) {
      optName.textContent = optPlan.structure ? optPlan.structure.replace(/_/g, ' ') : 'DEFINED RISK SPREAD';
    }

    const optSummary = document.getElementById('pane-opt-summary');
    if (optSummary) {
      optSummary.textContent = optPlan.summary || 'Defined risk options structure.';
    }

    const optExpiry = document.getElementById('pane-opt-expiry');
    if (optExpiry) {
      optExpiry.textContent = optPlan.expiration || '--';
    }

    const optDebit = document.getElementById('pane-opt-debit');
    if (optDebit) {
      optDebit.textContent = optPlan.target_debit ? `~$${Number(optPlan.target_debit).toFixed(2)}` : '--';
    }

    const optLoss = document.getElementById('pane-opt-loss');
    if (optLoss) {
      optLoss.textContent = optPlan.max_loss ? `$${Number(optPlan.max_loss).toLocaleString()}` : '--';
    }

    const optProfit = document.getElementById('pane-opt-profit');
    if (optProfit) {
      optProfit.textContent = optPlan.max_profit ? `$${Number(optPlan.max_profit).toLocaleString()}` : '--';
    }

    // 4. Equity Execution Plan
    const shPlan = wl.shares_plan || {};
    const eqSide = document.getElementById('pane-eq-side');
    if (eqSide) {
      eqSide.textContent = (shPlan.side || 'LONG').toUpperCase();
    }

    const eqLimit = document.getElementById('pane-eq-limit');
    if (eqLimit) {
      const low = shPlan.entry_zone_low ? `$${Number(shPlan.entry_zone_low).toFixed(2)}` : '--';
      const high = shPlan.entry_zone_high ? `$${Number(shPlan.entry_zone_high).toFixed(2)}` : '--';
      eqLimit.textContent = `${low} – ${high}`;
    }

    const eqStop = document.getElementById('pane-eq-stop');
    if (eqStop) {
      eqStop.textContent = shPlan.tactical_stop ? `$${Number(shPlan.tactical_stop).toFixed(2)}` : '--';
    }

    const eqBreakout = document.getElementById('pane-eq-breakout');
    if (eqBreakout) {
      eqBreakout.textContent = shPlan.breakout_level ? `$${Number(shPlan.breakout_level).toFixed(2)}` : '--';
    }

    const eqT1 = document.getElementById('pane-eq-t1');
    if (eqT1) {
      eqT1.textContent = shPlan.target_1 ? `$${Number(shPlan.target_1).toFixed(2)}` : '--';
    }

    const eqT2 = document.getElementById('pane-eq-t2');
    if (eqT2) {
      eqT2.textContent = shPlan.target_2 ? `$${Number(shPlan.target_2).toFixed(2)}` : '--';
    }

    // 5. Invalidation
    const inv = wl.invalidation || {};
    const invCond = document.getElementById('pane-inv-cond');
    if (invCond) {
      const cond = inv.condition ? inv.condition.replace(/_/g, ' ') : 'DAILY CLOSE BELOW';
      const px = inv.price_level ? `$${Number(inv.price_level).toFixed(2)}` : (shPlan.tactical_stop ? `$${Number(shPlan.tactical_stop).toFixed(2)}` : '--');
      invCond.textContent = `Invalidation: ${cond} ${px}`;
    }

    const invRat = document.getElementById('pane-inv-rationale');
    if (invRat) {
      invRat.textContent = inv.rationale || 'Sustained breach terminates trade posture.';
    }
  },

  renderSuggestedPositionsFullTab(data, container) {
    const wl = data ? data.watch_levels : null;
    const ticker = data.ticker || 'STOCK';
    const date = data.date || '';
    if (!wl) {
      container.innerHTML = `
        <div style="padding:40px; text-align:center; color:var(--text-muted);">
          <div style="font-size:28px; margin-bottom:10px;">📋</div>
          <div style="font-size:14px; font-weight:700;">No structured suggested positions found for ${ticker} on ${date}.</div>
          <div style="font-size:12px; margin-top:6px;">Check the Senior PM Arbitration and Model A tabs for narrative execution notes.</div>
        </div>
      `;
      return;
    }

    const verdict = (wl.verdict || 'ENTER').toUpperCase();
    const conv = wl.conviction ? `${wl.conviction}/10` : '--';
    const status = (wl.status || 'STALKING').toUpperCase();
    const statusClass = (status === 'IN_ZONE' || status === 'IN_TRADE') ? 'pill green' : (status === 'INVALIDATED' ? 'pill red' : 'pill amber');
    const optPlan = wl.options_plan || {};
    const shPlan = wl.shares_plan || {};
    const inv = wl.invalidation || {};

    const livePx = data.live_price ? `$${Number(data.live_price).toFixed(2)}` : '--';
    const spotPx = data.spot_price ? `$${Number(data.spot_price).toFixed(2)}` : '--';

    container.innerHTML = `
      <div style="padding:22px; max-width:1050px; margin:0 auto; font-family:'Outfit',sans-serif;">
        <!-- Card Header -->
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:20px; padding-bottom:16px; border-bottom:1px solid var(--border);">
          <div>
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-size:20px; font-weight:800; color:var(--text-main);">$${ticker} SUGGESTED TRADE PLAN</span>
              <span class="badge in_zone" style="font-size:12px; font-weight:800; padding:2px 8px;">${verdict}</span>
              <span class="${statusClass}" style="font-size:11px; font-weight:800; padding:2px 8px;">${status}</span>
              <span style="font-size:12px; color:var(--text-muted); font-weight:600;">Conviction: <strong>${conv}</strong></span>
            </div>
            <div style="font-size:12px; color:var(--text-muted); margin-top:4px;">
              Report Date: <strong>${date}</strong> • Spot at Report: <strong>${spotPx}</strong> • Live Price: <strong style="color:#059669;">${livePx}</strong>
            </div>
          </div>
          <div style="display:flex; gap:8px;">
            <button class="btn" onclick="AppSwing.askCopilotExecutionPlan()" style="padding:6px 14px; font-size:12px; font-weight:800; background:#059669; color:#fff; border-color:#059669; cursor:pointer;">
              ⚡ Plan Execution with Copilot
            </button>
            <button class="btn secondary" onclick="AppSwing.switchDossierTab('tv')" style="padding:6px 14px; font-size:12px; font-weight:700; cursor:pointer;">
              📈 View on Live TV
            </button>
          </div>
        </div>



        <!-- 2-Column Grid: Options Vehicle vs Equity Vehicle -->
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap:18px; margin-bottom:20px;">
          
          <!-- Column 1: Options Vehicle (Primary Spread) -->
          <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:18px; display:flex; flex-direction:column; justify-content:space-between;">
            <div>
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                <span style="font-size:13px; font-weight:800; color:var(--blue); text-transform:uppercase; letter-spacing:0.5px;">
                  🎯 Options Structure (Primary Vehicle)
                </span>
                ${optPlan.actionable ? '<span class="pill green" style="font-size:10px; font-weight:800;">ACTIONABLE</span>' : ''}
              </div>

              <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:12px; margin-bottom:14px;">
                <div style="font-family:'JetBrains Mono',monospace; font-size:14px; font-weight:800; color:var(--text-main); margin-bottom:6px;">
                  ${optPlan.structure ? optPlan.structure.replace(/_/g, ' ') : 'DEFINED RISK STRUCTURE'}
                </div>
                <div style="font-size:12px; color:var(--text-muted); line-height:1.45;">
                  ${optPlan.summary || 'No detailed options narrative provided.'}
                </div>
              </div>

              <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; font-family:'JetBrains Mono',monospace; font-size:12px;">
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--text-muted); text-transform:uppercase; font-family:'Outfit',sans-serif;">Expiration</div>
                  <div style="font-weight:700; color:var(--text-main); margin-top:2px;">${optPlan.expiration || '--'}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--text-muted); text-transform:uppercase; font-family:'Outfit',sans-serif;">Entry Trigger</div>
                  <div style="font-weight:700; color:var(--text-main); margin-top:2px;">${optPlan.entry_trigger ? optPlan.entry_trigger.replace(/_/g, ' ') : 'AT MARKET'}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--text-muted); text-transform:uppercase; font-family:'Outfit',sans-serif;">Strikes (Long / Short)</div>
                  <div style="font-weight:700; color:var(--blue); margin-top:2px;">${optPlan.long_strike ? `$${optPlan.long_strike}` : '--'} / ${optPlan.short_strike ? `$${optPlan.short_strike}` : '--'}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--text-muted); text-transform:uppercase; font-family:'Outfit',sans-serif;">Target Net Debit</div>
                  <div style="font-weight:700; color:var(--text-main); margin-top:2px;">${optPlan.target_debit ? `~$${Number(optPlan.target_debit).toFixed(2)}` : '--'}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--rose); text-transform:uppercase; font-family:'Outfit',sans-serif;">Max Risk / Loss</div>
                  <div style="font-weight:800; color:var(--rose); margin-top:2px;">${optPlan.max_loss ? `$${Number(optPlan.max_loss).toLocaleString()}` : '--'}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--emerald); text-transform:uppercase; font-family:'Outfit',sans-serif;">Max Profit</div>
                  <div style="font-weight:800; color:var(--emerald); margin-top:2px;">${optPlan.max_profit ? `$${Number(optPlan.max_profit).toLocaleString()}` : '--'}</div>
                </div>
              </div>
            </div>
          </div>

          <!-- Column 2: Equity Shares Plan -->
          <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:18px; display:flex; flex-direction:column; justify-content:space-between;">
            <div>
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                <span style="font-size:13px; font-weight:800; color:var(--emerald); text-transform:uppercase; letter-spacing:0.5px;">
                  📊 Equity Shares Execution Plan
                </span>
                <span class="pill blue" style="font-size:10px; font-weight:800;">${shPlan.side || 'LONG'}</span>
              </div>

              <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:12px; margin-bottom:14px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                  <span style="font-size:12px; font-weight:700; color:var(--text-muted);">LIMIT ENTRY ZONE</span>
                  <span style="font-family:'JetBrains Mono',monospace; font-size:15px; font-weight:800; color:#059669;">
                    ${shPlan.entry_zone_low ? `$${Number(shPlan.entry_zone_low).toFixed(2)}` : '--'} – ${shPlan.entry_zone_high ? `$${Number(shPlan.entry_zone_high).toFixed(2)}` : '--'}
                  </span>
                </div>
              </div>

              <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; font-family:'JetBrains Mono',monospace; font-size:12px;">
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--rose); text-transform:uppercase; font-family:'Outfit',sans-serif;">Tactical Stop Loss</div>
                  <div style="font-weight:800; color:var(--rose); margin-top:2px;">${shPlan.tactical_stop ? `$${Number(shPlan.tactical_stop).toFixed(2)}` : '--'}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--blue); text-transform:uppercase; font-family:'Outfit',sans-serif;">Breakout Level</div>
                  <div style="font-weight:700; color:var(--blue); margin-top:2px;">${shPlan.breakout_level ? `$${Number(shPlan.breakout_level).toFixed(2)}` : '--'}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--emerald); text-transform:uppercase; font-family:'Outfit',sans-serif;">Target 1 (First Scale)</div>
                  <div style="font-weight:800; color:var(--emerald); margin-top:2px;">${shPlan.target_1 ? `$${Number(shPlan.target_1).toFixed(2)}` : '--'}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--emerald); text-transform:uppercase; font-family:'Outfit',sans-serif;">Target 2 (Runner)</div>
                  <div style="font-weight:800; color:var(--emerald); margin-top:2px;">${shPlan.target_2 ? `$${Number(shPlan.target_2).toFixed(2)}` : '--'}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Invalidation & Defense Rationale Box -->
        <div style="background:rgba(239, 68, 68, 0.04); border:1px solid rgba(239, 68, 68, 0.25); border-radius:8px; padding:14px 16px;">
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">
            <span style="font-size:16px;">🛑</span>
            <span style="font-size:12.5px; font-weight:800; color:var(--rose); text-transform:uppercase; letter-spacing:0.5px;">
              Thesis Invalidation Rule: ${inv.condition ? inv.condition.replace(/_/g, ' ') : 'DAILY CLOSE BELOW'} ${inv.price_level ? `$${Number(inv.price_level).toFixed(2)}` : (shPlan.tactical_stop ? `$${Number(shPlan.tactical_stop).toFixed(2)}` : '--')}
            </span>
          </div>
          <div style="font-size:12.5px; color:var(--text-main); line-height:1.5;">
            ${inv.rationale || 'A sustained break below structural support and key volume nodes terminates this trade posture immediately.'}
          </div>
        </div>

      </div>
    `;
  },

  askCopilotExecutionPlan() {
    const data = window.AppState.currentReportData;
    if (!data) return;
    const ticker = data.ticker || 'STOCK';
    const optPlan = data.watch_levels?.options_plan?.summary || 'the suggested position';
    if (window.AppChat && typeof window.AppChat.askActiveChat === 'function') {
      window.AppChat.askActiveChat(`What is the exact execution step for $${ticker} (${optPlan}) right now?`);
    }
  },

  switchReportDate(newDate) {
    const data = window.AppState.currentReportData;
    const ticker = data ? data.ticker : 'AAPL';
    if (newDate) {
      this.openReportModal(newDate, ticker);
    }
  },

  minimizeReportModal(event) {
    if (event) event.stopPropagation();
    const modal = document.getElementById('report-modal');
    const modalContent = modal ? modal.querySelector('.modal-content') : null;
    if (!modal || !modalContent) return;

    modal.classList.add('minimized');
    modal.classList.remove('collapsed');

    // Explicit inline floating chat window styles
    modal.style.background = 'transparent';
    modal.style.pointerEvents = 'none';
    modal.style.backdropFilter = 'none';
    modal.style.padding = '0';
    modal.style.display = 'flex';
    modal.style.alignItems = 'flex-end';
    modal.style.justifyContent = 'flex-end';
    modal.style.zIndex = '999999';

    modalContent.style.position = 'fixed';
    modalContent.style.bottom = '20px';
    modalContent.style.right = '24px';
    modalContent.style.width = '440px';
    modalContent.style.height = '580px';
    modalContent.style.maxHeight = 'calc(100vh - 40px)';
    modalContent.style.maxWidth = 'calc(100vw - 32px)';
    modalContent.style.pointerEvents = 'auto';
    modalContent.style.boxShadow = '0 16px 50px rgba(0, 0, 0, 0.9), 0 0 30px rgba(6, 182, 212, 0.4)';
    modalContent.style.border = '1.5px solid var(--cyan-glow)';
    modalContent.style.borderRadius = '12px';

    const reportPane = document.getElementById('modal-report-pane');
    const posPane = document.getElementById('modal-positions-pane');
    const resizer = document.getElementById('modal-chat-resizer');
    const copilotDrawer = document.getElementById('modal-copilot-drawer');
    if (reportPane) reportPane.style.display = 'none';
    if (posPane) posPane.style.display = 'none';
    if (resizer) resizer.style.display = 'none';
    if (copilotDrawer) {
      copilotDrawer.style.width = '100%';
      copilotDrawer.style.maxWidth = '100%';
      copilotDrawer.style.flex = '1';
    }

    const data = window.AppState.currentReportData;
    const ticker = data ? data.ticker : 'REPORT';
    const date = data ? data.date : '';

    const titleEl = document.getElementById('modal-ticker-title');
    if (titleEl) titleEl.innerText = `💬 ${ticker} Copilot (${date || 'Active'})`;
    const pulse = document.getElementById('modal-minimized-pulse');
    if (pulse) pulse.style.display = 'inline-block';

    const expandBtn = document.getElementById('btn-modal-expand-full');
    if (expandBtn) expandBtn.style.display = 'inline-flex';

    const minBtn = document.getElementById('btn-modal-min-toggle');
    if (minBtn) {
      minBtn.innerHTML = '<span style="font-size:12px;">➖</span> Collapse';
      minBtn.title = 'Collapse chat to compact bar';
    }
  },

  restoreReportModal(event) {
    if (event) event.stopPropagation();
    const modal = document.getElementById('report-modal');
    const modalContent = modal ? modal.querySelector('.modal-content') : null;
    if (!modal || !modalContent) return;

    modal.classList.remove('minimized');
    modal.classList.remove('collapsed');

    // Reset inline styles to return to full-screen 2-column modal
    modal.style.background = '';
    modal.style.pointerEvents = '';
    modal.style.backdropFilter = '';
    modal.style.padding = '';
    modal.style.display = 'flex';
    modal.style.alignItems = '';
    modal.style.justifyContent = '';
    modal.style.zIndex = '';

    modalContent.style.position = '';
    modalContent.style.bottom = '';
    modalContent.style.right = '';
    modalContent.style.width = '96vw';
    modalContent.style.maxWidth = '1480px';
    modalContent.style.height = '92vh';
    modalContent.style.maxHeight = '';
    modalContent.style.pointerEvents = '';
    modalContent.style.boxShadow = '';
    modalContent.style.border = '';
    modalContent.style.borderRadius = '';

    const reportPane = document.getElementById('modal-report-pane');
    const posPane = document.getElementById('modal-positions-pane');
    const resizer = document.getElementById('modal-chat-resizer');
    const copilotDrawer = document.getElementById('modal-copilot-drawer');
    if (reportPane) reportPane.style.display = 'flex';
    if (posPane) posPane.style.display = (window.AppState.showPositionsPane !== false) ? 'flex' : 'none';
    if (resizer) resizer.style.display = 'flex';
    if (copilotDrawer && window.AppChat) {
      const savedW = parseFloat(localStorage.getItem('modal_copilot_w')) || 420;
      window.AppChat.applyModalChatWidth(savedW);
    }

    const data = window.AppState.currentReportData;
    const ticker = data ? data.ticker : 'REPORT';
    const date = data ? data.date : '';

    const titleEl = document.getElementById('modal-ticker-title');
    if (titleEl) titleEl.innerText = `${ticker} RESEARCH REPORT (${date})`;
    const pulse = document.getElementById('modal-minimized-pulse');
    if (pulse) pulse.style.display = 'none';

    const expandBtn = document.getElementById('btn-modal-expand-full');
    if (expandBtn) expandBtn.style.display = 'none';

    const minBtn = document.getElementById('btn-modal-min-toggle');
    if (minBtn) {
      minBtn.innerHTML = '<span style="font-size:12px;">🗕</span> Float Chat';
      minBtn.title = 'Float as bottom-right chat window';
    }
    if (window.AppChat) {
      window.AppChat.initModalChatResize();
    }
  },

  toggleMinimizeReportModal(event) {
    if (event) event.stopPropagation();
    const modal = document.getElementById('report-modal');
    const modalContent = modal ? modal.querySelector('.modal-content') : null;
    if (!modal || !modalContent) return;

    if (modal.classList.contains('minimized')) {
      if (modal.classList.contains('collapsed')) {
        modal.classList.remove('collapsed');
        modalContent.style.height = '580px';
        const splitCont = document.getElementById('modal-split-container');
        if (splitCont) splitCont.style.display = 'flex';
        const minBtn = document.getElementById('btn-modal-min-toggle');
        if (minBtn) minBtn.innerHTML = '<span style="font-size:12px;">➖</span> Collapse';
      } else {
        modal.classList.add('collapsed');
        modalContent.style.height = '50px';
        const splitCont = document.getElementById('modal-split-container');
        if (splitCont) splitCont.style.display = 'none';
        const minBtn = document.getElementById('btn-modal-min-toggle');
        if (minBtn) minBtn.innerHTML = '<span style="font-size:12px;">💬</span> Open Chat';
      }
    } else {
      this.minimizeReportModal(event);
    }
  },

  closeReportModal(event) {
    if (event) event.stopPropagation();
    const modal = document.getElementById('report-modal');
    const modalContent = modal ? modal.querySelector('.modal-content') : null;
    if (modal) {
      if (this._modalLivePricePollTimer) {
        clearInterval(this._modalLivePricePollTimer);
        this._modalLivePricePollTimer = null;
      }
      modal.classList.remove('minimized');
      modal.classList.remove('collapsed');
      modal.style.display = 'none';
      if (modalContent) {
        modalContent.style.position = '';
        modalContent.style.bottom = '';
        modalContent.style.right = '';
        modalContent.style.width = '96vw';
        modalContent.style.maxWidth = '1480px';
        modalContent.style.height = '92vh';
      }
    }
  },

  switchDossierTab(tab) {
    window.AppState.activeDossierTab = tab;
    ['plan', 'arb', 'sum', 'ind', 'history', 'chart', 'tv', 'flow'].forEach(t => {
      const btn = document.getElementById(`tab-${t}-btn`);
      if (btn) btn.className = `tab-btn ${t === tab ? 'active' : ''}`;
    });

    const bodyEl = document.getElementById('modal-body');
    const data = window.AppState.currentReportData;
    if (!bodyEl || !data) return;

    if (tab === 'plan') {
      this.renderSuggestedPositionsFullTab(data, bodyEl);
    } else if (tab === 'arb') {
      bodyEl.innerHTML = data.arbitration_md
        ? window.AppUtils.renderMarkdown(data.arbitration_md)
        : '<em>No arbitration report found.</em>';
    } else if (tab === 'sum') {
      bodyEl.innerHTML = data.summary_md
        ? window.AppUtils.renderMarkdown(data.summary_md)
        : '<em>No synthesis summary found.</em>';
    } else if (tab === 'ind') {
      bodyEl.innerHTML = data.independent_md
        ? window.AppUtils.renderMarkdown(data.independent_md)
        : '<em>No independent report found.</em>';
    } else if (tab === 'history') {
      const timeline = data.historical_timeline || [];
      const ticker = data.ticker || 'STOCK';
      const curDate = data.date;

      const rows = timeline.map(item => {
        const isCurrent = (item.date === curDate);
        const spotStr = item.spot ? `$${Number(item.spot).toFixed(2)}` : '--';
        const vClass = (window.AppUtils && typeof window.AppUtils.getVerdictBadgeClass === 'function')
          ? window.AppUtils.getVerdictBadgeClass(item.verdict)
          : '';
        const vStyle = vClass === 'in_zone'
          ? 'color:#065f46; border-color:#a7f3d0; background:#ecfdf5;'
          : (vClass === 'danger'
            ? 'color:#991b1b; border-color:#fecaca; background:#fef2f2;'
            : (vClass === 'stalking'
              ? 'color:#92400e; border-color:#fde68a; background:#fffbeb;'
              : 'color:#1d4ed8; border-color:#bfdbfe; background:#eff6ff;'));
        
        const rowBg = isCurrent ? 'background:#eff6ff;' : '';
        const badge = isCurrent ? '<span class="pill blue" style="font-size:9.5px; padding:1px 5px; margin-left:4px;">CURRENT</span>' : '';

        return `
          <tr style="${rowBg}">
            <td style="font-family:'JetBrains Mono',monospace; font-weight:700; white-space:nowrap;">
              <span style="color:var(--text-main); font-size:13px;">${item.date}</span> ${badge}
            </td>
            <td style="font-family:'JetBrains Mono',monospace; color:var(--text-main); font-weight:800; font-size:13px;">
              ${spotStr}
            </td>
            <td>
              <span class="pill ${vClass}" style="font-size:10.5px; padding:3px 8px; font-weight:700; white-space:nowrap; ${vStyle}">${item.verdict}</span>
            </td>
            <td style="font-size:12px; color:var(--text-muted); line-height:1.4;">
              ${item.preview}
            </td>
            <td>
              <button class="btn secondary" onclick="AppSwing.switchReportDate('${item.date}')" style="padding:3px 9px; font-size:11px; white-space:nowrap; ${isCurrent ? 'opacity:0.5;' : ''}" ${isCurrent ? 'disabled' : ''}>
                ${isCurrent ? 'Active Date' : '📖 View Report'}
              </button>
            </td>
          </tr>
        `;
      }).join('');

      bodyEl.innerHTML = `
        <div style="padding:10px 4px;">
          <!-- Thesis Evolution Header Banner -->
          <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:16px 20px; margin-bottom:20px;">
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; flex-wrap:wrap; gap:8px;">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:18px;">📜</span>
                <span style="font-family:'Outfit',sans-serif; font-size:16px; font-weight:800; color:var(--text-main);">${ticker} Research History & Thesis Drift</span>
                <span class="pill blue" style="font-size:11px; padding:2px 8px;">${timeline.length} Total Dates</span>
              </div>
              <button class="btn secondary" onclick="AppChat.sendModalCopilotMsg('Compare today to prior research dates for ${ticker}. What changed, what was the price then vs now, and was the earlier analysis right or wrong?')" style="font-size:11.5px; padding:5px 12px; cursor:pointer;">
                💬 Ask Copilot: What Changed & Was the AI Right?
              </button>
            </div>
            <p style="font-size:12.5px; color:var(--text-muted); margin:0; line-height:1.5;">
              Every research run, trade decision, and price milestone is permanently indexed in the filesystem. 
              Click any date row below to inspect what the AI concluded when the stock was at earlier price levels (e.g. at compression base vs current expansion).
            </p>
          </div>

          <!-- Chronological Research Archive Table -->
          <div class="watchlist-table-wrap">
            <table class="watchlist-table">
              <thead>
                <tr>
                  <th style="width:160px;">RESEARCH DATE</th>
                  <th style="width:120px;">SPOT PRICE</th>
                  <th style="width:160px;">VERDICT</th>
                  <th>THESIS HIGHLIGHTS & SETUP SUMMARY</th>
                  <th style="width:130px;">ACTIONS</th>
                </tr>
              </thead>
              <tbody>
                ${rows}
              </tbody>
            </table>
          </div>
        </div>
      `;
    } else if (tab === 'chart') {
      let html = '';
      if (data.zoom_chart_url) {
        html += `<h3>90-Day Zoomed Chart</h3><img src="${data.zoom_chart_url}" class="chart-img" alt="Zoom Chart"/>`;
      }
      if (data.plain_chart_url) {
        html += `<h3>Plain Multi-Year Chart</h3><img src="${data.plain_chart_url}" class="chart-img" alt="Plain Chart"/>`;
      }
      if (!html) html = '<em>No chart images available for this ticker.</em>';
      bodyEl.innerHTML = html;
    } else if (tab === 'tv') {
      const sym = (data.ticker || 'META').toUpperCase();
      const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      const interval = this._tvInterval || 'D';
      bodyEl.innerHTML = `
        <div style="height:100%; display:flex; flex-direction:column; min-height:600px; padding:0;">
          <div style="display:flex; align-items:center; justify-content:space-between; padding:8px 14px; background:var(--bg-subtle); border-bottom:1px solid var(--border); flex-shrink:0;">
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-weight:800; font-size:13px; color:var(--text-main);">📈 Real-Time TradingView (${sym})</span>
              <span class="pill green" style="font-size:9.5px; padding:1px 6px;"><span class="dot pulse"></span>Interactive Stream</span>
            </div>
            <div style="display:flex; align-items:center; gap:5px;">
              <button class="btn secondary ${interval==='D'?'active':''}" onclick="AppSwing.setTradingViewInterval('D')" style="padding:2px 8px; font-size:11px; font-weight:700;">1D</button>
              <button class="btn secondary ${interval==='60'?'active':''}" onclick="AppSwing.setTradingViewInterval('60')" style="padding:2px 8px; font-size:11px; font-weight:700;">1H</button>
              <button class="btn secondary ${interval==='15'?'active':''}" onclick="AppSwing.setTradingViewInterval('15')" style="padding:2px 8px; font-size:11px; font-weight:700;">15m</button>
              <button class="btn secondary ${interval==='5'?'active':''}" onclick="AppSwing.setTradingViewInterval('5')" style="padding:2px 8px; font-size:11px; font-weight:700;">5m</button>
              <button class="btn secondary ${interval==='W'?'active':''}" onclick="AppSwing.setTradingViewInterval('W')" style="padding:2px 8px; font-size:11px; font-weight:700;">1W</button>
              <a href="https://www.tradingview.com/chart/jPAQSlZC/?symbol=${encodeURIComponent(sym)}&interval=D" target="_blank" class="btn secondary" style="padding:2px 10px; font-size:11px; font-weight:700; color:var(--blue); text-decoration:none; margin-left:6px; display:inline-flex; align-items:center; gap:4px;" title="Open in your logged-in TradingView session with the Rev - Enhanced v2 layout">
                🚀 Open Personal Layout (All Indicators) ↗
              </a>
            </div>
          </div>
          <div style="flex:1; width:100%; min-height:550px; background:var(--bg-surface); position:relative;">
            <iframe id="tradingview-embed-frame"
              src="https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(sym)}&interval=${encodeURIComponent(interval)}&hidesidetoolbar=0&symboledit=1&saveimage=1&toolbarbg=f1f3f6&studies=%5B%5D&theme=${theme}&style=1&timezone=America%2FNew_York"
              style="width:100%; height:100%; min-height:550px; border:none;"
              allowtransparency="true"
              scrolling="no">
            </iframe>
          </div>
        </div>
      `;
    } else if (tab === 'flow') {
      this.renderOptionsFlowTab(data.ticker);
    }
  },

  _tvInterval: 'D',

  setTradingViewInterval(interval) {
    this._tvInterval = interval;
    const frame = document.getElementById('tradingview-embed-frame');
    const data = window.AppState.currentReportData;
    if (frame && data) {
      const sym = (data.ticker || 'META').toUpperCase();
      const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      frame.src = `https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(sym)}&interval=${encodeURIComponent(interval)}&hidesidetoolbar=0&symboledit=1&saveimage=1&toolbarbg=f1f3f6&studies=%5B%5D&theme=${theme}&style=1&timezone=America%2FNew_York`;
    }
    this.switchDossierTab('tv');
  },

  _modalLivePricePollTimer: null,

  async refreshModalLivePrice(explicitTicker = null, presetPrice = null) {
    const priceValEl = document.getElementById('modal-live-price-val');
    if (!priceValEl) return;

    const data = window.AppState.currentReportData;
    const ticker = (explicitTicker || (data ? data.ticker : '')).toUpperCase();
    if (!ticker) return;

    // 1. If presetPrice provided, show immediately
    if (presetPrice !== null && presetPrice !== undefined) {
      this._updateModalPriceUI(presetPrice, data);
      return;
    }

    // 2. Fetch fresh quote from /api/quote/{ticker}
    try {
      const q = await window.AppApi.getTickerQuote(ticker);
      if (q && q.price) {
        this._updateModalPriceUI(q.price, data);
      }
    } catch (e) {
      console.warn('Modal live price fetch error:', e);
    }
  },

  _updateModalPriceUI(livePrice, data) {
    const priceValEl = document.getElementById('modal-live-price-val');
    const diffEl = document.getElementById('modal-live-spot-diff');
    if (!priceValEl) return;

    const num = Number(livePrice);
    if (isNaN(num) || num <= 0) return;

    priceValEl.innerText = `$${num.toFixed(2)}`;

    // Calculate diff vs research date spot price
    let spot = null;
    if (data && data.historical_timeline) {
      const curItem = data.historical_timeline.find(x => x.date === data.date);
      if (curItem && curItem.spot) spot = Number(curItem.spot);
    }
    if (!spot && data && data.spot) spot = Number(data.spot);

    if (spot && diffEl) {
      const diff = num - spot;
      const diffPct = (diff / spot) * 100;
      const sign = diff >= 0 ? '+' : '';
      const color = diff >= 0 ? '#059669' : '#dc2626';
      diffEl.innerHTML = `<span style="color:${color}; font-weight:700;">(${sign}$${diff.toFixed(2)} / ${sign}${diffPct.toFixed(2)}% vs Spot $${spot.toFixed(2)})</span>`;
    } else if (diffEl) {
      diffEl.innerHTML = '';
    }
  },

  _optionsFlowFilter: 'ALL',

  async renderOptionsFlowTab(ticker, forceRefresh = false) {
    const bodyEl = document.getElementById('modal-body');
    if (!bodyEl) return;
    const sym = (ticker || 'STOCK').toUpperCase();

    bodyEl.innerHTML = `
      <div style="padding:40px; text-align:center; color:var(--text-muted);">
        <span class="dot pulse" style="background:#f59e0b; margin-right:6px;"></span>
        <strong>Querying Schwab API for 90-day institutional sweeps on ${sym}...</strong>
      </div>
    `;

    try {
      const data = await window.AppApi.getOptionsFlow(sym, forceRefresh);
      if (!data || data.status === 'error') {
        bodyEl.innerHTML = `
          <div style="padding:20px; background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px;">
            <div style="font-weight:700; color:#dc2626; margin-bottom:8px;">⚠️ Options Flow Notice</div>
            <p style="font-size:13px; color:var(--text-muted); line-height:1.5;">${data ? data.error : 'Could not load options flow.'}</p>
            <button class="btn secondary" onclick="AppSwing.renderOptionsFlowTab('${sym}', true)" style="margin-top:10px; font-weight:700;">🔄 Retry Schwab Query</button>
          </div>
        `;
        return;
      }

      window.AppState.currentOptionsFlowData = data;
      this._drawOptionsFlowUI(data);

    } catch (e) {
      bodyEl.innerHTML = `
        <div style="padding:20px; color:#dc2626;">
          <strong>Error fetching Schwab options flow:</strong> ${e.message}
        </div>
      `;
    }
  },

  setOptionsFlowFilter(filter) {
    this._optionsFlowFilter = filter;
    const data = window.AppState.currentOptionsFlowData;
    if (data) this._drawOptionsFlowUI(data);
  },

  _drawOptionsFlowUI(data) {
    const bodyEl = document.getElementById('modal-body');
    if (!bodyEl || !data) return;

    const sym = data.ticker;
    const spot = data.underlying_price ? `$${Number(data.underlying_price).toFixed(2)}` : '--';
    const totalAnomalies = data.total_anomalies_count || 0;
    const callPremM = ((data.total_call_premium || 0) / 1e6).toFixed(2);
    const putPremM = ((data.total_put_premium || 0) / 1e6).toFixed(2);
    const callVolK = ((data.total_call_volume || 0) / 1000).toFixed(1);
    const putVolK = ((data.total_put_volume || 0) / 1000).toFixed(1);

    const isBullish = data.sentiment === 'BULLISH_SWEEPS';
    const isBearish = data.sentiment === 'BEARISH_SWEEPS';
    const sentColor = isBullish ? '#059669' : (isBearish ? '#dc2626' : '#d97706');
    const sentBg = isBullish ? '#ecfdf5' : (isBearish ? '#fef2f2' : '#fffbeb');
    const sentBorder = isBullish ? '#a7f3d0' : (isBearish ? '#fecaca' : '#fde68a');

    const filter = this._optionsFlowFilter || 'ALL';
    let filteredList = data.anomalies || [];
    if (filter === 'CALL') filteredList = filteredList.filter(a => a.type === 'CALL');
    if (filter === 'PUT') filteredList = filteredList.filter(a => a.type === 'PUT');

    const rows = filteredList.map(a => {
      const isCall = a.type === 'CALL';
      const typeClass = isCall ? 'pill green' : 'pill red';
      const premStr = a.notional_premium >= 1e6
        ? `$${(a.notional_premium / 1e6).toFixed(2)}M`
        : `$${Math.round(a.notional_premium / 1000)}k`;

      const oiSpikeBadge = a.vol_to_oi >= 10
        ? `<span class="pill amber" style="font-size:9.5px; padding:1px 5px; font-weight:800;">${a.vol_to_oi}x OI 🔥</span>`
        : `<span style="font-weight:700; color:var(--text-main); font-size:12px;">${a.vol_to_oi}x</span>`;

      const distBadge = a.distance_from_spot_pct
        ? `<span style="font-size:11px; color:var(--text-muted); font-family:'JetBrains Mono',monospace;">${a.distance_from_spot_pct > 0 ? '+' : ''}${a.distance_from_spot_pct}%</span>`
        : '';

      return `
        <tr>
          <td><span class="${typeClass}" style="font-weight:800; font-size:11px; padding:2px 8px;">${a.type}</span></td>
          <td style="font-family:'JetBrains Mono',monospace; font-weight:800; font-size:13px; color:var(--text-main);">
            $${Number(a.strike).toFixed(1)} ${distBadge}
          </td>
          <td style="font-family:'JetBrains Mono',monospace; font-weight:600; font-size:12px;">${a.expiry}</td>
          <td style="font-size:12px; color:var(--text-muted);">${a.dte}d</td>
          <td style="font-family:'JetBrains Mono',monospace; font-weight:800; color:var(--text-main); font-size:13px;">
            ${Number(a.volume).toLocaleString()}
          </td>
          <td style="font-family:'JetBrains Mono',monospace; color:var(--text-muted); font-size:12px;">
            ${Number(a.open_interest).toLocaleString()}
          </td>
          <td>${oiSpikeBadge}</td>
          <td style="font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--text-muted);">${a.delta}</td>
          <td style="font-family:'JetBrains Mono',monospace; font-weight:700; font-size:12.5px;">$${Number(a.mid).toFixed(2)}</td>
          <td style="font-family:'JetBrains Mono',monospace; font-weight:800; color:${isCall ? '#059669' : '#dc2626'}; font-size:13px;">
            ${premStr}
          </td>
        </tr>
      `;
    }).join('');

    bodyEl.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:16px; padding:4px;">
        
        <!-- Header Banner -->
        <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:16px 20px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px;">
          <div>
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:4px;">
              <span style="font-size:18px;">🔥</span>
              <span style="font-family:'Outfit',sans-serif; font-size:16px; font-weight:800; color:var(--text-main);">Schwab Institutional Options Flow (${sym})</span>
              <span class="pill green" style="font-size:9.5px; padding:1px 6px;">Live API Feed</span>
            </div>
            <div style="font-size:12px; color:var(--text-muted); display:flex; align-items:center; gap:12px;">
              <span>Spot Price: <strong style="color:var(--text-main);">${spot}</strong></span>
              <span>•</span>
              <span>Updated: ${data.cached_at || 'Live'}</span>
              <span>•</span>
              <span>Threshold: Vol &gt; 1.5× OI &amp; Vol &ge; 500</span>
            </div>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="padding:4px 12px; border-radius:6px; font-size:12px; font-weight:800; color:${sentColor}; background:${sentBg}; border:1px solid ${sentBorder};">
              ${data.sentiment_label}
            </span>
            <button class="btn secondary" onclick="AppSwing.renderOptionsFlowTab('${sym}', true)" style="font-weight:700; font-size:11.5px; padding:5px 12px;">
              🔄 Refresh Flow
            </button>
          </div>
        </div>

        <!-- 4 KPI Summary Metric Cards -->
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:12px;">
          <div class="stat-card" style="padding:14px; background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px;">
            <div style="font-size:11px; font-weight:700; color:var(--text-muted); margin-bottom:4px;">TOTAL SWEEPS DETECTED</div>
            <div style="font-size:22px; font-weight:800; color:var(--text-main); font-family:'Outfit',sans-serif;">${totalAnomalies}</div>
            <div style="font-size:11.5px; color:var(--text-muted); margin-top:2px;">${data.call_sweeps_count} Calls • ${data.put_sweeps_count} Puts</div>
          </div>

          <div class="stat-card" style="padding:14px; background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px;">
            <div style="font-size:11px; font-weight:700; color:#059669; margin-bottom:4px;">CALL SWEEPS VOLUME</div>
            <div style="font-size:22px; font-weight:800; color:#059669; font-family:'Outfit',sans-serif;">${callVolK}k <span style="font-size:13px; font-weight:600;">contracts</span></div>
            <div style="font-size:11.5px; color:var(--text-muted); margin-top:2px;">Est. Premium: <strong>$${callPremM}M</strong></div>
          </div>

          <div class="stat-card" style="padding:14px; background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px;">
            <div style="font-size:11px; font-weight:700; color:#dc2626; margin-bottom:4px;">PUT SWEEPS VOLUME</div>
            <div style="font-size:22px; font-weight:800; color:#dc2626; font-family:'Outfit',sans-serif;">${putVolK}k <span style="font-size:13px; font-weight:600;">contracts</span></div>
            <div style="font-size:11.5px; color:var(--text-muted); margin-top:2px;">Est. Premium: <strong>$${putPremM}M</strong></div>
          </div>

          <div class="stat-card" style="padding:14px; background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px;">
            <div style="font-size:11px; font-weight:700; color:var(--text-muted); margin-bottom:4px;">PUT / CALL FLOW RATIO</div>
            <div style="font-size:22px; font-weight:800; color:var(--text-main); font-family:'Outfit',sans-serif;">${data.put_call_volume_ratio}x</div>
            <div style="font-size:11.5px; color:var(--text-muted); margin-top:2px;">Premium Ratio: <strong>${data.put_call_premium_ratio}x</strong></div>
          </div>
        </div>

        <!-- Filter Buttons & Controls -->
        <div style="display:flex; align-items:center; justify-content:space-between; margin-top:4px;">
          <div style="display:flex; align-items:center; gap:6px;">
            <button class="btn secondary ${filter === 'ALL' ? 'active' : ''}" onclick="AppSwing.setOptionsFlowFilter('ALL')" style="padding:4px 12px; font-size:11.5px; font-weight:700;">
              All Sweeps (${totalAnomalies})
            </button>
            <button class="btn secondary ${filter === 'CALL' ? 'active' : ''}" onclick="AppSwing.setOptionsFlowFilter('CALL')" style="padding:4px 12px; font-size:11.5px; font-weight:700; color:#059669;">
              Calls (${data.call_sweeps_count})
            </button>
            <button class="btn secondary ${filter === 'PUT' ? 'active' : ''}" onclick="AppSwing.setOptionsFlowFilter('PUT')" style="padding:4px 12px; font-size:11.5px; font-weight:700; color:#dc2626;">
              Puts (${data.put_sweeps_count})
            </button>
          </div>
          <div style="font-size:11.5px; color:var(--text-muted);">
            Showing top institutional block orders sorted by daily volume
          </div>
        </div>

        <!-- Institutional Flow Table -->
        <div class="watchlist-table-wrap" style="border:1px solid var(--border); border-radius:8px; overflow:hidden;">
          <table class="watchlist-table">
            <thead>
              <tr>
                <th style="width:70px;">TYPE</th>
                <th style="width:140px;">STRIKE</th>
                <th style="width:110px;">EXPIRATION</th>
                <th style="width:60px;">DTE</th>
                <th style="width:110px;">VOLUME</th>
                <th style="width:100px;">OPEN INT</th>
                <th style="width:100px;">VOL / OI</th>
                <th style="width:70px;">DELTA</th>
                <th style="width:90px;">MID PRICE</th>
                <th style="width:120px;">EST. NOTIONAL</th>
              </tr>
            </thead>
            <tbody>
              ${rows || `<tr><td colspan="10" style="text-align:center; padding:32px 16px; color:var(--text-muted); font-size:13px;">
                <div style="font-size:24px; margin-bottom:8px;">🏖️</div>
                <strong style="color:var(--text-main); font-size:14px;">No Unusual Institutional Sweeps Detected</strong>
                <p style="margin-top:6px; font-size:12px; color:var(--text-muted); max-width:480px; margin-left:auto; margin-right:auto;">
                  Daily options volume across all 90-day strikes on <strong>${sym}</strong> is currently within the normal baseline (under 1.5× Open Interest or under 500 contracts). Smart money has not initiated large directional block positioning today.
                </p>
              </td></tr>`}
            </tbody>
          </table>
        </div>

      </div>
    `;
  },

  _cachedCalibrationData: null,
  _lastCalibrationJson: null,
  _isForecastsDrawerOpen: false,

  async loadCalibrationScoreboard() {
    const container = document.getElementById('calibration-scoreboard-widget');
    if (!container) return;
    try {
      const res = await fetch('/api/superforecasting/stats');
      if (!res.ok) {
        if (!container.innerHTML.trim()) {
          container.innerHTML = `
            <div style="color:var(--text-muted); font-size:12px; padding:10px; display:flex; align-items:center; justify-content:space-between;">
              <span>⚠️ Calibration data currently unavailable (HTTP ${res.status}).</span>
              <button class="btn secondary" onclick="AppSwing.loadCalibrationScoreboard()" style="padding:2px 8px; font-size:11px;">Retry</button>
            </div>
          `;
        }
        return;
      }
      const data = await res.json();
      const newJson = JSON.stringify(data);
      // Skip DOM rebuild if data is completely unchanged and widget is already populated
      if (newJson === this._lastCalibrationJson && container.children.length > 0) {
        return;
      }
      this._lastCalibrationJson = newJson;
      this.renderCalibrationScoreboard(data);
    } catch (e) {
      console.warn('Failed to load calibration stats:', e);
      if (!container.innerHTML.trim()) {
        container.innerHTML = `
          <div style="color:var(--text-muted); font-size:12px; padding:10px; display:flex; align-items:center; justify-content:space-between;">
            <span>⚠️ Connection error loading scoreboard.</span>
            <button class="btn secondary" onclick="AppSwing.loadCalibrationScoreboard()" style="padding:2px 8px; font-size:11px;">Retry</button>
          </div>
        `;
      }
    }
  },

  renderCalibrationScoreboard(data) {
    const container = document.getElementById('calibration-scoreboard-widget');
    if (!container) return;
    this._cachedCalibrationData = data;

    // Check if forecasts drawer was open before re-render, and preserve scroll position
    const existingDrawer = document.getElementById('forecasts-ledger-drawer');
    const wasOpen = Boolean(this._isForecastsDrawerOpen || (existingDrawer && existingDrawer.style.display === 'block'));
    this._isForecastsDrawerOpen = wasOpen;
    const prevScrollTop = (existingDrawer && wasOpen) ? existingDrawer.scrollTop : 0;

    const a = data.model_a || { total: 0, resolved: 0, brier_score: 0.0, accuracy_pct: 0.0 };
    const b = data.model_b || { total: 0, resolved: 0, brier_score: 0.0, accuracy_pct: 0.0 };

    const getModelBadge = (score, resolved, total) => {
      if (!resolved || resolved === 0) {
        return `<span class="pill cyan" style="font-weight:600;">⏳ ${total} Active (Pending Maturity)</span>`;
      }
      if (resolved < 5) {
        return `<span class="pill amber" style="font-weight:600;">🧪 Early Sample (${resolved}/${total} Matured)</span>`;
      }
      if (score <= 0.15) return `<span class="pill green" style="font-weight:700;">⭐ Brier: ${score} (Superforecaster)</span>`;
      if (score <= 0.25) return `<span class="pill cyan" style="font-weight:700;">✅ Brier: ${score} (Well-Calibrated)</span>`;
      return `<span class="pill amber" style="font-weight:700;">⚠️ Brier: ${score} (High Variance)</span>`;
    };

    const getHitRateDisplay = (resolved, accuracy_pct) => {
      if (!resolved || resolved === 0) {
        return `<div style="font-size:12px; font-weight:600; color:var(--text-muted); padding-top:4px;" title="Forecast horizons have not reached expiration date yet.">Pending</div>`;
      }
      return `<div style="font-size:16px; font-weight:800; color:${accuracy_pct > 0 ? '#4ade80' : 'var(--amber-light)'};">${accuracy_pct}%</div>`;
    };

    container.innerHTML = `
      <!-- Model A (Pine) Card -->
      <div class="calibration-model-card" style="background:var(--bg-surface); border:1px solid var(--border); border-radius:8px; padding:12px 16px; box-shadow:var(--shadow-card);">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
          <div>
            <span style="font-weight:700; font-size:13px; color:var(--blue);">🌲 MODEL A (Proprietary Pine)</span>
            <span style="font-size:11px; color:var(--text-muted); margin-left:6px;">Pine Multi-Indicator Thesis</span>
          </div>
          ${getModelBadge(a.brier_score, a.resolved, a.total)}
        </div>
        <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:10px; text-align:center;">
          <div style="background:var(--bg-subtle); border:1px solid var(--border); padding:8px 6px; border-radius:6px;">
            <div style="font-size:11px; color:var(--text-muted); margin-bottom:2px;">Forward Bets</div>
            <div style="font-size:16px; font-weight:800; color:var(--text-main);">${a.total}</div>
          </div>
          <div style="background:var(--bg-subtle); border:1px solid var(--border); padding:8px 6px; border-radius:6px;">
            <div style="font-size:11px; color:var(--text-muted); margin-bottom:2px;">Matured</div>
            <div style="font-size:16px; font-weight:800; color:var(--blue);">${a.resolved}</div>
          </div>
          <div style="background:var(--bg-subtle); border:1px solid var(--border); padding:8px 6px; border-radius:6px;">
            <div style="font-size:11px; color:var(--text-muted); margin-bottom:2px;">Hit Rate</div>
            ${getHitRateDisplay(a.resolved, a.accuracy_pct)}
          </div>
        </div>
      </div>

      <!-- Model B (Independent Quant) Card -->
      <div class="calibration-model-card" style="background:var(--bg-surface); border:1px solid var(--border); border-radius:8px; padding:12px 16px; box-shadow:var(--shadow-card);">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
          <div>
            <span style="font-weight:700; font-size:13px; color:var(--violet);">📐 MODEL B (Independent Quant)</span>
            <span style="font-size:11px; color:var(--text-muted); margin-left:6px;">Pure OHLC + GEX + Conformal</span>
          </div>
          ${getModelBadge(b.brier_score, b.resolved, b.total)}
        </div>
        <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:10px; text-align:center;">
          <div style="background:var(--bg-subtle); border:1px solid var(--border); padding:8px 6px; border-radius:6px;">
            <div style="font-size:11px; color:var(--text-muted); margin-bottom:2px;">Forward Bets</div>
            <div style="font-size:16px; font-weight:800; color:var(--text-main);">${b.total}</div>
          </div>
          <div style="background:var(--bg-subtle); border:1px solid var(--border); padding:8px 6px; border-radius:6px;">
            <div style="font-size:11px; color:var(--text-muted); margin-bottom:2px;">Matured</div>
            <div style="font-size:16px; font-weight:800; color:var(--violet);">${b.resolved}</div>
          </div>
          <div style="background:var(--bg-subtle); border:1px solid var(--border); padding:8px 6px; border-radius:6px;">
            <div style="font-size:11px; color:var(--text-muted); margin-bottom:2px;">Hit Rate</div>
            ${getHitRateDisplay(b.resolved, b.accuracy_pct)}
          </div>
        </div>
      </div>

      <!-- Explainer & Ledger Drawer -->
      <div style="grid-column: 1 / -1; background:var(--bg-subtle); border:1px dashed var(--border); border-radius:8px; padding:10px 14px; display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:10px;">
        <div style="font-size:12px; color:var(--text-muted); line-height:1.5; flex:1; min-width:280px;">
          ℹ️ <strong>What is this?</strong> Both models generate falsifiable forward probability forecasts (e.g. 14d, 30d, 60d price targets or stop levels). Because holding horizons mature in the future, predictions remain <em>Pending</em> until their target dates expire.
        </div>
        <button class="btn secondary" onclick="AppSwing.toggleForecastsList()" id="btn-toggle-forecasts" style="padding:5px 12px; font-size:11.5px; white-space:nowrap;">
          ${wasOpen ? '✕ Close Forecasts Ledger' : `👁️ View Active Forecasts Ledger (${(data.recent_audits || []).length})`}
        </button>
      </div>

      <!-- Expandable Forecasts Drawer -->
      <div id="forecasts-ledger-drawer" style="grid-column: 1 / -1; display:${wasOpen ? 'block' : 'none'}; background:var(--bg-surface); border:1px solid var(--border); border-radius:8px; padding:14px; max-height:360px; overflow-y:auto; box-shadow:var(--shadow-card);">
        <div style="font-size:12px; font-weight:700; color:var(--text-main); margin-bottom:8px; display:flex; justify-content:space-between;">
          <span>📋 TRACKED PROBABILITY FORECASTS</span>
          <span style="font-size:11px; color:var(--text-muted);">Audited daily at market close</span>
        </div>
        <table style="width:100%; font-size:11.5px; border-collapse:collapse; text-align:left;">
          <thead>
            <tr style="border-bottom:1px solid var(--border); color:var(--text-muted);">
              <th style="padding:6px;">Ticker</th>
              <th style="padding:6px;">Model</th>
              <th style="padding:6px;">Horizon</th>
              <th style="padding:6px;">Target Date</th>
              <th style="padding:6px;">Event Forecast</th>
              <th style="padding:6px;">Probability</th>
              <th style="padding:6px;">Outcome</th>
            </tr>
          </thead>
          <tbody>
            ${(data.recent_audits || []).map(f => {
              const modelBadge = f.model_type.includes('PINE') 
                ? '<span class="pill cyan" style="font-size:10px;">Model A</span>' 
                : '<span class="pill purple" style="font-size:10px;">Model B</span>';
              let outcomeBadge = '<span class="pill muted" style="font-size:10px;">⏳ Pending</span>';
              if (f.actual_outcome === 1) outcomeBadge = '<span class="pill green" style="font-size:10px;">✅ Occurred</span>';
              else if (f.actual_outcome === 0) outcomeBadge = '<span class="pill red" style="font-size:10px;">❌ Not Met</span>';

              return `
                <tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
                  <td style="padding:6px; font-weight:700; color:#38bdf8;">${f.ticker}</td>
                  <td style="padding:6px;">${modelBadge}</td>
                  <td style="padding:6px;">${f.horizon_days}d</td>
                  <td style="padding:6px; font-family:monospace;">${f.target_date}</td>
                  <td style="padding:6px; color:#e2e8f0; max-width:320px;">${f.event_description}</td>
                  <td style="padding:6px; font-weight:700; color:#fbbf24;">${Math.round(f.predicted_probability * 100)}%</td>
                  <td style="padding:6px;">${outcomeBadge}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      </div>
    `;

    // Restore previous scroll position if drawer was previously open
    if (wasOpen) {
      const newDrawer = document.getElementById('forecasts-ledger-drawer');
      if (newDrawer && prevScrollTop > 0) {
        newDrawer.scrollTop = prevScrollTop;
      }
    }
  },

  toggleForecastsList() {
    const drawer = document.getElementById('forecasts-ledger-drawer');
    const btn = document.getElementById('btn-toggle-forecasts');
    if (!drawer) return;
    const isOpening = (drawer.style.display === 'none' || !drawer.style.display);
    this._isForecastsDrawerOpen = isOpening;
    if (isOpening) {
      drawer.style.display = 'block';
      if (btn) btn.innerText = '✕ Close Forecasts Ledger';
    } else {
      drawer.style.display = 'none';
      if (btn) {
        const count = (this._cachedCalibrationData?.recent_audits || []).length;
        btn.innerText = `👁️ View Active Forecasts Ledger (${count})`;
      }
    }
  },

  async triggerCalibrationAudit() {
    const pill = document.getElementById('calibration-status-pill');
    if (pill) pill.innerText = 'Auditing...';
    try {
      const res = await fetch('/api/superforecasting/audit', { method: 'POST' });
      const data = await res.json();
      if (pill) pill.innerText = `${data.indexed_count || 0} Indexed`;
      await this.loadCalibrationScoreboard();
    } catch (e) {
      alert('Audit trigger failed: ' + e.message);
      if (pill) pill.innerText = 'Audit Error';
    }
  }
};

// Immediate Self-Healing Auto-Load for Calibration Scoreboard
if (document.readyState === 'complete' || document.readyState === 'interactive') {
  setTimeout(() => { if (window.AppSwing) window.AppSwing.loadCalibrationScoreboard(); }, 50);
} else {
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => { if (window.AppSwing) window.AppSwing.loadCalibrationScoreboard(); }, 50);
  });
}
