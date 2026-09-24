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

  async launchResearch(dateOverride = null, forceOverride = null) {
    const tickerInput = document.getElementById('ticker-input');
    const rawInput = (tickerInput ? tickerInput.value : '').trim();
    if (!rawInput) return alert('Please enter a ticker');

    // Support comma-separated or space-separated multiple tickers: e.g. "AAPL, MSFT, NVDA"
    const tickers = rawInput.split(/[,\s]+/).map(t => t.trim().toUpperCase()).filter(Boolean);
    if (tickers.length === 0) return alert('Please enter a valid ticker');

    const modeSelect = document.getElementById('mode-select');
    const mode = modeSelect ? modeSelect.value : 'full';
    const targetDate = dateOverride || null;
    const force = (forceOverride !== null && forceOverride !== undefined) ? forceOverride : false;

    try {
      let startedCount = 0;
      let queuedCount = 0;
      let alreadyActiveCount = 0;
      let lastRes = null;

      for (const t of tickers) {
        if (window.AppUtils && AppUtils.showToast && tickers.length === 1) {
          AppUtils.showToast(`🚀 Launching ${mode} research for $${t}...`, 'info');
        }

        const res = await window.AppApi.triggerResearch(t, mode, targetDate, force);
        lastRes = res;
        if (res && res.status === 'started') startedCount++;
        else if (res && res.status === 'queued') queuedCount++;
        else if (res && res.status === 'running') alreadyActiveCount++;
      }

      if (window.AppStatus) {
        await window.AppStatus.updateStatus();
        await window.AppStatus.loadJobs();
      }

      if (tickers.length === 1) {
        const t = tickers[0];
        if (lastRes && lastRes.status === 'started') {
          if (window.AppUtils && AppUtils.showToast) {
            AppUtils.showToast(`🚀 Started research for $${t} in open slot! (Job: ${lastRes.job_id})`, 'success');
          }
        } else if (lastRes && lastRes.status === 'queued') {
          if (window.AppUtils && AppUtils.showToast) {
            AppUtils.showToast(`⏳ Hardware slots busy (2/2). $${t} queued for research!`, 'info');
          }
        } else if (lastRes && lastRes.status === 'running') {
          if (window.AppUtils && AppUtils.showToast) {
            AppUtils.showToast(`⚡ Research already active for $${t}!`, 'info');
          }
        } else if (lastRes && lastRes.status === 'skipped') {
          const skipMsg = lastRes.message || `${t} already researched for ${lastRes.target_date || targetDate || 'today'}`;
          if (window.AppUtils && AppUtils.showToast) {
            AppUtils.showToast(`ℹ️ ${skipMsg}. Opening dossier...`, 'info');
          }
          if (typeof this.openReportModal === 'function') {
            this.openReportModal(lastRes.target_date || targetDate || null, t);
          }
        }
      } else {
        if (window.AppUtils && AppUtils.showToast) {
          AppUtils.showToast(`📋 Dispatched ${tickers.length} tickers: ${startedCount} started, ${queuedCount} queued!`, 'success');
        }
      }

      // Automatically uncollapse active procs panel if hidden so user sees progress
      const procsEl = document.getElementById('active-procs-wrapper');
      if (procsEl && procsEl.style.display === 'none') {
        this.toggleActiveProcsPanel();
      }
    } catch (e) {
      if (window.AppUtils && AppUtils.showToast) {
        AppUtils.showToast(`Research Error: ${e.message}`, 'error');
      } else {
        alert(`Research Alert: ${e.message}`);
      }
    }
  },

  toggleActiveProcsPanel() {
    const el = document.getElementById('active-procs-wrapper');
    const btn = document.getElementById('btn-toggle-procs-panel');
    if (!el) return;
    const isHidden = el.style.display === 'none';
    el.style.display = isHidden ? 'block' : 'none';
    if (btn) btn.innerText = isHidden ? 'Collapse ▲' : 'Expand ▼';
    try { localStorage.setItem('launcher_procs_collapsed', isHidden ? '0' : '1'); } catch(e){}
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
    await this.loadSchwabScreener();
  },

  _currentScreenerCandidates: [],
  _screenerSide: (function() {
    try { return localStorage.getItem('screener_side') || 'long'; } catch (e) { return 'long'; }
  })(),

  updateScreenerTabButtons() {
    const isShort = this._screenerSide === 'short';
    const longBtn = document.getElementById('screener-side-long-btn');
    const shortBtn = document.getElementById('screener-side-short-btn');
    const scanBtn = document.getElementById('btn-run-schwab-scan');
    const researchAllBtn = document.getElementById('btn-scrape-all-survivors');

    if (longBtn && shortBtn) {
      if (!isShort) {
        longBtn.style.background = 'var(--green)';
        longBtn.style.color = '#fff';
        longBtn.style.borderColor = 'var(--green)';
        longBtn.style.fontWeight = '800';
        longBtn.style.boxShadow = '0 0 8px rgba(16,185,129,0.3)';

        shortBtn.style.background = '';
        shortBtn.style.color = '';
        shortBtn.style.borderColor = '';
        shortBtn.style.fontWeight = '700';
        shortBtn.style.boxShadow = '';
      } else {
        shortBtn.style.background = 'var(--red)';
        shortBtn.style.color = '#fff';
        shortBtn.style.borderColor = 'var(--red)';
        shortBtn.style.fontWeight = '800';
        shortBtn.style.boxShadow = '0 0 8px rgba(239,68,68,0.3)';

        longBtn.style.background = '';
        longBtn.style.color = '';
        longBtn.style.borderColor = '';
        longBtn.style.fontWeight = '700';
        longBtn.style.boxShadow = '';
      }
    }

    if (scanBtn && !scanBtn.disabled) {
      scanBtn.innerHTML = isShort ? '🔍 Scan Prime Shorts' : '🔍 Scan Long Bases';
      scanBtn.title = isShort ? 'Scan 983 Schwab constituents for overextended ceiling stalls' : 'Scan 983 Schwab constituents for 20 EMA / 50 SMA coiling bases';
    }

    if (researchAllBtn) {
      researchAllBtn.innerHTML = isShort ? '🚀 Research All Shorts' : '🚀 Research All Longs';
      researchAllBtn.title = isShort ? 'Launch full deep research pipeline for all prime short candidates' : 'Launch full deep research pipeline for all coiled long candidates';
    }
  },

  setScreenerSide(side) {
    this._screenerSide = (side || 'long').toLowerCase();
    try { localStorage.setItem('screener_side', this._screenerSide); } catch (e) {}
    this.updateScreenerTabButtons();
    this.loadSchwabScreener();
  },

  async loadSchwabScreener(explicitDate = null) {
    const container = document.getElementById('schwab-screener-container');
    if (!container) return;
    this.updateScreenerTabButtons();

    try {
      const activeDate = explicitDate || null;
      const isShort = this._screenerSide === 'short';
      const data = await window.AppApi.getSchwabScreenerCandidates(activeDate, this._screenerSide);
      const allCandidates = (data && data.candidates) ? data.candidates : [];
      // Strictly filter out any stub/queue items that have neither price nor support level
      const candidates = allCandidates.filter(c => c && (c.price !== undefined && c.price !== null || c.support_level !== undefined && c.support_level !== null));
      this._currentScreenerCandidates = candidates;

      const countPill = document.getElementById('schwab-screener-status-pill');
      if (countPill) {
        const label = isShort ? 'Prime Short Exhaustion' : 'Coiled Base Setups';
        countPill.innerText = `${candidates.length} ${label} (${data.date || 'Today'})`;
        countPill.style.color = isShort ? 'var(--red-light)' : 'var(--cyan-glow)';
        countPill.style.borderColor = isShort ? 'var(--red)' : 'var(--border)';
      }

      if (data && data.market_tide) {
        const tidePill = document.getElementById('schwab-market-tide-pill');
        if (tidePill) {
          if (isShort) {
            if (data.market_tide.bullish) {
              tidePill.innerText = `SPY Bullish (⚠️ Counter-Trend Fades)`;
              tidePill.style.color = '#f59e0b';
              tidePill.style.borderColor = '#d97706';
            } else {
              tidePill.innerText = `SPY Defensive (✅ Short Tide Confirmed)`;
              tidePill.style.color = 'var(--red-light)';
              tidePill.style.borderColor = 'var(--red)';
            }
          } else {
            tidePill.innerText = `SPY ${data.market_tide.trend_str || 'Bullish'}`;
            tidePill.style.color = data.market_tide.bullish ? 'var(--green-light)' : 'var(--red-light)';
            tidePill.style.borderColor = data.market_tide.bullish ? 'var(--green)' : 'var(--red)';
          }
        }
      }

      if (candidates.length === 0) {
        container.innerHTML = `
          <div style="color:var(--text-muted); font-size:12.5px; padding:16px 8px; text-align:center;">
            <span>No ${isShort ? 'prime short exhaustion' : 'pre-move compression'} setups found for ${data.date || 'today'}. Click <strong>"${isShort ? 'Scan Prime Shorts' : 'Scan Long Bases'}"</strong> above to scan the 983 Schwab constituents live!</span>
          </div>
        `;
        return;
      }

      let html = '';
      if (!isShort) {
        // LONG BASING TABLE
        html = `
          <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px;">
            <thead>
              <tr style="border-bottom:1px solid var(--border); background:var(--bg-subtle);">
                <th style="text-align:left; padding:8px 10px;">Ticker</th>
                <th style="text-align:right; padding:8px 10px;">Price</th>
                <th style="text-align:right; padding:8px 10px;">Stop Floor</th>
                <th style="text-align:right; padding:8px 10px;">Ceiling</th>
                <th style="text-align:center; padding:8px 10px;">Long R:R</th>
                <th style="text-align:center; padding:8px 10px;">Stage</th>
                <th style="text-align:center; padding:8px 10px;">Rev Zone</th>
                <th style="text-align:center; padding:8px 10px;">Priority</th>
                <th style="text-align:left; padding:8px 10px;">Support Anchor</th>
                <th style="text-align:center; padding:8px 10px;">Posture / Headroom</th>
                <th style="text-align:center; padding:8px 10px;">Squeeze (SQZ)</th>
                <th style="text-align:center; padding:8px 10px;">NR7 Bar</th>
                <th style="text-align:right; padding:8px 10px;">RS vs SPY</th>
                <th style="text-align:center; padding:8px 10px;">Tastytrade Vol</th>
                <th style="text-align:center; padding:8px 10px;">Live Actions</th>
              </tr>
            </thead>
            <tbody>
        `;

        candidates.forEach(c => {
          const sym = c.Symbol || c.Ticker;
          const px = c.price ? `$${Number(c.price).toFixed(2)}` : '-';
          const sup = c.support_level ? `$${Number(c.support_level).toFixed(2)}` : '-';
          const stop = c.stop_level ? `$${Number(c.stop_level).toFixed(2)}` : sup;
          const ceil = c.ceiling_level ? `$${Number(c.ceiling_level).toFixed(2)}` : '-';
          const longRR = c.long_rr !== undefined ? `${Number(c.long_rr).toFixed(1)}:1` : '2.0:1';
          const longRRBadge = `<span class="badge" style="background:#ecfdf5; color:#065f46; border:1px solid #a7f3d0; font-weight:800; font-size:11.5px;">${longRR}</span>`;
          const setup = c.screener_setup || '20 EMA';
          const sqzBadge = c.squeeze_on ? `<span class="badge" style="background:#ecfdf5; color:#059669; border:1px solid #a7f3d0; font-weight:700;">🔥 SQUEEZE</span>` : `<span style="color:var(--text-muted);">No</span>`;
          const nr7Badge = c.nr7 ? `<span class="badge" style="background:#eff6ff; color:#2563eb; border:1px solid #bfdbfe; font-weight:700;">NR7</span>` : `<span style="color:var(--text-muted);">-</span>`;
          const rsVal = c.relative_strength_20d !== undefined ? Number(c.relative_strength_20d).toFixed(1) : null;
          const rsBadge = rsVal ? `<span style="color:${Number(rsVal) >= 0 ? 'var(--green-light)' : 'var(--red-light)'}; font-weight:700;">${Number(rsVal) >= 0 ? '+' : ''}${rsVal}%</span>` : '-';
          const pos52Str = (c.pos_52w !== undefined && c.pos_52w > 0) ? ` [${Number(c.pos_52w).toFixed(0)}% Base]` : '';

          // Tastytrade Volatility & 24/7 Cloud Alert Badge
          let ttBadge = '<span style="color:var(--text-muted); font-size:11px;">-</span>';
          if (c.tastytrade && c.tastytrade.connected) {
            const ivr = (c.tastytrade.iv_rank !== null && c.tastytrade.iv_rank !== undefined) ? `${Number(c.tastytrade.iv_rank).toFixed(0)}%` : '-';
            const ivp = (c.tastytrade.iv_percentile !== null && c.tastytrade.iv_percentile !== undefined) ? `${Number(c.tastytrade.iv_percentile).toFixed(0)}%` : '-';
            const hv30 = (c.tastytrade.hv30 !== null && c.tastytrade.hv30 !== undefined) ? `${Number(c.tastytrade.hv30).toFixed(0)}%` : '-';
            const diff = (c.tastytrade.iv_hv_diff !== null && c.tastytrade.iv_hv_diff !== undefined) ? `${Number(c.tastytrade.iv_hv_diff) > 0 ? '+' : ''}${Number(c.tastytrade.iv_hv_diff).toFixed(0)}%` : null;
            const alertTag = c.tastytrade_alert_active ? `<span class="badge" style="background:rgba(239,68,68,0.18); color:#f87171; border:1px solid rgba(239,68,68,0.4); font-size:9.5px; padding:1px 4px; margin-top:2px;" title="24/7 Cloud Quote Alert Active on Tastytrade Mobile">🔔 Cloud Alert</span>` : '';

            ttBadge = `
              <div style="display:flex; flex-direction:column; gap:2px; align-items:center;">
                <span class="badge" style="background:rgba(239,68,68,0.12); color:#f87171; border:1px solid rgba(239,68,68,0.3); font-weight:700; font-size:11px;" title="Tastytrade IV Rank: ${ivr} | IV Percentile: ${ivp} | 30d HV: ${hv30}${diff ? ' | IV-HV: ' + diff : ''}">
                  IVR ${ivr}
                </span>
                ${diff ? `<span style="font-size:9.5px; color:var(--text-muted);" title="IV-HV Spread">IV-HV ${diff}</span>` : ''}
                ${alertTag}
              </div>
            `;
          }

          // Pine Screener Model Badges
          const stageNum = c.weinstein_stage !== undefined ? Number(c.weinstein_stage) : 1;
          let stageBadge = `<span class="badge" style="background:#eff6ff; color:#2563eb; font-weight:700;">Stg 1 (Base)</span>`;
          if (stageNum === 2) {
            stageBadge = `<span class="badge" style="background:#ecfdf5; color:#059669; font-weight:800; border:1px solid #a7f3d0;">Stg 2 (Adv)</span>`;
          } else if (stageNum === 3) {
            stageBadge = `<span class="badge" style="background:#fef3c7; color:#d97706; font-weight:700;">Stg 3 (Dist)</span>`;
          } else if (stageNum === 4) {
            stageBadge = `<span class="badge" style="background:#fee2e2; color:#dc2626; font-weight:800; border:1px solid #fca5a5;">Stg 4 (Dec)</span>`;
          } else if (stageNum === 5) {
            stageBadge = `<span class="badge" style="background:#f3e8ff; color:#9333ea; font-weight:700;">Stg 5 (Rec)</span>`;
          }

          const revLong = c.rev_zone_long !== undefined ? Number(c.rev_zone_long) : 0.0;
          let revBadge = `<span style="color:var(--text-muted); font-size:11px;">-</span>`;
          if (c.is_extreme_reversal || revLong >= 7.0) {
            revBadge = `<span class="badge" style="background:rgba(16,185,129,0.22); color:#34d399; border:1px solid #10b981; font-weight:800;" title="Connors Extreme Reversal Zone (86-91% Win Rate Setup)">⚡ Z1+ (${revLong.toFixed(1)})</span>`;
          } else if (revLong >= 4.0) {
            revBadge = `<span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; font-weight:700;">Z2 (${revLong.toFixed(1)})</span>`;
          }

          const prioScore = c.priority_score !== undefined ? Number(c.priority_score) : 50.0;
          const prioTier = c.priority_tier || 'MONITOR';
          let prioBadge = `<span class="badge" style="color:var(--text-muted); border:1px solid var(--border); font-size:11px;">MONITOR (${prioScore.toFixed(0)})</span>`;
          if (prioTier === 'HIGH_PRIORITY' || prioScore >= 75) {
            prioBadge = `<span class="badge" style="background:rgba(16,185,129,0.2); color:#10b981; border:1px solid rgba(16,185,129,0.5); font-weight:800; font-size:11.5px;" title="Autonomous Dispatch Eligible: High-Priority Pine Setup">🔥 HIGH (${prioScore.toFixed(0)})</span>`;
          } else if (prioTier === 'MEDIUM_PRIORITY' || prioScore >= 55) {
            prioBadge = `<span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.3); font-weight:700; font-size:11px;">MED (${prioScore.toFixed(0)})</span>`;
          }

          let postureBadge = '-';
          if (c.headroom_pct !== undefined && Number(c.headroom_pct) >= 12.0) {
            postureBadge = `<span class="badge" style="background:#ecfdf5; color:#047857; border:1px solid #a7f3d0; font-weight:700;" title="Substantial upside room to resistance. Ground floor swing edge.">🚀 OPEN RUNWAY (+${Number(c.headroom_pct).toFixed(1)}%)${pos52Str}</span>`;
          } else if (c.headroom_pct !== undefined) {
            postureBadge = `<span class="badge" style="background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; font-weight:600;">MID PULLBACK (+${Number(c.headroom_pct).toFixed(1)}%)${pos52Str}</span>`;
          }

          const compName = window.AppUtils ? AppUtils.getCompanyName(sym) : sym;
          const compTitle = (compName || sym).replace(/"/g, '&quot;');

          html += `
            <tr style="border-bottom:1px solid var(--border); transition:background 0.2s;" onmouseover="this.style.background='var(--bg-hover)'" onmouseout="this.style.background='transparent'">
              <td style="padding:8px 10px;">
                <button class="ticker-pill-btn" onclick="AppSwing.openTradingViewModal('${sym}', 'D')" style="font-weight:800; padding:2px 8px; font-size:12px; cursor:pointer;" title="${compTitle} ($${sym}) - Click to view live TradingView Chart" data-ticker="${sym}">$${sym}</button>
              </td>
              <td style="text-align:right; padding:8px 10px; font-weight:700; font-family:var(--font-mono);">${px}</td>
              <td style="text-align:right; padding:8px 10px; font-family:var(--font-mono); color:var(--red-light);">${stop}</td>
              <td style="text-align:right; padding:8px 10px; font-family:var(--font-mono); color:var(--text-muted);">${ceil}</td>
              <td style="text-align:center; padding:8px 10px;">${longRRBadge}</td>
              <td style="text-align:center; padding:8px 10px;">${stageBadge}</td>
              <td style="text-align:center; padding:8px 10px;">${revBadge}</td>
              <td style="text-align:center; padding:8px 10px;">${prioBadge}</td>
              <td style="padding:8px 10px; color:var(--blue); font-weight:600;">
                ${setup} (${sup})
                ${c.pattern && c.pattern !== 'Support Coil' ? `<div style="font-size:11px; margin-top:2px; font-weight:700; color:#059669;" title="TA-Lib Bullish Candlestick Pattern">🕯️ ${c.pattern}</div>` : ''}
              </td>
              <td style="text-align:center; padding:8px 10px;">${postureBadge}</td>
              <td style="text-align:center; padding:8px 10px;">${sqzBadge}</td>
              <td style="text-align:center; padding:8px 10px;">${nr7Badge}</td>
              <td style="text-align:right; padding:8px 10px; font-family:var(--font-mono);">${rsBadge}</td>
              <td style="text-align:center; padding:8px 10px;">${ttBadge}</td>
              <td style="text-align:center; padding:8px 10px;">
                <div style="display:inline-flex; gap:5px; align-items:center;">
                  <a href="https://www.tradingview.com/chart/jPAQSlZC/?symbol=${encodeURIComponent(sym)}&interval=D" target="_blank" class="btn secondary" style="padding:2px 7px; font-size:11px; font-weight:700; color:var(--blue); text-decoration:none; display:inline-flex; align-items:center; gap:2px;" title="Open directly in TradingView with Rev - Enhanced v2 & R-VRVP Indicators">
                    📊 Rev v2 ↗
                  </a>
                  <button class="btn secondary" onclick="AppSwing.openTradingViewModal('${sym}', 'D')" style="padding:2px 7px; font-size:11px; font-weight:700;" title="Open Interactive TradingView Modal">
                    📈 Modal
                  </button>
                  <button class="btn secondary" onclick="AppSwing.setTicker('${sym}'); AppSwing.launchResearch();" style="padding:2px 7px; font-size:11px; font-weight:700;" title="Queue Full Deep Research">
                    ⚡ Deep Research
                  </button>
                </div>
              </td>
            </tr>
          `;
        });
      } else {
        // PRIME SHORT TABLE
        html = `
          <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px;">
            <thead>
              <tr style="border-bottom:1px solid var(--border); background:var(--bg-subtle);">
                <th style="text-align:left; padding:8px 10px;">Ticker</th>
                <th style="text-align:right; padding:8px 10px;">Price</th>
                <th style="text-align:right; padding:8px 10px;">Stop Loss</th>
                <th style="text-align:right; padding:8px 10px;">Ceiling Resist</th>
                <th style="text-align:right; padding:8px 10px;">Dist to Ceil</th>
                <th style="text-align:center; padding:8px 10px;">Short R:R</th>
                <th style="text-align:center; padding:8px 10px;">Stage</th>
                <th style="text-align:center; padding:8px 10px;">Rev Short</th>
                <th style="text-align:center; padding:8px 10px;">Priority</th>
                <th style="text-align:right; padding:8px 10px;">50 SMA Target</th>
                <th style="text-align:right; padding:8px 10px;">Profit Runway</th>
                <th style="text-align:center; padding:8px 10px;">Squeeze (SQZ)</th>
                <th style="text-align:center; padding:8px 10px;">NR7 Bar</th>
                <th style="text-align:right; padding:8px 10px;">Ext vs 200 SMA</th>
                <th style="text-align:center; padding:8px 10px;">Exhaustion Pattern</th>
                <th style="text-align:center; padding:8px 10px;">Tastytrade Vol</th>
                <th style="text-align:center; padding:8px 10px;">Live Actions</th>
              </tr>
            </thead>
            <tbody>
        `;

        candidates.forEach(c => {
          const sym = c.Symbol || c.Ticker;
          const px = c.price ? `$${Number(c.price).toFixed(2)}` : '-';
          const stop = c.stop_level ? `$${Number(c.stop_level).toFixed(2)}` : '-';
          const ceil = c.ceiling_level ? `$${Number(c.ceiling_level).toFixed(2)}` : '-';
          const ceilDist = c.headroom_pct !== undefined ? `+${Number(c.headroom_pct).toFixed(1)}%` : '-';
          const ext200 = c.ext_200_pct !== undefined ? `+${Number(c.ext_200_pct).toFixed(1)}%` : '-';
          const tgt = c.target_level ? `$${Number(c.target_level).toFixed(2)}` : '-';
          const downPct = c.downside_to_50sma !== undefined ? Number(c.downside_to_50sma).toFixed(1) : '15.0';
          const rr = c.short_rr !== undefined ? `${Number(c.short_rr).toFixed(1)}:1` : '1.5:1';
          const rrBadge = `<span class="badge" style="background:rgba(239,68,68,0.15); color:#f87171; border:1px solid rgba(239,68,68,0.4); font-weight:800; font-size:11.5px;">${rr}</span>`;
          const runwayBadge = `<span class="badge" style="background:rgba(16,185,129,0.15); color:#34d399; border:1px solid rgba(16,185,129,0.4); font-weight:700;">+${downPct}%</span>`;
          const sqzBadge = c.squeeze_on ? `<span class="badge" style="background:rgba(239,68,68,0.15); color:#f87171; border:1px solid rgba(239,68,68,0.4); font-weight:700;">🔥 SQUEEZE</span>` : `<span style="color:var(--text-muted);">No</span>`;
          const nr7Badge = c.nr7 ? `<span class="badge" style="background:#eff6ff; color:#2563eb; border:1px solid #bfdbfe; font-weight:700;">NR7</span>` : `<span style="color:var(--text-muted);">-</span>`;

          // Tastytrade Volatility & 24/7 Cloud Alert Badge
          let ttBadge = '<span style="color:var(--text-muted); font-size:11px;">-</span>';
          if (c.tastytrade && c.tastytrade.connected) {
            const ivr = (c.tastytrade.iv_rank !== null && c.tastytrade.iv_rank !== undefined) ? `${Number(c.tastytrade.iv_rank).toFixed(0)}%` : '-';
            const ivp = (c.tastytrade.iv_percentile !== null && c.tastytrade.iv_percentile !== undefined) ? `${Number(c.tastytrade.iv_percentile).toFixed(0)}%` : '-';
            const hv30 = (c.tastytrade.hv30 !== null && c.tastytrade.hv30 !== undefined) ? `${Number(c.tastytrade.hv30).toFixed(0)}%` : '-';
            const diff = (c.tastytrade.iv_hv_diff !== null && c.tastytrade.iv_hv_diff !== undefined) ? `${Number(c.tastytrade.iv_hv_diff) > 0 ? '+' : ''}${Number(c.tastytrade.iv_hv_diff).toFixed(0)}%` : null;
            const alertTag = c.tastytrade_alert_active ? `<span class="badge" style="background:rgba(239,68,68,0.18); color:#f87171; border:1px solid rgba(239,68,68,0.4); font-size:9.5px; padding:1px 4px; margin-top:2px;" title="24/7 Cloud Quote Alert Active on Tastytrade Mobile">🔔 TT Alert</span>` : '';

            ttBadge = `
              <div style="display:flex; flex-direction:column; gap:2px; align-items:center;">
                <span class="badge" style="background:rgba(239,68,68,0.12); color:#f87171; border:1px solid rgba(239,68,68,0.3); font-weight:700; font-size:11px;" title="Tastytrade IV Rank: ${ivr} | IV Percentile: ${ivp} | 30d HV: ${hv30}${diff ? ' | IV-HV: ' + diff : ''}">
                  IVR ${ivr}
                </span>
                ${diff ? `<span style="font-size:9.5px; color:var(--text-muted);" title="IV-HV Spread">IV-HV ${diff}</span>` : ''}
                ${alertTag}
              </div>
            `;
          }

          // Pine Screener Model Badges
          const stageNum = c.weinstein_stage !== undefined ? Number(c.weinstein_stage) : 3;
          let stageBadge = `<span class="badge" style="background:#fef3c7; color:#d97706; font-weight:700;">Stg 3 (Dist)</span>`;
          if (stageNum === 4) {
            stageBadge = `<span class="badge" style="background:#fee2e2; color:#dc2626; font-weight:800; border:1px solid #fca5a5;">Stg 4 (Dec)</span>`;
          } else if (stageNum === 2) {
            stageBadge = `<span class="badge" style="background:#ecfdf5; color:#059669; font-weight:700;">Stg 2 (Adv)</span>`;
          }

          const revShort = c.rev_zone_short !== undefined ? Number(c.rev_zone_short) : 0.0;
          let revBadge = `<span style="color:var(--text-muted); font-size:11px;">-</span>`;
          if (c.is_extreme_reversal || revShort >= 7.0) {
            revBadge = `<span class="badge" style="background:rgba(239,68,68,0.22); color:#f87171; border:1px solid #ef4444; font-weight:800;" title="Connors Extreme Overbought Rejection Zone">⚡ Z1+ (${revShort.toFixed(1)})</span>`;
          } else if (revShort >= 4.0) {
            revBadge = `<span class="badge" style="background:rgba(249,115,22,0.15); color:#fb923c; font-weight:700;">Z2 (${revShort.toFixed(1)})</span>`;
          }

          const prioScore = c.priority_score !== undefined ? Number(c.priority_score) : 50.0;
          const prioTier = c.priority_tier || 'MONITOR';
          let prioBadge = `<span class="badge" style="color:var(--text-muted); border:1px solid var(--border); font-size:11px;">MONITOR (${prioScore.toFixed(0)})</span>`;
          if (prioTier === 'HIGH_PRIORITY' || prioScore >= 75) {
            prioBadge = `<span class="badge" style="background:rgba(239,68,68,0.2); color:#f87171; border:1px solid rgba(239,68,68,0.5); font-weight:800; font-size:11.5px;" title="Autonomous Dispatch Eligible: High-Priority Pine Short Setup">🔥 HIGH (${prioScore.toFixed(0)})</span>`;
          } else if (prioTier === 'MEDIUM_PRIORITY' || prioScore >= 55) {
            prioBadge = `<span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.3); font-weight:700; font-size:11px;">MED (${prioScore.toFixed(0)})</span>`;
          }

          const pat = c.pattern || 'Ceiling Stall';
          let patBadge = `<span class="badge" style="background:rgba(245,158,11,0.15); color:#fbbf24; border:1px solid rgba(245,158,11,0.3); font-weight:700;">🧱 ${pat}</span>`;
          if (pat.includes('Shooting Star') || pat.includes('Evening Star')) {
            patBadge = `<span class="badge" style="background:rgba(239,68,68,0.18); color:#f87171; border:1px solid rgba(239,68,68,0.4); font-weight:800;" title="TA-Lib Confirmed Candlestick Rejection">⭐ ${pat}</span>`;
          } else if (pat.includes('Engulfing') || pat.includes('Dark Cloud')) {
            patBadge = `<span class="badge" style="background:rgba(220,38,38,0.18); color:#ef4444; border:1px solid rgba(220,38,38,0.4); font-weight:800;" title="TA-Lib Bearish Momentum Engulfing">🔻 ${pat}</span>`;
          } else if (pat.includes('Hanging Man') || pat.includes('Harami') || pat.includes('Hikkake') || pat.includes('Crows')) {
            patBadge = `<span class="badge" style="background:rgba(249,115,22,0.18); color:#fb923c; border:1px solid rgba(249,115,22,0.4); font-weight:800;" title="TA-Lib Reversal Pattern">⚡ ${pat}</span>`;
          } else if (pat.includes('Doji') || pat.includes('Spinning Top')) {
            patBadge = `<span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.3); font-weight:700;" title="TA-Lib Indecision / Exhaustion at Highs">⚖️ ${pat}</span>`;
          } else if (pat.includes('NR7')) {
            patBadge = `<span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.3); font-weight:700;" title="NR7 Volatility Contraction">📦 ${pat}</span>`;
          }

          const compName = window.AppUtils ? AppUtils.getCompanyName(sym) : sym;
          const compTitle = (compName || sym).replace(/"/g, '&quot;');

          html += `
            <tr style="border-bottom:1px solid var(--border); transition:background 0.2s;" onmouseover="this.style.background='var(--bg-hover)'" onmouseout="this.style.background='transparent'">
              <td style="padding:8px 10px;">
                <button class="ticker-pill-btn" onclick="AppSwing.openTradingViewModal('${sym}', 'D')" style="font-weight:800; padding:2px 8px; font-size:12px; cursor:pointer; color:var(--red-light); border-color:#fca5a5;" title="${compTitle} ($${sym}) - Click to view live TradingView Chart" data-ticker="${sym}">$${sym}</button>
              </td>
              <td style="text-align:right; padding:8px 10px; font-weight:700; font-family:var(--font-mono);">${px}</td>
              <td style="text-align:right; padding:8px 10px; font-family:var(--font-mono); font-weight:700; color:var(--rose-light);">${stop}</td>
              <td style="text-align:right; padding:8px 10px; font-family:var(--font-mono); font-weight:600; color:#fbbf24;">${ceil}</td>
              <td style="text-align:right; padding:8px 10px; font-family:var(--font-mono); color:var(--text-muted);">${ceilDist}</td>
              <td style="text-align:center; padding:8px 10px;">${rrBadge}</td>
              <td style="text-align:center; padding:8px 10px;">${stageBadge}</td>
              <td style="text-align:center; padding:8px 10px;">${revBadge}</td>
              <td style="text-align:center; padding:8px 10px;">${prioBadge}</td>
              <td style="text-align:right; padding:8px 10px; font-family:var(--font-mono); font-weight:700; color:var(--cyan-glow);">${tgt}</td>
              <td style="text-align:right; padding:8px 10px; font-family:var(--font-mono);">${runwayBadge}</td>
              <td style="text-align:center; padding:8px 10px;">${sqzBadge}</td>
              <td style="text-align:center; padding:8px 10px;">${nr7Badge}</td>
              <td style="text-align:right; padding:8px 10px; font-family:var(--font-mono); font-weight:700; color:#fb923c;">${ext200}</td>
              <td style="text-align:center; padding:8px 10px;">${patBadge}</td>
              <td style="text-align:center; padding:8px 10px;">${ttBadge}</td>
              <td style="text-align:center; padding:8px 10px;">
                <div style="display:inline-flex; gap:5px; align-items:center;">
                  <a href="https://www.tradingview.com/chart/jPAQSlZC/?symbol=${encodeURIComponent(sym)}&interval=D" target="_blank" class="btn secondary" style="padding:2px 7px; font-size:11px; font-weight:700; color:var(--blue); text-decoration:none; display:inline-flex; align-items:center; gap:2px;" title="Open directly in TradingView with Rev - Enhanced v2 & R-VRVP Indicators">
                    📊 Rev v2 ↗
                  </a>
                  <button class="btn secondary" onclick="AppSwing.openTradingViewModal('${sym}', 'D')" style="padding:2px 7px; font-size:11px; font-weight:700;" title="Open Interactive TradingView Modal">
                    📈 Modal
                  </button>
                  <button class="btn secondary" onclick="AppSwing.setTicker('${sym}'); AppSwing.launchResearch();" style="padding:2px 7px; font-size:11px; font-weight:700;" title="Queue Full Deep Research">
                    ⚡ Deep Research
                  </button>
                </div>
              </td>
            </tr>
          `;
        });
      }

      html += `</tbody></table>`;
      container.innerHTML = html;
    } catch (e) {
      console.error(`Failed loading Schwab screener candidates: ${e}`);
      container.innerHTML = `<div style="color:var(--red-light); font-size:12px; padding:8px;">Failed loading screener: ${e.message}</div>`;
    }
  },

  async runSchwabScreenerScan() {
    const isShort = this._screenerSide === 'short';
    const btn = document.getElementById('btn-run-schwab-scan');
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = isShort ? '⏳ Scanning Shorts...' : '⏳ Scanning 983 Stocks...';
    }

    try {
      await window.AppApi.runSchwabScreenerScan(10, false, this._screenerSide || 'long');
      
      const startTime = Date.now();
      if (this._schwabScanTimer) clearInterval(this._schwabScanTimer);
      
      this._schwabScanTimer = setInterval(async () => {
        const elapsedSec = Math.floor((Date.now() - startTime) / 1000);
        if (btn) {
          btn.innerHTML = `⏳ Scanning ${isShort ? 'Shorts' : '983 Stocks'} (${elapsedSec}s)...`;
        }
        
        try {
          const status = await window.AppApi.getSchwabScanStatus();
          if (!status.running || elapsedSec >= 120) {
            clearInterval(this._schwabScanTimer);
            this._schwabScanTimer = null;
            if (btn) {
              btn.disabled = false;
              this.updateScreenerTabButtons();
            }
            await this.loadSchwabScreener();
            await this.refreshResearchQueue();
          }
        } catch (pollErr) {
          if (elapsedSec >= 75) {
            clearInterval(this._schwabScanTimer);
            this._schwabScanTimer = null;
            if (btn) {
              btn.disabled = false;
              this.updateScreenerTabButtons();
            }
            await this.loadSchwabScreener();
          }
        }
      }, 2000);
    } catch (e) {
      alert(`Screener Scan Error: ${e.message}`);
      if (btn) {
        btn.disabled = false;
        this.updateScreenerTabButtons();
      }
    }
  },

  async runAutonomousScreenerScan() {
    const isShort = this._screenerSide === 'short';
    const btn = document.getElementById('btn-run-autonomous-scan');
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '🤖 Autonomous Pipeline...';
    }

    try {
      await window.AppApi.runAutonomousScreenerScan(10, this._screenerSide || 'long', 3);

      const startTime = Date.now();
      if (this._schwabScanTimer) clearInterval(this._schwabScanTimer);

      this._schwabScanTimer = setInterval(async () => {
        const elapsedSec = Math.floor((Date.now() - startTime) / 1000);
        if (btn) {
          btn.innerHTML = `🤖 Autonomous Pipeline (${elapsedSec}s)...`;
        }

        try {
          const status = await window.AppApi.getSchwabScanStatus();
          if (!status.running || elapsedSec >= 180) {
            clearInterval(this._schwabScanTimer);
            this._schwabScanTimer = null;
            if (btn) {
              btn.disabled = false;
              btn.innerHTML = '🤖 Autonomous Scan & Research';
            }
            await this.loadSchwabScreener();
            await this.refreshResearchQueue();
          }
        } catch (pollErr) {
          if (elapsedSec >= 120) {
            clearInterval(this._schwabScanTimer);
            this._schwabScanTimer = null;
            if (btn) {
              btn.disabled = false;
              btn.innerHTML = '🤖 Autonomous Scan & Research';
            }
            await this.loadSchwabScreener();
          }
        }
      }, 2000);
    } catch (e) {
      alert(`Autonomous Pipeline Error: ${e.message}`);
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '🤖 Autonomous Scan & Research';
      }
    }
  },

  _lastContinuousScanTime: null,
  _continuousPollingTimer: null,

  async toggleContinuousScreenerPause() {
    try {
      const btn = document.getElementById('btn-toggle-screener-pause');
      if (btn) btn.disabled = true;
      const res = await fetch('/api/screener/toggle-pause', { method: 'POST' });
      const data = await res.json();
      const isPaused = Boolean(data.paused);
      if (window.AppUtils && AppUtils.showToast) {
        AppUtils.showToast(isPaused ? '⏸️ Schwab screener paused' : '▶️ Schwab screener resumed', isPaused ? 'warning' : 'success');
      }
      await this.pollContinuousScreenerStatus();
      if (btn) btn.disabled = false;
    } catch (e) {
      console.error('Failed to toggle screener pause:', e);
      const btn = document.getElementById('btn-toggle-screener-pause');
      if (btn) btn.disabled = false;
    }
  },

  async triggerContinuousScanNow() {
    try {
      if (window.AppUtils && AppUtils.showToast) {
        AppUtils.showToast('⚡ Triggering continuous Schwab 1000 & Tastytrade scan...', 'info');
      }
      const labelEl = document.getElementById('screener-continuous-status-text');
      if (labelEl) labelEl.innerText = '⚡ Triggering...';
      const res = await fetch('/api/screener/continuous-scan-now', { method: 'POST' });
      const data = await res.json();
      setTimeout(() => this.pollContinuousScreenerStatus(), 1000);
    } catch (e) {
      console.error('Trigger continuous scan failed:', e);
    }
  },

  async pollContinuousScreenerStatus() {
    try {
      const res = await fetch('/api/screener/continuous-status');
      if (!res.ok) return;
      const s = await res.json();

      const labelEl = document.getElementById('screener-continuous-status-text');
      const timerEl = document.getElementById('screener-next-scan-timer');
      const badgeEl = document.getElementById('screener-continuous-badge');
      const ttBadgeEl = document.getElementById('screener-tastytrade-badge');
      const btnPause = document.getElementById('btn-toggle-screener-pause');

      if (btnPause) {
        if (s.paused) {
          btnPause.innerHTML = '▶️ Resume Screener';
          btnPause.style.borderColor = '#10b981';
          btnPause.style.color = '#10b981';
          btnPause.title = 'Screener is currently paused. Click to resume automated scanning.';
        } else {
          btnPause.innerHTML = '⏸️ Pause Screener';
          btnPause.style.borderColor = '';
          btnPause.style.color = '';
          btnPause.title = 'Pause continuous automated scanning';
        }
      }

      if (ttBadgeEl) {
        if (s.tastytrade_connected) {
          ttBadgeEl.innerHTML = `<span>🍒 TT Vol &amp; Alerts</span><span style="font-size:9.5px; background:rgba(0,0,0,0.35); padding:1px 5px; border-radius:4px; margin-left:2px; color:var(--text-muted);">${s.active_alerts_registered || 0} Alerts</span>`;
          ttBadgeEl.style.color = '#f87171';
          ttBadgeEl.style.borderColor = 'rgba(239,68,68,0.4)';
        } else {
          ttBadgeEl.innerHTML = `<span>🍒 TT Connected</span>`;
          ttBadgeEl.style.color = '#f87171';
          ttBadgeEl.style.borderColor = 'rgba(239,68,68,0.3)';
        }
      }

      if (s.paused) {
        if (labelEl) labelEl.innerText = '⏸️ Screener Paused';
        if (timerEl) timerEl.innerText = 'Paused';
        if (badgeEl) {
          badgeEl.style.borderColor = 'rgba(245,158,11,0.4)';
          badgeEl.style.color = '#f59e0b';
          const dot = badgeEl.querySelector('.dot');
          if (dot) {
            dot.style.background = '#f59e0b';
            dot.style.boxShadow = '0 0 6px #f59e0b';
          }
        }
      } else if (s.is_scanning) {
        if (labelEl) labelEl.innerText = '⚡ Scanning 983 Stocks...';
        if (timerEl) timerEl.innerText = 'Live';
        if (badgeEl) {
          badgeEl.style.borderColor = '#3b82f6';
          badgeEl.style.color = 'var(--cyan)';
          const dot = badgeEl.querySelector('.dot');
          if (dot) {
            dot.style.background = '#3b82f6';
            dot.style.boxShadow = '0 0 6px #3b82f6';
          }
        }
      } else {
        if (labelEl) labelEl.innerText = '🤖 Continuous Active';
        if (timerEl) {
          const sec = s.seconds_until_next_scan || 0;
          const m = Math.floor(sec / 60);
          const remS = sec % 60;
          timerEl.innerText = m > 0 ? `(~${m}m)` : `(${remS}s)`;
        }
        if (badgeEl) {
          badgeEl.style.borderColor = 'rgba(16,185,129,0.35)';
          badgeEl.style.color = '#10b981';
          const dot = badgeEl.querySelector('.dot');
          if (dot) {
            dot.style.background = '#10b981';
            dot.style.boxShadow = '0 0 6px #10b981';
          }
        }
      }

      // Auto-refresh candidate table when a new scan finishes
      if (s.last_scan_time && s.last_scan_time !== this._lastContinuousScanTime) {
        const isInitial = this._lastContinuousScanTime === null;
        this._lastContinuousScanTime = s.last_scan_time;
        if (!isInitial) {
          if (window.AppUtils && AppUtils.showToast) {
            AppUtils.showToast(`✅ Continuous screener updated (${s.long_count} Longs, ${s.short_count} Shorts)`, 'success');
          }
          this.loadSchwabScreener();
        }
      }
    } catch (e) {
      console.debug('Continuous status poll error:', e);
    }
  },

  startContinuousScreenerPolling() {
    if (this._continuousPollingTimer) clearInterval(this._continuousPollingTimer);
    this.pollContinuousScreenerStatus();
    this._continuousPollingTimer = setInterval(() => this.pollContinuousScreenerStatus(), 8000);
  },

  async autoResearchAllSurvivors() {
    const isShort = this._screenerSide === 'short';
    if (!this._currentScreenerCandidates || this._currentScreenerCandidates.length === 0) {
      return alert(`No ${isShort ? 'prime short' : 'long basing'} survivors available. Run the Schwab scan first!`);
    }

    const label = isShort ? 'prime short' : 'coiled long';
    const total = this._currentScreenerCandidates.length;
    const conf = confirm(`Queue deep research for all ${total} ${label} candidates?\n(The system will automatically process 2 at a time based on hardware slots until all ${total} finish)`);
    if (!conf) return;

    let startedCount = 0;
    let queuedCount = 0;
    for (const c of this._currentScreenerCandidates) {
      const sym = c.Symbol || c.Ticker;
      if (sym) {
        try {
          const res = await window.AppApi.triggerResearch(sym, 'full');
          if (res && res.status === 'started') startedCount++;
          else if (res && res.status === 'queued') queuedCount++;
        } catch (e) {
          console.warn(`Could not queue ${sym}:`, e);
        }
      }
    }
    if (window.AppStatus) {
      await window.AppStatus.updateStatus();
      await window.AppStatus.loadJobs();
    }
    alert(`Queued all ${total} ${label} candidates!\n${startedCount} running immediately in open slots, ${queuedCount} queued to auto-process as slots free up.\nMonitor progress in Hardware/Processes.`);
  },

  toggleResearchQueue() {
    const container = document.getElementById('research-queue-container');
    if (container) {
      container.style.display = container.style.display === 'none' ? '' : 'none';
    }
  },

  async refreshResearchQueue() {
    const container = document.getElementById('research-queue-container');
    if (container && container.style.display === 'none') return;
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

        const compQ = window.AppUtils ? AppUtils.getCompanyName(item.ticker) : item.ticker;
        const compTitle = (compQ || item.ticker).replace(/"/g, '&quot;');

        return `
          <div style="display:inline-flex; align-items:center; gap:6px; background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:3px 8px; box-shadow:0 1px 2px rgba(0,0,0,0.03);">
            <strong style="color:var(--text-main); font-size:12px; cursor:help;" title="${compTitle}" data-ticker="${item.ticker}">$${item.ticker}</strong>
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
        const isShortTarget = (t.side === 'SHORT') || (stop > entry);
        const sideBadge = isShortTarget
          ? `<span class="badge" style="background:rgba(239,68,68,0.15); color:#f87171; border:1px solid rgba(239,68,68,0.4); font-weight:800; font-size:10px; margin-left:4px;">🔴 SHORT</span>`
          : `<span class="badge" style="background:rgba(16,185,129,0.15); color:#34d399; border:1px solid rgba(16,185,129,0.4); font-weight:800; font-size:10px; margin-left:4px;">🟢 LONG</span>`;

        const ladderLabels = isShortTarget ? `
          <span style="color:var(--rose-light);">Stop $${stop.toFixed(2)}</span>
          <span style="color:var(--amber-light);">Ceiling Entry $${entry.toFixed(2)}</span>
          <span style="color:var(--cyan-glow); font-weight:700;">Spot $${spot.toFixed(2)}</span>
          <span style="color:var(--emerald-light);">T1 $${t1.toFixed(2)}</span>
          <span style="color:var(--violet-light);">T2 $${t2.toFixed(2)}</span>
        ` : `
          <span style="color:var(--rose-light);">Stop $${stop.toFixed(2)}</span>
          <span style="color:var(--amber-light);">Entry $${entry.toFixed(2)}</span>
          <span style="color:var(--cyan-glow); font-weight:700;">Spot $${spot.toFixed(2)}</span>
          <span style="color:var(--emerald-light);">T1 $${t1.toFixed(2)}</span>
          <span style="color:var(--violet-light);">T2 $${t2.toFixed(2)}</span>
        `;

        const compT = window.AppUtils ? AppUtils.getCompanyName(t.ticker) : t.ticker;
        const compTitle = (compT || t.ticker).replace(/"/g, '&quot;');

        return `
          <div class="target-card">
            <div class="target-header">
              <div class="target-sym">
                <span class="target-sym-text" title="${compTitle} ($${t.ticker})" data-ticker="${t.ticker}" style="cursor:help;">${t.ticker}</span>
                ${sideBadge}
                <span class="spot-badge">$${spot.toFixed(2)}</span>
                <span class="badge ${statusClass}">${t.status} (${distDisplay})</span>
              </div>
              <button class="btn secondary" onclick="AppSwing.openReportModal('${t.date}', '${t.ticker}')" style="padding:5px 12px; font-size:12px;">📑 Read Dossier</button>
            </div>
            
            <!-- Visual Price Ladder -->
            <div class="price-ladder-box">
              <div class="price-ladder-labels">
                ${ladderLabels}
              </div>
              <div class="price-ladder-track">
                <div class="price-ladder-progress" style="width: 100%;"></div>
              </div>
            </div>

            <div class="levels-row">
              <div class="level-col"><label>${isShortTarget ? 'Ceiling Entry' : 'Entry Zone'}</label><span>$${Number(t.entry_zone_low||0).toFixed(2)} - $${Number(t.entry_zone_high||0).toFixed(2)}</span></div>
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

  async deleteAllAlerts() {
    if (!confirm('⚠️ Are you sure you want to delete ALL cloud alerts from Tastytrade across ALL tickers? This cannot be undone.')) return;
    try {
      if (window.AppToast) window.AppToast.show('Clearing all Tastytrade cloud alerts...', 'info');
      const res = await window.AppApi.deleteAllTastytradeAlerts();
      if (window.AppToast) window.AppToast.show(`Successfully cleared ${res.deleted_count ?? 0} alerts from Tastytrade`, 'success');
      await this.loadTastytradeAlerts();
    } catch (e) {
      alert(`Error deleting all alerts: ${e.message}`);
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
          const label = (data.date_labels && data.date_labels[d]) || `📅 ${d}`;
          return `<option value="${d}" ${isSelected}>${label}</option>`;
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
      const isLatest = window.AppState.currentArchiveDate === ((window.AppState.allReportDates && window.AppState.allReportDates[0]) || '');
      const dateTag = isLatest ? `${window.AppState.currentArchiveDate} · Latest Session` : window.AppState.currentArchiveDate;
      countPill.innerText = query
        ? `${reports.length} / ${this._allArchiveReports.length} Reports`
        : `${reports.length} Reports (${dateTag})`;
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

      let entryStr = '--';
      if (r.entry_zone && r.entry_zone.length >= 2) {
        entryStr = `$${Number(r.entry_zone[0]).toFixed(2)} - $${Number(r.entry_zone[1]).toFixed(2)}`;
      } else if (r.entry_zone && r.entry_zone.length === 1) {
        entryStr = `$${Number(r.entry_zone[0]).toFixed(2)}`;
      }

      const stopStr = (r.tactical_stop !== null && r.tactical_stop !== undefined && !isNaN(r.tactical_stop))
        ? `$${Number(r.tactical_stop).toFixed(2)}`
        : '--';
      const t1Str = (r.target_1 !== null && r.target_1 !== undefined && !isNaN(r.target_1))
        ? `$${Number(r.target_1).toFixed(2)}`
        : '--';
      const t2Str = (r.target_2 !== null && r.target_2 !== undefined && !isNaN(r.target_2))
        ? `$${Number(r.target_2).toFixed(2)}`
        : '--';

      // Format research execution time
      let timeStr = '';
      if (r.researched_at) {
        try {
          const dt = new Date(r.researched_at);
          const now = new Date();
          const diffMs = now - dt;
          const diffHours = Math.floor(diffMs / 3600000);
          const timeH = dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          if (diffHours < 24 && dt.getDate() === now.getDate()) {
            timeStr = `⚡ Run Today ${timeH}${diffHours > 0 ? ` (${diffHours}h ago)` : ''}`;
          } else if (diffHours < 48) {
            timeStr = `📅 Yesterday ${timeH}`;
          } else {
            timeStr = `📅 ${r.date}`;
          }
        } catch (e) {}
      }

      return `
        <div class="target-card">
          <div class="target-header">
            <div class="target-sym">
              <span style="color:var(--cyan-glow); font-weight:800; font-size:15px; font-family:var(--font-mono);">${r.ticker}</span>
              <span class="badge ${verdictClass}">${verdictStr}</span>
              ${timeStr ? `<span style="font-size:11px; color:var(--text-muted); font-family:var(--font-mono); font-weight:600; margin-left:6px;">${timeStr}</span>` : ''}
            </div>
            <button class="btn secondary" onclick="AppSwing.openReportModal('${r.date}', '${r.ticker}')" style="padding:5px 12px; font-size:12px;">📑 Open Dossier</button>
          </div>

          <div class="options-snippet">
            <strong>Strategy:</strong> ${optText}
          </div>

          <div class="levels-row">
            <div class="level-col"><label>Entry Zone</label><span style="font-weight:700; color:var(--text-main); font-family:var(--font-mono);">${entryStr}</span></div>
            <div class="level-col"><label>Tactical Stop</label><span style="color:var(--rose-light); font-weight:700; font-family:var(--font-mono);">${stopStr}</span></div>
            <div class="level-col"><label>Target 1</label><span style="color:var(--emerald-light); font-weight:700; font-family:var(--font-mono);">${t1Str}</span></div>
            <div class="level-col"><label>Target 2</label><span style="color:var(--cyan-glow); font-weight:700; font-family:var(--font-mono);">${t2Str}</span></div>
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
    const sym = ticker.toUpperCase().trim();
    await this.openReportModal(date, sym, 'plan');
    this.togglePositionsPane(false);
    const drawer = document.getElementById('modal-copilot-drawer');
    if (drawer) {
      drawer.style.display = 'flex';
      try {
        drawer.scrollIntoView({ behavior: 'smooth', inline: 'end', block: 'nearest' });
      } catch (e) {}
    }
    const chat = window.AppState.activeChats ? window.AppState.activeChats.find(c => (c.ticker || '').toUpperCase().trim() === sym) : null;
    const data = (chat && chat.reportData) || window.AppState.currentReportData;
    const optPlan = (data && data.ticker === sym && data.watch_levels?.options_plan?.summary) || 'the suggested position';
    const prompt = `What is the exact execution step for $${sym} (${optPlan}) right now?`;
    if (window.AppChat && typeof window.AppChat.askModalCopilot === 'function') {
      window.AppChat.askModalCopilot(prompt, sym, date);
    } else if (window.AppChat && typeof window.AppChat.askActiveChat === 'function') {
      window.AppChat.askActiveChat(prompt);
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

    if (!window.AppState.activeChats) window.AppState.activeChats = [];
    const maxChats = window.AppState.maxActiveChats || 3;

    // 1. If ticker is already open in activeChats
    const existingIdx = window.AppState.activeChats.findIndex(c => c.ticker === ticker);
    if (existingIdx !== -1) {
      const existing = window.AppState.activeChats[existingIdx];
      if (date && date !== 'latest' && existing.date !== date) {
        // Different archive date requested for existing ticker, will fetch below
      } else {
        if (initialTab) existing.activeTab = initialTab;
        this.switchToChat(ticker, initialTab);
        return;
      }
    }

    // 2. Prepare modal UI
    const modal = document.getElementById('report-modal');
    if (!modal) return;
    if (modal.classList.contains('collapsed')) {
      this.restoreReportModal();
    }
    modal.style.display = 'flex';

    document.getElementById('modal-ticker-title').innerText = `${ticker} RESEARCH REPORT (${date || 'Latest'})`;
    const pulse = document.getElementById('modal-minimized-pulse');
    if (pulse) pulse.style.display = 'none';
    const bodyEl = document.getElementById('modal-body');
    if (bodyEl) bodyEl.innerHTML = `<div style="padding:40px; text-align:center;">⏳ Loading dossier for <strong>${ticker}</strong>...</div>`;

    try {
      const reqDate = date || (window.AppState ? window.AppState.currentArchiveDate : null) || 'latest';
      const data = await window.AppApi.getReportBundle(reqDate, ticker);
      data.ticker = ticker;
      const actualDate = data.date || reqDate;
      data.date = actualDate;

      // 3. Manage activeChats array (CAPPED AT 3 MAX)
      if (!window.AppState.activeChats) window.AppState.activeChats = [];
      const chatIndex = window.AppState.activeChats.findIndex(c => c.ticker === ticker);

      if (chatIndex !== -1) {
        window.AppState.activeChats[chatIndex].date = actualDate;
        window.AppState.activeChats[chatIndex].reportData = data;
        window.AppState.activeChats[chatIndex].activeTab = initialTab || window.AppState.activeChats[chatIndex].activeTab || 'arb';
        window.AppState.activeChats[chatIndex].lastAccessed = Date.now();
      } else {
        // Enforce max 3 chats limit
        if (window.AppState.activeChats.length >= maxChats) {
          // Find an inactive chat (not currently focused) to evict
          let evictIdx = window.AppState.activeChats.findIndex(c => c.ticker !== window.AppState.activeChatTicker);
          if (evictIdx === -1) evictIdx = 0;
          const evicted = window.AppState.activeChats.splice(evictIdx, 1)[0];
          console.log(`[Multi-Chat] Max 3 chats reached: replaced ${evicted?.ticker} with ${ticker}`);
        }

        window.AppState.activeChats.push({
          ticker: ticker,
          date: actualDate,
          reportData: data,
          activeTab: initialTab || 'arb',
          draftInput: '',
          lastAccessed: Date.now()
        });
      }

      // 4. Switch to this chat
      this.switchToChat(ticker, initialTab);

    } catch (e) {
      if (bodyEl) bodyEl.innerText = `Failed loading report for ${ticker}: ${e.message}`;
    }
  },

  switchToChat(ticker, initialTab = null) {
    ticker = (ticker || '').toUpperCase().trim();
    if (!window.AppState.activeChats || window.AppState.activeChats.length === 0) return;

    const chat = window.AppState.activeChats.find(c => c.ticker === ticker) || window.AppState.activeChats[0];
    if (!chat) return;

    // Preserve draft input for the previous active chat
    const currentInput = document.getElementById('modal-chat-input');
    if (currentInput && window.AppState.activeChatTicker && window.AppState.activeChatTicker !== chat.ticker) {
      const prev = window.AppState.activeChats.find(c => c.ticker === window.AppState.activeChatTicker);
      if (prev) prev.draftInput = currentInput.value;
    }

    // Set active chat and sync all focus state
    window.AppState.activeChatTicker = chat.ticker;
    window.AppState.currentReportData = chat.reportData;
    window.AppState.revChatFocusTicker = chat.ticker;
    const tickerInput = document.getElementById('ticker-input');
    if (tickerInput) tickerInput.value = chat.ticker;
    chat.lastAccessed = Date.now();
    if (initialTab) chat.activeTab = initialTab;

    const data = chat.reportData;
    const actualDate = chat.date;

    const modal = document.getElementById('report-modal');
    if (modal && modal.style.display === 'none') {
      modal.style.display = 'flex';
    }

    // Update modal title
    const titleEl = document.getElementById('modal-ticker-title');
    if (titleEl) {
      titleEl.innerText = `${chat.ticker} RESEARCH REPORT (${actualDate || 'Active'})`;
    }

    if (window.AppChat && typeof window.AppChat.updateSidebarFocusBadge === 'function') {
      window.AppChat.updateSidebarFocusBadge(chat.ticker);
    }

    // Render Suggested Positions Pane for this stock
    this.renderSuggestedPositionsPane(data);

    // Populate Date Switcher dropdown
    const dateSelect = document.getElementById('modal-report-date-select');
    const histCountEl = document.getElementById('modal-hist-count');
    const dates = (data && data.available_dates) ? data.available_dates : [actualDate];
    if (histCountEl) histCountEl.innerText = dates.length;

    if (dateSelect) {
      dateSelect.innerHTML = dates.map(d => {
        const isSel = (d === actualDate) ? 'selected' : '';
        const matchItem = ((data && data.historical_timeline) || []).find(x => x.date === d);
        const spotLabel = (matchItem && matchItem.spot) ? ` ($${Number(matchItem.spot).toFixed(2)})` : '';
        return `<option value="${d}" ${isSel}>${d}${spotLabel}</option>`;
      }).join('');
    }

    // Switch to active dossier tab
    const activeTab = chat.activeTab || window.AppState.activeDossierTab || 'arb';
    this.switchDossierTab(activeTab);

    // Refresh live price & poll for this ticker
    this.refreshModalLivePrice(chat.ticker, data ? data.live_price : null);
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

    // Render multi-chat tabs across modal and floating drawer
    this.renderActiveChatTabs();

    // Restore or initialize Copilot chat thread
    if (window.AppChat) {
      window.AppChat.initModalChatForTicker(chat.ticker, actualDate);
      if (currentInput) {
        currentInput.value = chat.draftInput || '';
      }
      if (typeof window.AppChat.updateModalSendButtonState === 'function') {
        window.AppChat.updateModalSendButtonState();
      }
    }
  },

  closeActiveChatTab(ticker, event) {
    if (event) event.stopPropagation();
    ticker = (ticker || '').toUpperCase().trim();
    if (window.AppChat && typeof window.AppChat.stopModalCopilotStream === 'function') {
      window.AppChat.stopModalCopilotStream(ticker);
    }
    if (!window.AppState.activeChats) return;

    const idx = window.AppState.activeChats.findIndex(c => c.ticker === ticker);
    if (idx === -1) return;

    const wasActive = (window.AppState.activeChatTicker === ticker);
    window.AppState.activeChats.splice(idx, 1);

    if (window.AppState.activeChats.length === 0) {
      window.AppState.activeChatTicker = '';
      this.closeReportModal();
    } else {
      if (wasActive) {
        const nextChat = window.AppState.activeChats[Math.min(idx, window.AppState.activeChats.length - 1)];
        this.switchToChat(nextChat.ticker);
      } else {
        this.renderActiveChatTabs();
      }
    }
  },

  renderActiveChatTabs() {
    const containers = [
      document.getElementById('modal-active-chat-tabs'),
      document.getElementById('modal-copilot-active-tabs')
    ];

    const activeChats = window.AppState.activeChats || [];
    const currentTicker = window.AppState.activeChatTicker || '';
    const maxChats = window.AppState.maxActiveChats || 3;

    containers.forEach(cont => {
      if (!cont) return;
      if (activeChats.length === 0) {
        cont.innerHTML = '';
        return;
      }

      let tabsHtml = activeChats.map(c => {
        const isActive = (c.ticker === currentTicker);
        return `
          <div class="chat-tab ${isActive ? 'active' : ''}" onclick="AppSwing.switchToChat('${c.ticker}')" title="${c.ticker} (${c.date}) - Click to switch chat">
            <span class="chat-tab-icon">💬</span>
            <span class="chat-tab-label">$${c.ticker}</span>
            <button class="chat-tab-close" onclick="AppSwing.closeActiveChatTab('${c.ticker}', event)" title="Close $${c.ticker} chat">✕</button>
          </div>
        `;
      }).join('');

      tabsHtml += `
        <span class="chat-tab-count" title="Active chats (max ${maxChats})">${activeChats.length}/${maxChats}</span>
      `;

      cont.innerHTML = tabsHtml;
    });
  },

  handleModalHeaderClick(event) {
    const modal = document.getElementById('report-modal');
    if (!modal) return;
    if (modal.classList.contains('collapsed')) {
      // If user clicked an action button or tab close, do not intercept
      if (event.target.closest('.modal-window-actions') || event.target.closest('.chat-tab-close')) {
        return;
      }
      this.restoreReportModal(event);
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

  async syncTastytradeAlertsForCurrentDossier(event) {
    if (event) event.stopPropagation();
    const ticker = window.AppState.activeChatTicker || (window.AppState.currentReportData ? window.AppState.currentReportData.ticker : null);
    if (!ticker) {
      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast('⚠️ No active ticker selected in dossier', 'warning');
      } else {
        alert('No active ticker selected in dossier');
      }
      return;
    }
    const date = (window.AppState.currentReportData ? window.AppState.currentReportData.date : null) || (window.AppState ? window.AppState.currentArchiveDate : null);

    // UI Loading state across all sync buttons
    const btnHeader = document.getElementById('btn-modal-sync-tt');
    const btnPane = document.getElementById('btn-pane-sync-tt');
    const origHeaderText = btnHeader ? btnHeader.innerHTML : '';
    const origPaneText = btnPane ? btnPane.innerHTML : '';
    if (btnHeader) { btnHeader.disabled = true; btnHeader.innerHTML = '⏳ Syncing...'; }
    if (btnPane) { btnPane.disabled = true; btnPane.innerHTML = '⏳ Registering alerts...'; }

    try {
      const res = await fetch('/api/tastytrade-alerts/sync-ticker', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: ticker, date: date }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Failed to sync Tastytrade alerts');
      }

      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast(`✅ Tastytrade cloud quote alerts registered for $${ticker}!`, 'success');
      }
      if (btnHeader) { btnHeader.innerHTML = '✅ TT Alerts Set'; }
      if (btnPane) { btnPane.innerHTML = '✅ Tastytrade Alerts Set'; }
      setTimeout(() => {
        if (btnHeader) { btnHeader.disabled = false; btnHeader.innerHTML = origHeaderText; }
        if (btnPane) { btnPane.disabled = false; btnPane.innerHTML = origPaneText; }
      }, 4000);
    } catch (e) {
      console.error(`[Tastytrade] Failed to sync alerts for ${ticker}:`, e);
      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast(`❌ Error: ${e.message}`, 'error');
      } else {
        alert(`Failed to set Tastytrade alerts: ${e.message}`);
      }
      if (btnHeader) { btnHeader.disabled = false; btnHeader.innerHTML = origHeaderText; }
      if (btnPane) { btnPane.disabled = false; btnPane.innerHTML = origPaneText; }
    }
  },

  async fetchLiveOptionSpread(ticker, optPlan) {
    if (!ticker || !optPlan || !optPlan.expiration || !optPlan.short_strike || !optPlan.long_strike) return;
    try {
      const url = `/api/options/spread-calc?ticker=${encodeURIComponent(ticker)}&expiration=${encodeURIComponent(optPlan.expiration)}&short_strike=${optPlan.short_strike}&long_strike=${optPlan.long_strike}&structure=${encodeURIComponent(optPlan.structure || 'BULL_PUT_SPREAD')}`;
      const res = await fetch(url);
      const data = await res.json();
      if (!data || !data.success) return;

      const optDebit = document.getElementById('pane-opt-debit');
      const optLoss = document.getElementById('pane-opt-loss');
      const optProfit = document.getElementById('pane-opt-profit');
      const optSummary = document.getElementById('pane-opt-summary');

      const isCredit = data.pricing_type === 'CREDIT';
      const label = isCredit ? 'Live Credit' : 'Live Debit';

      if (optDebit) {
        optDebit.innerHTML = `
          <div style="font-weight:800; color:var(--cyan-glow);">${label}: $${data.live_mid.toFixed(2)}</div>
          <div style="font-size:9px; color:var(--text-muted); margin-top:2px;">Nat: $${data.live_natural.toFixed(2)} | Plan: ~$${Number(optPlan.target_debit || (optPlan.max_profit ? optPlan.max_profit / 100 : 0) || 0).toFixed(2)}</div>
        `;
      }
      if (optLoss) {
        optLoss.innerHTML = `
          <div style="font-weight:800; color:var(--rose);">$${Math.round(data.live_max_loss).toLocaleString()}</div>
          <div style="font-size:9px; color:var(--text-muted); margin-top:2px;">Plan: $${Math.round(optPlan.max_loss || 0).toLocaleString()}</div>
        `;
      }
      if (optProfit) {
        optProfit.innerHTML = `
          <div style="font-weight:800; color:var(--emerald);">$${Math.round(data.live_max_profit).toLocaleString()}</div>
          <div style="font-size:9px; color:var(--text-muted); margin-top:2px;">Plan: $${Math.round(optPlan.max_profit || 0).toLocaleString()}</div>
        `;
      }
      if (optSummary) {
        const shortLeg = data.short_leg;
        const longLeg = data.long_leg;
        const shortLegStr = `$${shortLeg.strike} (Bid $${shortLeg.bid} / Ask $${shortLeg.ask})`;
        const longLegStr = `$${longLeg.strike} (Bid $${longLeg.bid} / Ask $${longLeg.ask})`;
        optSummary.innerHTML = `
          <div style="margin-bottom:6px;">${optPlan.summary || ''}</div>
          <div style="padding:5px 8px; background:rgba(6,182,212,0.09); border-left:3px solid var(--cyan-glow); border-radius:4px; font-size:10.5px; color:var(--cyan-glow); font-family:'JetBrains Mono',monospace;">
            ⚡ <strong>LIVE SCHWAB:</strong> Mid: $${data.live_mid.toFixed(2)} | Nat: $${data.live_natural.toFixed(2)}<br>
            <span style="opacity:0.85; font-size:9.5px;">Short ${shortLegStr} · Long ${longLegStr}</span>
          </div>
        `;
      }
    } catch (e) {
      console.debug('Live option spread calc skipped:', e);
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
    const isDemoted = !optPlan.actionable || optPlan.structure === 'NONE' || !optPlan.structure;
    const optAct = document.getElementById('pane-opt-actionable');
    if (optAct) {
      optAct.style.display = optPlan.actionable ? 'inline-block' : 'none';
    }

    const optName = document.getElementById('pane-opt-name');
    if (optName) {
      if (isDemoted) {
        optName.innerHTML = `<span style="color:var(--rose); font-weight:800;">⛔ DEMOTED</span>
          <span style="font-size:10px; color:var(--text-muted); font-weight:600;"> – Use Equity Plan</span>`;
      } else {
        optName.textContent = optPlan.structure.replace(/_/g, ' ');
      }
    }

    const optSummary = document.getElementById('pane-opt-summary');
    if (optSummary) {
      const summaryText = optPlan.summary || 'Defined risk options structure.';
      if (isDemoted && summaryText.includes('DEMOTED')) {
        const demotedIdx = summaryText.indexOf('[DEMOTED');
        const reason = demotedIdx >= 0 ? summaryText.slice(demotedIdx) : summaryText;
        optSummary.innerHTML = `<span style="color:var(--text-muted);">${summaryText.slice(0, demotedIdx > 0 ? demotedIdx : summaryText.length).trim()}</span>
          <span style="display:block; margin-top:4px; padding:4px 6px; background:rgba(239,68,68,0.08); border-left:2px solid var(--rose); border-radius:3px; font-size:10px; color:#f87171; font-style:italic;">${reason}</span>`;
      } else {
        optSummary.textContent = summaryText;
      }
    }

    const optExpiry = document.getElementById('pane-opt-expiry');
    if (optExpiry) {
      optExpiry.textContent = optPlan.expiration || '--';
    }

    const optDebit = document.getElementById('pane-opt-debit');
    if (optDebit) {
      optDebit.textContent = isDemoted ? 'N/A' : (optPlan.target_debit ? `~$${Number(optPlan.target_debit).toFixed(2)}` : '--');
    }

    const optLoss = document.getElementById('pane-opt-loss');
    if (optLoss) {
      optLoss.textContent = isDemoted ? 'N/A' : (optPlan.max_loss ? `$${Number(optPlan.max_loss).toLocaleString()}` : '--');
    }

    const optProfit = document.getElementById('pane-opt-profit');
    if (optProfit) {
      optProfit.textContent = isDemoted ? 'N/A' : (optPlan.max_profit ? `$${Number(optPlan.max_profit).toLocaleString()}` : '--');
    }

    // Trigger live mathematical options calculation from Schwab
    if (data && data.ticker && optPlan && optPlan.expiration && optPlan.short_strike && optPlan.long_strike) {
      this.fetchLiveOptionSpread(data.ticker, optPlan);
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

    // When options are demoted, elevate the equity plan as the primary suggestion
    if (isDemoted) {
      const paneBody = document.getElementById('modal-positions-pane-content');
      const optCard = document.getElementById('pane-card-options');
      const eqCard = document.getElementById('pane-card-equity');
      if (paneBody && optCard && eqCard) {
        // Remove any existing demoted banner from previous render
        const existingBanner = document.getElementById('pane-demoted-banner');
        if (existingBanner) existingBanner.remove();
        // Hide options card entirely — equity is the only suggestion
        optCard.style.display = 'none';
        // Insert equity card before options card (swap visual order)
        paneBody.insertBefore(eqCard, optCard);
        // Add banner above equity card indicating it's the primary plan
        const banner = document.createElement('div');
        banner.id = 'pane-demoted-banner';
        banner.style.cssText = 'background:rgba(5,150,105,0.08); border:1px solid rgba(5,150,105,0.3); border-radius:6px; padding:8px 12px; font-size:11px; font-weight:700; color:#059669; display:flex; align-items:center; gap:6px;';
        banner.innerHTML = '<span style="font-size:14px;">📊</span> EQUITY PLAN IS THE SUGGESTED TRADE — Options structure demoted (not actionable)';
        paneBody.insertBefore(banner, eqCard);
      }
    } else {
      // Clean up demoted state if options are now actionable
      const existingBanner = document.getElementById('pane-demoted-banner');
      if (existingBanner) existingBanner.remove();
      const optCard = document.getElementById('pane-card-options');
      if (optCard) {
        optCard.style.display = '';
        optCard.style.opacity = '';
        optCard.style.borderLeft = '';
        optCard.style.background = '';
      }
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
    const isDemoted = !optPlan.actionable || optPlan.structure === 'NONE' || !optPlan.structure;

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
          <div style="display:flex; gap:8px; align-items:center;">
            <button class="btn" onclick="AppSwing.syncTastytradeAlertsForCurrentDossier(event)" style="padding:6px 14px; font-size:12px; font-weight:800; background:linear-gradient(135deg, #0284c7 0%, #0369a1 100%); color:#fff; border:1px solid #38bdf8; cursor:pointer; display:inline-flex; align-items:center; gap:6px;" title="Register Entry, Stop, and Target cloud quote alerts with Tastytrade for this reviewed trade plan">
              📲 Set Tastytrade Alerts
            </button>
            <button class="btn" onclick="AppSwing.askCopilotExecutionPlan(event)" style="padding:6px 14px; font-size:12px; font-weight:800; background:#059669; color:#fff; border-color:#059669; cursor:pointer;">
              ⚡ Plan Execution with Copilot
            </button>
            <button class="btn secondary" onclick="AppSwing.switchDossierTab('tv')" style="padding:6px 14px; font-size:12px; font-weight:700; cursor:pointer;">
              📈 View on Live TV
            </button>
          </div>
        </div>



        <!-- Demotion banner when options are not actionable -->
        ${isDemoted ? `
        <div style="background:rgba(5,150,105,0.08); border:1px solid rgba(5,150,105,0.3); border-radius:6px; padding:8px 14px; margin-bottom:14px; display:flex; align-items:center; gap:8px; font-size:12px; font-weight:700; color:#059669;">
          <span style="font-size:16px;">📊</span>
          <div>Equity Shares Plan is the <strong>PRIMARY SUGGESTED TRADE</strong> — Options structure demoted (not actionable)</div>
        </div>
        ` : ''}

        <!-- 2-Column Grid: Options Vehicle vs Equity Vehicle -->
        <div style="display:grid; grid-template-columns: ${isDemoted ? '1fr' : 'repeat(auto-fit, minmax(360px, 1fr))'}; gap:18px; margin-bottom:20px;">
          
          <!-- Column 1: Options Vehicle (Primary Spread) -->
          <div style="${isDemoted ? 'display:none;' : ''}background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:18px; display:flex; flex-direction:column; justify-content:space-between;">
            <div>
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                <span style="font-size:13px; font-weight:800; color:${isDemoted ? 'var(--rose)' : 'var(--blue)'}; text-transform:uppercase; letter-spacing:0.5px;">
                  🎯 Options Structure ${isDemoted ? '(Not Actionable)' : '(Primary Vehicle)'}
                </span>
                ${optPlan.actionable ? '<span class="pill green" style="font-size:10px; font-weight:800;">ACTIONABLE</span>' : ''}
              </div>

              <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:12px; margin-bottom:14px;">
                <div style="font-family:'JetBrains Mono',monospace; font-size:14px; font-weight:800; color:var(--text-main); margin-bottom:6px;">
                  ${(optPlan.structure && optPlan.structure !== 'NONE') ? optPlan.structure.replace(/_/g, ' ') : '⚠️ NON-ACTIONABLE'}
                </div>
                <div style="font-size:12px; color:var(--text-muted); line-height:1.45;">
                  ${optPlan.summary || 'No detailed options narrative provided.'}
                </div>
              </div>

              ${(!optPlan.actionable || !optPlan.structure || optPlan.structure === 'NONE') && optPlan.summary && optPlan.summary.includes('DEMOTED') ? `
              <div style="background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.3); border-radius:6px; padding:8px 12px; margin-bottom:14px; display:flex; align-items:flex-start; gap:8px;">
                <span style="font-size:16px; flex-shrink:0;">⛔</span>
                <div>
                  <div style="font-size:11px; font-weight:800; color:#f87171; text-transform:uppercase; margin-bottom:2px;">Options Structure Demoted — Not Actionable</div>
                  <div style="font-size:10.5px; color:rgba(248,113,113,0.85); line-height:1.4;">${optPlan.summary.slice(optPlan.summary.indexOf('[DEMOTED'))}</div>
                  <div style="font-size:10px; color:var(--text-muted); margin-top:4px;">📊 Use the <strong>Equity Shares Plan</strong> or the <strong>LEAPS / Income tiers</strong> below.</div>
                </div>
              </div>
              ` : ''}

              <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; font-family:'JetBrains Mono',monospace; font-size:12px;">
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--text-muted); text-transform:uppercase; font-family:'Outfit',sans-serif;">Expiration</div>
                  <div style="font-weight:700; color:var(--text-main); margin-top:2px;">${optPlan.expiration || '--'}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--text-muted); text-transform:uppercase; font-family:'Outfit',sans-serif;">Entry Trigger</div>
                  <div style="font-weight:700; color:${(optPlan.entry_trigger && optPlan.entry_trigger !== 'NONE') ? 'var(--text-main)' : 'var(--text-muted)'}; margin-top:2px;">${(optPlan.entry_trigger && optPlan.entry_trigger !== 'NONE') ? optPlan.entry_trigger.replace(/_/g, ' ') : '— See Equity Plan'}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--text-muted); text-transform:uppercase; font-family:'Outfit',sans-serif;">Strikes (Long / Short)</div>
                  <div style="font-weight:700; color:${(optPlan.long_strike && optPlan.short_strike) ? 'var(--blue)' : 'var(--text-muted)'}; margin-top:2px;">${(optPlan.long_strike && optPlan.short_strike) ? `$${optPlan.long_strike} / $${optPlan.short_strike}` : 'N/A – Unscaled'}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--text-muted); text-transform:uppercase; font-family:'Outfit',sans-serif;">Target Net Debit</div>
                  <div style="font-weight:700; color:var(--text-main); margin-top:2px;">${(optPlan.target_debit && !(!optPlan.actionable || optPlan.structure === 'NONE')) ? `~$${Number(optPlan.target_debit).toFixed(2)}` : '--'}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--rose); text-transform:uppercase; font-family:'Outfit',sans-serif;">Max Risk / Loss</div>
                  <div style="font-weight:800; color:${(!optPlan.actionable || optPlan.structure === 'NONE') ? 'var(--text-muted)' : 'var(--rose)'}; margin-top:2px;">${(!optPlan.actionable || optPlan.structure === 'NONE') ? '--' : (optPlan.max_loss ? `$${Number(optPlan.max_loss).toLocaleString()}` : '--')}</div>
                </div>
                <div style="background:var(--bg-surface); border:1px solid var(--border); padding:8px 10px; border-radius:6px;">
                  <div style="font-size:10px; color:var(--emerald); text-transform:uppercase; font-family:'Outfit',sans-serif;">Max Profit</div>
                  <div style="font-weight:800; color:${(!optPlan.actionable || optPlan.structure === 'NONE') ? 'var(--text-muted)' : 'var(--emerald)'}; margin-top:2px;">${(!optPlan.actionable || optPlan.structure === 'NONE') ? '--' : (optPlan.max_profit ? `$${Number(optPlan.max_profit).toLocaleString()}` : '--')}</div>
                </div>
              </div>
            </div>
          </div>

          <!-- Column 2: Equity Shares Plan -->
          <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:18px; display:flex; flex-direction:column; justify-content:space-between;">
            <div>
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                <span style="font-size:13px; font-weight:800; color:var(--emerald); text-transform:uppercase; letter-spacing:0.5px;">
                  📊 Equity Shares Execution Plan ${isDemoted ? '(Primary Vehicle)' : ''}
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

        <!-- 3-Tiered Options Vehicle Menu (Tactical Spread vs Secular LEAPS vs Floor Income) -->
        ${(wl.options_menu && (wl.options_menu.tactical_spread || wl.options_menu.leaps || wl.options_menu.income_or_csp)) ? `
        <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:16px; margin-bottom:20px;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
            <span style="font-size:13px; font-weight:800; color:var(--cyan-glow); text-transform:uppercase; letter-spacing:0.5px;">
              ⚡ 3-Tiered Actionable Options Menu (Multi-Duration & Capital Postures)
            </span>
            <span class="pill cyan" style="font-size:10px; font-weight:800;">LIVE TOOLS SYNERGY</span>
          </div>
          <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:12px;">
            <!-- Tier 1: Tactical Spread -->
            <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:12px;">
              <div style="font-size:10.5px; font-weight:800; color:var(--blue); text-transform:uppercase; margin-bottom:4px;">1. Tactical Spread (30–45 DTE)</div>
              <div style="font-size:12px; font-weight:700; color:var(--text-main); font-family:'JetBrains Mono',monospace;">${wl.options_menu.tactical_spread?.summary || optPlan.summary || '30-45 DTE Defined Risk'}</div>
              <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">Primary defined-risk directional play at floor support.</div>
            </div>
            <!-- Tier 2: Secular LEAPS -->
            <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:12px;">
              <div style="font-size:10.5px; font-weight:800; color:var(--emerald); text-transform:uppercase; margin-bottom:4px;">2. Secular Trend LEAPS (6–12 Mo)</div>
              <div style="font-size:12px; font-weight:700; color:var(--text-main); font-family:'JetBrains Mono',monospace;">${wl.options_menu.leaps?.summary || (wl.options_menu.leaps?.long_strike ? `$${wl.options_menu.leaps.long_strike} Deep ITM Call` : 'Delta 0.75–0.85 Secular Participation')}</div>
              <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">Participate in macro trend with capped capital and low theta decay.</div>
            </div>
            <!-- Tier 3: Income / CSP -->
            <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:6px; padding:12px;">
              <div style="font-size:10.5px; font-weight:800; color:var(--amber); text-transform:uppercase; margin-bottom:4px;">3. Income / Floor Support Harvest</div>
              <div style="font-size:12px; font-weight:700; color:var(--text-main); font-family:'JetBrains Mono',monospace;">${wl.options_menu.income_or_csp?.summary || 'Covered Call / CSP at Put Wall'}</div>
              <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">Yield harvest on existing shares/LEAPS, or cash-secured put at floor.</div>
            </div>
          </div>
        </div>
        ` : ''}

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

  askCopilotExecutionPlan(event) {
    if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
    const btn = (event && (event.currentTarget || event.target)) || document.querySelector("button:has-text('Plan Execution with Copilot')") || null;
    let origHtml = '';
    if (btn) {
      origHtml = btn.innerHTML;
      btn.innerHTML = '<span>⚡</span> Opening Copilot Execution...';
      btn.style.opacity = '0.85';
    }

    // 1. Auto-collapse redundant positions side-pane so Copilot drawer is spacious & in view
    this.togglePositionsPane(false);

    // 2. Ensure Copilot drawer and resizer are visible and uncollapsed
    const drawer = document.getElementById('modal-copilot-drawer');
    const resizer = document.getElementById('modal-chat-resizer');
    const modal = document.getElementById('report-modal');

    if (modal && modal.classList.contains('collapsed')) {
      this.restoreReportModal();
    }

    if (drawer) {
      drawer.style.display = 'flex';
      if (window.AppChat && typeof window.AppChat.applyModalChatWidth === 'function') {
        const curW = parseFloat(drawer.style.width) || 420;
        if (curW < 380) window.AppChat.applyModalChatWidth(420);
      }

      // Smooth scroll drawer into view
      try {
        drawer.scrollIntoView({ behavior: 'smooth', inline: 'end', block: 'nearest' });
      } catch (e) {}

      // Flash highlight pulse on drawer
      drawer.style.transition = 'box-shadow 0.3s ease, border-color 0.3s ease';
      drawer.style.boxShadow = '0 0 25px rgba(6, 182, 212, 0.45)';
      drawer.style.borderColor = 'var(--cyan-glow)';
      setTimeout(() => {
        if (drawer) {
          drawer.style.boxShadow = '';
          drawer.style.borderColor = '';
        }
      }, 1800);
    }
    if (resizer) resizer.style.display = 'flex';

    // 3. Build execution query
    const sym = (window.AppState.activeChatTicker || (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || 'STOCK').toUpperCase().trim();
    const chat = window.AppState.activeChats ? window.AppState.activeChats.find(c => (c.ticker || '').toUpperCase().trim() === sym) : null;
    const data = (chat && chat.reportData) || window.AppState.currentReportData;
    const optPlan = (data && data.ticker === sym && data.watch_levels?.options_plan?.summary) || 'the suggested position';
    const prompt = `What is the exact execution step for $${sym} (${optPlan}) right now? If the primary structure has repriced or moved, engineer 3 actionable alternative vehicles (Covered Calls/PMCC, Floor Bull Put Spread/CSP, or LEAPS/Strike Shift) using live tools.`;

    // 4. Send directly to Modal Copilot
    if (window.AppChat && typeof window.AppChat.askModalCopilot === 'function') {
      window.AppChat.askModalCopilot(prompt, sym, data?.date);
    } else if (window.AppChat && typeof window.AppChat.askActiveChat === 'function') {
      window.AppChat.askActiveChat(prompt);
    }

    // 5. Reset button text with confirmation feedback
    if (btn) {
      setTimeout(() => {
        btn.innerHTML = '<span>⚡</span> Sent to Copilot!';
        setTimeout(() => {
          btn.innerHTML = origHtml || '⚡ Plan Execution with Copilot';
          btn.style.opacity = '';
        }, 1500);
      }, 600);
    }
  },

  askIndependentCopilot(type) {
    const sym = (window.AppState.activeChatTicker || (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || 'STOCK').toUpperCase().trim();
    const chat = window.AppState.activeChats ? window.AppState.activeChats.find(c => (c.ticker || '').toUpperCase().trim() === sym) : null;
    const data = (chat && chat.reportData) || window.AppState.currentReportData || {};

    // Ensure Copilot drawer and resizer are visible and uncollapsed
    const drawer = document.getElementById('modal-copilot-drawer');
    const resizer = document.getElementById('modal-chat-resizer');
    const modal = document.getElementById('report-modal');

    if (modal && modal.classList.contains('collapsed')) {
      this.restoreReportModal();
    }
    if (drawer) drawer.style.display = 'flex';
    if (resizer) resizer.style.display = 'flex';

    let prompt = '';
    if (type === 'two_pass') {
      prompt = `What is going on with ${sym} in this Model B Independent report right now? Run Pass 1: assess auction structure, live spot vs base floor, binary risk/earnings, and options volatility. In case of ANY ambiguity, ask me to clarify with options before proceeding.`;
    } else if (type === 'plan') {
      prompt = `Provide the exact execution plan for ${sym} based on the Model B report (Plan A equity limit entry / breakout trigger vs Plan B options credit spread). If ambiguous, ask for clarification first.`;
    } else {
      prompt = `Analyze the Model B Independent report for ${sym}.`;
    }

    if (window.AppChat && typeof window.AppChat.askModalCopilot === 'function') {
      window.AppChat.askModalCopilot(prompt, sym, data?.date);
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
    modalContent.style.width = '480px';
    modalContent.style.height = '600px';
    modalContent.style.maxHeight = 'calc(100vh - 40px)';
    modalContent.style.maxWidth = 'calc(100vw - 32px)';
    modalContent.style.pointerEvents = 'auto';
    modalContent.style.boxShadow = '0 16px 50px rgba(0, 0, 0, 0.55), 0 0 0 1px var(--border)';
    modalContent.style.border = 'none';
    modalContent.style.borderRadius = '12px';

    const splitCont = document.getElementById('modal-split-container');
    const reportPane = document.getElementById('modal-report-pane');
    const posPane = document.getElementById('modal-positions-pane');
    const resizer = document.getElementById('modal-chat-resizer');
    const copilotDrawer = document.getElementById('modal-copilot-drawer');
    if (splitCont) splitCont.style.display = 'flex';
    if (reportPane) reportPane.style.display = 'none';
    if (posPane) posPane.style.display = 'none';
    if (resizer) resizer.style.display = 'none';
    if (copilotDrawer) {
      copilotDrawer.style.width = '100%';
      copilotDrawer.style.maxWidth = '100%';
      copilotDrawer.style.flex = '1';
      copilotDrawer.style.display = 'flex';
    }

    const activeTicker = window.AppState.activeChatTicker || (window.AppState.currentReportData ? window.AppState.currentReportData.ticker : 'CHAT');
    const titleEl = document.getElementById('modal-ticker-title');
    if (titleEl) titleEl.innerText = `💬 ${activeTicker} Copilot`;
    const pulse = document.getElementById('modal-minimized-pulse');
    if (pulse) pulse.style.display = 'inline-block';

    const expandBtn = document.getElementById('btn-modal-expand-full');
    if (expandBtn) {
      expandBtn.style.display = 'inline-flex';
      expandBtn.innerHTML = '<span style="font-size:11px;">🗖</span> Maximize';
      expandBtn.className = 'modal-action-btn primary';
      expandBtn.title = 'Maximize to full dossier modal';
    }

    const minBtn = document.getElementById('btn-modal-min-toggle');
    if (minBtn) {
      minBtn.innerHTML = '<span style="font-size:12px;">➖</span> Collapse';
      minBtn.title = 'Collapse chat to bottom dock pill';
    }

    const togglePosBtn = document.getElementById('btn-toggle-positions-pane');
    if (togglePosBtn) togglePosBtn.style.display = 'none';

    this.renderActiveChatTabs();
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
    modalContent.style.minWidth = '';
    modalContent.style.pointerEvents = '';
    modalContent.style.boxShadow = '';
    modalContent.style.border = '';
    modalContent.style.borderRadius = '';

    const splitCont = document.getElementById('modal-split-container');
    const reportPane = document.getElementById('modal-report-pane');
    const posPane = document.getElementById('modal-positions-pane');
    const resizer = document.getElementById('modal-chat-resizer');
    const copilotDrawer = document.getElementById('modal-copilot-drawer');
    if (splitCont) splitCont.style.display = 'flex';
    if (reportPane) reportPane.style.display = 'flex';
    if (posPane) posPane.style.display = (window.AppState.showPositionsPane !== false) ? 'flex' : 'none';
    if (resizer) resizer.style.display = 'flex';
    if (copilotDrawer && window.AppChat) {
      const savedW = parseFloat(localStorage.getItem('modal_copilot_w')) || 420;
      window.AppChat.applyModalChatWidth(savedW);
    }

    const data = window.AppState.currentReportData;
    const ticker = data ? data.ticker : (window.AppState.activeChatTicker || 'REPORT');
    const date = data ? data.date : '';

    const titleEl = document.getElementById('modal-ticker-title');
    if (titleEl) titleEl.innerText = `${ticker} RESEARCH REPORT (${date || 'Active'})`;
    const pulse = document.getElementById('modal-minimized-pulse');
    if (pulse) pulse.style.display = 'none';

    const expandBtn = document.getElementById('btn-modal-expand-full');
    if (expandBtn) expandBtn.style.display = 'none';

    const minBtn = document.getElementById('btn-modal-min-toggle');
    if (minBtn) {
      minBtn.innerHTML = '<span style="font-size:12px;">🗕</span> Float Chat';
      minBtn.title = 'Float as bottom-right chat window';
    }

    const togglePosBtn = document.getElementById('btn-toggle-positions-pane');
    if (togglePosBtn) togglePosBtn.style.display = 'inline-flex';

    this.renderActiveChatTabs();
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
        // Expand back to floating chat
        modal.classList.remove('collapsed');
        modalContent.style.height = '600px';
        modalContent.style.width = '480px';
        modalContent.style.borderRadius = '12px';
        const splitCont = document.getElementById('modal-split-container');
        if (splitCont) splitCont.style.display = 'flex';
        const copilotDrawer = document.getElementById('modal-copilot-drawer');
        if (copilotDrawer) copilotDrawer.style.display = 'flex';
        const minBtn = document.getElementById('btn-modal-min-toggle');
        if (minBtn) {
          minBtn.innerHTML = '<span style="font-size:12px;">➖</span> Collapse';
          minBtn.title = 'Collapse chat to bottom dock pill';
        }
        const expandBtn = document.getElementById('btn-modal-expand-full');
        if (expandBtn) {
          expandBtn.style.display = 'inline-flex';
          expandBtn.innerHTML = '<span style="font-size:11px;">🗖</span> Maximize';
          expandBtn.className = 'modal-action-btn primary';
          expandBtn.title = 'Maximize to full dossier modal';
        }
      } else {
        // Collapse to dock pill
        modal.classList.add('collapsed');
        modalContent.style.height = '52px';
        modalContent.style.width = 'auto';
        modalContent.style.minWidth = '440px';
        modalContent.style.maxWidth = '760px';
        modalContent.style.borderRadius = '26px';
        const splitCont = document.getElementById('modal-split-container');
        if (splitCont) splitCont.style.display = 'none';
        const minBtn = document.getElementById('btn-modal-min-toggle');
        if (minBtn) {
          minBtn.innerHTML = '<span style="font-size:12px;">💬</span> Open Chat';
          minBtn.title = 'Expand to floating chat window';
        }
        const expandBtn = document.getElementById('btn-modal-expand-full');
        if (expandBtn) {
          expandBtn.style.display = 'inline-flex';
          expandBtn.innerHTML = '<span style="font-size:11px;">🗖</span> Maximize';
          expandBtn.className = 'modal-action-btn primary';
          expandBtn.title = 'Maximize to full dossier modal';
        }
      }
      this.renderActiveChatTabs();
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
      window.AppState.activeChats = [];
      window.AppState.activeChatTicker = '';
      this.renderActiveChatTabs();
    }
  },

  switchDossierTab(tab) {
    window.AppState.activeDossierTab = tab;
    if (window.AppState.activeChats && window.AppState.activeChatTicker) {
      const activeChat = window.AppState.activeChats.find(c => c.ticker === window.AppState.activeChatTicker);
      if (activeChat) activeChat.activeTab = tab;
    }
    ['plan', 'arb', 'sum', 'ind', 'history', 'chart', 'tv', 'flow'].forEach(t => {
      const btn = document.getElementById(`tab-${t}-btn`);
      if (btn) btn.className = `tab-btn ${t === tab ? 'active' : ''}`;
    });

    const bodyEl = document.getElementById('modal-body');
    const data = window.AppState.currentReportData;
    if (!bodyEl || !data) return;

    if (tab === 'plan') {
      this.togglePositionsPane(false);
      this.renderSuggestedPositionsFullTab(data, bodyEl);
    } else if (tab === 'arb') {
      const arbContent = data.arbitration_md || data.summary_md || data.independent_md;
      bodyEl.innerHTML = arbContent
        ? window.AppUtils.renderMarkdown(arbContent)
        : '<em>No arbitration or research report found for this date.</em>';
    } else if (tab === 'sum') {
      const sumContent = data.summary_md || data.arbitration_md || data.independent_md;
      bodyEl.innerHTML = sumContent
        ? window.AppUtils.renderMarkdown(sumContent)
        : '<em>No synthesis summary found for this date.</em>';
    } else if (tab === 'ind') {
      const bannerHtml = `
        <div style="background:linear-gradient(135deg, rgba(139,92,246,0.1), rgba(59,130,246,0.06)); border:1px solid rgba(139,92,246,0.28); border-radius:8px; padding:10px 14px; margin-bottom:14px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
          <div>
            <div style="display:flex; align-items:center; gap:6px;">
              <span style="font-weight:700; color:var(--violet); font-size:12.5px;">📐 MODEL B (Independent Quant & Macro)</span>
              <span class="pill purple" style="font-size:9.5px; padding:1px 6px;">ISOLATED CONTEXT</span>
            </div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">
              Objective macro & volume profile thesis. Two-Pass protocol active: clarifies ambiguities before locking execution.
            </div>
          </div>
          <div style="display:flex; gap:6px; flex-wrap:wrap;">
            <button class="btn" style="background:var(--violet); color:#fff; font-size:11px; padding:4px 10px; cursor:pointer;" onclick="AppSwing.askIndependentCopilot('two_pass')">
              ⚡ What Is Going On? (State Check)
            </button>
            <button class="btn secondary" style="font-size:11px; padding:4px 10px; cursor:pointer;" onclick="AppSwing.askIndependentCopilot('plan')">
              🎯 Tactical Execution Plan
            </button>
            <button class="btn secondary" style="font-size:11px; padding:4px 10px; cursor:pointer;" onclick="AppChat.startFreshModalSession()">
              🔄 Fresh Chat
            </button>
          </div>
        </div>
      `;
      const indContent = data.independent_md || data.arbitration_md || data.summary_md;
      const reportHtml = indContent
        ? window.AppUtils.renderMarkdown(indContent)
        : '<em>No independent report found for this date.</em>';
      bodyEl.innerHTML = bannerHtml + reportHtml;
    } else if (tab === 'history') {
      this.togglePositionsPane(false);
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
        
        const rowBg = isCurrent ? 'background:rgba(59,130,246,0.08);' : '';
        const badge = isCurrent ? '<span class="pill blue" style="font-size:9.5px; padding:1px 6px; margin-left:4px; font-weight:800;">CURRENT</span>' : '';

        let cleanPreview = (item.preview || '')
          .replace(/```[a-zA-Z0-9_:]*/g, '')
          .replace(/```/g, '')
          .trim();

        // If preview is a raw JSON block (e.g. from legacy watch_levels block)
        if (cleanPreview.startsWith('{') || cleanPreview.includes('"shares_plan"') || cleanPreview.includes('"ticker":')) {
          const mEzLow = cleanPreview.match(/"entry_zone_low":\s*([0-9.]+)/);
          const mEzHigh = cleanPreview.match(/"entry_zone_high":\s*([0-9.]+)/);
          const mStop = cleanPreview.match(/"tactical_stop":\s*([0-9.]+)/);
          const mT1 = cleanPreview.match(/"target_1":\s*([0-9.]+)/);
          const mT2 = cleanPreview.match(/"target_2":\s*([0-9.]+)/);
          const mOpt = cleanPreview.match(/"structure":\s*"([^"]+)"/);
          const mBreak = cleanPreview.match(/"breakout_level":\s*([0-9.]+)/);

          const parts = [];
          if (mEzLow && mEzHigh) parts.push(`🎯 Entry Limit: $${mEzLow[1]} – $${mEzHigh[1]}`);
          else if (mEzLow) parts.push(`🎯 Entry Limit: $${mEzLow[1]}`);
          if (mStop) parts.push(`🛑 Stop: $${mStop[1]}`);
          if (mT1) parts.push(`🏁 T1: $${mT1[1]}`);
          if (mT2) parts.push(`🏁 T2: $${mT2[1]}`);
          if (mBreak) parts.push(`🚀 Breakout: $${mBreak[1]}`);
          if (mOpt) parts.push(`⚡ Options: ${mOpt[1]}`);
          if (parts.length > 0) {
            cleanPreview = parts.join(' · ');
          } else {
            cleanPreview = cleanPreview.replace(/[{}\"\':]/g, ' ').replace(/\s+/g, ' ').trim();
          }
        }

        // Clean out any raw markdown formatting, bullets, asterisks, brackets
        cleanPreview = cleanPreview
          .replace(/\*\*/g, '')
          .replace(/__+/g, '')
          .replace(/^\s*[\*\-\•]\s*/, '')
          .replace(/\s+[\*\-\•]\s+/g, ' · ')
          .replace(/\s*\*\s*/g, ' · ')
          .replace(/\[\s*([^\]]+)\s*\]/g, '$1')
          .trim();

        return `
          <tr style="${rowBg} cursor:pointer;" onclick="AppSwing.switchReportDate('${item.date}')" title="Click to view full research report for ${item.date}">
            <td style="font-family:'JetBrains Mono',monospace; font-weight:700; white-space:nowrap;">
              <span style="color:var(--text-main); font-size:13px;">${item.date}</span> ${badge}
            </td>
            <td style="font-family:'JetBrains Mono',monospace; color:var(--text-main); font-weight:800; font-size:13px;">
              ${spotStr}
            </td>
            <td>
              <span class="pill ${vClass}" style="font-size:10.5px; padding:3px 8px; font-weight:700; white-space:nowrap; ${vStyle}">${item.verdict}</span>
            </td>
            <td style="font-size:12.5px; color:var(--text-main); line-height:1.55; white-space:normal; min-width:320px; max-width:600px; word-break:break-word;">
              ${cleanPreview}
            </td>
            <td>
              <button class="btn secondary" onclick="event.stopPropagation(); AppSwing.switchReportDate('${item.date}')" style="padding:4px 10px; font-size:11px; white-space:nowrap; ${isCurrent ? 'opacity:0.6;' : ''}" ${isCurrent ? 'disabled' : ''}>
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
                <span style="font-family:'Outfit',sans-serif; font-size:16px; font-weight:800; color:var(--text-main);">${ticker} Research History &amp; Thesis Drift</span>
                <span class="pill blue" style="font-size:11px; padding:2px 8px;">${timeline.length} Total Dates</span>
              </div>
              <button class="btn secondary" onclick="AppChat.sendModalCopilotMsg('Compare today to prior research dates for ${ticker}. What changed, what was the price then vs now, and was the earlier analysis right or wrong?')" style="font-size:11.5px; padding:5px 12px; cursor:pointer;">
                💬 Ask Copilot: What Changed &amp; Was the AI Right?
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
                  <th style="width:110px;">SPOT PRICE</th>
                  <th style="width:160px;">VERDICT</th>
                  <th style="min-width:320px;">THESIS HIGHLIGHTS &amp; SUMMARY</th>
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

  // =====================================================================
  // DEDICATED REAL-TIME TRADINGVIEW CHART MODAL
  // =====================================================================
  _currentTvModalTicker: 'SPY',
  _currentTvModalInterval: 'D',

  openTradingViewModal(ticker, interval = 'D') {
    const sym = (ticker || 'SPY').trim().toUpperCase();
    this._currentTvModalTicker = sym;
    this._currentTvModalInterval = interval;
    this.setTicker(sym);

    const modal = document.getElementById('tv-chart-modal');
    const title = document.getElementById('tv-modal-ticker-title');
    const extLink = document.getElementById('tv-modal-btn-external');
    const priceEl = document.getElementById('tv-modal-live-price');

    if (title) title.innerText = `$${sym} - TradingView Interactive Chart`;
    if (extLink) extLink.href = `https://www.tradingview.com/chart/jPAQSlZC/?symbol=${encodeURIComponent(sym)}&interval=${encodeURIComponent(interval)}`;
    if (priceEl) priceEl.innerText = 'Live Spot';

    // Update interval buttons styling
    ['15', '60', 'D', 'W'].forEach(tf => {
      const btn = document.getElementById(`tv-tf-${tf}`);
      if (btn) {
        if (tf === interval) {
          btn.style.background = 'var(--blue)';
          btn.style.color = '#fff';
          btn.style.borderColor = 'var(--blue)';
          btn.style.fontWeight = '800';
        } else {
          btn.style.background = '';
          btn.style.color = '';
          btn.style.borderColor = '';
          btn.style.fontWeight = '700';
        }
      }
    });

    this.renderTradingViewModalFrame();

    // Fetch live quote for header badge if available
    if (window.AppApi && typeof window.AppApi.getQuotes === 'function') {
      window.AppApi.getQuotes([sym]).then(quotes => {
        if (quotes && quotes[sym] && quotes[sym].price) {
          if (priceEl) priceEl.innerText = `$${Number(quotes[sym].price).toFixed(2)}`;
        }
      }).catch(() => {});
    }

    if (modal) {
      modal.style.display = 'flex';
    }
  },

  setTradingViewModalInterval(interval) {
    this._currentTvModalInterval = interval;
    const extLink = document.getElementById('tv-modal-btn-external');
    if (extLink) extLink.href = `https://www.tradingview.com/chart/jPAQSlZC/?symbol=${encodeURIComponent(this._currentTvModalTicker || 'SPY')}&interval=${encodeURIComponent(interval)}`;

    ['15', '60', 'D', 'W'].forEach(tf => {
      const btn = document.getElementById(`tv-tf-${tf}`);
      if (btn) {
        if (tf === interval) {
          btn.style.background = 'var(--blue)';
          btn.style.color = '#fff';
          btn.style.borderColor = 'var(--blue)';
          btn.style.fontWeight = '800';
        } else {
          btn.style.background = '';
          btn.style.color = '';
          btn.style.borderColor = '';
          btn.style.fontWeight = '700';
        }
      }
    });
    this.renderTradingViewModalFrame();
  },

  renderTradingViewModalFrame() {
    const host = document.getElementById('tv-modal-chart-host');
    if (!host) return;

    const rawSym = this._currentTvModalTicker || 'SPY';
    // If not already prefixed with an exchange, use BATS: for real-time (0 delay) data on US stocks
    const sym = rawSym.includes(':') ? rawSym : `BATS:${rawSym}`;
    const interval = this._currentTvModalInterval || 'D';
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    const theme = isDark ? 'dark' : 'light';
    const toolbarBg = isDark ? '1e293b' : 'f1f3f6';

    host.innerHTML = `
      <iframe
        id="tv-modal-iframe"
        src="https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(sym)}&interval=${encodeURIComponent(interval)}&hidesidetoolbar=0&symboledit=1&saveimage=1&toolbarbg=${toolbarBg}&studies=%5B%5D&theme=${theme}&style=1&timezone=America%2FNew_York"
        style="width:100%; height:100%; min-height:500px; border:none;"
        allowtransparency="true"
        scrolling="no">
      </iframe>
    `;
  },

  closeTradingViewModal() {
    const modal = document.getElementById('tv-chart-modal');
    if (modal) modal.style.display = 'none';
    const host = document.getElementById('tv-modal-chart-host');
    if (host) host.innerHTML = '';
  },

  launchResearchFromTvModal() {
    const sym = this._currentTvModalTicker;
    if (sym) {
      this.setTicker(sym);
      this.closeTradingViewModal();
      this.launchResearch();
    }
  },

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

// Immediate Self-Healing Auto-Load for Calibration Scoreboard & Schwab Screener
if (document.readyState === 'complete' || document.readyState === 'interactive') {
  setTimeout(() => {
    if (window.AppSwing) {
      window.AppSwing.loadCalibrationScoreboard();
      window.AppSwing.loadSchwabScreener();
      window.AppSwing.startContinuousScreenerPolling();
    }
  }, 50);
} else {
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
      if (window.AppSwing) {
        window.AppSwing.loadCalibrationScoreboard();
        window.AppSwing.loadSchwabScreener();
        window.AppSwing.startContinuousScreenerPolling();
      }
    }, 50);
  });
}

// Global Keyboard Shortcut: Escape to close TV Chart Modal or Report Modal
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (window.AppSwing && typeof window.AppSwing.closeTradingViewModal === 'function') {
      window.AppSwing.closeTradingViewModal();
    }
  }
});


