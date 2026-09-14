/**
 * Schwab Portfolio & Individual Positions Desk Controller
 * Manages live accounts, positions, filtering, P/L metrics, and deep research triggers.
 */

window.AppPortfolio = {
  activeAccountFilter: 'ALL',
  activeAssetFilter: 'ALL',
  searchQuery: '',
  sortBy: 'market_value_desc',
  positionsData: [],
  summaryData: null,
  isSyncing: false,

  async init() {
    await this.loadPortfolio();
  },

  async loadPortfolio() {
    try {
      await Promise.all([
        this.fetchSummary(),
        this.fetchPositions(),
      ]);
    } catch (err) {
      console.error('Failed to load portfolio:', err);
    }
  },

  async fetchSummary() {
    try {
      const res = await fetch('/api/schwab/portfolio/summary');
      if (!res.ok) return;
      const data = await res.json();
      this.summaryData = data;
      this.renderSummaryCards(data);
      this.renderAccountFilterPills(data.accounts || []);
    } catch (e) {
      console.error('Error fetching portfolio summary:', e);
    }
  },

  async fetchPositions() {
    try {
      const p = new URLSearchParams();
      if (this.activeAccountFilter && this.activeAccountFilter !== 'ALL') {
        p.append('account', this.activeAccountFilter);
      }
      if (this.activeAssetFilter && this.activeAssetFilter !== 'ALL') {
        p.append('asset_type', this.activeAssetFilter);
      }
      if (this.searchQuery && this.searchQuery.trim()) {
        p.append('search', this.searchQuery.trim());
      }
      if (this.sortBy) {
        p.append('sort', this.sortBy);
      }

      const res = await fetch(`/api/schwab/portfolio/positions?${p.toString()}`);
      if (!res.ok) return;
      const data = await res.json();
      this.positionsData = data.positions || [];
      this.renderPositionsTable(this.positionsData);
    } catch (e) {
      console.error('Error fetching portfolio positions:', e);
    }
  },

  async syncFromSchwab() {
    if (this.isSyncing) return;
    this.isSyncing = true;
    const btn = document.getElementById('btn-sync-portfolio');
    if (btn) {
      btn.innerHTML = '🔄 <span class="spinner" style="display:inline-block; animation:spin 1s linear infinite;">↻</span> Syncing...';
      btn.disabled = true;
    }

    try {
      const res = await fetch('/api/schwab/portfolio/sync', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        await this.loadPortfolio();
      } else {
        alert('Schwab Sync Notice: ' + (data.error || 'Failed syncing with Schwab API'));
      }
    } catch (e) {
      console.error('Error syncing Schwab portfolio:', e);
      alert('Error syncing Schwab portfolio: ' + e.message);
    } finally {
      this.isSyncing = false;
      if (btn) {
        btn.innerHTML = '🔄 Sync from Schwab';
        btn.disabled = false;
      }
    }
  },

  renderSummaryCards(s) {
    if (!s) return;

    // Total Liquidation Value
    const totalValEl = document.getElementById('pf-stat-total-val');
    if (totalValEl) {
      totalValEl.innerText = '$' + (s.total_liquidation_value || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    // Today's Day P/L
    const dayPnlEl = document.getElementById('pf-stat-day-pnl');
    const dayPnlPctEl = document.getElementById('pf-stat-day-pnl-pct');
    if (dayPnlEl && dayPnlPctEl) {
      const dayVal = s.total_day_pnl || 0;
      const dayPct = s.total_day_pnl_pct || 0;
      const sign = dayVal >= 0 ? '+' : '';
      const color = dayVal >= 0 ? 'var(--green, #10b981)' : 'var(--red, #ef4444)';
      
      dayPnlEl.innerText = `${sign}$${Math.abs(dayVal).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      dayPnlEl.style.color = color;
      dayPnlPctEl.innerText = `${sign}${dayPct.toFixed(2)}%`;
      dayPnlPctEl.style.color = color;
    }

    // Total Unrealized P/L
    const unPnlEl = document.getElementById('pf-stat-unrealized-pnl');
    const unPnlPctEl = document.getElementById('pf-stat-unrealized-pnl-pct');
    if (unPnlEl && unPnlPctEl) {
      const unVal = s.total_unrealized_pnl || 0;
      const unPct = s.total_unrealized_pnl_pct || 0;
      const sign = unVal >= 0 ? '+' : '';
      const color = unVal >= 0 ? 'var(--green, #10b981)' : 'var(--red, #ef4444)';

      unPnlEl.innerText = `${sign}$${Math.abs(unVal).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      unPnlEl.style.color = color;
      unPnlPctEl.innerText = `${sign}${unPct.toFixed(2)}%`;
      unPnlPctEl.style.color = color;
    }

    // Cash / Money Market Reserve
    const cashEl = document.getElementById('pf-stat-cash-bal');
    if (cashEl) {
      cashEl.innerText = '$' + (s.total_cash_balance || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    // Holdings Count Badge
    const breakdown = s.asset_breakdown || {};
    const eqCnt = breakdown.EQUITY ? breakdown.EQUITY.count : 0;
    const optCnt = breakdown.OPTION ? breakdown.OPTION.count : 0;
    const mfCnt = breakdown.MUTUAL_FUND ? breakdown.MUTUAL_FUND.count : 0;

    const countEl = document.getElementById('pf-stat-holdings-count');
    if (countEl) {
      countEl.innerText = `${s.position_count || 0} Total (${eqCnt} Eq, ${optCnt} Opt, ${mfCnt} Funds)`;
    }

    // Last Synced Timestamp
    const syncEl = document.getElementById('pf-last-synced-txt');
    if (syncEl && s.last_synced) {
      try {
        const d = new Date(s.last_synced);
        syncEl.innerText = `Last Synced: ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
      } catch (e) {
        syncEl.innerText = `Last Synced: Just now`;
      }
    }
  },

  renderAccountFilterPills(accounts) {
    const container = document.getElementById('pf-account-pills-container');
    if (!container) return;

    let html = `
      <button class="ticker-pill-btn ${this.activeAccountFilter === 'ALL' ? 'active' : ''}" onclick="AppPortfolio.setAccountFilter('ALL')">
        🌐 All Accounts
      </button>
    `;

    accounts.forEach(a => {
      const isAct = this.activeAccountFilter === a.account_number_masked || this.activeAccountFilter === a.account_id;
      const typeBadge = a.account_type === 'MARGIN' ? '⚡ Margin' : '💵 Cash';
      const valStr = '$' + (a.liquidation_value || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
      html += `
        <button class="ticker-pill-btn ${isAct ? 'active' : ''}" onclick="AppPortfolio.setAccountFilter('${a.account_number_masked}')">
          ${typeBadge} (${a.account_number_masked}) <span style="opacity:0.75; font-size:10px; margin-left:3px;">${valStr}</span>
        </button>
      `;
    });

    container.innerHTML = html;
  },

  setAccountFilter(acct) {
    this.activeAccountFilter = acct;
    this.renderAccountFilterPills(this.summaryData?.accounts || []);
    this.fetchPositions();
  },

  setAssetFilter(type) {
    this.activeAssetFilter = type;
    document.querySelectorAll('.pf-asset-filter-btn').forEach(b => {
      if (b.dataset.asset === type) {
        b.classList.add('active');
      } else {
        b.classList.remove('active');
      }
    });
    this.fetchPositions();
  },

  setSort(val) {
    this.sortBy = val;
    this.fetchPositions();
  },

  handleSearch(val) {
    this.searchQuery = val;
    this.fetchPositions();
  },

  renderPositionsTable(positions) {
    const tbody = document.getElementById('pf-positions-tbody');
    const countBadge = document.getElementById('pf-table-count-badge');
    if (countBadge) countBadge.innerText = `${positions.length} Positions`;

    if (!tbody) return;

    if (!positions || positions.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="10" style="text-align:center; padding:30px; color:var(--text-muted); font-size:13px;">
            No positions found matching current filters. Click <b>"🔄 Sync from Schwab"</b> to refresh.
          </td>
        </tr>
      `;
      return;
    }

    let html = '';
    positions.forEach((p, idx) => {
      const isOption = p.asset_type === 'OPTION';
      const isFund = p.asset_type === 'MUTUAL_FUND';
      const underlying = p.underlying_symbol || p.symbol;

      // Asset Badge
      let assetBadge = '';
      if (isOption) {
        const typeColor = p.option_type === 'CALL' ? '#10b981' : '#f87171';
        const typeBg = p.option_type === 'CALL' ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)';
        assetBadge = `
          <span style="font-size:10px; font-weight:700; padding:2px 6px; border-radius:4px; background:${typeBg}; color:${typeColor}; border:1px solid ${typeColor}40;">
            ${p.option_type || 'OPT'} $${p.option_strike || 0}
          </span>
          <span style="font-size:10px; color:var(--text-muted); margin-left:4px;">${p.option_expiration || ''}</span>
        `;
      } else if (isFund) {
        assetBadge = `<span style="font-size:10.5px; padding:2px 6px; border-radius:4px; background:rgba(139,92,246,0.15); color:#a78bfa; border:1px solid rgba(139,92,246,0.3);">FUND</span>`;
      } else {
        assetBadge = `<span style="font-size:10.5px; padding:2px 6px; border-radius:4px; background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.3);">EQUITY</span>`;
      }

      // Account Badge
      const acctBadge = `
        <span style="font-size:10.5px; font-family:var(--font-mono); color:var(--text-muted); background:var(--bg-card); padding:2px 6px; border-radius:4px; border:1px solid var(--border);">
          ${p.account_type === 'MARGIN' ? '⚡' : '💵'} ${p.account_number_masked}
        </span>
      `;

      // Day P/L Formatted
      const dayVal = p.day_profit_loss || 0;
      const dayPct = p.day_profit_loss_pct || 0;
      const daySign = dayVal >= 0 ? '+' : '';
      const dayColor = dayVal >= 0 ? 'var(--green, #10b981)' : 'var(--red, #ef4444)';
      const dayStr = `
        <span style="color:${dayColor}; font-weight:700; font-family:var(--font-mono);">
          ${daySign}$${Math.abs(dayVal).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          <span style="font-size:11px; opacity:0.85; margin-left:2px;">(${daySign}${dayPct.toFixed(2)}%)</span>
        </span>
      `;

      // Unrealized P/L Formatted
      const unVal = p.unrealized_profit_loss || 0;
      const unPct = p.unrealized_profit_loss_pct || 0;
      const unSign = unVal >= 0 ? '+' : '';
      const unColor = unVal >= 0 ? 'var(--green, #10b981)' : 'var(--red, #ef4444)';
      const unStr = `
        <span style="color:${unColor}; font-weight:700; font-family:var(--font-mono);">
          ${unSign}$${Math.abs(unVal).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          <span style="font-size:11px; opacity:0.85; margin-left:2px;">(${unSign}${unPct.toFixed(2)}%)</span>
        </span>
      `;

      // Quantity Formatted
      const qtyStr = isOption
        ? `<b style="font-family:var(--font-mono);">${p.quantity}</b> <span style="font-size:10.5px; color:var(--text-muted);">ct</span>`
        : `<b style="font-family:var(--font-mono);">${p.quantity.toLocaleString('en-US', { maximumFractionDigits: 3 })}</b> <span style="font-size:10.5px; color:var(--text-muted);">sh</span>`;

      // Symbol Display with Underlying
      const symDisplay = `
        <div style="display:flex; flex-direction:column; gap:2px;">
          <div style="display:flex; align-items:center; gap:6px;">
            <span style="font-size:13.5px; font-weight:800; font-family:var(--font-mono); color:var(--cyan);">${underlying}</span>
            ${assetBadge}
          </div>
          ${isOption ? `<span style="font-size:10.5px; color:var(--text-muted); font-family:var(--font-mono);">${p.symbol}</span>` : ''}
          ${p.description && !isOption ? `<span style="font-size:10.5px; color:var(--text-muted);">${p.description}</span>` : ''}
        </div>
      `;

      // Actions Column: 🔬 Deep Research, 📈 TV Chart
      const actions = `
        <div style="display:inline-flex; align-items:center; gap:6px;">
          <button class="btn" onclick="AppPortfolio.launchDeepResearch('${underlying}')" style="padding:3px 8px; font-size:11px; font-weight:700;" title="Launch full Agentic Deep Research on ${underlying}">
            🔬 Research
          </button>
          <button class="btn secondary" onclick="AppSwing.openTradingViewModal('${underlying}', 'D')" style="padding:3px 8px; font-size:11px;" title="Open interactive TradingView chart for ${underlying}">
            📈 Chart
          </button>
        </div>
      `;

      html += `
        <tr style="transition:background 0.15s ease;">
          <td style="padding:10px 14px;">${symDisplay}</td>
          <td style="padding:10px 14px;">${acctBadge}</td>
          <td style="padding:10px 14px; text-align:right;">${qtyStr}</td>
          <td style="padding:10px 14px; text-align:right; font-family:var(--font-mono); color:var(--text-muted);">$${(p.average_price || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
          <td style="padding:10px 14px; text-align:right; font-family:var(--font-mono); font-weight:600;">$${(p.current_price || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
          <td style="padding:10px 14px; text-align:right; font-family:var(--font-mono); font-weight:700; font-size:13px;">$${(p.market_value || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
          <td style="padding:10px 14px; text-align:right;">${dayStr}</td>
          <td style="padding:10px 14px; text-align:right;">${unStr}</td>
          <td style="padding:10px 14px; text-align:center;">${actions}</td>
        </tr>
      `;
    });

    tbody.innerHTML = html;
  },

  launchDeepResearch(ticker) {
    if (!ticker) return;
    const cleanSym = ticker.trim().toUpperCase();

    // Switch to swing desk and launch research
    App.switchDesk('swing');
    if (window.AppSwing && typeof window.AppSwing.setTicker === 'function') {
      window.AppSwing.setTicker(cleanSym);
    }
    const input = document.getElementById('ticker-input');
    if (input) input.value = cleanSym;

    if (window.AppSwing && typeof window.AppSwing.launchResearch === 'function') {
      window.AppSwing.launchResearch();
    }
  },
};
